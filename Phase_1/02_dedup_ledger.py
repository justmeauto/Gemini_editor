"""
02_dedup_ledger.py — Phase 1 Step 2: Content Deduplication & Disk Checker
===========================================================================
Checks if a reel shortcode or URL has already been processed or downloaded
using:
  - Content_Scraper_Modules/content_ledger.py
  - Local disk existence check in downloads/{owner}_{shortcode}/
"""

import os
import sys
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
        from Publishing_Modules.telegram_vault_indexer import TelegramVaultIndexer
        vault = TelegramVaultIndexer()
        vault_hit = vault.lookup_downloaded_source(shortcode)
    except Exception as _ve:
        logger.debug(f"[STEP 02] Vault dedup check notice: {_ve}")

    # 2. SECONDARY: Local Disk presence check
    already_on_disk = os.path.exists(meta_path) and os.path.exists(video_path)

    # 3. TERTIARY: Content Ledger check
    ledger_processed = False
    try:
        from Content_Scraper_Modules.content_ledger import get_ledger
        ledger = get_ledger()
        if hasattr(ledger, "is_downloaded") and callable(ledger.is_downloaded):
            ledger_processed = ledger.is_downloaded(shortcode)
    except Exception as e:
        logger.debug(f"Ledger check warning: {e}")

    is_duplicate = bool(vault_hit) or already_on_disk or ledger_processed

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
