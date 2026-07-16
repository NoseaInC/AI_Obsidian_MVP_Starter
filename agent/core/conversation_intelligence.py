from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any


_STOP = {
    "什么", "怎么", "如何", "一下", "这个", "那个", "可以", "是不是", "为什么",
    "please", "what", "how", "the", "and", "with", "about",
}
_DOMAIN_TERMS = (
    "倾向得分", "可识别性", "可估计性", "因果推断", "平均处理效应", "dragonnet",
    "目标正则化", "机器学习", "深度学习", "大语言模型", "agent", "llm", "psm",
    "回归", "假设检验", "概率", "统计", "优化", "梯度下降", "贝叶斯",
)


@dataclass(frozen=True)
class KnowledgeSignal:
    signal_type: str
    topic: str
    detail: str
    confidence: float

    @property
    def fingerprint(self) -> str:
        raw = f"{self.signal_type}:{self.topic.casefold()}:{self.detail.casefold()}"
        return hashlib.sha256(raw.encode()).hexdigest()[:24]

    def as_dict(self) -> dict[str, Any]:
        return {
            "signalType": self.signal_type,
            "topic": self.topic,
            "detail": self.detail,
            "confidence": self.confidence,
            "fingerprint": self.fingerprint,
        }


def _topics(text: str) -> list[str]:
    found: list[str] = []
    folded = text.casefold()
    for term in _DOMAIN_TERMS:
        if term.casefold() in folded and term not in found:
            found.append(term.upper() if term in {"psm", "llm"} else term)
    for match in re.findall(r"[A-Za-z][A-Za-z0-9+_.-]{2,24}|[\u4e00-\u9fff]{2,10}", text):
        value = match.strip("，。！？：:；;（）()[]")
        if value.casefold() not in _STOP and value not in found and len(found) < 5:
            found.append(value)
    return found[:5]


def extract_knowledge_signals(message: str) -> list[KnowledgeSignal]:
    """Extract only user-grounded learning signals; never infer mastery as fact."""

    text = " ".join(str(message).strip().split())
    if not text:
        return []
    topics = _topics(text)
    primary = topics[0] if topics else "当前主题"
    signals: list[KnowledgeSignal] = [KnowledgeSignal("topic", topic, "用户在对话中主动提及", .78) for topic in topics[:3]]

    confusion_markers = ("不懂", "没明白", "不理解", "搞不清", "困惑", "区别", "为什么")
    if any(marker in text for marker in confusion_markers):
        signals.append(KnowledgeSignal("confusion", primary, text[:180], .9))

    known_markers = ("我知道", "我已经学过", "我学过", "我会", "我能解释", "已经掌握")
    if any(marker in text for marker in known_markers):
        signals.append(KnowledgeSignal("claimed_knowledge", primary, text[:180], .82))

    prerequisite_markers = ("前置", "基础是什么", "需要先学", "从哪里开始", "入门")
    if any(marker in text for marker in prerequisite_markers):
        signals.append(KnowledgeSignal("missing_prerequisite", primary, text[:180], .84))

    interest_markers = ("感兴趣", "想深入", "想学", "继续讲", "多讲", "研究")
    if any(marker in text for marker in interest_markers):
        signals.append(KnowledgeSignal("interest", primary, text[:180], .8))

    deduped: dict[str, KnowledgeSignal] = {}
    for signal in signals:
        deduped[signal.fingerprint] = signal
    return list(deduped.values())[:8]


def build_conversation_summary(
    prior_summary: str,
    user_message: str,
    assistant_message: str,
    signals: list[KnowledgeSignal],
) -> str:
    topics = [signal.topic for signal in signals if signal.signal_type == "topic"]
    parts = []
    if prior_summary:
        parts.append(prior_summary[:500])
    if topics:
        parts.append("本轮主题：" + "、".join(dict.fromkeys(topics)))
    if any(signal.signal_type == "confusion" for signal in signals):
        parts.append("用户表达了需要继续澄清的疑问。")
    if any(signal.signal_type == "claimed_knowledge" for signal in signals):
        parts.append("用户自述已有相关基础；该信息尚未通过测验验证。")
    if not parts:
        parts.append("本轮进行了普通学习问答。")
    # The summary is intentionally bounded and contains no verbatim model trace.
    return " ".join(parts)[-900:]
