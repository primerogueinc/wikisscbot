"""Write collected edits to JSONL, CSV, a summary, and a static HTML page."""

from __future__ import annotations

import csv
import datetime as dt
import json
from collections import Counter, defaultdict
from pathlib import Path

from .config import ROOT

CSV_FIELDS = ["timestamp", "wiki", "title", "label", "ip", "range", "sizediff",
              "minor", "new", "comment", "url", "revid", "parentid", "label_source"]


def write_jsonl(edits: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for e in edits:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_csv(edits: list[dict], path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        for e in edits:
            w.writerow(e)


def summarize(edits: list[dict], status: dict) -> dict:
    by_label = Counter(e["label"] for e in edits)
    by_wiki = Counter(e["wiki"] for e in edits)
    by_month = Counter(e["timestamp"][:7] for e in edits)
    titles = Counter((e["wiki"], e["title"]) for e in edits)
    last_ip_edit = {}
    for e in edits:
        last_ip_edit[e["wiki"]] = max(last_ip_edit.get(e["wiki"], ""), e["timestamp"])
    label_titles = defaultdict(Counter)
    for e in edits:
        label_titles[e["label"]][e["title"]] += 1
    return {
        "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total_edits": len(edits),
        "unique_ips": len({e["ip"] for e in edits}),
        "by_label": dict(by_label.most_common()),
        "by_wiki": dict(by_wiki.most_common()),
        "by_month": dict(sorted(by_month.items())),
        "top_pages": [{"wiki": w, "title": t, "edits": n} for (w, t), n in titles.most_common(50)],
        "top_pages_by_label": {k: dict(v.most_common(10)) for k, v in label_titles.items()},
        "last_ip_attributed_edit_by_wiki": last_ip_edit,
        "scan_status": status,
    }


def available_years() -> list[str]:
    data = ROOT / "data"
    return sorted(p.name for p in data.glob("[0-9][0-9][0-9][0-9]") if (p / "summary.json").exists())


def write_html(edits: list[dict], summary: dict, path: Path, year: str,
               years: list[str] | None = None) -> None:
    template = (ROOT / "wikisscbot" / "template.html").read_text(encoding="utf-8")
    slim = [{k: e.get(k) for k in ("timestamp", "wiki", "title", "label", "ip",
                                   "sizediff", "comment", "url")} for e in edits]
    payload = json.dumps({"year": year, "years": years or [year], "summary": summary,
                          "edits": slim},
                         ensure_ascii=False)
    # Keep the JSON from terminating the <script> element early.
    payload = payload.replace("</", "<\\/")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(template.replace("__YEAR__", year).replace("__DATA__", payload),
                    encoding="utf-8")


def build(year: str) -> dict:
    """Rebuild every output for `year` from the per-wiki raw files."""
    base = ROOT / "data" / year
    edits: list[dict] = []
    for f in sorted((base / "raw").glob("*.jsonl")):
        edits.extend(read_jsonl(f))
    edits.sort(key=lambda e: (e["timestamp"], e["wiki"], e["revid"]))
    status_path = base / "status.json"
    status = json.loads(status_path.read_text()) if status_path.exists() else {}
    write_jsonl(edits, base / "edits.jsonl")
    write_csv(edits, base / "edits.csv")
    summary = summarize(edits, status)
    (base / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    years = available_years()
    write_html(edits, summary, ROOT / "docs" / f"{year}.html", year, years)
    # The index always shows the most recent collected year.
    latest = years[-1] if years else year
    if latest == year:
        write_html(edits, summary, ROOT / "docs" / "index.html", year, years)
    return summary
