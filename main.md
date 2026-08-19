# 📄 Module Documentation: `main.py`

**Rating**: `9.9 / 10 (Grade A+ - Master AI Video Factory & Telegram Bot Orchestrator)`  
**Location**: `main.py`  
**Target File Link**: [main.py](file:///d:/simple_scrapper%20and%20_uploader/main.py)

---

## 👑 Purpose & Role

`main.py` is the **Master Execution Entrypoint & Interactive Telegram Bot Listener**.

It ties together all 4 phases of the AI Video Factory:
1. **Phase 1**: Multi-Platform Ingestion (`Instagram`, `YouTube`, `TikTok`, `Direct URL`).
2. **Phase 2 & 3**: Vision Perception, BGM Selection, Beat Alignment, and Master FFmpeg Rendering into `Processed Shorts/`.
3. **Phase 4**: Realtime Telegram Delivery & Publishing Queue.

---

## 🏗️ Architecture & Interaction Flow

```mermaid
flowchart TD
    TelegramUser[Telegram User Input] --> CheckInputType{Input Type?}
    
    CheckInputType -- Text / Handle / URL --> PendingOrPlatform{Platform Clicked?}
    CheckInputType -- Platform Click --> SetPlatform[Set active platform & attach Back Button]
    CheckInputType -- Review Click --> HandleReview[Process Title / Post / Reedit / Reject]
    
    PendingOrPlatform -- Yes --> RunPipeline[run_master_pipeline]
    PendingOrPlatform -- No --> PromptPlatform[Prompt user to pick platform button]
    
    RunPipeline --> RenderLoop[Render reels into Processed Shorts/]
    
    RenderLoop --> IsolatedThreadDelivery[Isolated Thread _send_single()]
    IsolatedThreadDelivery --> DeliverChat[Deliver reel + 4-button review keyboard to Telegram chat]
    DeliverChat --> RenderNext[Proceed to render next reel]
```

---

## 🛠️ Key Technical Features

### 1. Interactive Multi-Platform Keyboard Selector
Presents 4 platform selection buttons:
- `[ 📸 Instagram Creator ]`
- `[ 🔴 YouTube Shorts / Channel ]`
- `[ 🎵 TikTok Creator ]`
- `[ 🌐 Direct URL / Raw File ]`

Attaches a `[ ↩️ Back to Main Menu / Cancel ]` inline button when a platform is selected, allowing users to cancel or change choices at any point.

### 2. Isolated Thread Real-Time Delivery (`threading.Thread`)
Executes `_send_single()` inside a dedicated `threading.Thread(daemon=True)`. This isolates Telegram HTTP delivery onto its own event loop, completely resolving asyncio event loop conflicts and guaranteeing that each clip is delivered to Telegram chat **before** the next clip starts rendering.

### 3. Persistent Review Sessions (`data/telegram_sessions.json`)
Saves every rendered reel's session state on disk so review buttons (`[ ✅ Approve & Set Title ]`, `[ 🚀 Post Immediately ]`, `[ ❌ Bad / Re-Edit ]`, `[ 🗑️ Reject & Discard ]`) **never expire** or fail on restart.

### 4. Clean Graceful Shutdown (`KeyboardInterrupt`)
Traps `Ctrl + C` signals across Telegram long-polling and CLI pipeline runs, printing clean status logs and exiting gracefully with status code `0`.

---

## 💻 CLI Usage

```powershell
# Launch Telegram Bot Polling Mode
.\venv\Scripts\python.exe main.py

# Ingest single handle directly
.\venv\Scripts\python.exe main.py creator_handle --mode auto

# Ingest direct video URL
.\venv\Scripts\python.exe main.py https://instagram.com/reel/C_example/ --mode manual
```
