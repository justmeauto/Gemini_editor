"""
Import_Modules / tracker_notifier.py
====================================
Lightweight event notifier helper.
Broadcasts Phase 1 pipeline events to tracker_server.py (http://127.0.0.1:8000/api/event)
so that when main.py or Telegram bot runs, live stage progress and particle lights
stream to phase1_pipeline_visualizer.html in real time.
"""

import json
import logging
import urllib.request
from typing import Dict, Any, Optional

logger = logging.getLogger("TrackerNotifier")

TRACKER_API_EVENT_URL = "http://127.0.0.1:8000/api/event"

def notify_tracker(step_id: str, status: str, payload: Optional[Dict[str, Any]] = None):
    """
    Sends stage event to local tracker server. Safe & non-blocking if server is offline.
    """
    if payload is None:
        payload = {}

    event_data = {
        "step_id": step_id,
        "status": status,
        "payload": payload
    }

    try:
        req = urllib.request.Request(
            TRACKER_API_EVENT_URL,
            data=json.dumps(event_data).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=1.0) as resp:
            pass
    except Exception:
        # Tracker server is offline or unreachable — ignore silently
        pass
