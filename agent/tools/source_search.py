from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from .vault_access import search_vault


class ResearchProvider(Protocol):
    name: str
    def search(self, query: str, limit: int) -> list[dict[str, Any]]: ...


@dataclass
class UnavailableResearchProvider:
    """Named external-provider boundary without implicit network access."""

    name: str
    reason: str

    def search(self, query: str, limit: int) -> list[dict[str, Any]]:
        raise RuntimeError(self.reason)


class ArxivResearchProvider(UnavailableResearchProvider):
    def __init__(self) -> None:
        super().__init__("arxiv", "arxiv_provider_not_configured")


class CrossrefResearchProvider(UnavailableResearchProvider):
    def __init__(self) -> None:
        super().__init__("crossref", "crossref_provider_not_configured")


class GenericWebSearchProvider(UnavailableResearchProvider):
    def __init__(self) -> None:
        super().__init__("generic_web_search", "web_search_provider_not_configured")


class UserProvidedUrlProvider(UnavailableResearchProvider):
    def __init__(self) -> None:
        super().__init__("user_provided_url", "use_source_fetch_for_explicit_urls")


@dataclass
class VaultResearchProvider:
    vault: Path
    name: str = "local_vault"

    def search(self, query: str, limit: int) -> list[dict[str, Any]]:
        rows = search_vault(self.vault, {"query": query, "limit": limit})["items"]
        return [{
            "id": f"source-{hashlib.sha256(('vault:' + row['path']).encode()).hexdigest()[:16]}",
            "source_type": self.name, "title": row["title"], "canonical_url": "",
            "authors": "", "published_at": None, "relevance": min(1, .55 + row["score"] * .1),
            "quality": .85 if row.get("status") in {"reviewed", "core"} else .6,
            "difficulty": "medium", "estimated_minutes": 8,
            "reason": "与本地知识库中的标题或正文匹配。", "metadata": {"path": row["path"], "status": row.get("status", "")},
        } for row in rows]


@dataclass
class ImportedMaterialProvider:
    vault: Path
    name: str = "imported_source"

    def search(self, query: str, limit: int) -> list[dict[str, Any]]:
        root = self.vault / "10-Sources"
        if not root.exists():
            return []
        terms = [term.casefold() for term in query.split() if len(term) > 1]
        rows: list[tuple[int, Path]] = []
        for path in root.rglob("*.md"):
            if path.is_symlink():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            score = sum(term in (path.stem + " " + text[:5000]).casefold() for term in terms)
            if score or not terms:
                rows.append((score, path))
        rows.sort(key=lambda row: (-row[0], row[1].stem.casefold()))
        return [{
            "id": f"source-{hashlib.sha256(('imported:' + str(path.relative_to(self.vault))).encode()).hexdigest()[:16]}",
            "source_type": self.name, "title": path.stem, "canonical_url": "", "authors": "",
            "published_at": None, "relevance": min(1, .5 + score * .12), "quality": .75,
            "difficulty": "medium", "estimated_minutes": 15,
            "reason": "已导入资料与研究问题匹配。", "metadata": {"path": str(path.relative_to(self.vault))},
        } for score, path in rows[:limit]]


@dataclass
class StaticResearchProvider:
    """Offline-test provider; never enabled implicitly in production."""
    items: list[dict[str, Any]]
    name: str = "academic_api"

    def search(self, query: str, limit: int) -> list[dict[str, Any]]:
        return [dict(item) for item in self.items[:limit]]


def search_sources(providers: list[ResearchProvider], payload: dict[str, Any]) -> dict[str, Any]:
    query = str(payload.get("query", "")).strip()
    limit = max(1, min(30, int(payload.get("limit", 12))))
    if not query:
        raise ValueError("research_query_required")
    results: list[dict[str, Any]] = []
    unavailable: list[dict[str, str]] = []
    for provider in providers:
        try:
            results.extend(provider.search(query, limit))
        except Exception as error:
            unavailable.append({"provider": provider.name, "status": "unavailable", "reason": type(error).__name__})
    deduped: dict[tuple[str, str], dict[str, Any]] = {}
    for item in results:
        key = (str(item.get("title", "")).strip().casefold(), str(item.get("canonical_url", "")).strip())
        current = deduped.get(key)
        if not current or float(item.get("relevance", 0)) + float(item.get("quality", 0)) > float(current.get("relevance", 0)) + float(current.get("quality", 0)):
            deduped[key] = item
    ordered = sorted(deduped.values(), key=lambda item: (-float(item.get("relevance", 0)), -float(item.get("quality", 0)), str(item.get("title", ""))))[:limit]
    return {"query": query, "retrieved_at": datetime.now().astimezone().isoformat(timespec="seconds"), "sources": ordered, "providers": unavailable}
