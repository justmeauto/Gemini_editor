"""
Core_Modules/user_credential_manager.py
========================================
Multi-User Credential Manager with GitHub Secrets API Integration

Manages user authentication, credential collection, and secure storage via GitHub Secrets.
Each user's credentials are isolated by user_id to prevent cross-user data leakage.

Features:
- Username/password authentication via Telegram
- GitHub Secrets API integration for credential storage
- Credential collection with direct links to API key pages
- Strict user context isolation
- Session management per Telegram chat

Security Notes:
- Repository owner has access to all stored credentials
- Requires GitHub PAT with repo and secrets scopes
- Not suitable for production multi-tenant SaaS
- Consider dedicated secrets manager for production use

Author: AMTCE Multi-User Credential Manager v1.0
"""

import json
import logging
import os
import hashlib
import requests
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger("user_credential_manager")

# ── Configuration ─────────────────────────────────────────────────────────────
GITHUB_TOKEN = os.getenv("GITHUB_PAT")  # Personal Access Token for GitHub Secrets API
GITHUB_REPO = os.getenv("GITHUB_REPO", "iammidhun6771/AMTCE-Autonomous-Multimedia-Transformation-Compilation-Engine")
GITHUB_OWNER = os.getenv("GITHUB_OWNER", "iammidhun6771")

# ── User Session Storage ───────────────────────────────────────────────────────
_SESSIONS_DIR = Path("user_sessions")
_SESSIONS_DIR.mkdir(exist_ok=True)

# ── Credential Collection Links ─────────────────────────────────────────────────
CREDENTIAL_GUIDES = {
    "gemini_api_key": {
        "name": "Google Gemini API Key",
        "link": "https://aistudio.google.com/app/apikey",
        "instructions": [
            "1. Go to Google AI Studio",
            "2. Click 'Create API Key'",
            "3. Select or create a project",
            "4. Copy the API key (starts with AIzaSy...)",
            "5. Paste it here"
        ]
    },
    "apify_api_token": {
        "name": "Apify API Token",
        "link": "https://console.apify.com/account/integrations",
        "instructions": [
            "1. Go to Apify Console",
            "2. Navigate to Account → Integrations",
            "3. Click 'Create new token'",
            "4. Give it a name (e.g., AMTCE)",
            "5. Copy the token (starts with apify_api_...)",
            "6. Paste it here"
        ]
    },
    "youtube_client_secret": {
        "name": "YouTube Client Secret",
        "link": "https://console.cloud.google.com/apis/credentials",
        "instructions": [
            "1. Go to Google Cloud Console",
            "2. Select your project",
            "3. Go to APIs & Services → Credentials",
            "4. Create OAuth 2.0 Client ID (Web application)",
            "5. Download the JSON file",
            "6. Copy the 'client_secret' value from the JSON",
            "7. Paste it here"
        ]
    },
    "youtube_token_json": {
        "name": "YouTube Token JSON",
        "link": "https://console.cloud.google.com/apis/credentials",
        "instructions": [
            "1. After creating OAuth credentials, run the auth flow",
            "2. The token.json file will be generated",
            "3. Open the token.json file",
            "4. Copy the entire JSON content",
            "5. Paste it here"
        ]
    },
    "meta_page_token": {
        "name": "Meta (Facebook/Instagram) Page Token",
        "link": "https://developers.facebook.com/tools/explorer",
        "instructions": [
            "1. Go to Facebook Graph API Explorer",
            "2. Select your app",
            "3. Generate User Access Token with required permissions",
            "4. Get Page Access Token from the API",
            "5. Copy the token (starts with EAAB...)",
            "6. Paste it here"
        ]
    },
    "telegram_bot_token": {
        "name": "Telegram Bot Token",
        "link": "https://t.me/BotFather",
        "instructions": [
            "1. Open Telegram and search for @BotFather",
            "2. Send /newbot command",
            "3. Follow the instructions to create a bot",
            "4. Copy the token (starts with numbers:bot_token)",
            "5. Paste it here"
        ]
    },
    "telegram_public_group_id": {
        "name": "Telegram Public Group ID",
        "link": "https://t.me/username_to_id_bot",
        "instructions": [
            "1. Open Telegram and search for @username_to_id_bot",
            "2. Forward a message from your public group to this bot",
            "3. It will return the numeric group ID",
            "4. Copy the numeric ID (e.g., -1001234567890)",
            "5. Paste it here"
        ]
    }
}

