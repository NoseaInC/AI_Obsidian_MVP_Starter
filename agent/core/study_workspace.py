from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


STUDY_STATES = {"active", "paused", "quiz", "completing", "completed", "abandoned", "error"}


@dataclass(frozen=True)
class CandidateAdmissionResult:
    candidate_id: str
    grade: str
    admitted: bool
    placement: str
    reasons: tuple[str, ...]
    entity_valid: bool = False
    canonical_topic_resolved: bool = False
    novelty_verified: bool = False
    prerequisites_satisfied: bool = False
    direct_sources_available: bool = False
    lesson_blueprint_complete: bool = False
    duration_appropriate: bool = False
    duplicate_risk: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidateId": self.candidate_id,
            "grade": self.grade,
            "admitted": self.admitted,
            "placement": self.placement,
            "reasons": list(self.reasons),
            "entityValid": self.entity_valid, "canonicalTopicResolved": self.canonical_topic_resolved,
            "noveltyVerified": self.novelty_verified, "prerequisitesSatisfied": self.prerequisites_satisfied,
            "directSourcesAvailable": self.direct_sources_available, "lessonBlueprintComplete": self.lesson_blueprint_complete,
            "durationAppropriate": self.duration_appropriate, "duplicateRisk": self.duplicate_risk,
        }


def admit_candidate(item: dict[str, Any]) -> CandidateAdmissionResult:
    grade = str(item.get("verificationGrade") or "C")
    title = str(item.get("title") or "").strip()
    verification = dict(item.get("verification") or {})
    entity_valid = 2 <= len(title) <= 120 and not any(token in title.casefold() for token in ("class 的", "这个", "那个", "上面的", "刚才的"))
    canonical = entity_valid and bool("".join(char for char in title if char.isalnum()))
    novelty = bool(verification.get("noveltyVerified", item.get("candidate")))
    prerequisites = bool(verification.get("prerequisitesSatisfied", True))
    sources = bool(item.get("sourceBasis"))
    lesson_complete = bool(item.get("learningOutcomes"))
    duration = 5 <= int(item.get("estimatedMinutes") or 0) <= 25
    duplicate_risk = max(0.0, min(1.0, float(verification.get("duplicateRisk", 0))))
    reasons: list[str] = []
    if not item.get("candidate"):
        reasons.append("不是课程候选")
    if not item.get("sourceBasis"):
        reasons.append("缺少可核验来源")
    if float(item.get("confidence") or 0) < .65:
        reasons.append("置信度不足")
    if not item.get("learningOutcomes"):
        reasons.append("缺少可检验学习目标")
    if not entity_valid: reasons.append("未解析为稳定知识实体")
    if not duration: reasons.append("学习时长过长，需要先拆分")
    if duplicate_risk >= .35: reasons.append("与现有知识重复风险过高")
    values = (entity_valid, canonical, novelty, prerequisites, sources, lesson_complete, duration, duplicate_risk)
    if grade == "A" and item.get("candidate") and not reasons:
        return CandidateAdmissionResult(str(item["id"]), grade, True, "daily-plan", ("A 级机器验证通过",), *values)
    if grade == "B" and item.get("candidate") and entity_valid and canonical and lesson_complete and duration and duplicate_risk < .35:
        return CandidateAdmissionResult(str(item["id"]), grade, True, "validated-direction", tuple(reasons or ["来源有限，仅作为方向候选"]), *values)
    return CandidateAdmissionResult(str(item.get("id", "")), grade, False, "candidate-pool", tuple(reasons or ["未达到今日学习门槛"]), *values)


