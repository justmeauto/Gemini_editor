"""
AudioFamilyPipeline v2.0 — "Saints With Ego"
=============================================
Every module shares its intelligence with every other module that can use it.
No stage is an island. No knowledge is wasted.

Cross-Module Intelligence Flow (what actually gets shared):
------------------------------------------------------------

  audio_extractor
       │ produces: extracted WAV path
       ▼
  beat_engine  ◄─────────────────────────────────────────────────────────┐
       │ produces: beats, drops, tempo(BPM), avg_energy, vibe            │
       │                                                                  │
       ├──► pool_manager.select_best_audio(target_bpm, target_energy)    │
       │       Pool uses BPM + Energy to find the best-matching BGM      │
       │                                                                  │
       └──► pool_manager.process_new_audio(bpm, energy, beat_analysis)   │
               Pool stores the beat grid so future pipeline runs         │
               skip re-analysis entirely (cache hit)                     │
                                                                         │
  lyric_rhythm_aligner                                                   │
       │ produces: sections, tension_arc, emotional_peaks, vibe_tags,    │
       │           shot_directives, dominant_emotion                     │
       │                                                                  │
       ├──► pool_manager.select_best_audio(content_category=emotion)     │
       │       Emotion tag narrows which BGM Gemini said fits best       │
       │                                                                  │
       └──► audio_pipeline(music_offset=first_emotional_peak)            │
               BGM starts at the first tension peak, not at 0.0s        │
                                                                         │
  music_intelligence                                                     │
       │ produces: genre, confidence, filter_graph                       │
       │                                                                  │
       └──► audio_pipeline(music_vol = genre_preset_vol)                 │
               Genre determines mix volume (lofi=0.35 vs mass=0.55)     │
                                                                         │
  beat_engine (re-run on SELECTED BGM if pool selected a new track) ────┘
       Ensures beat grid matches the ACTUAL BGM going into mix

  audio_pipeline
       receives: video + voiceover + BEST-MATCHED bgm + genre vol + optimal offset
       produces: final mixed output video
"""

import os
import sys
import logging
import tempfile
import time
from typing import Optional, Dict, Any, List

# Ensure workspace root (D:\AMTCE) is in sys.path so Intelligence_Modules can be imported
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

logger = logging.getLogger("AudioFamilyPipeline")


# ─────────────────────────────────────────────────────────────────────────────
# AudioDataPacket — The Family's Shared Memory
# Every module writes here. Every downstream module reads from here.
# ─────────────────────────────────────────────────────────────────────────────

