from __future__ import annotations

import json
import urllib.error
from typing import Any

from .errors import BrainError
CURRICULUM_SCHEMA = {
    "name": "daily_curriculum_candidates",
    "schema": {
        "type": "object",
        "properties": {
            "candidates": {
                "type": "array", "maxItems": 3,
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "kind": {"type": "string", "enum": ["prerequisite", "bridge", "comparison", "core", "application", "exploration"]},
                        "domain": {"type": "string"},
                        "route": {"type": "string", "enum": ["mainline", "branch"]},
                        "prerequisites": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
                        "related_topics": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
                        "why_now": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 4},
                        "learning_outcomes": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 5},
                        "estimated_minutes": {"type": "integer", "minimum": 5, "maximum": 30},
                        "difficulty": {"type": "string", "enum": ["easy", "medium", "hard"]},
                        "mainline_score": {"type": "number", "minimum": 0, "maximum": 1},
                        "gap_score": {"type": "number", "minimum": 0, "maximum": 1},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    },
                    "required": ["title", "kind", "domain", "route", "prerequisites", "related_topics", "why_now", "learning_outcomes", "estimated_minutes", "difficulty", "mainline_score", "gap_score", "confidence"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["candidates"],
        "additionalProperties": False,
    },
}


def parse_model_json(response: dict[str, Any]) -> dict[str, Any]:
    content: Any = (((response.get("choices") or [{}])[0].get("message") or {}).get("content"))
    if isinstance(content, dict):
        return content
    if not isinstance(content, str) or not content.strip():
        raise BrainError("brain_invalid_model_output", "模型没有返回结构化内容", True, "重试或更换支持 Structured Output 的模型")
    value = content.strip()
    if value.startswith("```"):
        lines = value.splitlines()
        if lines and lines[0].startswith("```"): lines = lines[1:]
        if lines and lines[-1].strip() == "```": lines = lines[:-1]
        value = "\n".join(lines).strip()
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise BrainError("brain_invalid_model_output", "模型返回的 JSON 无法解析", True, "重试或更换模型") from error
    if not isinstance(parsed, dict):
        raise BrainError("brain_invalid_model_output", "模型结构化结果必须是对象", True, "重试或更换模型")
    return parsed


