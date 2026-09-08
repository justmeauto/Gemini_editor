"""
Core_Modules / rating_engine.py
===============================
Processes 1-5 star user feedback ratings for rendered clips.
Updates clip intelligence records in pool_metadata.json so RAG retrieval
weights high-rated editing patterns and penalizes low-rated clips.
"""

import logging
from typing import Dict, Any, Optional
from Gemini_Modules.clip_intelligence_store import ClipIntelligenceStore

logger = logging.getLogger("core.rating_engine")

# Rating weight multiplier scale
RATING_WEIGHT_MAP = {
    5: 1.5,   # Gold tier pattern — high priority RAG match
    4: 1.2,   # Good pattern — standard match
    3: 1.0,   # Neutral — baseline
    2: 0.7,   # Weak pattern — lower match priority
    1: 0.2    # Poor pattern — avoid list
}

def record_user_clip_rating(clip_id: str, rating: int, feedback_note: Optional[str] = None) -> Dict[str, Any]:
    """
    Record human feedback rating for a rendered clip and update pool_metadata.json.

    Args:
        clip_id: Clip identifier.
        rating: Integer score from 1 to 5.
        feedback_note: Optional text feedback note.

    Returns:
        Dict containing rating status, assigned weight, and updated clip summary.
    """
    clean_rating = max(1, min(5, int(rating)))
    weight = RATING_WEIGHT_MAP.get(clean_rating, 1.0)
    
    try:
        store = ClipIntelligenceStore()
        pool = store._pool_read()
        clips = pool.get("clips", {})
        
        if clip_id not in clips:
            logger.warning(f"⚠️ RatingEngine: clip '{clip_id}' not found in master pool — creating record")
            clip_data = {"rating": clean_rating, "rag_weight": weight}
        else:
            clip_data = clips[clip_id]
            clip_data["user_rating"] = clean_rating
            clip_data["rag_weight"] = weight
            if feedback_note:
                clip_data["feedback_note"] = str(feedback_note)

        # Write back to master RAG pool
        store._pool_write(clip_id, clip_data)
        logger.info(f"⭐ RatingEngine: Recorded {clean_rating}-star rating (weight={weight}) for clip '{clip_id}'")

        return {
            "status": "success",
            "clip_id": clip_id,
            "rating": clean_rating,
            "rag_weight": weight,
            "updated": True
        }
    except Exception as e:
        logger.error(f"❌ RatingEngine failed for clip '{clip_id}': {e}")
        return {"status": "error", "error": str(e), "clip_id": clip_id}


def record_multi_attempt_feedback(
    clip_id: str,
    winning_attempt_idx: Optional[int],
    total_attempts: int = 5,
    attempt_paths: Optional[list] = None
) -> Dict[str, Any]:
    """
    Records human rating across multi-attempt retries (1 to MAX_RETRIES).
    
    If winning_attempt_idx is provided (0-indexed):
      - Marks winning attempt index with 5-star rating (weight = 1.5)
      - Marks non-selected attempt indices as bad patterns (weight = 0.2)
    If winning_attempt_idx is None ("All 5 Attempts Are Bad"):
      - Marks all attempt indices as bad patterns (weight = 0.1) in pool_metadata.json.
    """
    try:
        store = ClipIntelligenceStore()
        pool = store._pool_read()
        clips = pool.get("clips", {})
        
        clip_data = clips.get(clip_id, {})
        attempts_record = clip_data.get("attempts", {})

        for idx in range(1, total_attempts + 1):
            att_key = f"attempt_{idx}"
            if winning_attempt_idx is not None and idx == (winning_attempt_idx + 1):
                attempts_record[att_key] = {
                    "rating": 5,
                    "rag_weight": 1.5,
                    "is_winner": True,
                    "path": attempt_paths[idx - 1] if (attempt_paths and idx - 1 < len(attempt_paths)) else None
                }
            else:
                attempts_record[att_key] = {
                    "rating": 1,
                    "rag_weight": 0.1 if winning_attempt_idx is None else 0.2,
                    "is_winner": False,
                    "path": attempt_paths[idx - 1] if (attempt_paths and idx - 1 < len(attempt_paths)) else None
                }

        clip_data["attempts"] = attempts_record
        if winning_attempt_idx is not None:
            clip_data["winning_attempt"] = winning_attempt_idx + 1
            clip_data["user_rating"] = 5
            clip_data["rag_weight"] = 1.5
        else:
            clip_data["winning_attempt"] = None
            clip_data["user_rating"] = 1
            clip_data["rag_weight"] = 0.1

        store._pool_write(clip_id, clip_data)
        logger.info(f"📊 RatingEngine: Updated multi-attempt RAG feedback for '{clip_id}' (winner={winning_attempt_idx + 1 if winning_attempt_idx is not None else 'None'})")

        return {
            "status": "success",
            "clip_id": clip_id,
            "winning_attempt": winning_attempt_idx + 1 if winning_attempt_idx is not None else None,
            "total_attempts": total_attempts
        }
    except Exception as e:
        logger.error(f"❌ RatingEngine multi-attempt error for '{clip_id}': {e}")
        return {"status": "error", "error": str(e), "clip_id": clip_id}
