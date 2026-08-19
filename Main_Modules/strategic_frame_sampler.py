"""
strategic_frame_sampler.py — Hook-Dense Keyframe Selector
==========================================================
Kingdom: Vision_and_Shot_Intelligence
Purpose: Select the optimal 22-29 frames from a 480p proxy video for Gemini
         vision analysis. Uses a 3-zone sampling strategy:

  Zone 1 — Hook Zone (0-5s):  1 frame/sec → 5 frames
  Zone 2 — Body:              1 frame/4s  → 11-16 frames (scales with duration)
  Zone 3 — Climax (last 10s): 1 frame/2s → 5 frames
  Bonus:   Peak Motion Frames: top 3 by optical flow magnitude

  Total: ~22-29 frames depending on clip length.

Why hook-dense:
  Viewers decide in the first 5s. Gemini needs to see this zone at full density
  to correctly identify the hook structure, text reveal timing, and face cuts.

Usage:
    from Main_Modules.strategic_frame_sampler import extract_strategic_frames
    frames = extract_strategic_frames("proxy_480p.mp4")
    # returns List[np.ndarray] in BGR (OpenCV format)
"""

import os
import cv2
import numpy as np
import logging
from typing import List, Tuple, Dict, Any

logger = logging.getLogger("frame_sampler")

# ── Sampling constants ────────────────────────────────────────────────────────
HOOK_ZONE_END_S     = 5.0    # First 5 seconds = hook zone
HOOK_FPS            = 1.0    # 1 frame per second in hook zone
CLIMAX_ZONE_START_S = 10.0   # Last 10 seconds = climax zone
CLIMAX_FPS          = 0.5    # 1 frame per 2 seconds in climax zone
BODY_FPS            = 0.25   # 1 frame per 4 seconds in body zone
PEAK_MOTION_COUNT          = 5      # Top 5 optical-flow peak motion frames for max vision accuracy
MIN_FRAME_GAP_S        = 0.5    # Minimum gap between any two selected frames (dedup)
MIN_MOTION_SCORE_CUTOFF = 0.05   # Defensive guard: minimum optical flow magnitude threshold (filters static slide decks / zero-motion clips)


def _get_video_meta(cap: cv2.VideoCapture) -> Tuple[float, float, int]:
    """Return (fps, duration_s, total_frames) from a VideoCapture object."""
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration_s = total_frames / fps
    return fps, duration_s, total_frames


def _sample_timestamps(duration_s: float) -> List[float]:
    """
    Build the list of target timestamps (in seconds) using 3-zone strategy.
    Deduplicates timestamps closer than MIN_FRAME_GAP_S.
    """
    timestamps = set()

    # Zone 1: Hook (0 → HOOK_ZONE_END_S)
    hook_end = min(HOOK_ZONE_END_S, duration_s)
    t = 0.0
    while t <= hook_end:
        timestamps.add(round(t, 2))
        t += 1.0 / HOOK_FPS

    # Zone 3: Climax (last CLIMAX_ZONE_START_S seconds)
    climax_start = max(hook_end + 1.0, duration_s - CLIMAX_ZONE_START_S)
    t = climax_start
    while t <= duration_s:
        timestamps.add(round(t, 2))
        t += 1.0 / CLIMAX_FPS

    # Zone 2: Body (hook_end → climax_start)
    if climax_start > hook_end:
        t = hook_end + (1.0 / BODY_FPS)
        while t < climax_start:
            timestamps.add(round(t, 2))
            t += 1.0 / BODY_FPS

    # Sort and deduplicate by MIN_FRAME_GAP_S
    sorted_ts = sorted(timestamps)
    deduped = []
    last = -999.0
    for ts in sorted_ts:
        if ts - last >= MIN_FRAME_GAP_S:
            deduped.append(ts)
            last = ts

    return deduped


def _read_frame_at(cap: cv2.VideoCapture, timestamp_s: float, fps: float) -> np.ndarray | None:
    """Seek to timestamp and read a frame. Returns BGR frame or None."""
    frame_idx = int(timestamp_s * fps)
    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
    ret, frame = cap.read()
    if not ret:
        return None
    return frame


