"""
Compiler_Modules/karaoke_subtitle_engine.py

V7 Cinema-Grade .ASS Karaoke Subtitle Engine
=============================================
Industry-standard karaoke subtitle renderer using Advanced SubStation Alpha (.ass).
Eliminates all ghosting, jitter, and scaling bugs from drawtext-based approaches.

Supports grounded segment word timestamps directly from script pipeline for 100% sync.
Native Malayalam Unicode font (Nirmala UI) rendering with zero character corruption.
"""
import asyncio
import logging
import os
import re
import subprocess
import tempfile
import shutil
from typing import Dict, List, Optional
try:
    from config import WIDTH, HEIGHT
except ImportError:
    WIDTH = int(os.getenv("TARGET_WIDTH", "1080"))
    HEIGHT = int(os.getenv("TARGET_HEIGHT", "1920"))

logger = logging.getLogger("karaoke_subtitle_engine")


def _env_bool(key: str, default: bool = True) -> bool:
    return os.getenv(key, str(default)).strip().lower() in ("true", "1", "yes", "on")

def _env_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)).strip())
    except (ValueError, TypeError):
        return default

def _env_str(key: str, default: str) -> str:
    return os.getenv(key, default).strip()


class KaraokeConfig:
    """Live-loaded config from .env. Re-read on each engine call for hot-reload."""

    @classmethod
    def load(cls) -> "KaraokeConfig":
        cfg = cls()
        cfg.enabled          = _env_bool("CINEMATIC_NARRATOR_ENABLED", _env_bool("KARAOKE_ENABLED", True))
        cfg.font_size        = _env_int("KARAOKE_FONT_SIZE", 48)
        cfg.safe_zone_margin = _env_int("KARAOKE_SAFE_ZONE", 160)
        cfg.side_margin      = _env_int("KARAOKE_MARGIN_SIDE", 100)
        cfg.shadow_depth     = _env_int("KARAOKE_SHADOW_DEPTH", 4)
        cfg.outline_width    = _env_int("KARAOKE_OUTLINE_WIDTH", 3)
        cfg.chunk_size       = _env_int("KARAOKE_CHUNK_SIZE", 2)
        cfg.highlight_color  = _env_str("KARAOKE_HIGHLIGHT_COLOR", "00FFFF")
        cfg.base_color       = _env_str("KARAOKE_BASE_COLOR", "FFFFFF")
        return cfg

    def log(self):
        logger.info(
            f"🎬 [KARAOKE_CFG] enabled={self.enabled} | "
            f"font={self.font_size}pt | safe_zone={self.safe_zone_margin}px | "
            f"outline={self.outline_width}pt | shadow={self.shadow_depth}pt | "
            f"chunk={self.chunk_size} words | "
            f"colors=#{self.highlight_color}/#{self.base_color}"
        )


def _format_ass_time(seconds: float) -> str:
    """Convert float seconds → ASS time format H:MM:SS.CC"""
    total_cs = int(round(seconds * 100))
    h = total_cs // 360000
    total_cs %= 360000
    m = total_cs // 6000
    total_cs %= 6000
    s = total_cs // 100
    cs = total_cs % 100
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _clean_word(word: str) -> str:
    """Clean punctuation from word without destroying Malayalam script or Matras."""
    if not word:
        return ""
    # Strip leading/trailing punctuation and quotes while keeping Malayalam script intact
    cleaned = word.strip(" \t\n\r\"'`:;.,!?()[]{}<>-—–")
    return cleaned


def _bridge_timestamps(words: List[Dict]) -> List[Dict]:
    """
    Timestamp Bridging: Force word[i].end == word[i+1].start.
    Eliminates gaps that cause black-frame flicker or subtitle disappearance.
    """
    for i in range(len(words) - 1):
        words[i]["end"] = words[i + 1]["start"]
    return words


