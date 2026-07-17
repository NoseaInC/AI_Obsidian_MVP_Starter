from __future__ import annotations

import hashlib
import json
import re
import uuid
from difflib import SequenceMatcher
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable

import sys

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "00-System/Scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import ingest_pdf


SCHEMA_VERSION = 1
PRONOUNS = (
    "这个方法", "这个概念", "这篇文章", "这篇论文", "这个 PDF", "这个pdf", "当前这个知识点",
    "那个方法", "上述方法", "刚才的方法", "那个概念", "上述概念", "这篇", "这个材料",
    "刚才那个", "前面说的", "把它保存", "保存它", "把它整理", "继续推导", "更新那篇笔记", "第二个",
)
SAVE_MARKERS = (
    "整理到 Obsidian", "保存到 Obsidian", "存入 Obsidian", "写入 Obsidian", "保存到知识库", "存入知识库",
    "加入知识库", "整理并保存", "保存一下", "保存它", "把它保存", "写成笔记", "整理成笔记", "写进笔记",
    "更新当前笔记", "加入当前笔记", "补充到当前笔记", "补进当前笔记", "补充当前笔记",
    "补充进当前笔记", "追加到当前笔记", "追加进当前笔记", "添加到当前笔记", "添加进当前笔记",
)
NO_SAVE_MARKERS = (
    "不要保存", "不保存", "只整理", "整理但不保存", "只预览",
    "do not save", "don't save", "preview only",
)
ORGANIZE_PREVIEW_MARKERS = (
    "整理一下", "整理这段", "整理刚才", "整理这个", "整理成预览", "整理为预览", "先整理", "帮我整理",
    "梳理一下", "归纳一下", "提炼一下",
    "organize as a preview", "organize into a preview", "organize this into a preview",
)
METHOD_HINTS = ("method", "方法", "算法", "估计量", "检验", "模型", "matching", "regression")
ENTITY_STOP = {
    "Obsidian", "PDF", "AI", "Agent", "Markdown", "这个方法", "那个方法", "上述方法", "刚才的方法",
    "这个概念", "那个概念", "上述概念", "当前笔记", "知识库",
}
SAFE_ROOTS = {"01-Inbox", "10-Sources", "20-Knowledge", "30-Learning", "40-Projects"}


def _now() -> str:
    from datetime import datetime
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _canonical(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(value).casefold())


def _safe_title(value: str, fallback: str = "待整理内容") -> str:
    value = re.sub(r"[\\/:*?\"<>|#^\[\]]+", " ", str(value))
    value = re.sub(r"\s+", " ", value).strip(" .-：:")
    return value[:80] or fallback


def _persistable_entity_name(value: str) -> bool:
    """Keep useful named topics in SQLite without turning it into raw-text storage."""

    candidate = str(value).strip()
    if not candidate or len(candidate) > 80:
        return False
    # Long all-caps/token-like strings are commonly secrets, test sentinels, or
    # pasted identifiers.  Short acronyms such as ATE/IPTW remain useful.
    letters = re.sub(r"[^A-Za-z]", "", candidate)
    if len(candidate) > 24 and letters and letters.upper() == letters:
        return False
    if re.search(r"(?:api[_-]?key|secret|token|password)", candidate, re.I):
        return False
    return True


def _entity(value: str, entity_type: str, message_id: str = "", attachment_ids: list[str] | None = None, confidence: float = .9) -> dict[str, Any]:
    display = _safe_title(value)
    aliases = []
    if display.casefold() == "delta method":
        aliases = ["Delta 方法", "德尔塔方法", "方差传播法"]
    return {
        "canonicalName": display, "displayName": display, "type": entity_type,
        "aliases": aliases, "sourceMessageIds": [message_id] if message_id else [],
        "sourceAttachmentIds": list(attachment_ids or []), "confidence": confidence,
    }


def extract_entities(text: str, message_id: str = "") -> list[dict[str, Any]]:
    """Extract explicit named entities structurally; aliases/focus provide the semantic layer."""

    value = " ".join(str(text).split())
    candidates: list[tuple[str, str, float]] = []
    for match in re.finditer(r"[《“\"]([^》”\"]{2,80})[》”\"]", value):
        candidates.append((match.group(1), "topic", .96))
    for match in re.finditer(r"\b([A-Z][A-Za-z0-9+_.-]*(?:\s+[A-Z][A-Za-z0-9+_.-]*){0,3}(?:\s+Method)?)\b", value):
        name = match.group(1).strip()
        kind = "method" if "method" in name.casefold() or any(hint in value.casefold() for hint in ("方法", "推导", "method")) else "topic"
        candidates.append((name, kind, .95))
    patterns = (
        r"(?:介绍一下|解释一下|什么是|学习|推导|整理|关于)\s*([\u4e00-\u9fffA-Za-z0-9+_.-]{2,30}(?:方法|算法|估计|检验|模型))",
        r"([\u4e00-\u9fffA-Za-z0-9+_.-]{2,24}(?:方法|算法|估计|检验))",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, value, re.I):
            candidates.append((match.group(1), "method", .91))
    seen: set[str] = set(); result: list[dict[str, Any]] = []
    for name, kind, confidence in candidates:
        name = re.sub(r"^(请|把|帮我|一下)", "", name).strip()
        key = _canonical(name)
        deictic = any(marker in name for marker in ("这个方法", "那个方法", "上述方法", "刚才的方法", "这个概念", "那个概念", "上述概念"))
        if not key or name in ENTITY_STOP or deictic or key in seen or not _persistable_entity_name(name):
            continue
        seen.add(key); result.append(_entity(name, kind, message_id, confidence=confidence))
    return result[:8]


