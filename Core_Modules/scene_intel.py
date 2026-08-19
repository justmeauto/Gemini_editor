"""
Core_Modules/scene_intel.py — Scene & Face Intelligence Layer
==============================================================
Universal Pre-Pipeline Scene Intelligence Layer for AMTCE Phase 2.

Features:
1. Samples 12 keyframes evenly spaced across video duration.
2. Detects human faces using OpenCV Res10 300x300 Caffe SSD DNN (with Haar Cascade fallback).
3. Clusters character faces spatially (Subject A, B, C...).
4. Maintains RAG Creator Face Store in `cache/face_cache/{creator_name}.jpg` (20% padded crops).
5. Queries Gemini 2.5 Flash Vision with frames + audio candidate metadata for `creative_possibilities`.
6. Generates structured ClipPlan for RhythmTimelineBuilder & FFmpeg Master Director.
"""

import os
import re
import json
import logging
import cv2
import numpy as np
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple

logger = logging.getLogger("scene_intel")

# ── Paths ─────────────────────────────────────────────────────────────────────
# Get the actual AMTCE root (not simpler update)
_current_dir = os.path.dirname(os.path.abspath(__file__))
_simpler_update_dir = os.path.dirname(_current_dir)
_REPO_ROOT = os.path.dirname(_simpler_update_dir)  # Go up to AMTCE root

FACE_CACHE_DIR = Path(os.path.join(_REPO_ROOT, "cache", "face_cache"))
FACE_CACHE_DIR.mkdir(parents=True, exist_ok=True)

DNN_PROTO = Path(os.path.join(_REPO_ROOT, "models", "deploy.prototxt"))
DNN_MODEL = Path(os.path.join(_REPO_ROOT, "models", "res10_300x300_ssd_iter_140000.caffemodel"))

# Haar Cascade Fallback
HAAR_CASCADE_PATH = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"


# ── Face Cache Helpers ────────────────────────────────────────────────────────

def _title_to_cache_key(title: str) -> str:
    """Convert creator handle/title to safe filename key."""
    return re.sub(r"[^a-zA-Z0-9_]", "_", title.strip()).lower()


def load_cached_face(creator_name: str) -> Optional[np.ndarray]:
    """Load previously saved 20%-padded face image for this creator. Returns None if missing."""
    if not creator_name:
        return None
    key = _title_to_cache_key(creator_name)
    path = FACE_CACHE_DIR / f"{key}.jpg"
    if path.exists():
        try:
            img = cv2.imread(str(path))
            if img is not None:
                logger.info(f"👤 [RAG FACE HIT] Loaded creator face cache for @{creator_name}")
                return img
        except Exception as e:
            logger.warning(f"   ⚠ Failed to load face cache {path}: {e}")
    return None


def save_face_cache(creator_name: str, frame: np.ndarray, bbox: Tuple[int, int, int, int]) -> bool:
    """
    Save face crop with 20% padding to `cache/face_cache/{creator_name}.jpg`.
    bbox format: (x, y, w, h)
    """
    if not creator_name or frame is None:
        return False
    try:
        h_img, w_img = frame.shape[:2]
        x, y, w, h = bbox

        # Apply 20% padding around face
        pad_x = int(w * 0.20)
        pad_y = int(h * 0.20)

        x1 = max(0, x - pad_x)
        y1 = max(0, y - pad_y)
        x2 = min(w_img, x + w + pad_x)
        y2 = min(h_img, y + h + pad_y)

        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return False

        key = _title_to_cache_key(creator_name)
        path = FACE_CACHE_DIR / f"{key}.jpg"
        cv2.imwrite(str(path), crop)
        logger.info(f"👤 [RAG FACE STORED] Saved 20%-padded face cache -> {path.name}")
        return True
    except Exception as e:
        logger.warning(f"   ⚠ Failed to save face cache for {creator_name}: {e}")
        return False


