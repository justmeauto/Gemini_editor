"""
Core_Modules/scene_intel.py — Scene & Face Intelligence Layer
==============================================================
Universal Pre-Pipeline Scene Intelligence Layer for AMTCE Phase 2.

Features:
  [Scene Cut Detection]
  1. detect_scene_cuts()        – PySceneDetect ContentDetector (preferred) with
                                   OpenCV HSV histogram fallback. Guards against
                                   duplicate/overlapping cuts at video intros and
                                   high-motion sections via min_scene_len.

  [Frame Sampling — replaces strategic_frame_sampler.py]
  2. extract_strategic_frames() – 3-Zone hook-dense keyframe selector
                                   (Hook 0-5s / Body / Climax last-10s) +
                                   Optical-flow peak motion frames.
  3. extract_strategic_frame_files() – Same, writes frames to disk as JPEGs.
  4. extract_high_gradient_crops()   – 256x256 Laplacian detail crops.
  5. extract_frames_from_vectors()   – Gemini-vector-guided exact timestamp extraction.
  6. frames_to_pil()                 – BGR to PIL converter for Gemini API.

  [Face & Character Detection]
  7. OpenCV Res10 300x300 Caffe SSD DNN face detector (Haar Cascade fallback).
  8. Spatial centroid clustering -> Subject A, B, C...
  9. RAG Creator Face Cache in cache/face_cache/{creator_name}.jpg (20% padding).

  [Pre-Pipeline Entrypoint]
  10. analyze_scene_pre_pipeline() – samples 12 keyframes, detects faces,
                                     clusters subjects, manages face cache.
"""

import os
import re
import json
import logging
try:
    import cv2
except ImportError:
    cv2 = None
import numpy as np
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple, Union

logger = logging.getLogger("scene_intel")

# ── Paths ─────────────────────────────────────────────────────────────────────
_current_dir        = os.path.dirname(os.path.abspath(__file__))
_simpler_update_dir = os.path.dirname(_current_dir)
_REPO_ROOT          = _simpler_update_dir  # Gemini_editor root

FACE_CACHE_DIR = Path(os.path.join(_REPO_ROOT, "cache", "face_cache"))
FACE_CACHE_DIR.mkdir(parents=True, exist_ok=True)

DNN_PROTO  = Path(os.path.join(_REPO_ROOT, "models", "deploy.prototxt"))
DNN_MODEL  = Path(os.path.join(_REPO_ROOT, "models", "res10_300x300_ssd_iter_140000.caffemodel"))

# ── Frame Sampling Constants ──────────────────────────────────────────────────
HOOK_ZONE_END_S         = 5.0    # First 5 seconds = hook zone
HOOK_FPS                = 1.0    # 1 frame/second in hook zone
CLIMAX_ZONE_START_S     = 10.0   # Last 10 seconds = climax zone
CLIMAX_FPS              = 0.5    # 1 frame per 2 seconds in climax zone
BODY_FPS                = 0.25   # 1 frame per 4 seconds in body zone
PEAK_MOTION_COUNT       = 5      # Top N optical-flow peak motion frames
MIN_FRAME_GAP_S         = 0.5    # Minimum gap between any two selected frames
MIN_MOTION_SCORE_CUTOFF = 0.05   # Minimum optical flow magnitude

# ── Scene Cut Detection Constants ─────────────────────────────────────────────
PYSCENE_THRESHOLD    = 27.0   # ContentDetector threshold (lower = more sensitive)
MIN_SCENE_LEN_FRAMES = 15     # Ignore scenes shorter than this (kills intro duplicates)


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — Scene Cut Detection
# ══════════════════════════════════════════════════════════════════════════════