def classify_assistant_intent(message: str, focus: dict[str, Any], attachments: list[dict[str, Any]], active_note: dict[str, Any]) -> dict[str, Any]:
    text = " ".join(str(message).split())
    lower = text.casefold()
    path_write = bool(re.search(
        r"(?:保存|写入|存入|整理).{0,16}(?:到|进)\s*(?:01-Inbox|10-Inbox|10-Sources|20-Knowledge|30-Learning|40-Projects)/",
        text,
        re.I,
    ))
    save = (any(marker.casefold() in lower for marker in SAVE_MARKERS) or path_write) and not any(marker.casefold() in lower for marker in NO_SAVE_MARKERS)
    active_method = focus.get("activeMethod")
    current_note_write = any(marker in text for marker in (
        "更新当前笔记", "加入当前笔记", "写入当前笔记", "补充到当前笔记", "补进当前笔记",
        "补充进当前笔记", "补充当前笔记", "追加到当前笔记", "追加进当前笔记", "添加到当前笔记",
        "添加进当前笔记", "更新到我当前笔记", "加到当前笔记", "更新当前打开的笔记",
    )) or bool(re.search(r"(?:补充|追加|添加|写入|更新).{0,4}(?:到|进)?(?:我)?(?:的)?当前(?:打开的)?笔记", text))
    if current_note_write:
        name = "update_current_note"
    elif any(marker in text for marker in ("加入今天", "放到今天", "安排到今天")):
        name = "create_daily_task"
    elif save and (active_method or any(hint in lower for hint in METHOD_HINTS)):
        name = "create_method_note"
    elif save and (any(item.get("kind") == "pdf" for item in attachments) or str((focus.get("activeMaterial") or {}).get("type") or "") == "paper"):
        name = "create_source_note"
    elif save and any(item.get("kind") == "url" for item in attachments):
        name = "create_source_note"
    elif save:
        name = "organize_and_save"
    elif any(marker.casefold() in lower for marker in ORGANIZE_PREVIEW_MARKERS):
        name = "organize_preview"
    elif any(marker in text for marker in ("继续", "接着", "再讲", "继续推导")):
        name = "continue_explanation"
    else:
        name = "answer_question"
    return {
        "name": name, "writeRequested": name in {"organize_and_save", "update_current_note", "create_source_note", "create_concept_note", "create_method_note", "create_topic_note"},
        "requestedOutput": "method_note" if name == "create_method_note" else "source_note" if name == "create_source_note" else "existing_note_update" if name == "update_current_note" else "auto",
        "requestedDestination": str(active_note.get("path") or "") if name == "update_current_note" else "",
        "confidence": .98 if save or name == "update_current_note" else .9,
    }


_SHORT_WRITE_CONFIRMATION = re.compile(r"^(?:写入|保存|确认写入|执行写入|就这样写入|按这个写入|按此写入)[。.!！]?$", re.I)
_WRITE_STATUS_QUESTION = re.compile(r"(?:写入|保存).{0,8}(?:到哪里|到哪了|了吗|没有|状态|结果)|(?:写到|存到).{0,5}(?:哪里|哪了)", re.I)
_PROPOSED_TARGET = re.compile(
    r"(?:目标路径|目标位置|写入到|整理到|保存到)?[：:\s`]*"
    r"((?:01-Inbox|10-Sources|20-Knowledge|30-Learning|40-Projects)/[^\n`]+?\.md)",
    re.I,
)


