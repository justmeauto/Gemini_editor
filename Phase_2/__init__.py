"""
Phase_2 package initialization.
Sequentially indexed Phase 2 Modules (01 -> 07):
  01_folder_scanner.py          - Step 1: Target Scanner & Clip Verification
  02_forensic_perception.py     - Step 2: Gemini Call 1 (Vision, Audio Context & Vectors)
  03_vector_frame_extractor.py  - Step 3: OpenCV Vector-Guided Frame Extraction
  04_bgm_selector.py            - Step 4: Gemini Call 2 (Pooled Audio RAG BGM Selector)
  05_rhythm_timeline.py         - Step 5: Rhythm & Micro-Shot Timeline Builder
  06_ffmpeg_synthesis.py        - Step 6: Gemini Call 3 (FFmpeg Editing Plan Generator)
  07_master_render.py           - Step 7: Master FFmpeg Render & QA Gate
  phase2_orchestrator.py        - Master Phase 2 Pipeline Runner
"""
