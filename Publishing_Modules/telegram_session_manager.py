"""
telegram_session_manager.py — Persistent Telegram Review Session Manager
========================================================================
Tracks reel review sessions, message IDs, approval states, and custom titles.
Saves session state persistently to `data/telegram_sessions.json`.
"""

import os
import json
import time
import logging
from typing import Dict, List, Optional, Any, Tuple

logger = logging.getLogger("telegram_session_manager")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(_REPO_ROOT, "data")
SESSIONS_FILE = os.path.join(DATA_DIR, "telegram_sessions.json")


class TelegramSessionManager:
    """
    Manages persistent Telegram approval sessions and title capture states.
    """

    def __init__(self, sessions_file: str = SESSIONS_FILE):
        self.sessions_file = sessions_file
        os.makedirs(os.path.dirname(self.sessions_file), exist_ok=True)
        self.sessions: Dict[str, Dict[str, Any]] = self._load_sessions()

    def _load_sessions(self) -> Dict[str, Dict[str, Any]]:
        if os.path.exists(self.sessions_file):
            try:
                with open(self.sessions_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"⚠️ Could not load telegram sessions file: {e}")
        return {}

    def _save_sessions(self):
        try:
            with open(self.sessions_file, "w", encoding="utf-8") as f:
                json.dump(self.sessions, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"❌ Failed to save telegram sessions file: {e}")

    def create_session(
        self,
        video_path: str,
        creator: str = "General",
        clip_id: Optional[str] = None,
        raw_video_path: Optional[str] = None,
        requestor_chat_id: Optional[int] = None
    ) -> str:
        """
        Creates a new review session for a rendered video reel.
        Robustly resolves clip_id and raw_video_path from downloads directory.
        """
        bname = os.path.basename(video_path)
        if not clip_id or clip_id == "Processed Shorts":
            if "_master.mp4" in bname:
                clip_id = bname.replace("_master.mp4", "")
            else:
                clip_id = os.path.basename(os.path.dirname(video_path))

        if not raw_video_path and clip_id:
            possible_raw = os.path.join(_REPO_ROOT, "downloads", clip_id, "video.mp4")
            if os.path.exists(possible_raw):
                raw_video_path = possible_raw

        sess_id = f"sess_{int(time.time())}_{abs(hash(video_path)) % 10000}"
        session_data = {
            "session_id": sess_id,
            "telegram_message_id": None,
            "requestor_chat_id": requestor_chat_id,
            "video_path": os.path.abspath(video_path),
            "raw_video_path": os.path.abspath(raw_video_path) if raw_video_path and os.path.exists(raw_video_path) else None,
            "clip_id": clip_id,
            "creator": creator,
            "status": "AWAITING_REVIEW",  # AWAITING_REVIEW | AWAITING_TITLE | APPROVED | REJECTED
            "custom_title": None,
            "created_at": time.time(),
            "updated_at": time.time()
        }
        self.sessions[sess_id] = session_data
        self._save_sessions()
        logger.info(f"📝 Created Telegram review session: {sess_id} for '{os.path.basename(video_path)}' (clip_id='{clip_id}', raw_path='{raw_video_path}')")
        return sess_id

    def update_message_id(self, session_id: str, message_id: int):
        """Links Telegram message ID to session."""
        if session_id in self.sessions:
            self.sessions[session_id]["telegram_message_id"] = message_id
            self.sessions[session_id]["updated_at"] = time.time()
            self._save_sessions()

    def set_awaiting_title(self, session_id: str) -> bool:
        """Marks session as awaiting custom title from user."""
        if session_id in self.sessions:
            self.sessions[session_id]["status"] = "AWAITING_TITLE"
            self.sessions[session_id]["updated_at"] = time.time()
            self._save_sessions()
            return True
        return False

    def set_approved_title(self, session_id: str, custom_title: str) -> Optional[Dict[str, Any]]:
        """Sets custom title, marks session APPROVED, and returns session data."""
        if session_id in self.sessions:
            self.sessions[session_id]["custom_title"] = custom_title
            self.sessions[session_id]["status"] = "APPROVED"
            self.sessions[session_id]["updated_at"] = time.time()
            self._save_sessions()
            logger.info(f"✅ Session {session_id} approved with title: '{custom_title}'")
            return self.sessions[session_id]
        return None

    def set_rejected(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Marks session REJECTED and returns session data."""
        if session_id in self.sessions:
            self.sessions[session_id]["status"] = "REJECTED"
            self.sessions[session_id]["updated_at"] = time.time()
            self._save_sessions()
            logger.info(f"🗑️ Session {session_id} marked REJECTED.")
            return self.sessions[session_id]
        return None

    def record_rendered_attempt(self, chat_id_or_sess_id: Any, video_path: str):
        """Records rendered attempt path into active session attempt history."""
        str_key = str(chat_id_or_sess_id)
        target_sess = None
        if str_key in self.sessions:
            target_sess = self.sessions[str_key]
        else:
            matching = [s for s in self.sessions.values() if str(s.get("requestor_chat_id")) == str_key]
            if matching:
                matching.sort(key=lambda x: x.get("updated_at", 0), reverse=True)
                target_sess = matching[0]

        if target_sess:
            history = target_sess.setdefault("attempt_history", [])
            if video_path not in history:
                history.append(video_path)
            target_sess["updated_at"] = time.time()
            self._save_sessions()

    def register_retry(self, chat_id_or_user_id: Any) -> Tuple[bool, int]:
        """Registers a retry attempt for the active session. Returns (should_retry, new_count)."""
        str_key = str(chat_id_or_user_id)
        target_sess = None
        if str_key in self.sessions:
            target_sess = self.sessions[str_key]
        else:
            matching = [s for s in self.sessions.values() if str(s.get("requestor_chat_id")) == str_key]
            if matching:
                matching.sort(key=lambda x: x.get("updated_at", 0), reverse=True)
                target_sess = matching[0]

        if target_sess:
            count = target_sess.get("retry_count", 0) + 1
            target_sess["retry_count"] = count
            target_sess["updated_at"] = time.time()
            self._save_sessions()
            from Core_Modules import MAX_RETRIES
            return (count <= MAX_RETRIES, count)

        return (True, 1)

    def get_pending_title_session(self) -> Optional[Dict[str, Any]]:
        """Returns the most recent session waiting for a custom title."""
        awaiting = [
            s for s in self.sessions.values()
            if s.get("status") == "AWAITING_TITLE"
        ]
        if awaiting:
            awaiting.sort(key=lambda x: x.get("updated_at", 0), reverse=True)
            return awaiting[0]
        return None

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        return self.sessions.get(session_id)


# Global singleton instance
session_manager = TelegramSessionManager()
