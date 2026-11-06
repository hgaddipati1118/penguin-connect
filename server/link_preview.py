"""Link previews (title, description, image, site name) for URLs in messages.

Fetches public http(s) pages only: hosts that resolve to private, loopback, or link-local
addresses are refused so a message link can't make the server probe the local network.
Results are cached in memory, including failures, so each URL is fetched at most once
per cache window.
"""

from __future__ import annotations

import html
import ipaddress
import re
import socket
import threading
import time
import urllib.parse
from typing import Any

import httpx

MAX_BYTES = 512 * 1024
TIMEOUT_SECONDS = 6.0
CACHE_SECONDS = 6 * 3600
CACHE_LIMIT = 2000
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) PenguinLinkPreview/1.0"

_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_cache_lock = threading.Lock()

_META_PATTERN = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
_ATTR_PATTERN = re.compile(r'([a-zA-Z_:-]+)\s*=\s*("([^"]*)"|\'([^\']*)\'|([^\s>]+))')
_TITLE_PATTERN = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


class PreviewRefused(ValueError):
    pass


def normalize_url(raw: str) -> str:
    url = str(raw or "").strip()
    if url.lower().startswith("www."):
        url = f"https://{url}"
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise PreviewRefused("unsupported_url")
    return url


def _assert_public_host(hostname: str, port: int | None) -> None:
    try:
        infos = socket.getaddrinfo(hostname, port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise PreviewRefused("unresolvable_host") from exc
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
        ):
            raise PreviewRefused("private_host")


def parse_preview(markup: str, base_url: str) -> dict[str, Any]:
    meta: dict[str, str] = {}
    for tag in _META_PATTERN.findall(markup):
        attrs = {}
        for match in _ATTR_PATTERN.finditer(tag):
            value = match.group(3) if match.group(3) is not None else (
                match.group(4) if match.group(4) is not None else match.group(5)
            )
            attrs[match.group(1).lower()] = html.unescape(value or "").strip()
        key = (attrs.get("property") or attrs.get("name") or "").lower()
        content = attrs.get("content", "")
        if key and content and key not in meta:
            meta[key] = content

    title = meta.get("og:title") or meta.get("twitter:title")
    if not title:
        found = _TITLE_PATTERN.search(markup)
        title = html.unescape(" ".join(found.group(1).split())) if found else ""
    description = meta.get("og:description") or meta.get("twitter:description") or meta.get("description") or ""
    image = meta.get("og:image") or meta.get("og:image:url") or meta.get("twitter:image") or ""
    if image:
        image = urllib.parse.urljoin(base_url, image)
        if urllib.parse.urlsplit(image).scheme not in {"http", "https"}:
            image = ""
    site = meta.get("og:site_name") or (urllib.parse.urlsplit(base_url).hostname or "").removeprefix("www.")
    return {
        "url": base_url,
        "title": title[:200],
        "description": " ".join(description.split())[:300],
        "image": image,
        "site_name": site[:80],
    }


def _fetch(url: str) -> dict[str, Any]:
    current = url
    with httpx.Client(timeout=TIMEOUT_SECONDS, follow_redirects=False, headers={"User-Agent": USER_AGENT}) as client:
        for _ in range(5):
            parsed = urllib.parse.urlsplit(current)
            _assert_public_host(parsed.hostname or "", parsed.port)
            with client.stream("GET", current) as response:
                if response.is_redirect:
                    location = response.headers.get("location", "")
                    current = normalize_url(urllib.parse.urljoin(current, location))
                    continue
                if response.status_code >= 400:
                    return {"url": url, "available": False, "reason": f"http_{response.status_code}"}
                content_type = response.headers.get("content-type", "").lower()
                if content_type.startswith("image/"):
                    host = (parsed.hostname or "").removeprefix("www.")
                    return {"url": url, "available": True, "title": "", "description": "", "image": current, "site_name": host}
                if "html" not in content_type:
                    return {"url": url, "available": False, "reason": "not_html"}
                body = b""
                for chunk in response.iter_bytes():
                    body += chunk
                    if len(body) >= MAX_BYTES:
                        break
                markup = body.decode(response.encoding or "utf-8", errors="replace")
                preview = parse_preview(markup, current)
                preview["url"] = url
                preview["available"] = bool(preview["title"] or preview["image"])
                return preview
    return {"url": url, "available": False, "reason": "too_many_redirects"}


def get_link_preview(raw_url: str) -> dict[str, Any]:
    try:
        url = normalize_url(raw_url)
    except PreviewRefused as exc:
        return {"url": raw_url, "available": False, "reason": str(exc)}
    now = time.time()
    with _cache_lock:
        cached = _cache.get(url)
        if cached and now - cached[0] < CACHE_SECONDS:
            return cached[1]
    try:
        preview = _fetch(url)
    except PreviewRefused as exc:
        preview = {"url": url, "available": False, "reason": str(exc)}
    except (httpx.HTTPError, UnicodeError, ValueError) as exc:
        preview = {"url": url, "available": False, "reason": exc.__class__.__name__}
    with _cache_lock:
        if len(_cache) >= CACHE_LIMIT:
            for key in sorted(_cache, key=lambda item: _cache[item][0])[: CACHE_LIMIT // 4]:
                _cache.pop(key, None)
        _cache[url] = (now, preview)
    return preview
