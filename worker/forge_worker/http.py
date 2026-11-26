"""Polite HTTP: app-name-only User-Agent, retries with backoff, per-host rate limit."""

from __future__ import annotations

import threading
import time
from typing import Any
from urllib.parse import urlsplit

import requests
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from forge_worker.brand import user_agent

TIMEOUT = (10, 120)  # connect, read seconds
# The read timeout only limits the pause between two pieces of an answer, so a server that
# trickles data could hold a download open for ever. This limits the whole request.
TOTAL_TIMEOUT = 600.0
RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}


class HttpError(RuntimeError):
    def __init__(self, url: str, status: int | None, detail: str = "") -> None:
        self.url, self.status = url, status
        host = urlsplit(url).netloc
        what = f"HTTP {status}" if status else "no connection"
        super().__init__(f"{host}: {what}{' - ' + detail if detail else ''}")


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, HttpError):
        return exc.status is None or exc.status in RETRY_STATUS
    return False


class Http:
    """One shared session. `min_interval` maps host -> minimum seconds between requests."""

    def __init__(self, min_interval: dict[str, float] | None = None) -> None:
        self._session = requests.Session()
        self._session.headers["User-Agent"] = user_agent()
        self._min_interval = min_interval or {}
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def _throttle(self, url: str) -> None:
        host = urlsplit(url).netloc
        gap = self._min_interval.get(host, 0.0)
        if gap <= 0:
            return
        with self._lock:
            wait = self._last.get(host, 0.0) + gap - time.monotonic()
            self._last[host] = max(time.monotonic(), self._last.get(host, 0.0) + gap)
        if wait > 0:
            time.sleep(wait)

    @retry(
        retry=retry_if_exception(_retryable),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=20),
        reraise=True,
    )
    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        self._throttle(url)
        total = float(kwargs.pop("total_timeout", TOTAL_TIMEOUT))
        end = time.monotonic() + total
        caller_reads = bool(kwargs.pop("stream", False))  # big files: the caller reads the pieces itself
        try:
            resp = self._session.request(
                method, url, timeout=kwargs.pop("timeout", TIMEOUT), stream=True, **kwargs
            )
            if not caller_reads:
                body = bytearray()
                for chunk in resp.iter_content(65536):
                    body += chunk
                    if time.monotonic() > end:
                        resp.close()
                        raise HttpError(url, None, f"no complete answer within {total:.0f} s")
                resp._content = bytes(body)
        except requests.RequestException as exc:
            raise HttpError(url, None, type(exc).__name__) from exc
        if resp.status_code >= 400:
            raise HttpError(url, resp.status_code, resp.text[:200].strip())
        return resp

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        return self.request("GET", url, **kwargs)

    def get_json(self, url: str, **kwargs: Any) -> Any:
        return self.get(url, **kwargs).json()

    def post_json(self, url: str, **kwargs: Any) -> Any:
        return self.request("POST", url, **kwargs).json()