def _empty_packet(
    video_path: str,
    bgm_path: Optional[str],
    voiceover_path: Optional[str],
    output_path: str,
) -> Dict[str, Any]:
    return {
        # ── Inputs ───────────────────────────────────────────────────────────
        "video_path":     video_path,
        "bgm_path":       bgm_path,          # Initial BGM hint (can be None)
        "voiceover_path": voiceover_path,
        "output_path":    output_path,

        # ── Stage 1: audio_extractor ──────────────────────────────────────────
        "extracted_wav":   None,   # Mono 16kHz WAV ripped from video
        "extract_success": False,

        # ── Stage 2: beat_engine (on BGM) ────────────────────────────────────
        # Downstream consumers: pool_manager (select + ingest), audio_pipeline (offset)
        "beat_data": {
            "beats":      [],    # List[{"time": float, "energy": float}]
            "drops":      [],    # List[float] — energy surge timestamps
            "tempo":      0.0,   # BPM — fed to pool_manager.select_best_audio(target_bpm)
            "avg_energy": 0.5,   # 0-1 — fed to pool_manager.select_best_audio(target_energy)
            "vibe":       "groove",
        },
        "beat_success": False,

        # ── Stage 3: lyric_rhythm_aligner (Gemini) ────────────────────────────
        # Downstream consumers: pool_manager (content_category), audio_pipeline (music_offset)
        "lyric_intel": {
            "has_vocals":             False,
            "tempo_bpm":              0.0,
            "dominant_emotion":       "neutral",  # → pool_manager content_category
            "energy_profile":         "medium",
            "sections":               [],
            "tension_arc":            [],
            "lyrics":                 [],
            "emotional_peak_moments": [],  # First value → music_offset in audio_pipeline
            "shot_directives":        [],
            "vibe_tags":              [],
            "_source":                "fallback",
        },
        "lyric_success": False,

        # ── Stage 4: music_intelligence ───────────────────────────────────────
        # Downstream consumers: audio_pipeline (music_vol driven by genre)
        "genre":         "neutral",
        "genre_conf":    0.5,
        "filter_graph":  "",
        "genre_music_vol": 0.40,   # Derived from genre — overrides default music_vol

        # ── Stage 5: pool_manager BGM selection ───────────────────────────────
        # Uses beat_data.tempo + beat_data.avg_energy + lyric_intel.dominant_emotion
        "selected_bgm":     bgm_path,  # Final BGM path fed to audio_pipeline
        "bgm_from_pool":    False,     # True if pool overrode the initial bgm_path

        # ── Stage 5b: beat_engine RE-RUN on selected BGM ──────────────────────
        # If pool selected a different BGM, re-analyze it so mix is in sync
        "final_beat_data":  None,  # Beat data for the ACTUAL bgm going to mix

        # ── Audio Context Routing (Collapse Prevention) ─────────────────────
        "audio_mode":     "replacement", # "replacement" | "performance" | "blend"
        "audio_metadata": {},            # Contract metadata for downstream AMTCE compilation

        # ── Stage 6: audio_pipeline ───────────────────────────────────────────
        "mix_success":   False,

        # ── Pipeline Meta ──────────────────────────────────────────────────────
        "pipeline_version": "2.0.0",
        "pipeline_family":  "Audio_and_Beat_Sync",
        "degraded_stages":  [],
        "elapsed_sec":       0.0,
    }


# ─────────────────────────────────────────────────────────────────────────────
# GENRE → MUSIC VOLUME MAP  (from music_intelligence.py source)
# ─────────────────────────────────────────────────────────────────────────────
_GENRE_VOL = {
    "lofi": 0.35, "romantic": 0.35, "ambient": 0.35, "classical": 0.35,
    "mass": 0.55, "pop": 0.55, "hiphop": 0.55, "high_energy": 0.55,
}

# EMOTION → CONTENT CATEGORY  (fed to pool_manager.select_best_audio content_category)
_EMOTION_TO_CONTENT = {
    "love": "fashion", "joy": "dance", "hype": "fitness",
    "euphoria": "dance", "celebration": "dance", "power": "motivational",
    "sadness": "aesthetic", "intimacy": "fashion", "freedom": "travel",
    "anger": "sports", "nostalgia": "aesthetic", "neutral": "",
}


# ─────────────────────────────────────────────────────────────────────────────
# AudioFamilyPipeline — The Wired, Intelligence-Sharing Family Class
# ─────────────────────────────────────────────────────────────────────────────

