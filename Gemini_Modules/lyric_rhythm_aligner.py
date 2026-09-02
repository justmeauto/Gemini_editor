"""
Audio_Modules/lyric_rhythm_aligner.py
======================================
Musical Intelligence Report — ONE Gemini call, maximum value.

Gemini receives the raw BGM audio file PLUS two grounding-context blocks —
machine-computed beat/tempo math and a word-level ASR transcript — and returns
a full structured intelligence report that drives ALL downstream rhythm
editing decisions:

    1. Lyric timestamps + emotional weight per word/phrase
    2. Section map   (intro / verse / pre-chorus / chorus / drop / bridge / outro)
    3. Tension arc   (0-1 score per second — drives hold vs. cut decisions)
    4. Shot directives (what visual to use at which moment)
    5. Vibe tags     (feeds CreativeBrain niche alignment)
    6. Emotional peak moments (single timestamps for instant-cut triggers)

Why ONE call? Because all 6 outputs share the same audio context window —
splitting them into 6 calls would cost 6x the quota for the same source material.

Controlled by: ENABLE_LYRIC_SYNC=true (default: true)
Gracefully returns empty structure on failure / instrumental audio.

────────────────────────────────────────────────────────────────────────────
CHANGELOG (fusion-precision rewrite)
────────────────────────────────────────────────────────────────────────────
This revision fixes a gap between intent and implementation: the module was
computing beat/tempo math (STEP 1) and running faster-whisper (STEP 2), but
the math was NEVER included in the Gemini prompt — it was only spliced into
the output *after* Gemini had already responded blind. Only sentence-level
(not word-level) ASR text was passed in, capped at 25 lines. The Gemini call
also had a hard 15s wall-clock timeout that guarantees fallback on any real
audio-understanding + large-JSON-generation task, which was almost certainly
the dominant cause of low output quality, not model capability.

Fixed in this version:
  - Math (BPM, beat grid, drop timestamps) is now serialized into the prompt
    as an explicit grounding block, not merged post-hoc.
  - Word-level ASR timestamps (not sentence-capped-at-25) are passed in,
    reused from the Phase-1 speech_boundaries.json when it already exists
    instead of re-running faster-whisper a second time.
  - Gemini call timeout raised to a realistic, configurable default.
  - Fixed a NameError in the upload-cleanup `finally:` block that silently
    caused every uploaded file to leak (never actually deleted).
  - Removed the blind "quote unquoted keys" regex JSON repair, which could
    corrupt lyric text containing colons. Replaced with structural-only
    cleanup + optional `json_repair` library as a verified last resort.
  - Cached / pooled results are stamped with a prompt version so upgrading
    this module doesn't get masked by stale results forever.
────────────────────────────────────────────────────────────────────────────
"""

import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional

try:
    from dotenv import load_dotenv
    if os.path.exists(".env"):
        load_dotenv(".env", override=False)
    _cred_env = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Credentials", ".env")
    if os.path.exists(_cred_env):
        load_dotenv(_cred_env, override=False)
except Exception:
    pass

from google import genai

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

logger = logging.getLogger("lyric_rhythm_aligner")

# ─── Config ───────────────────────────────────────────────────────────────────
ENABLE_LYRIC_SYNC = os.getenv("ENABLE_LYRIC_SYNC", "true").lower() in ("true", "1", "yes")

# Minimum audio file size — skip analysis on tiny/corrupt extracts
_MIN_AUDIO_BYTES = 32_768  # 32 KB

# Which faster-whisper model tier to use IF no Phase-1 transcript already
# exists and we have to run ASR fresh. "base" trades accuracy for speed;
# "medium" (default here) trades speed for accuracy — precision is
# the point, so this defaults to medium. Override with env if you need speed.
WHISPER_MODEL_SIZE = os.getenv("LYRIC_WHISPER_MODEL_SIZE", "medium")

# How long we actually wait for Gemini to finish the audio + math + ASR
# fusion call before giving up and falling back to BeatEngine-only output.
GEMINI_AUDIO_CALL_TIMEOUT_SEC = float(os.getenv("LYRIC_GEMINI_TIMEOUT_SEC", "150"))

# Bumped whenever the prompt/fusion logic changes meaningfully. Cached and
# pooled reports stamped with an older version are treated as stale and
# regenerated, instead of silently serving pre-fix output forever.
_PROMPT_VERSION = "2.1-timestamp-locked-text-correction"

# Section types the system understands downstream
VALID_SECTIONS = {"intro", "verse", "pre_chorus", "chorus", "drop", "bridge", "outro", "instrumental"}

# Emotion → visual shot directive mapping (used when Gemini returns emotion labels)
_EMOTION_DIRECTIVE = {
    "love":        "face_closeup",
    "longing":     "face_closeup",
    "nostalgia":   "face_closeup",
    "joy":         "wide_energetic",
    "euphoria":    "wide_energetic",
    "hype":        "fast_action",
    "power":       "low_angle",
    "sadness":     "slow_zoom_in",
    "intimacy":    "face_closeup",
    "freedom":     "wide_landscape",
    "anger":       "fast_action",
    "celebration": "wide_energetic",
}

# ─── Prompt ───────────────────────────────────────────────────────────────────

