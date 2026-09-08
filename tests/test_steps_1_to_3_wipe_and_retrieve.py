"""
tests/test_steps_1_to_3_wipe_and_retrieve.py
=============================================
Verifies Step 1 -> Step 3 Vault Storage & Retrieval lifecycle:
1. Store: Upload Raw Video, Extracted Audio, Pool Metadata JSON, and Master Vault Index to Telegram Storage Group.
2. Wipe: Delete local files from local disk to simulate lost data.
3. Retrieve: Pull deleted files back from Telegram Cloud using file_id pointers.
4. Validate: Confirm byte-for-byte & JSON integrity of restored files.
"""

import os
import sys
import json
import time
import shutil
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("test_steps_1_to_3")

from Telegram_Storage_Modules import telegram_http, telegram_vault_indexer
from Audio_Modules.audio_pool_manager import AudioPoolManager

def run_test():
    print("\n==================================================")
    print("[TEST] TESTING STEP 1 TO STEP 3: STORE -> WIPE -> RETRIEVE")
    print("==================================================")

    # Setup temporary test directories and files
    test_dir = os.path.join(os.getcwd(), "scratch", "test_vault_wipe_retrieve")
    os.makedirs(test_dir, exist_ok=True)

    dummy_video_path = os.path.join(test_dir, "test_raw_video.mp4")
    dummy_audio_path = os.path.join(test_dir, "test_extracted_audio.wav")
    dest_restored_video = os.path.join(test_dir, "restored_video.mp4")
    dest_restored_audio = os.path.join(test_dir, "restored_audio.wav")

    # Create dummy binary video and audio content (e.g. 50 KB each)
    video_bytes = b"RAW_VIDEO_STREAM_DATA_" + b"0123456789" * 5000
    audio_bytes = b"EXTRACTED_AUDIO_WAV_DATA_" + b"9876543210" * 5000

    with open(dummy_video_path, "wb") as f:
        f.write(video_bytes)
    with open(dummy_audio_path, "wb") as f:
        f.write(audio_bytes)

    # ── STEP 1: STORE ARTIFACTS IN TELEGRAM STORAGE GROUP ────────────────────────
    print("\n[STEP 1: STORE] Uploading Raw Video, Extracted Audio, and Pool Metadata to Telegram Vault...")

    # 1A. Upload Raw Video
    video_msg = telegram_http.send_document(dummy_video_path, caption="Step 1 Raw Video Clip")
    raw_video_file_id = telegram_http.extract_file_id(video_msg) if video_msg else None
    assert raw_video_file_id, "Failed to upload raw video to Telegram"
    print(f"  [OK] Raw Video Uploaded -> file_id: {raw_video_file_id[:20]}...")

    # 1B. Upload Extracted Audio
    audio_msg = telegram_http.send_document(dummy_audio_path, caption="Step 1 Extracted Audio")
    extracted_audio_file_id = telegram_http.extract_file_id(audio_msg) if audio_msg else None
    assert extracted_audio_file_id, "Failed to upload extracted audio to Telegram"
    print(f"  [OK] Extracted Audio Uploaded -> file_id: {extracted_audio_file_id[:20]}...")

    # 1C. Update & Sync Pool Metadata
    pool_mgr = AudioPoolManager()
    clip_id = f"test_clip_{int(time.time())}"
    clips = pool_mgr.metadata.setdefault("clips", {})
    clips[clip_id] = {
        "raw_video_path": dummy_video_path,
        "extracted_audio_path": dummy_audio_path,
        "raw_video_file_id": raw_video_file_id,
        "extracted_audio_file_id": extracted_audio_file_id,
        "audio_math": {"duration": 15.5, "bpm": 128.0},
        "user_id": "test_user"
    }
    pool_mgr._save_metadata()

    # Force cloud sync of pool_metadata.json
    pool_mgr._sync_to_telegram_vault()
    indexer = telegram_vault_indexer.TelegramVaultIndexer()
    pool_metadata_file_id = indexer.vault_index.get("pool_metadata_file_id")
    assert pool_metadata_file_id, "Failed to sync pool_metadata.json to Telegram!"
    print(f"  [OK] pool_metadata.json Synced -> pool_metadata_file_id: {pool_metadata_file_id[:20]}...")

    # ── STEP 2: WIPE LOCAL FILES (SIMULATE DATA LOSS) ───────────────────────────
    print("\n[STEP 2: WIPE] Deleting local files from hard drive...")
    
    os.remove(dummy_video_path)
    os.remove(dummy_audio_path)
    print(f"  [DELETED] {dummy_video_path}")
    print(f"  [DELETED] {dummy_audio_path}")
    
    assert not os.path.exists(dummy_video_path), "Video file failed to delete!"
    assert not os.path.exists(dummy_audio_path), "Audio file failed to delete!"

    # ── STEP 3: RETRIEVE FILES FROM TELEGRAM CLOUD ─────────────────────────────
    print("\n[STEP 3: RETRIEVE] Fetching deleted files back from Telegram Cloud using file_id pointers...")

    # 3A. Retrieve Raw Video by file_id
    success_video = telegram_http.download_file_by_id(raw_video_file_id, dest_restored_video)
    assert success_video and os.path.exists(dest_restored_video), "Failed to retrieve raw video!"
    print(f"  [OK] Retrieved Raw Video from Telegram -> {dest_restored_video}")

    # 3B. Retrieve Extracted Audio by file_id
    success_audio = telegram_http.download_file_by_id(extracted_audio_file_id, dest_restored_audio)
    assert success_audio and os.path.exists(dest_restored_audio), "Failed to retrieve extracted audio!"
    print(f"  [OK] Retrieved Extracted Audio from Telegram -> {dest_restored_audio}")

    # 3C. Retrieve Pool Metadata by file_id
    restored_metadata_path = os.path.join(test_dir, "restored_pool_metadata.json")
    success_meta = telegram_http.download_file_by_id(pool_metadata_file_id, restored_metadata_path)
    assert success_meta and os.path.exists(restored_metadata_path), "Failed to retrieve pool metadata!"
    print(f"  [OK] Retrieved pool_metadata.json from Telegram -> {restored_metadata_path}")

    # ── STEP 4: VERIFY INTEGRITY ────────────────────────────────────────────────
    print("\n[STEP 4: INTEGRITY VERIFICATION]")
    
    with open(dest_restored_video, "rb") as f:
        restored_video_bytes = f.read()
    assert restored_video_bytes == video_bytes, "Restored video content does not match original!"
    print(f"  [PASSED] Raw Video Byte Integrity: VERIFIED ({len(restored_video_bytes)} bytes)")

    with open(dest_restored_audio, "rb") as f:
        restored_audio_bytes = f.read()
    assert restored_audio_bytes == audio_bytes, "Restored audio content does not match original!"
    print(f"  [PASSED] Extracted Audio Byte Integrity: VERIFIED ({len(restored_audio_bytes)} bytes)")

    with open(restored_metadata_path, "r", encoding="utf-8") as f:
        restored_json = json.load(f)
    assert restored_json.get("version") == 2, "Restored JSON missing version: 2!"
    assert clip_id in restored_json.get("clips", {}), f"Clip {clip_id} missing from restored metadata!"
    print(f"  [PASSED] Pool Metadata JSON Integrity: VERIFIED (version=2, clip_id present)")

    # Cleanup temp directory
    shutil.rmtree(test_dir, ignore_errors=True)

    print("\n==================================================")
    print("[SUCCESS] ALL STEPS 1 TO 3 PASSED 100%! DATA RETRIEVAL CONFIRMED.")
    print("==================================================\n")

if __name__ == "__main__":
    run_test()
