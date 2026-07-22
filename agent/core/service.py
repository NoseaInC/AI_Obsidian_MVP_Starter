from __future__ import annotations

import json
import sys
import argparse
import hashlib
import os
import re
import uuid
from datetime import date, timedelta
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "00-System/Scripts"
if str(SCRIPTS) not in sys.path: sys.path.insert(0, str(SCRIPTS))

import prepared_pdf
import ingest_pdf
import review
from agent.core import learning
from agent.core import recommendations
from agent.core import learning_directions
from agent.core.models import KeyStore, ModelProfileService
from agent.core.pi_model_proxy import PiModelProxy
from agent.core.task_authorization import TaskAuthorizationService
from agent.core.reversible_transaction import ReversibleTransactionService
from agent.core.developer_workspace import DeveloperWorkspace
from agent.core.redaction import redact, redact_secret_text
from agent.core.storage import StateStore
from agent.core.intake import IntakeService
from agent.core.assistant_outcomes import AssistantOutcome, resolve_assistant_outcome
from agent.core.conversation_intelligence import build_conversation_summary, extract_knowledge_signals
from agent.core.web_research import WebResearchService
from agent.core.vault_autonomy import VaultAutonomyService, render_managed_block
from agent.core.context_material import ContextMaterialCoordinator
from agent.core import study_workspace
from agent.brain import BrainRequest
from agent.brain.errors import BrainError
from agent.brain.schemas import IntentResult
from agent.brain.model_gateway import BrainModelGateway
from agent.core.structured_workflow import StructuredWorkflowRunner
from agent.skills import build_skill_registry
from agent.tools import build_tool_registry
from agent.tools.change_set import ChangeSetTools
from agent.tools.vault_access import read_model_visible_note, safe_read_note


