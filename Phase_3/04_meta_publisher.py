"""
Phase 3 — Step 04: Meta (Instagram & Facebook Reels) Publisher
==============================================================
Handles publishing to Instagram Reels and Facebook Reels via Meta Graph API.
Falls back gracefully if Meta API credentials are not configured.
"""

import os
import logging
from typing import Dict, Any

logger = logging.getLogger("phase3.step04_meta_publisher")

def publish_to_meta(
    video_path: str,
    caption: str,
    target_platform: str = "instagram_reels"
) -> Dict[str, Any]:
    """
    Publish video to Instagram Reels / Facebook Reels.

    Args:
        video_path: Path to rendered master video file.
        caption: Full text caption with hashtags.
        target_platform: 'instagram_reels' or 'facebook_reels'.

    Returns:
        Dict with status, post_id, and platform response details.
    """
    if not os.path.exists(video_path):
        return {"status": "error", "error": f"Video file not found: {video_path}"}

    # Check for Meta credentials in environment
    ig_user_id = os.getenv("INSTAGRAM_USER_ID") or os.getenv("META_IG_ACCOUNT_ID")
    access_token = os.getenv("META_ACCESS_TOKEN") or os.getenv("INSTAGRAM_ACCESS_TOKEN")

    if not ig_user_id or not access_token:
        logger.warning(f"⚠️ Step 04 Meta Publisher: Credentials not set — simulating Meta {target_platform} publish")
        return {
            "status": "simulated",
            "platform": target_platform,
            "simulated_post_id": f"sim_meta_{os.path.basename(video_path)[:10]}",
            "message": "Meta credentials missing in .env; set INSTAGRAM_USER_ID & META_ACCESS_TOKEN to enable live uploads."
        }

    try:
        from Publishing_Modules.meta_uploader import upload_to_meta_reels
        res = upload_to_meta_reels(
            video_path=video_path,
            caption=caption,
            account_id=ig_user_id,
            access_token=access_token
        )
        logger.info(f"✅ Step 04 Meta Publisher: Successfully posted to {target_platform}")
        return {"status": "published", "platform": target_platform, "response": res}
    except Exception as e:
        logger.error(f"❌ Step 04 Meta Publisher failed: {e}")
        return {"status": "error", "platform": target_platform, "error": str(e)}
