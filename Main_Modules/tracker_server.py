"""
tracker_server.py — FastAPI Real-time Visual Pipeline Tracker Server
======================================================================
Provides a live FastAPI WebSocket & HTTP server to monitor Phase 1
ingestion pipeline progress in real-time.

Features:
  - WebSocket Stream (/ws/tracker): Broadcasts Step 01 -> Step 07 live events.
  - Web UI Dashboard (/): Serves phase1_live_tracker.html.
  - Trigger API (/api/harvest/manual, /api/harvest/auto): Launches runs and streams updates.

Usage:
  python tracker_server.py
  (Opens web dashboard at http://127.0.0.1:8000)
"""

import os
import sys
import time
import json
import asyncio
import logging
import threading
from typing import Set, Dict, Any, Optional

# Ensure project root in sys.path
_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s | [%(name)s] %(message)s")
logger = logging.getLogger("TrackerServer")

app = FastAPI(title="Phase 1 Live Visual Pipeline Tracker", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Active WebSocket Connection Manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        self._loop = None

    def set_loop(self, loop):
        self._loop = loop

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)
        logger.info(f"🔌 [WEBSOCKET] Client connected. Total clients: {len(self.active_connections)}")
        # Send initial welcome event
        await websocket.send_text(json.dumps({
            "event": "connected",
            "message": "Connected to Phase 1 Live Tracker WebSocket",
            "timestamp": time.time()
        }))

    def disconnect(self, websocket: WebSocket):
        self.active_connections.discard(websocket)
        logger.info(f"🔌 [WEBSOCKET] Client disconnected. Total clients: {len(self.active_connections)}")

    async def broadcast_async(self, data: Dict[str, Any]):
        message = json.dumps(data)
        to_remove = set()
        for connection in list(self.active_connections):
            try:
                await connection.send_text(message)
            except Exception:
                to_remove.add(connection)
        for conn in to_remove:
            self.active_connections.discard(conn)

    def broadcast_from_thread(self, data: Dict[str, Any]):
        """Thread-safe event broadcast helper called from pipeline threads."""
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self.broadcast_async(data), self._loop)

manager = ConnectionManager()


# ── Request Models ────────────────────────────────────────────────────────────
class ManualHarvestRequest(BaseModel):
    url: str
    platform: Optional[str] = "instagram"

class AutoHarvestRequest(BaseModel):
    target_accounts: Optional[list[str]] = None
    limit_per_account: Optional[int] = 3

class EventBroadcastRequest(BaseModel):
    step_id: str
    status: str
    payload: Optional[Dict[str, Any]] = None


# ── State Tracking ───────────────────────────────────────────────────────────
active_jobs: Dict[str, Any] = {}

def pipeline_event_callback(step_id: str, status: str, payload: Dict[str, Any]):
    """Callback passed to Phase 1 orchestrator to stream live updates."""
    event_data = {
        "event": "step_update",
        "step_id": step_id,
        "status": status,
        "payload": payload,
        "timestamp": time.time()
    }
    logger.info(f"📡 [LIVE TRACKER] {step_id.upper()} -> {status.upper()} | {payload.get('message', '')}")
    manager.broadcast_from_thread(event_data)


# ── REST API Endpoints ────────────────────────────────────────────────────────
@app.get("/api/status")
def get_status():
    return {
        "status": "online",
        "active_clients": len(manager.active_connections),
        "active_jobs": active_jobs,
        "timestamp": time.time()
    }

@app.post("/api/event")
def receive_pipeline_event(req: EventBroadcastRequest):
    """Receives event from main.py / Phase_1 modules and broadcasts to WebSockets."""
    pipeline_event_callback(req.step_id, req.status, req.payload or {})
    return {"status": "broadcasted"}

