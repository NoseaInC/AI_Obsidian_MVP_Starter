from __future__ import annotations

import hashlib
import html
import json
import os
import re
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, quote_plus, unquote, urlencode, urlparse

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
    if "docs." in host or host.endswith((".gov", ".edu")) or "/docs/" in path:
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
        resolver: Callable[[str], list[str]] | None = None,
    ) -> None:
        self.vault = vault.resolve(); self.store = store; self.fetcher = fetcher
        self.searcher = searcher
        self.resolver = resolver
        self.cache_root = self.vault / "90-Local-Only/Agent/WebCache"

    def _cache_path(self, cache_reference: str) -> Path:
        path = (self.vault / "90-Local-Only/Agent" / str(cache_reference)).resolve()
        if not path.is_relative_to(self.cache_root.resolve()) or not path.is_file() or path.is_symlink():
            raise ValueError("web_cache_reference_invalid")
        return path

    def cached_text(self, source: dict[str, Any]) -> str:
        payload = json.loads(self._cache_path(str(source.get("cacheReference") or "")).read_text(encoding="utf-8"))
        return str(payload.get("untrustedSourceContent") or "")

    def fetch_for_model(self, url: str, max_chars: int = 12_000) -> dict[str, Any]:
        """Fetch a public page and attach bounded, explicitly untrusted evidence.

        The full page remains in the local WebCache. SQLite and public tool
        events only retain metadata; the model receives this bounded evidence
        for the current turn so a successful fetch is actually useful.
        """
        source = self.fetch(url)
        evidence = self.cached_text(source)
        return {
            **source,
            "evidenceText": evidence[:min(max(int(max_chars), 1_000), 20_000)],
            "evidenceTruncated": len(evidence) > min(max(int(max_chars), 1_000), 20_000),
        }

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
        effective_resolver = resolver or self.resolver
        safe_url = validate_public_url(url, effective_resolver)
        existing = next((item for item in self.store.list_web_sources(500) if item.get("canonicalUrl") == safe_url), None)
        if existing:
            try:
                self._cache_path(str(existing.get("cacheReference") or ""))
                return existing
            except (OSError, ValueError, json.JSONDecodeError):
                pass
        payload: dict[str, Any] = {"url": safe_url, "max_bytes": 1_500_000, "timeout": 12}
        if effective_resolver:
            payload["resolver"] = effective_resolver
        fetched = self.fetcher(payload, **({"opener": opener} if opener else {})) if self.fetcher is fetch_user_url else self.fetcher(payload)
        final_url = validate_public_url(str(fetched["url"]), effective_resolver)
        document = extract_web_document(str(fetched["text"]), str(fetched["content_type"]), urlparse(final_url).path.rsplit("/", 1)[-1] or final_url)
        canonical = final_url
        if document["canonicalHint"]:
            try:
                canonical = validate_public_url(document["canonicalHint"], effective_resolver)
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
                safe = validate_public_url(candidate, self.resolver)
            except (ValueError, OSError):
                continue
            if not any(item["url"] == safe for item in results):
                results.append({"url": safe, "title": label or safe})
            if len(results) >= limit:
                break
        return results

    def _search_bing_rss(self, query: str, limit: int) -> list[dict[str, str]]:
        search_url = "https://www.bing.com/search?" + urlencode({"q": query, "format": "rss"})
        fetched = self.fetcher({"url": search_url, "max_bytes": 1_000_000, "timeout": 10})
        root = ET.fromstring(str(fetched["text"]))
        results: list[dict[str, str]] = []
        for item in root.findall(".//item"):
            url = (item.findtext("link") or "").strip()
            if not url:
                continue
            results.append({
                "url": url,
                "title": (item.findtext("title") or url).strip(),
                "snippet": (item.findtext("description") or "").strip(),
                "publishedAt": (item.findtext("pubDate") or "").strip(),
                "provider": "bing-rss",
            })
            if len(results) >= limit:
                break
        return results

    def _search_github(self, query: str, limit: int) -> list[dict[str, Any]]:
        terms = self._relevance_terms(query)
        payload: dict[str, Any] = {}
        for candidate in [query, " ".join(terms[:2])]:
            if not candidate:
                continue
            search_url = "https://api.github.com/search/repositories?" + urlencode({
                "q": candidate, "per_page": min(limit, 10), "sort": "stars",
            })
            fetched = self.fetcher({"url": search_url, "max_bytes": 1_500_000, "timeout": 10})
            payload = json.loads(str(fetched["text"]))
            if payload.get("items"):
                break
        results: list[dict[str, Any]] = []
        for item in (payload.get("items") or [])[:limit]:
            if not isinstance(item, dict):
                continue
            results.append({
                "url": str(item.get("html_url") or ""),
                "title": str(item.get("full_name") or item.get("name") or "GitHub repository"),
                "snippet": str(item.get("description") or ""),
                "publishedAt": str(item.get("updated_at") or ""),
                "provider": "github",
                "sourceType": "public_web",
                "qualityScore": min(.86, .66 + min(int(item.get("stargazers_count") or 0), 20_000) / 100_000),
            })
        return results

    def _search_hacker_news(self, query: str, limit: int) -> list[dict[str, Any]]:
        terms = self._relevance_terms(query)
        payload: dict[str, Any] = {}
        for candidate in [query, terms[0] if terms else ""]:
            if not candidate:
                continue
            search_url = "https://hn.algolia.com/api/v1/search?" + urlencode({
                "query": candidate, "hitsPerPage": min(limit, 10), "tags": "story",
            })
            fetched = self.fetcher({"url": search_url, "max_bytes": 1_500_000, "timeout": 10})
            payload = json.loads(str(fetched["text"]))
            if payload.get("hits"):
                break
        results: list[dict[str, Any]] = []
        for item in (payload.get("hits") or [])[:limit]:
            if not isinstance(item, dict):
                continue
            story_id = str(item.get("objectID") or "")
            url = str(item.get("url") or (f"https://news.ycombinator.com/item?id={story_id}" if story_id else ""))
            results.append({
                "url": url,
                "title": str(item.get("title") or item.get("story_title") or "Hacker News result"),
                "snippet": str(item.get("story_text") or ""),
                "publishedAt": str(item.get("created_at") or ""),
                "provider": "hacker-news",
                "sourceType": "public_web",
                "qualityScore": min(.84, .66 + min(int(item.get("points") or 0), 1_000) / 5_000),
            })
        return results

    @staticmethod
    def _relevance_terms(query: str) -> list[str]:
        folded = query.casefold()
        terms = re.findall(r"[a-z0-9][a-z0-9_.+\-]{1,}", folded)
        for block in re.findall(r"[\u3400-\u9fff]{2,}", folded):
            terms.append(block)
            for size in (2, 3, 4):
                terms.extend(block[index:index + size] for index in range(max(0, len(block) - size + 1)))
        return list(dict.fromkeys(term for term in terms if len(term) >= 2))[:30]

    @classmethod
    def _rank_search_rows(cls, query: str, rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
        terms = cls._relevance_terms(query)
        generic_terms = {
            "about", "docs", "documentation", "guide", "latest", "official",
            "search", "source", "sources", "tool", "tools", "web", "website",
        }
        distinctive_terms = [term for term in terms if len(term) >= 5 and term not in generic_terms]
        scored: list[tuple[float, int, dict[str, Any]]] = []
        seen: set[str] = set()
        for index, row in enumerate(rows):
            url = str(row.get("url") or "")
            if not url or url.casefold() in seen:
                continue
            seen.add(url.casefold())
            title = str(row.get("title") or "").casefold()
            snippet = str(row.get("snippet") or "").casefold()
            overlap = sum(3.0 for term in terms if term in title) + sum(1.0 for term in terms if term in snippet)
            if terms and overlap <= 0:
                continue
            if distinctive_terms and not any(term in title or term in snippet for term in distinctive_terms):
                continue
            score = overlap + float(row.get("qualityScore") or 0)
            scored.append((score, -index, row))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [item[2] for item in scored[:limit]]

    def _default_search(self, query: str, limit: int) -> tuple[list[dict[str, Any]], str, list[str]]:
        failures: list[str] = []
        combined: list[dict[str, Any]] = []
        for provider, searcher in (
            ("bing-rss", self._search_bing_rss),
            ("github", self._search_github),
            ("hacker-news", self._search_hacker_news),
        ):
            try:
                rows = searcher(query, limit)
                if rows:
                    combined.extend(rows)
                else:
                    failures.append(f"{provider}:empty")
            except Exception as error:
                failures.append(f"{provider}:{type(error).__name__}")
        ranked = self._rank_search_rows(query, combined, limit)
        # DuckDuckGo is intentionally a fallback. Its HTML endpoint can be
        # slow or unavailable in some regions, and should not delay a useful
        # answer that the primary federation has already produced.
        if len(ranked) < min(3, limit):
            try:
                rows = self._search_duckduckgo(query, limit)
                if rows:
                    combined.extend(rows)
                    ranked = self._rank_search_rows(query, combined, limit)
                else:
                    failures.append("duckduckgo-html:empty")
            except Exception as error:
                failures.append(f"duckduckgo-html:{type(error).__name__}")
        provider = str(ranked[0].get("provider") or "web-federation") if ranked else "unavailable"
        return ranked, provider, failures

    @staticmethod
    def _parse_arxiv(raw: str, limit: int) -> list[dict[str, Any]]:
        root = ET.fromstring(raw)
        namespace = {"atom": "http://www.w3.org/2005/Atom"}
        results: list[dict[str, Any]] = []
        for entry in root.findall("atom:entry", namespace):
            url = (entry.findtext("atom:id", default="", namespaces=namespace) or "").strip()
            authors = [
                str(node.findtext("atom:name", default="", namespaces=namespace) or "").strip()
                for node in entry.findall("atom:author", namespace)
            ]
            results.append({
                "url": url,
                "title": " ".join((entry.findtext("atom:title", default="", namespaces=namespace) or "").split()),
                "snippet": " ".join((entry.findtext("atom:summary", default="", namespaces=namespace) or "").split())[:800],
                "publishedAt": (entry.findtext("atom:published", default="", namespaces=namespace) or "").strip(),
                "authors": [item for item in authors if item],
                "provider": "arxiv",
                "sourceType": "paper",
                "qualityScore": .9,
            })
            if len(results) >= limit:
                break
        return results

    @staticmethod
    def _parse_crossref(raw: str, limit: int) -> list[dict[str, Any]]:
        payload = json.loads(raw)
        items = ((payload.get("message") or {}).get("items") or []) if isinstance(payload, dict) else []
        results: list[dict[str, Any]] = []
        for item in items[:limit]:
            if not isinstance(item, dict):
                continue
            title_value = item.get("title") or []
            title = str(title_value[0] if isinstance(title_value, list) and title_value else title_value or "")
            doi = str(item.get("DOI") or "").strip()
            url = str(item.get("URL") or (f"https://doi.org/{doi}" if doi else "")).strip()
            authors = []
            for author in item.get("author") or []:
                if isinstance(author, dict):
                    name = " ".join(filter(None, [str(author.get("given") or ""), str(author.get("family") or "")])).strip()
                    if name:
                        authors.append(name)
            date_parts = (((item.get("published-print") or item.get("published-online") or {}).get("date-parts")) or [])
            published = "-".join(str(value) for value in (date_parts[0] if date_parts else []))
            results.append({
                "url": url,
                "title": title or url,
                "snippet": str(item.get("abstract") or "")[:800],
                "publishedAt": published,
                "authors": authors,
                "provider": "crossref",
                "sourceType": "paper",
                "qualityScore": .88,
                "doi": doi,
            })
        return results

    def search(self, query: str, limit: int = 8) -> dict[str, Any]:
        query = " ".join(str(query).split())[:500]
        if not query:
            raise ValueError("web_search_query_required")
        bounded_limit = max(1, min(20, int(limit)))
        if self.searcher:
            rows = self.searcher(query, bounded_limit)
            provider, failures = "injected-search", []
        else:
            rows, provider, failures = self._default_search(query, bounded_limit)
        safe: list[dict[str, Any]] = []
        for row in rows:
            try:
                url = validate_public_url(str(row.get("url") or ""), self.resolver)
            except (ValueError, OSError):
                continue
            source_type = str(row.get("sourceType") or _source_type(url))
            safe.append({
                "url": url,
                "title": str(row.get("title") or url)[:300],
                "snippet": str(row.get("snippet") or "")[:800],
                "publishedAt": str(row.get("publishedAt") or "")[:80],
                "provider": str(row.get("provider") or provider)[:80],
                "sourceType": source_type,
                "qualityScore": float(row.get("qualityScore") or _quality(url, source_type)),
                "domain": urlparse(url).hostname or "",
            })
        return {
            "query": query,
            "results": safe[:bounded_limit],
            "searchedAt": _now(),
            "provider": provider,
            "providerFailures": failures,
        }

    def search_academic(self, query: str, limit: int = 8) -> dict[str, Any]:
        query = " ".join(str(query).split())[:500]
        if not query:
            raise ValueError("academic_search_query_required")
        bounded_limit = max(1, min(20, int(limit)))
        providers: list[tuple[str, str, Callable[[str, int], list[dict[str, Any]]]]] = [
            (
                "arxiv",
                "https://export.arxiv.org/api/query?" + urlencode({
                    "search_query": f"all:{query}", "start": 0, "max_results": bounded_limit,
                }),
                self._parse_arxiv,
            ),
            (
                "crossref",
                "https://api.crossref.org/works?" + urlencode({
                    "query.bibliographic": query, "rows": bounded_limit,
                }),
                self._parse_crossref,
            ),
        ]
        combined: list[dict[str, Any]] = []
        failures: list[str] = []
        seen: set[str] = set()
        for provider, url, parser in providers:
            try:
                fetched = self.fetcher({"url": url, "max_bytes": 1_500_000, "timeout": 12})
                for row in parser(str(fetched["text"]), bounded_limit):
                    try:
                        safe_url = validate_public_url(str(row.get("url") or ""), self.resolver)
                    except (ValueError, OSError):
                        continue
                    key = safe_url.casefold()
                    if key in seen:
                        continue
                    seen.add(key)
                    combined.append({**row, "url": safe_url, "domain": urlparse(safe_url).hostname or ""})
            except Exception as error:
                failures.append(f"{provider}:{type(error).__name__}")
        combined.sort(key=lambda item: (float(item.get("qualityScore") or 0), str(item.get("publishedAt") or "")), reverse=True)
        return {
            "query": query,
            "results": combined[:bounded_limit],
            "searchedAt": _now(),
            "provider": "academic-federation",
            "providerFailures": failures,
        }

    def capabilities(self) -> dict[str, Any]:
        return {
            "available": True,
            "defaultProvider": "web-federation",
            "fallbackProviders": ["duckduckgo-html"],
            "searchProviders": ["bing-rss", "github", "hacker-news", "duckduckgo-html"],
            "academicProviders": ["arxiv", "crossref"],
            "maxResults": 20,
            "maxEvidenceChars": 20_000,
            "trustBoundary": "untrusted_source_content",
            "requiresPerTurnAuthorization": True,
        }

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