def detect_scene_cuts(
    video_path: str,
    threshold: float = PYSCENE_THRESHOLD,
    min_scene_len_frames: int = MIN_SCENE_LEN_FRAMES,
) -> List[float]:
    """
    Detect shot/scene transitions and return cut timestamps in seconds.

    Strategy:
      PRIMARY  - PySceneDetect ContentDetector (HSL delta-based, high accuracy).
      FALLBACK - OpenCV HSV histogram correlation with min_scene_len guard.

    Args:
        video_path:           Path to video file.
        threshold:            PySceneDetect sensitivity. For OpenCV fallback:
                              histogram dissimilarity trigger [0, 1].
        min_scene_len_frames: Minimum frames a scene must last before recording.

    Returns:
        Sorted list of cut timestamps in seconds.
    """
    if not os.path.isfile(video_path):
        logger.warning(f"[scene_intel] detect_scene_cuts: file not found: {video_path}")
        return []

    # ── Try PySceneDetect first ───────────────────────────────────────────────
    try:
        from scenedetect import open_video, SceneManager
        from scenedetect.detectors import ContentDetector

        video = open_video(video_path)
        scene_manager = SceneManager()
        scene_manager.add_detector(
            ContentDetector(threshold=threshold, min_scene_len=min_scene_len_frames)
        )
        scene_manager.detect_scenes(video, show_progress=False)
        scene_list = scene_manager.get_scene_list()

        cuts = []
        for start_tc, _end_tc in scene_list:
            t = start_tc.get_seconds()
            if t > 0.0:   # skip the first scene (always starts at 0)
                cuts.append(round(t, 3))

        logger.info(
            f"[scene_intel] PySceneDetect: {len(cuts)} cuts "
            f"(threshold={threshold}, min_len={min_scene_len_frames}f)"
        )
        return cuts

    except ImportError:
        logger.debug(
            "[scene_intel] PySceneDetect not installed — using OpenCV HSV fallback"
        )
    except Exception as psd_err:
        logger.warning(
            f"[scene_intel] PySceneDetect error ({psd_err}) — using OpenCV fallback"
        )

    # ── OpenCV fallback ───────────────────────────────────────────────────────
    return _detect_scene_cuts_opencv(video_path, min_scene_len_frames)


def _detect_scene_cuts_opencv(
    video_path: str,
    min_scene_len_frames: int = MIN_SCENE_LEN_FRAMES,
    hsv_threshold: float = 0.35,
    sample_step_s: float = 0.25,
) -> List[float]:
    """
    OpenCV HSV histogram correlation fallback for scene cut detection.
    Enforces min_scene_len to suppress duplicate/overlapping cuts in
    fast-paced intros and high-motion sequences.
    """
    if cv2 is None:
        return []

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return []

    fps          = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration_s   = total_frames / fps

    cuts: List[float] = []
    prev_hist          = None
    last_cut_frame     = -min_scene_len_frames

    t = 0.0
    try:
        while t < duration_s:
            frame_idx = int(t * fps)
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ret, frame = cap.read()
            if not ret or frame is None:
                break

            small = cv2.resize(frame, (160, 90))
            hsv   = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
            hist  = cv2.calcHist([hsv], [0, 1], None, [16, 16], [0, 180, 0, 256])
            cv2.normalize(hist, hist, 0, 1, cv2.NORM_MINMAX)

            if prev_hist is not None:
                score = cv2.compareHist(prev_hist, hist, cv2.HISTCMP_CORREL)
                if score < (1.0 - hsv_threshold):
                    gap = frame_idx - last_cut_frame
                    if gap >= min_scene_len_frames:
                        cuts.append(round(t, 3))
                        last_cut_frame = frame_idx
                        logger.debug(
                            f"[scene_intel] Cut @ {t:.2f}s (sim={score:.3f})"
                        )
                    else:
                        logger.debug(
                            f"[scene_intel] Suppressed duplicate cut @ {t:.2f}s "
                            f"(gap={gap}f < {min_scene_len_frames}f)"
                        )

            prev_hist = hist
            t += sample_step_s
    finally:
        cap.release()

    logger.info(f"[scene_intel] OpenCV fallback: {len(cuts)} cuts detected")
    return cuts


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — Frame Sampling (absorbed from strategic_frame_sampler.py)
# ══════════════════════════════════════════════════════════════════════════════

