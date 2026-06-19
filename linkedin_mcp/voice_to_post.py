"""Voice-to-post: transcribe audio → humanize → draft.

Pipeline:
    audio file (mp3, m4a, wav, ogg)
        ↓
    ffmpeg converts to 16kHz mono WAV
        ↓
    Whisper transcription (local faster-whisper model)
        ↓
    humanize (light cleanup, remove fillers)
        ↓
    AI drafter (turn rambling into a polished post)
        ↓
    optional schedule / save as template

Whisper model is loaded lazily; first run downloads ~140MB. The model
is kept in memory for subsequent calls.

This module does NOT post automatically. It always returns a draft for
human review, unless `auto_post=true` and the safety gate allows it.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class VoiceError(Exception):
    """Base error for voice-to-post."""


class FFmpegNotFoundError(VoiceError):
    """ffmpeg binary not available on PATH."""


class AudioTooLongError(VoiceError):
    """Audio exceeds configured max duration."""


class TranscriptionError(VoiceError):
    """Whisper failed to transcribe."""


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


@dataclass
class VoiceDraft:
    """Result of a voice-to-post pipeline run."""

    source_path: str
    duration_seconds: float
    transcript_raw: str
    transcript_clean: str
    draft: str
    model: str
    language: str = "en"
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "duration_seconds": self.duration_seconds,
            "transcript_raw": self.transcript_raw,
            "transcript_clean": self.transcript_clean,
            "draft": self.draft,
            "model": self.model,
            "language": self.language,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def check_ffmpeg() -> bool:
    """Return True if ffmpeg is on PATH."""
    return shutil.which("ffmpeg") is not None


def probe_duration(audio_path: Path) -> float:
    """Use ffprobe to get audio duration in seconds. Returns 0.0 on failure."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return 0.0
    try:
        out = subprocess.run(  # noqa: S603
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)],
            capture_output=True, text=True, timeout=30,
        )
        return float(out.stdout.strip())
    except (subprocess.SubprocessError, ValueError):
        return 0.0


