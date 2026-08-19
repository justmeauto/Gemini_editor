"""
Intelligence_Modules/forensic_analyzer.py
------------------------------------------
Forensic Video Analyzer — Vision-AI based frame inspection.

Performs TWO tasks on extracted video frames via Gemini Vision:

  TASK 1 — WATERMARK DETECTION
    Returns bounding boxes for any watermark / logo / branding detected.

  TASK 2 — CONTENT ANALYSIS
    Classifies content intent, confidence, recommended editing feature flags,
    and monetization safety rating.

Output format (strict JSON):
{
  "watermarks": [{"x":0,"y":0,"w":0,"h":0}],
  "content_strategy": {
    "intent": "...",
    "confidence": 0.0-1.0,
    "feature_flags": {
        "enable_price_tags": true/false,
        "enable_fashion_caption": true/false,
        "enable_cinematic_zoom": true/false,
        "enable_speed_ramps": true/false,
        "enable_fast_pacing": true/false,
        "enable_voiceover": true/false
    },
    "recommended_editing_style": "...",
    "safety": "safe|risky|blocked"
  }
}
"""

import os
import json
import logging
import re
import subprocess
import tempfile
import shutil
from typing import List, Optional
try:
    from Gemini_Modules.gemini_router_module.gemini_governor import gemini_router
except ImportError:
    try:
        from Intelligence_Modules.gemini_governor import gemini_router
    except ImportError:
        from gemini_governor import gemini_router

from dotenv import load_dotenv

# Load env
if os.path.exists(".env"):
    load_dotenv(".env", override=True)
else:
    load_dotenv("Credentials/.env", override=True)

logger = logging.getLogger("forensic_analyzer")

# ── Prompt ────────────────────────────────────────────────────────────────────

