from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

from pydantic import BaseModel, TypeAdapter
from pydantic_ai import (
    Agent,
    DeferredToolRequests,
    DeferredToolResults,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ModelMessagesTypeAdapter,
    PartDeltaEvent,
    PartEndEvent,
    PartStartEvent,
    TextPart,
    ThinkingPart,
    ToolDenied,
    UsageLimits,
)
from pydantic_ai.messages import RetryPromptPart, TextPartDelta, ThinkingPartDelta

from agent.core.redaction import redact_model_context, redact_secret_text
from agent.core.models import normalized_model_settings

from .agent_factory import DEEP_MODE_INSTRUCTIONS, build_zhixu_agent
from .contracts import InlineConfirmation
from .dependencies import ZhixuDependencies
from .model_factory import build_pydantic_model
from .persistence import RuntimePersistence
from .sync_bridge import sync_iter_async


_DEFERRED_ADAPTER = TypeAdapter(DeferredToolRequests)


class PydanticAssistantRuntime:
    """Canonical assistant runtime backed by PydanticAI's tool loop."""

    version = "pydantic-ai-1"

    def __init__(self, service: Any) -> None:
        self.service = service
        self.persistence = RuntimePersistence(service.store)
        self.model_factory = build_pydantic_model
        self._cancelled: set[str] = set()
        self._cancel_lock = threading.RLock()

    @staticmethod
    def _bounded_history(
        history: list[Any],
        reasoning_mode: str,
    ) -> tuple[list[Any], dict[str, int] | None]:
        """Keep complete recent request/response pairs inside a turn budget."""
        budget = 48_000 if reasoning_mode == "deep" else 96_000
        encoded = ModelMessagesTypeAdapter.dump_json(history)
        if len(encoded) <= budget or len(history) <= 2:
            return history, None

        # Stored PydanticAI history alternates ModelRequest/ModelResponse. Work
        # backwards in pairs so a tool result or response is never orphaned.
        complete_end = len(history) - (len(history) % 2)
        if complete_end < 2:
            return history, None
        selected = history[complete_end - 2:complete_end]
        cursor = complete_end - 2
        while cursor >= 2:
            candidate = history[cursor - 2:complete_end]
            if len(ModelMessagesTypeAdapter.dump_json(candidate)) > budget:
                break
            cursor -= 2
            selected = candidate
        if not selected:
            selected = history[-2:]
        return selected, {
            "messagesBefore": len(history),
            "messagesAfter": len(selected),
            "bytesBefore": len(encoded),
            "bytesAfter": len(ModelMessagesTypeAdapter.dump_json(selected)),
        }

    @staticmethod
    def _turn_model_settings(prepared: dict[str, Any]) -> dict[str, Any] | None:
        if prepared.get("reasoning_mode") != "deep":
            return None
        settings: dict[str, Any] = {
            "max_tokens": min(
                32_768,
                max(8_192, int(prepared.get("max_tokens") or 3_000)),
            ),
        }
        provider_type = str(prepared.get("provider_type") or "")
        if provider_type == "deepseek":
            settings["extra_body"] = {"thinking": {"type": "enabled"}}
            settings["openai_reasoning_effort"] = "max"
        elif provider_type == "openai":
            settings["openai_reasoning_effort"] = "high"
        return settings

    def stream(self, body: dict[str, Any]) -> Iterator[dict[str, Any]]:
        return sync_iter_async(lambda: self._start(body))

    def confirm(
        self,
        run_id: str,
        confirmed: bool,
        *,
        answer: str = "",
        scope: str = "",
    ) -> Iterator[dict[str, Any]]:
        return sync_iter_async(
            lambda: self._resume(
                run_id,
                confirmed,
                answer=answer,
                scope=scope,
            )
        )

    def events(
        self,
        run_id: str,
        after_sequence: int = 0,
    ) -> dict[str, Any]:
        return {
            "items": self.persistence.events_after(
                run_id,
                after_sequence,
            ),
            "schemaVersion": 3,
        }

    def cancel(self, run_id: str) -> dict[str, Any]:
        snapshot = self.persistence.load(run_id)
        if snapshot.status in {"completed", "cancelled", "failed"}:
            return {"runId": run_id, "status": snapshot.status, "idempotent": True}
        with self._cancel_lock:
            self._cancelled.add(run_id)
        if snapshot.status == "waiting_confirmation":
            self.persistence.finish(run_id, "cancelled")
            self._emit(
                run_id,
                "run.cancelled",
                conversationId=snapshot.conversation_id,
                reason="user_cancelled",
            )
        return {"runId": run_id, "status": "cancelling", "idempotent": False}

    def compact(self, run_id: str) -> dict[str, Any]:
        snapshot = self.persistence.load(run_id)
        if snapshot.status == "waiting_confirmation":
            raise RuntimeError("cannot_compact_while_waiting_confirmation")
        history = ModelMessagesTypeAdapter.validate_json(snapshot.message_history_json)
        compacted = history[-12:]
        self.persistence.save_history(run_id, ModelMessagesTypeAdapter.dump_json(compacted))
        checkpoint_id = f"checkpoint-{uuid.uuid4().hex}"
        self.persistence.update_checkpoint(run_id, checkpoint_id)
        event = self._emit(
            run_id,
            "context.compacted",
            conversationId=snapshot.conversation_id,
            checkpointId=checkpoint_id,
            messagesBefore=len(history),
            messagesAfter=len(compacted),
        )
        return {"runId": run_id, "checkpointId": checkpoint_id, "event": event}

    def fork(self, run_id: str, sequence: int | None = None) -> dict[str, Any]:
        source = self.persistence.load(run_id)
        conversation = self.service.intake.create_conversation("分支会话")
        fork_id = f"run-{uuid.uuid4().hex}"
        request = dict(source.request)
        request_body = dict(request.get("body") or {})
        request_body["conversation_id"] = conversation["id"]
        request["body"] = request_body
        self.persistence.create_run(
            fork_id,
            conversation["id"],
            source.profile_id,
            source.model,
            request,
            parent_run_id=run_id,
            forked_from_sequence=sequence or source.last_event_sequence,
        )
        self.persistence.save_history(fork_id, source.message_history_json)
        self.persistence.finish(fork_id, "completed")
        event = self._emit(
            fork_id,
            "run.forked",
            conversationId=conversation["id"],
            parentRunId=run_id,
            forkedFromSequence=sequence or source.last_event_sequence,
        )
        return {"runId": fork_id, "conversationId": conversation["id"], "event": event}

    async def _start(
        self,
        body: dict[str, Any],
    ) -> AsyncIterator[dict[str, Any]]:
        prepared = self._prepare_request(body)
        run_id = prepared["run_id"]
        conversation_id = prepared["conversation_id"]
        self.persistence.create_run(
            run_id,
            conversation_id,
            prepared["profile_id"],
            prepared["model"],
            prepared["request_snapshot"],
        )

        yield self._emit(
            run_id,
            "run.started",
            conversationId=conversation_id,
            model=prepared["model"],
            profileId=prepared["profile_id"],
        )
        yield self._emit(
            run_id,
            "context.resolved",
            conversationId=conversation_id,
            focus=prepared["focus"],
            understanding=prepared["understanding"],
            sources=prepared["sources"],
        )

        history_json = self.persistence.latest_history(
            conversation_id
        )
        history = ModelMessagesTypeAdapter.validate_json(
            history_json
        )
        history, compacted = self._bounded_history(
            history,
            prepared["reasoning_mode"],
        )
        if compacted:
            self.persistence.save_history(
                run_id,
                ModelMessagesTypeAdapter.dump_json(history),
            )
            checkpoint_id = f"checkpoint-{uuid.uuid4().hex}"
            self.persistence.update_checkpoint(run_id, checkpoint_id)
            yield self._emit(
                run_id,
                "context.compacted",
                conversationId=conversation_id,
                checkpointId=checkpoint_id,
                reasoningMode=prepared["reasoning_mode"],
                **compacted,
            )

        async for event in self._execute_agent(
            prepared,
            history=history,
            deferred_results=None,
        ):
            yield event

    async def _resume(
        self,
        run_id: str,
        confirmed: bool,
        *,
        answer: str = "",
        scope: str = "",
    ) -> AsyncIterator[dict[str, Any]]:
        snapshot = self.persistence.load(run_id)
        if snapshot.status in {"completed", "cancelled", "failed"}:
            for event in self.persistence.events_after(run_id, 0):
                yield event
            return
        if snapshot.status != "waiting_confirmation":
            raise ValueError("assistant_run_not_waiting_confirmation")
        if not snapshot.deferred_requests_json:
            raise ValueError("assistant_deferred_request_missing")
        if not self.persistence.claim_confirmation(run_id):
            raise RuntimeError("assistant_confirmation_already_in_progress")

        requests = _DEFERRED_ADAPTER.validate_json(
            snapshot.deferred_requests_json
        )
        results = DeferredToolResults()
        for call in requests.approvals:
            results.approvals[call.tool_call_id] = (
                True
                if confirmed
                else ToolDenied("用户在当前对话中取消了这次修改")
            )
        for call in requests.calls:
            if confirmed and answer.strip():
                results.calls[call.tool_call_id] = answer.strip()[:20_000]
            else:
                results.calls[call.tool_call_id] = ToolDenied(
                    "用户取消了这个问题"
                )

        if (
            confirmed
            and scope
            and snapshot.confirmation
            and snapshot.confirmation.kind == "write"
            and scope in snapshot.confirmation.scope_candidates
        ):
            key = f"assistant_session_allow_roots:{snapshot.conversation_id}"
            current = self.service.store.get_setting(key, [])
            roots = list(current) if isinstance(current, list) else []
            if scope not in roots:
                roots.append(scope)
                self.service.store.set_setting(key, roots)

        prepared = self._restore_request(snapshot)
        history = ModelMessagesTypeAdapter.validate_json(
            snapshot.message_history_json
        )

        yield self._emit(
            run_id,
            "inline.confirmation.resolved",
            conversationId=snapshot.conversation_id,
            confirmed=confirmed,
            answered=bool(answer.strip()),
            proposalId=(
                snapshot.confirmation.proposal_id
                if snapshot.confirmation
                else ""
            ),
        )

        async for event in self._execute_agent(
            prepared,
            history=history,
            deferred_results=results,
        ):
            yield event

    async def _execute_agent(
        self,
        prepared: dict[str, Any],
        *,
        history: list[Any],
        deferred_results: DeferredToolResults | None,
    ) -> AsyncIterator[dict[str, Any]]:
        run_id = prepared["run_id"]
        conversation_id = prepared["conversation_id"]
        model = self.model_factory(
            self.service.models,
            prepared["profile_id"],
            prepared["model"],
        )
        agent = build_zhixu_agent(model)
        deps = self._dependencies(prepared)

        prompt = (
            prepared["prompt"]
            if deferred_results is None
            else None
        )
        output_value: str | DeferredToolRequests | None = None
        message_started = False
        assistant_text: list[str] = []
        reasoning_blocks: list[dict[str, str]] = []
        reasoning_block_serial = 0
        turn_model_settings = self._turn_model_settings(prepared)
        turn_max_tokens = int(
            (turn_model_settings or {}).get(
                "max_tokens",
                prepared["max_tokens"],
            )
        )

        try:
            async with agent.iter(
                prompt,
                deps=deps,
                message_history=history,
                deferred_tool_results=deferred_results,
                instructions=(
                    DEEP_MODE_INSTRUCTIONS
                    if prepared.get("reasoning_mode") == "deep"
                    else None
                ),
                model_settings=turn_model_settings,
                usage_limits=UsageLimits(
                    request_limit=10,
                    tool_calls_limit=24,
                    output_tokens_limit=max(
                        1000,
                        turn_max_tokens * 2,
                    ),
                ),
            ) as run:
                async for node in run:
                    self._raise_if_cancelled(run_id)
                    if Agent.is_model_request_node(node):
                        reasoning_by_part_index: dict[int, dict[str, str]] = {}
                        async with node.stream(run.ctx) as stream:
                            cumulative = ""
                            async for model_event in stream:
                                self._raise_if_cancelled(run_id)
                                if isinstance(model_event, PartStartEvent):
                                    if isinstance(model_event.part, TextPart):
                                        initial = model_event.part.content
                                        if initial:
                                            if not message_started:
                                                message_started = True
                                                yield self._emit(
                                                    run_id,
                                                    "message.started",
                                                    conversationId=conversation_id,
                                                    messageId=prepared[
                                                        "assistant_message_id"
                                                    ],
                                                    role="assistant",
                                                )
                                            cumulative += initial
                                            assistant_text.append(initial)
                                            yield self._emit(
                                                run_id,
                                                "message.delta",
                                                conversationId=conversation_id,
                                                messageId=prepared[
                                                    "assistant_message_id"
                                                ],
                                                delta=initial,
                                            )
                                    elif isinstance(model_event.part, ThinkingPart):
                                        reasoning_block_serial += 1
                                        provider_name = str(
                                            model_event.part.provider_name or ""
                                        )
                                        if (
                                            provider_name == "openai"
                                            and prepared.get("provider_type")
                                            not in {"openai", "openai-compatible"}
                                        ):
                                            provider_name = str(
                                                prepared.get("provider_type") or ""
                                            )
                                        block = {
                                            "id": f"reasoning-{reasoning_block_serial}",
                                            "provider": provider_name or prepared["model"],
                                            "content": "",
                                        }
                                        reasoning_blocks.append(block)
                                        reasoning_by_part_index[model_event.index] = block
                                        yield self._emit(
                                            run_id,
                                            "reasoning.started",
                                            conversationId=conversation_id,
                                            blockId=block["id"],
                                            provider=block["provider"],
                                        )
                                        initial = redact_secret_text(
                                            model_event.part.content or ""
                                        )
                                        if initial:
                                            block["content"] += initial
                                            yield self._emit(
                                                run_id,
                                                "reasoning.delta",
                                                conversationId=conversation_id,
                                                blockId=block["id"],
                                                provider=block["provider"],
                                                delta=initial,
                                            )
                                elif isinstance(model_event, PartDeltaEvent):
                                    if isinstance(
                                        model_event.delta,
                                        TextPartDelta,
                                    ):
                                        delta = (
                                            model_event.delta.content_delta
                                            or ""
                                        )
                                        if delta:
                                            if not message_started:
                                                message_started = True
                                                yield self._emit(
                                                    run_id,
                                                    "message.started",
                                                    conversationId=conversation_id,
                                                    messageId=prepared[
                                                        "assistant_message_id"
                                                    ],
                                                    role="assistant",
                                                )
                                            cumulative += delta
                                            assistant_text.append(delta)
                                            yield self._emit(
                                                run_id,
                                                "message.delta",
                                                conversationId=conversation_id,
                                                messageId=prepared[
                                                    "assistant_message_id"
                                                ],
                                                delta=delta,
                                            )
                                    elif isinstance(
                                        model_event.delta,
                                        ThinkingPartDelta,
                                    ):
                                        block = reasoning_by_part_index.get(
                                            model_event.index
                                        )
                                        delta = redact_secret_text(
                                            model_event.delta.content_delta or ""
                                        )
                                        if block is not None and delta:
                                            block["content"] += delta
                                            yield self._emit(
                                                run_id,
                                                "reasoning.delta",
                                                conversationId=conversation_id,
                                                blockId=block["id"],
                                                provider=block["provider"],
                                                delta=delta,
                                            )
                                elif isinstance(model_event, PartEndEvent):
                                    if isinstance(model_event.part, ThinkingPart):
                                        block = reasoning_by_part_index.pop(
                                            model_event.index,
                                            None,
                                        )
                                        if block is not None:
                                            yield self._emit(
                                                run_id,
                                                "reasoning.completed",
                                                conversationId=conversation_id,
                                                blockId=block["id"],
                                                provider=block["provider"],
                                            )

                    elif Agent.is_call_tools_node(node):
                        async with node.stream(run.ctx) as tool_stream:
                            async for tool_event in tool_stream:
                                self._raise_if_cancelled(run_id)
                                if isinstance(
                                    tool_event,
                                    FunctionToolCallEvent,
                                ):
                                    yield self._emit(
                                        run_id,
                                        "tool.started",
                                        conversationId=conversation_id,
                                        callId=tool_event.part.tool_call_id,
                                        tool=tool_event.part.tool_name,
                                        arguments=self._public_tool_arguments(
                                            tool_event.part.args
                                        ),
                                        input=self._public_tool_arguments(
                                            tool_event.part.args
                                        ),
                                        label=self._tool_label(
                                            tool_event.part.tool_name
                                        ),
                                    )
                                elif isinstance(
                                    tool_event,
                                    FunctionToolResultEvent,
                                ):
                                    raw_result = tool_event.part.content
                                    if isinstance(raw_result, BaseModel):
                                        raw_result = raw_result.model_dump(
                                            mode="json"
                                        )
                                    failed = (
                                        isinstance(
                                            tool_event.part,
                                            RetryPromptPart,
                                        )
                                        or (
                                            isinstance(raw_result, dict)
                                            and raw_result.get("ok") is False
                                        )
                                    )
                                    public_result = self._public_tool_result(
                                        tool_event.part.tool_name,
                                        tool_event.part.content
                                    )
                                    yield self._emit(
                                        run_id,
                                        "tool.completed",
                                        conversationId=conversation_id,
                                        callId=tool_event.tool_call_id,
                                        tool=tool_event.part.tool_name,
                                        status="failed" if failed else "completed",
                                        summary=self._summarize_tool_result(
                                            tool_event.part.tool_name,
                                            tool_event.part.content
                                        ),
                                        result=public_result,
                                    )
                                    if (
                                        not failed
                                        and tool_event.part.tool_name
                                        == "propose_vault_change"
                                        and public_result.get("proposalId")
                                    ):
                                        yield self._emit(
                                            run_id,
                                            "write.diff",
                                            conversationId=conversation_id,
                                            callId=tool_event.tool_call_id,
                                            proposalId=public_result["proposalId"],
                                            title=public_result.get("title", ""),
                                            writes=public_result.get("writes", []),
                                        )

                    elif Agent.is_end_node(node):
                        output_value = node.data.output

                if run.result is None:
                    raise RuntimeError("pydantic_agent_result_missing")

                history_json = run.result.all_messages_json()
                self.persistence.save_history(
                    run_id,
                    history_json,
                )

                if isinstance(
                    output_value,
                    DeferredToolRequests,
                ):
                    confirmation = self._confirmation_from_deferred(
                        run_id,
                        output_value,
                    )
                    deferred_json = _DEFERRED_ADAPTER.dump_json(
                        output_value
                    )
                    self.persistence.save_pending(
                        run_id,
                        history_json,
                        deferred_json,
                        confirmation,
                    )
                    yield self._emit(
                        run_id,
                        "inline.confirmation.required",
                        conversationId=conversation_id,
                        confirmation=confirmation.model_dump(),
                    )
                    yield self._emit(
                        run_id,
                        "run.waiting_confirmation",
                        conversationId=conversation_id,
                        proposalId=confirmation.proposal_id,
                        kind=confirmation.kind,
                    )
                    return

                final_text = (
                    output_value
                    if isinstance(output_value, str)
                    else "".join(assistant_text).strip()
                )
                if not final_text:
                    raise RuntimeError("empty_model_response")

                final_text = redact_secret_text(final_text)
                if not message_started:
                    message_started = True
                    yield self._emit(
                        run_id,
                        "message.started",
                        conversationId=conversation_id,
                        messageId=prepared["assistant_message_id"],
                        role="assistant",
                    )
                    yield self._emit(
                        run_id,
                        "message.delta",
                        conversationId=conversation_id,
                        messageId=prepared["assistant_message_id"],
                        delta=final_text,
                    )
                stored = self.service.intake.append_message(
                    conversation_id,
                    "assistant",
                    final_text,
                    "markdown",
                    reasoning_blocks=reasoning_blocks,
                )
                usage = run.result.usage
                yield self._emit(
                    run_id,
                    "usage.updated",
                    conversationId=conversation_id,
                    requests=int(usage.requests or 0),
                    inputTokens=int(usage.input_tokens or 0),
                    outputTokens=int(usage.output_tokens or 0),
                    totalTokens=int(usage.total_tokens or 0),
                )
                self.persistence.finish(run_id, "completed")
                yield self._emit(
                    run_id,
                    "message.completed",
                    conversationId=conversation_id,
                    message=stored,
                    streamMessageId=prepared[
                        "assistant_message_id"
                    ],
                )
                yield self._emit(
                    run_id,
                    "run.completed",
                    conversationId=conversation_id,
                    messageId=stored["id"],
                    model=prepared["model"],
                )
        except asyncio.CancelledError:
            self.persistence.finish(run_id, "cancelled")
            yield self._emit(
                run_id,
                "run.cancelled",
                conversationId=conversation_id,
                reason="user_cancelled",
            )
        except Exception as error:
            self.persistence.finish(run_id, "failed")
            yield self._emit(
                run_id,
                "run.failed",
                conversationId=conversation_id,
                code=type(error).__name__,
                message=redact_secret_text(str(error))[:1000],
                partial=bool(assistant_text),
            )
        finally:
            with self._cancel_lock:
                self._cancelled.discard(run_id)

    def _raise_if_cancelled(self, run_id: str) -> None:
        with self._cancel_lock:
            if run_id in self._cancelled:
                raise asyncio.CancelledError

    def _prepare_request(
        self,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        message = redact_secret_text(str(body.get("message") or "").strip())
        regenerate_message_id = str(
            body.get("regenerate_message_id") or ""
        ).strip()
        if (not message and not regenerate_message_id) or len(message) > 250_000:
            raise ValueError(
                "message must contain 1–250000 characters"
            )

        conversation_id = self.service.intake.ensure_conversation(
            str(body.get("conversation_id") or "") or None,
            self.service._conversation_title(message),
        )
        if regenerate_message_id:
            user_row = self.service.intake.get_message(
                conversation_id,
                regenerate_message_id,
            )
            if user_row.get("role") != "user":
                raise ValueError("regenerate_message_must_be_user")
            message = str(user_row.get("content") or "").strip()
        else:
            user_row = self.service.intake.append_message(
                conversation_id,
                "user",
                message,
            )

        raw_attachment_ids = [
            str(
                item.get("attachment_id")
                if isinstance(item, dict)
                else item
            )
            for item in body.get("attachments", [])
        ][:20]
        attachments = []
        for attachment_id in raw_attachment_ids:
            attachment = self.service.intake.get_attachment(
                attachment_id
            )
            if attachment["conversationId"] != conversation_id:
                raise ValueError(
                    "attachment_conversation_mismatch"
                )
            attachments.append(attachment)

        routes = self.service.model_routing()
        route = routes.get("assistant_chat") or routes.get(
            "assistant",
            {},
        )
        profile_id = str(
            body.get("profile_id")
            or route.get("profileId")
            or ""
        )
        if not profile_id:
            raise RuntimeError(
                "No assistant model profile is configured"
            )
        profile = next(
            (
                item
                for item in self.service.store.list_model_profiles()
                if item["id"] == profile_id
            ),
            None,
        )
        if not profile or not profile["enabled"]:
            raise RuntimeError(
                "Assistant model profile is unavailable"
            )

        model = str(
            body.get("model")
            or route.get("modelOverride")
            or profile["defaultModel"]
        )
        settings = normalized_model_settings(profile)
        active_note = (
            body.get("active_note")
            if isinstance(body.get("active_note"), dict)
            else {}
        )
        focus = self.service.store.get_conversation_focus(
            conversation_id
        ) or {}
        understanding: dict[str, Any] = {}
        sources = [
            {
                "id": item.get("id"),
                "title": item.get("displayName"),
                "kind": item.get("kind"),
                "status": item.get("status"),
            }
            for item in attachments
        ]
        if active_note.get("path"):
            sources.append(
                {
                    "id": "active-note",
                    "title": str(active_note.get("path")),
                    "kind": "vault_note",
                    "status": "local",
                }
            )

        options = (
            body.get("options")
            if isinstance(body.get("options"), dict)
            else {}
        )
        reasoning_mode = str(
            options.get("reasoning_mode") or "auto"
        )
        if reasoning_mode not in {"auto", "deep"}:
            reasoning_mode = "auto"
        run_id = f"run-{uuid.uuid4().hex}"
        assistant_message_id = f"msg-{uuid.uuid4().hex}"
        session_roots = self.service.store.get_setting(
            f"assistant_session_allow_roots:{conversation_id}",
            [],
        )
        if not isinstance(session_roots, list):
            session_roots = []
        recent = self.service.intake.recent_messages(
            conversation_id,
            12,
        )
        prompt_context = redact_model_context({
            "conversationId": conversation_id,
            "conversationFocus": focus,
            "recentMessages": [
                {
                    "role": str(item.get("role") or ""),
                    "content": str(item.get("content") or "")[:4_000],
                }
                for item in recent
            ],
            "activeNote": {
                "path": str(active_note.get("path") or ""),
                "selectionAvailable": bool(
                    str(active_note.get("selection") or "").strip()
                ),
            },
            "attachments": sources,
            "permissionMode": "commit-default-ask",
            "sessionAllowCreateRoots": session_roots,
            "networkAuthorized": options.get("allow_network") is True,
            "reasoningMode": reasoning_mode,
        })
        prompt = (
            f"{message}\n\n"
            "<runtime-context>\n"
            f"{json.dumps(prompt_context, ensure_ascii=False)}\n"
            "</runtime-context>"
        )

        request_snapshot = redact_model_context({
            "body": {
                "message": message,
                "conversation_id": conversation_id,
                "profile_id": profile_id,
                "model": model,
                "active_note": active_note,
                "attachments": raw_attachment_ids,
                "options": options,
            },
            "focus": focus,
            "understanding": understanding,
            "sources": sources,
            "session_allow_create_roots": session_roots,
            "assistant_message_id": assistant_message_id,
            "prompt": prompt,
        })
        return {
            "run_id": run_id,
            "conversation_id": conversation_id,
            "profile_id": profile_id,
            "model": model,
            "assistant_message_id": assistant_message_id,
            "prompt": prompt,
            "focus": focus,
            "understanding": understanding,
            "sources": sources,
            "active_note": active_note,
            "attachment_ids": raw_attachment_ids,
            "session_allow_create_roots": session_roots,
            "allow_network": options.get("allow_network") is True,
            "reasoning_mode": reasoning_mode,
            "provider_type": str(
                profile.get("providerType") or "openai-compatible"
            ),
            "max_tokens": int(settings.get("maxTokens", 3000)),
            "request_snapshot": request_snapshot,
        }

    def _restore_request(self, snapshot) -> dict[str, Any]:
        request = snapshot.request
        body = request["body"]
        profile = next(
            (
                item
                for item in self.service.store.list_model_profiles()
                if item["id"] == snapshot.profile_id
            ),
            {},
        )
        settings = normalized_model_settings(profile)
        options = body.get("options") or {}
        reasoning_mode = str(options.get("reasoning_mode") or "auto")
        if reasoning_mode not in {"auto", "deep"}:
            reasoning_mode = "auto"
        return {
            "run_id": snapshot.run_id,
            "conversation_id": snapshot.conversation_id,
            "profile_id": snapshot.profile_id,
            "model": snapshot.model,
            "assistant_message_id": request[
                "assistant_message_id"
            ],
            "prompt": request["prompt"],
            "focus": request.get("focus") or {},
            "understanding": request.get("understanding") or {},
            "sources": request.get("sources") or [],
            "active_note": body.get("active_note") or {},
            "attachment_ids": body.get("attachments") or [],
            "session_allow_create_roots": self.service.store.get_setting(
                f"assistant_session_allow_roots:{snapshot.conversation_id}",
                request.get("session_allow_create_roots") or [],
            ),
            "allow_network": bool(
                options.get("allow_network")
            ),
            "reasoning_mode": reasoning_mode,
            "provider_type": str(
                profile.get("providerType") or "openai-compatible"
            ),
            "max_tokens": int(settings.get("maxTokens", 3000)),
            "request_snapshot": request,
        }

    def _dependencies(
        self,
        prepared: dict[str, Any],
    ) -> ZhixuDependencies:
        active_note = prepared.get("active_note") or {}
        return ZhixuDependencies(
            vault=self.service.vault,
            store=self.service.store,
            intake=self.service.intake,
            tool_registry=self.service.tools,
            change_sets=self.service.brain_change_sets,
            run_id=prepared["run_id"],
            conversation_id=prepared["conversation_id"],
            active_note_path=str(
                active_note.get("path") or ""
            ),
            active_selection=str(
                active_note.get("selection") or ""
            ),
            attachment_ids=list(
                prepared.get("attachment_ids") or []
            ),
            allow_network=bool(
                prepared.get("allow_network")
            ),
            fetch_public_web=self.service.fetch_public_web,
            search_public_web=self.service.search_public_web,
            search_academic_web=self.service.search_academic_web,
            session_allow_create_roots=list(
                prepared.get("session_allow_create_roots") or []
            ),
        )

    def _confirmation_from_deferred(
        self,
        run_id: str,
        requests: DeferredToolRequests,
    ) -> InlineConfirmation:
        if requests.calls:
            call = requests.calls[0]
            metadata = (
                requests.metadata.get(call.tool_call_id, {})
                if requests.metadata
                else {}
            )
            arguments = call.args if isinstance(call.args, dict) else {}
            options = metadata.get("options") or arguments.get("options") or []
            return InlineConfirmation(
                run_id=run_id,
                kind="question",
                title=str(metadata.get("title") or "知序需要你的选择"),
                summary=str(metadata.get("reason") or "回答后将继续同一个任务"),
                risk_level="low",
                tool_name="ask_user",
                question=str(
                    metadata.get("question")
                    or arguments.get("question")
                    or "请提供继续任务所需的信息"
                ),
                options=[str(item) for item in options][:8],
                reason=str(metadata.get("reason") or ""),
                actions=["answer", "cancel"],
            )
        if not requests.approvals:
            raise RuntimeError("deferred_request_has_no_supported_call")
        call = requests.approvals[0]
        metadata = (
            requests.metadata.get(call.tool_call_id, {})
            if requests.metadata
            else {}
        )
        proposal_id = str(
            metadata.get("proposalId")
            or (
                call.args.get("proposal_id")
                if isinstance(call.args, dict)
                else ""
            )
            or ""
        )
        return InlineConfirmation(
            run_id=run_id,
            kind="write",
            proposal_id=proposal_id,
            title=str(
                metadata.get("title")
                or "确认修改 Obsidian"
            ),
            summary=str(
                metadata.get("summary")
                or metadata.get("reason")
                or "该操作会更新已有笔记"
            ),
            risk_level=(
                "high"
                if metadata.get("riskLevel") == "high"
                else "medium"
            ),
            writes=list(metadata.get("writes") or []),
            reason=str(metadata.get("reason") or ""),
            scope_candidates=[
                str(item) for item in metadata.get("scopeCandidates") or []
            ],
        )

    def _emit(
        self,
        run_id: str,
        event_type: str,
        **payload: Any,
    ) -> dict[str, Any]:
        return self.persistence.append_event(
            run_id,
            event_type,
            payload,
        )

    @staticmethod
    def _summarize_tool_result(tool_name: str, content: Any) -> str:
        if isinstance(content, BaseModel):
            content = content.model_dump(mode="json")
        if isinstance(content, dict):
            if content.get("ok") is False:
                error = content.get("error")
                code = (
                    str(error.get("code") or "tool_error")
                    if isinstance(error, dict)
                    else "tool_error"
                )
                return f"工具未完成 · {code[:160]}"
            if tool_name in {"get_current_note", "read_vault_note"}:
                path = str(content.get("path") or "已授权笔记")
                excerpt = str(content.get("excerpt") or "")
                return f"读取笔记 {path} · {len(excerpt)} 字符"
            if tool_name == "get_current_selection":
                return f"读取当前选区 · {len(str(content.get('selection') or ''))} 字符"
            if tool_name == "get_recent_conversation_messages":
                return f"读取最近 {len(content.get('items') or [])} 条对话"
            if tool_name == "get_conversation_focus":
                return "读取会话焦点"
            if tool_name == "list_vault_folder":
                items = content.get("items") or []
                folders = sum(
                    isinstance(item, dict)
                    and item.get("kind") == "folder"
                    for item in items
                )
                notes = len(items) - folders
                if folders and notes:
                    return f"列出文件夹 · {folders} 个目录、{notes} 篇笔记"
                if folders:
                    return f"列出文件夹 · {folders} 个目录"
                return f"列出文件夹 · {notes} 篇笔记"
            if tool_name == "get_vault_overview":
                return "读取知识库概览"
            if tool_name == "get_learning_state":
                return f"读取 {len(content.get('items') or [])} 条学习状态"
            if tool_name == "get_due_reviews":
                return f"找到 {len(content.get('items') or [])} 条到期复习"
            if tool_name == "get_recent_materials":
                return f"找到 {len(content.get('items') or [])} 条近期资料"
            if tool_name == "ask_user":
                return "已收到用户回答"
            if tool_name == "get_attachment_metadata":
                return f"读取附件元数据 · {str(content.get('displayName') or '附件')}"
            if tool_name in {"search_public_web", "search_academic_sources"}:
                count = len(content.get("results") or [])
                provider = str(content.get("provider") or "联网来源")
                label = "检索学术来源" if tool_name == "search_academic_sources" else "搜索公开网页"
                return f"{label} · {count} 条 · {provider}"
            if tool_name == "fetch_public_url":
                title = str(content.get("title") or content.get("canonicalUrl") or "公开网页")
                evidence = str(content.get("evidenceText") or "")
                return f"读取网页 {title[:80]} · {len(evidence)} 字符证据"
            if tool_name == "get_current_datetime":
                return f"当前时间 · {str(content.get('iso') or '')}"
            if isinstance(content.get("items"), list):
                return f"返回 {len(content['items'])} 项"
            if isinstance(content.get("pages"), list):
                return (
                    f"读取第 {content.get('pageStart')}–"
                    f"{content.get('pageEnd')} 页"
                )
            if content.get("message"):
                return str(content["message"])[:240]
            if content.get("preview"):
                return str(content["preview"])[:240]
            if content.get("available") is False:
                return f"工具不可用 · {str(content.get('reason') or '无可用上下文')}"
        return "工具执行完成"

    @staticmethod
    def _public_tool_arguments(arguments: Any) -> dict[str, Any]:
        value = arguments
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                return {"chars": len(value)}
        if not isinstance(value, dict):
            return {}
        public = redact_model_context(value)
        writes = public.get("writes") if isinstance(public, dict) else None
        if isinstance(writes, list):
            bounded = []
            for item in writes[:10]:
                if not isinstance(item, dict):
                    continue
                raw_content = str(item.get("content") or "")
                bounded.append({
                    "path": str(item.get("path") or ""),
                    "category": str(item.get("category") or ""),
                    "contentChars": len(raw_content),
                    "contentSha256": hashlib.sha256(
                        raw_content.encode("utf-8")
                    ).hexdigest()[:16],
                })
            public["writes"] = bounded
        return public

    @staticmethod
    def _public_tool_result(tool_name: str, content: Any) -> dict[str, Any]:
        value = content.model_dump(mode="json") if isinstance(content, BaseModel) else content
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                return {}
        if not isinstance(value, dict):
            return {}
        if value.get("ok") is False:
            error = value.get("error")
            if not isinstance(error, dict):
                error = {}
            return {
                "ok": False,
                "error": {
                    "code": str(error.get("code") or "tool_error")[:160],
                    "type": str(error.get("type") or "")[:80],
                    "recoverable": error.get("recoverable") is True,
                    "retryable": error.get("retryable") is True,
                },
            }
        if value.get("available") is False:
            return {
                "available": False,
                "reason": str(value.get("reason") or "tool_unavailable")[:160],
            }
        if tool_name in {"search_public_web", "search_academic_sources"}:
            results = []
            for item in list(value.get("results") or [])[:10]:
                if not isinstance(item, dict):
                    continue
                results.append({
                    "url": str(item.get("url") or "")[:2000],
                    "title": str(item.get("title") or "")[:300],
                    "snippet": str(item.get("snippet") or "")[:240],
                    "domain": str(item.get("domain") or "")[:200],
                    "provider": str(item.get("provider") or "")[:80],
                    "sourceType": str(item.get("sourceType") or "")[:80],
                    "publishedAt": str(item.get("publishedAt") or "")[:80],
                    "qualityScore": float(item.get("qualityScore") or 0),
                })
            return {
                "query": str(value.get("query") or "")[:500],
                "provider": str(value.get("provider") or "")[:80],
                "results": results,
            }
        if tool_name == "fetch_public_url":
            return {
                "source": {
                    "id": str(value.get("id") or "")[:120],
                    "url": str(value.get("canonicalUrl") or "")[:2000],
                    "title": str(value.get("title") or "")[:300],
                    "domain": str(value.get("domain") or "")[:200],
                    "sourceType": str(value.get("sourceType") or "")[:80],
                    "qualityScore": float(value.get("qualityScore") or 0),
                    "publishedAt": str(value.get("publishedAt") or "")[:80],
                    "promptInjectionDetected": value.get("promptInjectionDetected") is True,
                }
            }
        proposal_id = str(
            value.get("proposal_id") or value.get("proposalId") or ""
        )
        if not proposal_id:
            return {}
        writes = []
        for item in list(value.get("writes") or [])[:10]:
            if isinstance(item, dict):
                writes.append({
                    "path": str(item.get("path") or ""),
                    "action": str(item.get("action") or ""),
                    "category": str(item.get("category") or ""),
                })
        verification = value.get("verification") if isinstance(value.get("verification"), dict) else {}
        verification_files = []
        for item in list(verification.get("files") or [])[:10]:
            if isinstance(item, dict):
                verification_files.append({
                    "path": str(item.get("path") or ""),
                    "sha256": str(item.get("sha256") or "")[:64],
                    "match": item.get("match") is True,
                })
        return {
            "proposalId": proposal_id,
            "title": str(value.get("title") or ""),
            "preview": str(value.get("preview") or "")[:500],
            "writes": writes,
            "state": str(value.get("state") or ""),
            "transactionId": str(value.get("transaction_id") or ""),
            "verification": {
                "verified": verification.get("verified") is True,
                "files": verification_files,
            } if verification else {},
        }

    @staticmethod
    def _tool_label(name: str) -> str:
        return {
            "get_current_note": "读取当前笔记",
            "get_current_selection": "读取当前选区",
            "search_vault": "搜索知识库",
            "get_vault_overview": "读取知识库概览",
            "list_vault_folder": "列出知识库文件夹",
            "read_vault_note": "读取笔记正文",
            "find_related_notes": "查找相关笔记",
            "get_learning_state": "读取学习状态",
            "get_due_reviews": "读取到期复习",
            "get_recent_materials": "查找最近资料",
            "get_conversation_focus": "读取会话焦点",
            "get_recent_conversation_messages": "读取最近对话",
            "get_attachment_metadata": "读取附件信息",
            "read_pdf_pages": "读取 PDF 页面",
            "search_pdf": "搜索 PDF",
            "search_public_web": "搜索公开网页",
            "search_academic_sources": "检索学术来源",
            "fetch_public_url": "读取公开网页",
            "get_current_datetime": "读取当前时间",
            "ask_user": "询问用户",
            "propose_vault_change": "生成修改方案",
            "commit_vault_change": "提交 Obsidian 修改",
        }.get(name, name)