def _get_video_meta(cap: Any) -> Tuple[float, float, int]:
    """Return (fps, duration_s, total_frames) from a VideoCapture object."""
    if not cap or not hasattr(cap, "get") or cv2 is None:
        return 30.0, 0.0, 0
    fps          = cap.get(getattr(cv2, "CAP_PROP_FPS", 5)) or 30.0
    total_frames = int(cap.get(getattr(cv2, "CAP_PROP_FRAME_COUNT", 7)) or 0)
    duration_s   = total_frames / fps if fps > 0 else 0.0
    return fps, duration_s, total_frames


def _sample_timestamps(duration_s: float) -> List[float]:
    """
    Build target timestamps (seconds) using 3-zone strategy:
      Zone 1 - Hook    (0 to HOOK_ZONE_END_S):             1 frame/sec
      Zone 2 - Body    (hook_end to climax_start):         1 frame/4s
      Zone 3 - Climax  (last CLIMAX_ZONE_START_S secs):   1 frame/2s
    Deduplicates timestamps closer than MIN_FRAME_GAP_S.
    """
    timestamps = set()

    # Zone 1: Hook
    hook_end = min(HOOK_ZONE_END_S, duration_s)
    t = 0.0
    while t <= hook_end:
        timestamps.add(round(t, 2))
        t += 1.0 / HOOK_FPS

    # Zone 3: Climax
    climax_start = max(hook_end + 1.0, duration_s - CLIMAX_ZONE_START_S)
    t = climax_start
    while t <= duration_s:
        timestamps.add(round(t, 2))
        t += 1.0 / CLIMAX_FPS

    # Zone 2: Body
    if climax_start > hook_end:
        t = hook_end + (1.0 / BODY_FPS)
        while t < climax_start:
            timestamps.add(round(t, 2))
            t += 1.0 / BODY_FPS

    # Sort + dedup
    sorted_ts = sorted(timestamps)
    deduped   = []
    last      = -999.0
    for ts in sorted_ts:
        if ts - last >= MIN_FRAME_GAP_S:
            deduped.append(ts)
            last = ts

    return deduped


def _read_frame_at(
    cap: Any, timestamp_s: float, fps: float
) -> Optional[np.ndarray]:
    """Seek to timestamp_s and read a frame. Returns BGR frame or None."""
    if not cap or cv2 is None:
        return None
    frame_idx = int(timestamp_s * fps)
    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
    ret, frame = cap.read()
    return frame if ret else None


def _compute_optical_flow_scores(
    cap: Any,
    fps: float,
    duration_s: float,
    sample_interval_s: float = 2.0,
) -> List[Tuple[float, float]]:
    """
    Compute optical flow magnitude between consecutive sampled frames.
    Returns list of (timestamp_s, flow_score) sorted by score descending.
    Guards: low-res frames, NaN/Inf, zero-motion clips.
    """
    scores: List[Tuple[float, float]] = []
    prev_gray = None
    t = 0.0

    while t < duration_s:
        frame = _read_frame_at(cap, t, fps)
        if frame is None:
            break

        h, w = frame.shape[:2]
        if h < 60 or w < 60:
            t += sample_interval_s
            continue

        small = cv2.resize(frame, (214, 120))
        gray  = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

        if prev_gray is not None:
            try:
                flow = cv2.calcOpticalFlowFarneback(
                    prev_gray, gray, None,
                    pyr_scale=0.5, levels=2, winsize=8,
                    iterations=2, poly_n=5, poly_sigma=1.1, flags=0,
                )
                magnitude = np.mean(np.sqrt(flow[..., 0] ** 2 + flow[..., 1] ** 2))
                if np.isnan(magnitude) or np.isinf(magnitude):
                    magnitude = 0.0
                if magnitude >= MIN_MOTION_SCORE_CUTOFF:
                    scores.append((t, float(magnitude)))
                else:
                    logger.debug(
                        f"[scene_intel] Static frame @ {t:.1f}s suppressed "
                        f"(flow={magnitude:.4f})"
                    )
            except Exception as flow_err:
                logger.debug(
                    f"[scene_intel] Optical flow error @ {t:.1f}s: {flow_err}"
                )

        prev_gray = gray
        t += sample_interval_s

    return sorted(scores, key=lambda x: x[1], reverse=True)


