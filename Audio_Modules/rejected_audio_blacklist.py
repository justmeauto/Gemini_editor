"""
Audio_Modules/rejected_audio_blacklist.py
==========================================
Persistent blacklist of audio tracks that were admin-rejected.

Any audio track that survives a purge attempt with blacklist=True
is recorded here by filename, audio_shortcode, AND Telegram file_id.

select_best_audio_for_clip() and get_vault_audio_pool() both call
is_blacklisted() before adding any candidate to the selection set.

This makes rejection IRREVERSIBLE regardless of:
  - Pool rebuilds
  - Vault resync from Telegram
  - Scheduler re-indexing
  - Apify re-scraping the same source
"""

import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger("rejected_audio_blacklist")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BLACKLIST_FILE = os.path.join(_REPO_ROOT, "data", "rejected_audio_blacklist.json")
_lock = threading.RLock()

# Module-level in-memory cache (loaded once per process)
_cache: Optional[Dict[str, Any]] = None


def _load() -> Dict[str, Any]:
    global _cache
    if _cache is not None:
        return _cache
    if os.path.isfile(_BLACKLIST_FILE):
        try:
            with open(_BLACKLIST_FILE, "r", encoding="utf-8") as f:
                _cache = json.load(f)
                return _cache
        except Exception as e:
            logger.warning(f"[Blacklist] Failed to load blacklist file: {e}")
    _cache = {"version": 1, "entries": {}}
    return _cache


def _save(data: Dict[str, Any]) -> None:
    global _cache
    _cache = data
    try:
        os.makedirs(os.path.dirname(_BLACKLIST_FILE), exist_ok=True)
        with open(_BLACKLIST_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"[Blacklist] Failed to save blacklist file: {e}")


def add(
    audio_filename: Optional[str],
    audio_shortcode: Optional[str] = None,
    telegram_file_id: Optional[str] = None,
    reason: str = "admin_rejected",
) -> None:
    """
    Permanently blacklist an audio track.
    Called from purge_media_and_audio_entries() on every admin rejection.

    Keyed by audio_filename (canonical). Also indexes by shortcode and file_id
    so is_blacklisted() can catch vault_bgm_*.wav mismatches.
    """
    with _lock:
        data = _load()
        entries: Dict[str, Any] = data.setdefault("entries", {})

        fname_key = os.path.basename(str(audio_filename)).lower().strip() if audio_filename else ""
        sc_key = str(audio_shortcode).lower().strip() if audio_shortcode else ""

        # Derive shortcode from filename if not explicitly given
        if not sc_key and fname_key:
            sc_key = fname_key.replace("vault_bgm_", "").replace("bgm_", "")
            for ext in (".wav", ".mp3", ".m4a", ".aac"):
                sc_key = sc_key.replace(ext, "")
            sc_key = sc_key.strip()

        if not fname_key and not sc_key:
            logger.warning("[Blacklist] add() called with no identifiable audio — skipping.")
            return

        key = fname_key or sc_key
        entries[key] = {
            "filename": fname_key,
            "shortcode": sc_key,
            "telegram_file_id": telegram_file_id or "",
            "reason": reason,
            "blacklisted_at": time.time(),
        }

        # Also index by shortcode so reverse lookups work
        if sc_key and sc_key != key:
            entries[sc_key] = entries[key]

        _save(data)
        logger.info(
            f"🚫 [Blacklist] Permanently blacklisted audio: filename='{fname_key}' "
            f"shortcode='{sc_key}' file_id='{telegram_file_id}' reason='{reason}'"
        )

        # Cloud sync to Telegram Storage Group Vault
        try:
            sync_blacklist_to_vault()
        except Exception as _sync_err:
            logger.debug("[Blacklist] Vault sync notice: %s", _sync_err)


