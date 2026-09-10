"""
Test direct vault retrieval of extracted audio using file_id in pool_metadata.json
"""
import os
import sys
import json
import tempfile
import unittest
from unittest.mock import MagicMock, patch

REPO_ROOT = r"d:\editor\Gemini_editor"
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from Telegram_Storage_Modules.telegram_vault_indexer import TelegramVaultIndexer
from Audio_Modules.audio_pool_manager import AudioPoolManager

class TestVaultAudioRetrieval(unittest.TestCase):
    def setUp(self):
        self.indexer = TelegramVaultIndexer()
        self.test_dir = tempfile.mkdtemp()
        self.shortcode = "TestShortcode123"

    def test_hydrate_extracted_audio_from_vault_via_social_media_id(self):
        # Mock pool_metadata entry with extracted_audio_file_id
        mock_entry = {
            "shortcode": self.shortcode,
            "social_media_id": f"https://www.instagram.com/reel/{self.shortcode}/",
            "media_file_ids": {
                "extracted_audio_file_id": "VAULT_TEST_FILE_ID_999"
            }
        }
        
        with patch.object(self.indexer, "find_entry_by_shortcode", return_value=mock_entry), \
             patch.object(self.indexer, "download_vault_file_by_id", side_effect=lambda fid, out: self._fake_download(fid, out)):
            
            result = self.indexer.hydrate_extracted_audio_from_vault(self.shortcode, dest_dir=self.test_dir)
            self.assertIsNotNone(result)
            self.assertTrue(os.path.exists(result))
            self.assertTrue(result.endswith(f"{self.shortcode}.wav"))
            # Also verify video_extracted.wav is mirrored
            mirrored = os.path.join(self.test_dir, "video_extracted.wav")
            self.assertTrue(os.path.exists(mirrored))
            print(f"✅ Hydrated audio via social_media_id file_id: {result}")

    def test_hydrate_extracted_audio_from_vault_via_pool_metadata_files(self):
        # Mock pool_metadata where file_id is in files[track_name]
        with patch.object(self.indexer, "find_entry_by_shortcode", return_value=None), \
             patch.object(AudioPoolManager, "get_track_intelligence", return_value={"file_id": "POOL_TRACK_FILE_ID_777"}), \
             patch.object(self.indexer, "download_vault_file_by_id", side_effect=lambda fid, out: self._fake_download(fid, out)):
            
            result = self.indexer.hydrate_extracted_audio_from_vault(f"{self.shortcode}.wav", dest_dir=self.test_dir)
            self.assertIsNotNone(result)
            self.assertTrue(os.path.exists(result))
            print(f"✅ Hydrated audio via AudioPoolManager track_intelligence file_id: {result}")

    def _fake_download(self, fid, out_path):
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "wb") as f:
            f.write(b"RIFF" + b"\x00" * 2000) # Valid fake WAV > 1024 bytes
        return True

if __name__ == "__main__":
    unittest.main()
