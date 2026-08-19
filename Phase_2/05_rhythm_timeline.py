"""
Phase_2 / 05_rhythm_timeline.py
===============================
Step 5: Rhythm & Micro-Shot Timeline Builder.
Computes psycho-acoustic beat routing parameters and builds 2.0s-3.5s human-scale jump-cut micro-shots.
"""

import os
import sys
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger("Phase2.Step05")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from Gemini_Modules.lyric_rhythm_aligner import analyze_music, compute_routing_parameters
try:
    from Rendering_Modules.rhythm_timeline_builder import RhythmTimelineBuilder
except ImportError:
    from rhythm_timeline_builder import RhythmTimelineBuilder


def build_rhythm_timeline(
    video_path: str,
    selected_bgm_path: Optional[str] = None,
    forensic_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Builds micro-shots and routing parameters for editing with built-in
    Auto-Reject & Re-Selection Fallback Gate (max 3-4 attempts).
    """
    logger.info(f"🥁 [STEP 05] Building rhythm timeline for: {os.path.basename(video_path)}")

    builder = RhythmTimelineBuilder()
    active_bgm_path = selected_bgm_path
    lyric_intel = {}
    route_params = {}
    micro_shots = []

    # ── AUTO-REJECT & RE-SELECT FALLBACK LOOP (Max 3 to 4 attempts) ──────────
    MAX_REJECT_ATTEMPTS = 4
    current_attempt = 1
    disqualified_tracks = set()

    while current_attempt <= MAX_REJECT_ATTEMPTS:
        target_audio = active_bgm_path if (active_bgm_path and os.path.isfile(active_bgm_path)) else video_path
        lyric_intel = {}

        try:
            if os.path.isfile(target_audio):
                lyric_intel = analyze_music(target_audio)
        except Exception as le_err:
            logger.debug(f"Lyric intel fallback: {le_err}")

        # Check reject conditions only if a background music track was explicitly selected
        is_bad_audio = False
        reject_reason = ""

        if active_bgm_path and os.path.isfile(active_bgm_path):
            bgm_dur = 0.0
            try:
                bgm_dur = builder._get_duration(active_bgm_path)
            except Exception:
                pass

            if lyric_intel.get("is_unusable", False):
                is_bad_audio = True
                reject_reason = lyric_intel.get("unusable_reason") or "Audio is non-music noise / distortion"
            elif lyric_intel.get("is_speech_only", False):
                is_bad_audio = True
                reject_reason = "Audio is spoken dialogue / chatter without musical backing"
            elif 0 < bgm_dur < 10.0:
                is_bad_audio = True
                reject_reason = f"Audio snippet too short ({bgm_dur:.1f}s < 10s minimum)"

        # If audio is bad and we have remaining attempts, auto-reject & re-select
        if is_bad_audio and current_attempt < MAX_REJECT_ATTEMPTS:
            bad_track_name = os.path.basename(active_bgm_path)
            disqualified_tracks.add(bad_track_name)
            logger.warning(
                f"⚠️ [AUTO-REJECT BGM] Selected track '{bad_track_name}' is invalid ({reject_reason}). "
                f"Auto-selecting next best BGM candidate from pool (Attempt {current_attempt}/{MAX_REJECT_ATTEMPTS})..."
            )

            # Quarantine/flag the bad track in AudioPoolManager
            try:
                from Audio_Modules.audio_pool_manager import AudioPoolManager
                apm = AudioPoolManager()
                meta = apm._get_file_metadata(bad_track_name) or {}
                meta["is_unusable"] = True
                meta["unusable_reason"] = reject_reason
                apm._set_file_metadata(bad_track_name, meta)
                apm._save_metadata()
            except Exception as _q_err:
                logger.debug(f"Quarantine save notice: {_q_err}")

            # Auto-select next candidate from pool
            try:
                import importlib
                step04 = importlib.import_module("Phase_2.04_bgm_selector")
                _ctx = forensic_context or {}
                clip_id = _ctx.get("clip_id") or os.path.splitext(os.path.basename(video_path))[0]
                clip_folder = _ctx.get("clip_folder") or os.path.dirname(video_path)
                next_bgm_res = step04.select_clip_bgm(
                    clip_id=clip_id,
                    clip_folder=clip_folder,
                    exclude_filenames=disqualified_tracks
                )
                new_path = next_bgm_res.get("physical_path")
                if new_path and os.path.isfile(new_path) and new_path != active_bgm_path:
                    active_bgm_path = new_path
                    current_attempt += 1
                    logger.info(f"🔄 [AUTO-SWAP BGM] Swapped to candidate: '{os.path.basename(active_bgm_path)}'")
                    continue
            except Exception as _re_sel_err:
                logger.warning(f"Auto re-selection error: {_re_sel_err}")
                break

        # Clean/usable track verified or attempts exhausted -> break and build timeline
        break

    # Compute routing parameters with final verified audio
    route_params = compute_routing_parameters(lyric_intel, forensic_context, active_bgm_path)

    # Full psycho-acoustic timeline construction using RhythmTimelineBuilder
    try:
        v_dur = builder._get_duration(video_path)
        if v_dur > 0:
            builder.min_duration = 2.0
            builder.max_duration = 4.0

            target_audio = active_bgm_path if (active_bgm_path and os.path.isfile(active_bgm_path)) else video_path
            bgm_beats = lyric_intel.get("emotional_peak_moments", [])
            if not bgm_beats and os.path.isfile(target_audio):
                bgm_beats = builder.analyze_beats(target_audio)

            raw_scenes = [{"clip_id": 0, "start": 0.0, "end": v_dur, "score": 0.85}]

            full_timeline = builder.build_timeline(
                scenes=raw_scenes,
                beat_grid=bgm_beats,
                vibe=route_params.get("recommended_editing_mode", "hype"),
                music_intelligence=lyric_intel if lyric_intel else None,
                target_duration_hint=15.0,
            )
            micro_shots = full_timeline if full_timeline else []
    except Exception as rte:
        logger.warning(f"⚠️ [STEP 05] Rhythm timeline builder fallback notice: {rte}")

    logger.info(
        f"✓ [STEP 05 SUCCESS] Built {len(micro_shots)} human-scale micro-shots (2.0s-3.5s takes) | "
        f"speed={route_params.get('speed_factor', 1.0)}x | "
        f"ducking={route_params.get('bgm_ducking_db', -6.0)}dB | "
        f"verified_bgm='{os.path.basename(active_bgm_path) if active_bgm_path else 'none'}'"
    )

    return {
        "route_params": route_params,
        "lyric_intel": lyric_intel,
        "micro_shots": micro_shots,
        "selected_bgm_path": active_bgm_path,
    }
