"""
Phase 3 — Step 07: Master RAG Vector Memory Committer
=====================================================
Finalizes per-clip RAG intelligence in pool_metadata.json. Stores complete
end-to-end editing behavior (audio_data + visual_context + editing_plan + publishing_meta)
so future editing tasks can extract successful creator behavior without Gemini API calls.
"""

import os
import time
import logging
from typing import Dict, Any
from Gemini_Modules.clip_intelligence_store import ClipIntelligenceStore

logger = logging.getLogger("phase3.step07_rag_creator_updater")

def commit_rag_creator_behavior(
    clip_id: str,
    video_path: str,
    publishing_meta: Dict[str, Any],
    publish_results: Dict[str, Any],
    clip_dir: str = None
) -> Dict[str, Any]:
    """
    Commit final clip intelligence & publishing results to master RAG store.

    Args:
        clip_id: Unique clip identifier.
        video_path: Path to rendered video file.
        publishing_meta: Generated titles/captions/hashtags.
        publish_results: Results from platform publishing steps.
        clip_dir: Optional clip directory path.

    Returns:
        Dict with RAG commit status and pool metadata info.
    """
    try:
        store = ClipIntelligenceStore()
        folder = clip_dir or os.path.dirname(video_path)
        
        # Load existing intelligence record
        data = store.load(clip_id, folder) or store.create_blank(clip_id, folder)

        # Attach publishing performance & RAG tags
        data["publishing"] = {
            "title": publishing_meta.get("title", ""),
            "caption": publishing_meta.get("caption", ""),
            "intent": publishing_meta.get("intent", "general_content"),
            "tone": publishing_meta.get("tone", "engaging"),
            "results": publish_results,
            "committed_at": time.time()
        }

        # Update Master RAG pool
        store._pool_write(clip_id, data)
        master_pool = store._pool_read()

        logger.info(
            f"🧠 Step 07 RAG Committer: Successfully committed '{clip_id}' to master pool "
            f"({len(master_pool.get('clips', {}))} total clips indexed)"
        )

        return {
            "status": "success",
            "clip_id": clip_id,
            "master_clip_count": len(master_pool.get("clips", {})),
            "rag_committed": True
        }
    except Exception as e:
        logger.error(f"❌ Step 07 RAG Committer error: {e}")
        return {"status": "error", "error": str(e), "clip_id": clip_id}