@app.post("/api/harvest/manual")
def trigger_manual_harvest(req: ManualHarvestRequest, background_tasks: BackgroundTasks):
    if not req.url:
        raise HTTPException(status_code=400, detail="URL parameter is required")

    job_id = f"manual_{int(time.time())}"
    active_jobs[job_id] = {"type": "manual", "url": req.url, "status": "running", "start_time": time.time()}

    def _run_job():
        try:
            from Import_Modules.phase1_imports import run_phase1_ingestion
            manager.broadcast_from_thread({
                "event": "job_start",
                "job_id": job_id,
                "type": "manual",
                "url": req.url,
                "timestamp": time.time()
            })

            res = run_phase1_ingestion(
                mode="manual",
                url=req.url,
                platform=req.platform,
                event_callback=pipeline_event_callback
            )

            active_jobs[job_id]["status"] = "success" if res.get("success") else "failed"
            manager.broadcast_from_thread({
                "event": "job_complete",
                "job_id": job_id,
                "result": res,
                "timestamp": time.time()
            })
        except Exception as e:
            active_jobs[job_id]["status"] = "failed"
            logger.error(f"❌ Job {job_id} failed: {e}")
            manager.broadcast_from_thread({
                "event": "job_error",
                "job_id": job_id,
                "error": str(e),
                "timestamp": time.time()
            })

    thread = threading.Thread(target=_run_job, daemon=True)
    thread.start()

    return {"status": "started", "job_id": job_id, "url": req.url}


@app.post("/api/harvest/auto")
def trigger_auto_harvest(req: AutoHarvestRequest):
    job_id = f"auto_{int(time.time())}"
    active_jobs[job_id] = {"type": "auto", "status": "running", "start_time": time.time()}

    def _run_job():
        try:
            from Import_Modules.phase1_imports import run_phase1_ingestion
            manager.broadcast_from_thread({
                "event": "job_start",
                "job_id": job_id,
                "type": "auto",
                "timestamp": time.time()
            })

            res = run_phase1_ingestion(
                mode="auto",
                limit_per_account=req.limit_per_account,
                target_accounts=req.target_accounts,
                event_callback=pipeline_event_callback
            )

            active_jobs[job_id]["status"] = "success" if res.get("success") else "failed"
            manager.broadcast_from_thread({
                "event": "job_complete",
                "job_id": job_id,
                "result": res,
                "timestamp": time.time()
            })
        except Exception as e:
            active_jobs[job_id]["status"] = "failed"
            logger.error(f"❌ Job {job_id} failed: {e}")
            manager.broadcast_from_thread({
                "event": "job_error",
                "job_id": job_id,
                "error": str(e),
                "timestamp": time.time()
            })

    thread = threading.Thread(target=_run_job, daemon=True)
    thread.start()

    return {"status": "started", "job_id": job_id}


# ── WebSocket Endpoint ────────────────────────────────────────────────────────
@app.websocket("/ws/tracker")
async def websocket_tracker_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # Keep connection alive
            data = await websocket.receive_text()
            # Echo ping check if needed
            if data == "ping":
                await websocket.send_text(json.dumps({"event": "pong", "timestamp": time.time()}))
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.warning(f"WebSocket error: {e}")
        manager.disconnect(websocket)


# ── Web UI Dashboard Serve Endpoint ──────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
@app.get("/visualizer", response_class=HTMLResponse)
def serve_dashboard():
    dashboard_path = os.path.join(_REPO_ROOT, "phase1_pipeline_visualizer.html")
    if os.path.exists(dashboard_path):
        with open(dashboard_path, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>Phase 1 Visual Model (phase1_pipeline_visualizer.html not found)</h1>"


# ── Server Startup ────────────────────────────────────────────────────────────
@app.on_event("startup")
async def on_startup():
    loop = asyncio.get_event_loop()
    manager.set_loop(loop)
    logger.info("🚀 [FASTAPI SERVER] Started Phase 1 Live Tracker Web Server on http://127.0.0.1:8000")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("tracker_server:app", host="127.0.0.1", port=8000, reload=False)
