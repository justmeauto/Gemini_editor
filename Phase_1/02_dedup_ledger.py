"""
02_dedup_ledger.py — Phase 1 Step 2: Content Deduplication & Disk Checker
===========================================================================
Checks if a reel shortcode or URL has already been processed or downloaded
using:
  - Telegram Storage Group Vault (primary)
  - Local disk existence check in downloads/{owner}_{shortcode}/ (secondary)
  - Direct scan of Original_audio/pool_metadata.json by shortcode/URL (tertiary)
  - Published registry / queue files (quaternary)
"""

import os
import sys
import json
import logging
from typing import Dict, Any, Optional, Callable

logger = logging.getLogger("Phase1.Step02")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def check_deduplication(
    shortcode: str,
    owner: str = "actress",
    downloads_dir: Optional[str] = None,
    callback: Optional[Callable[[str, str, Dict[str, Any]], None]] = None
) -> Dict[str, Any]:
    """
    Step 2 Execution: Verifies if clip shortcode is clean/unique or already on disk.
    """
    if callback:
        callback("step_02", "running", {"message": f"Checking deduplication for shortcode '{shortcode}'..."})

    if not downloads_dir:
        downloads_dir = os.path.join(_REPO_ROOT, "downloads")

    clip_folder_name = f"{owner}_{shortcode}"
    clip_dir = os.path.join(downloads_dir, clip_folder_name)

    meta_path = os.path.join(clip_dir, "metadata.json")
    video_path = os.path.join(clip_dir, "video.mp4")

    # 1. PRIMARY: Telegram Storage Group Vault check
    vault_hit = None
    try:
        from Telegram_Storage_Modules.telegram_vault_indexer import TelegramVaultIndexer
        vault = TelegramVaultIndexer()
        # Try direct URL form first (most reliable match)
        ig_url = f"https://www.instagram.com/reel/{shortcode}/"
        vault_hit = vault.lookup_downloaded_source(ig_url) or vault.lookup_downloaded_source(shortcode)
        # Also check Column 2 by_session_id directly
        if not vault_hit:
            c2_sess = vault.vault_index.get("column_2_downloaded_sources", {}).get("by_session_id", {})
            for sess_id, entry in c2_sess.items():
                if shortcode in sess_id or shortcode in str(entry.get("social_media_id", "")):
                    vault_hit = entry
                    break
    except Exception as _ve:
        logger.debug(f"[STEP 02] Vault dedup check notice: {_ve}")


    # 2. SECONDARY: Local Disk presence check
    already_on_disk = os.path.exists(meta_path) and os.path.exists(video_path)

    # Extract clean normalized shortcode for high-precision matching
    clean_sc = str(shortcode or "").strip()
    if "/" in clean_sc:
        import re
        m = re.search(r"/(?:reel|reels|p)/([A-Za-z0-9_-]+)", clean_sc)
        if m:
            clean_sc = m.group(1)
        else:
            clean_sc = clean_sc.rstrip("/").split("/")[-1]
    if clean_sc.startswith("manual_"):
        clean_sc = clean_sc[7:]
    clean_sc_lower = clean_sc.lower()

    # 3. TERTIARY: Scan pool_metadata.json schema (files.social_media_id, clips)
    pool_hit = False
    try:
        pool_path = os.path.join(_REPO_ROOT, "Original_audio", "pool_metadata.json")
        if os.path.exists(pool_path):
            with open(pool_path, "r", encoding="utf-8") as pf:
                pool_data = json.load(pf)

            files_dict = pool_data.get("files", {}) if isinstance(pool_data, dict) else {}
            social_dict = files_dict.get("social_media_id", {}) if isinstance(files_dict, dict) else {}
            clips_dict = pool_data.get("clips", {}) if isinstance(pool_data, dict) else {}

            candidate_entries = []
            if isinstance(social_dict, dict):
                for k, v in social_dict.items():
                    if isinstance(v, dict):
                        candidate_entries.append((str(k), v))
                    elif isinstance(v, str):
                        candidate_entries.append((str(k), {"shortcode": v}))
            if isinstance(clips_dict, dict):
                for k, v in clips_dict.items():
                    if isinstance(v, dict):
                        candidate_entries.append((str(k), v))
            if isinstance(files_dict, dict):
                for k, v in files_dict.items():
                    if k != "social_media_id" and isinstance(v, dict):
                        candidate_entries.append((str(k), v))

            ig_url_core = f"/reel/{clean_sc_lower}"
            ig_p_core = f"/p/{clean_sc_lower}"

            for key_str, entry in candidate_entries:
                sc = str(entry.get("shortcode") or entry.get("shortCode") or "").lower().strip()
                url_val = str(entry.get("url") or entry.get("videoUrl") or entry.get("social_media_id") or "").lower()
                k_lower = key_str.lower()

                if (
                    (clean_sc_lower and clean_sc_lower == sc)
                    or (clean_sc_lower and (clean_sc_lower in k_lower or clean_sc_lower in url_val))
                    or (clean_sc_lower and (ig_url_core in k_lower or ig_p_core in k_lower or ig_url_core in url_val or ig_p_core in url_val))
                ):
                    pool_hit = True
                    logger.info(f"📋 [STEP 02 - TERTIARY] Shortcode '{shortcode}' found in pool_metadata.json. Flagging as duplicate.")
                    break
    except Exception as e:
        logger.debug(f"[STEP 02] pool_metadata scan warning: {e}")

    # 4. QUATERNARY: Published Registry / Queue files check
    published_check = False
    for p_file in [
        os.path.join(_REPO_ROOT, "data", "published_registry.json"),
        os.path.join(_REPO_ROOT, "published_registry.json"),
        os.path.join(_REPO_ROOT, "data", "publish_queue.json"),
    ]:
        if os.path.exists(p_file):
            try:
                with open(p_file, "r", encoding="utf-8") as f:
                    p_content = f.read()
                    if shortcode in p_content or (clean_sc and clean_sc in p_content):
                        published_check = True
                        break
            except Exception:
                pass

    # 5. QUINARY: Content Ledger (military-grade dedup) check
    ledger_hit = False
    try:
        from Content_Scraper_Modules.content_ledger import get_ledger
        ledger = get_ledger()
        if clean_sc and ledger.shortcode_seen(clean_sc):
            ledger_hit = True
            logger.info(f"📜 [STEP 02 - QUINARY] Shortcode '{shortcode}' found in ContentLedger. Flagging as duplicate.")
    except Exception as _cle:
        logger.debug(f"[STEP 02] ContentLedger scan notice: {_cle}")

    is_duplicate = bool(vault_hit) or already_on_disk or pool_hit or published_check or ledger_hit

    res = {
        "step": "step_02",
        "shortcode": shortcode,
        "is_duplicate": is_duplicate,
        "in_vault": bool(vault_hit),
        "already_on_disk": already_on_disk,
        "clip_dir": clip_dir,
        "video_path": video_path if already_on_disk else None,
        "vault_entry": vault_hit
    }

    if vault_hit:
        logger.info(f"🏛️ [STEP 02 - PRIMARY] Found '{shortcode}' in Telegram Storage Group Vault! Will hydrate from Telegram lake.")
        if callback:
            callback("step_02", "success", {
                "message": f"Clip '{shortcode}' indexed in Telegram Storage Vault. Ready for instant hydration.",
                "is_duplicate": True,
                "in_vault": True,
                "clip_dir": clip_dir
            })
    elif is_duplicate:
        logger.info(f"♻️ [STEP 02 - SECONDARY] Shortcode '{shortcode}' exists locally on disk in downloads/. Skipping scraper.")
        if callback:
            callback("step_02", "success", {
                "message": f"Clip '{shortcode}' exists locally on disk in downloads/. Skipping fetch.",
                "is_duplicate": True,
                "clip_dir": clip_dir
            })
    else:
        logger.info(f"✨ [STEP 02] Shortcode '{shortcode}' is NEW & clean to download.")
        if callback:
            callback("step_02", "success", {
                "message": f"Clip '{shortcode}' verified unique and ready for download.",
                "is_duplicate": False,
                "clip_dir": clip_dir
            })

    return res
