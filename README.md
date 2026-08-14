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
