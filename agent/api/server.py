from __future__ import annotations

import argparse
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from agent.core.service import AgentService
from agent.core.models import KeyStore
from agent.core.redaction import redact
from agent.brain.errors import BrainError


class Handler(BaseHTTPRequestHandler):
    service: AgentService
    session_token: str | None = None
    allowed_origins = {"app://obsidian.md", "capacitor://localhost", "http://localhost"}

    def _cors(self) -> None:
        origin = self.headers.get("Origin")
        if origin in self.allowed_origins:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
        self.send_header(
            "Access-Control-Allow-Headers",
            "Authorization, Content-Type, Idempotency-Key, X-Correlation-ID, "
            "X-Conversation-Id, X-Conversation-Title, X-Attachment-Name, X-Attachment-Kind",
        )
        self.send_header("Access-Control-Max-Age", "600")

    def _send(self, status: int, payload: object) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status); self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data))); self._cors(); self.end_headers(); self.wfile.write(data)

    def _send_ndjson(self, events: object) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "close")
        self._cors(); self.end_headers()
        iterator = iter(events)
        try:
            for item in iterator:
                line = (json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
                self.wfile.write(line); self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            close = getattr(iterator, "close", None)
            if close: close()
        except Exception as exc:
            safe = redact(str(exc))
            failure = {
                "schemaVersion": 3, "seq": 1, "type": "run.failed",
                "runId": "assistant-run-error", "conversationId": "",
                "code": type(exc).__name__,
                "message": safe if isinstance(safe, str) else "助手流启动失败",
                "partial": False,
            }
            try:
                self.wfile.write((json.dumps(failure, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
        finally:
            self.close_connection = True

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0")); raw = self.rfile.read(length) if length else b"{}"
        value = json.loads(raw); return value if isinstance(value, dict) else {}

    @staticmethod
    def _route(path: str) -> str:
        return path.removeprefix("/api/v1") if path.startswith("/api/v1/") else path

    @staticmethod
    def _identifier(value: str) -> str:
        """Decode a path identifier while preserving a literal plus sign."""
        return unquote(value)

    def _authorized(self, path: str) -> bool:
        if path == "/health" or not self.session_token: return True
        return self.headers.get("Authorization") == f"Bearer {self.session_token}"

    def _error(self, status: int, code: str, message: str, *, retryable: bool = False, suggested_action: str = "", correlation_id: str = "", details: dict | None = None) -> None:
        self._send(status, {"ok": False, "error": {"code": code, "message": message, "human_message": message, "retryable": retryable, "suggested_action": suggested_action, "correlation_id": correlation_id, "technical_details": details or {}}})

    def _exception(self, exc: Exception) -> None:
        if isinstance(exc, BrainError):
            status = 400 if exc.code in {"brain_invalid_request", "invalid_path", "invalid_url"} else 403 if exc.code in {"brain_policy_denied", "protected_note"} else 409
            self._error(status, exc.code, exc.human_message, retryable=exc.retryable, suggested_action=exc.suggested_action, correlation_id=exc.correlation_id, details=exc.technical_details)
            return
        safe = redact(str(exc))
        message = safe if isinstance(safe, str) else "请求处理失败；请查看脱敏诊断"
        if isinstance(exc, (ValueError, TypeError, KeyError, json.JSONDecodeError)):
            self._error(400, "invalid_request", message); return
        if isinstance(exc, FileNotFoundError):
            self._error(404, "not_found", message); return
        self._error(409, type(exc).__name__, message)

    def do_GET(self) -> None:
        try:
            parsed = urlparse(self.path); path = self._route(parsed.path); query = parse_qs(parsed.query)
            if not self._authorized(path): self._error(401, "unauthorized", "Missing or invalid session token"); return
            if path == "/health": payload = self.service.health()
            elif path == "/dashboard": payload = self.service.dashboard()
            elif path == "/daily/dashboard": payload = self.service.daily_dashboard()
            elif path == "/daily/plan": payload = {"plan": self.service.build_today()}
            elif path == "/learning/directions": payload = {"directions": self.service.learning_directions()}
            elif path == "/recommendations": payload = {"recommendations": self.service.list_recommendations()}
            elif path.startswith("/recommendations/"): payload = {"recommendation": self.service.get_recommendation(self._identifier(path.removeprefix("/recommendations/")))}
            elif path.startswith("/study-sessions/"): payload = self.service.get_study_session(self._identifier(path.removeprefix("/study-sessions/")))
            elif path == "/jobs": payload = {"jobs": self.service.list_jobs()}
            elif path.startswith("/jobs/"): payload = {"job": self.service.get_job(self._identifier(path.removeprefix("/jobs/")))}
            elif path == "/prepared": payload = {"bundles": self.service.list_prepared()}
            elif path == "/reviews": payload = {"artifacts": self.service.list_reviews()}
            elif path == "/learning/today": payload = self.service.today_learning()
            elif path == "/learning/profile": payload = {"profile": self.service.learner_profile()}
            elif path == "/learning/events": payload = {"items": self.service.store.list_learning_events(int(query.get("limit", [200])[0]), str(query.get("since", [""])[0])), "schemaVersion": 1}
            elif path == "/plans/current": payload = self.service.current_plan()
            elif path == "/model-profiles": payload = {"profiles": self.service.list_model_profiles()}
            elif path == "/model-routing": payload = {"routes": self.service.model_routing()}
            elif path == "/conversations": payload = self.service.list_conversations(int(query.get("limit", [50])[0]), int(query.get("offset", [0])[0]))
            elif path.startswith("/conversations/"): payload = {"conversation": self.service.get_conversation(self._identifier(path.removeprefix("/conversations/")))}
            elif path.startswith("/conversation-focus/"): payload = {"focus": self.service.conversation_focus(self._identifier(path.removeprefix("/conversation-focus/")))}
            elif path.startswith("/material-bundles/"): payload = {"bundle": self.service.material_bundle(self._identifier(path.removeprefix("/material-bundles/")))}
            elif path.startswith("/organization-plans/"): payload = {"plan": self.service.organization_plan(self._identifier(path.removeprefix("/organization-plans/")))}
            elif path == "/assistant/context":
                payload = self.service.assistant_context(
                    str(query.get("artifact_id", [""])[0]), str(query.get("conversation_id", [""])[0])
                )
            elif path.startswith("/assistant/runs/") and path.endswith("/events"):
                payload = self.service.assistant_run_events(
                    self._identifier(path.removeprefix("/assistant/runs/").removesuffix("/events")),
                    int(query.get("after", [0])[0]),
                )
            elif path.startswith("/intake/attachments/"): payload = {"attachment": self.service.get_attachment(self._identifier(path.removeprefix("/intake/attachments/")))}
            elif path == "/artifacts": payload = self.service.list_agent_artifacts(str(query.get("type", [""])[0]), str(query.get("status", [""])[0]), str(query.get("conversation_id", [""])[0]), int(query.get("limit", [100])[0]), int(query.get("offset", [0])[0]))
            elif path.startswith("/artifacts/"): payload = {"artifact": self.service.get_agent_artifact(self._identifier(path.removeprefix("/artifacts/")))}
            elif path == "/materials": payload = self.service.list_materials(str(query.get("status", [""])[0]), int(query.get("limit", [100])[0]), int(query.get("offset", [0])[0]))
            elif path.startswith("/materials/"): payload = {"material": self.service.get_material(self._identifier(path.removeprefix("/materials/")))}
            elif path == "/brain/runs":
                payload = self.service.list_brain_runs(limit=int(query.get("limit", [50])[0]), offset=int(query.get("offset", [0])[0]), status=str(query.get("status", [""])[0]))
            elif path.startswith("/brain/runs/") and path.endswith("/events"):
                payload = self.service.brain_events(
                    self._identifier(path.removeprefix("/brain/runs/").removesuffix("/events")),
                    int(query.get("after", [0])[0]),
                )
            elif path.startswith("/brain/runs/"):
                payload = {"run": self.service.get_brain_run(self._identifier(path.removeprefix("/brain/runs/")))}
            elif path == "/brain/capabilities": payload = self.service.brain_capabilities()
            elif path == "/brain/health": payload = self.service.brain_health()
            elif path == "/brain/diagnostics": payload = self.service.brain_diagnostics()
            elif path == "/research-bundles": payload = self.service.list_research_bundles(int(query.get("limit", [50])[0]), int(query.get("offset", [0])[0]))
            elif path == "/web/sources": payload = {"sources": self.service.store.list_web_sources(int(query.get("limit", [100])[0]))}
            elif path == "/web/capabilities": payload = self.service.web_capabilities()
            elif path == "/autonomy": payload = self.service.autonomy_status()
            elif path == "/agent-actions": payload = {"actions": self.service.store.list_agent_actions(int(query.get("limit", [100])[0]))}
            elif path.startswith("/agent-actions/"): payload = {"action": self.service.store.get_agent_action(self._identifier(path.removeprefix("/agent-actions/")))}
            elif path.startswith("/research-bundles/"): payload = {"bundle": self.service.get_research_bundle(self._identifier(path.removeprefix("/research-bundles/")))}
            elif path == "/curriculum/candidates": payload = self.service.list_curriculum_candidates(str(query.get("status", ["active"])[0]))
            elif path.startswith("/change-sets/") and path.endswith("/diff"):
                payload = {"diff": self.service.diff_change_set(self._identifier(path.removeprefix("/change-sets/").removesuffix("/diff")))}
            elif path.startswith("/change-sets/"):
                payload = {"change_set": self.service.get_change_set(self._identifier(path.removeprefix("/change-sets/")))}
            elif path.startswith("/brain-change-sets/"): payload = {"change_set": self.service.get_brain_change_set(self._identifier(path.removeprefix("/brain-change-sets/")))}
            elif path.startswith("/prepared/"): payload = {"preview": self.service.inspect_prepared(self._identifier(path.removeprefix("/prepared/")))}
            elif path.startswith("/reviews/") and path.endswith("/diff"):
                payload = {"diff": self.service.diff_review(self._identifier(path.removeprefix("/reviews/").removesuffix("/diff")))}
            elif path.startswith("/reviews/"): payload = {"content": self.service.show_review(self._identifier(path.removeprefix("/reviews/")))}
            else: self._error(404, "not_found", "API route not found"); return
            self._send(200, payload)
        except Exception as exc: self._exception(exc)

    def do_POST(self) -> None:
        try:
            path = self._route(urlparse(self.path).path)
            if not self._authorized(path): self._error(401, "unauthorized", "Missing or invalid session token"); return
            if path == "/intake/attachments":
                content_type = str(self.headers.get("Content-Type", "application/octet-stream"))
                if content_type.split(";", 1)[0].strip().lower() == "application/json":
                    body = self._body(); payload = {"attachment": self.service.create_attachment(body)}
                else:
                    length = int(self.headers.get("Content-Length", "0"))
                    if length <= 0 or length > 84 * 1024 * 1024:
                        self._error(413, "attachment_size_invalid", "附件大小必须在 1B–80MB"); return
                    raw = self.rfile.read(length)
                    metadata = {
                        "conversation_id": self._identifier(str(self.headers.get("X-Conversation-Id", ""))),
                        "conversation_title": self._identifier(str(self.headers.get("X-Conversation-Title", "新会话"))),
                        "display_name": self._identifier(str(self.headers.get("X-Attachment-Name", "attachment"))),
                        "kind": str(self.headers.get("X-Attachment-Kind", "")),
                    }
                    payload = {"attachment": self.service.create_attachment(metadata, raw, content_type)}
                self._send(200, payload); return
            body = self._body()
            if path == "/assistant/stream":
                self._send_ndjson(self.service.assistant_stream(body)); return
            if path.startswith("/assistant/runs/") and path.endswith("/confirm"):
                run_id = self._identifier(
                    path.removeprefix("/assistant/runs/").removesuffix("/confirm")
                )
                self._send_ndjson(
                    self.service.confirm_assistant_run(
                        run_id,
                        body.get("confirmed") is True,
                        answer=str(body.get("answer") or ""),
                        scope=str(body.get("scope") or ""),
                    )
                )
                return
            if path.startswith("/assistant/runs/") and path.endswith("/cancel"):
                run_id = self._identifier(
                    path.removeprefix("/assistant/runs/").removesuffix("/cancel")
                )
                self._send(200, self.service.cancel_assistant_run(run_id))
                return
            if path.startswith("/assistant/runs/") and path.endswith("/compact"):
                run_id = self._identifier(
                    path.removeprefix("/assistant/runs/").removesuffix("/compact")
                )
                self._send(200, self.service.compact_assistant_run(run_id))
                return
            if path.startswith("/assistant/runs/") and path.endswith("/fork"):
                run_id = self._identifier(
                    path.removeprefix("/assistant/runs/").removesuffix("/fork")
                )
                sequence = body.get("sequence")
                self._send(200, self.service.fork_assistant_run(run_id, int(sequence) if sequence is not None else None))
                return
            if path == "/intake/submit":
                payload = self.service.submit_intake(body, self.headers.get("Idempotency-Key", ""))
            elif path == "/conversations":
                payload = {"conversation": self.service.create_conversation(body)}
            elif path.startswith("/conversations/") and path.endswith("/export"):
                conversation_id = self._identifier(path.removeprefix("/conversations/").removesuffix("/export"))
                payload = self.service.export_conversation(conversation_id)
            elif path.startswith("/conversations/") and path.endswith("/retain-summary"):
                conversation_id = self._identifier(path.removeprefix("/conversations/").removesuffix("/retain-summary"))
                payload = self.service.retain_conversation_summary(conversation_id, body.get("confirmed") is True)
            elif path.startswith("/conversations/") and path.endswith("/messages"):
                conversation_id = self._identifier(path.removeprefix("/conversations/").removesuffix("/messages"))
                payload = self.service.submit_intake({**body, "conversation_id": conversation_id}, self.headers.get("Idempotency-Key", ""))
            elif path == "/jobs":
                job_id = self.service.enqueue(str(body.get("kind", "")), dict(body.get("payload", {})))
                payload = {"job": self.service.get_job(job_id)}
            elif path.startswith("/jobs/") and path.endswith("/cancel"):
                payload = {"job": self.service.cancel_job(self._identifier(path.removeprefix("/jobs/").removesuffix("/cancel")))}
            elif path.startswith("/jobs/") and path.endswith("/retry"):
                payload = {"job": self.service.retry_job(self._identifier(path.removeprefix("/jobs/").removesuffix("/retry")))}
            elif path.startswith("/jobs/") and path.endswith("/delete"):
                payload = self.service.delete_job(self._identifier(path.removeprefix("/jobs/").removesuffix("/delete")))
            elif path.startswith("/recommendations/") and path.endswith("/action"):
                recommendation_id = self._identifier(path.removeprefix("/recommendations/").removesuffix("/action"))
                payload = self.service.recommendation_action(recommendation_id, str(body["action"]), dict(body.get("details", {})))
            elif path == "/study-sessions":
                payload = self.service.start_study_session(str(body["recommendation_id"]))
            elif path == "/learning/events":
                payload = self.service.record_learning_events(body)
            elif path == "/learning/profile/rebuild":
                payload = {"profile": self.service.learner_profile(rebuild=True)}
            elif path == "/learning/directions/refresh":
                payload = {"directions": self.service.refresh_learning_directions()}
            elif path == "/daily/build":
                payload = {"plan": self.service.build_today(body.get("budget_minutes"), force=body.get("force") is True)}
            elif path == "/daily/adjust":
                payload = self.service.adjust_today(body)
            elif path.startswith("/daily/adjustments/") and path.endswith("/undo"):
                payload = self.service.undo_today_adjustment(self._identifier(path.removeprefix("/daily/adjustments/").removesuffix("/undo")))
            elif path.startswith("/study-sessions/") and path.endswith("/complete"):
                session_id = self._identifier(path.removeprefix("/study-sessions/").removesuffix("/complete"))
                payload = self.service.complete_study_session(session_id, float(body.get("correctness", 0)), str(body.get("notes", "")))
            elif path == "/prepared/apply": payload = {"result": self.service.apply_prepared(str(body["prepared_id"]))}
            elif path == "/review/transition": payload = {"result": self.service.transition_review(str(body["artifact_id"]), str(body["action"]), str(body.get("reason", "")))}
            elif path == "/learning/mastery/suggest": payload = {"mastery": self.service.suggest_mastery(int(body["current"]), float(body["correctness"]), bool(body.get("critical_error", False)))}
            elif path == "/learning/mastery/confirm": payload = {"result": self.service.confirm_mastery(str(body["path"]), int(body["mastery"]), list(body.get("weak_points", [])))}
            elif path == "/model-profiles": payload = {"profile": self.service.save_model_profile(body)}
            elif path.startswith("/model-profiles/") and path.endswith("/test"):
                payload = self.service.test_model_profile(self._identifier(path.removeprefix("/model-profiles/").removesuffix("/test")))
            elif path.startswith("/model-profiles/") and path.endswith("/models"):
                payload = {"models": self.service.list_profile_models(self._identifier(path.removeprefix("/model-profiles/").removesuffix("/models")))}
            elif path == "/chat": payload = self.service.chat(body)
            elif path == "/chat/stream": payload = self.service.chat(body, stream=True)
            elif path == "/integrations/today/add": payload = self.service.add_artifact_to_today(str(body["artifact_id"]))
            elif path == "/integrations/today/undo": payload = self.service.undo_artifact_today(str(body["artifact_id"]))
            elif path == "/brain/requests": payload = {"run": self.service.submit_brain(body, self.headers.get("Idempotency-Key", ""))}
            elif path == "/brain/capture": payload = {"run": self.service.submit_brain(body, self.headers.get("Idempotency-Key", ""), mode="capture")}
            elif path == "/brain/organize": payload = {"run": self.service.submit_brain(body, self.headers.get("Idempotency-Key", ""), mode="organize")}
            elif path == "/brain/research": payload = {"run": self.service.submit_brain(body, self.headers.get("Idempotency-Key", ""), mode="research")}
            elif path == "/web/search": payload = self.service.search_public_web(str(body.get("query") or ""), int(body.get("limit", 8)))
            elif path == "/web/academic": payload = self.service.search_academic_web(str(body.get("query") or ""), int(body.get("limit", 8)))
            elif path == "/web/fetch": payload = {"source": self.service.fetch_public_web(str(body.get("url") or ""))}
            elif path == "/web/research": payload = self.service.research_public_web(str(body.get("query") or ""), list(body.get("urls") or []) or None, int(body.get("limit", 5)))
            elif path == "/vault/changes": payload = self.service.apply_autonomous_vault_change(body)
            elif path.startswith("/agent-actions/") and path.endswith("/undo"):
                payload = self.service.undo_autonomous_vault_change(self._identifier(path.removeprefix("/agent-actions/").removesuffix("/undo")))
            elif path == "/brain/tutor": payload = {"run": self.service.submit_brain(body, self.headers.get("Idempotency-Key", ""), mode="tutor")}
            elif path.startswith("/brain/runs/") and path.endswith("/resume"):
                payload = self.service.resume_assistant_run(
                    self._identifier(path.removeprefix("/brain/runs/").removesuffix("/resume")),
                    body.get("confirmed") is True,
                )
            elif path.startswith("/brain/runs/") and path.endswith("/reject"):
                payload = self.service.reject_assistant_run(
                    self._identifier(path.removeprefix("/brain/runs/").removesuffix("/reject")),
                    str(body.get("reason") or ""),
                )
            elif path.startswith("/brain/runs/") and path.endswith("/cancel"):
                payload = {"run": self.service.cancel_brain_run(self._identifier(path.removeprefix("/brain/runs/").removesuffix("/cancel")))}
            elif path.startswith("/brain/runs/") and path.endswith("/retry"):
                payload = {"run": self.service.retry_brain_run(self._identifier(path.removeprefix("/brain/runs/").removesuffix("/retry")))}
            elif path.startswith("/research-bundles/") and path.endswith("/add-to-plan"):
                payload = {"proposal": self.service.add_research_to_plan(self._identifier(path.removeprefix("/research-bundles/").removesuffix("/add-to-plan")))}
            elif path.startswith("/research-bundles/") and path.endswith("/save"):
                payload = self.service.save_research_bundle(self._identifier(path.removeprefix("/research-bundles/").removesuffix("/save")))
            elif path == "/curriculum/refresh": payload = {"run": self.service.refresh_curriculum(body)}
            elif path.startswith("/plans/proposals/") and path.endswith("/confirm"):
                payload = {"proposal": self.service.confirm_plan_proposal(self._identifier(path.removeprefix("/plans/proposals/").removesuffix("/confirm")))}
            elif path.startswith("/curriculum/candidates/") and path.endswith("/action"):
                payload = self.service.curriculum_candidate_action(self._identifier(path.removeprefix("/curriculum/candidates/").removesuffix("/action")), str(body.get("action", "")), body.get("cooldown_until"))
            elif path.startswith("/brain-change-sets/") and path.endswith("/apply"):
                payload = {"change_set": self.service.apply_brain_change_set(self._identifier(path.removeprefix("/brain-change-sets/").removesuffix("/apply")), bool(body.get("confirmed", False)))}
            elif path.startswith("/artifacts/") and path.endswith("/revise"):
                payload = {"artifact": self.service.revise_agent_artifact(self._identifier(path.removeprefix("/artifacts/").removesuffix("/revise")), body)}
            elif path.startswith("/artifacts/") and path.endswith("/action"):
                payload = {"artifact": self.service.agent_artifact_action(self._identifier(path.removeprefix("/artifacts/").removesuffix("/action")), body)}
            else: self._error(404, "not_found", "API route not found"); return
            self._send(200, payload)
        except json.JSONDecodeError: self._error(400, "invalid_json", "Request body must be valid JSON")
        except Exception as exc: self._exception(exc)

    def do_PATCH(self) -> None:
        try:
            path, body = self._route(urlparse(self.path).path), self._body()
            if not self._authorized(path): self._error(401, "unauthorized", "Missing or invalid session token"); return
            if path.startswith("/model-profiles/"):
                payload = {"profile": self.service.save_model_profile(body, self._identifier(path.removeprefix("/model-profiles/")))}
            elif path == "/model-routing": payload = {"routes": self.service.set_model_routing(dict(body.get("routes", {})))}
            elif path == "/autonomy": payload = self.service.set_autonomy_mode(body)
            elif path.startswith("/plans/tasks/"):
                payload = {"task": self.service.patch_plan_task(self._identifier(path.removeprefix("/plans/tasks/")), body)}
            elif path.startswith("/conversations/") and path.endswith("/preferences"):
                conversation_id = self._identifier(path.removeprefix("/conversations/").removesuffix("/preferences"))
                payload = {"conversation": self.service.update_conversation_preferences(conversation_id, body)}
            elif path.startswith("/study-sessions/"):
                session_id = self._identifier(path.removeprefix("/study-sessions/"))
                payload = self.service.update_study_session(session_id, str(body.get("action") or "progress"), body)
            else: self._error(404, "not_found", "API route not found"); return
            self._send(200, payload)
        except json.JSONDecodeError: self._error(400, "invalid_json", "Request body must be valid JSON")
        except Exception as exc: self._exception(exc)

    def do_DELETE(self) -> None:
        try:
            parsed = urlparse(self.path); path = self._route(parsed.path); query = parse_qs(parsed.query)
            if not self._authorized(path): self._error(401, "unauthorized", "Missing or invalid session token"); return
            if path.startswith("/model-profiles/"):
                payload = self.service.delete_model_profile(self._identifier(path.removeprefix("/model-profiles/")))
            elif path.startswith("/intake/attachments/"):
                payload = self.service.delete_attachment(self._identifier(path.removeprefix("/intake/attachments/")))
            elif path == "/learning/events":
                payload = self.service.clear_learning_data(str(query.get("scope", [""])[0]))
            elif path == "/conversations":
                payload = self.service.clear_conversations(
                    str(query.get("scope", [""])[0]), str(query.get("confirm", ["false"])[0]).casefold() == "true",
                )
            elif path.startswith("/conversations/"):
                payload = self.service.delete_conversation(
                    self._identifier(path.removeprefix("/conversations/")),
                    str(query.get("confirm", ["false"])[0]).casefold() == "true",
                )
            else: self._error(404, "not_found", "API route not found"); return
            self._send(200, payload)
        except Exception as exc: self._exception(exc)

    def log_message(self, format: str, *args: object) -> None: return


def serve(vault: Path, host: str = "127.0.0.1", port: int = 8765, session_token: str | None = None, runtime_id: str = "", key_store: KeyStore | None = None) -> ThreadingHTTPServer:
    if host not in {"127.0.0.1", "localhost", "::1"}: raise RuntimeError("Agent API may only bind to localhost")
    service = AgentService(vault, runtime_id=runtime_id, key_store=key_store)
    handler = type("BoundHandler", (Handler,), {"service": service, "session_token": session_token})
    try:
        return ThreadingHTTPServer((host, port), handler)
    except Exception:
        service.store.close()
        raise


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--vault", required=True); parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(); server = serve(
        Path(args.vault), port=args.port,
        session_token=os.environ.get("OBSIDIAN_AGENT_SESSION_TOKEN"),
        runtime_id=os.environ.get("OBSIDIAN_AGENT_RUNTIME_ID", ""),
    )
    stop = threading.Event(); service = server.RequestHandlerClass.service
    def worker() -> None:
        while not stop.wait(.5): service.process_next()
    worker_thread = threading.Thread(target=worker, name="agent-job-worker", daemon=True); worker_thread.start()
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: stop.set(); worker_thread.join(timeout=5); server.shutdown(); server.server_close(); service.store.close()


if __name__ == "__main__": main()