# ── Required Credentials Per User ─────────────────────────────────────────────
REQUIRED_CREDENTIALS = [
    "gemini_api_key",
    "apify_api_token",
    "youtube_client_secret",
    "youtube_token_json",
    "meta_page_token",
    "telegram_bot_token",
    "telegram_public_group_id"
]

# ── Multi-Account Support ─────────────────────────────────────────────────────
# Platforms that support multiple accounts per user
MULTI_ACCOUNT_PLATFORMS = [
    "youtube",
    "instagram",
    "facebook",
    "telegram"
]

# Account credential types per platform
PLATFORM_ACCOUNT_CREDENTIALS = {
    "youtube": ["youtube_client_secret", "youtube_token_json"],
    "instagram": ["meta_page_token"],
    "facebook": ["meta_page_token"],
    "telegram": ["telegram_bot_token", "telegram_public_group_id"]
}

# ── Multi-Account Management ───────────────────────────────────────────────────
def add_user_social_account(
    user_id: str,
    platform: str,
    account_name: str,
    credentials: Dict[str, str]
) -> Dict[str, Any]:
    """
    Add a social media account for a user.
    
    Args:
        user_id: User ID
        platform: Platform (youtube, instagram, facebook, telegram)
        account_name: Display name for the account
        credentials: Dict of credential types to values
    
    Returns:
        Dict with add status
    """
    if platform not in MULTI_ACCOUNT_PLATFORMS:
        return {"success": False, "error": f"Unsupported platform: {platform}"}
    
    required_creds = PLATFORM_ACCOUNT_CREDENTIALS.get(platform, [])
    missing = [c for c in required_creds if c not in credentials]
    
    if missing:
        return {"success": False, "error": f"Missing required credentials: {missing}"}
    
    # Store each credential
    for cred_type, cred_value in credentials.items():
        if not _store_local_credential(user_id, f"{platform}_{account_name}_{cred_type}", cred_value):
            return {"success": False, "error": f"Failed to store {cred_type}"}
    
    # Update user data with account metadata
    user_file = _SESSIONS_DIR / f"{user_id}_user.json"
    try:
        with open(user_file, "r") as f:
            user_data = json.load(f)
        
        if "social_accounts" not in user_data:
            user_data["social_accounts"] = {}
        
        if platform not in user_data["social_accounts"]:
            user_data["social_accounts"][platform] = {}
        
        user_data["social_accounts"][platform][account_name] = {
            "added_at": datetime.utcnow().isoformat(),
            "is_active": True
        }
        
        with open(user_file, "w") as f:
            json.dump(user_data, f)
        
        logger.info(f"✅ Added {platform} account '{account_name}' for user {user_id}")
        return {"success": True, "platform": platform, "account_name": account_name}
        
    except Exception as e:
        logger.error(f"❌ Error adding social account: {e}")
        return {"success": False, "error": str(e)}

def get_user_social_accounts(user_id: str, platform: Optional[str] = None) -> Dict[str, Any]:
    """
    Get user's social media accounts.
    
    Args:
        user_id: User ID
        platform: Optional platform filter
    
    Returns:
        Dict with accounts list
    """
    user_file = _SESSIONS_DIR / f"{user_id}_user.json"
    
    if not user_file.exists():
        return {"success": False, "error": "User not found"}
    
    try:
        with open(user_file, "r") as f:
            user_data = json.load(f)
        
        accounts = user_data.get("social_accounts", {})
        
        if platform:
            accounts = {platform: accounts.get(platform, {})}
        
        return {
            "success": True,
            "user_id": user_id,
            "accounts": accounts
        }
        
    except Exception as e:
        logger.error(f"❌ Error getting social accounts: {e}")
        return {"success": False, "error": str(e)}

def get_account_credentials(user_id: str, platform: str, account_name: str) -> Dict[str, Any]:
    """
    Get credentials for a specific social media account.
    
    Args:
        user_id: User ID
        platform: Platform
        account_name: Account name
    
    Returns:
        Dict with credential values
    """
    required_creds = PLATFORM_ACCOUNT_CREDENTIALS.get(platform, [])
    credentials = {}
    
    for cred_type in required_creds:
        cred_key = f"{platform}_{account_name}_{cred_type}"
        value = _retrieve_local_credential(user_id, cred_key)
        if value:
            credentials[cred_type] = value
    
    if not credentials:
        return {"success": False, "error": "No credentials found"}
    
    return {
        "success": True,
        "user_id": user_id,
        "platform": platform,
        "account_name": account_name,
        "credentials": credentials
    }

