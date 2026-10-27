"""wikisscbot web app: search and aggregate GC anonymous Wikipedia edits.

Run:  uvicorn web.app:app --host 0.0.0.0 --port $PORT

Environment:
  WIKISSC_DATA_DIR    data directory to load (default: <repo>/data)
  WIKISSC_DB          SQLite path (default: <repo>/web/wikissc.db)
  WIKISSC_SYNC_URL    optional tarball URL of the GitHub repo; when set, the app
                      downloads it every WIKISSC_SYNC_HOURS, verifies the
                      manifest chain and checksums, and only then swaps in the
                      new data. Example:
                      https://codeload.github.com/primerogueinc/wikisscbot/tar.gz/refs/heads/main
  WIKISSC_SYNC_HOURS  default 6
  WIKISSC_CACHE_DIR   where synced copies are unpacked (default: /tmp/wikissc)
"""

from __future__ import annotations

import csv
import io
import ipaddress
import json
import os
import re
import shutil
import sqlite3
import sys
import tarfile
import tempfile
import threading
import time
import urllib.request
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from wikisscbot import __version__, provenance  # noqa: E402

from . import db as dbmod  # noqa: E402

DB_PATH = Path(os.environ.get("WIKISSC_DB", REPO / "web" / "wikissc.db"))
CACHE_DIR = Path(os.environ.get("WIKISSC_CACHE_DIR", "/tmp/wikissc"))
SYNC_URL = os.environ.get("WIKISSC_SYNC_URL", "").strip()
SYNC_HOURS = float(os.environ.get("WIKISSC_SYNC_HOURS", "6"))
STATIC = Path(__file__).resolve().parent / "static"

STATE = {
    "data_dir": Path(os.environ.get("WIKISSC_DATA_DIR", REPO / "data")),
    "verified": None,          # True / False / None (not checked)
    "problems": [],
    "last_sync": None,
    "sync_error": None,
}
_lock = threading.Lock()

# OpenTimestamps attestation tags (see opentimestamps python-opentimestamps).
OTS_BITCOIN = bytes.fromhex("0588960d73d71901")
OTS_PENDING = bytes.fromhex("83dfe30d2ef90c8e")


# --- database ---------------------------------------------------------------

def _ip_in(ip: str, cidr: str) -> int:
    try:
        return int(ipaddress.ip_address(ip) in ipaddress.ip_network(cidr, strict=False))
    except ValueError:
        return 0


_local = threading.local()
_generation = [0]   # bumped on every rebuild so threads reopen the swapped file


def conn() -> sqlite3.Connection:
    cached = getattr(_local, "conn", None)
    if cached and cached[0] == _generation[0]:
        return cached[1]
    if cached:
        cached[1].close()
    c = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.create_function("ip_in", 2, _ip_in, deterministic=True)
    _local.conn = (_generation[0], c)
    return c


def rebuild(data_dir: Path) -> dict:
    problems = provenance.verify(root=data_dir.parent)
    # A missing chain is acceptable for older data; tampering is not.
    hard = [p for p in problems if p != "no manifest chain yet"]
    with _lock:
        res = dbmod.build(data_dir, DB_PATH)
        _generation[0] += 1
        STATE.update(data_dir=data_dir, verified=not hard, problems=problems)
    return res


