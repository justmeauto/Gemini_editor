# 📄 Module Documentation: `content_ledger.py`

**Rating**: `9.5 / 10 (Grade A+ - Military-Grade Deduplication Ledger)`  
**Location**: `D:\AMTCE\AMTCE_Elite_Core\Ingestion_and_Download\content_ledger.py`  
**Target File Link**: [content_ledger.py](file:///D:/AMTCE/AMTCE_Elite_Core/Ingestion_and_Download/content_ledger.py)  
**Source**: `D:\AMTCE\Content_Harvester\content_ledger.py` (13,782 bytes)

---

## 👑 Purpose & Role

`content_ledger.py` is the **Military-Grade 3-Layer Deduplication Ledger (`ContentLedger`)** in the **Ingestion & Download** family.

Prevents re-downloading or re-publishing identical content across all sessions:
- **Layer 1 (Shortcode Lock)**: O(1) Instagram post ID lookup before download.
- **Layer 2 (Content Hash Lock)**: MD5 hash of video bytes after download.
- **Layer 3 (Cross-Channel Ledger)**: Tracks which channel consumed each clip to prevent cross-channel reuse.
