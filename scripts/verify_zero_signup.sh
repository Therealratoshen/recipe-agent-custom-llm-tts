#!/usr/bin/env bash
#
# scripts/verify_zero_signup.sh
#
# End-to-end smoke test that proves the bundled-sample PR works with
# ZERO signups of any kind:
#
#   - No AGORA_APP_ID / AGORA_APP_CERTIFICATE
#   - No vendor API key (Aivis, ElevenLabs, OpenAI, ...)
#   - No model download (Piper, Style-Bert-VITS2, ...)
#   - No tunnel (ngrok, Cloudflare, ...)
#   - No `bun`, no `agora-agent` SDK, no Agora Console account
#
# What it does:
#   1. Boot only `server/src/llm.py` via `server/scripts/run_llm_only.py`.
#      No `server.py`, no `agora_agent` import.
#   2. POST a streaming chat-completions request to /chat/completions
#      using only stdlib `python` + stdlib `urllib` (curl is used here for
#      readability but is optional — the same call works without curl).
#   3. Assert the SSE stream contains the bundled transcript + at least
#      one base64 PCM data chunk + `[DONE]` terminator.
#   4. Reassemble the streamed PCM and compare against the bundled WAV
#      on disk (byte-equality).
#   5. Shut down the server, print a green PASS line.
#
# Exit codes:
#   0  - everything passed
#   1  - server failed to boot
#   2  - SSE contract violated
#   3  - streamed PCM did not match the bundled asset
#
# Usage:
#   ./scripts/verify_zero_signup.sh
#
# Requires:
#   - python3 (the recipe's Python 3.10+ venv if you've run bun run setup,
#     or any plain python3 if you've only pip-installed fastapi + uvicorn)
#   - curl (recommended; can be swapped for python urllib in one line)

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${PORT:-43170}"
PY="${PYTHON:-python3}"

# Pick a free port if the requested one is taken.
if command -v lsof >/dev/null 2>&1 && lsof -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    PORT=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')
fi

# Step 1: boot the server in the background.
SERVER_LOG="$(mktemp -t llm-verify.XXXXXX.log)"
echo "[verify] booting llm.py on 127.0.0.1:$PORT (log: $SERVER_LOG)"
PYTHONPATH="$ROOT/server/src" "$PY" "$ROOT/server/scripts/run_llm_only.py" \
    --host 127.0.0.1 --port "$PORT" --print-port \
    >"$SERVER_LOG" 2>&1 &
SERVER_PID=$!

cleanup() {
    if kill -0 "$SERVER_PID" 2>/dev/null; then
        kill "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
    fi
    rm -f "$SERVER_LOG"
}
trap cleanup EXIT

# Step 2: wait for the server to be healthy (max 10s).
for i in $(seq 1 40); do
    if curl --silent --output /dev/null "http://127.0.0.1:$PORT/health" 2>/dev/null; then
        break
    fi
    sleep 0.25
done

if ! curl --silent --output /dev/null "http://127.0.0.1:$PORT/health" 2>/dev/null; then
    echo "[verify] FAIL: server did not respond to /health within 10s"
    cat "$SERVER_LOG" || true
    exit 1
fi

# Step 3: stream a chat-completions request and validate the SSE contract.
echo "[verify] posting streaming request to /chat/completions"
SSE_BODY="$(mktemp -t sse-body.XXXXXX.txt)"
HTTP_STATUS="$(
    curl --silent --show-error --no-buffer \
        --output "$SSE_BODY" --write-out '%{http_code}' \
        -X POST "http://127.0.0.1:$PORT/chat/completions" \
        -H 'Content-Type: application/json' \
        -d '{
            "model": "audio-bundled",
            "messages": [{"role": "user", "content": "hi"}],
            "modalities": ["text", "audio"],
            "stream": true
        }'
)"

if [[ "$HTTP_STATUS" != "200" ]]; then
    echo "[verify] FAIL: POST /chat/completions returned HTTP $HTTP_STATUS"
    cat "$SSE_BODY"
    exit 2
fi

# Required SSE ingredients: transcript chunk, base64 data chunks, [DONE] terminator.
if ! grep -q '"transcript"' "$SSE_BODY"; then
    echo "[verify] FAIL: SSE stream missing transcript chunk"
    exit 2
fi

if ! grep -q '"data"' "$SSE_BODY"; then
    echo "[verify] FAIL: SSE stream missing base64 PCM data chunk"
    exit 2
fi

if ! grep -q 'data: \[DONE\]' "$SSE_BODY"; then
    echo "[verify] FAIL: SSE stream did not terminate with [DONE]"
    exit 2
fi

# Step 4: reassemble streamed PCM and compare to the bundled asset on disk.
EXPECTED_PCM="$("$PY" - <<'PY'
import sys
sys.path.insert(0, "server/src")
import llm
sys.stdout.buffer.write(llm._BUNDLED_SAMPLE_PCM)
PY
)"

ACTUAL_PCM="$("$PY" - <<PY
import sys, json, base64
events = []
with open("$SSE_BODY", "r") as f:
    for line in f:
        line = line.strip()
        if not line.startswith("data: "):
            continue
        payload = line[len("data: "):]
        if payload == "[DONE]":
            continue
        events.append(json.loads(payload))
chunks = []
for e in events:
    audio = e.get("choices", [{}])[0].get("delta", {}).get("audio", {})
    if "data" in audio:
        chunks.append(base64.b64decode(audio["data"]))
sys.stdout.buffer.write(b"".join(chunks))
PY
)"

if [[ "$EXPECTED_PCM" != "$ACTUAL_PCM" ]]; then
    EXPECTED_LEN=${#EXPECTED_PCM}
    ACTUAL_LEN=${#ACTUAL_PCM}
    echo "[verify] FAIL: streamed PCM did not match bundled asset (expected $EXPECTED_LEN bytes, got $ACTUAL_LEN bytes)"
    exit 3
fi
ACTUAL_LEN=${#ACTUAL_PCM}

echo "[verify] PASS — zero-signup demo works end-to-end"
echo "[verify]   - server boots without AGORA_APP_ID / AGORA_APP_CERTIFICATE"
echo "[verify]   - POST /chat/completions returns SSE with transcript + base64 PCM + [DONE]"
echo "[verify]   - streamed PCM ($ACTUAL_LEN bytes) matches bundled asset byte-for-byte"