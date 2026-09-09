"""
Phase_2 / 04_bgm_selector.py
============================
Step 4: Gemini Call 2 — BGM Selector.
Cross-matches clip's visual_context + audio_data vs ALL pooled clip audio records from ClipIntelligenceStore.
Selects single best BGM track and saves decision to clip intelligence JSON.
"""

import os
import sys
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger("Phase2.Step04")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from Gemini_Modules.lyric_rhythm_aligner import select_best_audio_for_clip
from Audio_Modules.audio_pool_manager import AudioPoolManager


def select_clip_bgm(
    clip_id: str,
    clip_folder: Optional[str] = None,
    audio_dir: Optional[str] = None,
    intent_vector: Optional[Dict[str, Any]] = None,
    exclude_filenames: Optional[set] = None,
) -> Dict[str, Any]:
    """
    Executes Gemini Call 2 BGM Selector.

    Stage 2 Cache Lock:
        If intent_vector.preserve_music == True AND a cached BGM track is found
        in ClipIntelligenceStore, the cached track is returned IMMEDIATELY and
        Gemini Call 2 is skipped entirely. This guarantees 0 unintended music
        changes on re-edits and saves ~2000 tokens per run.

    Returns dict containing selected_audio_track, full physical path, and alignment score.
    """
    _intent = intent_vector or {}
    preserve_music = _intent.get("preserve_music", False)

    # ── STATEFUL CACHE LOCK (Stage 2 Cache-First BGM) ────────────────────────
    if preserve_music:
        cached_bgm = _try_load_cached_bgm(clip_id, clip_folder, audio_dir)
        cached_track = cached_bgm.get("selected_audio_track") if cached_bgm else None

        is_excluded = False
        if cached_track and exclude_filenames:
            ct_lower = cached_track.lower()
            ct_base = os.path.basename(cached_track).lower()
            for ef in exclude_filenames:
                ef_lower = str(ef).lower()
                if ct_lower == ef_lower or ct_base == os.path.basename(ef_lower):
                    is_excluded = True
                    break

        if cached_bgm and not is_excluded:
            logger.info(
                f"🔒 [STEP 04] BGM CACHE LOCK — Skipping Gemini Call 2. "
                f"Reusing: '{cached_track}' "
                f"(intent: preserve_music=True)"
            )
            return cached_bgm
        elif is_excluded:
            logger.info(
                f"🚫 [STEP 04] Cached BGM '{cached_track}' is in exclude_filenames. "
                "Running Gemini Call 2 fresh for alternative BGM."
            )
        else:
            logger.info(
                "[STEP 04] preserve_music=True but no cached BGM found. "
                "Running Gemini Call 2 fresh."
            )

    # ── STANDARD GEMINI CALL 2 ────────────────────────────────────────────────
    logger.info(f"🎶 [STEP 04] Running Gemini Call 2 BGM Selector for clip '{clip_id}'...")

    res = select_best_audio_for_clip(
        clip_id=clip_id,
        clip_folder=clip_folder,
        audio_dir=audio_dir,
        exclude_filenames=exclude_filenames
    )
    selected_track_name = res.get("selected_audio_track")

    # Resolve physical path
    resolved_path = None
    target_dest = clip_folder if (clip_folder and os.path.exists(clip_folder)) else os.path.join(_REPO_ROOT, "data", "runtime_audio")
    os.makedirs(target_dest, exist_ok=True)

    if selected_track_name:
        # Pass Gemini's selection directly to Telegram Vault Indexer to retrieve the audio
        try:
            from Telegram_Storage_Modules.telegram_vault_indexer import TelegramVaultIndexer
            vault = TelegramVaultIndexer()
            selected_fid = res.get("telegram_file_id")
            resolved_path = vault.hydrate_bgm_track_from_vault(
                selected_track_name,
                target_dest,
                file_id=selected_fid
            )
            if resolved_path and os.path.isfile(resolved_path):
                logger.info(f"✓ [STEP 04] BGM track retrieved via Telegram Vault Indexer: '{selected_track_name}' -> {resolved_path}")
        except Exception as _vh_err:
            logger.debug(f"[STEP 04] Vault BGM track hydration notice: {_vh_err}")

    # Fallback to pool manager ONLY if Gemini returned no track selection at all
    if not selected_track_name:
        logger.warning("⚠️ [STEP 04] Gemini returned no track selection. Resorting to pool manager emergency fallback.")
        try:
            pool = AudioPoolManager(base_dir=audio_dir)
            resolved_path = pool.select_best_audio(exclude_filenames=exclude_filenames)
            if resolved_path:
                selected_track_name = os.path.basename(resolved_path)
                res["selected_audio_track"] = selected_track_name
                res["alignment_score"] = res.get("alignment_score") or 0.80
        except Exception as pool_err:
            logger.warning(f"⚠️ [STEP 04] BGM pool manager fallback notice: {pool_err}")

    # Fallback to clip's own extracted audio if pool is empty or returned no track
    if (not resolved_path or not os.path.isfile(resolved_path)) and clip_folder and os.path.isdir(clip_folder):
        for cand_name in ("video_extracted.wav", "video_extracted.mp3"):
            cand_path = os.path.join(clip_folder, cand_name)
            if os.path.isfile(cand_path) and os.path.getsize(cand_path) > 1024:
                resolved_path = cand_path
                selected_track_name = cand_name
                res["selected_audio_track"] = selected_track_name
                res["alignment_score"] = res.get("alignment_score") or 0.85
                logger.info(f"🎵 [STEP 04 FALLBACK] No external BGM in pool. Adopted clip's extracted audio: {resolved_path}")
                break

    res["physical_path"] = resolved_path

    # Save selected BGM track choice to ClipIntelligenceStore
    if selected_track_name:
        try:
            from Gemini_Modules.clip_intelligence_store import ClipIntelligenceStore
            store = ClipIntelligenceStore(clip_id=clip_id, clip_folder=clip_folder)
            audio_data = store.get("audio_data") or {}
            audio_data["selected_bgm_track"] = selected_track_name
            audio_data["selected_audio_track"] = selected_track_name
            audio_data["alignment_score"] = res.get("alignment_score", 0.85)
            store.set("audio_data", audio_data)

            pm = AudioPoolManager()
            pm.record_selected_audio(
                social_url_or_shortcode=clip_id or "",
                selected_track_name=selected_track_name,
                telegram_file_id=res.get("telegram_file_id"),
                alignment_score=res.get("alignment_score", 0.85),
                reasoning=res.get("reasoning", "")
            )
        except Exception as _st_err:
            logger.warning(f"[STEP 04] ⚠️ Failed to record BGM selection in ClipIntelligenceStore / AudioPoolManager — "
                           f"selected_audio will NOT be persisted and rejection purge will be blind: {_st_err}")

    logger.info(
        f"✓ [STEP 04 SUCCESS] BGM Selected: '{selected_track_name}' "
        f"(score={res.get('alignment_score', 0.0):.2f}) -> {resolved_path}"
    )
    return res


