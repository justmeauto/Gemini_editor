import os
import sys
import time
import json
import urllib.request
import urllib.parse
import argparse

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from Utilities.github_secret_updater import sync_token_to_github_secret
except ImportError:
    sync_token_to_github_secret = lambda path, content=None: False

# ==============================================================================
# AMTCE YouTube Authentication Script
# 
# To refresh or generate a token manually via CLI, run this script from the AMTCE root directory:
#
#   1. Default (Root credentials):
#      python scripts/auth_youtube.py
#
#   2. Niche-specific credentials (e.g., for 'fashion'):
#      python scripts/auth_youtube.py --secret "Credentials/social_media/fashion/client_secret.json" --token "Credentials/social_media/fashion/token.json"
#
# This will trigger the OAuth flow and save the new token.json to the specified path.
# ==============================================================================

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
    "https://www.googleapis.com/auth/yt-analytics-monetary.readonly"
]

DEFAULT_CLIENT_SECRET_FILE = "Credentials/client_secret.json"
DEFAULT_TOKEN_FILE         = "Credentials/token.json"
AUTH_CODE_FILE             = "Credentials/yt_auth_code.txt"

DEVICE_CODE_URL  = "https://oauth2.googleapis.com/device/code"
TOKEN_URL        = "https://oauth2.googleapis.com/token"


# ── Telegram helpers ──────────────────────────────────────────────────────────

def _send_telegram(message: str, token: str, admin_id: str, button_url: str = None):
    try:
        api_url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": admin_id, "text": message, "parse_mode": "HTML"}
        if button_url:
            payload["reply_markup"] = json.dumps({
                "inline_keyboard": [[{"text": "🔗 Tap to Authorize", "url": button_url}]]
            })
        data = urllib.parse.urlencode(payload).encode("utf-8")
        urllib.request.urlopen(api_url, data=data, timeout=10)
        print("📡 Telegram notification sent.")
        return True
    except Exception as e:
        print(f"⚠️ Telegram send failed: {e}")
        return False


def _get_telegram_creds(override_admin_id=None):
    """
    Returns (bot_token, admin_private_chat_id).
    ALWAYS sends to the ADMIN's private chat — NEVER to a group.
    Priority: override_admin_id > TELEGRAM_ADMIN_ID > TELEGRAM_OWNER_CHAT_ID > first entry of ADMIN_IDS
    """
    try:
        from dotenv import load_dotenv
        for p in ["Credentials/.env", ".env"]:
            if os.path.exists(p):
                load_dotenv(p, override=False)
                break
    except ImportError:
        pass

    token = os.getenv("TELEGRAM_BOT_TOKEN")

    # Strictly private-chat admin ID — group IDs are negative, we want a positive user ID
    # Check all sources and pick the first valid personal chat ID
    admin_sources = []
    if override_admin_id:
        admin_sources.append(override_admin_id)
    if os.getenv("TELEGRAM_OWNER_CHAT_ID"):
        admin_sources.append(os.getenv("TELEGRAM_OWNER_CHAT_ID"))
    if os.getenv("TELEGRAM_ADMIN_ID"):
        admin_sources.append(os.getenv("TELEGRAM_ADMIN_ID"))
    
    admin_ids_str = os.getenv("ADMIN_IDS", "")
    for aid in admin_ids_str.split(","):
        if aid.strip():
            admin_sources.append(aid.strip())

    admin_id = None
    for src in admin_sources:
        if not src:
            continue
        src_str = str(src).strip()
        if not src_str:
            continue
        if src_str.startswith("@") or src_str.startswith("-"):
            print(f"⚠️ Candidate ID '{src_str}' looks like a public GROUP/CHANNEL. Checking next fallback...")
            continue
        admin_id = src_str
        break

    if not admin_id:
        print("⚠️ No valid private TELEGRAM_ADMIN_ID or ADMIN_IDS found. Auth messages will NOT be sent.")

    print(f"📡 Auth will notify: chat_id={admin_id}")
    return token, admin_id


