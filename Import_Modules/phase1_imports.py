"""
Import_Modules / phase1_imports.py
===================================
Centralized System Registry for Phase 1 Ingestion Pipeline Capabilities.
All external callers (main.py, phase2_main.py, scheduled_scraper_manager.py, etc.)
import Phase 1 capabilities through THIS SINGLE CENTRAL HUB.

Exports:
  - run_phase1_ingestion / run_phase1_pipeline
  - step01_source_config (resolve_target_accounts)
  - step02_dedup_ledger (check_deduplication)
  - step03_apify_harvester (harvest_reels_from_apify)
  - step04_core_downloader (download_stream)
  - step05_proxy_encoder (encode_proxy_video)
  - step06_audio_extractor (extract_clip_audio)
  - step07_beat_analyzer (analyze_rhythm_and_beats)
"""

import sys
import os
import importlib

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SAMPLE_UPDATE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for candidate in (_REPO_ROOT, _SAMPLE_UPDATE_ROOT):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

# Central imports from Phase_1 package
from Phase_1.phase1_orchestrator import run_phase1_pipeline, run_phase1_ingestion

spec_01 = importlib.import_module("Phase_1.01_source_config")
resolve_target_accounts = spec_01.resolve_target_accounts

spec_02 = importlib.import_module("Phase_1.02_dedup_ledger")
check_deduplication = spec_02.check_deduplication

spec_03 = importlib.import_module("Phase_1.03_apify_harvester")
harvest_reels_from_apify = spec_03.harvest_reels_from_apify

spec_04 = importlib.import_module("Phase_1.04_core_downloader")
download_stream = spec_04.download_stream

spec_05 = importlib.import_module("Phase_1.05_proxy_encoder")
encode_proxy_video = spec_05.encode_proxy_video

spec_06 = importlib.import_module("Phase_1.06_audio_extractor")
extract_clip_audio = spec_06.extract_clip_audio

spec_07 = importlib.import_module("Phase_1.07_beat_analyzer")
analyze_rhythm_and_beats = spec_07.analyze_rhythm_and_beats

__all__ = [
    "run_phase1_pipeline",
    "run_phase1_ingestion",
    "resolve_target_accounts",
    "check_deduplication",
    "harvest_reels_from_apify",
    "download_stream",
    "encode_proxy_video",
    "extract_clip_audio",
    "analyze_rhythm_and_beats",
]
