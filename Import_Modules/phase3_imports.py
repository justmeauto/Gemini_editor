"""
Import Hub for Phase 3 Package
================================
Exposes all Phase 3 steps and the master orchestrator cleanly.
"""

import importlib

# Dynamic imports for indexed step modules
m01 = importlib.import_module("Phase_3.01_queue_ingest")
m02 = importlib.import_module("Phase_3.02_monetization_gate")
m03 = importlib.import_module("Phase_3.03_metadata_generator")
m04 = importlib.import_module("Phase_3.04_meta_publisher")
m05 = importlib.import_module("Phase_3.05_tiktok_publisher")
m06 = importlib.import_module("Phase_3.06_youtube_publisher")
m07 = importlib.import_module("Phase_3.07_rag_creator_updater")
orch = importlib.import_module("Phase_3.phase3_orchestrator")

step01_queue_ingest = m01.ingest_to_publish_queue
step02_monetization_gate = m02.verify_monetization_compliance
step03_metadata_generator = m03.generate_publishing_metadata
step04_meta_publisher = m04.publish_to_meta
step05_tiktok_publisher = m05.publish_to_tiktok
step06_youtube_publisher = m06.publish_to_youtube_shorts
step07_rag_creator_updater = m07.commit_rag_creator_behavior

Phase3Orchestrator = orch.Phase3Orchestrator
run_phase3_orchestration = orch.run_phase3_orchestration

__all__ = [
    "step01_queue_ingest",
    "step02_monetization_gate",
    "step03_metadata_generator",
    "step04_meta_publisher",
    "step05_tiktok_publisher",
    "step06_youtube_publisher",
    "step07_rag_creator_updater",
    "Phase3Orchestrator",
    "run_phase3_orchestration"
]
