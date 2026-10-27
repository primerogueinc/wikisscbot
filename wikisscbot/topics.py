"""Topic labels for edited pages.

Two layers:

* Wikimedia topics - Wikimedia's own language-agnostic article-topic model
  (Lift Wing "outlink-topic-model"), e.g. "History and Society: Military and
  warfare". Fetched once per page and cached in data/topics.json.
* Beats - keyword rules tuned for Canadian government reporting
  (Canadian military, Elections, Federal government, ...). Computed locally
  from the title and edit summary, so they can be changed without refetching.
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

from .config import ROOT, settings
from .http import ApiError, get_json, post_json

CACHE = ROOT / "data" / "topics.json"
LIFTWING = "https://api.wikimedia.org/service/lw/inference/v1/models/outlink-topic-model:predict"
THRESHOLD = 0.5


def pretty_topic(raw: str) -> str:
    """'History_and_Society.Military_and_warfare' -> 'History and Society: Military and warfare'."""
    parts = [p.replace("_", " ").rstrip("*") for p in raw.split(".")]
    if len(parts) == 1:
        return parts[0]
    return f"{parts[0]}: {parts[-1]}"


def load_cache() -> dict:
    if CACHE.exists():
        return json.loads(CACHE.read_text())
    return {"model": "outlink-topic-model", "threshold": THRESHOLD, "pages": {}, "labels": {}}


def page_key(wiki: str, title: str) -> str:
    return f"{wiki}|{title}"


def fetch_topics(lang: str, title: str) -> list[dict]:
    data = post_json(LIFTWING, {"page_title": title.replace(" ", "_"), "lang": lang,
                                "threshold": THRESHOLD})
    results = (data.get("prediction") or {}).get("results") or []
    out = []
    for r in results:
        score = float(r.get("score", 0))
        if score >= THRESHOLD:
            out.append({"topic": pretty_topic(r["topic"]), "score": round(score, 3)})
    return sorted(out, key=lambda r: -r["score"])


def enrich(edits: list[dict], log=sys.stderr, delay: float = 0.1) -> dict:
    """Fetch Wikimedia topics for every (wiki, title) not yet cached."""
    cache = load_cache()
    pages = cache["pages"]
    langs = {w["id"]: w["lang"] for w in settings()["wikis"] if w["host"].endswith("wikipedia.org")}
    todo = sorted({(e["wiki"], e["title"]) for e in edits
                   if e["wiki"] in langs and e.get("ns", 0) == 0
                   and page_key(e["wiki"], e["title"]) not in pages})
    print(f"topics: {len(todo)} pages to classify ({len(pages)} cached)", file=log)
    done = failed = 0
    for wiki, title in todo:
        try:
            pages[page_key(wiki, title)] = fetch_topics(langs[wiki], title)
            done += 1
        except (ApiError, ValueError, KeyError) as ex:
            failed += 1
            if failed <= 5:
                print(f"  topic lookup failed for {wiki}:{title}: {ex}", file=log)
        if (done + failed) % 200 == 0:
            print(f"  {done + failed}/{len(todo)}", file=log)
            CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1, sort_keys=True))
        time.sleep(delay)
    labelled = label_wikidata(edits, cache, log)
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1, sort_keys=True))
    return {"classified": done, "failed": failed, "cached_total": len(pages),
            "wikidata_labelled": labelled}


WIKIDATA_API = "https://www.wikidata.org/w/api.php"
_ENTITY = re.compile(r"^(?:Property:|Lexeme:)?([QPL]\d+)$")


def label_wikidata(edits: list[dict], cache: dict, log=sys.stderr) -> int:
    """Fetch English/French labels for Wikidata item titles like 'Q12345'."""
    labels = cache.setdefault("labels", {})
    ids = sorted({m.group(1) for e in edits if e["wiki"] == "wikidatawiki"
                  for m in [_ENTITY.match(e["title"])] if m
                  and page_key("wikidatawiki", e["title"]) not in labels})
    titles = {}
    for e in edits:
        m = _ENTITY.match(e["title"]) if e["wiki"] == "wikidatawiki" else None
        if m:
            titles.setdefault(m.group(1), set()).add(e["title"])
    n = 0
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        try:
            data = get_json(WIKIDATA_API, {"action": "wbgetentities", "ids": "|".join(chunk),
                                           "props": "labels|descriptions", "languages": "en|fr",
                                           "format": "json"})
        except ApiError as ex:
            print(f"  wikidata labels failed: {ex}", file=log)
            continue
        for qid, ent in (data.get("entities") or {}).items():
            lab = ent.get("labels", {})
            desc = ent.get("descriptions", {})
            text = (lab.get("en") or lab.get("fr") or {}).get("value")
            d = (desc.get("en") or desc.get("fr") or {}).get("value")
            if text:
                for t in titles.get(qid, {qid}):
                    labels[page_key("wikidatawiki", t)] = f"{text} ({d})" if d else text
                n += 1
        time.sleep(0.2)
    return n


# --- Beats -----------------------------------------------------------------

BEATS: list[tuple[str, str]] = [
    ("Canadian military",
     r"\bregiment|battalion|brigade|division support|\bcfb\b|\bcfd\b|\bhmcs\b|royal canadian (navy|air force)|"
     r"canadian (forces|army|armed forces)|forces canadiennes|squadron|escadron|hussars|fusiliers|"
     r"foot guards|rangers\b|military police|police militaire|cadet|armou?ry|combat engineer|"
     r"field artillery|national defence|défense nationale|chief of the defence|griffon|"
     r"joint rescue|signal regiment|petty officer|queen's regulations|king's regulations|"
     r"national investigation service|dewolf|kingston-class|halifax-class|scottish\b|highlanders"),
    ("Elections & parties",
     r"election|élection|electoral|circonscription|riding\b|leadership race|course à la direction|"
     r"liberal party|conservative party|new democratic|\bndp\b|bloc québécois|parti |"
     r"opinion polling|sondage|candidat|referendum|référendum"),
    ("Federal government & agencies",
     r"government of canada|gouvernement du canada|agency|agence |commission\b|commissioner|"
     r"privy council|treasury board|shared services|service canada|public health agency|"
     r"border services|parliamentary budget|statistics canada|department of|ministère|"
     r"office of the prime minister|cabinet\b|conseil exécutif|minister\b|ministre\b|"
     r"high commission|embassy|ambassad"),
    ("Politicians & public figures", r"\(politician\)|\(homme politique\)|\(femme politique\)|premier of|"
     r"prime minister|premier ministre|senator|sénateur|member of parliament|mayor\b|maire\b"),
    ("Canada–US & foreign affairs",
     r"51st state|annexation|annexion|tariff|united states|états-unis|trump|nato|otan|"
     r"ukraine|russia|china|chine|israel|gaza|iran|india\b|foreign interference|ingérence"),
    ("Security & intelligence",
     r"intelligence|renseignement|csis|scrs|communications security|\bcse\b|classified|"
     r"espionage|surveillance|cyber|\brcmp\b|\bgrc\b|police\b|terror"),
    ("Weather & environment",
     r"weather|météo|climate|climat|storm|tempête|wildfire|feu de forêt|flood|inondation|"
     r"environment canada|tornado|hurricane|temperature"),
    ("Sports", r"hockey|senators|canucks|maple leafs|nhl\b|lnh\b|football|soccer|curling|"
     r"olympi|baseball|basketball|tennis|season\b|saison\b|roster|all-star"),
    ("Indigenous", r"first nation|premières nations|indigenous|autochtone|cree\b|inuit|métis|"
     r"anishinaabe|ojibwe|mohawk"),
    ("Science & health", r"health|santé|research council|research|recherche|disease|medical|"
     r"hospital|vaccine|virus|space agency|astronom|biolog|chemi|physics"),
]
_BEATS = [(name, re.compile(rx, re.I)) for name, rx in BEATS]


def beats(title: str, comment: str = "") -> list[str]:
    text = f"{title} {comment}"
    return [name for name, rx in _BEATS if rx.search(text)]


def wikimedia_topics(cache: dict, wiki: str, title: str) -> list[str]:
    return [t["topic"] for t in cache.get("pages", {}).get(page_key(wiki, title), [])]


def all_edits(root: Path = ROOT) -> list[dict]:
    out = []
    for f in sorted((root / "data").glob("[0-9][0-9][0-9][0-9]/edits.jsonl")):
        with f.open(encoding="utf-8") as fh:
            out.extend(json.loads(line) for line in fh if line.strip())
    return out
