"""Command line: python -m wikisscbot <command>

  refresh-ranges   pull current Shared Services Canada BGP prefixes (RIPEstat)
  backfill         collect all IP-attributed GC edits for one or more years
  enrich           classify edited pages with Wikimedia's article-topic model
  report           rebuild CSV / summary / HTML from collected data
  watch            check the last N hours for new edits, optionally post them
  verify           check the manifest hash chain and data checksums
  site             build the static website into docs/
  blocks           print the CIDR blocks that will be queried
  version          print the tool version

Every command that contacts the network writes a provenance manifest
(see wikisscbot/provenance.py).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys

from . import __version__, bgp, collect, post, provenance, report, site, topics
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


def _report_manifest(res: dict) -> None:
    print(f"manifest #{res['seq']} {provenance.rel(res['manifest'])} sha256={res['sha256']}",
          file=sys.stderr)


def cmd_refresh(args) -> None:
    since = args.since or f"{min(settings().get('backfill_years', [2025]))}-01-01T00:00"
    with provenance.Recorder("refresh-ranges") as rec:
        doc = bgp.refresh(since=since)
    print(f"{len(doc['prefixes'])} SSC prefixes; per ASN: {doc['counts_by_asn']}")
    _report_manifest(provenance.finalize(
        rec, {"since": since, "asns": list(settings()["ssc_asns"])},
        [bgp.OUT], {"prefixes": len(doc["prefixes"])}))


def cmd_blocks(args) -> None:
    labeller, blocks, ranges = _prepare()
    print(f"{len(ranges)} ranges -> {len(blocks)} query blocks", file=sys.stderr)
    for b in blocks:
        print(b)


def cmd_backfill(args) -> None:
    years = [str(y) for y in (args.year or settings().get("backfill_years", [2025]))]
    wikis = _wikis(args.wiki)
    labeller, blocks, ranges = _prepare()
    counts, fatal = {}, False
    with provenance.Recorder("backfill") as rec:
        for year in years:
            n, f = _backfill_year(year, wikis, labeller, blocks, ranges)
            counts[year] = n
            fatal = fatal or f
    _report_manifest(provenance.finalize(
        rec,
        {"years": years, "wikis": [w["id"] for w in wikis], "query_blocks": len(blocks),
         "ranges": len(ranges), "method": "MediaWiki list=usercontribs uciprange"},
        [ROOT / "data" / y for y in years], {f"edits_{y}": n for y, n in counts.items()}))
    if args.strict and fatal:
        sys.exit("one or more wikis rejected IP-range queries; see data/<year>/status.json")


def _backfill_year(year, wikis, labeller, blocks, ranges) -> tuple[int, bool]:
    start, end = f"{year}-12-31T23:59:59Z", f"{year}-01-01T00:00:00Z"
    base = ROOT / "data" / year
    status_path = base / "status.json"
    status = json.loads(status_path.read_text()) if status_path.exists() else {}
    print(f"{year}: {len(ranges)} ranges, {len(blocks)} query blocks", file=sys.stderr)
    for wiki in wikis:
        print(f"scanning {wiki['id']} {year} ...", file=sys.stderr)
        res = collect.scan_wiki(wiki, blocks, labeller, start, end)
        report.write_jsonl(res.edits, base / "raw" / f"{wiki['id']}.jsonl")
        status[wiki["id"]] = {
            "scanned_at": provenance.now(),
            "window": [end, start],
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
    print(json.dumps({"year": year, **{k: summary[k] for k in (
        "total_edits", "unique_ips", "by_wiki", "last_ip_attributed_edit_by_wiki")}}, indent=2))
    return summary["total_edits"], any(s.get("fatal") for s in status.values())


def cmd_enrich(args) -> None:
    edits = topics.all_edits()
    with provenance.Recorder("enrich") as rec:
        stats = topics.enrich(edits)
    print(json.dumps(stats))
    _report_manifest(provenance.finalize(
        rec, {"model": "outlink-topic-model", "threshold": topics.THRESHOLD},
        [topics.CACHE], stats))


def cmd_report(args) -> None:
    for y in (args.year or report.available_years()):
        s = report.build(str(y))
        print(f"{y}: {s['total_edits']} edits")


def cmd_watch(args) -> None:
    now = dt.datetime.now(dt.timezone.utc)
    start = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    end = (now - dt.timedelta(hours=args.hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    labeller, blocks, _ = _prepare()
    wikis = _wikis(args.wiki)
    log_path = ROOT / "data" / "live" / "edits.jsonl"
    known = report.read_jsonl(log_path)
    seen = {(e["wiki"], e["revid"]) for e in known}
    new = []
    with provenance.Recorder("watch") as rec:
        for wiki in wikis:
            res = collect.scan_wiki(wiki, blocks, labeller, start, end)
            new.extend(e for e in res.edits if (e["wiki"], e["revid"]) not in seen)
    new.sort(key=lambda e: e["timestamp"])
    print(f"{len(new)} new edits in the last {args.hours}h", file=sys.stderr)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    report.write_jsonl(known + new, log_path)
    _report_manifest(provenance.finalize(
        rec, {"window": [end, start], "wikis": [w["id"] for w in wikis]},
        [log_path], {"new_edits": len(new)}))
    if args.post and post.enabled():
        for e in new[: args.max_posts]:
            try:
                post.post(e)
            except Exception as ex:  # keep going; a failed post is not fatal
                print(f"post failed for {e['url']}: {ex}", file=sys.stderr)
    for e in new:
        print(post.format_status(e))


def cmd_site(args) -> None:
    print(json.dumps(site.build()))


def cmd_verify(args) -> None:
    problems = provenance.verify(check_ots=args.ots)
    if problems:
        for p in problems:
            print("FAIL", p)
        sys.exit(1)
    link = provenance.last_link()
    print(f"OK: chain of {link['seq'] + 1 if link else 0} manifests intact; "
          f"all files in data/SHA256SUMS match")


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="wikisscbot")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("refresh-ranges")
    r.add_argument("--since", default=None, help="default: Jan 1 of the earliest backfill year")
    r.set_defaults(fn=cmd_refresh)

    b = sub.add_parser("backfill")
    b.add_argument("--year", type=int, action="append",
                   help="year to collect; repeatable (default: backfill_years in settings)")
    b.add_argument("--wiki", action="append", help="wiki id; repeatable (default: all)")
    b.add_argument("--strict", action="store_true", help="exit non-zero if a wiki rejects queries")
    b.set_defaults(fn=cmd_backfill)

    e = sub.add_parser("enrich")
    e.set_defaults(fn=cmd_enrich)

    rp = sub.add_parser("report")
    rp.add_argument("--year", type=int, action="append")
    rp.set_defaults(fn=cmd_report)

    w = sub.add_parser("watch")
    w.add_argument("--hours", type=int, default=48)
    w.add_argument("--wiki", action="append")
    w.add_argument("--post", action="store_true")
    w.add_argument("--max-posts", type=int, default=20)
    w.set_defaults(fn=cmd_watch)

    v = sub.add_parser("verify")
    v.add_argument("--ots", action="store_true", help="also require an OpenTimestamps proof per manifest")
    v.set_defaults(fn=cmd_verify)

    sub.add_parser("site").set_defaults(fn=cmd_site)
    sub.add_parser("blocks").set_defaults(fn=cmd_blocks)
    sub.add_parser("version").set_defaults(fn=lambda a: print(__version__))

    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
