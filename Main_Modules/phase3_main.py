"""
phase3_main.py — Standalone Entry Point for Phase 3
===================================================
Executes Phase 3 Publishing, Distribution & Creator RAG Feedback Loop.

Usage:
    python phase3_main.py "Processed Shorts/manual_manual_1785614074_master.mp4"
"""

import os
import sys
import logging
import argparse

# Ensure the canonical workspace root is on sys.path before the sample tree.
# This lets imports resolve to the real AMTCE modules instead of the nested
# shadow copies under `simpler update/`.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SAMPLE_UPDATE_ROOT = os.path.dirname(os.path.abspath(__file__))

# Remove any direct sample-tree path entries that would otherwise shadow the
# real package roots. We then place the canonical repo root first and keep the
# sample tree available only as a fallback.
for shadow_path in [p for p in sys.path if os.path.abspath(p) == os.path.abspath(_SAMPLE_UPDATE_ROOT)]:
    sys.path.remove(shadow_path)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
if _SAMPLE_UPDATE_ROOT not in sys.path:
    sys.path.append(_SAMPLE_UPDATE_ROOT)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger("phase3_main")

def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Phase 3 Master Publishing & RAG Memory Entry Point")
    parser.add_argument("video_path", type=str, help="Path to rendered master video file")
    args = parser.parse_args()

    v_path = os.path.abspath(args.video_path)
    if not os.path.exists(v_path):
        logger.error(f"❌ Video file not found: {v_path}")
        sys.exit(1)

    from Import_Modules.phase3_imports import run_phase3_orchestration
    res = run_phase3_orchestration(v_path)
    
    print("\n" + "="*70)
    print("✨ PHASE 3 EXECUTION SUMMARY ✨")
    print(f"Status           : {res.get('status')}")
    print(f"Clip ID          : {res.get('clip_id')}")
    print(f"Title            : {res.get('metadata', {}).get('title')}")
    print(f"RAG Committed    : {res.get('rag_commit', {}).get('rag_committed')}")
    print(f"Execution Time   : {res.get('execution_time_sec')}s")
    print("="*70 + "\n")

if __name__ == "__main__":
    main()
