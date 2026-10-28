# wikisscbot
watching you watching me watching SSC

A searchable, tamper-evident archive of anonymous Wikipedia edits made from Government of Canada networks, rebuilt from the @gccaedits idea for the post-2025 internet.

**Version 1.2.0** · [Changelog](CHANGELOG.md) · [Roadmap](ROADMAP.md) · [Build log](data/BUILDLOG.md)

## What it does

1. **Collects.** For every Government of Canada IP block, the collector asks the MediaWiki API for all logged-out edits in a time window (`list=usercontribs&uciprange=`). It covers English and French Wikipedia, Wikidata, Commons, English Wiktionary and English Wikivoyage, for each year in `config/settings.json` (currently 2021 through 2025).
2. **Labels.** Each address is labelled by the most specific matching range. The ranges are the public-domain @gccaedits department list, plus every prefix Shared Services Canada's networks (AS2665, AS2675, AS2682, AS25689, AS806) announced in the period, pulled from RIPEstat.
3. **Classifies.** Pages get Wikimedia's own article topics (Lift Wing `outlink-topic-model`) and keyword "beats" tuned for Canadian government reporting. Wikidata items get readable labels.
4. **Proves.** Every pull writes a manifest with SHA-256 hashes of each API response and data file. Manifests are hash-chained and timestamped in Bitcoin with OpenTimestamps. See [Provenance](#provenance).
5. **Serves.** A web app (`web/`) searches and aggregates everything by address, topic and range.

### Why the old bots died

- Twitter suspended @CongressEdits in 2018, then paywalled its API. botsin.space, the Mastodon host most bots moved to, shut down in March 2025.
- The department range list is from 2014–16. Most federal traffic now exits through shared SSC address space.
- On **4 November 2025** English Wikipedia replaced public IP attribution with temporary accounts, and other wikis followed. Later logged-out edits are not public. This project never tries to reveal them.

## Provenance

Each data pull (backfill, enrich, refresh-ranges, daily watch) writes:

| File | Contents |
|---|---|
| `data/manifests/<run>.json` | Code commit, Actions run URL, parameters, SHA-256 of every config and output file, rolling hash over all API responses, SHA-256 of the previous manifest |
| `data/manifests/<run>.responses.jsonl` | Every request URL with the SHA-256 and size of the bytes received |
| `data/manifests/<run>.json.ots` | OpenTimestamps proof anchoring the manifest hash in Bitcoin |
| `data/manifests/chain.jsonl` | The hash chain, one link per run |
| `data/SHA256SUMS` | Current hash of every data file |
| `data/BUILDLOG.md` | Human-readable run log |

To verify:

```
python -m wikisscbot verify        # walks the chain, rechecks every hash
sha256sum -c data/SHA256SUMS       # same file check with standard tools
pip install opentimestamps-client
ots verify data/manifests/<run>.json.ots
```

Proofs start as *pending*. The daily workflow upgrades them to a Bitcoin attestation once the calendar transaction confirms, usually within hours. CI runs `verify` before and after every collection, and the web app verifies data before loading it.

## Web app

FastAPI and SQLite (FTS5), with a frontend that needs no build step. It runs as a single process from the repo.

- **Search**: titles, Wikidata labels, edit summaries, IPs and range labels. Search ignores accents, so "elections" matches "élections". Filter by wiki, year, beat, Wikimedia topic, range, IP or CIDR (e.g. `205.193.239.0/24`) and dates. Results export to CSV.
- **Addresses**: every IP with its edit count, pages, first and last edit, main beats and top pages. Each address has a profile with edits per month, hour of day (UTC or Eastern), weekday, beats, topics, top pages, and other addresses that edited the same pages.
- **Topics**: beats and Wikimedia topics, each with top addresses, ranges, pages and a timeline.
- **Ranges**: edits grouped by department or SSC label.
- **Provenance**: the manifest chain, hashes, Bitcoin timestamp status and downloadable proofs.

API endpoints live under `/api/`: `meta`, `search`, `export.csv`, `ips`, `ips/{ip}`, `topics`, `topics/{beat|topic}/{name}`, `ranges`, `provenance` and `health`.

### Deploy on a static host (no server)

`docs/` is a complete static build of the site, rebuilt and committed by every workflow run. Publish that folder on any static host (Netlify, Cloudflare Pages, GitHub Pages, S3, shared hosting) and point your domain at it:

- **Publish directory:** `docs`
- **Build command:** none (or `python -m wikisscbot site` to rebuild it yourself)
- **GitHub Pages:** Settings → Pages → Deploy from a branch → `main` / `/docs`

The browser loads the raw data files and does all searching and aggregation itself. It hashes every file it loads with SHA-256 and checks it against `data/SHA256SUMS` and the latest signed manifest; the Provenance page can also walk the full hash chain in the browser. The site updates whenever the repo does, if your host redeploys on push.

### Deploy with the Python server

Any platform that runs a Python web process works: Heroku-style buildpacks (Procfile), Docker, Render, Railway, Fly.io, Scalingo or a plain VPS.

- **Buildpack / Procfile hosts:** connect the repo; it installs `requirements.txt` and runs the `Procfile` web process on `$PORT`.
- **Docker:** `docker build -t wikisscbot . && docker run -p 8000:8000 wikisscbot`
- **VPS:** `pip install -r requirements.txt && uvicorn web.app:app --host 0.0.0.0 --port 8000` behind nginx or Caddy for TLS.

Then point your domain's DNS (a CNAME or A record) at the host.

Environment variables (all optional):

| Variable | Purpose |
|---|---|
| `WIKISSC_SYNC_URL` | Keep the site current without redeploying. Set to `https://codeload.github.com/primerogueinc/wikisscbot/tar.gz/refs/heads/main`; the app downloads the repo every `WIKISSC_SYNC_HOURS`, **verifies the chain and checksums**, and swaps in the data only if they pass |
| `WIKISSC_SYNC_HOURS` | Sync interval, default 6 |
| `WIKISSC_DATA_DIR` | Data directory, default `./data` |
| `WIKISSC_DB` | SQLite path, default `web/wikissc.db` (rebuilt at startup; an ephemeral disk is fine) |

Run a single process (no `--workers`). The database is small and rebuilt on start.

## Automation (GitHub Actions)

- **Backfill year** runs on every push to the collector code or config, or manually from the Actions tab for one year. It tests, verifies, refreshes SSC prefixes, collects, classifies, verifies again, timestamps the new manifests and commits.
- **Watch for new edits** runs daily. It checks the last 30 hours, writes a signed manifest even when nothing is found (proving the check ran), upgrades pending timestamps and commits. To post findings, add the secrets `MASTODON_INSTANCE` + `MASTODON_TOKEN` and/or `BLUESKY_HANDLE` + `BLUESKY_APP_PASSWORD`.

## Command line

```
python -m wikisscbot refresh-ranges     # SSC prefixes from RIPEstat
python -m wikisscbot backfill           # all configured years (or --year 2024)
python -m wikisscbot enrich             # Wikimedia topics + Wikidata labels
python -m wikisscbot report             # rebuild CSV/summary/static pages
python -m wikisscbot watch --hours 30
python -m wikisscbot verify
python -m wikisscbot site               # rebuild docs/ static site
python -m unittest discover -s tests
```

The collector uses only the Python standard library. The web app needs `requirements.txt`, and the tests also use `requirements-dev.txt`.

## Outputs

| Path | What |
|---|---|
| `data/<year>/edits.csv`, `edits.jsonl` | Every edit: time, wiki, page, range label, IP, byte change, summary, diff link |
| `data/<year>/summary.json`, `status.json` | Aggregates; per-wiki scan status and errors |
| `data/topics.json` | Wikimedia topics per page, Wikidata labels |
| `data/live/edits.jsonl` | Daily watcher findings |
| `docs/` | Static build of the full site (publish this folder) |
| `docs/reports/<year>.html` | Single-file report per year |

## Reading the labels

"Shared Services Canada" or "Shared Services Canada network (ASxxxx)" means the edit came through shared SSC infrastructure, so **the department cannot be determined**. Department labels come from a decade-old crowd-sourced list and are leads, not attribution. An edit from a government network is not evidence of who made it, or that it was made in an official capacity.

## Credits

Department ranges: [ruebot/gccaedits-ip-address-ranges](https://github.com/ruebot/gccaedits-ip-address-ranges) (public domain). Concept: Ed Summers' [anon](https://github.com/edsu/anon). BGP data: [RIPEstat](https://stat.ripe.net/). Topics: Wikimedia Lift Wing. Timestamps: [OpenTimestamps](https://opentimestamps.org/). A Prime Rogue Inc. project.
