"""
Phase 3 — Step 05: TikTok Publisher
=====================================
Handles publishing short-form video content to TikTok Content Posting API.
Falls back gracefully if TikTok API credentials are not configured.
"""

import os
import logging
from typing import Dict, Any

logger = logging.getLogger("phase3.step05_tiktok_publisher")

def publish_to_tiktok(
    video_path: str,
    title: str,
    caption: str
) -> Dict[str, Any]:
    """
    Publish video to TikTok platform.

    Args:
        video_path: Path to rendered master video file.
        title: Short title.
        caption: Caption text with hashtags.

    Returns:
        Dict with status, share_id, and platform response.
    """
    if not os.path.exists(video_path):
        return {"status": "error", "error": f"Video file not found: {video_path}"}

    open_id = os.getenv("TIKTOK_OPEN_ID")
    access_token = os.getenv("TIKTOK_ACCESS_TOKEN")

    if not open_id or not access_token:
        logger.warning("⚠️ Step 05 TikTok Publisher: Credentials not set — simulating TikTok publish")
        return {
            "status": "simulated",
            "platform": "tiktok",
            "simulated_share_id": f"sim_tt_{os.path.basename(video_path)[:10]}",
            "message": "TikTok credentials missing in .env; set TIKTOK_OPEN_ID & TIKTOK_ACCESS_TOKEN for live uploads."
        }

    try:
        from Publishing_Modules.tiktok_uploader import upload_to_tiktok
        res = upload_to_tiktok(
            video_path=video_path,
            title=title,
            caption=caption,
            open_id=open_id,
            access_token=access_token
        )
        logger.info("✅ Step 05 TikTok Publisher: Successfully posted to TikTok")
        return {"status": "published", "platform": "tiktok", "response": res}
    except Exception as e:
        logger.error(f"❌ Step 05 TikTok Publisher failed: {e}")
        return {"status": "error", "platform": "tiktok", "error": str(e)}
