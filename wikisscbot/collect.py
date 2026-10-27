"""Collect anonymous (IP-attributed) edits from GC ranges via the MediaWiki API.

Uses list=usercontribs with uciprange, which returns contributions made
while logged out from any IP in a CIDR block. Only edits that were
publicly attributed to an IP address are returned. Edits made under
Wikimedia's temporary accounts (enwiki from 2025-11-04) never expose an
IP publicly and are not collected; this tool does not attempt to reveal
them.
"""

from __future__ import annotations

import ipaddress
import sys
from dataclasses import dataclass, field

from .http import ApiError, get_json
from .ranges import Labeller, Network

PROPS = "ids|title|timestamp|comment|size|sizediff|flags|tags"


@dataclass
class ScanResult:
    wiki: str
    edits: list[dict] = field(default_factory=list)
    blocks_scanned: int = 0
    block_errors: dict[str, str] = field(default_factory=dict)
    fatal: str | None = None


def _is_ip(user: str) -> bool:
    try:
        ipaddress.ip_address(user)
        return True
    except ValueError:
        return False


def diff_url(host: str, revid: int, parentid: int | None) -> str:
    if parentid:
        return f"https://{host}/w/index.php?diff={revid}&oldid={parentid}"
    return f"https://{host}/w/index.php?oldid={revid}"


def scan_block(host: str, block: Network, start: str, end: str):
    """Yield raw contribution dicts for one CIDR block, newest first.

    `start` is the newer timestamp and `end` the older one (MediaWiki's
    ucdir=older convention).
    """
    api = f"https://{host}/w/api.php"
    params = {
        "action": "query", "list": "usercontribs", "format": "json",
        "formatversion": "2", "uciprange": str(block), "ucstart": start,
        "ucend": end, "ucdir": "older", "uclimit": "max", "ucprop": PROPS,
        "maxlag": "5", "errorformat": "plaintext",
    }
    while True:
        data = get_json(api, params)
        if "error" in data or "errors" in data:
            err = data.get("error") or data["errors"][0]
            raise ApiError(f"{err.get('code')}: {err.get('info') or err.get('text')}")
        yield from data.get("query", {}).get("usercontribs", [])
        cont = data.get("continue")
        if not cont:
            return
        params.update(cont)


def scan_wiki(wiki: dict, blocks: list[Network], labeller: Labeller,
              start: str, end: str, log=sys.stderr) -> ScanResult:
    res = ScanResult(wiki["id"])
    host = wiki["host"]
    seen: set[int] = set()
    consecutive_param_errors = 0
    for i, block in enumerate(blocks, 1):
        try:
            for c in scan_block(host, block, start, end):
                user = c.get("user", "")
                if not _is_ip(user) or c["revid"] in seen:
                    continue
                seen.add(c["revid"])
                rng = labeller.match(user)
                res.edits.append({
                    "wiki": wiki["id"],
                    "host": host,
                    "revid": c["revid"],
                    "parentid": c.get("parentid"),
                    "timestamp": c["timestamp"],
                    "title": c["title"],
                    "ns": c.get("ns", 0),
                    "comment": c.get("comment", ""),
                    "size": c.get("size"),
                    "sizediff": c.get("sizediff"),
                    "minor": bool(c.get("minor")),
                    "new": bool(c.get("new")),
                    "tags": c.get("tags", []),
                    "ip": user,
                    "range": str(rng.network) if rng else str(block),
                    "label": rng.label if rng else "Government of Canada (unlabelled)",
                    "label_source": rng.source if rng else "query-block",
                    "url": diff_url(host, c["revid"], c.get("parentid")),
                })
            consecutive_param_errors = 0
        except ApiError as e:
            msg = str(e)
            res.block_errors[str(block)] = msg
            print(f"  [{wiki['id']}] {block}: {msg}", file=log)
            # If the wiki rejects the parameter itself, every block will fail.
            if "badparam" in msg or "unrecognized" in msg.lower() or "invalidparam" in msg:
                consecutive_param_errors += 1
                if consecutive_param_errors >= 3:
                    res.fatal = f"wiki rejected IP-range queries: {msg}"
                    print(f"  [{wiki['id']}] aborting: {res.fatal}", file=log)
                    break
        res.blocks_scanned = i
        if i % 25 == 0:
            print(f"  [{wiki['id']}] {i}/{len(blocks)} blocks, {len(res.edits)} edits",
                  file=log)
    res.edits.sort(key=lambda e: e["timestamp"])
    return res
