"""
downloader_main.py — Root Phase 1 Ingestion Launcher
=====================================================
Wrapper for Downloader_Modules/downloader_main.py
"""

from Downloader_Modules.downloader_main import run_phase1_ingestion

if __name__ == "__main__":
    import sys
    import argparse
    parser = argparse.ArgumentParser(description="Phase 1 Ingestion Orchestrator (Worker 1 & Worker 2)")
    parser.add_argument("--mode", type=str, choices=["auto", "manual"], default="auto", help="Ingestion mode ('auto' or 'manual')")
    parser.add_argument("--url", "-i", type=str, default=None, help="Target video URL for manual input mode")
    parser.add_argument("--limit", type=int, default=3, help="Max reels per account for automated mode")

    args = parser.parse_args()
    mode_to_use = "manual" if args.url else args.mode

    res = run_phase1_ingestion(mode=mode_to_use, url=args.url, limit_per_account=args.limit)
    if res.get("success"):
        print(f"\n🎉 PHASE 1 INGESTION COMPLETE: {res['count']} clip(s) ready in {res['downloads_dir']}")
    else:
        print(f"\n💥 INGESTION FAILED: {res.get('error')}")
        sys.exit(1)
