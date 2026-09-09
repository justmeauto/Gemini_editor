"""
Core_Modules/disk_cleaner.py
─────────────────────────────────────────────────────────────────────────────
Zero-Leak Cloud Ephemeral Scratchpad Disk Cleaner

Responsible for wiping temporary download and render artifacts from local disk
immediately after Telegram Vault backup and user delivery:
1. downloads/<clip_id>/
2. Original_audio/beats/<clip_id>_*
3. Original_audio/active/vault_bgm_<clip_id>*

CRITICAL SAFETY GUARDS:
- NEVER deletes or modifies any intelligence index JSON:
  * pool_metadata.json
  * master_vault_index.json
  * rejected_audio_blacklist.json
  * telegram_sessions.json
  * source_accounts.json
  * telegram_users.json
  * scraper_rotation_pointer.json
- NEVER touches master audio library tracks in Original_audio/active/ (only vault_bgm_*).
- NEVER deletes entire directories outside downloads/.
─────────────────────────────────────────────────────────────────────────────
"""

import os
import shutil
import glob
import time
import logging
from typing import Dict, Any, List

logger = logging.getLogger("DiskCleaner")
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] [DiskCleaner] %(message)s"))
    logger.addHandler(_h)
    logger.setLevel(logging.INFO)

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Strict whitelist of directories that may contain ephemeral scratchpad artifacts
_DOWNLOADS_DIR = os.path.join(_REPO_ROOT, "downloads")
_BEATS_DIR = os.path.join(_REPO_ROOT, "Original_audio", "beats")
_ACTIVE_AUDIO_DIR = os.path.join(_REPO_ROOT, "Original_audio", "active")

# Protected database files that must NEVER be touched under any circumstance
_PROTECTED_FILES = {
    "pool_metadata.json",
    "master_vault_index.json",
    "rejected_audio_blacklist.json",
    "telegram_sessions.json",
    "source_accounts.json",
    "telegram_users.json",
    "scraper_rotation_pointer.json",
}


def clean_clip_artifacts(clip_id: str) -> Dict[str, Any]:
    """
    Safely purges ephemeral scratchpad artifacts for a single clip_id after delivery:
    1. downloads/<clip_id>/ (and downloads/manual_<clip_id>/)
    2. Original_audio/beats/<clip_id>_*
    3. Original_audio/active/vault_bgm_<clip_id>*

    Guaranteed not to touch user library songs or database JSONs.
    """
    if not clip_id or not str(clip_id).strip():
        return {"status": "skipped", "reason": "empty_clip_id", "deleted_items": []}

    clean_id = str(clip_id).strip().replace("manual_", "")
    patterns_to_match = [clip_id, f"manual_{clean_id}", clean_id]
    deleted_items: List[str] = []
    errors: List[str] = []

    # 1. Purge downloads/<clip_id> directories
    if os.path.isdir(_DOWNLOADS_DIR):
        for pattern in set(patterns_to_match):
            target_dir = os.path.join(_DOWNLOADS_DIR, pattern)
            if os.path.isdir(target_dir):
                try:
                    shutil.rmtree(target_dir, ignore_errors=True)
                    if not os.path.exists(target_dir):
                        deleted_items.append(f"dir:downloads/{pattern}")
                        logger.info(f"🧹 [CLEANER] Erased temporary download directory: downloads/{pattern}")
                except Exception as e:
                    errors.append(f"Failed to remove {target_dir}: {e}")

    # 2. Purge matching cache files in Original_audio/beats/
    if os.path.isdir(_BEATS_DIR):
        for pattern in set(patterns_to_match):
            beat_pattern = os.path.join(_BEATS_DIR, f"*{pattern}*")
            for fpath in glob.glob(beat_pattern):
                fname = os.path.basename(fpath)
                if fname in _PROTECTED_FILES:
                    logger.warning(f"🛡️ [CLEANER BLOCKED] Attempted to delete protected file: {fname}")
                    continue
                try:
                    if os.path.isfile(fpath):
                        os.remove(fpath)
                        deleted_items.append(f"file:Original_audio/beats/{fname}")
                        logger.info(f"🧹 [CLEANER] Erased temporary beat cache: {fname}")
                except Exception as e:
                    errors.append(f"Failed to remove {fpath}: {e}")

    # 3. Purge temporary hydrated vault BGM files in Original_audio/active/
    # STRICT GUARD: MUST start with 'vault_bgm_' to protect permanent user library songs!
    if os.path.isdir(_ACTIVE_AUDIO_DIR):
        for pattern in set(patterns_to_match):
            vault_bgm_pattern = os.path.join(_ACTIVE_AUDIO_DIR, f"vault_bgm_*{pattern}*")
            for fpath in glob.glob(vault_bgm_pattern):
                fname = os.path.basename(fpath)
                if not fname.startswith("vault_bgm_"):
                    # Safety belt
                    continue
                try:
                    if os.path.isfile(fpath):
                        os.remove(fpath)
                        deleted_items.append(f"file:Original_audio/active/{fname}")
                        logger.info(f"🧹 [CLEANER] Erased temporary hydrated vault audio: {fname}")
                except Exception as e:
                    errors.append(f"Failed to remove {fpath}: {e}")

    return {
        "status": "success",
        "clip_id": clip_id,
        "deleted_count": len(deleted_items),
        "deleted_items": deleted_items,
        "errors": errors,
    }


