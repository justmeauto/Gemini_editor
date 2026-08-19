"""
Publishing_Modules / telegram_user_manager.py
===========================================================================
Manages user registration, nickname setup, password creation, hashing,
session verification, OTP recovery, and persistent storage in data/telegram_users.json.
"""

import os
import time
import json
import secrets
import logging
import hashlib
from datetime import datetime
from typing import Dict, Optional, Tuple

logger = logging.getLogger("telegram_user_manager")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(_REPO_ROOT, "data")
USERS_JSON_PATH = os.path.join(DATA_DIR, "telegram_users.json")


def _hash_password(password: str) -> str:
    """Computes SHA-256 hash for secure password storage."""
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def load_all_users() -> Dict[str, Dict]:
    """Loads all registered user records from data/telegram_users.json."""
    if not os.path.exists(USERS_JSON_PATH):
        return {}
    try:
        with open(USERS_JSON_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as err:
        logger.error("Failed to load %s: %s", USERS_JSON_PATH, err)
        return {}


def sync_users_json_to_telegram_vault(upload_fn=None):
    """Uploads updated telegram_users.json to Storage Group & updates pinned master_vault_index.json."""
    try:
        if not upload_fn:
            try:
                from Downloader_Modules.telegram_listener import _send_file_multipart
                upload_fn = _send_file_multipart
            except Exception:
                upload_fn = None

        from Publishing_Modules.telegram_vault_indexer import TelegramVaultIndexer
        storage_group_id = os.getenv("TELEGRAM_STORAGE_GROUP_ID") or os.getenv("TELEGRAM_CHAT_ID")
        if storage_group_id and upload_fn and os.path.exists(USERS_JSON_PATH):
            res = upload_fn("sendDocument", storage_group_id, "document", USERS_JSON_PATH, caption=f"👤 **[VAULT BACKUP]** `telegram_users.json` (Updated {time.strftime('%H:%M:%S')})")
            if res and isinstance(res, dict):
                users_doc_id = res.get("document", {}).get("file_id")
                if users_doc_id:
                    indexer = TelegramVaultIndexer()
                    indexer.vault_index["telegram_users_file_id"] = users_doc_id
                    indexer._save_local_index()
                    indexer.upload_and_pin_vault_index_sync(upload_fn)
                    logger.info("✅ [USER VAULT BACKUP] Uploaded & PINNED updated telegram_users.json to Storage Group (file_id: %s)", users_doc_id[:15])
    except Exception as err:
        logger.warning("Notice uploading telegram_users.json to vault: %s", err)


def save_all_users(users: Dict[str, Dict], upload_fn=None, sync_to_vault: bool = True) -> bool:
    """Saves user records dictionary to telegram_users.json and syncs to Telegram Storage Group."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(USERS_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(users, f, indent=2, ensure_ascii=False)
        
        if sync_to_vault:
            sync_users_json_to_telegram_vault(upload_fn=upload_fn)
        return True
    except Exception as err:
        logger.error("Failed to save %s: %s", USERS_JSON_PATH, err)
        return False


SESSION_TIMEOUT_HOURS = float(os.getenv("TELEGRAM_SESSION_TIMEOUT_HOURS", "168.0"))  # Default: 7 days (168 hours)


def _escape_md(text: str) -> str:
    """Escapes Markdown special characters (like underscores in usernames) to prevent parser errors."""
    return text.replace("_", "\\_").replace("*", "\\*").replace("`", "\\`").replace("[", "\\[")


def check_and_expire_session(user_record: Dict) -> bool:
    """
    Checks if authenticated user session has expired due to inactivity (>7 days / 168 hours).
    Returns True if session was expired, False if still active.
    """
    if not user_record.get("authenticated", False):
        return False
    last_act_str = user_record.get("last_active", "")
    if last_act_str:
        try:
            last_act_dt = datetime.fromisoformat(last_act_str)
            inactive_hours = (datetime.now() - last_act_dt).total_seconds() / 3600.0
            if inactive_hours > SESSION_TIMEOUT_HOURS:
                user_record["authenticated"] = False
                user_id_str = str(user_record.get("user_id") or user_record.get("chat_id"))
                users = load_all_users()
                if user_id_str in users:
                    users[user_id_str]["authenticated"] = False
                    save_all_users(users)
                logger.info("🔒 [TELEGRAM USER MANAGER] Session expired due to inactivity (%.1f hrs) for User ID %s", inactive_hours, user_id_str)
                return True
        except Exception as _ex:
            logger.debug("Session parse error: %s", _ex)
    return False


def get_or_register_user(from_user: Dict, chat_id: str, admin_chat_id: Optional[str] = None) -> Dict:
    """
    Registers a new Telegram user record if not present, updates last active timestamp,
    and checks for 7-day session inactivity expiration.
    """
    users = load_all_users()
    user_id_str = str(from_user.get("id") or chat_id)

    if user_id_str not in users:
        is_admin = bool(admin_chat_id and (user_id_str == str(admin_chat_id) or chat_id == str(admin_chat_id)))
        users[user_id_str] = {
            "chat_id": str(chat_id),
            "user_id": from_user.get("id"),
            "first_name": from_user.get("first_name", "User"),
            "username": from_user.get("username", ""),
            "nickname": "",  # Set via /setnickname <nickname>
            "role": "admin" if is_admin else "user",
            "password_hash": "",
            "recovery_otp": "",
            "authenticated": False,  # Requires 1-time Nickname & Password setup
            "scrape_count": 0,
            "daily_scrape_count": 0,
            "quota_lock_timestamp": "",
            "apify_api_token": "",
            "joined_at": datetime.now().isoformat(),
            "last_active": datetime.now().isoformat()
        }
        save_all_users(users)
        logger.info("✨ [TELEGRAM USER MANAGER] Registered user: %s (@%s) [ID: %s]", from_user.get("first_name"), from_user.get("username"), user_id_str)
    else:
        # Check if session expired due to inactivity
        if users[user_id_str].get("password_hash") and users[user_id_str].get("nickname"):
            check_and_expire_session(users[user_id_str])
        else:
            users[user_id_str]["authenticated"] = False
        users[user_id_str].setdefault("scrape_count", 0)
        users[user_id_str].setdefault("daily_scrape_count", 0)
        users[user_id_str].setdefault("quota_lock_timestamp", "")
        users[user_id_str].setdefault("apify_api_token", "")
        users[user_id_str]["last_active"] = datetime.now().isoformat()
        if admin_chat_id and (user_id_str == str(admin_chat_id) or chat_id == str(admin_chat_id)):
            users[user_id_str]["role"] = "admin"
        save_all_users(users)

    return users[user_id_str]


def check_and_reset_daily_quota(user_record: Dict) -> Tuple[int, Optional[str], Optional[str]]:
    """
    Checks if 24 hours have elapsed since the 5th clip download timestamp.
    If 24h passed, auto-resets daily_scrape_count = 0.
    Returns (current_daily_count, countdown_str, reset_time_str).
    """
    lock_ts_str = user_record.get("quota_lock_timestamp", "")
    current_count = user_record.get("daily_scrape_count", 0)

    if not lock_ts_str:
        return current_count, None, None

    try:
        from datetime import datetime, timedelta
        lock_dt = datetime.fromisoformat(lock_ts_str)
        elapsed_sec = (datetime.now() - lock_dt).total_seconds()

        if elapsed_sec >= 86400.0:  # 24 hours = 86,400 seconds
            user_record["daily_scrape_count"] = 0
            user_record["quota_lock_timestamp"] = ""
            logger.info("⏳ [QUOTA RESET] 24 hours elapsed since 5th clip lock for user. Quota reset to 0/5!")
            return 0, None, None
        else:
            rem_sec = 86400.0 - elapsed_sec
            hours = int(rem_sec // 3600)
            mins = int((rem_sec % 3600) // 60)
            countdown_str = f"{hours}h {mins}m"
            reset_time_str = (lock_dt + timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
            return current_count, countdown_str, reset_time_str
    except Exception as _e:
        logger.warning("Error computing daily quota reset: %s", _e)
        return current_count, None, None


def increment_user_scrape_count(user_id_str: str) -> int:
    """Increments the daily scrape count for a registered user, logging 5th clip lock timestamp."""
    users = load_all_users()
    user_id_str = str(user_id_str)
    if user_id_str in users:
        u_rec = users[user_id_str]
        check_and_reset_daily_quota(u_rec)
        new_cnt = u_rec.get("daily_scrape_count", 0) + 1
        u_rec["daily_scrape_count"] = new_cnt
        u_rec["scrape_count"] = u_rec.get("scrape_count", 0) + 1
        if new_cnt >= 5 and not u_rec.get("quota_lock_timestamp"):
            u_rec["quota_lock_timestamp"] = datetime.now().isoformat()
            logger.info("⏱️ [QUOTA LOCK 5th CLIP] User ID %s reached 5th clip at %s. 24h timer started!", user_id_str, u_rec["quota_lock_timestamp"])
        save_all_users(users)
        logger.info("📊 [TELEGRAM USER MANAGER] User ID %s daily scrape count incremented to %d/5", user_id_str, new_cnt)
        return new_cnt
    return 0


def sync_user_secret_to_github(user_id_str: str, secret_suffix: str, secret_value: str) -> bool:
    """Syncs a user-specific secret (e.g. USER_1363193987_GEMINI_API_KEY) to GitHub Repository Secrets using GH_PAT."""
    try:
        from Utilities.github_secret_updater import sync_custom_secret_to_github
        secret_name = f"USER_{user_id_str}_{secret_suffix.upper()}"
        return sync_custom_secret_to_github(secret_name, secret_value)
    except Exception as _ex:
        logger.debug("Notice on user GitHub Secret sync: %s", _ex)
        return False


def set_user_apify_token(user_id_str: str, apify_token: str) -> bool:
    """Saves user personal Apify API token and syncs to GitHub Secrets."""
    users = load_all_users()
    user_id_str = str(user_id_str)
    clean_token = apify_token.strip()
    if user_id_str in users and clean_token:
        users[user_id_str]["apify_api_token"] = clean_token
        save_all_users(users)
        sync_user_secret_to_github(user_id_str, "APIFY_TOKEN", clean_token)
        logger.info("🔑 [TELEGRAM USER MANAGER] Personal Apify token saved for User ID %s", user_id_str)
        return True
    return False


def get_user_apify_token(user_id_str: str) -> Optional[str]:
    """Returns user personal Apify API token if present."""
    users = load_all_users()
    user_id_str = str(user_id_str)
    if user_id_str in users:
        return users[user_id_str].get("apify_api_token", "").strip() or None
    return None


def set_user_gemini_key(user_id_str: str, gemini_key: str) -> bool:
    """Saves user personal Gemini API key and syncs to GitHub Secrets."""
    users = load_all_users()
    user_id_str = str(user_id_str)
    clean_key = gemini_key.strip()
    if user_id_str in users and clean_key:
        users[user_id_str]["gemini_api_key"] = clean_key
        save_all_users(users)
        sync_user_secret_to_github(user_id_str, "GEMINI_API_KEY", clean_key)
        logger.info("🤖 [TELEGRAM USER MANAGER] Personal Gemini API key saved for User ID %s", user_id_str)
        return True
    return False


def set_user_meta_token(user_id_str: str, meta_token: str) -> bool:
    """Saves user personal Meta/Instagram Access Token and syncs to GitHub Secrets."""
    users = load_all_users()
    user_id_str = str(user_id_str)
    clean_token = meta_token.strip()
    if user_id_str in users and clean_token:
        users[user_id_str]["meta_page_token"] = clean_token
        save_all_users(users)
        sync_user_secret_to_github(user_id_str, "META_TOKEN", clean_token)
        logger.info("📸 [TELEGRAM USER MANAGER] Personal Meta Access Token saved for User ID %s", user_id_str)
        return True
    return False


def set_user_youtube_token(user_id_str: str, token_json_str: str) -> bool:
    """Saves user personal YouTube OAuth token JSON and syncs to GitHub Secrets."""
    users = load_all_users()
    user_id_str = str(user_id_str)
    clean_json = token_json_str.strip()
    if user_id_str in users and clean_json:
        users[user_id_str]["youtube_token_json"] = clean_json
        save_all_users(users)
        sync_user_secret_to_github(user_id_str, "YOUTUBE_TOKEN_JSON", clean_json)
        logger.info("🔴 [TELEGRAM USER MANAGER] Personal YouTube OAuth Token saved for User ID %s", user_id_str)
        return True
    return False


def set_user_nickname(user_id_str: str, nickname_text: str) -> Tuple[bool, str]:
    """Sets personal unique nickname for a registered user, enforcing strict uniqueness across all accounts."""
    users = load_all_users()
    clean_nick = nickname_text.strip()
    clean_nick_lower = clean_nick.lower()
    
    if not clean_nick:
        return False, "⚠️ *Nickname Cannot Be Blank*\n\nUsage: `/setnickname your_nickname`"

    # Enforce strict unique nickname constraint across all registered users
    for uid, udata in users.items():
        if str(uid) != str(user_id_str):
            existing_nick = (udata.get("nickname") or "").strip().lower()
            if existing_nick and existing_nick == clean_nick_lower:
                logger.warning("⚠️ [TELEGRAM USER MANAGER] Duplicate nickname attempt '%s' rejected for User ID %s (owned by User ID %s)", clean_nick, user_id_str, uid)
                return False, f"❌ *Nickname Taken*\n\nNickname `'{clean_nick}'` is already registered to another account! Please choose a unique nickname."

    if str(user_id_str) in users:
        users[str(user_id_str)]["nickname"] = clean_nick
        save_all_users(users)
        logger.info("🏷️ [TELEGRAM USER MANAGER] Unique nickname set to '%s' for User ID %s", clean_nick, user_id_str)
        return True, f"✅ *Nickname Updated!* You will now be called *{_escape_md(clean_nick)}*."
    return False, "❌ User account not found."


def set_user_password(user_id_str: str, plain_password: str) -> bool:
    """Sets and hashes personal account password for a registered user."""
    users = load_all_users()
    if user_id_str in users and plain_password.strip():
        users[user_id_str]["password_hash"] = _hash_password(plain_password.strip())
        users[user_id_str]["authenticated"] = True
        save_all_users(users)
        logger.info("🔐 [TELEGRAM USER MANAGER] Password set and session authenticated for User ID %s", user_id_str)
        return True
    return False


def verify_and_login_user(user_id_str: str, plain_password: str) -> bool:
    """Verifies plain text password against stored hash and authenticates user session."""
    users = load_all_users()
    if user_id_str in users:
        stored_hash = users[user_id_str].get("password_hash", "")
        if stored_hash and stored_hash == _hash_password(plain_password.strip()):
            users[user_id_str]["authenticated"] = True
            save_all_users(users)
            logger.info("🔓 [TELEGRAM USER MANAGER] User ID %s logged in successfully", user_id_str)
            return True
    return False


def generate_recovery_otp(user_id_str: str, valid_minutes: int = 10) -> Optional[Tuple[str, int]]:
    """
    Generates a 6-digit secure single-use OTP with an expiration time window (default: 10 minutes).
    Returns (otp_code, valid_minutes) or None on failure.
    """
    users = load_all_users()
    if user_id_str in users:
        otp = f"OTP-{secrets.randbelow(900000) + 100000}"
        expires_dt = datetime.now() + timedelta(minutes=valid_minutes)
        users[user_id_str]["recovery_otp"] = otp
        users[user_id_str]["recovery_otp_expires"] = expires_dt.isoformat()
        save_all_users(users)
        logger.info("🔑 [TELEGRAM USER MANAGER] Generated recovery OTP for User ID %s (expires in %d min)", user_id_str, valid_minutes)
        return otp, valid_minutes
    return None


def reset_password_with_otp(user_id_str: str, otp_input: str, new_password: str) -> Tuple[bool, str]:
    """
    Verifies OTP code and expiration timestamp.
    Returns (success: bool, status_reason: str).
    """
    users = load_all_users()
    if user_id_str in users:
        stored_otp = users[user_id_str].get("recovery_otp", "")
        expires_str = users[user_id_str].get("recovery_otp_expires", "")

        if not stored_otp or stored_otp.strip() != otp_input.strip():
            return False, "invalid"

        if expires_str:
            try:
                expires_dt = datetime.fromisoformat(expires_str)
                if datetime.now() > expires_dt:
                    users[user_id_str]["recovery_otp"] = ""
                    users[user_id_str]["recovery_otp_expires"] = ""
                    save_all_users(users)
                    logger.warning("⏳ [TELEGRAM USER MANAGER] Expired OTP attempt for User ID %s", user_id_str)
                    return False, "expired"
            except Exception as _te:
                logger.debug("OTP expiry parse notice: %s", _te)

        if new_password.strip():
            users[user_id_str]["password_hash"] = _hash_password(new_password.strip())
            users[user_id_str]["authenticated"] = True
            users[user_id_str]["recovery_otp"] = ""
            users[user_id_str]["recovery_otp_expires"] = ""
            save_all_users(users)
            logger.info("🎉 [TELEGRAM USER MANAGER] Successfully reset password via OTP for User ID %s", user_id_str)
            return True, "success"

    return False, "invalid"


def logout_user(user_id_str: str) -> bool:
    """Logs out user session."""
    users = load_all_users()
    if user_id_str in users:
        users[user_id_str]["authenticated"] = False
        save_all_users(users)
        return True
    return False
