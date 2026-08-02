"""Workspace policy data models: WriteIntent, NoteType, directory mapping."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Purpose(StrEnum):
    SOURCE_CAPTURE = "source_capture"
    COURSE_LEARNING = "course_learning"
    KNOWLEDGE_CONSOLIDATION = "knowledge_consolidation"
    PROJECT_PROGRESS = "project_progress"
    LEARNING_LOG = "learning_log"


class NoteType(StrEnum):
    SOURCE = "source"
    COURSE = "course"
    COURSE_CHAPTER = "course-chapter"
    TOPIC = "topic"
    CONCEPT = "concept"
    PROJECT = "project"
    PROJECT_NOTE = "project-note"
    LEARNING_LOG = "learning-log"


class Action(StrEnum):
    CREATE = "create"
    UPDATE = "update"


# 固定目录映射
NOTE_TYPE_DIRECTORY: dict[str, str] = {
    "source": "10-Sources",
    "course": "20-Knowledge/Courses",
    "course-chapter": "20-Knowledge/Courses",
    "topic": "20-Knowledge/Topics",
    "concept": "20-Knowledge/Concepts",
    "project": "40-Projects",
    "project-note": "40-Projects",
    "learning-log": "30-Learning",
}

# review_unit 与类型匹配
REVIEW_UNIT_ALLOWED: dict[str, bool] = {
    "source": False,
    "course": False,
    "course-chapter": False,
    "topic": True,
    "concept": True,
    "project": False,
    "project-note": False,
    "learning-log": False,
}


@dataclass
class WriteIntent:
    purpose: str
    noteType: str
    action: str
    title: str
    targetPath: str
    reason: str
    links: dict[str, Any] = field(default_factory=dict)
    existingTarget: str | None = None
    status: str = "ai-draft"
    reviewUnit: bool = False

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "WriteIntent":
        try:
            return cls(
                purpose=str(value["purpose"]),
                noteType=str(value["noteType"]),
                action=str(value["action"]),
                title=str(value["title"]),
                targetPath=str(value["targetPath"]),
                reason=str(value.get("reason", "")),
                links=dict(value.get("links") or {}),
                existingTarget=value.get("existingTarget"),
                status=str(value.get("status", "ai-draft")),
                reviewUnit=bool(value.get("reviewUnit", False)),
            )
        except KeyError as exc:
            raise ValueError(f"write_intent_missing_field:{exc.args[0]}") from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "purpose": self.purpose,
            "noteType": self.noteType,
            "action": self.action,
            "title": self.title,
            "targetPath": self.targetPath,
            "existingTarget": self.existingTarget,
            "reason": self.reason,
            "links": self.links,
            "status": self.status,
            "reviewUnit": self.reviewUnit,
        }
