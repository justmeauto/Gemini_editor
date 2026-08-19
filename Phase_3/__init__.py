"""
Phase 3 Package — Master Distribution, Publishing & Creator RAG Feedback
========================================================================
Steps:
  01_queue_ingest        : Rendered Master Ingest & Queue Manager
  02_monetization_gate   : Monetization & Safety QA Compliance Gate
  03_metadata_generator  : Viral Caption, Title & Hashtag Generator
  04_meta_publisher      : Instagram Reels & Facebook Reels Publisher
  05_tiktok_publisher    : TikTok Shorts Publisher
  06_youtube_publisher   : YouTube Shorts Publisher
  07_rag_creator_updater : Master RAG Vector Memory Committer
"""

from .phase3_orchestrator import run_phase3_orchestration, Phase3Orchestrator

__all__ = ["run_phase3_orchestration", "Phase3Orchestrator"]
