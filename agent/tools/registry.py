from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from agent.core.redaction import summary
from agent.core import learning

from .base import ToolDefinition, ToolHandler
from .change_set import ChangeSetTools
from .source_fetch import fetch_user_url
from .source_search import ImportedMaterialProvider, ResearchProvider, VaultResearchProvider, search_sources
from .vault_access import VaultReadIndex, read_note_excerpt, read_note_metadata, related_notes, search_vault, vault_overview


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

    def model_specs(self, names: list[str] | tuple[str, ...], *, allow_mutating: bool = False) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for name in dict.fromkeys(names):
            definition = self.definition(name)
            if definition.mutates_state and not allow_mutating:
                continue
            result.append(definition.model_spec())
        return result

    def call(self, name: str, payload: dict[str, Any], *, run_id: str, step_id: str = "") -> dict[str, Any]:
        if name not in self._tools:
            raise ValueError(f"unregistered_tool:{name}")
        definition = self._tools[name][0]
        _validate_payload(payload, definition.input_schema)
        event_id = f"tool-{uuid.uuid4().hex}"
        self.store.record_tool_event(event_id, run_id, step_id, name, "running", summary(payload))
        try:
            result = self._tools[name][1](payload)
            self.store.record_tool_event(event_id, run_id, step_id, name, "completed", summary(payload), summary(result))
            return result
        except Exception as error:
            self.store.record_tool_event(event_id, run_id, step_id, name, "failed", summary(payload), summary({"error": str(error)}), type(error).__name__)
            raise