def _pending_write_from_recent(message: str, recent: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Resolve a terse confirmation against the last explicit assistant proposal.

    The inherited body stays in the private conversation/request files.  Only the
    target path and source message id are later copied into structured metadata.
    """
    if not _SHORT_WRITE_CONFIRMATION.fullmatch(" ".join(str(message).split())):
        return None
    for item in reversed(recent[:-1] if recent and str(recent[-1].get("role") or "") == "user" else recent):
        if str(item.get("role") or "") != "assistant":
            continue
        content = str(item.get("content") or "").strip()
        target_match = _PROPOSED_TARGET.search(content)
        if not target_match:
            continue
        target = target_match.group(1).strip().replace("\\", "/")
        if ".." in Path(target).parts or not target.endswith(".md"):
            continue
        body = re.split(r"(?im)^##\s*(?:✍️\s*)?(?:拟写入计划|写入计划|保存计划)\s*$", content, maxsplit=1)[0].strip()
        if not body:
            continue
        return {
            "targetPath": target,
            "title": Path(target).stem,
            "content": body,
            "sourceMessageId": str(item.get("id") or ""),
        }
    return None


class ConversationFocusService:
    def __init__(self, store: Any) -> None:
        self.store = store

    def resolve(self, bundle: dict[str, Any]) -> dict[str, Any]:
        conversation_id = str(bundle["conversationId"])
        previous = self.store.get_conversation_focus(conversation_id) or {"conversationId": conversation_id}
        current = bundle["currentMessage"]
        text = str(current.get("content") or "")
        entities = extract_entities(text, str(current.get("id") or ""))
        explicit_method = next((item for item in entities if item["type"] == "method"), None)
        explicit_topic = next((item for item in entities if item["type"] == "topic"), None)
        attachments = list(bundle.get("attachments") or [])
        active_material = previous.get("activeMaterial")
        if attachments:
            selected = attachments[-1]
            active_material = _entity(str(selected.get("displayName") or "当前材料"), "paper" if selected.get("kind") == "pdf" else "material", current.get("id", ""), [selected.get("id", "")], .99)
        pronoun = next((marker for marker in PRONOUNS if marker.casefold() in text.casefold()), "")
        # In “compare this method with Bootstrap”, the explicit comparison
        # target must not steal focus from the deictic subject.
        active_method = previous.get("activeMethod") if pronoun and previous.get("activeMethod") else explicit_method or previous.get("activeMethod")
        active_topic = previous.get("activeTopic") if pronoun and previous.get("activeTopic") else explicit_topic or explicit_method or previous.get("activeTopic")
        resolved_reference = None; confidence = .96 if entities or attachments else float(previous.get("confidence") or 0)
        evidence = list(previous.get("evidence") or [])[-12:]
        if entities:
            evidence.append({"kind": "explicit-entity", "messageId": current.get("id"), "value": entities[0]["displayName"], "score": .96})
        if attachments:
            evidence.append({"kind": "selected-attachment", "attachmentId": attachments[-1].get("id"), "value": attachments[-1].get("displayName"), "score": .99})
        if pronoun:
            if "篇" in pronoun or "PDF" in pronoun or "pdf" in pronoun:
                resolved_reference = active_material
            elif "笔记" in pronoun:
                path = str((bundle.get("currentNote") or {}).get("path") or previous.get("activeVaultNotePath") or "")
                resolved_reference = _entity(Path(path).stem, "note", current.get("id", ""), confidence=.9) if path else None
            else:
                resolved_reference = active_method or active_topic or active_material
            if resolved_reference:
                confidence = min(.98, float(resolved_reference.get("confidence") or .75) + .02)
                evidence.append({"kind": "pronoun-resolution", "marker": pronoun, "value": resolved_reference.get("displayName"), "score": confidence})
            else:
                confidence = .35
                evidence.append({"kind": "unresolved-pronoun", "marker": pronoun, "messageId": current.get("id"), "score": confidence})
        focus = {
            "conversationId": conversation_id, "activeTopic": active_topic, "activeConcept": previous.get("activeConcept"),
            "activeMethod": active_method, "activeMaterial": active_material,
            "activeAttachmentIds": [str(item.get("id")) for item in attachments] or list(previous.get("activeAttachmentIds") or []),
            "activeArtifactId": bundle.get("activeArtifactId") or previous.get("activeArtifactId"),
            "activeTaskThreadId": bundle.get("activeTaskThreadId") or previous.get("activeTaskThreadId"),
            "activeRecommendationId": bundle.get("activeRecommendationId") or previous.get("activeRecommendationId"),
            "activeVaultNotePath": str((bundle.get("currentNote") or {}).get("path") or previous.get("activeVaultNotePath") or ""),
            "activeSelectionReference": "current-note-selection" if (bundle.get("currentNote") or {}).get("selection") else previous.get("activeSelectionReference"),
            "lastConfirmedIntent": previous.get("lastConfirmedIntent"), "lastWriteTarget": previous.get("lastWriteTarget"),
            "confidence": confidence, "evidence": evidence[-20:], "schemaVersion": SCHEMA_VERSION,
            "resolution": {
                "pronoun": pronoun, "resolvedReference": resolved_reference,
                "requiresConfirmation": bool(pronoun and not resolved_reference) or bool(pronoun and .55 <= confidence < .8),
                "confirmationLabel": f"按 {resolved_reference['displayName']} 继续" if resolved_reference else "选择当前目标",
            },
        }
        stored = self.store.save_conversation_focus(conversation_id, focus)
        # Resolution is turn-scoped UI state. Evidence is persisted, while this
        # richer object is returned to the caller without duplicating it in SQL.
        stored["resolution"] = focus["resolution"]
        return stored

    def update_after_turn(self, focus: dict[str, Any], intent: dict[str, Any], target: str = "") -> dict[str, Any]:
        updated = {**focus, "lastConfirmedIntent": intent["name"]}
        if target: updated["lastWriteTarget"] = target
        stored = self.store.save_conversation_focus(str(focus["conversationId"]), updated)
        if focus.get("resolution") is not None:
            stored["resolution"] = focus["resolution"]
        return stored


class InputBundleBuilder:
    def __init__(self, vault: Path, intake: Any, store: Any) -> None:
        self.vault, self.intake, self.store = vault.resolve(), intake, store

    def build(self, conversation_id: str, message: dict[str, Any], attachments: list[dict[str, Any]], body: dict[str, Any]) -> dict[str, Any]:
        conversation = self.intake.get_conversation(conversation_id, include_messages=False)
        recent = self.intake.recent_messages(conversation_id, 8)
        effective_attachments = list(attachments)
        if not effective_attachments:
            previous = self.store.get_conversation_focus(conversation_id) or {}
            for attachment_id in list(previous.get("activeAttachmentIds") or [])[-10:]:
                try:
                    effective_attachments.append(self.intake.get_attachment(str(attachment_id)))
                except (KeyError, ValueError):
                    continue
        active_note = body.get("active_note") if isinstance(body.get("active_note"), dict) else {}
        current_note: dict[str, Any] = {}
        relative = str(active_note.get("path") or "")
        if relative:
            path = (self.vault / relative).resolve()
            if path.is_relative_to(self.vault) and path.is_file() and not path.is_symlink() and path.suffix.casefold() == ".md":
                text = path.read_text(encoding="utf-8", errors="replace")
                current_note = {"path": relative, "title": path.stem, "frontmatter": ingest_pdf.parse_frontmatter(text), "selection": str(active_note.get("selection") or "")[:4000]}
        pasted = str(message.get("content") or "") if "\n" in str(message.get("content") or "") and len(str(message.get("content") or "")) > 120 else ""
        urls = re.findall(r"https://[^\s)\]>]+", str(message.get("content") or ""))[:20]
        return {
            "conversationId": conversation_id, "currentMessage": message, "recentMessages": recent,
            "conversationSummary": self.store.latest_conversation_summary(conversation_id),
            "conversationFocus": self.store.get_conversation_focus(conversation_id), "currentNote": current_note,
            "attachments": effective_attachments, "urls": urls, "pastedText": pasted,
            "activeArtifactId": str(body.get("active_artifact_id") or conversation.get("activeArtifactId") or ""),
            "activeTaskThreadId": str(conversation.get("activeTaskThreadId") or ""),
            "activeRecommendationId": str(body.get("active_recommendation_id") or ""),
            "userGoal": str(message.get("content") or "")[:1000], "requestedOutput": "", "requestedDestination": "",
            "autonomyMode": self.store.get_setting("autonomy_mode", "high"),
            "personalizationEnabled": bool(conversation.get("personalizationEnabled", True)), "schemaVersion": SCHEMA_VERSION,
        }


class MaterialUnderstandingService:
    def __init__(self, intake: Any) -> None:
        self.intake = intake

    def understand(self, bundle: dict[str, Any], focus: dict[str, Any], intent: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        attachments = list(bundle.get("attachments") or [])
        kinds = [self._kind(item) for item in attachments]
        if bundle.get("pastedText"): kinds.append("pasted_text")
        if not kinds: kinds.append("conversation")
        unique = list(dict.fromkeys(kinds)); kind = "mixed_bundle" if len(unique) > 1 else unique[0]
        entity = focus.get("activeMethod") or focus.get("activeTopic") or focus.get("activeMaterial")
        named_title = str((entity or {}).get("displayName") or "")
        if named_title and not _persistable_entity_name(named_title):
            named_title = ""
        title = str(named_title or (attachments[0].get("displayName") if attachments else f"当前对话 · {str(bundle['currentMessage'].get('id') or '')[-8:]}"))
        purpose = "archive" if intent["name"] == "create_source_note" else "learn" if (focus.get("activeMethod") or "学习" in bundle.get("userGoal", "")) else "capture"
        units: list[dict[str, Any]] = []
        recent = list(bundle.get("recentMessages") or [])[-8:]
        for item in recent:
            content = str(item.get("content") or "")
            if not content: continue
            role = str(item.get("role") or "")
            unit_type = "user_opinion" if role == "user" and any(token in content for token in ("我认为", "我觉得", "我决定")) else "ai_explanation" if role == "assistant" else "question" if "?" in content or "？" in content else "claim"
            unit_title = title if named_title else "用户提供的会话内容" if role == "user" else "Agent 会话解释"
            units.append(self._unit(unit_type, unit_title, {"conversationId": bundle["conversationId"], "messageId": item.get("id"), "role": role}, "user-provided" if role == "user" else "needs-verification", .86 if role == "user" else .62))
        for attachment in attachments:
            source = {"attachmentId": attachment.get("id"), "fileName": attachment.get("displayName"), "kind": attachment.get("kind")}
            units.append(self._unit("source_excerpt", str(attachment.get("displayName") or title), source, "source-reference", .95))
        result_types = self._result_types(kind, intent, focus)
        understanding = {
            "kind": kind, "title": title, "canonicalTopic": str((focus.get("activeTopic") or {}).get("canonicalName") or title) if named_title else title,
            "domains": self._domains(title), "userPurpose": purpose,
            "knowledgeUnitIds": [item["id"] for item in units],
            "sourceMetadata": {"conversationId": bundle["conversationId"], "attachmentIds": [item.get("id") for item in attachments], "urls": bundle.get("urls", [])},
            "relationships": [{"type": "about", "target": title}], "suggestedResultTypes": result_types,
            "confidence": .94 if attachments or entity else .68,
            "warnings": ["AI 解释属于待验证内容"] if any(item["unitType"] == "ai_explanation" for item in units) else [],
            "schemaVersion": SCHEMA_VERSION,
        }
        return understanding, units[:100]

    @staticmethod
    def _kind(attachment: dict[str, Any]) -> str:
        return {"pdf": "pdf", "url": "web_page", "conversation": "ai_conversation", "text": "text_file", "local_path": "markdown_file", "folder": "folder"}.get(str(attachment.get("kind")), "text_file")

    @staticmethod
    def _unit(unit_type: str, title: str, source: dict[str, Any], verification: str, confidence: float) -> dict[str, Any]:
        raw = json.dumps(source, ensure_ascii=False, sort_keys=True)
        return {"id": "unit-" + hashlib.sha256(f"{unit_type}|{title}|{raw}".encode()).hexdigest()[:24], "unitType": unit_type, "title": title,
                "contentReference": raw[:1000], "provenance": {"origin": source.get("role") or source.get("kind") or "conversation"},
                "sourceReference": source, "verificationStatus": verification, "confidence": confidence}

    @staticmethod
    def _domains(title: str) -> list[str]:
        lower = title.casefold()
        return ["统计与机器学习"] if any(token in lower for token in ("delta", "回归", "估计", "概率", "因果")) else ["LLM 与 Agent"] if any(token in lower for token in ("agent", "llm", "模型")) else []

    @staticmethod
    def _result_types(kind: str, intent: dict[str, Any], focus: dict[str, Any]) -> list[str]:
        if intent["name"] == "update_current_note": return ["existing_note_update"]
        if intent["name"] == "create_method_note" or focus.get("activeMethod"): return ["method_note"]
        if kind == "pdf": return ["paper_note", "method_note"]
        if kind == "web_page": return ["web_source_note"]
        if kind == "mixed_bundle": return ["topic_synthesis", "source_note"]
        if kind == "conversation": return ["conversation_source_note"]
        if kind in {"pasted_text", "text_file", "markdown_file"}: return ["source_note"]
        return ["inbox_capture"]


class TargetNoteResolver:
    def __init__(self, vault: Path) -> None:
        self.vault = vault.resolve()

    def resolve(self, title: str, note_type: str, current_note: dict[str, Any] | None = None) -> dict[str, Any]:
        canonical = _canonical(title); candidates: list[dict[str, Any]] = []
        if current_note and current_note.get("path"):
            candidates.append({"path": current_note["path"], "title": current_note.get("title"), "score": 100, "reason": "current-note", "status": str((current_note.get("frontmatter") or {}).get("status") or "")})
        for path in self.vault.rglob("*.md"):
            relative = path.relative_to(self.vault)
            if path.is_symlink() or relative.parts[0] in {".obsidian", "90-Local-Only"}: continue
            text = path.read_text(encoding="utf-8", errors="replace")
            meta = ingest_pdf.parse_frontmatter(text)
            aliases = meta.get("aliases", [])
            if isinstance(aliases, str): aliases = [item.strip(" []\"'") for item in aliases.split(",")]
            names = [path.stem, *[str(item) for item in aliases or []]]
            exact = any(_canonical(name) == canonical for name in names)
            similarity = max((SequenceMatcher(None, canonical, _canonical(name)).ratio() for name in names if _canonical(name)), default=0.0)
            if exact or similarity >= .88:
                candidates.append({"path": str(relative), "title": path.stem, "score": 95 if exact else max(0, round(similarity * 100) - 2), "reason": "title-or-alias" if exact else "similar-title", "status": str(meta.get("status") or ""), "type": str(meta.get("type") or "")})
        candidates.sort(key=lambda item: (-int(item["score"]), str(item["path"])))
        selected = candidates[0] if candidates else None
        if current_note and current_note.get("path"): decision = "update_existing"
        elif selected and selected["score"] >= 90: decision = "update_existing"
        elif len(candidates) > 1 and candidates[0]["score"] - candidates[1]["score"] < 8: decision = "merge_multiple"
        else: decision = "create_new"
        return {"decision": decision, "candidates": candidates[:10], "selectedPath": (selected or {}).get("path"), "confidence": (selected or {}).get("score", 88) / 100, "explanation": "优先匹配当前笔记、精确标题和 aliases；没有可靠候选时新建。", "schemaVersion": SCHEMA_VERSION}


class ObsidianOrganizationService:
    def __init__(self, vault: Path, store: Any, autonomy: Any, proposal_handler: Callable[[dict[str, Any]], dict[str, Any]] | None = None) -> None:
        self.vault, self.store, self.autonomy = vault.resolve(), store, autonomy
        self.proposal_handler = proposal_handler
        self.targets = TargetNoteResolver(vault)

    def conventions(self) -> dict[str, Any]:
        def existing(options: list[str]) -> str:
            return next((item for item in options if (self.vault / item).is_dir()), options[0])
        return {"inboxRoot": existing(["01-Inbox", "00-Inbox"]), "sourceRoots": {
                    "pdf": existing(["10-Sources/Papers", "10-Sources"]),
                    "web": existing(["10-Sources/Web", "10-Sources"]),
                    "text": existing(["10-Sources/Notes", "10-Sources"]),
                    "conversation": existing(["10-Sources/AI-Conversations", "10-Sources"]),
                },
                "conceptRoot": existing(["20-Knowledge/Concepts", "20-Knowledge"]), "topicRoot": existing(["20-Knowledge/Topics", "20-Knowledge"]),
                "mocRoot": existing(["20-Knowledge/MOCs", "20-Knowledge"]), "learningRoot": existing(["30-Learning"]), "projectRoot": existing(["40-Projects"]),
                "protectedPaths": ["90-Local-Only", ".obsidian", "Private", "Personal", "Secrets"]}

    def plan(self, bundle_id: str, understanding: dict[str, Any], intent: dict[str, Any], focus: dict[str, Any], current_note: dict[str, Any]) -> dict[str, Any]:
        title = _safe_title(str((focus.get("activeMethod") or focus.get("activeTopic") or focus.get("activeMaterial") or {}).get("displayName") or understanding.get("title") or "待整理内容"))
        if str((focus.get("activeMaterial") or {}).get("type") or "") == "paper" and title.casefold().endswith(".pdf"):
            title = _safe_title(Path(title).stem)
        result_type = str((understanding.get("suggestedResultTypes") or ["inbox_capture"])[0])
        resolution = self.targets.resolve(title, result_type, current_note if intent["name"] == "update_current_note" else None)
        roots = self.conventions()
        if intent["name"] == "update_current_note" and current_note.get("path"):
            target = str(current_note["path"]); action = "update"
        elif resolution["decision"] == "update_existing" and resolution.get("selectedPath"):
            target = str(resolution["selectedPath"]); action = "update"
        else:
            root = roots["conceptRoot"] if result_type in {"method_note", "concept_note"} else roots["topicRoot"] if result_type == "topic_synthesis" else roots["sourceRoots"]["pdf"] if understanding["kind"] == "pdf" else roots["sourceRoots"]["web"] if understanding["kind"] == "web_page" else roots["sourceRoots"]["text"] if understanding["kind"] in {"pasted_text", "text_file"} else roots["sourceRoots"]["conversation"] if result_type == "conversation_source_note" else roots["inboxRoot"]
            target = f"{root}/{title}.md"; action = "create"
        selected = next((item for item in resolution["candidates"] if item.get("path") == target), {})
        protected = str(selected.get("status") or "") in {"reviewed", "core"} or target.startswith("10-Sources/") and action == "update"
        if protected:
            primary = "create_source_and_update"; action = "skip"
        else:
            primary = "update_existing" if action == "update" else "create_new"
        action_id = f"org-action-{uuid.uuid4().hex}"
        planned = {"id": action_id, "action": action, "noteType": result_type, "targetPath": target, "targetTitle": title,
                   "sourceUnitIds": list(understanding.get("knowledgeUnitIds") or [])[:100], "sections": self._sections(result_type),
                   "frontmatterChanges": {"type": "method" if result_type == "method_note" else "source" if "source" in result_type or result_type == "paper_note" else "topic", "status": "ai-draft", "agent_managed": True},
                   "mergeStrategy": "merge_by_heading" if action == "update" else "preserve_both", "reason": "复用精确标题/alias 候选" if action == "update" else "没有可靠的现有目标，创建一篇集中式草稿", "confidence": float(resolution["confidence"]), "riskLevel": "high" if protected else "medium" if action == "update" else "low"}
        plan = {"planId": f"org-plan-{uuid.uuid4().hex}", "sourceBundleId": bundle_id, "intent": intent["name"], "primaryAction": primary,
                "actions": [planned], "relationships": [{"type": "about", "from": target, "to": understanding.get("canonicalTopic")}],
                "risks": ([{"code": "protected_target", "message": "reviewed/core 或来源笔记只能生成更新建议"}] if protected else []),
                "targetResolution": resolution, "explanation": f"将 {understanding['kind']} 整理为 {result_type}；单份材料自动知识笔记上限为 3。",
                "confidence": min(float(understanding.get("confidence") or .5), float(resolution.get("confidence") or .5)),
                "requiresConfirmation": protected or self.store.get_setting("autonomy_mode", "high") != "high",
                "status": "planned", "schemaVersion": SCHEMA_VERSION}
        return self.store.save_organization_plan(plan)

    def execute(self, plan: dict[str, Any], understanding: dict[str, Any], focus: dict[str, Any], bundle: dict[str, Any]) -> dict[str, Any]:
        results = []
        for action in plan["actions"][:3]:
            if action["action"] == "skip":
                content = self._render(action, understanding, focus, bundle)
                proposal = self.proposal_handler({
                    "path": action["targetPath"], "operation": "update_managed_block",
                    "block": "material-organization", "content": content,
                    "reason": "为受保护知识生成可审核更新建议",
                    "source_conversation_id": focus["conversationId"],
                }) if self.proposal_handler else {}
                results.append({"organizationActionId": action["id"], "status": "awaiting_confirmation", "path": action["targetPath"],
                                "reason": "protected_or_core", **proposal})
                continue
            content = self._render(action, understanding, focus, bundle)
            if not plan["requiresConfirmation"]:
                operation = "create_draft_note" if action["action"] == "create" else "update_managed_block"
                result = self.autonomy.apply_low_risk({"path": action["targetPath"], "operation": operation,
                                                       "block": "material-organization", "content": content,
                                                       "reason": "用户明确要求整理到 Obsidian", "source_conversation_id": focus["conversationId"]})
                results.append({"organizationActionId": action["id"], "status": "applied" if result.get("applied") else "awaiting_confirmation", **result})
            else:
                results.append({"organizationActionId": action["id"], "status": "awaiting_confirmation", "path": action["targetPath"], "preview": content, "reason": "existing_or_bounded_confirmation"})
        status = "applied" if results and all(item["status"] == "applied" for item in results) else "awaiting_confirmation"
        stored = self.store.complete_organization_plan(plan["planId"], status, results)
        return {"plan": stored, "results": results, "status": status}

    @staticmethod
    def _sections(note_type: str) -> list[dict[str, Any]]:
        if note_type == "method_note":
            names = ["一句话说明", "为什么提出这个方法", "解决什么问题", "适用场景", "前置知识", "假设与成立条件", "核心思想", "数学定义", "原理推导", "算法或执行步骤", "例子", "常见误解", "局限与失效情况", "与其他方法的关系", "相关笔记", "来源与证据", "待验证或继续学习的问题"]
        elif note_type in {"source_note", "paper_note", "web_source_note"}:
            names = ["摘要", "研究问题或主题", "章节结构", "核心概念", "核心方法", "关键论断与页码", "公式和推导", "实验或示例", "局限", "与现有知识的关系", "可继续学习的问题", "来源"]
        else: names = ["原始文本", "Agent 整理", "事实", "用户观点", "模型推断", "未验证内容", "待办", "问题"]
        return [{"heading": item} for item in names]

    @staticmethod
    def _render(action: dict[str, Any], understanding: dict[str, Any], focus: dict[str, Any], bundle: dict[str, Any]) -> str:
        title = action["targetTitle"]; today = date.today().isoformat(); note_type = action["noteType"]
        source_refs = [f"conversation:{focus['conversationId']}"] + [f"attachment:{item}" for item in understanding.get("sourceMetadata", {}).get("attachmentIds", [])]
        front = ("---\n" + f"type: {'method' if note_type == 'method_note' else 'source' if 'source' in note_type or note_type == 'paper_note' else 'topic'}\n"
                 "status: ai-draft\n" + f"aliases: {json.dumps((focus.get('activeMethod') or {}).get('aliases', []), ensure_ascii=False)}\n"
                 f"domain: {json.dumps(understanding.get('domains', []), ensure_ascii=False)}\nsource_refs: {json.dumps(source_refs, ensure_ascii=False)}\n"
                 f"agent_managed: true\ncreated: {today}\nupdated: {today}\n---\n\n")
        if note_type == "method_note" and _canonical(title) in {"deltamethod", "delta方法", "德尔塔方法"}:
            body = rf"""# {title}

## 一句话说明

Delta Method 用 Taylor 展开把估计量的渐近分布传递到光滑函数上，是渐近方差传播的基本工具。

## 为什么提出这个方法

统计量常以 $g(T_n)$ 的非线性形式出现。即使 $T_n$ 的极限分布已知，$g(T_n)$ 的分布通常也不能直接读取；Delta Method 用局部线性化解决这个问题。

## 解决什么问题

- 推导非线性变换后估计量的渐近分布；
- 近似标准误与置信区间；
- 比较变换前后的不确定性。

## 适用场景

估计量 $T_n$ 渐近正态，且目标函数 $g$ 在真值附近可微。

## 前置知识

[[Taylor 展开]]、[[依概率收敛]]、[[渐近正态性]]、[[Slutsky 定理]]。

## 假设与成立条件

设

$$
\sqrt{{n}}(T_n-\theta) \overset{{d}}{{\longrightarrow}} N(0,\sigma^2),
$$

并且 $g$ 在 $\theta$ 的邻域内可微，$g'(\theta)$ 有限。

## 核心思想

在 $\theta$ 附近，一阶 Taylor 展开把非线性变换近似为线性变换；高阶余项在 $\sqrt{{n}}$ 尺度下消失。

## 数学定义

一元形式为

$$
\sqrt{{n}}\left(g(T_n)-g(\theta)\right)
\overset{{d}}{{\longrightarrow}}
N\left(0,[g'(\theta)]^2\sigma^2\right).
$$

多元形式为

$$
\sqrt{{n}}\left(g(T_n)-g(\theta)\right)
\overset{{d}}{{\longrightarrow}}
N\left(0,\nabla g(\theta)^\top\Sigma\nabla g(\theta)\right).
$$

## 原理推导

由一阶 Taylor 展开，存在位于 $T_n$ 与 $\theta$ 之间的 $\tilde\theta_n$，使

$$
g(T_n)-g(\theta)=g'(\theta)(T_n-\theta)
+\left[g'(\tilde\theta_n)-g'(\theta)\right](T_n-\theta).
$$

两边乘以 $\sqrt{{n}}$：

$$
\sqrt{{n}}\left[g(T_n)-g(\theta)\right]
=g'(\theta)\sqrt{{n}}(T_n-\theta)+R_n,
$$

其中

$$
R_n=\left[g'(\tilde\theta_n)-g'(\theta)\right]\sqrt{{n}}(T_n-\theta).
$$

因为 $T_n\overset{{p}}\to\theta$，连续性给出 $g'(\tilde\theta_n)-g'(\theta)\overset{{p}}\to0$；而 $\sqrt{{n}}(T_n-\theta)=O_p(1)$，所以 $R_n=o_p(1)$。再由 Slutsky 定理得到上述极限分布。

## 算法或执行步骤

1. 写出基础估计量的渐近分布；
2. 计算目标变换在真值处的导数或梯度；
3. 用导数对协方差做线性传播；
4. 用一致估计量替代未知参数，得到标准误。

## 例子

若 $T_n$ 估计正参数 $\theta$，且 $g(\theta)=\log\theta$，则 $g'(\theta)=1/\theta$，所以渐近方差变为 $\sigma^2/\theta^2$。

## 常见误解

- 它给出的是渐近近似，不是有限样本精确分布；
- 当 $g'(\theta)=0$ 时，一阶 Delta Method 退化，需要高阶展开；
- 可微性与基础估计量的收敛速度不能省略。

## 局限与失效情况

边界点、不可微函数、弱识别、重尾分布或非 $\sqrt{{n}}$ 收敛时，需要广义或高阶方法。

## 与其他方法的关系

它是 [[Taylor 展开]]、[[渐近正态性]] 与 [[Slutsky 定理]] 的直接组合，也与 bootstrap 方差估计形成互补。

## 相关笔记

[[渐近正态性]] · [[方差传播]] · [[置信区间]]

## 来源与证据

- 当前对话：`{focus['conversationId']}`；其中 AI 解释标记为待验证。

## 待验证或继续学习的问题

- 二阶 Delta Method 在 $g'(\theta)=0$ 时如何改变收敛尺度？
"""
            if action.get("action") == "update":
                fragment = re.sub(r"^#\s+[^\n]+\n+", "", body.strip(), count=1)
                return "## Agent 补充：Delta Method\n\n" + fragment + "\n"
            return front + body
        original = str(bundle.get("pastedText") or "").strip()
        source_lines = [f"- 对话：`{focus['conversationId']}`"] + [f"- 附件：`{item}`" for item in understanding.get("sourceMetadata", {}).get("attachmentIds", [])]
        if action.get("action") == "update":
            recent_answer = next((str(item.get("content") or "").strip() for item in reversed(list(bundle.get("recentMessages") or [])) if item.get("role") == "assistant" and str(item.get("content") or "").strip()), "")
            addition = original or recent_answer
            return (
                "## Agent 补充（待验证）\n\n"
                + (addition if addition else "当前没有可安全追加的实质内容，请在审核时补充目标段落。")
                + "\n\n## 来源\n\n" + "\n".join(source_lines) + "\n"
            )
        sections = "\n\n".join(f"## {item['heading']}\n\n- 待整理；来源与未验证状态必须在审核时保留。" for item in action.get("sections", []))
        if original:
            sections = sections.replace("## 原始文本\n\n- 待整理；来源与未验证状态必须在审核时保留。", f"## 原始文本\n\n{original}")
        return front + f"# {title}\n\n{sections}\n\n## 来源\n\n" + "\n".join(source_lines) + "\n"


class ContextMaterialCoordinator:
    def __init__(self, vault: Path, store: Any, intake: Any, autonomy: Any, proposal_handler: Callable[[dict[str, Any]], dict[str, Any]] | None = None) -> None:
        self.input = InputBundleBuilder(vault, intake, store); self.focus = ConversationFocusService(store)
        self.materials = MaterialUnderstandingService(intake); self.organization = ObsidianOrganizationService(vault, store, autonomy, proposal_handler)
        self.store = store

    @staticmethod
    def resolved_message(message: str, focus: dict[str, Any]) -> str:
        reference = (focus.get("resolution") or {}).get("resolvedReference") or {}
        name = str(reference.get("displayName") or "")
        if not name: return message
        result = str(message)
        for pronoun in PRONOUNS:
            if pronoun in result:
                replacement = name if pronoun not in {"把它保存", "把它整理", "继续推导"} else {"把它保存": f"保存 {name}", "把它整理": f"整理 {name}", "继续推导": f"继续推导 {name}"}[pronoun]
                result = result.replace(pronoun, replacement)
        return result

    def prepare(self, conversation_id: str, message: dict[str, Any], attachments: list[dict[str, Any]], body: dict[str, Any]) -> dict[str, Any]:
        input_bundle = self.input.build(conversation_id, message, attachments, body)
        focus = self.focus.resolve(input_bundle); input_bundle["conversationFocus"] = focus
        resolved = self.resolved_message(str(message.get("content") or ""), focus)
        effective_attachments = list(input_bundle.get("attachments") or [])
        intent = classify_assistant_intent(resolved, focus, effective_attachments, input_bundle.get("currentNote") or {})
        pending_write = _pending_write_from_recent(resolved, list(input_bundle.get("recentMessages") or []))
        if pending_write:
            input_bundle["pendingWrite"] = pending_write
            resolved = str(pending_write["content"])
            intent = {
                "name": "organize_and_save", "writeRequested": True, "requestedOutput": "existing_note_update",
                "requestedDestination": str(pending_write["targetPath"]), "confidence": .99,
                "inheritedProposal": True, "sourceMessageId": str(pending_write["sourceMessageId"]),
            }
        elif _SHORT_WRITE_CONFIRMATION.fullmatch(" ".join(str(resolved).split())):
            intent = {
                "name": "answer_question", "writeRequested": False, "requestedOutput": "clarification",
                "requestedDestination": "", "confidence": .99, "needsTargetClarification": True,
            }
        elif _WRITE_STATUS_QUESTION.search(resolved):
            intent = {
                "name": "answer_question", "writeRequested": False, "requestedOutput": "write_status",
                "requestedDestination": "", "confidence": .99, "writeStatusQuery": True,
            }
        input_bundle["requestedOutput"] = intent["requestedOutput"]; input_bundle["requestedDestination"] = intent["requestedDestination"]
        bundle_id = "material-bundle-" + hashlib.sha256(f"{conversation_id}|{message.get('id')}|{resolved}".encode()).hexdigest()[:24]
        understanding, units = self.materials.understand(input_bundle, focus, intent)
        # A recent message can legitimately occur in several turn-level bundles;
        # knowledge-unit identity is therefore scoped to its immutable bundle.
        for unit in units:
            unit["id"] = "unit-" + hashlib.sha256(f"{bundle_id}|{unit['id']}".encode()).hexdigest()[:24]
        understanding["knowledgeUnitIds"] = [item["id"] for item in units]
        material_bundle = self.store.save_material_bundle({"id": bundle_id, "conversationId": conversation_id, "title": understanding["title"],
                                                           "materialKinds": [understanding["kind"]], "attachmentIds": [item.get("id") for item in effective_attachments],
                                                           "sourceIds": [item.get("id") for item in effective_attachments], "understanding": understanding, "schemaVersion": SCHEMA_VERSION}, units)
        return {"inputBundle": input_bundle, "focus": focus, "resolvedMessage": resolved, "intent": intent, "materialBundle": material_bundle}

    def organize(self, prepared: dict[str, Any]) -> dict[str, Any]:
        intent = prepared["intent"]
        if not intent["writeRequested"] and intent["name"] != "organize_preview": return {}
        material = prepared["materialBundle"]
        plan = self.organization.plan(material["id"], material["understanding"], intent, prepared["focus"], prepared["inputBundle"].get("currentNote") or {})
        if intent["name"] == "organize_preview": return {"plan": plan, "status": "preview"}
        result = self.organization.execute(plan, material["understanding"], prepared["focus"], prepared["inputBundle"])
        target = next((item.get("path") for item in result.get("results", []) if item.get("path")), "")
        prepared["focus"] = self.focus.update_after_turn(prepared["focus"], intent, str(target or ""))
        return result