def _load_client_secret(secret_path):
    with open(secret_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    # Supports both "installed" and "tv" / "web" top-level keys
    for key in ("installed", "tv", "web"):
        if key in data:
            return data[key]
    raise ValueError(f"Unrecognised client_secret.json format (top-level keys: {list(data.keys())})")


# ── Device Flow (fully automatic — user just goes to URL and signs in) ────────

def _try_device_flow(client_id, client_secret, tg_token, tg_admin, token_path):
    """
    Google Device Authorization Grant.
    Requires app type = 'TV and Limited Input devices' in Google Cloud Console.
    Returns True on success, False if device flow is unsupported.
    """
    print("📺 Trying Device Authorization Flow...")

    # Step 1 — request device + user code
    try:
        req_data = urllib.parse.urlencode({
            "client_id": client_id,
            "scope": " ".join(SCOPES)
        }).encode("utf-8")
        resp = urllib.request.urlopen(DEVICE_CODE_URL, data=req_data, timeout=15)
        device_resp = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"ℹ️ Device flow not available: {e}")
        return False

    if "error" in device_resp:
        print(f"ℹ️ Device flow rejected by Google: {device_resp.get('error')}")
        return False

    device_code      = device_resp["device_code"]
    user_code        = device_resp["user_code"]
    verification_url = device_resp.get("verification_url", "https://www.google.com/device")
    expires_in       = int(device_resp.get("expires_in", 1800))
    interval         = int(device_resp.get("interval", 5))

    print(f"\n📺 DEVICE FLOW ACTIVE")
    print(f"   Go to: {verification_url}")
    print(f"   Enter code: {user_code}\n")

    # Step 2 — tell user via Telegram
    if tg_token and tg_admin:
        msg = (
            f"🔐 <b>YouTube Auth Required</b>\n\n"
            f"1️⃣ Open this link on your phone:\n"
            f"<a href='{verification_url}'>{verification_url}</a>\n\n"
            f"2️⃣ Enter this code:\n"
            f"<code>  {user_code}  </code>\n\n"
            f"3️⃣ Sign in with Google\n\n"
            f"✅ Authorization will complete automatically!"
        )
        _send_telegram(msg, tg_token, tg_admin, button_url=verification_url)

    # Step 3 — poll for completion
    deadline = time.time() + expires_in
    print("⏳ Polling for authorization...")
    while time.time() < deadline:
        time.sleep(interval)
        try:
            poll_data = urllib.parse.urlencode({
                "client_id":     client_id,
                "client_secret": client_secret,
                "device_code":   device_code,
                "grant_type":    "urn:ietf:params:oauth:grant-type:device_code"
            }).encode("utf-8")
            poll_resp = urllib.request.urlopen(TOKEN_URL, data=poll_data, timeout=15)
            token_data = json.loads(poll_resp.read().decode("utf-8"))

            if "access_token" in token_data:
                # Build a token.json compatible with google-auth
                token_json = {
                    "token":         token_data["access_token"],
                    "refresh_token": token_data.get("refresh_token"),
                    "token_uri":     TOKEN_URL,
                    "client_id":     client_id,
                    "client_secret": client_secret,
                    "scopes":        SCOPES,
                }
                os.makedirs(os.path.dirname(token_path) or ".", exist_ok=True)
                with open(token_path, "w", encoding="utf-8") as f:
                    json.dump(token_json, f, indent=2)
                print(f"✅ Authorized! Token saved to {token_path}")
                sync_token_to_github_secret(token_path, json.dumps(token_json, indent=2))
                if tg_token and tg_admin:
                    _send_telegram(
                        "✅ <b>YouTube Authorized!</b>\n\nToken saved. Uploads will resume automatically.",
                        tg_token, tg_admin
                    )
                return True

        except urllib.error.HTTPError as e:
            body = json.loads(e.read().decode("utf-8"))
            err  = body.get("error", "")
            if err == "authorization_pending":
                continue          # normal — user hasn't approved yet
            if err == "slow_down":
                interval += 5
                continue
            if err in ("access_denied", "expired_token"):
                print(f"❌ Device flow failed: {err}")
                if tg_token and tg_admin:
                    _send_telegram(f"❌ Auth failed: {err}. Send /ytcode to try again.", tg_token, tg_admin)
                return True       # Handled (even if denied)
            print(f"⚠️ Unexpected device flow error: {body}")
            return False
        except Exception as e:
            print(f"⚠️ Poll error: {e}")
            time.sleep(interval)

    print("❌ Device flow timed out.")
    if tg_token and tg_admin:
        _send_telegram("⏱️ Auth timed out. Send /ytcode to start again.", tg_token, tg_admin)
    return True  # Handled (just timed out)