_PROMPT = """You are a world-class music supervisor and video editor with expertise in rhythm-based editing.

Listen to this audio track carefully. Extract EVERYTHING needed to edit a viral short-form video to this music.

You will also be given two machine-computed grounding-context blocks after this prompt:
  1. BEAT/TEMPO DATA — exact BPM, beat timestamps, and drop timestamps computed mathematically from
     the waveform. Treat this as ground truth for timing/rhythm; use it to sanity-check your own
     tension_arc and recommended_cut_pace values, not to override what you actually hear.
  2. ASR TRANSCRIPT REFERENCE — a word-level (or sentence-level, if word-level wasn't available)
     speech-to-text transcript with timestamps. This is machine-generated and WILL contain misheard
     words, especially for regional languages, slang, or ad-libs. Treat its TIMESTAMPS as reliable
     timing anchors for where vocals occur, but treat its WORDS only as a hint — correct the actual
     wording yourself by listening to the audio.

Return ONLY a single strict JSON object — no markdown, no explanation, no extra text.

JSON schema:
{
  "has_vocals": true | false,
  "language": "Hindi" | "English" | "Telugu" | "Tamil" | "Spanish" | "Instrumental" | ...,
  "tempo_bpm": <float — overall BPM estimate>,
  "bar_duration_sec": <float — duration of one musical bar in seconds>,
  "dominant_emotion": <string — single best emotion label: joy | love | hype | power | sadness | euphoria | nostalgia | celebration | anger | intimacy | freedom | neutral>,
  "energy_profile": "low" | "medium" | "high" | "building" | "explosive",

  "sections": [
    {
      "start": <float seconds>,
      "end": <float seconds>,
      "type": "intro" | "verse" | "pre_chorus" | "chorus" | "drop" | "bridge" | "outro" | "instrumental",
      "energy": <float 0.0-1.0>,
      "mood": <string — 1-2 word description e.g. "playful", "intense", "melancholic">,
      "recommended_cut_pace": "hold" | "slow" | "medium" | "fast" | "rapid_fire"
    }
  ],

  "tension_arc": [
    { "time": <float seconds>, "tension": <float 0.0-1.0> }
  ],

  "lyrics": [
    {
      "time": <float seconds — COPY EXACTLY from the ASR transcript timestamp. DO NOT change this value>,
      "end": <float seconds — COPY EXACTLY from the ASR transcript timestamp. DO NOT change this value>,
      "text": "<YOUR ONLY JOB HERE: correct the misheard word(s) by listening to the audio. The timestamp is already correct — only fix spelling/wording>",
      "emotion_weight": <float 0.0-1.0 — how emotionally charged this phrase is>,
      "emotion_tag": <string — joy | love | hype | power | sadness | euphoria | nostalgia | celebration | anger | intimacy | freedom | neutral>,
      "section": <string — which section this lyric falls in>,
      "asr_confidence": "high" | "medium" | "low",
      "asr_raw": "<the original whisper text verbatim, before your correction>"
    }
  ],

  "emotional_peak_moments": [<float seconds>, ...],

  "shot_directives": [
    {
      "time": <float seconds>,
      "duration": <float seconds — how long this directive applies>,
      "directive": "face_closeup" | "wide_energetic" | "fast_action" | "slow_zoom_in" | "wide_landscape" | "low_angle" | "match_cut_motion" | "hold_on_subject",
      "priority": <int 1-5 — 5 is most important>,
      "reason": "<brief reason, e.g.: 'chorus drop — maximum energy'>"
    }
  ],

  "vibe_tags": [<string>, ...],

  "instrumental_sections": [
    { "start": <float seconds>, "end": <float seconds> }
  ],

  "is_unusable": true | false,
  "unusable_reason": "<brief explanation if unusable: e.g. 'paparazzi shouting/chatter with no music', 'heavy traffic/car noise', 'trading floor shouting', 'pure mic static' or '' if usable>"
}

RULES:
- is_unusable: set to true ONLY if the audio is unusable non-music noise (e.g. paparazzi chatter, car traffic noise, stock market trading hall shouting, heavy static/distortion with no usable music).

- lyrics TIMESTAMP LOCK (CRITICAL — highest priority rule):
  The `time` and `end` values in every lyrics[] entry MUST be copied verbatim from the ASR transcript
  timestamps provided below. You are FORBIDDEN from changing, rounding, shifting, or estimating these
  values. The ASR timestamps are PCM-measured — they are more accurate than anything you can infer.
  Your ONLY permitted action on each lyrics entry is to correct the `text` field by listening to the
  audio. If a whisper segment sounds completely wrong as a word but the timing is correct, keep the
  timestamp and fix the word. If you hear a vocal that has NO corresponding ASR timestamp, you may add
  a new entry using the nearest beat timestamp from the BEAT/TEMPO DATA below, and set
  `"asr_confidence": "low"` and `"asr_raw": ""` on that entry to flag it as inferred.
  Do NOT truncate the lyrics array — every ASR word/segment must appear as a lyrics entry.
  Set `asr_confidence` to: "high" if whisper word sounds correct, "medium" if plausible but unsure,
  "low" if you had to significantly correct it or infer it from audio alone.

- tension_arc: provide one entry every 1 second (or every beat if BPM > 100). Tension rises into a chorus/drop, falls during verse/outro. Cross-check against the beat/drop timestamps provided below.
- sections: cover the entire track with no gaps. Every second must fall in exactly one section.
- emotional_peak_moments: timestamps where the music hits its hardest emotional/energy peak (typically the first chorus or drop). Maximum 5 entries.
- shot_directives: minimum 3, maximum 12 entries. Focus on the most critical edit decision moments.
- vibe_tags: 3-6 lowercase tags that describe the vibe (e.g. ["festive", "dance", "bollywood", "high_energy", "romantic"]).
- If the audio is very short (< 15s), still provide full structure based on what you can hear.
"""

# ─── Empty / fallback structure ───────────────────────────────────────────────

def _empty_report() -> Dict[str, Any]:
    return {
        "has_vocals": False,
        "language": "Unknown",
        "tempo_bpm": 0.0,
        "bar_duration_sec": 0.0,
        "dominant_emotion": "neutral",
        "energy_profile": "medium",
        "sections": [],
        "tension_arc": [],
        "lyrics": [],
        "emotional_peak_moments": [],
        "shot_directives": [],
        "vibe_tags": [],
        "instrumental_sections": [],
        "is_unusable": False,
        "unusable_reason": "",
        "_source": "fallback",
    }


def _clean_json(text: str) -> str:
    """
    Strip markdown wrappers and extract a JSON object from Gemini's raw text.
    """
    if not text:
        return ""
    if "```" in text:
        m = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
        if m:
            text = m.group(1)
        else:
            text = re.sub(r"```(?:json)?", "", text).replace("```", "")
    j_start = text.find("{")
    j_end   = text.rfind("}")
    if j_start != -1 and j_end > j_start:
        text = text[j_start:j_end + 1].strip()
    else:
        text = text.strip()

    # Structural repair only: trailing commas before closing braces/brackets.
    text = re.sub(r",\s*([\]}])", r"\1", text)

    try:
        json.loads(text)
        return text
    except json.JSONDecodeError:
        pass

    # Optional last resort: pip install json_repair --break-system-packages
    try:
        from json_repair import repair_json
        repaired = repair_json(text)
        json.loads(repaired)  # verify before trusting it
        return repaired
    except Exception:
        pass

    return text