class AgentService:
    """Restricted business API; deliberately has no generic write-file method."""

    def __init__(self, vault: Path, store: StateStore | None = None, runtime_id: str = "", key_store: KeyStore | None = None) -> None:
        self.vault = vault.resolve()
        self.runtime_id = runtime_id
        self.store = store or StateStore(self.vault / "90-Local-Only/Agent/agent.sqlite3")
        self.intake = IntakeService(self.vault, self.store)
        self.models = ModelProfileService(self.store, key_store)
        self.model_proxy = PiModelProxy(self.models, self.store)
        self.web = WebResearchService(self.vault, self.store)
        self.autonomy = VaultAutonomyService(self.vault, self.store)
        self.context_material = ContextMaterialCoordinator(self.vault, self.store, self.intake, self.autonomy, self.apply_autonomous_vault_change)
        self.model_gateway = BrainModelGateway(self.models)
        self.tools = build_tool_registry(
            self.vault,
            self.store,
            intake=self.intake,
            allow_network=False,
        )
        self.skills = build_skill_registry(self.vault, self.store, self.tools, self.list_prepared, self.model_gateway)
        self.workflows = StructuredWorkflowRunner(self.vault, self.store, self.skills)
        self.brain_change_sets = ChangeSetTools(self.vault, self.store)
        self.task_authorizations = TaskAuthorizationService(
            self.vault, self.store, self.autonomy.classify,
        )
        self.reversible_transactions = ReversibleTransactionService(
            self.vault, self.store, self.task_authorizations,
        )
        self.developer_workspace = DeveloperWorkspace(self.vault, self.store)
        self.store.recover_interrupted()
        self.log_path = self.vault / "90-Local-Only/Agent/logs/events.jsonl"
        self.sync_indexes()

    def log(self, event: str, details: dict[str, Any]) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        safe_details = redact(details)
        record = {"at": datetime.now().astimezone().isoformat(timespec="seconds"), "event": event, "details": safe_details}
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        self.store.audit(event, str(details.get("job_id") or details.get("artifact_id") or details.get("prepared_id") or details.get("run_id") or ""), safe_details)

    def health(self) -> dict[str, Any]:
        self.sync_indexes()
        return {
            "ok": True,
            "status": "ok",
            "service": "obsidian-learning-agent",
            "protocol_version": 1,
            "vault": str(self.vault),
            "jobs": len(self.store.list_jobs()),
            "pid": os.getpid(),
            "runtime_id": self.runtime_id,
            "workflow_version": self.workflows.version,
            "assistant_runtime_version": "pi-agent-runtime/1",
            "model_proxy_version": self.model_proxy.version,
            "schema_version": self.store.schema_version(),
        }

    def submit_workflow(self, body: dict[str, Any], idempotency_key: str = "", *, mode: str | None = None) -> dict[str, Any]:
        request_body = dict(body)
        if mode:
            request_body["mode"] = mode
        request = BrainRequest.from_dict(request_body, idempotency_key=idempotency_key)
        result = self.workflows.submit(request)
        self.log("workflow.submitted", {"run_id": result["id"], "correlation_id": result["correlation_id"], "workflow": result.get("primary_intent"), "status": result["status"]})
        return result

    def create_conversation(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.intake.create_conversation(str(body.get("title") or "新会话"))

    def list_conversations(self, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        return self.intake.list_conversations(limit, offset)

    def get_conversation(self, conversation_id: str) -> dict[str, Any]:
        conversation = self.intake.get_conversation(conversation_id)
        return {
            **conversation,
            "latestTaskThread": self.intake.latest_task_thread(conversation_id),
            "latestArtifactGroup": self.intake.latest_artifact_group(conversation_id),
            "summary": self.store.latest_conversation_summary(conversation_id),
            "knowledgeSignals": self.store.list_conversation_signals(conversation_id, 50),
            "focus": self.store.get_conversation_focus(conversation_id),
        }

    def update_conversation_preferences(self, conversation_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return self.intake.update_conversation_preferences(
            conversation_id,
            personalization_enabled=body.get("personalization_enabled") if "personalization_enabled" in body else None,
            retention_policy=str(body["retention_policy"]) if body.get("retention_policy") is not None else None,
        )

    def export_conversation(self, conversation_id: str) -> dict[str, Any]:
        result = self.intake.export_conversation(conversation_id, {
            "summary": self.store.latest_conversation_summary(conversation_id),
            "knowledgeSignals": self.store.list_conversation_signals(conversation_id, 100),
            "artifacts": self.intake.list_artifacts(conversation_id=conversation_id, limit=200).get("items", []),
        })
        self.log("conversation.exported", {"conversation_id": conversation_id, "size_bytes": result["sizeBytes"]})
        return result

    def retain_conversation_summary(self, conversation_id: str, confirmed: bool) -> dict[str, Any]:
        if not confirmed:
            raise ValueError("explicit_confirmation_required")
        result = self.intake.retain_summary_only(conversation_id)
        self.log("conversation.summary-only", {"conversation_id": conversation_id, "removed_messages": result["removedMessages"]})
        return result

    def delete_conversation(self, conversation_id: str, confirmed: bool) -> dict[str, Any]:
        if not confirmed:
            raise ValueError("explicit_confirmation_required")
        result = self.intake.delete_conversation(conversation_id)
        self.log("conversation.deleted", {"conversation_id": conversation_id, "removed_files": result["removedFiles"]})
        return result

    def clear_conversations(self, scope: str, confirmed: bool) -> dict[str, Any]:
        if not confirmed:
            raise ValueError("explicit_confirmation_required")
        result = self.intake.clear_conversations(scope)
        self.log("conversations.cleared", {"scope": scope, "deleted_count": result["deletedCount"]})
        return result

    def create_attachment(self, body: dict[str, Any], raw: bytes | None = None, content_type: str = "") -> dict[str, Any]:
        conversation_id = self.intake.ensure_conversation(str(body.get("conversation_id") or "") or None, str(body.get("conversation_title") or "新会话"))
        if raw is not None:
            return self.intake.store_binary_attachment(
                conversation_id, str(body.get("display_name") or "attachment"),
                content_type or str(body.get("mime_type") or "application/octet-stream"), raw,
                str(body.get("kind") or ""),
            )
        if body.get("path"):
            return self.intake.reference_local_path(
                conversation_id, str(body["path"]),
                explicit_user_selection=body.get("explicit_user_selection") is True,
                confirmed_large_folder=body.get("confirmed_large_folder") is True,
            )
        if body.get("url"):
            if body.get("allow_network") is not True:
                raise ValueError("network_permission_required")
            source = self.fetch_public_web(str(body["url"]))
            return self.intake.store_url_content(
                conversation_id, source["canonicalUrl"], self.web.cached_text(source), "text/plain",
            )
        raise ValueError("attachment_body_required")

    def get_attachment(self, attachment_id: str) -> dict[str, Any]:
        return self.intake.get_attachment(attachment_id)

    def delete_attachment(self, attachment_id: str) -> dict[str, Any]:
        attachment = self.intake.get_attachment(attachment_id)
        result = self.intake.delete_attachment(attachment_id)
        conversation_id = str(attachment.get("conversationId") or "")
        focus = self.store.get_conversation_focus(conversation_id) if conversation_id else None
        if focus and attachment_id in list(focus.get("activeAttachmentIds") or []):
            focus["activeAttachmentIds"] = [item for item in focus.get("activeAttachmentIds", []) if item != attachment_id]
            active_material = focus.get("activeMaterial") or {}
            if attachment_id in list(active_material.get("sourceAttachmentIds") or []):
                focus["activeMaterial"] = None
            self.store.save_conversation_focus(conversation_id, focus)
        self.log("attachment.deleted", {"attachment_id": attachment_id})
        return result

    def submit_intake(self, body: dict[str, Any], idempotency_key: str = "") -> dict[str, Any]:
        message = str(body.get("message") or "").strip()
        if not message or len(message) > 250_000:
            raise ValueError("message must contain 1–250000 characters")
        if idempotency_key:
            existing_run_id = self.store.find_brain_run_by_idempotency(idempotency_key)
            if existing_run_id:
                existing_request = self.store.load_brain_request(existing_run_id)
                existing_metadata = existing_request.get("metadata") or {}
                existing_conversation = str(existing_metadata.get("conversation_id") or body.get("conversation_id") or "")
                artifacts = self.intake.list_artifacts(conversation_id=existing_conversation, limit=200)["items"] if existing_conversation else []
                artifacts = [self.intake.get_artifact(item["id"]) for item in artifacts if item.get("sourceRunId") == existing_run_id]
                bundle_id = str(existing_metadata.get("material_bundle_id") or "")
                material_bundle = self.store.get_material_bundle(bundle_id) if bundle_id else None
                organization = {}
                if bundle_id:
                    with self.store.lock:
                        row = self.store.connection.execute(
                            "SELECT id FROM organization_plans WHERE bundle_id=? ORDER BY updated_at DESC LIMIT 1", (bundle_id,),
                        ).fetchone()
                    if row:
                        plan = self.store.get_organization_plan(str(row["id"]))
                        organization = {"plan": plan, "results": list(plan.get("executionResults") or []), "status": plan.get("status")}
                return {
                    "conversation": self.intake.get_conversation(existing_conversation) if existing_conversation else None,
                    "run": self.get_brain_run(existing_run_id), "artifacts": artifacts,
                    "task_thread": self.intake.latest_task_thread(existing_conversation) if existing_conversation else None,
                    "artifact_group": self.intake.latest_artifact_group(existing_conversation) if existing_conversation else None,
                    "outcome": dict(existing_metadata.get("assistant_outcome") or {}),
                    "assistantIntent": dict(existing_metadata.get("assistant_intent") or {}),
                    "focus": self.store.get_conversation_focus(existing_conversation) if existing_conversation else None,
                    "materialBundle": material_bundle, "organization": organization,
                    "intake_id": str(existing_metadata.get("intake_id") or ""), "idempotent": True,
                }
        conversation_id = self.intake.ensure_conversation(
            str(body.get("conversation_id") or "") or None,
            self._conversation_title(message),
        )
        message_row = self.intake.append_message(conversation_id, "user", message)
        attachment_ids = [str(item.get("attachment_id") if isinstance(item, dict) else item) for item in body.get("attachments", [])]
        attachments = []
        for attachment_id in attachment_ids:
            attachment = self.intake.get_attachment(attachment_id)
            if attachment["conversationId"] != conversation_id:
                raise ValueError("attachment_conversation_mismatch")
            attachments.append(attachment)
        references = list(body.get("references", []))[:50]
        intake_id = self.intake.create_intake_item(conversation_id, message_row["id"], attachment_ids, references)
        conversation = self.intake.get_conversation(conversation_id, include_messages=False)
        context_material = self.context_material.prepare(conversation_id, message_row, attachments, body)
        resolved_message = str(context_material["resolvedMessage"])
        precise_intent = context_material["intent"]
        outcome = resolve_assistant_outcome(
            intent=precise_intent,
            mode=str(body.get("mode") or "auto"),
            has_attachments=bool(attachments),
            personalization_enabled=bool(conversation.get("personalizationEnabled", True)),
        )
        if precise_intent.get("needsTargetClarification"):
            return self._complete_local_intake_answer(
                conversation_id, intake_id, message_row["id"], context_material,
                "我还没有找到可继承的上一轮写入提案。请先指定目标笔记，或先让我生成整理方案；我不会把“写入”两个字当成笔记正文。",
                "write_target_required",
            )
        if precise_intent.get("writeStatusQuery"):
            return self._complete_local_intake_answer(
                conversation_id, intake_id, message_row["id"], context_material,
                self._latest_write_status(conversation_id),
                "write_status",
            )
        task_thread: dict[str, Any] | None = None
        active_artifact_id = str(body.get("active_artifact_id") or conversation.get("activeArtifactId") or "")
        if active_artifact_id and not attachments and body.get("artifact_revision") is True:
            task_thread = self.intake.create_task_thread(conversation_id, message_row["id"], "更新当前成果")
            try:
                artifact = self.intake.revise_artifact(
                    active_artifact_id, message, dict(body.get("artifact_patch", {})),
                    int(body["expected_version"]) if body.get("expected_version") is not None else None,
                )
                request = BrainRequest(text=message, source="artifact-revision", idempotency_key=idempotency_key)
                run_id = f"run-{uuid.uuid4().hex}"
                self.store.create_brain_run(run_id, request)
                self.store.set_brain_intent(run_id, IntentResult("continue_artifact_revision", basis="active-artifact"))
                self.store.finish_brain_run(run_id, "completed", {"results": [{"kind": "artifact-revision", "artifact": artifact}]})
                self.intake.update_intake_item(intake_id, "completed", 100, run_id)
                artifact_group = self.intake.create_artifact_group(conversation_id, task_thread["id"], artifact["title"], [artifact], "artifact-revision")
                task_thread = self.intake.update_task_thread(
                    task_thread["id"], title="更新当前成果", intent="continue_artifact_revision",
                    status="completed", progress=100,
                    artifact_group_id=artifact_group["id"] if artifact_group else None,
                )
                self.intake.append_message(
                    conversation_id, "assistant", f"已按你的要求更新同一成果，当前为第 {artifact['version']} 版。",
                    "artifact", task_thread["id"], artifact_group["id"] if artifact_group else None,
                )
                self.log("artifact.revised", {"artifact_id": active_artifact_id, "run_id": run_id, "version": artifact["version"]})
                return {"conversation": self.intake.get_conversation(conversation_id), "run": self.get_brain_run(run_id), "artifacts": [artifact], "task_thread": task_thread, "artifact_group": artifact_group, "outcome": AssistantOutcome("create_artifact", "explicit-artifact-revision", True).as_dict(), "intake_id": intake_id}
            except Exception as error:
                self.intake.update_intake_item(intake_id, "failed", 100, error_code=type(error).__name__)
                raise
        if outcome.creates_task_thread:
            task_thread = self.intake.create_task_thread(
                conversation_id, message_row["id"], self._assistant_task_title("unknown", message),
            )
        self.intake.update_intake_item(intake_id, "running", 15)
        active_note = body.get("active_note") if isinstance(body.get("active_note"), dict) else {}
        options = body.get("options") if isinstance(body.get("options"), dict) else {}
        request_body = {
            "text": resolved_message,
            "mode": str(body.get("mode") or "auto"),
            "source": "unified-intake",
            "active_note": str(active_note.get("path") or ""),
            "selected_text": str(active_note.get("selection") or ""),
            "time_budget_minutes": options.get("available_minutes"),
            "metadata": {
                "conversation_id": conversation_id,
                "intake_id": intake_id,
                "attachments": attachments,
                "references": references,
                "allow_network": options.get("allow_network") is True,
                "assistant_outcome": outcome.as_dict(),
                "conversation_focus": context_material["focus"],
                "assistant_intent": precise_intent,
                "material_bundle_id": context_material["materialBundle"]["id"],
            },
        }
        if attachments and request_body["mode"] == "auto":
            request_body["mode"] = "material"
        try:
            if precise_intent.get("inheritedProposal"):
                request_body["mode"] = "capture"
            explicit_mode = str(request_body.get("mode") or "auto").casefold()
            if explicit_mode == "auto":
                explicit_mode = "material" if attachments else "capture" if outcome.kind == "propose_write" else "organize" if outcome.kind == "create_artifact" else "tutor"
            request_body["mode"] = explicit_mode
            run = self.submit_workflow(request_body, idempotency_key)
            self.intake.update_intake_item(intake_id, run["status"], 100, run["id"], run.get("error_code"))
            artifacts = self._artifacts_for_run(conversation_id, run, message_row["id"], attachments, message, outcome.kind)
            # A terse confirmation is already materialized by capture_text as
            # one immutable Change Set.  Running the organization layer again
            # would create a duplicate proposal or, in high-autonomy mode,
            # incorrectly apply it before the user has reviewed the Diff.
            organization_result = {} if precise_intent.get("inheritedProposal") else self.context_material.organize(context_material)
            if organization_result:
                plan = organization_result.get("plan") or {}
                plan_artifact = self.intake.create_artifact(
                    "organization_plan", f"整理方案：{context_material['materialBundle']['title']}",
                    "completed" if organization_result.get("status") == "applied" else "awaiting_confirmation",
                    conversation_id, run["id"], {
                        "kind": "organization-plan", "planId": plan.get("planId"),
                        "intent": precise_intent["name"], "primaryAction": plan.get("primaryAction"),
                        "summary": plan.get("explanation"), "confidence": plan.get("confidence"),
                        "requiresConfirmation": plan.get("requiresConfirmation"),
                        "actions": plan.get("actions", []), "risks": plan.get("risks", []),
                    },
                )
                artifacts.append(plan_artifact)
                applied = next((item for item in organization_result.get("results", []) if item.get("status") == "applied"), None)
                if applied:
                    artifacts.append(self.intake.create_artifact(
                        "write_result", f"已整理《{context_material['materialBundle']['title']}》", "completed",
                        conversation_id, run["id"], {
                            "kind": "write-result", "path": applied.get("path"), "actionId": applied.get("actionId"),
                            "undoAvailable": applied.get("undoAvailable", False), "summary": "已完成低风险草稿写入，并创建快照、Diff 与撤销记录。",
                        },
                    ))
                proposal = next((item for item in organization_result.get("results", []) if item.get("changeSet")), None)
                if proposal:
                    artifacts.append(self.intake.create_artifact(
                        "update_suggestion", f"更新建议：{Path(str(proposal.get('path') or '正式知识')).stem}", "awaiting_confirmation",
                        conversation_id, run["id"], {
                            "kind": "update-suggestion", "path": proposal.get("proposalPath"),
                            "targetPath": proposal.get("path"), "changeSetId": proposal.get("changeSet", {}).get("id"),
                            "summary": "目标是 reviewed/core 或受保护来源；原文未修改，已生成带来源的更新建议。",
                        },
                    ))
            for attachment in attachments:
                source_type = {
                    "pdf": "PDF", "url": "公开网页", "conversation": "AI 对话",
                    "folder": "授权文件夹", "local_path": "本地文件", "text": "用户文本",
                }.get(str(attachment["kind"]), "本地材料")
                payload = {
                    "kind": "material", "sourceType": source_type, "attachmentId": attachment["id"],
                    "mimeType": attachment.get("mimeType", ""), "contentHash": attachment.get("sha256", ""),
                    "originalContent": "保留在 90-Local-Only 的受控附件引用中",
                    "summary": "材料已进入受控整理流程；原始指令保存在本地会话文件。",
                }
                status = "ready"
                if attachment["kind"] == "pdf":
                    pdf_path = self.intake.resolve_attachment_path(attachment["id"])
                    job_id = self.enqueue("prepare-pdf", {"pdf": str(pdf_path), "kind": "paper", "source_type": "assistant-attachment"})
                    payload.update({"jobId": job_id, "progress": 0})
                    status = "processing"
                material = self.intake.create_artifact(
                    "material", attachment["displayName"], status, conversation_id, run["id"], payload,
                )
                artifacts.insert(0, material)
                if attachment["kind"] == "url" and not any(item["type"] == "research_bundle" for item in artifacts):
                    private_attachment = self.intake.get_attachment(attachment["id"], include_private_reference=True)
                    reference = str(private_attachment.get("storageReference") or "")
                    canonical_url = reference.removeprefix("url:").split("|", 1)[0]
                    web_result = self.research_public_web(message, [canonical_url], 3)
                    bundle = web_result["bundle"]
                    research_artifact = self.intake.create_artifact(
                        "research_bundle", str(bundle.get("title") or "网页研究资料包"), "draft",
                        conversation_id, run["id"], {
                            "kind": "research-bundle", "bundleId": bundle["id"],
                            "summary": f"已整理 {len(web_result['sources'])} 个公开来源；网页内容按不可信外部材料处理。",
                            "estimatedMinutes": int(bundle.get("estimated_minutes") or 0),
                            "sourceType": "公开网页", "sources": list(bundle.get("sources") or []),
                            "failures": list(web_result.get("failures") or []),
                        },
                    )
                    artifacts.insert(0, research_artifact)
                    if outcome.kind == "propose_write":
                        # The web-specific proposal must reference the verified
                        # Research Bundle, not merely echo the user's URL prompt.
                        artifacts = [item for item in artifacts if item["type"] not in {"capture_proposal", "change_set"}]
                        save = self.save_research_bundle(str(bundle["id"]))
                        change_set = save["change_set"]
                        artifacts.append(self.intake.create_artifact(
                            "change_set", str(change_set.get("title") or "保存网页研究包"), "awaiting_confirmation",
                            conversation_id, run["id"], {
                                "kind": "change-set", "changeSetId": change_set["id"],
                                "createCount": sum(item.get("action") == "create" for item in change_set.get("writes", [])),
                                "updateCount": sum(item.get("action") == "update" for item in change_set.get("writes", [])),
                                "riskLevel": "low", "summary": "网页研究包已生成保存提案，尚未写入 Obsidian。",
                            },
                        ))
            active = next((item for item in artifacts if item["type"] in {"learning_pack", "material", "capture_proposal", "research_bundle", "learning_plan", "knowledge_gap"}), None)
            if active:
                self.intake.set_active_artifact(conversation_id, active["id"])
            artifact_group = self.intake.create_artifact_group(
                conversation_id, task_thread["id"],
                self._assistant_task_title(str(run.get("primary_intent") or ""), message), artifacts,
                "learning-result" if any(item["type"] == "learning_pack" for item in artifacts) else "assistant-result",
            ) if task_thread and artifacts else None
            run_status = str(run.get("status") or "completed")
            task_status = "failed" if run_status == "failed" else "waiting_user" if run_status == "awaiting_confirmation" else "completed"
            if task_thread:
                task_thread = self.intake.update_task_thread(
                    task_thread["id"], title=self._assistant_task_title(str(run.get("primary_intent") or ""), message),
                    intent=str(run.get("primary_intent") or "unknown"), status=task_status, progress=100,
                    artifact_group_id=artifact_group["id"] if artifact_group else None,
                    error_code=run.get("error_code"), recoverable=run_status == "failed",
                )
            response_text = self._assistant_response(run, artifacts, outcome)
            if precise_intent.get("inheritedProposal"):
                change_artifact = next((item for item in artifacts if item["type"] == "change_set"), None)
                target = str(precise_intent.get("requestedDestination") or "")
                if change_artifact:
                    response_text = (
                        "已生成待确认的 Change Set，尚未写入 Obsidian。\n\n"
                        f"- 目标：`{target}`\n"
                        f"- Change Set：`{change_artifact['payload'].get('changeSetId', '')}`\n"
                        "- 下一步：在审核页查看 Diff，确认后才会事务写入。"
                    )
            if organization_result.get("status") == "applied":
                applied = next((item for item in organization_result.get("results", []) if item.get("status") == "applied"), {})
                response_text = (
                    f"已识别目标：{context_material['materialBundle']['title']}\n\n"
                    f"已整理到 Obsidian：`{applied.get('path', '')}`\n\n"
                    "写入前已生成组织计划；写入后已保存快照和变化记录，可从最近修改中撤销。"
                )
            elif organization_result.get("status") == "preview":
                response_text = f"已识别目标：{context_material['materialBundle']['title']}\n\n已生成整理预览，尚未写入 Obsidian。"
            daily_adjustment = None
            requested_budget = self._requested_today_budget(message)
            requested_constraints = self._requested_today_constraints(message)
            assistant_message = self.intake.append_message(
                conversation_id, "assistant", response_text,
                "artifact-summary" if artifacts else "answer",
                task_thread["id"] if task_thread else None,
                artifact_group["id"] if artifact_group else None,
            )
            intelligence = self._record_conversation_intelligence(
                conversation_id, message_row["id"], assistant_message["id"], message,
                response_text, outcome,
            )
            if not organization_result:
                context_material["focus"] = self.context_material.focus.update_after_turn(
                    context_material["focus"], precise_intent,
                )
            previous_constraints = self.store.get_setting("today_constraints", {})
            if requested_constraints:
                self.store.set_setting("today_constraints", requested_constraints)
            if (requested_budget is not None or requested_constraints) and outcome.kind in {"answer_only", "answer_and_track", "suggest_action"}:
                current_plan = self.store.get_daily_plan(date.today().isoformat())
                daily_adjustment = self.adjust_today({
                    "type": "set_budget", "available_minutes": requested_budget or int((current_plan or {}).get("budgetMinutes") or 25),
                    "reason": "用户在助手中明确调整今日可用时间或内容偏好",
                    "_previous_constraints": previous_constraints,
                })
                action_message = (
                    "今日安排已调整\n\n"
                    f"{daily_adjustment['beforeMinutes']} 分钟 → {daily_adjustment['afterMinutes']} 分钟"
                    + ("\n已优先移除公式或推导型非固定任务。" if requested_constraints.get("noFormula") else "")
                    + "\n\n你可以在「今日」查看变化，或撤销这次调整。"
                )
                self.intake.append_message(conversation_id, "assistant", action_message, "action-result")
            self.log("intake.completed", {"intake_id": intake_id, "run_id": run["id"], "artifact_count": len(artifacts), "outcome": outcome.kind})
            return {"conversation": self.get_conversation(conversation_id), "run": run, "artifacts": artifacts, "task_thread": task_thread, "artifact_group": artifact_group, "outcome": outcome.as_dict(), "assistantIntent": precise_intent, "focus": context_material["focus"], "materialBundle": context_material["materialBundle"], "organization": organization_result, "intelligence": intelligence, "dailyAdjustment": daily_adjustment, "intake_id": intake_id}
        except Exception as error:
            self.intake.update_intake_item(intake_id, "failed", 100, error_code=getattr(error, "code", type(error).__name__))
            if task_thread:
                self.intake.update_task_thread(
                    task_thread["id"], status="failed", progress=60,
                    error_code=getattr(error, "code", type(error).__name__), recoverable=True,
                )
            raise

    def _artifacts_for_run(
        self, conversation_id: str, run: dict[str, Any], original_reference: str,
        attachments: list[dict[str, Any]], original_message: str = "", outcome: str = "create_artifact",
    ) -> list[dict[str, Any]]:
        artifacts: list[dict[str, Any]] = []
        for result in (run.get("result") or {}).get("results", []):
            kind = str(result.get("kind") or "")
            if kind in {"capture", "organized-text", "save-proposal"} and outcome in {"propose_write", "create_artifact"}:
                notes = result.get("proposed_notes") or []
                title = str((notes[0] if notes else {}).get("title") or result.get("suggested_title") or "Obsidian 保存提案")
                payload = {**result, "originalReference": original_reference, "sourceType": "用户原文", "riskLevel": "low"}
                artifacts.append(self.intake.create_artifact("capture_proposal", title, "awaiting_confirmation", conversation_id, run["id"], payload))
            elif kind == "research-bundle" and outcome == "create_artifact":
                bundle = result.get("bundle") or {}
                artifacts.append(self.intake.create_artifact("research_bundle", str(bundle.get("title") or "研究资料包"), "draft", conversation_id, run["id"], {**result, "estimatedMinutes": bundle.get("estimated_minutes", 0), "sourceType": "研究"}))
            elif kind == "plan-proposal" and outcome == "create_artifact":
                proposal = result.get("proposal") or {}
                artifacts.append(self.intake.create_artifact("learning_plan", str(proposal.get("title") or "学习计划"), "awaiting_confirmation", conversation_id, run["id"], {**result, "estimatedMinutes": sum(int(item.get("minutes", 0)) for item in proposal.get("tasks", []))}))
            elif kind == "curriculum" and outcome == "create_artifact":
                artifacts.append(self.intake.create_artifact("knowledge_gap", "AI 补全知识候选", "draft", conversation_id, run["id"], {**result, "summary": "基于 reviewed/core 薄弱点生成，不是正式知识"}))
            elif kind == "tutor" and outcome == "create_artifact":
                question = str(result.get("question") or original_message or "当前主题")
                topic_source = re.sub(r"^(请|帮我|介绍一下|解释一下|学习|了解)\s*", "", question).strip(" ：:。")
                topic_source = re.sub(r"^把\s*", "", topic_source)
                topic_source = re.split(r"(?:整理成|做成|生成|构建)(?:一个)?学习包", topic_source, maxsplit=1)[0].strip()
                topic = re.split(r"[，,。；;！？!?]", topic_source, maxsplit=1)[0].strip()[:48] or "当前主题"
                no_formula = any(token in original_message for token in ("不要公式", "暂时不要公式", "先讲直觉", "不讲公式"))
                sections = [
                    {"title": "背景与直觉", "minutes": 4},
                    {"title": "条件与边界", "minutes": 3},
                    {"title": "配对流程", "minutes": 3},
                    {"title": "实践小结", "minutes": 2},
                ]
                learning_pack = {
                    **result, "kind": "learning-pack", "summary": result.get("answer", "学习包已生成"),
                    "estimatedMinutes": 12, "domain": "因果推断" if any(token in topic.upper() for token in ("PSM", "倾向得分", "因果")) else "当前学习主线",
                    "format": "微课", "noFormula": no_formula,
                    "learningOutcomes": [f"用直觉解释{topic}的核心思想", "识别适用条件与常见误区", "完成一组理解小测"],
                    "prerequisites": ["基础统计知识", "处理组与对照组", "可比性直觉"],
                    "sections": sections, "quizPreview": list(result.get("quiz_preview") or [])[:3],
                    "sources": list(result.get("evidence") or []), "originalReference": original_reference,
                }
                artifacts.append(self.intake.create_artifact("learning_pack", f"{topic}入门学习包", "draft", conversation_id, run["id"], learning_pack))
            change_set = result.get("change_set")
            if outcome in {"propose_write", "create_artifact"} and isinstance(change_set, dict) and change_set.get("id"):
                writes = list(change_set.get("writes") or [])
                artifacts.append(self.intake.create_artifact(
                    "change_set", str(change_set.get("title") or "Change Set"), "awaiting_confirmation", conversation_id, run["id"],
                    {"kind": "change-set", "changeSetId": change_set["id"], "createCount": sum(item.get("action") == "create" for item in writes), "updateCount": sum(item.get("action") == "update" for item in writes), "linkCount": 0, "conflictCount": 0, "riskLevel": "low", "summary": change_set.get("preview", "等待确认")},
                ))
        return artifacts

    @staticmethod
    def _assistant_response(run: dict[str, Any], artifacts: list[dict[str, Any]], outcome: AssistantOutcome) -> str:
        if run.get("status") == "failed":
            return str(run.get("error_message") or "任务处理失败")
        if outcome.kind in {"answer_only", "answer_and_track", "suggest_action"}:
            for result in (run.get("result") or {}).get("results", []):
                answer = str(result.get("answer") or "").strip()
                if answer:
                    if outcome.kind == "suggest_action":
                        return answer + "\n\n如果你希望，我可以在你明确确认后把它整理成学习任务或笔记提案。"
                    return answer
        return AgentService._intake_summary(run, artifacts)

    def _record_conversation_intelligence(
        self, conversation_id: str, user_message_id: str, assistant_message_id: str,
        user_message: str, assistant_message: str, outcome: AssistantOutcome,
    ) -> dict[str, Any]:
        if outcome.kind == "answer_only":
            return {"tracked": False, "signals": [], "summary": None}
        signals = extract_knowledge_signals(user_message)
        stored = [self.store.upsert_conversation_signal(conversation_id, item.as_dict(), user_message_id) for item in signals]
        previous = self.store.latest_conversation_summary(conversation_id)
        summary_text = build_conversation_summary(
            str((previous or {}).get("summary") or ""), user_message, assistant_message, signals,
        )
        summary = self.store.save_conversation_summary(
            conversation_id, summary_text, [user_message_id, assistant_message_id],
        )
        primary_topic = next((item.topic for item in signals if item.signal_type == "topic"), "")
        if primary_topic:
            with self.store.lock:
                self.store.connection.execute(
                    "UPDATE conversations SET active_topic=?, updated_at=? WHERE id=?",
                    (primary_topic, datetime.now().astimezone().isoformat(timespec="seconds"), conversation_id),
                )
                self.store.connection.commit()
        directions = self.refresh_learning_directions() if stored else self.learning_directions()
        return {"tracked": True, "signals": stored, "summary": summary, "directions": directions}

    @staticmethod
    def _requested_today_budget(message: str) -> int | None:
        text = " ".join(str(message).split())
        if not any(token in text for token in ("今天只有", "今日只有", "今天安排", "今日安排", "今天可用", "今日可用", "只剩", "调整为", "改成")):
            return None
        match = re.search(r"(\d{1,3})\s*分钟", text)
        if not match:
            return None
        return max(5, min(360, int(match.group(1))))

    @staticmethod
    def _requested_today_constraints(message: str) -> dict[str, Any]:
        text = " ".join(str(message).split())
        if not any(token in text for token in ("今天", "今日")):
            return {}
        return {"date": date.today().isoformat(), "noFormula": True} if any(
            token in text for token in ("不想看公式", "不要公式", "暂时不要公式", "先不看公式")
        ) else {}

    def _today_candidates(self) -> list[dict[str, Any]]:
        items = self.list_recommendations()
        # The daily plan is the execution layer. Direction predictions and
        # unverified curriculum candidates stay in their own pools until they
        # pass the explicit admission gate.
        items = [item for item in items if not item.get("direction") and (
            not item.get("candidate") or study_workspace.admit_candidate(item).placement == "daily-plan"
        ) and int(item.get("splitPart") or 1) == 1]
        constraints = self.store.get_setting("today_constraints", {})
        if constraints.get("date") == date.today().isoformat() and constraints.get("noFormula"):
            items = [item for item in items if not any(
                token in f"{item.get('title', '')} {item.get('reason', '')}" for token in ("公式", "推导")
            )]
        return items

    def refresh_learning_directions(self) -> list[dict[str, Any]]:
        candidates = learning_directions.predict_directions(self.vault, self.store, 5)
        with self.store.lock:
            self.store.connection.execute("UPDATE direction_predictions SET state='stale' WHERE state='active'")
            self.store.connection.commit()
        stored = []
        for candidate in candidates:
            stored.append(self.store.save_direction_prediction({
                "id": candidate["id"], "horizon": candidate["horizon"], "title": candidate["title"],
                "rationale": candidate["why"], "evidence": candidate["evidence"],
                "confidence": candidate["confidence"], "state": "active",
            }))
        self.log("learning-directions.refreshed", {"count": len(stored)})
        return self.learning_directions()

    def learning_directions(self) -> list[dict[str, Any]]:
        metadata = {item["id"]: item for item in learning_directions.predict_directions(self.vault, self.store, 5)}
        persisted = self.store.list_direction_predictions("active", 20)
        return [{**item, **metadata.get(item["id"], {})} for item in persisted]

    def build_today(self, budget_minutes: int | None = None, *, force: bool = False) -> dict[str, Any]:
        plan_date = date.today().isoformat()
        current = self.store.get_daily_plan(plan_date)
        if current and not force and budget_minutes is None:
            eligible = {str(item["id"]): item for item in self._today_candidates()}
            if all(item.get("state") == "completed" or str(item["recommendationId"]) in eligible for item in current.get("items", [])):
                return self._hydrate_daily_plan(current, eligible)
        budget = max(5, min(360, int(budget_minutes or (current or {}).get("budgetMinutes") or 25)))
        recommendations_all = self._today_candidates()
        recommendation_by_id = {str(item["id"]): item for item in recommendations_all}
        fixed_items = [dict(item) for item in (current or {}).get("items", []) if item.get("fixed") and str(item.get("recommendationId")) in recommendation_by_id]
        chosen: list[dict[str, Any]] = fixed_items
        used = {str(item["recommendationId"]) for item in chosen}
        consumed = sum(int(item["minutes"]) for item in chosen)
        budget = max(budget, min(360, consumed))
        for recommendation in recommendations_all:
            rec_id = str(recommendation["id"])
            if rec_id in used or consumed >= budget:
                continue
            estimate = max(5, int(recommendation.get("estimatedMinutes", 10)))
            remaining = budget - consumed
            if estimate > remaining and remaining < 5:
                continue
            minutes = min(estimate, remaining)
            chosen.append({"recommendationId": rec_id, "minutes": minutes, "state": "planned", "fixed": False, "source": "deterministic-ranking"})
            used.add(rec_id); consumed += minutes
        plan = self.store.replace_daily_plan(
            plan_date, budget, chosen, expected_version=int((current or {}).get("version", 0)),
        )
        return self._hydrate_daily_plan(plan, recommendation_by_id)

    def _hydrate_daily_plan(self, plan: dict[str, Any], recommendation_by_id: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
        lookup = recommendation_by_id or {str(item["id"]): item for item in self.list_recommendations()}
        for planned in plan.get("items", []):
            recommendation_id = str(planned["recommendationId"])
            if recommendation_id in lookup: continue
            with self.store.lock:
                row = self.store.connection.execute(
                    "SELECT details_json FROM study_sessions WHERE recommendation_id=? ORDER BY updated_at DESC LIMIT 1", (recommendation_id,),
                ).fetchone()
            if row:
                stored = json.loads(row["details_json"] or "{}").get("recommendation")
                if isinstance(stored, dict): lookup[recommendation_id] = stored
        items = [{**item, "recommendation": lookup.get(str(item["recommendationId"]), {
            "id": item["recommendationId"], "title": "推荐内容不可用", "kind": "learn",
        })} for item in plan.get("items", [])]
        return {**plan, "items": items, "totalMinutes": sum(int(item["minutes"]) for item in items)}

    def adjust_today(self, change: dict[str, Any]) -> dict[str, Any]:
        plan_date = date.today().isoformat()
        before = self.store.get_daily_plan(plan_date)
        if before is None:
            self.build_today()
            before = self.store.get_daily_plan(plan_date)
        assert before is not None
        adjustment_type = str(change.get("type") or "set_budget")
        target_id = str(change.get("target_id") or "")
        items = [dict(item) for item in before["items"]]
        budget = int(before["budgetMinutes"])
        if adjustment_type == "set_budget":
            budget = max(5, min(360, int(change.get("available_minutes") or budget)))
            fixed = [item for item in items if item.get("fixed")]
            fixed_minutes = sum(int(item["minutes"]) for item in fixed)
            if fixed_minutes > budget:
                raise ValueError("fixed_tasks_exceed_budget")
            recs = self._today_candidates()
            items = fixed; used = {str(item["recommendationId"]) for item in items}; consumed = fixed_minutes
            for rec in recs:
                if str(rec["id"]) in used or consumed >= budget:
                    continue
                estimate = max(5, int(rec.get("estimatedMinutes", 10))); remaining = budget - consumed
                if remaining < 5:
                    break
                items.append({"recommendationId": str(rec["id"]), "minutes": min(estimate, remaining), "state": "planned", "fixed": False, "source": "agent-adjustment"})
                used.add(str(rec["id"])); consumed += min(estimate, remaining)
        elif adjustment_type in {"move_tomorrow", "move_weekend", "remove"}:
            target = next((item for item in items if item["recommendationId"] == target_id), None)
            if not target:
                raise ValueError("daily_item_not_found")
            if target.get("fixed"):
                raise ValueError("fixed_task_cannot_be_removed")
            items = [item for item in items if item["recommendationId"] != target_id]
            if adjustment_type != "remove":
                recommendations.record_action(self.store, target_id, "tomorrow" if adjustment_type == "move_tomorrow" else "weekend", {"agent_adjusted": True})
        elif adjustment_type == "shorten":
            target = next((item for item in items if item["recommendationId"] == target_id), None)
            if not target:
                raise ValueError("daily_item_not_found")
            target["minutes"] = max(5, min(int(target["minutes"]), int(change.get("minutes") or 5)))
        elif adjustment_type == "reorder":
            order = [str(value) for value in change.get("order", [])]
            position = {value: index for index, value in enumerate(order)}
            items.sort(key=lambda item: position.get(str(item["recommendationId"]), len(position) + int(item.get("position", 0))))
        else:
            raise ValueError("unsupported_daily_adjustment")
        normalized = [{key: value for key, value in item.items() if key != "recommendation"} for item in items]
        after = self.store.replace_daily_plan(plan_date, budget, normalized, expected_version=int(before["version"]))
        prior_constraints = change.get("_previous_constraints") if "_previous_constraints" in change else self.store.get_setting("today_constraints", {})
        before_snapshot = {**before, "constraints": dict(prior_constraints or {})}
        after_snapshot = {**after, "constraints": self.store.get_setting("today_constraints", {})}
        action = self.store.save_daily_adjustment(
            adjustment_type, target_id, before_snapshot, after_snapshot, str(change.get("reason") or "Agent 调整今日安排"),
        )
        self.log("daily-plan.adjusted", {"action_id": action["actionId"], "type": adjustment_type, "before_minutes": sum(int(item["minutes"]) for item in before["items"]), "after_minutes": sum(int(item["minutes"]) for item in after["items"])})
        return {**action, "plan": self._hydrate_daily_plan(after),
                "beforeMinutes": sum(int(item["minutes"]) for item in before["items"]),
                "afterMinutes": sum(int(item["minutes"]) for item in after["items"]), "undoAvailable": True}

    def undo_today_adjustment(self, action_id: str) -> dict[str, Any]:
        action = self.store.get_daily_adjustment(action_id)
        if action["state"] != "applied":
            raise ValueError("daily_adjustment_not_undoable")
        before, after = action["before"], action["after"]
        current = self.store.get_daily_plan(str(after["date"]))
        if not current or int(current["version"]) != int(after["version"]):
            raise RuntimeError("daily_plan_changed_since_adjustment")
        restored = self.store.replace_daily_plan(
            str(before["date"]), int(before["budgetMinutes"]), list(before["items"]),
            expected_version=int(current["version"]),
        )
        self.store.set_setting("today_constraints", dict(before.get("constraints") or {}))
        self.store.mark_daily_adjustment_undone(action_id)
        self.log("daily-plan.adjustment-undone", {"action_id": action_id})
        return {"actionId": action_id, "state": "undone", "plan": self._hydrate_daily_plan(restored)}

    def _complete_local_intake_answer(
        self, conversation_id: str, intake_id: str, user_message_id: str,
        context_material: dict[str, Any], answer: str, result_kind: str,
    ) -> dict[str, Any]:
        """Finish a deterministic control turn without asking the model to guess state."""
        request = BrainRequest(
            text=answer, mode="qa", source="unified-intake-control",
            metadata={
                "conversation_id": conversation_id,
                "user_message_id": user_message_id,
                "control_kind": result_kind,
            },
        )
        run_id = f"run-{uuid.uuid4().hex}"
        self.store.create_brain_run(run_id, request)
        self.store.set_brain_intent(run_id, IntentResult("ask_question", basis="deterministic-control"))
        self.store.finish_brain_run(run_id, "completed", {"results": [{"kind": result_kind, "answer": answer}]})
        self.intake.update_intake_item(intake_id, "completed", 100, run_id)
        assistant_message = self.intake.append_message(conversation_id, "assistant", answer, "action-result")
        focus = self.context_material.focus.update_after_turn(context_material["focus"], context_material["intent"])
        self.log("intake.control-completed", {"intake_id": intake_id, "run_id": run_id, "kind": result_kind})
        return {
            "conversation": self.get_conversation(conversation_id), "run": self.get_brain_run(run_id),
            "artifacts": [], "task_thread": None, "artifact_group": None,
            "outcome": AssistantOutcome("answer_only", result_kind, False).as_dict(),
            "assistantIntent": context_material["intent"], "focus": focus,
            "materialBundle": context_material["materialBundle"], "organization": {},
            "intelligence": {"tracked": False, "signals": [], "summary": None},
            "dailyAdjustment": None, "intake_id": intake_id, "assistantMessage": assistant_message,
        }

    def _latest_write_status(self, conversation_id: str) -> str:
        change_set_id = ""
        artifacts = self.intake.list_artifacts(conversation_id=conversation_id, limit=200).get("items", [])
        for item in artifacts:
            if item.get("type") != "change_set":
                continue
            detail = self.intake.get_artifact(str(item["id"]))
            change_set_id = str((detail.get("payload") or {}).get("changeSetId") or "")
            if change_set_id:
                break
        if not change_set_id:
            with self.store.lock:
                rows = self.store.connection.execute(
                    "SELECT cs.id, br.request_json FROM brain_change_sets cs "
                    "JOIN brain_runs br ON br.id=cs.run_id ORDER BY cs.created_at DESC LIMIT 100"
                ).fetchall()
            for row in rows:
                try:
                    request = json.loads(row["request_json"] or "{}")
                except json.JSONDecodeError:
                    continue
                if str((request.get("metadata") or {}).get("conversation_id") or "") == conversation_id:
                    change_set_id = str(row["id"]); break
        if not change_set_id:
            return "当前会话还没有生成写入提案，也没有执行任何文件写入。"
        record = self.store.get_brain_change_set(change_set_id)
        targets = [str(item.get("path") or "") for item in record.get("writes", []) if item.get("path")]
        target_text = "、".join(f"`{item}`" for item in targets) or "未记录目标"
        state = str(record.get("state") or "proposed")
        if state == "applied":
            return f"最近的 Change Set `{change_set_id}` 已写入：{target_text}。"
        if state == "proposed":
            return f"最近的 Change Set `{change_set_id}` 仍在等待确认，尚未写入。拟写入目标：{target_text}。"
        return f"最近的 Change Set `{change_set_id}` 状态为 `{state}`，没有可声称已完成的写入。目标：{target_text}。"

    @staticmethod
    def _intake_summary(run: dict[str, Any], artifacts: list[dict[str, Any]]) -> str:
        if run.get("status") == "failed":
            return str(run.get("error_message") or "任务处理失败")
        labels = {"material": "资料任务", "capture_proposal": "保存提案", "research_bundle": "研究包", "learning_plan": "学习计划", "change_set": "Change Set", "knowledge_gap": "知识缺口", "learning_pack": "学习包", "quiz": "小测"}
        names = [labels.get(item["type"], item["title"]) for item in artifacts]
        return "已完成处理。" + (f"生成：{'、'.join(names)}。" if names else "未生成需要确认的写入。")

    @staticmethod
    def _conversation_title(message: str) -> str:
        message = redact_secret_text(message)
        if any(token in message for token in ("PDF", "论文", "教材", "材料")):
            return "资料处理会话"
        if any(token in message for token in ("保存", "存入", "记下来")):
            return "Obsidian 保存提案"
        if any(token in message for token in ("研究", "找资料", "检索")):
            return "主题研究"
        if any(token in message for token in ("计划", "安排学习")):
            return "学习计划"
        return (message.splitlines()[0][:32].strip() or "新会话")

    @staticmethod
    def _assistant_task_title(intent: str, message: str) -> str:
        topic = re.sub(r"[，。！？!?].*$", "", message.strip()).strip()[:42]
        labels = {
            "learn_topic": "构建入门学习包", "ask_question": "回答当前问题",
            "research_topic": "整理可信资料与学习路径", "create_study_plan": "生成学习计划",
            "capture_text": "整理原文并生成保存提案", "organize_text": "整理原文并生成保存提案",
            "import_material": "处理资料并生成学习提案", "continue_artifact_revision": "更新当前成果",
        }
        base = labels.get(intent, "处理当前任务")
        return f"{base}：{topic}" if topic and intent in {"learn_topic", "research_topic"} else base

    def list_agent_artifacts(self, artifact_type: str = "", status: str = "", conversation_id: str = "", limit: int = 100, offset: int = 0) -> dict[str, Any]:
        return self.intake.list_artifacts(artifact_type, status, conversation_id, limit, offset)

    def get_agent_artifact(self, artifact_id: str) -> dict[str, Any]:
        return self.intake.get_artifact(artifact_id)

    def revise_agent_artifact(self, artifact_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return self.intake.revise_artifact(artifact_id, str(body.get("instruction") or ""), dict(body.get("patch", {})), body.get("expected_version"))

    def agent_artifact_action(self, artifact_id: str, body: dict[str, Any]) -> dict[str, Any]:
        result = self.intake.artifact_action(artifact_id, str(body.get("action") or ""))
        self.log("artifact.action", {"artifact_id": artifact_id, "action": body.get("action")})
        return result

    def list_materials(self, status: str = "", limit: int = 100, offset: int = 0) -> dict[str, Any]:
        return self.intake.list_materials(status, limit, offset)

    def get_material(self, intake_id: str) -> dict[str, Any]:
        return self.intake.get_material(intake_id)

    def list_brain_runs(self, *, limit: int = 50, offset: int = 0, status: str = "") -> dict[str, Any]:
        items = self.store.list_brain_runs(limit, offset, status)
        return {"items": items, "limit": max(1, min(100, limit)), "offset": max(0, offset), "has_more": len(items) == max(1, min(100, limit))}

    def get_brain_run(self, run_id: str) -> dict[str, Any]:
        return self.store.get_brain_run(run_id, include_details=True)

    def cancel_brain_run(self, run_id: str) -> dict[str, Any]:
        self.store.request_brain_cancel(run_id)
        self.log("workflow.cancel-requested", {"run_id": run_id})
        return self.store.get_brain_run(run_id, include_details=True)

    def retry_brain_run(self, run_id: str) -> dict[str, Any]:
        result = self.workflows.retry(run_id); self.log("workflow.retried", {"run_id": result["id"], "prior_run_id": run_id}); return result

    def brain_events(self, run_id: str, after_sequence: int = 0) -> dict[str, Any]:
        run = self.get_brain_run(run_id)
        agent_events = self.store.list_agent_run_events(run_id, after_sequence)
        return {
            "schemaVersion": 2,
            "run_id": run_id,
            "status": run["status"],
            "steps": run["steps"],
            "tool_events": run["tool_events"],
            "proposed_actions": run["proposed_actions"],
            "events": [item["payload"] for item in agent_events],
            "checkpoint": self.store.get_agent_run_checkpoint(run_id),
        }

    def assistant_run_events(
        self,
        run_id: str,
        after_sequence: int = 0,
        limit: int = 500,
    ) -> dict[str, Any]:
        run = self.store.get_brain_run(run_id)
        saved = self.store.list_agent_run_events(
            run_id,
            max(0, int(after_sequence)),
            limit,
        )
        return {
            "schemaVersion": 2,
            "runId": run_id,
            "status": run["status"],
            "events": [item["payload"] for item in saved],
            "checkpoint": self.store.get_agent_run_checkpoint(run_id),
        }

    def brain_capabilities(self) -> dict[str, Any]:
        return {"workflow_version": self.workflows.version, "skills": self.skills.definitions(), "tools": self.tools.definitions(), "pi_is_only_agent_loop": True, "reviewed_core_read_only": True}

    def brain_health(self) -> dict[str, Any]:
        profiles = self.list_model_profiles()
        routes = self.model_routing()
        return {
            "ok": True, "workflow_version": self.workflows.version, "schema_version": self.store.schema_version(),
            "model_configured": any(item["configured"] and item["enabled"] for item in profiles),
            "model_routes": {key: bool(value.get("profileId")) for key, value in routes.items()},
            "fallback_available": True,
        }

    def brain_diagnostics(self) -> dict[str, Any]:
        profiles = self.list_model_profiles()
        from urllib.parse import urlparse
        with self.store.lock:
            counts = {
                "conversations": int(self.store.connection.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]),
                "conversation_summaries": int(self.store.connection.execute("SELECT COUNT(*) FROM conversation_summaries").fetchone()[0]),
                "knowledge_signals": int(self.store.connection.execute("SELECT COUNT(*) FROM conversation_knowledge_signals WHERE status='active'").fetchone()[0]),
                "learning_events": int(self.store.connection.execute("SELECT COUNT(*) FROM learning_events").fetchone()[0]),
                "direction_predictions": int(self.store.connection.execute("SELECT COUNT(*) FROM direction_predictions WHERE state='active'").fetchone()[0]),
                "today_adjustments": int(self.store.connection.execute("SELECT COUNT(*) FROM daily_plan_adjustments").fetchone()[0]),
                "web_sources": int(self.store.connection.execute("SELECT COUNT(*) FROM web_sources").fetchone()[0]),
                "agent_actions": int(self.store.connection.execute("SELECT COUNT(*) FROM agent_actions").fetchone()[0]),
                "snapshots": int(self.store.connection.execute("SELECT COUNT(*) FROM file_snapshots").fetchone()[0]),
                "undo_available": int(self.store.connection.execute("SELECT COUNT(*) FROM agent_actions WHERE status='applied' AND action_type!='undo'").fetchone()[0]),
                "conversation_focus": int(self.store.connection.execute("SELECT COUNT(*) FROM conversation_focus").fetchone()[0]),
                "material_bundles": int(self.store.connection.execute("SELECT COUNT(*) FROM material_bundles").fetchone()[0]),
                "organization_plans": int(self.store.connection.execute("SELECT COUNT(*) FROM organization_plans").fetchone()[0]),
            }
            latest_web = self.store.connection.execute("SELECT title, fetched_at FROM web_sources ORDER BY fetched_at DESC LIMIT 1").fetchone()
            latest_error = self.store.connection.execute(
                "SELECT error_code, error_message, updated_at FROM brain_runs WHERE status='failed' ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
        jobs = self.store.list_jobs()
        routes = self.model_routing()
        return {
            "service": {"status": "online", "api_version": "v1", "workflow_version": self.workflows.version, "schema_version": self.store.schema_version(), "runtime_id": self.runtime_id},
            "models": [{"id": item["id"], "name": item["displayName"], "provider": item["providerType"], "base_url_host": urlparse(item["baseUrl"]).hostname or "", "configured": item["configured"], "model": item["defaultModel"]} for item in profiles],
            "brain_runs": self.store.list_brain_runs(20, 0),
            "indexes": {"reviewed": len(learning.scan_reviewed(self.vault)), "curriculum_candidates": len(self.store.list_curriculum_candidates("active")), "research_bundles": len(self.store.list_research_bundles(100, 0)), "pending_reviews": sum(item.get("review_state") == "pending" for item in self.list_reviews()), **counts},
            "runtime": {
                "current_model_route": (routes.get("assistant_chat") or routes.get("assistant") or {}).get("profileId") or "local-deterministic",
                "key_configured": any(item["configured"] and item["enabled"] for item in profiles),
                "web_provider": "public-http" if counts["web_sources"] else "not-used",
                "latest_web_fetch": dict(latest_web) if latest_web else None,
                "pdf_jobs": sum(item.get("kind") == "prepare-pdf" for item in jobs),
                "typescript_boundary": "Obsidian UI, events and native Markdown rendering",
                "python_boundary": "policy, persistence, retrieval, transactions and model adapters",
            },
            "recent_error": redact(dict(latest_error)) if latest_error else None,
        }

    def get_change_set(self, change_set_id: str) -> dict[str, Any]:
        return self.brain_change_sets.public_record(change_set_id)

    def diff_change_set(self, change_set_id: str) -> dict[str, Any]:
        return self.brain_change_sets.diff(change_set_id)

    def _append_assistant_lifecycle_event(
        self,
        run_id: str,
        event_type: str,
        **payload: Any,
    ) -> dict[str, Any]:
        existing = self.store.list_agent_run_events(run_id, 0, 2000)
        for item in existing:
            value = item.get("payload") or {}
            if item.get("type") == event_type and (
                not payload.get("proposalId")
                or value.get("proposalId") == payload.get("proposalId")
            ):
                return value
        request = self.store.load_brain_request(run_id)
        metadata = request.get("metadata") or {}
        return self.store.append_agent_run_event_next(
            run_id,
            event_type,
            {
                "conversationId": str(metadata.get("conversation_id") or ""),
                **payload,
            },
        )

    def list_research_bundles(self, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        items = self.store.list_research_bundles(limit, offset)
        return {"items": items, "limit": limit, "offset": offset, "has_more": len(items) == limit}

    def get_research_bundle(self, bundle_id: str) -> dict[str, Any]:
        return self.store.get_research_bundle(bundle_id)

    def search_public_web(self, query: str, limit: int = 8) -> dict[str, Any]:
        result = self.web.search(query, limit)
        self.log("web.search-completed", {"query_length": len(query), "result_count": len(result["results"])})
        return result

    def search_academic_web(self, query: str, limit: int = 8) -> dict[str, Any]:
        result = self.web.search_academic(query, limit)
        self.log("web.academic-search-completed", {
            "query_length": len(query),
            "result_count": len(result["results"]),
            "provider_failure_count": len(result.get("providerFailures") or []),
        })
        return result

    def fetch_public_web(self, url: str) -> dict[str, Any]:
        source = self.web.fetch_for_model(url)
        self.log("web.source-fetched", {"source_id": source["id"], "domain": source["domain"], "prompt_injection_detected": source["promptInjectionDetected"]})
        return source

    def web_capabilities(self) -> dict[str, Any]:
        return self.web.capabilities()

    def research_public_web(self, query: str, urls: list[str] | None = None, limit: int = 5) -> dict[str, Any]:
        result = self.web.research(query, urls, limit)
        self.log("web.research-completed", {"bundle_id": result["bundle"]["id"], "source_count": len(result["sources"]), "failure_count": len(result["failures"])})
        return result

    def autonomy_status(self) -> dict[str, Any]:
        return self.autonomy.status()

    def set_autonomy_mode(self, body: dict[str, Any]) -> dict[str, Any]:
        result = self.autonomy.set_mode(str(body.get("mode") or ""), acknowledge_summary=body.get("acknowledge_summary") is True)
        self.log("autonomy.mode-updated", {"mode": result["mode"], "summary_acknowledged": result["permissionSummaryAcknowledged"]})
        return result

    def apply_autonomous_vault_change(self, body: dict[str, Any]) -> dict[str, Any]:
        result = self.autonomy.apply_low_risk(body)
        if result.get("applied"):
            self.log("vault.low-risk-change-applied", {"action_id": result["actionId"], "path": result["path"]})
            return result
        relative = str(body.get("path") or "")
        target = (self.vault / relative).resolve()
        if not target.is_relative_to(self.vault) or target.is_symlink():
            raise ValueError("invalid_vault_path")
        original = target.read_text(encoding="utf-8", errors="replace") if target.is_file() else ""
        operation = str(body.get("operation") or "update_managed_block")
        proposed = render_managed_block(original, str(body.get("block") or "learning-brain"), str(body.get("content") or "")) if operation == "update_managed_block" else str(body.get("content") or "").rstrip() + "\n"
        change_path = relative
        category = "ordinary-update"
        if result["classification"]["protected"]:
            suggestion_id = hashlib.sha256((relative + proposed).encode()).hexdigest()[:12]
            change_path = f"90-Local-Only/Agent-Managed/Update-Suggestions/{Path(relative).stem}-{suggestion_id}.md"
            proposed = (
                "---\ntype: update-suggestion\nstatus: ai-draft\nreview_state: pending\n"
                f"target: \"{relative}\"\n---\n\n# 更新建议：{Path(relative).stem}\n\n"
                "## 建议内容\n\n" + str(body.get("content") or "").rstrip() + "\n\n"
                "## 安全说明\n\n原笔记为 reviewed/core 或受保护内容，本提案不会自动覆盖原文。\n"
            )
            category = "update-suggestion"
        request = BrainRequest(text=f"受保护写入提案：{relative}", mode="capture", source="vault-autonomy")
        run_id = f"run-{uuid.uuid4().hex}"; self.store.create_brain_run(run_id, request)
        change_set = self.brain_change_sets.create({
            "run_id": run_id, "title": f"写入提案：{Path(relative).stem}",
            "writes": [{"path": change_path, "content": proposed, "category": category}],
        })
        self.store.finish_brain_run(run_id, "awaiting_confirmation", {"results": [{"kind": "save-proposal", "change_set": change_set}]})
        self.log("vault.change-proposed", {"run_id": run_id, "change_set_id": change_set["id"], "path": relative, "reason": result.get("reason")})
        return {**result, "runId": run_id, "changeSet": change_set, "proposalPath": change_path}

    def undo_autonomous_vault_change(self, action_id: str) -> dict[str, Any]:
        result = self.autonomy.undo(action_id)
        self.log("vault.change-undone", {"action_id": action_id, "undo_action_id": result["undoActionId"], "path": result["path"]})
        return result

    def save_research_bundle(self, bundle_id: str) -> dict[str, Any]:
        bundle = self.store.get_research_bundle(bundle_id)
        title = str(bundle["title"]).replace("/", "／").replace(":", "：")[:80]
        source_rows = []
        for index, source in enumerate(bundle.get("sources", []), 1):
            location = source.get("canonical_url") or source.get("metadata", {}).get("path", "无外部链接")
            source_rows.append(f"{index}. **{source['title']}** · `{source['source_type']}` · {source['reason']}\n   - 位置：{location}")
        content = (
            "---\ntype: research-bundle\nstatus: inbox\nsource: agent-research\n"
            f"created: {date.today().isoformat()}\n---\n\n# {title}\n\n"
            f"## 研究问题\n\n{bundle['question']}\n\n## 知识缺口\n\n"
            + "\n".join(f"- {item}" for item in bundle.get("knowledge_gaps", []))
            + "\n\n## 推荐资料\n\n" + ("\n".join(source_rows) or "- 暂无")
            + "\n\n## 待验证问题\n\n" + "\n".join(f"- {item}" for item in bundle.get("unverified_questions", []))
            + "\n\n## 下一步\n\n" + "\n".join(f"- [ ] {item}" for item in bundle.get("next_steps", [])) + "\n"
        )
        folder = Path("01-Inbox/Notes") if (self.vault / "01-Inbox/Notes").is_dir() else Path("01-Inbox")
        relative = str(folder / f"{title}.md")
        if (self.vault / relative).exists():
            relative = str(folder / f"{title}-{bundle_id.removeprefix('research-')[:8]}.md")
        request = BrainRequest(text=f"保存 Research Bundle：{title}", mode="capture", source="research-save")
        run_id = f"run-{uuid.uuid4().hex}"; self.store.create_brain_run(run_id, request)
        change_set = self.brain_change_sets.create({"run_id": run_id, "title": f"保存研究包：{title}", "writes": [{"path": relative, "content": content, "category": "research-bundle"}]})
        self.store.finish_brain_run(run_id, "awaiting_confirmation", {"results": [{"kind": "save-proposal", "change_set": change_set}], "research_bundle_id": bundle_id})
        self.log("research.save-proposed", {"bundle_id": bundle_id, "run_id": run_id, "change_set_id": change_set["id"]})
        return {"run": self.store.get_brain_run(run_id, include_details=True), "change_set": change_set}

    def add_research_to_plan(self, bundle_id: str) -> dict[str, Any]:
        bundle = self.store.get_research_bundle(bundle_id)
        tasks = [{"id": f"research-task-{uuid.uuid4().hex[:12]}", "title": source["title"], "minutes": source["estimated_minutes"], "state": "proposed", "source": source.get("canonical_url") or source.get("metadata", {}).get("path", "")} for source in bundle.get("sources", [])]
        proposal = {"id": f"plan-{uuid.uuid4().hex}", "title": f"阅读计划：{bundle['title']}", "state": "proposed", "tasks": tasks}
        self.store.save_plan_proposal(proposal); self.log("research.added-to-plan", {"bundle_id": bundle_id, "proposal_id": proposal["id"]}); return proposal

    def confirm_plan_proposal(self, proposal_id: str) -> dict[str, Any]:
        proposal = self.store.update_plan_proposal_state(proposal_id, "confirmed")
        self.log("plan-proposal.confirmed", {"proposal_id": proposal_id, "task_count": len(proposal["tasks"])})
        return proposal

    def list_curriculum_candidates(self, status: str = "active") -> dict[str, Any]:
        return {"items": self.store.list_curriculum_candidates(status), "status": status}

    def refresh_curriculum(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.submit_workflow({**body, "text": str(body.get("text") or "根据当前知识状态生成学习计划"), "mode": "plan", "source": "curriculum"})

    def curriculum_candidate_action(self, candidate_id: str, action: str, cooldown_until: str | None = None) -> dict[str, Any]:
        self.store.curriculum_action(candidate_id, action, cooldown_until); self.log("curriculum.action", {"candidate_id": candidate_id, "action": action}); return {"candidate_id": candidate_id, "action": action}

    @staticmethod
    def _public_job(row: dict[str, Any]) -> dict[str, Any]:
        job = dict(row)
        job["payload"] = json.loads(job.pop("payload_json"))
        raw_result = job.pop("result_json")
        job["result"] = json.loads(raw_result) if raw_result else None
        job["cancel_requested"] = bool(job["cancel_requested"])
        return job

    def list_jobs(self) -> list[dict[str, Any]]:
        return [self._public_job(row) for row in self.store.list_jobs()]

    def get_job(self, job_id: str) -> dict[str, Any]:
        row = self.store.get_job(job_id)
        if not row: raise RuntimeError(f"Unknown job: {job_id}")
        job = self._public_job(row)
        job["timeline"] = self.store.list_job_events(job_id)
        return job

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        job = self._public_job(self.store.request_cancel(job_id)); self.log("job.cancel-requested", {"job_id": job_id}); return job

    def retry_job(self, job_id: str) -> dict[str, Any]:
        prior = self.store.get_job(job_id)
        if not prior: raise RuntimeError(f"Unknown job: {job_id}")
        if prior["state"] not in {"failed", "cancelled"}: raise RuntimeError(f"Job cannot be retried from state {prior['state']}")
        new_id = self.enqueue(str(prior["kind"]), json.loads(prior["payload_json"]))
        self.log("job.retried", {"job_id": new_id, "prior_job_id": job_id})
        return self.get_job(new_id)

    def delete_job(self, job_id: str) -> dict[str, Any]:
        self.store.delete_job_record(job_id); self.log("job.record-deleted", {"job_id": job_id}); return {"job_id": job_id, "deleted": True, "knowledge_files_deleted": False}
    def list_prepared(self) -> list[dict[str, str]]:
        rows = prepared_pdf.list_prepared(self.vault); now = datetime.now().astimezone().isoformat(timespec="seconds")
        with self.store.lock:
            for item in rows:
                self.store.connection.execute("INSERT INTO change_sets(change_set_id, prepared_id, state, bundle_hash, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(change_set_id) DO UPDATE SET state=excluded.state, bundle_hash=excluded.bundle_hash, updated_at=excluded.updated_at", (item["prepared_id"], item["prepared_id"], item["state"], item["bundle_hash"], now, now))
            self.store.connection.commit()
        return rows
    def inspect_prepared(self, prepared_id: str) -> str: return prepared_pdf.inspect_bundle(self.vault, prepared_id)
    def apply_prepared(self, prepared_id: str) -> str:
        linked = next((row for row in self.store.list_jobs() if row.get("prepared_id") == prepared_id and row["state"] == "awaiting_confirmation"), None)
        if linked: self.store.update_job(str(linked["job_id"]), "applying", stage="applying", progress=100)
        try:
            result = str(prepared_pdf.apply_prepared(self.vault, prepared_id))
        except Exception:
            if linked: self.store.update_job(str(linked["job_id"]), "awaiting_confirmation", stage="awaiting_confirmation", progress=100)
            raise
        if linked: self.store.update_job(str(linked["job_id"]), "completed", result={"apply_result": result, "prepared_id": prepared_id}, stage="completed", progress=100, prepared_id=prepared_id)
        self.log("prepared.applied", {"prepared_id": prepared_id}); return result
    def list_reviews(self) -> list[dict[str, Any]]:
        self.sync_artifacts(); return [{**item, "path": str(item["path"])} for item in review.scan_artifacts(self.vault)]
    def show_review(self, artifact_id: str) -> str: return review.review_packet(self.vault, artifact_id)
    def diff_review(self, artifact_id: str) -> str: return review.artifact_diff(self.vault, artifact_id)
    def transition_review(self, artifact_id: str, action: str, reason: str = "") -> str:
        result = str(review.transition(self.vault, artifact_id, action, reason)); self.log("review.transition", {"artifact_id": artifact_id, "action": action}); return result
    def today_learning(self) -> dict[str, Any]: return learning.daily_plan(self.vault)

    LEARNING_EVENT_TYPES = {
        "recommendation_exposed", "recommendation_clicked", "recommendation_started", "recommendation_completed", "recommendation_snoozed",
        "recommendation_added_to_plan", "recommendation_feedback", "recommendation_dismissed",
        "study_session_started", "study_session_paused", "study_session_resumed",
        "study_session_completed", "study_session_abandoned", "quiz_started",
        "quiz_answered", "quiz_completed", "hint_opened", "related_note_opened",
        "source_opened", "lesson_error_reported", "plan_task_completed", "plan_task_postponed", "assistant_topic_revisited",
    }
    LEARNING_PAYLOAD_KEYS = {"action", "category", "route", "correctness", "hintUsed", "reason", "errorCode", "sourceType", "verificationGrade"}

    def record_learning_events(self, body: dict[str, Any]) -> dict[str, Any]:
        if int(body.get("schemaVersion", 1)) != 1:
            raise ValueError("learning_event_schema_mismatch")
        raw_events = body.get("events", [])
        if not isinstance(raw_events, list) or not raw_events or len(raw_events) > 100:
            raise ValueError("learning_events_batch_must_contain_1_to_100_items")
        events: list[dict[str, Any]] = []
        for raw in raw_events:
            if not isinstance(raw, dict):
                raise ValueError("learning_event_must_be_an_object")
            event_id = str(raw.get("id", ""))
            event_type = str(raw.get("eventType", ""))
            if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", event_id):
                raise ValueError("learning_event_id_invalid")
            if event_type not in self.LEARNING_EVENT_TYPES:
                raise ValueError("learning_event_type_invalid")
            if int(raw.get("schemaVersion", 0)) != 1:
                raise ValueError("learning_event_schema_mismatch")
            payload = raw.get("payload", {})
            if not isinstance(payload, dict) or not set(payload).issubset(self.LEARNING_PAYLOAD_KEYS):
                raise ValueError("learning_event_payload_contains_private_or_unsupported_fields")
            if len(json.dumps(payload, ensure_ascii=False)) > 2048:
                raise ValueError("learning_event_payload_too_large")
            created_at = str(raw.get("createdAt", ""))
            try: datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            except ValueError as error: raise ValueError("learning_event_created_at_invalid") from error
            duration = raw.get("durationMs")
            if duration is not None and (not isinstance(duration, int) or duration < 0 or duration > 86_400_000):
                raise ValueError("learning_event_duration_invalid")
            events.append({
                "id": event_id, "eventType": event_type,
                "subjectType": str(raw.get("subjectType", ""))[:64], "subjectId": str(raw.get("subjectId", ""))[:256],
                "topic": str(raw.get("topic", ""))[:160] or None, "domain": str(raw.get("domain", ""))[:120] or None,
                "recommendationId": str(raw.get("recommendationId", ""))[:256] or None,
                "sessionId": str(raw.get("sessionId", ""))[:256] or None,
                "durationMs": duration, "payload": payload, "createdAt": created_at, "schemaVersion": 1,
            })
        inserted, duplicates = self.store.append_learning_events(events)
        self.log("learning.events-recorded", {"inserted": inserted, "duplicates": duplicates})
        return {"ok": True, "inserted": inserted, "duplicates": duplicates, "schemaVersion": 1}

    def learner_profile(self, *, rebuild: bool = False) -> dict[str, Any]:
        events = self.store.list_learning_events(5000)
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        dates = sorted({str(item["created_at"])[:10] for item in events if item.get("created_at")})
        count, days = len(events), len(dates)
        weight = min(.05, count / 400) if count < 20 else min(.10, .05 + (count - 20) / 1600) if count <= 100 or days < 7 else .15
        by_type: dict[str, int] = {}
        for item in events: by_type[str(item["event_type"])] = by_type.get(str(item["event_type"]), 0) + 1
        exposed = max(1, by_type.get("recommendation_exposed", 0))
        started = max(1, by_type.get("study_session_started", 0))
        feature_rows = [
            ("recommendation_click_rate", "global", by_type.get("recommendation_clicked", 0) / exposed, exposed),
            ("recommendation_completion_rate", "global", by_type.get("study_session_completed", 0) / started, started),
            ("hint_dependency_rate", "global", by_type.get("hint_opened", 0) / started, started),
        ]
        for domain in sorted({str(item.get("domain") or "") for item in events if item.get("domain")}):
            relevant = [item for item in events if item.get("domain") == domain]
            starts = sum(item["event_type"] == "study_session_started" for item in relevant)
            completed = sum(item["event_type"] == "study_session_completed" for item in relevant)
            feature_rows.append(("topic_completion_rate", domain, completed / max(1, starts), max(1, starts)))
        window_start = dates[0] if dates else now[:10]
        features = [{
            "key": key, "scope": scope, "value": round(value, 4),
            "confidence": round(min(.95, evidence / 100), 3), "evidenceCount": evidence,
            "windowStart": window_start, "windowEnd": now[:10], "updatedAt": now, "source": "inferred",
        } for key, scope, value, evidence in feature_rows]
        if rebuild or events: self.store.replace_inferred_features(features)
        return {"schemaVersion": 1, "eventCount": count, "coveredDays": days, "behaviorWeight": round(weight, 4), "features": self.store.list_learner_features(), "lastRebuiltAt": now}

    def clear_learning_data(self, scope: str) -> dict[str, Any]:
        if scope not in {"recent-7-days", "all"}: raise ValueError("learning_data_scope_invalid")
        since = (date.today() - timedelta(days=7)).isoformat() if scope == "recent-7-days" else ""
        deleted = self.store.clear_learning_events(since)
        profile = self.learner_profile(rebuild=True)
        self.log("learning.data-cleared", {"scope": scope, "deleted": deleted})
        return {"ok": True, "scope": scope, "deleted": deleted, "profile": profile}

    def daily_dashboard(self) -> dict[str, Any]:
        dashboard = self.dashboard()
        today_plan = self.build_today()
        planned_items = list(today_plan.get("items") or [])
        dashboard["summary"] = {**dashboard["summary"],
            "suggested_minutes": sum(int(item.get("minutes") or 0) for item in planned_items if item.get("state") != "skipped"),
            "completed_minutes": sum(int(item.get("minutes") or 0) for item in planned_items if item.get("state") == "completed"),
            "review_count": sum((item.get("recommendation") or {}).get("kind") == "review" for item in planned_items),
            "learn_count": sum((item.get("recommendation") or {}).get("kind") in {"learn", "explore"} for item in planned_items),
        }
        recent_adjustment = self.store.latest_daily_adjustment()
        profiles = self.models.list()
        routes = self.models.routing()
        model_route = next((routes.get(name) for name in ("curriculum_planner", "daily_knowledge_generator") if routes.get(name, {}).get("profileId")), {})
        profile = next((item for item in profiles if item["id"] == model_route.get("profileId")), None)
        return {
            **dashboard, "schemaVersion": 1, "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
            "todayPlan": today_plan, "directions": self.learning_directions(),
            "todayConstraints": self.store.get_setting("today_constraints", {}),
            "recentAdjustment": ({
                "actionId": recent_adjustment["actionId"], "type": recent_adjustment["type"],
                "beforeMinutes": sum(int(item["minutes"]) for item in recent_adjustment["before"]["items"]),
                "afterMinutes": sum(int(item["minutes"]) for item in recent_adjustment["after"]["items"]),
                "undoAvailable": True,
            } if recent_adjustment else None),
            "learnerProfile": self.learner_profile(),
            "runtime": {"ranking": "typescript-domain-v1", "persistence": "python-sqlite-adapter-v1", "modelConfigured": bool(profile and profile.get("configured")), "provider": profile.get("displayName") if profile else None},
        }
    def list_recommendations(self) -> list[dict[str, Any]]:
        items = recommendations.build(self.vault, self.store, self.list_prepared())
        generated_ids = {str(item["id"]) for item in items}
        for persisted in self.store.list_persisted_recommendations("active"):
            if str(persisted["id"]) not in generated_ids:
                items.append(persisted)
        for candidate in self.store.list_curriculum_candidates("active"):
            verification = candidate.get("verification", {})
            grade = str(verification.get("grade", "C"))
            # Deterministic fallback candidates remain useful for the learning
            # route but may not masquerade as model-generated daily knowledge.
            model_generated = bool(candidate.get("model_profile_id"))
            scores = candidate.get("scores", {})
            score = round(100 * (.35 * float(scores.get("gap", 0)) + .3 * float(scores.get("mainline", 0)) + .2 * float(scores.get("reuse", 0)) + .15 * float(candidate.get("confidence", 0))), 2)
            recommendation = {
                "id": candidate["candidate_id"], "title": candidate["title"], "kind": "explore",
                "estimatedMinutes": candidate["estimated_minutes"], "score": score,
                "reason": candidate["why_now"], "reasonDetails": [
                    "尚未成为正式笔记", f"候选类型：{candidate['kind']}",
                    f"生成依据：{candidate.get('generated_by', 'deterministic-curriculum-v1')}",
                ],
                "prerequisites": [{"title": title, "path": ""} for title in candidate.get("prerequisites", [])],
                "relatedNotes": [{"title": title, "path": ""} for title in candidate.get("related_topics", [])],
                "microConcepts": [], "sourcePath": "", "domain": candidate["domain"],
                "route": candidate["route"], "dueState": "candidate", "mastery": 0,
                "actions": ["later", "tomorrow", "weekend", "favorite", "not_interested", "start"],
                "candidate": model_generated, "candidateKind": candidate["kind"], "generatedAt": candidate["generated_at"],
                "confidence": float(candidate.get("confidence", 0)), "verificationGrade": grade,
                "verificationScore": float(verification.get("score", 0)), "verification": verification,
                "sourceBasis": list(candidate.get("basis", {}).get("sourceBasis", [])),
                "behaviorBasis": list(candidate.get("basis", {}).get("behaviorBasis", [])),
                "learningOutcomes": list(candidate.get("learning_outcomes", [])),
                "gapScore": float(scores.get("gap", 0)) * 100,
                "behaviorScore": float(scores.get("behavior", .5)) * 100,
                "difficultyScore": {"easy": 1, "medium": 2, "hard": 3}.get(str(candidate.get("difficulty")), 2),
            }
            items.extend(study_workspace.split_candidate(recommendation))
        existing_ids = {str(item["id"]) for item in items}
        for direction in self.learning_directions():
            if str(direction["id"]) in existing_ids:
                continue
            priority_score = {"高优先级": 76.0, "中优先级": 64.0, "探索性": 52.0}.get(str(direction.get("priority")), 58.0)
            items.append({
                "id": direction["id"], "title": direction["title"], "kind": "explore",
                "estimatedMinutes": int(direction.get("estimatedMinutes", 15)), "score": priority_score,
                "reason": "；".join(direction.get("why", [])), "reasonDetails": list(direction.get("why", [])),
                "prerequisites": [{"title": title, "path": ""} for title in direction.get("prerequisites", [])],
                "relatedNotes": [{"title": title, "path": ""} for title in direction.get("connections", [])],
                "microConcepts": [], "sourcePath": "", "domain": str((direction.get("connections") or ["学习方向"])[0]),
                "route": direction.get("route", "mainline"), "dueState": "direction", "mastery": 0,
                "actions": ["tomorrow", "weekend", "not_interested", "start"],
                "direction": True, "horizon": direction.get("horizon"), "priority": direction.get("priority"),
                "confidenceLabel": direction.get("confidenceLabel"), "noveltyBasis": direction.get("noveltyBasis"),
                "isNewKnowledge": bool(direction.get("isNewKnowledge")), "sourceQuality": direction.get("sourceQuality"),
                "conversationScore": min(100, float(direction.get("confidence", 0)) * 100),
            })
        return sorted(items, key=lambda item: (-float(item.get("score", 0)), int(item.get("estimatedMinutes", 10)), str(item.get("title", ""))))

    def add_artifact_to_today(self, artifact_id: str) -> dict[str, Any]:
        artifact = self.intake.get_artifact(artifact_id)
        recommendation_id = f"rec-assistant-{hashlib.sha256(artifact_id.encode()).hexdigest()[:16]}"
        existing = self.store.get_persisted_recommendation(recommendation_id)
        if existing and existing.get("state") == "active":
            return {"recommendation": existing, "duplicate": True, "undo_available": True}
        payload = artifact.get("payload") or {}
        item = {
            "id": recommendation_id, "title": artifact["title"], "kind": "learn",
            "estimatedMinutes": max(5, min(180, int(payload.get("estimatedMinutes") or 12))),
            "score": 78.0, "state": "active", "artifactId": artifact_id,
            "reason": "由助手生成并由你加入今日，适合继续完成当前学习目标。",
            "reasonDetails": ["来自当前助手任务", "尚未自动写入正式知识"],
            "prerequisites": [{"title": str(title), "path": ""} for title in payload.get("prerequisites", [])[:5]],
            "relatedNotes": [{"title": str(item.get("title") or "相关笔记"), "path": str(item.get("path") or "")} for item in payload.get("sources", [])[:5]],
            "microConcepts": [], "quizPreview": {"question": str((payload.get("quizPreview") or ["请复述核心定义与适用条件。"])[:1][0]), "answerHint": "先讲直觉，再补充条件。"},
            "sourcePath": str((payload.get("sources") or [{}])[0].get("path") or ""),
            "domain": str(payload.get("domain") or "当前学习主线"), "route": "mainline",
            "dueState": "assistant", "mastery": 0,
            "actions": ["later", "tomorrow", "weekend", "favorite", "not_interested", "start"],
        }
        stored = self.store.upsert_recommendation(item)
        self.log("assistant.today-added", {"artifact_id": artifact_id, "recommendation_id": recommendation_id})
        return {"recommendation": stored, "duplicate": False, "undo_available": True}

    def undo_artifact_today(self, artifact_id: str) -> dict[str, Any]:
        recommendation_id = f"rec-assistant-{hashlib.sha256(artifact_id.encode()).hexdigest()[:16]}"
        removed = self.store.set_persisted_recommendation_state(recommendation_id, "removed")
        self.log("assistant.today-undone", {"artifact_id": artifact_id, "recommendation_id": recommendation_id})
        return {"recommendation_id": recommendation_id, "removed": removed, "undo_available": False}

    def assistant_context(self, artifact_id: str = "", conversation_id: str = "") -> dict[str, Any]:
        artifact = self.intake.get_artifact(artifact_id) if artifact_id else None
        resolved_conversation = conversation_id or str((artifact or {}).get("conversationId") or "")
        group = self.intake.latest_artifact_group(resolved_conversation) if resolved_conversation else None
        task = self.intake.latest_task_thread(resolved_conversation) if resolved_conversation else None
        payload = (artifact or {}).get("payload") or {}
        sources = list(payload.get("sources") or payload.get("evidence") or [])[:5]
        materials = self.intake.list_artifacts("material", conversation_id=resolved_conversation, limit=5)["items"] if resolved_conversation else []
        today_recommendation = None
        if artifact_id:
            recommendation_id = f"rec-assistant-{hashlib.sha256(artifact_id.encode()).hexdigest()[:16]}"
            today_recommendation = self.store.get_persisted_recommendation(recommendation_id)
        focus = self.store.get_conversation_focus(resolved_conversation) if resolved_conversation else None
        latest_bundle = None; latest_plan = None
        if resolved_conversation:
            with self.store.lock:
                bundle_row = self.store.connection.execute(
                    "SELECT id FROM material_bundles WHERE conversation_id=? ORDER BY updated_at DESC LIMIT 1", (resolved_conversation,),
                ).fetchone()
                plan_row = self.store.connection.execute(
                    """SELECT p.id FROM organization_plans p JOIN material_bundles b ON b.id=p.bundle_id
                       WHERE b.conversation_id=? ORDER BY p.updated_at DESC LIMIT 1""", (resolved_conversation,),
                ).fetchone()
            latest_bundle = self.store.get_material_bundle(str(bundle_row["id"])) if bundle_row else None
            latest_plan = self.store.get_organization_plan(str(plan_row["id"])) if plan_row else None
        return {
            "task": task, "artifactGroup": group,
            "focus": focus, "currentUnderstanding": (latest_bundle or {}).get("understanding"),
            "organizationPlan": latest_plan,
            "inToday": bool(today_recommendation and today_recommendation.get("state") == "active"),
            "relatedNotes": [{"title": str(item.get("title") or "相关笔记"), "path": str(item.get("path") or "")} for item in sources],
            "recentMaterials": [{"id": item["id"], "title": item["title"], "status": item["status"]} for item in materials],
            "recommendedActions": [
                {"id": "today", "label": "加入今日", "target": "today"},
                {"id": "path", "label": "生成学习路径", "target": "plan"},
                {"id": "follow-up", "label": "继续追问", "target": "assistant"},
            ],
        }

    def conversation_focus(self, conversation_id: str) -> dict[str, Any]:
        focus = self.store.get_conversation_focus(conversation_id)
        if not focus: raise ValueError("conversation_focus_not_found")
        return focus

    def material_bundle(self, bundle_id: str) -> dict[str, Any]:
        return self.store.get_material_bundle(bundle_id)

    def organization_plan(self, plan_id: str) -> dict[str, Any]:
        return self.store.get_organization_plan(plan_id)

    def get_recommendation(self, recommendation_id: str) -> dict[str, Any]:
        item = next((item for item in self.list_recommendations() if item["id"] == recommendation_id), None)
        if not item: raise RuntimeError(f"Recommendation not found: {recommendation_id}")
        return item

    def recommendation_action(self, recommendation_id: str, action: str, details: dict[str, Any]) -> dict[str, Any]:
        if action == "undo":
            if not self.store.undo_recommendation_feedback(recommendation_id): raise RuntimeError("No recommendation feedback to undo")
            self.log("recommendation.undo", {"recommendation_id": recommendation_id})
            return {"recommendation_id": recommendation_id, "action": action, "undo_available": False}
        rec = self.get_recommendation(recommendation_id)
        candidate_root = str(rec.get("candidateRootId") or recommendation_id)
        if candidate_root.startswith("candidate-") and action in {"later", "tomorrow", "weekend", "favorite", "not_interested"}:
            cooldown = (date.today() + timedelta(days=7)).isoformat() if action in {"later", "not_interested"} else None
            self.store.curriculum_action(candidate_root, action, cooldown)
        details = {**details, "domain": rec.get("domain", ""), "title": rec.get("title", "")}
        recommendations.record_action(self.store, recommendation_id, action, details)
        self.log("recommendation.action", {"recommendation_id": recommendation_id, "action": action})
        return {"recommendation_id": recommendation_id, "action": action, "undo_available": True}

    def dashboard(self) -> dict[str, Any]:
        recs = self.list_recommendations(); jobs = self.list_jobs(); prepared = self.list_prepared(); reviews = self.list_reviews()
        active = [job for job in jobs if job["state"] in {"queued", "running", "prepared", "awaiting_confirmation", "applying"}]
        failed = [job for job in jobs if job["state"] == "failed"]
        return {
            "date": date.today().isoformat(), "recommendations": recs,
            "summary": {
                "suggested_minutes": sum(int(item["estimatedMinutes"]) for item in recs[:3]),
                "completed_minutes": 0,
                "review_count": sum(item["kind"] == "review" for item in recs),
                "learn_count": sum(item["kind"] in {"learn", "explore"} for item in recs),
                "prepared_count": sum(item.get("state") == "prepared" for item in prepared),
                "review_count_pending": sum(item.get("review_state") == "pending" for item in reviews),
                "failed_count": len(failed), "active_job_count": len(active),
            },
            "active_jobs": active[:3], "failed_jobs": failed[:3],
        }

    def current_plan(self) -> dict[str, Any]:
        feedback = self.store.recommendation_feedback(); weekend: list[dict[str, Any]] = []; tomorrow: list[dict[str, Any]] = []
        for row in feedback:
            if row["action"] not in {"weekend", "tomorrow"}: continue
            details = json.loads(row["details_json"] or "{}")
            target = weekend if row["action"] == "weekend" else tomorrow
            if not any(item["recommendation_id"] == row["recommendation_id"] for item in target): target.append({"recommendation_id": row["recommendation_id"], "title": details.get("title", "学习任务"), "domain": details.get("domain", "")})
        weekly: list[dict[str, Any]] = []
        root = self.vault / "30-Learning/Weekly"
        if root.exists():
            for path in sorted(root.glob("*.md"), reverse=True)[:4]:
                meta = ingest_pdf.parse_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
                weekly.append({"title": path.stem, "path": str(path.relative_to(self.vault)), "status": str(meta.get("status", ""))})
        return {"tomorrow": tomorrow, "weekend": weekend, "weekly": weekly, "proposals": self.store.list_plan_proposals(), "mainline_ratio": 70, "branch_ratio": 30}

    def patch_plan_task(self, task_id: str, body: dict[str, Any]) -> dict[str, Any]:
        allowed = {"date", "minutes", "state", "route", "position"}
        patch = {key: value for key, value in body.items() if key in allowed}
        if not patch:
            raise ValueError("plan_task_patch_required")
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        with self.store.lock:
            rows = self.store.connection.execute("SELECT id, tasks_json FROM plan_proposals").fetchall()
            found = None
            for row in rows:
                tasks = json.loads(row["tasks_json"])
                for task in tasks:
                    if str(task.get("id")) == task_id:
                        task.update(patch); found = dict(task)
                        break
                if found:
                    if "position" in patch:
                        tasks.sort(key=lambda item: (int(item.get("position", 10_000)), str(item.get("date", ""))))
                    self.store.connection.execute("UPDATE plan_proposals SET tasks_json=?, updated_at=? WHERE id=?", (json.dumps(tasks, ensure_ascii=False), now, row["id"]))
                    self.store.connection.commit(); break
        if not found:
            raise ValueError("plan_task_not_found")
        self.log("plan-task.updated", {"task_id": task_id, "fields": sorted(patch)})
        return found

    def list_model_profiles(self) -> list[dict[str, Any]]: return self.models.list()
    def save_model_profile(self, data: dict[str, Any], profile_id: str | None = None) -> dict[str, Any]:
        profile = self.models.save(data, profile_id); self.log("model-profile.saved", {"profile_id": profile["id"], "configured": profile["configured"]}); return profile
    def delete_model_profile(self, profile_id: str) -> dict[str, Any]:
        self.models.delete(profile_id); self.log("model-profile.deleted", {"profile_id": profile_id}); return {"id": profile_id, "deleted": True}
    def test_model_profile(self, profile_id: str) -> dict[str, Any]: return self.models.test(profile_id)
    def list_profile_models(self, profile_id: str) -> list[str]: return self.models.models(profile_id)
    def model_routing(self) -> dict[str, dict[str, str | None]]: return self.models.routing()
    def set_model_routing(self, routes: dict[str, dict[str, Any]]) -> dict[str, dict[str, str | None]]: return self.models.set_routing(routes)

    def model_capabilities(self, profile_id: str = "") -> dict[str, Any]:
        return self.model_proxy.capabilities(profile_id)

    def probe_model_capabilities(self, profile_id: str) -> dict[str, Any]:
        return self.model_proxy.probe(profile_id)

    def stream_model_proxy(self, body: dict[str, Any]) -> Iterator[dict[str, Any]]:
        return self.model_proxy.stream(body)

    def tool_contracts(self) -> dict[str, Any]:
        # Pi receives a stable, provider-neutral tool surface. Legacy proposal
        # tools stay available to the legacy runtime but are not exposed here.
        permissions = {"read_only"}
        items = [
            item
            for item in self.tools.definitions()
            if str(item.get("permission_level") or "") in permissions
        ]
        markdown_write = {
            "type": "object",
            "properties": {
                "path": {"type": "string", "minLength": 1, "maxLength": 500},
                "content": {"type": "string", "minLength": 1, "maxLength": 100000},
                "category": {"type": "string", "maxLength": 80},
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        }
        common = {
            "output_schema": {"type": "object", "additionalProperties": True},
            "uses_network": False,
            "mutates_state": True,
            "timeout_seconds": 30,
            "permission_level": "proposal",
            "cancellable": False,
            "max_result_bytes": 32000,
        }
        items.extend([
            {
                "name": "get_current_note",
                "description": "读取当前 Turn 明确提供的活动笔记；path 必须来自 zhixu_turn_context.currentNote。",
                "input_schema": {
                    "type": "object",
                    "properties": {"path": {"type": "string", "minLength": 1, "maxLength": 500}},
                    "required": ["path"],
                    "additionalProperties": False,
                },
                "output_schema": {"type": "object", "additionalProperties": True},
                "uses_network": False,
                "mutates_state": False,
                "timeout_seconds": 20,
                "permission_level": "read_only",
                "idempotent": True,
                "cancellable": False,
                "max_result_bytes": 32000,
            },
            {
                "name": "read_vault_note",
                "description": "分页读取搜索结果中的安全 Markdown 笔记正文；长笔记按 next_offset 继续读取，修改或完整复制前必须读到 truncated=false。",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "minLength": 1, "maxLength": 500},
                        "offset": {"type": "integer", "minimum": 0, "maximum": 10_000_000},
                        "max_chars": {"type": "integer", "minimum": 100, "maximum": 50_000},
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
                "output_schema": {"type": "object", "additionalProperties": True},
                "uses_network": False,
                "mutates_state": False,
                "timeout_seconds": 20,
                "permission_level": "read_only",
                "idempotent": True,
                "cancellable": False,
                "max_result_bytes": 200000,
            },
            {
                "name": "find_related_notes",
                "description": "读取指定笔记的出链、反向链接和图谱相关笔记。",
                "input_schema": {
                    "type": "object",
                    "properties": {"path": {"type": "string", "minLength": 1, "maxLength": 500}},
                    "required": ["path"],
                    "additionalProperties": False,
                },
                "output_schema": {"type": "object", "additionalProperties": True},
                "uses_network": False,
                "mutates_state": False,
                "timeout_seconds": 20,
                "permission_level": "read_only",
                "idempotent": True,
                "cancellable": False,
                "max_result_bytes": 32000,
            },
            {
                "name": "search_public_web",
                "description": "在当前 Turn 已授权联网时搜索公开网页；结果只是待验证外部资料。",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "minLength": 1, "maxLength": 500},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 10},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
                "output_schema": {"type": "object", "additionalProperties": True},
                "uses_network": True,
                "mutates_state": False,
                "timeout_seconds": 45,
                "permission_level": "read_only",
                "idempotent": True,
                "cancellable": True,
                "max_result_bytes": 32000,
            },
            {
                "name": "fetch_public_url",
                "description": "在当前 Turn 已授权联网时安全抓取一个公开 URL；正文以不可信证据边界返回。",
                "input_schema": {
                    "type": "object",
                    "properties": {"url": {"type": "string", "minLength": 8, "maxLength": 2000}},
                    "required": ["url"],
                    "additionalProperties": False,
                },
                "output_schema": {"type": "object", "additionalProperties": True},
                "uses_network": True,
                "mutates_state": False,
                "timeout_seconds": 45,
                "permission_level": "read_only",
                "idempotent": True,
                "cancellable": True,
                "max_result_bytes": 48000,
            },
            {
                **common,
                "name": "plan_vault_change",
                "description": "为当前任务生成内部 Markdown 写入计划和事后 Diff；不会等待人工审批。",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "minLength": 1, "maxLength": 200},
                        "writes": {"type": "array", "minItems": 1, "maxItems": 10, "items": markdown_write},
                    },
                    "required": ["title", "writes"],
                    "additionalProperties": False,
                },
                "idempotent": False,
            },
            {
                **common,
                "name": "plan_vault_copy",
                "description": "把已有安全 Markdown 笔记完整复制到 01-Inbox 或 20-Knowledge/Drafts 下的新目录，并生成一个或多个内部 Change Set。正文由 Harness 本地读取，禁止模型先读取再把全文塞进工具参数。返回 change_sets 后逐个调用 apply_vault_change。",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "minLength": 1, "maxLength": 200},
                        "source_paths": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 50,
                            "items": {"type": "string", "minLength": 1, "maxLength": 500},
                        },
                        "destination_root": {"type": "string", "minLength": 1, "maxLength": 500},
                    },
                    "required": ["title", "source_paths", "destination_root"],
                    "additionalProperties": False,
                },
                "idempotent": False,
            },
            {
                **common,
                "name": "organize_vault_notes",
                "description": (
                    "在当前 Task Authorization 内批量创建安全 Vault 子目录并移动普通 Markdown 笔记；"
                    "自动创建目标父目录，整批事务化、失败完整回滚且可撤销。"
                    "reviewed/core、受保护文件和 Vault 外路径永远禁止移动；"
                    "10-Sources 仅允许在明确授权后整理非正式 source-index 草稿。"
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "minLength": 1, "maxLength": 200},
                        "directories": {
                            "type": "array",
                            "maxItems": 50,
                            "items": {"type": "string", "minLength": 1, "maxLength": 500},
                        },
                        "moves": {
                            "type": "array",
                            "maxItems": 50,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "source_path": {"type": "string", "minLength": 1, "maxLength": 500},
                                    "target_path": {"type": "string", "minLength": 1, "maxLength": 500},
                                    "destination_path": {
                                        "type": "string",
                                        "minLength": 1,
                                        "maxLength": 500,
                                        "description": "已弃用别名；新调用使用 target_path",
                                    },
                                },
                                "required": ["source_path"],
                                "anyOf": [
                                    {"required": ["target_path"]},
                                    {"required": ["destination_path"]},
                                ],
                                "additionalProperties": False,
                            },
                        },
                        "remove_empty_source_dirs": {"type": "boolean", "default": False},
                    },
                    "additionalProperties": False,
                },
                "idempotent": False,
            },
            {
                **common,
                "name": "apply_vault_change",
                "description": "在当前 Task Authorization 内快照、原子应用并校验内部 Change Set；返回可撤销 Action。",
                "input_schema": {
                    "type": "object",
                    "properties": {"change_set_id": {"type": "string", "minLength": 1, "maxLength": 128}},
                    "required": ["change_set_id"],
                    "additionalProperties": False,
                },
                "idempotent": True,
            },
            {
                **common,
                "name": "undo_agent_action",
                "description": "冲突安全地撤销一个由当前 Harness 完成的可逆 Vault Action。",
                "input_schema": {
                    "type": "object",
                    "properties": {"action_id": {"type": "string", "minLength": 1, "maxLength": 128}},
                    "required": ["action_id"],
                    "additionalProperties": False,
                },
                "idempotent": True,
            },
        ])
        developer_common = {
            "output_schema": {"type": "object", "additionalProperties": True},
            "uses_network": False,
            "mutates_state": True,
            "timeout_seconds": 310,
            "permission_level": "proposal",
            "idempotent": False,
            "cancellable": True,
            "max_result_bytes": 64000,
        }
        workspace_id = {"type": "string", "minLength": 1, "maxLength": 128}
        run_payload = {
            "type": "object",
            "properties": {
                "workspace_id": workspace_id,
                "cwd": {"type": "string", "maxLength": 500, "default": "."},
                "timeoutMs": {"type": "integer", "minimum": 100, "maximum": 300000},
                "networkPolicy": {"type": "string", "enum": ["deny"]},
                "expectedOutputs": {"type": "array", "maxItems": 50, "items": {"type": "string", "maxLength": 500}},
            },
            "required": ["workspace_id"],
            "additionalProperties": False,
        }
        items.extend([
            {**developer_common, "name": "create_git_worktree", "description": "为当前开发任务创建隔离的 Git worktree 和任务分支。", "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
            {**developer_common, "mutates_state": False, "idempotent": True, "name": "read_workspace_file", "description": "读取当前受控开发 worktree 内的 UTF-8 文件。", "input_schema": {"type": "object", "properties": {"workspace_id": workspace_id, "path": {"type": "string", "minLength": 1, "maxLength": 500}}, "required": ["workspace_id", "path"], "additionalProperties": False}},
            {**developer_common, "name": "write_workspace_file", "description": "在当前受控 worktree 内原子写入文件，可带 base hash 防止覆盖并发修改。", "input_schema": {"type": "object", "properties": {"workspace_id": workspace_id, "path": {"type": "string", "minLength": 1, "maxLength": 500}, "content": {"type": "string", "maxLength": 1000000}, "base_hash": {"type": "string", "maxLength": 128}}, "required": ["workspace_id", "path", "content"], "additionalProperties": False}},
            {**developer_common, "name": "run_command", "description": "用结构化 argv 在 macOS sandbox 内运行白名单开发命令；默认断网且不继承密钥。", "input_schema": {**run_payload, "properties": {**run_payload["properties"], "executable": {"type": "string", "enum": ["git", "npm", "node", "python", "python3", "pytest", "rg", "ls", "find", "make"]}, "args": {"type": "array", "maxItems": 100, "items": {"type": "string", "maxLength": 4000}}}, "required": ["workspace_id", "executable", "args"]}},
            {**developer_common, "name": "run_bash", "description": "仅在当前 worktree 内运行受 macOS sandbox 和永久拒绝列表约束的 zsh 脚本。优先使用 run_command。", "input_schema": {**run_payload, "properties": {**run_payload["properties"], "script": {"type": "string", "minLength": 1, "maxLength": 40000}, "riskExplanation": {"type": "string", "maxLength": 2000}}, "required": ["workspace_id", "script", "riskExplanation"]}},
            {**developer_common, "mutates_state": False, "idempotent": True, "name": "git_status", "description": "查看受控任务分支状态。", "input_schema": {"type": "object", "properties": {"workspace_id": workspace_id}, "required": ["workspace_id"], "additionalProperties": False}},
            {**developer_common, "mutates_state": False, "idempotent": True, "name": "git_diff", "description": "查看受控任务分支未提交 Diff。", "input_schema": {"type": "object", "properties": {"workspace_id": workspace_id}, "required": ["workspace_id"], "additionalProperties": False}},
            {**developer_common, "name": "git_commit", "description": "在受控任务分支创建本地提交，不推送网络。", "input_schema": {"type": "object", "properties": {"workspace_id": workspace_id, "message": {"type": "string", "minLength": 1, "maxLength": 200}}, "required": ["workspace_id", "message"], "additionalProperties": False}},
            {**developer_common, "name": "merge_task_branch", "description": "仅在主项目和任务分支都干净时，将已提交任务分支合并到当前项目；失败自动 abort。", "input_schema": {"type": "object", "properties": {"workspace_id": workspace_id}, "required": ["workspace_id"], "additionalProperties": False}},
            {**developer_common, "name": "activate_runtime_upgrade", "description": "验证已合并任务并请求 Obsidian 进程管理器执行固定检查、构建、安装、Runtime 重启和健康检查；失败时自动调用 rollback_task_branch。", "input_schema": {"type": "object", "properties": {"workspace_id": workspace_id}, "required": ["workspace_id"], "additionalProperties": False}},
            {**developer_common, "name": "rollback_task_branch", "description": "删除当前任务 worktree 和任务分支，撤销未合并的开发任务。", "input_schema": {"type": "object", "properties": {"workspace_id": workspace_id}, "required": ["workspace_id"], "additionalProperties": False}},
            {**developer_common, "mutates_state": False, "idempotent": True, "name": "validate_skill_draft", "description": "校验受控 worktree 中的 Skill Draft，不启用、不读取密钥。", "input_schema": {"type": "object", "properties": {"workspace_id": workspace_id, "path": {"type": "string", "minLength": 1, "maxLength": 500}}, "required": ["workspace_id", "path"], "additionalProperties": False}},
            {**developer_common, "mutates_state": False, "idempotent": True, "name": "validate_mcp_server", "description": "校验受控 worktree 中的 MCP manifest 与隔离权限。", "input_schema": {"type": "object", "properties": {"workspace_id": workspace_id, "path": {"type": "string", "minLength": 1, "maxLength": 500}}, "required": ["workspace_id", "path"], "additionalProperties": False}},
            {**developer_common, "mutates_state": False, "idempotent": False, "name": "probe_mcp_server", "description": "在断网、最小环境的 sandbox 中临时启动 MCP 并执行 initialize 与 tools/list。", "input_schema": {"type": "object", "properties": {"workspace_id": workspace_id, "path": {"type": "string", "minLength": 1, "maxLength": 500}}, "required": ["workspace_id", "path"], "additionalProperties": False}},
        ])
        return {"schemaVersion": 1, "items": items}

    def register_task_authorization(self, body: dict[str, Any]) -> dict[str, Any]:
        authorization = dict(body.get("taskAuthorization") or {})
        authorization["conversationId"] = str(
            body.get("conversationId") or authorization.get("sessionId") or ""
        )
        authorization["model"] = str(body.get("model") or "")
        created = self.task_authorizations.create(authorization)
        conversation_id = str(authorization.get("conversationId") or "")
        objective = str(authorization.get("objective") or "").strip()
        source_message_id = str(authorization.get("sourceMessageId") or "")
        if conversation_id and objective and source_message_id:
            self.intake.ensure_runtime_conversation(conversation_id, objective)
            self.intake.append_message(
                conversation_id,
                "user",
                objective,
                "pi-runtime",
                message_id=source_message_id,
            )
        return {"taskAuthorization": created}

    def expand_task_authorization(
        self,
        authorization_id: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        """Apply one typed, explicit grant to the same active Run."""
        run_id = str(body.get("runId") or "")
        mode = str(body.get("mode") or "once")
        writes = body.get("writes")
        organization = body.get("organization")
        capability = body.get("capability")
        if isinstance(capability, dict):
            if not isinstance(organization, dict) and isinstance(capability.get("organization"), dict):
                organization = capability.get("organization")
            if not isinstance(writes, list) and isinstance(capability.get("writes"), list):
                writes = capability.get("writes")
        if not run_id:
            raise ValueError("task_permission_request_invalid")
        capability_type = (
            str(capability.get("type") or "") if isinstance(capability, dict) else ""
        )
        # Typed capabilities must be dispatched before the generic writes list:
        # Pi intentionally sends writes=[] for non-Vault permission cards.
        if capability_type == "developer_workspace":
            workspace_id = str(capability.get("workspaceId") or "").strip()
            tool_name = str(capability.get("toolName") or "").strip()
            if not workspace_id or not tool_name:
                raise ValueError("developer_workspace_permission_invalid")
            # Never trust a client-supplied project path. The persisted record
            # proves Run ownership and that this is an isolated worktree.
            workspace = self.developer_workspace.get(workspace_id, run_id)
            self.task_authorizations.register_workspace(
                authorization_id,
                run_id,
                workspace_id,
                str(workspace["project"]),
            )
            result = self.task_authorizations.grant_workspace_operation(
                authorization_id,
                run_id,
                workspace_id,
                tool_name,
                mode,
            )
            result["capability"] = {
                "type": "developer_workspace",
                "workspaceId": workspace_id,
                "toolName": tool_name,
            }
        elif capability_type == "network":
            # Network scope is represented by networkPolicy on the Turn and is
            # established before model execution. Never misroute it to an empty
            # Vault write grant.
            raise ValueError("network_permission_requires_explicit_turn_policy")
        elif isinstance(organization, dict):
            result = self.task_authorizations.grant_organization(
                authorization_id, run_id, organization, mode,
            )
        elif isinstance(writes, list) and len(writes) <= 100:
            result = self.task_authorizations.grant_scope(
                authorization_id,
                run_id,
                [dict(item) for item in writes if isinstance(item, dict)],
                mode,
            )
        else:
            raise ValueError("task_permission_request_invalid")
        return {"taskAuthorization": result["authorization"], **result}

    @staticmethod
    def _runtime_permission_request(
        code: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any] | None:
        if code in {
            "task_scope_expansion_requires_new_user_turn",
            "task_create_root_requires_scope_expansion",
            "task_create_scope_required",
            "task_update_scope_required",
        }:
            if tool_name == "plan_vault_copy":
                destination_root = str(arguments.get("destination_root") or "").strip().strip("/")
                requested_writes = [
                    {"path": f"{destination_root}/{Path(str(source)).name}"}
                    for source in list(arguments.get("source_paths") or [])
                    if str(source).strip()
                ]
            else:
                requested_writes = [
                    {"path": str(item.get("path") or "")}
                    for item in list(arguments.get("writes") or [])
                    if isinstance(item, dict) and item.get("path")
                ]
            return {
                "type": "vault_writes",
                "toolName": tool_name,
                "summary": f"授权本轮写入 {len(requested_writes)} 个明确 Markdown 目标",
                "writes": requested_writes,
            }
        if code == "task_organization_scope_required" and tool_name == "organize_vault_notes":
            moves = [
                {
                    "source_path": str(item.get("source_path") or ""),
                    "target_path": str(
                        item.get("target_path") or item.get("destination_path") or ""
                    ),
                }
                for item in list(arguments.get("moves") or [])
                if isinstance(item, dict)
            ]
            summary = "; ".join(
                f"{item['source_path']} → {item['target_path']}" for item in moves
            )
            return {
                "type": "vault_organization",
                "toolName": "organize_vault_notes",
                "summary": summary or "创建受控 Vault 子目录",
                "organization": {
                    "directories": [str(item) for item in list(arguments.get("directories") or [])],
                    "moves": moves,
                    "remove_empty_source_dirs": arguments.get("remove_empty_source_dirs") is True,
                },
            }
        if code in {
            "developer_workspace_not_authorized",
            "developer_operation_not_authorized",
        }:
            return {
                "type": "developer_workspace",
                "toolName": tool_name,
                "workspaceId": str(arguments.get("workspace_id") or ""),
                "summary": f"授权当前 Run 在隔离 worktree 执行 {tool_name}",
            }
        return None

    def append_pi_events(self, body: dict[str, Any]) -> dict[str, Any]:
        run_id = str(body.get("runId") or "")
        events = body.get("events")
        if not run_id or not isinstance(events, list) or len(events) > 1000:
            raise ValueError("pi_events_invalid")
        last = self.store.append_pi_agent_events(run_id, events)
        terminal = next(
            (item for item in reversed(events) if item.get("type") in {"done", "error"}),
            None,
        )
        if terminal:
            status = (
                "completed"
                if terminal.get("type") == "done" and terminal.get("status") != "cancelled"
                else "cancelled" if terminal.get("status") == "cancelled" else "failed"
            )
            self.store.complete_pi_run(run_id, status, str(terminal.get("code") or ""))
            run = self.store.get_pi_run(run_id)
            text = "".join(
                str(item.get("content") or "")
                for item in self.store.list_pi_agent_events(run_id, 0, 5000)
                if item.get("type") == "text"
            ).strip()
            if text:
                session = self.store.get_pi_session(str(run["session_id"]))
                conversation_id = str(session.get("conversation_id") or run["session_id"])
                self.intake.append_message(
                    conversation_id,
                    "assistant",
                    text,
                    "pi-runtime",
                    message_id=f"pi-assistant-{run_id}",
                )
        return {"runId": run_id, "lastEventSequence": last}

    def pi_run_events(self, run_id: str, after_sequence: int = 0) -> dict[str, Any]:
        run = self.store.get_pi_run(run_id)
        return {
            "run": run,
            "items": self.store.list_pi_agent_events(run_id, after_sequence),
            "schemaVersion": 1,
        }

    def pi_session(self, session_id: str) -> dict[str, Any]:
        try:
            session: dict[str, Any] | None = self.store.get_pi_session(session_id)
            conversation_id = str(session.get("conversation_id") or session_id)
        except ValueError:
            session = None
            conversation_id = session_id
        try:
            history = self.intake.get_conversation(conversation_id).get("messages", [])
        except ValueError:
            history = []
        return {
            "session": session,
            "history": [
                {
                    "id": str(item.get("id") or ""),
                    "role": str(item.get("role") or ""),
                    "content": str(item.get("content") or ""),
                    "createdAt": str(item.get("createdAt") or ""),
                }
                for item in history
                if item.get("role") in {"user", "assistant"} and item.get("content")
            ],
            "schemaVersion": 2,
        }

    def control_pi_run(self, run_id: str, body: dict[str, Any]) -> dict[str, Any]:
        control_type = str(body.get("type") or "")
        text = str(body.get("text") or "").strip()
        if control_type not in {"steering", "follow_up"} or not text or len(text) > 20_000:
            raise ValueError("pi_control_invalid")
        run = self.store.get_pi_run(run_id)
        if str(run.get("status") or "") != "running":
            raise RuntimeError("pi_run_not_running")
        entry = self.store.record_pi_control(run_id, control_type, {"text": text})
        return {"control": entry}

    def cancel_pi_run(self, run_id: str) -> dict[str, Any]:
        run = self.store.get_pi_run(run_id)
        if str(run.get("status") or "") not in {"completed", "failed", "cancelled"}:
            self.store.complete_pi_run(run_id, "cancelled", "user_cancelled")
        return {"runId": run_id, "status": "cancelled"}

    def get_agent_action(self, action_id: str) -> dict[str, Any]:
        return {"action": self.reversible_transactions.get(action_id)}

    def get_agent_action_diff(self, action_id: str) -> dict[str, Any]:
        return self.reversible_transactions.diff(action_id)

    def undo_agent_action(self, action_id: str) -> dict[str, Any]:
        # This method is reached only from the authenticated, explicit UI undo
        # endpoint. Model tool calls use the strict Run-bound branch below.
        return {
            "action": self.reversible_transactions.undo(
                action_id,
                user_confirmed=True,
            )
        }

    def call_runtime_tool(self, body: dict[str, Any]) -> dict[str, Any]:
        tool_name = str(body.get("toolName") or "")
        arguments = body.get("arguments")
        if not tool_name or not isinstance(arguments, dict):
            raise ValueError("runtime_tool_request_invalid")
        run_id = str(body.get("runId") or "")
        turn_id = str(body.get("turnId") or "")
        call_id = str(body.get("toolCallId") or "")
        if not run_id or not turn_id or not call_id:
            raise ValueError("runtime_tool_identity_required")
        stable_writes = {
            "plan_vault_change", "plan_vault_copy", "organize_vault_notes",
            "apply_vault_change", "undo_agent_action",
        }
        stable_reads = {
            "get_current_note", "read_vault_note", "find_related_notes",
            "search_public_web", "fetch_public_url",
        }
        developer_tools = {
            "create_git_worktree", "read_workspace_file", "write_workspace_file",
            "run_command", "run_bash", "git_status", "git_diff", "git_commit",
            "merge_task_branch", "activate_runtime_upgrade", "rollback_task_branch", "validate_skill_draft", "validate_mcp_server",
            "probe_mcp_server",
        }
        definition = None if tool_name in stable_writes | stable_reads | developer_tools else self.tools.definition(tool_name)
        uses_network = tool_name in {"search_public_web", "fetch_public_url"} or bool(definition and definition.uses_network)
        if uses_network and body.get("networkAuthorized") is not True:
            return {
                "ok": False,
                "error": {
                    "code": "network_authorization_required",
                    "message": "当前任务未授权联网",
                    "recoverable": True,
                    "retryable": False,
                },
                "isError": True,
            }
        try:
            authorization_id = str(body.get("taskAuthorizationId") or "")
            if tool_name == "plan_vault_change":
                result = self.brain_change_sets.create({
                    "run_id": run_id,
                    "task_authorization_id": authorization_id,
                    "title": str(arguments.get("title") or "Agent 写入计划"),
                    "writes": list(arguments.get("writes") or []),
                })
                _, bundle = self.brain_change_sets.prepared(str(result["id"]))
                try:
                    planned = self.task_authorizations.plan(
                        authorization_id,
                        list(bundle["writes"]),
                    )
                except Exception:
                    self.brain_change_sets.update_state(str(result["id"]), "failed")
                    raise
                result = {
                    **result,
                    "authorization": planned["authorization"],
                    "diff": self.brain_change_sets.diff(str(result["id"])),
                }
            elif tool_name == "plan_vault_copy":
                destination_root = str(arguments.get("destination_root") or "").strip().strip("/")
                if not (
                    destination_root == "01-Inbox"
                    or destination_root.startswith("01-Inbox/")
                    or destination_root == "20-Knowledge/Drafts"
                    or destination_root.startswith("20-Knowledge/Drafts/")
                ):
                    raise PermissionError("vault_copy_destination_requires_draft_root")
                writes: list[dict[str, Any]] = []
                seen_targets: set[str] = set()
                for raw_source in list(arguments.get("source_paths") or []):
                    source = safe_read_note(self.vault, str(raw_source))
                    if not source.is_file():
                        raise FileNotFoundError("copy_source_not_found")
                    source_text = read_model_visible_note(source)
                    target = f"{destination_root}/{source.name}"
                    if target in seen_targets:
                        raise ValueError("copy_target_collision")
                    seen_targets.add(target)
                    writes.append({
                        "path": target,
                        "content": source_text,
                        "category": "vault-copy",
                    })
                change_sets: list[dict[str, Any]] = []
                title = str(arguments.get("title") or "复制 Vault 笔记")
                for batch_index in range(0, len(writes), 10):
                    batch = writes[batch_index:batch_index + 10]
                    planned = self.brain_change_sets.create({
                        "run_id": run_id,
                        "task_authorization_id": authorization_id,
                        "title": title if len(writes) <= 10 else f"{title}（{batch_index // 10 + 1}）",
                        "writes": batch,
                    })
                    _, bundle = self.brain_change_sets.prepared(str(planned["id"]))
                    try:
                        authorization = self.task_authorizations.plan(
                            authorization_id,
                            list(bundle["writes"]),
                        )
                    except Exception:
                        self.brain_change_sets.update_state(str(planned["id"]), "failed")
                        raise
                    # Keep the full authenticated Diff in the Change Set store.
                    # Returning every copied body/Diff to the model would defeat
                    # this server-side batch tool and can exhaust the transport.
                    change_sets.append({
                        "id": planned["id"],
                        "state": planned["state"],
                        "title": planned["title"],
                        "files": [item["path"] for item in planned["writes"]],
                        "fileCount": len(planned["writes"]),
                    })
                result = {
                    "sourceCount": len(writes),
                    "destinationRoot": destination_root,
                    "change_sets": change_sets,
                    "next": "逐个调用 apply_vault_change，并传入每个 change_set 的 id",
                }
            elif tool_name == "apply_vault_change":
                result = self.reversible_transactions.apply_change_set(
                    self.brain_change_sets,
                    {
                        "change_set_id": str(arguments.get("change_set_id") or ""),
                        "task_authorization_id": authorization_id,
                        "turn_id": turn_id,
                        "source_message_id": str(body.get("sourceMessageId") or ""),
                    },
                )
            elif tool_name == "organize_vault_notes":
                result = self.reversible_transactions.organize({
                    "task_authorization_id": authorization_id,
                    "run_id": run_id,
                    "turn_id": turn_id,
                    "source_message_id": str(body.get("sourceMessageId") or ""),
                    "title": str(arguments.get("title") or "整理 Vault 笔记"),
                    "directories": list(arguments.get("directories") or []),
                    "moves": list(arguments.get("moves") or []),
                    "remove_empty_source_dirs": arguments.get("remove_empty_source_dirs") is True,
                })
            elif tool_name == "undo_agent_action":
                result = self.reversible_transactions.undo(
                    str(arguments.get("action_id") or ""),
                    authorization_id=authorization_id,
                    run_id=run_id,
                )
            elif tool_name in {"get_current_note", "read_vault_note"}:
                result = self.tools.call(
                    "read_note_excerpt",
                    {
                        "path": str(arguments.get("path") or ""),
                        "offset": max(0, int(arguments.get("offset") or 0)),
                        "max_chars": max(100, min(50_000, int(arguments.get("max_chars") or 12_000))),
                    },
                    run_id=run_id,
                    step_id=f"{turn_id}:{call_id}",
                    allowed_permissions=("read_only",),
                    record_event=False,
                )
            elif tool_name == "find_related_notes":
                result = self.tools.call(
                    "get_related_notes",
                    {"path": str(arguments.get("path") or "")},
                    run_id=run_id,
                    step_id=f"{turn_id}:{call_id}",
                    allowed_permissions=("read_only",),
                    record_event=False,
                )
            elif tool_name == "search_public_web":
                result = self.web.search(
                    str(arguments.get("query") or ""),
                    max(1, min(10, int(arguments.get("limit") or 8))),
                )
            elif tool_name == "fetch_public_url":
                result = self.web.fetch_for_model(str(arguments.get("url") or ""))
            elif tool_name == "create_git_worktree":
                result = self.developer_workspace.create(run_id)
                self.task_authorizations.register_workspace(
                    authorization_id, run_id, str(result["id"]), str(result["project"]),
                )
            elif tool_name in developer_tools:
                workspace_id_value = str(arguments.get("workspace_id") or "")
                self.task_authorizations.validate_workspace(
                    authorization_id, run_id, workspace_id_value, tool_name,
                )
                if tool_name == "read_workspace_file":
                    result = self.developer_workspace.read(workspace_id_value, run_id, str(arguments.get("path") or ""))
                elif tool_name == "write_workspace_file":
                    result = self.developer_workspace.write(workspace_id_value, run_id, str(arguments.get("path") or ""), str(arguments.get("content") or ""), str(arguments.get("base_hash") or ""))
                elif tool_name == "run_command":
                    result = self.developer_workspace.run_command(workspace_id_value, run_id, arguments)
                elif tool_name == "run_bash":
                    result = self.developer_workspace.run_bash(workspace_id_value, run_id, arguments)
                elif tool_name == "git_status":
                    result = self.developer_workspace.git_status(workspace_id_value, run_id)
                elif tool_name == "git_diff":
                    result = self.developer_workspace.git_diff(workspace_id_value, run_id)
                elif tool_name == "git_commit":
                    result = self.developer_workspace.git_commit(workspace_id_value, run_id, str(arguments.get("message") or ""))
                elif tool_name == "merge_task_branch":
                    result = self.developer_workspace.merge(workspace_id_value, run_id)
                elif tool_name == "activate_runtime_upgrade":
                    result = self.developer_workspace.activation_request(workspace_id_value, run_id)
                elif tool_name == "rollback_task_branch":
                    result = self.developer_workspace.rollback(workspace_id_value, run_id)
                elif tool_name == "validate_skill_draft":
                    result = self.developer_workspace.validate_skill(workspace_id_value, run_id, str(arguments.get("path") or ""))
                elif tool_name == "validate_mcp_server":
                    result = self.developer_workspace.validate_mcp(workspace_id_value, run_id, str(arguments.get("path") or ""))
                else:
                    result = self.developer_workspace.probe_mcp(workspace_id_value, run_id, str(arguments.get("path") or ""))
            else:
                result = self.tools.call(
                    tool_name,
                    arguments,
                    run_id=run_id,
                    step_id=f"{turn_id}:{call_id}",
                    allowed_permissions=("read_only",),
                    # Pi lifecycle events are persisted in the assistant runtime
                    # event store. The legacy tool_events table is FK-bound to
                    # brain_runs and must not receive Pi run identifiers.
                    record_event=False,
                )
            return {
                "ok": True,
                "content": result,
                "isError": False,
                "recoverable": False,
                "action": result if tool_name in {
                    "organize_vault_notes", "apply_vault_change", "undo_agent_action",
                } else None,
            }
        except (ValueError, FileNotFoundError, FileExistsError, PermissionError, RuntimeError) as exc:
            code = str(exc)[:160] or type(exc).__name__
            permission_request = self._runtime_permission_request(code, tool_name, arguments)
            return {
                "ok": False,
                "error": {
                    "code": code,
                    "message": "工具未能完成；请根据该 Observation 调整步骤",
                    "recoverable": permission_request is not None or not isinstance(exc, PermissionError),
                    "retryable": isinstance(exc, (FileNotFoundError, RuntimeError)),
                    **({"permissionRequest": permission_request} if permission_request else {}),
                },
                "isError": True,
            }

    def chat(self, body: dict[str, Any], stream: bool = False) -> dict[str, Any]:
        routes = self.model_routing(); route = routes.get("assistant_chat") or routes.get("assistant", {})
        profile_id = str(body.get("profile_id") or route.get("profileId") or "")
        if not profile_id: raise RuntimeError("No assistant model profile is configured")
        profile = next((item for item in self.store.list_model_profiles() if item["id"] == profile_id), None)
        if not profile or not profile["enabled"]: raise RuntimeError("Assistant model profile is unavailable")
        messages = body.get("messages", [])
        if not isinstance(messages, list) or not messages or len(messages) > 100: raise ValueError("messages must contain 1–100 entries")
        clean: list[dict[str, str]] = []
        for item in messages:
            role, content = str(item.get("role", "")), str(item.get("content", ""))
            if role not in {"system", "user", "assistant"} or not content or len(content) > 100_000: raise ValueError("Invalid chat message")
            clean.append({"role": role, "content": content})
        settings = profile["settings"]; model = str(body.get("model") or route.get("modelOverride") or profile["defaultModel"])
        provider = self.models.provider(profile_id)
        if stream:
            chunks: list[str] = []
            for event in provider.stream_chat(model, clean, temperature=settings.get("temperature", .3), max_tokens=settings.get("maxTokens", 2000)):
                if event.get("type") == "delta": chunks.append(str(event.get("content") or ""))
            result = {"choices": [{"message": {"content": "".join(chunks)}}]}
        else:
            result = provider.chat(model, clean, temperature=settings.get("temperature", .3), max_tokens=settings.get("maxTokens", 2000))
        content = str((((result.get("choices") or [{}])[0].get("message") or {}).get("content")) or "")
        conversation_id = str(body.get("conversation_id") or uuid.uuid4().hex)
        if len(conversation_id) > 128 or not conversation_id.replace("-", "").isalnum(): raise ValueError("Invalid conversation id")
        root = self.vault / "90-Local-Only/Agent/Conversations"; root.mkdir(parents=True, exist_ok=True)
        record = {"at": datetime.now().astimezone().isoformat(timespec="seconds"), "profile_id": profile_id, "model": model, "messages": clean, "assistant": content}
        with (root / f"{conversation_id}.jsonl").open("a", encoding="utf-8") as handle: handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        self.log("chat.completed", {"conversation_id": conversation_id, "profile_id": profile_id, "model": model, "message_count": len(clean)})
        return {"conversation_id": conversation_id, "model": model, "message": {"role": "assistant", "content": content}, "streaming": False, "stream_requested": stream}

    def _assistant_grounding_text(self, attachments: list[dict[str, Any]], active_note: dict[str, Any]) -> str:
        sections: list[str] = []
        note_path = str(active_note.get("path") or "")
        if note_path:
            path = (self.vault / note_path).resolve()
            if path.is_relative_to(self.vault) and path.is_file() and not path.is_symlink() and path.suffix.casefold() == ".md":
                selection = str(active_note.get("selection") or "").strip()
                text = selection or path.read_text(encoding="utf-8", errors="replace")
                sections.append(f"[当前笔记 {note_path}]\n{text[:8_000]}")
        for attachment in attachments[:10]:
            try:
                path = self.intake.resolve_attachment_path(str(attachment["id"]))
                if attachment.get("kind") == "pdf":
                    pages = ingest_pdf.extract_pages(path)
                    text = "\n".join(pages)[:16_000]
                elif path.is_file() and path.stat().st_size <= 5 * 1024 * 1024:
                    text = path.read_text(encoding="utf-8", errors="replace")[:12_000]
                else:
                    text = ""
                if text:
                    sections.append(f"[附件 {attachment.get('displayName')}]\n{text}")
            except (OSError, UnicodeError, RuntimeError, ValueError):
                continue
        return "\n\n".join(sections)[:24_000]

    def start_study_session(self, recommendation_id: str) -> dict[str, Any]:
        rec = self.get_recommendation(recommendation_id)
        if rec["kind"] == "source": raise RuntimeError("Source recommendations must be reviewed before study")
        existing = self.store.resumable_study_session(recommendation_id)
        if existing:
            self.log("study.resumed", {"recommendation_id": recommendation_id, "session_id": existing["session_id"]})
            return {**study_workspace.public_session(existing), "recommendation": rec, "resumed": True}
        lesson = study_workspace.lesson_blueprint(rec)
        session_id = self.store.create_study_session(recommendation_id, {
            "title": rec["title"], "estimated_minutes": rec["estimatedMinutes"], "lesson": lesson, "recommendation": rec,
            "progress": {"sectionIndex": 0, "completedSectionIds": [], "quizAnswers": {}, "notes": ""},
        })
        self.store.set_daily_recommendation_state(date.today().isoformat(), recommendation_id, "in_progress")
        self.log("study.started", {"recommendation_id": recommendation_id, "session_id": session_id})
        return {**study_workspace.public_session(self.store.get_study_session(session_id)), "recommendation": rec, "resumed": False}

    def get_study_session(self, session_id: str) -> dict[str, Any]:
        return study_workspace.public_session(self.store.get_study_session(session_id))

    def update_study_session(self, session_id: str, action: str, body: dict[str, Any]) -> dict[str, Any]:
        states = {"pause": "paused", "resume": "active", "open_quiz": "quiz", "leave_quiz": "active", "progress": "active", "abandon": "abandoned", "retry": "active", "undo_complete": "active"}
        if action not in states: raise ValueError("unsupported_study_action")
        current = self.store.get_study_session(session_id)
        if action == "undo_complete" and current["state"] != "completed": raise ValueError("study_completion_not_undoable")
        if action != "undo_complete" and current["state"] == "completed": raise ValueError("completed_study_session_is_read_only")
        patch = {"progress": dict(body.get("progress") or {})}
        if body.get("error"): patch["last_error"] = str(body["error"])
        updated = self.store.update_study_session(session_id, states[action], patch)
        if action == "undo_complete":
            self.store.undo_recommendation_feedback(str(updated["recommendation_id"]))
            self.store.undo_recommendation_feedback(str(updated["recommendation_id"]))
        daily_state = {"pause": "paused", "resume": "in_progress", "open_quiz": "in_progress", "leave_quiz": "in_progress", "progress": "in_progress", "abandon": "skipped", "retry": "in_progress", "undo_complete": "in_progress"}[action]
        self.store.set_daily_recommendation_state(date.today().isoformat(), str(updated["recommendation_id"]), daily_state)
        self.log(f"study.{action.replace('_', '-')}", {"session_id": session_id, "recommendation_id": updated["recommendation_id"]})
        return study_workspace.public_session(updated)

    def complete_study_session(self, session_id: str, correctness: float, notes: str = "") -> dict[str, Any]:
        row = self.store.get_study_session(session_id)
        if row["state"] == "completed":
            details = row.get("details") or {}
            return {"session_id": session_id, "state": "completed", "suggested_mastery": int(details.get("suggested_mastery", 0)), "requires_confirmation": True, "path": details.get("path"), "next_review": details.get("next_review"), "review_scheduled": bool(details.get("review_scheduled", True)), "idempotent": True}
        rec = self.get_recommendation(str(row["recommendation_id"]))
        suggested = self.suggest_mastery(int(rec.get("mastery", 0)), correctness)
        next_review = (date.today() + timedelta(days=1 if correctness < .8 else 2)).isoformat()
        completed = self.store.complete_study_session(session_id, {"correctness": correctness, "notes": notes, "suggested_mastery": suggested, "path": rec.get("sourcePath"), "next_review": next_review, "review_scheduled": True})
        recommendations.record_action(self.store, str(row["recommendation_id"]), "completed", {"session_id": session_id})
        recommendations.record_action(self.store, str(row["recommendation_id"]), "tomorrow", {"title": rec.get("title", "学习内容"), "domain": rec.get("domain", ""), "scheduled_review": True, "next_review": next_review})
        self.store.set_daily_recommendation_state(date.today().isoformat(), str(row["recommendation_id"]), "completed")
        self.log("study.completed", {"recommendation_id": row["recommendation_id"], "session_id": session_id})
        return {"session_id": session_id, "state": completed["state"], "suggested_mastery": suggested, "requires_confirmation": True, "path": rec.get("sourcePath"), "next_review": next_review, "review_scheduled": True}
    def suggest_mastery(self, current: int, correctness: float, critical_error: bool = False) -> int:
        return learning.suggest_mastery(current, correctness, critical_error)
    def confirm_mastery(self, path: str, mastery: int, weak_points: list[str]) -> str:
        target = (self.vault / path).resolve()
        result = str(learning.confirm_mastery(self.vault, self.store, target, mastery, weak_points)); self.log("learning.mastery-confirmed", {"path": path, "mastery": mastery}); return result

    def enqueue(self, kind: str, payload: dict[str, Any]) -> str:
        allowed = {"prepare-pdf", "expand-idea", "learning-plan", "quiz"}
        if kind not in allowed: raise RuntimeError(f"Unsupported job kind: {kind}")
        job_id = self.store.create_job(kind, payload); self.log("job.queued", {"job_id": job_id, "kind": kind}); return job_id

    def run_job(self, job_id: str, handlers: dict[str, Callable[[dict[str, Any]], Any]]) -> None:
        job = self.store.get_job(job_id)
        if not job or job["state"] != "queued": raise RuntimeError("Job is not queued")
        payload = json.loads(job["payload_json"]); kind = str(job["kind"])
        if kind not in handlers: raise RuntimeError(f"No handler for {kind}")
        self.store.update_job(job_id, "running", stage="running", progress=5)
        try:
            if kind == "prepare-pdf":
                payload["_progress"] = lambda stage, progress: self.store.update_job(job_id, "running", stage=stage, progress=progress)
            result = handlers[kind](payload)
            latest = self.store.get_job(job_id)
            if latest and latest["cancel_requested"]:
                self.store.update_job(job_id, "cancelled", result=result, stage="cancelled", progress=int(latest["progress"]))
                return
            if kind == "prepare-pdf":
                prepared_id = str(result.get("prepared_id") or Path(str(result["bundle"])).name)
                self.store.update_job(job_id, "prepared", result=result, stage="prepared", progress=95, prepared_id=prepared_id)
                self.store.update_job(job_id, "awaiting_confirmation", result=result, stage="awaiting_confirmation", progress=100, prepared_id=prepared_id)
            else:
                self.store.update_job(job_id, "completed", result=result, stage="completed", progress=100)
        except Exception as exc:
            self.store.update_job(job_id, "failed", error=f"{type(exc).__name__}: {exc}", stage="failed")
            raise

    def default_handlers(self) -> dict[str, Callable[[dict[str, Any]], Any]]:
        def prepare_pdf_job(payload: dict[str, Any]) -> dict[str, str]:
            args = argparse.Namespace(
                pdf=str(payload["pdf"]), vault=str(self.vault), kind=str(payload.get("kind", "paper")),
                domain_focus=str(payload.get("domain_focus", "")), model=str(payload.get("model", "deepseek-v4-pro")),
                base_url=str(payload.get("base_url", "https://api.deepseek.com")), max_concepts=int(payload.get("max_concepts", 3)),
            )
            bundle = Path(prepared_pdf.prepare_bundle(args, progress=payload.get("_progress")))
            return {"bundle": str(bundle), "prepared_id": bundle.name}
        def learning_plan_job(payload: dict[str, Any]) -> dict[str, str]:
            today = date.today(); start = today + timedelta(days=(7 - today.weekday()) % 7 or 7)
            return {"path": str(learning.create_weekly_plan(self.vault, start))}
        def quiz_job(payload: dict[str, Any]) -> dict[str, Any]:
            plan = learning.daily_plan(self.vault); candidates = plan["review"] or plan["new_learning"]
            if not candidates: return {"questions": [], "reason": "no reviewed/core candidate"}
            path = Path(candidates[0]["path"]); item = next(item for item in learning.scan_reviewed(self.vault) if item.path == path)
            return {"artifact": item.title, "questions": learning.quiz(item, weekend=date.today().weekday() >= 5)}
        def expand_idea_job(payload: dict[str, Any]) -> dict[str, Any]:
            relative = Path(str(payload["path"])); path = (self.vault / relative).resolve()
            if not path.is_relative_to(self.vault / "01-Inbox/Ideas") or not path.is_file(): raise RuntimeError("idea path is outside 01-Inbox/Ideas")
            return {"path": str(relative), "state": "awaiting-model-prepared-expansion", "message": "Idea retained; no unrestricted write was performed."}
        return {"prepare-pdf": prepare_pdf_job, "learning-plan": learning_plan_job, "quiz": quiz_job, "expand-idea": expand_idea_job}

    def process_next(self, handlers: dict[str, Callable[[dict[str, Any]], Any]] | None = None) -> str | None:
        job = self.store.next_queued()
        if not job: return None
        try: self.run_job(str(job["job_id"]), handlers or self.default_handlers())
        except Exception as exc: self.log("job.failed", {"job_id": job["job_id"], "error": f"{type(exc).__name__}: {exc}"})
        return str(job["job_id"])

    def sync_artifacts(self) -> None:
        items = review.scan_artifacts(self.vault); now = datetime.now().astimezone().isoformat(timespec="seconds")
        with self.store.lock:
            self.store.connection.execute("DELETE FROM artifacts")
            for item in items:
                path = Path(item["path"]); digest = hashlib.sha256(path.read_bytes()).hexdigest()
                self.store.connection.execute("INSERT INTO artifacts VALUES (?, ?, ?, ?, ?, ?, ?)", (item["artifact_id"], item["artifact_role"], str(path.relative_to(self.vault)), item["status"], item["review_state"], digest, now))
            self.store.connection.commit()

    def sync_transactions(self) -> None:
        root = self.vault / "90-Local-Only/Processing-Cache/transactions"; now = datetime.now().astimezone().isoformat(timespec="seconds")
        if not root.exists(): return
        with self.store.lock:
            for journal in root.glob("*/journal.json"):
                try: data = json.loads(journal.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError): continue
                transaction_id = str(data.get("transaction_id", journal.parent.name)); state = str(data.get("status", "unknown"))
                self.store.connection.execute("INSERT INTO transactions VALUES (?, ?, ?, ?, ?) ON CONFLICT(transaction_id) DO UPDATE SET state=excluded.state, journal_path=excluded.journal_path, updated_at=excluded.updated_at", (transaction_id, state, str(journal.relative_to(self.vault)), now, now))
            self.store.connection.commit()

    def sync_indexes(self) -> None:
        self.sync_artifacts(); self.list_prepared(); self.sync_transactions()
