# 📄 Module Documentation: `telegram_session_manager.py`

**Rating**: `9.7 / 10 (Grade A+ - Persistent Telegram Review Session Manager)`  
**Location**: `Publishing_Modules/telegram_session_manager.py`  
**Target File Link**: [telegram_session_manager.py](file:///d:/simple_scrapper%20and%20_uploader/Publishing_Modules/telegram_session_manager.py)

---

## 👑 Purpose & Role

`telegram_session_manager.py` manages persistent Telegram review sessions, custom title prompts, message ID linkages, and approval states for rendered master reels.

All session state is stored on disk in `data/telegram_sessions.json`, ensuring inline review buttons (`[ ✅ Approve & Set Title ]`, `[ 🚀 Post Immediately ]`, `[ ❌ Bad / Re-Edit ]`, `[ 🗑️ Reject & Discard ]`) **never expire** or lose context across system restarts.

---

## 🏗️ Architecture & Session Lifecycle

```mermaid
flowchart TD
    ReelRendered[Reel Rendered in Processed Shorts/] --> CreateSess[TelegramSessionManager.create_session]
    
    CreateSess --> SaveDisk[Persist to data/telegram_sessions.json\nstatus: AWAITING_REVIEW]
    SaveDisk --> SendTelegram[Send Video to Telegram Chat with 4-Button Review Keyboard]
    
    SendTelegram --> UpdateMsgID[TelegramSessionManager.update_message_id]
    
    UserAction{User Telegram Button Click}
    UserAction -- Approve & Set Title --> SetAwaitingTitle[set_awaiting_title -> status: AWAITING_TITLE]
    UserAction -- Post Immediately --> SetApproved[set_approved_title -> Queue to PublishQueue]
    UserAction -- Bad / Re-Edit --> TriggerReedit[Trigger MasterAIEditor re-edit with candidate BGM]
    UserAction -- Reject & Discard --> DeleteFile[Remove local file & mark REJECTED]
    
    SetAwaitingTitle --> ReplyText[User replies with custom title text]
    ReplyText --> CaptureTitle[set_approved_title(title) -> Queue to PublishQueue]
```

---

## 🛠️ Key Technical Features

### 1. Persistent Storage (`data/telegram_sessions.json`)
Saves session parameters (`session_id`, `video_path`, `creator`, `status`, `telegram_message_id`, `custom_title`, `created_at`, `updated_at`) directly to disk. On boot, existing sessions are automatically re-indexed.

### 2. Session Lifecycle States
- **`AWAITING_REVIEW`**: Initial state when reel is dispatched to Telegram.
- **`AWAITING_TITLE`**: Set when user clicks `[ ✅ Approve & Set Title ]`, prompting user for custom text.
- **`APPROVED`**: Reel custom title captured and queued to `PublishQueue`.
- **`REJECTED`**: Reel file deleted and session closed.

---

## 💻 Code Usage

```python
from Publishing_Modules.telegram_session_manager import TelegramSessionManager

session_manager = TelegramSessionManager()

# Create session for rendered reel
session_id = session_manager.create_session("Processed Shorts/reel_master.mp4", creator="creator_handle")

# Mark awaiting title
session_manager.set_awaiting_title(session_id)

# Set approved title & retrieve session data
session_data = session_manager.set_approved_title(session_id, custom_title="Viral Reel Showcase")
```
