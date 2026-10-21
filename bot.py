#!/usr/bin/env python3
import requests
import json
import os
import time
import io
import threading
import socket
from concurrent.futures import ThreadPoolExecutor
from flask import Flask

# --- FORCE IPV4 (Prevents network timeouts) ---
orig_getaddrinfo = socket.getaddrinfo
def getaddrinfo_ipv4(host, port, family=0, type=0, proto=0, flags=0):
    return orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
socket.getaddrinfo = getaddrinfo_ipv4
# ----------------------------------------------

# --- HARDCODED CONFIG ---
BOT_TOKEN = "8845376984:AAF6YYQkDgx_WdR5Q3adOwPU5NXDTjUQP6c"
ADMIN_ID = 8584025855
BASE_URL = "https://meeshoji.hidenplay.net"
REFER_CODE = "TWXJ79C"
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

SESSIONS_FILE = "telegram_sessions.json"
DB_DIR = "database"

os.makedirs(DB_DIR, exist_ok=True)
executor = ThreadPoolExecutor(max_workers=50)

USER_STATES = {}
SESSIONS = []
TYPING_THREADS = {}

if os.path.exists(SESSIONS_FILE):
    try:
        with open(SESSIONS_FILE, "r") as f:
            SESSIONS = json.load(f)
    except: pass

def save_sessions():
    with open(SESSIONS_FILE, "w") as f:
        json.dump(SESSIONS, f)

def tg_api(method, **kwargs):
    try:
        req_timeout = 10 if method != "getUpdates" else (kwargs.get("timeout", 30) + 10)
        r = requests.post(f"{API_URL}/{method}", json=kwargs, timeout=req_timeout)
        return r.json()
    except Exception:
        return {}

def send_document(chat_id, file_obj, caption):
    url = f"{API_URL}/sendDocument"
    payload = {"chat_id": chat_id, "caption": caption}
    files = {"document": file_obj}
    try:
        r = requests.post(url, data=payload, files=files, timeout=30)
        return r.json()
    except Exception:
        return {}

def send_msg(chat_id, text, markdown=False):
    kwargs = {"chat_id": chat_id, "text": text}
    if markdown: kwargs["parse_mode"] = "Markdown"
    return tg_api("sendMessage", **kwargs)

def notify_admin(error_msg):
    send_msg(ADMIN_ID, f"🚨 ADMIN ALERT\n\n{error_msg}")

def start_typing(chat_id):
    stop_event = threading.Event()
    def loop():
        while not stop_event.is_set():
            tg_api("sendChatAction", chat_id=chat_id, action="typing")
            time.sleep(4)
    t = threading.Thread(target=loop, daemon=True)
    t.start()
    TYPING_THREADS[chat_id] = stop_event

def stop_typing(chat_id):
    if chat_id in TYPING_THREADS:
        TYPING_THREADS[chat_id].set()
        del TYPING_THREADS[chat_id]

def get_available_session():
    for s in SESSIONS:
        if not s.get("locked") and s.get("active", True):
            return s
    return None

def release_session(session):
    session["locked"] = False

# ---------------------------------------------------------
# CORE MEESHO LOGIC
# ---------------------------------------------------------
def setup_meesho_session(init_data):
    session = requests.Session()
    session.headers.update({
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": BASE_URL,
        "Referer": f"{BASE_URL}/",
        "User-Agent": "Mozilla/5.0 (Linux; Android 14; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
        "x-telegram-init-data": init_data
    })
    
    for attempt in range(2):
        try:
            r = session.post(f"{BASE_URL}/api/telegram/verify", json={"init_data": init_data}, timeout=45)
            if r.status_code == 401:
                return None, "AUTH_DEAD"
                
            token = r.json().get("session_token")
            if not token: return None, "Auth failed"
            
            session.headers.update({"Authorization": f"Bearer {token}", "x-session-token": token})
            session.cookies.set("session_token", token)
            
            session.post(f"{BASE_URL}/api/accounts/add", json={}, timeout=45)
            return session, None
        except requests.exceptions.Timeout:
            if attempt == 1: return None, "TIMEOUT"
            time.sleep(1)
        except Exception as e:
            return None, str(e)
            
    return None, "Unknown Error"

def check_fod(session):
    """Hunt for 170. Waits for server cooldown between tries."""
    for attempt in range(15):
        try:
            r = session.get(f"{BASE_URL}/api/fod", timeout=5)
            if r.status_code == 200:
                data = r.json()
                if data.get("enabled") and data.get("offer_bucket") == 170:
                    return True, data
            
            session.get(f"{BASE_URL}/api/offer/hunt", timeout=5)
            time.sleep(1.5) 
        except:
            pass
            
    try:
        r = session.get(f"{BASE_URL}/api/fod", timeout=5)
        if r.status_code == 200:
            return False, r.json()
    except: pass
    
    return False, {}

