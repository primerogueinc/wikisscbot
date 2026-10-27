"""Refresh the list of prefixes announced by Shared Services Canada ASNs."""

from __future__ import annotations

import datetime as dt
import ipaddress
import json
from pathlib import Path

from .config import ROOT, settings
from .http import get_json

RIPESTAT = "https://stat.ripe.net/data/announced-prefixes/data.json"
OUT = ROOT / "config" / "ranges_ssc_bgp.json"


def refresh(since: str = "2025-01-01T00:00", out: Path = OUT) -> dict:
    """Fetch every prefix announced by each SSC ASN since `since`.

    The window starts at the beginning of the backfill period so that
    prefixes withdrawn part-way through 2025 still count.
    """
    asns = settings()["ssc_asns"]
    prefixes: dict[str, dict] = {}
    per_asn = {}
    for asn, label in asns.items():
        data = get_json(RIPESTAT, {"resource": f"AS{asn}", "starttime": since,
                                   "sourceapp": "wikisscbot"})
        found = data.get("data", {}).get("prefixes", [])
        per_asn[asn] = len(found)
        for p in found:
            net = ipaddress.ip_network(p["prefix"], strict=False)
            key = str(net)
            # If two SSC ASNs announced the same prefix, keep the first label.
            prefixes.setdefault(key, {"prefix": key, "asn": int(asn), "label": label})
    doc = {
        "source": "RIPEstat announced-prefixes",
        "since": since,
        "fetched": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "counts_by_asn": per_asn,
        "prefixes": sorted(prefixes.values(),
                           key=lambda x: (ipaddress.ip_network(x["prefix"]).version,
                                          ipaddress.ip_network(x["prefix"]))),
    }
    out.write_text(json.dumps(doc, indent=2) + "\n")
    return doc
