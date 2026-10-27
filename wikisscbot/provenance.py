"""Tamper-evident record of every data pull.

Each run (backfill, watch, enrich, refresh-ranges) produces:

  data/manifests/<run_id>.json            the manifest
  data/manifests/<run_id>.responses.jsonl SHA-256 of every API response received
  data/manifests/<run_id>.json.ots        OpenTimestamps proof (added by the workflow)

The manifest records the code commit, workflow run, parameters, SHA-256 of
every input config file and every output data file, and a rolling hash over
all API responses. It also carries the SHA-256 of the previous manifest, so
the manifests form a hash chain: altering or deleting any past run breaks
every later link. data/manifests/chain.jsonl indexes the chain,
data/SHA256SUMS lists current data files in `sha256sum -c` format, and
data/BUILDLOG.md is a human-readable run log.

The OpenTimestamps proof anchors the manifest hash in the Bitcoin
blockchain via free public calendar servers, proving the manifest (and so
every hash inside it) existed no later than the block time.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import secrets
import subprocess
from pathlib import Path

from . import __version__, http
from .config import ROOT

DATA = ROOT / "data"
MANIFESTS = DATA / "manifests"
CHAIN = MANIFESTS / "chain.jsonl"
SUMS = DATA / "SHA256SUMS"
BUILDLOG = DATA / "BUILDLOG.md"
SCHEMA = "wikisscbot-manifest/1"
GENESIS = "0" * 64

# Files never listed in SHA256SUMS: the manifests themselves (they hash the data)
# and files rewritten after the sums are computed.
EXCLUDE_FROM_SUMS = {"SHA256SUMS", "BUILDLOG.md"}


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def rel(path: Path) -> str:
    return path.resolve().relative_to(ROOT).as_posix()


def git_commit() -> str | None:
    if os.environ.get("GITHUB_SHA"):
        return os.environ["GITHUB_SHA"]
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                              text=True, check=True).stdout.strip()
    except Exception:
        return None


def workflow_run() -> dict | None:
    rid = os.environ.get("GITHUB_RUN_ID")
    if not rid:
        return None
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    return {"id": rid, "attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
            "workflow": os.environ.get("GITHUB_WORKFLOW"),
            "url": f"{server}/{repo}/actions/runs/{rid}"}


class Recorder:
    """Hashes every HTTP response while active."""

    def __init__(self, kind: str):
        self.kind = kind
        self.started = now()
        self.run_id = f"{self.started.replace(':', '')}-{kind}-{secrets.token_hex(2)}"
        self.entries: list[dict] = []
        self.chain = hashlib.sha256(b"wikisscbot-responses/1")
        self.by_host: dict[str, dict] = {}

    def __call__(self, method: str, url: str, req_body: bytes | None, raw: bytes) -> None:
        digest = hashlib.sha256(raw).hexdigest()
        entry = {"t": now(), "method": method, "url": url, "bytes": len(raw), "sha256": digest}
        if req_body is not None:
            entry["request_sha256"] = hashlib.sha256(req_body).hexdigest()
        self.entries.append(entry)
        self.chain.update(bytes.fromhex(digest))
        host = url.split("/")[2] if "://" in url else url
        h = self.by_host.setdefault(host, {"responses": 0, "bytes": 0})
        h["responses"] += 1
        h["bytes"] += len(raw)

    def __enter__(self):
        http.RECORDER = self
        return self

    def __exit__(self, *exc):
        http.RECORDER = None
        return False


def last_link() -> dict | None:
    if not CHAIN.exists():
        return None
    lines = [line for line in CHAIN.read_text().splitlines() if line.strip()]
    return json.loads(lines[-1]) if lines else None


def data_files() -> list[Path]:
    out = []
    for p in sorted(DATA.rglob("*")):
        if not p.is_file() or p.name in EXCLUDE_FROM_SUMS:
            continue
        if MANIFESTS in p.parents:
            continue
        out.append(p)
    return out


def write_sums() -> dict[str, str]:
    sums = {rel(p): sha256_file(p) for p in data_files()}
    SUMS.write_text("".join(f"{h}  {path}\n" for path, h in sums.items()))
    return sums


def finalize(rec: Recorder, params: dict, outputs: list[Path], counts: dict,
             notes: str = "") -> dict:
    """Write the manifest, extend the chain, refresh SHA256SUMS and the build log."""
    MANIFESTS.mkdir(parents=True, exist_ok=True)
    resp_path = MANIFESTS / f"{rec.run_id}.responses.jsonl"
    resp_path.write_text("".join(json.dumps(e) + "\n" for e in rec.entries))

    sums = write_sums()
    prev = last_link()
    inputs = {rel(p): sha256_file(p) for p in sorted((ROOT / "config").glob("*.json"))}
    out_hashes = {}
    for p in outputs:
        for f in ([p] if p.is_file() else sorted(x for x in p.rglob("*") if x.is_file())):
            out_hashes[rel(f)] = sums.get(rel(f)) or sha256_file(f)

    manifest = {
        "schema": SCHEMA,
        "run_id": rec.run_id,
        "kind": rec.kind,
        "started": rec.started,
        "finished": now(),
        "tool_version": __version__,
        "code_commit": git_commit(),
        "workflow_run": workflow_run(),
        "params": params,
        "inputs": inputs,
        "responses": {
            "count": len(rec.entries),
            "bytes": sum(e["bytes"] for e in rec.entries),
            "by_host": rec.by_host,
            "chain_sha256": rec.chain.hexdigest(),
            "log": rel(resp_path),
            "log_sha256": sha256_file(resp_path),
        },
        "outputs": out_hashes,
        "data_sums_sha256": sha256_file(SUMS),
        "counts": counts,
        "previous_manifest": {"run_id": prev["run_id"], "sha256": prev["sha256"]}
        if prev else {"run_id": None, "sha256": GENESIS},
        "notes": notes,
    }
    path = MANIFESTS / f"{rec.run_id}.json"
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    digest = sha256_file(path)
    link = {"seq": (prev["seq"] + 1) if prev else 0, "run_id": rec.run_id, "kind": rec.kind,
            "finished": manifest["finished"], "manifest": rel(path), "sha256": digest,
            "previous": manifest["previous_manifest"]["sha256"]}
    with CHAIN.open("a") as f:
        f.write(json.dumps(link) + "\n")
    _append_buildlog(manifest, digest)
    return {"manifest": path, "sha256": digest, "seq": link["seq"]}


def _append_buildlog(m: dict, digest: str) -> None:
    if not BUILDLOG.exists():
        BUILDLOG.write_text(
            "# Build log\n\nOne line per data pull, newest last. Each manifest hash is "
            "chained to the previous one and timestamped with OpenTimestamps.\n\n"
            "| Finished (UTC) | Run | Kind | Responses | Counts | Manifest SHA-256 | Workflow |\n"
            "|---|---|---|---|---|---|---|\n")
    wf = m["workflow_run"]["url"] if m["workflow_run"] else "local"
    counts = ", ".join(f"{k} {v}" for k, v in m["counts"].items()) or "-"
    with BUILDLOG.open("a") as f:
        f.write(f"| {m['finished']} | `{m['run_id']}` | {m['kind']} | {m['responses']['count']} "
                f"| {counts} | `{digest}` | {wf} |\n")


def chain_links(root: Path = ROOT) -> list[dict]:
    chain = root / "data" / "manifests" / "chain.jsonl"
    if not chain.exists():
        return []
    return [json.loads(line) for line in chain.read_text().splitlines() if line.strip()]


def verify(check_ots: bool = False, root: Path = ROOT) -> list[str]:
    """Return a list of problems; empty means everything checks out."""
    problems = []
    ROOT_ = root
    CHAIN = root / "data" / "manifests" / "chain.jsonl"
    SUMS = root / "data" / "SHA256SUMS"
    # 1. Hash chain
    if CHAIN.exists():
        prev = GENESIS
        for i, line in enumerate(CHAIN.read_text().splitlines()):
            if not line.strip():
                continue
            link = json.loads(line)
            mpath = ROOT_ / link["manifest"]
            if not mpath.exists():
                problems.append(f"chain #{i}: manifest missing: {link['manifest']}")
                prev = link["sha256"]
                continue
            actual = sha256_file(mpath)
            if actual != link["sha256"]:
                problems.append(f"chain #{i}: manifest hash mismatch for {link['manifest']}")
            m = json.loads(mpath.read_text())
            if m["previous_manifest"]["sha256"] != prev or link["previous"] != prev:
                problems.append(f"chain #{i}: broken link (previous hash does not match)")
            log = ROOT_ / m["responses"]["log"]
            if log.exists() and sha256_file(log) != m["responses"]["log_sha256"]:
                problems.append(f"chain #{i}: response log altered: {m['responses']['log']}")
            if check_ots and not mpath.with_suffix(".json.ots").exists():
                problems.append(f"chain #{i}: no OpenTimestamps proof for {link['manifest']}")
            prev = link["sha256"]
    else:
        problems.append("no manifest chain yet")
    # 2. Current files against SHA256SUMS
    if SUMS.exists():
        for line in SUMS.read_text().splitlines():
            h, path = line.split("  ", 1)
            p = ROOT_ / path
            if not p.exists():
                problems.append(f"SHA256SUMS: missing file {path}")
            elif sha256_file(p) != h:
                problems.append(f"SHA256SUMS: hash mismatch {path}")
    return problems
