# Agora Conversational AI — Custom LLM-TTS Recipe (Python)

The **custom-llm-tts** recipe in the Agora Conversational AI recipes family. Your
endpoint returns **audio directly** — playing both the LLM and TTS roles — so Agora
plays it over RTC with **no separate TTS step**. STT (Deepgram) still transcribes the
user's speech for your endpoint.

This repo ships a **zero-key mock** that emits a sine-wave tone, so you can run the
full STT → custom audio endpoint → RTC pipeline immediately, then replace the mock.

## Prerequisites

- [Python 3.8+](https://www.python.org/)
- [Bun](https://bun.sh/)
- [ngrok](https://ngrok.com/) (or any tunnel to expose localhost)
- Agora App ID + App Certificate (the [Agora CLI](https://github.com/AgoraIO/cli) makes this easy)

The same commands work on macOS, Linux, and Windows. On macOS/Linux, setup uses
`python3`; on Windows, it uses the Python launcher (`py`) or `python`. WSL and
virtualenv activation are not required.

## Run it

```bash
# 1. Install web deps + create the Python venv
bun run setup

# 2. Add Agora credentials (CLI), or edit server/.env.local by hand
agora login
agora project use <your-project>
agora project env write server/.env.local

# 3. Expose the backend publicly (Agora cloud calls the mounted /audio endpoint)
ngrok http 8000

# 4. Add the tunnel URL (note the /audio path) to server/.env.local
#    CUSTOM_LLM_URL=https://<your-tunnel>.ngrok-free.dev/audio/chat/completions

# 5. Run all three services
bun run dev
```

Open [http://localhost:3000](http://localhost:3000) → **Start Conversation** → speak.
You'll hear the mock tone as the agent's reply.

## Architecture

```
Browser (localhost:3000)
  │  fetch /api/*
  ▼
Next.js  ──rewrite──▶  Agent backend  (server/, localhost:8000)
                          │  CustomLLM(output_modalities=["audio"])
                          ▼
                       Agora ConvoAI Cloud
                          │  POST <CUSTOM_LLM_URL>   (Authorization: Bearer)
                          ▼
                       Custom audio endpoint  (mounted at /audio in server/, :8000)
                          │  returns transcript + PCM audio (SSE)
                          ▲  public via ngrok tunnel
                       (no TTS — audio plays straight to RTC)
```

See [ARCHITECTURE.md](./ARCHITECTURE.md).

## Project structure

```
recipe-agent-custom-llm-tts/
├── server/   # Single backend (:8000) — token/agent endpoints + mounted /audio endpoint
│   ├── src/{server.py, agent.py, llm.py}   # llm.py: POST /audio/chat/completions, no agora deps
│   ├── scripts/run_fake_server.py
│   └── tests/{conftest.py, test_llm_mount.py}
├── web/      # Shared Next.js frontend (:3000)
└── package.json
```

## Environment variables

Backend env file: [`server/.env.example`](server/.env.example).

| Variable | Required | Default | Notes |
| --- | :---: | :---: | --- |
| `AGORA_APP_ID` | ✅ | — | Agora Console → Project → App ID |
| `AGORA_APP_CERTIFICATE` | ✅ | — | Agora Console → Project → App Certificate (server only) |
| `CUSTOM_LLM_URL` | ✅ | — | **Public** URL of the mounted `/audio` endpoint, ending in `/audio/chat/completions`. Agora cloud calls it; cannot be `localhost`. |
| `CUSTOM_LLM_API_KEY` | ✅ | `any-key-here` | Forwarded by Agora cloud as `Authorization: Bearer`. Required by the `CustomLLM` vendor. |
| `CUSTOM_LLM_MODEL` |  | `audio-mock` | Model name passed to your endpoint |
| `AGENT_GREETING` |  | built-in | Opening line (supported in audio mode via the messages protocol) |
| `PORT` |  | `8000` | Backend port (serves the token/agent endpoints and `/audio`) |
| `AGENT_BACKEND_URL` (web deploy) | ✅ | — | Required in a deployed `web` app when proxying to the backend |

## Commands

```bash
bun run setup            # install web deps + create the server/ venv
bun run dev              # run backend (:8000) + web (:3000)

bun run doctor           # prerequisite check (no creds needed)
bun run doctor:local     # + .env.local + credentials + CUSTOM_LLM_URL checks

bun run verify           # web-only gate (no Agora creds needed)
bun run verify:local     # full local gate: backend compile + smoke tests + web build
bun run clean            # remove venvs and build artifacts
```

## Replacing the mock

Replace `generate_tone()` in [`server/src/llm.py`](server/src/llm.py) with your real
audio source. Keep the SSE contract (transcript chunk + base64 PCM16/16kHz chunks +
`[DONE]`) — the transcript is required for agent context. Keep `llm.py` free of
`agora-agents` (a test enforces this). See [`server/README.md`](server/README.md).

### Example: bundled sample audio (zero-credential swap-in)

> **⚠️ This is a PLACEHOLDER, not a real TTS integration.**
> The bundled WAV is a 2-second 440 Hz tone with a short envelope — the
> recipe author generated it deterministically with Python's stdlib so the
> PR has zero external dependencies. **Operators replace the file** with
> whatever audio they actually want to ship (see the `ffmpeg` snippet below).

This fork ships a placeholder WAV at `server/assets/sample.wav` (mono PCM16,
16 kHz, ~2 s). On boot, `llm.py` loads the file once and the endpoint plays
that audio instead of the sine-tone mock. Operators who want their own audio
drop a different file at the same path — the recipe enforces the
mono/PCM16/16 kHz contract at module import so a malformed asset fails fast
instead of sending garbage PCM to the user.

```bash
# Replace the bundled placeholder with your own audio. Anything that
# satisfies the contract works: a clip rendered by an offline TTS engine,
# a recording from a microphone, a previously-synthesized sample.
ffmpeg -i your_audio.wav -ac 1 -ar 16000 -sample_fmt s16 \
    server/assets/sample.wav
```

The endpoint is intentionally credential-free: no API key, no model
download, no network call. This makes the recipe trivially reviewable and
keeps CI green without setup. Want a real TTS/LLM? Look at `audio_source()`
in [`server/src/llm.py`](server/src/llm.py) — that's the function this PR
leaves for you to swap with your provider of choice.

See [`server/tests/test_bundled_sample.py`](server/tests/test_bundled_sample.py)
for the behavioural contract: bundled-sample load, end-to-end SSE
streaming, fallback when the asset is missing, and module-loadability
without any environment variables.

### Zero-signup verification

The bundled-sample PR is intentionally verifiable end-to-end with **nothing
but `git clone` + `pip install fastapi uvicorn pytest` + `curl`** — no Agora
credentials, no vendor API key, no model download, no tunnel, no `bun`.

```bash
# 1. Install ONLY fastapi + uvicorn + pytest (no agora-agents, no bun)
pip install fastapi uvicorn pytest

# 2. Run the zero-signup smoke test (boots /chat/completions on a
#    random free port, streams the SSE contract, byte-compares the
#    returned PCM against server/assets/sample.wav)
./scripts/verify_zero_signup.sh
```

Or via pytest, if you prefer:

```bash
pytest server/tests/test_zero_signup_e2e.py -v
```

Expected output:

```
[verify] PASS — zero-signup demo works end-to-end
[verify]   - server boots without AGORA_APP_ID / AGORA_APP_CERTIFICATE
[verify]   - POST /chat/completions returns SSE with transcript + base64 PCM + [DONE]
[verify]   - streamed PCM (62xxx bytes) matches bundled asset byte-for-byte
```

This is the maintainer-facing "will this PR's demo work without me signing up
for anything?" gate. The Agora credentials only matter for the *full* recipe
(`server.py` token/agent endpoints), which is upstream and out of scope for
this PR. The contribution itself — `server/src/llm.py` — is fully
credential-free, and `scripts/verify_zero_signup.sh` proves it.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| Agent joins but no audio / garbled audio | `CUSTOM_LLM_URL` must be public and end in `/audio/chat/completions`; audio must be PCM16/16kHz/mono. |
| Agent doesn't remember context | Your endpoint must include `audio.transcript` in the first chunk. |
| `doctor:local` warns about localhost | Replace the local URL with your public tunnel URL. |
| Local calls fail under a global proxy (Clash, etc.) | Route `127.0.0.1`/`localhost`/RFC-1918 DIRECT in your proxy (don't disable it). |
| `Missing server/venv` during verify | Run `bun run setup`. |

## License

MIT
