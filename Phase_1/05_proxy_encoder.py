"""
05_proxy_encoder.py — Phase 1 Step 5: 480p Proxy Video Encoder
===============================================================
Encodes a lightweight 480p proxy version of video.mp4:
  video.mp4 -> proxy_480p.mp4
Speeds up downstream Phase 2 Vision & Keyframe sampling.
"""

import os
import sys
import logging
from typing import Dict, Any, Optional, Callable

logger = logging.getLogger("Phase1.Step05")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def encode_proxy_video(
    video_path: str,
    callback: Optional[Callable[[str, str, Dict[str, Any]], None]] = None
) -> Dict[str, Any]:
    """
    Step 5 Execution: Encodes 480p proxy video.
    """
    if callback:
        callback("step_05", "running", {
            "message": f"Encoding 480p proxy video for '{os.path.basename(video_path)}'..."
        })

    clip_dir = os.path.dirname(video_path)
    proxy_path = os.path.join(clip_dir, "proxy_480p.mp4")

    if os.path.exists(proxy_path):
        logger.info(f"📹 [STEP 05] Proxy video already exists: {proxy_path}")
        if callback:
            callback("step_05", "success", {
                "message": "Proxy 480p video already exists. Skipping encoding.",
                "proxy_path": proxy_path
            })
        return {"step": "step_05", "status": "success", "proxy_path": proxy_path, "reused": True}

    try:
        from Main_Modules.proxy_encoder import encode_proxy
        res_proxy = encode_proxy(video_path)
        out_proxy = res_proxy if isinstance(res_proxy, str) and os.path.exists(res_proxy) else proxy_path

        logger.info(f"   ✓ [STEP 05 SUCCESS] Encoded 480p proxy -> {out_proxy}")
        if callback:
            callback("step_05", "success", {
                "message": f"Proxy 480p video encoded: {os.path.basename(out_proxy)}",
                "proxy_path": out_proxy
            })
        return {"step": "step_05", "status": "success", "proxy_path": out_proxy, "reused": False}

    except Exception as e:
        logger.warning(f"⚠️ [STEP 05 WARNING] Proxy encoding non-fatal error: {e}")
        if callback:
            callback("step_05", "success", {
                "message": f"Proxy encoding warning (non-fatal): {e}",
                "proxy_path": None
            })
        return {"step": "step_05", "status": "warning", "error": str(e)}