def _build_ass_content(words: List[Dict], cfg: KaraokeConfig) -> str:
    """
    Build the full .ASS subtitle file content with per-word karaoke highlighting.
    Uses Nirmala UI for native Malayalam glyph support.
    """
    YELLOW_TAG = r"{\1c&H" + cfg.highlight_color + r"&}"
    WHITE_TAG  = r"{\1c&H" + cfg.base_color + r"&}"

    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {WIDTH}",
        f"PlayResY: {HEIGHT}",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        # Style: Nirmala UI / Segoe UI for native Malayalam Unicode glyph support
        f"Style: Default,Nirmala UI,{cfg.font_size},&H00{cfg.base_color},&H000000FF,&H00000000,&H90000000,1,0,0,0,100,100,0,0,1,{cfg.outline_width},{cfg.shadow_depth},2,{cfg.side_margin},{cfg.side_margin},{cfg.safe_zone_margin},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    # Group words into phrase chunks
    chunks = [words[i:i + cfg.chunk_size] for i in range(0, len(words), cfg.chunk_size)]

    for chunk in chunks:
        # For each active word in the chunk, emit one Dialogue line
        for active_idx, active_word in enumerate(chunk):
            w_start = _format_ass_time(active_word["start"])
            w_end   = _format_ass_time(active_word["end"])

            # Build the line: highlight active word, white for rest
            parts = []
            for j, w in enumerate(chunk):
                cleaned = _clean_word(w["word"])
                if not cleaned:
                    continue
                if j == active_idx:
                    parts.append(f"{YELLOW_TAG}{cleaned}{WHITE_TAG}")
                else:
                    parts.append(cleaned)

            if parts:
                full_line = " ".join(parts)
                lines.append(f"Dialogue: 0,{w_start},{w_end},Default,,0,0,0,,{full_line}")

    return "\n".join(lines)


