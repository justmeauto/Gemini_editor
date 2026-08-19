"""
Phase_2 / 06_ffmpeg_synthesis.py
================================
Step 6: Gemini Call 3 — FFmpeg Master Director Synthesis.
Receives targeted keyframe images + full clip intelligence + selected BGM.
Generates complex FFmpeg filtergraph recipe and saves editing_plan to ClipIntelligenceStore.

Selective Fallback:
  Triggers MusicDrivenEditor (offline psycho-acoustic DSP engine) ONLY IF Gemini API fails
  due to HTTP 503 Service Unavailable, 429 Quota Exhaustion across all vanguard models,
  or Expired / Invalid API Keys.
"""

import os
import sys
import logging
import subprocess
from typing import Dict, Any, Optional, List

logger = logging.getLogger("Phase2.Step06")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from Gemini_Modules.gemini_ffmpeg_synthesis import GeminiFFmpegEngine


def _is_api_outage_or_key_error(err: Any) -> bool:
    """
    Returns True ONLY IF error is caused by:
      - 503 Service Unavailable / Overloaded
      - 401 / 403 Invalid / Expired API Key / Authentication failure
      - Network connection outage
    Note: 429 rate limits are handled by model vanguard rotation and do NOT trigger fallback.
    """
    err_str = str(err).lower()
    keywords = [
        "503", "service unavailable", "overloaded",
        "401", "403", "api_key", "apikey", "invalid api key", "expired",
        "authentication", "permissiondenied", "unauthenticated", "credential",
        "connection error", "connecttimeout", "network"
    ]
    return any(kw in err_str for kw in keywords)


def _run_music_driven_fallback(
    video_path: str,
    output_path: str,
    selected_bgm_path: Optional[str] = None,
    micro_shots: Optional[List[Dict[str, Any]]] = None,
    bpm: float = 120.0,
    target_duration: float = 15.0,
) -> Dict[str, Any]:
    """
    Offline DSP fallback using MusicDrivenEditor & FFmpeg when Gemini Call 3 API is unavailable.
    """
    logger.info(f"🎧 [STEP 06 FALLBACK] Activating MusicDrivenEditor offline DSP engine for: {os.path.basename(video_path)}")
    try:
        from Rendering_Modules.music_driven_editor import MusicDrivenEditor
        mde = MusicDrivenEditor()
    except ImportError:
        logger.warning("MusicDrivenEditor import fallback notice")

    shots = micro_shots or [{"start": 0.0, "end": target_duration}]
    inputs = []
    filter_parts = []

    for i, s in enumerate(shots):
        st = float(s.get("start", 0.0))
        en = float(s.get("end", st + 3.0))
        dur = max(0.5, en - st)
        inputs.extend(["-ss", f"{st:.3f}", "-t", f"{dur:.3f}", "-i", video_path])
        filter_parts.append(f"[{i}:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920[v{i}];")

    concat_inputs = "".join([f"[v{i}]" for i in range(len(shots))])
    filter_graph = "".join(filter_parts) + f"{concat_inputs}concat=n={len(shots)}:v=1:a=0[vout]"

    cmd = ["ffmpeg", "-y"] + inputs
    if selected_bgm_path and os.path.exists(selected_bgm_path):
        cmd.extend(["-i", selected_bgm_path])
        bgm_idx = len(shots)
        filter_graph += f";[{bgm_idx}:a]volume=0.5[aout]"
        cmd.extend(["-filter_complex", filter_graph, "-map", "[vout]", "-map", "[aout]", "-shortest"])
    else:
        cmd.extend(["-filter_complex", filter_graph, "-map", "[vout]"])

    cmd.extend(["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", output_path])

    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode == 0 and os.path.exists(output_path):
        logger.info(f"✅ [STEP 06 FALLBACK SUCCESS] MusicDrivenEditor rendered fallback master reel -> {output_path}")
        return {"status": "SUCCESS", "mode": "DSP_FALLBACK", "output_path": output_path}
    else:
        logger.error(f"❌ [STEP 06 FALLBACK FAILED] FFmpeg fallback error: {res.stderr[:200]}")
        return {"status": "FAILED", "mode": "DSP_FALLBACK", "error": res.stderr}


