"""
Gemini FFmpeg Command Synthesis Engine — AMTCE Video Engine
============================================================
Architecture (multi-pass human-grade autonomous editor):

  [Raw Source Video] + [Audio]
       │
       ├──► gemini_enhance_for_watermark.py → [Forensic Context] (watermark x/y/w/h only)
       │
       └──► VideoContextExtractor
               │  1. Samples 480p/720p @ 1 FPS
               │  2. MotionAnalyzer (Farneback Optical Flow: Motion Vectors + Energy Arc)
               │  3. BeatEngine (Audio Beat Timestamps Grid)
               │  4. Dedicated Gemini Vision Call:
               │     "Describe what is IN this video — scenes, subject, mood, pacing"
               ▼
          [Video Semantic & Motion/Beat Context Cache]
               │
  [ChromaDB RAG] → [Reference Editing EXAMPLES]
    (examples only — mood board, NOT instructions)
               │
               ▼
  [GeminiFFmpegEngine.run_full_pipeline()]
       │  1. Calls gemini_router.generate() to synthesize initial JSON plan
       │  2. Validates plan against GEMINI_FFMPEG_SCHEMA
       │  3. RefinementLoop (Pass 1 execution + quality scoring)
       │  4. If score < 0.75 → Pass 2 re-manipulation with Gemini before finalizing
       ▼
  [execute_pipeline()] — Topological sort + subprocess run

Author: AMTCE Autonomous Multimedia Transformation Compilation Engine
"""

import os
import json
import logging
import re
import shlex
import subprocess
import math
import uuid
from typing import Dict, List, Any, Optional, Tuple

try:
    import jsonschema
    _HAS_JSONSCHEMA = True
except ImportError:
    _HAS_JSONSCHEMA = False

try:
    import cv2
    _HAS_CV2 = True
except ImportError:
    _HAS_CV2 = False

# Lazy MotionAnalyzer import
_HAS_MOTION = False
try:
    from Video_Modules.motion_analyzer import MotionAnalyzer
    _HAS_MOTION = True
except ImportError:
    MotionAnalyzer = None

# Lazy BeatEngine import
_HAS_BEAT = False
try:
    from Audio_Modules.beat_engine import BeatEngine
    _HAS_BEAT = True
except ImportError:
    BeatEngine = None

# Lazy RAG ChromaDB imports
_HAS_RAG = False
try:
    from rag.chroma_client import get_chroma_collection
    from rag.rag_bootstrap import ensure_collection_ready
    from rag.retriever import get_top_patterns
    _HAS_RAG = True
except ImportError:
    _HAS_RAG = False

_HAS_ROUTER = False
try:
    from Gemini_Modules.gemini_router_module.gemini_governor import gemini_router
    _HAS_ROUTER = True
except ImportError:
    try:
        from Intelligence_Modules.gemini_governor import gemini_router
        _HAS_ROUTER = True
    except ImportError:
        try:
            from gemini_governor import gemini_router
            _HAS_ROUTER = True
        except ImportError:
            gemini_router = None

_CACHED_FONT_PATH: Optional[str] = None

logger = logging.getLogger("gemini_ffmpeg_synthesis")

# Optimal creative rendering order for video pipeline operations
OPERATION_PRIORITY = {
    "trim": 10,
    "scale_aspect": 20,
    "speed_change": 30,
    "speed_ramp": 30,
    "delogo_blur": 40,
    "transition": 50,
    "xfade": 50,
    "watermark_overlay": 60,
    "subtitle_burnin": 70,
    "audio_ducking_mix": 80,
    "audio_ducking": 80,
    "bgm_mix": 80,
    "audio_mix": 80
}

# =====================================================================
# 1. VIDEO CONTEXT EXTRACTOR
#    Integrates Gemini Vision, MotionAnalyzer (Optical Flow), and BeatEngine (Audio Beats)
# =====================================================================

VIDEO_UNDERSTANDING_PROMPT = """You are a world-class Video Director and Creative Analyst.
You are given a sequence of sampled frames from a video at ~1 frame per second, along with pre-computed Motion Vector & Optical Flow metrics and Audio Beat grid timestamps.

Your job is to produce a RICH, DETAILED understanding of what is actually IN this video.

Analyse the frames and metrics carefully to produce a JSON description covering:
- Subject and topic (what is this video showing? who/what is the main focus?)
- Scenes (how many distinct scenes/settings can you identify? describe each briefly)
- Visual energy (low / medium / high — based on motion blur, camera movement, cuts density)
- Pacing feel (slow/cinematic | moderate | fast/viral)
- Dominant mood (e.g. energetic, calm, dramatic, humorous, inspirational, tense)
- Visual style (e.g. UGC, studio, documentary, B-roll, lifestyle, talking-head, montage, travel)
- Dominant colors and lighting (e.g. warm golden, cold blue, high contrast, overexposed)
- Audio clues (infer from visual + provided beat grid)
- Recommended aspect ratio for social (9:16 vertical / 1:1 square / 16:9 landscape)
- Hook window (approximately which second range contains the most attention-grabbing visual)
- Problem areas (e.g. watermark visible at top-left, shaky cam, overexposed, dull intro)

OUTPUT FORMAT — Strict JSON only, no commentary outside it:
{
  "subject": "...",
  "scene_count": <int>,
  "scenes": [{"index": 0, "description": "...", "duration_estimate_s": <float>}],
  "visual_energy": "low" | "medium" | "high",
  "pacing_feel": "slow" | "moderate" | "fast",
  "mood": "...",
  "visual_style": "...",
  "dominant_colors": "...",
  "lighting": "...",
  "audio_inference": "...",
  "recommended_aspect": "9:16" | "1:1" | "16:9",
  "hook_window_s": [<start_s>, <end_s>],
  "problem_areas": ["..."],
  "edit_suggestions": ["..."]
}
"""


