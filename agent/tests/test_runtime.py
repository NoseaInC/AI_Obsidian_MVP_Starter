from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

from agent.api.server import serve
from agent.core.service import AgentService
from agent.core.storage import StateStore


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.vault = Path(self.temp.name) / "Vault"; self.vault.mkdir()

    def tearDown(self): self.temp.cleanup()

    def test_job_lifecycle_and_crash_recovery(self):
        store = StateStore(self.vault / "state.sqlite3")
        job = store.create_job("quiz", {"x": 1}); store.update_job(job, "running"); store.close()
        recovered = StateStore(self.vault / "state.sqlite3")
        self.assertEqual(recovered.recover_interrupted(), 1)
        self.assertEqual(recovered.get_job(job)["state"], "queued")
        recovered.close()

    def test_service_runs_restricted_jobs(self):
        service = AgentService(self.vault)
        job = service.enqueue("quiz", {"count": 2})
        service.run_job(job, {"quiz": lambda payload: {"questions": payload["count"]}})
        self.assertEqual(service.store.get_job(job)["state"], "completed")
        self.assertFalse(hasattr(service, "write_file"))
        with self.assertRaises(RuntimeError): service.enqueue("write-file", {})
        service.store.close()

    def test_worker_consumes_queue_and_records_failure(self):
        service = AgentService(self.vault)
        ok = service.enqueue("quiz", {})
        self.assertEqual(service.process_next({"quiz": lambda payload: {"ok": True}}), ok)
        self.assertEqual(service.store.get_job(ok)["state"], "completed")
        bad = service.enqueue("quiz", {})
        service.process_next({"quiz": lambda payload: (_ for _ in ()).throw(RuntimeError("boom"))})
        self.assertEqual(service.store.get_job(bad)["state"], "failed")
        service.store.close()

    def test_markdown_artifact_registry_is_rebuildable(self):
        folder = self.vault / "20-Knowledge/Concepts"; folder.mkdir(parents=True)
        path = folder / "x.md"; path.write_text('''---
type: concept
status: "ai-draft"
review_state: "pending"
artifact_role: "concept"
artifact_id: "source:concept:0"
---
# x
''', encoding="utf-8")
        service = AgentService(self.vault); service.sync_artifacts()
        row = service.store.connection.execute("SELECT artifact_id, path FROM artifacts").fetchone()
        self.assertEqual(row["artifact_id"], "source:concept:0")
        path.unlink(); service.sync_artifacts()
        self.assertEqual(service.store.connection.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0], 0)
        service.store.close()

    def test_server_refuses_non_local_bind(self):
        with self.assertRaises(RuntimeError): serve(self.vault, host="0.0.0.0", port=0)

    def test_health_http_api(self):
        server = serve(self.vault, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/health", timeout=2) as response:
                payload = json.loads(response.read())
            self.assertEqual(payload["status"], "ok")
        finally:
            server.shutdown(); server.server_close(); server.RequestHandlerClass.service.store.close(); thread.join(timeout=2)


if __name__ == "__main__": unittest.main()
