"""
tests/test_vault_end_to_end_flow.py
======================================
End-to-End Integration Test for Steps 1 -> 2 -> 3:
  1. Clip Metadata & Ingestion Hydration in pool_metadata.json
  2. Gemini Call 1 Intelligence Generation & Merge into gemini_semantic_intelligence
  3. Telegram Vault Cloud Sync via telegram_http & Retrieval Verification by file_id
"""

import os
import sys
import json
import time
import logging

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("test_vault_flow")

def run_end_to_end_test():
    print(f"\n==================================================")
    print(f"🚀 RUNNING END-TO-END VAULT INTEGRATION TEST (STEPS 1 - 3)")
    print(f"==================================================\n")

    audio_path = os.path.join(_REPO_ROOT, "downloads", "manual_DcblNXVC4pz", "video_extracted.wav")
    assert os.path.exists(audio_path), f"Audio file not found: {audio_path}"

    # ── STEP 1: AudioPoolManager Ingestion & Metadata Hydration ─────────────
    print("🔹 [STEP 1] Testing AudioPoolManager Ingestion & Metadata Hydration...")
    from Audio_Modules.audio_pool_manager import AudioPoolManager
    pm = AudioPoolManager()
    
    # Ensure manual_DcblNXVC4pz metadata is hydrated
    files_dict = pm.metadata.get("files", {})
    clip_sources = files_dict.get("clips") or files_dict.get("social_media_id") or files_dict.get("clip_source_math", {})
    
    found_clip = None
    for url, entry in clip_sources.items():
        if "DcblNXVC4pz" in url or "DcblNXVC4pz" in str(entry.get("shortcode")):
            found_clip = entry
            break

    assert found_clip is not None, "❌ Step 1 Failed: manual_DcblNXVC4pz not found in pool_metadata.json clips!"
    assert found_clip.get("audio_math"), "❌ Step 1 Failed: audio_math is missing!"
    assert found_clip.get("whisper_transcript"), "❌ Step 1 Failed: whisper_transcript is missing!"
    print("✅ [STEP 1 PASSED] Clip metadata, audio_math, and whisper_transcript successfully hydrated in pool_metadata.json!")

    # ── STEP 2: Gemini Call 1 Musical Intelligence Report & Merge ────────────
    print("\n🔹 [STEP 2] Testing Gemini Call 1 Intelligence Generation & Pool Merge...")
    from Gemini_Modules.lyric_rhythm_aligner import analyze_music
    report = analyze_music(audio_path)
    assert report and report.get("sections"), "❌ Step 2 Failed: analyze_music returned empty report!"
    
    # Verify pool_metadata.json was updated with gemini_semantic_intelligence
    pm_fresh = AudioPoolManager()
    files_fresh = pm_fresh.metadata.get("files", {})
    fresh_sources = files_fresh.get("clips") or files_fresh.get("social_media_id") or files_fresh.get("clip_source_math", {})
    fresh_clip = None
    for url, entry in fresh_sources.items():
        if "DcblNXVC4pz" in url or "DcblNXVC4pz" in str(entry.get("shortcode")):
            fresh_clip = entry
            break

    g_intel = fresh_clip.get("gemini_semantic_audio_intelligence") or fresh_clip.get("gemini_semantic_intelligence")
    assert fresh_clip and g_intel, "❌ Step 2 Failed: gemini_semantic_audio_intelligence not saved into pool_metadata.json!"
    print(f"✅ [STEP 2 PASSED] Gemini Call 1 report merged! Transcript: '{g_intel.get('transcript')}'")

    # ── STEP 3: Telegram Vault Cloud Sync & Retrieval by file_id ────────────
    print("\n🔹 [STEP 3] Testing Telegram Vault Upload via telegram_http & Retrieval by file_id...")
    from Telegram_Storage_Modules.telegram_http import send_document, extract_file_id, download_file_by_id
    from Telegram_Storage_Modules.telegram_vault_indexer import TelegramVaultIndexer

    # Save metadata and trigger sync
    pm_fresh._save_metadata(sync_to_vault=True)

    vault_index_path = os.path.join(_REPO_ROOT, "data", "master_vault_index.json")
    with open(vault_index_path, "r", encoding="utf-8") as vf:
        v_idx = json.load(vf)

    pool_file_id = v_idx.get("pool_metadata_file_id")
    assert pool_file_id, "❌ Step 3 Failed: pool_metadata_file_id not saved in master_vault_index.json!"
    print(f"  • Captured pool_metadata_file_id: {pool_file_id}")

    # RETRIEVAL TEST: Download pool_metadata.json from Telegram by file_id
    temp_download_path = os.path.join(_REPO_ROOT, "downloads", "temp_retrieved_pool_metadata.json")
    if os.path.exists(temp_download_path):
        os.remove(temp_download_path)

    print(f"  • Requesting download from Telegram Storage Group using file_id...")
    success = download_file_by_id(pool_file_id, temp_download_path)
    assert success, "❌ Step 3 Failed: Could not download pool_metadata.json from Telegram by file_id!"
    assert os.path.exists(temp_download_path) and os.path.getsize(temp_download_path) > 100, "❌ Step 3 Failed: Downloaded file is empty or corrupted!"

    with open(temp_download_path, "r", encoding="utf-8") as df:
        retrieved_json = json.load(df)

    assert retrieved_json.get("files"), "❌ Step 3 Failed: Downloaded pool_metadata.json missing 'files' key!"
    print(f"✅ [STEP 3 PASSED] Successfully retrieved pool_metadata.json from Telegram Storage Group by file_id!")
    print(f"  • File size: {os.path.getsize(temp_download_path)} bytes")

    # Cleanup temp test file
    if os.path.exists(temp_download_path):
        os.remove(temp_download_path)

    print(f"\n==================================================")
    print(f"🎉 ALL STEPS (1, 2, 3) PASSED PERFECTLY!")
    print(f"==================================================\n")

if __name__ == "__main__":
    run_end_to_end_test()
