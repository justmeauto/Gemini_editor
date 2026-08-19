# 📄 Module Documentation: `meta_uploader.py`

**Rating**: `9.4 / 10 (Grade A - Resilient Async Meta Publishing Engine)`  
**Location**: `D:\AMTCE\AMTCE_Elite_Core\Publishing_and_Monetization\meta_uploader.py`  
**Target File Link**: [meta_uploader.py](file:///D:/AMTCE/AMTCE_Elite_Core/Publishing_and_Monetization/meta_uploader.py)

---

## 👑 Purpose & Role: Resilient Async Meta Publishing Engine

`meta_uploader.py` is the **Async Meta Publishing Pipeline (`AsyncMetaUploader`)** in the **Publishing & Monetization Engine** family.

It handles multi-niche automated video Reels and image post publishing to Instagram Business accounts and Facebook Pages via Meta Graph API v19.0. It integrates a **3-tier credential resolution engine**, **person-safe OpenCV 4:5 image cropping**, a **3-provider temporary image hosting fallback cascade**, and **Graph API resumable binary uploads**.

---

## 🏗️ Technical Architecture & Resumable Upload Lifecycle

```mermaid
flowchart TD
    VideoFile[Video MP4 / Image JPEG] --> CredResolve[_resolve_meta_config\n3-Tier Credential Resolution]
    
    CredResolve --> CheckGate{Niche & Meta Platform Enabled?}
    CheckGate -- No --> Skip[Skip Meta Upload]
    CheckGate -- Yes --> PlatformCheck{Select Targets: IG / FB}
    
    PlatformCheck --> IGUpload[_upload_to_instagram\nResumable Binary Upload]
    PlatformCheck --> FBUpload[_upload_to_facebook\nFacebook Page Video / Reels API]
    
    IGUpload --> InitIG[POST /ig_id/media data={upload_type: resumable}]
    InitIG --> RUpload[POST to rupload URI with Content-Type video/mp4]
    RUpload --> PollIG[_wait_for_media_status\nPoll status until FINISHED]
    PollIG --> PubIG[POST /ig_id/media_publish]
    
    PubIG --> IGThumbCheck{Is Thumbnail Provided?}
    IGThumbCheck -- Yes --> SmartCrop[_prepare_ig_photo\nOpenCV Face & HOG Body Person-Safe 4:5 Crop]
    SmartCrop --> HostImage[_host_temp_image\n3-Provider Fallback: imgBB -> freeimage -> tmpfiles]
    HostImage --> PostPhoto[_upload_photo_to_instagram]
    
    PubIG --> ReturnResults[Return Status, Media IDs, Permalinks]
    FBUpload --> ReturnResults
    PostPhoto --> ReturnResults
```

---

## 🛠️ Key Technical Modules

### 1. 3-Tier Credential Resolution (`_resolve_meta_config`)
1. **Tier 1**: `Credentials/social_media/{niche}/meta_config.json` (Niche-specific credentials; filters out `DEMO_` or `your_` placeholder strings).
2. **Tier 2**: `Credentials/social_media/General_Fallback/meta_config.json` (Shared fallback account).
3. **Tier 3**: Root `.env` environment variables (`IG_BUSINESS_ID`, `IG_BUSINESS_TOKEN`, `META_PAGE_ID`, `META_PAGE_TOKEN`).

### 2. Person-Safe OpenCV 4:5 Formatter (`_prepare_ig_photo`)
Detects head-top bounds (OpenCV face cascade) and feet-bottom bounds (OpenCV HOG body detector). Centers and crops background pixels around the subject to fit Instagram's 4:5 portrait ratio ($1080 \times 1350\text{px}$) — **the subject is NEVER cut**.

### 3. 3-Provider Public Image Hosting Cascade (`_host_temp_image`)
Sequentially attempts 3 providers to obtain temporary public URLs required by Instagram's image container API:
1. `imgBB` API
2. `freeimage.host` API
3. `tmpfiles.org` API

---

## 💥 Brutal & Honest Engineering Audit

| Metric | Score | Raw Unfiltered Reality |
| :--- | :---: | :--- |
| **Publishing Resilience** | `9.7 / 10` | Full resumable upload support, container status polling, and automatic OAuth token expiration error detection. |
| **Person-Safe Image Cropping**| `9.5 / 10` | Dual face + HOG body detection preserves subjects perfectly when converting to Instagram 4:5 aspect ratio. |
| **Credential Resolution** | `9.4 / 10` | 3-tier fallback resolution with demo-placeholder filtering isolates multi-niche accounts seamlessly. |
| **Full File Memory Reading**| `8.2 / 10` | `video_bytes = f.read()` loads the entire file into RAM before posting to rupload instead of using streaming chunk generators. |
| **Duplicate Declarations** | `8.8 / 10` | Lines 14-16 and 17-19 contain duplicate constant definitions (`GRAPH_API_URL`, `MAX_RETRIES`, `RETRY_DELAY`). |

---

## 💻 Code Usage & Public API

```python
import asyncio
from Publishing_and_Monetization.meta_uploader import AsyncMetaUploader

# Orchestrate multi-platform Meta upload
results = asyncio.run(
    AsyncMetaUploader.upload_to_meta(
        video_path="Influencer_Output/final_reel.mp4",
        caption="Check out this style! 🔥 #fashion #reels #vid_a3f9b2",
        niche="Fashion & Style",
        thumbnail_path="Influencer_Output/cover.jpg"
    )
)

print(f"Instagram Post: {results['instagram']['status']} | Link: {results['instagram'].get('link')}")
print(f"Facebook Post: {results['facebook']['status']} | Link: {results['facebook'].get('link')}")
```
