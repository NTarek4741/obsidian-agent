"""
Input pipelines for the Obsidian Agent.

Each pipeline is a self-contained function that takes raw input and delivers
a proper timestamped transcript for the agent.

Functions:
    transcribe_file    — Local audio file → transcript
    transcribe_youtube — YouTube URL → transcript + metadata
    transcribe_live    — WAV bytes → transcript + metadata

Classes:
    LiveRecorder       — Microphone recording utility
"""

from __future__ import annotations

import asyncio
import io
import re
import time
import wave
from datetime import datetime
from pathlib import Path

import numpy as np
import sounddevice as sd
from dedalus_labs import AsyncDedalus
from youtube_transcript_api import YouTubeTranscriptApi
from yt_dlp import YoutubeDL

from obsidian_agent.orchestrator.utility import _ensure_vault_path, _load_api_key

AUDIO_EXTENSIONS = {
    ".mp3",
    ".m4a",
    ".wav",
    ".ogg",
    ".webm",
    ".mp4",
    ".mov",
    ".flac",
    ".aac",
}


def _validate_file_path(file_path: str) -> Path:
    path_str = file_path.strip()
    if not path_str:
        raise ValueError("No path provided.")
    path = Path(path_str)
    if not path.exists():
        raise ValueError(f"File not found: {path_str}")
    if path.suffix.lower() not in AUDIO_EXTENSIONS:
        raise ValueError(f"'{path_str}' does not look like a supported audio file.")
    return path


# ── Shared transcription helpers ────────────────────────────────────────


def _format_timestamp(seconds: float) -> str:
    total = int(seconds)
    hrs = total // 3600
    mins = (total % 3600) // 60
    secs = total % 60
    if hrs > 0:
        return f"{hrs:02d}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"


def _seg_value(seg, key: str, default=None):
    if hasattr(seg, "get"):
        return seg.get(key, default)
    return getattr(seg, key, default)


def _segments_to_transcript(segments: list) -> str:
    lines = []
    for seg in segments:
        start = _seg_value(seg, "start", 0.0)
        text = (_seg_value(seg, "text", "") or "").strip()
        if text:
            lines.append(f"[{_format_timestamp(start)}] {text}")
    return "\n".join(lines)


async def _transcribe_bytes(filename: str, audio_bytes: bytes) -> str:
    api_key = _load_api_key()
    client = AsyncDedalus(api_key=api_key, timeout=1800)

    response = await client.audio.transcriptions.create(
        file=(filename, audio_bytes),
        model="openai/whisper-1",
        response_format="verbose_json",
    )

    segments = getattr(response, "segments", None)
    if segments is None:
        try:
            data = (
                response.model_dump()
                if hasattr(response, "model_dump")
                else dict(response)
            )
            segments = data.get("segments", [])
        except Exception:
            segments = []

    if not segments:
        return response.text

    return _segments_to_transcript(segments)


# ── File transcription ──────────────────────────────────────────────────


async def transcribe_file(file_path: str) -> str:
    """Transcribe a local audio file. Returns a timestamped transcript string."""
    _validate_file_path(file_path)
    path = Path(file_path)
    audio_bytes = path.read_bytes()
    return await _transcribe_bytes(path.name, audio_bytes)


# ── YouTube transcription ───────────────────────────────────────────────

YOUTUBE_ID_PATTERNS = [
    r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/)([a-zA-Z0-9_-]{11})",
    r"youtube\.com/watch\?.*v=([a-zA-Z0-9_-]{11})",
]


def _extract_video_id(url: str) -> str | None:
    for pattern in YOUTUBE_ID_PATTERNS:
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    return None


class _NoOpLogger:
    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        pass


def _fetch_metadata(url: str) -> dict:
    ydl_opts = {
        "quiet": True,
        "skip_download": True,
        "logger": _NoOpLogger(),
        "warnings": "no_warnings",
    }
    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        return {
            "title": info.get("title", "Untitled Video"),
            "channel": info.get("uploader") or info.get("channel", ""),
        }
    except Exception:
        return {"title": "Untitled Video", "channel": ""}


def _fetch_captions(url: str) -> str:
    video_id = _extract_video_id(url)
    if not video_id:
        raise RuntimeError("Could not extract a valid YouTube video ID from the URL.")

    ytt_api = YouTubeTranscriptApi()
    transcript = ytt_api.fetch(video_id)
    segments = [
        {"start": snippet.start, "text": snippet.text} for snippet in transcript
    ]
    return _segments_to_transcript(segments)


async def transcribe_youtube(url: str) -> dict:
    """Transcribe a YouTube video. Returns {'transcript': str, 'source_info': str}."""
    if "youtube.com" not in url and "youtu.be" not in url:
        raise ValueError("URL does not look like a YouTube link.")

    metadata = await asyncio.to_thread(_fetch_metadata, url)
    captions = await asyncio.to_thread(_fetch_captions, url)

    parts = [
        f"Title: {metadata['title']}",
        f"Source: {url}",
    ]
    if metadata.get("channel"):
        parts.append(f"Channel: {metadata['channel']}")

    header = "\n".join(parts)
    return {"transcript": captions, "source_info": header}


# ── Live recording ──────────────────────────────────────────────────────


class LiveRecorder:
    """Records microphone audio into an in-memory WAV buffer."""

    def __init__(self, samplerate: int = 16000, channels: int = 1):
        self.samplerate = samplerate
        self.channels = channels
        self._frames: list[np.ndarray] = []
        self._is_recording = False
        self._stream = None
        self._start_time: float = 0.0

    def _callback(self, indata, frames, time_info, status):
        if self._is_recording:
            data = np.frombuffer(indata, dtype=np.int16).reshape(-1, self.channels)
            self._frames.append(data.copy())

    def start(self) -> None:
        self._frames = []
        self._is_recording = True
        self._start_time = time.time()
        self._stream = sd.RawInputStream(
            samplerate=self.samplerate,
            channels=self.channels,
            dtype=np.int16,
            callback=self._callback,
        )
        self._stream.start()

    def stop(self) -> bytes:
        self._is_recording = False
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None

        if not self._frames:
            return b""

        audio_data = np.concatenate(self._frames, axis=0)
        wav_io = io.BytesIO()
        with wave.open(wav_io, "wb") as wav_file:
            wav_file.setnchannels(self.channels)
            wav_file.setsampwidth(2)
            wav_file.setframerate(self.samplerate)
            wav_file.writeframes(audio_data.tobytes())
        return wav_io.getvalue()

    @property
    def elapsed(self) -> float:
        return time.time() - self._start_time if self._start_time else 0.0

    @property
    def frame_count(self) -> int:
        return len(self._frames)


async def transcribe_live(wav_bytes: bytes) -> dict:
    """Save WAV to vault folder, transcribe, and return result dict."""
    agent_folder = _ensure_vault_path()
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    recording_name = f"live_recording_{timestamp}.wav"
    recording_path = Path(agent_folder) / recording_name
    recording_path.write_bytes(wav_bytes)

    transcript = await _transcribe_bytes(recording_name, wav_bytes)

    return {
        "transcript": transcript,
        "recording_path": str(recording_path),
        "source_info": f"Live recording: {recording_path.name}",
    }
