"""
Unit tests for Universal Semantic + Mathematical Audio Intelligence Engine.
Tests:
  1. Creative Archetype Classification (Podcast, Paparazzi Strut, Action, Cinematic, Playful)
  2. 4D Hybrid Scoring & Fatal Clash Disqualification (S_archetype = 0.0)
  3. Strict Speech/Noise Gating in Vault Indexer
  4. End-to-End Archetype-Driven BGM Selection
"""

import unittest
import os
import sys
import tempfile
import json
from unittest.mock import patch

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from Gemini_Modules.lyric_rhythm_aligner import (
    CREATIVE_ARCHETYPES,
    classify_video_archetype,
    compute_4d_audio_score,
    select_best_audio_for_clip,
)
from Telegram_Storage_Modules.telegram_vault_indexer import TelegramVaultIndexer


class TestUniversalAudioIntelligence(unittest.TestCase):

    def test_archetype_classification(self):
        # 1. Talking Head / Podcast
        ctx_pod = {
            "intent": "talking_head",
            "visual_event": "Host speaking into Shure SM7B microphone",
            "tone": "educational",
            "speech_intelligence": {"speech_mode": "on_camera_dialogue", "is_talking_visually": True},
        }
        meta_pod = {"caption": "Full deep dive interview with AI researcher #podcast #ai"}
        arch_pod = classify_video_archetype(ctx_pod, {}, metadata=meta_pod)
        self.assertEqual(arch_pod["name"], "TALKING_HEAD_PODCAST")

        # 2. Paparazzi / Glamour Strut
        ctx_pap = {
            "intent": "candid_walk",
            "visual_event": "Kendall Jenner walking to SUV with paparazzi flashes",
            "tone": "glamorous",
            "speech_intelligence": {"speech_mode": "silent_broll", "is_talking_visually": False},
        }
        meta_pap = {"caption": "Kendall arriving in NYC #kendalljenner #fashion #streetstyle #paparazzi"}
        arch_pap = classify_video_archetype(ctx_pap, {}, metadata=meta_pap)
        self.assertEqual(arch_pap["name"], "GLAMOUR_STRUT_PAPARAZZI")

        # 3. Adrenaline Action
        ctx_act = {
            "intent": "fitness",
            "visual_event": "Heavy barbell deadlift PR attempt in gym",
            "tone": "hype",
            "speech_intelligence": {"speech_mode": "silent_broll", "is_talking_visually": False},
        }
        meta_act = {"caption": "New deadlift record! 500lbs #gym #workout #bodybuilding"}
        arch_act = classify_video_archetype(ctx_act, {}, metadata=meta_act)
        self.assertEqual(arch_act["name"], "ADRENALINE_ACTION")

        # 4. Atmospheric Cinematic
        ctx_cin = {
            "intent": "travel",
            "visual_event": "Drone shot of Amalfi Coast at golden hour",
            "tone": "peaceful",
            "speech_intelligence": {"speech_mode": "silent_broll", "is_talking_visually": False},
        }
        meta_cin = {"caption": "Summer in Positano Italy #travel #italy #cinematic"}
        arch_cin = classify_video_archetype(ctx_cin, {}, metadata=meta_cin)
        self.assertEqual(arch_cin["name"], "ATMOSPHERIC_CINEMATIC")

        # 5. Playful Rhythmic
        ctx_com = {
            "intent": "comedy",
            "visual_event": "Cat knocking glass off table and looking innocent",
            "tone": "humorous",
            "speech_intelligence": {"speech_mode": "silent_broll", "is_talking_visually": False},
        }
        meta_com = {"caption": "He really thought nobody was looking #funny #meme #cats"}
        arch_com = classify_video_archetype(ctx_com, {}, metadata=meta_com)
        self.assertEqual(arch_com["name"], "PLAYFUL_RHYTHMIC")

    def test_4d_scoring_and_fatal_clashes(self):
        arch_pap = CREATIVE_ARCHETYPES["GLAMOUR_STRUT_PAPARAZZI"]
        arch_pod = CREATIVE_ARCHETYPES["TALKING_HEAD_PODCAST"]

        # Track A: Aggressive Heavy Bass Swagger Phonk
        track_swagger = {
            "gemini_genre": "hip_hop",
            "vibe_tags": ["swagger", "bass", "trap", "strut"],
            "energy": 0.85,
            "tempo_bpm": 125.0,
            "has_vocals": False,
            "usage_count": 0,
            "last_used": 0,
        }

        # Track B: Soft Acoustic Ambient Piano
        track_soft = {
            "gemini_genre": "ambient",
            "vibe_tags": ["soft_acoustic", "meditation", "chatter"],
            "energy": 0.25,
            "tempo_bpm": 80.0,
            "has_vocals": False,
            "usage_count": 0,
            "last_used": 0,
        }

        # Track C: Chill Lofi Instrumental Chords
        track_lofi = {
            "gemini_genre": "lofi",
            "vibe_tags": ["chill", "lofi", "ambient", "subtle"],
            "energy": 0.30,
            "tempo_bpm": 85.0,
            "has_vocals": False,
            "usage_count": 0,
            "last_used": 0,
        }

        # Test on PAPARAZZI CLIP:
        # Swagger track MUST have S_archetype = 1.0 and high score
        score_pap_swag, s_arch1, _, _, _ = compute_4d_audio_score(
            archetype=arch_pap,
            candidate_meta=track_swagger,
            candidate_filename="swagger_beat.mp3",
            visual_ctx={"intent": "candid_walk", "tone": "glamour"},
            current_audio={"math": {"tempo_bpm": 125.0}},
        )
        self.assertEqual(s_arch1, 1.0)
        self.assertGreater(score_pap_swag, 0.2)

        # Soft acoustic / ambient MUST be FATALLY DISQUALIFIED (score = 0.0) on Paparazzi
        score_pap_soft, s_arch2, _, _, _ = compute_4d_audio_score(
            archetype=arch_pap,
            candidate_meta=track_soft,
            candidate_filename="soft_acoustic_meditation.mp3",
            visual_ctx={"intent": "candid_walk", "tone": "glamour"},
            current_audio={"math": {"tempo_bpm": 125.0}},
        )
        self.assertEqual(s_arch2, 0.0)
        self.assertEqual(score_pap_soft, 0.0)

        # Test on PODCAST CLIP:
        # Swagger club/phonk track MUST be FATALLY DISQUALIFIED on Podcast
        score_pod_swag, s_arch3, _, _, _ = compute_4d_audio_score(
            archetype=arch_pod,
            candidate_meta=track_swagger,
            candidate_filename="swagger_beat.mp3",
            visual_ctx={"intent": "talking_head"},
            current_audio={"math": {"tempo_bpm": 90.0}},
        )
        self.assertEqual(s_arch3, 0.0)
        self.assertEqual(score_pod_swag, 0.0)

        # Lofi track MUST be accepted on Podcast
        score_pod_lofi, s_arch4, _, _, _ = compute_4d_audio_score(
            archetype=arch_pod,
            candidate_meta=track_lofi,
            candidate_filename="chill_lofi.mp3",
            visual_ctx={"intent": "talking_head"},
            current_audio={"math": {"tempo_bpm": 90.0}},
        )
        self.assertEqual(s_arch4, 1.0)
        self.assertGreater(score_pod_lofi, 0.2)

    def test_strict_noise_and_speech_gating(self):
        import tempfile
        import json
        from unittest.mock import patch

        indexer = TelegramVaultIndexer()
        mock_data = {
            "files": {
                "social_media_id": {
                    "https://www.instagram.com/reel/SpeechOnly/": {
                        "shortcode": "SpeechOnly",
                        "extracted_audio_file_id": "FID_SPEECH",
                        "is_speech_only": True,
                        "audio_data": {"tempo_bpm": 0, "avg_energy": 0.4},
                    },
                    "https://www.instagram.com/reel/PodcastDialogue/": {
                        "shortcode": "PodcastDialogue",
                        "extracted_audio_file_id": "FID_PODCAST",
                        "speech_intelligence": {"speech_mode": "on_camera_dialogue"},
                        "audio_data": {"tempo_bpm": 120, "has_music": False},
                    },
                    "https://www.instagram.com/reel/CrowdNoise/": {
                        "shortcode": "CrowdNoise",
                        "extracted_audio_file_id": "FID_CROWD",
                        "audio_data": {"tempo_bpm": 120, "vibe": "crowd babble"},
                    },
                    "https://www.instagram.com/reel/PureMusic/": {
                        "shortcode": "PureMusic",
                        "extracted_audio_file_id": "FID_MUSIC",
                        "audio_data": {"tempo_bpm": 128.0, "avg_energy": 0.85, "vibe": "swagger"},
                    },
                }
            }
        }

        with tempfile.TemporaryDirectory() as td:
            oa_dir = os.path.join(td, "Original_audio")
            os.makedirs(oa_dir, exist_ok=True)
            mock_pm = os.path.join(oa_dir, "pool_metadata.json")
            with open(mock_pm, "w", encoding="utf-8") as f:
                json.dump(mock_data, f)

            with patch("Telegram_Storage_Modules.telegram_vault_indexer._REPO_ROOT", td):
                pool = indexer.get_vault_audio_pool()

            self.assertIn("PureMusic.wav", pool)
            self.assertNotIn("SpeechOnly.wav", pool)
            self.assertNotIn("PodcastDialogue.wav", pool)
            self.assertNotIn("CrowdNoise.wav", pool)

    def test_pool_manager_lru_fallback_under_all_cooldown(self):
        """When all candidate tracks are in cooldown, pool manager must fall back to LRU track instead of None."""
        from Audio_Modules.audio_pool_manager import AudioPoolManager
        import time

        now = time.time()
        mock_data = {
            "version": 3,
            "files": {
                "TrackRecent.mp3": {
                    "filename": "TrackRecent.mp3",
                    "bpm": 120.0,
                    "energy": 0.8,
                    "duration": 30.0,
                    "last_used": now - 3600,  # 1h ago
                    "usage_count": 2
                },
                "TrackOlder.mp3": {
                    "filename": "TrackOlder.mp3",
                    "bpm": 120.0,
                    "energy": 0.8,
                    "duration": 30.0,
                    "last_used": now - (5 * 3600),  # 5h ago (LRU)
                    "usage_count": 1
                }
            }
        }

        with tempfile.TemporaryDirectory() as td:
            oa_dir = os.path.join(td, "Original_audio")
            act_dir = os.path.join(oa_dir, "active")
            os.makedirs(act_dir, exist_ok=True)
            mock_pm = os.path.join(oa_dir, "pool_metadata.json")
            with open(mock_pm, "w", encoding="utf-8") as f:
                json.dump(mock_data, f)
            # Create physical mock files in active/
            with open(os.path.join(act_dir, "TrackRecent.mp3"), "wb") as f:
                f.write(b"MOCK_AUDIO_DATA_FOR_RECENT" * 100)
            with open(os.path.join(act_dir, "TrackOlder.mp3"), "wb") as f:
                f.write(b"MOCK_AUDIO_DATA_FOR_OLDER" * 100)

            with patch("Telegram_Storage_Modules.telegram_vault_indexer._REPO_ROOT", td):
                pool = AudioPoolManager(base_dir=oa_dir)
                selected = pool.select_best_audio()
            self.assertIsNotNone(selected)
            self.assertIn("TrackOlder.mp3", selected)

    def test_vault_hydration_resolves_from_social_media_id(self):
        """hydrate_bgm_track_from_vault must find extracted_audio_file_id inside social_media_id dictionary."""
        from Telegram_Storage_Modules.telegram_vault_indexer import TelegramVaultIndexer

        indexer = TelegramVaultIndexer()
        mock_data = {
            "version": 3,
            "files": {
                "social_media_id": {
                    "https://www.instagram.com/reel/DeHarvest123/": {
                        "shortcode": "DeHarvest123",
                        "media_file_ids": {
                            "extracted_audio_file_id": "FID_RESOLVED_FROM_SM"
                        }
                    }
                }
            }
        }

        with tempfile.TemporaryDirectory() as td:
            oa_dir = os.path.join(td, "Original_audio")
            os.makedirs(oa_dir, exist_ok=True)
            mock_pm = os.path.join(oa_dir, "pool_metadata.json")
            with open(mock_pm, "w", encoding="utf-8") as f:
                json.dump(mock_data, f)

            with patch("Telegram_Storage_Modules.telegram_vault_indexer._REPO_ROOT", td), \
                 patch.object(indexer, "download_vault_file_by_id", return_value=True) as mock_dl:
                dest_dir = os.path.join(td, "dest")
                res = indexer.hydrate_bgm_track_from_vault("DeHarvest123.wav", dest_dir=dest_dir)
                self.assertIsNotNone(res)
                mock_dl.assert_called_once()
                self.assertEqual(mock_dl.call_args[0][0], "FID_RESOLVED_FROM_SM")


if __name__ == "__main__":
    unittest.main()

