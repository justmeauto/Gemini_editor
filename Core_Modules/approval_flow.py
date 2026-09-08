"""
Core_Modules / approval_flow.py
================================
Escalating retry policy & human-in-the-loop approval workflow manager.

Features:
  - Bounded Escalating Retries (Standard -> Aggressive -> Deep Scan -> Hard Stop at Max 2)
  - Pre-Publish Watermark QA Verification
  - DRY_RUN Publishing Preview
"""

import logging
from typing import Dict, Any, Tuple
from Core_Modules.session_manager import MAX_RETRIES

logger = logging.getLogger("core.approval_flow")

# Escalating retry modes
RETRY_MODE_MAP = {
    0: "standard_perception",
    1: "aggressive_pacing",   # Retry 1: increase cut density & fast speed ramps
    2: "deep_scan_frames"     # Retry 2: high optical-flow density & alternate BGM selection
}

def get_escalated_retry_mode(current_retry_count: int) -> Tuple[bool, str]:
    """
    Determine if a retry is allowed and return the escalated retry mode.

    Args:
        current_retry_count: Current number of attempts performed so far.

    Returns:
        Tuple of (allowed: bool, mode_name: str)
    """
    next_retry = current_retry_count + 1
    if next_retry > MAX_RETRIES:
        logger.warning(f"⛔ ApprovalFlow: Hard retry cap reached ({current_retry_count}/{MAX_RETRIES}). Stopping retries.")
        return False, "max_retries_exceeded"

    mode = RETRY_MODE_MAP.get(next_retry, "aggressive_pacing")
    logger.info(f"🔄 ApprovalFlow: Escalating to retry {next_retry}/{MAX_RETRIES} (mode={mode})")
    return True, mode


def verify_watermark_approval(intelligence: Dict[str, Any]) -> Dict[str, Any]:
    """
    Isolate pre-publish watermark check to let human catch uncleaned overlays.

    Args:
        intelligence: Clip intelligence record.

    Returns:
        Dict with watermark approval status and clean state flag.
    """
    watermarks = intelligence.get("watermarks", [])
    has_watermarks = len(watermarks) > 0

    if has_watermarks:
        logger.warning(f"⚠️ ApprovalFlow: {len(watermarks)} watermarks detected in clip")
        return {
            "clean": False,
            "watermark_count": len(watermarks),
            "watermarks": watermarks,
            "action": "human_verify_required"
        }

    return {
        "clean": True,
        "watermark_count": 0,
        "watermarks": [],
        "action": "approved"
    }


def generate_dry_run_preview(video_path: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
    """
    Generate a DRY_RUN publishing preview card without modifying live social APIs.

    Args:
        video_path: Video file path.
        metadata: Generated titles and captions.

    Returns:
        Dict containing DRY_RUN preview card payload.
    """
    title = metadata.get("title", "Untitled Short")
    caption = metadata.get("caption", "")
    platforms = list(metadata.get("platforms", {}).keys()) or ["youtube_shorts", "instagram_reels", "tiktok"]

    preview_text = (
        f"🧪 [DRY_RUN PREVIEW CARD]\n"
        f"📌 Title: {title}\n"
        f"📝 Caption:\n{caption[:150]}...\n"
        f"🌐 Platforms Ready: {', '.join(platforms)}"
    )

    logger.info("🧪 ApprovalFlow: Generated DRY_RUN publishing preview")

    return {
        "mode": "DRY_RUN",
        "video_path": video_path,
        "title": title,
        "preview_text": preview_text,
        "platforms": platforms
    }
