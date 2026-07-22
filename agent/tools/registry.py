from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

import ingest_pdf

from agent.core import learning
from agent.core.redaction import summary

from .base import ToolDefinition, ToolHandler
from .change_set import ChangeSetTools
from .source_fetch import fetch_user_url
from .source_search import (
    ImportedMaterialProvider,
    ResearchProvider,
    VaultResearchProvider,
    search_sources,
)
from .vault_access import (
    VaultReadIndex,
    list_vault_folder,
    read_note_excerpt,
    read_note_metadata,
    related_notes,
    search_vault,
    vault_overview,
)


class ToolRegistry:
    def __init__(self, store: Any) -> None:
        self.store = store
        self._tools: dict[str, tuple[ToolDefinition, ToolHandler]] = {}

    def register(self, definition: ToolDefinition, handler: ToolHandler) -> None:
        if definition.name in self._tools:
            raise ValueError(f"duplicate_tool:{definition.name}")
        self._tools[definition.name] = (definition, handler)

    def has(self, name: str) -> bool:
        return name in self._tools

    def definition(self, name: str) -> ToolDefinition:
        if name not in self._tools:
            raise ValueError(f"unregistered_tool:{name}")
        return self._tools[name][0]

    def definitions(self) -> list[dict[str, Any]]:
        return [definition.__dict__ for definition, _ in self._tools.values()]

    def model_specs(
        self,
        names: list[str] | tuple[str, ...],
        *,
        allowed_permissions: tuple[str, ...] = ("read_only",),
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        allowed = set(allowed_permissions)
        for name in dict.fromkeys(names):
            definition = self.definition(name)
            if definition.permission_level not in allowed:
                continue
            result.append(definition.model_spec())
        return result

    def call(
        self,
        name: str,
        payload: dict[str, Any],
        *,
        run_id: str,
        step_id: str = "",
        allowed_permissions: tuple[str, ...] = ("read_only",),
        record_event: bool = True,
    ) -> dict[str, Any]:
        if name not in self._tools:
            raise ValueError(f"unregistered_tool:{name}")

        definition, handler = self._tools[name]
        if definition.permission_level not in set(allowed_permissions):
            raise PermissionError(f"tool_permission_denied:{name}")

        _validate_payload(payload, definition.input_schema)
        input_summary = _tool_input_summary(name, payload)
        event_id = f"tool-{uuid.uuid4().hex}"
        if record_event:
            self.store.record_tool_event(
                event_id,
                run_id,
                step_id,
                name,
                "running",
                input_summary,
            )
        try:
            result = handler(payload)
            if not isinstance(result, dict):
                raise ValueError("tool_output_object_required")
            _validate_payload(result, definition.output_schema)
            encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode()
            if len(encoded) > definition.max_result_bytes:
                raise ValueError("tool_output_too_large")
            if record_event:
                self.store.record_tool_event(
                    event_id,
                    run_id,
                    step_id,
                    name,
                    "completed",
                    input_summary,
                    summary(result),
                )
            return result
        except Exception as error:
            if record_event:
                self.store.record_tool_event(
                    event_id,
                    run_id,
                    step_id,
                    name,
                    "failed",
                    input_summary,
                    summary({"error": type(error).__name__}),
                    type(error).__name__,
                )
            raise


def _tool_input_summary(name: str, payload: dict[str, Any]) -> str:
    if name != "create_change_set":
        return summary(payload)
    writes = []
    for item in list(payload.get("writes") or []):
        content = str(item.get("content") or "")
        writes.append(
            {
                "path": str(item.get("path") or ""),
                "category": str(item.get("category") or ""),
                "content": {
                    "type": "private-text",
                    "chars": len(content),
                    "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest()[:16],
                },
            }
        )
    return summary(
        {
            "run_id": str(payload.get("run_id") or ""),
            "title": str(payload.get("title") or ""),
            "writes": writes,
        }
    )


def _validate_payload(value: Any, schema: dict[str, Any], path: str = "input") -> None:
    expected = schema.get("type")
    if expected == "object":
        if not isinstance(value, dict):
            raise ValueError(f"invalid_tool_arguments:{path}:object_required")
        properties = dict(schema.get("properties") or {})
        required = set(schema.get("required") or [])
        missing = sorted(required - set(value))
        if missing:
            raise ValueError(
                f"invalid_tool_arguments:{path}:missing:{','.join(missing)}"
            )
        if schema.get("additionalProperties") is False:
            unknown = sorted(set(value) - set(properties))
            if unknown:
                raise ValueError(
                    f"invalid_tool_arguments:{path}:unknown:{','.join(unknown)}"
                )
        for key, item in value.items():
            if key in properties:
                _validate_payload(item, properties[key], f"{path}.{key}")
        return

    if expected == "array":
        if not isinstance(value, list):
            raise ValueError(f"invalid_tool_arguments:{path}:array_required")
        if "maxItems" in schema and len(value) > int(schema["maxItems"]):
            raise ValueError(f"invalid_tool_arguments:{path}:too_many_items")
        if "minItems" in schema and len(value) < int(schema["minItems"]):
            raise ValueError(f"invalid_tool_arguments:{path}:too_few_items")
        for index, item in enumerate(value):
            _validate_payload(item, dict(schema.get("items") or {}), f"{path}[{index}]")
        return

    type_checks = {
        "string": lambda item: isinstance(item, str),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "number": lambda item: isinstance(item, (int, float))
        and not isinstance(item, bool),
        "boolean": lambda item: isinstance(item, bool),
    }
    if expected in type_checks and not type_checks[expected](value):
        raise ValueError(f"invalid_tool_arguments:{path}:{expected}_required")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"invalid_tool_arguments:{path}:unsupported_value")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < int(schema["minLength"]):
            raise ValueError(f"invalid_tool_arguments:{path}:too_short")
        if "maxLength" in schema and len(value) > int(schema["maxLength"]):
            raise ValueError(f"invalid_tool_arguments:{path}:too_long")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise ValueError(f"invalid_tool_arguments:{path}:below_minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise ValueError(f"invalid_tool_arguments:{path}:above_maximum")


def build_tool_registry(
    vault: Path,
    store: Any,
    *,
    intake: Any | None = None,
    research_providers: list[ResearchProvider] | None = None,
    allow_network: bool = False,
) -> ToolRegistry:
    vault = vault.resolve()
    registry = ToolRegistry(store)
    change_sets = ChangeSetTools(vault, store)
    vault_index = VaultReadIndex(vault)
    providers = research_providers or [
        VaultResearchProvider(vault),
        ImportedMaterialProvider(vault),
    ]

    any_object = {"type": "object", "additionalProperties": True}
    empty = {"type": "object", "properties": {}, "additionalProperties": False}
    query = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "minLength": 1, "maxLength": 1000},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        },
        "required": ["query"],
        "additionalProperties": False,
    }
    note_path = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "minLength": 1, "maxLength": 500},
        },
        "required": ["path"],
        "additionalProperties": False,
    }
    note_excerpt = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "minLength": 1, "maxLength": 500},
            "offset": {"type": "integer", "minimum": 0, "maximum": 10_000_000},
            "max_chars": {"type": "integer", "minimum": 100, "maximum": 50_000},
        },
        "required": ["path"],
        "additionalProperties": False,
    }
    folder_list = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "minLength": 1, "maxLength": 500},
            "recursive": {"type": "boolean"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            "cursor": {"type": "integer", "minimum": 0, "maximum": 1000000},
        },
        "required": ["path"],
        "additionalProperties": False,
    }
    due = {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        },
        "additionalProperties": False,
    }
    overview = {
        "type": "object",
        "properties": {
            "recent_limit": {"type": "integer", "minimum": 3, "maximum": 20},
        },
        "additionalProperties": False,
    }
    change_set = {
        "type": "object",
        "properties": {
            "run_id": {"type": "string", "minLength": 1, "maxLength": 128},
            "title": {"type": "string", "minLength": 1, "maxLength": 200},
            "writes": {
                "type": "array",
                "minItems": 1,
                "maxItems": 10,
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 500,
                        },
                        "title": {
                            "type": "string",
                            "maxLength": 200,
                        },
                        "content": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 100_000,
                        },
                        "category": {
                            "type": "string",
                            "maxLength": 80,
                        },
                    },
                    "required": ["path", "content"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["run_id", "title", "writes"],
        "additionalProperties": False,
    }
    change_set_ref = {
        "type": "object",
        "properties": {
            "change_set_id": {
                "type": "string",
                "minLength": 1,
                "maxLength": 128,
            },
            "confirmed": {"type": "boolean"},
        },
        "required": ["change_set_id"],
        "additionalProperties": False,
    }
    attachment_ref = {
        "type": "object",
        "properties": {
            "attachment_id": {
                "type": "string",
                "minLength": 1,
                "maxLength": 128,
            },
        },
        "required": ["attachment_id"],
        "additionalProperties": False,
    }
    pdf_pages = {
        "type": "object",
        "properties": {
            "attachment_id": {
                "type": "string",
                "minLength": 1,
                "maxLength": 128,
            },
            "page_start": {"type": "integer", "minimum": 1, "maximum": 10_000},
            "page_end": {"type": "integer", "minimum": 1, "maximum": 10_000},
        },
        "required": ["attachment_id", "page_start", "page_end"],
        "additionalProperties": False,
    }
    pdf_search = {
        "type": "object",
        "properties": {
            "attachment_id": {
                "type": "string",
                "minLength": 1,
                "maxLength": 128,
            },
            "query": {"type": "string", "minLength": 1, "maxLength": 500},
            "limit": {"type": "integer", "minimum": 1, "maximum": 30},
        },
        "required": ["attachment_id", "query"],
        "additionalProperties": False,
    }

    def register(
        name: str,
        description: str,
        schema: dict[str, Any],
        handler: ToolHandler,
        *,
        output_schema: dict[str, Any] | None = None,
        network: bool = False,
        mutates: bool = False,
        permission: str = "read_only",
        idempotent: bool = True,
        cancellable: bool = False,
        timeout: int = 20,
        max_result_bytes: int = 16_000,
    ) -> None:
        registry.register(
            ToolDefinition(
                name=name,
                description=description,
                input_schema=schema,
                output_schema=output_schema or any_object,
                uses_network=network,
                mutates_state=mutates,
                timeout_seconds=timeout,
                permission_level=permission,
                idempotent=idempotent,
                cancellable=cancellable,
                max_result_bytes=max_result_bytes,
            ),
            handler,
        )

    register(
        "get_vault_overview",
        "读取允许知识目录的真实概览、分类计数和最近笔记。",
        overview,
        lambda p: vault_overview(vault, p, vault_index),
    )
    register(
        "search_vault",
        "按标题、别名与正文相关性搜索 Vault；用于主动查找已有知识和避免重复。",
        query,
        lambda p: search_vault(vault, p, vault_index),
    )
    register(
        "list_vault_folder",
        "列出一个已授权 Vault 文件夹中的子目录和 Markdown 笔记；path 为 / 或 . 时只列出模型可见的顶层目录。",
        folder_list,
        lambda p: list_vault_folder(vault, p, vault_index),
        max_result_bytes=48_000,
    )
    register(
        "read_note_metadata",
        "读取指定 Markdown 笔记的白名单元数据。",
        note_path,
        lambda p: read_note_metadata(vault, p),
    )
    register(
        "read_note_excerpt",
        "分页读取指定 Markdown 笔记正文；若 truncated=true，使用 next_offset 继续读取，不能把单页上限误认为笔记或模型上限。",
        note_excerpt,
        lambda p: read_note_excerpt(vault, p),
        max_result_bytes=200_000,
    )
    register(
        "get_related_notes",
        "读取指定笔记的 Wiki 出链、反向链接和相关笔记。",
        note_path,
        lambda p: related_notes(vault, p, vault_index),
    )
    register(
        "get_backlinks",
        "读取指定笔记的反向链接。",
        note_path,
        lambda p: {"items": related_notes(vault, p, vault_index)["backlinks"]},
    )
    register(
        "get_learning_state",
        "读取 reviewed/core 知识的学习状态。",
        empty,
        lambda p: {"items": [learning.serialize(item) for item in learning.scan_reviewed(vault)]},
        max_result_bytes=32_000,
    )
    register(
        "get_due_reviews",
        "读取当前到期的 reviewed/core 复习项。",
        due,
        lambda p: {
            "items": [
                learning.serialize(item)
                for item in learning.due_reviews(
                    learning.scan_reviewed(vault),
                    __import__("datetime").date.today(),
                    int(p.get("limit", 3)),
                )
            ]
        },
    )
    register(
        "get_recent_materials",
        "按主题搜索 Vault 中最近导入的资料索引。",
        query,
        lambda p: search_vault(
            vault,
            {"query": p.get("query", ""), "limit": p.get("limit", 10)},
            vault_index,
        ),
    )
    register(
        "search_academic_sources",
        "搜索本地、已导入及显式配置的学术来源。",
        query,
        lambda p: search_sources(providers, p),
        network=any(
            getattr(provider, "name", "")
            not in {"local_vault", "imported_source"}
            for provider in providers
        ),
        max_result_bytes=32_000,
    )

    if allow_network:
        register(
            "fetch_user_provided_url",
            "安全获取用户明确提供的公开 URL。",
            {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "minLength": 8,
                        "maxLength": 2000,
                    }
                },
                "required": ["url"],
                "additionalProperties": False,
            },
            fetch_user_url,
            network=True,
            timeout=30,
            max_result_bytes=48_000,
        )

    if intake is not None:
        def attachment_meta(payload: dict[str, Any]) -> dict[str, Any]:
            item = intake.get_attachment(str(payload["attachment_id"]))
            return {
                "id": item["id"],
                "conversationId": item["conversationId"],
                "kind": item["kind"],
                "displayName": item["displayName"],
                "mimeType": item["mimeType"],
                "sizeBytes": item["sizeBytes"],
                "status": item["status"],
            }

        def read_pdf(payload: dict[str, Any]) -> dict[str, Any]:
            attachment_id = str(payload["attachment_id"])
            item = intake.get_attachment(attachment_id)
            if item.get("kind") != "pdf":
                raise ValueError("attachment_is_not_pdf")
            pages = ingest_pdf.extract_pages(intake.resolve_attachment_path(attachment_id))
            start = int(payload["page_start"])
            end = int(payload["page_end"])
            if start > end:
                raise ValueError("pdf_page_range_invalid")
            if start > len(pages):
                raise ValueError("pdf_page_out_of_range")
            end = min(end, len(pages))
            selected = [
                {"page": index + 1, "text": pages[index][:12_000]}
                for index in range(start - 1, end)
            ]
            return {
                "attachmentId": attachment_id,
                "displayName": item["displayName"],
                "pageCount": len(pages),
                "pageStart": start,
                "pageEnd": end,
                "pages": selected,
            }

        def search_pdf_pages(payload: dict[str, Any]) -> dict[str, Any]:
            attachment_id = str(payload["attachment_id"])
            item = intake.get_attachment(attachment_id)
            if item.get("kind") != "pdf":
                raise ValueError("attachment_is_not_pdf")
            query_text = str(payload["query"]).casefold()
            limit = int(payload.get("limit", 10))
            pages = ingest_pdf.extract_pages(intake.resolve_attachment_path(attachment_id))
            matches = []
            for index, text in enumerate(pages):
                pos = text.casefold().find(query_text)
                if pos < 0:
                    continue
                left = max(0, pos - 300)
                right = min(len(text), pos + len(query_text) + 700)
                matches.append(
                    {
                        "page": index + 1,
                        "excerpt": text[left:right],
                    }
                )
                if len(matches) >= limit:
                    break
            return {
                "attachmentId": attachment_id,
                "displayName": item["displayName"],
                "query": payload["query"],
                "items": matches,
            }

        register(
            "get_attachment_metadata",
            "读取当前会话附件的真实元数据。",
            attachment_ref,
            attachment_meta,
        )
        register(
            "read_pdf_pages",
            "读取 PDF 的指定页码范围并保留页码来源。",
            pdf_pages,
            read_pdf,
            timeout=45,
            max_result_bytes=64_000,
        )
        register(
            "search_pdf",
            "在 PDF 页面中搜索术语，返回真实页码和摘录。",
            pdf_search,
            search_pdf_pages,
            timeout=45,
            max_result_bytes=48_000,
        )
        register(
            "get_conversation_focus",
            "读取当前会话的结构化主题、方法、材料和写入目标。",
            {
                "type": "object",
                "properties": {
                    "conversation_id": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 128,
                    }
                },
                "required": ["conversation_id"],
                "additionalProperties": False,
            },
            lambda p: store.get_conversation_focus(str(p["conversation_id"])) or {},
        )
        register(
            "get_recent_conversation_messages",
            "读取当前会话最近消息；用于解析“这个方法”“刚才那个”等指代。",
            {
                "type": "object",
                "properties": {
                    "conversation_id": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 128,
                    },
                    "limit": {"type": "integer", "minimum": 1, "maximum": 30},
                },
                "required": ["conversation_id"],
                "additionalProperties": False,
            },
            lambda p: {
                "items": intake.recent_messages(
                    str(p["conversation_id"]),
                    int(p.get("limit", 12)),
                )
            },
            max_result_bytes=32_000,
        )

    register(
        "create_change_set",
        "只创建候选 Change Set、Diff 与审批记录；绝不直接修改 Vault。写入任务在读取目标和相关内容后调用。",
        change_set,
        change_sets.create,
        mutates=True,
        permission="proposal",
        idempotent=False,
        max_result_bytes=32_000,
    )
    register(
        "create_plan_proposal",
        "保存 proposed 学习计划；不会自动确认或排入正式计划。",
        {"type": "object", "additionalProperties": True},
        lambda p: (store.save_plan_proposal(p) or {"proposal": p}),
        mutates=True,
        permission="proposal",
        idempotent=False,
        max_result_bytes=32_000,
    )
    register(
        "save_recommendation_feedback",
        "保存用户对推荐结果的明确反馈。",
        {
            "type": "object",
            "properties": {
                "recommendation_id": {"type": "string", "minLength": 1},
                "action": {"type": "string", "minLength": 1},
                "details": {"type": "object", "additionalProperties": True},
            },
            "required": ["recommendation_id", "action"],
            "additionalProperties": False,
        },
        lambda p: (
            store.add_recommendation_feedback(
                str(p["recommendation_id"]),
                str(p["action"]),
                dict(p.get("details", {})),
            )
            or {"saved": True}
        ),
        mutates=True,
        permission="proposal",
        idempotent=False,
    )
    register(
        "validate_change_set",
        "校验候选写入的路径、base hash 和权限。",
        change_set_ref,
        change_sets.validate,
        permission="approval_required",
    )
    return registry