# ---------------------------------------------------------
# BOT FLOW HANDLERS
# ---------------------------------------------------------
def process_start(chat_id):
    send_msg(chat_id, "🔄 Starting up...")
    
    session_obj = get_available_session()
    if not session_obj:
        send_msg(chat_id, "⚠️ No active sessions available. Admin needs to add a new session.")
        return

    session_obj["locked"] = True
    start_typing(chat_id)
    
    session, err = setup_meesho_session(session_obj["init_data"])
    
    if err:
        stop_typing(chat_id)
        session_obj["locked"] = False
        if err == "AUTH_DEAD":
            session_obj["active"] = False
            save_sessions()
            notify_admin(f"Session dead (Invalid Token).")
            send_msg(chat_id, "❌ Session expired. Admin notified.")
        elif err == "TIMEOUT":
            send_msg(chat_id, "⚠️ Server is lagging (Timeout). Please send /start again.")
        else:
            notify_admin(f"Session error: {err}")
            send_msg(chat_id, "❌ Backend error. Admin notified.")
        return
        
    is_perfect, fod_data = check_fod(session)
        
    USER_STATES[chat_id] = {
        "step": "WAITING_PHONE",
        "session": session,
        "pool_obj": session_obj
    }
    stop_typing(chat_id)
    
    offer_text = fod_data.get("offer_text", "Unknown")
    bucket = fod_data.get("offer_bucket", "Unknown")
    
    if is_perfect:
        msg = f"✅ Slot ready!\n\n🎁 **FOD Secured:** {offer_text} (Bucket: {bucket})\n\n📱 Please enter the 10-digit Phone Number:"
    else:
        msg = f"⚠️ Got: **{offer_text}** (Bucket: {bucket})\n\n📱 Please enter the 10-digit Phone Number:"
        
    send_msg(chat_id, msg, markdown=True)

def process_phone(chat_id, phone, state):
    phone = phone.strip()
    if len(phone) != 10 or not phone.isdigit():
        send_msg(chat_id, "❌ Invalid format. Enter 10 digits:")
        return

    session = state["session"]
    start_typing(chat_id)
    send_msg(chat_id, f"⏳ Verifying offer & sending OTP...")
    
    try:
        # --- SECURE FOD RIGHT BEFORE OTP ---
        r_fod = session.get(f"{BASE_URL}/api/fod", timeout=4)
        current_bucket = r_fod.json().get("offer_bucket") if r_fod.status_code == 200 else 0
        
        if current_bucket != 170:
            for _ in range(8):
                session.get(f"{BASE_URL}/api/offer/hunt", timeout=4)
                time.sleep(1.5)
                r_fod = session.get(f"{BASE_URL}/api/fod", timeout=4)
                if r_fod.status_code == 200 and r_fod.json().get("offer_bucket") == 170:
                    break
        # -----------------------------------
        
        try: session.post(f"{BASE_URL}/api/login/check-phone", json={"phone": phone}, timeout=10)
        except: pass
        
        try: session.post(f"{BASE_URL}/api/referral/apply", json={"code": REFER_CODE}, timeout=10)
        except: pass
            
        r_otp = session.post(f"{BASE_URL}/api/login/request-otp", json={"phone": phone}, timeout=20)
        if r_otp.status_code != 200 or not r_otp.json().get("ok"):
            stop_typing(chat_id)
            send_msg(chat_id, f"❌ Failed to send OTP: {r_otp.text}")
            cleanup_user(chat_id)
            return
    except Exception as e:
        stop_typing(chat_id)
        send_msg(chat_id, f"❌ Network error: {str(e)}")
        cleanup_user(chat_id)
        return

    state["phone"] = phone
    state["step"] = "WAITING_OTP"
    stop_typing(chat_id)
    send_msg(chat_id, "📩 OTP Sent via SMS! Enter the OTP:")

def process_otp(chat_id, otp, state):
    otp = otp.strip()
    session = state["session"]
    phone = state["phone"]
    
    start_typing(chat_id)
    send_msg(chat_id, "⏳ Verifying OTP...")
    
    try:
        r = session.post(f"{BASE_URL}/api/login/verify", json={"otp": otp}, timeout=30)
        if r.status_code != 200 or not r.json().get("ok"):
            stop_typing(chat_id)
            send_msg(chat_id, f"❌ Verification failed: {r.text}")
            cleanup_user(chat_id)
            return
            
        send_msg(chat_id, "✅ OTP Verified! Extracting JSON...")
        r_exp = session.get(f"{BASE_URL}/api/session/export", timeout=30)
        export_data = r_exp.json()
        
        if not export_data.get("ok") or "json" not in export_data:
            stop_typing(chat_id)
            send_msg(chat_id, "❌ Failed to export JSON from server.")
            notify_admin(f"Export failed for {phone}:\n{str(export_data)}")
            cleanup_user(chat_id)
            return
            
        meesho_json_str = export_data["json"]
        
        db_path = os.path.join(DB_DIR, f"meesho_{phone}.json")
        with open(db_path, "w") as f: f.write(meesho_json_str)
            
        stop_typing(chat_id)
        
        file_obj = io.BytesIO(meesho_json_str.encode('utf-8'))
        file_obj.name = f"meesho_{phone}.json"
        send_document(chat_id, file_obj, f"🎉 Success! Here is your session JSON for +91 {phone}.")
        
        admin_file_obj = io.BytesIO(meesho_json_str.encode('utf-8'))
        admin_file_obj.name = f"meesho_{phone}.json"
        send_document(ADMIN_ID, admin_file_obj, f"🛠 **Admin Copy**\nUser: {chat_id}\nPhone: +91 {phone}")
        
        notify_admin(f"✅ User {chat_id} harvested {phone}.")
    except Exception as e:
        stop_typing(chat_id)
        send_msg(chat_id, f"❌ Error: {str(e)}")
        
    cleanup_user(chat_id)

