"""
04_core_downloader.py — Phase 1 Step 4: Multi-Platform Stream Downloader
==========================================================================
Executes video downloading using Downloader_Modules/downloader.py:
  - 8 yt-dlp extraction strategies
  - Tier 9 Apify fallback extraction
  - Standardizes output in downloads/{owner}_{shortcode}/video.mp4 & metadata.json
"""

import os
import sys
import json
import logging
from typing import Dict, Any, Optional, Callable

logger = logging.getLogger("Phase1.Step04")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def download_stream(
    url: str,
    destination_dir: str,
    metadata: Optional[Dict[str, Any]] = None,
    callback: Optional[Callable[[str, str, Dict[str, Any]], None]] = None
) -> Dict[str, Any]:
    """
    Step 4 Execution: Downloads video stream into target clip folder.
    """
    if callback:
        callback("step_04", "running", {
            "message": f"Downloading video stream to '{os.path.basename(destination_dir)}'..."
        })

    os.makedirs(destination_dir, exist_ok=True)
    out_video_path = os.path.join(destination_dir, "video.mp4")
    meta_path = os.path.join(destination_dir, "metadata.json")

    # ── 1. PRIMARY: Telegram Storage Group Vault Hydration ───────────────────
    try:
        from Publishing_Modules.telegram_vault_indexer import TelegramVaultIndexer
        vault = TelegramVaultIndexer()
        vault_video = vault.hydrate_raw_video_from_vault(url, destination_dir)
        if vault_video and os.path.exists(vault_video) and os.path.getsize(vault_video) > 1024:
            if metadata:
                with open(meta_path, "w", encoding="utf-8") as mf:
                    json.dump(metadata, mf, indent=2, ensure_ascii=False)
            logger.info(f"📥 [STEP 04 - PRIMARY] Hydrated raw source video directly from Telegram Storage Group Vault: {os.path.basename(destination_dir)}/video.mp4")
            res = {
                "step": "step_04",
                "status": "success",
                "video_path": vault_video,
                "metadata_path": meta_path,
                "reused_existing": True,
                "source": "telegram_storage_vault"
            }
            if callback:
                callback("step_04", "success", {
                    "message": "Raw video hydrated directly from Telegram Storage Group Vault in ~1s.",
                    "video_path": vault_video
                })
            return res
    except Exception as _ve:
        logger.debug(f"[STEP 04] Vault primary hydration notice: {_ve}")

    # ── 2. SECONDARY: Local Disk Cache Check ─────────────────────────────────
    if os.path.exists(out_video_path) and os.path.exists(meta_path) and os.path.getsize(out_video_path) > 1024:
        logger.info(f"⚡ [STEP 04 - SECONDARY] Video found in local downloads folder: {out_video_path}")
        res = {
            "step": "step_04",
            "status": "success",
            "video_path": out_video_path,
            "metadata_path": meta_path,
            "reused_existing": True,
            "source": "local_disk"
        }
        if callback:
            callback("step_04", "success", {
                "message": "Video stream already on disk. Skipping redownload.",
                "video_path": out_video_path
            })
        return res

    # ── 3. TERTIARY: External Platform Downloader (yt-dlp / Apify) ───────────

    try:
        from Downloader_Modules.downloader import download_video
        dl_res = download_video(
            url,
            custom_title="video",
            force_filename="video.mp4",
            destination_dir=destination_dir
        )
        downloaded_file = dl_res[0] if isinstance(dl_res, (tuple, list)) else dl_res

        if downloaded_file and isinstance(downloaded_file, str) and os.path.exists(downloaded_file):
            # Save metadata JSON
            if metadata:
                with open(meta_path, "w", encoding="utf-8") as mf:
                    json.dump(metadata, mf, indent=2, ensure_ascii=False)

            logger.info(f"   ✓ [STEP 04 SUCCESS] Saved -> {destination_dir}/video.mp4 & metadata.json")
            res = {
                "step": "step_04",
                "status": "success",
                "video_path": downloaded_file,
                "metadata_path": meta_path,
                "reused_existing": False
            }
            if callback:
                callback("step_04", "success", {
                    "message": f"Video stream downloaded successfully: {os.path.basename(destination_dir)}/video.mp4",
                    "video_path": downloaded_file
                })
            return res
        else:
            raise RuntimeError(f"Download completed but output file not found in {destination_dir}")

    except Exception as e:
        logger.error(f"❌ [STEP 04 FAILED] Download error for {url}: {e}")
        if callback:
            callback("step_04", "failed", {"message": f"Video stream download error: {e}"})
        return {"step": "step_04", "status": "failed", "error": str(e)}
