# Changelog

All notable changes to wikisscbot. Versions follow [semantic versioning](https://semver.org/); times are UTC. The automatic per-run record of data pulls is in [`data/BUILDLOG.md`](data/BUILDLOG.md).

## [1.2.0] — 2026-10-27

### Added
- **Provenance for every data pull.** Each network command writes a manifest (`data/manifests/<run>.json`) recording the code commit, GitHub Actions run, parameters, SHA-256 of every config file and output file, and a rolling hash over every API response. The SHA-256 of each response body is logged in `<run>.responses.jsonl`.
- **Hash chain.** Each manifest carries the SHA-256 of the previous one; `data/manifests/chain.jsonl` indexes the chain. Altering or deleting any past run breaks every later link.
- **Blockchain timestamps.** Workflows stamp each manifest with OpenTimestamps (free, Bitcoin-anchored) and upgrade pending proofs daily.
- **`data/SHA256SUMS`** for `sha256sum -c`, and **`data/BUILDLOG.md`**, a run-by-run log.
- **`python -m wikisscbot verify`**: recomputes the chain and every checksum. Runs in CI before and after each collection.
- **Topic enrichment** (`enrich`): Wikimedia's language-agnostic article-topic model (Lift Wing `outlink-topic-model`) for each edited Wikipedia page, plus English/French labels for Wikidata items. Cached in `data/topics.json`.
- **Beats**: keyword groupings tuned for Canadian government reporting (Canadian military, Elections & parties, Federal government & agencies, …).
- **Web app** (`web/`): FastAPI + SQLite FTS5 backend and a no-build frontend. Full-text search (accent-insensitive), filters by wiki, year, beat, Wikimedia topic, range, IP or CIDR and date; aggregation by address, topic and range; address profiles with hour-of-day, weekday and "addresses editing the same pages"; CSV export; provenance page. Optional self-updating from GitHub with verification before each data swap.
- Deploy files: `Procfile`, `Dockerfile`, `requirements.txt`, `.python-version`.
- **Static build** (`python -m wikisscbot site` → `docs/`): the full site with no server. An in-browser engine (`web/static/engine.js`) answers the same queries as the API, and SHA-256-verifies every data file against `SHA256SUMS` and the latest signed manifest, with an optional full chain check. Rebuilt by every workflow run. Year reports moved to `docs/reports/`.
- `CHANGELOG.md`, `ROADMAP.md`.

### Changed
- Topic enrichment is bounded: 6 parallel requests, 15 s timeouts, a 15-minute budget (`WIKISSC_ENRICH_MINUTES`) and a stop after 25 consecutive failures; unfinished pages carry over via the cache. The workflow step also has a 25-minute timeout.
- HTTP layer passes every response through the provenance recorder before parsing.
- `version` subcommand and `--version` flag.

## [1.1.0] — 2026-10-27

### Added
- Multi-year collection (`backfill_years` in `config/settings.json`, now 2024 and 2025) with a report page per year (`docs/<year>.html`).

### Fixed
- Wikidata returned nothing: read queries no longer send `maxlag`, which Wikidata applies to query-service lag.
- Address blocks that time out (HTTP 503/504) are split in half and retried down to /24 (IPv4) or /48 (IPv6).
- Department labels now always win over generic SSC BGP labels when both cover an address.

## [1.0.0] — 2026-10-27

### Added
- Rebuild of the @gccaedits idea as a historical collector: MediaWiki `list=usercontribs&uciprange=` over every Government of Canada block, for English and French Wikipedia, Wikidata and Commons.
- Range sources: the public-domain @gccaedits department list, plus prefixes announced by Shared Services Canada ASNs (AS2665, AS2675, AS2682, AS25689, AS806) from RIPEstat.
- Outputs: `edits.csv`, `edits.jsonl`, `summary.json`, `status.json`, static report page.
- GitHub Actions: backfill on push or manual dispatch; daily watcher with optional Mastodon/Bluesky posting.
- First 2025 collection: 1,818 edits (2,643 after the 1.1.0 Wikidata fix).