def _validate_and_enrich(report: Dict[str, Any]) -> Dict[str, Any]:
    """
    Post-process Gemini output:
    - Clamp numeric fields to valid ranges
    - Normalize section types to known values
    - Add `shot_directive` hints derived from lyric emotion tags
    - Sort tension_arc and sections by time
    """
    # Clamp tempo
    report["tempo_bpm"] = max(0.0, float(report.get("tempo_bpm", 0.0)))
    report["bar_duration_sec"] = max(0.0, float(report.get("bar_duration_sec", 0.0)))

    # Auto-compute bar duration if Gemini skipped it
    if report["bar_duration_sec"] == 0.0 and report["tempo_bpm"] > 0:
        report["bar_duration_sec"] = round(4 * 60.0 / report["tempo_bpm"], 3)

    # Normalize sections
    for sec in report.get("sections", []):
        sec["start"] = float(sec.get("start", 0.0))
        sec["end"]   = float(sec.get("end",   0.0))
        sec["energy"] = max(0.0, min(1.0, float(sec.get("energy", 0.5))))
        if sec.get("type") not in VALID_SECTIONS:
            sec["type"] = "verse"  # safe default
    report["sections"] = sorted(report.get("sections", []), key=lambda x: x["start"])

    # Sort tension arc
    report["tension_arc"] = sorted(
        report.get("tension_arc", []),
        key=lambda x: float(x.get("time", 0.0))
    )

    # Clamp tension values
    for pt in report["tension_arc"]:
        pt["tension"] = max(0.0, min(1.0, float(pt.get("tension", 0.5))))

    # Sort lyrics
    report["lyrics"] = sorted(
        report.get("lyrics", []),
        key=lambda x: float(x.get("time", 0.0))
    )
    for lyric in report["lyrics"]:
        lyric["emotion_weight"] = max(0.0, min(1.0, float(lyric.get("emotion_weight", 0.5))))

    # Derive shot directives from lyrics if Gemini didn't provide them
    existing_directive_times = {d.get("time") for d in report.get("shot_directives", [])}
    derived = []
    for lyric in report.get("lyrics", []):
        if lyric.get("emotion_weight", 0) >= 0.7 and lyric.get("time") not in existing_directive_times:
            emotion = lyric.get("emotion_tag", "neutral")
            directive = _EMOTION_DIRECTIVE.get(emotion, "hold_on_subject")
            derived.append({
                "time":      lyric["time"],
                "duration":  lyric.get("end", lyric["time"] + 2.0) - lyric["time"],
                "directive": directive,
                "priority":  4,
                "reason":    f"High-emotion lyric: '{lyric.get('text', '')}' ({emotion})",
            })
    report["shot_directives"] = sorted(
        report.get("shot_directives", []) + derived,
        key=lambda x: float(x.get("time", 0.0))
    )

    # Sort emotional peaks
    report["emotional_peak_moments"] = sorted(
        [float(t) for t in report.get("emotional_peak_moments", [])]
    )

    report["is_unusable"] = bool(report.get("is_unusable", False))
    report["unusable_reason"] = str(report.get("unusable_reason", "")).strip()

    report["_source"] = "gemini"
    return report


# ─── Math + ASR grounding-context builders ─────────────────────────────────────

def _normalize_word(w: Dict) -> Optional[Dict[str, Any]]:
    """
    Defensive key-name handling for speech_boundary_detector word entries.
    """
    if not isinstance(w, dict):
        return None
    text = w.get("word") or w.get("text")
    start = w.get("start")
    if start is None:
        start = w.get("start_time")
    if start is None:
        start = w.get("time")
    end = w.get("end")
    if end is None:
        end = w.get("end_time")
    if text is None or start is None:
        return None
    try:
        start_f = float(start)
        end_f = float(end) if end is not None else start_f + 0.3
        return {"text": str(text).strip(), "start": start_f, "end": end_f}
    except (TypeError, ValueError):
        return None


