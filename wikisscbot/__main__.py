"""Command line: python -m wikisscbot <command>

  refresh-ranges   pull current Shared Services Canada BGP prefixes (RIPEstat)
  backfill         collect all IP-attributed GC edits for a calendar year
  report           rebuild CSV / summary / HTML from collected data
  watch            check the last N hours for new edits, optionally post them
  blocks           print the CIDR blocks that will be queried
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys

from . import bgp, collect, post, report
from .config import ROOT, settings
from .ranges import Labeller, load_all, query_blocks


def _wikis(selected: list[str] | None) -> list[dict]:
    wikis = settings()["wikis"]
    if selected:
        wikis = [w for w in wikis if w["id"] in selected]
        missing = set(selected) - {w["id"] for w in wikis}
        if missing:
            sys.exit(f"unknown wiki id(s): {', '.join(sorted(missing))}")
    return wikis


def _prepare():
    ranges = load_all()
    blocks, warnings = query_blocks(ranges)
    for w in warnings:
        print("warning:", w, file=sys.stderr)
    return Labeller(ranges), blocks, ranges


def cmd_refresh(args) -> None:
    since = args.since or f"{min(settings().get('backfill_years', [2025]))}-01-01T00:00"
    doc = bgp.refresh(since=since)
    print(f"{len(doc['prefixes'])} SSC prefixes; per ASN: {doc['counts_by_asn']}")


def cmd_blocks(args) -> None:
    labeller, blocks, ranges = _prepare()
    print(f"{len(ranges)} ranges -> {len(blocks)} query blocks", file=sys.stderr)
    for b in blocks:
        print(b)


def cmd_backfill(args) -> None:
    years = args.year or settings().get("backfill_years", [2025])
    for y in years:
        _backfill_year(str(y), args)


def _backfill_year(year: str, args) -> None:
    start, end = f"{year}-12-31T23:59:59Z", f"{year}-01-01T00:00:00Z"
    labeller, blocks, ranges = _prepare()
    base = ROOT / "data" / year
    status_path = base / "status.json"
    status = json.loads(status_path.read_text()) if status_path.exists() else {}
    print(f"{len(ranges)} ranges, {len(blocks)} query blocks, window {end} .. {start}",
          file=sys.stderr)
    for wiki in _wikis(args.wiki):
        print(f"scanning {wiki['id']} ...", file=sys.stderr)
        res = collect.scan_wiki(wiki, blocks, labeller, start, end)
        report.write_jsonl(res.edits, base / "raw" / f"{wiki['id']}.jsonl")
        status[wiki["id"]] = {
            "scanned_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "blocks_total": len(blocks),
            "blocks_scanned": res.blocks_scanned,
            "block_errors": len(res.block_errors),
            "error_samples": dict(list(res.block_errors.items())[:5]),
            "fatal": res.fatal,
            "edits": len(res.edits),
        }
        base.mkdir(parents=True, exist_ok=True)
        status_path.write_text(json.dumps(status, indent=2) + "\n")
        print(f"  {wiki['id']}: {len(res.edits)} edits, {len(res.block_errors)} block errors",
              file=sys.stderr)
    summary = report.build(year)
    print(json.dumps({k: summary[k] for k in ("total_edits", "unique_ips", "by_wiki",
                                               "last_ip_attributed_edit_by_wiki")}, indent=2))
    if args.strict and any(s.get("fatal") for s in status.values()):
        sys.exit("one or more wikis rejected IP-range queries; see data/%s/status.json" % year)


def cmd_report(args) -> None:
    s = report.build(str(args.year))
    print(f"{s['total_edits']} edits")


def cmd_watch(args) -> None:
    now = dt.datetime.now(dt.timezone.utc)
    start = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    end = (now - dt.timedelta(hours=args.hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    labeller, blocks, _ = _prepare()
    live = ROOT / "data" / "live"
    log_path = live / "edits.jsonl"
    known = report.read_jsonl(log_path)
    seen = {(e["wiki"], e["revid"]) for e in known}
    new = []
    for wiki in _wikis(args.wiki):
        res = collect.scan_wiki(wiki, blocks, labeller, start, end)
        new.extend(e for e in res.edits if (e["wiki"], e["revid"]) not in seen)
    new.sort(key=lambda e: e["timestamp"])
    print(f"{len(new)} new edits in the last {args.hours}h", file=sys.stderr)
    if new:
        report.write_jsonl(known + new, log_path)
    if args.post and post.enabled():
        for e in new[: args.max_posts]:
            try:
                post.post(e)
            except Exception as ex:  # keep going; a failed post is not fatal
                print(f"post failed for {e['url']}: {ex}", file=sys.stderr)
    for e in new:
        print(post.format_status(e))


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="wikisscbot")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("refresh-ranges")
    r.add_argument("--since", default=None,
                   help="default: Jan 1 of the earliest backfill year")
    r.set_defaults(fn=cmd_refresh)

    b = sub.add_parser("backfill")
    b.add_argument("--year", type=int, action="append",
                   help="year to collect; repeatable (default: backfill_years in settings)")
    b.add_argument("--wiki", action="append", help="wiki id; repeatable (default: all)")
    b.add_argument("--strict", action="store_true", help="exit non-zero if a wiki rejects queries")
    b.set_defaults(fn=cmd_backfill)

    rp = sub.add_parser("report")
    rp.add_argument("--year", type=int, default=2025)
    rp.set_defaults(fn=cmd_report)

    w = sub.add_parser("watch")
    w.add_argument("--hours", type=int, default=48)
    w.add_argument("--wiki", action="append")
    w.add_argument("--post", action="store_true")
    w.add_argument("--max-posts", type=int, default=20)
    w.set_defaults(fn=cmd_watch)

    bl = sub.add_parser("blocks")
    bl.set_defaults(fn=cmd_blocks)

    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
