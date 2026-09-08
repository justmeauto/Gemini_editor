"""
Telegram_Storage_Modules / vault_api_server.py
================================================
THE DOOR — HTTP API server for the Telegram Vault.

Exposes the vault as a REST API so it can be controlled from anywhere:
  - Your phone (via curl or a simple HTTP client)
  - Another machine / GitHub Actions runner
  - Any program that can make HTTP requests

Run once on any machine with a network connection:
    pip install fastapi uvicorn python-multipart
    python -m Telegram_Storage_Modules.vault_api_server

Or with uvicorn directly:
    uvicorn Telegram_Storage_Modules.vault_api_server:app --host 0.0.0.0 --port 8787
"""

import os
import sys
import json
import shutil
import tempfile
import logging
from typing import Optional

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

from Telegram_Storage_Modules.telegram_vault_indexer import (
    TelegramVaultIndexer,
    acquire_lock,
    release_lock,
)
from Telegram_Storage_Modules.telegram_user_manager import load_all_users

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
    """Summary stats from the local vault index."""
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


# ── HYDRATION ─────────────────────────────────────────────────────────────────

@app.post("/vault/hydrate", tags=["Vault"])
async def vault_hydrate(force: bool = False, x_vault_key: Optional[str] = Header(None)):
    """Pulls latest master_vault_index.json and restores registered JSON files."""
    _check_auth(x_vault_key)
    indexer = TelegramVaultIndexer()
    results = indexer.hydrate_all_vault_jsons_on_startup(force=force)
    return {"ok": True, "hydration_results": results}


# ── FILE SEND ─────────────────────────────────────────────────────────────────

@app.post("/vault/send", tags=["Files"])
async def vault_send(
    resource_name: Optional[str] = Form(None),
    caption: str = Form(""),
    file: UploadFile = File(...),
    x_vault_key: Optional[str] = Header(None),
):
    """Uploads a file to Telegram Storage Group."""
    _check_auth(x_vault_key)

    tmp_dir = tempfile.mkdtemp(prefix="vault_upload_")
    try:
        tmp_path = os.path.join(tmp_dir, file.filename or "upload.bin")
        with open(tmp_path, "wb") as out:
            shutil.copyfileobj(file.file, out)

        from Telegram_Storage_Modules import telegram_http
        msg = telegram_http.send_document(tmp_path, caption=caption or os.path.basename(tmp_path))
        if not msg:
            raise HTTPException(status_code=502, detail="Telegram upload failed — check bot token and storage group id")

        file_id = telegram_http.extract_file_id(msg)
        msg_id = msg.get("message_id")

        if resource_name:
            manifest_path = os.path.join(_REPO_ROOT, "data", "vault_file_manifest.json")
            if os.path.exists(manifest_path):
                with open(manifest_path, "r", encoding="utf-8") as f:
                    manifest = json.load(f)
                if resource_name not in manifest:
                    raise HTTPException(
                        status_code=400,
                        detail=f"'{resource_name}' is not in vault_file_manifest.json."
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


# ── FILE RETRIEVE ─────────────────────────────────────────────────────────────

@app.post("/vault/retrieve", tags=["Files"])
async def vault_retrieve(
    resource_name: Optional[str] = Form(None),
    file_id: Optional[str] = Form(None),
    dest_path: Optional[str] = Form(None),
    x_vault_key: Optional[str] = Header(None),
):
    """Downloads a file from Telegram Storage Group."""
    _check_auth(x_vault_key)
    from Telegram_Storage_Modules import telegram_http

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
            "error": "Download failed.",
        }
    return {"ok": True, "local_path": resolved_dest}


# ── ADVISORY LOCK ─────────────────────────────────────────────────────────────

@app.post("/vault/lock/acquire", tags=["Lock"])
async def vault_lock_acquire(
    purpose: str = Form(""),
    ttl_sec: float = Form(45),
    x_vault_key: Optional[str] = Header(None),
):
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
    _check_auth(x_vault_key)
    released = release_lock(holder_id)
    return {"ok": released, "released": released}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    host = os.getenv("VAULT_HOST", "0.0.0.0")
    port = int(os.getenv("VAULT_PORT", "8787"))
    print(f"\n[Vault API Server] Starting on http://{host}:{port}")
    uvicorn.run("Telegram_Storage_Modules.vault_api_server:app", host=host, port=port, reload=False)