def _load_or_run_whisper(audio_path: str, cache_dir: str, audio_basename: str) -> Dict[str, Any]:
    """
    Reuses the Phase-1 word-level transcript (speech_boundaries.json) when available.
    """
    clip_dir = os.path.dirname(audio_path)
    phase1_path = os.path.join(clip_dir, "speech_boundaries.json")
    if os.path.exists(phase1_path):
        try:
            with open(phase1_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("has_speech") and (data.get("words") or data.get("sentences")):
                logger.info(f"[LYRIC_ALIGNER] Reusing Phase-1 transcript: {phase1_path}")
                return data
        except Exception as e:
            logger.debug(f"[LYRIC_ALIGNER] Could not read Phase-1 transcript: {e}")

    whisper_json_path = os.path.join(cache_dir, f"{audio_basename}_whisper.json")
    if os.path.exists(whisper_json_path):
        try:
            with open(whisper_json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("has_speech") and (data.get("words") or data.get("sentences")):
                logger.info(f"[LYRIC_ALIGNER] Reusing cached whisper pass: {whisper_json_path}")
                return data
        except Exception as e:
            logger.debug(f"[LYRIC_ALIGNER] Could not read whisper cache: {e}")

    try:
        from Audio_Modules.speech_boundary_detector import extract_speech_boundaries
        logger.info(f"[LYRIC_ALIGNER] No prior transcript found — running faster-whisper (model={WHISPER_MODEL_SIZE})")
        result = extract_speech_boundaries(
            audio_path, output_json_path=whisper_json_path, model_size=WHISPER_MODEL_SIZE
        )
        return result or {}
    except Exception as e:
        logger.warning(f"[LYRIC_ALIGNER] Whisper transcription failed: {e}")
        return {}


def _build_transcript_context(whisper_data: Dict[str, Any], max_words: int = 4000) -> str:
    """
    Builds the ASR grounding-context block appended to the prompt.
    """
    if not whisper_data or not whisper_data.get("has_speech"):
        return "\n\n### ASR TRANSCRIPT REFERENCE: none (instrumental or no speech detected)\n"

    words = [w for w in (_normalize_word(w) for w in whisper_data.get("words", [])) if w]

    if words:
        if len(words) > max_words:
            logger.warning(
                f"[LYRIC_ALIGNER] Transcript has {len(words)} words — truncating to {max_words}."
            )
        header = (
            "\n\n### FASTER-WHISPER WORD-LEVEL TRANSCRIPT (AUTHORITATIVE ASR TIMING — "
            f"{len(words)} words, model={WHISPER_MODEL_SIZE}):\n"
        )
        body = " ".join(f'[{w["start"]:.2f}-{w["end"]:.2f}] {w["text"]}' for w in words[:max_words])
    else:
        sentences = whisper_data.get("sentences", [])
        header = "\n\n### FASTER-WHISPER SENTENCE-LEVEL TRANSCRIPT (AUTHORITATIVE ASR TIMING — word-level unavailable):\n"
        body = "\n".join(
            f'[{s.get("start", 0.0):.2f}s - {s.get("end", 0.0):.2f}s]: "{s.get("text", "")}"'
            for s in sentences
        )

    instructions = (
        "\n\n### TIMESTAMP LOCK INSTRUCTIONS (READ CAREFULLY BEFORE BUILDING lyrics[]):\n"
        "Each [start-end] pair above is a PCM-measured timestamp from faster-whisper. "
        "These are the most accurate timing data you will ever have — more accurate than anything "
        "you can infer by listening. Follow these rules without exception:\n"
        "  1. COPY every [start] and [end] value EXACTLY into lyrics[].time and lyrics[].end. "
        "     Do not round, shift, snap to bar, or approximate. Copy the number as-is.\n"
        "  2. Your ONLY permitted edit per entry is lyrics[].text — fix the word Whisper got wrong "
        "     by listening to the audio. The timestamp stays locked.\n"
        "  3. Fill asr_raw with the original whisper word(s) verbatim (before correction).\n"
        "  4. Set asr_confidence=high if the whisper word sounds correct, medium if plausible, "
        "     low if you had to significantly change it.\n"
        "  5. If you hear a clear vocal that has no ASR entry at all, add a new lyrics entry using "
        "     the nearest beat timestamp from the BEAT/TEMPO DATA block, set asr_confidence=low, "
        "     and asr_raw=\"\" to flag it as inferred.\n"
        "  6. Do NOT skip, merge, or drop any ASR entry — every word above must appear as a lyrics entry.\n"
        "  7. Translate regional/Hinglish emotion into English when picking emotion_tag "
        "     (e.g. 'muskurana' -> face_closeup, 'naach' -> wide_energetic)."
    )
    return header + body + instructions


def _build_math_context(fast_report: Dict[str, Any]) -> str:
    """
    Serializes the machine-computed BeatEngine/pool math into a compact JSON
    block appended to the prompt.
    """
    if not fast_report or not fast_report.get("tempo_bpm"):
        return "\n\n### BEAT/TEMPO DATA: unavailable\n"

    def _safe_float(val: Any) -> Optional[float]:
        if isinstance(val, (int, float)):
            return float(val)
        if isinstance(val, dict):
            t = val.get("time")
            if isinstance(t, (int, float)):
                return float(t)
            if isinstance(t, str):
                try:
                    return float(t)
                except ValueError:
                    pass
        elif isinstance(val, str):
            try:
                return float(val)
            except ValueError:
                pass
        return None

    tension_pts = fast_report.get("tension_arc") or []
    beat_times = [
        round(t, 2)
        for t in (_safe_float(p) for p in tension_pts[:300])
        if t is not None
    ]
    drops = [
        round(t, 2)
        for t in (_safe_float(p) for p in (fast_report.get("emotional_peak_moments") or []))
        if t is not None
    ]

    payload = {
        "tempo_bpm": fast_report.get("tempo_bpm"),
        "bar_duration_sec": fast_report.get("bar_duration_sec"),
        "energy_profile": fast_report.get("energy_profile"),
        "beat_timestamps_sec": beat_times,
        "drop_timestamps_sec": drops,
    }
    return (
        "\n\n### MACHINE-COMPUTED BEAT/TEMPO DATA (ground truth from waveform analysis — "
        "cross-check your tension_arc and recommended_cut_pace against this, don't contradict it "
        "without strong audible evidence):\n" + json.dumps(payload) +
        "\nUse the drop timestamps as strong candidates for emotional_peak_moments and beat "
        "timestamps to sanity-check whether a lyric phrase timestamp lands where vocal energy "
        "actually is versus an instrumental gap."
    )


# ─── Main API ─────────────────────────────────────────────────────────────────

def analyze_music(audio_path: str) -> Dict[str, Any]:
    """
    Run the full Musical Intelligence Report on `audio_path`.
    """
    if not ENABLE_LYRIC_SYNC:
        logger.info("[LYRIC_ALIGNER] ENABLE_LYRIC_SYNC=false — skipping.")
        return _empty_report()

    if not audio_path or not os.path.exists(audio_path):
        logger.warning(f"[LYRIC_ALIGNER] Audio file not found: {audio_path}")
        return _empty_report()

    file_size = os.path.getsize(audio_path)
    if file_size < _MIN_AUDIO_BYTES:
        logger.warning(f"[LYRIC_ALIGNER] Audio too small ({file_size}B) — skipping.")
        return _empty_report()

    # Resolve persistent cache file path (Original_audio/beats/<basename>_lyric.json)
    audio_basename = os.path.splitext(os.path.basename(audio_path))[0]
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cache_dir = os.path.join(repo_root, "Original_audio", "beats")
    os.makedirs(cache_dir, exist_ok=True)
    cache_json_path = os.path.join(cache_dir, f"{audio_basename}_lyric.json")

    # 💾 CACHE HIT GUARD: only trust cache generated by the CURRENT prompt/fusion version
    if os.path.exists(cache_json_path) and os.path.getsize(cache_json_path) > 50:
        try:
            with open(cache_json_path, "r", encoding="utf-8") as f:
                cached_report = json.load(f)
            if (
                isinstance(cached_report, dict)
                and cached_report.get("_prompt_version") == _PROMPT_VERSION
                and (cached_report.get("_source") == "gemini" or cached_report.get("lyrics") or cached_report.get("shot_directives"))
            ):
                cached_report["_source"] = "cache_hit"
                logger.info(f"[LYRIC_ALIGNER] 💾 Persistent Cache Hit for '{audio_basename}' -> Loaded from {cache_json_path}")
                return cached_report
            elif isinstance(cached_report, dict):
                logger.info(f"[LYRIC_ALIGNER] Cache for '{audio_basename}' is from an older prompt version — regenerating.")
        except Exception as _ce:
            logger.warning(f"[LYRIC_ALIGNER] Cache read fallback for '{audio_basename}': {_ce}")

    # ── Lookup pool_metadata.json by name (0.000s single source of truth lookup) ────
    pool_meta = None
    try:
        from Audio_Modules.audio_pool_manager import AudioPoolManager
        audio_filename = os.path.basename(audio_path)
        pool_meta = AudioPoolManager().get_track_intelligence(audio_filename)
        if pool_meta and isinstance(pool_meta, dict):
            if pool_meta.get("_prompt_version") == _PROMPT_VERSION and (pool_meta.get("lyrics") or pool_meta.get("shot_directives")):
                logger.info(f"[LYRIC_ALIGNER] ⚡ pool_metadata.json Hit for '{audio_filename}' -> Loaded full semantic intel (0.000s).")
                pool_meta["_source"] = "pool_metadata_hit"
                return pool_meta
            elif pool_meta.get("lyrics") or pool_meta.get("shot_directives"):
                logger.info(f"[LYRIC_ALIGNER] pool_metadata.json intel for '{audio_filename}' is from an older prompt version — regenerating.")
    except Exception as _pme:
        logger.debug(f"[LYRIC_ALIGNER] pool_metadata lookup notice: {_pme}")

    # ⚡ STEP 1: Compute or Reuse BeatEngine Rhythm Math (BPM, Beats, Drops, Tension Arc)
    fast_report = _empty_report()
    if pool_meta and pool_meta.get("tempo_bpm"):
        bpm = float(pool_meta.get("tempo_bpm", 120.0))
        beats = pool_meta.get("beats", [])
        drops = pool_meta.get("drops", [])
        bar_dur = float(pool_meta.get("bar_duration_sec", round(4 * 60.0 / (bpm or 120.0), 3)))
        fast_report = {
            "has_vocals": pool_meta.get("has_vocals", False),
            "language": pool_meta.get("language", "Instrumental"),
            "tempo_bpm": bpm,
            "bar_duration_sec": bar_dur,
            "dominant_emotion": pool_meta.get("dominant_emotion", "hype" if len(drops) > 0 else "joy"),
            "energy_profile": pool_meta.get("energy_profile", "medium"),
            "sections": pool_meta.get("sections", []),
            "tension_arc": pool_meta.get("tension_arc", []),
            "lyrics": pool_meta.get("lyrics", []),
            "emotional_peak_moments": drops[:5] if drops else pool_meta.get("emotional_peak_moments", []),
            "shot_directives": pool_meta.get("shot_directives", []),
            "vibe_tags": pool_meta.get("vibe_tags", []),
            "instrumental_sections": [],
            "is_unusable": False,
            "unusable_reason": "",
            "_source": "pool_metadata_fastpath"
        }
        logger.info(f"⚡ [LYRIC_ALIGNER] Reused pre-computed rhythm math from pool_metadata.json: bpm={bpm:.1f}")
    else:
        try:
            from Audio_Modules.beat_engine import BeatEngine
            be_res = BeatEngine().analyze_beats_with_drops(audio_path)
            bpm = be_res.get("bpm", 120.0)
            beats = be_res.get("beats", [])
            drops = be_res.get("drops", [])
            bar_dur = round(4 * 60.0 / (bpm or 120.0), 3)
            fast_report = {
                "has_vocals": False,
                "language": "Instrumental",
                "tempo_bpm": bpm,
                "bar_duration_sec": bar_dur,
                "dominant_emotion": "hype" if len(drops) > 0 else "joy",
                "energy_profile": "high" if len(drops) > 0 else "medium",
                "sections": [
                    {"start": 0.0, "end": 15.0, "type": "chorus" if len(drops) > 0 else "verse", "energy": 0.85, "mood": "energetic", "recommended_cut_pace": "fast"}
                ],
                "tension_arc": [{"time": b, "tension": 0.8 if b in drops else 0.5} for b in beats[:20]],
                "lyrics": [],
                "emotional_peak_moments": drops[:5],
                "shot_directives": [],
                "vibe_tags": [be_res.get("vibe", "groove")],
                "instrumental_sections": [],
                "is_unusable": False,
                "unusable_reason": "",
                "_source": "beat_engine_fastpath"
            }
            logger.info(f"⚡ [LYRIC_ALIGNER] BeatEngine computed rhythm math: bpm={bpm:.1f}, drops={len(drops)}")
        except Exception as _fe:
            logger.warning(f"Beat engine computation notice: {_fe}")

    # 🎙️ STEP 2: Load or run word-level ASR
    whisper_data = _load_or_run_whisper(audio_path, cache_dir, audio_basename)
    transcript_context = _build_transcript_context(whisper_data)

    # 🧮 STEP 2b: Machine-computed rhythm math, serialized as grounding context.
    math_context = _build_math_context(fast_report)

    # 🧠 STEP 3: Gemini Multimodal Audio Call (Semantic Lyric & Emotion Extraction)
    gemini_router = None
    try:
        from Gemini_Modules.gemini_router_module import gemini_router
    except Exception as _e1:
        try:
            from Gemini_Modules.gemini_router_module.gemini_governor import gemini_router
        except Exception as _e2:
            try:
                from gemini_governor import gemini_router
            except Exception as _e3:
                logger.warning(f"[LYRIC_ALIGNER] gemini_router import failed: {_e1} | {_e2} | {_e3}")
                return fast_report if fast_report.get("tempo_bpm", 0) > 0 else _empty_report()

    analysis_res = []
    _uploaded_file = None
    _client_holder: Dict[str, Any] = {}

    def _worker():
        nonlocal _uploaded_file
        try:
            from google import genai as _genai_client_mod
            _api_key = os.getenv("GEMINI_API_KEY", "") or os.getenv("GOOGLE_API_KEY", "")
            _client = _genai_client_mod.Client(api_key=_api_key)
            _client_holder["client"] = _client
            print(f"  └─ 📤 [LYRIC_ALIGNER] Uploading {os.path.basename(audio_path)} to Gemini API...", flush=True)
            uf = _client.files.upload(file=audio_path)
            _uploaded_file = uf
            _wait = 0
            while getattr(getattr(uf, "state", None), "name", "ACTIVE") == "PROCESSING" and _wait < 10:
                time.sleep(1)
                uf = _client.files.get(name=uf.name)
                _wait += 1
            if getattr(getattr(uf, "state", None), "name", "ACTIVE") != "ACTIVE":
                analysis_res.append(RuntimeError(f"Gemini file upload never reached ACTIVE state: {getattr(uf, 'state', None)}"))
                return
            print(f"  └─ ⏳ [LYRIC_ALIGNER] Audio ready. Requesting Musical Intelligence Report (math+ASR fused)...", flush=True)
            t_start = time.time()
            full_prompt = _PROMPT + math_context + transcript_context
            raw_response = gemini_router.generate(
                task_type="analysis",
                prompt=[uf, full_prompt],
                module_name="lyric_rhythm_aligner",
                gen_config={"temperature": 0.2, "max_output_tokens": 8192},
            )
            latency = time.time() - t_start
            print(f"  └─ ✅ [LYRIC_ALIGNER] Musical Intelligence received in {latency:.1f}s.", flush=True)
            analysis_res.append(raw_response)
        except Exception as _ex:
            analysis_res.append(_ex)

    import threading
    a_thread = threading.Thread(target=_worker, daemon=True)
    a_thread.start()
    a_thread.join(timeout=GEMINI_AUDIO_CALL_TIMEOUT_SEC)

    if a_thread.is_alive():
        logger.warning(
            f"[LYRIC_ALIGNER] Gemini call still running past the {GEMINI_AUDIO_CALL_TIMEOUT_SEC:.0f}s "
            f"budget for '{os.path.basename(audio_path)}' — falling back to BeatEngine-only output."
        )
        def _late_cleanup():
            a_thread.join(timeout=GEMINI_AUDIO_CALL_TIMEOUT_SEC * 2)
            _client = _client_holder.get("client")
            if _client and _uploaded_file:
                try:
                    _client.files.delete(name=_uploaded_file.name)
                    logger.debug(f"[LYRIC_ALIGNER] Late cleanup deleted uploaded file: {_uploaded_file.name}")
                except Exception:
                    pass
        threading.Thread(target=_late_cleanup, daemon=True).start()

        try:
            with open(cache_json_path, "w", encoding="utf-8") as cf:
                json.dump(fast_report, cf, indent=2)
        except Exception:
            pass
        return fast_report if fast_report.get("tempo_bpm", 0) > 0 else _empty_report()

    if not analysis_res or isinstance(analysis_res[0], Exception):
        _err = analysis_res[0] if analysis_res else "no result returned"
        logger.warning(f"[LYRIC_ALIGNER] Gemini Audio Call failed for '{os.path.basename(audio_path)}': {_err} — using BeatEngine fallback.")
        try:
            with open(cache_json_path, "w", encoding="utf-8") as cf:
                json.dump(fast_report, cf, indent=2)
        except Exception:
            pass
        return fast_report if fast_report.get("tempo_bpm", 0) > 0 else _empty_report()

    raw_response = analysis_res[0]

    if not raw_response or len(str(raw_response).strip()) < 10:
        logger.warning("[LYRIC_ALIGNER] Empty response from Gemini — using BeatEngine fallback.")
        return fast_report if fast_report.get("tempo_bpm", 0) > 0 else _empty_report()

    try:
        cleaned = _clean_json(str(raw_response))
        report  = json.loads(cleaned)

        if not isinstance(report, dict):
            logger.warning("[LYRIC_ALIGNER] Gemini returned non-dict JSON — using BeatEngine fallback.")
            return fast_report if fast_report.get("tempo_bpm", 0) > 0 else _empty_report()

        if fast_report.get("tempo_bpm", 0) > 0:
            if not report.get("tempo_bpm"):
                report["tempo_bpm"] = fast_report["tempo_bpm"]
            if not report.get("bar_duration_sec"):
                report["bar_duration_sec"] = fast_report["bar_duration_sec"]
            if not report.get("tension_arc"):
                report["tension_arc"] = fast_report["tension_arc"]
            if not report.get("emotional_peak_moments"):
                report["emotional_peak_moments"] = fast_report["emotional_peak_moments"]

        report = _validate_and_enrich(report)
        report["_source"] = "gemini"
        report["_prompt_version"] = _PROMPT_VERSION

        try:
            temp_cache = cache_json_path + ".tmp"
            with open(temp_cache, "w", encoding="utf-8") as cf:
                json.dump(report, cf, indent=2)
            os.replace(temp_cache, cache_json_path)
            logger.info(f"[LYRIC_ALIGNER] 💾 Persisted lyric intelligence to disk: {cache_json_path}")
            try:
                from Audio_Modules.audio_pool_manager import AudioPoolManager
                AudioPoolManager().merge_lyric_into_pool(
                    os.path.basename(audio_path), report
                )
            except Exception as _pm:
                logger.debug(f"[LYRIC_ALIGNER] pool merge (gemini_path): {_pm}")
        except Exception as _swe:
            logger.warning(f"[LYRIC_ALIGNER] Failed to persist lyric cache: {_swe}")

        n_sec  = len(report.get("sections", []))
        n_lyr  = len(report.get("lyrics", []))
        n_dir  = len(report.get("shot_directives", []))
        n_arc  = len(report.get("tension_arc", []))
        n_peak = len(report.get("emotional_peak_moments", []))
        logger.info(
            f"[LYRIC_ALIGNER] 🎶 FUSED Musical Intelligence Report | "
            f"vocals={report.get('has_vocals')} lang={report.get('language')} "
            f"bpm={report.get('tempo_bpm')} emotion={report.get('dominant_emotion')} "
            f"sections={n_sec} lyrics={n_lyr} directives={n_dir} "
            f"tension_arc={n_arc}pts peaks={n_peak}"
        )
        return report

    except json.JSONDecodeError as _jde:
        logger.error(f"[LYRIC_ALIGNER] JSON parse error: {_jde}")
        return fast_report if fast_report.get("tempo_bpm", 0) > 0 else _empty_report()
    except Exception as _e:
        logger.warning(f"[LYRIC_ALIGNER] Gemini processing exception: {_e}")
        return fast_report if fast_report.get("tempo_bpm", 0) > 0 else _empty_report()

    finally:
        _client = _client_holder.get("client")
        if _client and _uploaded_file:
            try:
                _client.files.delete(name=_uploaded_file.name)
                logger.debug(f"[LYRIC_ALIGNER] Cleaned up uploaded file: {_uploaded_file.name}")
            except Exception as _cde:
                logger.debug(f"[LYRIC_ALIGNER] Uploaded file cleanup failed (non-fatal): {_cde}")


def get_tension_at(tension_arc: List[Dict], time_sec: float) -> float:
    """
    Interpolate tension score at a specific timestamp from the tension arc.
    Returns 0.5 (neutral) if the arc is empty.
    """
    if not tension_arc:
        return 0.5
    arc = sorted(tension_arc, key=lambda x: x.get("time", 0.0))
    if time_sec <= arc[0].get("time", 0.0):
        return float(arc[0].get("tension", 0.5))
    if time_sec >= arc[-1].get("time", 0.0):
        return float(arc[-1].get("tension", 0.5))
    for i in range(len(arc) - 1):
        t0 = float(arc[i].get("time", 0.0))
        t1 = float(arc[i + 1].get("time", 0.0))
        if t0 <= time_sec <= t1:
            if t1 == t0:
                return float(arc[i].get("tension", 0.5))
            alpha = (time_sec - t0) / (t1 - t0)
            v0 = float(arc[i].get("tension", 0.5))
            v1 = float(arc[i + 1].get("tension", 0.5))
            return round(v0 + alpha * (v1 - v0), 3)
    return 0.5


def get_section_at(sections: List[Dict], time_sec: float) -> Optional[Dict]:
    """
    Return the section dict that contains `time_sec`, or None.
    """
    for sec in sections:
        if float(sec.get("start", 0)) <= time_sec < float(sec.get("end", 0)):
            return sec
    return None


def get_directive_at(directives: List[Dict], time_sec: float) -> Optional[Dict]:
    """
    Return the highest-priority shot directive active at `time_sec`, or None.
    """
    active = [
        d for d in directives
        if float(d.get("time", 0)) <= time_sec < float(d.get("time", 0)) + float(d.get("duration", 2.0))
    ]
    if not active:
        return None
    return max(active, key=lambda x: int(x.get("priority", 1)))


def select_best_audio_for_clip(
    clip_id: str,
    clip_folder: Optional[str] = None,
    audio_dir: Optional[str] = None,
    exclude_filenames: Optional[set] = None,
) -> Dict[str, Any]:
    """
    Gemini Call 2 — BGM Selector.
    Receives current clip's visual_context + audio_data (math + context)
    + ALL pooled clip audio_data records from ClipIntelligenceStore.
    Gemini cross-matches visual intent vs pooled audio behavior to select the single best BGM track.
    Saves decision to clip intelligence JSON and returns selection dict.
    """
    from Gemini_Modules.clip_intelligence_store import ClipIntelligenceStore

    store = ClipIntelligenceStore()
    clip_data = store.load(clip_id, clip_folder) or store.create_blank(clip_id, clip_folder or "")

    visual_ctx = clip_data.get("visual_context", {})
    current_audio = clip_data.get("audio_data", {})

    vault_audio_pool = {}
    try:
        from Publishing_Modules.telegram_vault_indexer import TelegramVaultIndexer
        vault = TelegramVaultIndexer()
        vault_audio_pool = vault.get_vault_audio_pool(current_clip_id=clip_id)
        if vault_audio_pool:
            logger.info(f"🏛️ [BGM SELECTOR - PRIMARY] Loaded {len(vault_audio_pool)} candidate audio track(s) from Telegram Storage Vault index.")
    except Exception as _tve:
        logger.debug(f"[BGM SELECTOR] Vault audio index lookup notice: {_tve}")

    from Audio_Modules.audio_pool_manager import AudioPoolManager
    _pm = AudioPoolManager()
    local_pool_files = _pm.get_files_index()

    pool_files = dict(vault_audio_pool)
    for fname, meta in local_pool_files.items():
        if fname in pool_files:
            was_source_extract = pool_files[fname].get("is_source_extract", False)
            pool_files[fname].update(meta)
            if was_source_extract:
                pool_files[fname]["is_source_extract"] = True
        else:
            pool_files[fname] = meta

    try:
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        active_dir = os.path.join(repo_root, "Original_audio", "active")
        if os.path.isdir(active_dir):
            for lf in os.listdir(active_dir):
                if lf.lower().endswith((".mp3", ".wav", ".m4a")) and lf not in pool_files:
                    pool_files[lf] = {
                        "usage_count": 0,
                        "last_used": 0,
                        "bpm": 120.0,
                        "energy": 0.5,
                        "dominant_emotion": "hype"
                    }
    except Exception as _dir_err:
        logger.debug(f"[BGM SELECTOR] Local disk check notice: {_dir_err}")

    if not pool_files:
        logger.warning("🎶 [BGM Selector] Neither Telegram Vault index nor local pool has candidates.")
        return {"selected_audio_track": None, "alignment_score": 0.0, "reasoning": "Empty pool index."}

    previous_bgm = current_audio.get("selected_bgm_track") or current_audio.get("selected_audio_track")
    disqualified_tracks = set()
    if previous_bgm:
        disqualified_tracks.add(previous_bgm.lower())
    if exclude_filenames:
        for ef in exclude_filenames:
            disqualified_tracks.add(ef.lower())
            disqualified_tracks.add(os.path.basename(ef).lower())

    try:
        from Audio_Modules.audio_pool_manager import _is_pipeline_artifact
    except ImportError:
        def _is_pipeline_artifact(f):
            fl = f.lower()
            return fl.startswith("sess_") or "extracted" in fl or fl in ("video.wav", "video.mp4", "video_extracted.wav")

    def _is_noisy_or_unusable(fname, meta):
        if not isinstance(meta, dict):
            return False
        if meta.get("is_unusable", False) or meta.get("is_speech_only", False):
            return True
        reason = str(meta.get("unusable_reason", "")).lower()
        vibe = str(meta.get("vibe_tags", [])).lower()
        noise_kws = ("paparazzi", "crowd", "babble", "car_sound", "traffic", "pollution", "horn", "shouting", "chatter", "mic_static")
        if any(kw in reason for kw in noise_kws) or any(kw in vibe for kw in noise_kws):
            return True
        return False

    all_candidates = [
        fname for fname, meta in pool_files.items()
        if isinstance(meta, dict)
        and not _is_noisy_or_unusable(fname, meta)
        and not _is_pipeline_artifact(fname)
        and fname.lower().endswith((".mp3", ".wav", ".m4a"))
    ]

    if not all_candidates:
        logger.warning("🎶 [BGM Selector] No valid musical candidates found in merged pool index (pipeline artifacts and noisy audio excluded).")
        return {"selected_audio_track": None, "alignment_score": 0.0, "reasoning": "No valid clean BGM tracks in pool."}

    fresh_candidates = [
        c for c in all_candidates
        if c.lower() not in disqualified_tracks and os.path.basename(c).lower() not in disqualified_tracks
    ]
    available_candidates = fresh_candidates if fresh_candidates else all_candidates

    candidate_lines = {}
    candidate_scores = []

    clip_intent = str(visual_ctx.get("intent", "viral_reel")).lower()
    clip_tone = str(visual_ctx.get("tone", "aspirational")).lower()
    clip_bpm = float(current_audio.get("math", {}).get("tempo_bpm", 120.0))
    now = time.time()

    for c_file in available_candidates:
        meta = pool_files.get(c_file, {})

        last_used = meta.get("last_used", 0)
        u_count = meta.get("usage_count", 0)

        c_bpm = float(meta.get("tempo_bpm") or meta.get("bpm") or 120.0)
        c_emotion = str(meta.get("dominant_emotion", "hype")).lower()
        c_genre = str(meta.get("gemini_genre") or "music").lower()
        vibes = meta.get("vibe_tags") or [meta.get("energy_profile", "medium")]
        c_vibe = ", ".join(vibes) if isinstance(vibes, list) else str(vibes)
        c_vocals = bool(meta.get("has_vocals", False))
        c_lang = str(meta.get("language", "unknown"))

        is_own_clip_audio = (
            c_file.lower() == f"bgm_{clip_id.lower()}.wav"
            or c_file.lower().startswith(f"bgm_{clip_id.lower()}")
            or (clip_folder and os.path.basename(clip_folder).lower() in c_file.lower())
        )
        is_harvested_audio = (
            bool(meta.get("is_source_extract", False))
            or c_file.lower().startswith("bgm_manual_")
            or c_file.lower().startswith("sess_")
        )
        # Tier 1 = Real BGM Music Library (.mp3)
        # Tier 2 = Harvested Audio from OTHER clips
        # Tier 3 = Harvested Audio from THIS CURRENT clip (last resort)
        tier = 1 if not is_harvested_audio else (2 if not is_own_clip_audio else 3)
        is_self_audio = (tier == 3)

        hrs_since_used = (now - last_used) / 3600.0 if last_used > 0 else 999.0
        recency_penalty = min(1.0, hrs_since_used / 12.0) if last_used > 0 else 1.0
        usage_penalty = 1.0 / (1.0 + u_count * 0.25)
        self_discount = 0.05 if tier == 3 else (0.40 if tier == 2 else 1.0)

        bpm_match = max(0.0, 1.0 - (abs(clip_bpm - c_bpm) / 100.0))
        emotion_match = 1.0 if c_emotion in clip_tone or clip_tone in c_emotion else 0.5
        math_score = (bpm_match * 0.35 + emotion_match * 0.45) * max(0.05, recency_penalty) * usage_penalty * self_discount

        self_tag = " [CLIP'S ORIGINAL HARVESTED AUDIO - LAST RESORT FALLBACK ONLY]" if tier == 3 else (" [HARVESTED AUDIO FROM OTHER CLIP]" if tier == 2 else "")

        c_fid = str(meta.get("file_id") or "")
        fid_tag = f", telegram_file_id='{c_fid}'" if c_fid else ""

        candidate_scores.append((math_score, c_file, tier, c_fid))
        candidate_lines[c_file] = (
            f"- '{c_file}'{self_tag}{fid_tag}: genre='{c_genre}', bpm={c_bpm:.1f}, emotion='{c_emotion}', vibe='{c_vibe}', "
            f"vocals={c_vocals}, lang='{c_lang}', last_used={hrs_since_used:.1f}h_ago, usage_count={u_count}"
        )

    tier1 = [c for c in candidate_scores if c[2] == 1]
    tier2 = [c for c in candidate_scores if c[2] == 2]
    tier3 = [c for c in candidate_scores if c[2] == 3]

    tier1.sort(key=lambda x: x[0], reverse=True)
    tier2.sort(key=lambda x: x[0], reverse=True)
    tier3.sort(key=lambda x: x[0], reverse=True)

    top_candidates = (tier1[:7] + tier2[:2] + tier3[:1]) if tier1 else (tier2[:8] + tier3[:1])

    fresh_candidates = [c for c in top_candidates if c[1].lower() not in disqualified_tracks]
    if fresh_candidates:
        best_math_candidate = fresh_candidates[0][1]
        best_math_fid = fresh_candidates[0][3]
        best_math_score = float(fresh_candidates[0][0])
    else:
        best_math_candidate = top_candidates[0][1] if top_candidates else available_candidates[0]
        best_math_fid = top_candidates[0][3] if top_candidates else ""
        best_math_score = float(top_candidates[0][0]) if top_candidates else 0.85

    selected_track = best_math_candidate
    selected_file_id = best_math_fid
    alignment_score = best_math_score
    reasoning = f"Smart Mathematical & Semantic Audio Match (score={alignment_score:.2f})."

    top_lines = []
    for rank, (sc, fname, is_self, fid) in enumerate(top_candidates, start=1):
        line = candidate_lines.get(fname, f"- '{fname}': score={sc:.3f}")
        top_lines.append(f"#{rank} {line}")
    candidates_str = "\n".join(top_lines)
    forbidden_str = ", ".join([f"'{t}'" for t in disqualified_tracks]) or "None"

    prompt = f"""You are an Expert BGM Music Selector for short-form video reels.

Rules:
- NEVER pick a track from FORBIDDEN list
- STRICT NOISE REJECTION: STRICTLY REJECT and NEVER select audio tracks corrupted by heavy background noise, car/traffic sounds, crowd babble, paparazzi shouting, camera shutter clicks, horn blares, or environmental noise pollution. Select ONLY clean, studio-quality, high-energy musical tracks or high-fidelity musical scores.
- FIRST PRIORITY: Select from the EXTERNAL candidate tracks (#1 to #{len(external_candidates[:9])}). Choose a fresh external BGM track that elevates, enhances, or brings a higher-quality musical energy to the reel.
- LAST RESORT FALLBACK: The very last option ('[CLIP'S ORIGINAL HARVESTED AUDIO - LAST RESORT FALLBACK ONLY]') MUST ONLY be selected if ALL external candidate tracks above are completely incompatible in BPM, genre, or vibe.
- Prioritize musical style, emotional vibe, and BPM alignment with the video.
- Do NOT reference past clips — judge purely on the clip context and track metadata below.

[FORBIDDEN TRACKS — DO NOT SELECT]
{forbidden_str}

[CURRENT CLIP CONTEXT]
- Intent: '{visual_ctx.get('intent', 'viral_reel')}'
- Tone: '{visual_ctx.get('tone', 'aspirational')}'
- Narrative: '{visual_ctx.get('recommended_narrative', 'lifestyle')}'
- Target BPM: {clip_bpm}
- Speech mode: '{current_audio.get('context', {}).get('speech_mode', 'on_camera_dialogue')}'

[TOP-10 CANDIDATE MUSIC TRACKS]
{candidates_str}

Return ONLY valid JSON:
{{
  "selected_audio_track": "chosen_filename.mp3",
  "telegram_file_id": "file_id_if_available",
  "alignment_score": 0.95,
  "reasoning": "One sentence: why this specific track fits or elevates this clip's intent/tone over alternatives."
}}
"""

    try:
        try:
            from Gemini_Modules.gemini_router_module.gemini_governor import gemini_router
        except Exception:
            try:
                from Intelligence_Modules.gemini_governor import gemini_router
            except Exception:
                from gemini_governor import gemini_router

        if gemini_router:
            logger.info("🎶 [BGM Selector] Querying Gemini Router across model vanguard...")
            raw_response = gemini_router.generate(
                task_type="analysis",
                prompt=prompt,
                module_name="bgm_selector",
                gen_config={"temperature": 0.3}
            )
            if raw_response:
                data = json.loads(_clean_json(raw_response))
                win_track = data.get("selected_audio_track")
                win_fid = data.get("telegram_file_id")
                if win_track and any(c.lower() == win_track.lower() for c in available_candidates) and win_track.lower() not in disqualified_tracks:
                    selected_track = win_track
                    reasoning = data.get("reasoning", reasoning)
                    alignment_score = float(data.get("alignment_score", 0.90))
                    if win_fid:
                        selected_file_id = win_fid
                    else:
                        selected_file_id = pool_files.get(selected_track, {}).get("file_id", selected_file_id)
                    logger.info(f"🎶 [BGM Selector - Gemini Call 2] Winner: '{selected_track}' (file_id={selected_file_id}, score={alignment_score:.2f})")
                else:
                    selected_track = best_math_candidate
                    selected_file_id = best_math_fid
                    logger.warning(f"🎶 [BGM Selector] Gemini returned disqualified/invalid track '{win_track}' — forcing fresh math winner '{selected_track}'.")
    except Exception as e:
        selected_track = best_math_candidate
        selected_file_id = best_math_fid
        logger.warning(f"🎶 [BGM Selector - Gemini Call 2] Router fallback to smart math match: {e}")

    store.patch_bgm_selection(clip_data, selected_track, reasoning, alignment_score)
    store.save(clip_id, clip_data, clip_folder)

    try:
        from Audio_Modules.audio_pool_manager import AudioPoolManager
        AudioPoolManager().use_audio(selected_track)
    except Exception as _pe:
        logger.warning(f"🎶 [BGM Selector] Could not register usage with AudioPoolManager: {_pe}")

    return {
        "selected_audio_track": selected_track,
        "telegram_file_id": selected_file_id,
        "alignment_score": alignment_score,
        "reasoning": reasoning,
    }


def compute_routing_parameters(
    lyric_intel: Optional[Dict[str, Any]],
    forensic_context: Optional[Dict[str, Any]],
    selected_bgm_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Translates lyric & music intelligence + visual context into mathematical FFmpeg synthesis parameters.
    Replaces standalone audio_rhythm_router.py by integrating semantic routing directly.
    """
    emotion = str((lyric_intel or {}).get("dominant_emotion", "hype")).lower().strip()
    energy  = str((lyric_intel or {}).get("energy_profile", "medium")).lower().strip()

    v_ctx = forensic_context or {}
    intent  = str(v_ctx.get("intent", "viral_reel")).lower()
    tone    = str(v_ctx.get("tone", "aspirational")).lower()

    if emotion in ("sadness", "melancholic", "nostalgia") or tone in ("dramatic", "melancholic"):
        strategy_name = "CINEMATIC_EMOTIONAL_SLOWMO"
        speed_factor  = 0.75
        transition_type = "dissolve"
        transition_duration = 0.8
        ducking_db = -3.0
        target_cut_interval = 2.8
    elif intent in ("podcast_speech", "speech") or emotion == "conversational":
        strategy_name = "VOICEOVER_PODCAST_DUCKING"
        speed_factor  = 1.0
        transition_type = "fade"
        transition_duration = 0.4
        ducking_db = -18.0
        target_cut_interval = 3.5
    elif energy in ("explosive", "high") or emotion in ("hype", "power", "euphoria", "joy", "celebration"):
        strategy_name = "BEAT_SNAPPED_HIGH_ENERGY"
        speed_factor  = 1.1
        transition_type = "glitch"
        transition_duration = 0.2
        ducking_db = -6.0
        target_cut_interval = 0.8
    else:
        strategy_name = "DYNAMIC_CONTRAST_REEL"
        speed_factor  = 1.0
        transition_type = "whip_pan"
        transition_duration = 0.3
        ducking_db = -6.0
        target_cut_interval = 1.5

    return {
        "strategy_name": strategy_name,
        "speed_factor": speed_factor,
        "transition_type": transition_type,
        "transition_duration": transition_duration,
        "bgm_ducking_db": ducking_db,
        "music_volume": 0.85 if strategy_name != "VOICEOVER_PODCAST_DUCKING" else 0.15,
        "target_cut_interval": target_cut_interval,
        "selected_audio_path": selected_bgm_path,
        "dominant_emotion": emotion,
        "energy_profile": energy,
        "recommended_editing_mode": strategy_name
    }
