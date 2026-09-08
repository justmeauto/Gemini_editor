"""
Phase_1 Package — Multi-Platform Ingestion & Pre-Processing Pipeline
=====================================================================
Sequentially indexed step modules:
  01_source_config.py      -> Step 1: Target account pool & channel resolution
  02_dedup_ledger.py       -> Step 2: Content ledger deduplication check
  03_apify_harvester.py    -> Step 3: Apify reel scraper & Gemini thumbnail prescreener
  04_core_downloader.py    -> Step 4: Multi-platform stream downloader
  05_proxy_encoder.py      -> Step 5: 480p lightweight proxy encoder
  06_audio_extractor.py    -> Step 6: Mono 16kHz WAV audio extractor
  07_beat_analyzer.py      -> Step 7: BeatEngine rhythm & drop analyzer
  phase1_orchestrator.py   -> Master orchestrator with real-time status callbacks
"""

from .phase1_orchestrator import run_phase1_pipeline, run_phase1_ingestion

__all__ = [
    "run_phase1_pipeline",
    "run_phase1_ingestion",
]
