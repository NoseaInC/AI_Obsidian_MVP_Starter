from __future__ import annotations

from dataclasses import dataclass


OUTCOMES = {
    "answer_only",
    "answer_and_track",
    "suggest_action",
    "create_artifact",
    "propose_write",
}


@dataclass(frozen=True)
class AssistantOutcome:
    kind: str
    reason: str
    creates_task_thread: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "reason": self.reason,
            "createsTaskThread": self.creates_task_thread,
        }


_WRITE_TOKENS = (
    "保存到 obsidian", "存入 obsidian", "写入 obsidian", "保存进 obsidian",
    "创建笔记", "生成笔记并保存", "记到知识库", "存入知识库", "帮我记下来",
    "请保存", "整理并保存", "保存这个", "记录灵感", "记下来",
)
_NO_WRITE_TOKENS = (
    "不要保存", "不保存", "无需保存", "不用保存", "不要创建笔记", "不用创建笔记",
    "不要写入", "不写入", "只回答", "仅回答",
)
_ARTIFACT_TOKENS = (
    "生成学习包", "整理成学习包", "做成学习包", "构建学习包", "生成学习单元",
    "生成小测", "出一组题", "生成练习", "整理成卡片", "生成研究包",
    "生成学习计划", "制定学习计划", "安排学习计划", "整理这份资料", "处理这个 pdf",
)
_SUGGEST_TOKENS = (
    "值得整理吗", "要不要整理", "下一步怎么做", "建议我做什么", "可以怎么继续",
)


def decide_assistant_outcome(
    message: str,
    *,
    mode: str = "auto",
    has_attachments: bool = False,
    personalization_enabled: bool = True,
) -> AssistantOutcome:
    """Choose the user-visible outcome independently from Brain skill internals.

    A tutoring skill may answer a question, but it must not implicitly create an
    Artifact or a write proposal. Those require an explicit user request.
    """

    text = " ".join(str(message).casefold().split())
    normalized_mode = str(mode or "auto").casefold()
    explicit_no_write = any(token in text for token in _NO_WRITE_TOKENS)
    if (any(token in text for token in _WRITE_TOKENS) and not explicit_no_write) or normalized_mode in {"capture", "save"}:
        return AssistantOutcome("propose_write", "explicit-write-request", True)
    if has_attachments or normalized_mode in {"material", "research", "plan", "organize"}:
        return AssistantOutcome("create_artifact", "explicit-structured-task", True)
    if any(token in text for token in _ARTIFACT_TOKENS):
        return AssistantOutcome("create_artifact", "explicit-artifact-request", True)
    if any(token in text for token in _SUGGEST_TOKENS):
        return AssistantOutcome("suggest_action", "advice-request", False)
    if not personalization_enabled:
        return AssistantOutcome("answer_only", "personalization-disabled", False)
    return AssistantOutcome("answer_and_track", "ordinary-learning-conversation", False)