def sync_once() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(dir=CACHE_DIR))
    try:
        req = urllib.request.Request(SYNC_URL, headers={"User-Agent": f"wikisscbot-web/{__version__}"})
        with urllib.request.urlopen(req, timeout=120) as resp, tarfile.open(fileobj=resp, mode="r|gz") as tar:
            for m in tar:
                parts = Path(m.name).parts
                # Keep only <top>/data/... ; refuse anything odd.
                if len(parts) < 2 or parts[1] != "data" or ".." in parts or not (m.isfile() or m.isdir()):
                    continue
                dest = tmp / Path(*parts[1:])
                if m.isdir():
                    dest.mkdir(parents=True, exist_ok=True)
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(m) as src, dest.open("wb") as out:
                    shutil.copyfileobj(src, out)
        problems = provenance.verify(root=tmp)
        hard = [p for p in problems if p != "no manifest chain yet"]
        if hard:
            STATE.update(sync_error="downloaded data failed verification; kept previous data",
                         problems=problems)
            shutil.rmtree(tmp, ignore_errors=True)
            return
        final = CACHE_DIR / "current"
        old = CACHE_DIR / "previous"
        shutil.rmtree(old, ignore_errors=True)
        if final.exists():
            final.rename(old)
        tmp.rename(final)
        rebuild(final / "data")
        STATE.update(last_sync=provenance.now(), sync_error=None)
        shutil.rmtree(old, ignore_errors=True)
    except Exception as ex:  # keep serving the old data
        STATE["sync_error"] = f"{type(ex).__name__}: {ex}"
        shutil.rmtree(tmp, ignore_errors=True)


def _sync_loop() -> None:
    while True:
        sync_once()
        time.sleep(max(SYNC_HOURS, 0.25) * 3600)


@asynccontextmanager
async def lifespan(_app):
    rebuild(STATE["data_dir"])
    if SYNC_URL:
        threading.Thread(target=_sync_loop, daemon=True).start()
    yield


app = FastAPI(title="wikisscbot", version=__version__, lifespan=lifespan)


# --- filters ----------------------------------------------------------------

def fts_query(q: str) -> str:
    tokens = re.findall(r"[\w.:\-']+", q, flags=re.UNICODE)
    return " ".join('"' + t.replace('"', "") + '"*' for t in tokens)


def where(q=None, wiki=None, label=None, ip=None, beat=None, topic=None, year=None,
          date_from=None, date_to=None, title=None):
    clauses, args = [], []
    if q:
        fq = fts_query(q)
        if fq:
            clauses.append("e.id IN (SELECT rowid FROM edits_fts WHERE edits_fts MATCH ?)")
            args.append(fq)
    if wiki:
        clauses.append("e.wiki = ?")
        args.append(wiki)
    if label:
        clauses.append("e.label = ?")
        args.append(label)
    if ip:
        if "/" in ip:
            clauses.append("ip_in(e.ip, ?)")
        else:
            clauses.append("e.ip = ?")
        args.append(ip)
    if beat:
        clauses.append("EXISTS (SELECT 1 FROM edit_beats b WHERE b.edit_id = e.id AND b.beat = ?)")
        args.append(beat)
    if topic:
        clauses.append("EXISTS (SELECT 1 FROM page_topics t WHERE t.wiki = e.wiki AND t.title = e.title AND t.topic = ?)")
        args.append(topic)
    if year:
        clauses.append("e.year = ?")
        args.append(str(year))
    if date_from:
        clauses.append("e.ts >= ?")
        args.append(date_from)
    if date_to:
        clauses.append("e.ts <= ?")
        args.append(date_to + ("T23:59:59Z" if len(date_to) == 10 else ""))
    if title:
        clauses.append("e.title = ?")
        args.append(title)
    return ("WHERE " + " AND ".join(clauses)) if clauses else "", args


def rows(cur) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]


EDIT_COLS = ("e.id, e.wiki, e.ts, e.title, e.display_title, e.comment, e.sizediff, e.minor, e.is_new, e.ip, "
             "e.range, e.label, e.url")


def _attach_tags(c, items: list[dict]) -> list[dict]:
    if not items:
        return items
    ids = [i["id"] for i in items]
    qm = ",".join("?" * len(ids))
    beats: dict[int, list[str]] = {}
    for r in c.execute(f"SELECT edit_id, beat FROM edit_beats WHERE edit_id IN ({qm})", ids):
        beats.setdefault(r[0], []).append(r[1])
    pages = {(i["wiki"], i["title"]) for i in items}
    tmap: dict[tuple, list[str]] = {}
    for w, t in pages:
        tmap[(w, t)] = [r[0] for r in c.execute(
            "SELECT topic FROM page_topics WHERE wiki=? AND title=? ORDER BY score DESC", (w, t))]
    for i in items:
        i["beats"] = beats.get(i["id"], [])
        i["topics"] = tmap.get((i["wiki"], i["title"]), [])
    return items


