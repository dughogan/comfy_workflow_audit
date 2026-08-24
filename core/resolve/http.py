"""Shared HTTP plumbing for the provenance sources.

Three things every source needs and none should implement twice: an on-disk
cache (audits get re-run constantly while a workflow is being fixed, and these
APIs are rate limited), credentials read from the environment, and failure that
degrades instead of raising - a source that cannot be reached must never take
the audit down with it.

Rate limits are tracked per host rather than guessed at, because the practical
difference between "GitHub says you have 4 requests left" and "GitHub says you
are blocked for 40 minutes" is what the report should tell the reader to do.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

DEFAULT_TTL = 7 * 24 * 3600      # a week; licences do not change hourly
USER_AGENT = "comfyaudit/0.3 (+https://github.com/dughogan/dughogan)"


def cache_dir() -> str:
    base = os.environ.get("COMFYAUDIT_CACHE") or os.path.join(
        os.path.expanduser("~"), ".cache", "comfyaudit"
    )
    os.makedirs(base, exist_ok=True)
    return base


@dataclass
class Fetched:
    """One HTTP result, successful or not."""

    ok: bool = False
    status: int = 0
    data: Any = None
    error: str = ""
    from_cache: bool = False

    def __bool__(self) -> bool:
        return self.ok


@dataclass
class RateLimit:
    """What a host last told us about our budget."""

    limit: int | None = None
    remaining: int | None = None
    reset_at: int | None = None
    blocked: bool = False

    def describe(self, host: str) -> str:
        if self.blocked:
            when = ""
            if self.reset_at:
                minutes = max(0, int((self.reset_at - time.time()) / 60))
                when = f", resets in about {minutes} minute(s)"
            return f"{host} rate limit reached{when}"
        if self.remaining is not None and self.limit:
            return f"{host}: {self.remaining}/{self.limit} requests remaining"
        return ""


class HttpClient:
    """Cached JSON GET with graceful failure."""

    def __init__(self, ttl: int = DEFAULT_TTL, timeout: float = 12.0,
                 offline: bool = False) -> None:
        self.ttl = ttl
        self.timeout = timeout
        self.offline = offline
        self.hits = 0
        self.requests = 0
        self.errors: list[str] = []
        self.rate_limits: dict[str, RateLimit] = {}

    # -- public ------------------------------------------------------------

    def get_json(self, url: str, headers: dict[str, str] | None = None) -> Fetched:
        cached = self._read(self._path(url))
        if cached is not None:
            self.hits += 1
            return Fetched(ok=True, status=200, data=cached, from_cache=True)
        if self.offline:
            return Fetched(error="offline")

        host = urllib.parse.urlparse(url).netloc
        limit = self.rate_limits.get(host)
        if limit and limit.blocked and limit.reset_at and time.time() < limit.reset_at:
            # Asking again before the window resets just burns time.
            return Fetched(status=429, error=limit.describe(host))

        return self._request(url, headers or {}, host, parse_json=True)

    def get_text(self, url: str, headers: dict[str, str] | None = None) -> Fetched:
        """Fetch a plain-text resource, e.g. a LICENSE file from raw.githubusercontent."""
        cached = self._read(self._path("text:" + url))
        if cached is not None:
            self.hits += 1
            return Fetched(ok=True, status=200, data=cached, from_cache=True)
        if self.offline:
            return Fetched(error="offline")
        host = urllib.parse.urlparse(url).netloc
        return self._request(url, headers or {}, host, parse_json=False)

    def rate_limit_notes(self) -> list[str]:
        out = []
        for host, limit in self.rate_limits.items():
            text = limit.describe(host)
            if text and (limit.blocked or (limit.remaining is not None and limit.remaining < 10)):
                out.append(text)
        return out

    # -- internals ---------------------------------------------------------

    def _request(self, url: str, headers: dict[str, str], host: str,
                 parse_json: bool) -> Fetched:
        request = urllib.request.Request(
            url, headers={"User-Agent": USER_AGENT, **headers})
        self.requests += 1
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                self._note_limits(host, response.headers)
                raw = response.read().decode("utf-8", "replace")
                payload = json.loads(raw) if parse_json else raw
        except urllib.error.HTTPError as exc:
            self._note_limits(host, getattr(exc, "headers", None))
            return self._http_error(url, host, exc, parse_json)
        except json.JSONDecodeError as exc:
            self.errors.append(f"{url} -> malformed JSON: {exc}")
            return Fetched(status=200, error="malformed JSON")
        except Exception as exc:  # noqa: BLE001 - DNS, TLS, timeouts, proxies
            message = f"{type(exc).__name__}: {exc}"
            self.errors.append(f"{url} -> {message}")
            return Fetched(error=message)

        self._write(self._path(("text:" if not parse_json else "") + url), payload)
        return Fetched(ok=True, status=200, data=payload)

    def _http_error(self, url: str, host: str, exc: urllib.error.HTTPError,
                    parse_json: bool) -> Fetched:
        status = exc.code
        message = {
            401: "not authorised - check the API token",
            403: "forbidden (rate limited, or the token lacks access)",
            404: "not found",
            429: "rate limited",
        }.get(status, f"HTTP {status}")

        if status in (403, 429):
            limit = self.rate_limits.setdefault(host, RateLimit())
            # GitHub distinguishes "no budget left" from a genuine permission
            # error using the same status code; the header is the only way to
            # tell, and telling the user which one it was actually matters.
            if limit.remaining == 0 or status == 429:
                limit.blocked = True
                message = limit.describe(host) or message
        elif status in (401, 404):
            # A definitive no is worth caching so we stop asking every run.
            self._write(self._path(("text:" if not parse_json else "") + url),
                        {"__error__": status})

        self.errors.append(f"{url} -> {message}")
        return Fetched(status=status, error=message)

    def _note_limits(self, host: str, headers: Any) -> None:
        if not headers:
            return
        limit = self.rate_limits.setdefault(host, RateLimit())
        for name, attr in (("X-RateLimit-Limit", "limit"),
                           ("X-RateLimit-Remaining", "remaining"),
                           ("X-RateLimit-Reset", "reset_at")):
            raw = headers.get(name)
            if raw is None:
                continue
            try:
                setattr(limit, attr, int(raw))
            except (TypeError, ValueError):
                continue
        if limit.remaining is not None and limit.remaining > 0:
            limit.blocked = False

    def _path(self, key: str) -> str:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
        return os.path.join(cache_dir(), f"{digest}.json")

    def _read(self, path: str) -> Any | None:
        try:
            stat = os.stat(path)
        except OSError:
            return None
        if not self.offline and time.time() - stat.st_mtime > self.ttl:
            return None
        try:
            with open(path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, json.JSONDecodeError):
            return None
        if isinstance(payload, dict) and "__error__" in payload:
            return None
        return payload

    def _write(self, path: str, payload: Any) -> None:
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
        except OSError:
            pass


# --------------------------------------------------------------------------
# Credentials
# --------------------------------------------------------------------------


@dataclass
class Credentials:
    """Tokens for the provenance sources, from arguments or the environment.

    None of these are required. Each one buys something specific:
    HuggingFace for gated repositories and a higher rate limit, GitHub to lift
    the 60-requests-an-hour unauthenticated cap, Civitai for early-access models.
    """

    huggingface: str = ""
    civitai: str = ""
    github: str = ""

    @classmethod
    def from_environment(cls, **overrides: str) -> "Credentials":
        creds = cls(
            huggingface=os.environ.get("HF_TOKEN")
            or os.environ.get("HUGGING_FACE_HUB_TOKEN", ""),
            civitai=os.environ.get("CIVITAI_API_KEY", ""),
            github=os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN", ""),
        )
        for name, value in overrides.items():
            if value and hasattr(creds, name):
                setattr(creds, name, value)
        return creds

    def describe(self) -> dict[str, bool]:
        return {"huggingface": bool(self.huggingface),
                "civitai": bool(self.civitai),
                "github": bool(self.github)}