# ── OpenCV Res10 300x300 Caffe SSD DNN + Haar Fallback Detector ──────────────

class OpenCVFaceDetector:
    """
    OpenCV DNN Res10 300x300 Caffe SSD Face Detector with Haar Cascade Fallback.
    """

    def __init__(self):
        self.face_net = None
        self.haar = None

        if DNN_PROTO.exists() and DNN_MODEL.exists():
            # Try DNN Caffe model first
            if hasattr(cv2, "dnn"):
                try:
                    # Use readNet instead of readNetFromCaffe (deprecated in OpenCV 5.x)
                    self.face_net = cv2.dnn.readNet(str(DNN_PROTO), str(DNN_MODEL))
                    logger.info("✅ SceneIntel: Loaded DNN Face Detector")
                    return
                except Exception as e:
                    logger.warning(f"Failed to load DNN with readNet: {e}")
                    # Try the old method if available
                    if hasattr(cv2.dnn, "readNetFromCaffe"):
                        try:
                            self.face_net = cv2.dnn.readNetFromCaffe(str(DNN_PROTO), str(DNN_MODEL))
                            logger.info("✅ SceneIntel: Loaded DNN Face Detector (legacy method)")
                            return
                        except Exception as e2:
                            logger.warning(f"Failed to load DNN with readNetFromCaffe: {e2}")

        if self.face_net is None:
            try:
                cascade_path = getattr(cv2.data, "haarcascades", "") + "haarcascade_frontalface_default.xml" if hasattr(cv2, "data") else ""
                if cascade_path and os.path.exists(cascade_path) and hasattr(cv2, "CascadeClassifier"):
                    self.haar = cv2.CascadeClassifier(cascade_path)
                    if not self.haar.empty():
                        logger.info("👁️ OpenCV Haar Cascade Face Detector initialized (fallback).")
                    else:
                        logger.warning("   ⚠ Haar Cascade Classifier failed to load.")
                        self.haar = None
                else:
                    logger.warning("   ⚠ Haar Cascade file not found or cv2.CascadeClassifier not available")
            except Exception as e:
                logger.warning(f"   ⚠ Failed to load Haar Cascade: {e}")

    def detect_faces(self, frame: np.ndarray, confidence_threshold: float = 0.5) -> List[Tuple[int, int, int, int]]:
        """
        Detect faces in frame.
        Returns list of bboxes: [(x, y, w, h), ...]
        """
        if frame is None:
            return []

        h_img, w_img = frame.shape[:2]
        bboxes = []

        # Strategy A: OpenCV Caffe SSD DNN (Res10 300x300)
        if self.net is not None:
            try:
                blob = cv2.dnn.blobFromImage(
                    cv2.resize(frame, (300, 300)),
                    1.0,
                    (300, 300),
                    (104.0, 177.0, 123.0)
                )
                self.net.setInput(blob)
                detections = self.net.forward()

                for i in range(detections.shape[2]):
                    confidence = detections[0, 0, i, 2]
                    if confidence >= confidence_threshold:
                        box = detections[0, 0, i, 3:7] * np.array([w_img, h_img, w_img, h_img])
                        x1, y1, x2, y2 = box.astype("int")
                        x1, y1 = max(0, x1), max(0, y1)
                        x2, y2 = min(w_img, x2), min(h_img, y2)
                        w, h = x2 - x1, y2 - y1
                        if w > 10 and h > 10:
                            bboxes.append((x1, y1, w, h))
                if bboxes:
                    return bboxes
            except Exception as e:
                logger.debug(f"DNN detection error: {e}")

        # Strategy B: Haar Cascade Fallback
        if self.haar is not None:
            try:
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                faces = self.haar.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=4, minSize=(30, 30))
                for (x, y, w, h) in faces:
                    bboxes.append((int(x), int(y), int(w), int(h)))
            except Exception as e:
                logger.debug(f"Haar detection error: {e}")

        return bboxes