def cleanup_user(chat_id):
    stop_typing(chat_id)
    if chat_id in USER_STATES:
        pool_obj = USER_STATES[chat_id].get("pool_obj")
        if pool_obj: release_session(pool_obj)
        del USER_STATES[chat_id]

# ---------------------------------------------------------
# MAIN LOOP
# ---------------------------------------------------------
def handle_message(msg):
    global SESSIONS
    chat_id = msg["chat"]["id"]
    text = msg.get("text", "")
    
    if chat_id == ADMIN_ID:
        if text.startswith("/addsession"):
            parts = text.split(" ", 1)
            if len(parts) > 1:
                SESSIONS.append({"init_data": parts[1].strip(), "active": True, "locked": False})
                save_sessions()
                send_msg(ADMIN_ID, f"✅ Session added! Total: {len(SESSIONS)}")
            return
        if text == "/admin":
            active = len([s for s in SESSIONS if s.get("active")])
            locked = len([s for s in SESSIONS if s.get("locked")])
            send_msg(ADMIN_ID, f"🛠 Admin\nSessions: {len(SESSIONS)}\nActive: {active}\nLocked: {locked}\nUsers: {len(USER_STATES)}")
            return
        if text == "/revive":
            for s in SESSIONS: s["active"] = True
            save_sessions()
            send_msg(ADMIN_ID, "♻️ All sessions marked as active again.")
            return

    if text == "/start":
        if chat_id in USER_STATES: cleanup_user(chat_id)
        executor.submit(process_start, chat_id)
        return

    if chat_id in USER_STATES:
        state = USER_STATES[chat_id]
        if state["step"] == "WAITING_PHONE":
            executor.submit(process_phone, chat_id, text, state)
        elif state["step"] == "WAITING_OTP":
            executor.submit(process_otp, chat_id, text, state)
    else:
        send_msg(chat_id, "Send /start to begin.")

def poll_updates():
    last_id = 0
    print(f"Bot is running for Admin {ADMIN_ID}...")
    tg_api("deleteWebhook", drop_pending_updates=True)
    
    while True:
        try:
            res = tg_api("getUpdates", offset=last_id + 1, timeout=30)
            for update in res.get("result", []):
                last_id = update["update_id"]
                if "message" in update:
                    executor.submit(handle_message, update["message"])
        except Exception as e:
            time.sleep(1)

# ---------------------------------------------------------
# RENDER FREE TIER FIX (Dummy Web Server)
# ---------------------------------------------------------
app = Flask(__name__)

@app.route('/')
def health_check():
    return "Bot is alive!", 200

def run_web_server():
    app.run(host='0.0.0.0', port=8080)

if __name__ == "__main__":
    # Start the web server in a background thread so Render doesn't kill the app
    threading.Thread(target=run_web_server, daemon=True).start()
    
    # Start your bot's main loop
    if not SESSIONS:
        SESSIONS.append({
            "init_data": "query_id=AAH_1qV_AwAAAP_WpX-aYHK9&user=%7B%22id%22%3A8584025855%2C%22first_name%22%3A%22Rishabh%22%2C%22last_name%22%3A%22%22%2C%22username%22%3A%22rishabhkumar7%22%2C%22language_code%22%3A%22en%22%2C%22allows_write_to_pm%22%3Atrue%2C%22photo_url%22%3A%22https%3A%5C%2F%5C%2Ft.me%5C%2Fi%5C%2Fuserpic%5C%2F320%5C%2FS_68PEYzPX6fpq4XnLlAha3PHCDZhprQvxEnpDQFAWwWFKNqzM3UFEANmHY0GZ5a.svg%22%7D&auth_date=1792563167&signature=ypE_owVcfGNRVZC5T20fmE0mIkIQtDvNatOEMVlmDlJzAbMRErMz6VfUQN6XwEXC7HPiIeCqC-TfrHndQhQ7Cw&hash=b7806e6a41ad7048c703d6b5be3dfb040007accaa38c6e30a85eeb9da42d192f",
            "active": True, "locked": False
        })
        save_sessions()
    poll_updates()