"""
tests/test_lyric_rhythm_aligner_call1.py
=========================================
Integration test for Gemini Call 1 (analyze_music) in Gemini_Modules/lyric_rhythm_aligner.py
using E:\\Creator_Behaviour_Editor\\downloads\\filmygyan_Dc6sUXhCK-S\\video_extracted.wav
"""

import os
import sys
import json
import logging

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Add repo root to path
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("test_call1")

from Gemini_Modules.lyric_rhythm_aligner import analyze_music, _PROMPT_VERSION

def test_gemini_call_1(audio_target: str = None):
    if not audio_target:
        if len(sys.argv) > 1:
            audio_target = sys.argv[1]
        else:
            audio_target = os.path.join(_REPO_ROOT, "downloads", "manual_DcblNXVC4pz", "video_extracted.wav")
    
    audio_path = os.path.abspath(audio_target)
    assert os.path.exists(audio_path), f"Audio file not found at: {audio_path}"

    print(f"\n==================================================")
    print(f"🎵 Testing Gemini Call 1 (analyze_music)")
    print(f"📁 Target Audio: {audio_path}")
    print(f"🏷️ Prompt Version: {_PROMPT_VERSION}")
    print(f"==================================================\n")

    # Force fresh execution by removing stale speech_boundaries.json and lyric cache
    clip_dir = os.path.dirname(audio_path)
    sb_path = os.path.join(clip_dir, "speech_boundaries.json")
    if os.path.exists(sb_path):
        try:
            os.remove(sb_path)
            print(f"🧹 Removed stale speech_boundaries.json: {sb_path}")
        except Exception:
            pass

    cache_dir = os.path.join(_REPO_ROOT, "Original_audio", "beats")
    parent_dir = os.path.basename(os.path.dirname(audio_path))
    raw_basename = os.path.splitext(os.path.basename(audio_path))[0]
    audio_basename = f"{parent_dir}_{raw_basename}" if parent_dir else raw_basename
    cache_json_path = os.path.join(cache_dir, f"{audio_basename}_lyric.json")
    cache_whisper_path = os.path.join(cache_dir, f"{audio_basename}_whisper.json")
    for p in [cache_json_path, cache_whisper_path]:
        if os.path.exists(p):
            try:
                os.remove(p)
                print(f"🧹 Removed stale cache: {p}")
            except Exception:
                pass

    report = analyze_music(audio_path)

    print("\n--- 📊 Musical Intelligence Report Returned ---")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print("--------------------------------------------------\n")

    # Assertions
    required_keys = [
        "has_vocals", "language", "tempo_bpm", "bar_duration_sec",
        "dominant_emotion", "energy_profile", "sections", "tension_arc",
        "lyrics", "emotional_peak_moments", "shot_directives",
        "vibe_tags", "is_unusable", "unusable_reason", "transcript"
    ]
    for key in required_keys:
        assert key in report, f"Missing required key in report: '{key}'"

    print("✅ All required fields present in report!")
    print(f"  • Source: {report.get('_source')}")
    print(f"  • Vocals: {report.get('has_vocals')} | Language: {report.get('language')}")
    print(f"  • BPM: {report.get('tempo_bpm')} | Dominant Emotion: {report.get('dominant_emotion')}")
    print(f"  • Sections count: {len(report.get('sections', []))}")
    print(f"  • Lyrics count: {len(report.get('lyrics', []))}")
    print(f"  • Directives count: {len(report.get('shot_directives', []))}")
    print(f"  • Transcript length: {len(report.get('transcript', ''))} chars")
    print(f"  • Transcript text: {repr(report.get('transcript'))}")

if __name__ == "__main__":
    test_gemini_call_1()
