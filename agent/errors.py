from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class BrainError(Exception):
    code: str
    human_message: str
    retryable: bool = False
    suggested_action: str = ""
    correlation_id: str = ""
    technical_details: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.human_message

    def public(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "human_message": self.human_message,
            "retryable": self.retryable,
            "suggested_action": self.suggested_action,
            "correlation_id": self.correlation_id,
            "technical_details": self.technical_details,
        }


def as_brain_error(error: Exception, correlation_id: str = "") -> BrainError:
    if isinstance(error, BrainError):
        if correlation_id and not error.correlation_id:
            error.correlation_id = correlation_id
        return error
    if isinstance(error, TimeoutError):
        return BrainError("brain_timeout", "任务执行超时", True, "重试或缩小任务范围", correlation_id)
    if isinstance(error, (ValueError, TypeError)):
        return BrainError("brain_invalid_request", str(error), False, "检查输入内容", correlation_id)
    return BrainError("brain_internal_error", "主脑暂时无法完成任务", True, "查看脱敏诊断后重试", correlation_id)
