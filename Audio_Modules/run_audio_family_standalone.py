#!/usr/bin/env python3
"""
Audio Family CLI Master Trigger (main.py)
=========================================
Command-line interface to execute the Audio & Beat Synchronization Family
pipeline standalone on any video clip.

Usage Examples:
---------------
  1. Basic Run (Input Video only):
     python run_audio_family_standalone.py --input D:\\AMTCE\\downloads\\Video_by_ruhisingh12.mp4

  2. Custom Output Path & Optional Video Title:
     python run_audio_family_standalone.py -i video.mp4 -o output.mp4 --title "Bollywood Dance"

  3. Custom BGM Track & Disable Gemini Lyric Sync:
     python run_audio_family_standalone.py -i video.mp4 --bgm music.mp3 --no-lyric-sync

  4. View Help & Understanding:
     python run_audio_family_standalone.py --help
"""

import os
import sys
import argparse
import logging
from typing import Optional

# Ensure workspace root (D:\AMTCE) and local dir are on Python path
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

from audio_family_pipeline import AudioFamilyPipeline

# Configure clean console logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("AudioFamilyCLI")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python run_audio_family_standalone.py",
        description="""
===============================================================================
AMTCE AUDIO & BEAT SYNCHRONIZATION FAMILY -- CLI MASTER TRIGGER
===============================================================================
Executes the unified 7-module psycho-acoustic audio pipeline standalone:
  1. audio_extractor.py       -> Extracts mono 16kHz WAV from input video
  2. beat_engine.py           -> Runs FFT peak analysis (BPM, drops, vibe)
  3. lyric_rhythm_aligner.py  -> Gemini multimodal analysis (emotions, peaks)
  4. music_intelligence.py    -> Classifies genre & derives dynamic volume
  5. audio_pool_manager.py    -> Matches & selects best BGM from pool
  5b. beat_engine (Re-run)   -> Re-analyzes selected BGM to lock beat sync
  6. audio_pipeline.py        -> Multi-track mix with sidechain ducking
===============================================================================
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=True
    )

    # Required Arguments
    required_group = parser.add_argument_group("Required Arguments")
    required_group.add_argument(
        "-i", "--input",
        type=str,
        required=True,
        metavar="PATH",
        help="Path to the input video file (e.g. D:\\AMTCE\\downloads\\video.mp4)"
    )

    # Optional Arguments
    optional_group = parser.add_argument_group("Optional Arguments")
    optional_group.add_argument(
        "-o", "--output",
        type=str,
        default=None,
        metavar="PATH",
        help="Destination path for output synced video (Default: <input>_audio_synced.mp4)"
    )
    optional_group.add_argument(
        "-t", "--title",
        type=str,
        default="",
        metavar="TEXT",
        help="Optional title or topic descriptor for the video (e.g. 'Bollywood Dance')"
    )
    optional_group.add_argument(
        "-b", "--bgm",
        type=str,
        default=None,
        metavar="PATH",
        help="Path to a specific background music track (Default: auto-select from pool)"
    )
    optional_group.add_argument(
        "--no-pool",
        action="store_true",
        help="Disable AudioPoolManager BGM rotation and selection"
    )
    optional_group.add_argument(
        "--no-lyric-sync",
        action="store_true",
        help="Disable Gemini lyric & tension arc analysis (saves API calls)"
    )
    optional_group.add_argument(
        "--vo-vol",
        type=float,
        default=1.5,
        metavar="FLOAT",
        help="Voiceover volume multiplier (Default: 1.5)"
    )

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    # Validate Input Video File
    input_path = os.path.abspath(args.input)
    if not os.path.exists(input_path):
        print(f"\n❌ Error: Input video file does not exist: {input_path}\n")
        sys.exit(1)

    # Determine Output Path if not provided
    if args.output:
        output_path = os.path.abspath(args.output)
    else:
        base, ext = os.path.splitext(input_path)
        output_path = f"{base}_audio_synced{ext}"

    # Ensure output parent directory exists
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    print("\n" + "="*70)
    print("AMTCE AUDIO FAMILY STANDALONE PIPELINE TRIGGER")
    print("="*70)
    print(f"  Input Video  : {input_path}")
    print(f"  Output Video : {output_path}")
    if args.title:
        print(f"  Video Title  : {args.title}")
    if args.bgm:
        print(f"  Explicit BGM : {args.bgm}")
    print("="*70 + "\n")

    # Instantiate Audio Family Master Orchestrator
    pipeline = AudioFamilyPipeline(
        music_dir=os.path.join(CURRENT_DIR, "..", "..", "music"),
        original_audio_dir=os.path.join(CURRENT_DIR, "..", "..", "Original_audio"),
        use_pool_manager=not args.no_pool,
        enable_lyric_sync=not args.no_lyric_sync
    )

    # Run Pipeline
    result = pipeline.run(
        video_path=input_path,
        output_path=output_path,
        bgm_path=args.bgm,
        vo_vol=args.vo_vol
    )

    # Print Formatted Results
    print("\n" + "="*70)
    print("EXECUTION RESULTS & DIAGNOSTICS")
    print("="*70)
    print(f"  Elapsed Time     : {result.get('elapsed_sec')} seconds")
    print(f"  Stage 1 Extracted: {result.get('extract_success')}")
    print(f"  Stage 2 Beats    : {result.get('beat_success')} (Tempo: {result['beat_data'].get('tempo')} BPM | Vibe: {result['beat_data'].get('vibe')})")
    print(f"  Stage 3 Lyrics   : {result.get('lyric_success')} (Emotion: {result['lyric_intel'].get('dominant_emotion')})")
    print(f"  Stage 4 Genre    : {result.get('genre')} (Confidence: {result.get('genre_conf'):.2f} | Volume: {result.get('genre_music_vol')})")
    print(f"  Stage 4b Audio Mode: {result.get('audio_mode')} (Original Vol: {'1.0 (Kept)' if result.get('audio_mode')=='performance' else '0.0 (Muted)' if result.get('audio_mode')=='replacement' else '0.35 (Blended)'})")
    print(f"  Stage 5 Pool BGM : {os.path.basename(str(result.get('selected_bgm')))}")
    print(f"  Stage 6 Mix Status: {result.get('mix_success')}")
    print(f"  Degraded Warnings: {result.get('degraded_stages') or 'None (100% Clean)'}")
    print("="*70)

    if result.get("mix_success"):
        print(f"\n[SUCCESS] Final audio-synced video created at:\n   {output_path}\n")
    else:
        print(f"\n[WARNING] Pipeline finished with soft warnings. Check output at:\n   {output_path}\n")


if __name__ == "__main__":
    main()
