"""
proxy_encoder.py — 480p Proxy Compression Engine
=================================================
Kingdom: Timeline_and_Compilation
Purpose: Fast pre-analysis proxy generation before Gemini vision calls.
         Compresses raw video (any platform, any resolution) to a lightweight
         480p H.264 proxy for strategic frame sampling and Gemini analysis.

Why 480p:
  - Watermarks are fully visible at 480p (gemini_enhance_for_watermark.py
    upscales frames to 1440px independently anyway)
  - Gemini receives ~24 frames @ ~120KB each = ~3MB payload
  - FFmpeg ultrafast CRF-28 on a 60s clip = ~2-4s encoding time on CPU

Usage:
    from Main_Modules.proxy_encoder import encode_proxy_480p
    proxy_path = encode_proxy_480p(raw_video_path)
"""

import os
import subprocess
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger("proxy_encoder")

# ── Constants ────────────────────────────────────────────────────────────────
PROXY_WIDTH  = 854
PROXY_HEIGHT = 480
PROXY_CRF    = 28          # Higher = smaller file, lower quality (fine for analysis)
PROXY_PRESET = "ultrafast" # Fastest encode, slightly larger file — analysis only
AUDIO_BITRATE = "96k"

def _ffmpeg_bin() -> str:
    """Resolve ffmpeg binary path. Checks PATH first, then common install locations."""
    if shutil.which("ffmpeg"):
        return "ffmpeg"
    # Windows common paths
    candidates = [
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
        r"C:\tools\ffmpeg\bin\ffmpeg.exe",
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    raise FileNotFoundError(
        "ffmpeg not found. Install from https://ffmpeg.org and add to PATH."
    )


def encode_proxy(
    input_path: str,
    output_path: Optional[str] = None,
    width: int = PROXY_WIDTH,
    height: int = PROXY_HEIGHT,
    crf: int = PROXY_CRF,
    preset: str = PROXY_PRESET,
    overwrite: bool = False,
) -> str:
    """
    Encode a raw video to a 480p H.264 proxy for Gemini vision analysis.

    Args:
        input_path:  Path to the raw source video (any format, any resolution).
        output_path: Where to write the proxy. If None, writes alongside input
                     with '_proxy480p.mp4' suffix.
        width:       Target width in pixels (default 854).
        height:      Target height in pixels (default 480).
        crf:         FFmpeg CRF quality (0=lossless, 28=analysis-quality).
        preset:      FFmpeg speed preset (ultrafast for analysis proxies).
        overwrite:   If True, re-encode even if proxy already exists.

    Returns:
        str: Absolute path to the encoded proxy .mp4 file.

    Raises:
        FileNotFoundError: If input_path or ffmpeg binary is missing.
        RuntimeError: If FFmpeg encoding fails.
    """
    input_path = str(Path(input_path).resolve())
    if not os.path.isfile(input_path):
        raise FileNotFoundError(f"Input video not found: {input_path}")

    # ── Resolve output path ───────────────────────────────────────────────────
    if output_path is None:
        stem = Path(input_path).stem
        parent = Path(input_path).parent
        output_path = str(parent / f"{stem}_proxy480p.mp4")

    # ── Skip if proxy already exists and overwrite=False ──────────────────────
    if os.path.isfile(output_path) and not overwrite:
        logger.info(f"[proxy_encoder] Proxy already exists, skipping encode: {output_path}")
        return output_path

    ffmpeg = _ffmpeg_bin()

    # ── Video filter: scale to 480p, preserve aspect ratio, pad to exact size ─
    # force_original_aspect_ratio=decrease: shrink so longest side fits
    # pad: center the video on black bars if aspect ratio differs from 16:9
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
    )

    cmd = [
        ffmpeg,
        "-y",                      # overwrite output without prompt
        "-i", input_path,
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", preset,
        "-crf", str(crf),
        "-c:a", "aac",
        "-b:a", AUDIO_BITRATE,
        "-ar", "44100",
        "-movflags", "+faststart", # web-streaming optimized
        output_path,
    ]

    logger.info(f"[proxy_encoder] Encoding proxy: {os.path.basename(input_path)} → {os.path.basename(output_path)}")
    logger.debug(f"[proxy_encoder] FFmpeg cmd: {' '.join(cmd)}")

    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=300,  # 5-minute hard timeout for encoding
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"FFmpeg proxy encode timed out (>300s) for: {input_path}")

    if result.returncode != 0:
        err_output = result.stderr.decode("utf-8", errors="ignore")
        raise RuntimeError(
            f"FFmpeg proxy encode failed (exit {result.returncode}) for: {input_path}\n"
            f"FFmpeg stderr:\n{err_output[-2000:]}"  # last 2000 chars of error
        )

    proxy_size_mb = os.path.getsize(output_path) / (1024 * 1024)
    logger.info(
        f"[proxy_encoder] ✓ Proxy encoded: {os.path.basename(output_path)} "
        f"({proxy_size_mb:.1f} MB)"
    )
    return output_path


def get_proxy_path(raw_video_path: str) -> str:
    """
    Return the expected proxy path for a given raw video, without encoding.
    Use to check if a proxy exists before encoding.
    """
    stem = Path(raw_video_path).stem
    parent = Path(raw_video_path).parent
    return str(parent / f"{stem}_proxy480p.mp4")


def ensure_proxy(raw_video_path: str, **kwargs) -> str:
    """
    Return existing proxy if found, else encode and return new proxy.
    Convenience wrapper around encode_proxy(overwrite=False).
    """
    proxy_path = get_proxy_path(raw_video_path)
    if os.path.isfile(proxy_path):
        logger.info(f"[proxy_encoder] Using cached proxy: {os.path.basename(proxy_path)}")
        return proxy_path
    return encode_proxy(raw_video_path, output_path=proxy_path, overwrite=False, **kwargs)


# Backward compatibility alias
encode_proxy_480p = encode_proxy


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")

    parser = argparse.ArgumentParser(description="AMTCE 480p Proxy Encoder")
    parser.add_argument("input",  type=str, help="Path to raw video file")
    parser.add_argument("--output", type=str, default=None, help="Output proxy path (optional)")
    parser.add_argument("--crf",    type=int, default=PROXY_CRF, help=f"CRF quality (default {PROXY_CRF})")
    parser.add_argument("--overwrite", action="store_true", help="Re-encode even if proxy exists")
    args = parser.parse_args()

    out = encode_proxy(
        input_path=args.input,
        output_path=args.output,
        crf=args.crf,
        overwrite=args.overwrite,
    )
    print(f"Proxy ready: {out}")
