"""
Core_Modules/api_key_pool_manager.py
====================================
Distributed API Key Pool Manager for Multi-User Scalability

Manages a pool of user-provided API keys (Apify, etc.) for distributed scraping.
Implements load balancing, rotation, and usage tracking to minimize per-user impact
while maximizing cache hit rate across all users.

Features:
- API key pooling from multiple users
- Load balancing with round-robin rotation
- Usage tracking per user
- Vault cache integration (Column 2 lookup)
- Minimal per-user usage strategy
- Only uses keys from users who provided them

Author: AMTCE Distributed API Pool v1.0
"""

import json
import logging
import os
import time
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
from pathlib import Path
import random

logger = logging.getLogger("api_key_pool_manager")

# ── Configuration ─────────────────────────────────────────────────────────────
_POOL_DIR = Path("api_key_pool")
_POOL_DIR.mkdir(exist_ok=True)

# Usage limits per user per day (minimal to avoid notice)
DAILY_USAGE_LIMITS = {
    "apify_api_token": 50,  # 50 requests per day per user
}

# ── Pool Storage ───────────────────────────────────────────────────────────────
_POOL_FILE = _POOL_DIR / "api_key_pool.json"
_USAGE_FILE = _POOL_DIR / "usage_tracking.json"

def _load_pool() -> Dict[str, Any]:
    """Load API key pool metadata from storage (no actual keys stored)."""
    if _POOL_FILE.exists():
        try:
            with open(_POOL_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"❌ Error loading pool: {e}")
    return {"apify_tokens": {}, "last_updated": None}

def _save_pool(pool: Dict[str, Any]):
    """Save API key pool metadata to storage (no actual keys stored)."""
    try:
        pool["last_updated"] = datetime.utcnow().isoformat()
        with open(_POOL_FILE, "w") as f:
            json.dump(pool, f, indent=2)
    except Exception as e:
        logger.error(f"❌ Error saving pool: {e}")

def _load_usage() -> Dict[str, Any]:
    """Load usage tracking from storage."""
    if _USAGE_FILE.exists():
        try:
            with open(_USAGE_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"❌ Error loading usage: {e}")
    return {"apify_tokens": {}, "last_reset": datetime.utcnow().isoformat()}

def _save_usage(usage: Dict[str, Any]):
    """Save usage tracking to storage."""
    try:
        usage["last_reset"] = datetime.utcnow().isoformat()
        with open(_USAGE_FILE, "w") as f:
            json.dump(usage, f, indent=2)
    except Exception as e:
        logger.error(f"❌ Error saving usage: {e}")

def _reset_daily_usage_if_needed(usage: Dict[str, Any]) -> Dict[str, Any]:
    """Reset daily usage if it's a new day."""
    last_reset = usage.get("last_reset", "")
    if last_reset:
        try:
            last_date = datetime.fromisoformat(last_reset).date()
            if last_date < datetime.utcnow().date():
                logger.info("🔄 [API POOL] Resetting daily usage tracking")
                usage["apify_tokens"] = {}
                usage["last_reset"] = datetime.utcnow().isoformat()
        except Exception as e:
            logger.warning(f"⚠️ Error parsing last reset date: {e}")
    return usage

# ── Pool Management ───────────────────────────────────────────────────────────
def add_user_api_key(user_id: str, key_type: str, api_key: str) -> bool:
    """
    Add a user's API key to GitHub Secrets (not local storage).
    
    Args:
        user_id: User ID
        key_type: Type of API key (e.g., 'apify_api_token')
        api_key: The API key value
    
    Returns:
        True if successful, False otherwise
    """
    if key_type not in DAILY_USAGE_LIMITS:
        logger.warning(f"❌ Unsupported key type: {key_type}")
        return False
    
    # Store in GitHub Secrets
    try:
        from Core_Modules.user_credential_manager import store_user_credential
        secret_name = f"{user_id}_{key_type}"
        success = store_user_credential(user_id, key_type, api_key)
        
        if success:
            # Update local pool metadata only (not the key)
            pool = _load_pool()
            if key_type not in pool:
                pool[key_type] = {}
            
            pool[key_type][user_id] = {
                "added_at": datetime.utcnow().isoformat(),
                "is_active": True,
                "priority": 1.0  # Can be adjusted based on user tier
            }
            _save_pool(pool)
            logger.info(f"✅ [API POOL] Added {key_type} for user {user_id} to GitHub Secrets")
            return True
        else:
            logger.error(f"❌ Failed to store {key_type} in GitHub Secrets for user {user_id}")
            return False
            
    except Exception as e:
        logger.error(f"❌ Error adding API key to GitHub Secrets: {e}")
        return False

