"""
Unit & Integration tests for Audio_Modules/audio_strategy.py
and its connections to Phase 2 and MasterAIEditor.
"""
import os
import sys
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from Audio_Modules.audio_strategy import (
    speech_stats,
    decide_audio_strategy,
    clip_card,
    track_card,
    semantic_connection,
    rank_candidates,
    routing_for_strategy,
)


class TestAudioStrategy(unittest.TestCase):

    def test_01_speech_stats_word_timestamps(self):
        whisper = {
            "words": [
                {"word": "hello", "start": 0.5, "end": 0.9},
                {"word": "world", "start": 1.0, "end": 1.4},
                {"word": "this", "start": 1.5, "end": 1.8},
                {"word": "is", "start": 1.9, "end": 2.1},
                {"word": "speech", "start": 2.2, "end": 2.6},
            ]
        }
        stats = speech_stats(whisper, duration=5.0)
        self.assertTrue(stats["known"])
        self.assertEqual(stats["n_words"], 5)
        self.assertGreater(stats["speech_ratio"], 0.3)
        self.assertGreater(stats["wpm"], 50.0)

    def test_02_speech_stats_text_fallback(self):
        whisper = {"text": "hello world welcome to this reel where we test speech"}
        stats = speech_stats(whisper, duration=4.0)
        self.assertTrue(stats["known"])
        self.assertEqual(stats["n_words"], 10)
        self.assertGreater(stats["speech_ratio"], 0.2)
        self.assertGreater(stats["wpm"], 100.0)

    def test_03_speech_stats_empty(self):
        stats = speech_stats(None, duration=10.0)
        self.assertFalse(stats["known"])
        self.assertEqual(stats["speech_ratio"], 0.0)
        self.assertEqual(stats["wpm"], 0.0)

    def test_04_decide_audio_strategy_speech(self):
        stats = {"known": True, "speech_ratio": 0.65, "wpm": 130.0, "n_words": 20}
        visual_ctx = {"is_talking_on_camera": True, "intent": "education"}
        strategy = decide_audio_strategy(visual_ctx, stats, audio_type="speech", has_audio=True)
        self.assertEqual(strategy["action"], "preserve")
        self.assertEqual(strategy["mode"], "speech_primary")
        self.assertFalse(strategy["bgm_allowed"])

    def test_05_decide_audio_strategy_silent_broll(self):
        stats = {"known": False, "speech_ratio": 0.0, "wpm": 0.0, "n_words": 0}
        visual_ctx = {"intent": "aesthetic_travel"}
        strategy = decide_audio_strategy(visual_ctx, stats, audio_type="silence", has_audio=False)
        self.assertEqual(strategy["action"], "replace")
        self.assertEqual(strategy["mode"], "silent_broll")
        self.assertTrue(strategy["bgm_allowed"])

    def test_06_decide_audio_strategy_ambient(self):
        stats = {"known": False, "speech_ratio": 0.0, "wpm": 0.0, "n_words": 0}
        visual_ctx = {"intent": "nature"}
        strategy = decide_audio_strategy(visual_ctx, stats, audio_type="ambient", has_audio=True)
        self.assertEqual(strategy["action"], "preserve")
        self.assertEqual(strategy["mode"], "ambient_natural")
        self.assertEqual(strategy["bed_db"], -16.0)

    def test_07_decide_audio_strategy_music_broll(self):
        stats = {"known": True, "speech_ratio": 0.02, "wpm": 5.0, "n_words": 1}
        visual_ctx = {"is_talking_on_camera": False, "intent": "fashion"}
        strategy = decide_audio_strategy(visual_ctx, stats, audio_type="music", has_audio=True)
        self.assertEqual(strategy["action"], "replace")
        self.assertEqual(strategy["mode"], "music_broll")
        self.assertTrue(strategy["bgm_allowed"])

    def test_08_rank_candidates_both_dict_formats(self):
        cands = [
            {
                "track_name": "energetic_summer_pop.mp3",
                "bpm": 128.0,
                "avg_energy": 0.8,
                "genre": "pop",
                "dominant_emotion": "hype",
                "vibe_tags": ["summer", "energetic"],
            },
            {
                "filename": "sad_piano_slow.mp3",
                "meta": {
                    "genre": "classical",
                    "dominant_emotion": "sadness",
                    "tempo_bpm": 65.0,
                    "energy": 0.2,
                    "vibe_tags": ["melancholy", "piano"],
                },
            },
        ]
        clip_text = "summer pop energetic party reel celebration"
        ranked = rank_candidates(cands, clip_text=clip_text, clip_energy=0.8, clip_bpm=128.0)
        self.assertEqual(len(ranked), 2)
        # energetic_summer_pop should easily win on semantic + energy + bpm
        self.assertEqual(ranked[0].get("track_name"), "energetic_summer_pop.mp3")
        self.assertGreater(ranked[0]["score"], ranked[1]["score"])

    def test_09_routing_for_strategy_protects_speech(self):
        music_routing = {
            "strategy_name": "UPBEAT_DANCE",
            "speed_factor": 1.15,
            "cut_on": "beats",
            "beat_sync": True,
            "bgm_ducking_db": -6.0,
        }
        speech_strategy = {
            "action": "preserve",
            "mode": "speech_primary",
            "bgm_allowed": False,
        }
        guarded = routing_for_strategy(speech_strategy, music_routing)
        self.assertEqual(guarded["speed_factor"], 1.0)  # Never time-stretch speech!
        self.assertEqual(guarded["cut_on"], "speech_boundaries")
        self.assertFalse(guarded["beat_sync"])
        self.assertEqual(guarded["bgm_ducking_db"], -20.0)
        self.assertEqual(guarded["strategy_name"], "SPEECH_FOLLOW_CUTS")

    def test_10_routing_for_strategy_allows_replace(self):
        music_routing = {
            "strategy_name": "UPBEAT_DANCE",
            "speed_factor": 1.15,
            "cut_on": "beats",
            "beat_sync": True,
            "bgm_ducking_db": -6.0,
        }
        replace_strategy = {
            "action": "replace",
            "mode": "silent_broll",
            "bgm_allowed": True,
        }
        guarded = routing_for_strategy(replace_strategy, music_routing)
        self.assertEqual(guarded["speed_factor"], 1.15)
        self.assertEqual(guarded["cut_on"], "beats")
        self.assertTrue(guarded["beat_sync"])


if __name__ == "__main__":
    unittest.main()