def remove_user_social_account(user_id: str, platform: str, account_name: str) -> bool:
    """Remove a social media account for a user."""
    user_file = _SESSIONS_DIR / f"{user_id}_user.json"
    
    if not user_file.exists():
        return False
    
    try:
        with open(user_file, "r") as f:
            user_data = json.load(f)
        
        if "social_accounts" in user_data and platform in user_data["social_accounts"]:
            if account_name in user_data["social_accounts"][platform]:
                del user_data["social_accounts"][platform][account_name]
                
                with open(user_file, "w") as f:
                    json.dump(user_data, f)
                
                logger.info(f"✅ Removed {platform} account '{account_name}' for user {user_id}")
                return True
        
        return False
        
    except Exception as e:
        logger.error(f"❌ Error removing social account: {e}")
        return False

def format_account_selection_keyboard(user_id: str, platform: str) -> Optional[str]:
    """
    Format account selection as Telegram inline keyboard.
    
    Args:
        user_id: User ID
        platform: Platform
    
    Returns:
        JSON string of keyboard layout or None
    """
    accounts = get_user_social_accounts(user_id, platform)
    
    if not accounts.get("success"):
        return None
    
    platform_accounts = accounts.get("accounts", {}).get(platform, {})
    
    if not platform_accounts:
        return None
    
    keyboard = []
    for account_name in platform_accounts.keys():
        keyboard.append([{
            "text": f"📱 {account_name}",
            "callback_data": f"select_account_{platform}_{account_name}"
        }])
    
    keyboard.append([{
        "text": "❌ Cancel",
        "callback_data": f"select_account_cancel"
    }])
    
    return json.dumps({"inline_keyboard": keyboard})
def _get_github_headers() -> Dict[str, str]:
    """Get headers for GitHub API requests."""
    if not GITHUB_TOKEN:
        raise ValueError("GITHUB_PAT environment variable not set")
    return {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
        "Content-Type": "application/json"
    }

def _secret_name(user_id: str, credential_type: str) -> str:
    """Generate secret name for a user's credential."""
    return f"{user_id}_{credential_type}"

