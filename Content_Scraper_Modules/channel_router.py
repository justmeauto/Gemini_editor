"""
channel_router.py — Content Channel Router
==========================================
Determines which destination channel a scraped reel should go to,
based on creator lookup and content category.

Public API:
  resolve_channel(ig_id, reel)  → (folder: str, title: str, is_nsfw: bool)
  get_source_accounts()         → list of source Instagram account IDs to scrape
  detect_gender_from_name(name) → "neutral" | "unknown"

Channel Map:
  General_Fallback   → General / Curated content
  Entertainment      → Entertainment / Highlight channel
  Fashion_Style      → Manual style input only
"""

import os
import json
import logging
import re
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Paths ─────────────────────────────────────────────────────────────────────
_BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
_IDENTITIES_PATH = os.path.join(_BASE_DIR, "source_accounts.json")

# ── Channel constants ─────────────────────────────────────────────────────────
CHANNEL_GENERAL       = "General_Fallback"
CHANNEL_ENTERTAINMENT = "Entertainment"
CHANNEL_FASHION       = "Fashion_Style"
CHANNEL_MANUAL        = "Fashion_Style"

# ── Runtime: detect if an Entertainment credential folder exists ───────────────
def _entertainment_creds_exist() -> bool:
    """Returns True if Credentials/social_media/Entertainment/ has credential files."""
    base = os.path.join("Credentials", "social_media", "Entertainment")
    return os.path.isdir(base) and any(os.path.isfile(os.path.join(base, f)) for f in os.listdir(base))

# ── In-memory cache ───────────────────────────────────────────────────────────
_identities_cache: Optional[Dict] = None


def _load_identities(force: bool = False) -> Dict:
    """Load source_accounts.json. Caches in memory."""
    global _identities_cache
    if _identities_cache is None or force:
        try:
            with open(_IDENTITIES_PATH, "r", encoding="utf-8") as f:
                raw_cfg = json.load(f)
            
            src_accounts = raw_cfg.get("source_accounts", [])

            _identities_cache = {
                "source_accounts": src_accounts,
                "nsfw_accounts": raw_cfg.get("nsfw_accounts", []),
                "creators": {}
            }
            logger.info("📋 Identities loaded — %d source accounts", len(src_accounts))
        except Exception as exc:
            logger.error("💥 Failed to load source_accounts.json: %s", exc)
            _identities_cache = {
                "source_accounts": [],
                "nsfw_accounts": [],
                "creators": {}
            }
    return _identities_cache


# ── Public API ─────────────────────────────────────────────────────────────────

def get_source_accounts() -> List[str]:
    """
    Returns the list of source Instagram account IDs to scrape from.
    Reads from source_accounts.json['source_accounts'].
    Also reads SOURCE_ACCOUNTS env var (comma-separated) as override.
    """
    env_override = os.getenv("SOURCE_ACCOUNTS", "").strip()
    if env_override:
        accounts = [a.strip().lstrip("@") for a in env_override.split(",") if a.strip()]
        logger.info("📡 Source accounts from ENV: %s", accounts)
        return accounts

    cfg = _load_identities()
    accounts = cfg.get("source_accounts", [])
    # Filter out placeholder values
    accounts = [
        a for a in accounts
        if a and not a.startswith("REPLACE_WITH")
    ]
    if not accounts:
        logger.warning(
            "⚠️ No source accounts configured! "
            "Edit Content_Scraper_Modules/source_accounts.json['source_accounts'] "
            "or set SOURCE_ACCOUNTS in .env"
        )
    return accounts


def detect_gender_from_name(name: str) -> str:
    """Neutral compatibility stub — gender heuristics deprecated."""
    return "neutral"


def _extract_person_name(reel: Dict) -> str:
    """
    Tries to extract the featured person or creator's name from reel metadata.
    Checks: taggedUsers fullName, caption name patterns, ownerUsername display name.
    Returns best candidate name string.
    """
    # Priority 1: Tagged users
    tagged = reel.get("taggedUsers", [])
    if tagged and isinstance(tagged, list):
        for user in tagged:
            if isinstance(user, dict):
                full_name = user.get("full_name") or user.get("fullName", "")
                if full_name and len(full_name) > 2:
                    return full_name

    # Priority 2: ownerUsername display name
    owner_display = (
        reel.get("ownerFullName")
        or reel.get("ownerName")
        or reel.get("fullName")
        or ""
    )
    if owner_display and len(owner_display) > 2:
        return owner_display

    # Priority 3: Caption — look for a name-like pattern (2+ capitalized words)
    caption = reel.get("caption", "") or ""
    name_match = re.findall(r"([A-Z][a-z]{2,}\s+[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})?)", caption)
    if name_match:
        return name_match[0]

    return reel.get("ownerUsername", "")


def resolve_channel(ig_id: str, reel: Dict) -> Tuple[str, str, bool]:
    """
    Determines the destination channel for a scraped reel.

    Args:
        ig_id : Instagram username of the account that POSTED the reel
        reel  : Full reel metadata dict from Apify

    Returns:
        (channel_folder, person_title, is_nsfw)
        - channel_folder : "General_Fallback" | "Entertainment"
        - person_title   : Human-readable name of the featured person or creator
        - is_nsfw        : True if flagged in nsfw_accounts list
    """
    cfg      = _load_identities()
    nsfw_ids = set(cfg.get("nsfw_accounts", []))
    ig_id_clean = ig_id.lower().lstrip("@")
    is_nsfw  = ig_id_clean in nsfw_ids

    # Extract best candidate title/person
    person_name = _extract_person_name(reel) or ig_id_clean

    target_channel = CHANNEL_ENTERTAINMENT if _entertainment_creds_exist() else CHANNEL_GENERAL
    logger.info("🎬 [ROUTER] Resolved channel: @%s ('%s') → %s%s", ig_id_clean, person_name, target_channel, " [NSFW]" if is_nsfw else "")
    return target_channel, person_name, is_nsfw