def sweep_stale_temp_artifacts(max_age_hours: int = 2) -> Dict[str, Any]:
    """
    Sweeps orphaned temporary artifacts left over from interrupted runs or crashes.
    Only purges files/folders older than `max_age_hours`.
    """
    cutoff_time = time.time() - (max_age_hours * 3600)
    swept_items: List[str] = []
    errors: List[str] = []

    # 1. Sweep stale downloads/ directories
    if os.path.isdir(_DOWNLOADS_DIR):
        for entry in os.listdir(_DOWNLOADS_DIR):
            entry_path = os.path.join(_DOWNLOADS_DIR, entry)
            if os.path.isdir(entry_path):
                try:
                    mtime = os.path.getmtime(entry_path)
                    if mtime < cutoff_time:
                        shutil.rmtree(entry_path, ignore_errors=True)
                        if not os.path.exists(entry_path):
                            swept_items.append(f"dir:downloads/{entry}")
                            logger.info(f"🧹 [SWEEPER] Swept stale download dir (> {max_age_hours}h): {entry}")
                except Exception as e:
                    errors.append(f"Failed sweeping {entry_path}: {e}")

    # 2. Sweep stale Original_audio/beats/ files
    if os.path.isdir(_BEATS_DIR):
        for fname in os.listdir(_BEATS_DIR):
            if fname in _PROTECTED_FILES:
                continue
            fpath = os.path.join(_BEATS_DIR, fname)
            try:
                if os.path.isfile(fpath):
                    mtime = os.path.getmtime(fpath)
                    if mtime < cutoff_time:
                        os.remove(fpath)
                        swept_items.append(f"file:beats/{fname}")
                        logger.info(f"🧹 [SWEEPER] Swept stale beat cache (> {max_age_hours}h): {fname}")
            except Exception as e:
                errors.append(f"Failed sweeping {fpath}: {e}")

    # 3. Sweep stale Original_audio/active/vault_bgm_* files
    if os.path.isdir(_ACTIVE_AUDIO_DIR):
        for fname in os.listdir(_ACTIVE_AUDIO_DIR):
            if not fname.startswith("vault_bgm_"):
                # NEVER touch master user library files!
                continue
            fpath = os.path.join(_ACTIVE_AUDIO_DIR, fname)
            try:
                if os.path.isfile(fpath):
                    mtime = os.path.getmtime(fpath)
                    if mtime < cutoff_time:
                        os.remove(fpath)
                        swept_items.append(f"file:vault_bgm/{fname}")
                        logger.info(f"🧹 [SWEEPER] Swept stale hydrated audio (> {max_age_hours}h): {fname}")
            except Exception as e:
                errors.append(f"Failed sweeping {fpath}: {e}")

    return {
        "status": "success",
        "swept_count": len(swept_items),
        "swept_items": swept_items,
        "errors": errors,
    }
