"""Explicit workflow service.

This module isolates the *legacy Brain* code paths that the ordinary Pi Agent
Assistant no longer uses. The ordinary Assistant conversation goes exclusively
through ``PiAgentRuntime`` (model stream -> tool contracts -> tool call ->
agent events -> session projection -> reversible transaction) and must never
import or call any of the symbols below.

Everything that still needs the old Brain (Prepared PDF, ``apply-prepared``,
manual material import, fixed learning plans, explicit workflow runs,
background maintenance) lives here and is reached through
``ExplicitWorkflowService`` so that ``agent.core.service`` stays free of any
legacy Brain dependency.

Forbidden legacy symbols that live ONLY here (not in ``service.py``):
``BrainModelGateway``, ``IntentResult``, ``StructuredWorkflowRunner``,
``AssistantOutcome``, ``resolve_assistant_outcome``, ``BrainRequest``,
``ContextMaterialCoordinator``, ``submit_intake``, ``precise_intent``,
``assistantIntent``, ``assistant_intent``, ``primary_intent``.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import date
from pathlib import Path
from typing import Any

from agent.brain import BrainRequest
from agent.brain.schemas import IntentResult
from agent.brain.model_gateway import BrainModelGateway
from agent.core.assistant_outcomes import AssistantOutcome, resolve_assistant_outcome
from agent.core.context_material import ContextMaterialCoordinator
from agent.core.structured_workflow import StructuredWorkflowRunner
from agent.core.vault_autonomy import VaultAutonomyService, render_managed_block
from agent.skills import build_skill_registry
from agent.tools.change_set import ChangeSetTools


class ExplicitWorkflowService:
    """Owns the legacy Brain-coupled workflow paths.

    The parent ``AgentService`` is passed in so the migrated methods can keep
    calling the shared helpers (store, intake, log, research, etc.) through
    bound references without duplicating state.
    """

    def __init__(self, agent: Any) -> None:
        self.agent = agent
        self.vault = agent.vault
        self.store = agent.store
        self.intake = agent.intake
        self.models = agent.models
        self.autonomy = agent.autonomy
        self.tools = agent.tools
        self.web = agent.web
        self.log = agent.log
        self.get_brain_run = agent.get_brain_run
        self.get_conversation = agent.get_conversation
        self._artifacts_for_run = agent._artifacts_for_run
        self._assistant_task_title = agent._assistant_task_title
        self._latest_write_status = agent._latest_write_status
        self._conversation_title = agent._conversation_title
        self._requested_today_budget = agent._requested_today_budget
        self._requested_today_constraints = agent._requested_today_constraints
        self.adjust_today = agent.adjust_today
        self.enqueue = agent.enqueue
        self.research_public_web = agent.research_public_web
        # Legacy Brain-owned dependencies.
        self.context_material = ContextMaterialCoordinator(
            self.vault, self.store, self.intake, self.autonomy,
            self.apply_autonomous_vault_change,
        )
        self.model_gateway = BrainModelGateway(self.models)
        self.skills = build_skill_registry(
            self.vault, self.store, self.tools, self.agent.list_prepared, self.model_gateway,
        )
        self.workflows = StructuredWorkflowRunner(self.vault, self.store, self.skills)
        self.brain_change_sets = ChangeSetTools(self.vault, self.store)

    # ------------------------------------------------------------------
    # Explicit workflow submission (legacy Brain path)
    # ------------------------------------------------------------------
    def submit_workflow(
        self, body: dict[str, Any], idempotency_key: str = "", *, mode: str | None = None,
    ) -> dict[str, Any]:
        request_body = dict(body)
        if mode:
            request_body["mode"] = mode
        request = BrainRequest.from_dict(request_body, idempotency_key=idempotency_key)
        result = self.workflows.submit(request)
        self.log("workflow.submitted", {
            "run_id": result["id"], "correlation_id": result["correlation_id"],
            "workflow": result.get("primary_intent"), "status": result["status"],
        })
        return result

    def submit_intake(
        self, body: dict[str, Any], idempotency_key: str = "",
    ) -> dict[str, Any]:
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
            run = self.workflows.submit(BrainRequest.from_dict(request_body, idempotency_key=idempotency_key))
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
            response_text = self.agent._assistant_response(run, artifacts, outcome)
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
            intelligence = self.agent._record_conversation_intelligence(
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