FORENSIC_PROMPT = """
You are a Professional Short-Form Content Director analyzing a sparse set of sampled video frames.
You do not have the full video — only {frame_count} frames at {width}x{height}. Treat every frame as
partial evidence, not the whole story.

Your output has two consumers, both of which matter:
1. A second-stage AI editor (Gemini Call 3) will read your JSON as raw text and reason over it to build
   an actual FFmpeg edit plan. Your fields are its only source of visual understanding — it never sees
   the frames itself. Vague or generic output here directly degrades what it produces.
2. Your output is stored permanently as training/retrieval signal for a future recommendation system.
   Precision and honesty matter more than confident-sounding guesses — a wrong but confident answer
   pollutes that store in a way that compounds over time.

---

GROUNDING RULES (apply to everything below)

- Only report what is directly visible in the frames provided. Do not infer off-screen action, audio
  content, or context you cannot see. If you would need information from outside these frames to be
  sure, say so in `visual_event` rather than stating it as fact.
- If frames are ambiguous, contradictory, or too sparse to classify confidently, say so — do not force
  a confident answer to satisfy the schema. Use lower confidence and the fallback category below
  instead of guessing. A well-flagged "uncertain" is more useful to Call 3 than a wrong "certain."
- If the video appears to mix multiple content types (e.g. a tutorial with a meme cutaway), classify by
  whichever type occupies the most visual weight across sampled frames, and name the secondary type
  explicitly inside `visual_event` — don't silently drop it.

---

TASK 1 — UNIVERSAL CONTENT CLASSIFICATION

Content Categories (`intent`) — choose the single best fit:
  - "educational_explainer" : Tutorials, coding guides, lessons, technical diagrams, how-tos.
  - "kids_animation"        : 3D/2D animated cartoons, playful stories, animated characters.
  - "tech_review"           : Gadgets, software demos, gaming streams, product breakdowns.
  - "fitness_action"        : Workouts, sports, high-energy athletics, outdoor action.
  - "nature_travel"         : Landscapes, wildlife, travel vlogs, scenic ambient views.
  - "fashion_lifestyle"     : Outfits, modeling, beauty routines, celebrity/paparazzi highlights.
  - "meme_viral"            : Humor, internet trends, reaction clips, skits.
  - "food_cooking"          : Recipes, cooking demos, food prep, taste tests.
  - "talking_head"          : Podcast clips, interviews, commentary, direct-to-camera speech, news.
  - "music_performance"     : Singing, instruments, DJ sets, dance choreography, concerts.
  - "general_content"       : Video does not clearly match any category above, or evidence is too
                              sparse/ambiguous to classify confidently. This is a valid, useful answer —
                              do not stretch another category to avoid it. Call 3 handles this fine.

Allowed Feature Flags (`feature_flags`):
  enable_price_tags, enable_fashion_caption, enable_cinematic_zoom,
  enable_speed_ramps, enable_fast_pacing, enable_voiceover

Guidelines by category (defaults — override if frame evidence clearly contradicts them):
- Educational / Tutorial / Talking Head → enable_voiceover=true, enable_speed_ramps=false.
  Instructional pacing must stay legible; don't enable fast pacing just because one section is energetic.
- Animation / Cartoons  → enable_cinematic_zoom=true, playful pacing.
- Fashion / Modeling    → enable_fashion_caption=true, enable_cinematic_zoom=true. Only set
  enable_price_tags=true if actual products/apparel are visibly the focus, not just worn incidentally.
- Fitness / Action      → enable_speed_ramps=true, enable_fast_pacing=true.
- Nature / Travel       → enable_cinematic_zoom=true, enable_fast_pacing=false.
- Music / Performance   → enable_speed_ramps=true if rhythm/choreography-driven, else cinematic zoom.
- Food / Cooking        → enable_cinematic_zoom=true (close-ups), enable_voiceover=true if narration visible.
- meme_viral / general_content → default enable_fast_pacing=true unless frames suggest a slower,
  narrative-driven clip.

Conflict rule: voiceover-driven legibility takes precedence over fast pacing when they'd fight each
other (e.g. an energetic tutorial) — but both can be true together when the content genuinely supports it.

Confidence (`confidence`, 0.0–1.0) reflects evidence quality, not category enthusiasm — this number is
read directly by Call 3 as a trust signal, so miscalibration here misleads a second model, not just a log:
- 0.8–1.0: category unambiguous across most/all sampled frames.
- 0.5–0.79: likely, but some frames are inconclusive, low-detail, or mixed-content.
- 0.0–0.49: too sparse/blurry/static/contradictory to classify confidently — prefer "general_content"
  at this range over forcing a specific category.

Safety Rules (`safety.classification`):
- "safe": brand-safe, no policy-sensitive content, suitable for ad monetization.
- "risky": borderline — mild innuendo, implied aggressive language, alcohol/tobacco visible but not the
  focus, intense-but-non-graphic action, unverified claims-style content.
- "blocked": nudity/sexual content, graphic violence/gore, weapons used to threaten, visible drug use,
  hate symbols or hateful gestures, content endangering minors, self-harm depiction, or copyrighted
  broadcast footage (sports leagues, films, TV) used as the primary subject.
If any "blocked" trigger is present, `monetization_safe` must be false regardless of other flags.

---

TASK 2 — WATERMARK DETECTION

Only report bounding boxes for overlay elements added during recording/editing: burned-in app logos,
usernames/handles, platform UI chrome (TikTok/CapCut/screen-recorder icons), timestamps, burned-in
captions added in post, or repeated corner/edge branding.
Do NOT report shop signage, product packaging text, clothing text/logos worn by a subject, background
posters, or any text that is physically part of the scene rather than an overlay.
If uncertain whether something is overlay vs. in-scene text, do not report it — a missed watermark is
preferable to a false-positive box on real scene content that a downstream tool might crop or blur.

---

TASK 3 — CONTENT DIRECTOR (Human Editor Intelligence)

This block is the richest signal Call 3 receives — write it like you're briefing a human editor who
has never seen the footage, not like you're filling out a form.

1. detected_entities: only entities directly visible. Format: ["person:female", "environment:indoor",
   "code:editor", "animation:cartoon"]. Don't list entities you're inferring rather than seeing.
2. visual_event: 1-2 concrete sentences describing what's actually happening across sampled frames.
   If content is mixed or ambiguous, say so here explicitly.
3. viewer_attention: the single object/subject most likely to grab attention in the first frame.
4. internet_context: recognizable cultural/tech/social references visible (neutral wording, no
   speculation about the identity of real people).
5. possible_narratives: pick from ["educational_guide", "playful_story", "tech_breakdown",
   "fitness_motivation", "scenic_journey", "fashion_moment", "celebrity_highlight", "humor_reaction",
   "recipe_walkthrough", "commentary_talk", "music_showcase"].
6. recommended_narrative: the single best fit from the list above.
7. tone: "educational", "playful", "aspirational", "dramatic", "humorous", "calm_ambient", "hype",
   "informative", or "neutral" if none clearly fit.
8. editing_style: one of "educational_tutorial", "playful_animation", "fast_social", "cinematic",
   "documentary", "product_review", "fashion_showcase", "vlog", "news", "podcast_clip".
9. engagement_hook: one concrete sentence for what should appear/be said in the first 3 seconds —
   grounded in what's actually in the frames, not a generic hook template. Call 3 will likely act on
   this directly, so specificity here has outsized value.
10. feature_commands: echo the recommended feature flags for consistency with Task 1.

---

Return ONLY valid JSON. No markdown wrappers, no commentary. Just the JSON object.

Required JSON format:
{{
  "watermarks": [
    {{"x": <int>, "y": <int>, "w": <int>, "h": <int>}}
  ],
  "intent": "educational_explainer|kids_animation|tech_review|fitness_action|nature_travel|fashion_lifestyle|meme_viral|food_cooking|talking_head|music_performance|general_content",
  "confidence": <float 0.0-1.0>,
  "editing_style": "educational_tutorial|playful_animation|fast_social|cinematic|documentary|product_review|fashion_showcase|vlog|news|podcast_clip",
  "feature_flags": {{
    "enable_price_tags": <bool>,
    "enable_fashion_caption": <bool>,
    "enable_cinematic_zoom": <bool>,
    "enable_speed_ramps": <bool>,
    "enable_fast_pacing": <bool>,
    "enable_voiceover": <bool>
  }},
  "platform_priority": ["youtube_shorts", "instagram_reels", "facebook_reels"],
  "safety": {{
    "classification": "safe|risky|blocked",
    "monetization_safe": <bool>
  }},
  "content_director": {{
    "detected_entities": [],
    "visual_event": "",
    "viewer_attention": "",
    "internet_context": [],
    "possible_narratives": [],
    "recommended_narrative": "",
    "tone": "",
    "editing_style": "",
    "is_talking_on_camera": <bool>,
    "engagement_hook": "",
    "feature_commands": {{
      "enable_fast_pacing": <bool>,
      "enable_cinematic_zoom": <bool>,
      "enable_speed_ramps": <bool>,
      "enable_voiceover": <bool>,
      "enable_price_tags": <bool>,
      "enable_fashion_caption": <bool>
    }}
  }}
}}

Frame dimensions: {width}x{height} pixels.
Number of frames provided: {frame_count}
"""

