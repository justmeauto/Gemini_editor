"""
backfill_pool_metadata.py
One-time script to merge all existing Original_audio/beats/*_lyric.json files
into pool_metadata.json["files"], making it the unified audio index.

Run once from the project root:
    python scripts/backfill_pool_metadata.py
"""
import os
import sys
import json

# Ensure project root is on path
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from Audio_Modules.audio_pool_manager import AudioPoolManager

def main():
    beats_dir = os.path.join(_ROOT, "Original_audio", "beats")
    if not os.path.isdir(beats_dir):
        print(f"[BACKFILL] beats/ dir not found: {beats_dir}")
        return

    pm = AudioPoolManager()
    merged = 0
    skipped = 0
    created = 0

    lyric_files = [f for f in os.listdir(beats_dir) if f.endswith("_lyric.json")]
    print(f"[BACKFILL] Found {len(lyric_files)} _lyric.json files to process...")

    for lyric_fname in sorted(lyric_files):
        lyric_path = os.path.join(beats_dir, lyric_fname)
        # e.g. "Zareena_khan_lyric.json" -> "Zareena_khan.mp3"
        audio_base = lyric_fname[:-len("_lyric.json")]  # strip _lyric.json suffix

        # Try both .mp3 and .wav extensions
        track_filename = None
        for ext in (".mp3", ".wav", ".m4a"):
            candidate = audio_base + ext
            if pm._get_file_metadata(candidate) is not None:
                track_filename = candidate
                break

        # If not registered in pool yet, default to .mp3 (will create a stub entry)
        if track_filename is None:
            track_filename = audio_base + ".mp3"
            print(f"  [BACKFILL] No pool entry for '{audio_base}' — will create stub + merge")
            created += 1

        try:
            with open(lyric_path, "r", encoding="utf-8") as f:
                lyric_data = json.load(f)
        except Exception as e:
            print(f"  [BACKFILL] ERROR Failed to read {lyric_fname}: {e}")
            skipped += 1
            continue

        ok = pm.merge_lyric_into_pool(track_filename, lyric_data)
        if ok:
            merged += 1
            bpm = lyric_data.get("tempo_bpm", "?")
            emotion = lyric_data.get("dominant_emotion", "?")
            print(f"  [BACKFILL] OK {track_filename:<35} bpm={bpm} emotion={emotion}")
        else:
            skipped += 1
            print(f"  [BACKFILL] WARN Merge failed for {track_filename}")

    print(f"\n[BACKFILL] Done — merged={merged}, created_stubs={created}, skipped={skipped}")
    print(f"[BACKFILL] pool_metadata.json now has {len(pm.get_files_index())} entries with lyric intel.")

if __name__ == "__main__":
    main()
