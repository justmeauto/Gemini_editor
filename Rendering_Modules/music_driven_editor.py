"""
Music Driven Editor — Psycho-Acoustic Visual Sync Engine
=========================================================
Thinks like a human editor who understands:
  - Frequency perception: bass hits demand high-energy visuals
  - Harmonic structure: verse/chorus arcs map to story arcs
  - Psycho-acoustic anticipation: the brain predicts the next beat 80-150ms ahead
  - Emotional arousal: tempo + energy together determine viewer nervous system state

Key fixes vs legacy:
  1. ZERO duplicate shots — global `used_source_ranges` tracks every used [start,end]
     and rejects any candidate with > OVERLAP_THRESHOLD intersection
  2. Vibe classification from BPM + energy profile → controls shot length preferences
  3. Emotion-responsive selection: drops get action shots, weak beats get calm shots
  4. Scene scoring includes duration sweet-spot weighting (1.2–2.8s ideal for viral shorts)

Fixes applied in this revision (see audit notes):
  A. Beat-consumption bug: the old code fast-forwarded beat_idx past every beat
     whose timestamp fell inside the just-placed clip's duration. Because weak/
     ambient shots can run up to 6s, this silently ate any drop or strong beat
     that happened to land inside that window — directly defeating the
     "drops get priority treatment" design goal, with no error or log line.
     Fixed: beat_idx now always advances by 1; spacing is enforced solely via
     MIN_BEAT_SPACING, and drops get a reduced spacing floor so they are never
     dropped just because the prior shot is still "playing".
  B. clip_id collision: `scene.get("clip_id", 0)` silently mapped every scene
     missing that field onto the same id, causing _is_duplicate to reject
     genuinely distinct footage as duplicates. Fixed: falls back to a stable
     per-object id (informational warning fired once) instead of a shared 0.
  C. `remaining.remove(scene)` matched by value equality, so two scenes with
     identical field values could remove the wrong one. Fixed: removal is now
     done by object identity.
  D. `score_scenes` read back its own "importance" output field as an input on
     re-entry, compounding the sweet-spot multiplier if called twice on the
     same list. Fixed: base importance now only ever derives from
     "composite_score" or the duration proxy, never from its own output key.
  E. Minor: removed unused `math` import, added optional `seed` for
     reproducible runs, and ffprobe failures in `_get_video_info` now log a
     warning instead of failing silently.
"""
import logging
import random
from typing import List, Dict, Any, Optional, Tuple

logger = logging.getLogger("music_driven_editor")

# ── Psycho-acoustic constants ──────────────────────────────────────────────────
OVERLAP_THRESHOLD   = 0.25   # max fractional overlap allowed before rejecting a shot
MIN_BEAT_SPACING    = 0.65   # seconds — human flicker fusion threshold ~15Hz = 0.067s,
                              # but audience fatigue starts < 0.65s sustained
DROP_MIN_SPACING    = 0.30   # seconds — drops get a reduced spacing floor so a long
                              # preceding shot can never silently eat a drop beat
ANTICIPATION_MS     = 80     # ms — visual cortex lag; cut this early relative to beat
VIRAL_SWEET_SPOT    = (1.2, 2.8)  # seconds — peak retention range for short-form edits
DROP_HOLD_MIN       = 0.8    # drops deserve at least 0.8s to land visually

# ── Vibe profiles (BPM + energy → editorial personality) ─────────────────────
# Each profile: (min_shot_s, max_shot_s, cut_style_bias, emotional_arc)
_VIBE_PROFILES = {
    "explosive":   (0.6,  1.5,  "aggressive", "shock→release"),   # >150 BPM, high energy
    "hype":        (0.8,  2.0,  "punchy",     "build→explode"),   # 120-150 BPM
    "groove":      (1.0,  2.5,  "rhythmic",   "flow→peak"),       # 90-120 BPM
    "cinematic":   (1.8,  4.0,  "smooth",     "tension→resolve"), # 60-90 BPM
    "ambient":     (2.5,  6.0,  "drift",      "emotion→release"), # <60 BPM
}


def _safe_float(v, default=0.0):
    try:
        return float(v) if v is not None else default
    except Exception:
        return default


