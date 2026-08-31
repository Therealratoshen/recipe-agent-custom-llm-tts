"""Zero-signup end-to-end test.

Boots the bundled-sample endpoint over a real TCP socket (no in-process
TestClient) and exercises it with `urllib` from the standard library.

This test is the canonical "I never signed up for anything, will this
PR's demo work?" gate for the maintainer. It does not require:
- AGORA_APP_ID / AGORA_APP_CERTIFICATE
- any vendor API key
- any network egress

If you can run `pip install fastapi uvicorn pytest` and
`pytest server/tests/test_zero_signup_e2e.py` and see green, the
bundled-sample integration is verified.
"""
from __future__ import annotations

import base64
import contextlib
import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from typing import Iterator

import pytest


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.contextmanager
def _boot_server() -> Iterator[tuple[str, int]]:
    """Boot `server.src.llm:app` on a free port without any env setup.

    Runs `server/scripts/run_llm_only.py --print-port` in a thread and
    waits until the printed port comes up.
    """
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    script = os.path.join(repo_root, "server", "scripts", "run_llm_only.py")
    server_src = os.path.join(repo_root, "server", "src")

    env = os.environ.copy()
    env.pop("AGORA_APP_ID", None)
    env.pop("AGORA_APP_CERTIFICATE", None)
    env.pop("OPENAI_API_KEY", None)
    env.pop("AIVIS_API_KEY", None)
    env["PYTHONPATH"] = server_src

    port = _free_port()

    proc_state = {"port": None, "exc": None}

    def _reader(stream, sink: list[str]) -> None:
        for line in stream:
            sink.append(line)
            if line.startswith("PORT="):
                with contextlib.suppress(ValueError):
                    proc_state["port"] = int(line.strip().split("=", 1)[1])

    import subprocess  # local import keeps test collection snappy

    proc = subprocess.Popen(
        [sys.executable, script, "--host", "127.0.0.1", "--port", str(port), "--print-port"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=repo_root,
    )
    t = threading.Thread(target=_reader, args=(proc.stdout, []), daemon=True)
    t.start()

    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline and proc_state["port"] is None:
        if proc.poll() is not None:
            raise RuntimeError(f"server exited early: code={proc.returncode}")
        time.sleep(0.05)

    if proc_state["port"] is None:
        proc.terminate()
        raise RuntimeError("server did not announce a port within 10s")

    # Wait for /health to come up.
    url = f"http://127.0.0.1:{proc_state['port']}"
    for _ in range(40):
        try:
            with urllib.request.urlopen(f"{url}/health", timeout=1.0) as r:
                if r.status == 200:
                    break
        except (urllib.error.URLError, ConnectionError):
            time.sleep(0.1)
    else:
        proc.terminate()
        raise RuntimeError("/health never returned 200")

    try:
        yield url, proc_state["port"]
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_zero_signup_streaming_end_to_end() -> None:
    """Stream a chat-completions request, validate SSE, verify PCM equality."""
    # We need to import llm just to know the expected bundled PCM. This
    # works because `import llm` does not touch agora_agent at all.
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    server_src = os.path.join(repo_root, "server", "src")
    sys.path.insert(0, server_src)
    import llm  # noqa: E402

    expected_pcm = llm._BUNDLED_SAMPLE_PCM
    assert expected_pcm, "bundled sample must be loaded"

    body = json.dumps({
        "model": "audio-bundled",
        "messages": [{"role": "user", "content": "hi"}],
        "modalities": ["text", "audio"],
        "stream": True,
    }).encode("utf-8")

    with _boot_server() as (base_url, _port):
        req = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            assert resp.status == 200
            chunks: list[bytes] = []
            transcript_seen = False
            done_seen = False
            for raw in resp:
                line = raw.decode("utf-8", errors="replace").rstrip("\n")
                if not line.startswith("data: "):
                    continue
                payload = line[len("data: "):]
                if payload == "[DONE]":
                    done_seen = True
                    continue
                event = json.loads(payload)
                if '"transcript"' in line:
                    transcript_seen = True
                delta_audio = (
                    event.get("choices", [{}])[0]
                    .get("delta", {})
                    .get("audio", {})
                )
                if "data" in delta_audio:
                    chunks.append(base64.b64decode(delta_audio["data"]))

            assert transcript_seen, "SSE stream must include a transcript chunk"
            assert done_seen, "SSE stream must terminate with [DONE]"
            assert chunks, "SSE stream must include at least one audio data chunk"

            actual_pcm = b"".join(chunks)
            assert actual_pcm == expected_pcm, (
                f"streamed PCM must match bundled asset byte-for-byte "
                f"(expected {len(expected_pcm)} bytes, got {len(actual_pcm)} bytes)"
            )


def test_health_endpoint_does_not_require_agora_credentials() -> None:
    """`/health` must respond 200 with zero Agora env vars set."""
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    server_src = os.path.join(repo_root, "server", "src")
    sys.path.insert(0, server_src)
    import llm  # noqa: E402

    from fastapi.testclient import TestClient

    # TestClient uses ASGI in-process — no socket, no real subprocess.
    # This proves the contribution's code path is credential-free at the
    # FastAPI app level too, not just at the TCP level.
    assert "AGORA_APP_ID" not in os.environ
    assert "AGORA_APP_CERTIFICATE" not in os.environ

    with TestClient(llm.app) as client:
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"