SORTS = {"newest": "e.ts DESC", "oldest": "e.ts ASC", "largest": "ABS(COALESCE(e.sizediff,0)) DESC"}


# --- API --------------------------------------------------------------------

@app.get("/api/meta")
def api_meta():
    c = conn()
    meta = {r["key"]: r["value"] for r in c.execute("SELECT key, value FROM meta")}
    out = {
        "version": __version__,
        "built": meta.get("built"),
        "edits": c.execute("SELECT COUNT(*) FROM edits").fetchone()[0],
        "ips": c.execute("SELECT COUNT(DISTINCT ip) FROM edits").fetchone()[0],
        "pages": c.execute("SELECT COUNT(DISTINCT wiki||'|'||title) FROM edits").fetchone()[0],
        "first": c.execute("SELECT MIN(ts) FROM edits").fetchone()[0],
        "last": c.execute("SELECT MAX(ts) FROM edits").fetchone()[0],
        "years": rows(c.execute("SELECT year, COUNT(*) n FROM edits GROUP BY year ORDER BY year")),
        "wikis": rows(c.execute("SELECT wiki, COUNT(*) n FROM edits GROUP BY wiki ORDER BY n DESC")),
        "labels": rows(c.execute("SELECT label, COUNT(*) n FROM edits GROUP BY label ORDER BY n DESC")),
        "beats": rows(c.execute("SELECT beat, COUNT(*) n FROM edit_beats GROUP BY beat ORDER BY n DESC")),
        "months": rows(c.execute("SELECT month, COUNT(*) n FROM edits GROUP BY month ORDER BY month")),
        "provenance": {
            "chain_length": int(meta.get("chain_length") or 0),
            "latest": json.loads(meta["latest_manifest"]) if meta.get("latest_manifest") else None,
            "verified": STATE["verified"],
            "problems": STATE["problems"],
            "last_sync": STATE["last_sync"],
            "sync_error": STATE["sync_error"],
            "sync_enabled": bool(SYNC_URL),
        },
    }
    return out


@app.get("/api/search")
def api_search(q: str | None = None, wiki: str | None = None, label: str | None = None,
               ip: str | None = None, beat: str | None = None, topic: str | None = None,
               year: str | None = None, date_from: str | None = Query(None, alias="from"),
               date_to: str | None = Query(None, alias="to"), title: str | None = None,
               sort: str = "newest", limit: int = Query(50, ge=1, le=500),
               offset: int = Query(0, ge=0), facets: bool = True):
    f = dict(q=q, wiki=wiki, label=label, ip=ip, beat=beat, topic=topic, year=year,
             date_from=date_from, date_to=date_to, title=title)
    w, a = where(**f)
    c = conn()
    total = c.execute(f"SELECT COUNT(*) FROM edits e {w}", a).fetchone()[0]
    items = rows(c.execute(
        f"SELECT {EDIT_COLS} FROM edits e {w} ORDER BY {SORTS.get(sort, SORTS['newest'])} "
        f"LIMIT ? OFFSET ?", a + [limit, offset]))
    out = {"total": total, "offset": offset, "limit": limit, "items": _attach_tags(c, items)}
    if facets:
        out["facets"] = {
            "wiki": rows(c.execute(f"SELECT e.wiki k, COUNT(*) n FROM edits e {w} GROUP BY k ORDER BY n DESC", a)),
            "label": rows(c.execute(f"SELECT e.label k, COUNT(*) n FROM edits e {w} GROUP BY k ORDER BY n DESC LIMIT 25", a)),
            "year": rows(c.execute(f"SELECT e.year k, COUNT(*) n FROM edits e {w} GROUP BY k ORDER BY k", a)),
            "beat": rows(c.execute(f"SELECT b.beat k, COUNT(*) n FROM edits e JOIN edit_beats b ON b.edit_id = e.id {w} GROUP BY k ORDER BY n DESC", a)),
            "topic": rows(c.execute(f"SELECT t.topic k, COUNT(*) n FROM edits e JOIN page_topics t ON t.wiki = e.wiki AND t.title = e.title {w} GROUP BY k ORDER BY n DESC LIMIT 20", a)),
            "ip": rows(c.execute(f"SELECT e.ip k, COUNT(*) n FROM edits e {w} GROUP BY k ORDER BY n DESC LIMIT 15", a)),
            "month": rows(c.execute(f"SELECT e.month k, COUNT(*) n FROM edits e {w} GROUP BY k ORDER BY k", a)),
        }
    return out


