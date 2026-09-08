"""
Gemini_Modules/script_synthesis_engine.py
=========================================
ScriptSynthesisEngine: The Creative Bridge between Creator Behavior Model (CBM)
and Gemini FFmpeg Command Synthesis.

Architecture:
  [pool_metadata.json (Clips Ground Truth)] ──┐
                                              ├──► ScriptSynthesisEngine ──► creative_script.json
  [CBM Creator DNA (Verified Style Only)]   ──┘           │
                                                          ▼
                                            GeminiFFmpegEngine.run_full_pipeline()
                                            (consumes structured script blueprint)

Principles:
  1. Strict Separation: Content Payload (factual footage) vs Style Guide (Creator DNA).
  2. Grounded Output Schema: Traceable segments with source clip, timestamps, and exact quotes.
  3. Two-Tier Segment Validation: Drop invalid/hallucinated segments individually; fallback if surviving < 2.
  4. Shared Timeline Post-Processing: Deterministically compute sequential timeline_time_range_s on all paths.
  5. Citation Verification: Substring check quotes against transcripts/captions before downstream release.
  6. Per-Segment Vocal Intelligence: Modal checking (vocals vs visual overlays) per individual clip.
  7. Operation Enum Mapping: Constrain editing notes strictly to FFmpeg-executable primitives.
  8. Behavioral Fallback: Zero-hallucination per-clip timestamp tracking with bounded offsets.

Version: 1.1
Author: AMTCE Autonomous Multimedia Transformation Compilation Engine
"""

import os
import re
import json
import time
import uuid
import logging
from typing import Dict, List, Optional, Any, Tuple

logger = logging.getLogger("script_synthesis_engine")

MODULE_VERSION = "1.1"

# Allowed editing notes directly map to GEMINI_FFMPEG_SCHEMA supported operations
ALLOWED_EDITING_NOTES = {
    "trim",
    "jump_cut",
    "speed_change",
    "zoom_in",
    "zoom_out",
    "freeze_frame",
    "text_overlay",
    "delogo_blur",
    "xfade",
    "transition"
}

EDITING_NOTE_FALLBACK_MAP = {
    "fast_cut": "jump_cut",
    "quick_cut": "jump_cut",
    "speed_up": "speed_change",
    "slow_mo": "speed_change",
    "slowmo": "speed_change",
    "punch_in": "zoom_in",
    "zoom": "zoom_in",
    "cut": "trim",
    "drawtext": "text_overlay",
    "title": "text_overlay",
    "caption": "text_overlay",
    "blur": "delogo_blur",
    "crossfade": "xfade",
    "dissolve": "transition",
    "fade": "transition"
}

SCRIPT_SYNTHESIS_SYSTEM_PROMPT = """You are a world-class Short-Form Video Director and Screenwriter.
Your role is to synthesize a concrete, timestamped, scene-by-scene creative video script (creative_script.json)
for a short-form video (Reel / TikTok / Short).

You are provided with TWO separate inputs:
1. ### SOURCE MATERIAL (Ground Truth Footage):
   - Factual transcripts, Whisper word timestamps, captions, scene descriptions, and beat grids from actual video clips.
   - You MUST ONLY use the actual words spoken in the transcript or written in the caption. DO NOT invent dialogue or quotes!
2. ### STYLE CONSTRAINTS (Creator DNA):
   - High-level pacing, hook formula, visual editing tics, and emotional tone of the creator.
   - Use this ONLY as a directorial constraint (how to structure cuts, pace scenes, and overlay text). Never let style constraints fabricate footage facts!

OUTPUT REQUIREMENTS:
- Return ONLY a single valid JSON object adhering strictly to the schema provided.
- `audio_strategy.narration_mode` MUST be autonomously chosen based on footage audio and creator format:
  * "voiceover": Footage is B-roll or silent and needs spoken storytelling. You MUST write concise spoken sentences in `spoken_or_caption_text` for voice synthesis.
  * "preserve_original_voice": Footage contains clear on-camera speech. Original voice will be preserved with BGM ducked.
  * "music_and_text_only": High-energy visual montage, paparazzi reel, or music-driven clip. Zero spoken voice; use bold `on_screen_text_overlay`.
- Every segment MUST reference an actual source_clip from the provided source material.
- `source_time_range_s` MUST be timestamps [start_s, end_s] WITHIN THAT SPECIFIC SOURCE CLIP (not exceeding clip duration).
- `spoken_or_caption_text` MUST be an exact substring quote from the clip's transcript or caption (or dialogue for voiceover).
- `editing_note` MUST be one of: ["trim", "jump_cut", "speed_change", "zoom_in", "zoom_out", "freeze_frame", "text_overlay", "delogo_blur", "xfade", "transition"].
"""


