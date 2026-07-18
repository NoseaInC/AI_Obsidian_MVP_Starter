from __future__ import annotations

from collections import Counter
from datetime import datetime
import re
from pathlib import Path
from threading import RLock
from typing import Any

import sys

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "00-System/Scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import ingest_pdf


WORD = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)
WIKI_LINK = re.compile(r"\[\[([^\]|#]+)(?:\|[^\]]+)?\]\]")
LATIN_WORD = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.+-]+")
CJK_SEQUENCE = re.compile(r"[\u4e00-\u9fff]+")
SAFE_ROOTS = ("00-Inbox", "01-Inbox", "10-Sources", "20-Knowledge", "30-Learning", "40-Projects")
CJK_QUERY_FILLERS = (
    "请帮我", "帮我", "读取一下", "查看一下", "介绍一下", "解释一下", "总结一下",
    "读取", "查看", "浏览", "介绍", "解释", "总结", "现在", "当前", "我的", "一下",
    "有哪些", "有什么", "知识库", "知识", "笔记", "内容",
)


def _safe_relative(relative: Path) -> bool:
    return bool(relative.parts) and relative.parts[0] in SAFE_ROOTS


def safe_note(vault: Path, relative: str) -> Path:
    if not relative or Path(relative).is_absolute():
        raise ValueError("invalid_note_path")
    target = (vault / relative).resolve()
    try:
        target_relative = target.relative_to(vault)
    except ValueError as error:
        raise ValueError("invalid_note_path") from error
    if target.suffix.lower() != ".md" or target.is_symlink():
        raise ValueError("invalid_note_path")
    for parent in [target.parent, *target.parents]:
        if parent == vault:
            break
        if parent.is_symlink():
            raise ValueError("symlink_path_not_allowed")
    return target


def safe_read_note(vault: Path, relative: str) -> Path:
    target = safe_note(vault, relative)
    if not _safe_relative(target.relative_to(vault.resolve())):
        raise ValueError("invalid_note_path")
    return target


def _terms(value: str) -> set[str]:
    return {item.casefold() for item in WORD.findall(value) if len(item) > 1}


def _query_terms(value: str) -> list[str]:
    terms = {item.casefold() for item in LATIN_WORD.findall(value) if len(item) > 1}
    cjk_text = value
    for filler in CJK_QUERY_FILLERS:
        cjk_text = cjk_text.replace(filler, " ")
    for sequence in CJK_SEQUENCE.findall(cjk_text):
        if len(sequence) < 2:
            continue
        terms.add(sequence)
        if len(sequence) > 4:
            for size in (2, 3, 4):
                terms.update(sequence[index:index + size] for index in range(len(sequence) - size + 1))
    return sorted(terms, key=lambda item: (-len(item), item))[:40]