def apply_karaoke_subtitles(
    input_video: str,
    output_video: str,
    script_text: str,
    temp_dir: Optional[str] = None,
    existing_audio_path: Optional[str] = None,
    skip_audio_mix: bool = False,
    word_timestamps: Optional[List[Dict]] = None
) -> bool:
    """
    Master entry point. Applies V7 Cinema-Grade karaoke subtitles to a video.
    Accepts grounded word_timestamps for 100% accurate, zero-latency subtitle sync.
    """
    cfg = KaraokeConfig.load()

    if not cfg.enabled:
        logger.info("🔕 [KARAOKE] KARAOKE_ENABLED=false — skipping subtitle injection.")
        try:
            shutil.copy2(input_video, output_video)
            return True
        except Exception as e:
            logger.error(f"❌ [KARAOKE] Copy fallback failed: {e}")
            return False

    cfg.log()

    if not os.path.exists(input_video):
        logger.error(f"❌ [KARAOKE] Input video not found: {input_video}")
        return False

    if temp_dir is None:
        temp_dir = os.path.join(os.path.dirname(input_video), "_karaoke_tmp")
    os.makedirs(temp_dir, exist_ok=True)

    audio_path = existing_audio_path if existing_audio_path else os.path.join(temp_dir, "karaoke_voice.mp3")
    ass_path   = os.path.join(temp_dir, "karaoke_captions.ass")

    try:
        words = []
        if word_timestamps and len(word_timestamps) > 0:
            logger.info(f"✨ [KARAOKE] Using {len(word_timestamps)} GROUNDED script word timestamps (100% Sync).")
            words = _bridge_timestamps(word_timestamps)
        elif script_text and script_text.strip():
            logger.info("✨ [KARAOKE] Building word timestamps directly from clean script text (Zero-Whisper Hallucination Mode).")
            # Probe video duration to distribute words evenly over full video
            try:
                probe_cmd = [
                    "ffprobe", "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    input_video
                ]
                total_dur = float(subprocess.check_output(probe_cmd, stderr=subprocess.DEVNULL).decode().strip())
            except Exception:
                total_dur = 30.0

            clean_words = [w.strip() for w in script_text.split() if w.strip()]
            if clean_words:
                total_chars = max(1, sum(len(w) for w in clean_words))
                cursor = 0.0
                for w in clean_words:
                    w_len = len(w)
                    w_start = (cursor / total_chars) * total_dur
                    w_end = ((cursor + w_len) / total_chars) * total_dur
                    w_end = max(w_end, w_start + 0.15)
                    cursor += w_len
                    words.append({
                        "word": w,
                        "start": round(w_start, 3),
                        "end": round(w_end, 3)
                    })
                words = _bridge_timestamps(words)

        if not words:
            logger.error("❌ [KARAOKE] No word timestamps available — copying video without subtitles.")
            shutil.copy2(input_video, output_video)
            return True

        logger.info(f"✅ [KARAOKE] Processing ASS captions for {len(words)} words.")

        ass_content = _build_ass_content(words, cfg)
        with open(ass_path, "w", encoding="utf-8") as f:
            f.write(ass_content)
        logger.info(f"✅ [KARAOKE] ASS file written: {ass_path}")

        try:
            probe_cmd = [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                input_video
            ]
            duration = float(subprocess.check_output(probe_cmd, stderr=subprocess.DEVNULL).decode().strip())
        except Exception as e:
            logger.warning(f"⚠️ [KARAOKE] Could not probe duration: {e} — using 30s fallback")
            duration = 30.0

        try:
            has_audio = subprocess.check_output(
                ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_type", "-of", "csv=p=0", input_video]
            ).decode().strip() != ""
        except:
            has_audio = False

        safe_ass = ass_path.replace("\\", "/").replace(":", "\\:")
        logger.info("🎬 [KARAOKE] Starting final render with Cinema-Grade .ASS subtitles...")

        if skip_audio_mix:
            filter_chain = f"[0:v]format=yuv420p,scale=trunc(iw/2)*2:trunc(ih/2)*2,subtitles='{safe_ass}'[v]"
            audio_map = "0:a" if has_audio else None
        elif has_audio:
            filter_chain = f"[0:v]format=yuv420p,scale=trunc(iw/2)*2:trunc(ih/2)*2,subtitles='{safe_ass}'[v];[0:a][1:a]amix=inputs=2:duration=longest[a]"
            audio_map = "[a]"
        else:
            filter_chain = f"[0:v]format=yuv420p,scale=trunc(iw/2)*2:trunc(ih/2)*2,subtitles='{safe_ass}'[v]"
            audio_map = "1:a"

        cmd = [
            "ffmpeg", "-y",
            "-threads", "2",
            "-i", input_video,
        ]
        
        if not skip_audio_mix and os.path.exists(audio_path):
            cmd.extend(["-i", audio_path])

        cmd.extend([
            "-filter_complex", filter_chain,
            "-map", "[v]"
        ])
        
        if audio_map:
            cmd.extend(["-map", audio_map])
            
        cmd.extend([
            "-c:v", "libx264",
            "-preset", "fast" if os.getenv("RENDER_TARGET", "quality").strip().lower() == "speed" else "medium",
            "-crf", "26" if os.getenv("RENDER_TARGET", "quality").strip().lower() == "speed" else "20",
            "-c:a", "aac",
            "-b:a", "192k",
            "-t", str(duration),
            output_video
        ])

        result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if result.returncode != 0:
            err = result.stderr.decode(errors="ignore")[-1000:]
            logger.error(f"❌ [KARAOKE] FFmpeg render failed:\n{err}")
            return False

        logger.info(f"✨ [KARAOKE] Cinema-Grade render complete: {output_video}")
        return True

    except Exception as e:
        logger.exception(f"❌ [KARAOKE] Unexpected error in apply_karaoke_subtitles: {e}")
        return False


def is_karaoke_enabled() -> bool:
    return _env_bool("CINEMATIC_NARRATOR_ENABLED", _env_bool("KARAOKE_ENABLED", True))
