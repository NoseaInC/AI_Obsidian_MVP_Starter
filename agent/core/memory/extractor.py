"""Memory candidate extraction.

Inputs allowed: user message, final answer, safe Tool Observation summary,
Action Result, Learning Event.
Forbidden: provider reasoning, chain-of-thought, unexecuted plans, system
prompts, full private attachment bodies.

Model generates candidates only. Python policy decides save/activate/conflict.
V1 prefers under-recording over wrong recording.
"""
from __future__ import annotations

from typing import Any

# 明确表达意图的信号（正则片段）
_EXPLICIT_PATTERNS = {
    "goal": ["我的长期目标是", "我希望以后", "我想持续", "记住，我要"],
    "preference": ["记住，", "以后请", "以后统一", "我希望你一直", "记住我偏好"],
    "project_decision": ["这个项目以后统一", "决定采用", "以后不再", "统一使用"],
    "knowledge_state": ["我已经掌握", "我还不会", "我不懂", "我学会了", "我知道"],
}


def extract_candidates(user_message: str) -> list[dict[str, Any]]:
    """Extract explicit memory candidates from a user message.

    Returns a list of {memory_type, memory_key, value, explicit} dicts.
    V1: only explicit user expressions become candidates for immediate
    activation; implicit signals are not auto-extracted (宁可少记).
    """
    candidates: list[dict[str, Any]] = []
    for memory_type, patterns in _EXPLICIT_PATTERNS.items():
        for pattern in patterns:
            if pattern in user_message:
                remainder = user_message.split(pattern, 1)[1].strip()
                if not remainder:
                    continue
                # 取第一句作为 key/value
                first_sentence = remainder.split("。")[0].split("，")[0].strip()
                if len(first_sentence) > 120:
                    first_sentence = first_sentence[:120]
                candidates.append({
                    "memory_type": memory_type,
                    "memory_key": first_sentence,
                    "value": {"summary": remainder[:400]},
                    "explicit": True,
                })
                break
    # 去重（相同 key）
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for c in candidates:
        if c["memory_key"] not in seen:
            seen.add(c["memory_key"])
            unique.append(c)
    return unique[:4]