def _validate_payload(value: Any, schema: dict[str, Any], path: str = "input") -> None:
    """Small strict validator for model-facing tool arguments.

    Tool schemas intentionally use a bounded JSON-Schema subset so validation
    stays dependency-free and identical in offline tests and the local Runtime.
    """

    expected = schema.get("type")
    if expected == "object":
        if not isinstance(value, dict):
            raise ValueError(f"invalid_tool_arguments:{path}:object_required")
        properties = dict(schema.get("properties") or {})
        required = set(schema.get("required") or [])
        missing = sorted(required - set(value))
        if missing:
            raise ValueError(f"invalid_tool_arguments:{path}:missing:{','.join(missing)}")
        if schema.get("additionalProperties") is False:
            unknown = sorted(set(value) - set(properties))
            if unknown:
                raise ValueError(f"invalid_tool_arguments:{path}:unknown:{','.join(unknown)}")
        for key, item in value.items():
            if key in properties:
                _validate_payload(item, properties[key], f"{path}.{key}")
        return
    if expected == "array":
        if not isinstance(value, list):
            raise ValueError(f"invalid_tool_arguments:{path}:array_required")
        if "maxItems" in schema and len(value) > int(schema["maxItems"]):
            raise ValueError(f"invalid_tool_arguments:{path}:too_many_items")
        for index, item in enumerate(value):
            _validate_payload(item, dict(schema.get("items") or {}), f"{path}[{index}]")
        return
    type_checks = {
        "string": lambda item: isinstance(item, str),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
        "boolean": lambda item: isinstance(item, bool),
    }
    if expected in type_checks and not type_checks[expected](value):
        raise ValueError(f"invalid_tool_arguments:{path}:{expected}_required")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"invalid_tool_arguments:{path}:unsupported_value")
    if isinstance(value, str) and "maxLength" in schema and len(value) > int(schema["maxLength"]):
        raise ValueError(f"invalid_tool_arguments:{path}:too_long")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise ValueError(f"invalid_tool_arguments:{path}:below_minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise ValueError(f"invalid_tool_arguments:{path}:above_maximum")


def build_tool_registry(vault: Path, store: Any, *, research_providers: list[ResearchProvider] | None = None, allow_network: bool = False) -> ToolRegistry:
    vault = vault.resolve()
    registry = ToolRegistry(store)
    change_sets = ChangeSetTools(vault, store)
    vault_index = VaultReadIndex(vault)
    providers = research_providers or [VaultResearchProvider(vault), ImportedMaterialProvider(vault)]

    empty = {"type": "object", "properties": {}, "additionalProperties": False}
    query = {"type": "object", "properties": {
        "query": {"type": "string", "maxLength": 1000},
        "limit": {"type": "integer", "minimum": 1, "maximum": 50},
    }, "required": ["query"], "additionalProperties": False}
    note_path = {"type": "object", "properties": {
        "path": {"type": "string", "maxLength": 500},
    }, "required": ["path"], "additionalProperties": False}
    note_excerpt = {"type": "object", "properties": {
        "path": {"type": "string", "maxLength": 500},
        "max_chars": {"type": "integer", "minimum": 100, "maximum": 4000},
    }, "required": ["path"], "additionalProperties": False}
    due = {"type": "object", "properties": {
        "limit": {"type": "integer", "minimum": 1, "maximum": 50},
    }, "additionalProperties": False}
    overview = {"type": "object", "properties": {
        "recent_limit": {"type": "integer", "minimum": 3, "maximum": 20},
    }, "additionalProperties": False}
    change_set = {"type": "object", "properties": {
        "run_id": {"type": "string", "maxLength": 128},
        "title": {"type": "string", "maxLength": 200},
        "writes": {"type": "array", "maxItems": 20, "items": {"type": "object"}},
    }, "required": ["run_id", "writes"], "additionalProperties": False}
    change_set_ref = {"type": "object", "properties": {
        "change_set_id": {"type": "string", "maxLength": 128},
        "confirmed": {"type": "boolean"},
    }, "required": ["change_set_id"], "additionalProperties": True}

    def register(name: str, description: str, schema: dict[str, Any], handler: ToolHandler, *, network: bool = False, mutates: bool = False) -> None:
        registry.register(ToolDefinition(name, description, schema, {"type": "object"}, network, mutates), handler)

    register("get_vault_overview", "读取允许知识目录的真实概览、分类计数和最近笔记；不读取私密运行目录或工程代码", overview, lambda p: vault_overview(vault, p, vault_index))
    register("search_vault", "按相关性搜索允许知识目录内的 Vault 笔记；返回标题、路径、正式状态和相关度", query, lambda p: search_vault(vault, p, vault_index))
    register("read_note_metadata", "读取指定 Markdown 笔记的白名单元数据，不返回密钥或本地私密正文", note_path, lambda p: read_note_metadata(vault, p))
    register("read_note_excerpt", "读取指定 Markdown 笔记的有长度上限正文片段", note_excerpt, lambda p: read_note_excerpt(vault, p))
    register("get_related_notes", "读取指定笔记的 Wiki 出链和反向链接", note_path, lambda p: related_notes(vault, p, vault_index))
    register("get_backlinks", "读取指定笔记的反向链接", note_path, lambda p: {"items": related_notes(vault, p, vault_index)["backlinks"]})
    register("get_learning_state", "读取 reviewed/core 知识的学习状态", empty, lambda p: {"items": [learning.serialize(item) for item in learning.scan_reviewed(vault)]})
    register("get_due_reviews", "读取当前到期的 reviewed/core 复习项", due, lambda p: {"items": [learning.serialize(item) for item in learning.due_reviews(learning.scan_reviewed(vault), __import__('datetime').date.today(), int(p.get('limit', 3)))]})
    register("get_recent_materials", "按主题搜索 Vault 中最近导入的资料索引", query, lambda p: search_vault(vault, {"query": p.get("query", ""), "limit": p.get("limit", 10)}, vault_index))
    register("create_change_set", "创建候选写入，不直接修改知识库", change_set, change_sets.create, mutates=True)
    register("validate_change_set", "校验候选写入的路径、hash 和权限", change_set_ref, change_sets.validate)
    register("apply_confirmed_change_set", "事务应用已明确确认的 Change Set", change_set_ref, change_sets.apply, mutates=True)
    register("create_plan_proposal", "保存 proposed 学习计划，不自动确认", {"type": "object", "additionalProperties": True}, lambda p: (store.save_plan_proposal(p) or {"proposal": p}), mutates=True)
    register("save_recommendation_feedback", "保存推荐反馈", {"type": "object", "properties": {"recommendation_id": {"type": "string"}, "action": {"type": "string"}, "details": {"type": "object"}}, "required": ["recommendation_id", "action"], "additionalProperties": False}, lambda p: (store.add_recommendation_feedback(str(p["recommendation_id"]), str(p["action"]), dict(p.get("details", {}))) or {"saved": True}), mutates=True)
    register("search_academic_sources", "搜索本地、已导入及显式配置的学术来源", query, lambda p: search_sources(providers, p), network=any(getattr(provider, "name", "") not in {"local_vault", "imported_source"} for provider in providers))
    if allow_network:
        register("fetch_user_provided_url", "安全获取用户明确提供的公开 URL", {"type": "object", "properties": {"url": {"type": "string", "maxLength": 2000}}, "required": ["url"], "additionalProperties": False}, fetch_user_url, network=True)
    else:
        register("fetch_user_provided_url", "网络访问未启用", {"type": "object", "properties": {"url": {"type": "string", "maxLength": 2000}}, "required": ["url"], "additionalProperties": False}, lambda p: (_ for _ in ()).throw(RuntimeError("generic_web_search_not_configured")), network=True)
    return registry
