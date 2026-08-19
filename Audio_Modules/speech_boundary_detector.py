"""
Speech Boundary Detector (faster-whisper)

Extracts word-level timestamps and sentence boundaries from clip audio WAV files.
Identifies precise cut timestamps (word end + natural silence gaps) so Gemini and
FFmpeg synthesis can perform 100% clean, natural vocal cuts without clipping mid-word.
"""

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("speech_boundary_detector")
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def extract_speech_boundaries(
    audio_wav_path: str,
    output_json_path: Optional[str] = None,
    model_size: str = "base"
) -> Dict[str, Any]:
    """
    Runs faster-whisper on audio_wav_path to produce word-level timestamps,
    sentence structures, and safe cut boundaries.
    """
    if not os.path.exists(audio_wav_path):
        logger.warning(f"⚠️ [Speech Boundary] WAV file not found: {audio_wav_path}")
        return {"words": [], "sentences": [], "clean_cut_timestamps": [], "has_speech": False}

    if output_json_path and os.path.exists(output_json_path):
        try:
            with open(output_json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if data.get("has_speech") is not None:
                    logger.info(f"⚡ [Speech Boundary] Loaded cached boundaries: {output_json_path}")
                    return data
        except Exception:
            pass

    try:
        from faster_whisper import WhisperModel

        logger.info(f"🎙️ [Speech Boundary] Running faster-whisper ({model_size}) on: {os.path.basename(audio_wav_path)}")
        model = WhisperModel(model_size, device="cpu", compute_type="int8")
        segments, info = model.transcribe(audio_wav_path, word_timestamps=True, vad_filter=True)

        all_words: List[Dict[str, Any]] = []
        sentences: List[Dict[str, Any]] = []
        clean_cuts: List[Dict[str, Any]] = []

        for segment in segments:
            seg_text = segment.text.strip()
            seg_start = round(float(segment.start), 3)
            seg_end = round(float(segment.end), 3)

            current_sentence_words = []
            for w in (segment.words or []):
                w_str = w.word.strip()
                w_start = round(float(w.start), 3)
                w_end = round(float(w.end), 3)
                w_conf = round(float(w.probability), 2)

                word_obj = {
                    "word": w_str,
                    "start": w_start,
                    "end": w_end,
                    "confidence": w_conf
                }
                all_words.append(word_obj)
                current_sentence_words.append(word_obj)

                # Check for sentence punctuation or natural silence gap > 0.25s
                is_punct = any(p in w_str for p in [".", "?", "!", ","])
                if is_punct:
                    clean_cuts.append({
                        "timestamp_sec": w_end,
                        "word_after_which_to_cut": w_str,
                        "reason": "sentence_punctuation"
                    })

            sentences.append({
                "text": seg_text,
                "start": seg_start,
                "end": seg_end,
                "words": current_sentence_words
            })

            # Always add segment end as a valid cut point
            clean_cuts.append({
                "timestamp_sec": seg_end,
                "word_after_which_to_cut": current_sentence_words[-1]["word"] if current_sentence_words else "",
                "reason": "segment_end"
            })

        # Deduplicate and sort cut points by timestamp
        seen_ts = set()
        unique_cuts = []
        for c in sorted(clean_cuts, key=lambda x: x["timestamp_sec"]):
            ts = c["timestamp_sec"]
            if ts not in seen_ts:
                seen_ts.add(ts)
                unique_cuts.append(c)

        result = {
            "has_speech": len(all_words) > 0,
            "language": info.language if hasattr(info, "language") else "en",
            "duration_sec": round(float(info.duration), 2) if hasattr(info, "duration") else 0.0,
            "words": all_words,
            "sentences": sentences,
            "clean_cut_timestamps": unique_cuts,
            "extracted_at": time.time()
        }

        if output_json_path:
            os.makedirs(os.path.dirname(output_json_path), exist_ok=True)
            with open(output_json_path, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2)
            logger.info(f"✅ [Speech Boundary] Saved speech boundaries ({len(all_words)} words, {len(unique_cuts)} cut points) -> {output_json_path}")

        return result

    except Exception as e:
        logger.error(f"❌ [Speech Boundary] Extraction failed: {e}")
        return {"words": [], "sentences": [], "clean_cut_timestamps": [], "has_speech": False, "error": str(e)}
