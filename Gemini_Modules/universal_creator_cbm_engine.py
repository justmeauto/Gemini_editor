"""
Gemini_Modules/universal_creator_cbm_engine.py
================================================
Universal Creator Behavior Model (CBM) Engine V4.6.

Extracts deep Human Creator Behavior & Personality DNA by absorbing multi-clip
data from pool_metadata.json (Transcripts, Visual Intelligence, Captions,
Audio Math, and Social Metadata), along with optional 480p compressed video frames.

Key Enhancements & Ground Truth Safeguards (V4.6):
  1. Non-Empty Citation Validation: Schema validator mandates non-empty `evidence_citation`
     strings; empty or blank citation strings trigger schema validation rejection.
  2. Pure Vocal Transcript Corpus: Transcript corpus strictly filters out silent/BGM clips
     (has_vocals == False), preventing Whisper hallucination text from polluting grounding checks.
  3. Clean Fallback Citation Handling: Fallback results bypass citation verification and cleanly
     report `citation_grounding_verified: null` (N/A) rather than misleading unverified statuses.
  4. Comprehensive Schema & Type Validation: Validates top-level keys, format enums, boolean types
     (`has_spoken_voice`, `fourth_wall_breaks`), and non-empty `evidence_citation` string fields.
  5. Vocal Ratio Thresholding & Symmetric Realignment: Ground truth vocal ratio thresholding (>= 0.33)
     drives format realignment and verbal signature gating.
"""

import os
import sys
import re
import json
import time
import subprocess
import tempfile
import shutil
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger("universal_creator_cbm_engine")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ─────────────────────────────────────────────────────────────────────────────
# DEEP CREATOR BEHAVIOR & PERSONALITY DNA PROMPT V4.6
# ─────────────────────────────────────────────────────────────────────────────

