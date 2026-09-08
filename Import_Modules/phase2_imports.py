"""
Import_Modules / phase2_imports.py
==================================
Central Hub exporting all Phase 2 capabilities (Vision Perception, BGM Selection,
Rhythm Speed Ramps, FFmpeg Master Director Synthesis, and Rendering).

Importing from this file ensures all Phase 2 module pointers are managed in ONE SINGLE location.

Usage:
  from Import_Modules.phase2_imports import run_phase2_orchestration, edit_video_master
"""

import os
import sys
import importlib

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SAMPLE_UPDATE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for candidate in (_REPO_ROOT, _SAMPLE_UPDATE_ROOT):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

# Dynamically import indexed step modules
m01 = importlib.import_module("Phase_2.01_folder_scanner")
step01_scan_clip_targets = m01.scan_clip_targets

m02 = importlib.import_module("Phase_2.02_forensic_perception")
step02_run_forensic_perception = m02.run_forensic_perception

m03 = importlib.import_module("Phase_2.03_vector_frame_extractor")
step03_extract_targeted_frames = m03.extract_targeted_frames

m04 = importlib.import_module("Phase_2.04_bgm_selector")
step04_select_clip_bgm = m04.select_clip_bgm

m05 = importlib.import_module("Phase_2.05_rhythm_timeline")
step05_build_rhythm_timeline = m05.build_rhythm_timeline

m06 = importlib.import_module("Phase_2.06_ffmpeg_synthesis")
step06_synthesize_editing_plan = m06.synthesize_editing_plan

m07 = importlib.import_module("Phase_2.07_master_render")
step07_verify_master_render = m07.verify_master_render

# Master Orchestrators
from Phase_2.phase2_orchestrator import run_phase2_pipeline as run_phase2_orchestration
from Main_Modules.master_ai_editor import edit_video_master

__all__ = [
    "step01_scan_clip_targets",
    "step02_run_forensic_perception",
    "step03_extract_targeted_frames",
    "step04_select_clip_bgm",
    "step05_build_rhythm_timeline",
    "step06_synthesize_editing_plan",
    "step07_verify_master_render",
    "run_phase2_orchestration",
    "edit_video_master",
]
