"""Optional posting of new edits to Mastodon and/or Bluesky.

Configure with environment variables (GitHub Actions secrets):
  MASTODON_INSTANCE  e.g. https://mastodon.social
  MASTODON_TOKEN     access token with write:statuses
  BLUESKY_HANDLE     e.g. wikisscbot.bsky.social
  BLUESKY_APP_PASSWORD
Nothing is posted unless the variables are set.
"""

from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime, timezone

from .config import settings


def format_status(e: dict) -> str:
    lang = next((w["lang"] for w in settings()["wikis"] if w["id"] == e["wiki"]), "en")
    title = e["title"] if len(e["title"]) <= 120 else e["title"][:119] + "…"
    if lang == "fr":
        return f"« {title} » modifié anonymement depuis {e['label']} {e['url']}"
    return f"{title} Wikipedia article edited anonymously from {e['label']} {e['url']}"


def _post_json(url: str, body: dict, headers: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": settings()["user_agent"], **headers})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode() or "{}")


def post_mastodon(text: str) -> None:
    inst, tok = os.environ.get("MASTODON_INSTANCE"), os.environ.get("MASTODON_TOKEN")
    if not (inst and tok):
        return
    _post_json(f"{inst.rstrip('/')}/api/v1/statuses", {"status": text[:500]},
               {"Authorization": f"Bearer {tok}"})


def post_bluesky(text: str) -> None:
    handle, pw = os.environ.get("BLUESKY_HANDLE"), os.environ.get("BLUESKY_APP_PASSWORD")
    if not (handle and pw):
        return
    base = "https://bsky.social/xrpc"
    sess = _post_json(f"{base}/com.atproto.server.createSession",
                      {"identifier": handle, "password": pw}, {})
    text = text if len(text) <= 300 else text[:297] + "..."
    record = {"$type": "app.bsky.feed.post", "text": text,
              "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}
    # Make the diff link clickable (Bluesky needs explicit link facets, byte offsets).
    url = text.rsplit(" ", 1)[-1]
    if url.startswith("http"):
        start = len(text.encode()) - len(url.encode())
        record["facets"] = [{"index": {"byteStart": start, "byteEnd": len(text.encode())},
                             "features": [{"$type": "app.bsky.richtext.facet#link", "uri": url}]}]
    _post_json(f"{base}/com.atproto.repo.createRecord",
               {"repo": sess["did"], "collection": "app.bsky.feed.post", "record": record},
               {"Authorization": f"Bearer {sess['accessJwt']}"})


def enabled() -> bool:
    return bool((os.environ.get("MASTODON_INSTANCE") and os.environ.get("MASTODON_TOKEN"))
                or (os.environ.get("BLUESKY_HANDLE") and os.environ.get("BLUESKY_APP_PASSWORD")))


def post(e: dict) -> None:
    text = format_status(e)
    post_mastodon(text)
    post_bluesky(text)