class VideoContextExtractor:
    """
    Extracts rich semantic video understanding, Optical Flow motion continuity/energy,
    and Audio Beat grid timestamps.
    """

    def __init__(self, sample_fps: float = 1.0, target_width: int = 720):
        self.sample_fps = sample_fps
        self.target_width = target_width
        self.motion_analyzer = MotionAnalyzer(target_width=480) if _HAS_MOTION else None
        self.beat_engine = BeatEngine() if _HAS_BEAT else None

    def _sample_frames(self, video_path: str) -> List[Any]:
        if not _HAS_CV2:
            raise RuntimeError("OpenCV (cv2) is required for VideoContextExtractor frame sampling.")
        try:
            from PIL import Image
        except ImportError:
            raise RuntimeError("Pillow (PIL) is required for VideoContextExtractor frame conversion.")

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open video: {video_path}")

        native_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        interval = max(1, int(round(native_fps / self.sample_fps)))

        pil_frames: List[Any] = []
        frame_idx = 0

        while True:
            ret, frame = cap.read()
            if not ret:
                break
            if frame_idx % interval == 0:
                h, w = frame.shape[:2]
                if w > self.target_width:
                    scale = self.target_width / w
                    frame = cv2.resize(frame, (self.target_width, int(h * scale)))
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                pil_frames.append(Image.fromarray(rgb))
            frame_idx += 1

        cap.release()
        logger.info(
            f"[VideoContextExtractor] Sampled {len(pil_frames)} frames "
            f"from {total_frames} total @ {self.sample_fps} FPS."
        )
        return pil_frames

    def _clean_json(self, text: str) -> str:
        if "```" in text:
            match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
            if match:
                return match.group(1).strip()
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return text[start:end + 1].strip()
        return text.strip()

    def extract(self, video_path: str, audio_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Main entry point. Extracts Gemini Vision context + Motion Vector Arc + Beat Grid.
        """
        fallback = {
            "subject": "unknown",
            "scene_count": 1,
            "scenes": [],
            "visual_energy": "medium",
            "pacing_feel": "moderate",
            "mood": "unknown",
            "visual_style": "unknown",
            "dominant_colors": "unknown",
            "lighting": "unknown",
            "audio_inference": "unknown",
            "recommended_aspect": "9:16",
            "hook_window_s": [0, 5],
            "problem_areas": [],
            "edit_suggestions": [],
            "motion_energy_arc": {},
            "audio_beats_s": [],
            "_source": "fallback_no_gemini"
        }

        # 1. Compute Audio Beats if available
        audio_target = audio_path or video_path
        if self.beat_engine and os.path.exists(audio_target):
            try:
                beats = self.beat_engine.analyze_beats(audio_target)
                fallback["audio_beats_s"] = [round(b, 2) for b in beats[:30]] # top 30 beats
                logger.info(f"[BeatEngine] Extracted {len(beats)} audio beats.")
            except Exception as e:
                logger.warning(f"[BeatEngine] Extraction failed: {e}")

        # 2. Compute Motion Vector Energy Arc (prefer 480p proxy if available for 4x speed)
        proxy_path = video_path.replace(".mp4", "_proxy480p.mp4")
        motion_target = proxy_path if os.path.exists(proxy_path) else video_path

        if self.motion_analyzer and os.path.exists(motion_target):
            try:
                arc_data = self.motion_analyzer.compute_energy_arc(motion_target, sample_interval_s=1.0)
                fallback["motion_energy_arc"] = arc_data
                logger.info(f"[MotionAnalyzer] Extracted optical flow energy arc ({len(arc_data.get('energy_curve', []))} points).")
            except Exception as e:
                logger.warning(f"[MotionAnalyzer] Energy arc extraction failed: {e}")

        if not _HAS_ROUTER or not gemini_router or not _HAS_CV2:
            fallback["_source"] = "fallback_heuristic"
            return fallback

        try:
            pil_frames = self._sample_frames(video_path)
        except Exception as e:
            logger.error(f"[VideoContextExtractor] Frame sampling failed: {e}")
            fallback["_source"] = f"fallback_sampling_error:{e}"
            return fallback

        if not pil_frames:
            fallback["_source"] = "fallback_no_frames"
            return fallback

        payload = [VIDEO_UNDERSTANDING_PROMPT] + pil_frames

        try:
            raw_text = gemini_router.generate(
                task_type="vision",
                prompt=payload,
                module_name="video_context_extractor",
                gen_config={"temperature": 0.3}
            )
        except Exception as e:
            logger.error(f"[VideoContextExtractor] Gemini call failed: {e}")
            fallback["_source"] = f"fallback_gemini_error:{e}"
            return fallback

        if not raw_text:
            fallback["_source"] = "fallback_empty_response"
            return fallback

        try:
            clean = self._clean_json(raw_text)
            ctx = json.loads(clean)
            ctx["_source"] = "gemini_vision"
            ctx["_frame_count"] = len(pil_frames)
            ctx["motion_energy_arc"] = fallback.get("motion_energy_arc", {})
            ctx["audio_beats_s"] = fallback.get("audio_beats_s", [])
            return ctx
        except Exception as e:
            logger.error(f"[VideoContextExtractor] JSON parse error: {e}")
            fallback["_source"] = "fallback_parse_error"
            return fallback

    def extract_from_forensic(self, video_path: str, forensic_context: Dict[str, Any], audio_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Fast Context Extractor that reuses pre-computed forensic context from Step 3.
        Avoids making a redundant 2nd Gemini Vision API call, reducing latency by 3 to 7 minutes.
        """
        prob_areas = list(forensic_context.get("flags", []))
        items = forensic_context.get("items", [])
        has_wm = forensic_context.get("watermark_detected") or any("watermark" in str(i).lower() or "logo" in str(i).lower() for i in items)
        if has_wm and not any("watermark" in str(p).lower() for p in prob_areas):
            prob_areas.append("Watermark logo detected in video frame")

        ctx = {
            "subject": forensic_context.get("subject") or forensic_context.get("creator", "unknown"),
            "scene_count": forensic_context.get("face_count", 1),
            "visual_energy": forensic_context.get("visual_energy", "medium"),
            "pacing_feel": forensic_context.get("style", "moderate"),
            "mood": forensic_context.get("mood", "cinematic"),
            "visual_style": forensic_context.get("style", "cinematic"),
            "recommended_aspect": forensic_context.get("recommended_aspect", "9:16"),
            "problem_areas": prob_areas,
            "motion_energy_arc": {},
            "audio_beats_s": [],
            "_source": "reused_forensic_fastpath"
        }

        # 1. Compute Audio Beats if available
        audio_target = audio_path or video_path
        if self.beat_engine and os.path.exists(audio_target):
            try:
                beats = self.beat_engine.analyze_beats(audio_target)
                ctx["audio_beats_s"] = [round(b, 2) for b in beats[:30]]
                logger.info(f"[BeatEngine] Fastpath extracted {len(beats)} audio beats.")
            except Exception as e:
                logger.warning(f"[BeatEngine] Extraction failed: {e}")

        # 2. Compute Motion Energy Arc (prefer 480p proxy if available for 4x speed)
        proxy_path = video_path.replace(".mp4", "_proxy480p.mp4")
        motion_target = proxy_path if os.path.exists(proxy_path) else video_path

        if self.motion_analyzer and os.path.exists(motion_target):
            try:
                arc_data = self.motion_analyzer.compute_energy_arc(motion_target, sample_interval_s=1.0)
                ctx["motion_energy_arc"] = arc_data
                logger.info(f"[MotionAnalyzer] Fastpath extracted optical flow energy arc ({len(arc_data.get('energy_curve', []))} points).")
            except Exception as e:
                logger.warning(f"[MotionAnalyzer] Energy arc extraction failed: {e}")

        return ctx


# =====================================================================
# 2. CORE FFMPEG COMMAND GENERATOR LAYER
# =====================================================================

class FFmpegCommandGenerator:
    """
    Generates deterministic, production-ready FFmpeg terminal commands.
    """

    def __init__(self, ffmpeg_path: str = "ffmpeg", hwaccel: Optional[str] = None):
        self.ffmpeg_path = ffmpeg_path
        self.hwaccel = hwaccel.lower() if hwaccel else None
        self._audio_stream_cache: Dict[str, bool] = {}

    def _get_encoder_flags(
        self,
        video_codec: str = "libx264",
        crf: int = 18,
        preset: str = "veryfast",
        encoding_cfg: Optional[Dict[str, Any]] = None
    ) -> List[str]:
        if isinstance(encoding_cfg, dict):
            video_codec = encoding_cfg.get("codec") or encoding_cfg.get("video_codec") or video_codec
            preset = encoding_cfg.get("preset") or preset
            crf_val = encoding_cfg.get("crf")
            if crf_val is not None:
                try:
                    crf = int(crf_val)
                except (ValueError, TypeError):
                    pass

        if self.hwaccel in ("cuda", "nvenc") or video_codec == "h264_nvenc":
            return ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", str(crf)]
        elif self.hwaccel == "qsv" or video_codec == "h264_qsv":
            return ["-c:v", "h264_qsv", "-preset", str(preset), "-global_quality", str(crf)]
        return ["-c:v", str(video_codec), "-preset", str(preset), "-crf", str(crf), "-pix_fmt", "yuv420p"]

    @staticmethod
    def cmd_list_to_string(cmd_list: List[str]) -> str:
        return " ".join(shlex.quote(arg) for arg in cmd_list)

    def _has_audio_stream(self, path: str) -> bool:
        if path in self._audio_stream_cache:
            return self._audio_stream_cache[path]
        if not os.path.exists(path):
            return getattr(self, "_root_input_has_audio", True)
        try:
            cmd = ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=codec_type", "-of", "json", path]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            data = json.loads(res.stdout)
            has_a = bool(data.get("streams"))
            self._audio_stream_cache[path] = has_a
            return has_a
        except Exception:
            return getattr(self, "_root_input_has_audio", True)

    def build_trim_command(self, input_path, output_path, start_time=0.0, end_time=None, duration=None, exact=True, reencode=True, encoding_cfg=None):
        cmd = [self.ffmpeg_path, "-y"]
        if not exact:
            cmd.extend(["-ss", str(start_time)])
        cmd.extend(["-i", input_path])
        if exact:
            cmd.extend(["-ss", str(start_time)])
        if duration is not None:
            cmd.extend(["-t", str(duration)])
        elif end_time is not None:
            calc_dur = max(0.0, end_time - start_time)
            cmd.extend(["-t", str(calc_dur)])
        if reencode:
            cmd.extend(self._get_encoder_flags(encoding_cfg=encoding_cfg))
            cmd.extend(["-avoid_negative_ts", "make_zero"])
            if self._has_audio_stream(input_path):
                cmd.extend(["-c:a", "copy"])
        else:
            cmd.extend(["-c:v", "copy"])
            if self._has_audio_stream(input_path):
                cmd.extend(["-c:a", "copy"])
        cmd.append(output_path)
        return {"cmd_list": cmd, "terminal_command": self.cmd_list_to_string(cmd), "operation": "trim", "input": input_path, "output": output_path}

    def build_scale_aspect_command(self, input_path, output_path, target_width=1080, target_height=1920, mode="crop", pad_color="black", encoding_cfg=None):
        cmd = [self.ffmpeg_path, "-y", "-i", input_path]
        if mode == "blur_pad":
            filter_str = (
                f"[0:v]scale={target_width}:{target_height}:force_original_aspect_ratio=increase,"
                f"crop={target_width}:{target_height},boxblur=20:10[bg];"
                f"[0:v]scale={target_width}:{target_height}:force_original_aspect_ratio=decrease[fg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2[vout]"
            )
            cmd.extend(["-filter_complex", filter_str, "-map", "[vout]"])
            if self._has_audio_stream(input_path):
                cmd.extend(["-map", "0:a?"])
        else:
            filter_str = (
                f"scale={target_width}:{target_height}:force_original_aspect_ratio=increase,"
                f"crop={target_width}:{target_height}"
            ) if mode == "crop" else (
                f"scale={target_width}:{target_height}:force_original_aspect_ratio=decrease,"
                f"pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2:{pad_color}"
            ) if mode == "pad" else f"scale={target_width}:{target_height}"
            cmd.extend(["-vf", filter_str])
        cmd.extend(self._get_encoder_flags(encoding_cfg=encoding_cfg))
        if self._has_audio_stream(input_path):
            cmd.extend(["-c:a", "copy"])
        cmd.append(output_path)
        return {"cmd_list": cmd, "terminal_command": self.cmd_list_to_string(cmd), "operation": "scale_aspect", "mode": mode, "input": input_path, "output": output_path}

    def compute_precision_speed_factor(
        self,
        source_duration_s: float,
        target_duration_s: float,
        audio_beats_s: Optional[List[float]] = None
    ) -> float:
        """
        Returns exact speed_factor to hit target_duration_s, optionally snapped so the 
        resulting clip end lands on the nearest audio beat rather than an arbitrary timestamp.
        """
        if target_duration_s <= 0:
            return 1.0
        raw_factor = source_duration_s / target_duration_s
        if audio_beats_s:
            candidate_durations = [b for b in audio_beats_s if b <= source_duration_s]
            if candidate_durations:
                nearest_beat = min(candidate_durations, key=lambda b: abs(b - target_duration_s))
                if nearest_beat > 0:
                    raw_factor = source_duration_s / nearest_beat
        return round(max(0.5, min(4.0, raw_factor)), 4)

    def _build_chained_atempo_filter(self, speed_factor: float) -> str:
        """Chain FFmpeg atempo filters if speed_factor exceeds standard 0.5 - 2.0 bounds."""
        factors = []
        rem = speed_factor
        while rem > 2.0:
            factors.append(2.0)
            rem /= 2.0
        while rem < 0.5:
            factors.append(0.5)
            rem /= 0.5
        factors.append(round(rem, 4))
        filter_str = ",".join(f"atempo={f:.4f}" for f in factors if abs(f - 1.0) > 1e-4)
        return filter_str or "atempo=1.0"

    def build_speed_command(self, input_path, output_path, speed_factor=1.0, encoding_cfg=None):
        if speed_factor <= 0:
            raise ValueError("speed_factor must be positive")
        pts_factor = 1.0 / speed_factor
        has_audio = self._has_audio_stream(input_path)
        cmd = [self.ffmpeg_path, "-y", "-i", input_path]
        if has_audio:
            atempo_str = self._build_chained_atempo_filter(speed_factor)
            filter_complex = f"[0:v]setpts={pts_factor:.4f}*PTS[vout];[0:a]{atempo_str}[aout]"
            cmd.extend(["-filter_complex", filter_complex, "-map", "[vout]", "-map", "[aout]"])
        else:
            cmd.extend(["-filter:v", f"setpts={pts_factor:.4f}*PTS"])
        cmd.extend(self._get_encoder_flags(encoding_cfg=encoding_cfg))
        if has_audio:
            cmd.extend(["-c:a", "aac", "-b:a", "192k"])
        cmd.append(output_path)
        return {"cmd_list": cmd, "terminal_command": self.cmd_list_to_string(cmd), "operation": "speed_change", "speed_factor": speed_factor, "input": input_path, "output": output_path}

    def build_audio_strip_command(self, input_path, output_path, encoding_cfg=None):
        """Fast stream-copy audio removal — no re-encode. Run before reshape/speed
        ops when BGM will replace the original track, so downstream steps never
        carry (and later mix in) pitch-shifted or jump-cut original audio."""
        cmd = [self.ffmpeg_path, "-y", "-i", input_path, "-c:v", "copy", "-an", output_path]
        return {
            "cmd_list": cmd,
            "terminal_command": self.cmd_list_to_string(cmd),
            "operation": "audio_strip",
            "input": input_path,
            "output": output_path
        }

    def build_watermark_command(self, input_path, output_path, watermark_path, position="top_right", margin_x=20, margin_y=20, scale=0.15, opacity=0.8, encoding_cfg=None):
        pos_map = {
            "top_left": f"x={margin_x}:y={margin_y}", "top_right": f"x=main_w-overlay_w-{margin_x}:y={margin_y}",
            "bottom_left": f"x={margin_x}:y=main_h-overlay_h-{margin_y}", "bottom_right": f"x=main_w-overlay_w-{margin_x}:y=main_h-overlay_h-{margin_y}",
            "center": "x=(main_w-overlay_w)/2:y=(main_h-overlay_h)/2"
        }
        overlay_coords = pos_map.get(position, pos_map["top_right"])
        filter_complex = (
            f"[1:v]scale=iw*{scale}:-1,format=rgba,colorchannelmixer=aa={opacity}[wm];"
            f"[0:v][wm]overlay={overlay_coords}"
        )
        cmd = [self.ffmpeg_path, "-y", "-i", input_path, "-i", watermark_path, "-filter_complex", filter_complex]
        cmd.extend(self._get_encoder_flags(encoding_cfg=encoding_cfg))
        if self._has_audio_stream(input_path):
            cmd.extend(["-c:a", "copy"])
        cmd.append(output_path)
        return {"cmd_list": cmd, "terminal_command": self.cmd_list_to_string(cmd), "operation": "watermark_overlay", "input": input_path, "watermark": watermark_path, "output": output_path}

    def build_delogo_blur_command(self, input_path, output_path, x, y, w, h, band=4, encoding_cfg=None):
        cmd = [self.ffmpeg_path, "-y", "-i", input_path, "-vf", f"delogo=x={x}:y={y}:w={w}:h={h}"]
        cmd.extend(self._get_encoder_flags(encoding_cfg=encoding_cfg))
        if self._has_audio_stream(input_path):
            cmd.extend(["-c:a", "copy"])
        cmd.append(output_path)
        return {"cmd_list": cmd, "terminal_command": self.cmd_list_to_string(cmd), "operation": "delogo_blur", "bounding_box": {"x": x, "y": y, "w": w, "h": h}, "input": input_path, "output": output_path}

    def build_drawtext_command(self, input_path, output_path, text="AMTCE", fontsize=36, fontcolor="white@0.8", position="bottom_center", x=None, y=None, w=None, h=None, enable_box=True, boxcolor="black@0.6", fontfile=None, encoding_cfg=None):
        global _CACHED_FONT_PATH
        if fontfile:
            font_path = fontfile
        elif _CACHED_FONT_PATH and os.path.exists(_CACHED_FONT_PATH):
            font_path = _CACHED_FONT_PATH
        else:
            try:
                from Text_Modules.Font_manager import ensure_montserrat_font
                font_path = ensure_montserrat_font()
                _CACHED_FONT_PATH = font_path
            except Exception:
                font_path = None

        font_arg = f"fontfile='{font_path.replace(os.sep, '/')}':" if (font_path and os.path.exists(font_path)) else ""
        text_arg = text.replace("'", "'\\''")

        if x is not None and y is not None and w is not None and h is not None:
            # 2-Tier Shield: drawbox fill exact (w x h) rectangle + centered drawtext
            drawbox_str = f"drawbox=x={x}:y={y}:w={w}:h={h}:color={boxcolor}:t=fill"
            drawtext_str = f"drawtext={font_arg}text='{text_arg}':fontsize={fontsize}:fontcolor={fontcolor}:x={x}+({w}-tw)/2:y={y}+({h}-th)/2"
            vf_filter = f"{drawbox_str},{drawtext_str}"
        else:
            if x is not None and y is not None:
                coords = f"x={x}:y={y}"
            else:
                pos_map = {
                    "top_left": "x=50:y=50",
                    "top_right": "x=w-tw-50:y=50",
                    "bottom_left": "x=50:y=h-th-100",
                    "bottom_right": "x=w-tw-50:y=h-th-100",
                    "bottom_center": "x=(w-tw)/2:y=h-th-120",
                    "center": "x=(w-tw)/2:y=(h-th)/2"
                }
                coords = pos_map.get(position, pos_map["bottom_center"])

            box_arg = f":box=1:boxcolor={boxcolor}:boxborderw=6" if enable_box else ""
            vf_filter = f"drawtext={font_arg}text='{text_arg}':fontsize={fontsize}:fontcolor={fontcolor}{box_arg}:{coords}"

        cmd = [self.ffmpeg_path, "-y", "-i", input_path, "-vf", vf_filter]
        cmd.extend(self._get_encoder_flags(encoding_cfg=encoding_cfg))
        if self._has_audio_stream(input_path):
            cmd.extend(["-c:a", "copy"])
        cmd.append(output_path)
        return {"cmd_list": cmd, "terminal_command": self.cmd_list_to_string(cmd), "operation": "drawtext", "text": text, "input": input_path, "output": output_path}

    def build_audio_ducking_mix_command(self, video_input, voiceover_input, music_input, output_path, music_volume=0.2, ducking_threshold=0.1, encoding_cfg=None):
        filter_complex = (
            f"[2:a]volume={music_volume}[music];"
            f"[music][1:a]sidechaincompress=threshold={ducking_threshold}:ratio=4:attack=150:release=300[ducked_music];"
            f"[1:a][ducked_music]amix=inputs=2:duration=first:dropout_transition=2[aout]"
        )
        cmd = [self.ffmpeg_path, "-y", "-i", video_input, "-i", voiceover_input, "-i", music_input,
               "-filter_complex", filter_complex, "-map", "0:v", "-map", "[aout]", "-shortest"]
        cmd.extend(self._get_encoder_flags(encoding_cfg=encoding_cfg))
        cmd.extend(["-c:a", "aac", "-b:a", "192k", output_path])
        return {"cmd_list": cmd, "terminal_command": self.cmd_list_to_string(cmd), "operation": "audio_ducking_mix",
                "video_input": video_input, "voiceover_input": voiceover_input, "music_input": music_input, "output": output_path}

    def build_bgm_mix_command(self, video_input: str, music_input: str, output_path: str, music_volume: float = 0.5, video_volume: float = 0.3, encoding_cfg=None):
        """
        Mixes external BGM audio track with video audio.
        If video has audio, blends BGM and original audio. If video has no audio, plays BGM directly.
        Always includes -shortest to prevent final frame freezing on duration mismatch.
        """
        has_video_audio = self._has_audio_stream(video_input)
        if has_video_audio:
            filter_complex = (
                f"[0:a]volume={video_volume}[aorig];"
                f"[1:a]volume={music_volume}[abgm];"
                f"[aorig][abgm]amix=inputs=2:duration=first:dropout_transition=2[aout]"
            )
            cmd = [self.ffmpeg_path, "-y", "-i", video_input, "-i", music_input,
                   "-filter_complex", filter_complex, "-map", "0:v", "-map", "[aout]", "-shortest"]
        else:
            filter_complex = f"[1:a]volume={music_volume}[aout]"
            cmd = [self.ffmpeg_path, "-y", "-i", video_input, "-i", music_input,
                   "-filter_complex", filter_complex, "-map", "0:v", "-map", "[aout]", "-shortest"]
        cmd.extend(self._get_encoder_flags(encoding_cfg=encoding_cfg))
        cmd.extend(["-c:a", "aac", "-b:a", "192k", output_path])
        return {
            "cmd_list": cmd,
            "terminal_command": self.cmd_list_to_string(cmd),
            "operation": "bgm_mix",
            "video_input": video_input,
            "music_input": music_input,
            "output": output_path
        }

    def build_subtitle_burnin_command(self, input_path, subtitle_path, output_path, force_style=None, encoding_cfg=None):
        force_style = force_style or "FontSize=20,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1"
        sub_path_escaped = subtitle_path.replace("\\", "/").replace(":", "\\:")
        sub_filter = (f"ass='{sub_path_escaped}'" if subtitle_path.endswith(".ass")
                      else f"subtitles='{sub_path_escaped}':force_style='{force_style}'")
        cmd = [self.ffmpeg_path, "-y", "-i", input_path, "-vf", sub_filter]
        cmd.extend(self._get_encoder_flags(encoding_cfg=encoding_cfg))
        cmd.append(output_path)
        return {"cmd_list": cmd, "terminal_command": self.cmd_list_to_string(cmd), "operation": "subtitle_burnin", "input": input_path, "subtitle": subtitle_path, "output": output_path}

    def build_transition_xfade_command(self, shot_inputs, output_path, gap_transitions, encoding_cfg=None):
        if len(shot_inputs) < 2:
            raise ValueError("xfade transition requires at least 2 input shots")
        inputs_cmd = []
        for p in shot_inputs:
            inputs_cmd.extend(["-i", p])
        filter_parts = []
        curr_v_label = "[0:v]"
        curr_a_label = "[0:a]"
        for idx, trans in enumerate(gap_transitions):
            next_idx = idx + 1
            if next_idx >= len(shot_inputs):
                break
            t_type = trans.get("transition_type", "fade")
            t_dur = trans.get("duration", 0.5)
            t_off = trans.get("offset", 3.0)
            next_v_in = f"[{next_idx}:v]"
            next_a_in = f"[{next_idx}:a]"
            out_v_label = f"[vtrans_{idx}]" if next_idx < len(shot_inputs) - 1 else "[vout]"
            out_a_label = f"[atrans_{idx}]" if next_idx < len(shot_inputs) - 1 else "[aout]"
            filter_parts.append(f"{curr_v_label}{next_v_in}xfade=transition={t_type}:duration={t_dur}:offset={t_off:.2f}{out_v_label}")
            filter_parts.append(f"{curr_a_label}{next_a_in}acrossfade=d={t_dur}{out_a_label}")
            curr_v_label = out_v_label
            curr_a_label = out_a_label
        cmd = [self.ffmpeg_path, "-y"] + inputs_cmd + [
            "-filter_complex", ";".join(filter_parts), "-map", "[vout]", "-map", "[aout]"
        ]
        cmd.extend(self._get_encoder_flags(encoding_cfg=encoding_cfg))
        cmd.extend(["-c:a", "aac", "-b:a", "192k", output_path])
        return {"cmd_list": cmd, "terminal_command": self.cmd_list_to_string(cmd), "operation": "transition_xfade",
                "shot_count": len(shot_inputs), "gaps_processed": len(gap_transitions), "inputs": shot_inputs, "output": output_path}

    def build_concat_command(self, input_paths: List[str], output_path: str, encoding_cfg=None):
        """Builds an FFmpeg filter_complex concat command to merge multiple video clips into a single file."""
        if not input_paths:
            raise ValueError("input_paths list cannot be empty for concat")
        if len(input_paths) == 1:
            cmd = [self.ffmpeg_path, "-y", "-i", input_paths[0]] + self._get_encoder_flags(encoding_cfg=encoding_cfg) + [output_path]
            return {"cmd_list": cmd, "terminal_command": self.cmd_list_to_string(cmd), "operation": "concat", "input": input_paths[0], "output": output_path}

        inputs_cmd = []
        filter_v = []
        filter_a = []
        has_audio = any(self._has_audio_stream(p) for p in input_paths)

        for idx, p in enumerate(input_paths):
            inputs_cmd.extend(["-i", p])
            filter_v.append(f"[{idx}:v]")
            if has_audio:
                filter_a.append(f"[{idx}:a]")

        if has_audio:
            filter_str = "".join(f"{v}{a}" for v, a in zip(filter_v, filter_a)) + f"concat=n={len(input_paths)}:v=1:a=1[vout][aout]"
            cmd = [self.ffmpeg_path, "-y"] + inputs_cmd + ["-filter_complex", filter_str, "-map", "[vout]", "-map", "[aout]"]
        else:
            filter_str = "".join(filter_v) + f"concat=n={len(input_paths)}:v=1:a=0[vout]"
            cmd = [self.ffmpeg_path, "-y"] + inputs_cmd + ["-filter_complex", filter_str, "-map", "[vout]"]

        cmd.extend(self._get_encoder_flags(encoding_cfg=encoding_cfg))
        if has_audio:
            cmd.extend(["-c:a", "aac", "-b:a", "192k"])
        cmd.append(output_path)
        return {"cmd_list": cmd, "terminal_command": self.cmd_list_to_string(cmd), "operation": "concat", "inputs": input_paths, "output": output_path}

    def build_single_pass_filtergraph(
        self,
        input_path: str,
        output_path: str,
        micro_shots: Optional[List[Dict[str, Any]]] = None,
        bgm_path: Optional[str] = None,
        target_width: int = 1080,
        target_height: int = 1920,
        watermark_boxes: Optional[List[Dict[str, Any]]] = None,
        brand_text: Optional[str] = None,
        brand_fontsize: int = 32,
        brand_fontcolor: str = "white@0.85",
        brand_fontfile: Optional[str] = None,
        music_volume: float = 0.5,
        encoding_cfg: Optional[Dict[str, Any]] = None,
        gemini_operations: Optional[List[Dict[str, Any]]] = None,
        extra_inputs: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        🏆 TRUE SINGLE-PASS FFMPEG FILTERGRAPH (AI CREATIVE DIRECTIVE DRIVEN)
        ====================================================================
        Executes Gemini Call 3's creative editing operations in ONE unified filtergraph:
          N × trim → concat → speed_change → optional delogo → scale (blur_pad/crop/pad) → drawtext → [vout]
          bgm audio volume mixing → [aout]
        Encodes to H.264 EXACTLY ONCE — zero generational quality loss.
        """
        cmd = [self.ffmpeg_path, "-y", "-i", input_path]
        bgm_idx = None
        if bgm_path and os.path.exists(bgm_path):
            cmd.extend(["-i", bgm_path])
            bgm_idx = 1

        filter_parts: List[str] = []
        shot_labels: List[str] = []

        # ── Parse Gemini Creative Operations ──────────────────────────────────
        ops = gemini_operations or []
        spd_op = next((op for op in ops if op.get("operation_type") in ("speed_change", "speed_ramp")), None)
        scale_op = next((op for op in ops if op.get("operation_type") == "scale_aspect"), None)
        dt_op = next((op for op in ops if op.get("operation_type") in ("drawtext", "brand_watermark")), None)
        mix_op = next((op for op in ops if op.get("operation_type") in ("bgm_mix", "audio_ducking_mix", "audio_mix")), None)

        is_preserve_input = bool(extra_inputs and extra_inputs.get("preserve_original_audio"))
        video_volume = 0.80 if is_preserve_input else 0.00
        if mix_op:
            if mix_op.get("music_volume") is not None:
                try:
                    music_volume = float(mix_op.get("music_volume"))
                except (TypeError, ValueError):
                    pass
            if mix_op.get("video_volume") is not None:
                try:
                    video_volume = float(mix_op.get("video_volume"))
                except (TypeError, ValueError):
                    pass

        env_brand = (
            os.getenv("BRAND_WATERMARK_TEXT", "").strip()
            or os.getenv("WATERMARK_TEXT", "").strip()
            or os.getenv("BRAND_NAME", "").strip()
        )
        if env_brand:
            brand_text = env_brand
        elif dt_op and dt_op.get("text"):
            brand_text = dt_op.get("text")
        elif not brand_text:
            brand_text = env_brand or None

        # Check if input video has an audio stream AND whether we explicitly preserve original audio
        preserve_orig = False
        if extra_inputs and extra_inputs.get("preserve_original_audio"):
            preserve_orig = True
        elif not bgm_path:
            preserve_orig = True

        has_input_audio = self._has_audio_stream(input_path) and preserve_orig

        # ── Step A: Trim segments ────────────────────────────────────────────────
        shots = micro_shots or []
        audio_shot_labels: List[str] = []
        total_visual_dur = 0.0
        if shots:
            for i, s in enumerate(shots):
                st = float(s.get("start", s.get("start_time", 0.0)))
                en = float(s.get("end", s.get("end_time", st + 3.0)))
                dur = max(0.1, en - st)
                total_visual_dur += dur
                v_label = f"v{i}"
                filter_parts.append(
                    f"[0:v]trim=start={st:.4f}:duration={dur:.4f},setpts=PTS-STARTPTS[{v_label}]"
                )
                shot_labels.append(f"[{v_label}]")
                if has_input_audio:
                    a_label = f"a{i}"
                    filter_parts.append(
                        f"[0:a]atrim=start={st:.4f}:duration={dur:.4f},asetpts=PTS-STARTPTS[{a_label}]"
                    )
                    audio_shot_labels.append(f"[{a_label}]")
        else:
            filter_parts.append("[0:v]setpts=PTS-STARTPTS[v0]")
            shot_labels = ["[v0]"]
            total_visual_dur = 15.0
            if has_input_audio:
                filter_parts.append("[0:a]asetpts=PTS-STARTPTS[a0]")
                audio_shot_labels = ["[a0]"]

        # ── Step B: Concat all shot segments ─────────────────────────────────────
        n = len(shot_labels)
        if n > 1:
            if has_input_audio and len(audio_shot_labels) == n:
                concat_pairs = "".join(f"{v}{a}" for v, a in zip(shot_labels, audio_shot_labels))
                filter_parts.append(f"{concat_pairs}concat=n={n}:v=1:a=1[vc][ac]")
            else:
                concat_inputs = "".join(shot_labels)
                filter_parts.append(f"{concat_inputs}concat=n={n}:v=1:a=0[vc]")
        else:
            filter_parts.append(f"{shot_labels[0]}copy[vc]")
            if has_input_audio and audio_shot_labels:
                filter_parts.append(f"{audio_shot_labels[0]}copy[ac]")

        current_label = "[vc]"


        # ── Step B2: Optional Gemini Speed Ramp / Speed Change ──────────────────
        if spd_op:
            try:
                spd_factor = float(spd_op.get("speed_factor", 1.0))
                if spd_factor > 0 and abs(spd_factor - 1.0) > 0.01:
                    pts_factor = 1.0 / spd_factor
                    next_label = "[vspd]"
                    filter_parts.append(f"{current_label}setpts={pts_factor:.4f}*PTS{next_label}")
                    current_label = next_label
                    logger.info(f"⚡ [SINGLE-PASS] Applied Gemini Speed Ramp: {spd_factor:.2f}x (setpts={pts_factor:.4f})")
            except (ValueError, TypeError):
                pass

        # ── Step C: Optional delogo (erase original watermark bounding box) ──────
        if watermark_boxes:
            for box in watermark_boxes:
                bx = int(box.get("x", 0))
                by = int(box.get("y", 0))
                bw = int(box.get("w", 100))
                bh = int(box.get("h", 50))
                if bw > 0 and bh > 0:
                    next_label = "[vd]"
                    filter_parts.append(f"{current_label}delogo=x={bx}:y={by}:w={bw}:h={bh}{next_label}")
                    current_label = next_label
                    break

        # ── Step D: Scale to 9:16 (Gemini mode: blur_pad, crop, or pad) ────────
        tw, th = target_width, target_height
        scale_mode = str(scale_op.get("mode", "blur_pad")).lower() if scale_op else "blur_pad"

        if scale_mode == "crop":
            filter_parts.append(f"{current_label}scale={tw}:{th}:force_original_aspect_ratio=increase,crop={tw}:{th}[vs]")
        elif scale_mode == "pad":
            filter_parts.append(f"{current_label}scale={tw}:{th}:force_original_aspect_ratio=decrease,pad={tw}:{th}:(ow-iw)/2:(oh-ih)/2:black[vs]")
        else:
            # default: blur_pad
            bg_src = current_label
            filter_parts.append(
                f"{bg_src}split[bg_in][fg_in];"
                f"[bg_in]scale={tw}:{th}:force_original_aspect_ratio=increase,"
                f"crop={tw}:{th},boxblur=20:10[bg];"
                f"[fg_in]scale={tw}:{th}:force_original_aspect_ratio=decrease[fg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2[vs]"
            )
        current_label = "[vs]"


        # ── Step E: Optional drawtext brand watermark (MASKING INPAINT RESIDUE) ──
        brand_overlay_data = None
        if brand_text:
            font_arg = ""
            if brand_fontfile and os.path.exists(brand_fontfile):
                safe_path = brand_fontfile.replace("\\", "/").replace(":", "\\:")
                font_arg = f"fontfile='{safe_path}':"
            else:
                try:
                    from Text_Modules.Font_manager import ensure_montserrat_font
                    fp = ensure_montserrat_font()
                    if fp:
                        safe_path = fp.replace("\\", "/").replace(":", "\\:")
                        font_arg = f"fontfile='{safe_path}':"
                except Exception:
                    pass

            safe_text = brand_text.replace("'", "\\'")

            # Position over detected watermark box if coordinates available
            if watermark_boxes:
                box0 = watermark_boxes[0]
                op_vecs = box0.get("opencv_vectors", {})

                # Use OpenCV's effective expanded mask bounding box if available
                x_raw = int(op_vecs.get("effective_x") if op_vecs.get("effective_x") is not None else box0.get("x", 0))
                y_raw = int(op_vecs.get("effective_y") if op_vecs.get("effective_y") is not None else box0.get("y", 0))
                w_raw = int(op_vecs.get("effective_w") if op_vecs.get("effective_w") is not None else box0.get("w", 300))
                h_raw = int(op_vecs.get("effective_h") if op_vecs.get("effective_h") is not None else box0.get("h", 50))

                # Transform raw video coordinates to 9:16 vertical frame (1080x1920)
                orig_w = int(box0.get("video_width") or box0.get("orig_w") or 1080)
                orig_h = int(box0.get("video_height") or box0.get("orig_h") or 1920)

                scale_w = tw / float(orig_w) if orig_w > 0 else 1.0
                scale_h = th / float(orig_h) if orig_h > 0 else 1.0
                scale_fg = min(scale_w, scale_h)

                fg_w = int(orig_w * scale_fg)
                fg_h = int(orig_h * scale_fg)
                dx = (tw - fg_w) // 2
                dy = (th - fg_h) // 2

                tx = int(dx + x_raw * scale_fg)
                ty = int(dy + y_raw * scale_fg)
                tw_box = max(180, int(w_raw * scale_fg))
                th_box = max(40, int(h_raw * scale_fg))

                # Clamp to 9:16 frame bounds safely
                tx = max(10, min(tw - tw_box - 10, tx))
                ty = max(10, min(th - th_box - 10, ty))

                bg_texture = str(op_vecs.get("background_texture") or box0.get("semantic_vectors", {}).get("background_texture", "")).lower()
                boxcolor = "black@0.75" if any(k in bg_texture for k in ["complex", "hair", "foliage", "busy"]) else "black@0.60"
                
                # Proportional font sizing constrained by both box height and width
                max_text_len = max(1, len(brand_text))
                max_fontsize_by_w = int((tw_box * 0.85) / (max_text_len * 0.58))
                auto_fontsize = max(16, min(42, int(th_box * 0.60), max_fontsize_by_w))

                # ── 2-TIER MATHEMATICAL SHIELD ──
                # Tier 1: drawbox fills the EXACT (tw_box × th_box) inpaint rectangle so 0% residue is exposed
                drawbox_node = "[vbox]"
                drawbox_filter = f"{current_label}drawbox=x={tx}:y={ty}:w={tw_box}:h={th_box}:color={boxcolor}:t=fill{drawbox_node}"
                filter_parts.append(drawbox_filter)
                current_label = drawbox_node

                # Tier 2: drawtext mathematically centered inside the exact plate
                pos_expr = f"x={tx}+({tw_box}-tw)/2:y={ty}+({th_box}-th)/2"
                drawtext_filter = (
                    f"{current_label}drawtext={font_arg}"
                    f"text='{safe_text}':"
                    f"fontsize={auto_fontsize}:"
                    f"fontcolor={brand_fontcolor}:"
                    f"{pos_expr}[vout]"
                )
                filter_parts.append(drawtext_filter)
                current_label = "[vout]"

                brand_overlay_data = {"text": brand_text, "x": tx, "y": ty, "w": tw_box, "h": th_box, "opencv_vectors": op_vecs}
                logger.info(
                    f"🎯 [2-TIER BRAND MASK SHIELD] Inpaint footprint ({x_raw},{y_raw},{w_raw},{h_raw}) → "
                    f"9:16 drawbox shield ({tx},{ty},{tw_box},{th_box}) [fill={boxcolor}] + centered text [fontsize={auto_fontsize}]"
                )

            else:
                auto_fontsize = brand_fontsize
                boxcolor = "black@0.45"
                pos_expr = "x=(w-tw)/2:y=h-th-120"
                brand_overlay_data = {"text": brand_text, "x": (tw - 300) // 2, "y": th - 160, "w": 320, "h": 70}

                drawtext_filter = (
                    f"{current_label}drawtext={font_arg}"
                    f"text='{safe_text}':"
                    f"fontsize={auto_fontsize}:"
                    f"fontcolor={brand_fontcolor}:"
                    f"{pos_expr}:"
                    f"box=1:boxcolor={boxcolor}:boxborderw=6[vout]"
                )
                filter_parts.append(drawtext_filter)
                current_label = "[vout]"
        else:
            # No brand text — rename final node
            filter_parts.append(f"{current_label}copy[vout]")
            current_label = "[vout]"

        # ── Step F: Audio Assembly ────────────────────────────────────────────────
        has_bgm = bgm_idx is not None
        has_audio = has_bgm or has_input_audio

        if has_bgm and has_input_audio and video_volume > 0.01:
            filter_parts.append(
                f"[ac]volume={video_volume:.2f}[ac_v];"
                f"[{bgm_idx}:a]atrim=start=0:duration={total_visual_dur:.4f},asetpts=PTS-STARTPTS,volume={music_volume:.2f}[bgm_v];"
                f"[ac_v][bgm_v]amix=inputs=2:duration=first[aout]"
            )
        elif has_bgm:
            filter_parts.append(
                f"[{bgm_idx}:a]atrim=start=0:duration={total_visual_dur:.4f},asetpts=PTS-STARTPTS,volume={music_volume:.2f}[aout]"
            )
        elif has_input_audio:
            filter_parts.append(f"[ac]volume=1.00[aout]")

        # ── Assemble full filtergraph ─────────────────────────────────────────────
        filtergraph = ";".join(filter_parts)
        cmd.extend(["-filter_complex", filtergraph])
        cmd.extend(["-map", "[vout]"])
        if has_audio:
            cmd.extend(["-map", "[aout]", "-shortest"])

        # ── Encoder flags ─────────────────────────────────────────────────────────
        cmd.extend(self._get_encoder_flags(encoding_cfg=encoding_cfg))
        if has_audio:
            cmd.extend(["-c:a", "aac", "-b:a", "192k"])
        cmd.append(output_path)

        terminal_command = self.cmd_list_to_string(cmd)
        return {
            "cmd_list": cmd,
            "terminal_command": terminal_command,
            "operation": "single_pass",
            "input": input_path,
            "output": output_path,
            "brand_overlay": brand_overlay_data,
        }




# =====================================================================

FFMPEG_SYSTEM_PROMPT = """You are a World-Class Video Editor and Senior FFmpeg Automation Expert with 15+ years of viral content creation experience.

YOUR ROLE:
- You will receive a detailed understanding of video content, Optical Flow motion metrics, and Audio Beat grid timestamps.
- You will receive REFERENCE editing patterns from ChromaDB RAG. These are EXAMPLES ONLY — a creative mood board. Adapt them. Do NOT copy blindly.
- Transform the source clip into a 9:16 vertical viral short reel.
- Align trim points to the provided Audio Beat grid timestamps whenever possible.
- Avoid placing hard transitions where Motion Vector angles conflict (direction clash).

RULES:
1. Respond with ONLY valid JSON — no commentary outside the JSON block.
2. The rag_examples/reference patterns are SAMPLES. Refine them based on real video metrics.
3. Include a 'creative_rationale' field explaining HOW you adapted the references to this video.
4. Fill ALL creative gaps with expert judgment.
5. CRITICAL: Do NOT output 'transition' or 'xfade' operations when editing a single input video clip. Transition operations require multiple separate input shot files. For single-video edits, use operations like 'trim', 'scale_aspect', 'speed_change', 'speed_ramp', 'delogo_blur', 'audio_ducking_mix'.
6. DURATION LOCK — CRITICAL: Any operation that changes video length (trim, speed_change, speed_ramp) MUST appear in the operations array BEFORE any audio-mixing operation (bgm_mix, audio_ducking_mix, audio_mix). The final rendered video's audio track must never exceed the final video track's duration — this causes the last frame to freeze while audio continues. If you select a BGM track longer than the target visual duration, that is expected and correct: it will be truncated to match, not the other way around.
7. MULTI-TRIM RHYTHM RULE: When an RTB BEAT-SNAPPED SHOT PLAN is provided in the prompt, output MULTIPLE 'trim' operations (one for each requested beat window), followed by a SINGLE 'concat' operation to join them in sequence. This is the REQUIRED approach for beat-driven jump-cut editing. Example: trim(0.0->2.5), trim(4.7->7.2), trim(9.4->12.0), concat, scale_aspect, bgm_mix. Only use a single trim operation if no RTB Shot Plan is present.

FEW-SHOT EXAMPLE:
{
  "editing_intent": "Convert to 9:16 short and align cuts to beat 2.70s.",
  "creative_rationale": "Adapted RAG pattern to match cinematic mood. Aligned trim end to beat grid timestamp 2.70s for emotional rhythm.",
  "operations": [
    {"operation_type": "delogo_blur", "x": 10, "y": 10, "w": 180, "h": 60},
    {"operation_type": "scale_aspect", "target_width": 1080, "target_height": 1920, "mode": "blur_pad"},
    {"operation_type": "trim", "start_time": 0.0, "end_time": 2.70}
  ],
  "global_encoding": {"codec": "libx264", "preset": "veryfast", "crf": 18}
}
"""

GEMINI_FFMPEG_SCHEMA = {
    "type": "object",
    "properties": {
        "editing_intent": {"type": "string"},
        "creative_rationale": {"type": "string"},
        "operations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "operation_type": {
                        "type": "string",
                        "enum": ["trim", "scale_aspect", "speed_change", "speed_ramp", "watermark_overlay",
                                 "delogo_blur", "audio_ducking_mix", "audio_ducking", "bgm_mix", "audio_mix", "subtitle_burnin", "concat", "transition", "xfade",
                                 "speed", "slowmo", "delogo", "crop", "drawtext", "brand_watermark", "text_watermark"]
                    },
                    "gap_index": {"type": "integer", "minimum": 0},
                    "from_shot_index": {"type": "integer", "minimum": 0},
                    "to_shot_index": {"type": "integer", "minimum": 0},
                    "transition_type": {
                        "type": "string",
                        "enum": ["fade", "wipeleft", "wiperight", "slideleft", "slideright",
                                 "zoomin", "dissolve", "circlecrop", "pixelize", "slideup", "slidedown"]
                    },
                    "transition_duration": {"type": "number", "minimum": 0.1, "maximum": 3.0},
                    "offset": {"type": "number", "minimum": 0.0},
                    "start_time": {"type": "number", "minimum": 0.0},
                    "end_time": {"type": "number", "minimum": 0.0},
                    "duration": {"type": "number", "minimum": 0.0},
                    "target_width": {"type": "integer", "minimum": 1},
                    "target_height": {"type": "integer", "minimum": 1},
                    "mode": {"type": "string", "enum": ["crop", "pad", "blur_pad", "stretch", "crop_cover", "cover", "crop_fill", "fill"]},
                    "speed_factor": {"type": "number", "exclusiveMinimum": 0.0},
                    "x": {"type": "integer", "minimum": 0},
                    "y": {"type": "integer", "minimum": 0},
                    "w": {"type": "integer", "minimum": 1},
                    "h": {"type": "integer", "minimum": 1},
                    "music_volume": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                    "ducking_threshold": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                    "subtitle_file": {"type": "string"},
                    "text": {"type": "string"},
                    "fontcolor": {"type": "string"},
                    "fontsize": {"type": "integer"},
                    "position": {"type": "string"},
                    "enable_box": {"type": "boolean"},
                    "boxcolor": {"type": "string"}
                },
                "required": ["operation_type"]
            }
        },
        "global_encoding": {
            "type": "object",
            "properties": {
                "codec": {"type": "string"},
                "preset": {"type": "string"},
                "crf": {"type": "integer", "minimum": 0, "maximum": 51}
            }
        },
        "raw_ffmpeg_command_suggestion": {"type": "string"}
    },
    "required": ["editing_intent", "operations"]
}

# =====================================================================
# 4. MULTI-PASS REFINEMENT LOOP
# =====================================================================

class RefinementLoop:
    """
    Evaluates rendered output against intended video context score.
    If quality score < 0.75, triggers a Pass 2 re-manipulation call to Gemini.
    """

    def __init__(self, engine: 'GeminiFFmpegEngine'):
        self.engine = engine

    def score_execution(self, intended_context: Dict[str, Any], synthesis_res: Dict[str, Any]) -> Dict[str, Any]:
        """
        Computes dynamic 0.0 - 1.0 quality score for synthesized plan.
        Returns dict containing score, breakdown, failed_criteria, and passed_criteria.
        """
        ops = synthesis_res.get("command_steps", [])
        failed_criteria: List[str] = []
        passed_criteria: List[str] = []
        breakdown: Dict[str, float] = {}

        if not ops:
            return {
                "score": 0.40,
                "breakdown": breakdown,
                "failed_criteria": ["Empty synthesized plan: no valid FFmpeg command steps generated."],
                "passed_criteria": passed_criteria
            }

        total_points = 0.0
        max_possible = 0.0

        # 1. Aspect Ratio Scaling Coverage (Target 9:16)
        max_possible += 25.0
        has_scale = any(s.get("operation") == "scale_aspect" for s in ops)
        rec_aspect = intended_context.get("recommended_aspect", "9:16")
        if has_scale:
            total_points += 25.0
            breakdown["aspect_ratio"] = 25.0
            passed_criteria.append("Included scale_aspect operation for 9:16 vertical short formatting.")
        elif rec_aspect != "9:16":
            total_points += 15.0
            breakdown["aspect_ratio"] = 15.0
        else:
            failed_criteria.append("Missing scale_aspect operation to reformat video to 9:16 vertical aspect ratio.")

        # 2. Watermark / Delogo Coverage
        problem_areas = intended_context.get("problem_areas", [])
        has_watermark_problem = any("watermark" in str(p).lower() for p in problem_areas)
        has_delogo_op = any(s.get("operation") == "delogo_blur" for s in ops)
        if has_watermark_problem:
            max_possible += 25.0
            if has_delogo_op:
                delogo_step = next(s for s in ops if s.get("operation") == "delogo_blur")
                bbox = delogo_step.get("bounding_box", {})
                if bbox.get("w", 0) > 0 and bbox.get("h", 0) > 0:
                    total_points += 25.0
                    breakdown["watermark_delogo"] = 25.0
                    passed_criteria.append("Included delogo_blur operation with valid bounding box coordinates for detected watermark.")
                else:
                    total_points += 10.0
                    breakdown["watermark_delogo"] = 10.0
                    failed_criteria.append("delogo_blur operation missing valid non-zero bounding box coordinates (w, h).")
            else:
                failed_criteria.append("Watermark detected in forensic context but plan is missing delogo_blur operation.")
        else:
            if has_delogo_op:
                max_possible += 15.0
                total_points += 15.0
                breakdown["watermark_delogo"] = 15.0

        # 3. Pacing & Beat Synchronization Alignment
        max_possible += 20.0
        has_speed = any(s.get("operation") in ("speed_change", "speed_ramp") for s in ops)
        has_trim  = any(s.get("operation") in ("trim", "concat") for s in ops)
        beats = intended_context.get("audio_beats_s", [])
        if has_speed or (has_trim and beats):
            total_points += 20.0
            breakdown["beat_alignment"] = 20.0
            passed_criteria.append("Aligned trim/speed operations to audio beat grid timestamps.")
        elif has_trim:
            total_points += 12.0
            breakdown["beat_alignment"] = 12.0
        else:
            failed_criteria.append("Missing trim or speed_change operation to align clip length with target audio beats.")

        # 4. Audio Ducking & BGM Integration
        audio_inf = intended_context.get("audio_inference", "unknown")
        has_ducking = any(s.get("operation") in ("audio_ducking_mix", "audio_ducking") for s in ops)
        has_bgm_blend = any(s.get("operation") in ("bgm_mix", "audio_mix") for s in ops)
        is_speech = any(k in str(audio_inf).lower() for k in ("voiceover", "speech", "dialogue", "talk", "on_camera"))

        if is_speech:
            max_possible += 15.0
            if has_ducking:
                duck_step = next((s for s in ops if s.get("operation") in ("audio_ducking_mix", "audio_ducking")), {})
                vo_in = duck_step.get("voiceover_input")
                if vo_in and os.path.exists(str(vo_in)):
                    total_points += 15.0
                    breakdown["audio_mix"] = 15.0
                    passed_criteria.append("Included audio_ducking_mix operation with verified voiceover audio track.")
                else:
                    total_points += 7.5
                    breakdown["audio_mix"] = 7.5
                    failed_criteria.append("audio_ducking_mix requested for voiceover but voiceover audio file is missing on disk.")
            elif has_bgm_blend:
                total_points += 15.0
                breakdown["audio_mix"] = 15.0
                passed_criteria.append("Included bgm_mix static audio blend preserving original voice/dialogue track.")
            else:
                total_points += 5.0
                breakdown["audio_mix"] = 5.0
                failed_criteria.append("Speech/voiceover detected in audio context but plan missing audio mixing operation.")
        else:
            if has_ducking or has_bgm_blend:
                max_possible += 10.0
                total_points += 10.0
                breakdown["audio_mix"] = 10.0

        # 5. Step Execution Validity
        max_possible += 15.0
        valid_ops = [s for s in ops if s.get("operation")]
        total_points += (len(valid_ops) / len(ops)) * 15.0
        breakdown["execution_validity"] = (len(valid_ops) / len(ops)) * 15.0

        # 6. Duration-Lock Sequencing (penalize if audio op precedes a duration-changing op)
        max_possible += 15.0
        op_types_in_order = [s.get("operation") for s in ops]
        duration_changers = {"trim", "speed_change", "speed_ramp"}
        audio_ops = {"bgm_mix", "audio_mix", "audio_ducking_mix", "audio_ducking"}
        last_duration_change_idx = max([i for i, t in enumerate(op_types_in_order) if t in duration_changers], default=-1)
        first_audio_op_idx = min([i for i, t in enumerate(op_types_in_order) if t in audio_ops], default=999)
        if last_duration_change_idx < first_audio_op_idx:
            total_points += 15.0
            breakdown["duration_sequencing"] = 15.0
            passed_criteria.append("Valid duration-lock sequencing: all length-changing operations precede audio mixing.")
        else:
            total_points += 2.0  # sequencing violation, real freeze/drift risk
            breakdown["duration_sequencing"] = 2.0
            failed_criteria.append("CRITICAL DURATION-LOCK VIOLATION: Audio-mixing operation appears BEFORE length-changing operations (trim/speed_change), risking last-frame freeze.")

        score = total_points / max(1.0, max_possible)
        efficiency_bonus = min(0.05, 0.01 * len(ops))
        final_score = round(max(0.40, min(0.98, score + efficiency_bonus)), 2)

        return {
            "score": final_score,
            "breakdown": breakdown,
            "failed_criteria": failed_criteria,
            "passed_criteria": passed_criteria
        }


# =====================================================================
# 5. GEMINI SYNTHESIS ENGINE & FULL END-TO-END PIPELINE
# =====================================================================

class GeminiFFmpegEngine:
    """
    Engineers prompts, retrieves ChromaDB RAG mood boards, queries Gemini Vision/LLM,
    synthesizes schema-validated FFmpeg terminal commands, and runs a 2-pass RefinementLoop.
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.cmd_generator = FFmpegCommandGenerator()
        self.context_extractor = VideoContextExtractor(sample_fps=1.0, target_width=720)
        self.refinement_loop = RefinementLoop(self)

    def fetch_rag_reference_examples(self, query: str, profile: Optional[Dict] = None) -> List[Dict]:
        if not _HAS_RAG:
            return []
        try:
            collection = get_chroma_collection()
            if collection and ensure_collection_ready(collection):
                profile = profile or {"energy": "medium", "pace": "moderate"}
                return get_top_patterns(collection, query, profile, k=3)
        except Exception as e:
            logger.warning(f"[RAG] Failed to load reference examples: {e}")
        return []

    def validate_schema(self, data: Dict[str, Any]) -> bool:
        if _HAS_JSONSCHEMA:
            try:
                jsonschema.validate(instance=data, schema=GEMINI_FFMPEG_SCHEMA)
                return True
            except jsonschema.ValidationError as err:
                logger.error(f"Schema validation failed: {err.message}")
                return False
        return isinstance(data, dict) and "editing_intent" in data and isinstance(data.get("operations"), list)

    def sanitize_command_string(self, cmd_str: str) -> str:
        forbidden = [r"-filter_complex_script", r"-vscript", r"file:", r"http://", r"https://", r"ftp://", r";", r"&&", r"\|\|"]
        for p in forbidden:
            if re.search(p, cmd_str, re.IGNORECASE):
                cmd_str = re.sub(p, "", cmd_str, flags=re.IGNORECASE)
        return cmd_str

    def sort_operations(self, operations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return sorted(operations, key=lambda op: OPERATION_PRIORITY.get(op.get("operation_type", ""), 99))

    def generate_prompt_payload(
        self,
        user_request: str,
        video_path: Optional[str] = None,
        audio_path: Optional[str] = None,
        forensic_context: Optional[Dict[str, Any]] = None,
        use_rag_references: bool = True,
        video_context: Optional[Dict[str, Any]] = None,
        lyric_intel: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        if video_context is None and forensic_context and video_path:
            video_context = self.context_extractor.extract_from_forensic(video_path, forensic_context, audio_path=audio_path)
        elif video_context is None and video_path:
            video_context = self.context_extractor.extract(video_path, audio_path=audio_path)
        elif video_context is None:
            video_context = {"_source": "not_provided"}

        # Attempt to load persistent lyric intelligence cache or forensic_context if not provided explicitly
        if not lyric_intel and forensic_context and forensic_context.get("lyric_intel"):
            lyric_intel = forensic_context["lyric_intel"]

        if audio_path and os.path.exists(audio_path) and not lyric_intel:
            try:
                audio_base = os.path.splitext(os.path.basename(audio_path))[0]
                repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                beats_cache = os.path.join(repo_root, "Original_audio", "beats", f"{audio_base}_lyric.json")
                if os.path.exists(beats_cache):
                    with open(beats_cache, "r", encoding="utf-8") as bf:
                        lyric_intel = json.load(bf)
            except Exception as _lce:
                logger.debug(f"[GeminiFFmpegEngine] Lyric cache load fallback: {_lce}")

        rag_examples = self.fetch_rag_reference_examples(
            query=user_request,
            profile={
                "energy": video_context.get("visual_energy", "medium"),
                "pace": video_context.get("pacing_feel", "moderate")
            }
        ) if use_rag_references else []

        prompt_parts = [
            f"### User Editing Request\n{user_request}\n",
        ]
        if "RE-EDIT DIRECTIVE" in user_request.upper() or "RE-EDIT" in user_request.upper():
            req_upper = user_request.upper()
            is_surgical = any(kw in req_upper for kw in ["DON'T CHANGE", "DONT CHANGE", "KEEP", "ONLY", "EXCEPT", "NO CHANGE", "PRESERVE", "SAVE", "WATERMARK", "INPAINT", "DELOGO"])
            if is_surgical:
                prompt_parts.append(
                    "🔒 [SURGICAL EDIT MANDATE — PRESERVE EXISTING EDITS]\n"
                    "The user explicitly requested to PRESERVE existing edits/music.\n"
                    "1. DO NOT change the background music or audio selection.\n"
                    "2. DO NOT change existing clip cuts, speed ramps, or video timing.\n"
                    "3. ONLY apply/adjust the specific requested operation (e.g. delogo_blur / watermark inpainting / position fix).\n"
                    "4. Maintain all other operational parameters exact as they were.\n\n"
                )
            else:
                prompt_parts.append(
                    "⚡ [HIGH-PRIORITY RE-EDIT DIRECTIVE]\n"
                    "The human reviewer requested a re-edit. You MUST:\n"
                    "1. Strictly resolve the human feedback directive above.\n"
                    "2. Only modify cuts or music if explicitly requested or needed to satisfy the directive.\n\n"
                )
        prompt_parts.append(f"### Video Semantic & Motion/Beat Context\n{json.dumps(video_context, indent=2, default=str)}\n")

        if forensic_context:
            clean_forensic = {k: v for k, v in forensic_context.items() if k not in ("watermarks", "watermark_detected")}
            prompt_parts.append(f"### Forensic Context (Scene & Visual Intelligence)\n{json.dumps(clean_forensic, indent=2, default=str)}\n")

        if lyric_intel and isinstance(lyric_intel, dict) and lyric_intel.get("_source") != "fallback":
            lyric_summary = {
                "dominant_emotion": lyric_intel.get("dominant_emotion"),
                "language": lyric_intel.get("language"),
                "has_vocals": lyric_intel.get("has_vocals"),
                "sections": lyric_intel.get("sections", []),
                "emotional_peak_moments": lyric_intel.get("emotional_peak_moments", []),
                "vibe_tags": lyric_intel.get("vibe_tags", []),
                "shot_directives": lyric_intel.get("shot_directives", []),
                "lyrics_sample": lyric_intel.get("lyrics", [])[:6]
            }
            prompt_parts.append(f"### Audio Lyric & Rhythm Context (Hivemind Sync)\n{json.dumps(lyric_summary, indent=2, default=str)}\n")

        if rag_examples:
            prompt_parts.append(f"### Reference Editing Patterns (MOOD BOARD — Adapt)\n{json.dumps(rag_examples, indent=2, default=str)}\n")

        prompt_parts.append("### Output Instructions\nReturn strict JSON plan adhering to GEMINI_FFMPEG_SCHEMA.")

        return {
            "system_instruction": FFMPEG_SYSTEM_PROMPT,
            "user_prompt": "\n".join(prompt_parts),
            "response_schema": GEMINI_FFMPEG_SCHEMA,
            "video_context": video_context,
            "forensic_context": forensic_context,
            "rag_examples_count": len(rag_examples)
        }

    def _normalize_gemini_json(self, gemini_json: Dict[str, Any]) -> Dict[str, Any]:
        """Auto-correct common LLM enum synonyms and filter unsupported operations before schema validation."""
        if not isinstance(gemini_json, dict):
            return gemini_json

        valid_ops = {"trim", "scale_aspect", "speed_change", "speed_ramp", "watermark_overlay", "drawtext", "brand_watermark", "text_watermark",
                     "delogo_blur", "audio_ducking_mix", "audio_ducking", "bgm_mix", "audio_mix", "subtitle_burnin", "concat", "transition", "xfade"}

        ops = gemini_json.get("operations", [])
        clean_ops = []
        if isinstance(ops, list):
            for op in ops:
                if isinstance(op, dict):
                    op_type = op.get("operation_type")
                    if op_type:
                        op_str = str(op_type).lower().strip()
                        if op_str in ("speed", "slowmo", "fastmo", "speedup", "tempo", "speed_adjust"):
                            op["operation_type"] = "speed_change"
                        elif op_str in ("delogo", "logo_blur", "watermark_remove", "blur_watermark"):
                            op["operation_type"] = "delogo_blur"
                        elif op_str in ("scale", "aspect_ratio", "resize", "crop"):
                            op["operation_type"] = "scale_aspect"
                        elif op_str in ("cut", "clip"):
                            op["operation_type"] = "trim"
                        elif op_str in ("drawtext", "brand_watermark", "text_watermark", "brand_logo_text", "watermark_text"):
                            op["operation_type"] = "drawtext"
                        elif op_str in ("fashion_caption", "caption", "text_overlay", "subtitles", "subtitle", "title_caption", "custom_caption", "overlay_text") or op_str.endswith("_caption") or op_str.endswith("_subtitles"):
                            op["operation_type"] = "subtitle_burnin"

                    mode = op.get("mode")
                    if mode:
                        mode_str = str(mode).lower().strip()
                        if mode_str in ("crop_cover", "cover", "crop_fill", "fill"):
                            op["mode"] = "crop"
                        elif mode_str in ("blur_cover", "blur_fill", "blur"):
                            op["mode"] = "blur_pad"
                        elif mode_str in ("fit", "padding"):
                            op["mode"] = "pad"

                    if op.get("operation_type") in valid_ops:
                        clean_ops.append(op)
                    else:
                        logger.warning(f"⚠️ [SCHEMA NORM] Dropping unsupported operation_type '{op.get('operation_type')}' from plan.")

            gemini_json["operations"] = clean_ops
        return gemini_json

    def synthesize_from_gemini_json(
        self,
        gemini_json: Dict[str, Any],
        input_path: str = "input.mp4",
        output_path: str = "output.mp4",
        extra_inputs: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        gemini_json = self._normalize_gemini_json(gemini_json)
        if not self.validate_schema(gemini_json):
            raise ValueError("Gemini JSON response does not match GEMINI_FFMPEG_SCHEMA")

        self.cmd_generator._root_input_has_audio = self.cmd_generator._has_audio_stream(input_path)
        encoding_cfg = gemini_json.get("global_encoding")
        extra_inputs = extra_inputs or {}
        sorted_ops = self.sort_operations(gemini_json.get("operations", []))

        transition_ops = [op for op in sorted_ops if op.get("operation_type") in ("transition", "xfade")]
        standard_ops = [op for op in sorted_ops if op.get("operation_type") not in ("transition", "xfade")]

        trim_ops = [op for op in standard_ops if op.get("operation_type") == "trim"]
        non_trim_ops = [op for op in standard_ops if op.get("operation_type") != "trim"]

        # Auto-inject multi_trim slicing if micro_shots timeline provided or if single long clip
        micro_shots = extra_inputs.get("micro_shots", [])
        if micro_shots and len(trim_ops) <= 1:
            trim_ops = [
                {"operation_type": "trim", "start_time": s["start"], "end_time": s["end"]}
                for s in micro_shots if isinstance(s, dict) and "start" in s and "end" in s
            ]

        grouped_ops = []
        if len(trim_ops) > 1:
            grouped_ops.append({"operation_type": "multi_trim", "trims": trim_ops})
            grouped_ops.extend(non_trim_ops)
        else:
            grouped_ops = standard_ops

        command_steps = []
        run_id = uuid.uuid4().hex[:8]
        current_input = input_path
        out_dir = os.path.dirname(os.path.abspath(output_path))
        out_base = os.path.basename(output_path)

        # ── STRIP ORIGINAL AUDIO EARLY IF A BGM TRACK IS SUPPLIED ──────────────
        strip_original_audio = bool(
            (extra_inputs.get("music") or extra_inputs.get("bgm")) and
            not extra_inputs.get("preserve_original_audio", False)
        )
        if strip_original_audio and self.cmd_generator._root_input_has_audio:
            stripped_out = os.path.join(out_dir, f"step_stripped_{run_id}_{out_base}")
            strip_res = self.cmd_generator.build_audio_strip_command(current_input, stripped_out)
            command_steps.append(strip_res)
            current_input = stripped_out
            self.cmd_generator._root_input_has_audio = False
            self.cmd_generator._audio_stream_cache[current_input] = False
            logger.info("🔇 [AUDIO_STRIP] Stripped original clip audio up front to prevent speed-ramp pitch shifts & mid-sentence jump cuts under BGM.")

        for idx, op in enumerate(grouped_ops):
            op_type = op.get("operation_type")
            is_last = (idx == len(grouped_ops) - 1) and not transition_ops
            step_output = output_path if is_last else os.path.join(out_dir, f"step_{idx}_{run_id}_{out_base}")

            if op_type == "multi_trim":
                sub_trims = op.get("trims", [])
                sub_outputs = []
                # Trim each segment from pre-trim source (current_input)
                for s_idx, t_op in enumerate(sub_trims):
                    sub_out = os.path.join(out_dir, f"step_{idx}_sub_{s_idx}_{run_id}_{out_base}")
                    res_trim = self.cmd_generator.build_trim_command(
                        current_input, sub_out,
                        start_time=t_op.get("start_time", 0.0),
                        end_time=t_op.get("end_time"),
                        duration=t_op.get("duration"),
                        encoding_cfg=encoding_cfg
                    )
                    command_steps.append(res_trim)
                    sub_outputs.append(sub_out)

                # Concatenate all trimmed segments into step_output
                res_concat = self.cmd_generator.build_concat_command(sub_outputs, step_output, encoding_cfg=encoding_cfg)
                command_steps.append(res_concat)

            elif op_type == "trim":
                res = self.cmd_generator.build_trim_command(current_input, step_output,
                    start_time=op.get("start_time", 0.0), end_time=op.get("end_time"), duration=op.get("duration"),
                    encoding_cfg=encoding_cfg)
                command_steps.append(res)
            elif op_type == "scale_aspect":
                res = self.cmd_generator.build_scale_aspect_command(current_input, step_output,
                    target_width=op.get("target_width", 1080), target_height=op.get("target_height", 1920), mode=op.get("mode", "crop"),
                    encoding_cfg=encoding_cfg)
                command_steps.append(res)
            elif op_type in ("speed_change", "speed_ramp"):
                raw_speed = float(op.get("speed_factor", 1.25))
                # Derive precision speed factor snapped to audio beat timestamps if available
                beats = (extra_inputs or {}).get("audio_beats_s", [])
                source_dur = (extra_inputs or {}).get("source_duration_s")
                if not source_dur and os.path.exists(current_input):
                    try:
                        probe_res = subprocess.run(
                            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", current_input],
                            capture_output=True, text=True, timeout=5
                        )
                        source_dur = float(json.loads(probe_res.stdout).get("format", {}).get("duration", 0.0))
                    except Exception:
                        source_dur = None

                if source_dur and source_dur > 0 and raw_speed > 0:
                    target_dur = source_dur / raw_speed
                    speed_factor = self.cmd_generator.compute_precision_speed_factor(
                        source_duration_s=source_dur,
                        target_duration_s=target_dur,
                        audio_beats_s=beats
                    )
                else:
                    speed_factor = raw_speed

                res = self.cmd_generator.build_speed_command(current_input, step_output, speed_factor=speed_factor, encoding_cfg=encoding_cfg)
                command_steps.append(res)
            elif op_type == "watermark_overlay":
                wm_file = op.get("watermark_path") or extra_inputs.get("watermark")
                if wm_file and os.path.exists(wm_file):
                    res = self.cmd_generator.build_watermark_command(
                        current_input, step_output, watermark_path=wm_file,
                        position=op.get("position", "top_right"),
                        scale=op.get("scale", 0.15),
                        opacity=op.get("opacity", 0.8),
                        encoding_cfg=encoding_cfg
                    )
                    command_steps.append(res)
                else:
                    logger.warning(f"⚠️ Watermark overlay requested by plan but watermark image file missing ('{wm_file}'). Skipping watermark step.")
                    continue
            elif op_type == "delogo_blur":
                res = self.cmd_generator.build_delogo_blur_command(current_input, step_output,
                    x=op.get("x", 0), y=op.get("y", 0), w=op.get("w", 100), h=op.get("h", 50), band=op.get("band", 4),
                    encoding_cfg=encoding_cfg)
                command_steps.append(res)
            elif op_type in ("drawtext", "brand_watermark", "text_watermark"):
                wm_boxes = extra_inputs.get("watermark_boxes") or []
                target_x = op.get("x")
                target_y = op.get("y")
                fontsize = op.get("fontsize")
                boxcolor = op.get("boxcolor")

                if target_x is None and target_y is None and wm_boxes and isinstance(wm_boxes[0], dict):
                    first_box = wm_boxes[0]
                    target_x = first_box.get("x")
                    target_y = first_box.get("y")
                    box_h = first_box.get("h", 50)
                    bg_texture = str(first_box.get("background_texture", "")).lower()

                    if not fontsize and box_h > 10:
                        fontsize = max(18, min(48, int(box_h * 0.65)))

                    if not boxcolor:
                        if any(k in bg_texture for k in ["complex", "hair", "foliage", "busy"]):
                            boxcolor = "black@0.65"
                        else:
                            boxcolor = "black@0.35"

                res = self.cmd_generator.build_drawtext_command(
                    current_input, step_output,
                    text=op.get("text") or os.getenv("BRAND_WATERMARK_TEXT", "AMTCE"),
                    fontsize=fontsize or int(os.getenv("BRAND_WATERMARK_SIZE", "36")),
                    fontcolor=op.get("fontcolor", "white@0.8"),
                    position=op.get("position") or os.getenv("BRAND_WATERMARK_POSITION", "bottom_center"),
                    x=target_x,
                    y=target_y,
                    enable_box=op.get("enable_box", True),
                    boxcolor=boxcolor or "black@0.4",
                    fontfile=op.get("fontfile"),
                    encoding_cfg=encoding_cfg
                )
                command_steps.append(res)
            elif op_type == "concat":
                raw_inputs = op.get("inputs")
                if isinstance(raw_inputs, str):
                    input_clips = [raw_inputs]
                elif isinstance(raw_inputs, (list, tuple)):
                    input_clips = raw_inputs
                else:
                    shots = extra_inputs.get("shots")
                    if isinstance(shots, (list, tuple)):
                        input_clips = shots
                    else:
                        input_clips = [current_input]

                valid_clips = [c for c in input_clips if isinstance(c, str) and c]
                if valid_clips:
                    res = self.cmd_generator.build_concat_command(valid_clips, step_output, encoding_cfg=encoding_cfg)
                    command_steps.append(res)
                else:
                    logger.warning("⚠️ Concat operation requested by Gemini plan but input clip list is empty. Skipping concat step.")
                    continue
            elif op_type in ("audio_ducking_mix", "audio_ducking", "bgm_mix", "audio_mix"):
                vo_file = extra_inputs.get("voiceover")
                bgm_file = extra_inputs.get("music") or extra_inputs.get("bgm")
                if vo_file and bgm_file and os.path.exists(vo_file) and os.path.exists(bgm_file):
                    res = self.cmd_generator.build_audio_ducking_mix_command(
                        current_input, vo_file, bgm_file, step_output,
                        music_volume=op.get("music_volume", 0.2), ducking_threshold=op.get("ducking_threshold", 0.1),
                        encoding_cfg=encoding_cfg)
                    command_steps.append(res)
                elif bgm_file and os.path.exists(bgm_file):
                    if extra_inputs.get("preserve_original_audio"):
                        v_vol = 1.0
                        m_vol = 0.20
                    else:
                        v_vol = op.get("video_volume", 0.0)
                        m_vol = op.get("music_volume", 0.50)
                    res = self.cmd_generator.build_bgm_mix_command(
                        current_input, bgm_file, step_output,
                        music_volume=m_vol,
                        video_volume=v_vol,
                        encoding_cfg=encoding_cfg)
                    command_steps.append(res)
                else:
                    logger.warning("Audio mix requested by Gemini but external BGM track not found. Skipping audio mix step.")
                    continue
            elif op_type == "subtitle_burnin":
                sub_file = op.get("subtitle_file") or extra_inputs.get("subtitle", "subtitles.ass")
                if not sub_file or not os.path.exists(sub_file):
                    logger.warning(f"Subtitle burn-in requested ('{op_type}') but subtitle file '{sub_file}' not found on disk. Skipping step.")
                    continue
                res = self.cmd_generator.build_subtitle_burnin_command(current_input,
                    sub_file, step_output,
                    encoding_cfg=encoding_cfg)
                command_steps.append(res)
            else:
                logger.warning(f"⚠️ [SYNTHESIS DISPATCH] Skipping unknown operation_type '{op_type}'.")
                continue

            current_input = step_output

        if transition_ops:
            shot_list = extra_inputs.get("shots", [])
            valid_shots = [s for s in shot_list if os.path.exists(s)]
            if len(valid_shots) >= 2:
                gap_transitions = [{"gap_index": op.get("gap_index", 0), "transition_type": op.get("transition_type", "fade"),
                                     "duration": op.get("transition_duration", 0.5), "offset": op.get("offset", 4.0)}
                                    for op in transition_ops]
                command_steps.append(self.cmd_generator.build_transition_xfade_command(valid_shots, output_path, gap_transitions, encoding_cfg=encoding_cfg))
            else:
                logger.info("ℹ️ Skipping transition_xfade step: single input video mode (no separate shot files provided).")

        raw_suggestion = gemini_json.get("raw_ffmpeg_command_suggestion", "")
        return {
            "editing_intent": gemini_json.get("editing_intent"),
            "creative_rationale": gemini_json.get("creative_rationale", ""),
            "total_steps": len(command_steps),
            "command_steps": command_steps,
            "final_terminal_command": command_steps[-1]["terminal_command"] if command_steps else None,
            "raw_gemini_command_suggestion": self.sanitize_command_string(raw_suggestion) if raw_suggestion else None
        }

    def run_full_pipeline(
        self,
        user_request: str,
        input_video_path: str,
        output_video_path: str,
        audio_path: Optional[str] = None,
        forensic_context: Optional[Dict[str, Any]] = None,
        extra_inputs: Optional[Dict[str, str]] = None,
        dry_run: bool = False
    ) -> Dict[str, Any]:
        """
        END-TO-END AUTONOMOUS PIPELINE:
        1. Extracts video context + Motion Energy Arc + Beat grid
        2. Retrieves ChromaDB RAG mood board
        3. Calls gemini_router.generate() to synthesize JSON editing plan
        4. Validates schema and runs RefinementLoop
        5. Executes FFmpeg terminal pipeline
        """
        logger.info(f"🎬 Starting Full Autonomous Pipeline for request: '{user_request}'")

        # 1. Build Payload
        payload_data = self.generate_prompt_payload(
            user_request=user_request,
            video_path=input_video_path,
            audio_path=audio_path,
            forensic_context=forensic_context
        )

        gemini_plan_json = None

        # 2. Call Gemini Router if available
        if _HAS_ROUTER and gemini_router:
            prompt_payload = [payload_data["system_instruction"], payload_data["user_prompt"]]
            try:
                logger.info("🧠 Requesting Gemini API synthesis via gemini_router...")
                raw_resp = gemini_router.generate(
                    task_type="creative",
                    prompt=prompt_payload,
                    module_name="gemini_ffmpeg_synthesis",
                    gen_config={"temperature": 0.2}
                )
                if raw_resp:
                    clean_text = self.context_extractor._clean_json(raw_resp)
                    gemini_plan_json = json.loads(clean_text)
                    logger.info(f"✅ Received Gemini plan: '{gemini_plan_json.get('editing_intent')}'")
            except Exception as e:
                logger.warning(f"Gemini API call failed: {e}. Falling back to default baseline plan.")

        # Heuristic fallback if Gemini API is offline/dry-run
        if not gemini_plan_json:
            fallback_ops = []
            forensic = forensic_context or {}
            items = forensic.get("items", [])
            has_wm = forensic.get("watermark_detected") or any("watermark" in str(i).lower() or "logo" in str(i).lower() for i in items)
            if has_wm and items:
                first_wm = items[0]
                fallback_ops.append({
                    "operation_type": "delogo_blur",
                    "x": first_wm.get("x", 10),
                    "y": first_wm.get("y", 10),
                    "w": first_wm.get("w", 180),
                    "h": first_wm.get("h", 60)
                })

            fallback_ops.append({
                "operation_type": "scale_aspect",
                "target_width": 1080,
                "target_height": 1920,
                "mode": "blur_pad"
            })

            gemini_plan_json = {
                "editing_intent": f"Fallback Plan for '{user_request}'",
                "creative_rationale": "Default baseline transformation payload.",
                "operations": fallback_ops,
                "global_encoding": {"codec": "libx264", "preset": "veryfast", "crf": 18}
            }

        # 3. Synthesize & Score Plan with Best-of-N Candidate Tracking & Up to 3 Retries
        extra_inputs = extra_inputs or {}
        if audio_path and os.path.exists(audio_path):
            extra_inputs.setdefault("music", audio_path)
            extra_inputs.setdefault("bgm", audio_path)
            extra_inputs.setdefault("audio", audio_path)

        # Thread audio beats and source duration from video_context for beat-snapped speed changes
        v_ctx = payload_data.get("video_context", {})
        if "audio_beats_s" not in extra_inputs and "audio_beats_s" in v_ctx:
            extra_inputs["audio_beats_s"] = v_ctx.get("audio_beats_s", [])
        if "source_duration_s" not in extra_inputs:
            extra_inputs["source_duration_s"] = v_ctx.get("duration_s") or v_ctx.get("source_duration_s")

        # Auto-wire watermark image file from forensic_context if available
        forensic = forensic_context or {}
        if "watermark" not in extra_inputs:
            wm_candidate = forensic.get("watermark_path") or forensic.get("watermark_file") or forensic.get("watermark_image")
            if wm_candidate and os.path.exists(str(wm_candidate)):
                extra_inputs["watermark"] = str(wm_candidate)

        # Auto-wire voiceover audio file from forensic_context if available
        if "voiceover" not in extra_inputs:
            vo_candidate = (forensic.get("voiceover_path") or forensic.get("voiceover_file") or
                            forensic.get("speech_path") or forensic.get("voiceover"))
            if vo_candidate and os.path.exists(str(vo_candidate)):
                extra_inputs["voiceover"] = str(vo_candidate)

        # Auto-wire speech_intelligence / preserve_original_audio from forensic_context
        speech_intel = forensic.get("speech_intelligence") or v_ctx.get("speech_intelligence") or {}
        rec_action = speech_intel.get("recommended_audio_action")
        speech_mode = speech_intel.get("speech_mode")

        if "preserve_original_audio" not in extra_inputs:
            if rec_action == "preserve_voice_duck_bgm" or speech_mode in ("on_camera_dialogue", "voiceover_narration") or forensic.get("intent") == "talking_head":
                extra_inputs["preserve_original_audio"] = True
                logger.info(f"🎙️ [SPEECH INTEL] Speech mode '{speech_mode}' detected -> Enabling preserve_original_audio (static_audio_blend).")
            elif not rec_action and not speech_mode and self.cmd_generator._has_audio_stream(input_video_path):
                # SAFE FALLBACK: If signal is missing/ambiguous, default to preserving voice if input video has an audio stream!
                extra_inputs["preserve_original_audio"] = True
                logger.info("🎙️ [SPEECH INTEL] Ambiguous/missing speech signal -> Safe Fallback: Defaulting to preserve_original_audio.")

        # Automatically inject BGM mix step if external audio track was provided but plan missing audio operation
        if audio_path and os.path.exists(audio_path):
            has_audio_op = any(op.get("operation_type") in ("audio_ducking_mix", "audio_ducking", "bgm_mix", "audio_mix")
                               for op in gemini_plan_json.get("operations", []))
            if not has_audio_op:
                is_preserve = extra_inputs.get("preserve_original_audio", False)
                m_vol = 0.20 if is_preserve else 0.50
                v_vol = 1.00 if is_preserve else 0.30
                logger.info(f"🎶 Auto-injecting BGM mix step for '{os.path.basename(audio_path)}' (video_vol={v_vol}, music_vol={m_vol})")
                gemini_plan_json.setdefault("operations", []).append({
                    "operation_type": "bgm_mix",
                    "music_volume": m_vol,
                    "video_volume": v_vol
                })

        # Automatically inject Brand Watermark drawtext step if BRAND_WATERMARK_TEXT / WATERMARK_TEXT is configured
        env_brand_text = (
            os.getenv("BRAND_WATERMARK_TEXT", "").strip()
            or os.getenv("WATERMARK_TEXT", "").strip()
            or os.getenv("BRAND_NAME", "").strip()
        )
        if env_brand_text:
            has_brand_op = any(
                op.get("operation_type") in ("drawtext", "brand_watermark", "text_watermark")
                for op in gemini_plan_json.get("operations", [])
            )
            if not has_brand_op:
                logger.info(f"🏷️ Auto-injecting brand watermark drawtext step for: '{env_brand_text}'")
                gemini_plan_json.setdefault("operations", []).append({
                    "operation_type": "drawtext",
                    "text": env_brand_text,
                    "position": os.getenv("BRAND_WATERMARK_POSITION", "bottom_center"),
                    "fontsize": int(os.getenv("BRAND_WATERMARK_SIZE", "36")),
                    "fontcolor": "white@0.85",
                    "enable_box": True,
                    "boxcolor": "black@0.4"
                })

        candidates: List[Tuple[float, Dict[str, Any], Dict[str, Any]]] = []

        current_plan_json = gemini_plan_json
        synthesis = self.synthesize_from_gemini_json(current_plan_json, input_video_path, output_video_path, extra_inputs)
        score_info = self.refinement_loop.score_execution(payload_data["video_context"], synthesis)
        current_score = score_info["score"]

        candidates.append((current_score, synthesis, current_plan_json))
        logger.info(f"📊 Initial Plan Quality Score: {current_score:.2f}")

        # Up to 3 Pass 2 Self-Healing Retries if score < 0.75 and Gemini router available
        max_retries = 3
        prev_score = current_score

        if current_score < 0.75 and _HAS_ROUTER and gemini_router:
            for retry_idx in range(1, max_retries + 1):
                failed_reasons = score_info.get("failed_criteria", [])
                if not failed_reasons:
                    break

                logger.warning(f"⚠️ Plan quality score ({current_score:.2f}) < 0.75. Triggering Pass 2 Self-Healing Retry attempt {retry_idx}/{max_retries}...")
                feedback_str = "\n".join(f"- {reason}" for reason in failed_reasons)
                retry_prompt = (
                    f"{payload_data['user_prompt']}\n\n"
                    f"### PASS 2 SELF-CORRECTION DIRECTIVES (Retry attempt {retry_idx}/{max_retries}):\n"
                    f"Your previous JSON edit plan scored {current_score:.2f} (below 0.75 requirement).\n"
                    f"You MUST fix the following specific defect(s):\n{feedback_str}\n"
                    f"Regenerate a clean, valid JSON plan correcting these issues."
                )

                try:
                    retry_resp = gemini_router.generate(
                        task_type="creative",
                        prompt=[payload_data["system_instruction"], retry_prompt],
                        module_name="gemini_ffmpeg_synthesis_refinement",
                        gen_config={"temperature": 0.2}
                    )
                    if retry_resp:
                        clean_retry = self.context_extractor._clean_json(retry_resp)
                        retry_json = json.loads(clean_retry)
                        retry_synth = self.synthesize_from_gemini_json(retry_json, input_video_path, output_video_path, extra_inputs)
                        retry_score_info = self.refinement_loop.score_execution(payload_data["video_context"], retry_synth)
                        new_score = retry_score_info["score"]

                        logger.info(f"📊 Pass 2 Retry attempt {retry_idx} Score: {new_score:.2f}")
                        candidates.append((new_score, retry_synth, retry_json))

                        if new_score >= 0.75:
                            logger.info(f"🎉 Pass 2 Self-Healing Retry succeeded! Quality Score hit target: {new_score:.2f}")
                            score_info = retry_score_info
                            current_plan_json = retry_json
                            current_score = new_score
                            break
                        elif abs(new_score - prev_score) < 0.02:
                            logger.warning(f"⏸️ [CIRCUIT BREAKER] Score delta ({abs(new_score - prev_score):.4f}) < 0.02 — Gemini output converged. Stopping retries.")
                            break

                        prev_score = new_score
                        current_score = new_score
                        score_info = retry_score_info
                        current_plan_json = retry_json
                except Exception as retry_err:
                    logger.warning(f"⚠️ Pass 2 Self-Healing retry attempt {retry_idx} failed: {retry_err}")
                    break

        # Best-of-N Candidate Selection
        best_candidate = max(candidates, key=lambda c: c[0])
        best_score, best_synthesis, best_plan_json = best_candidate
        logger.info(f"🏆 Selected Best-of-N Plan (Score: {best_score:.2f} across {len(candidates)} candidate(s))")

        # 4. Execute Pipeline using best plan
        return self.execute_pipeline(
            gemini_json=best_plan_json,
            input_path=input_video_path,
            output_path=output_video_path,
            extra_inputs=extra_inputs,
            dry_run=dry_run
        )

    def _enforce_duration_lock(self, video_path: str, output_path: str) -> Dict[str, Any]:
        """Post-flight QA gate: verifies final output audio duration does not exceed video duration.
        Probes source video_path if provided to compare transformed output against source duration.
        If drift > 50ms, re-muxes with -shortest as a repair pass. Runs after every pipeline execution."""
        if not os.path.isfile(output_path):
            return {"repaired": False, "reason": "output_file_not_found"}
        try:
            source_v_dur = None
            if video_path and os.path.isfile(video_path):
                try:
                    probe_src = subprocess.run(
                        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                         "-select_streams", "v:0", "-of", "json", video_path],
                        capture_output=True, text=True, timeout=10
                    )
                    source_v_dur = float(json.loads(probe_src.stdout).get("format", {}).get("duration", 0.0))
                except Exception:
                    pass

            probe_v = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-select_streams", "v:0", "-of", "json", output_path],
                capture_output=True, text=True, timeout=10
            )
            v_data = json.loads(probe_v.stdout)
            v_dur = float(v_data.get("format", {}).get("duration", 0.0))

            probe_a = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "stream=duration",
                 "-select_streams", "a:0", "-of", "json", output_path],
                capture_output=True, text=True, timeout=10
            )
            a_streams = json.loads(probe_a.stdout).get("streams", [])
            a_dur = float(a_streams[0]["duration"]) if a_streams and "duration" in a_streams[0] else None

            if source_v_dur:
                logger.info(f"[DurationLock] Source duration: {source_v_dur:.2f}s | Output video: {v_dur:.2f}s | Output audio: {a_dur:.2f}s" if a_dur else f"[DurationLock] Source: {source_v_dur:.2f}s | Output video: {v_dur:.2f}s")

            if a_dur and v_dur > 0 and v_dur < a_dur - 0.05:  # >50ms drift = frame-freeze risk
                logger.warning(f"[DurationLock] Drift detected: video={v_dur:.2f}s audio={a_dur:.2f}s. Repairing.")
                repaired = output_path.replace(".mp4", "_durfix.mp4")
                repair_cmd = [self.cmd_generator.ffmpeg_path, "-y", "-i", output_path,
                              "-c", "copy", "-shortest", repaired]
                subprocess.run(repair_cmd, check=True, capture_output=True)
                if os.path.exists(repaired):
                    os.replace(repaired, output_path)
                return {"repaired": True, "video_duration": round(v_dur, 2), "audio_duration_before": round(a_dur, 2), "source_duration": round(source_v_dur, 2) if source_v_dur else None}
            return {"repaired": False, "video_duration": round(v_dur, 2), "audio_duration_before": round(a_dur, 2) if a_dur else None, "source_duration": round(source_v_dur, 2) if source_v_dur else None}
        except Exception as e:
            logger.debug(f"[DurationLock] Post-flight check skipped: {e}")
            return {"repaired": False, "error": str(e)}

    def _step_timeout(self, step: Dict[str, Any]) -> int:
        """Scale timeout by operation cost, bounded to sane min (60s) / max (900s)."""
        op = step.get("operation", "")
        base = {
            "trim": 60,
            "scale_aspect": 180,
            "delogo_blur": 180,
            "speed_change": 180,
            "watermark_overlay": 120,
            "subtitle_burnin": 120,
            "audio_ducking_mix": 90,
            "bgm_mix": 90,
            "concat": 120,
            "transition_xfade": 180
        }.get(op, 300)
        return max(60, min(base, 900))

    def _save_clip_intelligence(self, synthesis: Dict[str, Any], input_path: str, output_path: str, duration_lock_res: Dict[str, Any]) -> None:
        """Save Gemini Call 3 Editing Plan & Output to ClipIntelligenceStore."""
        try:
            from Gemini_Modules.clip_intelligence_store import ClipIntelligenceStore
            store = ClipIntelligenceStore()
            clip_folder = os.path.dirname(input_path)
            clip_id = os.path.basename(clip_folder)
            clip_data = store.load(clip_id, clip_folder) or store.create_blank(clip_id, clip_folder)
            plan_dict = {
                "editing_intent": synthesis.get("editing_intent"),
                "creative_rationale": synthesis.get("creative_rationale"),
                "strategy_name": synthesis.get("editing_intent", "rhythm_driven"),
                "total_steps": synthesis.get("total_steps", 1),
                "operations": synthesis.get("operations", []),
            }
            store.patch_editing_plan(clip_data, plan_dict)
            brand_overlay_info = synthesis.get("brand_overlay") or {
                "text": os.getenv("BRAND_WATERMARK_TEXT", ""),
                "x": 100, "y": 1190, "w": 300, "h": 50
            }
            output_dict = {
                "master_reel_path": output_path,
                "render_success": True,
                "duration_lock": duration_lock_res,
                "mode": "SINGLE_PASS",
                "brand_overlay": brand_overlay_info
            }
            store.patch_output(clip_data, output_dict)
            store.save(clip_id, clip_data, clip_folder)
            logger.info(f"🧠 [ClipIntelligenceStore] Saved Gemini Call 3 Editing Plan & Output for '{clip_id}'")
        except Exception as store_err:
            logger.warning(f"🧠 [ClipIntelligenceStore] Call 3 save warning: {store_err}")


    def execute_pipeline(

        self,
        gemini_json: Dict[str, Any],
        input_path: str = "input.mp4",
        output_path: str = "output.mp4",
        extra_inputs: Optional[Dict[str, str]] = None,
        dry_run: bool = False
    ) -> Dict[str, Any]:
        extra_inputs = extra_inputs or {}
        synthesis = self.synthesize_from_gemini_json(gemini_json, input_path, output_path, extra_inputs)
        logger.info(f"Starting pipeline execution: {synthesis['editing_intent']}")

        # ── 🏆 SINGLE-PASS PRIMARY PATH ──────────────────────────────────────────
        # Attempt to collapse all operations into ONE ffmpeg -filter_complex call.
        micro_shots   = extra_inputs.get("micro_shots") or []
        wm_boxes      = extra_inputs.get("watermark_boxes") or []
        bgm_path      = extra_inputs.get("music") or extra_inputs.get("bgm") or ""
        brand_text    = (
            os.getenv("BRAND_WATERMARK_TEXT", "").strip()
            or os.getenv("WATERMARK_TEXT", "").strip()
            or os.getenv("BRAND_NAME", "").strip()
        )
        encoding_cfg  = gemini_json.get("global_encoding")

        _single_pass_ok = False
        if not dry_run and micro_shots:
            try:
                sp_step = self.cmd_generator.build_single_pass_filtergraph(
                    input_path=input_path,
                    output_path=output_path,
                    micro_shots=micro_shots,
                    bgm_path=bgm_path if bgm_path and os.path.exists(bgm_path) else None,
                    watermark_boxes=wm_boxes if wm_boxes else None,
                    brand_text=brand_text if brand_text else None,
                    encoding_cfg=encoding_cfg,
                    gemini_operations=gemini_json.get("operations", []),
                )
                logger.info(f"🏆 [SINGLE-PASS] Executing 1 unified filtergraph: {sp_step['terminal_command']}")
                clean_cmd = [str(a) for a in sp_step["cmd_list"]]
                result = subprocess.run(clean_cmd, check=True, capture_output=True, timeout=600)
                if os.path.exists(output_path) and os.path.getsize(output_path) > 1024:
                    logger.info("✅ [SINGLE-PASS] Render complete — 1 encode, 0 generational loss.")
                    if sp_step.get("brand_overlay"):
                        synthesis["brand_overlay"] = sp_step["brand_overlay"]
                    duration_lock_res = self._enforce_duration_lock(input_path, output_path)
                    self._save_clip_intelligence(synthesis, input_path, output_path, duration_lock_res)

                    return {
                        "status": "SUCCESS", "mode": "SINGLE_PASS",
                        "editing_intent": synthesis["editing_intent"],
                        "creative_rationale": synthesis.get("creative_rationale", ""),
                        "total_steps": 1, "final_output": output_path,
                        "duration_lock": duration_lock_res,
                    }
            except Exception as _sp_err:
                logger.warning(f"⚠️ [SINGLE-PASS] Build or execution failed: {_sp_err}. Falling back to multi-step pipeline.")

        # ── 🔁 MULTI-STEP FALLBACK PATH ───────────────────────────────────────────
        steps = synthesis.get("command_steps", [])
        executed_files = []

        def _cleanup(files: List[str]):
            for f in files:
                if f != output_path and os.path.exists(f):
                    try:
                        os.remove(f)
                    except Exception as cleanup_err:
                        logger.warning(f"Cleanup failed for {f}: {cleanup_err}")



        for idx, step in enumerate(steps, 1):
            logger.info(f"Executing Step {idx}/{len(steps)} [{step['operation']}]: {step['terminal_command']}")
            executed_files.append(step["output"])

            if not dry_run:
                step_to = self._step_timeout(step)
                try:
                    clean_cmd = [str(arg) for arg in step["cmd_list"]]
                    subprocess.run(clean_cmd, check=True, capture_output=True, timeout=step_to)
                except subprocess.TimeoutExpired:
                    logger.error(f"FFmpeg Step {idx} execution timed out after {step_to}s: {step['terminal_command']}")
                    _cleanup(executed_files)
                    return {"status": "FAILED", "failed_step": idx, "error": f"FFmpeg step execution timed out after {step_to} seconds",
                            "terminal_command": step["terminal_command"], "executed_steps": steps}
                except subprocess.CalledProcessError as err:
                    stderr_msg = err.stderr.decode() if err.stderr else str(err)
                    logger.error(f"FFmpeg Step {idx} failed: {stderr_msg}")
                    _cleanup(executed_files)
                    return {"status": "FAILED", "failed_step": idx, "error": stderr_msg,
                            "terminal_command": step["terminal_command"], "executed_steps": steps}

        if not dry_run:
            _cleanup(executed_files)
            duration_lock_res = self._enforce_duration_lock(input_path, output_path)
        else:
            duration_lock_res = {}

        # ── Save Gemini Call 3 Editing Plan & Output to ClipIntelligenceStore ──
        try:
            from Gemini_Modules.clip_intelligence_store import ClipIntelligenceStore
            store = ClipIntelligenceStore()
            clip_folder = os.path.dirname(input_path)
            clip_id = os.path.basename(clip_folder)

            clip_data = store.load(clip_id, clip_folder) or store.create_blank(clip_id, clip_folder)

            plan_dict = {
                "editing_intent": synthesis.get("editing_intent"),
                "creative_rationale": synthesis.get("creative_rationale"),
                "strategy_name": synthesis.get("editing_intent", "rhythm_driven"),
                "total_steps": len(steps),
                "operations": gemini_json.get("operations", []),
            }
            store.patch_editing_plan(clip_data, plan_dict)

            output_dict = {
                "master_reel_path": output_path,
                "render_success": True if not dry_run else False,
                "duration_lock": duration_lock_res,
                "mode": "DRY_RUN" if dry_run else "EXECUTED",
            }
            store.patch_output(clip_data, output_dict)

            store.save(clip_id, clip_data, clip_folder)
            logger.info(f"🧠 [ClipIntelligenceStore] Saved Gemini Call 3 Editing Plan & Output for '{clip_id}'")
        except Exception as store_err:
            logger.warning(f"🧠 [ClipIntelligenceStore] Call 3 save warning: {store_err}")

        return {"status": "SUCCESS", "mode": "DRY_RUN" if dry_run else "EXECUTED",
                "editing_intent": synthesis["editing_intent"],
                "creative_rationale": synthesis.get("creative_rationale", ""),
                "total_steps": len(steps), "final_output": output_path, "executed_steps": steps,
                "duration_lock": duration_lock_res}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    engine = GeminiFFmpegEngine()

    print("\n--- End-to-End Autonomous Pipeline Synthesis Test ---\n")
    res = engine.run_full_pipeline(
        user_request="Remove top watermark and convert to 9:16 vertical short with beat alignment",
        input_video_path=r"d:\AMTCE\temp\test_input.mp4",
        output_video_path=r"d:\AMTCE\temp\final_autonomous_output.mp4",
        forensic_context={
            "watermark_detected": True,
            "items": [{"x": 10, "y": 10, "w": 180, "h": 60, "type": "studio_logo"}]
        },
        dry_run=True
    )

    print(f"\nExecution Status: {res['status']}")
    print(f"Editing Intent: {res['editing_intent']}")
    print(f"Total FFmpeg Steps: {res['total_steps']}")
