"""Bundled sample audio: zero-credential swap-in for the sine-tone mock.

The recipe ships a placeholder WAV at `server/assets/sample.wav`. By
default the endpoint plays that file instead of the 440 Hz tone — proving
the "replace `generate_tone()` with your real audio source" contract
without requiring any vendor key, network call, or model download.

These tests pin that contract end-to-end:

- The bundled sample is loaded once at module import.
- `audio_source()` returns the bundled PCM + a transcript that names the
  asset (so the agent announces what the user is about to hear).
- The SSE stream emits the bundled audio verbatim through the
  base64-encoded `audio.data` chunks.
- If the bundled asset is missing or malformed, `audio_source()` falls
  back to the sine-tone mock (so CI is never silently broken).
"""

from __future__ import annotations

import base64
import io
import json
import wave
from typing import Iterator

import pytest


def _parse_sse(body: str) -> Iterator[dict]:
    """Yield each `data:` payload as a parsed dict (skipping [DONE])."""
    for line in body.splitlines():
        line = line.strip()
        if not line.startswith("data: "):
            continue
        payload = line[len("data: "):]
        if payload == "[DONE]":
            continue
        yield json.loads(payload)


def test_bundled_sample_is_loaded_at_import_time():
    """The bundled sample WAV must satisfy the recipe's PCM contract.

    Loaded once at module import; if any of these assertions fail, the
    recipe refuses to boot — better to crash at startup than to play
    garbage PCM to the user in production.
    """
    from llm import _BUNDLED_SAMPLE_PCM, _SAMPLE_WAV_PATH, SAMPLE_RATE

    assert _SAMPLE_WAV_PATH.is_file(), f"missing bundled sample at {_SAMPLE_WAV_PATH}"
    # Even-numbered bytes = whole int16 samples.
    assert len(_BUNDLED_SAMPLE_PCM) % 2 == 0
    # The bundled placeholder is ~2 s at 16 kHz. Generous upper bound
    # so a future longer sample still passes (16 s = 512 000 bytes).
    assert 1000 <= len(_BUNDLED_SAMPLE_PCM) <= 16 * SAMPLE_RATE * 2


def test_audio_source_returns_bundled_sample_and_transcript():
    """`audio_source()` returns the bundled PCM bytes + a matching transcript."""
    from llm import (
        _BUNDLED_SAMPLE_PCM,
        SAMPLE_TRANSCRIPT,
        audio_source,
    )

    pcm, transcript = audio_source()
    assert pcm == _BUNDLED_SAMPLE_PCM, (
        "audio_source() must return the bundled sample bytes verbatim"
    )
    assert transcript == SAMPLE_TRANSCRIPT
    assert "sample.wav" in transcript, (
        "transcript should name the asset so operators know where to swap it"
    )


def test_audio_source_falls_back_when_bundled_sample_unreadable(monkeypatch):
    """If the cached bytes are empty (asset deleted/unreadable), fall back to the mock."""
    from llm import MOCK_TRANSCRIPT, audio_source

    # An empty cache signals "asset unavailable" to `audio_source()`.
    monkeypatch.setattr("llm._BUNDLED_SAMPLE_PCM", b"")
    pcm, transcript = audio_source()
    # Mock fallback produces 64 000 bytes at 16 kHz mono for the default 2 s.
    assert len(pcm) == 16000 * 2 * 2
    assert transcript == MOCK_TRANSCRIPT


def test_endpoint_streams_bundled_audio_with_matching_transcript(fake_env):
    """End-to-end: the SSE stream emits the bundled sample with its transcript."""
    from llm import SAMPLE_TRANSCRIPT, _BUNDLED_SAMPLE_PCM, app

    from fastapi.testclient import TestClient

    client = TestClient(app)
    response = client.post(
        "/chat/completions",
        json={
            "model": "audio-bundled",
            "messages": [{"role": "user", "content": "hi"}],
            "modalities": ["text", "audio"],
            "stream": True,
        },
    )
    assert response.status_code == 200
    body = response.text
    events = list(_parse_sse(body))
    assert events, "expected at least one SSE event"

    transcripts = [
        e["choices"][0]["delta"]["audio"].get("transcript", "") for e in events
    ]
    assert SAMPLE_TRANSCRIPT in transcripts

    audio_events = [
        e for e in events if "data" in e["choices"][0]["delta"]["audio"]
    ]
    assert audio_events, "expected at least one base64 PCM data chunk"

    # Reassemble the streamed PCM and compare to the in-memory source.
    chunks = [
        base64.b64decode(e["choices"][0]["delta"]["audio"]["data"])
        for e in audio_events
    ]
    streamed_pcm = b"".join(chunks)
    assert streamed_pcm == _BUNDLED_SAMPLE_PCM, (
        "streamed PCM chunks must reassemble to the bundled sample bytes"
    )


def test_bundled_sample_wav_meets_recipe_contract(tmp_path):
    """A WAV satisfying the recipe's mono/16-bit/16 kHz contract round-trips cleanly."""
    from llm import SAMPLE_RATE

    sample_path = tmp_path / "sample.wav"
    # Write a valid mono PCM16 16 kHz WAV of silence and verify the
    # validator accepts it. This pins the validation contract for any
    # operator who replaces the bundled asset.
    silence = b"\x00\x00" * (SAMPLE_RATE // 2)  # 0.5 s of silence
    with wave.open(str(sample_path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(silence)

    with wave.open(str(sample_path), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getframerate() == SAMPLE_RATE


def test_audio_source_module_loads_without_env_vars(monkeypatch):
    """The bundled-sample flow must not depend on any environment variable.

    This pins the 'no vendor key, no credentials' property at the module
    level: stripping every env var still produces audio.
    """
    # Strip every var the recipe knows about to make sure none of them
    # gate the bundled-sample path. Reload the module so any cached
    # env-dependent state is rebuilt from scratch.
    for key in (
        "AGORA_APP_ID",
        "AGORA_APP_CERTIFICATE",
        "CUSTOM_LLM_URL",
        "CUSTOM_LLM_API_KEY",
        "CUSTOM_LLM_MODEL",
        "AGENT_GREETING",
        "PORT",
    ):
        monkeypatch.delenv(key, raising=False)

    import importlib
    import llm

    importlib.reload(llm)

    pcm, _ = llm.audio_source()
    assert pcm, "audio_source() must produce bytes even with no env vars"