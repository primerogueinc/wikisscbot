# wikisscbot
watching you watching me watching SSC

An archive of anonymous Wikipedia edits made from Government of Canada networks, rebuilt from the @gccaedits idea for the post-2025 internet.

## Why the old bots died, and what this does instead

@gccaedits and its siblings watched Wikipedia's live feed for logged-out edits from government IP ranges and posted them. Three things broke that model:

1. Twitter suspended the template bot (@CongressEdits) in 2018 and later paywalled its API; the Mastodon host most of the bots moved to (botsin.space) shut down in March 2025.
2. The department range list dates from 2014–2016. Shared Services Canada has since folded most departmental networks into its own address space and ASNs, so the old labels miss a lot of traffic.
3. On 4 November 2025 English Wikipedia replaced public IP attribution with **temporary accounts**. Logged-out edits since then no longer show an IP to the public, so no public bot can attribute them.

wikisscbot therefore works as a **historical collector** rather than a live firehose:

- It queries the MediaWiki API's IP-range contributions (`list=usercontribs&uciprange=…`) for every GC block, which returns all publicly IP-attributed edits in a time window. No live stream needed.
- It merges the old department-labelled list with every prefix announced by SSC's ASNs (AS2665, AS2675, AS2682, AS25689, AS806), pulled fresh from RIPEstat, so consolidated SSC space is covered.
- It covers English and French Wikipedia, Wikidata and Commons (edit `config/settings.json`).
- It never tries to reveal temporary-account IPs; those are restricted to Wikipedia's anti-abuse functionaries.

## Outputs

| Path | What |
|---|---|
| `data/2025/edits.csv` | Every edit: time, wiki, page, range label, IP, byte change, summary, diff link |
| `data/2025/edits.jsonl` | Same, one JSON object per line |
| `data/2025/summary.json` | Counts by range label, wiki and month; top pages; last IP-attributed edit per wiki |
| `data/2025/status.json` | Per-wiki scan status and any API errors |
| `docs/index.html` | Searchable, filterable report page |
| `data/live/edits.jsonl` | Edits found by the daily watcher |

To publish the report page: repo **Settings → Pages → Deploy from a branch → `main` / `/docs`**.

## Running

Everything runs on GitHub Actions; no server needed.

- **Backfill year** runs on every push that changes the code or config, or manually from the Actions tab (choose any year). It refreshes SSC prefixes, collects the year and commits the results.
- **Watch for new edits** runs daily and checks the last 30 hours. It will mostly find nothing on wikis that use temporary accounts; it exists to catch any wiki that still exposes IPs. To have it post, add repository secrets `MASTODON_INSTANCE` + `MASTODON_TOKEN` and/or `BLUESKY_HANDLE` + `BLUESKY_APP_PASSWORD`. The backfill never posts.

Locally (Python 3.10+, standard library only):

```
python -m wikisscbot refresh-ranges
python -m wikisscbot backfill --year 2025 --wiki enwiki
python -m wikisscbot report --year 2025
python -m unittest discover -s tests
```

## Reading the labels

Each IP gets the most specific matching range from the department list. "Shared Services Canada" or "Shared Services Canada network (ASxxxx)" means the edit came through shared SSC infrastructure and **the department cannot be determined**. Department labels come from a decade-old crowd-sourced list and should be treated as leads, not attribution. An edit from a government network is not evidence of who made it or that it was made in an official capacity.

## Credits

Department ranges: [ruebot/gccaedits-ip-address-ranges](https://github.com/ruebot/gccaedits-ip-address-ranges) (public domain). Concept: Ed Summers' [anon](https://github.com/edsu/anon). BGP data: [RIPEstat](https://stat.ripe.net/).
