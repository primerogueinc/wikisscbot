# Roadmap

Status as of 2026-10-27 (v1.2.0). Items move to the [changelog](CHANGELOG.md) when shipped.

## Now
- [x] 2025 backfill (en/fr Wikipedia, Wikidata, Commons)
- [x] 2024 backfill
- [x] 2021 backfill
- [x] 2022 backfill
- [x] 2023 backfill (overlaps the final months of @gccaedits, which stopped in April 2023)
- [x] Checksummed, hash-chained, OpenTimestamped manifests for every pull
- [x] Searchable web app with aggregation by address, topic and range
- [ ] First signed pulls committed and Bitcoin-anchored (next workflow run)
- [ ] Deploy web app to the Prime Rogue domain

## Next
- **Earlier years.** Extend `backfill_years` back to 2014 to overlap the original @gccaedits era. Each year is one config change.
- **Full diffs.** Fetch and archive the added/removed text of each edit (MediaWiki `compare`), so the archive survives revision deletion and search can cover changed text, not just titles and summaries.
- **Revision-deletion watch.** Re-check archived revisions periodically and flag any that Wikipedia has since hidden or suppressed.
- **Range attribution upgrades.** Pull ARIN reassignment records and reverse DNS for each address and record them in the manifest, with dates, so labels can be audited.
- **More wikis.** Other language Wikipedias where federal staff are likely to edit (es, de, zh, ar, uk), Wiktionary, Wikinews.
- **Data dumps fallback.** Use Wikimedia's public `mediawiki_history` dumps to cross-check API results and fill gaps if IP-range queries are ever restricted.

## Later
- Alerts for watched pages or beats (email/RSS) on new archive entries.
- Annotated story mode: pin edits, add notes and publish a permalinked collection.
- Public API keys and rate limits for researchers.
- Second, independent timestamp anchor (e.g. RFC 3161 TSA) alongside OpenTimestamps.
- ATIP helper: generate access-to-information request text for a given set of edits, ranges and dates.

## Known limits
- Logged-out edits after the temporary-accounts switch (English Wikipedia: 4 Nov 2025) are not public and are out of scope.
- Department labels rely on a 2014–16 crowd-sourced list; most current traffic exits through shared SSC gateways.
