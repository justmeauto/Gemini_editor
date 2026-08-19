"""
Core_Modules / session_manager.py
==================================
Thread-safe session store with per-user RLock synchronization and atomic disk persistence.
Replaces global user_sessions dicts and environment variable smuggling side-channels.
"""

from __future__ import annotations

import json
import os
import time
import logging
import tempfile
import threading
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, Dict, Any, Tuple

logger = logging.getLogger("core.session_manager")

VALID_STATES = {
    "IDLE",
    "WAITING_FOR_TITLE",
    "WAITING_FOR_APPROVAL",
    "PROCESSING",
    "REJECTED",
}

MAX_RETRIES = 5  # Limit retries to 5 attempts before forcing multi-attempt star rating

@dataclass
class Session:
    user_id: int
    state: str = "IDLE"
    video_path: Optional[str] = None
    final_path: Optional[str] = None
    title: Optional[str] = None
    user_affiliate_link: Optional[str] = None  # Optional: e-commerce product link (Amazon/Myntra/etc)
    real_mrp: Optional[int] = None             # Optional: product price / MRP
    retry_count: int = 0
    attempt_history: list = field(default_factory=list) # List of rendered video paths per attempt [att_1, att_2, ...]
    monetization_report: dict = field(default_factory=dict)
    rating: Optional[int] = None              # 1 to 5 star rating feedback
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def touch(self):
        self.updated_at = time.time()


class SessionManager:
    """
    Thread-safe session store with per-user locking and atomic disk persistence.
    One instance per process. No global module-level dict required.
    """

    def __init__(self, job_dir: str = "cache/sessions", ttl_secs: int = 86400):
        self._sessions: Dict[int, Session] = {}
        self._locks: Dict[int, threading.RLock] = {}
        self._registry_lock = threading.Lock()
        self.job_dir = Path(job_dir)
        self.job_dir.mkdir(parents=True, exist_ok=True)
        self.ttl_secs = ttl_secs
        self.load_all()

    # ── Lock acquisition ────────────────────────────────────────────────
    def _lock_for(self, user_id: int) -> threading.RLock:
        with self._registry_lock:
            if user_id not in self._locks:
                self._locks[user_id] = threading.RLock()
            return self._locks[user_id]

    def acquire(self, user_id: int):
        """Context manager to acquire user-level lock and retrieve session."""
        class SessionContext:
            def __init__(ctx_self, manager, uid):
                ctx_self.manager = manager
                ctx_self.uid = uid
                ctx_self.lock = manager._lock_for(uid)

            def __enter__(ctx_self) -> Session:
                ctx_self.lock.acquire()
                return ctx_self.manager._get_or_create(ctx_self.uid)

            def __exit__(ctx_self, exc_type, exc_val, exc_tb):
                ctx_self.lock.release()

        return SessionContext(self, user_id)

    # ── Core CRUD ────────────────────────────────────────────────────────
    def _get_or_create(self, user_id: int) -> Session:
        if user_id not in self._sessions:
            self._sessions[user_id] = Session(user_id=user_id)
        return self._sessions[user_id]

    def get(self, user_id: int) -> Optional[Session]:
        return self._sessions.get(user_id)

    def clear(self, user_id: int):
        with self.acquire(user_id):
            self._sessions.pop(user_id, None)
        self._delete_disk(user_id)

    def set_state(self, user_id: int, state: str):
        if state not in VALID_STATES:
            raise ValueError(f"Unknown session state: {state!r}")
        with self.acquire(user_id) as sess:
            sess.state = state
            sess.touch()
        self.save(user_id)

    # ── Business-logic helpers ──────────────────────────────────────────
    def start_job(self, user_id: int, video_path: str, title: str):
        with self.acquire(user_id) as sess:
            sess.video_path = video_path
            sess.title = title
            sess.state = "PROCESSING"
            sess.retry_count = 0
            sess.touch()
        self.save(user_id)

    def register_retry(self, user_id: int) -> Tuple[bool, int]:
        """
        Increments retry_count. Returns (should_retry, new_count).
        Caller decides UX messaging; this enforces the hard cap.
        """
        with self.acquire(user_id) as sess:
            sess.retry_count += 1
            sess.touch()
            should_retry = sess.retry_count <= MAX_RETRIES
            count = sess.retry_count
        self.save(user_id)
        return should_retry, count

    def record_rendered_attempt(self, user_id: int, video_path: str):
        """Records a rendered attempt video path into attempt_history."""
        with self.acquire(user_id) as sess:
            if video_path not in sess.attempt_history:
                sess.attempt_history.append(video_path)
            sess.touch()
        self.save(user_id)

    def set_rating(self, user_id: int, rating: int) -> Optional[Session]:
        """Record 1-5 star user feedback rating."""
        with self.acquire(user_id) as sess:
            sess.rating = max(1, min(5, rating))
            sess.touch()
        self.save(user_id)
        return self.get(user_id)

    def set_affiliate_info(self, user_id: int, link: Optional[str] = None, mrp: Optional[int] = None) -> Optional[Session]:
        """Record optional e-commerce affiliate link and product MRP/price."""
        with self.acquire(user_id) as sess:
            if link is not None:
                sess.user_affiliate_link = str(link).strip()
            if mrp is not None:
                sess.real_mrp = int(mrp)
            sess.touch()
        self.save(user_id)
        return self.get(user_id)

    def approve(self, user_id: int, final_path: str):
        with self.acquire(user_id) as sess:
            sess.final_path = final_path
            sess.state = "WAITING_FOR_APPROVAL"
            sess.touch()
        self.save(user_id)

    # ── Persistence ──────────────────────────────────────────────────────
    def _session_file(self, user_id: int) -> Path:
        return self.job_dir / f"session_{user_id}.json"

    def save(self, user_id: int):
        sess = self._sessions.get(user_id)
        if not sess:
            return
        target = self._session_file(user_id)
        fd, tmp_path = tempfile.mkstemp(dir=target.parent, prefix=".tmp_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(asdict(sess), f, indent=2)
            os.replace(tmp_path, target)
        except Exception as e:
            logger.warning(f"Failed to save session for user {user_id}: {e}")
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def _delete_disk(self, user_id: int):
        target = self._session_file(user_id)
        if target.exists():
            try:
                target.unlink()
            except OSError:
                pass

    def load_all(self) -> int:
        """Recover active sessions from disk on startup."""
        now = time.time()
        restored = 0
        for f in self.job_dir.glob("session_*.json"):
            try:
                if now - f.stat().st_mtime > self.ttl_secs:
                    f.unlink()
                    continue
                with open(f, "r", encoding="utf-8") as fp:
                    data = json.load(fp)
                sess = Session(**data)
                self._sessions[sess.user_id] = sess
                restored += 1
            except Exception:
                continue
        if restored > 0:
            logger.info(f"💾 SessionManager: Restored {restored} active sessions from disk")
        return restored
