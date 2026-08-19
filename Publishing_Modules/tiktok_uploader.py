"""
tiktok_uploader.py — AMTCE TikTok Content Posting API (Direct Post)
======================================================================
Publishes processed clips directly to TikTok using the Content Posting API
in FILE_UPLOAD / DIRECT_POST mode.

Flow:
  1. Resolve credentials (niche hierarchy → General_Fallback → root)
  2. Refresh access token if expiring in < 24 h
  3. Init the post  → POST /v2/post/publish/video/init/
  4. Chunked PUT upload (up to 64 MB per chunk)
  5. Poll status    → POST /v2/post/publish/status/fetch/
  6. Return {"status": "success", "id": publish_id} or {"status": "failed", "error": "..."}

ENV Flags (in Credentials/.env):
  SEND_TO_TIKTOK           = on | off   (default: off — safe)
  TIKTOK_CLIENT_KEY        = awfjdwrfnzhfk68c
  TIKTOK_CLIENT_SECRET     = c0rL2DHfU829edDUkBQWRCWKDPehQ7OI
  TIKTOK_TOKEN_FILE        = Credentials/tiktok_token_store.json   (root fallback)

Credential Hierarchy (mirrors Meta/YouTube):
  1. Credentials/social_media/{niche}/tiktok_token_store.json
  2. Credentials/social_media/General_Fallback/tiktok_token_store.json
  3. Credentials/tiktok_token_store.json
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import time
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)

# ── TikTok API base ───────────────────────────────────────────────────────────
_TT_BASE = "https://open.tiktokapis.com"
_INIT_URL   = f"{_TT_BASE}/v2/post/publish/video/init/"
_STATUS_URL = f"{_TT_BASE}/v2/post/publish/status/fetch/"
_REFRESH_URL = f"{_TT_BASE}/v2/oauth/token/"

# 64 MB is the max chunk size TikTok accepts
_CHUNK_SIZE = 64 * 1024 * 1024   # 64 MB
_POLL_INTERVAL_S = 5
_POLL_MAX_ATTEMPTS = 60           # 5 min total

# ── Credential helpers ────────────────────────────────────────────────────────
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SOCIAL_CREDS_ROOT = os.path.join(_REPO_ROOT, "Credentials", "social_media")
_ROOT_TOKEN_FILE   = os.environ.get(
    "TIKTOK_TOKEN_FILE",
    os.path.join(_REPO_ROOT, "Credentials", "tiktok_token_store.json"),
)


def _resolve_token_file(niche: Optional[str]) -> Optional[str]:
    """
    Walk the credential hierarchy and return the first token_store.json found.
    Returns None if none exists yet (first-time setup needed).
    """
    candidates = []
    if niche and niche != "General_Fallback":
        candidates.append(
            os.path.join(_SOCIAL_CREDS_ROOT, niche, "tiktok_token_store.json")
        )
    candidates.append(
        os.path.join(_SOCIAL_CREDS_ROOT, "General_Fallback", "tiktok_token_store.json")
    )
    candidates.append(_ROOT_TOKEN_FILE)

    for path in candidates:
        if os.path.exists(path):
            logger.debug("🔑 [TIKTOK] Using token file: %s", path)
            return path

    return None


def _load_tokens(token_file: str) -> Dict[str, Any]:
    with open(token_file, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_tokens(token_file: str, data: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(token_file)), exist_ok=True)
    with open(token_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    logger.info("💾 [TIKTOK] Tokens saved to %s", token_file)


def _refresh_access_token(token_file: str) -> str:
    """
    Refresh the TikTok access token using the stored refresh_token.
    Updates the token_store.json in-place and returns the new access_token.
    """
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(_REPO_ROOT, "Credentials", ".env"), override=False)
    except ImportError:
        pass

    client_key    = os.getenv("TIKTOK_CLIENT_KEY", "")
    client_secret = os.getenv("TIKTOK_CLIENT_SECRET", "")

    if not client_key or not client_secret:
        raise ValueError("TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET not set in .env")

    tokens = _load_tokens(token_file)
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        raise ValueError(
            f"No refresh_token found in {token_file}. Run scripts/auth_tiktok.py first."
        )

    resp = requests.post(
        _REFRESH_URL,
        data={
            "client_key":     client_key,
            "client_secret":  client_secret,
            "grant_type":     "refresh_token",
            "refresh_token":  refresh_token,
        },
        timeout=30,
    )
    resp.raise_for_status()
    payload = resp.json()

    if "error" in payload or payload.get("data", {}).get("error_code"):
        raise RuntimeError(f"Token refresh failed: {payload}")

    data = payload.get("data", payload)
    tokens["access_token"]            = data["access_token"]
    tokens["refresh_token"]           = data.get("refresh_token", refresh_token)
    tokens["expires_in"]              = data.get("expires_in", 86400)
    tokens["refresh_token_expires_in"]= data.get("refresh_token_expires_in", 2592000)
    tokens["obtained_at"]             = int(time.time())

    _save_tokens(token_file, tokens)
    logger.info("🔄 [TIKTOK] Access token refreshed successfully.")
    return tokens["access_token"]


def _get_valid_access_token(token_file: str) -> str:
    """
    Return a valid (non-expired) access token, auto-refreshing if needed.
    Considers token expired if it expires in < 1 hour.
    """
    tokens   = _load_tokens(token_file)
    obtained = tokens.get("obtained_at", 0)
    expires  = tokens.get("expires_in", 86400)
    age_s    = time.time() - obtained
    remaining_s = expires - age_s

    if remaining_s < 3600:   # < 1 hour left
        logger.info("⏰ [TIKTOK] Token expiring soon (%.0f s left) — refreshing…", remaining_s)
        return _refresh_access_token(token_file)

    return tokens["access_token"]


# ── Core upload logic ─────────────────────────────────────────────────────────

def _init_post(
    access_token: str,
    file_size: int,
    title: str,
    hashtags: str,
) -> Dict[str, Any]:
    """
    Initialize a Direct Post upload session.
    Returns the full /init/ response data dict.
    """
    # Build caption with hashtags (TikTok cap: 2200 chars)
    caption = f"{title}\n\n{hashtags}"[:2200]

    # Parse chunk count
    chunk_count = max(1, math.ceil(file_size / _CHUNK_SIZE))

    payload = {
        "post_info": {
            "title":         caption,
            "privacy_level": "SELF_ONLY",   # Unaudited apps: SELF_ONLY only
            "disable_duet":  False,
            "disable_comment": False,
            "disable_stitch":  False,
        },
        "source_info": {
            "source":      "FILE_UPLOAD",
            "video_size":  file_size,
            "chunk_size":  min(_CHUNK_SIZE, file_size),
            "total_chunk_count": chunk_count,
        },
    }

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type":  "application/json; charset=UTF-8",
    }

    resp = requests.post(_INIT_URL, json=payload, headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    logger.debug("[TIKTOK] Init response: %s", data)

    if data.get("error", {}).get("code", "ok") != "ok":
        raise RuntimeError(f"TikTok /init/ error: {data}")

    return data.get("data", {})


def _upload_chunks(upload_url: str, file_path: str) -> None:
    """Chunked PUT upload to the pre-signed upload_url from /init/."""
    file_size = os.path.getsize(file_path)
    offset    = 0

    with open(file_path, "rb") as fh:
        chunk_idx = 0
        while offset < file_size:
            chunk = fh.read(_CHUNK_SIZE)
            if not chunk:
                break

            end = offset + len(chunk) - 1
            headers = {
                "Content-Type":   "video/mp4",
                "Content-Length": str(len(chunk)),
                "Content-Range":  f"bytes {offset}-{end}/{file_size}",
            }
            resp = requests.put(upload_url, data=chunk, headers=headers, timeout=300)
            resp.raise_for_status()
            logger.info(
                "📦 [TIKTOK] Chunk %d uploaded: bytes %d-%d / %d",
                chunk_idx, offset, end, file_size,
            )
            offset += len(chunk)
            chunk_idx += 1


def _poll_status(publish_id: str, access_token: str) -> str:
    """Poll until PUBLISH_COMPLETE or FAILED. Returns final status string."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type":  "application/json; charset=UTF-8",
    }
    for attempt in range(_POLL_MAX_ATTEMPTS):
        resp = requests.post(
            _STATUS_URL,
            json={"publish_id": publish_id},
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        data   = resp.json().get("data", {})
        status = data.get("status", "PROCESSING")
        logger.info("🔄 [TIKTOK] Publish status (%d/%d): %s", attempt + 1, _POLL_MAX_ATTEMPTS, status)

        if status == "PUBLISH_COMPLETE":
            return "PUBLISH_COMPLETE"
        if status in ("FAILED", "CANCELLED"):
            fail_reason = data.get("fail_reason", "unknown")
            raise RuntimeError(f"TikTok publish failed: {fail_reason}")

        time.sleep(_POLL_INTERVAL_S)

    raise TimeoutError("TikTok status poll timed out after 5 minutes.")


# ── Public API ────────────────────────────────────────────────────────────────

async def upload_to_tiktok(
    file_path: str,
    title: str,
    hashtags: str = "",
    niche: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Upload a video to TikTok via the Direct Post API.

    Args:
        file_path: Absolute or relative path to the .mp4 file.
        title:     Caption/title string (will be combined with hashtags).
        hashtags:  Hashtag string (e.g. "#viral #shorts #trending").
        niche:     AMTCE niche folder name for credential resolution.

    Returns:
        {"status": "success", "id": publish_id}   on success
        {"status": "failed",  "error": "message"}  on failure
    """
    # Load .env
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(_REPO_ROOT, "Credentials", ".env"), override=False)
    except ImportError:
        pass

    # Resolve token file
    token_file = _resolve_token_file(niche)
    if not token_file:
        msg = (
            "No TikTok token store found. Run `python scripts/auth_tiktok.py` "
            "to complete first-time OAuth."
        )
        logger.error("❌ [TIKTOK] %s", msg)
        return {"status": "failed", "error": msg}

    # Resolve file path
    if not os.path.isabs(file_path):
        file_path = os.path.join(_REPO_ROOT, file_path)

    if not os.path.exists(file_path):
        msg = f"Video file not found: {file_path}"
        logger.error("❌ [TIKTOK] %s", msg)
        return {"status": "failed", "error": msg}

    file_size = os.path.getsize(file_path)
    logger.info(
        "🎵 [TIKTOK] Starting upload: %s (%.1f MB)",
        os.path.basename(file_path), file_size / (1024 * 1024),
    )

    try:
        # Run blocking I/O in a thread executor to keep the async event loop free
        loop = asyncio.get_event_loop()

        # Step 1: Get valid access token
        access_token = await loop.run_in_executor(
            None, _get_valid_access_token, token_file
        )

        # Step 2: Initialize post
        init_data = await loop.run_in_executor(
            None, _init_post, access_token, file_size, title, hashtags
        )

        publish_id = init_data.get("publish_id")
        upload_url = init_data.get("upload_url")

        if not publish_id or not upload_url:
            raise RuntimeError(f"Missing publish_id or upload_url from /init/: {init_data}")

        logger.info("🆔 [TIKTOK] Publish ID: %s", publish_id)

        # Step 3: Upload chunks
        await loop.run_in_executor(None, _upload_chunks, upload_url, file_path)
        logger.info("✅ [TIKTOK] All chunks uploaded for publish_id=%s", publish_id)

        # Step 4: Poll status
        final_status = await loop.run_in_executor(
            None, _poll_status, publish_id, access_token
        )

        if final_status == "PUBLISH_COMPLETE":
            logger.info("🎉 [TIKTOK] Published successfully! ID: %s", publish_id)
            return {"status": "success", "id": publish_id}
        else:
            return {"status": "failed", "error": f"Unexpected final status: {final_status}"}

    except Exception as exc:
        logger.error("❌ [TIKTOK] Upload failed: %s", exc, exc_info=True)
        return {"status": "failed", "error": str(exc)}
