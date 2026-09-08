"""
Telegram_Storage_Modules/telegram_http.py
===========================================
Minimal raw HTTP wrapper for the Telegram Bot API with MTProto Large File Streaming.

This is the primary module in the vault stack that talks to Telegram directly.
Uses stdlib urllib where possible so it works without any extra packages.
Falls back to `requests` for streaming downloads if available.
Supports Pyrogram MTProto fallback for files > 20MB (up to 2GB).
"""

import os
import json
import uuid
import logging
import urllib.request
import urllib.error
from typing import Any, Dict, Optional

logger = logging.getLogger("vault.telegram_http")

# Automatically load environment variables from Credentials/.env or .env
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
try:
    from dotenv import load_dotenv
    for env_path in [
        os.path.join(_REPO_ROOT, "Credentials", ".env"),
        os.path.join(_REPO_ROOT, ".env"),
    ]:
        if os.path.exists(env_path):
            load_dotenv(env_path, override=True)
except ImportError:
    pass


def _token() -> str:
    t = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not t:
        raise RuntimeError("TELEGRAM_BOT_TOKEN env var is not set")
    return t


def _group_id() -> str:
    g = os.getenv("TELEGRAM_STORAGE_GROUP_ID", "").strip()
    if not g:
        raise RuntimeError("TELEGRAM_STORAGE_GROUP_ID env var is not set")
    return g


def _api_url(method: str) -> str:
    return f"https://api.telegram.org/bot{_token()}/{method}"


# ── UPLOAD ────────────────────────────────────────────────────────────────────

def send_document(local_path: str, chat_id: Optional[str] = None, caption: str = "", max_retries: int = 3) -> Optional[Dict[str, Any]]:
    """
    Uploads local_path as a document to the Telegram storage group.
    Returns the raw Telegram message dict on success, or None on failure.
    Retries up to max_retries on transient socket/connection errors.
    """
    import time
    chat_id = chat_id or _group_id()
    if not os.path.exists(local_path):
        logger.error("[telegram_http] send_document: file not found: %s", local_path)
        return None

    filename = os.path.basename(local_path)

    for attempt in range(1, max_retries + 1):
        boundary = f"----WebKitFormBoundary{uuid.uuid4().hex}"
        body = bytearray()

        def _add_field(name: str, value: str) -> None:
            body.extend(f"--{boundary}\r\n".encode())
            body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
            body.extend(f"{value}\r\n".encode())

        _add_field("chat_id", chat_id)
        if caption:
            _add_field("caption", caption[:1024])

        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="document"; filename="{filename}"\r\n'.encode())
        body.extend(b"Content-Type: application/octet-stream\r\n\r\n")
        with open(local_path, "rb") as f:
            body.extend(f.read())
        body.extend(b"\r\n")
        body.extend(f"--{boundary}--\r\n".encode())

        req = urllib.request.Request(
            _api_url("sendDocument"),
            data=bytes(body),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                     "User-Agent": "AMTCE-Vault/2.0"},
        )
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                result = json.loads(resp.read().decode())
                if result.get("ok"):
                    return result.get("result")
                logger.warning("[telegram_http] sendDocument not OK: %s", result)
                return None
        except Exception as e:
            if attempt < max_retries:
                logger.warning("[telegram_http] send_document attempt %d failed for %s (%s) — retrying...", attempt, filename, e)
                time.sleep(2.0 * attempt)
            else:
                logger.warning("[telegram_http] send_document failed after %d attempts for %s: %s", max_retries, filename, e)
                return None


def extract_file_id(message: Dict[str, Any]) -> Optional[str]:
    """Extracts the file_id from any Telegram message dict regardless of media type."""
    doc = message.get("document") or {}
    if doc.get("file_id"):
        return doc["file_id"]
    for kind in ("video", "audio"):
        v = message.get(kind) or {}
        if isinstance(v, dict) and v.get("file_id"):
            return v["file_id"]
    photo = message.get("photo")
    if isinstance(photo, list) and photo:
        return photo[-1].get("file_id")
    return None


# ── DOWNLOAD ──────────────────────────────────────────────────────────────────