# ── Spatial Centroid Clustering ───────────────────────────────────────────────

def cluster_faces(frame_detections: List[List[Tuple[int, int, int, int]]], merge_threshold: float = 0.25) -> Dict[str, Any]:
    """
    Groups bounding box locations across keyframes into Subject A, B, C...
    """
    clusters = []
    for frame_idx, bboxes in enumerate(frame_detections):
        for bbox in bboxes:
            x, y, w, h = bbox
            cx, cy = x + w / 2.0, y + h / 2.0

            # Find matching cluster
            matched = False
            for cluster in clusters:
                avg_cx, avg_cy = cluster["centroid"]
                dist = np.sqrt((cx - avg_cx)**2 + (cy - avg_cy)**2)
                if dist <= (merge_threshold * 1000): # normalized screen distance estimate
                    cluster["bboxes"].append(bbox)
                    cluster["frames"].append(frame_idx)
                    cluster["centroid"] = ((avg_cx + cx) / 2.0, (avg_cy + cy) / 2.0)
                    matched = True
                    break

            if not matched:
                clusters.append({
                    "centroid": (cx, cy),
                    "bboxes": [bbox],
                    "frames": [frame_idx],
                })

    # Sort clusters by frequency/size
    clusters.sort(key=lambda c: len(c["bboxes"]), reverse=True)
    subjects = []
    labels = ["A", "B", "C", "D", "E"]
    for idx, c in enumerate(clusters[:5]):
        label = labels[idx] if idx < len(labels) else f"Subject_{idx+1}"
        best_bbox = max(c["bboxes"], key=lambda b: b[2] * b[3])
        subjects.append({
            "subject_id": label,
            "appearance_count": len(c["bboxes"]),
            "best_bbox": best_bbox,
            "primary": (idx == 0) # Primary creator subject candidate
        })

    return {
        "num_detected_faces": sum(len(b) for b in frame_detections),
        "num_subjects": len(subjects),
        "subjects": subjects
    }


# ── Pre-Pipeline Scene Intelligence Entrypoint ───────────────────────────────

def analyze_scene_pre_pipeline(
    video_path: str,
    creator_name: Optional[str] = None,
    sample_count: int = 12
) -> Dict[str, Any]:
    """
    Pre-Pipeline Scene & Face Intelligence Layer:
    1. Samples 12 keyframes.
    2. Runs OpenCV Res10 300x300 Caffe SSD DNN face detection.
    3. Clusters character subjects spatially (Subject A, B, C).
    4. Checks & stores RAG Creator Face Cache (`cache/face_cache/{creator_name}.jpg`).
    """
    if not os.path.isfile(video_path):
        raise FileNotFoundError(f"Input video not found: {video_path}")

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return {"num_detected_faces": 0, "num_subjects": 0, "subjects": [], "face_cache_status": "error"}

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if total_frames <= 0:
        cap.release()
        return {"num_detected_faces": 0, "num_subjects": 0, "subjects": [], "face_cache_status": "error"}

    indices = np.linspace(0, total_frames - 1, sample_count, dtype=int)
    detector = OpenCVFaceDetector()

    frame_detections = []
    best_creator_frame = None
    best_creator_bbox = None
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
                max_area = area
                best_creator_frame = frame.copy()
                best_creator_bbox = bbox

    cap.release()

    cluster_res = cluster_faces(frame_detections)

    # Manage Creator RAG Face Cache
    face_cache_status = "none"
    if creator_name:
        cached_img = load_cached_face(creator_name)
        if cached_img is not None:
            face_cache_status = "hit"
        elif best_creator_frame is not None and best_creator_bbox is not None:
            if save_face_cache(creator_name, best_creator_frame, best_creator_bbox):
                face_cache_status = "saved"

    cluster_res["face_cache_status"] = face_cache_status
    cluster_res["creator_name"] = creator_name or "unknown"
    return cluster_res
