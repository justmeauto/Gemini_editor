"""
Publishing_Modules/vault_api_server.py
========================================
THE DOOR — HTTP API server for the Telegram Vault.

Exposes the vault as a REST API so it can be controlled from anywhere:
  - Your phone (via curl or a simple HTTP client)
  - Another machine / GitHub Actions runner
  - Any program that can make HTTP requests

Run once on any machine with a network connection:
    pip install fastapi uvicorn python-multipart
    python -m Publishing_Modules.vault_api_server

Or with uvicorn directly:
    uvicorn Publishing_Modules.vault_api_server:app --host 0.0.0.0 --port 8787

Security:
    Set VAULT_API_KEY env var to enable a shared-secret header check.
    Set VAULT_HOST / VAULT_PORT to change bind address/port.
    If exposed beyond localhost, put it behind HTTPS (nginx / caddy / ngrok).
    Without VAULT_API_KEY set, auth is disabled (fine for local use only).

Endpoints:
    GET  /health                      — liveness check
    GET  /vault/status                — summary stats from local index
    POST /vault/hydrate               — pull latest index from Telegram
    POST /vault/send   (multipart)    — upload a file to the vault
    POST /vault/retrieve              — download a file from the vault
    GET  /vault/manifest              — list all registered JSON resources
    POST /vault/lock/acquire          — acquire the advisory lock
    POST /vault/lock/release          — release the advisory lock
"""

import os
import sys
import json
import shutil
import tempfile
import logging
from typing import Optional

# Ensure project root is on path when run as __main__
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

try:
    from fastapi import FastAPI, UploadFile, File, Form, Header, HTTPException, Request
    from fastapi.responses import JSONResponse
    import uvicorn
except ImportError:
    raise ImportError(
        "vault_api_server requires fastapi, uvicorn, and python-multipart.\n"
        "Install with: pip install fastapi uvicorn python-multipart"
    )

from Publishing_Modules.telegram_vault_indexer import (
    TelegramVaultIndexer,
    acquire_lock,
    release_lock,
)
from Publishing_Modules.telegram_user_manager import load_all_users

logger = logging.getLogger("vault.api_server")

app = FastAPI(
    title="Telegram Vault API",
    description="Control the Telegram Storage Group vault over HTTP",
    version="1.0",
)

_API_KEY = os.getenv("VAULT_API_KEY", "").strip()


def _check_auth(x_vault_key: Optional[str]) -> None:
    """If VAULT_API_KEY is set, validates the X-Vault-Key header."""
    if not _API_KEY:
        return
    if x_vault_key != _API_KEY:
        raise HTTPException(status_code=401, detail="Missing or invalid X-Vault-Key header")


# ── LIVENESS ──────────────────────────────────────────────────────────────────

@app.get("/health", tags=["System"])
async def health():
    """Simple liveness probe."""
    return {"ok": True, "service": "telegram-vault-api"}


# ── STATUS & MANIFEST ─────────────────────────────────────────────────────────

@app.get("/vault/status", tags=["Vault"])
async def vault_status(x_vault_key: Optional[str] = Header(None)):
    """
    Returns summary stats from the local vault index — no Telegram round-trip.
    Use /vault/hydrate first if you want the freshest remote state.
    """
    _check_auth(x_vault_key)
    indexer = TelegramVaultIndexer()
    vault = indexer.vault_index
    c1 = vault.get("column_1_processed_reels", {})
    c2 = vault.get("column_2_downloaded_sources", {})
    users = load_all_users()
    lock = vault.get("lock")
    return {
        "ok": True,
        "pinned_message_id": vault.get("pinned_message_id"),
        "column_1_processed_reels": len(c1.get("by_session_id", {})),
        "column_2_downloaded_sources": len(c2.get("by_social_media_id", {})),
        "registered_users": len(users),
        "lock": lock,
        "updated_at": vault.get("updated_at"),
    }