@app.get("/api/export.csv")
def api_export(q: str | None = None, wiki: str | None = None, label: str | None = None,
               ip: str | None = None, beat: str | None = None, topic: str | None = None,
               year: str | None = None, date_from: str | None = Query(None, alias="from"),
               date_to: str | None = Query(None, alias="to"), title: str | None = None):
    w, a = where(q=q, wiki=wiki, label=label, ip=ip, beat=beat, topic=topic, year=year,
                 date_from=date_from, date_to=date_to, title=title)
    c = conn()
    cur = c.execute(f"SELECT e.ts, e.wiki, e.title, e.label, e.ip, e.range, e.sizediff, "
                    f"e.comment, e.url, e.revid FROM edits e {w} ORDER BY e.ts", a)
    buf = io.StringIO()
    wr = csv.writer(buf)
    wr.writerow(["timestamp", "wiki", "title", "label", "ip", "range", "sizediff", "comment",
                 "url", "revid"])
    wr.writerows(tuple(r) for r in cur)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=wikissc-edits.csv"})


def _top_list(c, sql: str, args: list) -> dict[str, list]:
    out: dict[str, list] = {}
    for r in c.execute(sql, args):
        out.setdefault(r[0], []).append({"k": r[1], "n": r[2]})
    return out


@app.get("/api/ips")
def api_ips(q: str | None = None, label: str | None = None, beat: str | None = None,
            topic: str | None = None, year: str | None = None, wiki: str | None = None,
            ip: str | None = None, sort: str = "edits", limit: int = Query(100, le=500),
            offset: int = Query(0, ge=0)):
    w, a = where(q=q, label=label, beat=beat, topic=topic, year=year, wiki=wiki, ip=ip)
    order = {"edits": "edits DESC", "recent": "last DESC", "pages": "pages DESC",
             "first": "first ASC"}.get(sort, "edits DESC")
    c = conn()
    total = c.execute(f"SELECT COUNT(DISTINCT e.ip) FROM edits e {w}", a).fetchone()[0]
    items = rows(c.execute(
        f"SELECT e.ip, MAX(e.label) label, MAX(e.range) range, COUNT(*) edits, "
        f"COUNT(DISTINCT e.wiki||'|'||e.title) pages, MIN(e.ts) first, MAX(e.ts) last, "
        f"GROUP_CONCAT(DISTINCT e.wiki) wikis FROM edits e {w} GROUP BY e.ip "
        f"ORDER BY {order} LIMIT ? OFFSET ?", a + [limit, offset]))
    if items:
        ips = [i["ip"] for i in items]
        qm = ",".join("?" * len(ips))
        beats = _top_list(c, f"SELECT e.ip, b.beat, COUNT(*) n FROM edits e JOIN edit_beats b "
                             f"ON b.edit_id = e.id WHERE e.ip IN ({qm}) GROUP BY e.ip, b.beat "
                             f"ORDER BY e.ip, n DESC", ips)
        tops = _top_list(c, f"SELECT e.ip, COALESCE(MAX(e.display_title), e.title), COUNT(*) n FROM edits e WHERE e.ip IN ({qm}) "
                            f"GROUP BY e.ip, e.title ORDER BY e.ip, n DESC", ips)
        for i in items:
            i["beats"] = beats.get(i["ip"], [])[:4]
            i["top_pages"] = tops.get(i["ip"], [])[:3]
    return {"total": total, "items": items}


