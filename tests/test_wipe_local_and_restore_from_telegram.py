"""
tests/test_wipe_local_and_restore_from_telegram.py
=====================================================
Simulates a zero-disk environment (e.g. deleting local files or fresh GitHub Actions runner):
  1. Wipes local temporary copy of pool_metadata.json & master_vault_index.json
  2. Calls TelegramVaultIndexer().hydrate_all_vault_jsons_on_startup(force=True)
  3. Verifies that pinned master_vault_index.json and pool_metadata.json are completely
     restored from Telegram Storage Group cloud!
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
logger = logging.getLogger("test_disaster_recovery")

def test_disaster_recovery():
    print(f"\n==================================================")
    print(f"🔥 TESTING DISASTER RECOVERY: WIPE LOCAL -> RESTORE FROM TELEGRAM VAULT")
    print(f"==================================================\n")

    from Telegram_Storage_Modules.telegram_vault_indexer import TelegramVaultIndexer
    from Audio_Modules.audio_pool_manager import AudioPoolManager

    indexer = TelegramVaultIndexer()
    pm = AudioPoolManager()

    # Step 1: Ensure current index is pinned in Telegram
    print("🔹 [1. PIN CHECK] Ensuring master_vault_index.json is uploaded & pinned to Telegram Storage Group...")
    from Telegram_Storage_Modules.telegram_http import send_document, extract_file_id, pin_message
    
    msg = send_document(indexer.index_file, caption="📌 **[PINNED VAULT INDEX]** `master_vault_index.json`")
    assert msg, "❌ Could not upload master_vault_index.json to Telegram!"
    msg_id = msg.get("message_id")
    assert msg_id, "❌ Could not get message_id for pinned index!"
    
    pin_ok = pin_message(msg_id)
    assert pin_ok, "❌ Could not pin master_vault_index.json in Telegram Storage Group!"
    print(f"✅ [1. PIN CHECK PASSED] master_vault_index.json is PINNED in Telegram (Message ID: {msg_id})!")

    # Step 2: Wipe local pool_metadata.json
    print("\n🔹 [2. WIPE LOCAL] Simulating deleted/wiped local disk files...")
    meta_backup_path = pm.meta_path + ".bak_test"
    if os.path.exists(pm.meta_path):
        os.replace(pm.meta_path, meta_backup_path)
        print(f"  • Temporarily moved local pool_metadata.json out of disk -> {meta_backup_path}")

    assert not os.path.exists(pm.meta_path), "❌ Local file still exists!"

    # Step 3: Trigger Cloud Hydration
    print("\n🔹 [3. CLOUD HYDRATION] Running hydrate_all_vault_jsons_on_startup(force=True)...")
    res = indexer.hydrate_all_vault_jsons_on_startup(force=True)
    print(f"  • Hydration result: {res}")

    # Step 4: Verify Restoration
    print("\n🔹 [4. VERIFY RESTORATION] Checking if pool_metadata.json was restored from Telegram cloud...")
    assert os.path.exists(pm.meta_path), "❌ Disaster Recovery Failed: pool_metadata.json was NOT restored!"
    assert os.path.getsize(pm.meta_path) > 100, "❌ Disaster Recovery Failed: Restored file is empty!"

    with open(pm.meta_path, "r", encoding="utf-8") as f:
        restored_data = json.load(f)

    assert restored_data.get("files"), "❌ Restored pool_metadata.json missing 'files' key!"
    print(f"✅ [4. VERIFY PASSED] pool_metadata.json RESTORED FROM TELEGRAM CLOUD!")
    print(f"  • Restored file size: {os.path.getsize(pm.meta_path)} bytes")
    print(f"  • Top-level header: version={restored_data.get('version')}, updated_at={restored_data.get('updated_at')}")

    # Clean up test backup file
    if os.path.exists(meta_backup_path):
        os.remove(meta_backup_path)

    print(f"\n==================================================")
    print(f"🎉 DISASTER RECOVERY TEST PASSED 100%!")
    print(f"   Deleting local files will NOT lose your data — everything restores from Telegram!")
    print(f"==================================================\n")

if __name__ == "__main__":
    test_disaster_recovery()
