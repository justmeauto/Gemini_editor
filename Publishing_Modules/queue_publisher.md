# 📄 Module Documentation: `queue_publisher.py`

**Rating**: `9.5 / 10 (Grade A+ - Layer 3 Queue, Multi-Platform Publish & Cleanup Engine)`  
**Location**: `Publishing_Modules/queue_publisher.py`  
**Target File Link**: [queue_publisher.py](file:///d:/simple_scrapper%20and%20_uploader/Publishing_Modules/queue_publisher.py)  

---

## 👑 Purpose & Role

`queue_publisher.py` is the **Layer 3 Queue & Timed Scheduler Engine (`PublishQueue` / `start_publish_scheduler`)** in the **Publishing_Modules** family.

Manages persistent publishing queues (`publish_queue.json`), enforces peak-slot timing (`07:30`, `12:30`, `19:30`), triggers multi-channel uploads via `media_publisher_main.py`, commits published records to the ledger, and cleans up local video files post-publishing.
