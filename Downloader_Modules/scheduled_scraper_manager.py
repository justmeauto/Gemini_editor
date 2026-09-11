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
DATA_DIR = os.path.join(_REPO_ROOT, "data")
os.makedirs(DATA_DIR, exist_ok=True)


THIRTY_DAYS_SECONDS = 30 * 86400  # 30 Days in Seconds

DEFAULT_SOURCE_ACCOUNTS = ["glitz.in", "mobile_multiplex", "papsdesk", "justbollywood.in"]


def _save_accounts_json(data: Dict[str, Any]) -> bool:
    """Atomically saves data to source_accounts.json using a temp file."""
    try:
        os.makedirs(os.path.dirname(ACCOUNTS_JSON), exist_ok=True)
        tmp_path = ACCOUNTS_JSON + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as wf:
            json.dump(data, wf, indent=2, ensure_ascii=False)
        os.replace(tmp_path, ACCOUNTS_JSON)
        return True
    except Exception as e:
        logger.error("❌ Failed to save source_accounts.json: %s", e)
        return False


def _load_accounts_json() -> Dict[str, Any]:
    """Loads source_accounts.json with automatic repair on JSON corruption."""
    if os.path.exists(ACCOUNTS_JSON):
        try:
            with open(ACCOUNTS_JSON, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and "source_accounts" in data:
                    return data
        except Exception as e:
            logger.error("❌ JSON error reading %s: %s. Rebuilding with default accounts...", ACCOUNTS_JSON, e)

    default_data = {
        "platform": "instagram",
        "source_accounts": list(DEFAULT_SOURCE_ACCOUNTS),
        "account_added_timestamps": {acc: time.time() for acc in DEFAULT_SOURCE_ACCOUNTS},
        "account_last_scraped": {},
        "account_last_scraped_iso": {}
    }
    _save_accounts_json(default_data)
    return default_data


def purge_expired_accounts() -> List[str]:
    """
    Checks all configured source accounts and purges any account older than 30 days.
    Returns list of removed handles.
    """
    try:
        data = _load_accounts_json()
        accs = data.setdefault("source_accounts", [])
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
            _save_accounts_json(data)
            sync_source_accounts_to_telegram_vault()
            logger.info("⏰ [EXPIRATION] Purged %d expired account(s) after 30 days: %s", len(expired), expired)

        return expired
    except Exception as e:
        logger.error("❌ Error during account expiration check: %s", e)
        return []


def get_active_accounts_metadata() -> List[Dict[str, Any]]:
    """Returns list of active target accounts with creation timestamps and days remaining until 30-day limit."""
    purge_expired_accounts()
    try:
        data = _load_accounts_json()
        accs = data.get("source_accounts", [])
        timestamps = data.get("account_added_timestamps", {})
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

    try:
        data = _load_accounts_json()

        all_accounts = data.get("source_accounts", [])
        if not all_accounts:
            data = _load_accounts_json()
            all_accounts = data.get("source_accounts", [])

        if not all_accounts:
            logger.warning("⚠️ No target source accounts configured in source_accounts.json. Use /addaccount <handle> to add accounts.")
            return []

        last_scraped_map = data.get("account_last_scraped", {})

        # Sort accounts by last scraped timestamp (0 for never scraped -> highest priority)
        sorted_accounts = sorted(all_accounts, key=lambda acc: last_scraped_map.get(acc, 0.0))

        selected = sorted_accounts[:min(max_accounts, len(sorted_accounts))]

        # Save active rotation state to data/scraper_rotation_pointer.json (preserving existing stats)
        try:
            pointer_path = os.path.join(DATA_DIR, "scraper_rotation_pointer.json")
            pointer_state = {}
            if os.path.exists(pointer_path):
                try:
                    with open(pointer_path, "r", encoding="utf-8") as pf:
                        pointer_state = json.load(pf)
                except Exception:
                    pointer_state = {}
            pointer_state.update({
                "pointer": (all_accounts.index(selected[-1]) + 1) % len(all_accounts) if selected else 0,
                "last_selected": selected,
                "timestamp": time.time(),
                "iso_timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
            })
            with open(pointer_path, "w", encoding="utf-8") as pf:
                json.dump(pointer_state, pf, indent=2, ensure_ascii=False)
            logger.info(f"🔄 [SCHEDULER SCRAPER] Anti-duplicate account pool selection (max {max_accounts}): selected={selected}")
            sync_source_accounts_to_telegram_vault()
        except Exception as _pe:
            logger.debug("Notice saving scraper_rotation_pointer.json: %s", _pe)

        return selected
    except Exception as e:
        logger.error(f"❌ Error rotating source accounts: {e}")
        return []


def run_scheduled_scraper_batch(max_accounts: int = 2) -> List[str]:
    """
    Runs a scheduled batch with max 2 target accounts:
    1. Reads scraper_rotation_pointer.json and alternates publishing quota (2 <-> 3).
    2. Executes Phase 1 Ingestion (scrapes 6 reels, drops top 3 pinned).
    3. Executes Phase 2 & 3 Master AI Editing.
    4. Audits master clips using pool_metadata.json intelligence (duration >= 5s, watermark, engagement).
    5. Publishes up to target quota, writes clean flat pointer JSON, and syncs to Telegram Vault.
    """
    target_accounts = get_rotated_max_two_accounts(max_accounts=max_accounts)
    logger.info(f"🚀 [SCHEDULED BATCH] Triggering Apify scraper for accounts: {target_accounts}")

    from Downloader_Modules.downloader_main import run_phase1_ingestion
    from Main_Modules.phase2_main import run_phase2_orchestration

    import random

    # Read previous session stats from scraper_rotation_pointer.json to alternate quota
    pointer_path = os.path.join(DATA_DIR, "scraper_rotation_pointer.json")
    pointer_state = {}
    if os.path.exists(pointer_path):
        try:
            with open(pointer_path, "r", encoding="utf-8") as pf:
                pointer_state = json.load(pf)
        except Exception as _pe:
            logger.debug("Notice loading scraper_rotation_pointer.json: %s", _pe)
            pointer_state = {}

    last_uploaded = pointer_state.get("clips_uploaded", 2)
    # Smart alternating cadence: 2 -> 3 -> 2 to break social algorithm bot footprint
    if last_uploaded == 2:
        target_publish_quota = 3
    elif last_uploaded == 3:
        target_publish_quota = 2
    else:
        target_publish_quota = 3 if (last_uploaded % 2 == 0) else 2

    logger.info(f"🎯 [ORGANIC CADENCE ROTATION] Previous uploaded count: {last_uploaded}. Target publish quota this session: {target_publish_quota}")

    clips_per_run = 6
    try:
        clips_per_run = int(os.getenv("CLIPS_PER_ACCOUNT_PER_RUN", "6"))
    except ValueError:
        clips_per_run = 6

    scrape_limit = 6
    try:
        scrape_limit = int(os.getenv("APIFY_REELS_PER_ACCOUNT", "6"))
    except ValueError:
        scrape_limit = 6

    # Run ingestion for selected accounts (scrapes 6 clips, avoiding top 3 pinned clips)
    ingest_res = run_phase1_ingestion(mode="auto", limit_per_account=scrape_limit, target_accounts=target_accounts)
    downloaded_files = ingest_res.get("downloaded_files", [])
    if not ingest_res.get("success") or not downloaded_files:
        logger.warning("⚠️ [SCHEDULED BATCH] Ingestion returned 0 new clips.")
        mark_and_sync_scraped_accounts(target_accounts)
        return []

    # Target newly downloaded clip directories up to processing limit (6 clips)
    target_dirs = list(set(os.path.dirname(f) for f in downloaded_files if os.path.exists(f)))[:clips_per_run]

    # Run AI Master Editor on target downloaded clips
    phase2_res = run_phase2_orchestration(target_dirs=target_dirs, limit=clips_per_run)
    rendered_reels = phase2_res.get("rendered_files", [])
    logger.info(f"🎬 [SCHEDULED BATCH RENDER COMPLETE] Rendered {len(rendered_reels)} reel(s).")

    # Step 3: Smart Audited Publishing up to target quota
    clips_published_this_session = 0
    if rendered_reels:
        logger.info(f"🎲 [SMART AUDITED PUBLISHING] Auditing up to {len(rendered_reels)} rendered reels to achieve target quota of {target_publish_quota}...")
        try:
            from Publishing_Modules.media_publisher_main import run_phase4_publishing
            from Gemini_Modules.gemini_clip_auditor import run_clip_audit_and_seo
            from Telegram_Storage_Modules.telegram_vault_indexer import TelegramVaultIndexer
            from Gemini_Modules.platform_seo_generator import sanitize_raw_handles_out
            
            indexer = TelegramVaultIndexer()
            stagger_min = int(os.getenv("PUBLISH_STAGGER_MIN_SECONDS", "180"))
            stagger_max = int(os.getenv("PUBLISH_STAGGER_MAX_SECONDS", "360"))

            for idx, r_file in enumerate(rendered_reels):
                if clips_published_this_session >= target_publish_quota:
                    logger.info(f"🏁 [TARGET QUOTA REACHED] Successfully published target {target_publish_quota} clips. Stopping session publishing.")
                    break

                if not os.path.exists(r_file):
                    continue

                base_name = os.path.basename(r_file)
                clip_id = base_name.replace("_master.mp4", "").replace(".mp4", "")
                parts = clip_id.split("_")
                raw_handle = parts[0] if parts else ""

                # 1. Retrieve unified intelligence directly from pool_metadata.json
                pool_entry = indexer.find_entry_by_shortcode(clip_id) or {}
                visual_data = pool_entry.get("visual_data", {})
                
                # 2. Extract spatial bounding boxes for watermark audit (coordinates only, NO raw text)
                watermark_output = visual_data.get("gemini_watermark_output", {})
                inpaint_boxes = (
                    watermark_output.get("bounding_boxes")
                    or [item.get("box_2d") for item in watermark_output.get("items", []) if item.get("box_2d")]
                    or visual_data.get("vectors", [])
                )

                # 3. Resolve dynamic creator / hero subject
                discovered_hero = (
                    visual_data.get("gemini_visual_output", {}).get("main_subject")
                    or visual_data.get("gemini_visual_output", {}).get("person_name")
                    or pool_entry.get("ownerFullName")
                    or pool_entry.get("ownerUsername")
                    or ""
                )
                effective_creator = discovered_hero if (discovered_hero and raw_handle.lower() not in discovered_hero.lower()) else "Trending"
                scraped_caption = pool_entry.get("caption") or ""
                if str(scraped_caption).startswith("Video by"):
                    scraped_caption = ""

                # 4. Run Gemini Vision Quality, Watermark, & Engagement Audit
                audit_res = run_clip_audit_and_seo(
                    video_path=r_file,
                    inpainted_boxes=inpaint_boxes,
                    creator_name=effective_creator,
                    niche=visual_data.get("gemini_visual_output", {}).get("niche") or "fashion_lifestyle",
                    title_hint=scraped_caption,
                    pool_entry=pool_entry
                )

                # Gate check: Reject clip if quality, duration (<5s), or watermark failed
                if not audit_res.get("audit_passed", False):
                    logger.warning(
                        f"⛔ [CLIP REJECTED BY AUDITOR] '{base_name}' failed audit: {audit_res.get('rejection_reason', 'Quality/watermark threshold not met')}. Skipping to next reel..."
                    )
                    continue

                # 5. Stagger delay between successful publishes to simulate organic human activity
                if clips_published_this_session > 0 and stagger_max > 0:
                    stagger_sec = random.randint(stagger_min, stagger_max)
                    logger.info(f"⏳ [ORGANIC PUBLISH STAGGER] Waiting {stagger_sec}s ({stagger_sec/60.0:.1f} min) before publishing next clip...")
                    time.sleep(stagger_sec)

                seo_info = audit_res.get("seo_metadata", {})
                fallback_subject = discovered_hero or "Featured Reel"
                raw_title = seo_info.get("viral_seo_title") or f"{fallback_subject} ✨ | Trending Lookbook"
                raw_desc = seo_info.get("description") or f"Featured: {fallback_subject} 🔥\n\n#shorts #viral #trending"
                raw_tags = " ".join(seo_info.get("hashtags", ["#viral", "#shorts", "#trending", "#reels"]))

                viral_title = sanitize_raw_handles_out(raw_title, raw_handle)
                description = sanitize_raw_handles_out(raw_desc, raw_handle)
                hashtags = sanitize_raw_handles_out(raw_tags, raw_handle)

                logger.info(f"📤 [SCHEDULED PUBLISH] Clip {clips_published_this_session+1}/{target_publish_quota} Title: '{viral_title}'")
                pub_res = run_phase4_publishing(
                    video_path=r_file,
                    title=viral_title,
                    description=description,
                    tags=hashtags
                )
                if pub_res.get("success", True):
                    clips_published_this_session += 1
                    logger.info(f"✅ [PUBLISHED] Clip {clips_published_this_session}/{target_publish_quota} successfully published!")
                else:
                    logger.warning(f"⚠️ [PUBLISH FAILED] Phase 4 failed for '{base_name}': {pub_res.get('error')}")

        except Exception as pub_err:
            logger.warning(f"⚠️ [INSTANT PUBLISHING WARNING] Exception during automated publish step: {pub_err}")

    # Step 4: Write clean flat scraper_rotation_pointer.json (zero redundant upload_history bloat)
    next_target = 3 if clips_published_this_session == 2 else 2
    try:
        current_pointer = pointer_state.get("pointer", 0)
        flat_pointer_data = {
            "pointer": current_pointer,
            "last_selected": target_accounts,
            "timestamp": time.time(),
            "iso_timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "clips_rendered": len(rendered_reels),
            "clips_uploaded": clips_published_this_session,
            "next_target_quota": next_target
        }
        with open(pointer_path, "w", encoding="utf-8") as pf:
            json.dump(flat_pointer_data, pf, indent=2, ensure_ascii=False)
        logger.info(f"💾 [ROTATION POINTER SAVED] Uploaded: {clips_published_this_session}, Next Target Quota: {next_target}")
    except Exception as pe:
        logger.warning(f"Notice saving scraper_rotation_pointer.json: {pe}")

    # Mark scraped accounts in source_accounts.json and backup both to Telegram Vault
    mark_and_sync_scraped_accounts(target_accounts)

    return rendered_reels


def add_source_account(account_handle: str, platform: str = "instagram") -> bool:
    """Adds a new target account handle with creation timestamp to source_accounts.json and syncs to Telegram Vault."""
    clean_handle = account_handle.strip().lstrip("@")
    if not clean_handle:
        return False
    try:
        data = _load_accounts_json()
        data["platform"] = platform
        
        accs = data.setdefault("source_accounts", [])
        timestamps = data.setdefault("account_added_timestamps", {})

        if clean_handle not in accs:
            accs.append(clean_handle)
            timestamps[clean_handle] = time.time()
            _save_accounts_json(data)
            sync_source_accounts_to_telegram_vault()
            logger.info("➕ [SOURCE ACCOUNTS] Added @%s (%s) with 30-day limit to source_accounts.json & synced to Telegram Vault", clean_handle, platform)
            return True
        else:
            # Refresh timestamp on re-adding
            timestamps[clean_handle] = time.time()
            _save_accounts_json(data)
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
        data = _load_accounts_json()
        accs = data.get("source_accounts", [])
        timestamps = data.get("account_added_timestamps", {})

        if clean_handle in accs:
            accs.remove(clean_handle)
            timestamps.pop(clean_handle, None)
            data.get("account_last_scraped", {}).pop(clean_handle, None)
            data.get("account_last_scraped_iso", {}).pop(clean_handle, None)

            _save_accounts_json(data)
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
        data = _load_accounts_json()
        data.setdefault("platform", "instagram")
        data.setdefault("source_accounts", [])

        if scraped_accounts:
            now_ts = time.time()
            iso_now = time.strftime("%Y-%m-%d %H:%M:%S")

            last_scraped_map = data.setdefault("account_last_scraped", {})
            last_scraped_iso_map = data.setdefault("account_last_scraped_iso", {})

            for acc in scraped_accounts:
                last_scraped_map[acc] = now_ts
                last_scraped_iso_map[acc] = iso_now

            _save_accounts_json(data)
            logger.info("📝 [ACCOUNT SCRAPE TIMESTAMPS MARKED] Updated last_scraped timestamps for: %s", scraped_accounts)

        return sync_source_accounts_to_telegram_vault()
    except Exception as e:
        logger.error("❌ Failed to mark and sync scraped accounts: %s", e)
        return False


def sync_source_accounts_to_telegram_vault() -> bool:
    """Uploads source_accounts.json and scraper_rotation_pointer.json to Telegram Storage Group cloud vault."""
    try:
        from Telegram_Storage_Modules.telegram_vault_indexer import TelegramVaultIndexer
        from Telegram_Storage_Modules.telegram_user_manager import _upload_file_to_telegram_storage
        indexer = TelegramVaultIndexer()
        storage_group_id = os.getenv("TELEGRAM_STORAGE_GROUP_ID")
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        if not storage_group_id or not bot_token:
            return False

        # Pre-fetch pinned master index from Telegram cloud to prevent overwriting other file_ids
        indexer.sync_pinned_index_from_telegram_sync()

        # 1. Upload source_accounts.json
        if os.path.exists(ACCOUNTS_JSON):
            caption = f"📋 **[AUTO INPUT SOURCE ACCOUNTS VAULT BACKUP]** `source_accounts.json` (Updated {time.strftime('%H:%M:%S')})"
            sa_doc_id = _upload_file_to_telegram_storage(ACCOUNTS_JSON, caption=caption)
            if not sa_doc_id:
                import requests
                url = f"https://api.telegram.org/bot{bot_token}/sendDocument"
                with open(ACCOUNTS_JSON, "rb") as f:
                    resp = requests.post(url, data={"chat_id": storage_group_id, "caption": caption}, files={"document": f}, timeout=30)
                if resp.status_code == 200:
                    sa_doc_id = resp.json().get("result", {}).get("document", {}).get("file_id")
            if sa_doc_id:
                indexer.vault_index["auto_input_source_account_file_id"] = sa_doc_id
                indexer.vault_index["source_accounts_file_id"] = sa_doc_id
                indexer._save_local_index()
                logger.info("✅ [AUTO INPUT VAULT SYNC] Uploaded source_accounts.json (source_accounts_file_id: %s)", sa_doc_id[:15])

        # 2. Upload scraper_rotation_pointer.json
        data_dir = os.path.join(_REPO_ROOT, "data")
        pointer_path = os.path.join(data_dir, "scraper_rotation_pointer.json")
        if os.path.exists(pointer_path):
            caption = f"🔄 **[SCRAPER ROTATION POINTER BACKUP]** `scraper_rotation_pointer.json` (Updated {time.strftime('%H:%M:%S')})"
            srp_doc_id = _upload_file_to_telegram_storage(pointer_path, caption=caption)
            if not srp_doc_id:
                import requests
                url = f"https://api.telegram.org/bot{bot_token}/sendDocument"
                with open(pointer_path, "rb") as pf:
                    resp = requests.post(url, data={"chat_id": storage_group_id, "caption": caption}, files={"document": pf}, timeout=30)
                if resp.status_code == 200:
                    srp_doc_id = resp.json().get("result", {}).get("document", {}).get("file_id")
            if srp_doc_id:
                indexer.vault_index["scraper_rotation_pointer_file_id"] = srp_doc_id
                indexer._save_local_index()
                logger.info("✅ [ROTATION POINTER VAULT SYNC] Uploaded scraper_rotation_pointer.json (scraper_rotation_pointer_file_id: %s)", srp_doc_id[:15])

        indexer.upload_and_pin_vault_index_sync()
        return True
    except Exception as _e:
        logger.warning("Notice syncing source_accounts & scraper_rotation_pointer to vault: %s", _e)
        return False
