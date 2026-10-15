"""Shared HTTP plumbing for every fetcher.

- one polite `httpx` client with a project User-Agent
- per-host minimum interval between requests (GDELT asks for <= 1 req / 5 s)
- retries with exponential backoff on network errors, 429 and 5xx
- a small on-disk response cache (default 1 h) so re-runs don't re-hit APIs
- a raw copy of every response under data/raw/<source>/ for auditability
- `FetchRun`: records fetched / inserted / rejected counts in `fetch_log`
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections import Counter
from datetime import datetime
from urllib.parse import urlencode, urlparse

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from app.config import get_settings
from app.db import session_scope
from app.models import FetchLog

log = logging.getLogger("samvedna.fetch")
settings = get_settings()

# Seconds to wait between two requests to the same host.
HOST_MIN_INTERVAL = {"api.gdeltproject.org": settings.gdelt_min_interval_s}
DEFAULT_MIN_INTERVAL = 1.0

_last_call: dict[str, float] = {}
_lock = threading.Lock()


class FetchError(RuntimeError):
    pass


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return isinstance(exc, (httpx.TransportError, httpx.TimeoutException))


def _throttle(host: str) -> None:
    gap = HOST_MIN_INTERVAL.get(host, DEFAULT_MIN_INTERVAL)
    with _lock:
        wait = _last_call.get(host, 0) + gap - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_call[host] = time.monotonic()


_client = httpx.Client(
    timeout=httpx.Timeout(60.0, connect=15.0),
    headers={"User-Agent": settings.user_agent},
    follow_redirects=True,
)


@retry(
    retry=retry_if_exception(_retryable),
    wait=wait_exponential(multiplier=10, max=120),
    stop=stop_after_attempt(6),
    reraise=True,
)
def _get(url: str) -> httpx.Response:
    _throttle(urlparse(url).netloc)
    resp = _client.get(url)
    if resp.status_code == 429:
        # Adaptive politeness: each 429 doubles this host's minimum gap (max 60 s).
        host = urlparse(url).netloc
        with _lock:
            HOST_MIN_INTERVAL[host] = min(HOST_MIN_INTERVAL.get(host, DEFAULT_MIN_INTERVAL) * 2, 60.0)
        log.warning("429 from %s; min interval now %.0fs", host, HOST_MIN_INTERVAL[host])
    resp.raise_for_status()
    return resp


def build_url(url: str, params: dict | None) -> str:
    return f"{url}?{urlencode(params)}" if params else url


def _cache_key(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()


def fetch_text(
    source: str,
    url: str,
    params: dict | None = None,
    *,
    ttl: int | None = None,
    redact: str | None = None,
) -> str:
    """GET `url` (with cache) and return the body; also archive a raw copy.

    `redact` is a secret (e.g. the FIRMS key) to strip from logged/archived names.
    """
    url = build_url(url, params)
    ttl = settings.cache_ttl_s if ttl is None else ttl
    key = _cache_key(url)
    cache_file = settings.cache_dir / f"{key}.txt"
    if ttl > 0 and cache_file.exists() and time.time() - cache_file.stat().st_mtime < ttl:
        return cache_file.read_text()

    shown = url.replace(redact, "***") if redact else url
    log.info("GET %s", shown)
    body = _get(url).text

    settings.cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(body)
    raw_dir = settings.raw_dir / source / datetime.now().strftime("%Y-%m-%d")
    raw_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%H%M%S")
    (raw_dir / f"{stamp}_{key[:10]}.txt").write_text(body)
    with (raw_dir / "index.jsonl").open("a") as f:
        f.write(json.dumps({"file": f"{stamp}_{key[:10]}.txt", "url": shown}) + "\n")
    return body


def fetch_json(source: str, url: str, params: dict | None = None, **kw) -> dict:
    body = fetch_text(source, url, params, **kw)
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        # GDELT in particular returns plain-text errors with HTTP 200.
        # Drop the cached copy so the next run retries instead of re-reading junk.
        cached = settings.cache_dir / f"{_cache_key(build_url(url, params))}.txt"
        cached.unlink(missing_ok=True)
        raise FetchError(f"{source}: non-JSON response: {body[:200]!r}") from exc


class FetchRun:
    """Context manager that writes one `fetch_log` row per fetch run."""

    def __init__(self, source: str):
        self.source = source
        self.fetched = 0
        self.inserted = 0
        self.rejected: Counter[str] = Counter()

    def reject(self, reasons: Counter[str] | dict[str, int]) -> None:
        self.rejected.update(reasons)

    def __enter__(self) -> FetchRun:
        self.started = datetime.now()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        status = "ok" if exc is None else "error"
        with session_scope() as s:
            s.add(
                FetchLog(
                    source=self.source,
                    started_at=self.started,
                    finished_at=datetime.now(),
                    fetched=self.fetched,
                    inserted=self.inserted,
                    rejected=sum(self.rejected.values()),
                    rejected_reasons=json.dumps(dict(self.rejected)),
                    status=status,
                    error=None if exc is None else f"{exc_type.__name__}: {exc}"[:2000],
                )
            )
        log.info(
            "%s: fetched=%d inserted=%d rejected=%d %s status=%s",
            self.source, self.fetched, self.inserted, sum(self.rejected.values()),
            dict(self.rejected), status,
        )
        return False  # never swallow exceptions