class BrainModelGateway:
    """Single model boundary for Brain. It never stores prompts, keys or headers."""

    def __init__(self, model_profiles: Any) -> None:
        self.models = model_profiles

    def _route(self, task: str, aliases: tuple[str, ...] = ()) -> tuple[str, str] | None:
        routes = self.models.routing()
        route = next((routes.get(name) for name in (task, *aliases) if routes.get(name, {}).get("profileId")), None)
        if not route:
            return None
        profile_id = str(route["profileId"])
        profile = next((item for item in self.models.store.list_model_profiles() if item["id"] == profile_id and item["enabled"]), None)
        if not profile:
            raise BrainError("brain_model_unavailable", "所选模型配置不可用", True, "检查模型路由和 Profile 状态")
        model = str(route.get("modelOverride") or profile["defaultModel"])
        if not model:
            raise BrainError("brain_model_not_configured", "模型名称尚未选择", False, "在模型设置中选择模型")
        return profile_id, model

    def structured(self, task: str, messages: list[dict[str, str]], schema: dict[str, Any], *, aliases: tuple[str, ...] = ()) -> dict[str, Any] | None:
        route = self._route(task, aliases)
        if route is None:
            return None
        profile_id, model = route
        try:
            provider = self.models.provider(profile_id)
            response = provider.structured_output(model, messages, schema, temperature=0, max_tokens=800)
            return parse_model_json(response)
        except BrainError:
            raise
        except urllib.error.HTTPError as error:
            if error.code in {401, 403}:
                raise BrainError("model_authentication_failed", "模型认证失败", False, "检查 Keychain Reference 和 API Key") from None
            if error.code == 429:
                raise BrainError("model_rate_limited", "模型请求受到速率限制", True, "稍后重试") from None
            if error.code == 400:
                raise BrainError("model_protocol_error", "模型接口拒绝了请求格式（HTTP 400）", False, "检查 Provider 协议与模型能力") from None
            raise BrainError("brain_model_unavailable", f"模型服务返回 HTTP {error.code}", True, "测试连接后重试") from None
        except TimeoutError as error:
            raise BrainError("model_timeout", "模型请求超时", True, "重试或调整超时设置") from error
        except Exception as error:
            message = str(error).casefold()
            if "api key" in message or "keychain" in message:
                raise BrainError("keychain_not_configured", "模型密钥尚未配置或不可访问", False, "检查 Keychain Reference") from None
            raise BrainError("brain_model_unavailable", "模型服务暂时不可用", True, "测试连接后重试") from None

    def answer_question(self, question: str, context: dict[str, Any]) -> dict[str, Any] | None:
        route = self._route("assistant_chat", aliases=("assistant",))
        if route is None:
            return None
        profile_id, model = route
        profile = next(item for item in self.models.store.list_model_profiles() if item["id"] == profile_id)
        notes = [
            {
                "title": item.get("title"),
                "status": item.get("status"),
                "excerpt": str(item.get("excerpt", ""))[:1600],
            }
            for item in context.get("relevant_notes", [])[:6]
        ]
        system = (
            "你是知序的学习助手。优先使用给定的 reviewed/core 笔记回答；"
            "如果资料不足，可以给出一般性解释，但必须明确标记为待验证，不能声称它来自知识库。"
            "不得建议直接覆盖 reviewed/core，也不得生成文件路径、命令或未经确认的写入。"
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps({"question": question, "reviewed_context": notes}, ensure_ascii=False)},
        ]
        settings = profile["settings"]
        try:
            response = self.models.provider(profile_id).chat(
                model, messages,
                temperature=settings.get("temperature", .3),
                max_tokens=settings.get("maxTokens", 2000),
            )
        except urllib.error.HTTPError as error:
            if error.code in {401, 403}:
                raise BrainError("model_authentication_failed", "模型认证失败", False, "检查 Keychain Reference 和 API Key") from None
            if error.code == 429:
                raise BrainError("model_rate_limited", "模型请求受到速率限制", True, "稍后重试") from None
            raise BrainError("brain_model_unavailable", f"模型服务返回 HTTP {error.code}", True, "测试连接后重试") from None
        except (TimeoutError, urllib.error.URLError) as error:
            raise BrainError("model_timeout", "模型请求超时", True, "重试或调整超时设置") from error
        content = str((((response.get("choices") or [{}])[0].get("message") or {}).get("content")) or "").strip()
        if not content:
            raise BrainError("brain_invalid_model_output", "模型没有返回回答", True, "重试或更换模型")
        return {
            "answer": content,
            "model": model,
            "model_generated": True,
            "verification_status": "reviewed-context" if notes else "needs-verification",
        }

    def generate_curriculum_candidates(self, learning_state: list[dict[str, Any]]) -> dict[str, Any] | None:
        route = self._route("curriculum_planner", aliases=("daily_knowledge_generator",))
        if route is None:
            return None
        profile_id, model = route
        bounded = [{
            "title": str(item.get("title", ""))[:120], "status": str(item.get("status", ""))[:24],
            "domain": str(item.get("domain", ""))[:80], "mastery": int(item.get("mastery", 0) or 0),
            "importance": int(item.get("importance", 3) or 3),
            "weak_points": [str(value)[:100] for value in (item.get("weak_points", []) or [])[:5]],
        } for item in learning_state[:80]]
        messages = [
            {"role": "system", "content": (
                "你是知序的课程候选生成器。只提出当前正式知识列表中不存在、但能连接现有知识的微型学习主题。"
                "不得虚构用户行为、来源、论文或 URL；不得生成文件写入。优先桥接概念、前置概念和可复用比较，"
                "避免论文专属模块。返回 1–3 个候选；如果没有可靠的新候选，返回空数组。"
            )},
            {"role": "user", "content": json.dumps({"existing_knowledge": bounded, "mainline_ratio": .7, "weekday_budget_minutes": 25}, ensure_ascii=False)},
        ]
        result = self.structured("curriculum_planner", messages, CURRICULUM_SCHEMA, aliases=("daily_knowledge_generator",))
        if result is None:
            return None
        return {**result, "_profile_id": profile_id, "_model": model}