def _overlap_fraction(a_start, a_end, b_start, b_end) -> float:
    """Fractional overlap of [b_start,b_end] within [a_start,a_end]."""
    olen = max(0.0, min(a_end, b_end) - max(a_start, b_start))
    a_dur = max(0.001, a_end - a_start)
    return olen / a_dur


def _get_vibe(bpm: float, avg_energy: float) -> str:
    """Classify music vibe from BPM and normalised energy [0-1]."""
    if bpm > 145 or (bpm > 120 and avg_energy > 0.75):
        return "explosive"
    if bpm > 115:
        return "hype"
    if bpm > 85:
        return "groove"
    if bpm > 60:
        return "cinematic"
    return "ambient"


class MusicDrivenEditor:
    def __init__(self, seed: Optional[int] = None):
        self.target_duration_min = 8.0
        self.target_duration_max = 60.0   # let orchestrator/VO hint cap it
        # Fix E: dedicated RNG so runs are reproducible when a seed is supplied,
        # while still falling back to system randomness by default.
        self._rng = random.Random(seed) if seed is not None else random.Random()
        self._warned_missing_clip_id = False

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _stable_clip_id(self, candidate: Dict) -> int:
        """
        Fix B: resolve a clip id for duplicate-tracking. Prefer an explicit
        'clip_id' field; if absent, fall back to the scene dict's own object
        identity rather than a shared default of 0, which previously caused
        unrelated scenes from different source clips to be treated as the
        same clip and wrongly rejected as duplicates.
        """
        raw = candidate.get("clip_id")
        if raw is not None:
            try:
                return int(raw)
            except Exception:
                pass
        if not self._warned_missing_clip_id:
            logger.warning(
                "⚠️ [MDE] scene missing 'clip_id' — falling back to per-object "
                "identity for duplicate tracking. Populate clip_id upstream "
                "for reliable dedup across runs."
            )
            self._warned_missing_clip_id = True
        return id(candidate)

    def _is_duplicate(self, candidate: Dict, used_ranges: List[Tuple[int, float, float]]) -> bool:
        """
        Returns True if this candidate's [start,end] overlaps significantly
        with any already-used range from the same clip.
        """
        c_id    = self._stable_clip_id(candidate)
        c_start = _safe_float(candidate.get("start"))
        c_end   = _safe_float(candidate.get("end"))
        for u_id, u_start, u_end in used_ranges:
            if u_id != c_id:
                continue
            frac = _overlap_fraction(c_start, c_end, u_start, u_end)
            if frac > OVERLAP_THRESHOLD:
                return True
        return False

    def _pick_scene_for_beat(
        self,
        beat_strength: str,
        candidates: List[Dict],
        used_ranges: List[Tuple],
        vibe: str,
    ) -> Optional[Dict]:
        """
        Pick the best scene for a given beat strength.
        Priority rules (psych-acoustic editorial logic):
          - DROP   → highest motion + face presence (shock value)
          - STRONG → high importance, clear subject
          - MEDIUM → moderate importance, visual variety
          - WEAK   → calm, long, breathing-room shots
        """
        pool = [s for s in candidates if not self._is_duplicate(s, used_ranges)]
        if not pool:
            return None

        def score_for_beat(s: Dict) -> float:
            imp   = _safe_float(s.get("importance", s.get("score", 0.5)))
            mot   = _safe_float(s.get("motion_score", s.get("motion", 0.0)))
            face  = _safe_float(s.get("face_score", 0.0))
            dur   = _safe_float(s.get("end")) - _safe_float(s.get("start"))
            sv_lo, sv_hi = VIRAL_SWEET_SPOT
            sweet = 1.0 if sv_lo <= dur <= sv_hi else (0.75 if dur < sv_lo else 0.85)

            if beat_strength == "drop":
                return (0.50 * mot) + (0.30 * face) + (0.10 * imp) + (0.10 * sweet)
            elif beat_strength == "strong":
                return (0.35 * imp) + (0.30 * mot) + (0.20 * face) + (0.15 * sweet)
            elif beat_strength == "medium":
                return (0.40 * imp) + (0.20 * mot) + (0.15 * face) + (0.25 * sweet)
            else:  # weak — breathing room
                calm = 1.0 - mot
                return (0.40 * imp) + (0.30 * calm) + (0.20 * sweet) + (0.10 * face)

        scored = sorted(pool, key=score_for_beat, reverse=True)
        top = scored[:3]
        self._rng.shuffle(top)
        return top[0] if top else None

    @staticmethod
    def _remove_by_identity(container: List[Dict], item: Dict) -> None:
        """
        Fix C: remove `item` from `container` by object identity, not value
        equality. Two scenes with identical field values (plausible from
        near-duplicate detector output) would otherwise let list.remove()
        delete the wrong one.
        """
        for i, s in enumerate(container):
            if s is item:
                del container[i]
                return

    # ── Public API ─────────────────────────────────────────────────────────────

    def score_scenes(self, scenes: List[Dict], motion_events: List[Dict]) -> List[Dict]:
        """
        Score scenes with psycho-acoustic weights:
          final_score = 0.35*motion + 0.30*face + 0.35*importance
        Motion is max-normalised to prevent dominance from outlier clips.

        Fix D: base importance is derived only from 'composite_score' or the
        duration proxy — never from this method's own 'importance' output
        field — so calling score_scenes twice on the same list no longer
        compounds the sweet-spot multiplier.
        """
        if not scenes:
            return []

        max_motion = 0.001
        for s in scenes:
            s_start = _safe_float(s.get("start"))
            s_end   = _safe_float(s.get("end"))
            raw = 0.0
            for ev in (motion_events or []):
                t = _safe_float(ev.get("time", ev.get("start", -1)))
                if s_start <= t < s_end:
                    val = {"large": 1.0, "medium": 0.6, "small": 0.3}.get(
                        ev.get("strength", ""), 0.1
                    )
                    raw = max(raw, val)
            max_motion = max(max_motion, raw)
            s["_raw_motion"] = raw

        for s in scenes:
            motion_score = min(s["_raw_motion"] / max_motion, 1.0)
            face_score   = _safe_float(s.get("face_score"))
            dur          = _safe_float(s.get("end")) - _safe_float(s.get("start"))

            # Fix D: only 'composite_score' (an external/preset field) or the
            # duration proxy feed base_importance — 'importance' is never read
            # back here, since this method is the one that writes it.
            base_importance = _safe_float(
                s.get("composite_score", min(1.0, dur / 3.0))
            )
            sv_lo, sv_hi = VIRAL_SWEET_SPOT
            if sv_lo <= dur <= sv_hi:
                base_importance = min(1.0, base_importance * 1.1)

            final_score = (0.35 * motion_score) + (0.30 * face_score) + (0.35 * base_importance)
            s["motion_score"] = round(motion_score, 3)
            s["face_score"]   = round(face_score, 3)
            s["importance"]   = round(final_score, 3)

        return sorted(scenes, key=lambda x: x["importance"], reverse=True)

    def map_scenes_to_beats(
        self,
        scored_scenes: List[Dict],
        classified_beats: List[Dict],
        bpm: float = 120.0,
        avg_energy: float = 0.5,
    ) -> List[Dict]:
        """
        Psycho-acoustic beat→scene mapping.

        Algorithm:
          1. Classify music vibe from BPM + energy
          2. Walk the beat grid one beat at a time (Fix A: no longer
             fast-forwards past beats that fall inside a placed clip's
             duration — that was silently eating drops)
          3. For each beat pick the best-scoring NON-DUPLICATE scene
          4. Clip scene to a duration derived from vibe profile + beat interval
          5. Track all used [clip_id, start, end] to prevent re-use
        """
        if not scored_scenes or not classified_beats:
            return []

        vibe     = _get_vibe(bpm, avg_energy)
        vp       = _VIBE_PROFILES[vibe]
        min_shot = vp[0]
        max_shot = vp[1]
        logger.info(f"🎧 [MDE] Vibe={vibe} ({bpm:.0f}BPM e={avg_energy:.2f}) "
                    f"shot_range=[{min_shot:.1f}s, {max_shot:.1f}s]")

        anticipation_s = ANTICIPATION_MS / 1000.0
        timeline: List[Dict] = []
        used_ranges: List[Tuple[int, float, float]] = []  # (clip_id, src_start, src_end)
        timeline_duration = 0.0
        skipped_drops = 0

        remaining = list(scored_scenes)

        last_cut_time = -MIN_BEAT_SPACING
        beat_idx = 0

        while beat_idx < len(classified_beats) and remaining:
            b = classified_beats[beat_idx]
            b_time     = _safe_float(b.get("time"))
            b_strength = b.get("strength", "weak")

            # Fix A: drops get a reduced spacing floor so a long-running prior
            # shot can never silently swallow a drop beat.
            spacing_floor = DROP_MIN_SPACING if b_strength == "drop" else MIN_BEAT_SPACING
            if b_time - last_cut_time < spacing_floor:
                if b_strength == "drop":
                    skipped_drops += 1
                beat_idx += 1
                continue

            if beat_idx < len(classified_beats) - 1:
                next_b_time = _safe_float(classified_beats[beat_idx + 1].get("time"))
                beat_interval = max(0.1, next_b_time - b_time)
            else:
                beat_interval = 0.5

            if b_strength == "drop":
                target_dur = max(DROP_HOLD_MIN, min(beat_interval * 1.5, max_shot))
            elif b_strength == "strong":
                target_dur = self._rng.uniform(
                    max(min_shot, beat_interval * 0.8),
                    min(max_shot, beat_interval * 1.2)
                )
            elif b_strength == "medium":
                target_dur = self._rng.uniform(min_shot, min(max_shot, beat_interval * 2.0))
            else:  # weak — breathing room
                target_dur = self._rng.uniform(
                    max(min_shot, beat_interval),
                    max_shot
                )

            target_dur = max(min_shot, min(max_shot, target_dur))

            scene = self._pick_scene_for_beat(b_strength, remaining, used_ranges, vibe)
            if scene is None:
                remaining = list(scored_scenes)
                scene = self._pick_scene_for_beat(b_strength, remaining, used_ranges, vibe)
                if scene is None:
                    beat_idx += 1
                    continue

            src_start = _safe_float(scene.get("start"))
            src_end   = _safe_float(scene.get("end"))
            src_avail = src_end - src_start

            if src_avail > target_dur:
                max_offset = src_avail - target_dur
                offset = self._rng.uniform(0.0, min(max_offset, src_avail * 0.4))
                clip_start = round(src_start + offset, 3)
                clip_end   = round(clip_start + target_dur, 3)
            else:
                clip_start = round(src_start, 3)
                clip_end   = round(src_end, 3)
                target_dur = src_avail

            c_id = self._stable_clip_id(scene)
            used_ranges.append((c_id, clip_start, clip_end))
            # Fix C: remove by identity, not value equality.
            self._remove_by_identity(remaining, scene)

            beat_out = round(b_time - anticipation_s, 3)

            block = {
                "scene_ref":      scene,
                "scene_start":    clip_start,
                "scene_end":      clip_end,
                "beat_start":     beat_out,
                "beat_end":       round(beat_out + target_dur, 3),
                "dur":            round(target_dur, 3),
                "_beat_strength": b_strength,
                "_vibe":          vibe,
                "_beat_interval": round(beat_interval, 3),
                "clip_id":        c_id,
            }
            timeline.append(block)
            timeline_duration += target_dur
            last_cut_time = b_time

            # Fix A: always advance one beat at a time — never fast-forward
            # past beats whose timestamp falls inside this clip's duration.
            # Spacing between cuts is governed solely by spacing_floor above.
            beat_idx += 1

            if timeline_duration >= self.target_duration_max:
                break

        logger.info(
            f"🎬 [MDE] Mapped {len(timeline)} beat-scene blocks | "
            f"vibe={vibe} | total={timeline_duration:.1f}s | "
            f"unique_ranges={len(used_ranges)}"
            + (f" | drops_skipped_for_spacing={skipped_drops}" if skipped_drops else "")
        )
        return timeline

    def insert_transitions(self, timeline):
        """Legacy stub — transition logic handled by STIE in RhythmTimelineBuilder."""
        return timeline

    def _get_video_info(self, path):
        import subprocess, json
        try:
            cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0",
                   "-show_entries", "stream=duration", "-of", "json", path]
            res = subprocess.run(cmd, capture_output=True, text=True)
            data = json.loads(res.stdout)
            return {"duration": float(data.get("streams", [{}])[0].get("duration", 0))}
        except Exception as e:
            # Fix E: log the failure instead of silently returning a fake
            # duration — a bad path or corrupt file should be visible.
            logger.warning(f"⚠️ [MDE] ffprobe failed for '{path}': {e}. Falling back to 15.0s.")
            return {"duration": 15.0}
