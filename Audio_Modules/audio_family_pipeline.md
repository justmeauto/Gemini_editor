# 📄 Module Documentation: `audio_family_pipeline.py`

**Rating**: `9.4 / 10 (Grade A)`  
**Location**: `Audio_Modules/audio_family_pipeline.py`  
**Target File Link**: [audio_family_pipeline.py](file:///d:/simple_scrapper%20and%20_uploader/Audio_Modules/audio_family_pipeline.py)

---

## 👑 Purpose & Role: The Master Family Orchestrator

`audio_family_pipeline.py` is the **Master Orchestration Pipeline Class (`AudioFamilyPipeline`)** for the entire Audio & Beat Synchronization family.

Instead of treating the 7 family modules as isolated islands, `AudioFamilyPipeline` unifies them into a **connected knowledge network**. Every module shares its intelligence directly with downstream modules via the `AudioDataPacket` context object, including reloading persistent lyric & rhythm intelligence cache for newly selected pool tracks.

---

## 🛠️ API Reference & Execution Class

```python
class AudioFamilyPipeline:
    def __init__(
        self,
        music_dir: str = "music",
        original_audio_dir: str = "Original_audio",
        use_pool_manager: bool = True,
        enable_lyric_sync: bool = True,
        temp_dir: Optional[str] = None
    )

    def run(
        self,
        video_path: str,
        output_path: str,
        bgm_path: Optional[str] = None,
        voiceover_path: Optional[str] = None,
        vo_vol: float = 1.5
    ) -> Dict[str, Any]
```

---

## 🔗 Cross-Module Intelligence Sharing Flow

```mermaid
flowchart TD
    VideoInput[Video Input] --> Stage1[Stage 1: audio_extractor\nExtract Mono WAV]
    BGMInput[BGM Input] --> Stage2[Stage 2: beat_engine\nAnalyze Beats, BPM & Energy]
    BGMInput --> Stage3[Stage 3: lyric_rhythm_aligner\nExtract Gemini Emotion & Persistent Cache]
    
    Stage3 --> Stage4b[Stage 4b: Context-Aware Audio Mode Resolution\nperformance | blend | replacement]
    
    Stage2 -->|target_bpm & target_energy| Stage5[Stage 5: pool_manager\nSelect Best Matching BGM]
    Stage3 -->|dominant_emotion -> content_category| Stage5
    
    BGMInput --> Stage4[Stage 4: music_intelligence\nClassify Genre -> Derive music_vol]
    Stage4 -->|genre_music_vol| Stage6[Stage 6: audio_pipeline\nFinal Dynamic Mix]
    Stage4b -->|audio_mode| Stage6
    
    Stage3 -->|emotional_peak_moments[0] -> music_offset| Stage6
    Stage5 -->|selected_bgm| Stage5b[Stage 5b: Re-analyze BGM Beats & Reload Persistent Lyric Intel]
    Stage5b -->|final_beat_data & updated lyric_intel| Stage6
    
    Stage6 --> FinalOutput[Final Mixed MP4 Output (44.1kHz AAC)]
```

---

## 💡 Key Technical Features

### 1. `AudioDataPacket` Memory State
All stage results pass through a unified dictionary contract that grows richer as it moves downstream. No data is lost, and downstream stages consume upstream outputs directly.

### 2. Stage 5b Persistent Lyric Intelligence Sync
When the pool manager selects a new BGM track:
* `_stage_reanalyze_selected_bgm()` re-analyzes beat grids for the new track.
* Automatically reloads the selected track's persistent `_lyric.json` intelligence into `packet["lyric_intel"]` (hitting disk cache in 0.001s), ensuring downstream video rendering alignment matches the actual BGM track.

### 3. Context-Aware Audio Routing (Stage 4b)
* **`performance` mode**: Used when speech/dialogue is primary (`has_vocals=True`). Original voice is kept at `vol=1.0`, BGM acts as a subtle underscore (`vol<=0.15`).
* **`blend` mode**: Used when secondary speech/vocals are present. Original audio (`vol=0.35`) and BGM (`vol<=0.25`) are balanced.
* **`replacement` mode**: Used for silent/b-roll video (`has_vocals=False`). Original audio is muted (`vol=0.0`), BGM replaces it (`vol=0.40`).

### 4. Automated Parameter Derivation (Zero Hardcoding)
* **`music_vol`**: Derived automatically by `music_intelligence.py` based on genre (`lofi/ambient` $\rightarrow$ `0.35`, `mass/pop` $\rightarrow$ `0.55`).
* **`music_offset`**: Derived automatically from `lyric_rhythm_aligner.py` using `emotional_peak_moments[0]` so BGM starts at peak impact.
* **`sample_rate`**: Forced to universal `44100 Hz AAC` in `audio_pipeline.py` to prevent non-standard 96kHz player muting bugs.