CREATOR_BEHAVIOR_PROMPT = """\
You are a Lead Behavioral Intelligence AI performing a deep forensic autopsy on the content creator: '{creator_name}'.

You have been provided with multi-clip pool history (transcripts, visual analysis, captions, audio signals):
```json
{pool_metadata_context}
```

────────────────────────────────────────────────────────────────────────────────
YOUR MISSION: Perform an open, deep reasoning analysis of this creator's human behavior,
verbal style, humor/flattery mechanisms, presentation, and storytelling DNA.

CRITICAL GROUNDING & FORMAT RULES:
1. UPSTREAM FORMAT CLASSIFICATION (Mandatory Top-Level Field):
   Categorize creator_format as exactly one of:
   - talking_head: Creator speaks directly on camera.
   - voiceover_narrator: Creator narrates over visual footage/b-roll.
   - curator_aggregator: Re-posted clips, paparazzi, celebrity b-roll, synced music, minimal/no host speech.
   - text_music_only: Visuals with text overlays and BGM, no vocal speech.
   
2. VERBAL SIGNATURE GROUNDING:
   - If creator_format is curator_aggregator or text_music_only, OR if has_vocals is false, DO NOT invent spoken verbal signatures. Set spoken verbal_signatures to empty lists.
   - Verbal signatures MUST be extracted strictly from `transcript_full` / `whisper_transcript` text. NEVER label caption text (e.g. "snapped at") as spoken verbal tics!

3. EVIDENCE CITATION REQUIREMENT:
   - You MUST include a non-empty `evidence_citation` string in `humor_praise_and_wit_profile` and `content_philosophy_and_risk`.
   - Use EXACT quotes from transcript or caption. Ground truth verification will substring-check your citations against the raw corpus text!

4. OPEN PARALLEL POSSIBILITIES:
   - Understand the full spectrum of possibilities (roast, sarcasm, fan flattery, subject glorification, hype building).
   - Describe what makes '{creator_name}' uniquely recognizable.
────────────────────────────────────────────────────────────────────────────────

Return ONLY a single JSON object matching this EXACT schema (no markdown, no prose wrapper):

{{
  "creator_id": "{creator_name}",
  "schema_version": "4.6",
  "creator_format": "talking_head|voiceover_narrator|curator_aggregator|text_music_only",

  "behavior": {{
    "creator_archetype": "<3-5 word behavioral archetype>",
    "content_domain": "<primary domain e.g. entertainment_gossip | technology | fitness | gaming | fashion>",

    "personality_and_voice_dna": {{
      "has_spoken_voice": true|false,
      "humor_praise_and_wit_profile": {{
        "attraction_mechanisms": ["<e.g. fan_flattery_and_praise", "subject_glorification", "hype_building", "admiration_and_awe", "witty_compliments", "obsessive_fandom_commentary", "playful_banter", "self_deprecating", "deadpan", "roast", "sarcastic">],
        "sarcasm_vs_flattery_balance": "<pure_flattery_and_hype | balanced_wit | heavy_sarcasm_and_roast>",
        "entertainment_tactics": "<how praise/wit/humor is deployed to hook target audience>",
        "evidence_citation": "<exact quote from caption or transcript supporting this mechanism>"
      }},
      "verbal_signatures": {{
        "filler_words": ["<spoken fillers from whisper_transcript strictly, or empty if non-vocal>"],
        "sentence_openers": ["<repeated spoken hooks from whisper_transcript strictly, or empty if non-vocal>"],
        "catchphrases": ["<spoken signature catchphrases from whisper_transcript strictly, or empty if non-vocal>"]
      }},
      "certainty_and_posture": "<declarative_authoritative | conversational_hedging | inquisitive_curious | hype_admiration | text_overlay_captioning>",
      "emotional_register": "<enthusiastic | intimate | sarcastic | authoritative | chaotic | calm | celebratory>"
    }},

    "presentation_and_visual_style_dna": {{
      "visual_presentation_style": "<e.g. paparazzi_clip_compilation | direct_camera_talking_head | dynamic_voiceover_broll | text_over_broll>",
      "on_camera_energy": "<high_hyper | relaxed_conversational | intense_focused | N/A_curated_clips>",
      "signature_editing_tics": ["<e.g. slow_mo_walk_sync", "text_overlay_emoji_punch", "rapid_cut_montage", "reaction_freeze_frame">],
      "visual_framing_preference": "close_up_face | medium_shot | full_body | product_macro | paparazzi_telephoto | mixed"
    }},

    "audience_relationship_and_stance": {{
      "parasocial_register": "fellow_fanatic | best_friend | mentor | older_sibling | stage_performer | peer_in_struggle | spectator_curator",
      "address_style": "direct_you | formal_audience | storytelling_narrative | third_person_curation",
      "inclusivity_language": "we_tribe | direct_you | self_focused_i | subject_centric",
      "fourth_wall_breaks": true|false
    }},

    "content_philosophy_and_risk": {{
      "core_stance": "consensus_hype_builder | contrarian_myth_buster | deep_dive_educator | casual_entertainer | fan_celebration",
      "risk_appetite_and_edge": "brand_safe | edgy_unfiltered | opinionated_controversial",
      "topics_and_themes": ["<core recurring themes or topics>"],
      "evidence_citation": "<exact quote supporting core stance>"
    }},

    "narrative_and_storytelling_dna": {{
      "storytelling_logic": "anecdote_first | fact_first | mystery_reveal | escalation_challenge | highlight_glorification | paparazzi_event_capture",
      "hook_formula": "<repeatable hook pattern in opening seconds>",
      "tension_building_pattern": "<how curiosity, hype, or suspense is constructed>",
      "payoff_and_twist_style": "<how reveals, punchlines, or clips conclude>"
    }},

    "code_switching_and_cultural_dna": {{
      "primary_language": "<e.g. English | Hindi | Hinglish | Spanish>",
      "code_switching_style": "<e.g. fluent_hinglish | formal_to_casual_switch | regional_slang_heavy | caption_slang_only | monolingual>",
      "slang_density": "high | moderate | rare"
    }},

    "signature_quirks_and_tropes": [
      "<Unique quirk 1 that makes this creator 100% recognizable>",
      "<Unique quirk 2>",
      "<Unique quirk 3>"
    ],

    "creator_fingerprint_summary": "<2-sentence summary of what makes this creator completely unique in their niche>"
  }}
}}
"""

ALLOWED_CREATOR_FORMATS = [
    "talking_head",
    "voiceover_narrator",
    "curator_aggregator",
    "text_music_only"
]

REQUIRED_TOP_LEVEL_KEYS = ["creator_id", "schema_version", "creator_format", "behavior"]

REQUIRED_BEHAVIOR_KEYS = [
    "personality_and_voice_dna",
    "presentation_and_visual_style_dna",
    "audience_relationship_and_stance",
    "content_philosophy_and_risk",
    "narrative_and_storytelling_dna",
    "code_switching_and_cultural_dna",
    "signature_quirks_and_tropes",
    "creator_fingerprint_summary"
]


