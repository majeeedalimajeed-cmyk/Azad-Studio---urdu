"""Polite HTTP client with robots.txt checks, caching and source outage isolation."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import requests

from .config import CONFIG


@dataclass
class FetchResult:
    ok: bool
    url: str
    status_code: int | None
    text: str
    content: bytes
    headers: dict[str, str]
    message: str


class PoliteHTTPClient:
    def __init__(self, config=CONFIG):
        self.config = config
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": config.user_agent, "Accept": "*/*"})
        self._lock = threading.RLock()
        self._robots: dict[str, tuple[RobotFileParser | None, str]] = {}
        self._last_request: dict[str, float] = {}

    def _origin(self, url: str) -> str:
        p = urlsplit(url)
        return urlunsplit((p.scheme, p.netloc, "", "", ""))

    def _robots_for(self, url: str) -> tuple[RobotFileParser | None, str]:
        origin = self._origin(url)
        cached = self._robots.get(origin)
        if cached:
            return cached
        robots_url = origin + "/robots.txt"
        r = None
        last_exc = None
        for _attempt in range(1):
            try:
                r = self.session.get(robots_url, timeout=self.config.request_timeout)
                last_exc = None
                break
            except requests.RequestException as exc:
                last_exc = exc
                time.sleep(0.5 * (_attempt + 1))
        if r is None:
            # A fresh connection sometimes succeeds when a pooled one is reset.
            try:
                r = requests.get(robots_url, headers={"User-Agent": self.config.user_agent},
                                 timeout=self.config.request_timeout)
                last_exc = None
            except requests.RequestException as exc:
                last_exc = exc
        if r is None or last_exc is not None:
            return (None, f"robots.txt unavailable: {type(last_exc).__name__ if last_exc else 'Unknown'}")
        try:
            content_type = r.headers.get("content-type", "")
            if r.status_code == 403:
                return (None, "robots.txt HTTP 403; source marked unavailable")
            if r.status_code >= 400:
                # Absent robots.txt is permissive by convention.
                rp = RobotFileParser()
                rp.parse([])
                return (rp, f"robots.txt HTTP {r.status_code}; no directives")
            if "html" in content_type.lower() and "user-agent" not in r.text.lower()[:5000]:
                host = (urlsplit(url).netloc or "").lower()
                lower = r.text.lower()
                if host in self.config.html_robots_allow_hosts and 'name="robots"' in lower and "index,follow" in lower and "nofollow" not in lower:
                    rp = RobotFileParser()
                    rp.parse(["User-agent: *", "Allow: /"])
                    result = (rp, "HTML page meta robots=index,follow verified")
                else:
                    return (None, "robots.txt returned an HTML page; not machine-readable")
            else:
                rp = RobotFileParser()
                rp.parse(r.text.splitlines())
                result = (rp, "robots.txt parsed")
        except requests.RequestException as exc:
            return (None, f"robots.txt unavailable: {type(exc).__name__}")
        self._robots[origin] = result
        return result

    def can_fetch(self, url: str) -> tuple[bool, str]:
        rp, message = self._robots_for(url)
        if rp is None:
            # Do not silently bypass an unreadable policy. Caller records an outage.
            return False, message
        try:
            allowed = rp.can_fetch(self.config.user_agent, url)
            return allowed, "allowed" if allowed else "disallowed by robots.txt"
        except Exception as exc:
            return False, f"robots check failed: {type(exc).__name__}"

    def get(self, url: str, *, etag: str | None = None, last_modified: str | None = None,
            params: dict | None = None, allow_unavailable_robots: bool = False) -> FetchResult:
        origin = self._origin(url)
        with self._lock:
            wait = self.config.min_request_interval - (time.monotonic() - self._last_request.get(origin, 0))
            if wait > 0:
                time.sleep(wait)
            self._last_request[origin] = time.monotonic()
        allowed, policy_message = self.can_fetch(url)
        if not allowed:
            if not allow_unavailable_robots:
                return FetchResult(False, url, None, "", b"", {}, policy_message)
            # Only for a machine-readable 403 from a CDN where the operator has
            # separately documented permission to use the feed. Default is off.
            if "403" not in policy_message:
                return FetchResult(False, url, None, "", b"", {}, policy_message)
        headers: dict[str, str] = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        try:
            r = self.session.get(url, params=params, headers=headers, timeout=self.config.request_timeout)
            result = FetchResult(
                ok=200 <= r.status_code < 300,
                url=r.url,
                status_code=r.status_code,
                text=r.text,
                content=r.content,
                headers={k.lower(): v for k, v in r.headers.items()},
                message="HTTP 304 Not Modified" if r.status_code == 304 else f"HTTP {r.status_code}",
            )
            return result
        except requests.RequestException as exc:
            return FetchResult(False, url, None, "", b"", {}, f"network error: {type(exc).__name__}")