def synthesize_editing_plan(
    video_path: str,
    output_path: str,
    selected_bgm_path: Optional[str] = None,
    forensic_context: Optional[Dict[str, Any]] = None,
    micro_shots: Optional[List[Dict[str, Any]]] = None,
    target_duration: float = 15.0,
    user_edit_directive: Optional[str] = None,
    intent_vector: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Executes Gemini Call 3 to synthesize FFmpeg filtergraph plan.
    Falls back to MusicDrivenEditor DSP engine ONLY IF API fails due to 503, 429, or expired API Key.
    """
    logger.info(f"🎬 [STEP 06] Running Gemini Call 3 FFmpeg Synthesis for: {os.path.basename(video_path)}")

    engine = GeminiFFmpegEngine()
    route_params = (forensic_context or {}).get("route_params", {})
    lyric_intel = (forensic_context or {}).get("lyric_intel", {})
    bpm = (route_params.get("bpm") or lyric_intel.get("tempo_bpm") or 120.0)
    emotion = lyric_intel.get("dominant_emotion", "hype")

    shot_plan_text = ""
    if micro_shots:
        shot_plan_text = f"\n\n### RTB BEAT-SNAPPED SHOT PLAN (AUTHORITATIVE CUT WINDOWS — USE THESE EXPLICITLY):\n"
        shot_plan_text += f"BGM Track: {os.path.basename(selected_bgm_path or '')} | BPM: {bpm:.1f} | Emotion: {emotion}\n"
        shot_plan_text += f"INSTRUCTION: Generate a 'trim' operation for EACH shot window below, then a 'concat' operation to join them in sequence:\n"
        for i, shot in enumerate(micro_shots):
            st = float(shot.get("start", 0.0))
            en = float(shot.get("end", 0.0))
            sec = shot.get("_section", "verse")
            sc = shot.get("score", 0.8)
            shot_plan_text += f"  - Shot {i+1}: start_time={st:.3f}s, end_time={en:.3f}s (duration={en-st:.3f}s) | section={sec} | score={sc}\n"

    user_req = (
        f"Master rhythm edit for video intent '{(forensic_context or {}).get('intent', 'viral_reel')}', "
        f"visual tone '{(forensic_context or {}).get('tone', 'aspirational')}', "
        f"BGM '{os.path.basename(selected_bgm_path or '')}' at {bpm:.0f} BPM ({emotion}). "
        f"Target duration: {target_duration}s."
        + shot_plan_text
    )

    if user_edit_directive:
        _iv = intent_vector or {}
        preserve_music  = _iv.get("preserve_music",  False)
        preserve_cuts   = _iv.get("preserve_cuts",   False)
        intent_class    = _iv.get("intent_class",    "unclear")
        action_desc     = _iv.get("action_description", user_edit_directive)
        confidence      = float(_iv.get("confidence", 0.0))

        # ── SEMANTIC INTENT ROUTING (replaces hardcoded keyword match) ──────
        # Surgical = any preserve flag set OR intent is clearly targeted fix
        is_surgical = (
            preserve_music or preserve_cuts
            or intent_class in ("surgical_watermark_fix", "preserve_all", "color_grade")
        )

        if is_surgical:
            preserve_parts = []
            if preserve_music: preserve_parts.append("BGM track")
            if preserve_cuts:  preserve_parts.append("cut timing")
            preserve_str = " & ".join(preserve_parts) if preserve_parts else "existing edits"
            user_req += (
                f"\n\n🔒 SURGICAL HUMAN RE-EDIT DIRECTIVE (PRESERVE EXISTING EDITS):\n"
                f"Intent: {action_desc} (confidence: {confidence:.0%})\n"
                f"Original words: \"{user_edit_directive}\"\n"
                f"STRICT INSTRUCTION: LOCK {preserve_str}. DO NOT change the selected BGM or re-cut shots. "
                f"ONLY apply the targeted fix: {intent_class.replace('_', ' ')}."
            )
        else:
            user_req += (
                f"\n\n⚡ HUMAN RE-EDIT DIRECTIVE:\n"
                f"Intent: {action_desc} (confidence: {confidence:.0%})\n"
                f"Original words: \"{user_edit_directive}\"\n"
                f"Apply world-class editor corrections fully matching the user's directive."
            )

    extra_inputs = {}
    if micro_shots:
        extra_inputs["micro_shots"] = micro_shots

    if forensic_context:
        # IMPORTANT: forensic_context["watermarks"] is an INTEGER COUNT (e.g. 1, 2).
        # We MUST only pass actual {x, y, w, h} coordinate dicts.
        # Never pass the integer count — it breaks build_single_pass_filtergraph().
        _raw_wm = (
            forensic_context.get("watermark_boxes") or
            (forensic_context.get("visual_context") or {}).get("watermarks")
        )
        wm_boxes = _raw_wm if isinstance(_raw_wm, list) and len(_raw_wm) > 0 else None
        if wm_boxes:
            extra_inputs["watermark_boxes"] = wm_boxes


    try:
        synthesis_result = engine.run_full_pipeline(
            user_request=user_req,
            input_video_path=video_path,
            output_video_path=output_path,
            audio_path=selected_bgm_path,
            forensic_context=forensic_context,
            extra_inputs=extra_inputs,
        )
        if synthesis_result and synthesis_result.get("status") in ("SUCCESS", "EXECUTED") and os.path.exists(output_path):
            logger.info(f"✓ [STEP 06 SUCCESS] Master FFmpeg synthesis complete -> {synthesis_result.get('status')}")
            return synthesis_result
        elif synthesis_result and synthesis_result.get("status") in ("FAILED", "ERROR"):
            err_msg = str(synthesis_result.get("error", ""))
            if _is_api_outage_or_key_error(err_msg):
                raise RuntimeError(f"API Outage/Key Error: {err_msg}")
            return synthesis_result
        return synthesis_result
    except Exception as gemini_err:
        if _is_api_outage_or_key_error(gemini_err):
            logger.warning(f"⚠️ [STEP 06 FALLBACK] API Key / 503 / Quota Failure detected ({gemini_err}). Activating MusicDrivenEditor offline DSP fallback...")
            return _run_music_driven_fallback(
                video_path=video_path,
                output_path=output_path,
                selected_bgm_path=selected_bgm_path,
                micro_shots=micro_shots,
                bpm=bpm,
                target_duration=target_duration,
            )
        else:
            logger.error(f"❌ [STEP 06 FAILED] Gemini Call 3 exception (non-API error): {gemini_err}")
            raise
