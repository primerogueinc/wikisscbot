"""Tiny stdlib HTTP helper with retries and Wikimedia maxlag handling."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from .config import settings


class ApiError(RuntimeError):
    pass


def get_json(url: str, params: dict | None = None, *, retries: int = 6,
             timeout: int = 60) -> dict:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    headers = {"User-Agent": settings()["user_agent"], "Accept": "application/json"}
    delay = 5
    for attempt in range(retries):
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                wait = int(e.headers.get("Retry-After") or delay)
                time.sleep(wait)
                delay = min(delay * 2, 120)
                continue
            raise ApiError(f"HTTP {e.code} for {url}") from e
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < retries - 1:
                time.sleep(delay)
                delay = min(delay * 2, 120)
                continue
            raise ApiError(f"network error for {url}: {e}") from e

        # MediaWiki reports replication lag as an API error, not an HTTP status.
        err = data.get("error") if isinstance(data, dict) else None
        if err and err.get("code") == "maxlag" and attempt < retries - 1:
            time.sleep(delay)
            delay = min(delay * 2, 120)
            continue
        return data
    raise ApiError(f"gave up after {retries} attempts: {url}")
