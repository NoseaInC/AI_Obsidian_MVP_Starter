from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from pathlib import Path
from unittest.mock import patch
from pydantic_ai.models.test import TestModel

from agent.api.server import Handler, serve
from agent.brain import BrainRequest
from agent.core.service import AgentService
from agent.core.storage import StateStore
from agent.core.models import FakeKeyStore


class _RuntimeChatProvider:
    def chat(self, model, messages, **options):
        return {"choices": [{"message": {"content": "离线假模型回答"}}]}

    def stream_chat(self, model, messages, **options):
        yield {"type": "delta", "content": "离线假模型回答"}
        yield {"type": "finish", "finishReason": "stop"}
        yield {"type": "done"}

    def structured_output(self, model, messages, schema, **options):
        return {"choices": [{"message": {"content": json.dumps({
            "action": "respond",
            "tool_name": "",
            "arguments": {},
            "purpose": "",
            "clarification": "",
        }, ensure_ascii=False)}}]}


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.vault = Path(self.temp.name) / "Vault"; self.vault.mkdir()

    def tearDown(self): self.temp.cleanup()

    def _serve_local(self, **kwargs):
        try:
            return serve(self.vault, **kwargs)
        except PermissionError:
            self.skipTest("当前沙箱禁止绑定 localhost；无套接字 API 契约测试继续执行")

    def test_url_encoded_identifiers_preserve_plus_sign(self):
        encoded = "20260713T000038%2B0800-938e97f334d2-d3e7d649"
        self.assertEqual(
            Handler._identifier(encoded),
            "20260713T000038+0800-938e97f334d2-d3e7d649",
        )

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

    def test_job_cancel_and_retry_are_explicit_state_transitions(self):
        service = AgentService(self.vault)
        cancelled = service.enqueue("quiz", {"count": 1})
        self.assertEqual(service.cancel_job(cancelled)["state"], "cancelled")
        retried = service.retry_job(cancelled)
        self.assertEqual(retried["state"], "queued")
        self.assertNotEqual(retried["job_id"], cancelled)
        with self.assertRaises(RuntimeError): service.retry_job(retried["job_id"])
        service.store.close()

    def test_delete_failed_job_removes_only_runtime_record(self):
        service = AgentService(self.vault)
        job_id = service.enqueue("quiz", {})
        service.process_next({"quiz": lambda payload: (_ for _ in ()).throw(RuntimeError("boom"))})
        marker = self.vault / "knowledge.md"; marker.write_text("keep", encoding="utf-8")
        result = service.delete_job(job_id)
        self.assertTrue(result["deleted"]); self.assertFalse(result["knowledge_files_deleted"])
        self.assertIsNone(service.store.get_job(job_id)); self.assertEqual(marker.read_text(), "keep")
        service.store.close()

    def test_prepare_job_remains_visible_awaiting_confirmation(self):
        service = AgentService(self.vault)
        job_id = service.enqueue("prepare-pdf", {"pdf": "/tmp/fake.pdf"})
        service.process_next({"prepare-pdf": lambda payload: {
            "bundle": str(self.vault / "90-Local-Only/Prepared-Bundles/prepared-123"),
            "prepared_id": "prepared-123",
        }})
        job = service.store.get_job(job_id)
        self.assertEqual(job["state"], "awaiting_confirmation")
        self.assertEqual(job["prepared_id"], "prepared-123")
        self.assertEqual(job["current_stage"], "awaiting_confirmation")
        self.assertEqual(job["progress"], 100)
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
        server = self._serve_local(port=0, runtime_id="runtime-test")
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/health", timeout=2) as response:
                payload = json.loads(response.read())
            self.assertEqual(payload["status"], "ok")
            self.assertIs(payload["ok"], True)
            self.assertEqual(payload["service"], "obsidian-learning-agent")
            self.assertEqual(payload["protocol_version"], 1)
            self.assertEqual(payload["runtime_id"], "runtime-test")
            self.assertIsInstance(payload["pid"], int)
        finally:
            server.shutdown(); server.server_close(); server.RequestHandlerClass.service.store.close(); thread.join(timeout=2)

    def test_cors_preflight(self):
        server = self._serve_local(port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/health", method="OPTIONS",
                headers={"Origin": "app://obsidian.md", "Access-Control-Request-Method": "GET"},
            )
            with urllib.request.urlopen(request, timeout=2) as response:
                self.assertEqual(response.status, 204)
                self.assertEqual(response.headers["Access-Control-Allow-Origin"], "app://obsidian.md")
                self.assertIn("OPTIONS", response.headers["Access-Control-Allow-Methods"])
                self.assertIn("Authorization", response.headers["Access-Control-Allow-Headers"])
                self.assertIn("Idempotency-Key", response.headers["Access-Control-Allow-Headers"])
                self.assertIn("X-Conversation-Id", response.headers["Access-Control-Allow-Headers"])
                self.assertIn("X-Attachment-Name", response.headers["Access-Control-Allow-Headers"])
        finally:
            server.shutdown(); server.server_close(); server.RequestHandlerClass.service.store.close(); thread.join(timeout=2)

    def test_v1_business_api_requires_session_token_and_returns_structured_error(self):
        server = self._serve_local(port=0, session_token="test-session-token")
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/api/v1/jobs"
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(url, timeout=2)
            payload = json.loads(caught.exception.read())
            self.assertEqual(payload["error"]["code"], "unauthorized")
            request = urllib.request.Request(url, headers={"Authorization": "Bearer test-session-token"})
            with urllib.request.urlopen(request, timeout=2) as response:
                self.assertEqual(json.loads(response.read()), {"jobs": []})
        finally:
            server.shutdown(); server.server_close(); server.RequestHandlerClass.service.store.close(); thread.join(timeout=2)

    def test_create_job_returns_visible_queued_job(self):
        server = self._serve_local(port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/api/v1/jobs", method="POST",
                headers={"Content-Type": "application/json"},
                data=json.dumps({"kind": "quiz", "payload": {}}).encode(),
            )
            with urllib.request.urlopen(request, timeout=2) as response: job = json.loads(response.read())["job"]
            self.assertEqual(job["state"], "queued")
            self.assertEqual(job["current_stage"], "queued")
            self.assertEqual(job["progress"], 0)
        finally:
            server.shutdown(); server.server_close(); server.RequestHandlerClass.service.store.close(); thread.join(timeout=2)

    def test_model_profile_api_crud_never_returns_key(self):
        server = self._serve_local(port=0, session_token="session", key_store=FakeKeyStore())
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}/api/v1/model-profiles"
            headers = {"Authorization": "Bearer session", "Content-Type": "application/json"}
            body = {"displayName": "测试", "providerType": "openai-compatible", "baseUrl": "http://localhost:9000/v1", "apiKey": "secret-api-key", "defaultModel": "fake", "settings": {"customHeaders": {}}}
            request = urllib.request.Request(base, method="POST", headers=headers, data=json.dumps(body).encode())
            with urllib.request.urlopen(request, timeout=2) as response: profile = json.loads(response.read())["profile"]
            self.assertTrue(profile["configured"]); self.assertNotIn("apiKey", profile)
            request = urllib.request.Request(base, headers={"Authorization": "Bearer session"})
            with urllib.request.urlopen(request, timeout=2) as response: payload = json.loads(response.read())
            self.assertNotIn("secret-api-key", json.dumps(payload))
            request = urllib.request.Request(f"{base}/{profile['id']}", method="DELETE", headers={"Authorization": "Bearer session"})
            with urllib.request.urlopen(request, timeout=2) as response: self.assertTrue(json.loads(response.read())["deleted"])
        finally:
            server.shutdown(); server.server_close(); server.RequestHandlerClass.service.store.close(); thread.join(timeout=2)

    def test_chat_http_api_uses_fake_provider_without_network(self):
        keys = FakeKeyStore(); server = self._serve_local(port=0, session_token="session", key_store=keys)
        service = server.RequestHandlerClass.service
        profile = service.save_model_profile({
            "displayName": "离线测试", "providerType": "openai-compatible",
            "baseUrl": "http://localhost:9000/v1", "apiKey": "fake-only",
            "defaultModel": "fake", "settings": {"customHeaders": {}},
        })
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/api/v1/chat", method="POST",
                headers={"Authorization": "Bearer session", "Content-Type": "application/json"},
                data=json.dumps({"profile_id": profile["id"], "messages": [{"role": "user", "content": "test"}]}).encode(),
            )
            with patch.object(service.models, "provider", return_value=_RuntimeChatProvider()):
                with urllib.request.urlopen(request, timeout=2) as response: payload = json.loads(response.read())
            self.assertEqual(payload["message"]["content"], "离线假模型回答")
            self.assertFalse(payload["streaming"])
        finally:
            server.shutdown(); server.server_close(); service.store.close(); thread.join(timeout=2)

    def test_assistant_ndjson_stream_is_real_versioned_persisted_and_authenticated(self):
        keys = FakeKeyStore(); server = self._serve_local(port=0, session_token="session", key_store=keys)
        service = server.RequestHandlerClass.service
        profile = service.save_model_profile({
            "displayName": "离线流模型", "providerType": "openai-compatible",
            "baseUrl": "http://localhost:9000/v1", "apiKey": "fake-only",
            "defaultModel": "fake-stream", "settings": {"customHeaders": {}},
        })
        service.set_model_routing({"assistant_chat": {"profileId": profile["id"], "modelOverride": "fake-stream"}})
        service.assistant_runtime.model_factory = lambda *_: TestModel(call_tools=[], custom_output_text="离线假模型回答")
        conversation = service.create_conversation({"title": "流式测试"})
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            body = json.dumps({"conversation_id": conversation["id"], "message": "你是哪一个模型"}, ensure_ascii=False).encode()
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/api/v1/assistant/stream", method="POST",
                headers={"Authorization": "Bearer session", "Content-Type": "application/json"}, data=body,
            )
            with urllib.request.urlopen(request, timeout=3) as response:
                self.assertEqual(response.headers.get_content_type(), "application/x-ndjson")
                events = [json.loads(line) for line in response if line.strip()]
            self.assertEqual([item["seq"] for item in events], list(range(1, len(events) + 1)))
            self.assertEqual(events[0]["type"], "run.started")
            self.assertIn("context.resolved", [item["type"] for item in events])
            self.assertIn("message.delta", [item["type"] for item in events])
            self.assertEqual(events[-1]["type"], "run.completed")
            persisted = service.get_conversation(conversation["id"])["messages"]
            self.assertEqual([item["role"] for item in persisted], ["user", "assistant"])
            self.assertEqual(persisted[-1]["content"], "离线假模型回答")
            self.assertEqual(service.list_agent_artifacts(conversation_id=conversation["id"])["items"], [])
        finally:
            server.shutdown(); server.server_close(); service.store.close(); thread.join(timeout=2)

    @unittest.skip("legacy schema-v2 coordinator API removed; schema-v3 coverage lives in test_pydantic_assistant_runtime")
    def test_assistant_v3_reconnect_resume_and_reject_http_api(self):
        server = self._serve_local(port=0, session_token="session", key_store=FakeKeyStore())
        service = server.RequestHandlerClass.service

        def pending_run(suffix: str) -> tuple[str, str, Path]:
            run_id = f"run-http-{suffix}"
            target = self.vault / f"01-Inbox/{suffix}.md"
            service.store.create_brain_run(run_id, BrainRequest(text=f"创建 {suffix}"))
            service.store.update_brain_run(run_id, "awaiting_confirmation")
            proposal = service.tools.call(
                "create_change_set",
                {
                    "run_id": run_id,
                    "title": f"HTTP {suffix}",
                    "writes": [
                        {
                            "path": f"01-Inbox/{suffix}.md",
                            "content": f"# {suffix}\n",
                            "category": "assistant-proposal",
                        }
                    ],
                },
                run_id=run_id,
                allowed_permissions=("proposal",),
            )
            service.store.save_agent_run_checkpoint(
                run_id,
                "awaiting_approval",
                {"awaitingApproval": True},
                pending_approval_id=proposal["id"],
            )
            service.store.append_agent_run_event(
                run_id,
                1,
                "run.awaiting_approval",
                {
                    "schemaVersion": 2,
                    "seq": 1,
                    "type": "run.awaiting_approval",
                    "runId": run_id,
                    "conversationId": "conversation-http",
                    "proposalId": proposal["id"],
                },
            )
            return run_id, proposal["id"], target

        resume_id, _, resume_target = pending_run("resume")
        reject_id, reject_proposal, reject_target = pending_run("reject")
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        base = f"http://127.0.0.1:{server.server_port}/api/v1/assistant/runs"
        headers = {"Authorization": "Bearer session", "Content-Type": "application/json"}
        try:
            request = urllib.request.Request(
                f"{base}/{resume_id}/events?after=0&limit=10",
                headers={"Authorization": "Bearer session"},
            )
            with urllib.request.urlopen(request, timeout=3) as response:
                events = json.loads(response.read())
            self.assertEqual(events["schemaVersion"], 2)
            self.assertEqual(events["events"][0]["type"], "run.awaiting_approval")

            request = urllib.request.Request(
                f"{base}/{resume_id}/resume",
                method="POST",
                headers=headers,
                data=json.dumps({"confirmed": True}).encode(),
            )
            with urllib.request.urlopen(request, timeout=3) as response:
                resumed = json.loads(response.read())
            self.assertEqual(resumed["run"]["status"], "completed")
            self.assertEqual(resume_target.read_text(encoding="utf-8"), "# resume\n")

            request = urllib.request.Request(
                f"{base}/{reject_id}/reject",
                method="POST",
                headers=headers,
                data=json.dumps({"reason": "用户拒绝"}, ensure_ascii=False).encode(),
            )
            with urllib.request.urlopen(request, timeout=3) as response:
                rejected = json.loads(response.read())
            self.assertEqual(rejected["run"]["status"], "cancelled")
            self.assertFalse(reject_target.exists())
            self.assertEqual(service.store.get_brain_change_set(reject_proposal)["state"], "rejected")
        finally:
            server.shutdown(); server.server_close(); service.store.close(); thread.join(timeout=2)

    @unittest.skip("frontend-close semantics replaced by explicit backend cancel")
    def test_closing_assistant_stream_persists_only_received_partial_text(self):
        keys = FakeKeyStore(); service = AgentService(self.vault, key_store=keys)
        profile = service.save_model_profile({
            "displayName": "离线流模型", "providerType": "openai-compatible",
            "baseUrl": "http://localhost:9000/v1", "apiKey": "fake-only",
            "defaultModel": "fake-stream", "settings": {"customHeaders": {}},
        })
        service.set_model_routing({"assistant_chat": {"profileId": profile["id"]}})
        service.assistant_runtime.model_factory = lambda *_: TestModel(call_tools=[], custom_output_text="离线假模型回答")
        conversation = service.create_conversation({"title": "取消测试"})
        stream = service.assistant_stream({"conversation_id": conversation["id"], "message": "开始回答"})
        with patch.object(service.models, "provider", return_value=_RuntimeChatProvider()):
            for item in stream:
                if item["type"] == "message.delta": break
            stream.close()
        messages = service.get_conversation(conversation["id"])["messages"]
        self.assertEqual(messages[-1]["messageType"], "partial")
        self.assertEqual(messages[-1]["content"], "离线假模型回答")
        service.store.close()

    def test_assistant_regeneration_reuses_user_turn_without_duplicate_user_message(self):
        keys = FakeKeyStore(); service = AgentService(self.vault, key_store=keys)
        profile = service.save_model_profile({
            "displayName": "离线流模型", "providerType": "openai-compatible",
            "baseUrl": "http://localhost:9000/v1", "apiKey": "fake-only",
            "defaultModel": "fake-stream", "settings": {"customHeaders": {}},
        })
        service.set_model_routing({"assistant_chat": {"profileId": profile["id"]}})
        service.assistant_runtime.model_factory = lambda *_: TestModel(call_tools=[], custom_output_text="离线假模型回答")
        conversation = service.create_conversation({"title": "重新生成测试"})
        list(service.assistant_stream({"conversation_id": conversation["id"], "message": "解释 Delta Method"}))
        first_messages = service.get_conversation(conversation["id"])["messages"]
        user_message_id = first_messages[0]["id"]
        events = list(service.assistant_stream({
            "conversation_id": conversation["id"], "message": "不得替换原问题",
            "regenerate_message_id": user_message_id,
        }))
        messages = service.get_conversation(conversation["id"])["messages"]
        self.assertEqual([item["role"] for item in messages], ["user", "assistant", "assistant"])
        self.assertEqual(messages[0]["content"], "解释 Delta Method")
        self.assertEqual(events[0]["type"], "run.started")
        service.store.close()

    def test_daily_contract_is_authenticated_versioned_and_idempotent(self):
        server = self._serve_local(port=0, session_token="session", key_store=FakeKeyStore())
        service = server.RequestHandlerClass.service
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}/api/v1"
            headers = {"Authorization": "Bearer session", "Content-Type": "application/json", "Idempotency-Key": "event-batch-1"}
            event = {"id": "event-api-1", "eventType": "recommendation_clicked", "subjectType": "recommendation", "subjectId": "rec-1", "payload": {"category": "review"}, "createdAt": "2026-07-14T09:00:00+08:00", "schemaVersion": 1}
            request = urllib.request.Request(f"{base}/learning/events", method="POST", headers=headers, data=json.dumps({"events": [event], "schemaVersion": 1}).encode())
            with urllib.request.urlopen(request, timeout=2) as response: first = json.loads(response.read())
            request = urllib.request.Request(f"{base}/learning/events", method="POST", headers=headers, data=json.dumps({"events": [event], "schemaVersion": 1}).encode())
            with urllib.request.urlopen(request, timeout=2) as response: duplicate = json.loads(response.read())
            self.assertEqual(first["inserted"], 1); self.assertEqual(duplicate["duplicates"], 1)
            for path in ("/daily/dashboard", "/learning/profile"):
                request = urllib.request.Request(base + path, headers={"Authorization": "Bearer session"})
                with urllib.request.urlopen(request, timeout=2) as response: payload = json.loads(response.read())
                self.assertEqual(payload.get("schemaVersion") or payload["profile"]["schemaVersion"], 1)
            request = urllib.request.Request(f"{base}/learning/events", method="POST", headers=headers, data=json.dumps({"events": [{**event, "id": "event-api-2", "payload": {"prompt": "private"}}], "schemaVersion": 1}).encode())
            with self.assertRaises(urllib.error.HTTPError) as caught: urllib.request.urlopen(request, timeout=2)
            self.assertEqual(json.loads(caught.exception.read())["error"]["code"], "invalid_request")
        finally:
            server.shutdown(); server.server_close(); service.store.close(); thread.join(timeout=2)

    def test_brain_api_is_authenticated_idempotent_auditable_and_confirmed(self):
        server = self._serve_local(port=0, session_token="session", key_store=FakeKeyStore())
        service = server.RequestHandlerClass.service
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}/api/v1"
            headers = {"Authorization": "Bearer session", "Content-Type": "application/json", "Idempotency-Key": "capture-once"}
            body = json.dumps({"text": "记录灵感：测试 Brain API", "mode": "capture"}, ensure_ascii=False).encode()
            request = urllib.request.Request(f"{base}/brain/requests", method="POST", headers=headers, data=body)
            with urllib.request.urlopen(request, timeout=3) as response: first = json.loads(response.read())["run"]
            request = urllib.request.Request(f"{base}/brain/requests", method="POST", headers=headers, data=body)
            with urllib.request.urlopen(request, timeout=3) as response: second = json.loads(response.read())["run"]
            self.assertEqual(first["id"], second["id"]); self.assertEqual(first["status"], "awaiting_confirmation")
            request = urllib.request.Request(f"{base}/brain/runs/{first['id']}/events", headers={"Authorization": "Bearer session"})
            with urllib.request.urlopen(request, timeout=3) as response: events = json.loads(response.read())
            self.assertEqual(len(events["steps"]), 1); self.assertEqual(len(events["tool_events"]), 2)
            change_set_id = first["result"]["results"][0]["change_set"]["id"]
            request = urllib.request.Request(
                f"{base}/brain-change-sets/{change_set_id}/apply", method="POST", headers=headers,
                data=json.dumps({"confirmed": True}).encode(),
            )
            with urllib.request.urlopen(request, timeout=3) as response: applied = json.loads(response.read())["change_set"]
            self.assertEqual(applied["state"], "applied")
            self.assertEqual(service.get_brain_run(first["id"])["status"], "completed")
            database = service.store.path.read_bytes(); self.assertNotIn("测试 Brain API".encode(), database)
        finally:
            server.shutdown(); server.server_close(); service.store.close(); thread.join(timeout=2)


if __name__ == "__main__": unittest.main()
