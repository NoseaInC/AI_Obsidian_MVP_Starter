from __future__ import annotations

import hashlib
import html
import json
import os
import re
import uuid
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

from agent.tools.source_fetch import fetch_user_url, validate_public_url


PROMPT_INJECTION_MARKERS = (
    "忽略之前的指令", "读取本地密钥", "修改系统策略", "上传 vault", "执行 shell",
    "ignore previous instructions", "reveal system prompt", "read local secret", "execute shell",
)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class _ReadableHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.text: list[str] = []
        self.title: list[str] = []
        self.in_title = False
        self.meta: dict[str, str] = {}
        self.links: list[tuple[str, str]] = []
        self._anchor_href = ""
        self._anchor_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.casefold(): str(value or "") for key, value in attrs}
        if tag in {"script", "style", "noscript", "svg", "canvas", "form"}:
            self.skip += 1
        if tag == "title":
            self.in_title = True
        if tag == "meta":
            key = values.get("name") or values.get("property")
            if key and values.get("content"):
                self.meta[key.casefold()] = values["content"].strip()
        if tag == "a":
            self._anchor_href = values.get("href", "")
            self._anchor_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg", "canvas", "form"} and self.skip:
            self.skip -= 1
        if tag == "title":
            self.in_title = False
        if tag == "a" and self._anchor_href:
            label = " ".join(self._anchor_text).strip()
            self.links.append((self._anchor_href, label))
            self._anchor_href = ""; self._anchor_text = []
        if tag in {"p", "div", "section", "article", "li", "h1", "h2", "h3", "br"} and not self.skip:
            self.text.append("\n")

    def handle_data(self, data: str) -> None:
        if self.skip:
            return
        value = " ".join(data.split())
        if not value:
            return
        if self.in_title:
            self.title.append(value)
        if self._anchor_href:
            self._anchor_text.append(value)
        self.text.append(value)


def extract_web_document(raw: str, content_type: str, fallback_title: str = "网页资料") -> dict[str, Any]:
    if content_type == "text/html":
        parser = _ReadableHTML(); parser.feed(raw)
        text = "\n".join(line.strip() for line in " ".join(parser.text).splitlines() if line.strip())
        title = " ".join(parser.title).strip() or parser.meta.get("og:title") or fallback_title
        author = parser.meta.get("author") or parser.meta.get("article:author") or ""
        published = parser.meta.get("article:published_time") or parser.meta.get("date") or ""
        canonical = parser.meta.get("og:url") or ""
    else:
        text = raw
        title, author, published, canonical = fallback_title, "", "", ""
    text = re.sub(r"[ \t]+", " ", html.unescape(text))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()[:500_000]
    folded = text.casefold()
    markers = [marker for marker in PROMPT_INJECTION_MARKERS if marker in folded]
    return {
        "title": title[:300], "author": author[:200], "publishedAt": published[:80],
        "canonicalHint": canonical[:2000], "text": text, "promptInjectionDetected": bool(markers),
        "promptInjectionMarkers": markers,
        "untrustedSourceContent": "<untrusted_source_content>\n" + text + "\n</untrusted_source_content>",
    }


def _quality(url: str, source_type: str) -> float:
    host = (urlparse(url).hostname or "").casefold()
    if source_type in {"paper", "official_documentation"}:
        return .9
    if host.endswith((".edu", ".ac.uk")) or host in {"arxiv.org", "doi.org", "docs.python.org"}:
        return .88
    if "docs." in host or host.endswith(".gov"):
        return .84
    return .62


def _source_type(url: str) -> str:
    host = (urlparse(url).hostname or "").casefold()
    path = urlparse(url).path.casefold()
    if host in {"arxiv.org", "doi.org"} or "/paper" in path or "/abs/" in path:
        return "paper"
    if "docs." in host or host.endswith((".gov", ".edu")):
        return "official_documentation"
    return "public_web"