def _try_load_cached_bgm(
    clip_id: str,
    clip_folder: Optional[str],
    audio_dir: Optional[str],
) -> Optional[Dict[str, Any]]:
    """
    Attempts to load a previously selected BGM track from ClipIntelligenceStore.
    Returns a fully resolved BGM result dict if a cache hit is found, else None.
    """
    try:
        from Gemini_Modules.clip_intelligence_store import ClipIntelligenceStore
        store = ClipIntelligenceStore(clip_id=clip_id, clip_folder=clip_folder)
        audio_data = store.get("audio_data") or {}
        cached_track = audio_data.get("selected_bgm_track") or audio_data.get("selected_audio_track")

        if not cached_track:
            return None

        # Resolve physical path from disk
        if audio_dir is None:
            audio_dir = os.path.join(_REPO_ROOT, "Original_audio")

        resolved_path = None
        for candidate_dir in [
            os.path.join(audio_dir, "active"),
            audio_dir,
            os.path.join(audio_dir, "cooldown"),
        ]:
            if os.path.isdir(candidate_dir):
                cpath = os.path.join(candidate_dir, cached_track)
                if os.path.isfile(cpath):
                    resolved_path = cpath
                    break

        if not resolved_path:
            logger.warning(f"[STEP 04] Cached BGM '{cached_track}' not found on disk. Will re-select.")
            return None

        return {
            "selected_audio_track": cached_track,
            "physical_path": resolved_path,
            "alignment_score": audio_data.get("alignment_score", 1.0),
            "_source": "cache_lock",
        }

    except Exception as e:
        logger.debug(f"[STEP 04] Cache load attempt failed: {e}")
        return None