def sync_blacklist_to_vault() -> Optional[str]:
    """
    Uploads rejected_audio_blacklist.json to Telegram Storage Group via telegram_http.py
    and updates rejected_audio_blacklist_file_id in master_vault_index.json.
    """
    try:
        if not os.path.isfile(_BLACKLIST_FILE):
            return None

        from Telegram_Storage_Modules.telegram_http import send_document, extract_file_id
        from Telegram_Storage_Modules.telegram_vault_indexer import TelegramVaultIndexer

        res = send_document(
            local_path=_BLACKLIST_FILE,
            caption=f"🚫 **[VAULT BACKUP]** `rejected_audio_blacklist.json` (Updated {time.strftime('%H:%M:%S')})"
        )
        if res:
            doc_id = extract_file_id(res)
            if doc_id:
                indexer = TelegramVaultIndexer()
                indexer.vault_index["rejected_audio_blacklist_file_id"] = doc_id
                indexer._save_local_index()
                logger.info("✅ [BLACKLIST VAULT BACKUP] Uploaded rejected_audio_blacklist.json to Telegram Storage Group (file_id: %s)", doc_id[:15])
                return doc_id
    except Exception as _e:
        logger.warning("⚠️ Notice triggering blacklist vault sync: %s", _e)
    return None


def merge_blacklist_from_file(remote_file_path: str) -> bool:
    """
    Merges an incoming rejected_audio_blacklist.json (e.g. downloaded from Telegram vault)
    into the local blacklist store.
    """
    if not os.path.isfile(remote_file_path):
        return False
    try:
        with open(remote_file_path, "r", encoding="utf-8") as f:
            incoming = json.load(f)
        if not isinstance(incoming, dict):
            return False

        incoming_entries = incoming.get("entries", {})
        if not incoming_entries:
            return True

        with _lock:
            data = _load()
            local_entries = data.setdefault("entries", {})
            for k, v in incoming_entries.items():
                if k not in local_entries:
                    local_entries[k] = v
                elif isinstance(v, dict):
                    for subk, subv in v.items():
                        if subv and not local_entries[k].get(subk):
                            local_entries[k][subk] = subv
            _save(data)
            logger.info("🔄 [Blacklist] Merged %d entries from remote vault file into local blacklist.", len(incoming_entries))
            return True
    except Exception as e:
        logger.warning("⚠️ [Blacklist] Failed to merge remote blacklist file: %s", e)
        return False


def is_blacklisted(
    audio_filename: Optional[str],
    audio_shortcode: Optional[str] = None,
    telegram_file_id: Optional[str] = None,
) -> bool:
    """
    Returns True if any of the given identifiers matches a blacklisted entry.
    Called before any audio candidate is added to the selection set.
    """
    with _lock:
        data = _load()
        entries: Dict[str, Any] = data.get("entries", {})

        if not entries:
            return False

        fname_key = os.path.basename(str(audio_filename)).lower().strip() if audio_filename else ""
        sc_key = str(audio_shortcode).lower().strip() if audio_shortcode else ""
        fid_key = str(telegram_file_id).strip() if telegram_file_id else ""

        # Derive shortcode from filename if not given
        if not sc_key and fname_key:
            sc_key = fname_key.replace("vault_bgm_", "").replace("bgm_", "")
            for ext in (".wav", ".mp3", ".m4a", ".aac"):
                sc_key = sc_key.replace(ext, "")
            sc_key = sc_key.strip()

        # Direct key hit
        if fname_key and fname_key in entries:
            return True
        if sc_key and sc_key in entries:
            return True

        # Telegram file_id hit (catches vault resync of same audio with new filename)
        if fid_key:
            for entry in entries.values():
                if isinstance(entry, dict) and entry.get("telegram_file_id") == fid_key:
                    return True

        # Substring scan (catches vault_bgm_<shortcode>.wav vs bare shortcode entries)
        for blk_key in entries:
            if blk_key and fname_key and blk_key in fname_key:
                return True
            if blk_key and sc_key and blk_key in sc_key:
                return True

        return False


def get_all() -> Dict[str, Any]:
    """Returns the full blacklist entries dict (read-only reference)."""
    with _lock:
        return dict(_load().get("entries", {}))


def get_blacklisted_shortcodes() -> Set[str]:
    """Returns a set of all blacklisted shortcodes for fast O(1) membership testing."""
    with _lock:
        data = _load()
        result: Set[str] = set()
        for entry in data.get("entries", {}).values():
            if isinstance(entry, dict):
                sc = entry.get("shortcode", "")
                fn = entry.get("filename", "")
                if sc:
                    result.add(sc)
                if fn:
                    result.add(fn)
        return result