def split_candidate(item: dict[str, Any], maximum_minutes: int = 25) -> list[dict[str, Any]]:
    minutes = max(5, int(item.get("estimatedMinutes") or 10))
    if minutes <= maximum_minutes: return [item]
    parts = max(2, (minutes + maximum_minutes - 1) // maximum_minutes)
    outcomes = list(item.get("learningOutcomes") or item.get("reasonDetails") or [item.get("reason") or "完成本阶段目标"])
    result = []
    for index in range(parts):
        result.append({
            **item,
            "id": f"{item['id']}-part-{index + 1}",
            "candidateRootId": item["id"],
            "title": f"{item['title']} · {index + 1}/{parts}",
            "estimatedMinutes": (minutes + parts - 1) // parts,
            "learningOutcomes": [str(outcomes[index % len(outcomes)])],
            "reason": f"{item.get('reason') or ''}（已拆为可在一次学习会话内完成的第 {index + 1} 部分）",
            "splitPart": index + 1,
            "splitTotal": parts,
        })
    return result


def lesson_blueprint(item: dict[str, Any]) -> dict[str, Any]:
    title = str(item.get("title") or "学习内容")
    minutes = max(5, int(item.get("estimatedMinutes") or 10))
    outcomes = [str(value) for value in item.get("learningOutcomes") or []] or [f"解释「{title}」的直觉、定义、成立条件与一个典型应用。"]
    prerequisites = [str(value.get("title") or "") for value in item.get("prerequisites") or [] if value.get("title")]
    related = [{"title": str(value.get("title") or "相关笔记"), "path": str(value.get("path") or "")} for value in item.get("relatedNotes") or []]
    concepts = [str(value.get("title") or "") for value in item.get("microConcepts") or [] if value.get("title")]
    details = [str(value) for value in item.get("reasonDetails") or []]
    estimates = [max(1, round(minutes * weight / 8)) for weight in (1, 2, 2, 2, 1)]
    formula = ""
    if "影响函数" in title:
        formula = "\n\n$$\nIF(x;T,F)=\\lim_{\\epsilon\\to0}\\frac{T((1-\\epsilon)F+\\epsilon\\delta_x)-T(F)}{\\epsilon}\n$$\n\n它描述在分布 $F$ 中加入极小权重的点 $x$ 时，统计泛函 $T$ 的一阶变化。"
    elif "倾向得分" in title:
        formula = "\n\n倾向得分写作 $e(x)=P(T=1\\mid X=x)$；它是处理分配机制的条件概率，不是结果预测。"
    sections = [
        {"id": "why", "title": f"为什么需要{title}", "kind": "overview", "estimatedMinutes": estimates[0], "markdown": "## 本节学习目标\n\n" + "\n".join(f"- {value}" for value in outcomes) + f"\n\n{item.get('reason') or ''}"},
        {"id": "definition", "title": "直观定义", "kind": "definition", "estimatedMinutes": estimates[1], "markdown": f"## 核心定义\n\n**{title}** 是当前学习路线中的一个可检验知识单元。先抓住直觉，再核对成立条件。\n\n" + ("\n".join(f"- {value}" for value in details) or "- 从定义、条件和边界三个层次理解。") + formula},
        {"id": "connection", "title": f"与{(prerequisites or [value['title'] for value in related] or ['已有知识'])[0]}的关系", "kind": "connection", "estimatedMinutes": estimates[2], "markdown": f"## 知识连接\n\n前置知识：{'、'.join(prerequisites) or '无需额外前置'}。\n\n相关笔记：{'、'.join(value['title'] for value in related) or '当前尚无正式相关笔记'}。\n\n" + (f"需要补齐：{'、'.join(concepts)}。" if concepts else "重点区分概念本身、识别条件与估计方法。")},
        {"id": "example", "title": "一个简单例子", "kind": "example", "estimatedMinutes": estimates[3], "markdown": f"## 应用检查\n\n用一个你熟悉的场景回答：\n\n1. 「{title}」解决什么问题？\n2. 它依赖哪些条件？\n3. 条件失败时会得到什么误导性结论？"},
        {"id": "summary", "title": "本节总结", "kind": "summary", "estimatedMinutes": estimates[4], "markdown": "## 小结\n\n完成后，你应当能够：\n\n" + "\n".join(f"- {value}" for value in outcomes) + "\n\n> 本课程内容不会自动写入 reviewed/core 笔记。"},
    ]
    quiz = item.get("quizPreview") or {}
    return {
        "version": 1,
        "recommendationId": str(item["id"]),
        "title": title,
        "goal": outcomes[0],
        "estimatedMinutes": minutes,
        "sections": sections,
        "quizzes": [{
            "id": "checkpoint",
            "question": str(quiz.get("question") or f"关于「{title}」，哪一项最能检查你是否理解了它的适用边界？"),
            "options": ["能复述名称", "能说明定义、条件与反例", "看过相关笔记", "记住一个术语"],
            "answerIndex": 1,
            "explanation": str(quiz.get("answerHint") or "掌握概念需要同时说明定义、成立条件和边界。"),
        }],
        "prerequisites": prerequisites,
        "relatedNotes": related,
        "learningPath": [*prerequisites, title, *concepts],
        "sources": list(item.get("sourceBasis") or []),
        "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def public_session(row: dict[str, Any]) -> dict[str, Any]:
    details = dict(row.get("details") or {})
    return {
        "session_id": row["session_id"],
        "recommendation_id": row["recommendation_id"],
        "state": row["state"],
        "started_at": row["started_at"],
        "updated_at": row["updated_at"],
        "completed_at": row.get("completed_at"),
        "details": details,
        "lesson": details.get("lesson"),
        "progress": details.get("progress", {}),
    }
