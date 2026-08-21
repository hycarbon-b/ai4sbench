from __future__ import annotations

import json
import re
import secrets
import shutil
import subprocess
import threading
from collections import deque
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

QUICK_TUNNEL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def extract_quick_tunnel_url(line: str) -> str | None:
    match = QUICK_TUNNEL_RE.search(line.lower())
    return match.group(0) if match else None


class DebugState:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.token = secrets.token_urlsafe(24)
        self.status = "starting"
        self.lines: deque[str] = deque(maxlen=100)
        self._lock = threading.Lock()

    def update(self, line: str) -> None:
        with self._lock:
            self.lines.append(line[-1000:])

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {"run_id": self.run_id, "status": self.status, "recent_logs": list(self.lines)}


def start_debug_server(state: DebugState) -> tuple[ThreadingHTTPServer, str]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            if urlparse(self.path).path != f"/debug/{state.token}":
                self.send_error(404)
                return
            body = json.dumps(state.snapshot()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}"


def start_quick_tunnel(
    target: str, state: DebugState, on_url: Callable[[str], None]
) -> subprocess.Popen[str] | None:
    binary = shutil.which("cloudflared")
    if binary is None:
        return None
    process = subprocess.Popen(
        [binary, "tunnel", "--no-autoupdate", "--url", target],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    def read_output() -> None:
        if process.stdout is None:
            return
        announced = False
        for line in process.stdout:
            if not announced and (url := extract_quick_tunnel_url(line)):
                announced = True
                on_url(f"{url}/debug/{state.token}")

    threading.Thread(target=read_output, daemon=True).start()
    return process
