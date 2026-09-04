"""
Publishing_Modules / telegram_vault_indexer.py
================================================================
Telegram Storage Group Unified Master Vault Indexer.

Turns Telegram into an unlimited, zero-cost cloud data lake for ephemeral runners
(GitHub Actions / Docker). Stores permanent file_id references and full visual/lyric
intelligence in a single pinned master_vault_index.json document inside the storage group.

Columns:
  Column 1 (processed_reels):
    Indexed by session_id, social_media_id, and custom_title.
    Stores master_video_file_id, audio_data (pool_metadata + lyric_intel),
    and visual_data (.clip_intelligence.json).

  Column 2 (downloaded_sources):
    Indexed by social_media_id (Instagram/YouTube URL) and session_id.
    Stores raw_video_file_id, extracted_audio_file_id, and audio_math.
    Enables 1.5s cache hits on duplicate URL requests without re-downloading.

Author: AMTCE Serverless Vault Architecture v1.0
"""

import os
import sys
import json
import time
import uuid
import threading
import urllib.request
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger("telegram_vault_indexer")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(_REPO_ROOT, "data")
MASTER_INDEX_FILE = os.path.join(DATA_DIR, "master_vault_index.json")

# Cooldown guard: prevents duplicate vault hydration within 60 seconds
_LAST_HYDRATION_TIMESTAMP = 0.0


def _empty_vault_index() -> Dict[str, Any]:
    return {
        "version": 2.0,
        "updated_at": time.time(),
        "pinned_message_id": None,
        "pool_metadata_file_id": None,
        "telegram_users_file_id": None,
        "source_accounts_file_id": None,
        # Advisory lock stored inside the shared pinned index so all runners see it.
        # See acquire_lock() / release_lock() / vault_session below.
        "lock": None,
        "column_1_processed_reels": {
            "by_session_id": {},
            "by_social_media_id": {},
            "by_user_id": {},  # User-scoped indexing
        },
        "column_2_downloaded_sources": {
            "by_social_media_id": {},
            "by_session_id": {},
            "by_user_id": {},  # User-scoped indexing
        },
    }


