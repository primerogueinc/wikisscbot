"""Continuity watch (exploratory): who edits the pages government networks
used to edit, now that Wikipedia hides logged-out editors' IPs?

Takes the pages most edited from Government of Canada addresses in the
archive, pulls every revision since the temporary-accounts switch, and
summarises edits by temporary accounts ("~YYYY-NNNNN-NN").

Output: research/continuity/latest.json and latest.md

This does NOT attribute any temporary account to a government network or a
person. Temporary accounts on these pages are leads only.
"""

from __future__ import annotations

import collections
import datetime as dt
import glob
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from wikisscbot.http import ApiError, get_json  # noqa: E402

SWITCH = {"enwiki": "2025-11-04T00:00:00Z", "frwiki": "2025-11-04T00:00:00Z"}
HOSTS = {"enwiki": "en.wikipedia.org", "frwiki": "fr.wikipedia.org"}
OUT = ROOT / "research" / "continuity"
MIN_GC_EDITS = 3
MAX_PAGES = 250


def archive_edits() -> list[dict]:
    rows = []
    for f in sorted(glob.glob(str(ROOT / "data" / "[0-9][0-9][0-9][0-9]" / "edits.jsonl"))):
        with open(f, encoding="utf-8") as fh:
            rows.extend(json.loads(line) for line in fh if line.strip())
    return rows


def revisions_since(wiki: str, title: str, since: str) -> list[dict]:
    api = f"https://{HOSTS[wiki]}/w/api.php"
    params = {"action": "query", "prop": "revisions", "titles": title, "format": "json",
              "formatversion": "2", "rvprop": "ids|timestamp|user|comment|size",
              "rvlimit": "max", "rvdir": "newer", "rvstart": since}
    out = []
    while True:
        data = get_json(api, params)
        pages = data.get("query", {}).get("pages", [])
        for p in pages:
            out.extend(p.get("revisions", []))
        cont = data.get("continue")
        if not cont:
            return out
        params.update(cont)


def main() -> None:
    edits = archive_edits()
    by_page = collections.defaultdict(list)
    for e in edits:
        if e["wiki"] in HOSTS and e.get("ns", 0) == 0:
            by_page[(e["wiki"], e["title"])].append(e)
    pages = sorted(((k, v) for k, v in by_page.items() if len(v) >= MIN_GC_EDITS),
                   key=lambda kv: -len(kv[1]))[:MAX_PAGES]
    print(f"{len(pages)} pages with >= {MIN_GC_EDITS} GC edits", file=sys.stderr)

    results, temp_accounts = [], collections.defaultdict(set)
    hours, weekdays = collections.Counter(), collections.Counter()
    for (wiki, title), gc in pages:
        try:
            revs = revisions_since(wiki, title, SWITCH[wiki])
        except ApiError as ex:
            print(f"  {wiki}:{title}: {ex}", file=sys.stderr)
            continue
        temp = [r for r in revs if str(r.get("user", "")).startswith("~")]
        for r in temp:
            temp_accounts[r["user"]].add(f"{wiki}|{title}")
            t = dt.datetime.strptime(r["timestamp"], "%Y-%m-%dT%H:%M:%SZ")
            hours[t.hour] += 1
            weekdays[t.isoweekday()] += 1
        gc_ips = collections.Counter(e["ip"] for e in gc)
        results.append({
            "wiki": wiki, "title": title, "gc_edits": len(gc),
            "gc_top_ips": gc_ips.most_common(3),
            "gc_labels": sorted({e["label"] for e in gc}),
            "gc_last": max(e["timestamp"] for e in gc),
            "revisions_since_switch": len(revs),
            "temp_account_edits": len(temp),
            "temp_accounts": sorted({r["user"] for r in temp}),
            "temp_edits": [{"ts": r["timestamp"], "user": r["user"], "comment": r.get("comment", ""),
                            "url": f"https://{HOSTS[wiki]}/w/index.php?diff={r['revid']}&oldid={r.get('parentid') or r['revid']}"}
                           for r in temp],
        })

    # Temporary accounts seen on more than one GC-heavy page.
    multi = {u: sorted(p) for u, p in temp_accounts.items() if len(p) > 1}
    doc = {
        "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pages_checked": len(results),
        "pages_with_temp_edits": sum(1 for r in results if r["temp_account_edits"]),
        "temp_account_edits": sum(r["temp_account_edits"] for r in results),
        "distinct_temp_accounts": len(temp_accounts),
        "temp_accounts_on_multiple_pages": multi,
        "temp_edit_hours_utc": dict(sorted(hours.items())),
        "temp_edit_weekdays": dict(sorted(weekdays.items())),
        "pages": sorted(results, key=lambda r: -r["temp_account_edits"]),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "latest.json").write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n")

    lines = [f"# Continuity watch — {doc['generated']}", "",
             "Pages most edited from Government of Canada addresses before Wikipedia's "
             "temporary-account switch, and the temporary-account edits made to them since. "
             "**Temporary accounts are not attributed to any network or person; these are leads only.**", "",
             f"- Pages checked: {doc['pages_checked']}",
             f"- Pages with temporary-account edits since the switch: {doc['pages_with_temp_edits']}",
             f"- Temporary-account edits: {doc['temp_account_edits']} from {doc['distinct_temp_accounts']} accounts",
             f"- Accounts seen on 2+ of these pages: {len(multi)}", "",
             "| Page | GC edits (before) | Main GC address | Temp-account edits since | Accounts |",
             "|---|---|---|---|---|"]
    for r in doc["pages"][:60]:
        if not r["temp_account_edits"]:
            continue
        top = r["gc_top_ips"][0][0] if r["gc_top_ips"] else ""
        lines.append(f"| {r['wiki']}: {r['title']} | {r['gc_edits']} | {top} | "
                     f"{r['temp_account_edits']} | {len(r['temp_accounts'])} |")
    if multi:
        lines += ["", "## Temporary accounts on several of these pages", ""]
        for u, ps in sorted(multi.items(), key=lambda kv: -len(kv[1])):
            lines.append(f"- `{u}`: " + "; ".join(p.split('|', 1)[1] for p in ps))
    (OUT / "latest.md").write_text("\n".join(lines) + "\n")
    print(json.dumps({k: doc[k] for k in ("pages_checked", "pages_with_temp_edits",
                                           "temp_account_edits", "distinct_temp_accounts")}))


if __name__ == "__main__":
    main()
