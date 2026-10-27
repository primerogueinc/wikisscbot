"""Tiny stdlib HTTP helper with retries, maxlag handling and response hashing.

Every successful response body is passed to the active provenance recorder
(if one is set) before parsing, so each run can prove exactly which bytes
it received from Wikimedia and RIPEstat.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from .config import settings

# Set by provenance.Recorder.activate(); called as
# RECORDER(method, url, request_body_or_None, response_bytes).
RECORDER = None


class ApiError(RuntimeError):
    pass


def _request(method: str, url: str, body: bytes | None, retries: int, timeout: int) -> dict:
    headers = {"User-Agent": settings()["user_agent"], "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    delay = 5
    for attempt in range(retries):
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                wait = e.headers.get("Retry-After")
                time.sleep(int(wait) if wait and wait.isdigit() else delay)
                delay = min(delay * 2, 120)
                continue
            raise ApiError(f"HTTP {e.code} for {url}") from e
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < retries - 1:
                time.sleep(delay)
                delay = min(delay * 2, 120)
                continue
            raise ApiError(f"network error for {url}: {e}") from e

        if RECORDER is not None:
            RECORDER(method, url, body, raw)
        data = json.loads(raw.decode("utf-8"))
        # MediaWiki reports replication lag as an API error, not an HTTP status.
        err = data.get("error") if isinstance(data, dict) else None
        if err and err.get("code") == "maxlag" and attempt < retries - 1:
            time.sleep(delay)
            delay = min(delay * 2, 120)
            continue
        return data
    raise ApiError(f"gave up after {retries} attempts: {url}")


def get_json(url: str, params: dict | None = None, *, retries: int = 6,
             timeout: int = 60) -> dict:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    return _request("GET", url, None, retries, timeout)


def post_json(url: str, payload: dict, *, retries: int = 4, timeout: int = 60) -> dict:
    return _request("POST", url, json.dumps(payload).encode("utf-8"), retries, timeout)
