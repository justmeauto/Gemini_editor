# 📄 Module Documentation: `downloader.py`

**Rating**: `9.6 / 10 (Grade A+ - Harvest & Media Ingestion Downloader)`  
**Location**: `D:\AMTCE\AMTCE_Elite_Core\Ingestion_and_Download\downloader.py`  
**Target File Link**: [downloader.py](file:///D:/AMTCE/AMTCE_Elite_Core/Ingestion_and_Download/downloader.py)

---

## 👑 Purpose & Role: Ingestion & Harvest Downloader

`downloader.py` is the **Harvest & Media Ingestion Downloader (`download_video` / `DownloadIndex`)** in the **Ingestion & Download** family.

It manages automated video downloading across social media platforms (Instagram, TikTok, YouTube, Facebook), executing an 8-stage authentication fallback strategy (`no_auth`, `cookies`, `browser_cookies`), thread-safe `yt-dlp` auto-updating, SQLite content indexing (`data/index.db`), perceptual frame fingerprinting, and direct CDN fallback downloading.

---

## 🏗️ Architecture & Harvest Ingestion Flow

```mermaid
flowchart TD
    URLInput[Input Video URL & Target Metadata] --> DetectPlatform[_detect_platform & _extract_url_id: Identify Platform & Content ID]
    
    DetectPlatform --> CheckIDIndex{DownloadIndex.find_by_id: Fast SQLite O(1) Check}
    CheckIDIndex -- Cache Hit --> ReturnCached[RETURN Cached Local File Path, is_cached = True]
    
    CheckIDIndex -- Miss --> MultiAuthLoop[Loop 8 Auth Strategies: no_auth -> cookies_file -> browser_cookies]
    
    MultiAuthLoop --> ExecYtDlp[yt_dlp.YoutubeDL.download]
    
    ExecYtDlp -- Extractor Error / Rate Limit --> AutoUpdate[_update_yt_dlp: Thread-Safe 12h Cooldown yt-dlp Pip Upgrade]
    AutoUpdate --> RetryAuth[Retry Download Loop with Updated Extractor]
    
    ExecYtDlp -- Extractor Fails Completely & Apify Enabled --> CDNFallback[_download_cdn_direct: Download Direct CDN Video via Requests/Urllib]
    
    ExecYtDlp -- Success --> Fingerprint[_calculate_content_fingerprint: Calculate 4-Frame Perceptual Hash / File SHA-1]
    CDNFallback --> Fingerprint
    
    Fingerprint --> CheckHashIndex{DownloadIndex.find_by_hash: Duplicate Content Hash in DB?}
    CheckHashIndex -- Match Found --> RemoveDup[Delete Downloaded Temp & RETURN Existing File Path]
    
    CheckHashIndex -- Unique --> RegisterDB[DownloadIndex.register: Store URL ID, Hash & File Path in data/index.db]
    
    RegisterDB --> ReturnNew[RETURN Local File Path, is_cached = False]
```

---

## 🛠️ Key Technical Features

### 1. 8-Stage Auth Fallback Strategy (`_build_strategy_opts`)
Rotates through 8 authentication strategies dynamically when site extractors encounter login walls or rate-limits:
- **`no_auth`**: Unauthenticated public link extraction.
- **`cookies_file`**: Import disk cookies from `cookies.txt`.
- **`username_password`**: Login credentials via environment variables.
- **`browser_*`**: Local browser cookie extraction (`firefox`, `edge`, `brave`, `chrome`, `opera`).

### 2. Thread-Safe Auto-Updating (`_update_yt_dlp`)
Detects extractor breakage keywords (`"no suitable extractor"`, `"unsupported url"`, `"401"`), triggering a thread-safe `pip install -U yt-dlp` upgrade with a 12-hour cooldown timer (`_UPDATE_COOLDOWN`).

### 3. Persistent SQLite Index (`DownloadIndex`)
Stores URL IDs, content hashes, file paths, and timestamps in a WAL-mode SQLite database (`data/index.db`), preventing redundant video downloads across worker execution sessions.

### 4. Perceptual 4-Frame Fingerprinting (`_calculate_content_fingerprint`)
Computes perceptual hashes across 4 evenly-spaced video frames ($0\%, 25\%, 50\%, 75\%$) using OpenCV grayscale scaling, falling back to SHA-1 file hashing when OpenCV is absent.

---

## 💥 Brutal & Honest Engineering Audit

| Metric | Score | Raw Unfiltered Reality |
| :--- | :---: | :--- |
| **Ingestion Resilience** | `9.8 / 10` | 8-stage authentication rotation and direct CDN fallbacks bypass social media login blocks. |
| **Deduplication Efficiency** | `9.7 / 10` | SQLite WAL database indexing and 4-frame perceptual fingerprinting eliminate duplicate video processing. |
| **Extractor Longevity** | `9.6 / 10` | Automatic `yt-dlp` pip updating prevents pipeline breakdown when platforms change video API formats. |
| **Subprocess Blocking During Update** | `7.8 / 10` | **CRITICAL FLAW**: `_update_yt_dlp` runs `pip install` synchronously in the main worker thread, blocking worker execution for up to 30 seconds. |
| **SQLite Lock Timeout Risk** | `8.2 / 10` | Multi-worker concurrent writes to `data/index.db` can trigger SQLite lock exceptions if `check_same_thread` wait exceeds 10s. |

---

## 💻 Code Usage & Public API

```python
from Ingestion_and_Download.downloader import download_video, DownloadIndex

# 1. Download video URL with multi-strategy fallback and automatic deduplication
local_path, is_cached = download_video(
    url="https://www.instagram.com/reel/C123456789/",
    custom_title="Viral Fashion Reel"
)

if local_path:
    print(f"Downloaded Video Path: {local_path} (Is Cached: {is_cached})")
else:
    print("Download failed across all 8 auth strategies.")

# 2. Check if a video URL ID exists in the persistent index
existing_path = DownloadIndex.find_by_id("C123456789")
if existing_path:
    print(f"Index Hit: File available at {existing_path}")
```
