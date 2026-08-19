"""Compatibility shim for the sample update tree.

This file provides a lightweight local publish queue implementation so the
master launcher can boot even when the legacy Content_Harvester package is
missing from the workspace.
"""

import os
from typing import Any, Dict, List, Optional


class PublishQueue:
    """In-memory publish queue that mirrors the minimal API used by AMTCE."""

    _items: List[Dict[str, Any]] = []

    @classmethod
    def add(cls, video_path: str, channel_title: str = "General", channel_folder: str = "General") -> Dict[str, Any]:
        item = {
            "video_path": os.path.abspath(video_path) if video_path else None,
            "channel_title": channel_title,
            "channel_folder": channel_folder,
            "status": "queued",
        }
        cls._items.append(item)
        return item

    @classmethod
    def list(cls) -> List[Dict[str, Any]]:
        return list(cls._items)

    @classmethod
    def clear(cls) -> None:
        cls._items.clear()


def start_publish_scheduler() -> Dict[str, Any]:
    """No-op scheduler placeholder for compatibility with legacy imports."""
    return {"status": "ok", "scheduler": "local_stub"}


__all__ = ["PublishQueue", "start_publish_scheduler"]
