"""Compatibility shim for the sample update tree.

This file provides a lightweight local publish queue implementation so the
master launcher can boot even when the legacy Content_Harvester package is
missing from the workspace.
"""

import os
import time
import json
from typing import Any, Dict, List, Optional


class PublishQueue:
    """In-memory publish queue that mirrors the minimal API used by AMTCE."""

    _items: List[Dict[str, Any]] = []

    @classmethod
    def _save_queue(cls) -> None:
        try:
            repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            queue_file = os.path.join(repo_root, "data", "publish_queue.json")
            os.makedirs(os.path.dirname(queue_file), exist_ok=True)
            with open(queue_file, "w", encoding="utf-8") as f:
                json.dump(cls._items, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    @classmethod
    def add(
        cls,
        video_path: str,
        channel_title: str = "General",
        channel_folder: str = "General",
        meta: Optional[Dict[str, Any]] = None,
        **kwargs: Any
    ) -> Dict[str, Any]:
        item_id = kwargs.get("id") or f"queue_{len(cls._items) + 1}_{int(time.time())}"
        resolved_title = kwargs.get("actress_title") or channel_title
        resolved_folder = kwargs.get("actress_folder") or kwargs.get("niche") or channel_folder
        resolved_meta = meta if meta is not None else kwargs.get("metadata", {})

        item = {
            "id": item_id,
            "video_path": os.path.abspath(video_path) if video_path else None,
            "channel_title": resolved_title,
            "channel_folder": resolved_folder,
            "meta": resolved_meta,
            "status": "queued",
            "enqueued_at": time.time(),
            **kwargs,
        }
        item["id"] = item_id
        item["video_path"] = os.path.abspath(video_path) if video_path else None
        item["channel_title"] = resolved_title
        item["channel_folder"] = resolved_folder
        item["meta"] = resolved_meta

        cls._items.append(item)
        cls._save_queue()
        return item

    @classmethod
    def pop_one(cls, **kwargs: Any) -> Optional[Dict[str, Any]]:
        if cls._items:
            item = cls._items.pop(0)
            cls._save_queue()
            return item
        return None

    @classmethod
    def list(cls) -> List[Dict[str, Any]]:
        return list(cls._items)

    @classmethod
    def clear(cls) -> None:
        cls._items.clear()
        cls._save_queue()


def start_publish_scheduler() -> Dict[str, Any]:
    """No-op scheduler placeholder for compatibility with legacy imports."""
    return {"status": "ok", "scheduler": "local_stub"}


__all__ = ["PublishQueue", "start_publish_scheduler"]