def extract_strategic_frames(
    video_path: str,
    peak_motion_count: int = PEAK_MOTION_COUNT,
    enable_scene_cuts: bool = True,
    return_timestamps: bool = False,
) -> Union[List[np.ndarray], Tuple[List[np.ndarray], List[float]]]:
    """
    Extract adaptive strategic keyframes from a proxy video for Gemini vision.
    Merges:
      - 3-Zone sampling (Hook / Body / Climax)
      - PySceneDetect scene cuts (OpenCV fallback)
      - Optical-flow peak motion frames

    Args:
        video_path:         Path to proxy video.
        peak_motion_count:  Max optical-flow peak frames to add.
        enable_scene_cuts:  Whether to run scene cut detection.
        return_timestamps:  If True, returns (frames, timestamps) tuple.

    Returns:
        List of BGR frames, or (frames, timestamps) if return_timestamps=True.
    """
    if not os.path.isfile(video_path):
        raise FileNotFoundError(f"Video not found: {video_path}")

    if cv2 is None:
        return ([], []) if return_timestamps else []

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")

    try:
        fps, duration_s, total_frames = _get_video_meta(cap)
        logger.info(
            f"[scene_intel] Video: {os.path.basename(video_path)} | "
            f"{duration_s:.1f}s | {fps:.1f} FPS | {total_frames} frames"
        )

        # ── 1. Zone-based timestamps ──────────────────────────────────────────
        target_timestamps = _sample_timestamps(duration_s)

        # ── 2. Scene-Cut timestamps ───────────────────────────────────────────
        if enable_scene_cuts:
            scene_cuts = detect_scene_cuts(video_path)
            for cut_t in scene_cuts:
                if all(abs(cut_t - t) >= MIN_FRAME_GAP_S for t in target_timestamps):
                    target_timestamps.append(cut_t)

        # ── 3. Peak motion timestamps ─────────────────────────────────────────
        if peak_motion_count > 0:
            flow_scores = _compute_optical_flow_scores(cap, fps, duration_s)
            added = 0
            for peak_ts, score in flow_scores:
                if (
                    all(abs(peak_ts - t) >= MIN_FRAME_GAP_S for t in target_timestamps)
                    and added < peak_motion_count
                ):
                    target_timestamps.append(peak_ts)
                    added += 1
                    logger.debug(
                        f"[scene_intel] Peak motion @ {peak_ts:.1f}s (flow={score:.3f})"
                    )

        target_timestamps = sorted(set(round(t, 2) for t in target_timestamps))

        # ── Extract frames ────────────────────────────────────────────────────
        frames: List[np.ndarray] = []
        selected_timestamps: List[float] = []

        for ts in target_timestamps:
            if ts > duration_s:
                continue
            frame = _read_frame_at(cap, ts, fps)
            if frame is not None:
                frames.append(frame)
                selected_timestamps.append(ts)

        logger.info(
            f"[scene_intel] Extracted {len(frames)} strategic frames "
            f"from {duration_s:.1f}s clip"
        )

    finally:
        cap.release()

    if return_timestamps:
        return frames, selected_timestamps
    return frames


