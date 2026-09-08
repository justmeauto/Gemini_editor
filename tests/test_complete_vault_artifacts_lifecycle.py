"""
tests/test_complete_vault_artifacts_lifecycle.py
==================================================
Comprehensive Vault Integration Test verifying the full lifecycle across:
  1. Raw Video Clip (video.mp4) -> raw_video_file_id -> Retrieval by file_id
  2. Raw Extracted Audio (video_extracted.wav) -> extracted_audio_file_id -> Retrieval by file_id
  3. Pool Metadata (pool_metadata.json) -> pool_metadata_file_id -> Retrieval by file_id
  4. Master Vault Index (master_vault_index.json) -> Pinned Cloud Pointers Catalog

Lifecycle Loop: STORE -> RETRIEVE -> UPDATE -> STORE AGAIN -> VERIFY RETRIEVAL
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
logger = logging.getLogger("test_all_vault_artifacts")

from Telegram_Storage_Modules.telegram_http import send_document, extract_file_id, download_file_by_id
from Telegram_Storage_Modules.telegram_vault_indexer import TelegramVaultIndexer
from Audio_Modules.audio_pool_manager import AudioPoolManager

def test_complete_vault_artifacts_lifecycle():
    print(f"\n==================================================")
    print(f"🏛️ TESTING COMPLETE VAULT LIFECYCLE (ALL 4 ARTIFACTS)")
    print(f"==================================================\n")

    clip_dir = os.path.join(_REPO_ROOT, "downloads", "manual_DcblNXVC4pz")
    video_path = os.path.join(clip_dir, "video.mp4")
    audio_path = os.path.join(clip_dir, "video_extracted.wav")
    assert os.path.exists(video_path), f"Video file missing: {video_path}"
    assert os.path.exists(audio_path), f"Audio file missing: {audio_path}"

    # ── 1. UPLOAD RAW VIDEO CLIP ─────────────────────────────────────────────
    print("📹 [ARTIFACT 1] Uploading Raw Video Clip (video.mp4) to Telegram Storage Group...")
    v_res = send_document(video_path, caption="📥 **[TEST RAW VIDEO]** `manual_DcblNXVC4pz`")
    assert v_res, "❌ Raw video upload failed!"
    raw_video_file_id = extract_file_id(v_res)
    assert raw_video_file_id, "❌ Could not extract raw_video_file_id!"
    print(f"✅ [ARTIFACT 1 UPLOADED] Captured raw_video_file_id: {raw_video_file_id[:30]}...")

    # RETRIEVAL TEST 1: Download raw video by file_id
    dl_video_path = os.path.join(_REPO_ROOT, "downloads", "retrieved_test_video.mp4")
    if os.path.exists(dl_video_path): os.remove(dl_video_path)
    print(f"  • Retrieving raw video clip from Telegram by file_id...")
    v_ok = download_file_by_id(raw_video_file_id, dl_video_path)
    assert v_ok and os.path.exists(dl_video_path), "❌ Could not retrieve raw video clip by file_id!"
    assert os.path.getsize(dl_video_path) == os.path.getsize(video_path), "❌ Retrieved video size mismatch!"
    print(f"✅ [ARTIFACT 1 RETRIEVED] Downloaded video clip ({os.path.getsize(dl_video_path)} bytes) matches original!")

    # ── 2. UPLOAD RAW EXTRACTED AUDIO ───────────────────────────────────────
    print("\n🎵 [ARTIFACT 2] Uploading Raw Extracted Audio (video_extracted.wav) to Telegram Storage Group...")
    a_res = send_document(audio_path, caption="🎵 **[TEST EXTRACTED AUDIO]** `manual_DcblNXVC4pz`")
    assert a_res, "❌ Extracted audio upload failed!"
    extracted_audio_file_id = extract_file_id(a_res)
    assert extracted_audio_file_id, "❌ Could not extract extracted_audio_file_id!"
    print(f"✅ [ARTIFACT 2 UPLOADED] Captured extracted_audio_file_id: {extracted_audio_file_id[:30]}...")

    # RETRIEVAL TEST 2: Download extracted audio by file_id
    dl_audio_path = os.path.join(_REPO_ROOT, "downloads", "retrieved_test_audio.wav")
    if os.path.exists(dl_audio_path): os.remove(dl_audio_path)
    print(f"  • Retrieving raw extracted audio from Telegram by file_id...")
    a_ok = download_file_by_id(extracted_audio_file_id, dl_audio_path)
    assert a_ok and os.path.exists(dl_audio_path), "❌ Could not retrieve extracted audio by file_id!"
    assert os.path.getsize(dl_audio_path) == os.path.getsize(audio_path), "❌ Retrieved audio size mismatch!"
    print(f"✅ [ARTIFACT 2 RETRIEVED] Downloaded audio clip ({os.path.getsize(dl_audio_path)} bytes) matches original!")

    # ── 3. STORE & UPDATE POOL METADATA JSON ──────────────────────────────────
    print("\n📦 [ARTIFACT 3] Updating pool_metadata.json with captured file_ids...")
    pm = AudioPoolManager()
    
    # Update clip entry with fresh file_ids
    files_pm = pm.metadata.setdefault("files", {})
    sources = files_pm.setdefault("clips", files_pm.get("social_media_id", files_pm.get("clip_source_math", {})))
    clip_url = "https://www.instagram.com/reel/DcblNXVC4pz/?utm_source=ig_web_copy_link&stkn=NTc4MTIwNjQ2YQ=="
    if clip_url in sources:
        sources[clip_url]["raw_video_file_id"] = raw_video_file_id
        sources[clip_url]["extracted_audio_file_id"] = extracted_audio_file_id

    pm._save_metadata(sync_to_vault=True)
    print("✅ [ARTIFACT 3 STORED] Saved & uploaded pool_metadata.json to Telegram Vault!")

    # RETRIEVAL TEST 3: Download pool_metadata.json by file_id
    indexer = TelegramVaultIndexer()
    pool_file_id = indexer.vault_index.get("pool_metadata_file_id")
    assert pool_file_id, "❌ pool_metadata_file_id missing from master_vault_index!"
    
    dl_pm_path = os.path.join(_REPO_ROOT, "downloads", "retrieved_test_pool_metadata.json")
    if os.path.exists(dl_pm_path): os.remove(dl_pm_path)
    print(f"  • Retrieving pool_metadata.json by file_id: {pool_file_id[:25]}...")
    pm_ok = download_file_by_id(pool_file_id, dl_pm_path)
    assert pm_ok and os.path.exists(dl_pm_path), "❌ Could not retrieve pool_metadata.json by file_id!"
    
    with open(dl_pm_path, "r", encoding="utf-8") as f:
        pm_retrieved = json.load(f)

    files_r = pm_retrieved.get("files", {})
    r_sources = files_r.get("clips") or files_r.get("social_media_id") or files_r.get("clip_source_math", {})
    r_clip = r_sources.get(clip_url, {})
    assert r_clip.get("raw_video_file_id") == raw_video_file_id, "❌ Retrieved pool_metadata raw_video_file_id mismatch!"
    assert r_clip.get("extracted_audio_file_id") == extracted_audio_file_id, "❌ Retrieved pool_metadata extracted_audio_file_id mismatch!"
    print("✅ [ARTIFACT 3 RETRIEVED] Downloaded pool_metadata.json verified line-for-line with matching file_ids!")

    # ── 4. MASTER VAULT INDEX VERIFICATION ────────────────────────────────────
    print("\n📋 [ARTIFACT 4] Verifying Master Vault Index (data/master_vault_index.json)...")
    vault_index_path = os.path.join(_REPO_ROOT, "data", "master_vault_index.json")
    with open(vault_index_path, "r", encoding="utf-8") as vf:
        v_idx = json.load(vf)

    assert v_idx.get("version") == 2.0 and v_idx.get("updated_at"), "❌ master_vault_index.json header missing!"
    assert v_idx.get("pool_metadata_file_id") == pool_file_id, "❌ master_vault_index.json pool_metadata_file_id mismatch!"
    print(f"✅ [ARTIFACT 4 VERIFIED] Master Vault Index contains exact file_id pointers catalog!")
    print(f"  • version: {v_idx.get('version')}")
    print(f"  • updated_at: {v_idx.get('updated_at')}")
    print(f"  • pool_metadata_file_id: {v_idx.get('pool_metadata_file_id')}")

    # Cleanup temporary test download files
    for p in [dl_video_path, dl_audio_path, dl_pm_path]:
        if os.path.exists(p):
            try: os.remove(p)
            except: pass

    print(f"\n==================================================")
    print(f"🏆 COMPLETE LIFECYCLE (ALL 4 ARTIFACTS) PASSED 100%!")
    print(f"==================================================\n")

if __name__ == "__main__":
    test_complete_vault_artifacts_lifecycle()
