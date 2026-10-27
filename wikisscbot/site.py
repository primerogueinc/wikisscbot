"""Build the static website into docs/ (servable by any static host or GitHub Pages).

The static site runs the same front end as the web app, with an in-browser
data engine (web/static/engine.js). Raw data files are copied byte-for-byte
so the browser can check their SHA-256 against data/SHA256SUMS and the
signed manifests.
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
from pathlib import Path

from . import __version__, provenance, topics
from .config import ROOT

OUT = ROOT / "docs"
WEB = ROOT / "web" / "static"
OTS_BITCOIN = bytes.fromhex("0588960d73d71901")
OTS_PENDING = bytes.fromhex("83dfe30d2ef90c8e")


def _ots_status(path: Path) -> str:
    if not path.exists():
        return "missing"
    blob = path.read_bytes()
    return "bitcoin" if OTS_BITCOIN in blob else "pending" if OTS_PENDING in blob else "unknown"


def _copy(src: Path, out: Path) -> str:
    rel = src.resolve().relative_to(ROOT).as_posix()
    dest = out / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)
    return rel


def build(out: Path = OUT) -> dict:
    data = ROOT / "data"
    # Fresh copy of the data each build; keep docs/reports and anything else.
    shutil.rmtree(out / "data", ignore_errors=True)
    shutil.rmtree(out / "static", ignore_errors=True)
    out.mkdir(parents=True, exist_ok=True)

    files = []
    for f in sorted(data.glob("[0-9][0-9][0-9][0-9]/edits.jsonl")):
        files.append(_copy(f, out))
    live = data / "live" / "edits.jsonl"
    if live.exists():
        files.append(_copy(live, out))
    if (data / "topics.json").exists():
        files.append(_copy(data / "topics.json", out))
    for extra in ("SHA256SUMS", "BUILDLOG.md"):
        if (data / extra).exists():
            _copy(data / extra, out)
    manifests = data / "manifests"
    if manifests.exists():
        for f in sorted(manifests.iterdir()):
            if f.is_file():
                _copy(f, out)

    # Beats are computed here (Python rules) so the browser doesn't need them.
    cache = topics.load_cache()
    labels = cache.get("labels", {})
    beats = {}
    n = 0
    for rel in files:
        if not rel.endswith(".jsonl"):
            continue
        for line in (ROOT / rel).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            e = json.loads(line)
            n += 1
            b = topics.beats(labels.get(f"{e['wiki']}|{e['title']}") or e["title"], e.get("comment") or "")
            if b:
                beats[f"{e['wiki']}|{e['revid']}"] = b

    links = provenance.chain_links(ROOT)
    items = []
    for link in reversed(links[-200:]):
        mpath = ROOT / link["manifest"]
        m = json.loads(mpath.read_text()) if mpath.exists() else {}
        items.append({
            **link, "started": m.get("started"), "tool_version": m.get("tool_version"),
            "code_commit": m.get("code_commit"), "workflow_run": m.get("workflow_run"),
            "params": m.get("params"), "counts": m.get("counts"),
            "responses": {k: v for k, v in (m.get("responses") or {}).items() if k != "log"},
            "outputs": len(m.get("outputs") or {}),
            "ots": _ots_status(mpath.with_suffix(".json.ots")),
            "ots_file": link["manifest"] + ".ots",
        })
    derived = {
        "version": __version__,
        "built": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "files": files,
        "beats": beats,
        "provenance": {"chain_length": len(links), "latest": links[-1] if links else None,
                       "items": items},
    }
    (out / "derived.json").write_text(json.dumps(derived, ensure_ascii=False, separators=(",", ":")))

    # Front end: same files as the web app, switched to the in-browser engine.
    (out / "static").mkdir(parents=True, exist_ok=True)
    for name in ("app.js", "engine.js", "style.css"):
        shutil.copyfile(WEB / name, out / "static" / name)
    index = (WEB / "index.html").read_text(encoding="utf-8").replace(
        "<!--STATIC-->",
        '<script>window.WIKISSC_STATIC = true;</script>\n<script src="static/engine.js"></script>')
    (out / "index.html").write_text(index, encoding="utf-8")
    (out / "404.html").write_text(index, encoding="utf-8")
    (out / ".nojekyll").write_text("")
    return {"edits": n, "files": len(files), "manifests": len(links), "out": str(out)}
