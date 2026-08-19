# 📄 Module Documentation: `audio_pipeline.py`

**Rating**: `8.6 / 10 (Grade A-)`  
**Location**: `D:\AMTCE\AMTCE_Elite_Core\Audio_and_Beat_Sync\audio_pipeline.py`  
**Target File Link**: [audio_pipeline.py](file:///D:/AMTCE/AMTCE_Elite_Core/Audio_and_Beat_Sync/audio_pipeline.py)

---

## 🎯 Purpose & Role

`audio_pipeline.py` is the multi-track audio mixing engine of AMTCE. It is responsible for taking three distinct audio streams (Original Video Audio, AI Voiceover, and Background Music), applying dynamic sidechain compression (audio ducking), normalizing volume levels, and multiplexing the final audio stream into the output video.

---

## 🛠️ API Reference & Function Signature

```python
def mix_audio(
    video_path: str,
    output_path: str,
    voiceover_path: Optional[str] = None,
    music_path: Optional[str] = None,
    music_vol: float = 0.2,
    vo_vol: float = 1.5,
    duration: Optional[float] = None,
    music_offset: Optional[float] = 0.0,
) -> bool
```

### Parameters:
* `video_path` (`str`): Path to input video file.
* `output_path` (`str`): Path where the final mixed video file will be saved.
* `voiceover_path` (`Optional[str]`): Optional path to generated TTS narration (`.mp3`/`.wav`).
* `music_path` (`Optional[str]`): Optional path to background music track.
* `music_vol` (`float`, default `0.2`): Volume multiplier for background music.
* `vo_vol` (`float`, default `1.5`): Volume multiplier for voiceover narration.
* `duration` (`Optional[float]`): Target duration limit.
* `music_offset` (`Optional[float]`, default `0.0`): Start offset timestamp in seconds for background music.

---

## 💡 Key Technical Features

### 1. Dynamic Sidechain Compression (Audio Ducking)
When both Voiceover and Music are active, `audio_pipeline.py` splits the voiceover signal and feeds a trigger stream into FFmpeg's `sidechaincompress` filter:
```ini
[a_mus_pre][a_vo_trig]sidechaincompress=threshold=0.1:ratio=4:attack=20:release=700[a_mus_duck]
```
* **Effect**: Automatically lowers (ducks) the background music volume by 12dB whenever the narrator speaks, and smoothly recovers background volume during pauses.

### 2. Audio Stream Fallback (`anullsrc`)
If the source video has no native audio stream, `audio_pipeline.py` generates a synthetic silent stereo audio track (`anullsrc=channel_layout=stereo:sample_rate=44100`) to prevent FFmpeg filtergraph failures.

### 3. Video Freeze Protection (`duration=first`)
To prevent the video from freezing on the final frame while audio continues playing, `audio_pipeline.py` enforces:
```ini
[a_orig][a_vo_mix][a_mus_duck]amix=inputs=3:duration=first:dropout_transition=0[a_mixed]
```
The `duration=first` parameter locks master audio duration strictly to the video length.

### 4. Copyright Fingerprint Bypass (Heavy Remixing)
Applies dynamic pitch and speed remixing to background music (`heavy_remix`) while bypassing original audio tracks to preserve beat-sync accuracy.

---

## 🔄 Audio Stream Mixing Pipeline

```mermaid
flowchart TD
    Video[Video Input [0:a]] --> OrigVolume[Mute or Volume=0.0]
    VO[Voiceover Input [1:a]] --> VOSplit[Split Stream -> [a_vo_mix] & [a_vo_trig]]
    BGM[Music Input [2:a]] --> TrimOffset[Apply Offset & Volume=0.2]
    
    BGM --> Sidechain[FFmpeg sidechaincompress]
    VOSplit -->|Trigger Signal| Sidechain
    
    OrigVolume --> AMix[FFmpeg amix inputs=3 duration=first]
    VOSplit -->|Mix Stream| AMix
    Sidechain -->|Ducked Music| AMix
    
    AMix --> Output[Final Video Output]
```
