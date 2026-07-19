from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from typing import Any, Protocol


SCHEMA_VERSION = 1
TERM = re.compile(r"[A-Za-z0-9_.+-]+|[\u4e00-\u9fff]{2,}")


class EmbeddingProvider(Protocol):
    """Optional local-only embedding provider."""

    def available(self) -> bool: ...
    def search(self, query: str, entries: list[dict[str, Any]], limit: int) -> list[tuple[str, float]]: ...


def _list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if not value:
        return []
    return [item.strip() for item in re.split(r"[,;\n]", str(value)) if item.strip()]


def _terms(query: str) -> list[str]:
    return list(dict.fromkeys(item.casefold() for item in TERM.findall(query) if len(item) > 1))[:24]


def _rrf(rank: int, constant: int = 60) -> float:
    return 1.0 / (constant + rank)


class HybridVaultIndex:
    """Explainable Exact + FTS5 + optional local semantic + graph fusion."""

    def __init__(self, embedding: EmbeddingProvider | None = None) -> None:
        self.embedding = embedding
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.executescript(
            """
            CREATE TABLE documents(
              path TEXT PRIMARY KEY, source_hash TEXT NOT NULL, modified_at TEXT NOT NULL,
              title TEXT NOT NULL, aliases TEXT NOT NULL, headings TEXT NOT NULL,
              body TEXT NOT NULL, tags TEXT NOT NULL, domain TEXT NOT NULL,
              note_type TEXT NOT NULL, status TEXT NOT NULL, links TEXT NOT NULL
            );
            CREATE VIRTUAL TABLE documents_fts USING fts5(
              path UNINDEXED, title, aliases, headings, body, tags,
              tokenize='unicode61 remove_diacritics 2'
            );
            """
        )

    def sync(self, entries: list[dict[str, Any]]) -> None:
        present = {str(item["path"]) for item in entries}
        known = {str(row["path"]): str(row["source_hash"]) for row in self.db.execute("SELECT path, source_hash FROM documents")}
        for removed in set(known) - present:
            self.db.execute("DELETE FROM documents WHERE path=?", (removed,))
            self.db.execute("DELETE FROM documents_fts WHERE path=?", (removed,))
        for item in entries:
            path = str(item["path"])
            source_hash = str(item.get("sourceHash") or "")
            if known.get(path) == source_hash:
                continue
            aliases = " ".join(_list(item.get("aliases")))
            tags = " ".join(_list(item.get("tags")))
            headings = "\n".join(_list(item.get("headings")))
            links = " ".join(_list(item.get("links")))
            values = (
                path, source_hash, str(item.get("modifiedAt") or ""), str(item.get("title") or ""),
                aliases, headings, str(item.get("body") or ""), tags, str(item.get("domain") or ""),
                str(item.get("type") or "note"), str(item.get("status") or ""), links,
            )
            self.db.execute(
                """INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(path) DO UPDATE SET source_hash=excluded.source_hash,
                   modified_at=excluded.modified_at,title=excluded.title,aliases=excluded.aliases,
                   headings=excluded.headings,body=excluded.body,tags=excluded.tags,
                   domain=excluded.domain,note_type=excluded.note_type,status=excluded.status,links=excluded.links""",
                values,
            )
            self.db.execute("DELETE FROM documents_fts WHERE path=?", (path,))
            self.db.execute(
                "INSERT INTO documents_fts(path,title,aliases,headings,body,tags) VALUES (?, ?, ?, ?, ?, ?)",
                (path, values[3], aliases, headings, values[6], tags),
            )
        self.db.commit()

    def search(self, query: str, entries: list[dict[str, Any]], limit: int, current_note: str = "", focus: str = "") -> dict[str, Any]:
        self.sync(entries)
        terms = _terms(query)
        by_path = {str(item["path"]): item for item in entries}
        channel: dict[str, dict[str, float]] = defaultdict(dict)
        fields: dict[str, set[str]] = defaultdict(set)

        query_folded = query.casefold().strip()
        for item in entries:
            path = str(item["path"])
            title = str(item.get("title") or "").casefold()
            aliases = " ".join(_list(item.get("aliases"))).casefold()
            tags = " ".join(_list(item.get("tags"))).casefold()
            headings = " ".join(_list(item.get("headings"))).casefold()
            domain = str(item.get("domain") or "").casefold()
            exact = 0.0
            if query_folded and title == query_folded: exact += 12; fields[path].add("title")
            elif query_folded and title.startswith(query_folded): exact += 8; fields[path].add("title")
            for term in terms:
                if term in title: exact += 5; fields[path].add("title")
                if term in aliases: exact += 4; fields[path].add("aliases")
                if term in tags: exact += 3; fields[path].add("tags")
                if term in headings: exact += 3; fields[path].add("headings")
                if term in domain: exact += 2; fields[path].add("domain")
                if term in path.casefold(): exact += 2; fields[path].add("path")
            if exact: channel[path]["exact"] = exact

        if terms:
            expression = " OR ".join(f'"{term.replace(chr(34), chr(34) * 2)}"*' for term in terms)
            try:
                rows = self.db.execute(
                    """SELECT path, bm25(documents_fts, 0.0, 8.0, 5.0, 4.0, 1.0, 3.0) AS rank
                       FROM documents_fts WHERE documents_fts MATCH ? ORDER BY rank LIMIT ?""",
                    (expression, max(limit * 8, 40)),
                ).fetchall()
                for rank, row in enumerate(rows, 1):
                    channel[str(row["path"])]["fts"] = _rrf(rank)
                    fields[str(row["path"])].add("body")
            except sqlite3.OperationalError:
                # Unicode FTS tokenization can miss unsegmented CJK. The legacy
                # substring path remains a lowest-priority fallback only.
                pass
            for item in entries:
                path = str(item["path"])
                if path in channel:
                    continue
                body = str(item.get("body") or "").casefold()
                matches = sum(body.count(term) for term in terms)
                if matches:
                    channel[path]["substringFallback"] = min(3.0, float(matches))
                    fields[path].add("body")

        current = by_path.get(current_note)
        current_links = set(_list(current.get("links"))) if current else set()
        current_domain = str(current.get("domain") or "") if current else ""
        for path, item in by_path.items():
            graph = 0.0
            if str(item.get("title") or "") in current_links or PathLike.stem(path) in current_links: graph += 2.0
            if current_domain and str(item.get("domain") or "") == current_domain: graph += 1.0
            if focus and focus.casefold() in f"{item.get('title', '')} {item.get('domain', '')}".casefold(): graph += 1.0
            if graph: channel[path]["graph"] = graph

        semantic_state = "disabled"
        if self.embedding is not None:
            try:
                if self.embedding.available():
                    semantic_state = "available"
                    for rank, (path, score) in enumerate(self.embedding.search(query, entries, max(limit * 4, 20)), 1):
                        channel[str(path)]["semantic"] = max(float(score), _rrf(rank))
                else:
                    semantic_state = "unavailable"
            except Exception:
                semantic_state = "degraded"

        ranked: list[tuple[float, str]] = []
        for path, scores in channel.items():
            fused = 0.0
            if "exact" in scores: fused += min(0.25, scores["exact"] / 80.0) + _rrf(1)
            if "fts" in scores: fused += scores["fts"]
            if "semantic" in scores: fused += _rrf(max(1, int(1 / max(scores["semantic"], 1e-6))))
            if "graph" in scores: fused += min(0.05, scores["graph"] / 100.0)
            if "substringFallback" in scores: fused += min(0.01, scores["substringFallback"] / 1000.0)
            ranked.append((fused, path))
        ranked.sort(key=lambda pair: (-pair[0], pair[1].casefold()))

        items = []
        for score, path in ranked[:limit]:
            item = by_path[path]
            body = str(item.get("body") or "")
            position = min((body.casefold().find(term) for term in terms if term in body.casefold()), default=0)
            excerpt = body[max(0, position - 120):position + 360].strip().replace("\n", " ")
            items.append({
                "title": item.get("title"), "path": path, "score": round(score, 6),
                "channelScores": channel[path], "matchedFields": sorted(fields[path]),
                "excerpt": excerpt, "modifiedAt": item.get("modifiedAt"),
                "status": item.get("status"), "sourceHash": item.get("sourceHash"),
                "type": item.get("type"), "domain": item.get("domain"),
            })
        return {"query": query, "items": items, "total": len(ranked), "schemaVersion": SCHEMA_VERSION, "semanticState": semantic_state}


class PathLike:
    @staticmethod
    def stem(path: str) -> str:
        return path.rsplit("/", 1)[-1].removesuffix(".md")
