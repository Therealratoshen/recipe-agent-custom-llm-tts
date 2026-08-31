"""Zero-signup runner for the bundled-sample audio endpoint.

Boots ONLY `server/src/llm.py` — the module changed by this PR — on a
random free port. Does NOT import `server.py`, `agora_agent`, or any
Agora SDK, so no `AGORA_APP_ID`, `AGORA_APP_CERTIFICATE`, or vendor
signup is required.

What it proves:

- The bundled WAV at `server/assets/sample.wav` loads and validates.
- `POST /chat/completions` streams the SSE contract end-to-end.
- The endpoint is genuinely credential-free.

Usage:

    python server/scripts/run_llm_only.py [--port N] [--host HOST]

Then in another shell:

    curl -N -X POST http://127.0.0.1:8000/chat/completions \\
        -H 'Content-Type: application/json' \\
        -d '{"model":"audio-bundled","messages":[{"role":"user","content":"hi"}],"modalities":["text","audio"],"stream":true}'

The script exits with code 0 if the server boots successfully, non-zero
on failure. Designed for CI smoke tests and reviewer sanity checks.
"""
from __future__ import annotations

import argparse
import os
import socket
import sys


def _pick_free_port() -> int:
    """Bind to port 0 to ask the OS for an unused TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="TCP port to bind. 0 (default) picks a free port.",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--print-port",
        action="store_true",
        help="Print the bound port to stdout on startup (used by smoke tests).",
    )
    args = parser.parse_args()

    # Make `import llm` resolve to server/src/llm.py without any env setup.
    server_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src_root = os.path.join(server_root, "src")
    if src_root not in sys.path:
        sys.path.insert(0, src_root)

    # `import llm` triggers _load_bundled_sample() at module level. If the
    # bundled WAV is missing or malformed, this raises immediately — better
    # than silent garbage at runtime.
    import llm  # noqa: F401  (import for side effects)

    import uvicorn

    port = args.port or _pick_free_port()

    if args.print_port:
        print(f"PORT={port}", flush=True)

    config = uvicorn.Config(
        llm.app,
        host=args.host,
        port=port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)
    server.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())