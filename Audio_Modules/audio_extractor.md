# 📄 Module Documentation: `audio_extractor.py`

**Rating**: `8.8 / 10 (Grade A)`  
**Location**: `D:\AMTCE\AMTCE_Elite_Core\Audio_and_Beat_Sync\audio_extractor.py`  
**Target File Link**: [audio_extractor.py](file:///D:/AMTCE/AMTCE_Elite_Core/Audio_and_Beat_Sync/audio_extractor.py)

---

## 🎯 Purpose & Role

`audio_extractor.py` is a lightweight, production-grade utility function responsible for extracting and standardizing audio streams from video files. 

It strips video tracks and converts raw audio into **mono 16kHz 16-bit PCM WAV audio** (`pcm_s16le`), which is the required standard input format for downstream speech recognition (Whisper / Vosk) and beat detection algorithms ([beat_engine.py](file:///D:/AMTCE/AMTCE_Elite_Core/Audio_and_Beat_Sync/beat_engine.py)).

---

## 🛠️ API Reference & Function Signature

```python
def extract_audio(video_path: str, output_path: str) -> bool
```

### Parameters:
* `video_path` (`str`): Absolute path to the source video file (e.g., `.mp4`, `.mkv`, `.mov`).
* `output_path` (`str`): Absolute path where the extracted `.wav` audio file will be saved.

### Return Value:
* `bool`: Returns `True` if FFmpeg successfully extracted the audio file; returns `False` if an error occurs.

---

## ⚙️ FFmpeg Command Line Breakdown

Under the hood, `audio_extractor.py` executes the following optimized FFmpeg subprocess command:

```bash
ffmpeg -y -i <video_path> -vn -acodec pcm_s16le -ar 16000 -ac 1 <output_path>
```

| FFmpeg Flag | Engineering Explanation |
| :--- | :--- |
| `-y` | Automatically overwrites output path if the file already exists. |
| `-i <video_path>` | Specifies input video file. |
| `-vn` | Disables video recording (strips video stream for fast processing). |
| `-acodec pcm_s16le` | Forces uncompressed 16-bit Little-Endian PCM WAV audio codec. |
| `-ar 16000` | Sets audio sampling rate to 16,000 Hz (16 kHz standard for speech-to-text & DSP). |
| `-ac 1` | Downmixes multi-channel audio (stereo/surround) to a single mono channel. |

---

## 🔄 Downstream Pipeline Integration

```
[Raw MP4 Video] 
      │
      ▼
audio_extractor.py (Extracts mono 16kHz WAV)
      │
      ├───> beat_engine.py (Spectral FFT beat analysis)
      └───> Whisper / Gemini Speech-to-Text (Word-level transcription)
```

1. **Pre-processing Gate**: Serves as the very first step in the audio pipeline when processing raw user uploads.
2. **Standardization**: Eliminates codec incompatibility issues by ensuring all downstream audio processors receive identical 16kHz mono WAV streams.