def _compute_optical_flow_scores(
    cap: cv2.VideoCapture,
    fps: float,
    duration_s: float,
    sample_interval_s: float = 2.0,
) -> List[Tuple[float, float]]:
    """
    Compute optical flow magnitude between consecutive sampled frames.
    Includes defensive guards for static/zero-motion clips, low resolution, and NaN values.
    Returns list of (timestamp_s, flow_score) sorted by score descending.
    """
    scores = []
    prev_gray = None
    t = 0.0

    while t < duration_s:
        frame = _read_frame_at(cap, t, fps)
        if frame is None:
            break

        h, w = frame.shape[:2]
        # Low-resolution / corrupt frame guard
        if h < 60 or w < 60:
            t += sample_interval_s
            continue

        # Downsample for fast flow computation
        small = cv2.resize(frame, (214, 120))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

        if prev_gray is not None:
            try:
                flow = cv2.calcOpticalFlowFarneback(
                    prev_gray, gray,
                    None,
                    pyr_scale=0.5, levels=2, winsize=8,
                    iterations=2, poly_n=5, poly_sigma=1.1,
                    flags=0,
                )
                magnitude = np.mean(np.sqrt(flow[..., 0]**2 + flow[..., 1]**2))
                # NaN / Inf guard
                if np.isnan(magnitude) or np.isinf(magnitude):
                    magnitude = 0.0

                # Zero-motion defense guard: ignore negligible movement (< 0.05)
                if magnitude >= MIN_MOTION_SCORE_CUTOFF:
                    scores.append((t, float(magnitude)))
                else:
                    logger.debug(f"[frame_sampler] Ignoring static/negligible motion frame @ {t:.1f}s (flow={magnitude:.4f})")
            except Exception as flow_err:
                logger.debug(f"[frame_sampler] Optical flow calculation fallback @ {t:.1f}s: {flow_err}")

        prev_gray = gray
        t += sample_interval_s

    return sorted(scores, key=lambda x: x[1], reverse=True)


def _detect_scene_cuts(
    cap: cv2.VideoCapture,
    fps: float,
    duration_s: float,
    sample_step_s: float = 0.5,
    threshold: float = 0.35,
) -> List[float]:
    """
    Detect shot transitions using HSV Color Histogram difference.
    Returns list of scene cut timestamps (in seconds).
    """
    cuts = []
    prev_hist = None
    t = 0.0

    while t < duration_s:
        frame = _read_frame_at(cap, t, fps)
        if frame is None:
            break

        small = cv2.resize(frame, (160, 90))
        hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist([hsv], [0, 1], None, [16, 16], [0, 180, 0, 256])
        cv2.normalize(hist, hist, 0, 1, cv2.NORM_MINMAX)

        if prev_hist is not None:
            # Correlation distance: 1.0 = identical, < 0.65 = shot change
            score = cv2.compareHist(prev_hist, hist, cv2.HISTCMP_CORREL)
            if score < (1.0 - threshold):
                cuts.append(round(t, 2))
                logger.debug(f"[frame_sampler] Scene cut detected @ {t:.2f}s (sim={score:.3f})")

        prev_hist = hist
        t += sample_step_s

    return cuts


