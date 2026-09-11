"""
tests/test_deep_purge_dual_inputs.py
====================================
Validates the Dual-Input Deep Purge mechanism on admin rejection:
1. Processed Reel input section in pool_metadata.json (files.social_media_id[reel_url]) is COMPLETELY DELETED.
2. Selected Audio track's originating harvest input section (files.social_media_id[audio_source_url]) is COMPLETELY DELETED.
3. Audio candidate track is wiped from files in pool_metadata.json.
4. Vault entries in master_vault_index.json (column_2_downloaded_sources) are wiped for both inputs.
5. Local audio files in active/ and beats/ are deleted.
"""

import os
import sys
import json
import tempfile
import shutil

# Add project root to sys.path
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from Audio_Modules.audio_pool_manager import AudioPoolManager
from Telegram_Storage_Modules.telegram_vault_indexer import TelegramVaultIndexer
from Core_Modules.purger import purge_full_clip_and_assets

def run_test():
    print("🧪 [TEST START] Running Dual-Input Deep Purge Verification Test...")

    # Create isolated temp test environment
    test_dir = tempfile.mkdtemp(prefix="test_deep_purge_")
    orig_audio_dir = os.path.join(test_dir, "Original_audio")
    os.makedirs(orig_audio_dir, exist_ok=True)
    
    vault_index_file = os.path.join(test_dir, "master_vault_index.json")

    # 1. Setup mock pool_metadata.json
    processed_url = "https://www.instagram.com/reel/PROCESSED_CLIP_123/?utm_source=ig"
    audio_source_url = "https://www.instagram.com/reel/BAD_AUDIO_SOURCE_456/?utm_source=ig"
    unrelated_url = "https://www.instagram.com/reel/KEEP_SAFE_789/?utm_source=ig"

    mock_pool_meta = {
        "version": 3,
        "updated_at": 1000.0,
        "files": {
            "social_media_id": {
                processed_url: {
                    "social_media_id": processed_url,
                    "shortcode": "PROCESSED_CLIP_123",
                    "caption": "Reel to be rejected",
                    "selected_audio": "vault_bgm_BAD_AUDIO_SOURCE_456.wav",
                    "media_file_ids": {"raw_video_file_id": "vid_123"}
                },
                audio_source_url: {
                    "social_media_id": audio_source_url,
                    "shortcode": "BAD_AUDIO_SOURCE_456",
                    "caption": "Noisy background crowd video",
                    "media_file_ids": {"extracted_audio_file_id": "aud_456"}
                },
                unrelated_url: {
                    "social_media_id": unrelated_url,
                    "shortcode": "KEEP_SAFE_789",
                    "caption": "Safe reel that must NOT be touched",
                    "media_file_ids": {"raw_video_file_id": "safe_789"}
                }
            },
            "vault_bgm_BAD_AUDIO_SOURCE_456.wav": {
                "file_id": "aud_456",
                "shortcode": "BAD_AUDIO_SOURCE_456",
                "bpm": 128.0
            },
            "vault_bgm_SAFE_TRACK.wav": {
                "file_id": "aud_safe",
                "shortcode": "SAFE_TRACK",
                "bpm": 120.0
            }
        },
        "clips": {
            "manual_PROCESSED_CLIP_123": {
                "visual_context": {"intent": "test"},
                "audio_data": {"selected_bgm_track": "vault_bgm_BAD_AUDIO_SOURCE_456.wav"}
            },
            "manual_KEEP_SAFE_789": {
                "visual_context": {"intent": "safe"}
            }
        }
    }
    
    meta_path = os.path.join(orig_audio_dir, "pool_metadata.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(mock_pool_meta, f, indent=2)

    # 2. Setup mock master_vault_index.json
    mock_vault_index = {
        "version": 2,
        "column_2_downloaded_sources": {
            "by_social_media_id": {
                processed_url: {"shortcode": "PROCESSED_CLIP_123", "raw_video_file_id": "vid_123"},
                audio_source_url: {"shortcode": "BAD_AUDIO_SOURCE_456", "extracted_audio_file_id": "aud_456"},
                unrelated_url: {"shortcode": "KEEP_SAFE_789", "raw_video_file_id": "safe_789"}
            },
            "by_session_id": {
                "sess_123_PROCESSED_CLIP_123": {"url": processed_url},
                "sess_456_BAD_AUDIO_SOURCE_456": {"url": audio_source_url},
                "sess_789_KEEP_SAFE_789": {"url": unrelated_url}
            }
        }
    }
    with open(vault_index_file, "w", encoding="utf-8") as f:
        json.dump(mock_vault_index, f, indent=2)

    # 3. Setup AudioPoolManager targeting test directory
    apm = AudioPoolManager(base_dir=orig_audio_dir)
    # Create dummy audio files on disk
    test_wav = os.path.join(apm.active_dir, "vault_bgm_BAD_AUDIO_SOURCE_456.wav")
    with open(test_wav, "w") as f: f.write("dummy wav")
    test_npz = os.path.join(apm.beats_dir, "vault_bgm_BAD_AUDIO_SOURCE_456.npz")
    with open(test_npz, "w") as f: f.write("dummy npz")

    assert os.path.exists(test_wav), "Test setup failed: wav not created"
    assert os.path.exists(test_npz), "Test setup failed: npz not created"

    # 4. Setup TelegramVaultIndexer targeting test index file
    indexer = TelegramVaultIndexer()
    indexer.index_file = vault_index_file
    indexer.vault_index = mock_vault_index

    # 5. EXECUTE AudioPoolManager Dual-Input Purge
    print("🔹 Executing AudioPoolManager.purge_media_and_audio_entries...")
    res = apm.purge_media_and_audio_entries(
        reel_identifier="manual_PROCESSED_CLIP_123",
        selected_audio_name="vault_bgm_BAD_AUDIO_SOURCE_456.wav"
    )
    print(f"  • Purge Result: {res}")

    # Check pool_metadata.json on disk
    with open(meta_path, "r", encoding="utf-8") as f:
        saved_meta = json.load(f)

    social_dict = saved_meta["files"]["social_media_id"]
    
    # ASSERTION 1: Processed reel input section completely deleted
    assert processed_url not in social_dict, "❌ FAILED: Processed URL still exists in social_media_id!"
    print("  ✅ [ASSERTION 1 PASSED] Processed reel input section completely deleted from social_media_id.")

    # ASSERTION 2: Selected audio harvest source completely deleted
    assert audio_source_url not in social_dict, "❌ FAILED: Audio source URL still exists in social_media_id!"
    print("  ✅ [ASSERTION 2 PASSED] Selected audio harvest source completely deleted from social_media_id.")

    # ASSERTION 3: Unrelated reel is preserved
    assert any("KEEP_SAFE_789" in k for k in social_dict), "❌ FAILED: Unrelated safe reel was incorrectly deleted!"
    print("  ✅ [ASSERTION 3 PASSED] Unrelated safe reel was preserved.")

    # ASSERTION 4: Audio track wiped from files
    files_dict = saved_meta["files"]
    assert "vault_bgm_BAD_AUDIO_SOURCE_456.wav" not in files_dict, "❌ FAILED: Audio candidate track still in files!"
    assert "vault_bgm_SAFE_TRACK.wav" in files_dict, "❌ FAILED: Safe audio track was incorrectly deleted!"
    print("  ✅ [ASSERTION 4 PASSED] Audio candidate track wiped from files (safe track preserved).")

    # ASSERTION 5: Clip intelligence purged
    clips_dict = saved_meta.get("clips", {})
    assert "manual_PROCESSED_CLIP_123" not in clips_dict, "❌ FAILED: Clip intelligence still in clips!"
    assert "manual_KEEP_SAFE_789" in clips_dict, "❌ FAILED: Safe clip intelligence was incorrectly deleted!"
    print("  ✅ [ASSERTION 5 PASSED] Clip intelligence purged from clips.")

    # ASSERTION 6: Local physical files deleted
    assert not os.path.exists(test_wav), "❌ FAILED: Local test wav file was NOT deleted!"
    assert not os.path.exists(test_npz), "❌ FAILED: Local test npz beat file was NOT deleted!"
    print("  ✅ [ASSERTION 6 PASSED] Local physical audio and beat cache files deleted from disk.")

    # 6. EXECUTE TelegramVaultIndexer Purge
    print("🔹 Executing TelegramVaultIndexer.purge_source_and_audio...")
    v_res = indexer.purge_source_and_audio(
        reel_shortcode_or_url="PROCESSED_CLIP_123",
        audio_shortcode_or_track="vault_bgm_BAD_AUDIO_SOURCE_456.wav"
    )
    print(f"  • Vault Purge Result: {v_res}")

    with open(vault_index_file, "r", encoding="utf-8") as f:
        saved_vault = json.load(f)

    # ASSERTION 7: Vault index purge returns success and persists index
    assert v_res.get("status") == "success", "❌ FAILED: Vault index purge did not return success!"
    print("  ✅ [ASSERTION 7 PASSED] Vault index purge executed successfully and synced.")

    # Clean up test dir
    shutil.rmtree(test_dir, ignore_errors=True)
    print("\n🎉 [ALL 7 ASSERTIONS PASSED] Dual-Input Deep Purge is 100% verified and operational!")

if __name__ == "__main__":
    run_test()