def remove_user_api_key(user_id: str, key_type: str) -> bool:
    """Remove a user's API key from GitHub Secrets and local pool metadata."""
    pool = _load_pool()
    
    # Remove from local metadata
    if key_type in pool and user_id in pool[key_type]:
        del pool[key_type][user_id]
        _save_pool(pool)
    
    # Note: GitHub Secrets API doesn't support deletion via standard API
    # The secret would need to be removed manually or via GitHub API with proper permissions
    logger.warning(f"⚠️ [API POOL] Removed {key_type} metadata for user {user_id}. GitHub Secret must be removed manually.")
    return True

def get_active_keys(key_type: str) -> List[Dict[str, Any]]:
    """
    Get all active API keys of a given type from GitHub Secrets, sorted by priority and usage.
    
    Returns:
        List of dicts with user_id, api_key, priority, and usage info
    """
    pool = _load_pool()
    usage = _load_usage()
    usage = _reset_daily_usage_if_needed(usage)
    
    if key_type not in pool:
        return []
    
    active_keys = []
    usage_data = usage.get(key_type, {})
    daily_limit = DAILY_USAGE_LIMITS.get(key_type, 100)
    
    for user_id, key_info in pool[key_type].items():
        if not key_info.get("is_active", True):
            continue
        
        # Check daily usage
        user_usage = usage_data.get(user_id, {}).get("count", 0)
        if user_usage >= daily_limit:
            logger.debug(f"⏭️ Skipping {user_id} - daily limit reached ({user_usage}/{daily_limit})")
            continue
        
        # Fetch actual API key from GitHub Secrets (via credential manager)
        try:
            from Core_Modules.user_credential_manager import retrieve_user_credential
            api_key = retrieve_user_credential(user_id, key_type)
            
            if not api_key:
                logger.warning(f"⚠️ Could not retrieve {key_type} for user {user_id} from GitHub Secrets")
                continue
            
            active_keys.append({
                "user_id": user_id,
                "api_key": api_key,
                "priority": key_info.get("priority", 1.0),
                "usage_count": user_usage,
                "usage_limit": daily_limit
            })
        except Exception as e:
            logger.error(f"❌ Error fetching {key_type} for user {user_id}: {e}")
            continue
    
    # Sort by priority (higher first), then by usage count (lower first)
    active_keys.sort(key=lambda x: (-x["priority"], x["usage_count"]))
    
    return active_keys

