"""
scheduled_scraper_manager.py — Max 2-Account Rotating Scheduled Scraper Manager
=================================================================================
Manages scheduled account rotation for source_accounts.json:
  - Selects max 2 accounts per scheduled batch.
  - Updates source_accounts.json target list.
  - Executes Phase 1 Ingestion + Phase 2 AI Editing + Yields rendered reels one-by-one.
"""

import os
import sys
import json
import time
import logging
from typing import Dict, List, Optional, Any, Generator

logger = logging.getLogger("scheduled_scraper_manager")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACCOUNTS_JSON = os.path.join(_REPO_ROOT, "Content_Scraper_Modules", "source_accounts.json")


THIRTY_DAYS_SECONDS = 30 * 86400  # 30 Days in Seconds


def purge_expired_accounts() -> List[str]:
    """
    Checks all configured source accounts and purges any account older than 30 days.
    Returns list of removed handles.
    """
    if not os.path.exists(ACCOUNTS_JSON):
        return []

    try:
        with open(ACCOUNTS_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Support clean top-level schema (and fallback to _paparazzi)
        accs = data.get("source_accounts") or data.get("_paparazzi", {}).get("source_accounts", [])
        timestamps = data.setdefault("account_added_timestamps", {})
        now = time.time()

        expired = []
        for handle in list(accs):
            added_at = timestamps.get(handle)
            if added_at and (now - added_at) > THIRTY_DAYS_SECONDS:
                expired.append(handle)
                if handle in accs:
                    accs.remove(handle)
                timestamps.pop(handle, None)
                data.get("account_last_scraped", {}).pop(handle, None)
                data.get("account_last_scraped_iso", {}).pop(handle, None)

        if expired:
            data["source_accounts"] = accs
            with open(ACCOUNTS_JSON, "w", encoding="utf-8") as wf:
                json.dump(data, wf, indent=2, ensure_ascii=False)
            sync_source_accounts_to_telegram_vault()
            logger.info("⏰ [EXPIRATION] Purged %d expired account(s) after 30 days: %s", len(expired), expired)

        return expired
    except Exception as e:
        logger.error("❌ Error during account expiration check: %s", e)
        return []


def get_active_accounts_metadata() -> List[Dict[str, Any]]:
    """Returns list of active target accounts with creation timestamps and days remaining until 30-day limit."""
    purge_expired_accounts()
    if not os.path.exists(ACCOUNTS_JSON):
        return []
    try:
        with open(ACCOUNTS_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
        accs = data.get("source_accounts") or data.get("_paparazzi", {}).get("source_accounts", [])
        timestamps = data.get("account_added_timestamps") or data.get("_paparazzi", {}).get("account_timestamps", {})
        now = time.time()

        res = []
        for h in accs:
            added_at = timestamps.get(h, now)
            elapsed_days = int((now - added_at) / 86400)
            days_left = max(0, 30 - elapsed_days)
            res.append({
                "handle": h,
                "added_at": added_at,
                "days_elapsed": elapsed_days,
                "days_left": days_left
            })
        return res
    except Exception as e:
        logger.error("Error loading account metadata: %s", e)
        return []


def get_rotated_max_two_accounts(max_accounts: int = 2) -> List[str]:
    """
    Reads source_accounts.json and selects target accounts using anti-duplicate rotation:
    Prioritizes accounts with oldest or missing account_last_scraped timestamps so that
    6:00 AM and 7:00 PM sessions never duplicate scraping on the same account!
    """
    purge_expired_accounts()
    if not os.path.exists(ACCOUNTS_JSON):
        logger.warning(f"⚠️ {ACCOUNTS_JSON} not found. Please add target source accounts via Telegram Chat /addaccount.")
        return []

    try:
        with open(ACCOUNTS_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)

        all_accounts = data.get("source_accounts") or data.get("_paparazzi", {}).get("source_accounts", [])
        if not all_accounts:
            logger.warning("⚠️ No target source accounts configured in source_accounts.json. Use /addaccount <handle> to add accounts.")
            return []

        last_scraped_map = data.get("account_last_scraped", {})

        # Sort accounts by last scraped timestamp (0 for never scraped -> highest priority)
        sorted_accounts = sorted(all_accounts, key=lambda acc: last_scraped_map.get(acc, 0.0))

        selected = sorted_accounts[:min(max_accounts, len(sorted_accounts))]

        logger.info(f"🔄 [SCHEDULER SCRAPER] Anti-duplicate account pool selection (max {max_accounts}): selected={selected}")
        return selected
    except Exception as e:
        logger.error(f"❌ Error rotating source accounts: {e}")
        return []


def run_scheduled_scraper_batch(max_accounts: int = 2) -> List[str]:
    """
    Runs a scheduled batch with max 2 target accounts:
    1. Selects 2 target accounts.
    2. Executes Phase 1 Ingestion.
    3. Executes Phase 2 & 3 Master AI Editing.
    4. Returns list of rendered master reels.
    """
    target_accounts = get_rotated_max_two_accounts(max_accounts=max_accounts)
    logger.info(f"🚀 [SCHEDULED BATCH] Triggering Apify scraper for accounts: {target_accounts}")

    from Downloader_Modules.downloader_main import run_phase1_ingestion
    from Main_Modules.phase2_main import run_phase2_orchestration

    import random

    clips_per_run = 5
    try:
        clips_per_run = int(os.getenv("CLIPS_PER_ACCOUNT_PER_RUN", "5"))
    except ValueError:
        clips_per_run = 5

    scrape_limit = 5
    try:
        scrape_limit = int(os.getenv("APIFY_REELS_PER_ACCOUNT", "5"))
    except ValueError:
        scrape_limit = 5

    # Run ingestion for selected accounts (scrapes 5-6 clips, avoiding top 3 header clips)
    ingest_res = run_phase1_ingestion(mode="auto", limit_per_account=scrape_limit, target_accounts=target_accounts)
    downloaded_files = ingest_res.get("downloaded_files", [])
    if not ingest_res.get("success") or not downloaded_files:
        logger.warning("⚠️ [SCHEDULED BATCH] Ingestion returned 0 new clips.")
        return []

    # Target newly downloaded clip directories up to processing limit (5 clips)
    target_dirs = list(set(os.path.dirname(f) for f in downloaded_files if os.path.exists(f)))[:clips_per_run]

    # Run AI Master Editor on target downloaded clips
    phase2_res = run_phase2_orchestration(target_dirs=target_dirs, limit=clips_per_run)
    rendered_reels = phase2_res.get("rendered_files", [])
    logger.info(f"🎬 [SCHEDULED BATCH RENDER COMPLETE] Rendered {len(rendered_reels)} reel(s).")

    # Step 3: Instant Publishing of 2-3 clips with dynamic organic rotation (2 clips base + 50% chance for optional 3rd clip)
    base_pub = 2
    try:
        base_pub = int(os.getenv("PUBLISH_BATCH_SIZE", "2"))
    except ValueError:
        base_pub = 2

    # Dynamic rotation: randomly choose 2 or 3 clips to eliminate static bot behavioral footprint
    publish_count = base_pub + random.choice([0, 1])

    if rendered_reels:
        actual_pub_limit = min(publish_count, len(rendered_reels))
        logger.info(f"🎲 [ORGANIC ROTATION PUBLISHING] Publishing {actual_pub_limit} clip(s) this session (base: {base_pub}, dynamic limit: {publish_count})...")
        try:
            from Publishing_Modules.media_publisher_main import run_phase4_publishing
            from Gemini_Modules.gemini_clip_auditor import run_clip_audit_and_seo
            
            stagger_min = int(os.getenv("PUBLISH_STAGGER_MIN_SECONDS", "180"))
            stagger_max = int(os.getenv("PUBLISH_STAGGER_MAX_SECONDS", "360"))

            for idx, r_file in enumerate(rendered_reels[:actual_pub_limit]):
                if os.path.exists(r_file):
                    if idx > 0 and stagger_max > 0:
                        stagger_sec = random.randint(stagger_min, stagger_max)
                        logger.info(f"⏳ [ORGANIC PUBLISH STAGGER] Waiting {stagger_sec}s ({stagger_sec/60.0:.1f} min) before publishing clip {idx+1}/{actual_pub_limit} to simulate natural human engagement...")
                        time.sleep(stagger_sec)

                    base_name = os.path.basename(r_file)
                    parts = base_name.replace("_master.mp4", "").replace(".mp4", "").split("_")
                    raw_handle = parts[0] if parts else ""

                    from Gemini_Modules.platform_seo_generator import sanitize_raw_handles_out

                    # Run Gemini Vision Audit & Feed-Injection SEO Generation
                    # Gemini Vision & raw post metadata analyze the video to discover the real subject/star
                    audit_res = run_clip_audit_and_seo(
                        video_path=r_file,
                        creator_name="Source Content",
                        niche="fashion_lifestyle",
                        title_hint="Trending Style & Lifestyle Lookbook 🌟"
                    )
                    seo_info = audit_res.get("seo_metadata", {})
                    
                    raw_title = seo_info.get("viral_seo_title") or "Trending Fashion & Lifestyle Lookbook 🌟"
                    raw_desc = seo_info.get("description") or "Must watch viral short! 🔥\n\n#shorts #viral #trending"
                    raw_tags = " ".join(seo_info.get("hashtags", ["#viral", "#shorts", "#trending", "#fashion"]))

                    # Strictly sanitize to guarantee NO raw handle IDs appear in titles, descriptions, or hashtags
                    viral_title = sanitize_raw_handles_out(raw_title, raw_handle)
                    description = sanitize_raw_handles_out(raw_desc, raw_handle)
                    hashtags = sanitize_raw_handles_out(raw_tags, raw_handle)
                    
                    logger.info(f"📤 [SCHEDULED PUBLISH] Clip {idx+1}/{actual_pub_limit} Title: '{viral_title}'")
                    pub_res = run_phase4_publishing(
                        video_path=r_file,
                        title=viral_title,
                        description=description,
                        tags=hashtags
                    )
        except Exception as pub_err:
            logger.warning(f"⚠️ [INSTANT PUBLISHING WARNING] Could not complete automated publish step: {pub_err}")

    # Mark scraped accounts in source_accounts.json and backup to Telegram Vault
    mark_and_sync_scraped_accounts(target_accounts)

    return rendered_reels


def add_source_account(account_handle: str, platform: str = "instagram") -> bool:
    """Adds a new target account handle with creation timestamp to source_accounts.json and syncs to Telegram Vault."""
    clean_handle = account_handle.strip().lstrip("@")
    if not clean_handle:
        return False
    try:
        data = {"platform": platform, "source_accounts": []}
        if os.path.exists(ACCOUNTS_JSON):
            try:
                with open(ACCOUNTS_JSON, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {"platform": platform, "source_accounts": []}

        data["platform"] = platform
        accs = data.setdefault("source_accounts", [])
        timestamps = data.setdefault("account_added_timestamps", {})

        # Clean legacy _paparazzi if present
        data.pop("_paparazzi", None)

        if clean_handle not in accs:
            accs.append(clean_handle)
            timestamps[clean_handle] = time.time()
            with open(ACCOUNTS_JSON, "w", encoding="utf-8") as wf:
                json.dump(data, wf, indent=2, ensure_ascii=False)
            sync_source_accounts_to_telegram_vault()
            logger.info("➕ [SOURCE ACCOUNTS] Added @%s (%s) with 30-day limit to source_accounts.json & synced to Telegram Vault", clean_handle, platform)
            return True
        else:
            # Refresh timestamp on re-adding
            timestamps[clean_handle] = time.time()
            with open(ACCOUNTS_JSON, "w", encoding="utf-8") as wf:
                json.dump(data, wf, indent=2, ensure_ascii=False)
            return True
    except Exception as e:
        logger.error("❌ Failed to add source account @%s: %s", clean_handle, e)
    return False


def remove_source_account(account_handle: str) -> bool:
    """Removes a target account handle from source_accounts.json and syncs to Telegram Vault."""
    clean_handle = account_handle.strip().lstrip("@")
    if not clean_handle:
        return False
    try:
        if os.path.exists(ACCOUNTS_JSON):
            with open(ACCOUNTS_JSON, "r", encoding="utf-8") as f:
                data = json.load(f)
            accs = data.get("source_accounts", [])
            timestamps = data.get("account_added_timestamps", {})

            # Clean legacy _paparazzi
            data.pop("_paparazzi", None)

            if clean_handle in accs:
                accs.remove(clean_handle)
                timestamps.pop(clean_handle, None)
                data.get("account_last_scraped", {}).pop(clean_handle, None)
                data.get("account_last_scraped_iso", {}).pop(clean_handle, None)

                with open(ACCOUNTS_JSON, "w", encoding="utf-8") as wf:
                    json.dump(data, wf, indent=2, ensure_ascii=False)
                sync_source_accounts_to_telegram_vault()
                logger.info("🗑️ [SOURCE ACCOUNTS] Removed @%s from source_accounts.json & synced to Telegram Vault", clean_handle)
                return True
    except Exception as e:
        logger.error("❌ Failed to remove source account @%s: %s", clean_handle, e)
    return False


def mark_and_sync_scraped_accounts(scraped_accounts: Optional[List[str]] = None) -> bool:
    """
    Marks individual <account_id>: <last_scraped_timestamp> inside source_accounts.json
    so that 6 AM and 7 PM sessions avoid duplicate scraping on the same accounts.
    Saves clean schema and uploads to Telegram Storage Group Cloud Vault.
    """
    try:
        data = {}
        if os.path.exists(ACCOUNTS_JSON):
            try:
                with open(ACCOUNTS_JSON, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {}

        data.setdefault("platform", "instagram")
        data.setdefault("source_accounts", [])

        # Remove legacy _paparazzi key
        data.pop("_paparazzi", None)

        if scraped_accounts:
            now_ts = time.time()
            iso_now = time.strftime("%Y-%m-%d %H:%M:%S")

            last_scraped_map = data.setdefault("account_last_scraped", {})
            last_scraped_iso_map = data.setdefault("account_last_scraped_iso", {})

            for acc in scraped_accounts:
                last_scraped_map[acc] = now_ts
                last_scraped_iso_map[acc] = iso_now

            with open(ACCOUNTS_JSON, "w", encoding="utf-8") as wf:
                json.dump(data, wf, indent=2, ensure_ascii=False)
            logger.info("📝 [ACCOUNT SCRAPE TIMESTAMPS MARKED] Updated last_scraped timestamps for: %s", scraped_accounts)

        return sync_source_accounts_to_telegram_vault()
    except Exception as e:
        logger.error("❌ Failed to mark and sync scraped accounts: %s", e)
        return False


def sync_source_accounts_to_telegram_vault() -> bool:
    """Uploads source_accounts.json to Telegram Storage Group cloud vault and records auto_input_source_account_file_id in master_vault_index.json."""
    try:
        from Publishing_Modules.telegram_vault_indexer import TelegramVaultIndexer
        indexer = TelegramVaultIndexer()
        storage_group_id = os.getenv("TELEGRAM_STORAGE_GROUP_ID") or os.getenv("TELEGRAM_CHAT_ID")
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        if storage_group_id and bot_token and os.path.exists(ACCOUNTS_JSON):
            res = None
            try:
                from Downloader_Modules.telegram_listener import _send_file_multipart
                res = _send_file_multipart(
                    "sendDocument",
                    storage_group_id,
                    "document",
                    ACCOUNTS_JSON,
                    caption=f"📋 **[AUTO INPUT SOURCE ACCOUNTS VAULT BACKUP]** `source_accounts.json` (Updated {time.strftime('%H:%M:%S')})"
                )
            except Exception:
                res = None

            if not res:
                # Direct requests multipart upload fallback
                import requests
                url = f"https://api.telegram.org/bot{bot_token}/sendDocument"
                with open(ACCOUNTS_JSON, "rb") as f:
                    resp = requests.post(
                        url,
                        data={"chat_id": storage_group_id, "caption": f"📋 **[AUTO INPUT SOURCE ACCOUNTS VAULT BACKUP]** `source_accounts.json` (Updated {time.strftime('%H:%M:%S')})"},
                        files={"document": f},
                        timeout=30
                    )
                if resp.status_code == 200:
                    res = resp.json().get("result")

            if res and isinstance(res, dict):
                doc_id = res.get("document", {}).get("file_id") or (res.get("file_id") if "file_id" in res else None)
                if doc_id:
                    indexer.vault_index["auto_input_source_account_file_id"] = doc_id
                    indexer.vault_index["source_accounts_file_id"] = doc_id
                    indexer._save_local_index()
                    indexer.upload_and_pin_vault_index_sync()
                    logger.info("✅ [AUTO INPUT VAULT SYNC] Uploaded & PINNED source_accounts.json (auto_input_source_account_file_id: %s)", doc_id[:15])
                    return True
    except Exception as _e:
        logger.debug("Notice syncing source_accounts.json to vault: %s", _e)
    return False
