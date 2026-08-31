"""
Custom Audio LLM endpoint — bundled-sample PLACEHOLDER (custom-llm-tts recipe).

==========================================================================
!! THIS IS A PLACEHOLDER, NOT A REAL TTS/LLM INTEGRATION !!
==========================================================================
The default `audio_source()` returns the bytes of a bundled WAV at
`server/assets/sample.wav` (a 2-second mono PCM16 16 kHz tone). This proves
the recipe's "replace the mock with your real audio source" pattern without
needing any external account, API key, network call, or model download —
keeping the PR trivially reviewable and CI green.

Operators who want real audio drop a different file at the same path. See
README.md "Replacing the bundled asset" and the `audio_source()` docstring
below. The bundled WAV is deliberately a placeholder, not real speech.

==========================================================================

An OpenAI-compatible audio-modalities app that Agora cloud calls during a
conversation. It is mounted into the API server at `/audio` (see server.py), so
the public route is `POST /audio/chat/completions`. Instead of returning text
(delta.content), it returns AUDIO directly (delta.audio), bypassing TTS.

Contract — POST /chat/completions  (→ /audio/chat/completions once mounted), SSE:
  1. Transcript chunk:  choices[0].delta.audio = {"id": <id>, "transcript": <text>}
  2. Audio chunks:      choices[0].delta.audio = {"id": <id>, "data": <base64 PCM>}
  3. Terminates with:   data: [DONE]

Audio format: PCM16, 16 kHz, mono, 1280-byte (40 ms) chunks.

IMPORTANT: the transcript is NOT just for display. Agora cloud stores it as the
agent's conversation context; omitting `audio.transcript` means the agent will
not remember what it said.

Provider-agnostic by design — do NOT import `agora_agent` here (enforced by
server/tests/test_llm_mount.py). This is the component you replace with your own
model / TTS / pre-recorded clips, keeping the PCM format. A production endpoint
should also validate the `Authorization: Bearer` header that Agora cloud forwards.
"""
import asyncio
import base64
import json
import logging
import math
import struct
import uuid
import wave
from pathlib import Path
from typing import Dict, List, Optional, Union

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Custom Audio LLM Server (Mock)",
    description=(
        "OpenAI-compatible audio-modalities endpoint for Agora Conversational AI. "
        "Returns audio directly (delta.audio), bypassing TTS."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =============================================================================
# Request models — match what Agora ConvoAI Engine sends for audio modalities
# =============================================================================

class TextContent(BaseModel):
    type: str = "text"
    text: str


class SystemMessage(BaseModel):
    role: str = "system"
    content: Union[str, List[str]]


class UserMessage(BaseModel):
    role: str = "user"
    content: Union[str, List[Union[TextContent, Dict]]]


class AssistantMessage(BaseModel):
    role: str = "assistant"
    content: Union[str, List[TextContent], None] = None
    audio: Optional[Dict[str, str]] = None


class ChatCompletionRequest(BaseModel):
    model: Optional[str] = None
    messages: List[Union[SystemMessage, UserMessage, AssistantMessage]]
    modalities: List[str] = ["text", "audio"]
    audio: Optional[Dict[str, str]] = None
    stream: bool = True
    stream_options: Optional[Dict] = None


# =============================================================================
# Mock audio generation — sine-wave tone (pure stdlib)
# =============================================================================
# Replace this with your real audio source. Keep PCM16 / 16 kHz / mono.
# =============================================================================

SAMPLE_RATE = 16000      # 16 kHz
BYTES_PER_SAMPLE = 2     # PCM16
CHUNK_DURATION_MS = 40   # 40 ms per chunk
CHUNK_SIZE = int(SAMPLE_RATE * BYTES_PER_SAMPLE * CHUNK_DURATION_MS / 1000)  # 1280 bytes

MOCK_TRANSCRIPT = "This is a mock audio response from the custom LLM-TTS endpoint."

# Bundled sample audio. Loaded from `server/assets/sample.wav` once at
# module import. Demonstrates the "real audio replaces mock" pattern with
# zero external dependencies — no API key, no network call, no model
# download. Operators replace the file at the same path to ship their
# own audio (e.g. a voice-clipped WAV produced by an offline TTS engine).
#
# The asset is intentionally a *placeholder*: a 2-second mono tone the
# recipe author bundled, not a real speech recording. This keeps the PR
# credential-free and reviewable in a few minutes — operators replace
# the file with whatever audio they want shipped.
SAMPLE_TRANSCRIPT = (
    "This is a bundled sample audio. Replace `server/assets/sample.wav` "
    "with your own PCM16 mono 16 kHz WAV to ship custom audio."
)
_SAMPLE_WAV_PATH = Path(__file__).resolve().parent.parent / "assets" / "sample.wav"


def _load_bundled_sample() -> bytes:
    """Read and validate the bundled `sample.wav` at module import time.

    Raises if the asset is missing or malformed — the recipe refuses to
    boot in that state so operators notice immediately rather than
    hearing a 440 Hz tone in production. The PCM bytes are cached in a
    module-level variable for hot-path reuse.
    """
    if not _SAMPLE_WAV_PATH.is_file():
        raise RuntimeError(
            f"Bundled sample audio missing at {_SAMPLE_WAV_PATH}. "
            "The recipe ships a placeholder WAV; restore it from the repo."
        )
    try:
        with wave.open(str(_SAMPLE_WAV_PATH), "rb") as wav:
            if wav.getnchannels() != 1:
                raise RuntimeError(
                    f"Bundled sample has {wav.getnchannels()} channels; expected mono."
                )
            if wav.getsampwidth() != 2:
                raise RuntimeError(
                    f"Bundled sample has sample width {wav.getsampwidth()}; expected 2 (PCM16)."
                )
            if wav.getframerate() != SAMPLE_RATE:
                raise RuntimeError(
                    f"Bundled sample framerate is {wav.getframerate()}; expected {SAMPLE_RATE}."
                )
            return wav.readframes(wav.getnframes())
    except wave.Error as exc:
        raise RuntimeError(
            f"Failed to parse bundled sample at {_SAMPLE_WAV_PATH}: {exc}"
        ) from exc


# Module-level cache for the bundled sample PCM bytes. The recipe loads
# the asset once at import time so requests don't hit the disk. The cache
# is also where tests can inject a "broken" state to exercise the
# fallback path in `audio_source()`.
_BUNDLED_SAMPLE_PCM: bytes = _load_bundled_sample()


def generate_tone(duration_seconds: float = 2.0, frequency: float = 440.0) -> bytes:
    """Generate a mono PCM16 sine-wave tone with a short fade in/out envelope."""
    num_samples = int(SAMPLE_RATE * duration_seconds)
    fade = SAMPLE_RATE * 0.05  # 50 ms fade
    samples = []
    for i in range(num_samples):
        t = i / SAMPLE_RATE
        envelope = min(1.0, i / fade) * min(1.0, (num_samples - i) / fade)
        value = int(16000 * envelope * math.sin(2 * math.pi * frequency * t))
        samples.append(struct.pack("<h", max(-32768, min(32767, value))))
    return b"".join(samples)


def audio_source() -> tuple[bytes, str]:
    """Return `(pcm_bytes, transcript)` for the current request.

    *** THIS IS THE FUNCTION TO REPLACE WITH YOUR REAL AUDIO SOURCE. ***

    Default source is the bundled placeholder at `server/assets/sample.wav`,
    cached at module import. To ship your own audio, drop a different
    PCM16 mono 16 kHz WAV at that same path (or replace this function
    entirely). No credentials, no network.

    Falls back to the sine-tone mock if the cached bytes are empty (the
    sentinel for "asset unavailable") so the recipe never silently
    breaks — operators get audio either way and a warning in the log.
    """
    if not _BUNDLED_SAMPLE_PCM:
        logger.warning(
            "Bundled sample is empty; falling back to mock tone."
        )
        return generate_tone(), MOCK_TRANSCRIPT
    return _BUNDLED_SAMPLE_PCM, SAMPLE_TRANSCRIPT


def split_into_chunks(audio: bytes) -> List[bytes]:
    """Split PCM audio into fixed-size streaming chunks."""
    return [audio[i:i + CHUNK_SIZE] for i in range(0, len(audio), CHUNK_SIZE)]


@app.post("/chat/completions")
async def audio_chat_completions(
    request: ChatCompletionRequest,
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """
    OpenAI-compatible audio-modalities endpoint that Agora cloud calls.
    Streams a transcript chunk followed by base64 PCM audio chunks.
    """
    logger.info(
        "Received audio request: model=%s, modalities=%s, messages=%d",
        request.model, request.modalities, len(request.messages),
    )

    if not request.stream:
        raise HTTPException(status_code=400, detail="Only streaming mode is supported. Set stream=true.")

    audio_id = uuid.uuid4().hex
    message_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    pcm_audio, transcript = audio_source()
    chunks = split_into_chunks(pcm_audio)

    async def generate():
        # 1) Transcript chunk — REQUIRED: Agora cloud stores it as agent context.
        yield "data: " + json.dumps({
            "id": message_id,
            "choices": [{
                "index": 0,
                "delta": {"audio": {"id": audio_id, "transcript": transcript}},
                "finish_reason": None,
            }],
        }) + "\n\n"

        # 2) Audio chunks — base64-encoded PCM, ~40 ms real-time pacing.
        for chunk in chunks:
            yield "data: " + json.dumps({
                "id": message_id,
                "choices": [{
                    "index": 0,
                    "delta": {"audio": {"id": audio_id, "data": base64.b64encode(chunk).decode("utf-8")}},
                    "finish_reason": None,
                }],
            }) + "\n\n"
            await asyncio.sleep(CHUNK_DURATION_MS / 1000)

        # 3) Done.
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "custom-llm-tts-mock"}