def _send_telegram_file_sync(method: str, chat_id: str, file_key: str, file_path: str, caption: Optional[str] = None) -> Optional[Dict[str, Any]]:
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not bot_token or not chat_id or not os.path.exists(file_path):
        return None

    boundary = f"----WebKitFormBoundary{uuid.uuid4().hex}"
    url = f"https://api.telegram.org/bot{bot_token}/{method}"
    
    body = bytearray()
    
    def add_field(name, value):
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        body.extend(f"{value}\r\n".encode("utf-8"))

    add_field("chat_id", chat_id)
    if caption:
        add_field("caption", caption)

    filename = os.path.basename(file_path)
    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(f'Content-Disposition: form-data; name="{file_key}"; filename="{filename}"\r\n'.encode("utf-8"))
    body.extend(b"Content-Type: application/octet-stream\r\n\r\n")
    with open(file_path, "rb") as f:
        body.extend(f.read())
    body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode("utf-8"))

    req = urllib.request.Request(
        url,
        data=bytes(body),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as err:
        logger.warning(f"⚠️ Telegram file upload failed for {filename}: {err}")
        return None


_SAVE_LOCK = threading.Lock()


def _safe_replace(src: str, dst: str, max_retries: int = 5, delay: float = 0.1) -> None:
    """Safely replaces dst with src, with retries to handle Windows [WinError 5] Access is denied locks."""
    for attempt in range(1, max_retries + 1):
        try:
            os.replace(src, dst)
            return
        except (PermissionError, OSError) as e:
            if attempt == max_retries:
                try:
                    if os.path.exists(dst):
                        os.remove(dst)
                    os.replace(src, dst)
                    return
                except Exception:
                    raise e
            time.sleep(delay * attempt)


class TelegramVaultIndexer:
    """
    Manages reading, writing, uploading, and pinning the master_vault_index.json
    inside TELEGRAM_STORAGE_GROUP_ID.
    """

    def __init__(self, index_file: str = MASTER_INDEX_FILE):
        self.index_file = index_file
        os.makedirs(os.path.dirname(self.index_file), exist_ok=True)
        self.vault_index = self._load_local_index()

    def _load_local_index(self) -> Dict[str, Any]:
        if os.path.exists(self.index_file):
            try:
                with open(self.index_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict) and "column_1_processed_reels" in data:
                        return data
            except Exception as e:
                logger.warning(f"⚠️ Could not load local vault index: {e}")
        return _empty_vault_index()

    def _save_local_index(self):
        with _SAVE_LOCK:
            try:
                self.vault_index["updated_at"] = time.time()
                temp_path = self.index_file + ".tmp"
                with open(temp_path, "w", encoding="utf-8") as f:
                    json.dump(self.vault_index, f, indent=2, ensure_ascii=False)
                _safe_replace(temp_path, self.index_file)
            except Exception as e:
                logger.error(f"❌ Failed to save local vault index: {e}")

    # ── VAULT JSON HYDRATION & CLOUD SYNC APIs ───────────────────────────────

    def download_vault_file_by_id(self, file_id: str, dest_path: str) -> bool:
        """
        Downloads a document file (e.g. telegram_users.json or metadata_pool.json)
        from Telegram Storage Group into dest_path via Telegram Bot API getFile.
        Uses retry loop with custom headers to prevent connection reset drops.

        Note: Telegram Bot API getFile has a hard 20MB limit on downloads.
        Files > 20MB will trigger HTTP 400 Bad Request and return False.
        """
        if not file_id:
            return False
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        if not bot_token:
            return False

        headers = {"User-Agent": "AMTCE-Vault/1.0"}

        # Try using requests first for robust SSL/connection handling
        try:
            import requests
            get_file_url = f"https://api.telegram.org/bot{bot_token}/getFile?file_id={file_id}"
            resp = requests.get(get_file_url, headers=headers, timeout=15)
            if resp.status_code == 400:
                # Telegram Bot API getFile returns HTTP 400 Bad Request when file size > 20MB
                logger.warning(
                    "⚠️ Telegram Bot API 20MB limit reached for %s (file_id: %s). Falling back to direct platform download.",
                    os.path.basename(dest_path), file_id[:15]
                )
                return False
            if resp.status_code == 200:
                res_data = resp.json()
                if res_data.get("ok"):
                    f_path = res_data["result"]["file_path"]
                    dl_url = f"https://api.telegram.org/file/bot{bot_token}/{f_path}"
                    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                    temp_dest = dest_path + ".tmp"
                    dl_resp = requests.get(dl_url, headers=headers, timeout=30, stream=True)
                    if dl_resp.status_code == 200:
                        with open(temp_dest, "wb") as out_f:
                            for chunk in dl_resp.iter_content(chunk_size=8192):
                                out_f.write(chunk)
                        os.replace(temp_dest, dest_path)
                        logger.info("✅ [VAULT HYDRATION] Successfully downloaded %s from Telegram Storage Group (file_id: %s)", os.path.basename(dest_path), file_id[:15])
                        return True
        except Exception as req_err:
            logger.debug("requests-based vault download fallback to urllib: %s", req_err)

        # urllib fallback with retry loop
        import urllib.request
        import urllib.error
        import json as _json

        for attempt in range(1, 4):
            try:
                get_file_url = f"https://api.telegram.org/bot{bot_token}/getFile?file_id={file_id}"
                req = urllib.request.Request(get_file_url, headers=headers)
                with urllib.request.urlopen(req, timeout=15) as resp:
                    res_data = _json.loads(resp.read().decode("utf-8"))

                if res_data.get("ok"):
                    f_path = res_data["result"]["file_path"]
                    dl_url = f"https://api.telegram.org/file/bot{bot_token}/{f_path}"
                    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                    temp_dest = dest_path + ".tmp"
                    dl_req = urllib.request.Request(dl_url, headers=headers)
                    with urllib.request.urlopen(dl_req, timeout=30) as dl_resp, open(temp_dest, "wb") as out_f:
                        out_f.write(dl_resp.read())
                    os.replace(temp_dest, dest_path)
                    logger.info("✅ [VAULT HYDRATION] Successfully downloaded %s from Telegram Storage Group (file_id: %s)", os.path.basename(dest_path), file_id[:15])
                    return True
            except urllib.error.HTTPError as http_err:
                if http_err.code == 400:
                    logger.warning(
                        "⚠️ Telegram Bot API 20MB limit reached for %s (file_id: %s, HTTP 400). Falling back to direct platform download.",
                        os.path.basename(dest_path), file_id[:15]
                    )
                    return False
                if attempt == 3:
                    logger.warning("⚠️ Vault hydration download failed for %s: %s", os.path.basename(dest_path), http_err)
            except Exception as _dl_err:
                if attempt == 3:
                    logger.warning("⚠️ Vault hydration download failed for %s: %s", os.path.basename(dest_path), _dl_err)
                time.sleep(1.0)
        return False

    def sync_pinned_index_from_telegram_sync(self) -> bool:
        """
        Synchronously fetches TELEGRAM_STORAGE_GROUP_ID for pinned master_vault_index.json,
        downloads it, and updates local vault_index.
        """
        storage_group_id = os.getenv("TELEGRAM_STORAGE_GROUP_ID")
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        if not storage_group_id or not bot_token:
            return False

        try:
            import urllib.request
            import json as _json
            url = f"https://api.telegram.org/bot{bot_token}/getChat?chat_id={storage_group_id}"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = _json.loads(resp.read().decode("utf-8"))
            
            if data.get("ok"):
                pinned = data.get("result", {}).get("pinned_message", {})
                doc = pinned.get("document", {})
                if doc.get("file_name") in ["telegram_media_index.json", "master_vault_index.json"] and doc.get("file_id"):
                    file_id = doc["file_id"]
                    if self.download_vault_file_by_id(file_id, self.index_file):
                        self.vault_index = self._load_local_index()
                        logger.info("📌 [VAULT SYNC SUCCESS] Downloaded and reloaded pinned master_vault_index.json from Telegram Storage Group!")
                        return True
        except Exception as err:
            logger.warning("⚠️ Sync pinned master index notice: %s", err)
        return False

    def hydrate_all_vault_jsons_on_startup(self, force: bool = False) -> Dict[str, bool]:
        """
        1. Downloads pinned master_vault_index.json from Telegram Storage Group.
        2. Downloads latest telegram_users.json, metadata_pool.json, and source_accounts.json using file_ids in index.
        """
        global _LAST_HYDRATION_TIMESTAMP
        now = time.time()
        if not force and (now - _LAST_HYDRATION_TIMESTAMP) < 60.0:
            logger.debug("⚡ [VAULT HYDRATION] Skipped duplicate hydration (completed %.1fs ago)", now - _LAST_HYDRATION_TIMESTAMP)
            return {"pinned_index": True, "cached": True}
        _LAST_HYDRATION_TIMESTAMP = now

        results = {"pinned_index": False, "telegram_users": False, "metadata_pool": False, "source_accounts": False}
        try:
            # Step 1: Download pinned index from Telegram Storage Group first!
            results["pinned_index"] = self.sync_pinned_index_from_telegram_sync()

            # Step 2: Download telegram_users.json and merge with local records
            users_file_id = self.vault_index.get("telegram_users_file_id")
            if users_file_id:
                from Publishing_Modules.telegram_user_manager import USERS_JSON_PATH, load_all_users, save_all_users
                local_users = load_all_users()
                temp_download_path = USERS_JSON_PATH + ".download.tmp"
                if self.download_vault_file_by_id(users_file_id, temp_download_path):
                    try:
                        with open(temp_download_path, "r", encoding="utf-8") as f:
                            downloaded_users = json.load(f)
                        for uid, udata in downloaded_users.items():
                            if uid not in local_users:
                                local_users[uid] = udata
                            else:
                                for k, v in udata.items():
                                    if v and not local_users[uid].get(k):
                                        local_users[uid][k] = v
                        save_all_users(local_users, sync_to_vault=False)
                        results["telegram_users"] = True
                    except Exception as _m_err:
                        logger.warning("Notice merging hydrated users: %s", _m_err)
                    finally:
                        if os.path.exists(temp_download_path):
                            try:
                                os.remove(temp_download_path)
                            except Exception:
                                pass

            # Step 3: Download pool_metadata.json (backward compatible key check)
            pool_file_id = self.vault_index.get("pool_metadata_file_id") or self.vault_index.get("metadata_pool_file_id")
            if pool_file_id:
                from Audio_Modules.audio_pool_manager import AudioPoolManager
                pm = AudioPoolManager()
                results["pool_metadata"] = self.download_vault_file_by_id(pool_file_id, pm.meta_path)
                results["metadata_pool"] = results["pool_metadata"]  # Alias for backward compatibility

            # Step 4: Download source_accounts.json (Auto Input Source Accounts)
            sa_file_id = self.vault_index.get("auto_input_source_account_file_id") or self.vault_index.get("source_accounts_file_id")
            if sa_file_id:
                sa_path = os.path.join(_REPO_ROOT, "Content_Scraper_Modules", "source_accounts.json")
                results["source_accounts"] = self.download_vault_file_by_id(sa_file_id, sa_path)

            # Step 5: Download visual_pool_metadata.json (Master Clip Visual Catalog)
            vpm_file_id = self.vault_index.get("visual_pool_metadata_file_id")
            if vpm_file_id:
                vpm_path = os.path.join(DATA_DIR, "visual_pool_metadata.json")
                results["visual_pool_metadata"] = self.download_vault_file_by_id(vpm_file_id, vpm_path)

            # Step 6: Download telegram_sessions.json (Master Active Sessions Index)
            ts_file_id = self.vault_index.get("telegram_sessions_file_id")
            if ts_file_id:
                ts_path = os.path.join(DATA_DIR, "telegram_sessions.json")
                results["telegram_sessions"] = self.download_vault_file_by_id(ts_file_id, ts_path)

            # Step 7: Download scraper_rotation_pointer.json (Master Scraper Rotation Pointer)
            srp_file_id = self.vault_index.get("scraper_rotation_pointer_file_id")
            if srp_file_id:
                srp_path = os.path.join(DATA_DIR, "scraper_rotation_pointer.json")
                results["scraper_rotation_pointer"] = self.download_vault_file_by_id(srp_file_id, srp_path)
        except Exception as _h_err:
            logger.warning("⚠️ Vault JSON hydration notice: %s", _h_err)
        return results

    def hydrate_bgm_track_from_vault(
        self,
        track_name_or_file_id: str,
        dest_dir: Optional[str] = None,
        file_id: Optional[str] = None
    ) -> Optional[str]:
        """
        Synchronously hydrates a BGM track from Telegram Storage Group using
        file_id stored in pool_metadata.json (the single source of truth for audio data)
        or direct telegram_file_id string.
        """
        if not track_name_or_file_id:
            return None

        resolved_file_id = file_id
        filename = os.path.basename(track_name_or_file_id)

        # Direct file_id check (Telegram file_ids are alphanumeric strings usually > 20 chars without file extensions)
        if not resolved_file_id and len(track_name_or_file_id) > 20 and not track_name_or_file_id.endswith((".mp3", ".wav", ".m4a")):
            resolved_file_id = track_name_or_file_id
            filename = f"bgm_{resolved_file_id[:10]}.wav"

        if not dest_dir:
            dest_dir = os.path.join(_REPO_ROOT, "Original_audio", "active")
        os.makedirs(dest_dir, exist_ok=True)

        # 1. Primary Lookup: pool_metadata.json
        pm_path = os.path.join(_REPO_ROOT, "Original_audio", "pool_metadata.json")
        meta = {}
        if os.path.exists(pm_path):
            try:
                with open(pm_path, "r", encoding="utf-8") as f:
                    pm_data = json.load(f)
                files = pm_data.get("files", pm_data)
                meta = files.get(filename) or {}
                if not meta and resolved_file_id:
                    for k, v in files.items():
                        if isinstance(v, dict) and v.get("file_id") == resolved_file_id:
                            meta = v
                            filename = k
                            break
                if not meta and os.path.splitext(filename)[0]:
                    stem = os.path.splitext(filename.lower())[0]
                    for k, v in files.items():
                        if stem in k.lower() or k.lower() in filename.lower():
                            meta = v
                            filename = k
                            break
                if not resolved_file_id:
                    resolved_file_id = meta.get("file_id")
            except Exception as _pe:
                logger.debug("Notice on pool_metadata BGM lookup: %s", _pe)

        local_path = os.path.join(dest_dir, filename)
        if os.path.exists(local_path) and os.path.getsize(local_path) > 1024:
            logger.info("⚡ [LOCAL DISK CACHE HIT] BGM track '%s' already on local disk — skipping Telegram download.", filename)
            return local_path

        # 2. Secondary Fallback: Column 2 in master_vault_index.json
        if not resolved_file_id:
            track_stem = os.path.splitext(filename.lower())[0]
            c2_sess = self.vault_index.get("column_2_downloaded_sources", {}).get("by_session_id", {})
            for sess_id, entry in c2_sess.items():
                if entry.get("extracted_audio_file_id"):
                    s_id = str(sess_id).lower()
                    u_str = str(entry.get("social_media_id", "")).lower()
                    if track_stem in s_id or track_stem in u_str or filename.lower() in u_str:
                        resolved_file_id = entry["extracted_audio_file_id"]
                        break

        if resolved_file_id:
            logger.info("📥 [VAULT BGM HYDRATION] Fetching BGM '%s' from Telegram Storage Group (file_id: %s)...", filename, resolved_file_id[:15])
            if self.download_vault_file_by_id(resolved_file_id, local_path):
                logger.info("✅ [VAULT BGM HYDRATION SUCCESS] Downloaded BGM '%s' from Telegram Storage Group!", filename)
                return local_path

        return None

    def get_vault_audio_pool(self, current_clip_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Returns dictionary of all audio track metadata indexed in Column 2 & Column 1
        of master_vault_index.json plus pool_metadata.json if available.

        Args:
            current_clip_id: The shortcode/folder-name of the clip currently being edited
                             (e.g. 'manual_DcaZZkQvRcG'). When provided, ALL Column 2 entries
                             whose session_id or social_media_id contains this shortcode are
                             excluded — preventing same-reel session aliases from masquerading
                             as external BGM tracks.

        IMPORTANT — Column 2 tracks are ALWAYS tagged ``is_source_extract=True``.
        These are raw audio ripped from downloaded reels, NOT real music.
        The BGM selector uses this flag to exclude them from the external candidate pool.
        """
        pool = {}

        # Normalise current clip shortcode for comparison
        clip_stem = current_clip_id.lower().strip() if current_clip_id else ""

        # ── 1. Column 2 downloaded sources (source-extracted audio, NOT real BGM) ──
        c2 = self.vault_index.get("column_2_downloaded_sources", {}).get("by_social_media_id", {})
        for _url, entry in c2.items():
            file_id = entry.get("extracted_audio_file_id")
            if not file_id:
                continue

            sess_id = entry.get("session_id", "audio_track")
            social_id = str(entry.get("social_media_id", "")).lower()

            # Skip entries that belong to the clip currently being edited
            if clip_stem and (
                clip_stem in sess_id.lower()
                or clip_stem in social_id
                or sess_id.lower().endswith(f"_{clip_stem}")
            ):
                logger.debug(
                    "[VAULT POOL] Skipping same-reel session alias '%s' (clip_id='%s')",
                    sess_id, current_clip_id
                )
                continue

            fname = f"{sess_id}.wav"
            audio_math = entry.get("audio_math") or {}
            pool[fname] = {
                "file_id": file_id,
                "tempo_bpm": audio_math.get("tempo_bpm", 120.0),
                "dominant_emotion": audio_math.get("dominant_emotion", "hype"),
                "energy_profile": audio_math.get("energy_profile", "medium"),
                "has_vocals": audio_math.get("has_vocals", False),
                "language": audio_math.get("language", "unknown"),
                "last_used": entry.get("timestamp", 0),
                "usage_count": 0,
                # ─────────────────────────────────────────────────────────────
                # CRITICAL FLAG: This track is a raw audio extract from a
                # downloaded reel — it is NOT an independent music track.
                # BGM selector must NEVER treat it as an external BGM option.
                # ─────────────────────────────────────────────────────────────
                "is_source_extract": True,
            }

        # ── 2. Local pool_metadata.json (real BGM library + harvested clips) ──
        pm_path = os.path.join(_REPO_ROOT, "Original_audio", "pool_metadata.json")
        if os.path.exists(pm_path):
            try:
                with open(pm_path, "r", encoding="utf-8") as f:
                    pm_data = json.load(f)
                    files_dict = pm_data.get("files", pm_data) if isinstance(pm_data, dict) else {}
                    if isinstance(files_dict, dict):
                        for k, v in files_dict.items():
                            if isinstance(v, dict):
                                # If this pool entry is for the current clip's harvested audio,
                                # mark it as a source extract so the BGM selector deprioritises it.
                                if clip_stem and clip_stem in k.lower():
                                    v = dict(v)  # don't mutate original
                                    v["is_source_extract"] = True
                                pool[k] = v
            except Exception as _pme:
                logger.debug("Local pool metadata read notice: %s", _pme)

        return pool

    def upload_and_pin_vault_index_sync(self, upload_fn=None):
        """
        Synchronously uploads master_vault_index.json to TELEGRAM_STORAGE_GROUP_ID
        and pins the message so master_vault_index.json is NEVER lost!
        """
        storage_group_id = os.getenv("TELEGRAM_STORAGE_GROUP_ID")
        if not storage_group_id or not upload_fn or not os.path.exists(self.index_file):
            return

        try:
            reels_cnt = len(self.vault_index.get("column_1_processed_reels", {}).get("by_session_id", {}))
            sources_cnt = len(self.vault_index.get("column_2_downloaded_sources", {}).get("by_social_media_id", {}))
            caption = f"📌 **[VAULT MASTER INDEX]** Auto-Synced\n🕒 `{time.strftime('%Y-%m-%d %H:%M:%S')}`\n📊 Reels: `{reels_cnt}` | Sources: `{sources_cnt}`"
            res = upload_fn("sendDocument", storage_group_id, "document", self.index_file, caption=caption)
            if res and isinstance(res, dict):
                msg_id = res.get("message_id")
                if msg_id:
                    self.vault_index["pinned_message_id"] = msg_id
                    self._save_local_index()
                    try:
                        from Downloader_Modules.telegram_listener import _api_call
                        _api_call("pinChatMessage", {"chat_id": str(storage_group_id), "message_id": msg_id, "disable_notification": True})
                    except Exception as _p_call_err:
                        logger.warning("Notice on pinChatMessage call: %s", _p_call_err)
                    logger.info("📌 [VAULT PIN SUCCESS] Uploaded & PINNED master_vault_index.json in Storage Group (Message ID: %s)", msg_id)
        except Exception as _pin_err:
            logger.warning("⚠️ Vault index upload/pin notice: %s", _pin_err)

    def hydrate_raw_video_from_vault(self, social_url: str, dest_dir: str) -> Optional[str]:
        """
        ⚡ RAW VIDEO HYDRATION: Downloads already-stored raw source video from
        Telegram Storage Group using the raw_video_file_id in Column 2.

        Returns the local path to the downloaded video.mp4, or None if no cached
        file_id exists (caller must then fall through to yt-dlp / Apify).

        This is the PRIMARY path in 04_core_downloader.py — saves yt-dlp + Apify quota.
        """
        entry = self.lookup_downloaded_source(social_url)
        if not entry:
            return None

        raw_file_id = entry.get("raw_video_file_id") or entry.get("raw_file_id")
        if not raw_file_id:
            logger.debug("[VAULT HYDRATE VIDEO] Cache hit but no raw_video_file_id stored for: %s", social_url[:60])
            return None

        os.makedirs(dest_dir, exist_ok=True)
        out_path = os.path.join(dest_dir, "video.mp4")

        # Already on disk from a previous run — skip Telegram download entirely
        if os.path.exists(out_path) and os.path.getsize(out_path) > 1024:
            logger.info("⚡ [VAULT HYDRATE VIDEO] Raw video already on disk — skipping Telegram download: %s", out_path)
            return out_path

        logger.info("⚡ [VAULT HYDRATE VIDEO] Downloading raw source video from Telegram Storage Group (file_id: %s)...", raw_file_id[:15])
        success = self.download_vault_file_by_id(raw_file_id, out_path)
        if success and os.path.exists(out_path) and os.path.getsize(out_path) > 1024:
            logger.info("✅ [VAULT HYDRATE VIDEO] Raw source video hydrated from Telegram in ~1s: %s", os.path.basename(dest_dir))
            return out_path

        logger.debug("[VAULT HYDRATE VIDEO] Download failed or empty file for file_id: %s", raw_file_id[:15])
        return None

    # ── LOOKUP APIs ───────────────────────────────────────────────────────────


    def lookup_downloaded_source(self, social_url: str) -> Optional[Dict[str, Any]]:
        """
        Column 2 Lookup: Returns cached raw video file_id and audio_math if this URL
        was downloaded previously. Enables 1.5s cache hits on duplicate URL requests without re-downloading.
        """
        if not social_url:
            return None
        clean_url = str(social_url).strip().rstrip("`").rstrip("%60")
        c2_by_url = self.vault_index.get("column_2_downloaded_sources", {}).get("by_social_media_id", {})
        c2_by_sess = self.vault_index.get("column_2_downloaded_sources", {}).get("by_session_id", {})
        
        hit = c2_by_url.get(clean_url) or c2_by_url.get(social_url.strip())
        if hit:
            logger.info(f"⚡ [VAULT CACHE HIT] Column 2 found source for URL: {clean_url[:60]}...")
            return hit

        import re
        sc_match = re.search(r"/(?:reel|reels|p|shorts|v)/([A-Za-z0-9_-]{5,})", clean_url)
        shortcode = sc_match.group(1) if sc_match else clean_url

        if shortcode:
            # 1. Search by_social_media_id keys & entries
            for stored_url, entry in c2_by_url.items():
                if (shortcode in stored_url or 
                    shortcode in str(entry.get("session_id", "")) or 
                    shortcode in str(entry.get("shortcode", "")) or 
                    shortcode in str(entry.get("file_name", ""))):
                    logger.info(f"⚡ [VAULT CACHE HIT] Column 2 matched shortcode '{shortcode}' -> {stored_url[:60]}")
                    return entry

            # 2. Search by_session_id keys & entries
            for sess_id, entry in c2_by_sess.items():
                if (shortcode in sess_id or 
                    shortcode in str(entry.get("social_media_id", "")) or 
                    shortcode in str(entry.get("shortcode", "")) or 
                    shortcode in str(entry.get("file_name", ""))):
                    logger.info(f"⚡ [VAULT CACHE HIT] Column 2 matched session/shortcode '{shortcode}' -> {sess_id}")
                    return entry

        for stored_url, entry in c2_by_url.items():
            s_clean = stored_url.split("?")[0].rstrip("/").rstrip("`").rstrip("%60")
            u_clean = clean_url.split("?")[0].rstrip("/")
            if s_clean and u_clean and (s_clean == u_clean or s_clean.endswith(u_clean) or u_clean.endswith(s_clean)):
                logger.info(f"⚡ [VAULT CACHE HIT] Column 2 matched base URL -> {stored_url[:60]}")
                return entry

        return None

    def lookup_processed_reel(self, session_id: Optional[str] = None, social_url: Optional[str] = None, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Column 1 Lookup: Returns master reel data and full intelligence dicts by
        session_id, social_media_id, or user_id.
        """
        c1 = self.vault_index.get("column_1_processed_reels", {})
        
        if user_id:
            user_data = c1.get("by_user_id", {}).get(user_id, {})
            if session_id and session_id in user_data:
                return user_data[session_id]
            if social_url:
                for sess_id, entry in user_data.items():
                    if entry.get("social_media_id") == social_url.strip():
                        return entry
        
        if session_id:
            hit = c1.get("by_session_id", {}).get(session_id)
            if hit:
                return hit
        if social_url:
            sess_link = c1.get("by_social_media_id", {}).get(social_url.strip())
            if sess_link:
                return c1.get("by_session_id", {}).get(sess_link)
        return None

    async def download_audio_track_from_vault(self, bot, track_name: str, dest_dir: Optional[str] = None) -> Optional[str]:
        """
        On-Demand Audio Vault Fetcher:
        If an audio track selected from pool_metadata.json is missing on local disk,
        this method queries Column 2 of master_vault_index.json for extracted_audio_file_id,
        downloads it from Telegram Storage Group in ~1s, and saves it to Original_audio/active/<track_name>.
        """
        if not track_name or not bot:
            return None

        filename = os.path.basename(track_name)
        if not dest_dir:
            dest_dir = os.path.join(_REPO_ROOT, "Original_audio", "active")
        os.makedirs(dest_dir, exist_ok=True)
        local_path = os.path.join(dest_dir, filename)

        if os.path.exists(local_path) and os.path.getsize(local_path) > 1024:
            return local_path

        c2 = self.vault_index.get("column_2_downloaded_sources", {}).get("by_social_media_id", {})
        file_id = None
        for _url, entry in c2.items():
            if entry.get("extracted_audio_file_id") and (filename in _url or filename in str(entry.get("session_id", ""))):
                file_id = entry["extracted_audio_file_id"]
                break

        if not file_id:
            c2_sess = self.vault_index.get("column_2_downloaded_sources", {}).get("by_session_id", {})
            for sess_id, entry in c2_sess.items():
                if entry.get("extracted_audio_file_id") and (filename in sess_id or filename in str(entry.get("social_media_id", ""))):
                    file_id = entry["extracted_audio_file_id"]
                    break

        if file_id:
            try:
                logger.info(f"📥 [VAULT FETCH] Downloading on-demand audio track '{filename}' from Telegram Storage Group...")
                t_file = await bot.get_file(file_id)
                await t_file.download_to_drive(custom_path=local_path)
                if os.path.exists(local_path) and os.path.getsize(local_path) > 1024:
                    logger.info(f"✅ [VAULT FETCH SUCCESS] Audio track ready: {local_path}")
                    return local_path
            except Exception as e:
                logger.warning(f"⚠️ Vault on-demand audio fetch failed for '{filename}': {e}")
        return None

    # ── TELEGRAM SYNC & PIN ──────────────────────────────────────────────────

    async def sync_vault_index_from_telegram(self, bot) -> bool:
        """
        Startup Sync: Checks TELEGRAM_STORAGE_GROUP_ID for pinned master_vault_index.json.
        Downloads and merges it into local disk storage so ephemeral runners hydrate in 0.5s.
        """
        storage_group_id = os.getenv("TELEGRAM_STORAGE_GROUP_ID")
        if not storage_group_id or not bot:
            return False

        try:
            logger.info(f"🔍 [VAULT SYNC] Checking Storage Group ({storage_group_id}) for pinned master index...")
            chat = await bot.get_chat(chat_id=int(storage_group_id))
            pinned = chat.pinned_message if hasattr(chat, "pinned_message") else None

            if pinned and pinned.document and pinned.document.file_name == "master_vault_index.json":
                doc_file = await bot.get_file(pinned.document.file_id)
                temp_down = os.path.join(DATA_DIR, "pinned_vault_down.json")
                await doc_file.download_to_drive(custom_path=temp_down)

                if os.path.exists(temp_down) and os.path.getsize(temp_down) > 50:
                    with open(temp_down, "r", encoding="utf-8") as f:
                        remote_index = json.load(f)

                    if isinstance(remote_index, dict) and "column_1_processed_reels" in remote_index:
                        self.vault_index = remote_index
                        self.vault_index["pinned_message_id"] = pinned.message_id
                        self._save_local_index()
                        self._hydrate_local_caches()
                        logger.info(f"✅ [VAULT SYNC SUCCESS] Hydrated master index from Telegram (Pinned msg: {pinned.message_id})")
                        return True
        except Exception as e:
            logger.warning(f"⚠️ [VAULT SYNC] Could not fetch pinned vault index: {e}")
        return False

    def _hydrate_local_caches(self):
        """
        Hydrates local disk stores (pool_metadata.json, .clip_intelligence.json)
        from the downloaded master_vault_index.json.
        NOTE: sync_to_vault=False prevents re-upload loop during startup hydration.
        """
        try:
            c1_reels = self.vault_index.get("column_1_processed_reels", {}).get("by_session_id", {})
            if c1_reels:
                from Audio_Modules.audio_pool_manager import AudioPoolManager, _VAULT_HYDRATION_IN_PROGRESS
                import Audio_Modules.audio_pool_manager as _apm_mod
                _apm_mod._VAULT_HYDRATION_IN_PROGRESS = True
                try:
                    pm = AudioPoolManager()
                    for sess_id, rdata in c1_reels.items():
                        adata = rdata.get("audio_data") or {}
                        pool_meta = adata.get("pool_metadata") or {}
                        track_name = pool_meta.get("selected_audio_track") or pool_meta.get("selected_bgm_track")
                        if track_name and pool_meta:
                            pm._set_file_metadata(os.path.basename(track_name), pool_meta)
                    pm._save_metadata(sync_to_vault=False)  # NO re-upload during hydration
                finally:
                    _apm_mod._VAULT_HYDRATION_IN_PROGRESS = False

            logger.info("⚡ [VAULT HYDRATE] Local pool_metadata and clip caches updated from Vault Index.")
        except Exception as e:
            logger.debug(f"[VAULT HYDRATE] Cache hydration notice: {e}")


    # ── RECORDING APIS ───────────────────────────────────────────────────────

    def record_ingested_clip_source(
        self,
        social_url: str,
        raw_video_path: str,
        upload_fn,
        existing_raw_file_id: Optional[str] = None,
        extracted_audio_path: Optional[str] = None,
        audio_math: Optional[Dict[str, Any]] = None,
        whisper_transcript: Optional[Dict[str, Any]] = None,
        gemini_semantic: Optional[Dict[str, Any]] = None,
        file_size: Optional[int] = None,
        sha256: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Unified Storage Authority Entry Point:
        1. Uses existing_raw_file_id or uploads raw video to Storage Group ONCE via upload_fn -> gets raw_file_id.
        2. Uploads extracted audio WAV to Storage Group ONCE via upload_fn -> gets audio_file_id.
        3. Updates metadata_pool.json (clip_source_math) with file_ids, audio_math, whisper_transcript, gemini_semantic.
        4. Updates Column 2 of Master Vault Index.
        """
        storage_group_id = os.getenv("TELEGRAM_STORAGE_GROUP_ID")
        filename = os.path.basename(raw_video_path)
        ext = os.path.splitext(filename)[1].lower()
        
        method = "sendVideo"
        file_param = "video"
        if ext in [".mp3", ".m4a", ".aac", ".flac", ".wav"]:
            method = "sendAudio"
            file_param = "audio"
        elif ext in [".jpg", ".jpeg", ".png", ".webp"]:
            method = "sendPhoto"
            file_param = "photo"
        elif ext not in [".mp4", ".mkv", ".mov", ".webm", ".avi"]:
            method = "sendDocument"
            file_param = "document"

        caption = f"🎬 {filename}\n🔗 `{social_url}`" + (f"\n👤 User: `{user_id}`" if user_id else "")
        
        raw_file_id = existing_raw_file_id
        extracted_audio_file_id = None

        if storage_group_id and upload_fn:
            try:
                if not raw_file_id and os.path.exists(raw_video_path):
                    logger.info("📦 [STORAGE MANAGER] Uploading raw media clip (%s) to Storage Group (%s)...", filename, storage_group_id)
                    upload_res = upload_fn(method, storage_group_id, file_param, raw_video_path, caption=caption)
                    if upload_res and isinstance(upload_res, dict):
                        raw_file_id = upload_res.get(file_param, {}).get("file_id") or (upload_res.get("document", {}).get("file_id") if upload_res.get("document") else None)
                
                if extracted_audio_path and os.path.exists(extracted_audio_path):
                    audio_filename = os.path.basename(extracted_audio_path)
                    audio_caption = f"🎵 [EXTRACTED AUDIO] `{audio_filename}`\n🔗 `{social_url}`"
                    logger.info("🎙️ [STORAGE MANAGER] Uploading extracted audio (%s) to Storage Group...", audio_filename)
                    audio_upload_res = upload_fn("sendAudio", storage_group_id, "audio", extracted_audio_path, caption=audio_caption)
                    if audio_upload_res and isinstance(audio_upload_res, dict):
                        extracted_audio_file_id = audio_upload_res.get("audio", {}).get("file_id") or (audio_upload_res.get("document", {}).get("file_id") if audio_upload_res.get("document") else None)
                        logger.info("✅ [STORAGE MANAGER] Captured extracted_audio_file_id: %s", extracted_audio_file_id)
            except Exception as _up_err:
                logger.warning("⚠️ Storage Group upload warning: %s", _up_err)

        import re
        sc_m = re.search(r"/(?:reel|reels|p|shorts|v)/([A-Za-z0-9_-]{5,})", social_url)
        shortcode_val = sc_m.group(1) if sc_m else ""

        clip_entry = {
            "social_media_id": social_url,
            "shortcode": shortcode_val,
            "raw_video_file_id": raw_file_id,
            "extracted_audio_file_id": extracted_audio_file_id,
            "file_name": filename,
            "file_size": file_size or (os.path.getsize(raw_video_path) if os.path.exists(raw_video_path) else 0),
            "sha256": sha256 or "",
            "downloaded_at": time.time(),
            "user_id": user_id,
            "audio_math": audio_math or {},
            "whisper_transcript": whisper_transcript or {},
            "gemini_semantic_intelligence": gemini_semantic or {}
        }

        try:
            from Audio_Modules.audio_pool_manager import AudioPoolManager
            pm = AudioPoolManager()
            csm = pm.metadata.setdefault("clip_source_math", {})
            csm[social_url] = clip_entry
            pm._save_metadata()
            logger.info("📦 [STORAGE MANAGER] Indexed clip_source_math entry for %s in pool_metadata.json", social_url)

            if storage_group_id and upload_fn and os.path.exists(pm.meta_path):
                pool_upload_res = upload_fn("sendDocument", storage_group_id, "document", pm.meta_path, caption=f"📦 **[VAULT BACKUP]** `pool_metadata.json` (Updated {time.strftime('%H:%M:%S')})")
                if pool_upload_res and isinstance(pool_upload_res, dict):
                    pool_doc_id = pool_upload_res.get("document", {}).get("file_id")
                    self.vault_index["pool_metadata_file_id"] = pool_doc_id
                    self.vault_index["metadata_pool_file_id"] = pool_doc_id
                    logger.info("✅ [STORAGE MANAGER] Uploaded updated pool_metadata.json to Storage Group (file_id: %s)", pool_doc_id)

            try:
                from Publishing_Modules.telegram_user_manager import USERS_JSON_PATH
                if storage_group_id and upload_fn and os.path.exists(USERS_JSON_PATH):
                    users_upload_res = upload_fn("sendDocument", storage_group_id, "document", USERS_JSON_PATH, caption=f"👤 **[VAULT BACKUP]** `telegram_users.json` (Updated {time.strftime('%H:%M:%S')})")
                    if users_upload_res and isinstance(users_upload_res, dict):
                        users_doc_id = users_upload_res.get("document", {}).get("file_id")
                        self.vault_index["telegram_users_file_id"] = users_doc_id
                        logger.info("✅ [STORAGE MANAGER] Uploaded updated telegram_users.json to Storage Group (file_id: %s)", users_doc_id)
            except Exception as _users_err:
                logger.warning("⚠️ Could not upload telegram_users.json: %s", _users_err)
        except Exception as _pool_err:
            logger.warning("⚠️ Could not save/upload metadata_pool.json: %s", _pool_err)

        session_id = f"sess_{int(time.time())}"
        c2 = self.vault_index.setdefault("column_2_downloaded_sources", {})
        c2.setdefault("by_social_media_id", {})[social_url] = clip_entry
        c2.setdefault("by_session_id", {})[session_id] = clip_entry
        if user_id:
            c2.setdefault("by_user_id", {}).setdefault(user_id, {})[session_id] = clip_entry

        self._save_local_index()

        if upload_fn and storage_group_id:
            self.upload_and_pin_vault_index_sync(upload_fn=upload_fn)
        return {
            "raw_file_id": raw_file_id,
            "method": method,
            "file_param": file_param,
            "clip_entry": clip_entry
        }

    def update_inpainted_clean_source_in_vault(self, clean_video_path: str, clip_folder_name: str) -> Optional[str]:
        """
        [APPROACH 1 - CLEAN SOURCE REPLACEMENT]
        When Step 2.5 upfront inpainting cleans the raw video, this method uploads
        video_inpainted_clean.mp4 to Telegram Storage Group, captures the returned file_id,
        and updates Column 2 of master_vault_index.json so that raw_video_file_id points
        to the CLEAN inpainted video file.

        This guarantees that any future hydration or retry (even on a fresh runner)
        downloads the clean video directly from Telegram, making re-edits 100% immune
        to Gemini watermark detection flakes.
        """
        if not clean_video_path or not os.path.exists(clean_video_path):
            return None

        storage_group_id = os.getenv("TELEGRAM_STORAGE_GROUP_ID")
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        if not storage_group_id or not bot_token:
            return None

        try:
            filename = os.path.basename(clean_video_path)
            caption = f"🎬 [CLEAN INPAINTED SOURCE] `{filename}`\n🆔 `{clip_folder_name}`"
            logger.info("🧼 [VAULT CLEAN UPLOAD] Uploading inpainted clean video '%s' to Telegram Storage Group...", filename)

            upload_res = _send_telegram_file_sync("sendVideo", storage_group_id, "video", clean_video_path, caption=caption)
            if not upload_res or not isinstance(upload_res, dict) or not upload_res.get("ok"):
                upload_res = _send_telegram_file_sync("sendDocument", storage_group_id, "document", clean_video_path, caption=caption)

            clean_file_id = None
            if upload_res and isinstance(upload_res, dict) and upload_res.get("ok"):
                res_doc = upload_res.get("result", {})
                clean_file_id = res_doc.get("video", {}).get("file_id") or res_doc.get("document", {}).get("file_id")

            if clean_file_id:
                logger.info("✅ [VAULT CLEAN UPLOAD] Uploaded clean video to Telegram Storage Group — file_id: %s", clean_file_id[:20])

                # Update Column 2 in master_vault_index.json
                c2 = self.vault_index.setdefault("column_2_downloaded_sources", {})
                c2_url = c2.setdefault("by_social_media_id", {})
                c2_sess = c2.setdefault("by_session_id", {})

                updated = False
                for entry_dict in (c2_url, c2_sess):
                    for k, entry in list(entry_dict.items()):
                        if isinstance(entry, dict):
                            s_id = str(entry.get("session_id", ""))
                            u_str = str(entry.get("social_media_id", ""))
                            if clip_folder_name.lower() in s_id.lower() or clip_folder_name.lower() in u_str.lower() or clip_folder_name.lower() in k.lower():
                                entry["raw_video_file_id"] = clean_file_id
                                entry["inpainted_clean_file_id"] = clean_file_id
                                entry["is_inpainted"] = True
                                updated = True

                if updated:
                    self._save_local_index()
                    self.upload_and_pin_vault_index_sync()
                    logger.info("📌 [VAULT CLEAN UPDATE] Updated Column 2 raw_video_file_id to point to clean inpainted video for '%s'", clip_folder_name)

                return clean_file_id
        except Exception as _e:
            logger.warning("⚠️ Could not upload/update clean video in Telegram vault: %s", _e)

        return None

    def sync_visual_pool_metadata(self, clip_id: str, clip_data: Dict[str, Any]) -> bool:
        """
        [AUTOMATIC] Synchronizes visual clip intelligence, video file_ids, and editing_plan
        into data/visual_pool_metadata.json and uploads it to Telegram Storage Group.
        """
        if not clip_id or not isinstance(clip_data, dict):
            return False

        vpm_path = os.path.join(DATA_DIR, "visual_pool_metadata.json")
        vpm_data = {"version": 2, "updated_at": time.time(), "clips": {}}
        if os.path.exists(vpm_path):
            try:
                with open(vpm_path, "r", encoding="utf-8") as f:
                    vpm_data = json.load(f)
            except Exception as _re:
                logger.debug("Notice reading visual_pool_metadata.json: %s", _re)

        clips_dict = vpm_data.setdefault("clips", {})
        existing_clip = clips_dict.get(clip_id, {})

        vis_ctx = clip_data.get("visual_context") or {}
        editing_plan = clip_data.get("editing_plan") or {}
        audio_data = clip_data.get("audio_data") or {}

        # Look up file_ids from Column 1 / Column 2 index if missing
        master_file_id = clip_data.get("master_video_file_id") or existing_clip.get("master_video_file_id")
        raw_file_id = clip_data.get("raw_video_file_id") or existing_clip.get("raw_video_file_id")
        extracted_audio_file_id = clip_data.get("extracted_audio_file_id") or existing_clip.get("extracted_audio_file_id")
        selected_bgm_file_id = audio_data.get("selected_bgm_file_id") or editing_plan.get("selected_bgm_file_id") or existing_clip.get("selected_bgm_file_id")
        selected_bgm_track = audio_data.get("selected_bgm_track") or audio_data.get("selected_audio_track") or editing_plan.get("selected_bgm_track") or existing_clip.get("selected_bgm_track")

        c1 = self.vault_index.get("column_1_processed_reels", {}).get("by_session_id", {})
        for sess_id, entry in c1.items():
            if clip_id in sess_id or sess_id in clip_id:
                if not master_file_id:
                    master_file_id = entry.get("master_video_file_id")
                break

        c2 = self.vault_index.get("column_2_downloaded_sources", {}).get("by_session_id", {})
        for sess_id, entry in c2.items():
            if clip_id in sess_id or sess_id in clip_id:
                if not raw_file_id:
                    raw_file_id = entry.get("raw_video_file_id")
                if not extracted_audio_file_id:
                    extracted_audio_file_id = entry.get("extracted_audio_file_id")
                break

        updated_entry = {
            "clip_id": clip_id,
            "social_media_id": clip_data.get("social_media_id") or existing_clip.get("social_media_id", "direct_upload"),
            "master_video_file_id": master_file_id,
            "raw_video_file_id": raw_file_id,
            "extracted_audio_file_id": extracted_audio_file_id,
            "selected_bgm_file_id": selected_bgm_file_id,
            "selected_bgm_track": selected_bgm_track,
            "created_at": clip_data.get("created_at") or existing_clip.get("created_at", time.time()),
            "intent": vis_ctx.get("intent") or existing_clip.get("intent", "viral_reel"),
            "tone": vis_ctx.get("tone") or existing_clip.get("tone", "aspirational"),
            "editing_style": vis_ctx.get("editing_style") or existing_clip.get("editing_style", "fast_paced"),
            "recommended_narrative": vis_ctx.get("recommended_narrative") or existing_clip.get("recommended_narrative", "lifestyle"),
            "engagement_hook": vis_ctx.get("engagement_hook") or existing_clip.get("engagement_hook", ""),
            "detected_entities": vis_ctx.get("detected_entities") or existing_clip.get("detected_entities", []),
            "feature_flags": vis_ctx.get("feature_flags") or existing_clip.get("feature_flags", {}),
            "speech_intelligence": vis_ctx.get("speech_intelligence") or existing_clip.get("speech_intelligence", {}),
            "editing_plan": editing_plan or existing_clip.get("editing_plan", {}),
            "monetization_safe": vis_ctx.get("safety", {}).get("monetization_safe", True)
        }

        clips_dict[clip_id] = updated_entry
        vpm_data["clips"] = clips_dict
        vpm_data["updated_at"] = time.time()

        try:
            temp_vpm = vpm_path + ".tmp"
            with open(temp_vpm, "w", encoding="utf-8") as f:
                json.dump(vpm_data, f, indent=2, ensure_ascii=False)
            _safe_replace(temp_vpm, vpm_path)
            logger.info("✅ [VISUAL POOL METADATA] Automatically updated visual_pool_metadata.json for clip '%s'", clip_id)

            storage_group_id = os.getenv("TELEGRAM_STORAGE_GROUP_ID")
            from Publishing_Modules.telegram_vault_indexer import _send_telegram_file_sync
            if storage_group_id and os.path.exists(vpm_path):
                upload_res = _send_telegram_file_sync(
                    "sendDocument",
                    storage_group_id,
                    "document",
                    vpm_path,
                    caption=f"🎬 **[VAULT BACKUP]** `visual_pool_metadata.json` (Updated {time.strftime('%H:%M:%S')})"
                )
                if upload_res and isinstance(upload_res, dict) and upload_res.get("ok"):
                    vpm_doc_id = upload_res.get("result", {}).get("document", {}).get("file_id")
                    if vpm_doc_id:
                        self.vault_index["visual_pool_metadata_file_id"] = vpm_doc_id
                        self._save_local_index()
                        logger.info("✅ [VISUAL POOL METADATA VAULT BACKUP] Uploaded & PINNED visual_pool_metadata.json (file_id: %s)", vpm_doc_id[:15])
            return True
        except Exception as _ve:
            logger.warning("⚠️ Failed to sync visual_pool_metadata.json for clip '%s': %s", clip_id, _ve)
            return False

    async def record_downloaded_source(
        self,
        bot,
        social_url: str,
        session_id: str,
        raw_video_path: Optional[str] = None,
        audio_path: Optional[str] = None,
        beat_math: Optional[Dict[str, Any]] = None,
        user_id: Optional[str] = None,
        pin_now: bool = True,
    ) -> Dict[str, Any]:
        """
        Column 2 Record: Uploads raw source video and extracted audio to TELEGRAM_STORAGE_GROUP_ID,
        saves file_ids under column_2_downloaded_sources, and re-pins master_vault_index.json.
        """
        storage_group_id = os.getenv("TELEGRAM_STORAGE_GROUP_ID")
        raw_file_id = None
        audio_file_id = None

        if storage_group_id and bot:
            try:
                if raw_video_path and os.path.exists(raw_video_path):
                    with open(raw_video_path, "rb") as rf:
                        rmsg = await bot.send_video(
                            chat_id=int(storage_group_id),
                            video=rf,
                            caption=f"📥 **[VAULT RAW SOURCE]** `{os.path.basename(raw_video_path)}`\n🔗 `{social_url}`\n🆔 `{session_id}`" + (f"\n👤 User: `{user_id}`" if user_id else ""),
                            read_timeout=300,
                            write_timeout=300
                        )
                        if rmsg and rmsg.video:
                            raw_file_id = rmsg.video.file_id

                if audio_path and os.path.exists(audio_path):
                    logger.info(f"🎙️ [VAULT AUDIO UPLOAD] Sending extracted audio ({os.path.basename(audio_path)}, {os.path.getsize(audio_path)} bytes) to Storage Group...")
                    try:
                        with open(audio_path, "rb") as af:
                            amsg = await bot.send_document(
                                chat_id=int(storage_group_id),
                                document=af,
                                filename=os.path.basename(audio_path),
                                caption=f"🎵 **[VAULT AUDIO EXTRACT]** `{os.path.basename(audio_path)}`\n🆔 `{session_id}`" + (f"\n👤 User: `{user_id}`" if user_id else ""),
                                read_timeout=300,
                                write_timeout=300
                            )
                            if amsg:
                                audio_file_id = amsg.document.file_id if amsg.document else (amsg.audio.file_id if amsg.audio else None)
                                logger.info(f"✅ [VAULT AUDIO SUCCESS] Extracted audio file_id captured: {audio_file_id}")
                    except Exception as _aud_err:
                        logger.warning(f"❌ [VAULT AUDIO ERROR] Audio upload failed: {_aud_err}")
            except Exception as e:
                logger.warning(f"⚠️ Vault raw source upload warning: {e}")

        entry = {
            "social_media_id": social_url,
            "session_id": session_id,
            "raw_video_file_id": raw_file_id,
            "extracted_audio_file_id": audio_file_id,
            "audio_math": beat_math or {},
            "downloaded_at": time.time(),
            "user_id": user_id,
        }

        c2 = self.vault_index.setdefault("column_2_downloaded_sources", {})
        c2.setdefault("by_social_media_id", {})[social_url] = entry
        c2.setdefault("by_session_id", {})[session_id] = entry
        
        if user_id:
            c2.setdefault("by_user_id", {}).setdefault(user_id, {})[session_id] = entry

        self._save_local_index()
        await self._upload_and_pin_index(bot, storage_group_id)
        logger.info(f"📦 [VAULT RECORD] Recorded Column 2 source for URL: {social_url[:60]}" + (f" (User: {user_id})" if user_id else ""))
        return entry

    async def record_processed_reel(
        self,
        bot,
        session_id: str,
        social_url: Optional[str],
        custom_title: Optional[str],
        master_video_path: str,
        clip_intel: Optional[Dict[str, Any]] = None,
        lyric_intel: Optional[Dict[str, Any]] = None,
        master_file_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Column 1 Record: Saves rendered master reel intelligence and file_id into
        column_1_processed_reels, updates local index, and re-pins master_vault_index.json.
        """
        storage_group_id = os.getenv("TELEGRAM_STORAGE_GROUP_ID")

        if not master_file_id and storage_group_id and bot and master_video_path and os.path.exists(master_video_path):
            try:
                filename = os.path.basename(master_video_path)
                logger.info("🎬 [VAULT REEL UPLOAD] Sending master reel video (%s) to Storage Group...", filename)
                with open(master_video_path, "rb") as vf:
                    vmsg = await bot.send_video(
                        chat_id=int(storage_group_id),
                        video=vf,
                        caption=f"🎬 **[VAULT MASTER REEL]** `{filename}`\n🆔 `{session_id}`" + (f"\n👤 User: `{user_id}`" if user_id else ""),
                        read_timeout=300,
                        write_timeout=300
                    )
                    if vmsg and vmsg.video:
                        master_file_id = vmsg.video.file_id
                        logger.info("✅ [VAULT REEL SUCCESS] Master video reel file_id captured: %s", master_file_id)
            except Exception as _mv_err:
                logger.warning("⚠️ Could not upload master video reel to Telegram Storage Group: %s", _mv_err)

        entry = {
            "session_id": session_id,
            "social_media_id": social_url or "direct_upload",
            "custom_title": custom_title,
            "master_video_file_id": master_file_id,
            "video_path": os.path.abspath(master_video_path),
            "created_at": time.time(),
            "audio_data": {
                "lyric_intel": lyric_intel or {},
            },
            "visual_data": clip_intel or {},
            "editing_plan_history": [],
            "pipeline_execution_trajectory": {
                "stage_0_intent_classification": {},
                "stage_1_visual_forensics": clip_intel or {},
                "stage_2_audio_intelligence": lyric_intel or {},
                "stage_3_attempts_and_re_edits": [],
                "stage_4_final_verdict": {"status": "AWAITING_REVIEW", "timestamp": time.time()},
            },
            "user_id": user_id,
        }

        c1 = self.vault_index.setdefault("column_1_processed_reels", {})
        existing = c1.get("by_session_id", {}).get(session_id)
        if existing:
            if "editing_plan_history" in existing:
                entry["editing_plan_history"] = existing["editing_plan_history"]
            if "pipeline_execution_trajectory" in existing:
                entry["pipeline_execution_trajectory"] = existing["pipeline_execution_trajectory"]

        c1.setdefault("by_session_id", {})[session_id] = entry
        if social_url:
            c1.setdefault("by_social_media_id", {})[social_url] = session_id
        
        if user_id:
            c1.setdefault("by_user_id", {}).setdefault(user_id, {})[session_id] = entry

        self._save_local_index()
        await self._upload_and_pin_index(bot, storage_group_id)
        try:
            self.sync_visual_pool_metadata(session_id, entry)
        except Exception as _vp_err:
            logger.debug("Notice syncing visual_pool_metadata in record_processed_reel: %s", _vp_err)
        logger.info(f"🎬 [VAULT RECORD] Recorded Column 1 master reel for Session: {session_id}" + (f" (User: {user_id})" if user_id else ""))
        return entry

    async def update_pipeline_trajectory(
        self,
        bot,
        session_id: str,
        stage_name: str,
        stage_data: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """
        AI Trajectory Store: Records structured, un-mixed pipeline stage logs into
        column_1_processed_reels -> pipeline_execution_trajectory.
        """
        c1 = self.vault_index.setdefault("column_1_processed_reels", {})
        session_entry = c1.setdefault("by_session_id", {}).setdefault(session_id, {
            "session_id": session_id,
            "created_at": time.time(),
        })

        trajectory = session_entry.setdefault("pipeline_execution_trajectory", {
            "stage_0_intent_classification": {},
            "stage_1_visual_forensics": {},
            "stage_2_audio_intelligence": {},
            "stage_3_attempts_and_re_edits": [],
            "stage_4_final_verdict": {},
        })

        stage_key = {
            "stage_0_intent": "stage_0_intent_classification",
            "stage_1_visual": "stage_1_visual_forensics",
            "stage_2_audio": "stage_2_audio_intelligence",
            "stage_3_attempts": "stage_3_attempts_and_re_edits",
            "stage_4_verdict": "stage_4_final_verdict",
        }.get(stage_name, stage_name)

        if stage_key == "stage_3_attempts_and_re_edits":
            if not isinstance(trajectory.get("stage_3_attempts_and_re_edits"), list):
                trajectory["stage_3_attempts_and_re_edits"] = []
            trajectory["stage_3_attempts_and_re_edits"].append(stage_data)
        else:
            trajectory[stage_key] = stage_data

        session_entry["updated_at"] = time.time()
        self._save_local_index()
        storage_group_id = os.getenv("TELEGRAM_STORAGE_GROUP_ID")
        await self._upload_and_pin_index(bot, storage_group_id)
        logger.info(f"🧠 [TRAJECTORY RECORD] Updated {stage_key} for Session: {session_id}")
        return trajectory

    async def record_plan_attempt(
        self,
        bot,
        session_id: str,
        attempt_number: int,
        editing_plan: Dict[str, Any],
        user_approved: bool = False,
        user_feedback: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        RAG Creator Behavior Store: Appends an editing plan attempt (and user approval/feedback)
        to column_1_processed_reels -> editing_plan_history.
        """
        c1 = self.vault_index.get("column_1_processed_reels", {}).get("by_session_id", {})
        session_entry = c1.get(session_id)
        if not session_entry:
            logger.warning(f"⚠️ Cannot record plan attempt: session '{session_id}' not found in Column 1 index.")
            return None

        history = session_entry.setdefault("editing_plan_history", [])
        attempt_record = {
            "attempt_number": attempt_number,
            "timestamp": time.time(),
            "user_approved": user_approved,
            "user_feedback": user_feedback or ("Approved by user" if user_approved else "Rejected/Re-edit requested"),
            "editing_plan": editing_plan or {},
        }
        history.append(attempt_record)
        session_entry["editing_plan_history"] = sorted(history, key=lambda x: int(x.get("attempt_number", 0)))

        self._save_local_index()
        storage_group_id = os.getenv("TELEGRAM_STORAGE_GROUP_ID")
        await self._upload_and_pin_index(bot, storage_group_id)
        logger.info(f"🧠 [RAG PLAN RECORD] Recorded Attempt {attempt_number} (approved={user_approved}) for Session: {session_id}")
        return attempt_record

    async def _upload_and_pin_index(self, bot, storage_group_id: Optional[str]):
        """Uploads updated master_vault_index.json to TELEGRAM_STORAGE_GROUP_ID and pins it."""
        if not storage_group_id or not bot or not os.path.exists(self.index_file):
            return

        try:
            with open(self.index_file, "rb") as idf:
                doc_msg = await bot.send_document(
                    chat_id=int(storage_group_id),
                    document=idf,
                    filename="master_vault_index.json",
                    caption=f"📌 **[VAULT MASTER INDEX]** Auto-Synced\n🕒 `{time.strftime('%Y-%m-%d %H:%M:%S')}`\n📊 Reels: `{len(self.vault_index.get('column_1_processed_reels', {}).get('by_session_id', {}))}` | Sources: `{len(self.vault_index.get('column_2_downloaded_sources', {}).get('by_social_media_id', {}))}`"
                )
                if doc_msg and doc_msg.message_id:
                    await bot.pin_chat_message(
                        chat_id=int(storage_group_id),
                        message_id=doc_msg.message_id,
                        disable_notification=True
                    )
                    self.vault_index["pinned_message_id"] = doc_msg.message_id
                    logger.info(f"📌 [VAULT PIN] Pinned updated master_vault_index.json (Message ID: {doc_msg.message_id})")
        except Exception as e:
            logger.warning(f"⚠️ Vault index upload/pin notice: {e}")


# ── ADVISORY LOCK ─────────────────────────────────────────────────────────────
# Stored as a `lock` key inside the shared pinned master_vault_index.json so
# concurrent ephemeral runners (GitHub Actions / Docker / local) all see the
# same lock state without any external infrastructure.
#
# Advisory lock — not a perfect distributed lock. The TOCTOU window:
#   runner A sees lock free → runner B sees lock free → A pushes → B pushes
#   Both then re-pull and one will see the other's holder_id, correctly
#   backing off. The window is the Telegram upload round-trip (~1s), not
#   the 2s poll interval.
# Good enough for O(10) concurrent runners; not suitable for O(1000).

_LOCK_TTL_SEC = float(os.getenv("VAULT_LOCK_TTL_SEC", "45"))
_LOCK_MAX_WAIT_SEC = float(os.getenv("VAULT_LOCK_MAX_WAIT_SEC", "60"))
_LOCK_POLL_SEC = 2.0

# Cache the holder id for the lifetime of this process
_LOCK_HOLDER_ID: Optional[str] = None
_LOCK_HOLDER_LOCK = threading.Lock()


def _get_holder_id() -> str:
    global _LOCK_HOLDER_ID
    with _LOCK_HOLDER_LOCK:
        if _LOCK_HOLDER_ID is None:
            node = os.getenv("COMPUTERNAME") or os.getenv("HOSTNAME") or "host"
            _LOCK_HOLDER_ID = f"{node}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        return _LOCK_HOLDER_ID


def _peek_pinned_lock_state(last_known_msg_id: Optional[int]) -> Dict[str, Any]:
    """
    Cheap lock poll — only 1 Telegram API call (getChat) when the pinned
    message hasn't changed. Only downloads the full index (2 extra API calls)
    when the pinned message_id is different from what we last saw.

    Returns dict:
        {"lock": <lock_dict_or_None>, "msg_id": <int_or_None>}
    """
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    storage_group_id = os.getenv("TELEGRAM_STORAGE_GROUP_ID", "").strip()
    if not bot_token or not storage_group_id:
        return {"lock": None, "msg_id": None}

    try:
        url = f"https://api.telegram.org/bot{bot_token}/getChat?chat_id={storage_group_id}"
        req = urllib.request.Request(url, headers={"User-Agent": "AMTCE-VaultLock/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())

        if not data.get("ok"):
            return {"lock": None, "msg_id": last_known_msg_id}

        pinned = data.get("result", {}).get("pinned_message") or {}
        current_msg_id = pinned.get("message_id")

        # ── Fast path: pinned message unchanged → use cached local lock value ──
        if current_msg_id and current_msg_id == last_known_msg_id:
            indexer = TelegramVaultIndexer()
            return {"lock": indexer.vault_index.get("lock"), "msg_id": current_msg_id}

        # ── Slow path: pinned message changed → download the new index ──
        doc = pinned.get("document", {})
        file_id = doc.get("file_id") if doc.get("file_name") in (
            "master_vault_index.json", "telegram_media_index.json") else None

        if file_id:
            indexer = TelegramVaultIndexer()
            if indexer.download_vault_file_by_id(file_id, indexer.index_file):
                indexer.vault_index = indexer._load_local_index()
                return {"lock": indexer.vault_index.get("lock"), "msg_id": current_msg_id}

        return {"lock": None, "msg_id": current_msg_id}

    except Exception as e:
        logger.debug("[vault_lock] _peek_pinned_lock_state error: %s", e)
        return {"lock": None, "msg_id": last_known_msg_id}


def acquire_lock(purpose: str = "", ttl_sec: float = _LOCK_TTL_SEC,
                 max_wait_sec: float = _LOCK_MAX_WAIT_SEC) -> Optional[str]:
    """
    Advisory distributed lock via the shared pinned vault index.

    Polling cost: 1 Telegram API call per 2s when waiting (getChat only).
    Only pulls the full index (3 API calls) when the pinned message changed
    — i.e. when a write actually happened since the last poll.

    Returns the holder_id string on success (pass to release_lock),
    or None on timeout.

    Usage:
        holder = acquire_lock("uploading pool_metadata")
        if holder is None:
            raise RuntimeError("Could not acquire vault lock")
        try:
            ... do read-modify-write on vault ...
        finally:
            release_lock(holder)
    """
    holder = _get_holder_id()
    deadline = time.time() + max_wait_sec

    # Seed from whatever local index already has for fast-path on first poll
    indexer = TelegramVaultIndexer()
    last_msg_id = indexer.vault_index.get("pinned_message_id")

    while time.time() < deadline:
        peek = _peek_pinned_lock_state(last_msg_id)
        last_msg_id = peek.get("msg_id") or last_msg_id
        lock = peek.get("lock")
        now = time.time()

        lock_free = (
            lock is None
            or not lock.get("held_by")
            or float(lock.get("expires_at", 0)) < now
        )

        if lock_free:
            # Write our claim into the index
            indexer2 = TelegramVaultIndexer()
            indexer2.sync_pinned_index_from_telegram_sync()  # fresh pull before write
            indexer2.vault_index["lock"] = {
                "held_by": holder,
                "purpose": purpose,
                "acquired_at": now,
                "expires_at": now + ttl_sec,
            }
            indexer2._save_local_index()
            try:
                indexer2.upload_and_pin_vault_index_sync(
                    upload_fn=lambda method, chat_id, file_key, file_path, caption=None:
                        _send_telegram_file_sync(method, chat_id, file_key, file_path, caption)
                )
            except Exception as _push_err:
                logger.debug("[vault_lock] push during acquire: %s", _push_err)

            # Confirm we won — re-peek (forces full download since msg_id changed)
            confirm = _peek_pinned_lock_state(None)
            confirm_lock = confirm.get("lock") or {}
            if confirm_lock.get("held_by") == holder:
                logger.info("[vault_lock] acquired by %s (purpose=%s)", holder, purpose)
                return holder
            # Lost the race — another runner's write landed after ours; back off and retry
            logger.debug("[vault_lock] lost race to %s, retrying...", confirm_lock.get("held_by"))

        time.sleep(_LOCK_POLL_SEC)

    logger.warning("[vault_lock] timed out waiting for lock (purpose=%s)", purpose)
    return None


def release_lock(holder: str) -> bool:
    """
    Releases the advisory lock only if it is still held by `holder`.
    Never clears another process's lock.
    Returns True if the lock was released, False if it was already gone or
    held by someone else.
    """
    indexer = TelegramVaultIndexer()
    indexer.sync_pinned_index_from_telegram_sync()
    lock = indexer.vault_index.get("lock") or {}

    if lock.get("held_by") != holder:
        logger.debug("[vault_lock] release skipped — not held by %s (current: %s)",
                     holder, lock.get("held_by"))
        return False

    indexer.vault_index["lock"] = None
    indexer._save_local_index()
    try:
        indexer.upload_and_pin_vault_index_sync(
            upload_fn=lambda method, chat_id, file_key, file_path, caption=None:
                _send_telegram_file_sync(method, chat_id, file_key, file_path, caption)
        )
    except Exception as _rel_err:
        logger.debug("[vault_lock] push during release: %s", _rel_err)

    logger.info("[vault_lock] released by %s", holder)
    return True


class vault_session:
    """
    Context manager that acquires the vault advisory lock on enter and
    releases it on exit, even if an exception is raised. Use this for any
    read-modify-write sequence on the vault to prevent concurrent runner
    overwrites.

    Example:
        with vault_session("update pool_metadata") as acquired:
            if not acquired:
                raise RuntimeError("Could not get vault lock in time")
            # safe to read-modify-write here
    """

    def __init__(self, purpose: str = "", ttl_sec: float = _LOCK_TTL_SEC,
                 max_wait_sec: float = _LOCK_MAX_WAIT_SEC):
        self.purpose = purpose
        self.ttl_sec = ttl_sec
        self.max_wait_sec = max_wait_sec
        self._holder: Optional[str] = None

    def __enter__(self) -> bool:
        self._holder = acquire_lock(self.purpose, self.ttl_sec, self.max_wait_sec)
        return self._holder is not None

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._holder:
            release_lock(self._holder)
        return False  # never swallow exceptions