class UniversalCreatorCBMEngine:
    """
    Universal Creator Behavior Model (CBM) Engine V4.5.
    Absorbs multi-clip history from pool_metadata.json along with optional 480p compressed video frames
    to extract human behavioral patterns, personality DNA, flattery/humor mechanisms, and presentation signatures.
    """

    def __init__(self, pool_metadata_path: Optional[str] = None):
        if pool_metadata_path is None:
            pool_metadata_path = os.path.join(_REPO_ROOT, "Original_audio", "pool_metadata.json")
        self.meta_path = os.path.abspath(pool_metadata_path)

    def extract_creator_dna(
        self,
        creator_handle: str,
        video_path: Optional[str] = None,
        sample_keyframes: Optional[List[str]] = None,
        custom_pool_data: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Extracts human Creator Behavior & Personality DNA for the specified handle.
        Optionally processes 480p compressed video frames alongside pool metadata.
        """
        start_time = time.time()
        norm_handle = creator_handle.lower().strip("@").strip()
        logger.info(f"🧬 [UNIVERSAL CBM V4.5] Analyzing Creator Behavior DNA for handle '{norm_handle}'...")

        GENERIC_CREATOR_HANDLES = {
            "manual", "unknown", "default", "default_creator", "temp",
            "none", "input", "uploads", "downloads", "direct", "video", "clip"
        }

        if norm_handle in GENERIC_CREATOR_HANDLES or not norm_handle:
            logger.info(f"ℹ️ [UNIVERSAL CBM] Standalone/manual handle '{creator_handle}' requested — applying neutral baseline creator DNA.")
            fallback_res = self._behavioral_fallback(creator_handle, {})
            fallback_res["evidence"] = {
                "total_matched_clips_in_pool": 0,
                "source_clips_analyzed": 0,
                "vocal_clips_detected": 0,
                "vocal_ratio": 0.0,
                "confidence": "low",
                "has_vocals_ground_truth": False,
                "confidence_calculation_rule": "Standalone/generic handle — defaulting to neutral baseline DNA."
            }
            fallback_res["citation_grounding_verified"] = False
            fallback_res["execution_time_seconds"] = round(time.time() - start_time, 3)
            return fallback_res

        pool_data = custom_pool_data or self._load_pool_metadata()
        creator_clips = self._extract_clips_for_creator(pool_data, norm_handle)

        if not creator_clips:
            logger.warning(f"⚠️ [UNIVERSAL CBM] Creator '{creator_handle}' not found in pool metadata. Applying neutral baseline DNA.")
            fallback_res = self._behavioral_fallback(creator_handle, {})
            fallback_res["evidence"] = {
                "total_matched_clips_in_pool": 0,
                "source_clips_analyzed": 0,
                "vocal_clips_detected": 0,
                "vocal_ratio": 0.0,
                "confidence": "low",
                "has_vocals_ground_truth": False,
                "confidence_calculation_rule": "Creator not found in pool metadata — defaulting to neutral baseline DNA."
            }
            fallback_res["citation_grounding_verified"] = False
            fallback_res["execution_time_seconds"] = round(time.time() - start_time, 3)
            return fallback_res

        total_matched_clips = len(creator_clips)
        # Absorb cross-video creator patterns across at least 10-12 clips
        analyzed_clips_dict = dict(list(creator_clips.items())[:12])
        analyzed_count = len(analyzed_clips_dict)

        # ─────────────────────────────────────────────────────────────────────
        # 1. ACCURATE CONFIDENCE CALCULATION
        # ─────────────────────────────────────────────────────────────────────
        if analyzed_count < 3:
            programmatic_confidence = "low"
        elif analyzed_count < 6:
            programmatic_confidence = "medium"
        else:
            programmatic_confidence = "high"

        # Synthesize multi-clip context & build raw text corpus for citation grounding
        clip_summary = []
        transcript_texts = []
        caption_texts = []
        vocal_clip_count = 0

        for c_url, c_val in analyzed_clips_dict.items():
            trans = c_val.get("whisper_transcript", {})
            full_text = trans.get("text") or " ".join([s.get("text", "") for s in trans.get("sentences", []) if isinstance(s, dict)])
            caption = c_val.get("caption") or ""
            has_vocals_flag = c_val.get("audio_math", {}).get("has_vocals", False) is True

            if has_vocals_flag:
                vocal_clip_count += 1
                # GROUND TRUTH PROTECTION: Only append transcript_full if clip actually HAS VOCALS
                # Prevents Whisper hallucination text on silent clips from polluting transcript corpus!
                if full_text:
                    transcript_texts.append(full_text)

            if caption:
                caption_texts.append(caption)

            clip_summary.append({
                "url": c_url,
                "shortcode": c_val.get("shortcode"),
                "caption": caption,
                "hashtags": c_val.get("hashtags"),
                "taggedUsers": c_val.get("taggedUsers", []),
                "ownerUsername": c_val.get("ownerUsername"),
                "transcript_full": full_text if has_vocals_flag else "",
                "transcript_words": (trans.get("words", [])[:20]) if has_vocals_flag else [],
                "audio_math": {
                    "vibe": c_val.get("audio_math", {}).get("vibe"),
                    "avg_energy": c_val.get("audio_math", {}).get("avg_energy"),
                    "has_vocals": has_vocals_flag
                },
                "visual_intelligence": c_val.get("gemini_semantic_visual_intelligence", {}),
                "audio_intelligence": c_val.get("gemini_semantic_audio_intelligence", {})
            })

        transcript_corpus_string = " ".join(transcript_texts)
        full_corpus_string = " ".join(transcript_texts + caption_texts)

        # Ground-truth audio check: Require at least 33% vocal ratio (or >0 for small sample size <=2)
        vocal_ratio = round(vocal_clip_count / analyzed_count, 2) if analyzed_count > 0 else 0.0
        if analyzed_count <= 2:
            has_vocals_ground_truth = (vocal_clip_count > 0)
        else:
            has_vocals_ground_truth = (vocal_ratio >= 0.33)

        # Expand budget to 25,000 characters to fit rich 10-12 clip metadata
        pool_context_json = json.dumps(clip_summary, indent=2, ensure_ascii=False)[:25000]

        prompt_text = CREATOR_BEHAVIOR_PROMPT.format(
            creator_name=creator_handle,
            pool_metadata_context=pool_context_json
        )

        images = self._prepare_video_images(video_path=video_path, sample_keyframes=sample_keyframes)
        raw_json_str = self._call_gemini_api(prompt_text, images=images)

        is_fallback_path = False
        if raw_json_str:
            try:
                cleaned_json = self._clean_fence_wrappers(raw_json_str)
                res_dict = json.loads(cleaned_json)

                if self._validate_cbm_schema(res_dict):
                    res_dict["status"] = "success"
                    res_dict["api_mode"] = "live_gemini_creator_cbm"
                    logger.info(f"✅ [UNIVERSAL CBM] Successfully extracted Creator Behavior DNA for '{creator_handle}'.")
                else:
                    logger.warning("⚠️ [UNIVERSAL CBM] Schema validation notice — generating behavioral fallback.")
                    res_dict = self._behavioral_fallback(creator_handle, creator_clips)
                    is_fallback_path = True
            except Exception as e:
                logger.warning(f"⚠️ JSON parse error on CBM output: {e}")
                res_dict = self._behavioral_fallback(creator_handle, creator_clips)
                is_fallback_path = True
        else:
            logger.warning("⚠️ [UNIVERSAL CBM] Gemini call empty — generating behavioral fallback.")
            res_dict = self._behavioral_fallback(creator_handle, creator_clips)
            is_fallback_path = True

        res_dict["schema_version"] = "4.6"

        # ─────────────────────────────────────────────────────────────────────
        # 2. SYMMETRIC FORMAT REALIGNMENT AGAINST GROUND TRUTH
        # ─────────────────────────────────────────────────────────────────────
        fmt = str(res_dict.get("creator_format", "")).strip().lower()
        if fmt not in ALLOWED_CREATOR_FORMATS:
            fmt = "curator_aggregator" if not has_vocals_ground_truth else "voiceover_narrator"

        # Symmetric alignment:
        if not has_vocals_ground_truth and fmt in ["talking_head", "voiceover_narrator"]:
            logger.warning(f"⚠️ [FORMAT REALIGNMENT] Model reported vocal format '{fmt}' but has_vocals_ground_truth is False (ratio={vocal_ratio}). Realigning format to 'curator_aggregator'.")
            fmt = "curator_aggregator"
        elif has_vocals_ground_truth and fmt in ["curator_aggregator", "text_music_only"]:
            logger.info(f"ℹ️ [FORMAT REALIGNMENT] Model reported non-vocal format '{fmt}' but has_vocals_ground_truth is True ({vocal_clip_count}/{analyzed_count} clips). Realigning format to 'voiceover_narrator'.")
            fmt = "voiceover_narrator"

        res_dict["creator_format"] = fmt

        # ─────────────────────────────────────────────────────────────────────
        # 3. GROUND-TRUTH-DRIVEN VERBAL SIGNATURE GATING & TRANSCRIPT VERIFICATION
        # ─────────────────────────────────────────────────────────────────────
        b = res_dict.get("behavior", {})
        if isinstance(b, dict):
            p_dna = b.get("personality_and_voice_dna", {})
            if isinstance(p_dna, dict):
                p_dna["has_spoken_voice"] = has_vocals_ground_truth

                if not has_vocals_ground_truth:
                    # Sanitize false positive verbal signatures if non-vocal
                    p_dna["verbal_signatures"] = {
                        "filler_words": [],
                        "sentence_openers": [],
                        "catchphrases": [],
                        "note": f"Sanitized: has_vocals_ground_truth=False (vocal_ratio={vocal_ratio})"
                    }
                else:
                    # Verify verbal signatures strictly against vocal transcript_full (never captions!)
                    self._verify_verbal_signatures(p_dna.get("verbal_signatures", {}), transcript_corpus_string)

        # ─────────────────────────────────────────────────────────────────────
        # 4. NORMALIZED SUBSTRING CITATION VERIFICATION
        # ─────────────────────────────────────────────────────────────────────
        if not is_fallback_path:
            citation_verification = self._verify_citations(res_dict, full_corpus_string)
            res_dict["citation_grounding_verified"] = citation_verification["all_verified"]
            res_dict["citation_verification_details"] = citation_verification["details"]
        else:
            res_dict["citation_grounding_verified"] = None
            res_dict["citation_verification_details"] = []

        # Metadata envelope
        res_dict["evidence"] = {
            "total_matched_clips_in_pool": total_matched_clips,
            "source_clips_analyzed": analyzed_count,
            "vocal_clips_detected": vocal_clip_count,
            "vocal_ratio": vocal_ratio,
            "confidence": programmatic_confidence,
            "has_vocals_ground_truth": has_vocals_ground_truth,
            "confidence_calculation_rule": f"Hard-capped in Python code based on min(clip_count, 6) analyzed: {analyzed_count} analyzed (<3=low, 3-5=medium, >=6=high)"
        }

        res_dict["execution_time_seconds"] = round(time.time() - start_time, 3)
        self._save_cbm_to_pool_metadata(creator_handle, res_dict)
        return res_dict

    def _save_cbm_to_pool_metadata(self, creator_handle: str, cbm_payload: Dict[str, Any]) -> None:
        """
        Delegates CBM metadata saving to AudioPoolManager for thread safety,
        atomic file writing, and cloud vault sync.
        """
        try:
            from Audio_Modules.audio_pool_manager import AudioPoolManager
            pool_mgr = AudioPoolManager()
            count = pool_mgr.patch_creator_behavior_model(creator_handle, cbm_payload)
            if count > 0:
                logger.info(f"💾 [CBM POOL SAVE] Saved creator_behavior_model via AudioPoolManager across {count} clip(s) for '{creator_handle}'.")
        except Exception as e:
            logger.warning(f"⚠️ [CBM POOL SAVE] Could not save CBM via AudioPoolManager: {e}")

    def _normalize_text_for_matching(self, text: str) -> str:
        """
        Normalizes smart quotes, unicode dashes, ellipses, trailing punctuation, and whitespace.
        """
        if not text:
            return ""
        t = text.lower().strip()
        # Replace smart quotes and unicode quotes
        t = re.sub(r"[’'‘`]", "'", t)
        t = re.sub(r'[”"“]', '"', t)
        # Replace unicode dashes and ellipses
        t = re.sub(r"[—–−]", "-", t)
        t = re.sub(r"\.\.\.|…", " ", t)
        # Strip outer quotes
        t = t.strip('"').strip("'")
        # Collapse whitespace
        t = re.sub(r"\s+", " ", t).strip()
        return t

    def _verify_citations(self, res_dict: Dict[str, Any], corpus_text: str) -> Dict[str, Any]:
        """
        Normalized exact substring verification for evidence_citation strings.
        Mandates non-empty citation strings.
        """
        details = []
        all_verified = True

        behavior = res_dict.get("behavior", {})
        if not isinstance(behavior, dict):
            return {"all_verified": False, "details": []}

        norm_corpus = self._normalize_text_for_matching(corpus_text)

        targets = [
            ("humor_praise_and_wit_profile", behavior.get("personality_and_voice_dna", {}).get("humor_praise_and_wit_profile", {})),
            ("content_philosophy_and_risk", behavior.get("content_philosophy_and_risk", {}))
        ]

        for name, section in targets:
            if isinstance(section, dict):
                cite = str(section.get("evidence_citation", "")).strip()
                if not cite:
                    all_verified = False
                    logger.warning(f"⚠️ [EMPTY CITATION LOOPHOLE DETECTED] Section '{name}' has empty evidence_citation!")
                    section["evidence_citation_status"] = "empty_citation_failed"
                    details.append({
                        "section": name,
                        "citation": "",
                        "verified": False,
                        "error": "empty_string"
                    })
                else:
                    norm_cite = self._normalize_text_for_matching(cite)
                    match_found = norm_cite in norm_corpus if norm_cite else False

                    details.append({
                        "section": name,
                        "citation": cite,
                        "verified": match_found
                    })

                    if not match_found:
                        all_verified = False
                        logger.warning(f"⚠️ [CITATION UNVERIFIED] Section '{name}' citation '{cite}' exact match failed in corpus!")
                        section["evidence_citation_status"] = "unverified_quote"
                    else:
                        section["evidence_citation_status"] = "verified_exact_quote"

        return {"all_verified": all_verified, "details": details}

    def _verify_verbal_signatures(self, verbal_sig_dict: Dict[str, Any], transcript_corpus: str) -> None:
        """
        Verifies verbal signature lists strictly against transcript_full corpus text.
        Removes items that were pulled from captions instead of spoken transcripts.
        """
        if not isinstance(verbal_sig_dict, dict):
            return

        norm_transcript = self._normalize_text_for_matching(transcript_corpus)
        if not norm_transcript:
            return

        for category in ["filler_words", "sentence_openers", "catchphrases"]:
            items = verbal_sig_dict.get(category, [])
            if isinstance(items, list):
                verified_items = []
                for item in items:
                    norm_item = self._normalize_text_for_matching(str(item))
                    if norm_item and norm_item in norm_transcript:
                        verified_items.append(item)
                    else:
                        logger.info(f"ℹ️ [VERBAL SIGNATURE SANITIZED] Category '{category}' item '{item}' not found in transcript_full (possible caption leak). Removed.")
                verbal_sig_dict[category] = verified_items

    def _prepare_video_images(
        self,
        video_path: Optional[str] = None,
        sample_keyframes: Optional[List[str]] = None
    ) -> List[Any]:
        """
        Samples 6 compressed frames from 480p proxy video using duration-proportional timeline sampling.
        """
        images = []
        frame_paths = []

        tmp_dir = None
        if video_path and os.path.exists(video_path):
            tmp_dir = tempfile.mkdtemp(prefix="cbm_480p_frames_")
            try:
                duration = self._get_video_duration(video_path)
                fractions = [0.05, 0.12, 0.35, 0.60, 0.88, 0.96]
                timestamps = [round(duration * f, 2) for f in fractions]

                for i, ss_sec in enumerate(timestamps, 1):
                    ss = str(ss_sec)
                    out_f = os.path.join(tmp_dir, f"frame_{i}.jpg")
                    cmd = ["ffmpeg", "-y", "-ss", ss, "-i", video_path, "-vframes", "1", "-vf", "scale=640:360", "-q:v", "4", out_f]
                    res = subprocess.run(cmd, capture_output=True, timeout=10)
                    if res.returncode != 0:
                        logger.warning(f"⚠️ FFmpeg frame extraction at {ss}s returned error code {res.returncode}: {res.stderr.decode('utf-8', errors='ignore')[:150]}")
                    
                    if os.path.exists(out_f) and os.path.getsize(out_f) > 100:
                        frame_paths.append(out_f)
            except Exception as _fe:
                logger.warning(f"⚠️ 480p frame extraction notice: {_fe}")

        if not frame_paths and sample_keyframes:
            frame_paths = [p for p in sample_keyframes if os.path.exists(p)][:6]

        if frame_paths:
            try:
                from PIL import Image
                for p in frame_paths:
                    with Image.open(p) as img:
                        images.append(img.copy())
            except Exception as _ie:
                logger.warning(f"⚠️ PIL image load notice: {_ie}")

        if tmp_dir and os.path.isdir(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)

        return images

    def _get_video_duration(self, video_path: str) -> float:
        """Helper to get exact video duration using ffprobe with safe fallback."""
        try:
            cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", video_path]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            val = float(res.stdout.strip())
            return val if val > 0 else 10.0
        except Exception:
            return 10.0

    def _load_pool_metadata(self) -> Dict[str, Any]:
        """Loads pool_metadata.json safely."""
        if os.path.exists(self.meta_path):
            try:
                with open(self.meta_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"⚠️ Could not load pool_metadata.json: {e}")
        return {}

    def _extract_clips_for_creator(self, pool_data: Dict[str, Any], norm_handle: str) -> Dict[str, Any]:
        """
        Word-boundary / exact segment handle matching.
        """
        files_root = pool_data.get("files", {})
        social_entries = files_root.get("social_media_id", {})
        if not isinstance(social_entries, dict):
            return {}

        matching = {}
        pattern = re.compile(rf"(?:^|[/_@.]){re.escape(norm_handle)}(?:[/_@.]|$)", re.IGNORECASE)

        for url, clip in social_entries.items():
            if not isinstance(clip, dict):
                continue

            owner = (clip.get("ownerUsername") or "").lower().strip("@").strip()
            shortcode = (clip.get("shortcode") or "").lower().strip()

            if owner == norm_handle or shortcode == norm_handle:
                matching[url] = clip
            elif pattern.search(url):
                matching[url] = clip

        return matching

    def _clean_fence_wrappers(self, raw_str: str) -> str:
        """Robust Markdown fence stripper."""
        raw_str = re.sub(r"^[\s\n]*```(?:json)?", "", raw_str, flags=re.IGNORECASE | re.MULTILINE)
        raw_str = re.sub(r"```[\s\n]*$", "", raw_str, flags=re.MULTILINE)
        return raw_str.strip()

    def _validate_cbm_schema(self, res_dict: Dict[str, Any]) -> bool:
        """
        Comprehensive Schema & Type Validator:
          - Checks top-level keys
          - Checks creator_format enum validity
          - Checks behavior section keys
          - Validates nested has_spoken_voice is boolean
          - Validates fourth_wall_breaks is boolean
          - Validates presence of non-empty evidence_citation string fields
        """
        if not isinstance(res_dict, dict):
            return False

        for top_k in REQUIRED_TOP_LEVEL_KEYS:
            if top_k not in res_dict:
                logger.warning(f"⚠️ [CBM VALIDATION] Missing top-level key: '{top_k}'")
                return False

        if str(res_dict.get("creator_format")).lower() not in ALLOWED_CREATOR_FORMATS:
            logger.warning(f"⚠️ [CBM VALIDATION] Invalid creator_format: '{res_dict.get('creator_format')}'")
            return False

        behavior = res_dict.get("behavior")
        if not isinstance(behavior, dict):
            return False

        for k in REQUIRED_BEHAVIOR_KEYS:
            if k not in behavior:
                logger.warning(f"⚠️ [CBM VALIDATION] Missing behavior section: '{k}'")
                return False

        # Validate boolean type for has_spoken_voice
        p_dna = behavior.get("personality_and_voice_dna")
        if not isinstance(p_dna, dict) or not isinstance(p_dna.get("has_spoken_voice"), bool):
            logger.warning("⚠️ [CBM VALIDATION] 'personality_and_voice_dna.has_spoken_voice' must be a boolean.")
            return False

        # Validate fourth_wall_breaks is boolean
        aud_rel = behavior.get("audience_relationship_and_stance")
        if not isinstance(aud_rel, dict) or not isinstance(aud_rel.get("fourth_wall_breaks"), bool):
            logger.warning("⚠️ [CBM VALIDATION] 'audience_relationship_and_stance.fourth_wall_breaks' must be a boolean.")
            return False

        # Validate nested evidence_citation fields exist and are non-empty strings
        humor_profile = p_dna.get("humor_praise_and_wit_profile")
        h_cite = humor_profile.get("evidence_citation") if isinstance(humor_profile, dict) else None
        if not isinstance(h_cite, str) or not h_cite.strip():
            logger.warning("⚠️ [CBM VALIDATION] 'evidence_citation' in humor_praise_and_wit_profile must be a non-empty string.")
            return False

        phil_risk = behavior.get("content_philosophy_and_risk")
        p_cite = phil_risk.get("evidence_citation") if isinstance(phil_risk, dict) else None
        if not isinstance(p_cite, str) or not p_cite.strip():
            logger.warning("⚠️ [CBM VALIDATION] 'evidence_citation' in content_philosophy_and_risk must be a non-empty string.")
            return False

        return True

    def _call_gemini_api(self, prompt_text: str, images: Optional[List[Any]] = None) -> Optional[str]:
        """Calls Gemini Vision / text reasoning via gemini_router."""
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            return None

        try:
            from Gemini_Modules.gemini_router_module.gemini_governor import gemini_router
            if gemini_router:
                res = gemini_router.generate(
                    task_type="vision" if images else "analysis",
                    prompt=[prompt_text] + (images or []),
                    module_name="universal_creator_cbm_engine"
                )
                if res and len(str(res).strip()) > 10:
                    return str(res).strip()
        except Exception as _re:
            logger.error(f"❌ Router call error: {_re}")

        return None

    def _behavioral_fallback(
        self,
        creator_handle: str,
        clips: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Dynamic fallback synthesizing actual transcripts, visual intelligence, and audio_math.has_vocals ground truth.
        """
        analyzed_clips_list = list(clips.values())[:6]
        sample_clip = analyzed_clips_list[0] if analyzed_clips_list else {}

        vis_intel = sample_clip.get("gemini_semantic_visual_intelligence", {})
        director = vis_intel.get("content_director", {})
        intent = vis_intel.get("intent", "general_content")
        tone = director.get("tone", "engaging")

        # Ground truth check for fallback path
        vocal_clip_count = sum(1 for c in analyzed_clips_list if c.get("audio_math", {}).get("has_vocals", False) is True)
        analyzed_count = len(analyzed_clips_list)
        vocal_ratio = round(vocal_clip_count / analyzed_count, 2) if analyzed_count > 0 else 0.0

        if analyzed_count <= 2:
            has_vocals_ground_truth = (vocal_clip_count > 0)
        else:
            has_vocals_ground_truth = (vocal_ratio >= 0.33)

        fallback_format = "voiceover_narrator" if has_vocals_ground_truth else "curator_aggregator"

        return {
            "creator_id": creator_handle,
            "schema_version": "4.6",
            "creator_format": fallback_format,
            "behavior": {
                "creator_archetype": f"{tone}_{intent}_creator",
                "content_domain": intent,

                "personality_and_voice_dna": {
                    "has_spoken_voice": has_vocals_ground_truth,
                    "humor_praise_and_wit_profile": {
                        "attraction_mechanisms": ["subject_glorification", "hype_building"] if not has_vocals_ground_truth else ["playful_banter"],
                        "sarcasm_vs_flattery_balance": "pure_flattery_and_hype" if not has_vocals_ground_truth else "balanced_wit",
                        "entertainment_tactics": "Derived from visual intelligence fallback.",
                        "evidence_citation": sample_clip.get("caption", "") or "fallback_quote"
                    },
                    "verbal_signatures": {
                        "filler_words": [],
                        "sentence_openers": [],
                        "catchphrases": [],
                        "note": "Dynamic fallback"
                    },
                    "certainty_and_posture": "hype_admiration" if not has_vocals_ground_truth else "conversational_hedging",
                    "emotional_register": tone
                },

                "presentation_and_visual_style_dna": {
                    "visual_presentation_style": vis_intel.get("editing_style", "montage"),
                    "on_camera_energy": "relaxed_conversational" if has_vocals_ground_truth else "N/A_curated_clips",
                    "signature_editing_tics": ["rapid_cuts", "text_overlay"],
                    "visual_framing_preference": "medium_shot"
                },

                "audience_relationship_and_stance": {
                    "parasocial_register": "spectator_curator" if not has_vocals_ground_truth else "best_friend",
                    "address_style": "third_person_curation" if not has_vocals_ground_truth else "direct_you",
                    "inclusivity_language": "subject_centric" if not has_vocals_ground_truth else "direct_you",
                    "fourth_wall_breaks": False
                },

                "content_philosophy_and_risk": {
                    "core_stance": "consensus_hype_builder",
                    "risk_appetite_and_edge": "brand_safe",
                    "topics_and_themes": [intent],
                    "evidence_citation": sample_clip.get("caption", "") or "fallback_quote"
                },

                "narrative_and_storytelling_dna": {
                    "storytelling_logic": "highlight_glorification" if not has_vocals_ground_truth else "fact_first",
                    "hook_formula": director.get("engagement_hook", "High energy visual opening"),
                    "tension_building_pattern": "gradual_escalation",
                    "payoff_and_twist_style": "direct_reveal"
                },

                "code_switching_and_cultural_dna": {
                    "primary_language": "English",
                    "code_switching_style": "caption_slang_only" if not has_vocals_ground_truth else "formal_to_casual_switch",
                    "slang_density": "moderate"
                },

                "signature_quirks_and_tropes": [
                    f"Signature {tone} tone in content opening",
                    f"Uses {vis_intel.get('editing_style', 'dynamic')} editing style"
                ],

                "creator_fingerprint_summary": f"Creator '{creator_handle}' is characterized by a {tone} personality and {intent} focus."
            },
            "status": "success",
            "api_mode": "behavioral_metric_fallback"
        }


def extract_creator_cbm(
    creator_handle: str,
    video_path: Optional[str] = None,
    sample_keyframes: Optional[List[str]] = None
) -> Dict[str, Any]:
    """Module-level orchestrator function."""
    engine = UniversalCreatorCBMEngine()
    return engine.extract_creator_dna(creator_handle, video_path=video_path, sample_keyframes=sample_keyframes)