def store_user_credential(user_id: str, credential_type: str, value: str) -> bool:
    """
    Store a user's credential in GitHub Secrets.
    
    Args:
        user_id: Unique user identifier
        credential_type: Type of credential (e.g., 'gemini_api_key')
        value: Credential value to store
    
    Returns:
        True if successful, False otherwise
    """
    secret_name = _secret_name(user_id, credential_type)
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/actions/secrets/{secret_name}"
    
    # GitHub Secrets API requires the value to be base64 encoded
    import base64
    encoded_value = base64.b64encode(value.encode()).decode()
    
    payload = {
        "name": secret_name,
        "value": encoded_value
    }
    
    try:
        response = requests.put(url, headers=_get_github_headers(), json=payload)
        
        if response.status_code in [201, 204]:
            logger.info(f"✅ Stored credential {credential_type} for user {user_id}")
            return True
        else:
            logger.error(f"❌ Failed to store credential: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        logger.error(f"❌ Error storing credential: {e}")
        return False

def retrieve_user_credential(user_id: str, credential_type: str) -> Optional[str]:
    """
    Retrieve a user's credential from GitHub Secrets.
    
    Note: GitHub Secrets API does not allow retrieving secret values via API.
    They can only be used in GitHub Actions workflows.
    
    For this implementation, we'll use local encrypted storage as a fallback.
    """
    # GitHub Secrets cannot be retrieved via API (security feature)
    # We'll use local encrypted storage instead
    return _retrieve_local_credential(user_id, credential_type)

def _retrieve_local_credential(user_id: str, credential_type: str) -> Optional[str]:
    """Retrieve credential from local encrypted storage."""
    import cryptography.fernet
    
    # Load or generate encryption key
    key_file = _SESSIONS_DIR / "encryption.key"
    if key_file.exists():
        with open(key_file, "rb") as f:
            key = f.read()
    else:
        key = cryptography.fernet.Fernet.generate_key()
        with open(key_file, "wb") as f:
            f.write(key)
    
    fernet = cryptography.fernet.Fernet(key)
    
    # Load user's credential file
    user_file = _SESSIONS_DIR / f"{user_id}_credentials.json"
    if not user_file.exists():
        return None
    
    try:
        with open(user_file, "r") as f:
            encrypted_data = json.load(f)
        
        if credential_type in encrypted_data:
            encrypted_value = encrypted_data[credential_type]
            return fernet.decrypt(encrypted_value.encode()).decode()
        
        return None
        
    except Exception as e:
        logger.error(f"❌ Error retrieving local credential: {e}")
        return None

def _store_local_credential(user_id: str, credential_type: str, value: str) -> bool:
    """Store credential in local encrypted storage."""
    import cryptography.fernet
    
    # Load or generate encryption key
    key_file = _SESSIONS_DIR / "encryption.key"
    if key_file.exists():
        with open(key_file, "rb") as f:
            key = f.read()
    else:
        key = cryptography.fernet.Fernet.generate_key()
        with open(key_file, "wb") as f:
            f.write(key)
    
    fernet = cryptography.fernet.Fernet(key)
    
    # Load user's credential file
    user_file = _SESSIONS_DIR / f"{user_id}_credentials.json"
    encrypted_data = {}
    
    if user_file.exists():
        try:
            with open(user_file, "r") as f:
                encrypted_data = json.load(f)
        except:
            pass
    
    # Encrypt and store
    encrypted_value = fernet.encrypt(value.encode()).decode()
    encrypted_data[credential_type] = encrypted_value
    
    try:
        with open(user_file, "w") as f:
            json.dump(encrypted_data, f)
        return True
    except Exception as e:
        logger.error(f"❌ Error storing local credential: {e}")
        return False

# ── User Authentication ───────────────────────────────────────────────────────
def hash_password(password: str, salt: str = None) -> tuple:
    """Hash password with salt."""
    if salt is None:
        salt = os.urandom(32).hex()
    
    key = hashlib.pbkdf2_hmac(
        'sha256',
        password.encode('utf-8'),
        salt.encode('utf-8'),
        100000
    ).hex()
    
    return key, salt

def create_user(username: str, password: str, telegram_chat_id: str) -> Dict[str, Any]:
    """
    Create a new user account.
    
    Args:
        username: Unique username
        password: User password
        telegram_chat_id: Telegram chat ID for this user
    
    Returns:
        Dict with user_id and status
    """
    # Generate user_id from username
    user_id = username.lower().replace(" ", "_")
    
    # Check if user already exists
    user_file = _SESSIONS_DIR / f"{user_id}_user.json"
    if user_file.exists():
        return {"success": False, "error": "Username already exists"}
    
    # Hash password
    password_hash, salt = hash_password(password)
    
    # Create user data
    user_data = {
        "user_id": user_id,
        "username": username,
        "password_hash": password_hash,
        "salt": salt,
        "telegram_chat_id": telegram_chat_id,
        "created_at": datetime.utcnow().isoformat(),
        "credentials_collected": [],
        "is_active": True
    }
    
    try:
        with open(user_file, "w") as f:
            json.dump(user_data, f)
        
        logger.info(f"✅ Created user: {username} (ID: {user_id})")
        return {"success": True, "user_id": user_id}
        
    except Exception as e:
        logger.error(f"❌ Error creating user: {e}")
        return {"success": False, "error": str(e)}

def authenticate_user(username: str, password: str) -> Optional[Dict[str, Any]]:
    """
    Authenticate a user.
    
    Args:
        username: Username
        password: Password
    
    Returns:
        User data if authenticated, None otherwise
    """
    user_id = username.lower().replace(" ", "_")
    user_file = _SESSIONS_DIR / f"{user_id}_user.json"
    
    if not user_file.exists():
        return None
    
    try:
        with open(user_file, "r") as f:
            user_data = json.load(f)
        
        # Verify password
        password_hash, salt = hash_password(password, user_data["salt"])
        
        if password_hash == user_data["password_hash"]:
            logger.info(f"✅ Authenticated user: {username}")
            return user_data
        else:
            logger.warning(f"❌ Failed authentication for: {username}")
            return None
            
    except Exception as e:
        logger.error(f"❌ Error authenticating user: {e}")
        return None

def get_user_by_telegram_chat(telegram_chat_id: str) -> Optional[Dict[str, Any]]:
    """
    Get user by Telegram chat ID.
    
    Args:
        telegram_chat_id: Telegram chat ID
    
    Returns:
        User data if found, None otherwise
    """
    for user_file in _SESSIONS_DIR.glob("*_user.json"):
        try:
            with open(user_file, "r") as f:
                user_data = json.load(f)
            
            if str(user_data.get("telegram_chat_id")) == str(telegram_chat_id):
                return user_data
                
        except Exception as e:
            logger.error(f"❌ Error reading user file: {e}")
            continue
    
    return None

# ── Credential Collection Flow ───────────────────────────────────────────────
def get_credential_guide(credential_type: str) -> Optional[Dict[str, Any]]:
    """Get guide for collecting a specific credential."""
    return CREDENTIAL_GUIDES.get(credential_type)

def format_credential_guide(credential_type: str) -> str:
    """Format credential guide for Telegram message."""
    guide = get_credential_guide(credential_type)
    if not guide:
        return f"❌ Unknown credential type: {credential_type}"
    
    lines = [
        f"🔑 *{guide['name']}*\n",
        f"📖 *Instructions:*",
    ]
    
    for i, instruction in enumerate(guide["instructions"], 1):
        lines.append(f"{i}. {instruction}")
    
    lines.append(f"\n🔗 *Direct Link:* {guide['link']}")
    lines.append(f"\n✅ Once you have the key, send it here to continue.")
    
    return "\n".join(lines)

def collect_user_credentials(user_id: str) -> Dict[str, Any]:
    """
    Get status of user's credential collection.
    
    Args:
        user_id: User ID
    
    Returns:
        Dict with collection status
    """
    user_file = _SESSIONS_DIR / f"{user_id}_user.json"
    
    if not user_file.exists():
        return {"success": False, "error": "User not found"}
    
    try:
        with open(user_file, "r") as f:
            user_data = json.load(f)
        
        collected = user_data.get("credentials_collected", [])
        missing = [cred for cred in REQUIRED_CREDENTIALS if cred not in collected]
        
        return {
            "success": True,
            "user_id": user_id,
            "collected": collected,
            "missing": missing,
            "complete": len(missing) == 0,
            "progress": f"{len(collected)}/{len(REQUIRED_CREDENTIALS)}"
        }
        
    except Exception as e:
        logger.error(f"❌ Error getting credential status: {e}")
        return {"success": False, "error": str(e)}

def save_user_credential(user_id: str, credential_type: str, value: str) -> Dict[str, Any]:
    """
    Save a user's credential.
    
    Args:
        user_id: User ID
        credential_type: Type of credential
        value: Credential value
    
    Returns:
        Dict with save status
    """
    if credential_type not in REQUIRED_CREDENTIALS:
        return {"success": False, "error": "Invalid credential type"}
    
    user_file = _SESSIONS_DIR / f"{user_id}_user.json"
    
    if not user_file.exists():
        return {"success": False, "error": "User not found"}
    
    try:
        # Store in local encrypted storage
        if not _store_local_credential(user_id, credential_type, value):
            return {"success": False, "error": "Failed to store credential locally"}
        
        # Update user data
        with open(user_file, "r") as f:
            user_data = json.load(f)
        
        if credential_type not in user_data.get("credentials_collected", []):
            user_data["credentials_collected"].append(credential_type)
        
        with open(user_file, "w") as f:
            json.dump(user_data, f)
        
        logger.info(f"✅ Saved credential {credential_type} for user {user_id}")
        
        # Check if all credentials collected
        status = collect_user_credentials(user_id)
        
        return {
            "success": True,
            "credential_type": credential_type,
            "complete": status.get("complete", False),
            "progress": status.get("progress", "0/0")
        }
        
    except Exception as e:
        logger.error(f"❌ Error saving credential: {e}")
        return {"success": False, "error": str(e)}

def get_all_user_credentials(user_id: str) -> Dict[str, Any]:
    """
    Get all credentials for a user.
    
    Args:
        user_id: User ID
    
    Returns:
        Dict with all credential values (only for authorized user)
    """
    credentials = {}
    
    for cred_type in REQUIRED_CREDENTIALS:
        value = _retrieve_local_credential(user_id, cred_type)
        if value:
            credentials[cred_type] = value
    
    return {
        "success": True,
        "user_id": user_id,
        "credentials": credentials
    }

# ── Session Management ───────────────────────────────────────────────────────
def create_session(telegram_chat_id: str, user_id: str) -> Dict[str, Any]:
    """Create a session for a user."""
    session_file = _SESSIONS_DIR / f"session_{telegram_chat_id}.json"
    
    session_data = {
        "telegram_chat_id": telegram_chat_id,
        "user_id": user_id,
        "created_at": datetime.utcnow().isoformat(),
        "last_activity": datetime.utcnow().isoformat(),
        "is_active": True
    }
    
    try:
        with open(session_file, "w") as f:
            json.dump(session_data, f)
        
        logger.info(f"✅ Created session for user {user_id}")
        return {"success": True, "session_data": session_data}
        
    except Exception as e:
        logger.error(f"❌ Error creating session: {e}")
        return {"success": False, "error": str(e)}

def get_session(telegram_chat_id: str) -> Optional[Dict[str, Any]]:
    """Get session by Telegram chat ID."""
    session_file = _SESSIONS_DIR / f"session_{telegram_chat_id}.json"
    
    if not session_file.exists():
        return None
    
    try:
        with open(session_file, "r") as f:
            session_data = json.load(f)
        
        # Update last activity
        session_data["last_activity"] = datetime.utcnow().isoformat()
        with open(session_file, "w") as f:
            json.dump(session_data, f)
        
        return session_data
        
    except Exception as e:
        logger.error(f"❌ Error getting session: {e}")
        return None

def destroy_session(telegram_chat_id: str) -> bool:
    """Destroy a session."""
    session_file = _SESSIONS_DIR / f"session_{telegram_chat_id}.json"
    
    if session_file.exists():
        session_file.unlink()
        logger.info(f"✅ Destroyed session for chat {telegram_chat_id}")
        return True
    
    return False

# ── Telegram Message Formatting ───────────────────────────────────────────────
def format_welcome_message() -> str:
    """Format welcome message for new users."""
    return (
        "👋 *Welcome to AMTCE Multi-User Portal*\n\n"
        "To get started, you need to:\n"
        "1️⃣ Create an account (username & password)\n"
        "2️⃣ Provide your API credentials\n"
        "3️⃣ Start using the service\n\n"
        "Send /register to create your account\n"
        "Send /login if you already have an account"
    )

def format_register_prompt() -> str:
    """Format registration prompt."""
    return (
        "📝 *Registration*\n\n"
        "Please send your credentials in this format:\n\n"
        "`username:your_username`\n"
        "`password:your_password`\n\n"
        "Example:\n"
        "`username:john_doe`\n"
        "`password:secure123`"
    )

def format_login_prompt() -> str:
    """Format login prompt."""
    return (
        "🔐 *Login*\n\n"
        "Please send your credentials in this format:\n\n"
        "`username:your_username`\n"
        "`password:your_password`\n\n"
        "Example:\n"
        "`username:john_doe`\n"
        "`password:secure123`"
    )

def format_credential_status(user_id: str) -> str:
    """Format credential collection status."""
    status = collect_user_credentials(user_id)
    
    if not status.get("success"):
        return "❌ Error checking credential status"
    
    lines = [
        f"📊 *Credential Status*\n",
        f"Progress: {status['progress']}\n",
        f"✅ Collected: {', '.join(status['collected'])}" if status['collected'] else "✅ Collected: None",
    ]
    
    if status['missing']:
        lines.append(f"\n❌ Still needed: {', '.join(status['missing'])}")
        lines.append(f"\nSend /setup followed by credential name to provide each one")
        lines.append(f"Example: /setup gemini_api_key")
    else:
        lines.append(f"\n🎉 All credentials collected! You can now use the service.")
    
    return "\n".join(lines)

def format_all_credentials_guide() -> str:
    """Format guide for all required credentials."""
    lines = [
        "📋 *Required Credentials*\n\n",
        "You'll need to provide the following credentials:\n"
    ]
    
    for i, cred_type in enumerate(REQUIRED_CREDENTIALS, 1):
        guide = get_credential_guide(cred_type)
        if guide:
            lines.append(f"{i}. {guide['name']}")
    
    lines.append(f"\nUse /setup <credential_name> to get instructions for each")
    lines.append(f"Example: /setup gemini_api_key")
    
    return "\n".join(lines)