# ── Fallback: URL + Telegram code exchange flow ───────────────────────────────

def _fallback_url_flow(secret_path, token_path, tg_token, tg_admin):
    """
    URL + Telegram code exchange flow.
    1. Generates authorization URL immediately.
    2. Sends it to Telegram chat with a direct 'Tap to Authorize' button and HTML link.
    3. Opens browser locally on the host machine.
    4. Waits for code either via:
       - Telegram bot (/ytcode command dropping code in Credentials/yt_auth_code.txt)
       - Direct URL paste into Telegram bot chat
    5. Exchanges code for token.json, saves it, and syncs to GitHub Secrets via GH PAT.
    """
    from google_auth_oauthlib.flow import InstalledAppFlow
    import webbrowser

    flow = InstalledAppFlow.from_client_secrets_file(secret_path, SCOPES)
    flow.redirect_uri = "http://localhost"

    auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")
    print(f"\n🔗 AUTH URL:\n{auth_url}\n")

    # 1. Immediately notify user on Telegram with the authorization link & instructions
    if tg_token and tg_admin:
        msg = (
            f"🔐 <b>YouTube Authorization Required</b>\n\n"
            f"1️⃣ Tap <b>Tap to Authorize</b> below (or click the link) to sign in with Google:\n"
            f"<a href='{auth_url}'>👉 Click here to Sign in with Google</a>\n\n"
            f"2️⃣ Grant the requested YouTube permissions.\n\n"
            f"3️⃣ Google will redirect your browser to a page starting with <code>http://localhost/?code=...</code>.\n"
            f"<i>(This page may show 'site cannot be reached' — this is 100% normal!)</i>\n\n"
            f"4️⃣ Copy the entire URL from your browser's address bar and reply to this bot with:\n"
            f"<code>/ytcode YOUR_COPIED_URL</code>\n"
            f"<i>(or simply paste the URL directly into this chat)</i>"
        )
        _send_telegram(msg, tg_token, tg_admin, button_url=auth_url)
        print("📲 Dispatched YouTube Authorization link to Telegram admin chat.")

    # 2. Open browser locally only on desktop systems (skip in GitHub Actions / headless CI)
    is_headless = (
        os.getenv("GITHUB_ACTIONS") == "true"
        or os.getenv("CI") == "true"
        or (sys.platform != "win32" and not os.environ.get("DISPLAY"))
    )
    if not is_headless:
        try:
            webbrowser.open(auth_url)
        except Exception:
            pass

    # 3. Clean up any stale auth code file before starting polling
    if os.path.exists(AUTH_CODE_FILE):
        try:
            os.remove(AUTH_CODE_FILE)
        except Exception:
            pass

    # 4. Poll for code dropped by /ytcode Telegram bot command
    print("⏳ Waiting for authorization code via Telegram /ytcode or localhost URL (5 min)...")
    deadline = time.time() + 300
    while time.time() < deadline:
        if os.path.exists(AUTH_CODE_FILE):
            try:
                with open(AUTH_CODE_FILE, "r", encoding="utf-8") as f:
                    raw = f.read().strip()
                os.remove(AUTH_CODE_FILE)

                if raw:
                    # Auto-extract code parameter if full URL was pasted
                    if raw.startswith("http"):
                        parsed = urllib.parse.urlparse(raw)
                        qs = urllib.parse.parse_qs(parsed.query)
                        raw = qs.get("code", [raw])[0]

                    print(f"🔑 Exchanging authorization code for OAuth token...")
                    flow.fetch_token(code=raw)
                    creds = flow.credentials
                    os.makedirs(os.path.dirname(token_path) or ".", exist_ok=True)
                    token_json_str = creds.to_json()
                    with open(token_path, "w", encoding="utf-8") as f:
                        f.write(token_json_str)
                    print(f"✅ Token saved to {token_path}")

                    # Sync to GitHub Secrets using GitHub PAT
                    synced = sync_token_to_github_secret(token_path, token_json_str)
                    sync_msg = " and synced to GitHub Repository Secrets! 🔒" if synced else " (saved locally)."

                    if tg_token and tg_admin:
                        _send_telegram(
                            f"✅ <b>YouTube Authorized Successfully!</b>\n\n"
                            f"📁 Token saved to <code>{os.path.basename(token_path)}</code>{sync_msg}\n\n"
                            f"🚀 Uploads will resume automatically.",
                            tg_token,
                            tg_admin
                        )
                    return True
            except Exception as e:
                print(f"❌ Code exchange failed: {e}")
                if tg_token and tg_admin:
                    _send_telegram(f"❌ <b>Code exchange failed:</b>\n<code>{e}</code>\n\nSend /ytcode to try again.", tg_token, tg_admin)
                deadline = time.time() + 180

        time.sleep(2)

    print("❌ Timed out waiting for auth code.")
    if tg_token and tg_admin:
        _send_telegram("⏱️ <b>Auth timed out.</b> Send /ytcode to start again.", tg_token, tg_admin)
    return False


