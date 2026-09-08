"""
Telegram_Storage_Modules / telegram_vault_indexer.py
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
import re
import json
import time
import uuid
import threading
import urllib.request
import logging
from typing import Dict, Any, Optional, List

logger = logging.getLogger("telegram_vault_indexer")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(_REPO_ROOT, "data")
MASTER_INDEX_FILE = os.path.join(DATA_DIR, "master_vault_index.json")

# Cooldown guard: prevents duplicate vault hydration within 60 seconds
_LAST_HYDRATION_TIMESTAMP = 0.0


def _empty_vault_index() -> Dict[str, Any]:
    return {
        "version": 3.0,
        "updated_at": time.time(),
        "pinned_message_id": None,
        "lock": None,
        "pool_metadata_file_id": None,
        "telegram_users_file_id": None,
        "source_accounts_file_id": None,
        "scraper_rotation_pointer_file_id": None,
        "telegram_sessions_file_id": None,
    }


def _send_telegram_file_sync(
    method: str,
    chat_id: str,
    file_key: str,
    file_path: str,
    caption: Optional[str] = None,
    custom_filename: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not bot_token or not chat_id or not os.path.exists(file_path):
        return None

    filename = custom_filename or os.path.basename(file_path)
    file_size = os.path.getsize(file_path)

    # Telegram Bot API HTTP limit is 50MB. Proactively route files >= 45MB via Pyrogram MTProto
    if file_size >= 45 * 1024 * 1024:
        logger.info(f"🚀 File '{filename}' is {file_size / (1024*1024):.1f}MB (>=45MB). Using Pyrogram MTProto upload directly...")
        try:
            from Telegram_Storage_Modules.telegram_http import upload_file_with_pyrogram
            res = upload_file_with_pyrogram(
                local_path=file_path,
                chat_id=chat_id,
                caption=caption or "",
                file_name=filename,
                as_video=(method == "sendVideo" or file_key == "video")
            )
            if res and res.get("ok"):
                return res
        except Exception as _pyro_err:
            logger.warning(f"⚠️ Pyrogram direct upload error for {filename}: {_pyro_err}")

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
        logger.warning(f"⚠️ Telegram HTTP file upload failed for {filename}: {err}")
        # Automatic fallback to Pyrogram MTProto upload (handles HTTP 413, socket drops, etc.)
        try:
            from Telegram_Storage_Modules.telegram_http import upload_file_with_pyrogram
            logger.info(f"🔄 Attempting Pyrogram MTProto upload fallback for {filename}...")
            res = upload_file_with_pyrogram(
                local_path=file_path,
                chat_id=chat_id,
                caption=caption or "",
                file_name=filename,
                as_video=(method == "sendVideo" or file_key == "video")
            )
            if res and res.get("ok"):
                return res
        except Exception as _pyro_fallback_err:
            logger.warning(f"⚠️ Pyrogram fallback also failed for {filename}: {_pyro_fallback_err}")
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
                    if isinstance(data, dict) and ("version" in data or "pool_metadata_file_id" in data):
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
        from Telegram Storage Group into dest_path via Telegram Bot API getFile or Pyrogram MTProto.
        """
        if not file_id:
            return False
        from Telegram_Storage_Modules.telegram_http import download_file_by_id
        return download_file_by_id(file_id, dest_path)

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
                from Telegram_Storage_Modules.telegram_user_manager import USERS_JSON_PATH, load_all_users, save_all_users
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

            # Step 5: Download telegram_sessions.json (Master Active Sessions Index)
            ts_file_id = self.vault_index.get("telegram_sessions_file_id")
            if ts_file_id:
                ts_path = os.path.join(DATA_DIR, "telegram_sessions.json")
                results["telegram_sessions"] = self.download_vault_file_by_id(ts_file_id, ts_path)

            # Step 6: Download scraper_rotation_pointer.json (Master Scraper Rotation Pointer)
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
        file_id stored in pool_metadata.json or direct telegram_file_id string.
        """
        if not track_name_or_file_id:
            return None

        resolved_file_id = file_id
        filename = os.path.basename(track_name_or_file_id)

        if not resolved_file_id and len(track_name_or_file_id) > 20 and not track_name_or_file_id.endswith((".mp3", ".wav", ".m4a")):
            resolved_file_id = track_name_or_file_id
            filename = f"bgm_{resolved_file_id[:10]}.wav"

        if not dest_dir:
            dest_dir = os.path.join(_REPO_ROOT, "Original_audio", "active")
        os.makedirs(dest_dir, exist_ok=True)

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

        active_path = os.path.join(_REPO_ROOT, "Original_audio", "active", filename)
        if os.path.exists(active_path) and os.path.getsize(active_path) > 1024:
            logger.info("⚡ [LOCAL POOL CACHE HIT] BGM track '%s' found in active pool — skipping Telegram download.", filename)
            return active_path

        if not resolved_file_id:
            track_stem = os.path.splitext(filename.lower())[0].replace("vault_bgm_", "").replace("bgm_", "")
            c2_sess = self.vault_index.get("column_2_downloaded_sources", {}).get("by_session_id", {})
            for sess_id, entry in c2_sess.items():
                if entry.get("extracted_audio_file_id"):
                    s_id = str(sess_id).lower()
                    u_str = str(entry.get("social_media_id", "")).lower()
                    sc_str = str(entry.get("shortcode", "")).lower()
                    if track_stem in s_id or track_stem in u_str or (sc_str and track_stem in sc_str) or filename.lower() in u_str:
                        resolved_file_id = entry["extracted_audio_file_id"]
                        break

            if not resolved_file_id:
                c2_soc = self.vault_index.get("column_2_downloaded_sources", {}).get("by_social_media_id", {})
                for _url, entry in c2_soc.items():
                    if entry.get("extracted_audio_file_id"):
                        s_id = str(entry.get("session_id", "")).lower()
                        u_str = str(entry.get("social_media_id", "")).lower()
                        sc_str = str(entry.get("shortcode", "")).lower()
                        if track_stem in s_id or track_stem in u_str or (sc_str and track_stem in sc_str) or filename.lower() in u_str:
                            resolved_file_id = entry["extracted_audio_file_id"]
                            break

        if resolved_file_id:
            logger.info("📥 [VAULT BGM HYDRATION] Fetching BGM '%s' from Telegram Storage Group (file_id: %s)...", filename, resolved_file_id[:15])
            if self.download_vault_file_by_id(resolved_file_id, local_path):
                logger.info("✅ [VAULT BGM HYDRATION SUCCESS] Downloaded BGM '%s' from Telegram Storage Group!", filename)
                return local_path

        return None

    def get_vault_audio_pool(self, current_clip_id: Optional[str] = None) -> Dict[str, Any]:
        """Returns dictionary of all audio track metadata indexed in Column 2 & Column 1 of master_vault_index.json."""
        pool = {}
        clip_stem = current_clip_id.lower().strip() if current_clip_id else ""

        c2 = self.vault_index.get("column_2_downloaded_sources", {}).get("by_social_media_id", {})
        for _url, entry in c2.items():
            file_id = entry.get("extracted_audio_file_id")
            if not file_id:
                continue

            sess_id = entry.get("session_id", "audio_track")
            social_id = str(entry.get("social_media_id", "")).lower()

            if clip_stem and (
                clip_stem in sess_id.lower()
                or clip_stem in social_id
                or sess_id.lower().endswith(f"_{clip_stem}")
            ):
                continue

            shortcode_val = entry.get("shortcode") or ""
            if not shortcode_val and "/" in social_id:
                parts = [p for p in social_id.split("/") if p and not p.startswith("?")]
                shortcode_val = parts[-1].split("?")[0] if parts else ""
            clean_tag = shortcode_val or sess_id.replace("sess_", "").strip() or "track"
            fname = f"vault_bgm_{clean_tag}.wav"

            audio_math = entry.get("audio_math") or {}
            dur_val = audio_math.get("duration") or audio_math.get("duration_sec") or 0.0

            pool[fname] = {
                "file_id": file_id,
                "session_id": sess_id,
                "shortcode": shortcode_val,
                "tempo_bpm": audio_math.get("tempo_bpm", 120.0),
                "dominant_emotion": audio_math.get("dominant_emotion", "hype"),
                "energy_profile": audio_math.get("energy_profile", "medium"),
                "has_vocals": audio_math.get("has_vocals", False),
                "language": audio_math.get("language", "unknown"),
                "duration": dur_val,
                "duration_sec": dur_val,
                "last_used": entry.get("timestamp", 0),
                "usage_count": 0,
                "is_source_extract": True,
            }

        pm_path = os.path.join(_REPO_ROOT, "Original_audio", "pool_metadata.json")
        if os.path.exists(pm_path):
            try:
                with open(pm_path, "r", encoding="utf-8") as f:
                    pm_data = json.load(f)
                    files_dict = pm_data.get("files", pm_data) if isinstance(pm_data, dict) else {}
                    if isinstance(files_dict, dict):
                        for k, v in files_dict.items():
                            if isinstance(v, dict) and k != "social_media_id":
                                if clip_stem and clip_stem in k.lower():
                                    continue
                                if k in pool:
                                    pool[k].update(v)
                                else:
                                    pool[k] = v


            except Exception as _pme:
                logger.debug("Local pool metadata read notice: %s", _pme)

        return pool

    def upload_and_pin_vault_index_sync(self, upload_fn=None):
        """Uploads master_vault_index.json to TELEGRAM_STORAGE_GROUP_ID and pins it."""
        storage_group_id = os.getenv("TELEGRAM_STORAGE_GROUP_ID")
        if not storage_group_id or not upload_fn or not os.path.exists(self.index_file):
            return

        try:
            caption = f"📌 **[VAULT MASTER INDEX]** Auto-Synced\n🕒 `{time.strftime('%Y-%m-%d %H:%M:%S')}`"
            res = upload_fn("sendDocument", storage_group_id, "document", self.index_file, caption=caption)
            if res and isinstance(res, dict):
                msg_id = res.get("message_id")
                if msg_id:
                    self.vault_index["pinned_message_id"] = msg_id
                    self._save_local_index()
                    try:
                        from Telegram_Storage_Modules.telegram_http import pin_message
                        pin_message(message_id=int(msg_id), chat_id=str(storage_group_id))
                    except Exception as _p_call_err:
                        logger.warning("Notice on pinChatMessage call: %s", _p_call_err)
                    logger.info("📌 [VAULT PIN SUCCESS] Uploaded & PINNED master_vault_index.json in Storage Group (Message ID: %s)", msg_id)
        except Exception as _pin_err:
            logger.warning("⚠️ Vault index upload/pin notice: %s", _pin_err)

    def _normalize_search_keys(self, target: Any) -> List[str]:
        """Generates all normalized search keys (stripping manual_, URL parsing, etc.)."""
        raw_target = str(target).strip().strip("`")
        if raw_target.endswith("%60"):
            raw_target = raw_target[:-3].strip()
        clean_target = raw_target.replace("manual_", "").strip()
        extracted = None
        m_ig = re.search(r"/(?:p|reel|reels)/([A-Za-z0-9_-]+)", raw_target)
        if m_ig:
            extracted = m_ig.group(1)
        if not extracted:
            m_yt = re.search(r"(?:shorts/|v=|youtu\.be/)([A-Za-z0-9_-]{11})", raw_target)
            if m_yt:
                extracted = m_yt.group(1)
        if not extracted:
            m_tt = re.search(r"/video/(\d+)", raw_target)
            if m_tt:
                extracted = m_tt.group(1)

        search_keys = []
        for k in [raw_target, clean_target, extracted]:
            if not k:
                continue
            for cand in (k, k.lower(), k.split("?")[0].rstrip("/"), k.split("?")[0].rstrip("/") + "/"):
                if cand and cand not in search_keys:
                    search_keys.append(cand)
            k_nomanual = k.replace("manual_", "").strip()
            if k_nomanual and k_nomanual not in search_keys:
                search_keys.append(k_nomanual)
                search_keys.append(k_nomanual.lower())
                search_keys.append(f"manual_{k_nomanual}")
        return search_keys

    def find_entry_by_shortcode(self, shortcode: str) -> Optional[Dict[str, Any]]:
        """
        Finds video metadata entry in pool_metadata.json OR master_vault_index.json by shortcode or URL.
        Robustly extracts shortcode from URLs and handles 'manual_' prefixes.
        """
        if not shortcode:
            return None
        search_keys = self._normalize_search_keys(shortcode)

        found_entry = None
        # 1. Search pool_metadata.json -> files -> social_media_id
        try:
            from Audio_Modules.audio_pool_manager import AudioPoolManager
            pm = AudioPoolManager()
            clips = pm.metadata.get("files", {}).get("social_media_id", {})
            for k in search_keys:
                if k in clips:
                    found_entry = clips[k]
                    break

            if not found_entry:
                for stored_url, entry in clips.items():
                    entry_sc = str(entry.get("shortcode", "")).strip().lower()
                    stored_clean = stored_url.split("?")[0].rstrip("/").lower()
                    for k in search_keys:
                        kl = k.lower()
                        if (
                            (entry_sc and (entry_sc == kl or entry_sc == kl.replace("manual_", ""))) or
                            (kl and (kl in stored_url.lower() or kl.replace("manual_", "") in stored_url.lower())) or
                            (stored_clean and (stored_clean in kl or kl in stored_clean)) or
                            (kl and kl in str(entry.get("file_name", "")).lower())
                        ):
                            found_entry = entry
                            break
                    if found_entry:
                        break
        except Exception as e:
            logger.debug("Notice on find_entry_by_shortcode pool_metadata: %s", e)

        entry_raw_fid = None
        if found_entry:
            entry_raw_fid = (
                found_entry.get("media_file_ids", {}).get("raw_video_file_id") or
                found_entry.get("raw_video_file_id") or
                found_entry.get("raw_file_id")
            )

        # 2. Check master_vault_index.json -> column_2_downloaded_sources
        vault_entry = None
        try:
            c2_by_url = self.vault_index.get("column_2_downloaded_sources", {}).get("by_social_media_id", {})
            for k in search_keys:
                if k in c2_by_url:
                    vault_entry = c2_by_url[k]
                    break

            if not vault_entry:
                for stored_url, entry in c2_by_url.items():
                    entry_sc = str(entry.get("shortcode", "")).strip().lower()
                    for k in search_keys:
                        kl = k.lower()
                        if (
                            (entry_sc and (entry_sc == kl or entry_sc == kl.replace("manual_", ""))) or
                            (kl and (kl in stored_url.lower() or kl.replace("manual_", "") in stored_url.lower()))
                        ):
                            vault_entry = entry
                            break
                    if vault_entry:
                        break

            if not vault_entry:
                c2_by_sess = self.vault_index.get("column_2_downloaded_sources", {}).get("by_session_id", {})
                for k in search_keys:
                    if k in c2_by_sess:
                        vault_entry = c2_by_sess[k]
                        break
        except Exception as e:
            logger.debug("Notice on find_entry_by_shortcode master_vault_index: %s", e)

        # 3. Check TelegramSessionManager
        sess_entry = None
        try:
            from Telegram_Storage_Modules.telegram_session_manager import TelegramSessionManager
            sm = TelegramSessionManager()
            for k in search_keys:
                sess = sm.get_session(k)
                if sess:
                    sess_entry = {
                        "shortcode": sess.get("clip_id", "").replace("manual_", ""),
                        "session_id": sess.get("session_id"),
                        "raw_video_file_id": sess.get("raw_video_file_id"),
                        "raw_video_path": sess.get("raw_video_path"),
                        "media_file_ids": {
                            "raw_video_file_id": sess.get("raw_video_file_id")
                        }
                    }
                    break
        except Exception:
            pass

        # Resolve best raw_file_id from all sources
        best_raw_fid = (
            entry_raw_fid
            or (vault_entry.get("media_file_ids", {}).get("raw_video_file_id") if vault_entry else None)
            or (vault_entry.get("raw_video_file_id") if vault_entry else None)
            or (sess_entry.get("raw_video_file_id") if sess_entry else None)
        )

        if found_entry:
            if best_raw_fid and not entry_raw_fid:
                found_entry.setdefault("media_file_ids", {})["raw_video_file_id"] = best_raw_fid
                found_entry["raw_video_file_id"] = best_raw_fid
            return found_entry

        if vault_entry:
            if best_raw_fid:
                vault_entry.setdefault("media_file_ids", {})["raw_video_file_id"] = best_raw_fid
                vault_entry["raw_video_file_id"] = best_raw_fid
            return vault_entry

        return sess_entry

    def hydrate_raw_video_from_vault(self, identifier: str, dest_dir: Optional[str] = None) -> Optional[str]:
        """
        Downloads already-stored raw source video from Telegram Storage Group.
        Accepts social_url, shortcode, clip_id, or session_id.
        Resolves raw_video_file_id from pool_metadata.json, master_vault_index.json, or sessions.
        """
        if not identifier:
            return None

        search_keys = self._normalize_search_keys(identifier)
        entry = self.find_entry_by_shortcode(identifier) or self.lookup_downloaded_source(identifier)
        shortcode = (entry.get("shortcode") if entry else None) or identifier.replace("manual_", "")

        # 1. Check if already exists on local disk
        candidate_dirs = [
            dest_dir,
            os.path.join(_REPO_ROOT, "downloads", identifier),
            os.path.join(_REPO_ROOT, "downloads", shortcode),
            os.path.join(_REPO_ROOT, "downloads", f"manual_{shortcode}"),
        ]
        candidate_filenames = ["video.mp4", f"{shortcode}.mp4", f"manual_{shortcode}.mp4"]
        if entry and entry.get("file_name"):
            candidate_filenames.insert(0, entry["file_name"])

        for c_dir in candidate_dirs:
            if c_dir and os.path.exists(c_dir):
                for c_fn in candidate_filenames:
                    c_out = os.path.join(c_dir, c_fn)
                    if os.path.exists(c_out) and os.path.getsize(c_out) > 1024:
                        return c_out

        # Also check session manager for raw_video_path
        sess = None
        try:
            from Telegram_Storage_Modules.telegram_session_manager import TelegramSessionManager
            sm = TelegramSessionManager()
            for k in search_keys:
                sess = sm.get_session(k)
                if sess:
                    break
            if sess and sess.get("raw_video_path") and os.path.exists(sess["raw_video_path"]):
                if os.path.getsize(sess["raw_video_path"]) > 1024:
                    return sess["raw_video_path"]
        except Exception:
            pass

        # 2. Resolve raw_video_file_id from entry or session
        raw_file_id = None
        if entry:
            raw_file_id = (
                entry.get("media_file_ids", {}).get("raw_video_file_id") or
                entry.get("raw_video_file_id") or
                entry.get("raw_file_id")
            )
        if not raw_file_id and sess:
            raw_file_id = sess.get("raw_video_file_id")

        if not raw_file_id:
            logger.warning(f"⚠️ [VAULT HYDRATE] No raw_video_file_id found for '{identifier}'")
            return None

        # 3. Download via vault file downloader
        if not dest_dir:
            dest_dir = os.path.join(_REPO_ROOT, "downloads", shortcode)
        os.makedirs(dest_dir, exist_ok=True)
        out_path = os.path.join(dest_dir, "video.mp4")

        logger.info(f"📥 [VAULT HYDRATE] Downloading raw source for '{shortcode}' from Telegram Storage Group...")
        if self.download_vault_file_by_id(raw_file_id, out_path):
            if os.path.exists(out_path) and os.path.getsize(out_path) > 1024:
                logger.info(f"✅ [VAULT HYDRATE] Raw video recovered successfully -> {out_path}")
                return out_path

        return None

    def hydrate_processed_reel_from_vault(self, shortcode_or_session_id: str, dest_dir: Optional[str] = None) -> Optional[str]:
        """
        Retrieves processed master reel from Telegram Storage Group.
        Accepts shortcode, session_id, or social_url.
        Resolves processed_output_file_id from pool_metadata.json v3 (media_file_ids.processed_output_file_id).
        Returns local filesystem path to the recovered video.
        """
        if not shortcode_or_session_id:
            return None

        # 1. Check local session manager first
        sess = None
        try:
            from Telegram_Storage_Modules.telegram_session_manager import TelegramSessionManager
            sess = TelegramSessionManager().get_session(shortcode_or_session_id)
            if sess and sess.get("video_path") and os.path.exists(sess["video_path"]):
                if os.path.getsize(sess["video_path"]) > 1024:
                    return sess["video_path"]
        except Exception:
            pass

        # 2. Look up entry in pool_metadata.json
        entry = self.find_entry_by_shortcode(shortcode_or_session_id) or self.lookup_processed_reel(session_id=shortcode_or_session_id)
        shortcode = (entry.get("shortcode") if entry else None) or (sess.get("clip_id") if sess else None) or shortcode_or_session_id

        # Check default local processed reels path
        if not dest_dir:
            dest_dir = os.path.join(_REPO_ROOT, "downloads", "Processed_Shorts")
        os.makedirs(dest_dir, exist_ok=True)
        out_path = os.path.join(dest_dir, f"{shortcode}_master.mp4")

        if os.path.exists(out_path) and os.path.getsize(out_path) > 1024:
            return out_path

        # 3. Resolve processed_output_file_id
        processed_file_id = None
        if entry:
            processed_file_id = (
                entry.get("media_file_ids", {}).get("processed_output_file_id") or
                entry.get("processed_output_file_id") or
                entry.get("master_file_id")
            )

        if not processed_file_id:
            logger.warning(f"⚠️ [VAULT PROCESSED HYDRATE] No processed_output_file_id found for '{shortcode_or_session_id}'")
            return None

        # 4. Download from vault
        logger.info(f"📥 [VAULT PROCESSED HYDRATE] Downloading processed reel for '{shortcode}' from Telegram Storage Group...")
        if self.download_vault_file_by_id(processed_file_id, out_path):
            if os.path.exists(out_path) and os.path.getsize(out_path) > 1024:
                logger.info(f"✅ [VAULT PROCESSED HYDRATE] Processed reel recovered successfully -> {out_path}")
                return out_path

        return None

    def hydrate_clean_video_from_vault(self, identifier: str, dest_dir: Optional[str] = None) -> Optional[str]:
        """
        Downloads or locates the already watermark-cleaned video (video_inpainted_clean.mp4)
        from Telegram Storage Group or local disk using wm_clean_file_id.
        Allows Phase 2 to completely bypass OpenCV frame-by-frame watermark inpainting!
        """
        if not identifier:
            return None

        entry = self.find_entry_by_shortcode(identifier) or self.lookup_downloaded_source(identifier)
        shortcode = (entry.get("shortcode") if entry else None) or identifier

        if not dest_dir:
            dest_dir = os.path.join(_REPO_ROOT, "downloads", f"manual_{shortcode}")
            if not os.path.exists(dest_dir):
                dest_dir = os.path.join(_REPO_ROOT, "downloads", shortcode)
        os.makedirs(dest_dir, exist_ok=True)
        clean_path = os.path.join(dest_dir, "video_inpainted_clean.mp4")

        # 1. Local disk check
        if os.path.exists(clean_path) and os.path.getsize(clean_path) > 1024:
            return clean_path

        # 2. Vault download via wm_clean_file_id
        clean_file_id = None
        if entry:
            clean_file_id = (
                entry.get("media_file_ids", {}).get("wm_clean_file_id") or
                entry.get("wm_clean_file_id")
            )

        if clean_file_id:
            logger.info(f"⚡ [VAULT CLEAN HYDRATE] Downloading watermark-cleaned video for '{shortcode}' from Telegram Vault...")
            if self.download_vault_file_by_id(clean_file_id, clean_path):
                if os.path.exists(clean_path) and os.path.getsize(clean_path) > 1024:
                    logger.info(f"✅ [VAULT CLEAN HYDRATE] Clean video recovered successfully -> {clean_path}")
                    return clean_path

        return None

    def hydrate_selected_audio_from_vault(self, identifier: str, dest_dir: Optional[str] = None) -> Optional[str]:
        """
        Downloads or locates the chosen BGM audio track from Telegram Storage Group or local disk
        using selected_audio_file_id or track name in pool_metadata.json.
        """
        if not identifier:
            return None

        entry = self.find_entry_by_shortcode(identifier) or self.lookup_downloaded_source(identifier)
        track_name = None
        selected_fid = None
        if entry:
            selected_fid = (
                entry.get("media_file_ids", {}).get("selected_audio_file_id")
                or entry.get("selected_audio_file_id")
            )
            sel_dict = entry.get("audio_data", {}).get("selected_audio")
            if isinstance(sel_dict, dict):
                track_name = sel_dict.get("track_name")
                selected_fid = selected_fid or sel_dict.get("file_id")
            elif isinstance(entry.get("selected_audio"), str):
                track_name = entry.get("selected_audio")

        if not track_name and not selected_fid:
            track_name = os.path.basename(identifier)

        if not dest_dir:
            dest_dir = os.path.join(_REPO_ROOT, "Original_audio", "active")
        os.makedirs(dest_dir, exist_ok=True)

        if track_name:
            out_path = os.path.join(dest_dir, track_name)
            if os.path.exists(out_path) and os.path.getsize(out_path) > 1024:
                return out_path
            return self.hydrate_bgm_track_from_vault(track_name, dest_dir, file_id=selected_fid)
        elif selected_fid:
            out_path = os.path.join(dest_dir, f"vault_bgm_{selected_fid[:10]}.mp3")
            if self.download_vault_file_by_id(selected_fid, out_path):
                return out_path

        return None

    async def send_raw_video_to_user_chat(self, bot, chat_id: int | str, identifier: str) -> bool:
        """
        Sends raw downloaded video directly to the user's chat.
        Never leaks internal file_id values in the user-visible caption.
        Prioritizes:
          1. Existing local file
          2. Direct file_id delivery via Telegram Bot API (instant, 0 bandwidth)
          3. On-demand vault hydration fallback
        """
        if not bot or not chat_id or not identifier:
            return False

        caption = "📥 **Raw Downloaded Video**\n📁 `video.mp4`"
        search_keys = self._normalize_search_keys(identifier)
        shortcode = identifier.replace("manual_", "")

        # 1. Check if raw video file already exists on local disk
        candidate_dirs = [
            os.path.join(_REPO_ROOT, "downloads", identifier),
            os.path.join(_REPO_ROOT, "downloads", shortcode),
            os.path.join(_REPO_ROOT, "downloads", f"manual_{shortcode}"),
        ]
        local_raw = None
        for c_dir in candidate_dirs:
            if c_dir and os.path.exists(c_dir):
                for c_fn in ["video.mp4", f"{shortcode}.mp4", f"manual_{shortcode}.mp4"]:
                    c_out = os.path.join(c_dir, c_fn)
                    if os.path.exists(c_out) and os.path.getsize(c_out) > 1024:
                        local_raw = c_out
                        break
            if local_raw:
                break

        if not local_raw:
            try:
                from Telegram_Storage_Modules.telegram_session_manager import TelegramSessionManager
                sm = TelegramSessionManager()
                for k in search_keys:
                    s = sm.get_session(k)
                    if s and s.get("raw_video_path") and os.path.exists(s["raw_video_path"]):
                        if os.path.getsize(s["raw_video_path"]) > 1024:
                            local_raw = s["raw_video_path"]
                            break
            except Exception:
                pass

        if local_raw and os.path.exists(local_raw) and os.path.getsize(local_raw) > 1024:
            try:
                with open(local_raw, "rb") as vf:
                    await bot.send_video(
                        chat_id=int(chat_id),
                        video=vf,
                        caption=caption,
                        supports_streaming=True,
                        read_timeout=600,
                        write_timeout=600
                    )
                logger.info(f"✅ [RAW VIDEO DISPATCH] Sent local raw video to chat {chat_id} for '{identifier}'")
                return True
            except Exception as _send_err:
                logger.warning(f"⚠️ Failed to send local raw video file: {_send_err}")

        # 2. Try sending directly via Telegram file_id (instant cloud delivery, zero bandwidth)
        entry = self.find_entry_by_shortcode(identifier) or self.lookup_downloaded_source(identifier)
        raw_file_id = None
        if entry:
            raw_file_id = (
                entry.get("media_file_ids", {}).get("raw_video_file_id") or
                entry.get("raw_video_file_id") or
                entry.get("raw_file_id")
            )

        if not raw_file_id:
            try:
                from Telegram_Storage_Modules.telegram_session_manager import TelegramSessionManager
                sm = TelegramSessionManager()
                for k in search_keys:
                    s = sm.get_session(k)
                    if s and s.get("raw_video_file_id"):
                        raw_file_id = s["raw_video_file_id"]
                        break
            except Exception:
                pass

        if raw_file_id:
            try:
                await bot.send_video(
                    chat_id=int(chat_id),
                    video=raw_file_id,
                    caption=caption,
                    supports_streaming=True,
                    read_timeout=600,
                    write_timeout=600
                )
                logger.info(f"✅ [RAW VIDEO DISPATCH] Sent vault raw video by file_id to chat {chat_id} for '{identifier}'")
                return True
            except Exception as _fid_err:
                logger.warning(f"⚠️ Direct file_id send failed, attempting hydration fallback: {_fid_err}")

        # 3. Fallback: on-demand vault hydration and stream
        hydrated_path = self.hydrate_raw_video_from_vault(identifier)
        if hydrated_path and os.path.exists(hydrated_path) and os.path.getsize(hydrated_path) > 1024:
            try:
                with open(hydrated_path, "rb") as vf:
                    await bot.send_video(
                        chat_id=int(chat_id),
                        video=vf,
                        caption=caption,
                        supports_streaming=True,
                        read_timeout=600,
                        write_timeout=600
                    )
                logger.info(f"✅ [RAW VIDEO DISPATCH] Sent hydrated raw video to chat {chat_id} for '{identifier}'")
                return True
            except Exception as _h_err:
                logger.error(f"❌ Failed to send hydrated raw video: {_h_err}")

        return False


    def lookup_downloaded_source(self, social_url: str) -> Optional[Dict[str, Any]]:
        """Lookup harvested clip entry by URL or shortcode in pool_metadata.json."""
        if not social_url:
            return None
        clean_url = str(social_url).strip().strip("`")
        if clean_url.endswith("%60"):
            clean_url = clean_url[:-3].strip()
        try:
            from Audio_Modules.audio_pool_manager import AudioPoolManager
            pm = AudioPoolManager()
            clips = pm.metadata.get("files", {}).get("social_media_id", {})
            if clean_url in clips:
                return clips[clean_url]

            import re
            sc_match = re.search(r"/(?:reel|reels|p|shorts|v)/([A-Za-z0-9_-]{5,})", clean_url)
            shortcode = sc_match.group(1) if sc_match else clean_url

            if shortcode:
                for stored_url, entry in clips.items():
                    if (shortcode in stored_url or 
                        shortcode == str(entry.get("shortcode", "")) or 
                        shortcode in str(entry.get("file_name", ""))):
                        return entry
        except Exception as e:
            logger.debug("Notice on lookup_downloaded_source: %s", e)
        return None

    def lookup_processed_reel(self, session_id: Optional[str] = None, social_url: Optional[str] = None, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Lookup processed reel entry by session_id, social_url, or user_id in pool_metadata.json."""
        try:
            from Audio_Modules.audio_pool_manager import AudioPoolManager
            pm = AudioPoolManager()
            clips = pm.metadata.get("files", {}).get("social_media_id", {})
            for stored_url, entry in clips.items():
                if session_id and (session_id in stored_url or session_id == entry.get("shortcode")):
                    return entry
                if social_url and (social_url.strip() in stored_url or stored_url in social_url.strip()):
                    return entry
                if user_id and str(entry.get("user_id")) == str(user_id):
                    if session_id and session_id not in stored_url:
                        continue
                    return entry
        except Exception as e:
            logger.debug("Notice on lookup_processed_reel: %s", e)
        return None

    async def download_audio_track_from_vault(self, bot, track_name: str, dest_dir: Optional[str] = None) -> Optional[str]:
        """On-Demand Audio Vault Fetcher via pool_metadata.json."""
        if not track_name or not bot:
            return None

        filename = os.path.basename(track_name)
        if not dest_dir:
            dest_dir = os.path.join(_REPO_ROOT, "data", "runtime_audio")
        os.makedirs(dest_dir, exist_ok=True)
        local_path = os.path.join(dest_dir, filename)

        if os.path.exists(local_path) and os.path.getsize(local_path) > 1024:
            return local_path

        file_id = None
        try:
            from Audio_Modules.audio_pool_manager import AudioPoolManager
            pm = AudioPoolManager()
            clips = pm.metadata.get("files", {}).get("social_media_id", {})
            for _url, entry in clips.items():
                media_ids = entry.get("media_file_ids") or {}
                aud_id = media_ids.get("extracted_audio_file_id") or entry.get("extracted_audio_file_id")
                if aud_id and (filename in _url or filename in str(entry.get("shortcode", ""))):
                    file_id = aud_id
                    break
        except Exception as _fe:
            logger.debug("Notice on download_audio_track_from_vault lookup: %s", _fe)

        if file_id:
            try:
                t_file = await bot.get_file(file_id)
                await t_file.download_to_drive(custom_path=local_path)
                if os.path.exists(local_path) and os.path.getsize(local_path) > 1024:
                    return local_path
            except Exception as e:
                logger.warning(f"⚠️ Vault on-demand audio fetch failed for '{filename}': {e}")
        return None

    async def sync_vault_index_from_telegram(self, bot) -> bool:
        """Startup Sync from Telegram Storage Group."""
        storage_group_id = os.getenv("TELEGRAM_STORAGE_GROUP_ID")
        if not storage_group_id or not bot:
            return False

        try:
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
                        return True
        except Exception as e:
            logger.warning(f"⚠️ [VAULT SYNC] Could not fetch pinned vault index: {e}")
        return False

    def _hydrate_local_caches(self):
        try:
            c1_reels = self.vault_index.get("column_1_processed_reels", {}).get("by_session_id", {})
            if c1_reels:
                from Audio_Modules.audio_pool_manager import AudioPoolManager
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
                    pm._save_metadata(sync_to_vault=False)
                finally:
                    _apm_mod._VAULT_HYDRATION_IN_PROGRESS = False
        except Exception as e:
            logger.debug(f"[VAULT HYDRATE] Cache hydration notice: {e}")

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
        """Unified Storage Authority Entry Point."""
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
                    upload_res = upload_fn(method, storage_group_id, file_param, raw_video_path, caption=caption)
                    if upload_res and isinstance(upload_res, dict):
                        raw_file_id = upload_res.get(file_param, {}).get("file_id") or (upload_res.get("document", {}).get("file_id") if upload_res.get("document") else None)
                
                if extracted_audio_path and os.path.exists(extracted_audio_path):
                    audio_filename = os.path.basename(extracted_audio_path)
                    audio_caption = f"🎵 [EXTRACTED AUDIO] `{audio_filename}`\n🔗 `{social_url}`"
                    audio_upload_res = upload_fn("sendAudio", storage_group_id, "audio", extracted_audio_path, caption=audio_caption)
                    if audio_upload_res and isinstance(audio_upload_res, dict):
                        extracted_audio_file_id = audio_upload_res.get("audio", {}).get("file_id") or (audio_upload_res.get("document", {}).get("file_id") if audio_upload_res.get("document") else None)
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

            if storage_group_id and upload_fn and os.path.exists(pm.meta_path):
                pool_upload_res = upload_fn("sendDocument", storage_group_id, "document", pm.meta_path, caption=f"📦 **[VAULT BACKUP]** `pool_metadata.json` (Updated {time.strftime('%H:%M:%S')})")
                if pool_upload_res and isinstance(pool_upload_res, dict):
                    pool_doc_id = pool_upload_res.get("document", {}).get("file_id")
                    self.vault_index["pool_metadata_file_id"] = pool_doc_id
                    self.vault_index["metadata_pool_file_id"] = pool_doc_id

            try:
                from Telegram_Storage_Modules.telegram_user_manager import USERS_JSON_PATH
                if storage_group_id and upload_fn and os.path.exists(USERS_JSON_PATH):
                    users_upload_res = upload_fn("sendDocument", storage_group_id, "document", USERS_JSON_PATH, caption=f"👤 **[VAULT BACKUP]** `telegram_users.json` (Updated {time.strftime('%H:%M:%S')})")
                    if users_upload_res and isinstance(users_upload_res, dict):
                        users_doc_id = users_upload_res.get("document", {}).get("file_id")
                        self.vault_index["telegram_users_file_id"] = users_doc_id
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

        return {
            "raw_file_id": raw_file_id,
            "method": method,
            "file_param": file_param,
            "clip_entry": clip_entry,
        }

    def update_inpainted_clean_source_in_vault(self, clean_video_path: str, clip_folder_name: str) -> Optional[str]:
        """Uploads clean video_inpainted_clean.mp4 to Telegram Storage Group and updates wm_clean_file_id in pool_metadata.json."""
        if not clean_video_path or not os.path.exists(clean_video_path):
            return None

        storage_group_id = os.getenv("TELEGRAM_STORAGE_GROUP_ID")
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        if not storage_group_id or not bot_token:
            return None

        try:
            clean_display_name = f"{clip_folder_name}.mp4" if not clip_folder_name.endswith(".mp4") else clip_folder_name
            caption = f"🎬 [CLEAN INPAINTED SOURCE] `{clean_display_name}`\n🆔 `{clip_folder_name}`"

            upload_res = _send_telegram_file_sync(
                "sendVideo",
                storage_group_id,
                "video",
                clean_video_path,
                caption=caption,
                custom_filename=clean_display_name
            )
            if not upload_res or not isinstance(upload_res, dict) or not upload_res.get("ok"):
                upload_res = _send_telegram_file_sync(
                    "sendDocument",
                    storage_group_id,
                    "document",
                    clean_video_path,
                    caption=caption,
                    custom_filename=clean_display_name
                )

            clean_file_id = None
            if upload_res and isinstance(upload_res, dict) and upload_res.get("ok"):
                res_doc = upload_res.get("result", {})
                clean_file_id = res_doc.get("video", {}).get("file_id") or res_doc.get("document", {}).get("file_id")

            if clean_file_id:
                try:
                    from Audio_Modules.audio_pool_manager import AudioPoolManager
                    pm = AudioPoolManager()
                    clips = pm.metadata.setdefault("files", {}).setdefault("social_media_id", {})
                    for url_key, entry in clips.items():
                        if clip_folder_name.lower() in url_key.lower() or clip_folder_name.lower() in str(entry.get("shortcode", "")).lower():
                            m_ids = entry.setdefault("media_file_ids", {})
                            m_ids["wm_clean_file_id"] = clean_file_id
                    pm._save_metadata()
                except Exception as _pe:
                    logger.debug("Notice updating clean_file_id in pool_metadata: %s", _pe)

                return clean_file_id
        except Exception as _e:
            logger.warning("⚠️ Could not upload/update clean video in Telegram vault: %s", _e)

        return None

    def sync_visual_pool_metadata(self, clip_id: str, clip_data: Dict[str, Any]) -> bool:
        """Visual intelligence is merged directly into pool_metadata.json."""
        return True

    def upload_visual_pool_metadata_backup(self) -> bool:
        """Visual intelligence is backed up with pool_metadata.json."""
        return True

    def enrich_and_update_pool_metadata(self, social_url: str = "", clip_dir: str = "") -> bool:
        """
        Syncs extracted audio file_ids and harvested clip metadata into pool_metadata.json.
        """
        try:
            from Audio_Modules.audio_pool_manager import AudioPoolManager
            pm = AudioPoolManager()
            pm.hydrate_harvested_clip_metadata()
            return True
        except Exception as e:
            logger.warning("⚠️ [VAULT] Error enriching pool metadata: %s", e)
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
        """Record downloaded raw video and audio into Telegram Storage Group & pool_metadata.json."""
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
                            read_timeout=600,
                            write_timeout=600
                        )
                        if rmsg and rmsg.video:
                            raw_file_id = rmsg.video.file_id

                if audio_path and os.path.exists(audio_path):
                    try:
                        with open(audio_path, "rb") as af:
                            amsg = await bot.send_document(
                                chat_id=int(storage_group_id),
                                document=af,
                                filename=os.path.basename(audio_path),
                                caption=f"🎵 **[VAULT AUDIO EXTRACT]** `{os.path.basename(audio_path)}`\n🆔 `{session_id}`" + (f"\n👤 User: `{user_id}`" if user_id else ""),
                                read_timeout=600,
                                write_timeout=600
                            )
                            if amsg:
                                audio_file_id = amsg.document.file_id if amsg.document else (amsg.audio.file_id if amsg.audio else None)
                    except Exception as _aud_err:
                        logger.warning(f"❌ [VAULT AUDIO ERROR] Audio upload failed: {_aud_err}")
            except Exception as e:
                logger.warning(f"⚠️ Vault raw source upload warning: {e}")

        import re
        sc_val = ""
        if social_url:
            sc_m = re.search(r"/(?:reel|reels|p|shorts|v)/([A-Za-z0-9_-]{5,})", social_url)
            if sc_m:
                sc_val = sc_m.group(1)
        if not sc_val and raw_video_path:
            parent_name = os.path.basename(os.path.dirname(raw_video_path))
            sc_val = parent_name.replace("manual_", "").strip()

        try:
            from Audio_Modules.audio_pool_manager import AudioPoolManager
            pm = AudioPoolManager()
            clips = pm.metadata.setdefault("files", {}).setdefault("social_media_id", {})
            entry = clips.get(social_url) or {"social_media_id": social_url}
            if sc_val and not entry.get("shortcode"):
                entry["shortcode"] = sc_val
            if raw_video_path and not entry.get("file_name"):
                entry["file_name"] = os.path.basename(raw_video_path)
            m_ids = entry.setdefault("media_file_ids", {})
            if raw_file_id:
                m_ids["raw_video_file_id"] = raw_file_id
                entry["raw_video_file_id"] = raw_file_id
            if audio_file_id:
                m_ids["extracted_audio_file_id"] = audio_file_id
                entry["extracted_audio_file_id"] = audio_file_id
            if beat_math:
                entry.setdefault("audio_data", {})["audio_math"] = beat_math
            if user_id:
                entry["user_id"] = user_id
            clips[social_url] = entry
            pm._save_metadata()
        except Exception as _pe:
            logger.debug("Notice on record_downloaded_source pool_metadata save: %s", _pe)

        # Index into master_vault_index.json -> column_2_downloaded_sources
        try:
            c2 = self.vault_index.setdefault("column_2_downloaded_sources", {})
            c2_url = c2.setdefault("by_social_media_id", {})
            c2_sess = c2.setdefault("by_session_id", {})
            c2_entry = {
                "social_media_id": social_url,
                "shortcode": sc_val,
                "session_id": session_id,
                "raw_video_file_id": raw_file_id,
                "extracted_audio_file_id": audio_file_id,
                "file_name": os.path.basename(raw_video_path) if raw_video_path else "video.mp4",
                "media_file_ids": {
                    "raw_video_file_id": raw_file_id,
                    "extracted_audio_file_id": audio_file_id
                },
                "user_id": user_id,
                "timestamp": time.time()
            }
            if social_url:
                c2_url[social_url] = c2_entry
            if sc_val:
                c2_url[sc_val] = c2_entry
                c2_url[f"manual_{sc_val}"] = c2_entry
            if session_id:
                c2_sess[session_id] = c2_entry
            self._save_local_index()
        except Exception as _vi_err:
            logger.debug("Notice on updating master_vault_index column_2: %s", _vi_err)

        # Update TelegramSessionManager with raw_video_file_id
        if raw_file_id:
            try:
                from Telegram_Storage_Modules.telegram_session_manager import TelegramSessionManager
                sm = TelegramSessionManager()
                if session_id:
                    sm.update_raw_video_file_id(session_id, raw_file_id)
                if sc_val:
                    sm.update_raw_video_file_id(sc_val, raw_file_id)
                    sm.update_raw_video_file_id(f"manual_{sc_val}", raw_file_id)
            except Exception as _sm_err:
                logger.debug("Notice on update_raw_video_file_id in session_manager: %s", _sm_err)

        return {
            "social_media_id": social_url,
            "session_id": session_id,
            "raw_video_file_id": raw_file_id,
            "extracted_audio_file_id": audio_file_id,
            "user_id": user_id,
        }

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
        """Record processed video reel into Telegram Storage Group & pool_metadata.json."""
        storage_group_id = os.getenv("TELEGRAM_STORAGE_GROUP_ID")

        if not master_file_id and storage_group_id and bot and master_video_path and os.path.exists(master_video_path):
            try:
                filename = os.path.basename(master_video_path)
                with open(master_video_path, "rb") as vf:
                    vmsg = await bot.send_video(
                        chat_id=int(storage_group_id),
                        video=vf,
                        caption=f"🎬 **[VAULT PROCESSED REEL]** `{filename}`\n🆔 `{session_id}`" + (f"\n👤 User: `{user_id}`" if user_id else ""),
                        read_timeout=600,
                        write_timeout=600
                    )
                    if vmsg and vmsg.video:
                        master_file_id = vmsg.video.file_id
            except Exception as _mv_err:
                logger.warning("⚠️ Could not upload processed video reel to Telegram Storage Group: %s", _mv_err)

        key_url = social_url or session_id or f"direct_upload_{int(time.time())}"
        try:
            from Audio_Modules.audio_pool_manager import AudioPoolManager
            pm = AudioPoolManager()
            clips = pm.metadata.setdefault("files", {}).setdefault("social_media_id", {})
            entry = clips.get(key_url) or {"social_media_id": key_url}
            m_ids = entry.setdefault("media_file_ids", {})
            if master_file_id:
                m_ids["processed_output_file_id"] = master_file_id
            if clip_intel:
                v_data = entry.setdefault("visual_data", {})
                v_data["clip_intelligence"] = clip_intel
                sel_audio = (
                    clip_intel.get("audio_data", {}).get("selected_bgm_track")
                    or clip_intel.get("audio_data", {}).get("selected_audio_track")
                    or clip_intel.get("selected_audio_track")
                )
                if sel_audio:
                    entry["selected_audio"] = sel_audio
                    entry.setdefault("audio_data", {})["selected_audio"] = sel_audio
            if lyric_intel:
                a_data = entry.setdefault("audio_data", {})
                a_data["gemini_audio_output"] = lyric_intel
            if user_id:
                entry["user_id"] = user_id
            clips[key_url] = entry
            pm._save_metadata()
        except Exception as _pe:
            logger.debug("Notice on record_processed_reel pool_metadata save: %s", _pe)

        return {
            "session_id": session_id,
            "social_media_id": key_url,
            "processed_output_file_id": master_file_id,
            "user_id": user_id,
        }

    async def update_pipeline_trajectory(
        self,
        bot,
        session_id: str,
        stage_name: str,
        stage_data: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """AI Trajectory Store in pool_metadata.json."""
        try:
            from Audio_Modules.audio_pool_manager import AudioPoolManager
            pm = AudioPoolManager()
            clips = pm.metadata.setdefault("files", {}).setdefault("social_media_id", {})
            target_entry = None
            for url_key, entry in clips.items():
                if session_id in url_key or entry.get("shortcode") == session_id:
                    target_entry = entry
                    break
            if not target_entry:
                target_entry = clips.setdefault(session_id, {"social_media_id": session_id})

            trajectory = target_entry.setdefault("pipeline_execution_trajectory", {
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

            pm._save_metadata()
            return trajectory
        except Exception as e:
            logger.warning("Notice updating pipeline trajectory: %s", e)
            return None

    async def record_plan_attempt(
        self,
        bot,
        session_id: str,
        attempt_number: int,
        editing_plan: Dict[str, Any],
        user_approved: bool = False,
        user_feedback: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """RAG Creator Behavior Store in pool_metadata.json."""
        try:
            from Audio_Modules.audio_pool_manager import AudioPoolManager
            pm = AudioPoolManager()
            clips = pm.metadata.setdefault("files", {}).setdefault("social_media_id", {})
            target_entry = None
            for url_key, entry in clips.items():
                if session_id in url_key or entry.get("shortcode") == session_id:
                    target_entry = entry
                    break
            if not target_entry:
                return None

            history = target_entry.setdefault("editing_plan_history", [])
            attempt_record = {
                "attempt_number": attempt_number,
                "timestamp": time.time(),
                "user_approved": user_approved,
                "user_feedback": user_feedback or ("Approved by user" if user_approved else "Rejected/Re-edit requested"),
                "editing_plan": editing_plan or {},
            }
            history.append(attempt_record)
            target_entry["editing_plan_history"] = sorted(history, key=lambda x: int(x.get("attempt_number", 0)))
            pm._save_metadata()
            return attempt_record
        except Exception as e:
            logger.warning("Notice recording plan attempt: %s", e)
            return None

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
                    caption=f"📌 **[VAULT MASTER INDEX]** Auto-Synced\n🕒 `{time.strftime('%Y-%m-%d %H:%M:%S')}`",
                    read_timeout=600,
                    write_timeout=600
                )
                if doc_msg and doc_msg.message_id:
                    await bot.pin_chat_message(
                        chat_id=int(storage_group_id),
                        message_id=doc_msg.message_id,
                        disable_notification=True
                    )
                    self.vault_index["pinned_message_id"] = doc_msg.message_id
        except Exception as e:
            logger.warning(f"⚠️ Vault index upload/pin notice: {e}")

    def purge_source_and_audio(
        self,
        reel_shortcode_or_url: str,
        audio_shortcode_or_track: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Purges both the processed reel entry and the selected audio track entry
        from master_vault_index.json (column_2_downloaded_sources), saves, and pins to Telegram.
        """
        def _extract_sc(val: Any) -> str:
            if not val:
                return ""
            s = str(val).strip().strip("`")
            if s.endswith("%60"):
                s = s[:-3].strip()
            s = s.rstrip("/")
            m = re.search(r"/(?:p|reel|reels)/([A-Za-z0-9_-]+)", s)
            if m:
                return m.group(1)
            m_yt = re.search(r"(?:shorts/|v=|youtu\.be/)([A-Za-z0-9_-]{11})", s)
            if m_yt:
                return m_yt.group(1)
            base = os.path.basename(s).replace("manual_", "").split("?")[0]
            return base.replace(".mp4", "").replace(".wav", "").replace(".mp3", "").replace(".m4a", "").strip()

        proc_sc = _extract_sc(reel_shortcode_or_url).lower()
        reel_id_clean = str(reel_shortcode_or_url).lower().strip()

        audio_sc = ""
        if audio_shortcode_or_track:
            ab = os.path.basename(str(audio_shortcode_or_track)).lower()
            audio_sc = ab.replace("vault_bgm_", "").replace("bgm_", "")
            for ext in (".wav", ".mp3", ".m4a", ".aac"):
                audio_sc = audio_sc.replace(ext, "")
            audio_sc = audio_sc.strip()

        purged_vault_items = []

        # Column 2 by_social_media_id
        c2 = self.vault_index.setdefault("column_2_downloaded_sources", {}).setdefault("by_social_media_id", {})
        for url_key, entry in list(c2.items()):
            if not isinstance(entry, dict):
                continue
            sc_val = str(entry.get("shortcode", "")).lower()
            sm_val = str(entry.get("social_media_id", "")).lower()
            u_l = str(url_key).lower()

            is_match = False
            if proc_sc and (proc_sc == sc_val or proc_sc in u_l or proc_sc in sm_val):
                is_match = True
            elif reel_id_clean and (reel_id_clean in u_l or reel_id_clean in sm_val):
                is_match = True
            elif audio_sc and (audio_sc == sc_val or audio_sc in u_l or audio_sc in sm_val):
                is_match = True

            if is_match:
                del c2[url_key]
                purged_vault_items.append(f"Vault source: {url_key}")
                logger.info(f"🗑️ [VAULT PURGE] Removed from master_vault_index: {url_key}")

        # Column 2 by_session_id
        by_sess = self.vault_index.setdefault("column_2_downloaded_sources", {}).setdefault("by_session_id", {})
        for sess_key, entry in list(by_sess.items()):
            sk_l = str(sess_key).lower()
            if (proc_sc and proc_sc in sk_l) or (audio_sc and audio_sc in sk_l) or (reel_id_clean and reel_id_clean in sk_l):
                del by_sess[sess_key]
                purged_vault_items.append(f"Vault session: {sess_key}")
                logger.info(f"🗑️ [VAULT PURGE] Removed session from master_vault_index: {sess_key}")

        self._save_local_index()

        # Cloud sync / pin to Telegram Storage Group
        try:
            from Telegram_Storage_Modules.telegram_http import send_document
            storage_group_id = os.getenv("TELEGRAM_STORAGE_GROUP_ID")
            if storage_group_id and os.path.exists(self.index_file):
                res = send_document(
                    local_path=self.index_file,
                    caption=f"📌 **[VAULT MASTER INDEX]** Purged & Auto-Synced\n🕒 `{time.strftime('%Y-%m-%d %H:%M:%S')}`"
                )
                if res and isinstance(res, dict):
                    msg_id = res.get("message_id")
                    if msg_id:
                        self.vault_index["pinned_message_id"] = msg_id
                        self._save_local_index()
                        try:
                            from Telegram_Storage_Modules.telegram_http import pin_message
                            pin_message(message_id=int(msg_id), chat_id=str(storage_group_id))
                        except Exception as _p_err:
                            logger.warning("Notice on purge pinChatMessage call: %s", _p_err)
                        logger.info("📌 [VAULT PURGE PIN SUCCESS] Uploaded & PINNED updated master_vault_index.json")
        except Exception as _sync_e:
            logger.warning(f"⚠️ Vault purge sync notice: {_sync_e}")

        return {
            "status": "success",
            "purged_count": len(purged_vault_items),
            "purged_items": purged_vault_items
        }


# ── ADVISORY LOCK ─────────────────────────────────────────────────────────────

_LOCK_TTL_SEC = float(os.getenv("VAULT_LOCK_TTL_SEC", "45"))
_LOCK_MAX_WAIT_SEC = float(os.getenv("VAULT_LOCK_MAX_WAIT_SEC", "60"))
_LOCK_POLL_SEC = 2.0

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

        if current_msg_id and current_msg_id == last_known_msg_id:
            indexer = TelegramVaultIndexer()
            return {"lock": indexer.vault_index.get("lock"), "msg_id": current_msg_id}

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
    holder = _get_holder_id()
    deadline = time.time() + max_wait_sec

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
            indexer2 = TelegramVaultIndexer()
            indexer2.sync_pinned_index_from_telegram_sync()
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

            confirm = _peek_pinned_lock_state(None)
            confirm_lock = confirm.get("lock") or {}
            if confirm_lock.get("held_by") == holder:
                return holder

        time.sleep(_LOCK_POLL_SEC)

    return None


def release_lock(holder: str) -> bool:
    indexer = TelegramVaultIndexer()
    indexer.sync_pinned_index_from_telegram_sync()
    lock = indexer.vault_index.get("lock") or {}

    if lock.get("held_by") != holder:
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

    return True


class vault_session:
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
        return False


