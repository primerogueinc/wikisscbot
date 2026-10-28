"""Probe niche wikis for public IP-attributed edits from Government of Canada ranges.

For each candidate wiki in config/niche_wikis.json:
  1. Is the API reachable? Which MediaWiki version?
  2. Does it still show logged-out editors' IPs (no temporary accounts)?
  3. Does it accept IP-range contribution queries (uciprange)?
  4. How many edits from Government of Canada ranges does it hold (2021 onward)?

Range queries only. This tool never looks up usernames or follows any
account across sites.

Output: research/niche/probe.json and probe.md
"""

from __future__ import annotations

import collections
import datetime as dt
import ipaddress
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from wikisscbot.http import ApiError, get_json  # noqa: E402
from wikisscbot.ranges import Labeller, load_all, query_blocks  # noqa: E402

OUT = ROOT / "research" / "niche"
SINCE = "2021-01-01T00:00:00Z"
PER_WIKI_BUDGET_S = 240


def api_url(w: dict) -> str:
    return w["base"].rstrip("/") + w.get("api", "/api.php")


def probe(w: dict, blocks, labeller) -> dict:
    res = {"id": w["id"], "base": w["base"], "genre": w["genre"], "reachable": False}
    api = api_url(w)
    try:
        si = get_json(api, {"action": "query", "meta": "siteinfo", "siprop": "general",
                            "format": "json", "formatversion": "2"}, retries=2, timeout=20)
    except ApiError as ex:
        res["error"] = str(ex)[:200]
        return res
    gen = (si.get("query") or {}).get("general") or {}
    if not gen:
        res["error"] = "no siteinfo"
        return res
    res.update(reachable=True, sitename=gen.get("sitename"), generator=gen.get("generator"),
               temp_accounts=bool(gen.get("autocreatetempuser", {}).get("enabled")
                                  if isinstance(gen.get("autocreatetempuser"), dict) else False))

    # Range support test on the largest SSC block.
    test = {"action": "query", "list": "usercontribs", "uciprange": "205.193.0.0/16",
            "uclimit": "1", "format": "json", "formatversion": "2"}
    try:
        t = get_json(api, test, retries=2, timeout=20)
        err = t.get("error") or (t.get("errors") or [None])[0]
        res["range_queries"] = not err
        if err:
            res["range_error"] = f"{err.get('code')}: {err.get('info') or err.get('text')}"[:200]
    except ApiError as ex:
        res["range_queries"] = False
        res["range_error"] = str(ex)[:200]
    if not res["range_queries"]:
        return res

    # Count GC edits across all query blocks, within a time budget.
    deadline = time.monotonic() + PER_WIKI_BUDGET_S
    edits, scanned = [], 0
    for b in blocks:
        if time.monotonic() > deadline:
            break
        params = {"action": "query", "list": "usercontribs", "uciprange": str(b),
                  "ucend": SINCE, "uclimit": "500", "ucprop": "ids|title|timestamp|comment|sizediff",
                  "format": "json", "formatversion": "2"}
        try:
            while True:
                d = get_json(api, params, retries=2, timeout=30)
                for c in (d.get("query") or {}).get("usercontribs", []):
                    try:
                        ipaddress.ip_address(c.get("user", ""))
                    except ValueError:
                        continue
                    r = labeller.match(c["user"])
                    edits.append({"ts": c["timestamp"], "ip": c["user"], "title": c["title"],
                                  "label": r.label if r else "GC (unlabelled)",
                                  "comment": c.get("comment", ""), "sizediff": c.get("sizediff"),
                                  "url": f"{w['base']}/?diff={c['revid']}&oldid={c.get('parentid') or c['revid']}"})
                if not d.get("continue"):
                    break
                params.update(d["continue"])
        except ApiError:
            pass
        scanned += 1
    res["blocks_scanned"] = scanned
    res["blocks_total"] = len(blocks)
    res["gc_edits"] = len(edits)
    res["gc_ips"] = collections.Counter(e["ip"] for e in edits).most_common(10)
    res["top_pages"] = collections.Counter(e["title"] for e in edits).most_common(10)
    res["by_year"] = dict(sorted(collections.Counter(e["ts"][:4] for e in edits).items()))
    res["last_gc_edit"] = max((e["ts"] for e in edits), default=None)
    res["sample"] = sorted(edits, key=lambda e: e["ts"], reverse=True)[:15]
    return res


def main() -> None:
    cfg = json.loads((ROOT / "config" / "niche_wikis.json").read_text())
    ranges = load_all()
    blocks, _ = query_blocks(ranges)
    labeller = Labeller(ranges)
    results = []
    for w in cfg["wikis"]:
        print(f"probing {w['id']} ...", file=sys.stderr)
        r = probe(w, blocks, labeller)
        print(f"  {json.dumps({k: r.get(k) for k in ('reachable', 'temp_accounts', 'range_queries', 'gc_edits', 'error', 'range_error')})}",
              file=sys.stderr)
        results.append(r)
    doc = {"generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "since": SINCE, "wikis": results}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "probe.json").write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n")

    lines = [f"# Niche wiki probe — {doc['generated']}", "",
             "Range queries only (Government of Canada IP ranges); no usernames are looked up or followed.", "",
             "| Wiki | Genre | Reachable | Shows IPs | Range queries | GC edits since 2021 | Last GC edit | Top GC address |",
             "|---|---|---|---|---|---|---|---|"]
    for r in results:
        top = r["gc_ips"][0][0] if r.get("gc_ips") else ""
        lines.append(f"| {r['base']} | {r['genre']} | {'yes' if r['reachable'] else 'no'} | "
                     f"{'' if not r['reachable'] else ('no (temp accounts)' if r.get('temp_accounts') else 'yes')} | "
                     f"{'' if not r['reachable'] else ('yes' if r.get('range_queries') else 'no')} | "
                     f"{r.get('gc_edits', '')} | {(r.get('last_gc_edit') or '')[:10]} | {top} |")
    for r in results:
        if r.get("gc_edits"):
            lines += ["", f"## {r['base']}", "",
                      "Top pages: " + "; ".join(f"{t} ({n})" for t, n in r["top_pages"]),
                      "", "Top addresses: " + "; ".join(f"{i} ({n})" for i, n in r["gc_ips"])]
    (OUT / "probe.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