@app.get("/vault/manifest", tags=["Vault"])
async def vault_manifest(x_vault_key: Optional[str] = Header(None)):
    """Lists all named JSON resources registered in vault_file_manifest.json."""
    _check_auth(x_vault_key)
    manifest_path = os.path.join(_REPO_ROOT, "data", "vault_file_manifest.json")
    if not os.path.exists(manifest_path):
        raise HTTPException(status_code=404, detail="vault_file_manifest.json not found in data/")
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    return {"ok": True, "manifest": manifest}


# ── HYDRATION (PULL FROM TELEGRAM) ────────────────────────────────────────────

@app.post("/vault/hydrate", tags=["Vault"])
async def vault_hydrate(force: bool = False, x_vault_key: Optional[str] = Header(None)):
    """
    Pulls the latest master_vault_index.json from Telegram and restores all
    registered JSON files (users, pool_metadata, etc.) onto local disk.
    Set force=true to bypass the 60-second cooldown guard.
    """
    _check_auth(x_vault_key)
    indexer = TelegramVaultIndexer()
    results = indexer.hydrate_all_vault_jsons_on_startup(force=force)
    return {"ok": True, "hydration_results": results}


# ── FILE SEND (UPLOAD TO VAULT) ───────────────────────────────────────────────

@app.post("/vault/send", tags=["Files"])
async def vault_send(
    resource_name: Optional[str] = Form(None),
    caption: str = Form(""),
    file: UploadFile = File(...),
    x_vault_key: Optional[str] = Header(None),
):
    """
    Uploads a file to the Telegram Storage Group and records its file_id in
    the local vault index.

    resource_name: Optional. If provided, must match a key in
        vault_file_manifest.json (e.g. "telegram_users", "pool_metadata").
        The file will be indexed as that named resource.
        If omitted, the file is uploaded as a raw document with the given
        caption and the returned file_id can be used manually.
    """
    _check_auth(x_vault_key)

    tmp_dir = tempfile.mkdtemp(prefix="vault_upload_")
    try:
        tmp_path = os.path.join(tmp_dir, file.filename or "upload.bin")
        with open(tmp_path, "wb") as out:
            shutil.copyfileobj(file.file, out)

        from Publishing_Modules import telegram_http
        msg = telegram_http.send_document(tmp_path, caption=caption or os.path.basename(tmp_path))
        if not msg:
            raise HTTPException(status_code=502, detail="Telegram upload failed — check bot token and storage group id")

        file_id = telegram_http.extract_file_id(msg)
        msg_id = msg.get("message_id")

        # If a named resource was supplied, register the file_id in the index
        if resource_name:
            manifest_path = os.path.join(_REPO_ROOT, "data", "vault_file_manifest.json")
            if os.path.exists(manifest_path):
                with open(manifest_path, "r", encoding="utf-8") as f:
                    manifest = json.load(f)
                if resource_name not in manifest:
                    raise HTTPException(
                        status_code=400,
                        detail=f"'{resource_name}' is not in vault_file_manifest.json. "
                               f"Add it there first — do not invent vault keys at runtime."
                    )
            vault_key = manifest[resource_name].get("vault_key")
            if vault_key:
                indexer = TelegramVaultIndexer()
                indexer.vault_index[vault_key] = file_id
                indexer._save_local_index()

        return {
            "ok": True,
            "file_id": file_id,
            "message_id": msg_id,
            "resource_name": resource_name,
        }
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ── FILE RETRIEVE (DOWNLOAD FROM VAULT) ───────────────────────────────────────

