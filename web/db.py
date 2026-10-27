"""Build the SQLite search database from the bot's data directory.

Reads data/<year>/edits.jsonl, data/topics.json and the provenance chain,
and writes a single SQLite file with an FTS5 index. The build is atomic:
a new file is written next to the old one and swapped in.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from wikisscbot import topics as topicmod  # noqa: E402

SCHEMA = """
CREATE TABLE edits (
  id INTEGER PRIMARY KEY,
  wiki TEXT, host TEXT, revid INTEGER, parentid INTEGER,
  ts TEXT, year TEXT, month TEXT, hour INTEGER, weekday INTEGER,
  title TEXT, display_title TEXT, ns INTEGER, comment TEXT, size INTEGER, sizediff INTEGER,
  minor INTEGER, is_new INTEGER, ip TEXT, range TEXT, label TEXT, label_source TEXT, url TEXT,
  UNIQUE(wiki, revid)
);
CREATE TABLE edit_beats (edit_id INTEGER, beat TEXT);
CREATE TABLE page_topics (wiki TEXT, title TEXT, topic TEXT, score REAL);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE VIRTUAL TABLE edits_fts USING fts5(
  title, display_title, comment, ip, label, content='edits', content_rowid='id',
  tokenize='unicode61 remove_diacritics 2'
);
CREATE INDEX ix_edits_ip ON edits(ip);
CREATE INDEX ix_edits_ts ON edits(ts);
CREATE INDEX ix_edits_label ON edits(label);
CREATE INDEX ix_edits_title ON edits(wiki, title);
CREATE INDEX ix_beats ON edit_beats(beat, edit_id);
CREATE INDEX ix_beats_edit ON edit_beats(edit_id);
CREATE INDEX ix_topics ON page_topics(topic);
CREATE INDEX ix_topics_page ON page_topics(wiki, title);
"""


def _edits(data_dir: Path) -> list[dict]:
    out = []
    for f in sorted(data_dir.glob("[0-9][0-9][0-9][0-9]/edits.jsonl")):
        with f.open(encoding="utf-8") as fh:
            out.extend(json.loads(line) for line in fh if line.strip())
    live = data_dir / "live" / "edits.jsonl"
    if live.exists():
        with live.open(encoding="utf-8") as fh:
            out.extend(json.loads(line) for line in fh if line.strip())
    return out


def build(data_dir: Path, db_path: Path) -> dict:
    cache_path = data_dir / "topics.json"
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    labels = cache.get("labels", {})
    tmp = db_path.with_suffix(".building")
    if tmp.exists():
        tmp.unlink()
    con = sqlite3.connect(tmp)
    con.executescript(SCHEMA)
    seen = set()
    n = 0
    for e in _edits(data_dir):
        key = (e["wiki"], e["revid"])
        if key in seen:
            continue
        seen.add(key)
        ts = e["timestamp"]
        d = dt.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")
        display = labels.get(f"{e['wiki']}|{e['title']}")
        cur = con.execute(
            "INSERT INTO edits (wiki, host, revid, parentid, ts, year, month, hour, weekday, title,"
            " display_title, ns, comment, size, sizediff, minor, is_new, ip, range, label,"
            " label_source, url) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (e["wiki"], e.get("host"), e["revid"], e.get("parentid"), ts, ts[:4], ts[:7],
             d.hour, d.isoweekday(), e["title"], display, e.get("ns", 0), e.get("comment") or "",
             e.get("size"), e.get("sizediff"), int(bool(e.get("minor"))),
             int(bool(e.get("new"))), e["ip"], e.get("range"), e.get("label"),
             e.get("label_source"), e.get("url")))
        for b in topicmod.beats(display or e["title"], e.get("comment") or ""):
            con.execute("INSERT INTO edit_beats VALUES (?,?)", (cur.lastrowid, b))
        n += 1
    con.execute("INSERT INTO edits_fts(edits_fts) VALUES ('rebuild')")

    if cache:
        rows = []
        for key, items in cache.get("pages", {}).items():
            wiki, title = key.split("|", 1)
            rows.extend((wiki, title, t["topic"], t["score"]) for t in items)
        con.executemany("INSERT INTO page_topics VALUES (?,?,?,?)", rows)

    from wikisscbot import provenance
    links = provenance.chain_links(data_dir.parent)
    meta = {
        "built": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "edits": str(n),
        "chain_length": str(len(links)),
        "latest_manifest": json.dumps(links[-1]) if links else "",
    }
    con.executemany("INSERT INTO meta VALUES (?,?)", meta.items())
    con.commit()
    con.execute("ANALYZE")
    con.close()
    os.replace(tmp, db_path)
    return {"edits": n, "manifests": len(links)}


if __name__ == "__main__":
    d = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "data"
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else REPO / "web" / "wikissc.db"
    print(build(d, out))