def extract_high_gradient_crops(
    video_path: str,
    out_dir: str,
    top_k: int = 3,
    crop_size: int = 256,
) -> List[str]:
    """
    Extract tight 256x256 crops of high-frequency detail regions
    (watermarks, text, faces) using Laplacian Variance.
    Returns list of absolute JPEG file paths.
    """
    os.makedirs(out_dir, exist_ok=True)
    if not os.path.isfile(video_path) or cv2 is None:
        return []

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return []

    crop_paths: List[str] = []
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

            gray      = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            best_val  = -1.0
            best_crop = None

            step_y = max(32, (h - crop_size) // 4)
            step_x = max(32, (w - crop_size) // 4)

            for y in range(0, h - crop_size + 1, step_y):
                for x in range(0, w - crop_size + 1, step_x):
                    patch = gray[y: y + crop_size, x: x + crop_size]
                    score = cv2.Laplacian(patch, cv2.CV_64F).var()
                    if score > best_val:
                        best_val  = score
                        best_crop = frame[y: y + crop_size, x: x + crop_size]

            if best_crop is not None:
                out_path = os.path.join(out_dir, f"detail_crop_{idx + 1}_{ts:.1f}s.jpg")
                cv2.imwrite(out_path, best_crop)
                crop_paths.append(out_path)
                logger.debug(
                    f"[scene_intel] Detail crop saved @ {ts:.1f}s "
                    f"(lap_var={best_val:.1f})"
                )
    finally:
        cap.release()

    return crop_paths


def extract_frames_from_vectors(
    video_path: str,
    visual_vectors: Dict[str, Any],
    out_dir: str,
) -> List[str]:
    """
    Extract frames at the EXACT timestamps in visual_vectors (from Gemini Call 1).
    Falls back to strategic extraction if no vector timestamps found.
    Returns list of absolute JPEG file paths.
    """
    os.makedirs(out_dir, exist_ok=True)
    if not os.path.isfile(video_path):
        return []

    timestamps = visual_vectors.get("targeted_timestamps_sec", [])
    if not timestamps:
        return extract_strategic_frame_files(video_path, out_dir, include_micro_crops=False)

    if cv2 is None:
        return []

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return []

    file_paths: List[str] = []
    try:
        fps, duration_s, _ = _get_video_meta(cap)
        for i, ts in enumerate(timestamps):
            if ts > duration_s:
                continue
            frame = _read_frame_at(cap, ts, fps)
            if frame is not None:
                out_path = os.path.join(out_dir, f"vector_frame_{i + 1:02d}_{ts:.2f}s.jpg")
                cv2.imwrite(out_path, frame)
                file_paths.append(out_path)
                logger.debug(
                    f"[scene_intel] Vector-guided frame @ {ts:.2f}s -> {out_path}"
                )
    finally:
        cap.release()

    if not file_paths:
        return extract_strategic_frame_files(video_path, out_dir, include_micro_crops=False)

    logger.info(
        f"[scene_intel] Vector-Guided Extraction: {len(file_paths)} frames -> {out_dir}"
    )
    return file_paths


def extract_strategic_frame_files(
    video_path: str,
    out_dir: str,
    include_micro_crops: bool = True,
    return_meta: bool = False,
) -> Union[List[str], Tuple[List[str], Dict[str, Any]]]:
    """
    Extract adaptive strategic keyframes + 256x256 high-gradient detail crops
    and save them as JPEGs in out_dir.

    Args:
        video_path:         Path to proxy or source video.
        out_dir:            Output directory for JPEG files.
        include_micro_crops: Whether to also extract Laplacian detail crops.
        return_meta:        If True, returns (file_paths, meta_dict).

    Returns:
        List of absolute JPEG paths, or (file_paths, meta) if return_meta=True.
    """
    os.makedirs(out_dir, exist_ok=True)
    res = extract_strategic_frames(video_path, return_timestamps=True)
    if isinstance(res, tuple):
        frames, timestamps = res
    else:
        frames, timestamps = res, [float(i) for i in range(len(res))]

    file_paths: List[str] = []
    for i, (frame, ts) in enumerate(zip(frames, timestamps)):
        out_path = os.path.join(out_dir, f"frame_{i + 1:02d}_{ts:.2f}s.jpg")
        if cv2 is not None:
            cv2.imwrite(out_path, frame)
        file_paths.append(out_path)

    # Zone metadata
    hook_count    = sum(1 for ts in timestamps if ts <= HOOK_ZONE_END_S)
    climax_anchor = (timestamps[-1] - CLIMAX_ZONE_START_S) if timestamps else 0.0
    climax_count  = sum(1 for ts in timestamps if ts >= climax_anchor)
    body_count    = max(0, len(timestamps) - hook_count - climax_count)
    motion_count  = min(PEAK_MOTION_COUNT, len(timestamps))
    meta = {
        "hook_count":   hook_count,
        "body_count":   body_count,
        "climax_count": climax_count,
        "motion_count": motion_count,
        "total_frames": len(timestamps),
    }

    if include_micro_crops:
        crops = extract_high_gradient_crops(video_path, out_dir)
        file_paths.extend(crops)
        if crops:
            logger.info(
                f"[scene_intel] Attached {len(crops)} high-gradient 256x256 "
                f"micro-crops to frame payload"
            )

    if return_meta:
        return file_paths, meta
    return file_paths


def frames_to_pil(frames: List[np.ndarray]):
    """Convert BGR OpenCV frames to PIL Images for Gemini API upload."""
    from PIL import Image
    if cv2 is None:
        return []
    return [Image.fromarray(cv2.cvtColor(f, cv2.COLOR_BGR2RGB)) for f in frames]


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — Face Cache Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _title_to_cache_key(title: str) -> str:
    """Convert creator handle/title to safe filename key."""
    return re.sub(r"[^a-zA-Z0-9_]", "_", title.strip()).lower()


def load_cached_face(creator_name: str) -> Optional[np.ndarray]:
    """Load previously saved 20%-padded face image for this creator. Returns None if missing."""
    if not creator_name or cv2 is None:
        return None
    key  = _title_to_cache_key(creator_name)
    path = FACE_CACHE_DIR / f"{key}.jpg"
    if path.exists():
        try:
            img = cv2.imread(str(path))
            if img is not None:
                logger.info(
                    f"[RAG FACE HIT] Loaded creator face cache for @{creator_name}"
                )
                return img
        except Exception as e:
            logger.warning(f"   Failed to load face cache {path}: {e}")
    return None


def save_face_cache(
    creator_name: str,
    frame: np.ndarray,
    bbox: Tuple[int, int, int, int],
) -> bool:
    """
    Save face crop with 20% padding to cache/face_cache/{creator_name}.jpg.
    bbox format: (x, y, w, h)
    """
    if not creator_name or frame is None or cv2 is None:
        return False
    try:
        h_img, w_img = frame.shape[:2]
        x, y, w, h  = bbox

        pad_x = int(w * 0.20)
        pad_y = int(h * 0.20)
        x1 = max(0, x - pad_x)
        y1 = max(0, y - pad_y)
        x2 = min(w_img, x + w + pad_x)
        y2 = min(h_img, y + h + pad_y)

        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return False

        key  = _title_to_cache_key(creator_name)
        path = FACE_CACHE_DIR / f"{key}.jpg"
        cv2.imwrite(str(path), crop)
        logger.info(
            f"[RAG FACE STORED] Saved 20%-padded face cache -> {path.name}"
        )
        return True
    except Exception as e:
        logger.warning(f"   Failed to save face cache for {creator_name}: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — OpenCV Face Detector
# ══════════════════════════════════════════════════════════════════════════════

class OpenCVFaceDetector:
    """
    OpenCV DNN Res10 300x300 Caffe SSD Face Detector with Haar Cascade Fallback.
    """

    def __init__(self):
        self.face_net = None
        self.haar     = None

        if DNN_PROTO.exists() and DNN_MODEL.exists() and cv2 is not None and hasattr(cv2, "dnn"):
            try:
                self.face_net = cv2.dnn.readNet(str(DNN_PROTO), str(DNN_MODEL))
                logger.info("SceneIntel: Loaded DNN Face Detector")
                return
            except Exception as e:
                logger.warning(f"Failed to load DNN with readNet: {e}")
                if hasattr(cv2.dnn, "readNetFromCaffe"):
                    try:
                        self.face_net = cv2.dnn.readNetFromCaffe(
                            str(DNN_PROTO), str(DNN_MODEL)
                        )
                        logger.info("SceneIntel: Loaded DNN Face Detector (legacy method)")
                        return
                    except Exception as e2:
                        logger.warning(
                            f"Failed to load DNN with readNetFromCaffe: {e2}"
                        )

        # Haar Cascade fallback
        if self.face_net is None:
            try:
                cascade_path = (
                    getattr(cv2.data, "haarcascades", "")
                    + "haarcascade_frontalface_default.xml"
                    if (cv2 is not None and hasattr(cv2, "data")) else ""
                )
                if (
                    cascade_path
                    and os.path.exists(cascade_path)
                    and hasattr(cv2, "CascadeClassifier")
                ):
                    self.haar = cv2.CascadeClassifier(cascade_path)
                    if not self.haar.empty():
                        logger.info(
                            "OpenCV Haar Cascade Face Detector initialized (fallback)."
                        )
                    else:
                        logger.debug("Haar Cascade Classifier empty.")
                        self.haar = None
                else:
                    logger.debug(
                        "Haar Cascade file not found or CascadeClassifier unavailable (cloud mode)"
                    )
            except Exception as e:
                logger.debug(f"Haar Cascade notice: {e}")

    def detect_faces(
        self, frame: np.ndarray, confidence_threshold: float = 0.5
    ) -> List[Tuple[int, int, int, int]]:
        """
        Detect faces in frame.
        Returns list of bboxes: [(x, y, w, h), ...]
        """
        if frame is None or cv2 is None:
            return []

        h_img, w_img = frame.shape[:2]
        bboxes: List[Tuple[int, int, int, int]] = []

        # Strategy A: OpenCV Caffe SSD DNN
        if self.face_net is not None:
            try:
                blob = cv2.dnn.blobFromImage(
                    cv2.resize(frame, (300, 300)),
                    1.0,
                    (300, 300),
                    (104.0, 177.0, 123.0),
                )
                self.face_net.setInput(blob)
                detections = self.face_net.forward()

                for i in range(detections.shape[2]):
                    confidence = detections[0, 0, i, 2]
                    if confidence >= confidence_threshold:
                        box = detections[0, 0, i, 3:7] * np.array(
                            [w_img, h_img, w_img, h_img]
                        )
                        x1, y1, x2, y2 = box.astype("int")
                        x1, y1 = max(0, x1), max(0, y1)
                        x2, y2 = min(w_img, x2), min(h_img, y2)
                        w, h   = x2 - x1, y2 - y1
                        if w > 10 and h > 10:
                            bboxes.append((x1, y1, w, h))
                if bboxes:
                    return bboxes
            except Exception as e:
                logger.debug(f"DNN detection error: {e}")

        # Strategy B: Haar Cascade Fallback
        if self.haar is not None:
            try:
                gray  = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                faces = self.haar.detectMultiScale(
                    gray, scaleFactor=1.1, minNeighbors=4, minSize=(30, 30)
                )
                for (x, y, w, h) in faces:
                    bboxes.append((int(x), int(y), int(w), int(h)))
            except Exception as e:
                logger.debug(f"Haar detection error: {e}")

        return bboxes


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 5 — Spatial Centroid Clustering
# ══════════════════════════════════════════════════════════════════════════════

def cluster_faces(
    frame_detections: List[List[Tuple[int, int, int, int]]],
    merge_threshold: float = 0.25,
) -> Dict[str, Any]:
    """
    Groups bounding box locations across keyframes into Subject A, B, C...
    """
    clusters: List[Dict] = []

    for frame_idx, bboxes in enumerate(frame_detections):
        for bbox in bboxes:
            x, y, w, h = bbox
            cx, cy     = x + w / 2.0, y + h / 2.0

            matched = False
            for cluster in clusters:
                avg_cx, avg_cy = cluster["centroid"]
                dist = np.sqrt((cx - avg_cx) ** 2 + (cy - avg_cy) ** 2)
                if dist <= (merge_threshold * 1000):
                    cluster["bboxes"].append(bbox)
                    cluster["frames"].append(frame_idx)
                    cluster["centroid"] = (
                        (avg_cx + cx) / 2.0,
                        (avg_cy + cy) / 2.0,
                    )
                    matched = True
                    break

            if not matched:
                clusters.append({
                    "centroid": (cx, cy),
                    "bboxes":   [bbox],
                    "frames":   [frame_idx],
                })

    clusters.sort(key=lambda c: len(c["bboxes"]), reverse=True)
    labels   = ["A", "B", "C", "D", "E"]
    subjects = []
    for idx, c in enumerate(clusters[:5]):
        label     = labels[idx] if idx < len(labels) else f"Subject_{idx + 1}"
        best_bbox = max(c["bboxes"], key=lambda b: b[2] * b[3])
        subjects.append({
            "subject_id":       label,
            "appearance_count": len(c["bboxes"]),
            "best_bbox":        best_bbox,
            "primary":          (idx == 0),
        })

    return {
        "num_detected_faces": sum(len(b) for b in frame_detections),
        "num_subjects":       len(subjects),
        "subjects":           subjects,
    }


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 6 — Pre-Pipeline Scene Intelligence Entrypoint
# ══════════════════════════════════════════════════════════════════════════════

def analyze_scene_pre_pipeline(
    video_path: str,
    creator_name: Optional[str] = None,
    sample_count: int = 12,
) -> Dict[str, Any]:
    """
    Pre-Pipeline Scene & Face Intelligence Layer:
      1. Samples 12 keyframes evenly spaced across video.
      2. Runs OpenCV Res10 300x300 Caffe SSD DNN face detection.
      3. Clusters character subjects spatially (Subject A, B, C).
      4. Checks & stores RAG Creator Face Cache.

    Returns:
        Dict with num_detected_faces, num_subjects, subjects, face_cache_status.
    """
    if not os.path.isfile(video_path):
        raise FileNotFoundError(f"Input video not found: {video_path}")

    if cv2 is None:
        return {
            "num_detected_faces": 0,
            "num_subjects":       0,
            "subjects":           [],
            "face_cache_status":  "error",
        }

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return {
            "num_detected_faces": 0,
            "num_subjects":       0,
            "subjects":           [],
            "face_cache_status":  "error",
        }

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if total_frames <= 0:
        cap.release()
        return {
            "num_detected_faces": 0,
            "num_subjects":       0,
            "subjects":           [],
            "face_cache_status":  "error",
        }

    indices  = np.linspace(0, total_frames - 1, sample_count, dtype=int)
    detector = OpenCVFaceDetector()

    frame_detections: List[List[Tuple[int, int, int, int]]] = []
    best_creator_frame: Optional[np.ndarray]            = None
    best_creator_bbox:  Optional[Tuple[int, int, int, int]] = None
    max_area = 0

    for idx in indices:
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ret, frame = cap.read()
        if not ret or frame is None:
            continue

        bboxes = detector.detect_faces(frame)
        frame_detections.append(bboxes)

        for bbox in bboxes:
            x, y, w, h = bbox
            area = w * h
            if area > max_area:
                max_area           = area
                best_creator_frame = frame.copy()
                best_creator_bbox  = bbox

    cap.release()

    cluster_res = cluster_faces(frame_detections)

    face_cache_status = "none"
    if creator_name:
        cached_img = load_cached_face(creator_name)
        if cached_img is not None:
            face_cache_status = "hit"
        elif best_creator_frame is not None and best_creator_bbox is not None:
            if save_face_cache(creator_name, best_creator_frame, best_creator_bbox):
                face_cache_status = "saved"

    cluster_res["face_cache_status"] = face_cache_status
    cluster_res["creator_name"]      = creator_name or "unknown"
    return cluster_res