def convert_to_wav(src: Path, dst: Path) -> None:
    """Convert any ffmpeg-supported audio to 16kHz mono WAV."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise FFmpegNotFoundError(
            "ffmpeg not found on PATH. Install with: sudo apt install ffmpeg"
        )
    subprocess.run(  # noqa: S603
        [ffmpeg, "-y", "-i", str(src), "-ac", "1", "-ar", "16000",
         "-f", "wav", str(dst)],
        check=True, capture_output=True, timeout=120,
    )


# ---------------------------------------------------------------------------
# Transcriber (Whisper wrapper)
# ---------------------------------------------------------------------------


class Transcriber:
    """Lazy-loaded Whisper transcriber.

    The model is loaded once and cached. By default uses "base.en" which is
    ~140MB and fast on CPU. Override with `model_size` for accuracy trade-off:
        tiny.en, base.en, small.en, medium.en, large-v3
    """

    def __init__(self, model_size: str = "base.en") -> None:
        self.model_size = model_size
        self._model = None

    def _load(self):
        if self._model is not None:
            return self._model
        try:
            from faster_whisper import WhisperModel  # type: ignore
        except ImportError as exc:
            raise TranscriptionError(
                "faster-whisper not installed. Install with: "
                "pip install faster-whisper"
            ) from exc
        log.info("Loading Whisper model %s (first run downloads ~140MB)", self.model_size)
        # CPU-only by default; int8 quantization for speed
        self._model = WhisperModel(self.model_size, device="cpu", compute_type="int8")
        return self._model

    def transcribe(self, wav_path: Path, *, language: str = "en") -> tuple[str, str]:
        """Return (raw_text, detected_language)."""
        model = self._load()
        try:
            segments, info = model.transcribe(
                str(wav_path), language=language, beam_size=5
            )
            text_parts = [seg.text.strip() for seg in segments]
            return " ".join(p for p in text_parts if p), info.language
        except Exception as exc:
            raise TranscriptionError(f"Whisper failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Cleaner (remove fillers, normalize whitespace)
# ---------------------------------------------------------------------------

FILLER_WORDS = {
    "um", "uh", "er", "ah", "like", "you know", "i mean", "kinda", "sort of",
    "basically", "literally", "actually", "so yeah", "right", "okay so",
}


def clean_transcript(raw: str) -> str:
    """Light cleanup of a Whisper transcript for the drafter."""
    if not raw:
        return ""
    text = raw.strip()
    # Remove excessive whitespace
    text = " ".join(text.split())
    # Strip sentences that are just fillers (handle comma-suffixed ones)
    sentences = []
    for sent in text.replace("?", ".").replace("!", ".").split("."):
        s = sent.strip()
        if not s:
            continue
        s_clean = s.lower().strip(",. ")
        # Whole sentence is a filler
        if s_clean in FILLER_WORDS:
            continue
        # Sentence starts with a filler (single-word or multi-word like "you know")
        words = s_clean.split()
        stripped = False
        if words and words[0].rstrip(",") in FILLER_WORDS:
            s = s.split(",", 1)[-1].strip() if "," in s else " ".join(words[1:])
            stripped = True
        elif words and len(words) >= 2 and f"{words[0]} {words[1].rstrip(',')}" in FILLER_WORDS:
            # Multi-word filler at the start (e.g. "you know, ...")
            s = s.split(",", 1)[-1].strip() if "," in s else " ".join(words[2:])
            stripped = True
        if stripped and not s:
            continue
        sentences.append(s)
    return ". ".join(sentences) + ("." if sentences else "")


# ---------------------------------------------------------------------------
# Voice-to-post pipeline
# ---------------------------------------------------------------------------


DraftFn = Callable[[str], str]


class VoiceToPost:
    """Run the full voice → transcript → draft pipeline."""

    def __init__(
        self,
        transcriber: Transcriber | None = None,
        draft_fn: DraftFn | None = None,
        max_duration_seconds: float = 600.0,  # 10 min cap
    ) -> None:
        self.transcriber = transcriber or Transcriber()
        self.draft_fn = draft_fn
        self.max_duration_seconds = max_duration_seconds

    def run(
        self,
        audio_path: str | Path,
        *,
        language: str = "en",
        tone: str = "thought-leadership",
    ) -> VoiceDraft:
        """Transcribe + clean + draft. Always returns a draft for review."""
        src = Path(audio_path).expanduser().resolve()
        if not src.exists():
            raise FileNotFoundError(f"Audio file not found: {src}")
        if not check_ffmpeg():
            raise FFmpegNotFoundError(
                "ffmpeg not found. Install: sudo apt install ffmpeg"
            )

        # Probe duration
        duration = probe_duration(src)
        if duration > self.max_duration_seconds:
            raise AudioTooLongError(
                f"Audio is {duration:.0f}s, max is {self.max_duration_seconds:.0f}s"
            )

        # Convert to WAV
        with tempfile.TemporaryDirectory() as td:
            wav = Path(td) / "audio.wav"
            try:
                convert_to_wav(src, wav)
            except subprocess.CalledProcessError as exc:
                raise VoiceError(f"ffmpeg failed: {exc.stderr.decode(errors='replace')}") from exc

            # Transcribe
            try:
                raw, detected_lang = self.transcriber.transcribe(wav, language=language)
            except TranscriptionError:
                raise

        clean = clean_transcript(raw)

        # Draft
        draft_text = ""
        if self.draft_fn is not None:
            try:
                draft_text = self.draft_fn(clean)
            except Exception as exc:
                log.warning("Draft generation failed: %s", exc)
                draft_text = clean  # fall back to cleaned transcript

        return VoiceDraft(
            source_path=str(src),
            duration_seconds=duration,
            transcript_raw=raw,
            transcript_clean=clean,
            draft=draft_text,
            model=self.transcriber.model_size,
            language=detected_lang,
        )
