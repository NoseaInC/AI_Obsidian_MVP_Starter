from __future__ import annotations

import ipaddress
import socket
from typing import Any, Callable
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen


ALLOWED_CONTENT_TYPES = {"text/html", "text/plain", "application/json", "application/xml", "text/xml"}


def validate_public_url(url: str, resolver: Callable[[str], list[str]] | None = None) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("invalid_public_url")
    host = parsed.hostname.rstrip(".").casefold()
    if host in {"localhost", "localhost.localdomain", "metadata.google.internal", "metadata.azure.internal"} or host.endswith((".local", ".internal")):
        raise ValueError("private_url_not_allowed")
    resolve = resolver or (lambda value: list({row[4][0] for row in socket.getaddrinfo(value, None)}))
    for raw in resolve(host):
        address = ipaddress.ip_address(raw)
        if not address.is_global:
            raise ValueError("private_url_not_allowed")
    return parsed.geturl()


class PublicRedirectHandler(HTTPRedirectHandler):
    def __init__(self, resolver: Callable[[str], list[str]] | None = None) -> None:
        super().__init__(); self.resolver = resolver

    def redirect_request(self, request: Any, fp: Any, code: int, message: str, headers: Any, new_url: str) -> Any:
        safe_url = validate_public_url(new_url, self.resolver)
        return super().redirect_request(request, fp, code, message, headers, safe_url)


def fetch_user_url(payload: dict[str, Any], *, opener: Callable[..., Any] = urlopen) -> dict[str, Any]:
    url = validate_public_url(str(payload.get("url", "")), payload.get("resolver"))
    max_bytes = max(1024, min(2_000_000, int(payload.get("max_bytes", 500_000))))
    request = Request(url, headers={"User-Agent": "Zhixu-Agent/1.0", "Accept": "text/html,text/plain,application/json"})
    resolver = payload.get("resolver")
    active_opener = build_opener(PublicRedirectHandler(resolver)).open if opener is urlopen else opener
    with active_opener(request, timeout=max(1, min(20, int(payload.get("timeout", 8))))) as response:
        final_url = validate_public_url(response.geturl(), payload.get("resolver"))
        content_type = str(response.headers.get_content_type()).lower()
        if content_type not in ALLOWED_CONTENT_TYPES:
            raise ValueError("unsupported_content_type")
        raw_length = response.headers.get("Content-Length")
        if raw_length and int(raw_length) > max_bytes:
            raise ValueError("response_too_large")
        body = response.read(max_bytes + 1)
        if len(body) > max_bytes:
            raise ValueError("response_too_large")
        charset = response.headers.get_content_charset() or "utf-8"
        return {"url": final_url, "content_type": content_type, "text": body.decode(charset, errors="replace"), "bytes": len(body)}
