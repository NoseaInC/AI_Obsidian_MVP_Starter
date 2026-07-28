from __future__ import annotations

import hashlib
import re
from datetime import date
from pathlib import Path
from typing import Any


INVALID_TITLE = re.compile(r"[\\/:*?\"<>|#^\[\]]+")


def safe_title(text: str, fallback: str = "未命名记录") -> str:
    first = next((line.strip(" #\t") for line in text.splitlines() if line.strip()), fallback)
    first = INVALID_TITLE.sub(" ", first)
    first = re.sub(r"\s+", " ", first).strip(" .-")
    return (first[:48].strip() or fallback)


def unique_path(vault: Path, folder: Path, title: str, text: str) -> str:
    base = folder / f"{title}.md"
    if not (vault / base).exists():
        return str(base)
    suffix = hashlib.sha256(text.encode()).hexdigest()[:8]
    return str(folder / f"{title}-{suffix}.md")


def inbox_folder(vault: Path, kind: str) -> Path:
    candidates = {
        "idea": [Path("01-Inbox/Ideas"), Path("00-Inbox/Ideas")],
        "question": [Path("01-Inbox/Questions"), Path("00-Inbox/Questions")],
        "learning_reflection": [Path("30-Learning/Reflections")],
    }.get(kind, [Path("01-Inbox/Notes"), Path("00-Inbox/Notes"), Path("01-Inbox")])
    return next((item for item in candidates if (vault / item).is_dir()), candidates[0])


def classify_text(text: str) -> str:
    lowered = text.casefold()
    if "?" in text or "？" in text or lowered.startswith(("why ", "how ", "什么", "为什么", "如何")):
        return "question"
    if any(word in text for word in ("我决定", "决策", "决定采用")):
        return "decision"
    if any(word in text for word in ("待办", "任务", "TODO", "todo")):
        return "task"
    if any(word in text for word in ("学习反思", "今天学到", "复盘")):
        return "learning_reflection"
    if any(word in text for word in ("灵感", "想法", "idea")):
        return "idea"
    if text.lstrip().startswith((">", "“", "\"")):
        return "quote"
    return "note"


def frontmatter(kind: str, tags: list[str] | None = None) -> str:
    tag_text = ", ".join(tags or [])
    return (
        "---\n"
        f"type: {kind}\n"
        "status: inbox\n"
        f"created: {date.today().isoformat()}\n"
        "domain: []\n"
        f"tags: [{tag_text}]\n"
        "source: user\n"
        "---\n"
    )


def note_result(title: str, path: str, content: str, category: str) -> dict[str, Any]:
    return {"title": title, "path": path, "content": content, "category": category}