@app.get("/api/ips/{ip}")
def api_ip(ip: str):
    try:
        ipaddress.ip_address(ip)
    except ValueError:
        raise HTTPException(400, "not an IP address")
    c = conn()
    s = c.execute("SELECT COUNT(*) edits, COUNT(DISTINCT wiki||'|'||title) pages, MIN(ts) first, "
                  "MAX(ts) last, MAX(label) label, MAX(range) range, MAX(label_source) label_source "
                  "FROM edits WHERE ip = ?", (ip,)).fetchone()
    if not s["edits"]:
        raise HTTPException(404, "no edits from this address")
    out = dict(s)
    out["ip"] = ip
    out["by_month"] = rows(c.execute("SELECT month k, COUNT(*) n FROM edits WHERE ip=? GROUP BY k ORDER BY k", (ip,)))
    out["by_hour"] = rows(c.execute("SELECT hour k, COUNT(*) n FROM edits WHERE ip=? GROUP BY k ORDER BY k", (ip,)))
    out["by_weekday"] = rows(c.execute("SELECT weekday k, COUNT(*) n FROM edits WHERE ip=? GROUP BY k ORDER BY k", (ip,)))
    out["wikis"] = rows(c.execute("SELECT wiki k, COUNT(*) n FROM edits WHERE ip=? GROUP BY k ORDER BY n DESC", (ip,)))
    out["beats"] = rows(c.execute("SELECT b.beat k, COUNT(*) n FROM edits e JOIN edit_beats b ON b.edit_id=e.id WHERE e.ip=? GROUP BY k ORDER BY n DESC", (ip,)))
    out["topics"] = rows(c.execute("SELECT t.topic k, COUNT(*) n FROM edits e JOIN page_topics t ON t.wiki=e.wiki AND t.title=e.title WHERE e.ip=? GROUP BY k ORDER BY n DESC LIMIT 15", (ip,)))
    out["top_pages"] = rows(c.execute("SELECT wiki, title, MAX(display_title) display_title, COUNT(*) n, MAX(ts) last FROM edits WHERE ip=? GROUP BY wiki, title ORDER BY n DESC LIMIT 40", (ip,)))
    # Other addresses that edited the same pages: shared users or shared gateways.
    out["neighbours"] = rows(c.execute(
        "SELECT e2.ip k, COUNT(DISTINCT e2.wiki||'|'||e2.title) n FROM edits e1 JOIN edits e2 "
        "ON e1.wiki=e2.wiki AND e1.title=e2.title AND e2.ip != e1.ip WHERE e1.ip=? "
        "GROUP BY e2.ip ORDER BY n DESC LIMIT 10", (ip,)))
    return out


@app.get("/api/topics")
def api_topics(kind: str = "beat", year: str | None = None, label: str | None = None):
    w, a = where(year=year, label=label)
    c = conn()
    if kind == "beat":
        sql = (f"SELECT b.beat k, COUNT(*) n, COUNT(DISTINCT e.ip) ips, "
               f"COUNT(DISTINCT e.wiki||'|'||e.title) pages, MAX(e.ts) last "
               f"FROM edits e JOIN edit_beats b ON b.edit_id=e.id {w} GROUP BY k ORDER BY n DESC")
    else:
        sql = (f"SELECT t.topic k, COUNT(*) n, COUNT(DISTINCT e.ip) ips, "
               f"COUNT(DISTINCT e.wiki||'|'||e.title) pages, MAX(e.ts) last "
               f"FROM edits e JOIN page_topics t ON t.wiki=e.wiki AND t.title=e.title {w} "
               f"GROUP BY k ORDER BY n DESC")
    items = rows(c.execute(sql, a))
    return {"kind": kind, "items": items}


