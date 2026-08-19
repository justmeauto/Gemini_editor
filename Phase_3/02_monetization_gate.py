"""
Phase 3 — Step 02: Monetization & Safety QA Gate
=================================================
Evaluates safety classification (safe, risky, blocked) and watermark compliance
before allowing publication. Blocks ad-unsafe or policy-violating content.
"""

import logging
from typing import Dict, Any

logger = logging.getLogger("phase3.step02_monetization_gate")

def verify_monetization_compliance(intelligence: Dict[str, Any]) -> Dict[str, Any]:
    """
    Verify safety and monetization compliance from clip intelligence.

    Args:
        intelligence: Full clip intelligence dictionary from Phase 2 / ClipIntelligenceStore.

    Returns:
        Dict containing compliance evaluation (approved: bool, status: str, reasons: list).
    """
    safety = intelligence.get("safety", {})
    classification = safety.get("classification", "safe").lower()
    monetization_safe = safety.get("monetization_safe", True)
    watermarks = intelligence.get("watermarks", [])

    reasons = []
    approved = True

    if classification == "blocked":
        approved = False
        reasons.append("Safety classification is BLOCKED (policy violation detected)")

    if not monetization_safe:
        approved = False
        reasons.append("Monetization flag is FALSE (ad suitability failed)")

    if len(watermarks) > 3:
        logger.warning(f"⚠️ Step 02 QA Gate: High watermark count ({len(watermarks)}) detected")
        reasons.append(f"Contains {len(watermarks)} overlay watermarks")

    status = "APPROVED" if approved else "BLOCKED"

    if approved:
        logger.info(f"🟢 Step 02 QA Gate: PASSED (classification={classification}, monetizable=True)")
    else:
        logger.warning(f"🔴 Step 02 QA Gate: BLOCKED (reasons={reasons})")

    return {
        "approved": approved,
        "status": status,
        "classification": classification,
        "monetization_safe": monetization_safe,
        "watermark_count": len(watermarks),
        "reasons": reasons
    }