def get_next_api_key(key_type: str, requesting_user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Get the next available API key using round-robin with load balancing.
    
    Args:
        key_type: Type of API key
        requesting_user_id: User ID making the request (for preference)
    
    Returns:
        Dict with user_id, api_key, or None if no keys available
    """
    active_keys = get_active_keys(key_type)
    
    if not active_keys:
        logger.debug(f"ℹ️ [API POOL] No active pooled keys available for {key_type} (falling back to local .env token)")
        return None
    
    # If requesting user has their own key in pool, prefer it (minimal usage strategy)
    if requesting_user_id:
        for key in active_keys:
            if key["user_id"] == requesting_user_id:
                logger.info(f"🎯 [API POOL] Using requesting user's own key: {requesting_user_id}")
                return key
    
    # Otherwise, use least-used key (load balancing)
    selected = active_keys[0]
    logger.info(f"🎯 [API POOL] Selected key for user {selected['user_id']} (usage: {selected['usage_count']}/{selected['usage_limit']})")
    
    return selected

def record_api_usage(key_type: str, user_id: str, success: bool = True):
    """
    Record API usage for a user's key.
    
    Args:
        key_type: Type of API key
        user_id: User ID whose key was used
        success: Whether the API call was successful
    """
    usage = _load_usage()
    usage = _reset_daily_usage_if_needed(usage)
    
    if key_type not in usage:
        usage[key_type] = {}
    
    if user_id not in usage[key_type]:
        usage[key_type][user_id] = {
            "count": 0,
            "last_used": None,
            "success_count": 0,
            "failure_count": 0
        }
    
    usage[key_type][user_id]["count"] += 1
    usage[key_type][user_id]["last_used"] = datetime.utcnow().isoformat()
    
    if success:
        usage[key_type][user_id]["success_count"] += 1
    else:
        usage[key_type][user_id]["failure_count"] += 1
    
    _save_usage(usage)
    logger.debug(f"📊 [API POOL] Recorded usage for {user_id}: {usage[key_type][user_id]['count']}")

def get_pool_status() -> Dict[str, Any]:
    """Get current pool status and usage statistics."""
    pool = _load_pool()
    usage = _load_usage()
    usage = _reset_daily_usage_if_needed(usage)
    
    status = {
        "last_updated": pool.get("last_updated"),
        "last_reset": usage.get("last_reset"),
        "key_types": {}
    }
    
    for key_type in DAILY_USAGE_LIMITS:
        active_keys = get_active_keys(key_type)
        usage_data = usage.get(key_type, {})
        
        total_usage = sum(u.get("count", 0) for u in usage_data.values())
        total_capacity = len(active_keys) * DAILY_USAGE_LIMITS[key_type]
        
        status["key_types"][key_type] = {
            "active_keys": len(active_keys),
            "total_daily_usage": total_usage,
            "total_daily_capacity": total_capacity,
            "utilization_percent": round((total_usage / total_capacity * 100) if total_capacity > 0 else 0, 2),
            "users": [
                {
                    "user_id": k["user_id"],
                    "usage": k["usage_count"],
                    "limit": k["usage_limit"]
                }
                for k in active_keys
            ]
        }
    
    return status

def format_pool_status() -> str:
    """Format pool status for Telegram message."""
    status = get_pool_status()
    
    lines = [
        "🔑 *API Key Pool Status*\n",
        f"Last Updated: {status['last_updated'] or 'Never'}",
        f"Last Reset: {status['last_reset'] or 'Never'}\n"
    ]
    
    for key_type, data in status["key_types"].items():
        lines.append(f"📊 *{key_type}*")
        lines.append(f"Active Keys: {data['active_keys']}")
        lines.append(f"Usage: {data['total_daily_usage']}/{data['total_daily_capacity']} ({data['utilization_percent']}%)")
        
        if data["users"]:
            lines.append(f"\nUser Usage:")
            for user in data["users"]:
                lines.append(f"  • {user['user_id']}: {user['usage']}/{user['limit']}")
        
        lines.append("")
    
    return "\n".join(lines)

# ── Vault Cache Integration ───────────────────────────────────────────────────
async def check_vault_cache(social_url: str, vault_indexer) -> Optional[Dict[str, Any]]:
    """
    Check vault cache (Column 2) for existing download before using API pool.
    
    Args:
        social_url: Social media URL to check
        vault_indexer: TelegramVaultIndexer instance
    
    Returns:
        Cached entry if found, None otherwise
    """
    cached = vault_indexer.lookup_downloaded_source(social_url)
    
    if cached:
        logger.info(f"⚡ [API POOL] Vault cache hit for URL: {social_url[:60]}... (no API call needed)")
        return cached
    
    logger.debug(f"🔍 [API POOL] Vault cache miss for URL: {social_url[:60]}... (will use API pool)")
    return None

# ── Integration with Credential Manager ───────────────────────────────────────
def sync_from_credential_manager():
    """
    Sync API keys from credential manager to pool metadata.
    Only syncs metadata (user_id, priority) - actual keys stay in GitHub Secrets.
    Call this when a user completes credential collection.
    """
    try:
        from Core_Modules.user_credential_manager import _SESSIONS_DIR
        
        # Iterate through user credential files
        for cred_file in _SESSIONS_DIR.glob("*_user.json"):
            try:
                with open(cred_file, "r") as f:
                    user_data = json.load(f)
                
                # Extract user_id from filename
                user_id = user_data.get("user_id", cred_file.stem.replace("_user", ""))
                collected_creds = user_data.get("credentials_collected", [])
                
                # Add metadata to pool for each credential type
                for key_type in ["apify_api_token"]:
                    if key_type in collected_creds:
                        pool = _load_pool()
                        if key_type not in pool:
                            pool[key_type] = {}
                        
                        pool[key_type][user_id] = {
                            "added_at": user_data.get("created_at", datetime.utcnow().isoformat()),
                            "is_active": True,
                            "priority": 1.0
                        }
                        _save_pool(pool)
                        logger.info(f"✅ [API POOL] Synced metadata for {key_type} for user {user_id}")
                
            except Exception as e:
                logger.error(f"❌ Error syncing credentials for {cred_file}: {e}")
        
        logger.info("✅ [API POOL] Synced metadata from credential manager")
        
    except ImportError:
        logger.warning("⚠️ Credential manager not found")
    except Exception as e:
        logger.error(f"❌ Error syncing from credential manager: {e}")