class VaultReadIndex:
    """Incremental read-only index for model-visible Vault roots.

    Directory enumeration remains cheap, while unchanged Markdown files are not
    reopened and reparsed for every assistant turn. Private/runtime/project-code
    roots never enter the index.
    """

    def __init__(self, vault: Path) -> None:
        self.vault = vault.resolve()
        self._cache: dict[str, tuple[int, int, dict[str, Any]]] = {}
        self._lock = RLock()

    def entries(self) -> list[dict[str, Any]]:
        with self._lock:
            present: set[str] = set()
            for root_name in SAFE_ROOTS:
                root = self.vault / root_name
                if not root.is_dir() or root.is_symlink():
                    continue
                for path in root.rglob("*.md"):
                    if path.is_symlink() or not path.is_file():
                        continue
                    relative = path.relative_to(self.vault)
                    if not _safe_relative(relative) or any(parent.is_symlink() for parent in path.parents if parent != self.vault):
                        continue
                    key = relative.as_posix()
                    present.add(key)
                    try:
                        stat = path.stat()
                    except OSError:
                        continue
                    cached = self._cache.get(key)
                    if cached and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
                        continue
                    try:
                        text = path.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        continue
                    meta = ingest_pdf.parse_frontmatter(text)
                    if str(meta.get("agent_access", "")).strip().casefold() == "denied":
                        self._cache.pop(key, None)
                        continue
                    entry = {
                        "title": path.stem,
                        "path": key,
                        "root": relative.parts[0],
                        "status": str(meta.get("status", "")),
                        "type": str(meta.get("type", "note")),
                        "domain": str(meta.get("domain", "")),
                        "aliases": meta.get("aliases", ""),
                        "excerpt": text[:6_000],
                        "modifiedAt": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds"),
                        "mtime": stat.st_mtime,
                    }
                    self._cache[key] = (stat.st_mtime_ns, stat.st_size, entry)
            for key in set(self._cache) - present:
                self._cache.pop(key, None)
            return [dict(item[2]) for item in self._cache.values()]

    def search(self, payload: dict[str, Any]) -> dict[str, Any]:
        query = str(payload.get("query", "")).strip()
        limit = max(1, min(50, int(payload.get("limit", 10))))
        terms = _query_terms(query)
        rows: list[tuple[int, dict[str, Any]]] = []
        for entry in self.entries():
            title = str(entry["title"]).casefold()
            metadata = f"{entry.get('aliases', '')} {entry.get('domain', '')} {entry.get('type', '')}".casefold()
            excerpt = str(entry.get("excerpt", "")).casefold()
            score = sum(8 for term in terms if term in title)
            score += sum(3 for term in terms if term in metadata)
            score += sum(1 for term in terms if term in excerpt)
            if query and (not terms or score == 0):
                continue
            rows.append((score, {
                "title": entry["title"], "path": entry["path"],
                "status": entry["status"], "type": entry["type"],
                "domain": entry["domain"], "score": score,
            }))
        rows.sort(key=lambda row: (-row[0], row[1]["title"].casefold()))
        return {"query": query, "items": [row[1] for row in rows[:limit]], "total": len(rows)}

    def overview(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        limit = max(3, min(20, int((payload or {}).get("recent_limit", 8))))
        entries = self.entries()
        recent = sorted(entries, key=lambda item: (-float(item["mtime"]), str(item["title"])))[:limit]
        domains = Counter(str(item.get("domain") or "未分类") for item in entries)
        return {
            "scope": list(SAFE_ROOTS),
            "totalNotes": len(entries),
            "byRoot": dict(sorted(Counter(str(item["root"]) for item in entries).items())),
            "byStatus": dict(sorted(Counter(str(item.get("status") or "未标记") for item in entries).items())),
            "byType": dict(sorted(Counter(str(item.get("type") or "note") for item in entries).items())),
            "topDomains": [{"domain": name, "count": count} for name, count in domains.most_common(8)],
            "recentNotes": [{
                "title": item["title"], "path": item["path"], "status": item["status"],
                "type": item["type"], "modifiedAt": item["modifiedAt"],
            } for item in recent],
        }


def search_vault(vault: Path, payload: dict[str, Any], index: VaultReadIndex | None = None) -> dict[str, Any]:
    return (index or VaultReadIndex(vault)).search(payload)


def vault_overview(vault: Path, payload: dict[str, Any], index: VaultReadIndex | None = None) -> dict[str, Any]:
    return (index or VaultReadIndex(vault)).overview(payload)


def list_vault_folder(
    vault: Path,
    payload: dict[str, Any],
    index: VaultReadIndex | None = None,
) -> dict[str, Any]:
    """List one model-visible Vault folder without exposing project/private roots.

    ``/`` and ``.`` are explicit aliases for the model-visible Vault root.  They
    intentionally return only ``SAFE_ROOTS`` instead of enumerating the real
    workspace root, which also contains runtime code and local-only state.
    """
    vault = vault.resolve()
    requested = str(payload.get("path", "")).strip().replace("\\", "/")
    root_alias = requested in {"", ".", "/", "./"}
    raw = requested.rstrip("/")
    entries = (index or VaultReadIndex(vault)).entries()
    limit = max(1, min(100, int(payload.get("limit", 50))))
    cursor = max(0, int(payload.get("cursor", 0)))

    if root_alias:
        rows: list[dict[str, Any]] = []
        for root_name in SAFE_ROOTS:
            root = vault / root_name
            if not root.is_dir() or root.is_symlink():
                continue
            direct_count = 0
            total_count = 0
            for entry in entries:
                entry_path = Path(str(entry.get("path") or ""))
                if not entry_path.parts or entry_path.parts[0] != root_name:
                    continue
                total_count += 1
                if entry_path.parent == Path(root_name):
                    direct_count += 1
            try:
                modified_at = datetime.fromtimestamp(
                    root.stat().st_mtime
                ).astimezone().isoformat(timespec="seconds")
            except OSError:
                modified_at = ""
            rows.append({
                "kind": "folder",
                "title": root_name,
                "path": root_name,
                "status": "",
                "type": "folder",
                "modifiedAt": modified_at,
                "directNoteCount": direct_count,
                "totalNoteCount": total_count,
            })
        page = rows[cursor:cursor + limit]
        next_cursor = cursor + len(page)
        return {
            "path": ".",
            "scope": "model-visible-vault-root",
            "recursive": False,
            "items": page,
            "total": len(rows),
            "nextCursor": next_cursor if next_cursor < len(rows) else None,
        }

    relative = Path(raw)
    if not raw or relative.is_absolute() or ".." in relative.parts or not _safe_relative(relative):
        raise ValueError("invalid_folder_path")
    target = (vault / relative).resolve()
    try:
        target.relative_to(vault)
    except ValueError as error:
        raise ValueError("invalid_folder_path") from error
    if target.is_symlink() or any(
        parent.is_symlink() for parent in [target, *target.parents] if parent != vault
    ):
        raise ValueError("symlink_path_not_allowed")
    if not target.is_dir():
        raise FileNotFoundError("folder_not_found")

    recursive = payload.get("recursive") is True
    prefix = relative.as_posix()
    rows: list[dict[str, Any]] = []
    if not recursive:
        try:
            child_directories = sorted(
                (
                    child for child in target.iterdir()
                    if child.is_dir() and not child.is_symlink()
                ),
                key=lambda child: (child.name.casefold(), child.name),
            )
        except OSError:
            child_directories = []
        for child in child_directories:
            child_relative = child.relative_to(vault)
            child_prefix = child_relative.as_posix()
            child_entries = [
                entry for entry in entries
                if str(entry.get("path") or "").startswith(f"{child_prefix}/")
            ]
            rows.append({
                "kind": "folder",
                "title": child.name,
                "path": child_prefix,
                "status": "",
                "type": "folder",
                "modifiedAt": "",
                "directNoteCount": sum(
                    Path(str(entry.get("path") or "")).parent == child_relative
                    for entry in child_entries
                ),
                "totalNoteCount": len(child_entries),
            })

    for entry in entries:
        entry_path = str(entry.get("path") or "")
        parent = str(Path(entry_path).parent.as_posix())
        if recursive:
            if parent != prefix and not parent.startswith(f"{prefix}/"):
                continue
        elif parent != prefix:
            continue
        rows.append({
            "kind": "note",
            "title": str(entry.get("title") or ""),
            "path": entry_path,
            "status": str(entry.get("status") or ""),
            "type": str(entry.get("type") or "note"),
            "modifiedAt": str(entry.get("modifiedAt") or ""),
        })
    rows.sort(key=lambda item: (
        0 if item.get("kind") == "folder" else 1,
        item["path"].casefold(),
        item["path"],
    ))
    page = rows[cursor:cursor + limit]
    next_cursor = cursor + len(page)
    return {
        "path": prefix,
        "recursive": recursive,
        "items": page,
        "total": len(rows),
        "nextCursor": next_cursor if next_cursor < len(rows) else None,
    }


def read_note_metadata(vault: Path, payload: dict[str, Any]) -> dict[str, Any]:
    path = safe_read_note(vault, str(payload.get("path", "")))
    if not path.is_file():
        raise FileNotFoundError("note_not_found")
    text = path.read_text(encoding="utf-8", errors="replace")
    meta = ingest_pdf.parse_frontmatter(text)
    if str(meta.get("agent_access", "")).strip().casefold() == "denied":
        raise PermissionError("agent_access_denied")
    allowed = {key: meta.get(key) for key in (
        "type", "status", "domain", "aliases", "tags", "mastery", "importance",
        "next_review", "weak_points", "artifact_id", "generated_from",
    ) if key in meta}
    return {"title": path.stem, "path": str(path.relative_to(vault)), "metadata": allowed}


def read_note_excerpt(vault: Path, payload: dict[str, Any]) -> dict[str, Any]:
    path = safe_read_note(vault, str(payload.get("path", "")))
    if not path.is_file():
        raise FileNotFoundError("note_not_found")
    max_chars = max(100, min(4000, int(payload.get("max_chars", 1200))))
    text = path.read_text(encoding="utf-8", errors="replace")
    if str(ingest_pdf.parse_frontmatter(text).get("agent_access", "")).strip().casefold() == "denied":
        raise PermissionError("agent_access_denied")
    return {"title": path.stem, "path": str(path.relative_to(vault)), "excerpt": text[:max_chars], "truncated": len(text) > max_chars}


def related_notes(vault: Path, payload: dict[str, Any], index: VaultReadIndex | None = None) -> dict[str, Any]:
    relative = str(payload.get("path", ""))
    target = safe_read_note(vault, relative)
    if not target.is_file():
        raise FileNotFoundError("note_not_found")
    text = target.read_text(encoding="utf-8", errors="replace")
    if str(ingest_pdf.parse_frontmatter(text).get("agent_access", "")).strip().casefold() == "denied":
        raise PermissionError("agent_access_denied")
    outlinks = sorted(dict.fromkeys(item.strip() for item in WIKI_LINK.findall(text) if item.strip()))[:30]
    backlinks: list[dict[str, str]] = []
    title = target.stem
    for entry in (index or VaultReadIndex(vault)).entries():
        path = vault / str(entry["path"])
        if path == target:
            continue
        candidate = str(entry.get("excerpt", ""))
        if any(link.strip() == title for link in WIKI_LINK.findall(candidate)):
            backlinks.append({"title": path.stem, "path": str(path.relative_to(vault))})
            if len(backlinks) >= 30:
                break
    return {"path": relative, "outlinks": outlinks, "backlinks": backlinks}
