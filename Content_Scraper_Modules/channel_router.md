# 📄 Module Documentation: `channel_router.py`

**Rating**: `9.3 / 10 (Grade A - Paparazzi Channel & Identity Router)`  
**Location**: `D:\AMTCE\AMTCE_Elite_Core\Ingestion_and_Download\channel_router.py`  
**Target File Link**: [channel_router.py](file:///D:/AMTCE/AMTCE_Elite_Core/Ingestion_and_Download/channel_router.py)  
**Source**: `D:\AMTCE\Content_Harvester\channel_router.py` (14,397 bytes)

---

## 👑 Purpose & Role

`channel_router.py` is the **Paparazzi Channel & Identity Router (`resolve_channel`)** in the **Ingestion & Download** family.

Determines destination account pools for scraped reels using `paparazzi_identities.json` lookups, clothing coverage thresholds, and gender heuristics (`female` $\rightarrow$ General_Fallback, `male/unknown` $\rightarrow$ Paparazzi_Channel).