class ScriptSynthesisEngine:
    """
    Synthesizes concrete creative scripts from raw pool_metadata footage assets
    constrained by verified CBM Creator DNA.
    """

    def __init__(self, pool_metadata_path: Optional[str] = None):
        if pool_metadata_path is None:
            repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            pool_metadata_path = os.path.join(repo_root, "Original_audio", "pool_metadata.json")
        self.meta_path = os.path.abspath(pool_metadata_path)

    # ─────────────────────────────────────────────────────────────────────────
    # 1. TEXT NORMALIZATION & CITATION VERIFICATION
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _normalize_text_for_matching(text: str) -> str:
        """
        Normalizes quotes, dashes, ellipses, trailing punctuation, and whitespace.
        """
        if not text:
            return ""
        t = str(text).lower().strip()
        t = re.sub(r"[’'‘`]", "'", t)
        t = re.sub(r'[”"“]', '"', t)
        t = re.sub(r"[—–−]", "-", t)
        t = re.sub(r"\.\.\.|…", " ", t)
        t = t.strip('"').strip("'")
        t = re.sub(r"\s+", " ", t).strip()
        return t

    def _verify_script_citations(
        self,
        script_dict: Dict[str, Any],
        clips_by_key: Dict[str, Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Validates every segment's spoken_or_caption_text against the actual clip
        transcript or caption corpus.
        Returns verification summary and mutates segment status in place.
        """
        segments = script_dict.get("segments", [])
        verified_count = 0
        total_text_segments = 0
        details = []

        for seg in segments:
            if not isinstance(seg, dict):
                continue

            text = str(seg.get("spoken_or_caption_text", "")).strip()
            source_clip = str(seg.get("source_clip", "")).strip()
            text_source = str(seg.get("text_source", "transcript")).strip().lower()

            # Direct lookup first, then fallback scan
            clip = clips_by_key.get(source_clip)
            if not clip:
                for k, v in clips_by_key.items():
                    if source_clip in k or k in source_clip or (v.get("shortcode") and v["shortcode"] == source_clip):
                        clip = v
                        break

            if not text or text.lower() in ("none", "n/a", ""):
                seg["citation_verified"] = True
                seg["citation_note"] = "no_spoken_text"
                continue

            total_text_segments += 1
            norm_quote = self._normalize_text_for_matching(text)

            # Build target corpus for this clip
            # GROUND TRUTH SAFETY NOTE:
            # _extract_content_payload explicitly zeroes out transcript_full for clips with
            # has_vocals=False. If a segment claims text_source="transcript" on a non-vocal clip,
            # norm_transcript will be empty and the substring match will cleanly fail!
            clip_transcript = ""
            clip_caption = ""
            has_vocals_flag = False
            if clip:
                clip_transcript = clip.get("transcript_full", "")
                clip_caption = clip.get("caption", "")
                has_vocals_flag = clip.get("has_vocals", False)

            norm_transcript = self._normalize_text_for_matching(clip_transcript)
            norm_caption = self._normalize_text_for_matching(clip_caption)

            is_verified = False
            if text_source == "transcript":
                if not has_vocals_flag:
                    # Explicit guard: non-vocal clip cannot have spoken transcript citations
                    is_verified = False
                    seg["citation_note"] = "non_vocal_clip_transcript_rejected"
                else:
                    is_verified = norm_quote in norm_transcript if norm_transcript else False
            elif text_source == "caption":
                is_verified = norm_quote in norm_caption if norm_caption else False
            else:
                # Fallback: check across either corpus for this clip
                is_verified = (norm_quote in norm_transcript) or (norm_quote in norm_caption)

            seg["citation_verified"] = is_verified
            if is_verified:
                verified_count += 1
                details.append({"segment_id": seg.get("segment_id"), "verified": True, "text": text})
            else:
                if not seg.get("citation_note"):
                    seg["citation_note"] = "unverified_quote_flagged"
                # Scrub hallucinated/unverified dialogue so it is not mistaken for ground truth
                seg["spoken_or_caption_text"] = ""
                seg["unverified_quote_scrubbed"] = text
                logger.warning(
                    f"⚠️ [SCRIPT CITATION UNVERIFIED] Segment '{seg.get('segment_id')}' text '{text}' "
                    f"not found in source clip '{source_clip}'! Scrubbed spoken_or_caption_text."
                )
                details.append({"segment_id": seg.get("segment_id"), "verified": False, "scrubbed_text": text})

        all_verified = (verified_count == total_text_segments) if total_text_segments > 0 else True
        ratio = (verified_count / total_text_segments) if total_text_segments > 0 else 1.0

        return {
            "all_verified": all_verified,
            "verification_ratio": round(ratio, 2),
            "verified_count": verified_count,
            "total_text_segments": total_text_segments,
            "details": details
        }

    # ─────────────────────────────────────────────────────────────────────────
    # 2. INPUT SEPARATION: CONTENT PAYLOAD vs STYLE GUIDE
    # ─────────────────────────────────────────────────────────────────────────

    def _extract_content_payload(self, clips_dict: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Extracts purely factual ground truth from raw pool clips.
        Applies heuristic ranking (vocals + transcript richness + duration)
        before selecting the top 6 clips to guarantee maximum signal density.
        """
        def _clip_signal_score(c: Dict[str, Any]) -> float:
            score = 0.0
            audio_m = c.get("audio_math", {})
            if isinstance(audio_m, dict) and audio_m.get("has_vocals") is True:
                score += 50.0  # Dialogue/speech has highest creative utility
            whisper = c.get("whisper_transcript", {})
            txt = whisper.get("text", "") if isinstance(whisper, dict) else ""
            cap = c.get("caption", "") or ""
            score += min(30.0, (len(txt) + len(cap)) / 10.0)
            dur = c.get("duration") or c.get("duration_sec") or 0.0
            if dur >= 3.0:
                score += 20.0
            return score

        # Rank clips by creative signal quality
        ranked_items = sorted(
            clips_dict.items(),
            key=lambda kv: _clip_signal_score(kv[1]) if isinstance(kv[1], dict) else 0.0,
            reverse=True
        )
        selected_clips = dict(ranked_items[:6])

        payload = []
        for key, clip in selected_clips.items():
            if not isinstance(clip, dict):
                continue

            shortcode = clip.get("shortcode") or key.split("/")[-1]
            caption = clip.get("caption") or ""
            whisper = clip.get("whisper_transcript", {})

            # Extract full transcript (capped to 400 chars to avoid prompt bloat)
            full_text = ""
            words_slice = []
            if isinstance(whisper, dict):
                full_text = whisper.get("text") or " ".join(
                    [s.get("text", "") for s in whisper.get("sentences", []) if isinstance(s, dict)]
                )
                raw_words = whisper.get("words", [])
                if isinstance(raw_words, list):
                    words_slice = [
                        {"word": w.get("word"), "start": w.get("start"), "end": w.get("end")}
                        for w in raw_words[:25] if isinstance(w, dict) and "word" in w
                    ]

            # Audio math
            audio_math = clip.get("audio_math", {})
            has_vocals = audio_math.get("has_vocals", False) if isinstance(audio_math, dict) else False
            tempo_bpm = audio_math.get("tempo_bpm", 0.0) if isinstance(audio_math, dict) else 0.0

            # Visual & Speech intelligence
            vis_intel = clip.get("gemini_semantic_visual_intelligence", {})
            speech_intel = clip.get("speech_intelligence", {}) or vis_intel.get("speech_intelligence", {})
            scenes = vis_intel.get("scenes", []) if isinstance(vis_intel, dict) else []
            clip_intent = vis_intel.get("intent", "")
            rec_act = speech_intel.get("recommended_audio_action", "")
            s_mode = speech_intel.get("speech_mode", "")

            payload.append({
                "clip_id": key,
                "shortcode": shortcode,
                "duration_sec": clip.get("duration") or clip.get("duration_sec") or 10.0,
                "caption": caption[:250],
                "transcript_full": full_text[:400] if has_vocals else "",
                "transcript_word_samples": words_slice if has_vocals else [],
                "has_vocals": has_vocals,
                "tempo_bpm": tempo_bpm,
                "intent": clip_intent,
                "recommended_audio_action": rec_act,
                "speech_mode": s_mode,
                "scenes_summary": [s.get("description", "") for s in scenes[:4] if isinstance(s, dict)]
            })

        return payload

    def _filter_cbm_style_payload(self, cbm_profile: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Extracts only verified style constraints from CBM output.
        Fails safe to 'low' confidence on any missing or malformed signal.
        """
        if not isinstance(cbm_profile, dict):
            return {"status": "no_cbm_profile", "style_applied": False, "confidence": "low"}

        # Fail-safe confidence resolution: default strictly to "low"
        confidence = str(cbm_profile.get("evidence", {}).get("confidence", "")).lower()
        if not confidence:
            confidence = str(cbm_profile.get("confidence", "low")).lower()
        if confidence not in ("high", "medium", "low"):
            confidence = "low"

        # Low-confidence gate: If CBM confidence is low, do not enforce rigid qualitative styles
        if confidence == "low":
            logger.info("ℹ️ [SCRIPT ENGINE] CBM profile confidence is 'low' — operating with neutral style constraints.")
            return {
                "confidence": "low",
                "style_applied": False,
                "note": "Low confidence CBM profile — using factual footage defaults."
            }

        behavior = cbm_profile.get("behavior", {})
        if not isinstance(behavior, dict):
            behavior = cbm_profile

        narrative = behavior.get("narrative_and_storytelling_dna", {})
        vis_style = behavior.get("presentation_and_visual_style_dna", {})
        personality = behavior.get("personality_and_voice_dna", {})

        style_guide: Dict[str, Any] = {
            "confidence": confidence,
            "style_applied": True,
            "creator_format": cbm_profile.get("creator_format", "curator_aggregator"),
            "hook_formula": narrative.get("hook_formula") or "attention_grabbing_visual_or_quote",
            "tension_building_pattern": narrative.get("tension_building_pattern"),
            "signature_editing_tics": vis_style.get("signature_editing_tics", []),
            "emotional_register": personality.get("emotional_register", "enthusiastic")
        }

        # Include verbal signatures strictly if has_spoken_voice is verified true
        has_spoken_voice = personality.get("has_spoken_voice", False)
        if has_spoken_voice:
            verb_sigs = personality.get("verbal_signatures", {})
            if isinstance(verb_sigs, dict):
                style_guide["verified_verbal_signatures"] = {
                    "sentence_openers": verb_sigs.get("sentence_openers", [])[:3],
                    "catchphrases": verb_sigs.get("catchphrases", [])[:2]
                }

        return style_guide

    # ─────────────────────────────────────────────────────────────────────────
    # 3. OPERATION ENUM SANITIZATION & TWO-TIER VALIDATION
    # ─────────────────────────────────────────────────────────────────────────

    @classmethod
    def _sanitize_editing_note(cls, note: Any) -> str:
        """
        Ensures editing_note strictly conforms to ALLOWED_EDITING_NOTES.
        """
        if not note:
            return "trim"
        n = str(note).lower().strip().replace(" ", "_").replace("-", "_")
        if n in ALLOWED_EDITING_NOTES:
            return n
        if n in EDITING_NOTE_FALLBACK_MAP:
            return EDITING_NOTE_FALLBACK_MAP[n]
        for prefix, mapped in sorted(EDITING_NOTE_FALLBACK_MAP.items(), key=lambda x: len(x[0]), reverse=True):
            if prefix in n:
                return mapped
        return "trim"

    @staticmethod
    def _compute_timeline_ranges(segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Shared deterministic post-processor that calculates sequential output
        timeline positions (timeline_time_range_s) across surviving segments.
        Applied identically to both live Gemini parsed scripts and fallback scripts.
        """
        cur_time = 0.0
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            src_range = seg.get("source_time_range_s", [0.0, 3.0])
            dur = max(0.5, float(src_range[1]) - float(src_range[0])) if len(src_range) == 2 else 3.0
            seg["timeline_time_range_s"] = [round(cur_time, 2), round(cur_time + dur, 2)]
            cur_time += dur
        return segments

    def _validate_and_clean_segments(
        self,
        segments: List[Dict[str, Any]],
        clips_by_key: Dict[str, Dict[str, Any]]
    ) -> Tuple[List[Dict[str, Any]], List[str]]:
        """
        Tier 1 segment-level validation & sanitization:
        - Drops individual segments missing required fields or with unresolvable source_clip.
        - Drops segments with severe (>50%) out-of-bounds timestamps.
        - Clamps minor overshoots (<= 50%) to clip duration.
        Returns: (surviving_valid_segments, drop_reasons)
        """
        valid_segments = []
        drop_reasons = []

        for idx, seg in enumerate(segments):
            seg_id = seg.get("segment_id", f"seg_{idx + 1}")
            if not isinstance(seg, dict):
                drop_reasons.append(f"Segment {seg_id}: Not a dictionary.")
                continue

            # Check required fields
            source_clip = seg.get("source_clip")
            role = seg.get("role")
            src_range = seg.get("source_time_range_s")

            if not source_clip or not role or not isinstance(src_range, (list, tuple)) or len(src_range) != 2:
                drop_reasons.append(f"Segment {seg_id}: Missing required fields ('source_clip', 'role', or 'source_time_range_s').")
                continue

            # Resolve clip
            clip = clips_by_key.get(str(source_clip))
            if not clip:
                for k, v in clips_by_key.items():
                    if str(source_clip) in k or k in str(source_clip) or (v.get("shortcode") and v["shortcode"] == str(source_clip)):
                        clip = v
                        seg["source_clip"] = k
                        break

            if not clip:
                drop_reasons.append(f"Segment {seg_id}: Unresolvable source_clip '{source_clip}'.")
                continue

            # Validate timestamps
            try:
                start_s = float(src_range[0])
                end_s = float(src_range[1])
            except (ValueError, TypeError):
                drop_reasons.append(f"Segment {seg_id}: Non-numeric timestamps in {src_range}.")
                continue

            if start_s < 0.0 or end_s <= start_s:
                drop_reasons.append(f"Segment {seg_id}: Invalid range [{start_s}, {end_s}] (end <= start or negative).")
                continue

            clip_dur = float(clip.get("duration_sec") or clip.get("duration") or 15.0)
            seg_dur = end_s - start_s

            if start_s >= clip_dur:
                drop_reasons.append(f"Segment {seg_id}: Start time {start_s:.1f}s >= clip duration {clip_dur:.1f}s (past EOF).")
                continue

            if end_s > clip_dur:
                overshoot = end_s - clip_dur
                overshoot_ratio = overshoot / seg_dur if seg_dur > 0 else 1.0
                if overshoot_ratio > 0.50:
                    # >50% out-of-bounds hallucination -> Drop segment
                    drop_reasons.append(
                        f"Segment {seg_id}: Severe timestamp overshoot ({overshoot:.1f}s out of {seg_dur:.1f}s, {overshoot_ratio*100:.0f}% > 50% limit) against clip duration {clip_dur:.1f}s."
                    )
                    continue
                else:
                    # Minor overshoot <= 50% -> Clamp to clip_dur
                    logger.info(f"ℹ️ [TIMESTAMP CLAMP] Segment {seg_id} end clamped {end_s:.1f}s -> {clip_dur:.1f}s.")
                    seg["source_time_range_s"] = [round(start_s, 2), round(clip_dur, 2)]
                    seg["clamped"] = True
            else:
                seg["source_time_range_s"] = [round(start_s, 2), round(end_s, 2)]

            valid_segments.append(seg)

        return valid_segments, drop_reasons

    # ─────────────────────────────────────────────────────────────────────────
    # 4. SCRIPT QUALITY SCORING
    # ─────────────────────────────────────────────────────────────────────────

    def score_script(
        self,
        script_dict: Dict[str, Any],
        style_guide: Dict[str, Any],
        target_duration_s: float,
        clips_by_key: Optional[Dict[str, Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """
        Scores the synthesized script (0.0 - 1.0) against target duration,
        pacing, hook compliance, per-segment citation grounding, and editing notes.
        """
        segments = script_dict.get("segments", [])
        passed: List[str] = []
        failed: List[str] = []
        total_points = 0.0
        max_points = 100.0

        if not segments:
            return {
                "score": 0.20,
                "passed_criteria": [],
                "failed_criteria": ["Script has no valid segments."],
                "breakdown": {}
            }

        clips_by_key = clips_by_key or {}

        # 1. Total Duration Alignment (30 pts)
        est_duration = sum(
            (s.get("source_time_range_s", [0, 0])[1] - s.get("source_time_range_s", [0, 0])[0])
            for s in segments if isinstance(s.get("source_time_range_s"), (list, tuple)) and len(s.get("source_time_range_s")) == 2
        )
        dur_diff = abs(est_duration - target_duration_s)
        if dur_diff <= 3.0:
            total_points += 30.0
            passed.append(f"Script total duration ({est_duration:.1f}s) is within 3.0s of target ({target_duration_s:.1f}s).")
        elif dur_diff <= 6.0:
            total_points += 18.0
            passed.append(f"Script duration ({est_duration:.1f}s) moderately aligned to target ({target_duration_s:.1f}s).")
        else:
            total_points += 8.0
            failed.append(f"Script duration ({est_duration:.1f}s) deviates significantly from target ({target_duration_s:.1f}s).")

        # 2. Hook Segment Presence & Timing (25 pts)
        first_seg = segments[0]
        first_role = str(first_seg.get("role", "")).lower()
        first_dur = 0.0
        if isinstance(first_seg.get("source_time_range_s"), (list, tuple)) and len(first_seg.get("source_time_range_s")) == 2:
            first_dur = first_seg["source_time_range_s"][1] - first_seg["source_time_range_s"][0]

        if first_role == "hook" and 1.0 <= first_dur <= 5.0:
            total_points += 25.0
            passed.append(f"Opening segment is an explicit hook with rapid timing ({first_dur:.1f}s).")
        elif first_role == "hook":
            total_points += 15.0
            passed.append("Opening segment has hook role but duration exceeds standard hook window.")
        else:
            total_points += 5.0
            failed.append("Missing explicit hook role in opening segment.")

        # 3. Per-Segment Citation Grounding & Content Substance (25 pts)
        cit_summary = script_dict.get("citation_verification", {})
        cit_ratio = cit_summary.get("verification_ratio", 1.0)

        # Check modal fulfillment per segment:
        vocal_segments_expected = 0
        vocal_segments_fulfilled = 0
        non_vocal_overlays_fulfilled = 0
        non_vocal_segments = 0

        for seg in segments:
            s_clip_id = str(seg.get("source_clip", ""))
            clip_ref = clips_by_key.get(s_clip_id) or {}
            has_voc = clip_ref.get("has_vocals", False)

            if has_voc:
                vocal_segments_expected += 1
                if seg.get("spoken_or_caption_text") and seg.get("citation_verified", False):
                    vocal_segments_fulfilled += 1
            else:
                non_vocal_segments += 1
                if seg.get("on_screen_text_overlay"):
                    non_vocal_overlays_fulfilled += 1

        if vocal_segments_expected > 0:
            vocal_ratio = vocal_segments_fulfilled / vocal_segments_expected
            overlay_bonus = (non_vocal_overlays_fulfilled / non_vocal_segments) if non_vocal_segments > 0 else 1.0
            combined_content_ratio = (vocal_ratio * 0.7) + (overlay_bonus * 0.3)
            total_points += (cit_ratio * 15.0) + (combined_content_ratio * 10.0)

            if vocal_ratio >= 0.7:
                passed.append(f"High dialogue grounding: {vocal_segments_fulfilled}/{vocal_segments_expected} vocal segments fulfilled.")
            else:
                failed.append(f"Dialogue deficit: only {vocal_segments_fulfilled}/{vocal_segments_expected} vocal segments had grounded speech.")
        else:
            # Completely non-vocal footage: score by visual overlay coverage
            overlay_ratio = (non_vocal_overlays_fulfilled / non_vocal_segments) if non_vocal_segments > 0 else 0.5
            total_points += (cit_ratio * 10.0) + (overlay_ratio * 15.0)
            if overlay_ratio >= 0.5:
                passed.append("Non-vocal visual reel supported by on-screen text overlays.")
            else:
                failed.append("Non-vocal footage missing on-screen text overlays for viewer retention.")

        # 4. Editing Note Executability (20 pts)
        valid_notes = sum(1 for s in segments if s.get("editing_note") in ALLOWED_EDITING_NOTES)
        note_ratio = valid_notes / len(segments) if segments else 1.0
        total_points += note_ratio * 20.0
        if note_ratio >= 0.90:
            passed.append("All editing notes map directly to valid FFmpeg synthesis operations.")
        else:
            failed.append("Some editing notes contain unrecognized operations.")

        # Penalize dropped segments if any occurred
        dropped_count = script_dict.get("dropped_segments_count", 0)
        if dropped_count > 0:
            total_points = max(10.0, total_points - (dropped_count * 5.0))
            failed.append(f"Penalized {dropped_count * 5} pts for {dropped_count} dropped invalid segment(s).")

        final_score = round(max(0.20, min(0.99, total_points / max_points)), 2)
        return {
            "score": final_score,
            "estimated_duration_s": round(est_duration, 2),
            "passed_criteria": passed,
            "failed_criteria": failed
        }

    # ─────────────────────────────────────────────────────────────────────────
    # 5. BEHAVIORAL FALLBACK SCRIPT GENERATOR
    # ─────────────────────────────────────────────────────────────────────────

    def _behavioral_script_fallback(
        self,
        creator_handle: str,
        content_payload: List[Dict[str, Any]],
        style_guide: Dict[str, Any],
        target_duration_s: float
    ) -> Dict[str, Any]:
        """
        Deterministic, zero-hallucination fallback script using the available clips'
        actual transcripts and scene timings.
        Guarantees per-clip timestamp tracking bounded within clip duration.
        """
        logger.info("🛡️ [SCRIPT ENGINE] Generating deterministic behavioral fallback script.")
        segments = []
        segment_count = max(2, min(5, int(target_duration_s // 3.5)))
        seg_dur = round(target_duration_s / segment_count, 1)

        available_clips = [c for c in content_payload if c.get("duration_sec", 0) >= seg_dur]
        if not available_clips:
            available_clips = content_payload or [{"clip_id": "source_clip_0", "caption": "", "transcript_full": "", "duration_sec": 15.0}]

        roles = ["hook", "body", "climax", "cta"]
        clip_offsets: Dict[str, float] = {}

        for i in range(segment_count):
            clip = available_clips[i % len(available_clips)]
            role = roles[min(i, len(roles) - 1)]
            clip_id = clip.get("clip_id", f"clip_{i}")
            clip_dur = float(clip.get("duration_sec") or clip.get("duration") or 15.0)

            # Per-clip timestamp tracking (bounded within clip_dur)
            cur_offset = clip_offsets.get(clip_id, 0.0)
            if cur_offset + seg_dur > clip_dur:
                cur_offset = 0.0
            actual_seg_dur = min(seg_dur, clip_dur - cur_offset)
            if actual_seg_dur < 1.0:
                cur_offset = 0.0
                actual_seg_dur = min(seg_dur, clip_dur)

            clip_offsets[clip_id] = round(cur_offset + actual_seg_dur, 2)

            # Grab ground truth quote from verified speech transcript only
            transcript = clip.get("transcript_full", "")
            spoken_text = ""
            text_source = "none"

            if transcript and len(transcript) > 5 and "_" not in transcript:
                sents = re.split(r"[.!?]", transcript)
                candidate = sents[0].strip() if sents else transcript[:50]
                if "_" not in candidate and "appearance" not in candidate.lower() and "intent" not in candidate.lower():
                    spoken_text = candidate
                    text_source = "transcript"

            # Editing note
            if i == 0:
                op = "jump_cut"
            elif i == segment_count - 1:
                op = "xfade"
            else:
                op = "speed_change" if i % 2 == 1 else "zoom_in"

            clean_overlay = spoken_text[:35] if (spoken_text and "_" not in spoken_text) else ""

            segments.append({
                "segment_id": f"seg_{i + 1}",
                "role": role,
                "source_clip": clip_id,
                "source_time_range_s": [round(cur_offset, 2), round(cur_offset + actual_seg_dur, 2)],
                "spoken_or_caption_text": spoken_text,
                "text_source": text_source,
                "on_screen_text_overlay": clean_overlay,
                "editing_note": op
            })

        # Determine fallback narration mode from footage signals
        has_any_vocals = any(c.get("has_vocals", False) for c in content_payload)
        is_visual_or_music = any(
            c.get("recommended_audio_action") == "audio_replace_full_bgm" or
            c.get("speech_mode") in ("silent_broll", "music_broll", "lip_sync_dub") or
            c.get("intent") in ("bollywood_dance_performance", "dance", "fashion", "visual_broll")
            for c in content_payload
        )
        creator_fmt = str(style_guide.get("creator_format", "")).lower()

        if is_visual_or_music:
            fallback_narration_mode = "music_and_text_only"
            rationale = "Non-dialogue visual/dance footage — using full BGM and on-screen text overlays."
        elif has_any_vocals:
            fallback_narration_mode = "preserve_original_voice"
            rationale = "Footage contains vocal speech — preserving original voice."
        elif creator_fmt == "voiceover_narrator":
            fallback_narration_mode = "voiceover"
            rationale = "Creator format is voiceover_narrator — applying spoken narration."
        else:
            fallback_narration_mode = "music_and_text_only"
            rationale = "Non-vocal visual footage — using BGM and on-screen text overlays."

        return {
            "script_id": f"script_fallback_{uuid.uuid4().hex[:8]}",
            "schema_version": MODULE_VERSION,
            "creator_handle": creator_handle,
            "source_clips_used": list(set(s["source_clip"] for s in segments)),
            "total_target_duration_s": target_duration_s,
            "audio_strategy": {
                "narration_mode": fallback_narration_mode,
                "rationale": rationale
            },
            "segments": segments,
            "style_influences_applied": [
                f"hook_formula:{style_guide.get('hook_formula', 'default_visual_hook')}",
                "fallback:deterministic_timeline_sync"
            ],
            "confidence": "medium",
            "is_fallback": True
        }

    # ─────────────────────────────────────────────────────────────────────────
    # 6. GEMINI ROUTER CALL
    # ─────────────────────────────────────────────────────────────────────────

    def _call_gemini_api(self, prompt_text: str) -> Optional[str]:
        """Invokes Gemini analysis router using gemini_governor."""
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            return None

        try:
            from Gemini_Modules.gemini_router_module.gemini_governor import gemini_router
            if gemini_router:
                res = gemini_router.generate(
                    task_type="analysis",
                    prompt=[prompt_text],
                    module_name="script_synthesis_engine"
                )
                if res and len(str(res).strip()) > 10:
                    return str(res).strip()
        except Exception as err:
            logger.error(f"❌ [SCRIPT ENGINE] Gemini router call error: {err}")

        return None

    # ─────────────────────────────────────────────────────────────────────────
    # 7. MAIN ENGINE ENTRY POINT
    # ─────────────────────────────────────────────────────────────────────────

    def generate_script(
        self,
        creator_handle: str,
        target_duration_s: float = 15.0,
        user_goal: Optional[str] = None,
        pool_metadata_path: Optional[str] = None,
        clips_override: Optional[Dict[str, Any]] = None,
        cbm_profile: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Executes full Script Synthesis Pipeline:
          1. Loads pool_metadata and extracts creator clips.
          2. Extracts factual Content Payload (ranked by signal density).
          3. Filters CBM Style Constraints (failing safe to low confidence).
          4. Prompts Gemini with separated Content vs Style payloads.
          5. Tier 1: Validates and cleans segments individually.
          6. Tier 2: Validates aggregate viability (triggers fallback if <2 survive).
          7. Deterministically computes timeline_time_range_s sequentially.
          8. Verifies citations against source footage.
          9. Sanitizes editing notes and scores script quality.
        """
        start_time = time.time()
        logger.info(f"🎬 [SCRIPT ENGINE] Synthesizing creative script for creator='{creator_handle}', target={target_duration_s:.1f}s")

        # 1. Load clips from pool_metadata if not overridden
        clips_dict = clips_override or {}
        if not clips_dict:
            p_path = pool_metadata_path or self.meta_path
            if os.path.exists(p_path):
                try:
                    with open(p_path, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                    from Gemini_Modules.universal_creator_cbm_engine import UniversalCreatorCBMEngine
                    cbm_eng = UniversalCreatorCBMEngine()
                    clips_dict = cbm_eng._extract_clips_for_creator(meta, creator_handle.lower().strip("@"))
                    if not clips_dict and isinstance(meta.get("files"), dict):
                        norm_h = creator_handle.lower().strip("@")
                        for k, v in meta["files"].items():
                            if isinstance(v, dict):
                                if norm_h in k.lower() or norm_h in str(v.get("filename", "")).lower() or norm_h in str(v.get("ownerUsername", "")).lower():
                                    clips_dict[k] = v
                except Exception as pe:
                    logger.warning(f"⚠️ [SCRIPT ENGINE] Pool metadata clip extraction notice: {pe}")

        # If CBM profile not provided, attempt to load cached CBM from pool clips
        if not cbm_profile and clips_dict:
            for c in clips_dict.values():
                if isinstance(c, dict) and c.get("creator_behavior_model"):
                    cbm_profile = c["creator_behavior_model"]
                    break

        # If still missing from clips, dynamically extract and persist via UniversalCreatorCBMEngine
        GENERIC_CREATOR_HANDLES = {"manual", "unknown", "default", "default_creator", "temp", "none", "input", "uploads", "downloads", "direct", "video", "clip"}
        if not cbm_profile and clips_dict and creator_handle.lower().strip("@") not in GENERIC_CREATOR_HANDLES:
            try:
                from Gemini_Modules.universal_creator_cbm_engine import extract_creator_cbm
                logger.info(f"🧬 [SCRIPT ENGINE] No cached CBM found for '{creator_handle}'. Extracting on-demand...")
                extracted = extract_creator_cbm(creator_handle)
                if isinstance(extracted, dict) and extracted.get("status") != "error":
                    cbm_profile = extracted
                    logger.info(f"🧬 [SCRIPT ENGINE] Successfully extracted & persisted on-demand CBM for '{creator_handle}'.")
            except Exception as ce:
                logger.warning(f"⚠️ [SCRIPT ENGINE] Dynamic CBM extraction notice: {ce}")

        # 2. Extract separated Content Payload vs Style Payload
        content_payload = self._extract_content_payload(clips_dict)
        style_guide = self._filter_cbm_style_payload(cbm_profile)
        clips_by_key = {c["clip_id"]: c for c in content_payload}

        # 3. Build Prompt with strict section separation
        prompt_sections = [
            f"### User Editing Goal\n{user_goal or 'Create a viral, high-retention reel.'}\nTarget Duration: {target_duration_s:.1f}s\n",
            f"### SOURCE MATERIAL (Factual Ground Truth Footage — DO NOT FABRICATE)\n{json.dumps(content_payload, indent=2)}\n",
            f"### STYLE CONSTRAINTS (Creator DNA — Directs Pacing & Tone Only)\n{json.dumps(style_guide, indent=2)}\n",
            "### JSON RESPONSE SCHEMA:\n"
            "{\n"
            '  "script_id": "script_<id>",\n'
            f'  "creator_handle": "{creator_handle}",\n'
            '  "source_clips_used": ["<clip_id>", ...],\n'
            f'  "total_target_duration_s": {target_duration_s},\n'
            '  "audio_strategy": {\n'
            '    "narration_mode": "voiceover" | "preserve_original_voice" | "music_and_text_only",\n'
            '    "rationale": "<why this narration mode is chosen based on footage and creator DNA>"\n'
            '  },\n'
            '  "segments": [\n'
            '    {\n'
            '      "segment_id": "seg_1",\n'
            '      "role": "hook" | "body" | "climax" | "cta",\n'
            '      "source_clip": "<clip_id>",\n'
            '      "source_time_range_s": [<start_s>, <end_s>],\n'
            '      "spoken_or_caption_text": "<exact quote from transcript_full or caption>",\n'
            '      "text_source": "transcript" | "caption" | "none",\n'
            '      "on_screen_text_overlay": "<short text for drawtext>",\n'
            '      "editing_note": "trim" | "jump_cut" | "speed_change" | "zoom_in" | "zoom_out" | "freeze_frame" | "text_overlay" | "delogo_blur" | "xfade" | "transition"\n'
            '    }\n'
            '  ],\n'
            '  "style_influences_applied": ["hook_formula:...", "signature_editing_tics:..."],\n'
            '  "confidence": "high" | "medium" | "low"\n'
            "}"
        ]
        full_prompt = f"{SCRIPT_SYNTHESIS_SYSTEM_PROMPT}\n\n" + "\n".join(prompt_sections)

        # 4. Call Gemini Router
        raw_res = self._call_gemini_api(full_prompt)
        script_dict: Optional[Dict[str, Any]] = None

        if raw_res:
            try:
                clean_str = re.sub(r"^[\s\n]*```(?:json)?", "", raw_res, flags=re.IGNORECASE | re.MULTILINE)
                clean_str = re.sub(r"```[\s\n]*$", "", clean_str, flags=re.MULTILINE).strip()
                script_dict = json.loads(clean_str)
            except Exception as je:
                logger.warning(f"⚠️ [SCRIPT ENGINE] Failed to parse JSON response: {je}")

        # 5. Two-Tier Validation: Clean individual segments, fallback if surviving is insufficient
        is_fallback = False
        if script_dict and isinstance(script_dict.get("segments"), list) and len(script_dict["segments"]) > 0:
            valid_segs, drop_reasons = self._validate_and_clean_segments(script_dict["segments"], clips_by_key)
            for dr in drop_reasons:
                logger.warning(f"⚠️ [SCRIPT ENGINE SEGMENT DROP] {dr}")

            total_valid_dur = sum(
                (s["source_time_range_s"][1] - s["source_time_range_s"][0])
                for s in valid_segs
            )

            # Tier 2 aggregate check: require at least 2 valid segments and >= 5.0s
            if len(valid_segs) >= 2 and total_valid_dur >= 5.0:
                script_dict["segments"] = valid_segs
                script_dict["dropped_segments_count"] = len(drop_reasons)
                script_dict["dropped_segments_reasons"] = drop_reasons
            else:
                logger.warning(
                    f"⚠️ [SCRIPT ENGINE] Only {len(valid_segs)} valid segment(s) ({total_valid_dur:.1f}s) survived Tier 1 validation. "
                    f"Triggering deterministic behavioral fallback."
                )
                script_dict = self._behavioral_script_fallback(creator_handle, content_payload, style_guide, target_duration_s)
                is_fallback = True
        else:
            script_dict = self._behavioral_script_fallback(creator_handle, content_payload, style_guide, target_duration_s)
            is_fallback = True

        script_dict["schema_version"] = MODULE_VERSION

        # 6. Shared Deterministic Timeline Range Calculation
        script_dict["segments"] = self._compute_timeline_ranges(script_dict.get("segments", []))

        # 7. Sanitize editing notes
        for seg in script_dict.get("segments", []):
            if isinstance(seg, dict):
                seg["editing_note"] = self._sanitize_editing_note(seg.get("editing_note"))

        # 8. Verify Citations against footage corpus
        cit_res = self._verify_script_citations(script_dict, clips_by_key)
        script_dict["citation_verification"] = cit_res

        # 9. Score Script Quality
        score_res = self.score_script(script_dict, style_guide, target_duration_s, clips_by_key=clips_by_key)
        script_dict["quality_score"] = score_res

        elapsed = time.time() - start_time
        script_dict["execution_time_seconds"] = round(elapsed, 3)

        logger.info(
            f"✅ [SCRIPT ENGINE] Synthesized script '{script_dict.get('script_id')}' (v{MODULE_VERSION}) with "
            f"{len(script_dict.get('segments', []))} segments in {elapsed:.2f}s "
            f"(Score={score_res.get('score')}, Citations={cit_res.get('verification_ratio') * 100:.0f}% verified, fallback={is_fallback})"
        )

        return script_dict


# ─────────────────────────────────────────────────────────────────────────────
# MODULE HELPER API
# ─────────────────────────────────────────────────────────────────────────────

def synthesize_script_voiceover(
    creative_script: Dict[str, Any],
    output_path: str,
    voice: Optional[str] = None
) -> Optional[str]:
    """
    Synthesizes a single voiceover audio file from all spoken dialogue segments
    in creative_script using TTSEngine (Edge-TTS).
    """
    if not isinstance(creative_script, dict):
        return None

    segments = creative_script.get("segments", [])
    spoken_parts = []
    for seg in segments:
        if isinstance(seg, dict):
            text = str(seg.get("spoken_or_caption_text", "")).strip()
            if text:
                spoken_parts.append(text)

    full_text = " ... ".join(spoken_parts).strip()
    if not full_text:
        logger.info("ℹ️ [VOICEOVER SYNTHESIS] No spoken dialogue in script — voiceover generation skipped.")
        return None

    try:
        from Audio_Modules.narrator import TTSEngine
        tts = TTSEngine()
        target_voice = voice or "en-US-ChristopherNeural"
        if re.search(r'[\u0D00-\u0D7F]', full_text):
            target_voice = "ml-IN-MidhunNeural"

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        filename = os.path.basename(output_path)
        gen_path = tts.generate_voiceover(full_text, target_voice, filename)
        if gen_path and os.path.exists(gen_path) and os.path.getsize(gen_path) > 1000:
            if os.path.abspath(gen_path) != os.path.abspath(output_path):
                shutil.copy2(gen_path, output_path)
            logger.info(f"🎙️ [VOICEOVER SYNTHESIZED] Generated voiceover ({len(full_text)} chars) -> {output_path}")
            return output_path
        return gen_path if (gen_path and os.path.exists(gen_path)) else None
    except Exception as err:
        logger.warning(f"⚠️ [VOICEOVER SYNTHESIS] Edge-TTS voiceover synthesis notice: {err}")
        return None


def generate_creative_script(
    creator_handle: str,
    target_duration_s: float = 15.0,
    user_goal: Optional[str] = None,
    pool_metadata_path: Optional[str] = None
) -> Dict[str, Any]:
    """Module-level function to synthesize a creative script."""
    engine = ScriptSynthesisEngine(pool_metadata_path=pool_metadata_path)
    return engine.generate_script(
        creator_handle=creator_handle,
        target_duration_s=target_duration_s,
        user_goal=user_goal
    )


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="ScriptSynthesisEngine CLI")
    parser.add_argument("--creator", type=str, default="filmygyan", help="Creator username/handle")
    parser.add_argument("--duration", type=float, default=15.0, help="Target video duration in seconds")
    parser.add_argument("--goal", type=str, default=None, help="User editing goal/intent")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | [%(name)s] %(message)s")
    res = generate_creative_script(args.creator, target_duration_s=args.duration, user_goal=args.goal)
    print(json.dumps(res, indent=2))
