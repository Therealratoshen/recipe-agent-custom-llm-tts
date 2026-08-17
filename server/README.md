# Agora Agent Backend — Custom LLM-TTS Recipe

FastAPI service that owns Agora token generation and agent session lifecycle for
the custom-llm-tts recipe (port 8000). It also **mounts the custom audio endpoint at
`/audio`** (`src/llm.py`), so one process serves both the token/agent routes and
`POST /audio/chat/completions`. It is the service the web client reaches through the
Next.js `/api/*` rewrite proxy; because Agora cloud calls `/audio` directly, the
backend must be publicly reachable (e.g. `ngrok http 8000`), which makes the token
endpoints co-public and unauthenticated — add auth/rate-limiting before deployment.

## What's different from the base quickstart

The LLM stage uses `CustomLLM` with `output_modalities=["audio"]`, pointed at your
own endpoint (the `llm/` server). Agora cloud calls that endpoint and plays the
returned PCM audio directly over RTC — **no TTS is used**. STT (Deepgram) still
transcribes the user's speech into text for the LLM.

> `agent.py` still calls `.with_tts()` with an inert TTS vendor: the agora-agents
> builder requires one in cascading mode (in both 1.4 and 2.0), but with `["audio"]`
> output it has nothing to synthesize and is never used.

## Run

Use the repo-root `README.md` for the full local flow (`bun run dev`). To work on
this module directly:

The root commands below select the correct virtualenv interpreter on macOS,
Linux, and Windows, so activation is not required:

```shell
bun run setup:server
bun run backend
```

## Environment

`server/.env.example` is the template. Required:

- `AGORA_APP_ID`, `AGORA_APP_CERTIFICATE` — Agora project credentials.
- `CUSTOM_LLM_URL` — the **public** URL of the mounted `/audio` endpoint, ending in
  `/audio/chat/completions`. Agora cloud calls it, so it cannot be `localhost`.
- `CUSTOM_LLM_API_KEY` — forwarded by Agora cloud as `Authorization: Bearer`.
  Required by the `CustomLLM` vendor.

Optional: `CUSTOM_LLM_MODEL` (default `audio-mock`), `AGENT_GREETING`, `PORT`
(default `8000`).

## API

- `GET /get_config` — token + channel/UID config
- `POST /startAgent` — start an agent session
- `POST /stopAgent` — stop an agent session
- `POST /audio/chat/completions` — mounted custom audio endpoint (`src/llm.py`)
- `GET /audio/health` — mounted endpoint health check

`bun run verify:local:fastapi` exercises the token/agent routes through the Next proxy
with a fake agent; `bun run verify:local:llm` exercises the mounted `/audio` route.
`pytest tests` (run by `bun run verify:backend:pytest`) covers the mount and the
no-`agora-agents` boundary — no live Agora session required for any of them.
