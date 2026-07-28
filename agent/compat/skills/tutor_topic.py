from __future__ import annotations

from typing import Any


def run(payload: dict[str, Any], context: dict[str, Any], run_id: str, step_id: str, model_gateway: Any = None) -> dict[str, Any]:
    question = str(payload.get("text", "")).strip()
    notes = context.get("relevant_notes", [])
    evidence = [{"title": item.get("title"), "path": item.get("path"), "status": item.get("status")} for item in notes]
    model_answer = model_gateway.answer_question(question, context) if model_gateway else None
    if model_answer:
        answer = model_answer["answer"]
    elif notes:
        lead = notes[0]
        answer = f"我先基于已审核笔记《{lead['title']}》组织学习：先确认定义与成立条件，再做推导、辨析和迁移练习。"
    else:
        answer = "当前知识库没有匹配的 reviewed/core 笔记。我可以提供学习框架，但不会把未经验证的内容标成正式知识。"
    return {
        "kind": "tutor", "question": question, "answer": answer,
        "learning_sequence": ["定义", "推导", "典型问题", "例子", "自我解释", "代码"],
        "evidence": evidence, "quiz_preview": [f"请给出“{question[:50]}”的定义与适用条件。"],
        "mastery_suggestion": None, "mastery_requires_confirmation": True,
        "model": model_answer.get("model") if model_answer else None,
        "model_generated": bool(model_answer),
        "verification_status": model_answer.get("verification_status") if model_answer else ("reviewed-context" if notes else "local-fallback"),
    }