def _claims(text: str, source_id: str) -> list[dict[str, Any]]:
    sentences = re.split(r"(?<=[。！？.!?])\s+|\n+", text)
    result = []
    for sentence in sentences:
        value = sentence.strip()
        if 35 <= len(value) <= 500:
            result.append({"claim": value, "sourceId": source_id, "verification": "untrusted-web-evidence"})
        if len(result) >= 8:
            break
    return result


class WebResearchService:
    def __init__(
        self, vault: Path, store: Any,
        *, fetcher: Callable[[dict[str, Any]], dict[str, Any]] = fetch_user_url,
        searcher: Callable[[str, int], list[dict[str, str]]] | None = None,
    ) -> None:
        self.vault = vault.resolve(); self.store = store; self.fetcher = fetcher
        self.searcher = searcher or self._search_duckduckgo
        self.cache_root = self.vault / "90-Local-Only/Agent/WebCache"

    def _cache_path(self, cache_reference: str) -> Path:
        path = (self.vault / "90-Local-Only/Agent" / str(cache_reference)).resolve()
        if not path.is_relative_to(self.cache_root.resolve()) or not path.is_file() or path.is_symlink():
            raise ValueError("web_cache_reference_invalid")
        return path

    def cached_text(self, source: dict[str, Any]) -> str:
        payload = json.loads(self._cache_path(str(source.get("cacheReference") or "")).read_text(encoding="utf-8"))
        return str(payload.get("untrustedSourceContent") or "")

    def _atomic_cache(self, source_id: str, payload: dict[str, Any]) -> str:
        self.cache_root.mkdir(parents=True, exist_ok=True)
        target = self.cache_root / f"{source_id}.json"
        temp = self.cache_root / f".{source_id}.{uuid.uuid4().hex}.tmp"
        encoded = (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        with temp.open("wb") as handle:
            handle.write(encoded); handle.flush(); os.fsync(handle.fileno())
        os.replace(temp, target)
        return str(target.relative_to(self.vault / "90-Local-Only/Agent"))

    def fetch(self, url: str, *, resolver: Callable[[str], list[str]] | None = None, opener: Callable[..., Any] | None = None) -> dict[str, Any]:
        safe_url = validate_public_url(url, resolver)
        existing = next((item for item in self.store.list_web_sources(500) if item.get("canonicalUrl") == safe_url), None)
        if existing:
            try:
                self._cache_path(str(existing.get("cacheReference") or ""))
                return existing
            except (OSError, ValueError, json.JSONDecodeError):
                pass
        payload: dict[str, Any] = {"url": safe_url, "max_bytes": 1_500_000, "timeout": 12}
        if resolver:
            payload["resolver"] = resolver
        fetched = self.fetcher(payload, **({"opener": opener} if opener else {})) if self.fetcher is fetch_user_url else self.fetcher(payload)
        final_url = validate_public_url(str(fetched["url"]), resolver)
        document = extract_web_document(str(fetched["text"]), str(fetched["content_type"]), urlparse(final_url).path.rsplit("/", 1)[-1] or final_url)
        canonical = final_url
        if document["canonicalHint"]:
            try:
                canonical = validate_public_url(document["canonicalHint"], resolver)
            except ValueError:
                canonical = final_url
        digest = hashlib.sha256(document["text"].encode()).hexdigest()
        source_id = "web-" + hashlib.sha256(canonical.encode()).hexdigest()[:20]
        source_type = _source_type(canonical)
        now = _now()
        private_payload = {
            "schemaVersion": 1, "sourceId": source_id, "canonicalUrl": canonical,
            "fetchedAt": now, "contentHash": digest, **document,
        }
        cache_reference = self._atomic_cache(source_id, private_payload)
        record = {
            "id": source_id, "canonicalUrl": canonical, "title": document["title"],
            "author": document["author"], "publishedAt": document["publishedAt"],
            "fetchedAt": now, "domain": urlparse(canonical).hostname or "",
            "sourceType": source_type, "contentHash": digest, "qualityScore": _quality(canonical, source_type),
            "relevanceScore": 0.0, "supportedClaims": _claims(document["text"], source_id),
            "cacheReference": cache_reference, "promptInjectionDetected": document["promptInjectionDetected"],
            "promptInjectionMarkers": document["promptInjectionMarkers"], "bytes": int(fetched.get("bytes", 0)),
        }
        self.store.upsert_web_source(record)
        return record

    def _search_duckduckgo(self, query: str, limit: int) -> list[dict[str, str]]:
        search_url = "https://html.duckduckgo.com/html/?q=" + quote_plus(query)
        fetched = self.fetcher({"url": search_url, "max_bytes": 1_000_000, "timeout": 12})
        parser = _ReadableHTML(); parser.feed(str(fetched["text"]))
        results: list[dict[str, str]] = []
        for href, label in parser.links:
            parsed = urlparse(href)
            candidate = parse_qs(parsed.query).get("uddg", [href])[0]
            candidate = unquote(candidate)
            try:
                safe = validate_public_url(candidate)
            except (ValueError, OSError):
                continue
            if not any(item["url"] == safe for item in results):
                results.append({"url": safe, "title": label or safe})
            if len(results) >= limit:
                break
        return results

    def search(self, query: str, limit: int = 8) -> dict[str, Any]:
        query = " ".join(str(query).split())[:500]
        if not query:
            raise ValueError("web_search_query_required")
        rows = self.searcher(query, max(1, min(20, int(limit))))
        safe: list[dict[str, str]] = []
        for row in rows:
            try:
                url = validate_public_url(str(row.get("url") or ""))
            except (ValueError, OSError):
                continue
            safe.append({"url": url, "title": str(row.get("title") or url)[:300]})
        return {"query": query, "results": safe[:limit], "searchedAt": _now(), "provider": "public-web-search"}

    def research(self, query: str, urls: list[str] | None = None, limit: int = 5) -> dict[str, Any]:
        search = self.search(query, limit) if not urls else {"query": query, "results": [{"url": url, "title": url} for url in urls[:limit]], "searchedAt": _now(), "provider": "explicit-urls"}
        sources, failures = [], []
        for row in search["results"][:limit]:
            try:
                source = self.fetch(str(row["url"])); source["relevanceScore"] = .75
                sources.append(source)
            except Exception as error:
                failures.append({"url": str(row["url"]), "error": type(error).__name__})
        bundle_id = "research-web-" + hashlib.sha256((query + "|".join(item["canonicalUrl"] for item in sources)).encode()).hexdigest()[:16]
        bundle = {
            "id": bundle_id, "run_id": None, "title": f"网页研究：{query[:60]}", "question": query,
            "status": "proposed", "source_count": len(sources), "estimated_minutes": len(sources) * 8,
            "knowledge_gaps": ["网页内容仍需结合正式知识与原始论文验证"],
            "reading_order": [item["id"] for item in sources], "unverified_questions": [],
            "next_steps": ["检查来源质量", "核对关键论断", "确认后再整理到 Obsidian"],
            "failures": failures, "created_at": _now(), "sourceTrustBoundary": "untrusted_source_content",
        }
        rows = [{
            "id": item["id"], "source_type": item["sourceType"], "title": item["title"],
            "canonical_url": item["canonicalUrl"], "authors": item["author"],
            "published_at": item["publishedAt"] or None, "relevance": item["relevanceScore"],
            "quality": item["qualityScore"], "difficulty": "medium", "estimated_minutes": 8,
            "reason": "公开网页检索结果；内容按不可信来源处理。",
            "metadata": {"contentHash": item["contentHash"], "cacheReference": item["cacheReference"], "supportedClaims": item["supportedClaims"], "promptInjectionDetected": item["promptInjectionDetected"]},
        } for item in sources]
        self.store.save_research_bundle(bundle, rows)
        return {"bundle": {**bundle, "sources": rows}, "sources": sources, "failures": failures}
