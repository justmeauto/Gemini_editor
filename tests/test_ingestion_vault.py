"""
tests/test_ingestion_vault.py
==============================
Test suite for Phase 1 Ingestion (Worker 1 Auto Mode & Worker 2 Manual Mode)
and Telegram Storage Vault Non-blocking Async Upload integration.
"""

import sys
import os
import time
import json
import logging
import threading

# Ensure repo root is on sys.path
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from Downloader_Modules.downloader_main import run_phase1_ingestion, send_raw_download_to_telegram_vault_async

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test_ingestion_vault")


def test_manual_input():
    """Test Worker 2: Manual video URL ingestion & non-blocking vault upload."""
    test_url = "https://www.instagram.com/reel/DcblNXVC4pz/?utm_source=ig_web_copy_link&stkn=NTc4MTIwNjQ2YQ=="
    logger.info("\n==========================================")
    logger.info("TEST 1: Manual Input Downloader (Worker 2)")
    logger.info(f"Target URL: {test_url}")
    logger.info("==========================================")

    t0 = time.time()
    result = run_phase1_ingestion(mode="manual", url=test_url, platform="instagram")
    t1 = time.time()

    logger.info(f"Manual ingestion result: {result}")
    logger.info(f"Pipeline call duration: {(t1 - t0):.2f} seconds")

    assert "success" in result, "Result must contain 'success' key"
    assert result.get("mode") == "manual", "Mode must be 'manual'"
    
    # Wait for active VaultUploader and AudioVaultUploader background daemon threads to finish HTTP uploads
    for t in threading.enumerate():
        if t.name.startswith("VaultUploader") or t.name.startswith("AudioVaultUploader"):
            t.join(timeout=20)

    # Step 1 & Step 2 Verification Assertions
    if result.get("downloaded_files"):
        clip_dir = os.path.dirname(result["downloaded_files"][0])
        meta_path = os.path.join(clip_dir, "metadata.json")
        audio_path = os.path.join(clip_dir, "audio_analysis.json")

        if os.path.exists(meta_path):
            with open(meta_path, "r", encoding="utf-8") as mf:
                mdata = json.load(mf)
            logger.info(f"📄 metadata.json raw_vault_file_id: {mdata.get('raw_vault_file_id')}")
            assert mdata.get("raw_vault_file_id"), "Step 1 Failed: raw_vault_file_id missing from metadata.json"
            assert "extracted_audio_file_id" not in mdata, "Assertion Failed: extracted_audio_file_id should not be in metadata.json"

        if os.path.exists(audio_path):
            with open(audio_path, "r", encoding="utf-8") as af:
                adata = json.load(af)
            logger.info(f"🎵 audio_analysis.json extracted_audio_file_id: {adata.get('extracted_audio_file_id')}")
            assert adata.get("extracted_audio_file_id"), "Step 2 Failed: extracted_audio_file_id missing from audio_analysis.json"

    logger.info("✓ Manual input test execution finished.")


def test_auto_input():
    """Test Worker 1: Automated account scraper & non-blocking vault upload."""
    target_account = "filmygyan"
    logger.info("\n==========================================")
    logger.info("TEST 2: Automated Account Scraper (Worker 1)")
    logger.info(f"Target Account: {target_account}")
    logger.info("==========================================")

    t0 = time.time()
    # Fetch 5 posts so pinned posts are skipped and at least 1 recent reel is ingested
    result = run_phase1_ingestion(mode="auto", target_accounts=[target_account], limit_per_account=5)
    t1 = time.time()

    logger.info(f"Auto ingestion result: {result}")
    logger.info(f"Pipeline call duration: {(t1 - t0):.2f} seconds")

    assert "success" in result, "Result must contain 'success' key"
    assert result.get("mode") == "auto", "Mode must be 'auto'"
    assert result.get("count", 0) >= 1, f"Expected at least 1 clip harvested for account {target_account}, got {result.get('count')}"

    # Wait for active VaultUploader and AudioVaultUploader background daemon threads to finish HTTP uploads
    for t in threading.enumerate():
        if t.name.startswith("VaultUploader") or t.name.startswith("AudioVaultUploader"):
            t.join(timeout=20)

    if result.get("downloaded_files"):
        clip_dir = os.path.dirname(result["downloaded_files"][0])
        meta_path = os.path.join(clip_dir, "metadata.json")
        audio_path = os.path.join(clip_dir, "audio_analysis.json")

        if os.path.exists(meta_path):
            with open(meta_path, "r", encoding="utf-8") as mf:
                mdata = json.load(mf)
            logger.info(f"📄 metadata.json raw_vault_file_id: {mdata.get('raw_vault_file_id')}")
            assert mdata.get("raw_vault_file_id"), "Step 1 Failed: raw_vault_file_id missing from metadata.json"
            assert "extracted_audio_file_id" not in mdata, "Assertion Failed: extracted_audio_file_id should not be in metadata.json"

        if os.path.exists(audio_path):
            with open(audio_path, "r", encoding="utf-8") as af:
                adata = json.load(af)
            logger.info(f"🎵 audio_analysis.json extracted_audio_file_id: {adata.get('extracted_audio_file_id')}")
            assert adata.get("extracted_audio_file_id"), "Step 2 Failed: extracted_audio_file_id missing from audio_analysis.json"

    logger.info("✓ Auto input test execution finished.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Test Ingestion & Telegram Vault Integration")
    parser.add_argument("--mode", choices=["manual", "auto", "all"], default="all", help="Test mode to execute")
    args = parser.parse_args()

    if args.mode in ["manual", "all"]:
        try:
            test_manual_input()
        except Exception as e:
            logger.error(f"Manual input test error: {e}")

    if args.mode in ["auto", "all"]:
        try:
            test_auto_input()
        except Exception as e:
            logger.error(f"Auto input test error: {e}")
