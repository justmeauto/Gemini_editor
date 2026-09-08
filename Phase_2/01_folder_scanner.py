"""
Phase_2 / 01_folder_scanner.py
==============================
Step 1: Scans `downloads/` directory or single input path for clip folders.
Verifies target video files (`video.mp4`) and metadata.
"""

import os
import sys
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger("Phase2.Step01")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def scan_clip_targets(
    input_path: Optional[str] = None,
    downloads_dir: Optional[str] = None,
    target_dirs: Optional[List[str]] = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Scans for clip targets. Returns list of target clip dicts:
    [{"dir": clip_folder_abs_path, "file": video_abs_path, "folder_name": folder_name}]
    """
    clip_targets = []

    if target_dirs:
        for t_dir in target_dirs:
            t_dir = os.path.abspath(t_dir)
            if os.path.isdir(t_dir):
                clip_targets.append({"dir": t_dir, "file": None, "folder_name": os.path.basename(t_dir)})
            elif os.path.isfile(t_dir):
                parent = os.path.dirname(t_dir)
                clip_targets.append({"dir": parent, "file": t_dir, "folder_name": os.path.basename(parent)})
        logger.info(f"📋 [STEP 01] Targeted batch mode: {len(clip_targets)} clip folder(s)")

    elif input_path:
        input_path = os.path.abspath(input_path.strip().strip("'").strip('"'))
        if input_path.endswith(".mp4d"):
            input_path = input_path[:-1]

        if "_proxy480p.mp4" in input_path:
            real_source = input_path.replace("_proxy480p.mp4", ".mp4")
            if os.path.exists(real_source):
                input_path = real_source

        if os.path.isdir(input_path):
            clip_targets.append({"dir": input_path, "file": None, "folder_name": os.path.basename(input_path)})
        elif os.path.isfile(input_path):
            parent = os.path.dirname(input_path)
            clip_targets.append({"dir": parent, "file": input_path, "folder_name": os.path.basename(parent)})
        logger.info(f"📋 [STEP 01] Single input target: {input_path}")

    else:
        if downloads_dir is None:
            downloads_dir = os.path.join(_REPO_ROOT, "downloads")
        if os.path.isdir(downloads_dir):
            for d in os.listdir(downloads_dir):
                full_d = os.path.join(downloads_dir, d)
                if os.path.isdir(full_d):
                    clip_targets.append({"dir": full_d, "file": None, "folder_name": d})
        logger.info(f"📋 [STEP 01] Scanned downloads/ folder: found {len(clip_targets)} clip subfolder(s)")

    verified_targets = []
    for item in clip_targets:
        if limit is not None and len(verified_targets) >= limit:
            break

        clip_dir = item["dir"]
        explicit_file = item["file"]
        folder_name = item["folder_name"]

        if explicit_file and os.path.isfile(explicit_file):
            video_path = explicit_file
        else:
            video_path = os.path.join(clip_dir, "video.mp4")
            if not os.path.isfile(video_path):
                mp4s = [
                    os.path.join(clip_dir, f)
                    for f in os.listdir(clip_dir)
                    if f.lower().endswith(".mp4") and not f.endswith("_proxy480p.mp4")
                ]
                if mp4s:
                    video_path = mp4s[0]
                else:
                    logger.warning(f"⚠️ [STEP 01] Skipping '{folder_name}' — no valid video file found.")
                    continue

        verified_targets.append({
            "dir": clip_dir,
            "video_path": video_path,
            "folder_name": folder_name,
            "clip_id": folder_name,
        })

    logger.info(f"✓ [STEP 01 SUCCESS] Verified {len(verified_targets)} target clip(s) for Phase 2 processing.")
    return verified_targets