# ── Default fallback ──────────────────────────────────────────────────────────

DEFAULT_RESULT = {
    "watermarks": [],
    "intent":        "unknown",
    "confidence":    0.0,
    "editing_style": "cinematic",
    "feature_flags": {
        "enable_price_tags":      False,
        "enable_fashion_caption": False,
        "enable_cinematic_zoom":  True,
        "enable_speed_ramps":     False,
        "enable_fast_pacing":     False,
        "enable_voiceover":       True,
    },
    "platform_priority": ["youtube_shorts", "instagram_reels", "facebook_reels"],
    "safety": {
        "classification":    "risky",
        "monetization_safe": False,
    },
    # Backward-compat wrapper so orchestrator.get("content_strategy") still works
    "content_strategy": {
        "intent":                    "unknown",
        "confidence":                0.0,
        "recommended_editing_style": "cinematic",
        "feature_flags": {
            "enable_price_tags":      False,
            "enable_fashion_caption": False,
            "enable_cinematic_zoom":  True,
            "enable_speed_ramps":     False,
            "enable_fast_pacing":     False,
            "enable_voiceover":       True,
        },
        "safety": "risky",
    },
}


# ── Main class ────────────────────────────────────────────────────────────────

class ForensicVideoAnalyzer:
    """
    Extracts frames from a video and sends them to Gemini Vision for forensic analysis.

    Usage:
        analyzer = ForensicVideoAnalyzer()
        result = analyzer.analyze(video_path)
        # result is a dict matching the JSON schema above
    """

    # How many frames to sample from the video (spread evenly)
    FRAME_COUNT = 5
    # Target resolution for frames sent to Gemini (keeps token count low)
    FRAME_WIDTH = 640
    FRAME_HEIGHT = 360

    def __init__(self):
        self.router = gemini_router
        self._available = True if gemini_router else False

        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            logger.warning("🔬 ForensicAnalyzer: GEMINI_API_KEY not set — will return defaults")
            self._available = False
            return

        model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        logger.info(f"🔬 ForensicAnalyzer: ACTIVE (model={model_name})")

    # ── Public API ─────────────────────────────────────────────────────────────

    def analyze(self, video_path: str,
                frame_paths: Optional[List[str]] = None,
                creator_name: Optional[str] = None,
                audio_candidates: Optional[List[dict]] = None) -> dict:
        """
        Perform forensic & scene analysis on a video.

        Args:
            video_path:       Path to source video file.
            frame_paths:      Optional list of pre-extracted frame image paths.
            creator_name:     Optional creator handle/title hint for face cache RAG.
            audio_candidates: Optional list of candidate BGM audio metadata dicts.
        """
        try:
            # ── Pre-Pipeline Scene & Face Intelligence ─────────────────────────
            scene_context = {}
            try:
                try:
                    from Core_Modules.scene_intel import analyze_scene_pre_pipeline
                except ImportError:
                    from Gemini_Modules.scene_intel import analyze_scene_pre_pipeline
                scene_context = analyze_scene_pre_pipeline(video_path, creator_name=creator_name)
                logger.info(
                    f"👤 SceneIntel: faces={scene_context.get('num_detected_faces')} | "
                    f"subjects={scene_context.get('num_subjects')} | "
                    f"face_cache={scene_context.get('face_cache_status')}"
                )
            except Exception as sie:
                logger.debug(f"SceneIntel fallback: {sie}")

            if not self._available:
                logger.info("🔬 ForensicAnalyzer skipped (unavailable)")
                res = DEFAULT_RESULT.copy()
                res["scene_context"] = scene_context
                return res

            # ── Step 1: Extract frames (Hook-Dense Strategic Sampler) ─────────
            tmp_dir = None
            own_frames = False
            sampling_context = None
            if frame_paths and all(os.path.exists(p) for p in frame_paths):
                frames = frame_paths
            else:
                tmp_dir = tempfile.mkdtemp(prefix="forensic_frames_")
                own_frames = True
                try:
                    from Main_Modules.strategic_frame_sampler import extract_strategic_frame_files
                    res_frames = extract_strategic_frame_files(video_path, tmp_dir, return_meta=True)
                    if isinstance(res_frames, tuple):
                        frames, sample_meta = res_frames
                        sampling_context = self._build_sampling_note(sample_meta)
                    else:
                        frames = res_frames
                    logger.info(f"🔬 ForensicAnalyzer: loaded {len(frames)} strategic hook-dense frames")
                except Exception as sse:
                    logger.warning(f"🔬 ForensicAnalyzer: strategic_frame_sampler fallback to ffmpeg: {sse}")
                    frames = self._extract_frames(video_path, tmp_dir)
                    sampling_context = None

            if not frames:
                logger.warning("🔬 ForensicAnalyzer: no frames extracted — returning default")
                res = DEFAULT_RESULT.copy()
                res["scene_context"] = scene_context
                return res

            # ── Step 2: Build Gemini payload with audio candidate table ───────
            result = self._call_gemini_with_audio(
                frames,
                creator_name=creator_name,
                audio_candidates=audio_candidates,
                sampling_context=sampling_context
            )
            result["scene_context"] = scene_context

            # ── Step 2.5: Save to Master ClipIntelligenceStore (Schema v3) ──
            try:
                from Gemini_Modules.clip_intelligence_store import ClipIntelligenceStore
                from Audio_Modules.audio_extractor import load_audio_analysis

                store = ClipIntelligenceStore()
                clip_folder = os.path.dirname(video_path)
                clip_id = os.path.basename(clip_folder)

                clip_data = store.load(clip_id, clip_folder) or store.create_blank(clip_id, clip_folder)

                # 1. Fill audio_data.math FIRST from Phase 1 DSP audio_analysis.json
                p1_audio = load_audio_analysis(clip_folder)
                if p1_audio:
                    store.patch_audio_math(clip_data, {
                        "tempo_bpm": p1_audio.get("tempo_bpm", 120.0),
                        "beat_timestamps": p1_audio.get("beats", []),
                        "drop_timestamps": p1_audio.get("drops", []),
                        "avg_energy": p1_audio.get("avg_energy", 0.5),
                        "vibe": p1_audio.get("vibe", "unknown"),
                        "beat_count": p1_audio.get("beat_count", len(p1_audio.get("beats", []))),
                        "drop_count": p1_audio.get("drop_count", len(p1_audio.get("drops", []))),
                        "wav_path": p1_audio.get("wav_path"),
                    })

                # 2. Fill visual_context SECOND (Gemini visual semantic context)
                visual_ctx = {
                    "intent": result.get("intent", "viral_reel"),
                    "tone": result.get("content_director", {}).get("tone", "aspirational"),
                    "editing_style": result.get("editing_style", "cinematic"),
                    "engagement_hook": result.get("content_director", {}).get("engagement_hook", ""),
                    "detected_entities": result.get("content_director", {}).get("detected_entities", []),
                    "possible_narratives": result.get("content_director", {}).get("possible_narratives", []),
                    "recommended_narrative": result.get("content_director", {}).get("recommended_narrative", ""),
                    "feature_flags": result.get("feature_flags", {}),
                    "safety": result.get("safety", {}),
                    "creative_possibilities": result.get("creative_possibilities", []),
                }
                # Calculate 3-Signal speech_intelligence (Vision Talking + Audio Vocals + Speech Formants)
                cd_block = result.get("content_director", {})
                is_talking = bool(result.get("is_talking_on_camera") or cd_block.get("is_talking_on_camera", False) or result.get("intent") == "talking_head")
                has_audio = bool(p1_audio.get("has_audio", True)) if p1_audio else True
                has_vocals = bool(p1_audio.get("has_vocals", True)) if p1_audio else is_talking
                is_speech_vocal = bool(p1_audio.get("is_speech_vocal", True)) if p1_audio else is_talking

                if is_talking and has_vocals and is_speech_vocal:
                    speech_mode = "on_camera_dialogue"
                    rec_action = "preserve_voice_duck_bgm"
                elif is_talking and has_vocals and not is_speech_vocal:
                    speech_mode = "lip_sync_dub"
                    rec_action = "audio_replace_full_bgm"
                elif not is_talking and has_vocals and is_speech_vocal:
                    speech_mode = "voiceover_narration"
                    rec_action = "preserve_voice_duck_bgm"
                elif not is_talking and has_vocals and not is_speech_vocal:
                    speech_mode = "music_broll"
                    rec_action = "audio_replace_full_bgm"
                elif not has_vocals:
                    speech_mode = "silent_broll"
                    rec_action = "audio_replace_full_bgm"
                else:
                    # SAFE DEFAULT: Default to preserving voice if audio is present!
                    speech_mode = "on_camera_dialogue" if has_audio else "silent_broll"
                    rec_action = "preserve_voice_duck_bgm" if has_audio else "audio_replace_full_bgm"

                speech_intel = {
                    "speech_mode": speech_mode,
                    "is_talking_visually": is_talking,
                    "has_spoken_vocal": has_vocals,
                    "is_speech_vocal": is_speech_vocal,
                    "recommended_audio_action": rec_action
                }
                result["speech_intelligence"] = speech_intel
                visual_ctx["speech_intelligence"] = speech_intel

                store.patch_visual_context(clip_data, visual_ctx)

                # 3. Fill audio_data.context THIRD (Gemini audio semantic context into same audio_data block)
                audio_ctx = {
                    "has_vocals": has_vocals,
                    "speech_mode": speech_mode,
                    "recommended_audio_action": rec_action,
                    "dominant_emotion": result.get("content_director", {}).get("tone", "hype"),
                    "vibe": result.get("editing_style", "rhythm_driven"),
                }
                store.patch_audio_context(clip_data, audio_ctx)

                # 4. Fill visual_vectors FOURTH (Mathematical targeting timestamps for OpenCV)
                # Compute strategic targeting timestamps
                targeted_ts = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
                if isinstance(result.get("creative_possibilities"), list):
                    for cp in result["creative_possibilities"]:
                        if isinstance(cp, dict) and "target_duration_sec" in cp:
                            dur = float(cp["target_duration_sec"])
                            targeted_ts.extend([round(dur * 0.25, 2), round(dur * 0.5, 2), round(dur * 0.75, 2), round(dur, 2)])
                targeted_ts = sorted(list(set(targeted_ts)))

                vectors = {
                    "targeted_timestamps_sec": targeted_ts,
                    "scene_cut_timestamps": [t for t in targeted_ts if t > 2.0],
                    "hook_zone_end_sec": 5.0,
                    "climax_zone_start_sec": max(targeted_ts) if targeted_ts else 10.0,
                }
                store.patch_visual_vectors(clip_data, vectors)

                # Save updated clip intelligence JSON to disk and pool_metadata.json
                store.save(clip_id, clip_data, clip_folder)
                result["clip_intelligence"] = clip_data
                result["visual_vectors"] = vectors
                logger.info(f"🧠 [ClipIntelligenceStore] Updated clip '{clip_id}' -> speech_mode='{speech_mode}' ({rec_action}).")
            except Exception as store_err:
                logger.warning(f"🧠 [ClipIntelligenceStore] Update warning: {store_err}")

            # ── Step 3: Cleanup ───────────────────────────────────────────────
            if own_frames and tmp_dir and os.path.isdir(tmp_dir):
                shutil.rmtree(tmp_dir, ignore_errors=True)

            return result

        except Exception as e:
            logger.error(f"🔬 ForensicAnalyzer: unexpected error — {e}")
            return DEFAULT_RESULT.copy()

    def _build_sampling_note(self, sample_meta: dict) -> str:
        """Build a factual sampling-zone note from actual sampler output."""
        if not sample_meta or not isinstance(sample_meta, dict):
            return ""
        hook = sample_meta.get("hook_count")
        body = sample_meta.get("body_count")
        climax = sample_meta.get("climax_count")
        motion = sample_meta.get("motion_count")
        if hook is None or body is None:
            return ""
        return (
            f"[FRAME SAMPLING CONTEXT] {hook} hook frames (0-5s, dense), "
            f"{body} body frames (sparse, evenly spaced)"
            + (f", {climax} climax frames (last 10s, dense)" if climax else "")
            + (f", {motion} peak-motion frames (optical flow)" if motion else "")
            + ". Hook zone is intentionally over-represented — weight body/climax frames "
            "at least equally when judging overall tone, don't let the hook dominate the classification."
        )

    def _extract_frames(self, video_path: str, out_dir: str) -> List[str]:
        """
        Extract FRAME_COUNT frames evenly spread across the video using FFmpeg.
        Returns list of absolute paths to extracted JPEG files.
        """
        if not os.path.exists(video_path):
            logger.warning(f"🔬 Frame extraction: video not found — {video_path}")
            return []

        try:
            # Get duration via ffprobe (fixed: 'streams' plural)
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=duration",
                 "-of", "json", video_path],
                capture_output=True, text=True, timeout=15
            )
            dur_data = json.loads(probe.stdout)
            streams = dur_data.get("streams", [])
            duration = float(streams[0].get("duration", 10.0)) if streams else 10.0
        except Exception as pe:
            logger.warning(f"🔬 Frame extraction: duration probe failed — {pe}")
            duration = 10.0

        n = self.FRAME_COUNT
        interval = max(0.5, duration / (n + 1))
        frame_paths = []

        for i in range(1, n + 1):
            ts = round(i * interval, 2)
            out_path = os.path.join(out_dir, f"frame_{i:02d}.jpg")
            cmd = [
                "ffmpeg", "-y", "-ss", str(ts),
                "-i", video_path,
                "-vframes", "1",
                "-vf", f"scale={self.FRAME_WIDTH}:{self.FRAME_HEIGHT}",
                "-q:v", "3",
                out_path
            ]
            try:
                subprocess.run(cmd, check=True, capture_output=True, timeout=30)
                if os.path.exists(out_path) and os.path.getsize(out_path) > 100:
                    frame_paths.append(out_path)
            except Exception as fe:
                logger.debug(f"🔬 Frame {i} extraction failed: {fe}")

        logger.info(f"🔬 Extracted {len(frame_paths)}/{n} forensic frames")
        return frame_paths

    def _call_gemini_with_audio(self, frame_paths: List[str],
                                creator_name: Optional[str] = None,
                                audio_candidates: Optional[List[dict]] = None,
                                sampling_context: Optional[str] = None) -> dict:
        """
        Send keyframes + candidate BGM audio metadata table to Gemini 2.5 Flash Vision.
        Gemini selects matching audio track and generates structured creative_possibilities edit plan.
        """
        audio_table_str = ""
        if audio_candidates:
            table_lines = []
            for idx, c in enumerate(audio_candidates, start=1):
                table_lines.append(
                    f"{idx}. Track: '{c.get('track_name')}' | BPM: {c.get('bpm')} | Energy: {c.get('avg_energy')} | Drops: {c.get('drops')}"
                )
            audio_table_str = "\n".join(table_lines)

        global_frames = [p for p in frame_paths if "detail_crop" not in p]
        prompt_text = FORENSIC_PROMPT.format(
            width=self.FRAME_WIDTH,
            height=self.FRAME_HEIGHT,
            frame_count=len(global_frames)
        )
        if sampling_context:
            prompt_text += f"\n\n{sampling_context}\n"
        prompt_text += f"\nCreator Handle Hint: '{creator_name or 'unknown'}'\n"

        if audio_table_str:
            prompt_text += f"""
---

TASK 4 — BGM AUDIO SELECTION & CREATIVE POSSIBILITIES

[Candidate Background Music Tracks Available]
{audio_table_str}

Analyze the visual content against the candidate BGM audio tracks listed above:
1. `selected_audio_track`: Choose the SINGLE best matching track filename (e.g. 'Fit_girl_2.mp3').
2. `creative_possibilities`: Provide 2-3 structured edit options as objects:
   [
     {{"rank": 1, "clip_label": "hero_hook", "target_duration_sec": 15, "editing_style": "rhythm_driven"}},
     {{"rank": 2, "clip_label": "action_cut", "target_duration_sec": 30, "editing_style": "fast_cuts"}}
   ]

Include `selected_audio_track` and `creative_possibilities` in your returned JSON object.
"""

        # Inject Speech Boundary & Sentence Cut Rules if present
        if frame_paths:
            clip_dir = os.path.dirname(frame_paths[0])
            sb_json = os.path.join(clip_dir, "speech_boundaries.json")
            if os.path.exists(sb_json):
                try:
                    with open(sb_json, "r", encoding="utf-8") as f:
                        sb_data = json.load(f)
                    if sb_data.get("has_speech") and sb_data.get("clean_cut_timestamps"):
                        cuts = [f"{c['timestamp_sec']}s (after word '{c['word_after_which_to_cut']}')" for c in sb_data["clean_cut_timestamps"][:15]]
                        prompt_text += (
                            f"\n---\n\nTASK 5 — VOCAL & SPEECH BOUNDARY CUT RULES\n"
                            f"Whisper transcribed {len(sb_data.get('words', []))} spoken words in this clip.\n"
                            f"CRITICAL VOCAL CUT BOUNDARIES: When trimming or making shot directives for this clip, ONLY suggest cut points at these exact sentence-ending timestamps to avoid cutting speech mid-word:\n"
                            f"- " + "\n- ".join(cuts) + "\n"
                        )
                except Exception as _sb_e:
                    logger.debug(f"🔬 Could not load speech_boundaries.json: {_sb_e}")

        payload = [prompt_text]
        try:
            from PIL import Image
            for p in frame_paths:
                try:
                    img = Image.open(p)
                    payload.append(img)
                except Exception as ie:
                    logger.debug(f"🔬 Could not open frame {p}: {ie}")
        except ImportError:
            logger.warning("🔬 PIL not available — sending text-only prompt")

        try:
            res_txt = self.router.generate(
                task_type="vision",
                prompt=payload,
                module_name="forensic_analyzer",
                gen_config={"temperature": 0.2, "response_mime_type": "application/json"}
            )
            if not res_txt:
                return self._call_gemini(frame_paths, sampling_context=sampling_context)
            parsed = self._parse_response(res_txt)
            return parsed if parsed else self._call_gemini(frame_paths, sampling_context=sampling_context)
        except Exception as e:
            logger.error(f"Multimodal perception error: {e}")
            return self._call_gemini(frame_paths, sampling_context=sampling_context)

    def _call_gemini(self, frame_paths: List[str], sampling_context: Optional[str] = None) -> dict:
        """
        Send frames + micro-crops + prompt to Gemini Vision, parse and validate JSON response.
        Falls back gracefully to DEFAULT_RESULT on any error.
        """
        global_frames = [p for p in frame_paths if "detail_crop" not in p]
        micro_crops   = [p for p in frame_paths if "detail_crop" in p]

        # Build prompt with frame metadata
        prompt_text = FORENSIC_PROMPT.format(
            width=self.FRAME_WIDTH,
            height=self.FRAME_HEIGHT,
            frame_count=len(global_frames)
        )
        if sampling_context:
            prompt_text += f"\n\n{sampling_context}\n"
        if micro_crops:
            prompt_text += f"\nNOTE: Payload includes {len(micro_crops)} 256x256 high-resolution micro-crops of high-frequency detail regions for zero-hallucination watermark and text inspection."

        # Build payload: prompt text + PIL images
        payload = [prompt_text]
        try:
            from PIL import Image
            for p in frame_paths:
                try:
                    img = Image.open(p)
                    payload.append(img)
                except Exception as ie:
                    logger.debug(f"🔬 Could not open frame {p}: {ie}")
        except ImportError:
            logger.warning("🔬 PIL not available — sending text-only forensic prompt")

        # Model fallback list
        try:
            res_txt = self.router.generate(
                task_type="vision",
                prompt=payload,
                module_name="forensic_analyzer",
                gen_config={"temperature": 0.2, "response_mime_type": "application/json"}
            )
            if not res_txt: return DEFAULT_RESULT.copy()
            return self._parse_response(res_txt)
        except Exception as e:
            logger.error(f"Forensic error: {e}")
            return DEFAULT_RESULT.copy()
    def _parse_response(self, raw: str) -> Optional[dict]:
        """
        Parse and validate Gemini JSON response.
        Handles BOTH schemas:
          - New (flat): intent/feature_flags/safety.classification at root level
          - Old (nested): content_strategy.intent / content_strategy.safety string
        Always returns both formats so orchestrator.py backward-compat is maintained.
        """
        try:
            match = re.search(r'(\{.*\})', raw, re.DOTALL)
            if not match:
                logger.warning("🔬 Forensic parse: no JSON object found in response")
                return None

            data = json.loads(match.group(1))

            # ── Watermarks ────────────────────────────────────────────────────
            raw_wm = data.get("watermarks", [])
            if not isinstance(raw_wm, list):
                raw_wm = []
            watermarks = []
            for wm in raw_wm:
                if isinstance(wm, dict):
                    watermarks.append({
                        "x": int(wm.get("x", 0)),
                        "y": int(wm.get("y", 0)),
                        "w": int(wm.get("w", 0)),
                        "h": int(wm.get("h", 0)),
                    })

            # ── Detect which schema the model returned ────────────────────────
            # New schema: feature_flags at root level
            # Old schema: feature_flags inside content_strategy
            if "feature_flags" in data:
                # NEW flat schema (director prompt)
                flags_raw = data.get("feature_flags", {})
                intent    = str(data.get("intent", "unknown"))
                confidence = max(0.0, min(1.0, float(data.get("confidence", 0.5))))
                editing_style = str(data.get("editing_style", "cinematic"))
                platform_priority = data.get("platform_priority",
                                    ["youtube_shorts", "instagram_reels", "facebook_reels"])

                # safety is now a nested object
                safety_obj = data.get("safety", {})
                if isinstance(safety_obj, dict):
                    safety_cls   = str(safety_obj.get("classification", "risky")).lower()
                    mon_safe     = bool(safety_obj.get("monetization_safe", safety_cls == "safe"))
                else:
                    # Model returned a string instead of object — tolerate it
                    safety_cls   = str(safety_obj).lower().strip()
                    mon_safe     = safety_cls == "safe"

            else:
                # OLD nested content_strategy schema (backward compat)
                cs = data.get("content_strategy", {})
                if not isinstance(cs, dict):
                    cs = {}
                flags_raw      = cs.get("feature_flags", {})
                intent         = str(cs.get("intent", "unknown"))
                confidence     = max(0.0, min(1.0, float(cs.get("confidence", 0.5))))
                editing_style  = str(cs.get("recommended_editing_style", "cinematic"))
                platform_priority = ["youtube_shorts", "instagram_reels", "facebook_reels"]
                safety_cls     = str(cs.get("safety", "risky")).lower().strip()
                mon_safe       = safety_cls == "safe"

            if not isinstance(flags_raw, dict):
                flags_raw = {}

            safety_cls = safety_cls if safety_cls in ("safe", "risky", "blocked") else "risky"

            feature_flags = {
                "enable_price_tags":      bool(flags_raw.get("enable_price_tags",      False)),
                "enable_fashion_caption": bool(flags_raw.get("enable_fashion_caption", False)),
                "enable_cinematic_zoom":  bool(flags_raw.get("enable_cinematic_zoom",  True)),
                "enable_speed_ramps":     bool(flags_raw.get("enable_speed_ramps",     False)),
                "enable_fast_pacing":     bool(flags_raw.get("enable_fast_pacing",     False)),
                "enable_voiceover":       bool(flags_raw.get("enable_voiceover",       True)),
            }

            # Extract BGM selection fields if present
            selected_audio_track = str(data.get("selected_audio_track", ""))
            creative_possibilities = data.get("creative_possibilities", [])
            if not isinstance(creative_possibilities, list):
                creative_possibilities = []

            result = {
                # ── New flat schema ────────────────────────────────────────────
                "watermarks":             watermarks,
                "intent":                 intent,
                "confidence":             round(confidence, 3),
                "editing_style":          editing_style,
                "feature_flags":          feature_flags,
                "platform_priority":      platform_priority,
                "selected_audio_track":   selected_audio_track,
                "creative_possibilities": creative_possibilities,
                "safety": {
                    "classification":    safety_cls,
                    "monetization_safe": mon_safe,
                },
                # ── Backward-compat content_strategy wrapper ───────────────────
                # orchestrator.py reads .get("content_strategy", {}) for flags/safety/intent
                "content_strategy": {
                    "intent":                    intent,
                    "confidence":                round(confidence, 3),
                    "recommended_editing_style": editing_style,
                    "feature_flags":             feature_flags,
                    "safety":                    safety_cls,   # str — matches old code
                },
            }

            # ── Content Director block (new in this version) ───────────────────
            # Extract and validate. If missing, embed empty defaults (non-breaking).
            try:
                cd_raw = data.get("content_director", {})
                if isinstance(cd_raw, dict) and cd_raw:
                    def _str(k): return str(cd_raw.get(k, ""))
                    def _lst(k):
                        v = cd_raw.get(k, [])
                        return [str(x) for x in v] if isinstance(v, list) else []

                    allowed_flags = {
                        "enable_fast_pacing", "enable_cinematic_zoom",
                        "enable_speed_ramps", "enable_voiceover",
                        "enable_price_tags",  "enable_fashion_caption",
                    }
                    raw_cmds = cd_raw.get("feature_commands", {})
                    feature_commands = {
                        k: bool(v)
                        for k, v in (raw_cmds.items() if isinstance(raw_cmds, dict) else [])
                        if k in allowed_flags
                    }
                    for f in allowed_flags:
                        feature_commands.setdefault(f, False)

                    result["content_director"] = {
                        "detected_entities":     _lst("detected_entities"),
                        "visual_event":          _str("visual_event"),
                        "viewer_attention":      _str("viewer_attention"),
                        "internet_context":      _lst("internet_context"),
                        "possible_narratives":   _lst("possible_narratives"),
                        "recommended_narrative": _str("recommended_narrative"),
                        "tone":                  _str("tone"),
                        "editing_style":         _str("editing_style"),
                        "engagement_hook":       _str("engagement_hook"),
                        "feature_commands":      feature_commands,
                    }
                    logger.info(
                        f"🎬 ContentDirector: narrative={result['content_director']['recommended_narrative']} "
                        f"style={result['content_director']['editing_style']} "
                        f"tone={result['content_director']['tone']} "
                        f"hook='{result['content_director']['engagement_hook'][:60]}'"
                    )
                else:
                    logger.info("🎬 ContentDirector: no block in Gemini response — using defaults")
                    result["content_director"] = {}
            except Exception as _cde:
                logger.warning(f"🎬 ContentDirector parse error (non-critical): {_cde}")
                result["content_director"] = {}

            logger.info(
                f"🔬 Forensic result: intent={intent} "
                f"style={editing_style} safety={safety_cls} "
                f"watermarks={len(watermarks)} confidence={confidence:.2f} "
                f"monetizable={mon_safe}"
            )
            active_flags = [k.replace("enable_", "") for k, v in feature_flags.items() if v]
            logger.info(
                f"🔬 ForensicFlags: intent={intent}, style={editing_style}, "
                f"flags=[{', '.join(active_flags) or 'none'}]"
            )
            return result

        except (json.JSONDecodeError, Exception) as e:
            logger.warning(f"🔬 Forensic parse error: {e}")
            return None


# ── Module-level singleton ────────────────────────────────────────────────────

_analyzer: Optional[ForensicVideoAnalyzer] = None


def get_analyzer() -> ForensicVideoAnalyzer:
    """Return the module-level singleton, creating it on first call."""
    global _analyzer
    if _analyzer is None:
        _analyzer = ForensicVideoAnalyzer()
    return _analyzer


def analyze_video(video_path: str, frame_paths: Optional[List[str]] = None, intelligence_cache=None) -> dict:
    """
    Main Orchestrator for Forensic Analysis.
    Auto-extracts strategic frames if frame_paths is None.
    """
    return get_analyzer().analyze(video_path, frame_paths=frame_paths)

# Alias for legacy support
analyze = analyze_video
