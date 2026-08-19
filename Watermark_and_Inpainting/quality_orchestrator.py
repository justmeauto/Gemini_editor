"""
Visual Safety & Quality Orchestrator
------------------------------------
Governs spatial effects (Vignette) based on Face/Watermark geometry.
Strict adherence to User Ruleset #65.

Role:
- Face Detection (OpenCV DNN)
- Geometry Mapping (Shared Space)
- Policy Decision (Vignette Allowed?)

Output: Structured Decision Object (JSON-compatible dict)
"""

import cv2
import numpy as np
import os
import logging

logger = logging.getLogger("quality_orchestrator")

class HumanPresenceGuard:
    def __init__(self):
        self.face_net = None
        self.cascade_detector = None
        self._load_face_model()

    def _load_face_model(self):
        """Loads OpenCV DNN Face Detector (ResNet-10) with Haar Cascade fallback"""
        try:
            # First try Haar Cascade fallback to guarantee we have a detector even if cv2.dnn is broken/stubbed
            try:
                if hasattr(cv2, "CascadeClassifier"):
                    cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
                    if os.path.exists(cascade_path):
                        self.cascade_detector = cv2.CascadeClassifier(cascade_path)
                else:
                    logger.debug("cv2.CascadeClassifier not available in this OpenCV installation")
            except Exception as ce:
                logger.debug(f"Haar Cascade init error: {ce}")

            # Now try loading DNN Caffe model
            if hasattr(cv2, "dnn") and hasattr(cv2.dnn, "readNetFromCaffe"):
                # Search in current dir (isolated), parent dir (root), and root models dir
                base_dir = os.path.dirname(os.path.abspath(__file__))
                parent_dir = os.path.dirname(base_dir)
                root_dir = os.path.dirname(parent_dir)  # Go up to AMTCE root
                
                # Candidates
                candidates = [
                    os.path.join(base_dir, "models"),
                    os.path.join(parent_dir, "models"),
                    os.path.join(root_dir, "models")
                ]
                
                proto = None
                model = None
                
                logger.debug(f"HumanGuard: Searching for models in: {candidates}")
                
                for c in candidates:
                    p = os.path.join(c, "deploy.prototxt")
                    m = os.path.join(c, "res10_300x300_ssd_iter_140000.caffemodel")
                    logger.debug(f"HumanGuard: Checking {c}: proto exists={os.path.exists(p)}, model exists={os.path.exists(m)}")
                    if os.path.exists(p) and os.path.exists(m):
                        proto = p
                        model = m
                        logger.info(f"HumanGuard: Found models in {c}")
                        break
                
                if proto and model:
                    # Use readNet instead of readNetFromCaffe (deprecated in OpenCV 5.x)
                    try:
                        self.face_net = cv2.dnn.readNet(proto, model)
                        logger.info("✅ HumanGuard: Loaded DNN Identity Detector")
                        return
                    except Exception as e:
                        logger.warning(f"Failed to load DNN with readNet: {e}")
                        # Try the old method if available
                        if hasattr(cv2.dnn, "readNetFromCaffe"):
                            self.face_net = cv2.dnn.readNetFromCaffe(proto, model)
                            logger.info("✅ HumanGuard: Loaded DNN Identity Detector (legacy method)")
                            return
            
            # If Caffe loader not present/fails but Haar Cascade is loaded:
            if self.cascade_detector is not None:
                logger.info("✅ HumanGuard: Loaded Haar Cascade Face Detector (DNN fallback)")
            else:
                logger.warning("⚠️ HumanGuard: No Face Detector loaded (neither DNN nor Haar Cascade). Assuming NO HUMANS (CAUTION).")
        except Exception as e:
            logger.error(f"HumanGuard Init Error: {e}")
            self.face_net = None

    def detect_faces(self, frame):
        """
        Returns list of faces: {'box': [x,y,w,h], 'confidence': float}
        STRICT: Only returns faces with confidence >= 0.6
        """
        # If we have DNN, use it (higher accuracy)
        if self.face_net is not None:
            try:
                h_img, w_img = frame.shape[:2]
                blob = cv2.dnn.blobFromImage(cv2.resize(frame, (300, 300)), 1.0, (300, 300), (104.0, 177.0, 123.0))
                self.face_net.setInput(blob)
                detections = self.face_net.forward()
                
                faces = []
                for i in range(detections.shape[2]):
                    confidence = detections[0, 0, i, 2]
                    if confidence < 0.6: continue
                    box = detections[0, 0, i, 3:7] * np.array([w_img, h_img, w_img, h_img])
                    (x1, y1, x2, y2) = box.astype("int")
                    x1 = max(0, x1); y1 = max(0, y1)
                    x2 = min(w_img-1, x2); y2 = min(h_img-1, y2)
                    w = x2 - x1
                    h = y2 - y1
                    if w > 0 and h > 0:
                        faces.append({
                            'box': [x1, y1, w, h],
                            'confidence': float(confidence)
                        })
                return faces
            except Exception as e:
                logger.warning(f"DNN Face detection forward failed: {e}. Falling back to Haar Cascade.")

        # Fallback to Haar Cascade
        if self.cascade_detector is not None:
            try:
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                detected = self.cascade_detector.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30))
                faces = []
                for (x, y, w, h) in detected:
                    faces.append({
                        'box': [int(x), int(y), int(w), int(h)],
                        'confidence': 0.7  # Default dummy confidence for Haar Cascade match
                    })
                return faces
            except Exception as e:
                logger.warning(f"Haar Cascade detection failed: {e}")

        return []

    def analyze_human_presence(self, frame_path: str) -> dict:
        """
        Primary Quality Signal:
        Detects if humans are present to GATE risky enhancements.

        Returns:
            {
              "has_humans": bool,
              "safety_level": "SAFE_SCENERY" | "CAUTION_HUMAN" | "UNKNOWN"
            }
        """
        try:
            if self.face_net is None and self.cascade_detector is None:
                return {"has_humans": False, "safety_level": "UNKNOWN"}

            frame = cv2.imread(frame_path)
            if frame is None:
                return {"has_humans": False, "safety_level": "UNKNOWN"}
                
            faces = self.detect_faces(frame)
            
            if faces:
                # Human detected -> Enforce constraints
                return {
                    "has_humans": True,
                    "safety_level": "CAUTION_HUMAN"
                }
            else:
                # No human -> Allow stronger processing
                return {
                    "has_humans": False, 
                    "safety_level": "SAFE_SCENERY"
                }

        except Exception as e:
            logger.error(f"Human Guard Failed: {e}")
            # Fail-safe: Assume humans exist to be safe? Or unknown?
            # "OpenCV DNN exists to Protect humans" -> If error, assume Human to be safe.
            return {"has_humans": True, "safety_level": "CAUTION_FAILSAFE"}

# Singleton
human_guard = HumanPresenceGuard()