@app.post("/vault/retrieve", tags=["Files"])
async def vault_retrieve(
    resource_name: Optional[str] = Form(None),
    file_id: Optional[str] = Form(None),
    dest_path: Optional[str] = Form(None),
    x_vault_key: Optional[str] = Header(None),
):
    """
    Downloads a file from the Telegram Storage Group to local disk.

    Provide either:
      - resource_name: a key from vault_file_manifest.json — will look up
        its file_id from the index and write it to its registered local_path.
      - file_id: a raw Telegram file_id — dest_path is then required.

    Note: files > 20MB cannot be downloaded via the Bot API and will return
    ok=false. Raw video files are usually affected; JSON files usually are not.
    """
    _check_auth(x_vault_key)
    from Publishing_Modules import telegram_http

    resolved_file_id = file_id
    resolved_dest = dest_path

    if resource_name:
        manifest_path = os.path.join(_REPO_ROOT, "data", "vault_file_manifest.json")
        if not os.path.exists(manifest_path):
            raise HTTPException(status_code=404, detail="vault_file_manifest.json not found")
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        entry = manifest.get(resource_name)
        if not entry:
            raise HTTPException(status_code=400, detail=f"'{resource_name}' not in vault_file_manifest.json")
        vault_key = entry.get("vault_key")
        indexer = TelegramVaultIndexer()
        resolved_file_id = indexer.vault_index.get(vault_key) if vault_key else None
        if not resolved_file_id:
            return {"ok": False, "error": f"No file_id stored for resource '{resource_name}' yet"}
        resolved_dest = resolved_dest or os.path.join(_REPO_ROOT, entry.get("local_path", f"data/{resource_name}.json"))

    if not resolved_file_id:
        raise HTTPException(status_code=400, detail="Provide resource_name or file_id")
    if not resolved_dest:
        raise HTTPException(status_code=400, detail="dest_path is required when using a raw file_id")

    ok = telegram_http.download_file_by_id(resolved_file_id, resolved_dest)
    if not ok:
        return {
            "ok": False,
            "error": "Download failed — likely Telegram 20MB Bot API limit. "
                     "JSON/audio files usually work; raw video files > 20MB usually don't.",
        }
    return {"ok": True, "local_path": resolved_dest}


# ── ADVISORY LOCK ─────────────────────────────────────────────────────────────

@app.post("/vault/lock/acquire", tags=["Lock"])
async def vault_lock_acquire(
    purpose: str = Form(""),
    ttl_sec: float = Form(45),
    x_vault_key: Optional[str] = Header(None),
):
    """
    Acquires the advisory distributed lock stored in the vault index.
    Returns the holder_id on success — pass it to /vault/lock/release.
    Returns ok=false if the lock could not be acquired within 60 seconds.
    """
    _check_auth(x_vault_key)
    holder = acquire_lock(purpose=purpose, ttl_sec=ttl_sec)
    if holder:
        return {"ok": True, "holder_id": holder}
    return {"ok": False, "error": "Could not acquire vault lock within timeout"}


@app.post("/vault/lock/release", tags=["Lock"])
async def vault_lock_release(
    holder_id: str = Form(...),
    x_vault_key: Optional[str] = Header(None),
):
    """
    Releases the advisory lock. Only releases if you are still the holder.
    Pass the holder_id returned by /vault/lock/acquire.
    """
    _check_auth(x_vault_key)
    released = release_lock(holder_id)
    return {"ok": released, "released": released}


# ── ENTRY POINT ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    host = os.getenv("VAULT_HOST", "0.0.0.0")
    port = int(os.getenv("VAULT_PORT", "8787"))
    print(f"\n[Vault API Server] Starting on http://{host}:{port}")
    print(f"[Vault API Server] Auth: {'ENABLED (VAULT_API_KEY is set)' if _API_KEY else 'DISABLED (set VAULT_API_KEY to enable)'}")
    print("[Vault API Server] Endpoints: GET /health, GET /vault/status, GET /vault/manifest,")
    print("                              POST /vault/hydrate, POST /vault/send, POST /vault/retrieve,")
    print("                              POST /vault/lock/acquire, POST /vault/lock/release\n")
    uvicorn.run("Publishing_Modules.vault_api_server:app", host=host, port=port, reload=False)
