"""
tests/test_store_retrieve_update_store_loop.py
==================================================
Tests the exact full lifecycle loop:
  1. STORE  : Save local pool_metadata.json & upload to Telegram cloud -> get File ID #1
  2. RETRIEVE : Download from Telegram cloud using File ID #1 -> verify content matches
  3. UPDATE : Modify metadata (add new fields / update timestamp)
  4. STORE AGAIN : Re-save & re-upload updated pool_metadata.json -> get File ID #2 & verify retrieval
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
logger = logging.getLogger("test_loop")

def test_full_lifecycle_loop():
    print(f"\n==================================================")
    print(f"🔄 TESTING CYCLE: STORE -> RETRIEVE -> UPDATE -> STORE")
    print(f"==================================================\n")

    from Audio_Modules.audio_pool_manager import AudioPoolManager
    from Telegram_Storage_Modules.telegram_http import download_file_by_id

    pm = AudioPoolManager()
    vault_index_path = os.path.join(_REPO_ROOT, "data", "master_vault_index.json")

    # ── STEP 1: STORE ──────────────────────────────────────────────────────────
    print("🔹 [1. STORE] Saving pool_metadata.json and uploading to Telegram Vault...")
    pm._save_metadata(sync_to_vault=True)

    with open(vault_index_path, "r", encoding="utf-8") as vf:
        v1 = json.load(vf)

    file_id_1 = v1.get("pool_metadata_file_id")
    assert file_id_1, "❌ Step 1 (Store) Failed: pool_metadata_file_id not generated!"
    print(f"✅ [1. STORE PASSED] File ID #1 generated: {file_id_1[:25]}...")

    # ── STEP 2: RETRIEVE ──────────────────────────────────────────────────────
    print("\n🔹 [2. RETRIEVE] Downloading from Telegram Vault using File ID #1...")
    dl_path_1 = os.path.join(_REPO_ROOT, "downloads", "test_retrieve_1.json")
    if os.path.exists(dl_path_1):
        os.remove(dl_path_1)

    ok1 = download_file_by_id(file_id_1, dl_path_1)
    assert ok1 and os.path.exists(dl_path_1), "❌ Step 2 (Retrieve) Failed: Could not download File ID #1!"
    
    with open(dl_path_1, "r", encoding="utf-8") as f1:
        data1 = json.load(f1)

    assert data1.get("version") == 2 and data1.get("updated_at"), "❌ Step 2 Failed: Downloaded data missing header!"
    print(f"✅ [2. RETRIEVE PASSED] Retrieved File ID #1! version={data1['version']}, updated_at={data1['updated_at']}")

    # ── STEP 3: UPDATE ────────────────────────────────────────────────────────
    print("\n🔹 [3. UPDATE] Modifying pool_metadata content...")
    test_key = f"test_clip_{int(time.time())}"
    pm.metadata.setdefault("files", {}).setdefault("clips", {})[test_key] = {
        "social_media_id": f"https://www.instagram.com/p/{test_key}/",
        "shortcode": test_key,
        "test_status": "updated_in_loop"
    }
    print(f"  • Added test entry: '{test_key}'")
    time.sleep(1.1)  # Ensure timestamp changes

    # ── STEP 4: STORE AGAIN ───────────────────────────────────────────────────
    print("\n🔹 [4. STORE AGAIN] Re-saving updated metadata & re-uploading to Telegram Vault...")
    pm._save_metadata(sync_to_vault=True)

    with open(vault_index_path, "r", encoding="utf-8") as vf:
        v2 = json.load(vf)

    file_id_2 = v2.get("pool_metadata_file_id")
    assert file_id_2, "❌ Step 4 (Store Again) Failed: pool_metadata_file_id not updated!"
    print(f"✅ [4. STORE AGAIN PASSED] New File ID #2 generated: {file_id_2[:25]}...")

    # ── RETRIEVAL VERIFICATION OF STEP 4 ──────────────────────────────────────
    print("\n🔹 [4b. RETRIEVAL VERIFICATION] Downloading updated file using File ID #2...")
    dl_path_2 = os.path.join(_REPO_ROOT, "downloads", "test_retrieve_2.json")
    if os.path.exists(dl_path_2):
        os.remove(dl_path_2)

    ok2 = download_file_by_id(file_id_2, dl_path_2)
    assert ok2 and os.path.exists(dl_path_2), "❌ Step 4 Verification Failed: Could not download File ID #2!"

    with open(dl_path_2, "r", encoding="utf-8") as f2:
        data2 = json.load(f2)

    files_2 = data2.get("files", {})
    updated_clip = (files_2.get("clips") or files_2.get("social_media_id") or files_2.get("clip_source_math", {})).get(test_key)
    assert updated_clip and updated_clip.get("test_status") == "updated_in_loop", "❌ Step 4 Verification Failed: Updated clip data not found in retrieved file!"
    assert data2.get("updated_at") > data1.get("updated_at"), "❌ Step 4 Verification Failed: updated_at timestamp did not increment!"

    print(f"✅ [4b. VERIFICATION PASSED] Retrieved updated file! New timestamp: {data2['updated_at']}")
    print(f"  • Verified presence of test entry '{test_key}': {updated_clip}")

    # Clean up temp test files & test entry
    for p in [dl_path_1, dl_path_2]:
        if os.path.exists(p):
            os.remove(p)
    files_pm = pm.metadata.get("files", {})
    if test_key in files_pm.get("clips", {}):
        del files_pm["clips"][test_key]
        pm._save_metadata(sync_to_vault=True)

    print(f"\n==================================================")
    print(f"🎉 FULL LOOP (STORE -> RETRIEVE -> UPDATE -> STORE) PASSED 100%!")
    print(f"==================================================\n")

if __name__ == "__main__":
    test_full_lifecycle_loop()
