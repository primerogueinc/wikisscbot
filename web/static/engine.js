/* In-browser data engine for the static build.
 *
 * Mirrors the JSON shapes of the FastAPI backend (web/app.py) so the same
 * front end runs with or without a server. Loads the raw data files the
 * collector committed, checks each one's SHA-256 against data/SHA256SUMS,
 * and checks SHA256SUMS against the latest signed manifest.
 */
(function () {
  "use strict";

  const BASE = "./";
  const norm = (s) => (s || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");

  async function sha256Hex(buf) {
    const d = await crypto.subtle.digest("SHA-256", buf);
    return [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, "0")).join("");
  }
  async function fetchBuf(path) {
    const r = await fetch(BASE + path, { cache: "no-cache" });
    if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
    return r.arrayBuffer();
  }
  const text = (buf) => new TextDecoder().decode(buf);
  const jsonl = (buf) => text(buf).split("\n").filter((l) => l.trim()).map((l) => JSON.parse(l));

  // --- IP / CIDR -------------------------------------------------------------
  function ipToBig(ip) {
    if (ip.includes(":")) {
      let [head, tail] = ip.split("::");
      const h = head ? head.split(":") : [];
      const t = tail !== undefined ? (tail ? tail.split(":") : []) : [];
      const fill = tail !== undefined ? Array(8 - h.length - t.length).fill("0") : [];
      const parts = [...h, ...fill, ...t];
      if (parts.length !== 8) return null;
      return { v: 6, n: parts.reduce((a, p) => (a << 16n) + BigInt(parseInt(p || "0", 16)), 0n) };
    }
    const p = ip.split(".").map(Number);
    if (p.length !== 4 || p.some((x) => !(x >= 0 && x <= 255))) return null;
    return { v: 4, n: p.reduce((a, x) => (a << 8n) + BigInt(x), 0n) };
  }
  function inCidr(ip, cidr) {
    const [base, lenS] = cidr.split("/");
    const a = ipToBig(ip), b = ipToBig(base);
    if (!a || !b || a.v !== b.v) return false;
    const bits = a.v === 4 ? 32n : 128n;
    const len = BigInt(lenS === undefined ? bits : Number(lenS));
    const shift = bits - len;
    return (a.n >> shift) === (b.n >> shift);
  }
  const isIp = (s) => !!ipToBig(s || "");

  // --- load ------------------------------------------------------------------
  const DB = { edits: [], ready: null, meta: null, verify: { ok: null, problems: [], checked: [] } };

  async function load() {
    const derived = JSON.parse(text(await fetchBuf("derived.json")));
    DB.derived = derived;
    let sums = {};
    try {
      const sbuf = await fetchBuf("data/SHA256SUMS");
      DB.sumsHash = await sha256Hex(sbuf);
      text(sbuf).split("\n").filter(Boolean).forEach((l) => {
        const i = l.indexOf("  ");
        sums[l.slice(i + 2)] = l.slice(0, i);
      });
    } catch (e) {
      DB.noSums = true;
    }
    const files = derived.files; // e.g. ["data/2024/edits.jsonl", ...]
    const rows = [];
    for (const f of files) {
      const buf = await fetchBuf(f);
      const h = await sha256Hex(buf);
      if (sums[f] && sums[f] !== h) DB.verify.problems.push(`${f}: hash does not match SHA256SUMS`);
      else if (!sums[f] && !DB.noSums) DB.verify.problems.push(`${f}: not listed in SHA256SUMS`);
      DB.verify.checked.push({ file: f, sha256: h, listed: sums[f] || null });
      if (f.endsWith(".jsonl")) rows.push(...jsonl(buf));
      else if (f === "data/topics.json") DB.topics = JSON.parse(text(buf));
    }
    // SHA256SUMS must be the one the latest signed manifest committed to.
    const latest = derived.provenance.latest;
    if (latest && DB.sumsHash) {
      try {
        const m = JSON.parse(text(await fetchBuf(latest.manifest)));
        const mh = await sha256Hex(await fetchBuf(latest.manifest));
        if (mh !== latest.sha256) DB.verify.problems.push("latest manifest hash does not match chain");
        if (m.data_sums_sha256 && m.data_sums_sha256 !== DB.sumsHash)
          DB.verify.problems.push("SHA256SUMS differs from the latest signed manifest");
      } catch (e) {
        DB.verify.problems.push("latest manifest unavailable: " + e.message);
      }
    }
    if (latest && DB.noSums) DB.verify.problems.push("SHA256SUMS missing");
    // null = nothing signed yet (shown as "pending"), not a failure.
    DB.verify.ok = latest ? DB.verify.problems.length === 0 : (DB.verify.problems.length ? false : null);

    const topics = (DB.topics && DB.topics.pages) || {};
    const labels = (DB.topics && DB.topics.labels) || {};
    const beats = derived.beats || {};
    const seen = new Set();
    let id = 0;
    for (const e of rows) {
      const key = e.wiki + "|" + e.revid;
      if (seen.has(key)) continue;
      seen.add(key);
      const pk = e.wiki + "|" + e.title;
      const d = new Date(e.timestamp);
      const display = labels[pk] || null;
      DB.edits.push({
        id: ++id, wiki: e.wiki, revid: e.revid, ts: e.timestamp, year: e.timestamp.slice(0, 4),
        month: e.timestamp.slice(0, 7), hour: d.getUTCHours(), weekday: ((d.getUTCDay() + 6) % 7) + 1,
        title: e.title, display_title: display, comment: e.comment || "", sizediff: e.sizediff,
        minor: e.minor ? 1 : 0, is_new: e.new ? 1 : 0, ip: e.ip, range: e.range, label: e.label,
        label_source: e.label_source, url: e.url,
        beats: beats[key] || [], topics: (topics[pk] || []).map((t) => t.topic),
        _t: " " + norm([e.title, display, e.comment, e.ip, e.label].join(" ")).replace(/[^\p{L}\p{N}.:'\-]+/gu, " "),
      });
    }
  }
  DB.ready = load();

  // --- helpers ---------------------------------------------------------------
  const count = (arr, keyFn) => {
    const m = new Map();
    for (const x of arr) for (const k of [].concat(keyFn(x))) if (k != null) m.set(k, (m.get(k) || 0) + 1);
    return [...m].map(([k, n]) => ({ k, n }));
  };
  const byN = (a) => a.sort((x, y) => y.n - x.n || String(x.k).localeCompare(String(y.k)));
  const byK = (a) => a.sort((x, y) => String(x.k).localeCompare(String(y.k)));
  const minTs = (a) => a.reduce((m, e) => (!m || e.ts < m ? e.ts : m), null);
  const maxTs = (a) => a.reduce((m, e) => (!m || e.ts > m ? e.ts : m), null);
  const uniq = (a, f) => new Set(a.map(f)).size;
  const page = (e) => e.wiki + "|" + e.title;

  function filter(p) {
    const toks = p.q ? norm(p.q).match(/[\p{L}\p{N}.:'\-]+/gu) || [] : [];
    const to = p.to ? (p.to.length === 10 ? p.to + "T23:59:59Z" : p.to) : null;
    const cidr = p.ip && p.ip.includes("/");
    return DB.edits.filter((e) =>
      (!toks.length || toks.every((t) => e._t.includes(" " + t))) &&
      (!p.wiki || e.wiki === p.wiki) && (!p.label || e.label === p.label) &&
      (!p.ip || (cidr ? inCidr(e.ip, p.ip) : e.ip === p.ip)) &&
      (!p.beat || e.beats.includes(p.beat)) && (!p.topic || e.topics.includes(p.topic)) &&
      (!p.year || e.year === String(p.year)) && (!p.from || e.ts >= p.from) && (!to || e.ts <= to) &&
      (!p.title || e.title === p.title));
  }
  const SORTS = {
    newest: (a, b) => (a.ts < b.ts ? 1 : -1), oldest: (a, b) => (a.ts > b.ts ? 1 : -1),
    largest: (a, b) => Math.abs(b.sizediff || 0) - Math.abs(a.sizediff || 0),
  };
  const pub = (e) => {
    const { _t, ...rest } = e;
    return rest;
  };
  const err = (status, detail) => Object.assign(new Error(detail), { status });

  // --- endpoints (same shapes as web/app.py) ---------------------------------
  const R = {};
  R.meta = () => {
    const E = DB.edits;
    return {
      version: DB.derived.version, built: DB.derived.built, edits: E.length, ips: uniq(E, (e) => e.ip),
      pages: uniq(E, page), first: minTs(E), last: maxTs(E),
      years: byK(count(E, (e) => e.year)).map((x) => ({ year: x.k, n: x.n })),
      wikis: byN(count(E, (e) => e.wiki)).map((x) => ({ wiki: x.k, n: x.n })),
      labels: byN(count(E, (e) => e.label)).map((x) => ({ label: x.k, n: x.n })),
      beats: byN(count(E, (e) => e.beats)).map((x) => ({ beat: x.k, n: x.n })),
      months: byK(count(E, (e) => e.month)).map((x) => ({ month: x.k, n: x.n })),
      provenance: {
        chain_length: DB.derived.provenance.chain_length, latest: DB.derived.provenance.latest,
        verified: DB.verify.ok, problems: DB.verify.problems, last_sync: null, sync_error: null,
        sync_enabled: false, static: true,
      },
    };
  };
  R.search = (p) => {
    const rows = filter(p);
    const sorted = rows.slice().sort(SORTS[p.sort] || SORTS.newest);
    const limit = Math.min(Number(p.limit) || 50, 500), offset = Number(p.offset) || 0;
    const out = { total: rows.length, offset, limit, items: sorted.slice(offset, offset + limit).map(pub) };
    if (p.facets !== "false") {
      out.facets = {
        wiki: byN(count(rows, (e) => e.wiki)), label: byN(count(rows, (e) => e.label)).slice(0, 25),
        year: byK(count(rows, (e) => e.year)), beat: byN(count(rows, (e) => e.beats)),
        topic: byN(count(rows, (e) => e.topics)).slice(0, 20), ip: byN(count(rows, (e) => e.ip)).slice(0, 15),
        month: byK(count(rows, (e) => e.month)),
      };
    }
    return out;
  };
  R.export = (p) => {
    const rows = filter(p).sort(SORTS.oldest);
    const q = (v) => { v = v == null ? "" : String(v); return /[",\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v; };
    return ["timestamp,wiki,title,label,ip,range,sizediff,comment,url,revid"]
      .concat(rows.map((e) => [e.ts, e.wiki, e.title, e.label, e.ip, e.range, e.sizediff, e.comment, e.url, e.revid].map(q).join(",")))
      .join("\n") + "\n";
  };
  function groupIps(rows) {
    const m = new Map();
    for (const e of rows) {
      let g = m.get(e.ip);
      if (!g) m.set(e.ip, (g = { ip: e.ip, label: e.label, range: e.range, rows: [] }));
      g.rows.push(e);
    }
    return [...m.values()];
  }
  R.ips = (p) => {
    const groups = groupIps(filter(p)).map((g) => ({
      ip: g.ip, label: g.label, range: g.range, edits: g.rows.length, pages: uniq(g.rows, page),
      first: minTs(g.rows), last: maxTs(g.rows), wikis: [...new Set(g.rows.map((e) => e.wiki))].join(","),
      beats: byN(count(g.rows, (e) => e.beats)).slice(0, 4),
      top_pages: byN(count(g.rows, (e) => e.display_title || e.title)).slice(0, 3),
    }));
    const order = { edits: (a, b) => b.edits - a.edits, recent: (a, b) => (a.last < b.last ? 1 : -1),
      pages: (a, b) => b.pages - a.pages, first: (a, b) => (a.first > b.first ? 1 : -1) }[p.sort] || ((a, b) => b.edits - a.edits);
    groups.sort(order);
    const limit = Math.min(Number(p.limit) || 100, 500), offset = Number(p.offset) || 0;
    return { total: groups.length, items: groups.slice(offset, offset + limit) };
  };
  R.ip = (ip) => {
    if (!isIp(ip)) throw err(400, "not an IP address");
    const rows = DB.edits.filter((e) => e.ip === ip);
    if (!rows.length) throw err(404, "no edits from this address");
    const pages = new Set(rows.map(page));
    const neigh = new Map();
    for (const e of DB.edits) if (e.ip !== ip && pages.has(page(e))) {
      if (!neigh.has(e.ip)) neigh.set(e.ip, new Set());
      neigh.get(e.ip).add(page(e));
    }
    const tp = new Map();
    for (const e of rows) {
      const k = page(e);
      const t = tp.get(k) || { wiki: e.wiki, title: e.title, display_title: e.display_title, n: 0, last: e.ts };
      t.n++; if (e.ts > t.last) t.last = e.ts;
      tp.set(k, t);
    }
    return {
      ip, edits: rows.length, pages: pages.size, first: minTs(rows), last: maxTs(rows), label: rows[0].label,
      range: rows[0].range, label_source: rows[0].label_source,
      by_month: byK(count(rows, (e) => e.month)), by_hour: count(rows, (e) => e.hour).sort((a, b) => a.k - b.k),
      by_weekday: count(rows, (e) => e.weekday).sort((a, b) => a.k - b.k), wikis: byN(count(rows, (e) => e.wiki)),
      beats: byN(count(rows, (e) => e.beats)), topics: byN(count(rows, (e) => e.topics)).slice(0, 15),
      top_pages: [...tp.values()].sort((a, b) => b.n - a.n).slice(0, 40),
      neighbours: [...neigh].map(([k, s]) => ({ k, n: s.size })).sort((a, b) => b.n - a.n).slice(0, 10),
    };
  };
  R.topics = (p) => {
    const rows = filter({ year: p.year, label: p.label });
    const key = p.kind === "topic" ? "topics" : "beats";
    const m = new Map();
    for (const e of rows) for (const t of e[key]) {
      const g = m.get(t) || { k: t, n: 0, ips: new Set(), pages: new Set(), last: e.ts };
      g.n++; g.ips.add(e.ip); g.pages.add(page(e)); if (e.ts > g.last) g.last = e.ts;
      m.set(t, g);
    }
    return { kind: p.kind || "beat", items: [...m.values()].map((g) => ({ k: g.k, n: g.n, ips: g.ips.size, pages: g.pages.size, last: g.last })).sort((a, b) => b.n - a.n) };
  };
  R.topic = (kind, name) => {
    const rows = filter(kind === "beat" ? { beat: name } : { topic: name });
    if (!rows.length) throw err(404, "no edits for this topic");
    const ipLabel = Object.fromEntries(rows.map((e) => [e.ip, e.label]));
    const tp = new Map();
    for (const e of rows) {
      const k = page(e);
      const t = tp.get(k) || { wiki: e.wiki, title: e.title, display_title: e.display_title, n: 0, last: e.ts };
      t.n++; if (e.ts > t.last) t.last = e.ts;
      tp.set(k, t);
    }
    return {
      kind, name, edits: rows.length,
      ips: byN(count(rows, (e) => e.ip)).slice(0, 20).map((x) => ({ ...x, label: ipLabel[x.k] })),
      labels: byN(count(rows, (e) => e.label)).slice(0, 12),
      pages: [...tp.values()].sort((a, b) => b.n - a.n).slice(0, 30),
      by_month: byK(count(rows, (e) => e.month)),
    };
  };
  R.ranges = (p) => {
    const rows = filter({ year: p.year });
    const m = new Map();
    for (const e of rows) {
      const g = m.get(e.label) || { k: e.label, rows: [], source: e.label_source };
      g.rows.push(e); m.set(e.label, g);
    }
    return { items: [...m.values()].map((g) => ({ k: g.k, n: g.rows.length, ips: uniq(g.rows, (e) => e.ip),
      pages: uniq(g.rows, page), first: minTs(g.rows), last: maxTs(g.rows), source: g.source,
      beats: byN(count(g.rows, (e) => e.beats)).slice(0, 4) })).sort((a, b) => b.n - a.n) };
  };
  R.provenance = () => ({
    verified: DB.verify.ok, problems: DB.verify.problems, chain_length: DB.derived.provenance.chain_length,
    items: DB.derived.provenance.items, checked: DB.verify.checked, static: true,
  });

  // Full in-browser chain check (fetches every manifest).
  R.verifyChain = async () => {
    const links = jsonl(await fetchBuf("data/manifests/chain.jsonl"));
    const problems = [];
    let prev = "0".repeat(64);
    for (const [i, l] of links.entries()) {
      try {
        const buf = await fetchBuf(l.manifest);
        const h = await sha256Hex(buf);
        const m = JSON.parse(text(buf));
        if (h !== l.sha256) problems.push(`#${i}: manifest hash mismatch`);
        if (m.previous_manifest.sha256 !== prev || l.previous !== prev) problems.push(`#${i}: broken link`);
      } catch (e) { problems.push(`#${i}: ${e.message}`); }
      prev = l.sha256;
    }
    return { links: links.length, problems };
  };

  window.WIKISSC_ENGINE = {
    async call(path, params) {
      await DB.ready;
      const p = params || {};
      const parts = path.split("/").map(decodeURIComponent);
      try {
        switch (parts[0]) {
          case "meta": return R.meta();
          case "search": return R.search(p);
          case "ips": return parts[1] ? R.ip(parts[1]) : R.ips(p);
          case "topics": return parts[1] ? R.topic(parts[1], parts.slice(2).join("/")) : R.topics(p);
          case "ranges": return R.ranges(p);
          case "provenance": return R.provenance();
          case "health": return { ok: true, static: true, verified: DB.verify.ok };
        }
      } catch (e) {
        if (e.status) throw e;
        throw err(500, e.message);
      }
      throw err(404, "not found");
    },
    exportCsv: async (p) => { await DB.ready; return R.export(p || {}); },
    verifyChain: async () => { await DB.ready; return R.verifyChain(); },
  };
})();
