"""Tests for voice_to_post (transcriber mocked, ffmpeg mocked)."""

from __future__ import annotations

from pathlib import Path

import pytest

from linkedin_mcp.voice_to_post import (
    AudioTooLongError,
    FFmpegNotFoundError,
    Transcriber,
    TranscriptionError,
    VoiceDraft,
    VoiceToPost,
    check_ffmpeg,
    clean_transcript,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_check_ffmpeg():
    # Just check it doesn't raise; result depends on host
    result = check_ffmpeg()
    assert isinstance(result, bool)


def test_clean_transcript_removes_fillers():
    raw = "Um, I think AI is changing things. Uh, but we need to be careful. You know, it is just a tool."
    out = clean_transcript(raw)
    assert "Um," not in out
    assert "Uh," not in out
    assert "You know," not in out
    assert "AI is changing" in out


def test_clean_transcript_empty():
    assert clean_transcript("") == ""
    assert clean_transcript("   ") == ""


def test_clean_transcript_preserves_punctuation():
    raw = "I shipped a new tool today and it is already in production handling real traffic from real users."
    out = clean_transcript(raw)
    assert out.startswith("I shipped")
    assert out.endswith(".")


# ---------------------------------------------------------------------------
# VoiceToPost.run with mocks
# ---------------------------------------------------------------------------


def test_run_missing_file(tmp_path: Path):
    v = VoiceToPost()
    with pytest.raises(FileNotFoundError):
        v.run(tmp_path / "nope.mp3")


def test_run_no_ffmpeg(tmp_path: Path, monkeypatch):
    """If ffmpeg missing, FFmpegNotFoundError."""
    # Create a fake audio file
    f = tmp_path / "test.mp3"
    f.write_bytes(b"fake mp3 bytes")
    v = VoiceToPost()
    monkeypatch.setattr("linkedin_mcp.voice_to_post.check_ffmpeg", lambda: False)
    with pytest.raises(FFmpegNotFoundError):
        v.run(f)


def test_run_audio_too_long(tmp_path: Path, monkeypatch):
    f = tmp_path / "test.mp3"
    f.write_bytes(b"x")
    v = VoiceToPost(max_duration_seconds=10)
    monkeypatch.setattr("linkedin_mcp.voice_to_post.check_ffmpeg", lambda: True)
    monkeypatch.setattr("linkedin_mcp.voice_to_post.probe_duration", lambda p: 9999.0)
    with pytest.raises(AudioTooLongError):
        v.run(f)


def test_run_full_pipeline_mocked(tmp_path: Path, monkeypatch):
    f = tmp_path / "test.mp3"
    f.write_bytes(b"x")

    # Mock all the I/O
    monkeypatch.setattr("linkedin_mcp.voice_to_post.check_ffmpeg", lambda: True)
    monkeypatch.setattr("linkedin_mcp.voice_to_post.probe_duration", lambda p: 30.0)
    monkeypatch.setattr("linkedin_mcp.voice_to_post.convert_to_wav", lambda s, d: None)

    # Mock transcriber
    class FakeTranscriber:
        model_size = "tiny.en"
        def transcribe(self, wav_path, language="en"):
            return (
                "Um, I think AI is changing how we ship software today, friends.",
                "en",
            )

    v = VoiceToPost(transcriber=FakeTranscriber(), draft_fn=lambda t: "Polished: " + t)
    out = v.run(f)
    assert isinstance(out, VoiceDraft)
    assert out.duration_seconds == 30.0
    assert "AI is changing" in out.transcript_raw
    assert "Polished:" in out.draft
    assert out.language == "en"


def test_run_falls_back_to_clean_transcript_if_draft_fails(tmp_path: Path, monkeypatch):
    f = tmp_path / "test.mp3"
    f.write_bytes(b"x")
    monkeypatch.setattr("linkedin_mcp.voice_to_post.check_ffmpeg", lambda: True)
    monkeypatch.setattr("linkedin_mcp.voice_to_post.probe_duration", lambda p: 30.0)
    monkeypatch.setattr("linkedin_mcp.voice_to_post.convert_to_wav", lambda s, d: None)

    class FakeTranscriber:
        model_size = "tiny.en"
        def transcribe(self, wav_path, language="en"):
            return "Um, hello world today", "en"

    def bad_draft(t):
        raise RuntimeError("LLM down")

    v = VoiceToPost(transcriber=FakeTranscriber(), draft_fn=bad_draft)
    out = v.run(f)
    # Falls back to cleaned transcript
    assert out.draft == out.transcript_clean


def test_run_to_dict():
    d = VoiceDraft(
        source_path="/tmp/x.mp3",  # noqa: S108
        duration_seconds=10.0,
        transcript_raw="hello world",
        transcript_clean="hello world.",
        draft="Draft: hello world.",
        model="base.en",
        language="en",
    )
    out = d.to_dict()
    assert out["model"] == "base.en"
    assert out["duration_seconds"] == 10.0


# ---------------------------------------------------------------------------
# Transcriber class (loading, import error)
# ---------------------------------------------------------------------------


def test_transcriber_load_raises_if_no_faster_whisper(monkeypatch):
    """Simulate missing faster-whisper by patching import to fail."""
    import builtins

    def fake_import(name, *args, **kwargs):
        if name == "faster_whisper" or name.startswith("faster_whisper."):
            raise ImportError("simulated missing")
        return builtins.__import__(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    t = Transcriber("tiny.en")
    with pytest.raises(TranscriptionError) as exc_info:
        t._load()
    assert "faster-whisper" in str(exc_info.value).lower()
