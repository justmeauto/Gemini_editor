"""
Phase 3 Master Orchestrator — Distribution, Publishing & Creator RAG Feedback
=============================================================================
Sequentially runs steps 01 through 07:
  01_queue_ingest        : Enqueue master rendered video
  02_monetization_gate   : QA safety & monetization check
  03_metadata_generator  : Viral title & caption generation
  04_meta_publisher      : Instagram Reels & Facebook Reels publish
  05_tiktok_publisher    : TikTok publish
  06_youtube_publisher   : YouTube Shorts publish
  07_rag_creator_updater : Commit final behavior to RAG memory
"""

import os
import time
import logging
import importlib
from typing import Dict, Any, Optional

step01 = importlib.import_module("Phase_3.01_queue_ingest")
step02 = importlib.import_module("Phase_3.02_monetization_gate")
step03 = importlib.import_module("Phase_3.03_metadata_generator")
step04 = importlib.import_module("Phase_3.04_meta_publisher")
step05 = importlib.import_module("Phase_3.05_tiktok_publisher")
step06 = importlib.import_module("Phase_3.06_youtube_publisher")
step07 = importlib.import_module("Phase_3.07_rag_creator_updater")

logger = logging.getLogger("phase3.orchestrator")

def notify_tracker(step: str, status: str, details: dict):
    """Emit status updates to live WebSocket tracker server."""
    try:
        from Main_Modules.tracker_server import broadcast_event
        broadcast_event({"phase": "phase3", "step": step, "status": status, "details": details})
    except Exception:
        pass


class Phase3Orchestrator:
    """Master Orchestrator for Phase 3 Publishing & RAG Memory Feedback."""

    def run(self, video_path: str, intelligence: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Execute full Phase 3 distribution & publishing pipeline.

        Args:
            video_path: Absolute path to rendered master video file.
            intelligence: Optional clip intelligence dictionary from Phase 2.

        Returns:
            Dict containing pipeline results, upload status, and RAG stats.
        """
        start_time = time.time()
        clip_id = os.path.basename(os.path.dirname(video_path)) or os.path.splitext(os.path.basename(video_path))[0]
        intel = intelligence or {}

        logger.info(f"\n{'='*70}\n🚀 PHASE 3 ORCHESTRATOR: Starting Distribution & RAG Loop for '{clip_id}'\n{'='*70}")

        # ── Step 01: Queue Ingest ─────────────────────────────────────────────
        notify_tracker("step_01_queue_ingest", "running", {"video_path": video_path})
        s1_res = step01.ingest_to_publish_queue(video_path, metadata=intel)
        if s1_res.get("status") == "error":
            notify_tracker("step_01_queue_ingest", "failed", s1_res)
            return {"status": "failed", "step": "01_queue_ingest", "error": s1_res.get("error")}
        notify_tracker("step_01_queue_ingest", "completed", s1_res)

        # ── Step 02: Monetization QA Gate ──────────────────────────────────────
        notify_tracker("step_02_monetization_gate", "running", {})
        s2_res = step02.verify_monetization_compliance(intel)
        if not s2_res.get("approved"):
            notify_tracker("step_02_monetization_gate", "blocked", s2_res)
            logger.warning(f"🔴 Phase 3: Clip '{clip_id}' blocked by Monetization QA Gate: {s2_res.get('reasons')}")
            return {"status": "blocked", "step": "02_monetization_gate", "compliance": s2_res}
        notify_tracker("step_02_monetization_gate", "completed", s2_res)

        # ── Step 03: Viral Metadata Generator ─────────────────────────────────
        notify_tracker("step_03_metadata_generator", "running", {})
        s3_res = step03.generate_publishing_metadata(intel, fallback_title=clip_id)
        notify_tracker("step_03_metadata_generator", "completed", s3_res)

        # ── Step 04: Meta (Instagram / Facebook) Publisher ────────────────────
        notify_tracker("step_04_meta_publisher", "running", {})
        s4_res = step04.publish_to_meta(video_path, s3_res.get("caption", ""))
        notify_tracker("step_04_meta_publisher", "completed", s4_res)

        # ── Step 05: TikTok Publisher ─────────────────────────────────────────
        notify_tracker("step_05_tiktok_publisher", "running", {})
        s5_res = step05.publish_to_tiktok(video_path, s3_res.get("title", ""), s3_res.get("caption", ""))
        notify_tracker("step_05_tiktok_publisher", "completed", s5_res)

        # ── Step 06: YouTube Shorts Publisher ─────────────────────────────────
        notify_tracker("step_06_youtube_publisher", "running", {})
        yt_payload = s3_res.get("platforms", {}).get("youtube_shorts", {})
        s6_res = step06.publish_to_youtube_shorts(
            video_path,
            title=yt_payload.get("title", s3_res.get("title", "")),
            description=yt_payload.get("description", s3_res.get("caption", "")),
            tags=yt_payload.get("tags", [])
        )
        notify_tracker("step_06_youtube_publisher", "completed", s6_res)

        # Aggregate publish results
        publish_results = {
            "meta": s4_res,
            "tiktok": s5_res,
            "youtube": s6_res
        }

        # ── Step 07: Commit to RAG Creator Vector Memory ──────────────────────
        notify_tracker("step_07_rag_creator_updater", "running", {})
        s7_res = step07.commit_rag_creator_behavior(
            clip_id=clip_id,
            video_path=video_path,
            publishing_meta=s3_res,
            publish_results=publish_results
        )
        notify_tracker("step_07_rag_creator_updater", "completed", s7_res)

        elapsed = time.time() - start_time
        logger.info(f"\n✨ PHASE 3 COMPLETED: Clip '{clip_id}' published & RAG memory updated in {elapsed:.2f}s ✨\n")

        return {
            "status": "success",
            "clip_id": clip_id,
            "video_path": video_path,
            "metadata": s3_res,
            "publish_results": publish_results,
            "rag_commit": s7_res,
            "execution_time_sec": round(elapsed, 2)
        }


def run_phase3_orchestration(video_path: str, intelligence: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Convenience wrapper for executing Phase 3 orchestration."""
    orchestrator = Phase3Orchestrator()
    return orchestrator.run(video_path, intelligence=intelligence)