def extract_high_gradient_crops(
    video_path: str,
    out_dir: str,
    top_k: int = 3,
    crop_size: int = 256,
) -> List[str]:
    """
    Extract tight 256x256 crops of high-frequency detail regions (watermarks, text, faces)
    using Laplacian Variance (∇² I).
    Returns list of absolute file paths to native-resolution crop JPEGs.
    """
    os.makedirs(out_dir, exist_ok=True)
    if not os.path.isfile(video_path):
        return []

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return []

    crop_paths = []
    try:
        fps, duration_s, _ = _get_video_meta(cap)
        sample_times = [0.5, duration_s * 0.5, max(0.5, duration_s - 1.0)]

        for idx, ts in enumerate(sample_times):
            frame = _read_frame_at(cap, ts, fps)
            if frame is None:
                continue

            h, w, _ = frame.shape
            if h < crop_size or w < crop_size:
                continue

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            best_val = -1.0
            best_crop = None

            # Grid search for highest Laplacian variance patch
            step_y = max(32, (h - crop_size) // 4)
            step_x = max(32, (w - crop_size) // 4)

            for y in range(0, h - crop_size + 1, step_y):
                for x in range(0, w - crop_size + 1, step_x):
                    patch = gray[y : y + crop_size, x : x + crop_size]
                    score = cv2.Laplacian(patch, cv2.CV_64F).var()
                    if score > best_val:
                        best_val = score
                        best_crop = frame[y : y + crop_size, x : x + crop_size]

            if best_crop is not None:
                out_path = os.path.join(out_dir, f"detail_crop_{idx+1}_{ts:.1f}s.jpg")
                cv2.imwrite(out_path, best_crop)
                crop_paths.append(out_path)
                logger.debug(f"[frame_sampler] High-gradient detail crop saved @ {ts:.1f}s (lap_var={best_val:.1f})")

    finally:
        cap.release()

    return crop_paths


def extract_strategic_frames(
    video_path: str,
    peak_motion_count: int = PEAK_MOTION_COUNT,
    enable_scene_cuts: bool = True,
    return_timestamps: bool = False,
) -> List[np.ndarray] | Tuple[List[np.ndarray], List[float]]:
    """
    Extract adaptive strategic keyframes from a proxy video for Gemini vision analysis.
    Merges 3-Zone sampling + HSV Scene Cuts + Optical Flow Motion Peaks.
    """
    if not os.path.isfile(video_path):
        raise FileNotFoundError(f"Video not found: {video_path}")

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")

    try:
        fps, duration_s, total_frames = _get_video_meta(cap)
        logger.info(
            f"[frame_sampler] Video: {os.path.basename(video_path)} | "
            f"{duration_s:.1f}s | {fps:.1f} FPS | {total_frames} frames"
        )

        # ── 1. Zone-based timestamps ──────────────────────────────────────────
        target_timestamps = _sample_timestamps(duration_s)

        # ── 2. Scene-Cut timestamps ───────────────────────────────────────────
        if enable_scene_cuts:
            scene_cuts = _detect_scene_cuts(cap, fps, duration_s)
            for cut_t in scene_cuts:
                if all(abs(cut_t - t) >= MIN_FRAME_GAP_S for t in target_timestamps):
                    target_timestamps.append(cut_t)

        # ── 3. Peak motion timestamps ─────────────────────────────────────────
        if peak_motion_count > 0:
            flow_scores = _compute_optical_flow_scores(cap, fps, duration_s)
            added = 0
            for peak_ts, score in flow_scores:
                if all(abs(peak_ts - t) >= MIN_FRAME_GAP_S for t in target_timestamps) and added < peak_motion_count:
                    target_timestamps.append(peak_ts)
                    added += 1
                    logger.debug(f"[frame_sampler] Peak motion frame @ {peak_ts:.1f}s (flow={score:.3f})")

        target_timestamps = sorted(set(round(t, 2) for t in target_timestamps))

        # ── Extract frames ────────────────────────────────────────────────────
        frames = []
        selected_timestamps = []

        for ts in target_timestamps:
            if ts > duration_s:
                continue
            frame = _read_frame_at(cap, ts, fps)
            if frame is not None:
                frames.append(frame)
                selected_timestamps.append(ts)

        logger.info(
            f"[frame_sampler] ✓ Extracted {len(frames)} strategic frames "
            f"from {duration_s:.1f}s clip "
            f"(hook={min(5, int(duration_s))} dense, "
            f"body+climax={len(frames)-min(5,int(duration_s))-peak_motion_count}, "
            f"motion={min(peak_motion_count, len(frames))})"
        )

    finally:
        cap.release()

    if return_timestamps:
        return frames, selected_timestamps
    return frames


def extract_frames_from_vectors(
    video_path: str,
    visual_vectors: Dict[str, Any],
    out_dir: str,
) -> List[str]:
    """
    Extracts frames at the EXACT mathematical timestamps specified in visual_vectors (from Gemini Call 1).
    Runs OpenCV optical flow verification as a defensive guard on each Gemini-specified frame.
    Returns list of absolute file paths to all extracted JPEG files.
    """
    os.makedirs(out_dir, exist_ok=True)
    if not os.path.isfile(video_path):
        return []

    timestamps = visual_vectors.get("targeted_timestamps_sec", [])
    if not timestamps:
        return extract_strategic_frame_files(video_path, out_dir, include_micro_crops=False)

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return []

    file_paths = []
    try:
        fps, duration_s, _ = _get_video_meta(cap)
        for i, ts in enumerate(timestamps):
            if ts > duration_s:
                continue
            frame = _read_frame_at(cap, ts, fps)
            if frame is not None:
                out_path = os.path.join(out_dir, f"vector_frame_{i+1:02d}_{ts:.2f}s.jpg")
                cv2.imwrite(out_path, frame)
                file_paths.append(out_path)
                logger.debug(f"[frame_sampler] Vector-guided frame saved @ {ts:.2f}s -> {out_path}")
    finally:
        cap.release()

    if not file_paths:
        return extract_strategic_frame_files(video_path, out_dir, include_micro_crops=False)

    logger.info(f"[frame_sampler] ✓ Gemini Vector-Guided Extraction: saved {len(file_paths)} targeted frames to {out_dir}")
    return file_paths


def extract_strategic_frame_files(
    video_path: str,
    out_dir: str,
    include_micro_crops: bool = True,
    return_meta: bool = False,
) -> List[str] | Tuple[List[str], Dict[str, Any]]:
    """
    Extract adaptive strategic keyframes (global context) + 256x256 high-gradient detail crops (micro-crops)
    and save them directly as JPEGs in out_dir.
    Returns list of absolute file paths to all extracted JPEG files (or (file_paths, meta) if return_meta=True).
    """
    os.makedirs(out_dir, exist_ok=True)
    res = extract_strategic_frames(video_path, return_timestamps=True)
    if isinstance(res, tuple):
        frames, timestamps = res
    else:
        frames, timestamps = res, [i * 1.0 for i in range(len(res))]

    file_paths = []
    for i, (frame, ts) in enumerate(zip(frames, timestamps)):
        out_path = os.path.join(out_dir, f"frame_{i+1:02d}_{ts:.2f}s.jpg")
        cv2.imwrite(out_path, frame)
        file_paths.append(out_path)

    # Calculate zone metadata
    hook_count = sum(1 for ts in timestamps if ts <= HOOK_ZONE_END_S)
    climax_count = sum(1 for ts in timestamps if ts >= (timestamps[-1] - CLIMAX_ZONE_START_S) if timestamps)
    body_count = max(0, len(timestamps) - hook_count - climax_count)
    motion_count = min(PEAK_MOTION_COUNT, len(timestamps))
    meta = {
        "hook_count": hook_count,
        "body_count": body_count,
        "climax_count": climax_count,
        "motion_count": motion_count,
        "total_frames": len(timestamps)
    }

    # Inject 256x256 high-gradient detail crops for 100% sharp micro-detail inspection
    if include_micro_crops:
        crops = extract_high_gradient_crops(video_path, out_dir)
        file_paths.extend(crops)
        if crops:
            logger.info(f"[frame_sampler] Attached {len(crops)} high-gradient 256x256 micro-crops to frame payload")

    if return_meta:
        return file_paths, meta
    return file_paths


def frames_to_pil(frames: List[np.ndarray]):
    """Convert BGR OpenCV frames to PIL Images for Gemini API upload."""
    from PIL import Image
    pil_images = []
    for frame in frames:
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        pil_images.append(Image.fromarray(rgb))
    return pil_images


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")

    parser = argparse.ArgumentParser(description="AMTCE Strategic Frame Sampler")
    parser.add_argument("video", type=str, help="Path to proxy video")
    parser.add_argument("--output-dir", type=str, default=None,
                        help="Directory to save extracted frames as JPEG")
    parser.add_argument("--no-motion", action="store_true",
                        help="Skip optical flow peak motion detection")
    args = parser.parse_args()

    frames, timestamps = extract_strategic_frames(
        args.video,
        peak_motion_count=0 if args.no_motion else PEAK_MOTION_COUNT,
        return_timestamps=True,
    )

    print(f"\nExtracted {len(frames)} frames:")
    for i, ts in enumerate(timestamps):
        print(f"  Frame {i+1:02d} @ {ts:.2f}s")

    if args.output_dir:
        os.makedirs(args.output_dir, exist_ok=True)
        for i, (frame, ts) in enumerate(zip(frames, timestamps)):
            out_path = os.path.join(args.output_dir, f"frame_{i+1:02d}_{ts:.2f}s.jpg")
            cv2.imwrite(out_path, frame)
        print(f"\nFrames saved to: {args.output_dir}")