@app.get("/api/topics/{kind}/{name:path}")
def api_topic(kind: str, name: str):
    if kind not in ("beat", "topic"):
        raise HTTPException(404)
    w, a = where(beat=name) if kind == "beat" else where(topic=name)
    c = conn()
    total = c.execute(f"SELECT COUNT(*) FROM edits e {w}", a).fetchone()[0]
    if not total:
        raise HTTPException(404, "no edits for this topic")
    return {
        "kind": kind, "name": name, "edits": total,
        "ips": rows(c.execute(f"SELECT e.ip k, MAX(e.label) label, COUNT(*) n FROM edits e {w} GROUP BY k ORDER BY n DESC LIMIT 20", a)),
        "labels": rows(c.execute(f"SELECT e.label k, COUNT(*) n FROM edits e {w} GROUP BY k ORDER BY n DESC LIMIT 12", a)),
        "pages": rows(c.execute(f"SELECT e.wiki, e.title, MAX(e.display_title) display_title, COUNT(*) n, MAX(e.ts) last FROM edits e {w} GROUP BY e.wiki, e.title ORDER BY n DESC LIMIT 30", a)),
        "by_month": rows(c.execute(f"SELECT e.month k, COUNT(*) n FROM edits e {w} GROUP BY k ORDER BY k", a)),
    }


@app.get("/api/ranges")
def api_ranges(year: str | None = None):
    w, a = where(year=year)
    c = conn()
    items = rows(c.execute(
        f"SELECT e.label k, COUNT(*) n, COUNT(DISTINCT e.ip) ips, COUNT(DISTINCT e.wiki||'|'||e.title) pages, "
        f"MIN(e.ts) first, MAX(e.ts) last, MAX(e.label_source) source FROM edits e {w} GROUP BY k ORDER BY n DESC", a))
    beats = _top_list(c, f"SELECT e.label, b.beat, COUNT(*) n FROM edits e JOIN edit_beats b ON b.edit_id=e.id {w} "
                         f"GROUP BY e.label, b.beat ORDER BY e.label, n DESC", a)
    for i in items:
        i["beats"] = beats.get(i["k"], [])[:4]
    return {"items": items}


def _ots_status(path: Path) -> str:
    if not path.exists():
        return "missing"
    blob = path.read_bytes()
    if OTS_BITCOIN in blob:
        return "bitcoin"
    if OTS_PENDING in blob:
        return "pending"
    return "unknown"


@app.get("/api/provenance")
def api_provenance(limit: int = Query(100, le=1000)):
    root = STATE["data_dir"].parent
    links = provenance.chain_links(root)
    out = []
    for link in reversed(links[-limit:]):
        mpath = root / link["manifest"]
        m = json.loads(mpath.read_text()) if mpath.exists() else {}
        ots = mpath.with_suffix(".json.ots")
        out.append({
            **link,
            "started": m.get("started"),
            "tool_version": m.get("tool_version"),
            "code_commit": m.get("code_commit"),
            "workflow_run": m.get("workflow_run"),
            "params": m.get("params"),
            "counts": m.get("counts"),
            "responses": {k: v for k, v in (m.get("responses") or {}).items() if k != "log"},
            "outputs": len(m.get("outputs") or {}),
            "ots": _ots_status(ots),
            "ots_file": link["manifest"] + ".ots",
        })
    return {"verified": STATE["verified"], "problems": STATE["problems"],
            "chain_length": len(links), "items": out}


@app.get("/api/provenance/file/{path:path}")
def api_provenance_file(path: str):
    """Serve a manifest, response log or .ots proof for independent verification."""
    root = STATE["data_dir"].parent
    base = (root / "data" / "manifests").resolve()
    p = (root / path).resolve()
    if base not in p.parents or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p, filename=p.name)


@app.get("/api/health")
def api_health():
    return {"ok": True, "version": __version__, "verified": STATE["verified"]}


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/{full_path:path}", include_in_schema=False)
def spa(full_path: str):
    if full_path.startswith("api/"):
        return JSONResponse({"detail": "not found"}, status_code=404)
    return FileResponse(STATIC / "index.html")
