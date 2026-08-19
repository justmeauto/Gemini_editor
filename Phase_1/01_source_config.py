"""
01_source_config.py — Phase 1 Step 1: Target Source Account & Channel Resolver
================================================================================
Resolves target celebrity/actress/paparazzi account handles from:
  - Explicit function arguments
  - Content_Scraper_Modules/source_accounts.json
  - Fallback default pools
"""

import os
import sys
import json
import logging
from typing import List, Dict, Any, Optional, Callable

logger = logging.getLogger("Phase1.Step01")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACCOUNTS_JSON = os.path.join(_REPO_ROOT, "Content_Scraper_Modules", "source_accounts.json")


def resolve_target_accounts(
    target_accounts: Optional[List[str]] = None,
    max_limit: int = 2,
    callback: Optional[Callable[[str, str, Dict[str, Any]], None]] = None
) -> Dict[str, Any]:
    """
    Step 1 Execution: Resolves target accounts to scrape.
    """
    if callback:
        callback("step_01", "running", {"message": "Resolving target account pool..."})

    try:
        from Downloader_Modules.scheduled_scraper_manager import purge_expired_accounts
        purge_expired_accounts()
    except Exception:
        pass

    import re

    resolved_sources = []
    if target_accounts and isinstance(target_accounts, list):
        valid_candidates = []
        for a in target_accounts:
            if a and isinstance(a, str):
                h = a.strip().lstrip("@")
                if re.match(r"^[A-Za-z0-9._]{1,30}$", h):
                    valid_candidates.append(h)
                else:
                    logger.warning(f"⚠️ [STEP 01] Ignoring invalid target account string: '{a[:40]}...'")
        if valid_candidates:
            resolved_sources = valid_candidates
            logger.info(f"📋 [STEP 01] Received explicit target accounts: {resolved_sources}")

    if not resolved_sources and os.path.exists(ACCOUNTS_JSON):
        try:
            with open(ACCOUNTS_JSON, "r", encoding="utf-8") as f:
                data = json.load(f)
                raw_accs = data.get("source_accounts") or data.get("_paparazzi", {}).get("source_accounts", [])
                resolved_sources = [h for h in raw_accs if re.match(r"^[A-Za-z0-9._]{1,30}$", str(h))]
                logger.info(f"📋 [STEP 01] Loaded accounts from source_accounts.json: {resolved_sources}")
        except Exception as e:
            logger.warning(f"⚠️ [STEP 01] Failed to read source_accounts.json: {e}")

    if not resolved_sources:
        logger.warning("⚠️ [STEP 01] No valid target accounts configured. Please add accounts via Telegram Chat /addaccount <handle>.")
        resolved_sources = []

    # Enforce max limit for batch run
    final_targets = resolved_sources[:max_limit]

    res = {
        "step": "step_01",
        "status": "success",
        "accounts": final_targets,
        "count": len(final_targets)
    }

    if callback:
        callback("step_01", "success", {
            "message": f"Resolved {len(final_targets)} target account(s): {', '.join(final_targets)}",
            "accounts": final_targets
        })

    return res
