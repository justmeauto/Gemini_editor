"""
Core_Modules / purger.py
=======================
Complete Clip & Asset Purger.
When a user rejects or deletes a clip:
1. Deletes rendered master video file from Processed Shorts/
2. Deletes all intermediate FFmpeg step files (*.mp4) related to the clip
3. Deletes source download folder (downloads/<clip_id>/ containing video.mp4, proxies, extracted audio WAVs, beat analysis)
4. Purges clip metadata index from pool_metadata.json
"""

import os
import shutil
import logging
import glob
from typing import Dict, Any, Optional
from Gemini_Modules.clip_intelligence_store import ClipIntelligenceStore

logger = logging.getLogger("core.purger")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def purge_full_clip_and_assets(
    clip_id: Optional[str] = None,
    video_path: Optional[str] = None,
    attempt_history: Optional[list] = None
) -> Dict[str, Any]:
    """
    Completely purges a clip and all related assets:
      - Master rendered video (.mp4)
      - Intermediate FFmpeg step files
      - Source download directory (downloads/<clip_id>/)
      - Extracted audio files & beat analysis JSONs
      - Clip intelligence index from pool_metadata.json

    Returns dict summarizing purge results.
    """
    purged_items = []
    
    # 1. Determine clip_id and paths
    if not clip_id and video_path:
        base_name = os.path.basename(video_path)
        clip_id = base_name.replace("_master.mp4", "").replace(".mp4", "")

    if not clip_id:
        logger.warning("⚠️ Purger: No clip_id or video_path provided for purge.")
        return {"status": "error", "error": "No clip_id provided"}

    logger.info(f"🗑️ Purger: Initiating full asset & metadata purge for clip '{clip_id}'...")

    # 2. Delete main video_path and all attempt_history files
    paths_to_delete = set()
    if video_path and os.path.exists(video_path):
        paths_to_delete.add(video_path)
    if attempt_history:
        for p in attempt_history:
            if p and os.path.exists(p):
                paths_to_delete.add(p)

    for p in paths_to_delete:
        try:
            os.remove(p)
            purged_items.append(f"Video file: {os.path.basename(p)}")
            logger.info(f"🗑️ Purger: Deleted video file {p}")
        except Exception as e:
            logger.warning(f"⚠️ Purger: Failed to delete video file {p}: {e}")

    # 3. Delete intermediate FFmpeg step files in Processed Shorts/
    processed_dir = os.path.join(_REPO_ROOT, "Processed Shorts")
    if os.path.exists(processed_dir):
        step_pattern = os.path.join(processed_dir, f"*{clip_id}*.mp4")
        for step_file in glob.glob(step_pattern):
            try:
                os.remove(step_file)
                purged_items.append(f"Intermediate step: {os.path.basename(step_file)}")
                logger.info(f"🗑️ Purger: Deleted intermediate step file {step_file}")
            except Exception as e:
                logger.warning(f"⚠️ Purger: Failed to delete step file {step_file}: {e}")

    # 4. Delete source download folder (downloads/<clip_id>/)
    downloads_dir = os.path.join(_REPO_ROOT, "downloads")
    source_folder = os.path.join(downloads_dir, clip_id)
    if not os.path.exists(source_folder):
        # Search for matching folder name in downloads
        for entry in os.listdir(downloads_dir):
            if clip_id in entry or entry in clip_id:
                source_folder = os.path.join(downloads_dir, entry)
                break

    if os.path.exists(source_folder) and os.path.isdir(source_folder):
        try:
            shutil.rmtree(source_folder)
            purged_items.append(f"Download directory: downloads/{os.path.basename(source_folder)}")
            logger.info(f"🗑️ Purger: Deleted download directory {source_folder}")
        except Exception as e:
            logger.warning(f"⚠️ Purger: Failed to delete download directory {source_folder}: {e}")

    # 5. Purge clip index from pool_metadata.json
    try:
        store = ClipIntelligenceStore()
        store.purge_clip(clip_id, clip_folder=source_folder)
        purged_items.append("Metadata index in pool_metadata.json")
    except Exception as e:
        logger.warning(f"⚠️ Purger: Failed to purge metadata index from pool_metadata.json: {e}")

    return {
        "status": "success",
        "clip_id": clip_id,
        "purged_count": len(purged_items),
        "purged_items": purged_items
    }
