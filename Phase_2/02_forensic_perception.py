"""
Phase_2 / 02_forensic_perception.py
====================================
Step 2: Gemini Call 1 — Multimodal Forensic Perception & Frame Vector Generator.
Analyzes 480p proxy video + WAV audio + Phase 1 DSP math.
Fills visual_context, audio_data.context, and visual_vectors in ClipIntelligenceStore.
"""

import os
import sys
import logging
from typing import Dict, Any, Optional, List

logger = logging.getLogger("Phase2.Step02")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from Gemini_Modules.forensic_analyzer import ForensicVideoAnalyzer


def run_forensic_perception(
    video_path: str,
    creator_name: Optional[str] = None,
    audio_candidates: Optional[List[dict]] = None,
) -> Dict[str, Any]:
    """
    Executes Gemini Call 1.
    Returns result dictionary containing visual_context, audio_data, and visual_vectors.
    """
    logger.info(f"🔬 [STEP 02] Running Gemini Call 1 Forensic Perception for: {os.path.basename(video_path)}")
    analyzer = ForensicVideoAnalyzer()
    res = analyzer.analyze(
        video_path=video_path,
        creator_name=creator_name,
        audio_candidates=audio_candidates,
    )
    logger.info(
        f"✓ [STEP 02 SUCCESS] Forensic perception complete: "
        f"intent='{res.get('intent')}' | style='{res.get('editing_style')}'"
    )
    return res
