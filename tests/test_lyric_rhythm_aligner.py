"""
Unit tests for Gemini_Modules/lyric_rhythm_aligner.py (v2.1 prompt-locked fusion)
"""

import unittest
import json
import os
import sys

# Ensure module import path
_BOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BOT_DIR not in sys.path:
    sys.path.insert(0, _BOT_DIR)

from Gemini_Modules.lyric_rhythm_aligner import (
    _PROMPT_VERSION,
    _PROMPT,
    _empty_report,
    _clean_json,
    _validate_and_enrich,
    _build_transcript_context,
    _build_math_context,
    VALID_SECTIONS,
)

class TestLyricRhythmAligner(unittest.TestCase):

    def test_prompt_version_and_rules(self):
        self.assertEqual(_PROMPT_VERSION, "2.1-timestamp-locked-text-correction")
        self.assertIn("TIMESTAMP LOCK", _PROMPT)
        self.assertIn("asr_raw", _PROMPT)
        self.assertIn("asr_confidence", _PROMPT)

    def test_empty_report(self):
        report = _empty_report()
        self.assertIsInstance(report, dict)
        self.assertFalse(report["has_vocals"])
        self.assertEqual(report["_source"], "fallback")
        self.assertEqual(report["lyrics"], [])
        self.assertEqual(report["sections"], [])

    def test_clean_json(self):
        # Test markdown stripping
        raw_markdown = "```json\n{\"has_vocals\": true, \"tempo_bpm\": 120.0}\n```"
        cleaned = _clean_json(raw_markdown)
        data = json.loads(cleaned)
        self.assertTrue(data["has_vocals"])
        self.assertEqual(data["tempo_bpm"], 120.0)

        # Test trailing comma cleanup
        raw_trailing = '{"items": [1, 2, 3,], "name": "test",}'
        cleaned_trailing = _clean_json(raw_trailing)
        data_trailing = json.loads(cleaned_trailing)
        self.assertEqual(data_trailing["items"], [1, 2, 3])

    def test_validate_and_enrich(self):
        raw_report = {
            "tempo_bpm": 128.0,
            "sections": [
                {"start": 10.0, "end": 20.0, "type": "unknown_section", "energy": 1.5},
                {"start": 0.0, "end": 10.0, "type": "intro", "energy": 0.2}
            ],
            "tension_arc": [
                {"time": 5.0, "tension": 0.3},
                {"time": 1.0, "tension": 0.1}
            ],
            "lyrics": [
                {
                    "time": 12.0,
                    "end": 14.0,
                    "text": "High emotion word",
                    "emotion_weight": 0.9,
                    "emotion_tag": "hype"
                }
            ],
            "shot_directives": []
        }

        enriched = _validate_and_enrich(raw_report)

        # Section sorting & type fallback
        self.assertEqual(len(enriched["sections"]), 2)
        self.assertEqual(enriched["sections"][0]["type"], "intro")
        self.assertEqual(enriched["sections"][1]["type"], "verse") # fallback from unknown
        self.assertEqual(enriched["sections"][1]["energy"], 1.0) # clamped to 1.0

        # Tension arc sorting
        self.assertEqual(enriched["tension_arc"][0]["time"], 1.0)

        # Bar duration auto-calculation: 4 * 60 / 128 = 1.875
        self.assertEqual(enriched["bar_duration_sec"], 1.875)

        # Derived shot directives from high-emotion lyric
        self.assertTrue(len(enriched["shot_directives"]) > 0)
        self.assertEqual(enriched["shot_directives"][0]["directive"], "fast_action")

    def test_build_transcript_context(self):
        whisper_data = {
            "has_speech": True,
            "words": [
                {"word": "hello", "start": 1.2, "end": 1.5},
                {"word": "world", "start": 1.6, "end": 2.0}
            ]
        }
        context = _build_transcript_context(whisper_data)
        self.assertIn("[1.20-1.50] hello", context)
        self.assertIn("[1.60-2.00] world", context)
        self.assertIn("TIMESTAMP LOCK INSTRUCTIONS", context)

    def test_build_math_context(self):
        fast_report = {
            "tempo_bpm": 120.0,
            "bar_duration_sec": 2.0,
            "energy_profile": "high",
            "tension_arc": [{"time": 0.0}, {"time": 1.0}, {"time": 2.0}],
            "emotional_peak_moments": [10.5]
        }
        context = _build_math_context(fast_report)
        self.assertIn("120.0", context)
        self.assertIn("10.5", context)
        self.assertIn("MACHINE-COMPUTED BEAT/TEMPO DATA", context)

if __name__ == "__main__":
    unittest.main()