# ── Main entry point ──────────────────────────────────────────────────────────

def authenticate(client_secret_file=None, token_file=None, admin_id=None):
    tg_token, tg_admin = _get_telegram_creds(override_admin_id=admin_id)

    # Smart discovery if explicit secret is not provided
    targets = []
    if client_secret_file:
        targets.append((client_secret_file, token_file or DEFAULT_TOKEN_FILE))
    else:
        # Scan all known credential directories
        possible_pairs = [
            ("Credentials/social_media/Fashion/client_secret.json", "Credentials/social_media/Fashion/token.json"),
            ("Credentials/social_media/NSFW/client_secret.json", "Credentials/social_media/NSFW/token.json"),
            ("Credentials/social_media/General_Fallback/client_secret.json", "Credentials/social_media/General_Fallback/token.json"),
            (DEFAULT_CLIENT_SECRET_FILE, DEFAULT_TOKEN_FILE),
        ]
        for s_path, t_path in possible_pairs:
            if os.path.exists(s_path):
                targets.append((s_path, t_path))
                
    if not targets:
        msg = (
            f"❌ <b>YouTube Auth FAILED</b>\n\n"
            f"No valid <b>client_secret.json</b> found in any credential folder.\n\n"
            f"Download from Google Cloud Console → APIs &amp; Services → Credentials."
        )
        print("❌ No client_secret.json found!")
        if tg_token and tg_admin:
            _send_telegram(msg, tg_token, tg_admin)
        return

    print(f"🚀 Starting YouTube Authentication across {len(targets)} target(s)...")

    for secret_path, token_path in targets:
        print(f"\n🔑 Authenticating target: {secret_path}")
        try:
            secret = _load_client_secret(secret_path)
        except Exception as e:
            print(f"❌ Failed to read {secret_path}: {e}")
            continue

        client_id     = secret["client_id"]
        client_secret = secret["client_secret"]

        # Try Device Flow first (fully automatic — no code pasting)
        handled = _try_device_flow(client_id, client_secret, tg_token, tg_admin, token_path)

        if not handled:
            # Device flow unsupported → fallback to URL + /ytcode paste
            print("⬇️ Falling back to URL auth flow...")
            _fallback_url_flow(secret_path, token_path, tg_token, tg_admin)


if __name__ == "__main__":
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if os.path.basename(root_dir) == "simpler update":
        root_dir = os.path.dirname(root_dir)
    os.chdir(root_dir)

    parser = argparse.ArgumentParser(description="AMTCE YouTube Authentication")
    parser.add_argument("--secret",   help="Path to client_secret.json")
    parser.add_argument("--token",    help="Path to save token.json")
    parser.add_argument("--admin-id", help="Telegram Admin Chat ID to send auth links to")
    args = parser.parse_args()

    authenticate(client_secret_file=args.secret, token_file=args.token, admin_id=args.admin_id)