class AudioFamilyPipeline:
    """
    Unified Audio & Beat Synchronization pipeline where every module
    shares its intelligence with every other module that can use it.

    Stage order (fixed):
        1. audio_extractor      → WAV extraction
        2. beat_engine          → beat grid (BPM, energy, vibe)
        3. lyric_rhythm_aligner → Gemini intelligence (emotion, peaks, sections)
        4. music_intelligence   → genre classification + filter graph
        5. pool_manager         → BGM selection driven by BPM + energy + emotion
        5b. beat_engine (re-run) → re-analyze selected BGM if pool changed it
        6. audio_pipeline       → final mix (vol + offset from upstream intelligence)

    World Government (AMTCE master pipeline) affiliation:
        AudioFamilyPipeline.register_with_pipeline(amtce_registry)
    """

    _global_pipeline_registry: Optional[Dict[str, Any]] = None
    _family_id: str = "audio_beat_sync_v2"

    def __init__(
        self,
        music_dir: str = "music",
        original_audio_dir: str = "Original_audio",
        use_pool_manager: bool = True,
        enable_lyric_sync: bool = True,
        temp_dir: Optional[str] = None,
    ):
        self.music_dir          = music_dir
        self.original_audio_dir = original_audio_dir
        self.use_pool_manager   = use_pool_manager
        self.enable_lyric_sync  = enable_lyric_sync
        self.temp_dir           = temp_dir or tempfile.gettempdir()

        # Lazy-loaded singletons
        self._beat_engine   = None
        self._pool_manager  = None

        logger.info(
            f"[AudioFamilyPipeline v2] Initialized | "
            f"pool={use_pool_manager} lyric_sync={enable_lyric_sync}"
        )

    # ─────────────────────────────────────────────────────────────────────────
    # WORLD GOVERNMENT HOOK
    # ─────────────────────────────────────────────────────────────────────────

    @classmethod
    def register_with_pipeline(cls, master_registry: Dict[str, Any]) -> None:
        """
        Plug this family into the AMTCE master orchestration registry.

        Future AMTCE usage:
            AudioFamilyPipeline.register_with_pipeline(amtce_registry)
            instance = amtce_registry["audio_beat_sync_v2"]["factory"]()
            result   = instance.run(video_path=..., output_path=...)
        """
        cls._global_pipeline_registry = master_registry
        master_registry[cls._family_id] = {
            "family_id":   cls._family_id,
            "family_name": "Audio & Beat Synchronization",
            "version":     "2.0.0",
            "factory":     lambda **kw: AudioFamilyPipeline(**kw),
            "run_fn":      AudioFamilyPipeline.run,
            "packet_schema": {
                "inputs":  ["video_path", "bgm_path", "voiceover_path", "output_path"],
                "outputs": [
                    "beat_data", "final_beat_data",
                    "lyric_intel", "genre", "genre_music_vol",
                    "filter_graph", "selected_bgm", "mix_success",
                ],
            },
        }
        logger.info(f"[AudioFamilyPipeline] ✅ Registered in master registry: '{cls._family_id}'")

    # ─────────────────────────────────────────────────────────────────────────
    # MAIN ENTRY POINT
    # ─────────────────────────────────────────────────────────────────────────

    def run(
        self,
        video_path: str,
        output_path: str,
        bgm_path: Optional[str] = None,
        voiceover_path: Optional[str] = None,
        vo_vol: float = 1.5,
    ) -> Dict[str, Any]:
        """
        Run the full intelligence-sharing Audio Family pipeline.

        Note: music_vol is NOT a parameter here — it is determined automatically
        by Stage 4 (music_intelligence) based on the detected genre.
        Note: music_offset is NOT a parameter here — it is determined automatically
        by Stage 3 (lyric_aligner) based on the first emotional peak moment.

        Returns the AudioDataPacket dict. Never raises.
        """
        t_start = time.time()
        packet  = _empty_packet(video_path, bgm_path, voiceover_path, output_path)

        try:
            # Stage 1: Extract mono WAV from video
            packet = self._stage_extract_audio(packet)

            # Stage 2: Beat-analyze the initial BGM (or extracted WAV if no BGM)
            packet = self._stage_analyze_beats(packet)

            # Stage 3: Gemini lyric + tension + emotion intelligence
            if self.enable_lyric_sync:
                packet = self._stage_lyric_intelligence(packet)

            # Stage 4: Genre classify → derive music_vol for the mix
            packet = self._stage_classify_genre(packet)

            # Stage 4b: Context-Aware Audio Mode Resolution (Collapse Prevention)
            packet = self._resolve_audio_mode(packet)

            # Stage 5: Pool selects BEST BGM using BPM + energy + emotion from stages 2+3
            if self.use_pool_manager:
                packet = self._stage_pool_select_bgm(packet)
                # Stage 5b: If pool changed the BGM, re-analyze the NEW track
                if packet["bgm_from_pool"]:
                    packet = self._stage_reanalyze_selected_bgm(packet)

            # Stage 6: Final mix — all upstream intelligence feeds in
            packet = self._stage_mix_audio(packet, vo_vol=vo_vol)

        except Exception as fatal:
            logger.error(f"[AudioFamilyPipeline] ❌ Fatal: {fatal}", exc_info=True)
            packet["degraded_stages"].append(f"fatal:{fatal}")

        finally:
            self._cleanup_temp_wav(packet)
            packet["elapsed_sec"] = round(time.time() - t_start, 2)
            self._log_summary(packet)

        return packet

    # ─────────────────────────────────────────────────────────────────────────
    # STAGE 1 — audio_extractor
    # ─────────────────────────────────────────────────────────────────────────

    def _stage_extract_audio(self, packet: Dict[str, Any]) -> Dict[str, Any]:
        """Extract mono 16kHz WAV from the source video."""
        try:
            from audio_extractor import extract_audio

            fd, temp_wav = tempfile.mkstemp(suffix=".wav", dir=self.temp_dir)
            os.close(fd)

            ok = extract_audio(packet["video_path"], temp_wav)
            if ok and os.path.exists(temp_wav) and os.path.getsize(temp_wav) > 1024:
                packet["extracted_wav"]   = temp_wav
                packet["extract_success"] = True
                logger.info(f"[Stage 1] ✅ Extracted WAV: {temp_wav}")
            else:
                try: os.remove(temp_wav)
                except: pass
                packet["degraded_stages"].append("audio_extractor:empty_output")
        except Exception as e:
            logger.warning(f"[Stage 1] ⚠️ audio_extractor: {e}")
            packet["degraded_stages"].append(f"audio_extractor:{e}")

        return packet

    # ─────────────────────────────────────────────────────────────────────────
    # STAGE 2 — beat_engine  →  output feeds Stage 5 (pool selection)
    # ─────────────────────────────────────────────────────────────────────────

    def _stage_analyze_beats(self, packet: Dict[str, Any]) -> Dict[str, Any]:
        """
        Analyze beats on the BGM (preferred) or extracted WAV (fallback).

        What this feeds downstream:
            → Stage 5 pool_manager:
                select_best_audio(target_bpm=beat_data.tempo,
                                  target_energy=beat_data.avg_energy)
            → Stage 5 pool_manager:
                process_new_audio(bpm=beat_data.tempo,
                                  energy=beat_data.avg_energy,
                                  beat_analysis=beat_data)
        """
        try:
            from beat_engine import BeatEngine

            if self._beat_engine is None:
                self._beat_engine = BeatEngine()

            # Prefer clean BGM for beat accuracy (no compression artifacts from video)
            target = packet.get("bgm_path")
            if not target or not os.path.exists(str(target)):
                target = packet.get("extracted_wav")
            if not target or not os.path.exists(str(target)):
                packet["degraded_stages"].append("beat_engine:no_audio_source")
                return packet

            result = self._beat_engine.analyze_beats_with_drops(target)
            if result and result.get("tempo", 0) > 0:
                packet["beat_data"]    = result
                packet["beat_success"] = True
                logger.info(
                    f"[Stage 2] ✅ Beats={len(result['beats'])} "
                    f"BPM={result['tempo']} energy={result['avg_energy']:.2f} "
                    f"vibe={result['vibe']} drops={len(result['drops'])}"
                )
            else:
                packet["degraded_stages"].append("beat_engine:no_beats_detected")
        except Exception as e:
            logger.warning(f"[Stage 2] ⚠️ beat_engine: {e}")
            packet["degraded_stages"].append(f"beat_engine:{e}")

        return packet

    # ─────────────────────────────────────────────────────────────────────────
    # STAGE 3 — lyric_rhythm_aligner  →  output feeds Stage 5 + Stage 6
    # ─────────────────────────────────────────────────────────────────────────

    def _stage_lyric_intelligence(self, packet: Dict[str, Any]) -> Dict[str, Any]:
        """
        Single Gemini call for full music intelligence.

        What this feeds downstream:
            → Stage 5 pool_manager:
                select_best_audio(content_category = emotion→category map)
                Pool finds BGM Gemini already tagged as fitting this emotion.
            → Stage 6 audio_pipeline:
                music_offset = emotional_peak_moments[0]
                BGM starts at the FIRST emotional peak, not 0.0s.
        """
        try:
            from Gemini_Modules.lyric_rhythm_aligner import analyze_music

            bgm = packet.get("bgm_path") or packet.get("extracted_wav")
            if not bgm or not os.path.exists(str(bgm)):
                packet["degraded_stages"].append("lyric_aligner:no_audio")
                return packet

            report = analyze_music(bgm)
            if report and report.get("_source") != "fallback":
                packet["lyric_intel"]  = report
                packet["lyric_success"] = True
                logger.info(
                    f"[Stage 3] ✅ Gemini intel: emotion={report.get('dominant_emotion')} "
                    f"bpm={report.get('tempo_bpm')} sections={len(report.get('sections',[]))} "
                    f"peaks={report.get('emotional_peak_moments')}"
                )
            else:
                packet["degraded_stages"].append("lyric_aligner:gemini_fallback")
        except Exception as e:
            logger.warning(f"[Stage 3] ⚠️ lyric_aligner: {e}")
            packet["degraded_stages"].append(f"lyric_aligner:{e}")

        return packet

    # ─────────────────────────────────────────────────────────────────────────
    # STAGE 4 — music_intelligence  →  output feeds Stage 6 (music_vol)
    # ─────────────────────────────────────────────────────────────────────────

    def _stage_classify_genre(self, packet: Dict[str, Any]) -> Dict[str, Any]:
        """
        Classify BGM genre and derive the music volume for the final mix.

        What this feeds downstream:
            → Stage 6 audio_pipeline:
                music_vol = _GENRE_VOL[genre]
                Lofi/ambient BGM gets 0.35 vol.  Mass/pop gets 0.55 vol.
        """
        try:
            from music_intelligence import classify_music, get_filter_graph

            bgm = packet.get("bgm_path") or packet.get("extracted_wav")
            if not bgm or not os.path.exists(str(bgm)):
                packet["degraded_stages"].append("music_intelligence:no_audio")
                return packet

            genre, conf = classify_music(bgm)
            packet["genre"]      = genre
            packet["genre_conf"] = conf

            # Derive music_vol from genre (this replaces the fixed default 0.2)
            packet["genre_music_vol"] = _GENRE_VOL.get(genre, 0.40)

            # Build filter graph (for reference / future AMTCE filtergraph injection)
            beats = packet["beat_data"].get("beats", [])
            target_dur = beats[-1]["time"] if beats else 30.0
            packet["filter_graph"] = get_filter_graph(genre, target_dur)

            logger.info(
                f"[Stage 4] ✅ Genre={genre} conf={conf:.2f} "
                f"→ music_vol={packet['genre_music_vol']}"
            )
        except Exception as e:
            logger.warning(f"[Stage 4] ⚠️ music_intelligence: {e}")
            packet["degraded_stages"].append(f"music_intelligence:{e}")

        return packet

    # ─────────────────────────────────────────────────────────────────────────
    # STAGE 5 — pool_manager  →  uses BPM + energy + emotion from stages 2+3
    # ─────────────────────────────────────────────────────────────────────────

    def _stage_pool_select_bgm(self, packet: Dict[str, Any]) -> Dict[str, Any]:
        """
        Select the best BGM from the active pool using intelligence from stages 2 & 3.

        Inputs consumed FROM upstream stages:
            beat_data.tempo      → target_bpm for BPM-matched selection
            beat_data.avg_energy → target_energy for energy-matched selection
            lyric_intel.dominant_emotion → content_category (Gemini-enriched pool filter)

        Also calls process_new_audio() to register the extracted audio into the
        pool with its full beat analysis, so future runs can skip re-analysis.
        """
        try:
            from audio_pool_manager import AudioPoolManager

            if self._pool_manager is None:
                self._pool_manager = AudioPoolManager(base_dir=self.original_audio_dir)

            # ── Register extracted audio into pool (with beat data) ────────────
            # This gives the pool the beat grid for future cached selection.
            extracted_wav = packet.get("extracted_wav")
            beat_data     = packet["beat_data"]
            if extracted_wav and os.path.exists(extracted_wav) and beat_data["tempo"] > 0:
                self._pool_manager.process_new_audio(
                    audio_path=extracted_wav,
                    bpm=beat_data["tempo"],
                    energy=beat_data["avg_energy"],
                    beat_analysis=beat_data,
                )
                logger.info(
                    f"[Stage 5] ✅ Registered extracted audio into pool "
                    f"(BPM={beat_data['tempo']}, drops={len(beat_data['drops'])})"
                )

            # ── Select best BGM using upstream intelligence ────────────────────
            dominant_emotion = packet["lyric_intel"].get("dominant_emotion", "neutral")
            content_category = _EMOTION_TO_CONTENT.get(dominant_emotion, "")

            selected = self._pool_manager.select_best_audio(
                target_bpm=beat_data["tempo"],
                target_energy=beat_data["avg_energy"],
                content_category=content_category,
                exclude_path=extracted_wav,  # Never use the video's own audio as BGM
            )

            if selected and os.path.exists(selected):
                # Pool selected a different track than the initial bgm_path hint
                if selected != packet.get("bgm_path"):
                    packet["bgm_from_pool"] = True
                packet["selected_bgm"] = selected
                logger.info(
                    f"[Stage 5] ✅ Pool selected BGM: {os.path.basename(selected)} "
                    f"(emotion={dominant_emotion} → category={content_category})"
                )
            else:
                # Pool empty or failed — fall back to initial bgm_path
                packet["selected_bgm"] = packet.get("bgm_path") or extracted_wav
                packet["degraded_stages"].append("pool_manager:no_track_selected")
                logger.warning("[Stage 5] ⚠️ Pool returned nothing — using initial BGM")

        except Exception as e:
            logger.warning(f"[Stage 5] ⚠️ pool_manager: {e}")
            packet["selected_bgm"] = packet.get("bgm_path")
            packet["degraded_stages"].append(f"pool_manager:{e}")

        return packet

    # ─────────────────────────────────────────────────────────────────────────
    # STAGE 5b — beat_engine RE-RUN on the finally selected BGM
    # ─────────────────────────────────────────────────────────────────────────

    def _stage_reanalyze_selected_bgm(self, packet: Dict[str, Any]) -> Dict[str, Any]:
        """
        If the pool selected a DIFFERENT BGM than the initial hint, re-run
        beat_engine on it. The mix must be driven by the beat grid of the
        ACTUAL BGM going into the output — not the original analysis target.
        """
        try:
            selected = packet.get("selected_bgm")
            if not selected or not os.path.exists(selected):
                return packet

            if self._beat_engine is None:
                from beat_engine import BeatEngine
                self._beat_engine = BeatEngine()

            final_beats = self._beat_engine.analyze_beats_with_drops(selected)
            if final_beats and final_beats.get("beats"):
                packet["final_beat_data"] = final_beats
                logger.info(
                    f"[Stage 5b] ✅ Re-analyzed selected BGM: "
                    f"BPM={final_beats['tempo']} beats={len(final_beats['beats'])}"
                )
            else:
                packet["final_beat_data"] = packet["beat_data"]  # Keep original
                packet["degraded_stages"].append("beat_rerun:empty")

            # Reload lyric intelligence for selected BGM (instantly hits persistent disk cache if present)
            if self.enable_lyric_sync:
                try:
                    from lyric_rhythm_aligner import analyze_music
                    bgm_lyric_intel = analyze_music(selected)
                    if bgm_lyric_intel and bgm_lyric_intel.get("_source") != "fallback":
                        packet["lyric_intel"] = bgm_lyric_intel
                        logger.info(f"[Stage 5b] 🎶 Updated lyric_intel for selected BGM ({bgm_lyric_intel.get('_source')})")
                except Exception as _le:
                    logger.warning(f"[Stage 5b] Lyric intel reload skipped: {_le}")
        except Exception as e:
            logger.warning(f"[Stage 5b] ⚠️ beat re-run: {e}")
            packet["final_beat_data"] = packet["beat_data"]
            packet["degraded_stages"].append(f"beat_rerun:{e}")

        return packet

    def _resolve_audio_mode(self, packet: Dict[str, Any]) -> Dict[str, Any]:
        """
        Context-Aware Audio Routing (Collapse Prevention).

        Determines whether the input video contains primary speech/vocals that must
        be preserved ('performance'), weak background vocals ('blend'), or no vocals ('replacement').
        """
        lyric_intel = packet.get("lyric_intel", {})
        has_vocals = lyric_intel.get("has_vocals", False)
        lang = lyric_intel.get("lang", "").lower()

        if has_vocals and lang in ("hindi", "english", "spanish", "tamil", "telugu", "punjabi", "bengali", "kannada"):
            audio_mode = "performance"
        elif has_vocals:
            audio_mode = "blend"
        else:
            audio_mode = "replacement"

        packet["audio_mode"] = audio_mode
        packet["audio_metadata"] = {
            "audio_mix_mode":       audio_mode,
            "original_muted":        (audio_mode == "replacement"),
            "has_clean_vocal_track": has_vocals,
            "sample_rate_hz":        44100,
        }

        logger.info(
            f"[Stage 4b] 🎚️ Resolved Audio Mode='{audio_mode}' "
            f"(has_vocals={has_vocals}, lang={lang or 'none'})"
        )
        return packet

    # ─────────────────────────────────────────────────────────────────────────
    # STAGE 6 — audio_pipeline  →  consumes ALL upstream intelligence
    # ─────────────────────────────────────────────────────────────────────────

    def _stage_mix_audio(self, packet: Dict[str, Any], vo_vol: float) -> Dict[str, Any]:
        """
        Final audio mix. Every parameter comes from upstream stage outputs — nothing
        is hardcoded here.

        Inputs consumed FROM upstream stages:
            selected_bgm             → Stage 5 pool manager's best BGM
            genre_music_vol          → Stage 4 genre-derived volume level
            lyric_intel.emotional_peak_moments[0] → Stage 3 optimal BGM start offset
            audio_mode               → Stage 4b context-aware routing
        """
        try:
            from audio_pipeline import mix_audio

            # ── Derive music_offset from lyric intelligence ────────────────────
            # Start BGM at first emotional peak so the music drop lands with impact.
            peaks = packet["lyric_intel"].get("emotional_peak_moments", [])
            music_offset = float(peaks[0]) if peaks else 0.0

            # ── Derive music_vol from genre classification ─────────────────────
            music_vol = packet.get("genre_music_vol", 0.40)
            audio_mode = packet.get("audio_mode", "replacement")

            # Use the final beat-analyzed BGM
            bgm = packet.get("selected_bgm") or packet.get("bgm_path")

            logger.info(
                f"[Stage 6] Mixing → bgm={os.path.basename(str(bgm))} "
                f"mode={audio_mode} music_vol={music_vol} music_offset={music_offset:.1f}s "
                f"vo_vol={vo_vol}"
            )

            ok = mix_audio(
                video_path=packet["video_path"],
                output_path=packet["output_path"],
                voiceover_path=packet.get("voiceover_path"),
                music_path=bgm,
                music_vol=music_vol,
                vo_vol=vo_vol,
                music_offset=music_offset,
                audio_mode=audio_mode,
            )
            packet["mix_success"] = bool(ok)
            if ok:
                logger.info(f"[Stage 6] ✅ Final mix ({audio_mode} mode): {packet['output_path']}")
            else:
                packet["degraded_stages"].append("audio_pipeline:mix_false")

        except Exception as e:
            logger.warning(f"[Stage 6] ⚠️ audio_pipeline: {e}")
            packet["degraded_stages"].append(f"audio_pipeline:{e}")

        return packet

    # ─────────────────────────────────────────────────────────────────────────
    # HELPERS
    # ─────────────────────────────────────────────────────────────────────────

    def _cleanup_temp_wav(self, packet: Dict[str, Any]) -> None:
        """Remove temp WAV file created by audio_extractor."""
        wav = packet.get("extracted_wav")
        if wav and os.path.exists(wav) and self.temp_dir in wav:
            try: os.remove(wav)
            except: pass

    def _log_summary(self, packet: Dict[str, Any]) -> None:
        logger.info(
            f"[AudioFamilyPipeline v2] DONE in {packet['elapsed_sec']}s | "
            f"beat={packet['beat_success']} lyric={packet['lyric_success']} "
            f"genre={packet['genre']} vol={packet['genre_music_vol']} "
            f"bgm_from_pool={packet['bgm_from_pool']} "
            f"mix={packet['mix_success']} "
            f"degraded={packet['degraded_stages'] or 'none'}"
        )
