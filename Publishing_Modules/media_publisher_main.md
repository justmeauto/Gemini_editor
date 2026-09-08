# Phase 4 — Standalone Multi-Platform Media Publisher (`media_publisher_main.py`)

## 📌 Module Purpose
`media_publisher_main.py` serves as the centralized **Phase 4 Broadcasting Orchestrator** in the master pipeline. It connects all platform uploaders into a single, unified execution flow triggered immediately when a user approves a reel title or taps *'Post Immediately'*.

---

## 🏗️ Architecture & Platform Flow

```
                              [ User Approves Title ]
                                         │
                                         ▼
                     ┌──────────────────────────────────────┐
                     │ run_phase4_publishing(video, title)  │
                     └──────────────────┬───────────────────┘
                                        │
        ┌───────────────────────────────┼───────────────────────────────┐
        │                               │                               │
        ▼                               ▼                               ▼
┌──────────────┐                ┌──────────────┐                ┌──────────────┐
│  Platform 1  │                │  Platform 2  │                │  Platform 3  │
│YouTube Shorts│ ─────────────► │Instagram Reel│ ─────────────► │TikTok Creator│
│(uploader.py) │                │(meta_uploader)│               │(tiktok_upload)│
└──────────────┘                └──────────────┘                └──────────────┘
                                                                        │
                                                                        ▼
                                                                ┌──────────────┐
                                                                │  Platform 4  │
                                                                │ Telegram Bot │
                                                                └──────────────┘
```

---

## ⚙️ Function Call Signatures

### 1. `run_phase4_publishing(...)`
```python
def run_phase4_publishing(
    video_path: str,
    title: str,
    description: str = "",
    tags: str = "#viral #shorts #trending",
    niche: Optional[str] = None
) -> Dict[str, Any]
```
- **Inputs**: Absolute video path, approved video title, optional description, hashtags, and niche target.
- **Returns**: Execution summary dictionary containing per-platform status (`success`, `skipped`, `failed`).

---

## 🛡️ Fallback & Safety Rules
- **Missing Credentials**: If credentials for YouTube, Instagram, or TikTok are not yet configured in `.env` / `Credentials/`, the module logs a warning and marks that specific platform as `skipped` without crashing the rest of the pipeline.
- **Sequential Safety**: Platforms execute in order (YouTube -> Instagram -> TikTok -> Telegram) so real-time status reporting can update the user as each broadcast completes.
