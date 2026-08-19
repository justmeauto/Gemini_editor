# 📄 Module Documentation: `music_driven_editor.py`

**Rating**: `9.4 / 10 (Grade A - Psycho-Acoustic Visual Sync Engine)`  
**Location**: `D:\AMTCE\AMTCE_Elite_Core\Timeline_and_Compilation\music_driven_editor.py`  
**Target File Link**: [music_driven_editor.py](file:///D:/AMTCE/AMTCE_Elite_Core/Timeline_and_Compilation/music_driven_editor.py)

---

## 👑 Purpose & Role: Psycho-Acoustic Visual Sync Engine

`music_driven_editor.py` is the **Psycho-Acoustic Visual Sync Engine (`MusicDrivenEditor`)** in the **Timeline & Compilation Engine** (CEIE) family.

It maps video scenes to BGM audio beats based on human perceptual dynamics: frequency perception (bass drop action mapping), visual cortex processing lag ($80\text{ms}$ anticipation cuts), non-duplicate shot tracking, and viral short-form retention sweet-spots ($1.2\text{s} - 2.8\text{s}$).

---

## 🏗️ Architecture & Psycho-Acoustic Mapping Pipeline

```mermaid
flowchart TD
    AudioProfile[BPM & Avg Energy from BeatEngine] --> ClassifyVibe[_get_vibe\nClassify Vibe Profile: explosive | hype | groove | cinematic | ambient]
    
    ClassifyVibe --> ScoreScenes[score_scenes\nComposite Score = 0.35*motion + 0.30*face + 0.35*importance]
    ScoreScenes --> SweetSpotBonus[Viral Sweet-Spot Bonus +10% if duration 1.2s - 2.8s]
    
    SweetSpotBonus --> MapBeats[map_scenes_to_beats\nWalk Classified Beat Grid]
    
    MapBeats --> PickScene[_pick_scene_for_beat\nBeat Strength Matching: drop, strong, medium, weak]
    PickScene --> CheckDup{_is_duplicate\nOverlap > 25% with used_ranges?}
    
    CheckDup -- Yes --> PickNext[Pick Next Candidate Scene]
    PickNext --> CheckDup
    
    CheckDup -- No --> ClipScene[Clip Scene to Target Duration & Apply 80ms Anticipation Cut]
    ClipScene --> RecordRange[Record clip_id, start, end in used_ranges]
    RecordRange --> BuildBlock[Build Timeline Block & Advance Beat Index]
    
    BuildBlock --> LoopEnd{Timeline Duration >= Max OR Beats Exhausted?}
    LoopEnd -- No --> MapBeats
    LoopEnd -- Yes --> ReturnTimeline[RETURN Beat-Driven Timeline List]
```

---

## 🛠️ Key Technical Features

### 1. Psycho-Acoustic Vibe Profiles (`_get_vibe` / `_VIBE_PROFILES`)
Classifies music tracks into 5 editorial personalities based on BPM and average energy:
* **`explosive`** ($>150\text{ BPM}$): $0.6\text{s} - 1.5\text{s}$ shot range (Aggressive).
* **`hype`** ($115 - 150\text{ BPM}$): $0.8\text{s} - 2.0\text{s}$ shot range (Punchy).
* **`groove`** ($85 - 115\text{ BPM}$): $1.0\text{s} - 2.5\text{s}$ shot range (Rhythmic).
* **`cinematic`** ($60 - 85\text{ BPM}$): $1.8\text{s} - 4.0\text{s}$ shot range (Smooth).
* **`ambient`** ($<60\text{ BPM}$): $2.5\text{s} - 6.0\text{s}$ shot range (Drift).

### 2. Zero-Duplicate Range Tracking (`_is_duplicate`)
Tracks used source clip intervals `(clip_id, start, end)` in `used_ranges`. Rejects candidates if fractional overlap exceeds `OVERLAP_THRESHOLD = 0.25`, eliminating repeated clip loops in compilations.

### 3. 80ms Visual Cortex Anticipation Cut (`ANTICIPATION_MS = 80`)
Cuts visual transitions $80\text{ms}$ *before* the audio beat timestamp (`b_time - 0.08s`), compensating for human visual cortex processing delay so picture and audio hit perception simultaneously.

### 4. Viral Short-Form Sweet-Spot Weighting (`VIRAL_SWEET_SPOT = (1.2, 2.8)`)
Grants a $+10\%$ importance score bonus to scene candidates with durations between $1.2\text{s}$ and $2.8\text{s}$, favoring high-retention short-form pacing.

---

## 💥 Brutal & Honest Engineering Audit

| Metric | Score | Raw Unfiltered Reality |
| :--- | :---: | :--- |
| **Psycho-Acoustic Alignment** | `9.7 / 10` | 80ms anticipation cuts and energy-matched beat scoring reflect human editorial instinct. |
| **Duplicate Prevention** | `9.6 / 10` | Strict fractional overlap checking ($<0.25$) prevents repeated clip loops across multi-scene compilations. |
| **Vibe Classification** | `9.5 / 10` | BPM and energy profiles cleanly scale shot length bounds from 0.6s explosive cuts to 6.0s ambient holds. |
| **Non-Deterministic Top-3 Shuffle** | `8.2 / 10` | Line 129 executes `random.shuffle(top)` among the top-3 candidate scenes, producing non-deterministic variations when re-rendering identical video projects. |
| **Dead Stub Function** | `8.5 / 10` | `insert_transitions()` is an un-used legacy stub (`return timeline`) that performs no operation. |

---

## 💻 Code Usage & Public API

```python
from Timeline_and_Compilation.music_driven_editor import MusicDrivenEditor

mde = MusicDrivenEditor()

# 1. Scored scene candidates
raw_scenes = [
    {"clip_id": 1, "start": 0.0, "end": 4.0, "importance": 0.8, "face_score": 0.9},
    {"clip_id": 1, "start": 5.0, "end": 8.0, "importance": 0.6, "face_score": 0.4}
]
motion_events = [{"time": 2.0, "strength": "large"}]

scored_scenes = mde.score_scenes(scenes=raw_scenes, motion_events=motion_events)

# 2. Classified audio beats
classified_beats = [
    {"time": 1.0, "strength": "drop"},
    {"time": 3.0, "strength": "strong"},
    {"time": 5.5, "strength": "weak"}
]

# 3. Map scenes to beats (128 BPM hype track)
beat_timeline = mde.map_scenes_to_beats(
    scored_scenes=scored_scenes,
    classified_beats=classified_beats,
    bpm=128.0,
    avg_energy=0.8
)

print(f"Mapped Beat Blocks: {len(beat_timeline)} | Vibe: {beat_timeline[0]['_vibe']}")
```
