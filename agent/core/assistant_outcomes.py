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


def resolve_assistant_outcome(
    *,
    intent: dict[str, object],
    mode: str = "auto",
    has_attachments: bool = False,
    personalization_enabled: bool = True,
) -> AssistantOutcome:
    """Map explicit structured state to a user-visible outcome.

    Natural-language intent belongs to the Pi model/tool loop.  This legacy
    intake adapter deliberately does not inspect message text, tokens or regexes.
    """

    normalized_mode = str(mode or "auto").casefold()
    if bool(intent.get("writeRequested")) or normalized_mode in {"capture", "save"}:
        return AssistantOutcome("propose_write", "structured-write-request", True)
    if has_attachments or normalized_mode in {"material", "research", "plan", "organize"}:
        return AssistantOutcome("create_artifact", "structured-task", True)
    if str(intent.get("requestedOutput") or "auto") != "auto":
        return AssistantOutcome("create_artifact", "structured-output-request", True)
    if str(intent.get("name") or "") == "organize_preview":
        return AssistantOutcome("create_artifact", "structured-organize-preview", True)
    if not personalization_enabled:
        return AssistantOutcome("answer_only", "personalization-disabled", False)
    return AssistantOutcome("answer_and_track", "ordinary-learning-conversation", False)