def _download_with_pyrogram(file_id: str, dest_path: str) -> bool:
    """
    MTProto download fallback using Pyrogram for files > 20MB.
    Bypasses Telegram Bot API getFile size restrictions up to 2GB.
    """
    logger.info("[telegram_http] Initiating Pyrogram MTProto download for large file_id=%s...", file_id[:12])
    try:
        import asyncio
        from pyrogram import Client

        token = _token()
        api_id = os.getenv("TELEGRAM_API_ID") or 6
        api_hash = os.getenv("TELEGRAM_API_HASH") or "eb6e06484e316e2d0be2d4177051c2b1"

        async def _async_download():
            async with Client(
                "vault_pyrogram_session",
                api_id=int(api_id),
                api_hash=api_hash,
                bot_token=token,
                in_memory=True
            ) as app:
                os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
                tmp_path = dest_path + ".tmp"
                downloaded_file = await app.download_media(message=file_id, file_name=tmp_path)
                if downloaded_file and os.path.exists(tmp_path):
                    os.replace(tmp_path, dest_path)
                    logger.info("[telegram_http] Pyrogram MTProto download successful -> %s", dest_path)
                    return True
                return False

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            try:
                import nest_asyncio
                nest_asyncio.apply()
            except ImportError:
                pass
            return loop.run_until_complete(_async_download())
        else:
            return asyncio.run(_async_download())

    except Exception as e:
        logger.warning("[telegram_http] Pyrogram MTProto download failed for file_id=%s: %s", file_id[:12], e)
        return False


def download_file_by_id(file_id: str, dest_path: str) -> bool:
    """
    Downloads a Telegram file by file_id to dest_path.

    Automatically falls back to Pyrogram MTProto download if:
      - HTTP 400: Telegram's 20MB Bot API getFile limit is hit.
    """
    token = _token()
    headers = {"User-Agent": "AMTCE-Vault/2.0"}

    # --- Attempt 1: requests (better SSL on Windows) ---
    try:
        import requests as req_lib
        r = req_lib.get(
            f"https://api.telegram.org/bot{token}/getFile",
            params={"file_id": file_id},
            headers=headers,
            timeout=15,
        )
        if r.status_code == 400:
            logger.warning("[telegram_http] 20MB getFile limit hit for file_id=%s. Switching to Pyrogram MTProto fallback...", file_id[:12])
            return _download_with_pyrogram(file_id, dest_path)
        r.raise_for_status()
        body = r.json()
        if not body.get("ok"):
            return _download_with_pyrogram(file_id, dest_path)
        dl_url = f"https://api.telegram.org/file/bot{token}/{body['result']['file_path']}"
        os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
        tmp = dest_path + ".tmp"
        with req_lib.get(dl_url, headers=headers, timeout=60, stream=True) as dl:
            dl.raise_for_status()
            with open(tmp, "wb") as out:
                for chunk in dl.iter_content(8192):
                    out.write(chunk)
        os.replace(tmp, dest_path)
        return True
    except ImportError:
        pass  # fall through to urllib
    except Exception as e:
        logger.debug("[telegram_http] requests download failed, trying urllib: %s", e)

    # --- Attempt 2: urllib fallback with 3 retries ---
    import time
    for attempt in range(1, 4):
        try:
            get_url = f"https://api.telegram.org/bot{token}/getFile?file_id={file_id}"
            req = urllib.request.Request(get_url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = json.loads(resp.read().decode())
            if not body.get("ok"):
                return _download_with_pyrogram(file_id, dest_path)
            dl_url = f"https://api.telegram.org/file/bot{token}/{body['result']['file_path']}"
            os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
            tmp = dest_path + ".tmp"
            dl_req = urllib.request.Request(dl_url, headers=headers)
            with urllib.request.urlopen(dl_req, timeout=60) as dl, open(tmp, "wb") as out:
                out.write(dl.read())
            os.replace(tmp, dest_path)
            return True
        except urllib.error.HTTPError as he:
            if he.code == 400:
                logger.warning("[telegram_http] 20MB getFile limit (urllib) hit for file_id=%s. Switching to Pyrogram MTProto fallback...", file_id[:12])
                return _download_with_pyrogram(file_id, dest_path)
            if attempt == 3:
                logger.warning("[telegram_http] download failed after 3 attempts: %s", he)
        except Exception as e:
            if attempt == 3:
                logger.warning("[telegram_http] download failed: %s", e)
            time.sleep(1.0)

    return _download_with_pyrogram(file_id, dest_path)


# ── PIN / CHAT ─────────────────────────────────────────────────────────────────

def pin_message(message_id: int, chat_id: Optional[str] = None) -> bool:
    """Pins a message in the storage group. Returns True on success."""
    chat_id = chat_id or _group_id()
    body = json.dumps({"chat_id": chat_id, "message_id": message_id, "disable_notification": True}).encode()
    req = urllib.request.Request(
        _api_url("pinChatMessage"),
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "AMTCE-Vault/2.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode()).get("ok", False)
    except Exception as e:
        logger.warning("[telegram_http] pinChatMessage failed: %s", e)
        return False


def get_pinned_message(chat_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Returns the currently-pinned message dict for the storage group, or None."""
    chat_id = chat_id or _group_id()
    url = f"{_api_url('getChat')}?chat_id={chat_id}"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            body = json.loads(resp.read().decode())
            if body.get("ok"):
                return body["result"].get("pinned_message")
    except Exception as e:
        logger.warning("[telegram_http] get_pinned_message failed: %s", e)
    return None
