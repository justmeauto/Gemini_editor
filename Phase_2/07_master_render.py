"""
Phase_2 / 07_master_render.py
=============================
Step 7: Master FFmpeg Render & QA Gate.
Executes rendering pipeline, validates output reel in `Processed Shorts/`, and logs final output metadata.
"""

import os
import sys
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger("Phase2.Step07")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def verify_master_render(
    output_path: str,
    synthesis_result: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Verifies output master reel in Processed Shorts/.
    """
    output_path = os.path.abspath(output_path)
    if not os.path.isfile(output_path):
        logger.error(f"❌ [STEP 07 FAILED] Output master reel not found: {output_path}")
        return {"success": False, "error": f"Output file missing: {output_path}"}

    file_size_mb = os.path.getsize(output_path) / (1024 * 1024)
    if file_size_mb < 0.05:
        logger.error(f"❌ [STEP 07 FAILED] Output reel file size too small ({file_size_mb:.2f} MB): {output_path}")
        return {"success": False, "error": f"File size invalid: {file_size_mb:.2f} MB"}

    logger.info(
        f"✓ [STEP 07 SUCCESS] Master Reel Render Verified: {os.path.basename(output_path)} "
        f"({file_size_mb:.2f} MB) -> {output_path}"
    )
    return {
        "success": True,
        "output_video": output_path,
        "file_size_mb": round(file_size_mb, 2),
    }
