from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from agent.core.service import AgentService


class Handler(BaseHTTPRequestHandler):
    service: AgentService

    def _cors(self) -> None:
        # The service is localhost-only and uses no cookies or credentials.
        # A wildcard allows Obsidian's Electron origins (app://, file:// / null)
        # without granting network access beyond the loopback-bound server.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "600")

    def _send(self, status: int, payload: object) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status); self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data))); self._cors(); self.end_headers(); self.wfile.write(data)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0")); raw = self.rfile.read(length) if length else b"{}"
        value = json.loads(raw); return value if isinstance(value, dict) else {}

    def do_GET(self) -> None:
        try:
            path = urlparse(self.path).path
            if path == "/health": payload = self.service.health()
            elif path == "/jobs": payload = {"jobs": self.service.list_jobs()}
            elif path == "/prepared": payload = {"bundles": self.service.list_prepared()}
            elif path == "/reviews": payload = {"artifacts": self.service.list_reviews()}
            elif path == "/learning/today": payload = self.service.today_learning()
            elif path.startswith("/prepared/"): payload = {"preview": self.service.inspect_prepared(path.removeprefix("/prepared/"))}
            elif path.startswith("/reviews/") and path.endswith("/diff"):
                payload = {"diff": self.service.diff_review(path.removeprefix("/reviews/").removesuffix("/diff"))}
            elif path.startswith("/reviews/"): payload = {"content": self.service.show_review(path.removeprefix("/reviews/"))}
            else: self._send(404, {"error": "not found"}); return
            self._send(200, payload)
        except Exception as exc: self._send(409, {"error": f"{type(exc).__name__}: {exc}"})

    def do_POST(self) -> None:
        try:
            path, body = urlparse(self.path).path, self._body()
            if path == "/jobs": payload = {"job_id": self.service.enqueue(str(body.get("kind", "")), dict(body.get("payload", {})))}
            elif path == "/prepared/apply": payload = {"result": self.service.apply_prepared(str(body["prepared_id"]))}
            elif path == "/review/transition": payload = {"result": self.service.transition_review(str(body["artifact_id"]), str(body["action"]), str(body.get("reason", "")))}
            elif path == "/learning/mastery/suggest": payload = {"mastery": self.service.suggest_mastery(int(body["current"]), float(body["correctness"]), bool(body.get("critical_error", False)))}
            elif path == "/learning/mastery/confirm": payload = {"result": self.service.confirm_mastery(str(body["path"]), int(body["mastery"]), list(body.get("weak_points", [])))}
            else: self._send(404, {"error": "not found"}); return
            self._send(200, payload)
        except Exception as exc: self._send(409, {"error": f"{type(exc).__name__}: {exc}"})

    def log_message(self, format: str, *args: object) -> None: return


def serve(vault: Path, host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    if host not in {"127.0.0.1", "localhost", "::1"}: raise RuntimeError("Agent API may only bind to localhost")
    service = AgentService(vault)
    handler = type("BoundHandler", (Handler,), {"service": service})
    return ThreadingHTTPServer((host, port), handler)


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--vault", required=True); parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(); server = serve(Path(args.vault), port=args.port)
    stop = threading.Event(); service = server.RequestHandlerClass.service
    def worker() -> None:
        while not stop.wait(.5): service.process_next()
    worker_thread = threading.Thread(target=worker, name="agent-job-worker", daemon=True); worker_thread.start()
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: stop.set(); worker_thread.join(timeout=5); server.shutdown(); server.server_close(); service.store.close()


if __name__ == "__main__": main()
