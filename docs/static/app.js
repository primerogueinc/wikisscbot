/* wikisscbot front end: hash-routed, no build step. */
(function () {
  "use strict";

  const $ = (sel, root) => (root || document).querySelector(sel);
  const view = $("#view");
  const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  const h = (v) => (v == null ? "" : String(v).replace(/[&<>"']/g, (c) => ESC[c]));
  const num = (n) => (n == null ? "–" : Number(n).toLocaleString());
  const day = (ts) => (ts ? ts.slice(0, 10) : "");
  const when = (ts) => (ts ? ts.replace("T", " ").replace("Z", " UTC").slice(0, 20) : "");
  const enc = encodeURIComponent;
  const WD = ["", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  let META = null;

  const STATIC = !!window.WIKISSC_STATIC;
  async function api(path, params) {
    if (STATIC) return window.WIKISSC_ENGINE.call(path, params);
    const qs = params ? "?" + new URLSearchParams(Object.entries(params).filter(([, v]) => v !== "" && v != null)) : "";
    const r = await fetch("/api/" + path + qs);
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.status);
    return r.json();
  }

  // --- routing ---------------------------------------------------------------
  function parseHash() {
    const raw = location.hash.replace(/^#/, "") || "/";
    const [path, query] = raw.split("?");
    const parts = path.split("/").filter(Boolean).map(decodeURIComponent);
    return { parts, params: Object.fromEntries(new URLSearchParams(query || "")) };
  }
  function go(path, params) {
    const qs = params && Object.keys(params).length
      ? "?" + new URLSearchParams(Object.entries(params).filter(([, v]) => v !== "" && v != null)) : "";
    location.hash = "#" + path + qs;
  }
  const searchLink = (params) => "#/?" + new URLSearchParams(params);
  const ipLink = (ip) => "#/ip/" + enc(ip);
  const topicLink = (kind, name) => "#/topic/" + kind + "/" + enc(name);

  const ROUTES = {
    "": renderSearch, ips: renderIps, ip: renderIp, topics: renderTopics, topic: renderTopic,
    ranges: renderRanges, provenance: renderProvenance, about: renderAbout,
    profiles: renderProfiles, profile: renderProfile,
  };
  async function route() {
    const { parts, params } = parseHash();
    const key = parts[0] || "";
    document.querySelectorAll("#nav a").forEach((a) => {
      const r = a.dataset.route;
      a.classList.toggle("on", (r === "search" && key === "") || r === key || (r === "ips" && key === "ip") || (r === "topics" && key === "topic") || (r === "profiles" && key === "profile"));
    });
    const fn = ROUTES[key] || renderSearch;
    view.innerHTML = '<div class="loading">loading…</div>';
    try {
      await fn(parts.slice(1), params);
    } catch (e) {
      view.innerHTML = `<div class="empty">Couldn't load this view: ${h(e.message)}</div>`;
    }
    if (!params._keep) window.scrollTo(0, 0);
  }
  window.addEventListener("hashchange", route);

  // --- shared bits -----------------------------------------------------------
  function bars(data, opts) {
    // data: [{k, n}], opts: {labels: fn(k) -> string, every: int, all: [k...]}
    opts = opts || {};
    const keys = opts.all || data.map((d) => d.k);
    const map = Object.fromEntries(data.map((d) => [d.k, d.n]));
    const W = opts.w || Math.max(320, Math.min(view.clientWidth - 26, 1150)), H = opts.h || 120, pad = 18, n = keys.length || 1;
    const max = Math.max(1, ...keys.map((k) => map[k] || 0));
    const bw = (W - 4) / n;
    const every = opts.every || Math.ceil(n / Math.max(4, Math.min(12, Math.floor(W / 58))));
    let s = `<svg viewBox="0 0 ${W} ${H + pad}" role="img" aria-label="${h(opts.title || "chart")}">`;
    keys.forEach((k, i) => {
      const v = map[k] || 0;
      const bh = v ? Math.max(1.5, (v / max) * (H - 14)) : 0;
      const x = 2 + i * bw;
      s += `<rect class="b" x="${x + bw * 0.12}" y="${H - bh}" width="${bw * 0.76}" height="${bh}"><title>${h((opts.labels ? opts.labels(k) : k) + ": " + v)}</title></rect>`;
      if (i % every === 0) s += `<text x="${x + bw / 2}" y="${H + 13}" text-anchor="middle">${h(opts.labels ? opts.labels(k) : k)}</text>`;
    });
    s += `<text x="${W - 2}" y="10" text-anchor="end">max ${max}</text></svg>`;
    return s;
  }
  function monthRange(first, last) {
    if (!first || !last) return [];
    let [y, m] = first.slice(0, 7).split("-").map(Number);
    const [y2, m2] = last.slice(0, 7).split("-").map(Number);
    const out = [];
    while (y < y2 || (y === y2 && m <= m2)) {
      out.push(`${y}-${String(m).padStart(2, "0")}`);
      m++; if (m > 12) { m = 1; y++; }
    }
    return out;
  }
  function chartBox(caption, svg, extra) {
    return `<div class="chart"><div class="cap"><span>${caption}</span>${extra || ""}</div>${svg}</div>`;
  }
  function flist(items, href, fmt) {
    if (!items || !items.length) return '<p class="muted small">None.</p>';
    return `<ul class="flist">${items.map((i) => `<li><a href="${href(i)}" title="${h(fmt ? fmt(i) : i.k)}">${h(fmt ? fmt(i) : i.k)}</a><span>${num(i.n)}</span></li>`).join("")}</ul>`;
  }
  function delta(d) {
    if (d == null) return "";
    return `<span class="d ${d > 0 ? "pos" : d < 0 ? "neg" : ""}">${d > 0 ? "+" : ""}${num(d)}</span>`;
  }
  function pageTitle(e) {
    return e.display_title ? `${e.display_title}` : e.title;
  }
  function pageUrl(e) {
    try {
      const u = new URL(e.url);
      return `${u.origin}/wiki/${encodeURIComponent(e.title.replace(/ /g, "_"))}`;
    } catch (_) { return e.url; }
  }
  function editItem(e) {
    const beats = (e.beats || []).map((b) => `<a class="tag" href="${topicLink("beat", b)}">${h(b)}</a>`).join("");
    const wm = (e.topics || []).slice(0, 2).map((t) => `<a class="tag wm" href="${topicLink("topic", t)}">${h(t)}</a>`).join("");
    return `<li class="edit" data-url="${h(e.url)}">
      <div><a class="t" href="${h(e.url)}" target="_blank" rel="noopener" title="Open this edit on Wikipedia">${h(pageTitle(e))}</a>${e.display_title ? ` <span class="sub mono">${h(e.title)}</span>` : ""}</div>
      ${delta(e.sizediff)}
      <div class="meta">
        <span class="mono">${h(when(e.ts))}</span>
        <span>${h(e.wiki)}</span>
        <a class="mono" href="${ipLink(e.ip)}">${h(e.ip)}</a>
        <a href="${searchLink({ label: e.label })}">${h(e.label)}</a>
        ${e.is_new ? '<span class="tag">new page</span>' : ""}
        <span class="tags">${beats}${wm}</span>
      </div>
      ${e.comment ? `<div class="c">${h(e.comment)}</div>` : ""}
      <div class="links">
        <a class="golink" href="${h(e.url)}" target="_blank" rel="noopener">View edit ↗</a>
        <a href="${h(pageUrl(e))}" target="_blank" rel="noopener">Current page ↗</a>
        <a href="${searchLink({ title: e.title, wiki: e.wiki })}">All GC edits to this page</a>
      </div>
    </li>`;
  }
  // Tapping anywhere on an edit (outside its other links) opens the diff.
  document.addEventListener("click", (ev) => {
    const li = ev.target.closest && ev.target.closest("li.edit[data-url]");
    if (!li || ev.target.closest("a, button") || (window.getSelection && String(window.getSelection()))) return;
    window.open(li.dataset.url, "_blank", "noopener");
  });
  function setSeal(meta) {
    const p = meta.provenance;
    const seal = $("#seal");
    if (p.verified === false) { seal.className = "seal bad"; seal.textContent = "✕ verification failed"; }
    else if (p.chain_length) { seal.className = "seal ok"; seal.textContent = `✓ ${p.chain_length} signed pulls`; }
    else { seal.className = "seal"; seal.textContent = "provenance pending"; }
    $("#foot-version").textContent = `v${meta.version} · data built ${when(meta.built)}`;
  }

  // --- search ----------------------------------------------------------------
  const FILTER_KEYS = ["q", "wiki", "year", "beat", "topic", "label", "ip", "from", "to", "title", "sort"];
  async function renderSearch(_, params) {
    const f = Object.fromEntries(FILTER_KEYS.map((k) => [k, params[k] || ""]));
    const m = META;
    const opt = (list, key, cur, all) => `<option value="">${all}</option>` +
      list.map((x) => `<option value="${h(x[key])}" ${x[key] === cur ? "selected" : ""}>${h(x[key])} (${num(x.n)})</option>`).join("");
    view.innerHTML = `
      <h1>Anonymous Wikipedia edits from Government of Canada networks</h1>
      <p class="lede">${num(m.edits)} logged-out edits from ${num(m.ips)} government IP addresses to ${num(m.pages)} pages, ${h(day(m.first))} to ${h(day(m.last))}. Search titles, edit summaries, addresses and ranges.</p>
      <form class="searchbar" id="sform" role="search">
        <input id="q" type="search" placeholder="Search: regiment, élection, CBSA, 205.193.239.44…" value="${h(f.q)}" aria-label="Search edits">
        <button class="btn primary" type="submit">Search</button>
      </form>
      <details class="filterbox" id="fbox"><summary>Filters</summary><div class="filters">
        <select id="f-wiki" aria-label="Wiki">${opt(m.wikis, "wiki", f.wiki, "All wikis")}</select>
        <select id="f-year" aria-label="Year">${opt(m.years, "year", f.year, "All years")}</select>
        <select id="f-beat" aria-label="Beat">${opt(m.beats, "beat", f.beat, "All beats")}</select>
        <select id="f-label" aria-label="Range">${opt(m.labels, "label", f.label, "All ranges")}</select>
        <input id="f-ip" placeholder="IP or CIDR" value="${h(f.ip)}" size="16" aria-label="IP or CIDR">
        <input id="f-from" type="date" value="${h(f.from)}" aria-label="From date">
        <input id="f-to" type="date" value="${h(f.to)}" aria-label="To date">
        <select id="f-sort" aria-label="Sort">
          <option value="newest" ${f.sort === "newest" || !f.sort ? "selected" : ""}>Newest</option>
          <option value="oldest" ${f.sort === "oldest" ? "selected" : ""}>Oldest</option>
          <option value="largest" ${f.sort === "largest" ? "selected" : ""}>Largest change</option>
        </select>
      </div></details>
      <div class="chips" id="chips"></div>
      <div class="layout">
        <section>
          <div id="spark"></div>
          <div class="resultbar"><span id="count" class="muted small"></span><a class="btn" id="export" href="#">Export CSV</a></div>
          <ul class="edits" id="results"></ul>
          <button class="btn more-btn" id="more" hidden>Load more</button>
        </section>
        <aside id="facets"></aside>
      </div>`;

    const read = () => {
      const p = { q: $("#q").value.trim(), wiki: $("#f-wiki").value, year: $("#f-year").value, beat: $("#f-beat").value,
        label: $("#f-label").value, ip: $("#f-ip").value.trim(), from: $("#f-from").value, to: $("#f-to").value,
        sort: $("#f-sort").value === "newest" ? "" : $("#f-sort").value, topic: f.topic, title: f.title };
      return p;
    };
    const anyFilter = ["wiki", "year", "beat", "label", "ip", "from", "to", "sort"].some((k) => f[k]);
    $("#fbox").open = anyFilter || !window.matchMedia("(max-width: 760px)").matches;
    $("#sform").addEventListener("submit", (ev) => { ev.preventDefault(); go("/", read()); });
    view.querySelectorAll(".filters select, .filters input[type=date]").forEach((el) => el.addEventListener("change", () => go("/", read())));
    $("#f-ip").addEventListener("keydown", (ev) => { if (ev.key === "Enter") go("/", read()); });

    // active chips
    const active = FILTER_KEYS.filter((k) => f[k] && k !== "sort");
    $("#chips").innerHTML = active.map((k) => {
      const rest = { ...f }; delete rest[k];
      return `<a class="chip active" href="${searchLink(Object.fromEntries(Object.entries(rest).filter(([, v]) => v)))}">${h(k)}: ${h(f[k])} <span class="x" aria-label="remove">×</span></a>`;
    }).join("") + (active.length ? ` <a class="chip" href="#/">Clear all</a>` : "");

    const q = Object.fromEntries(Object.entries(f).filter(([, v]) => v));
    if (STATIC) {
      $("#export").addEventListener("click", async (ev) => {
        ev.preventDefault();
        const csv = await window.WIKISSC_ENGINE.exportCsv(q);
        const a = document.createElement("a");
        a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
        a.download = "wikissc-edits.csv";
        a.click();
        setTimeout(() => URL.revokeObjectURL(a.href), 2000);
      });
    } else {
      $("#export").href = "/api/export.csv?" + new URLSearchParams(q);
    }
    let offset = 0;
    const PAGE = 50;
    const load = async (first) => {
      const r = await api("search", { ...q, limit: PAGE, offset, facets: first ? "true" : "false" });
      if (first) {
        $("#count").textContent = `${num(r.total)} edits`;
        if (!r.total) { $("#results").innerHTML = '<li class="empty">No edits match.</li>'; }
        const fc = r.facets;
        const add = (k, v) => searchLink({ ...q, [k]: v });
        const months = monthRange(m.first, m.last);
        $("#spark").innerHTML = r.total ? chartBox(`Edits per month${active.length ? " (filtered)" : ""}`, bars(fc.month, { all: months, labels: (k) => k.slice(2), h: 80, w: Math.max(320, Math.min(view.clientWidth - 26, 880)) })) : "";
        $("#facets").innerHTML = `
          <div class="facet"><h3>Beats</h3>${flist(fc.beat, (i) => add("beat", i.k))}</div>
          <div class="facet"><h3>Top addresses</h3>${flist(fc.ip, (i) => ipLink(i.k))}</div>
          <div class="facet"><h3>Ranges</h3>${flist(fc.label.slice(0, 10), (i) => add("label", i.k))}</div>
          <div class="facet more"><h3>Wikimedia topics</h3>${flist(fc.topic.slice(0, 12), (i) => add("topic", i.k))}</div>
          <div class="facet more"><h3>Wikis</h3>${flist(fc.wiki, (i) => add("wiki", i.k))}</div>`;
      }
      $("#results").insertAdjacentHTML("beforeend", r.items.map(editItem).join(""));
      offset += r.items.length;
      $("#more").hidden = offset >= r.total;
    };
    $("#more").addEventListener("click", () => load(false));
    await load(true);
  }

  // --- addresses -------------------------------------------------------------
  async function renderIps(_, params) {
    const sort = params.sort || "edits";
    const r = await api("ips", { sort, beat: params.beat, label: params.label, year: params.year, q: params.q, limit: 300 });
    const sortBtn = (k, label) => `<button class="${sort === k ? "on" : ""}" data-sort="${k}">${label}</button>`;
    view.innerHTML = `
      <h1>Addresses</h1>
      <p class="lede">Every government IP address with at least one public edit, aggregated. Many are shared gateways serving hundreds of people, so an address profile describes a network exit point, not a person.</p>
      <div class="filters">
        <select id="f-beat"><option value="">All beats</option>${META.beats.map((b) => `<option ${b.beat === params.beat ? "selected" : ""}>${h(b.beat)}</option>`).join("")}</select>
        <select id="f-year"><option value="">All years</option>${META.years.map((y) => `<option ${y.year === params.year ? "selected" : ""}>${h(y.year)}</option>`).join("")}</select>
        <select id="f-label"><option value="">All ranges</option>${META.labels.map((l) => `<option ${l.label === params.label ? "selected" : ""}>${h(l.label)}</option>`).join("")}</select>
      </div>
      <p class="muted small">${num(r.total)} addresses${r.total > r.items.length ? `, showing top ${r.items.length}` : ""}</p>
      <div class="tablewrap"><table>
        <thead><tr><th>Address</th><th>Range label</th><th>${sortBtn("edits", "Edits")}</th><th>${sortBtn("pages", "Pages")}</th><th>${sortBtn("first", "First")}</th><th>${sortBtn("recent", "Last")}</th><th>Main beats</th><th>Most-edited pages</th></tr></thead>
        <tbody>${r.items.map((i) => `<tr>
          <td><a class="mono" href="${ipLink(i.ip)}">${h(i.ip)}</a></td>
          <td>${h(i.label)}</td>
          <td class="n">${num(i.edits)}</td><td class="n">${num(i.pages)}</td>
          <td class="mono small">${h(day(i.first))}</td><td class="mono small">${h(day(i.last))}</td>
          <td><span class="tags">${(i.beats || []).slice(0, 3).map((b) => `<a class="tag" href="${topicLink("beat", b.k)}">${h(b.k)} ${b.n}</a>`).join("")}</span></td>
          <td class="small">${(i.top_pages || []).map((p) => h(p.k)).join(" · ")}</td>
        </tr>`).join("")}</tbody>
      </table></div>`;
    const re = () => go("/ips", { sort: sort === "edits" ? "" : sort, beat: $("#f-beat").value, year: $("#f-year").value, label: $("#f-label").value });
    view.querySelectorAll("select").forEach((s) => s.addEventListener("change", re));
    view.querySelectorAll("th button").forEach((b) => b.addEventListener("click", () =>
      go("/ips", { sort: b.dataset.sort === "edits" ? "" : b.dataset.sort, beat: params.beat, year: params.year, label: params.label })));
  }

  async function renderIp(parts) {
    const ip = parts[0];
    const [p, recent] = await Promise.all([api("ips/" + enc(ip)), api("search", { ip, limit: 100, facets: "false" })]);
    const tzShift = (k) => ((k - 4 + 24) % 24); // EDT approximation for display
    let et = false;
    const half = Math.max(320, window.innerWidth > 800 ? (view.clientWidth - 70) / 2 : view.clientWidth - 26);
    view.innerHTML = `
      <a class="back" href="#/ips">← Addresses</a>
      <div class="ipline"><h1>${h(ip)}</h1><span class="muted">${h(p.label)}</span></div>
      <p class="muted small">Range <span class="mono">${h(p.range)}</span> · label from ${h(p.label_source === "ssc-bgp" ? "SSC BGP announcement (department unknown)" : "@gccaedits department list (historical)")}</p>
      <div class="stats">
        <div class="stat"><b>${num(p.edits)}</b><span>edits</span></div>
        <div class="stat"><b>${num(p.pages)}</b><span>distinct pages</span></div>
        <div class="stat"><b>${h(day(p.first))}</b><span>first edit</span></div>
        <div class="stat"><b>${h(day(p.last))}</b><span>last edit</span></div>
      </div>
      <div class="two">
        ${chartBox("Edits per month", bars(p.by_month, { all: monthRange(p.first, p.last), labels: (k) => k.slice(2), w: half }))}
        <div id="hours"></div>
      </div>
      <div class="three">
        <div><h2>Beats</h2>${flist(p.beats, (i) => searchLink({ ip, beat: i.k }))}</div>
        <div><h2>Wikimedia topics</h2>${flist(p.topics, (i) => searchLink({ ip, topic: i.k }))}</div>
        <div><h2>Weekday</h2>${flist(p.by_weekday.map((d) => ({ k: WD[d.k], n: d.n })), () => "#" + location.hash.slice(1))}
          <h2>Wikis</h2>${flist(p.wikis, (i) => searchLink({ ip, wiki: i.k }))}</div>
      </div>
      <div class="two">
        <div><h2>Most-edited pages</h2>${flist(p.top_pages.map((x) => ({ k: x.display_title || x.title, n: x.n, t: x.title })), (i) => searchLink({ ip, title: i.t }))}</div>
        <div><h2>Addresses editing the same pages</h2>
          <p class="muted small">Shared pages often mean the same people behind different exit addresses.</p>
          ${flist(p.neighbours, (i) => ipLink(i.k), (i) => i.k)}</div>
      </div>
      <h2>Recent edits</h2>
      <ul class="edits">${recent.items.map(editItem).join("")}</ul>
      ${recent.total > recent.items.length ? `<p><a class="btn" href="${searchLink({ ip })}">All ${num(recent.total)} edits →</a></p>` : ""}`;
    const drawHours = () => {
      const data = et ? p.by_hour.map((d) => ({ k: tzShift(d.k), n: d.n })) : p.by_hour;
      $("#hours").innerHTML = chartBox(`Hour of day (${et ? "Eastern, UTC−4" : "UTC"})`,
        bars(data, { all: [...Array(24).keys()], labels: (k) => String(k).padStart(2, "0"), every: 3, w: half }),
        `<button id="tz">${et ? "show UTC" : "show Eastern"}</button>`);
      $("#tz").addEventListener("click", () => { et = !et; drawHours(); });
    };
    drawHours();
  }

  // --- topics ----------------------------------------------------------------
  async function renderTopics(_, params) {
    const kind = params.kind === "topic" ? "topic" : "beat";
    const r = await api("topics", { kind, year: params.year });
    view.innerHTML = `
      <h1>Topics</h1>
      <p class="lede">${kind === "beat"
        ? "Beats are keyword groupings tuned for Canadian government reporting, matched against page titles and edit summaries."
        : "Wikimedia topics come from Wikimedia's own article-topic model, applied to each edited Wikipedia page."}</p>
      <div class="chips">
        <a class="chip ${kind === "beat" ? "active" : ""}" href="#/topics">Beats</a>
        <a class="chip ${kind === "topic" ? "active" : ""}" href="#/topics?kind=topic">Wikimedia topics</a>
        ${META.years.map((y) => `<a class="chip ${params.year === y.year ? "active" : ""}" href="#/topics?${new URLSearchParams({ kind: kind === "beat" ? "" : kind, year: params.year === y.year ? "" : y.year })}">${h(y.year)}</a>`).join("")}
      </div>
      ${r.items.length ? `<div class="grid">${r.items.map((t) => `
        <a class="card" href="${topicLink(kind, t.k)}">
          <h3>${h(t.k)}</h3>
          <div class="big">${num(t.n)}</div>
          <div class="muted small">edits · ${num(t.ips)} addresses · ${num(t.pages)} pages · last ${h(day(t.last))}</div>
        </a>`).join("")}</div>` : '<div class="empty">No topics yet. Run the enrich step to classify pages.</div>'}`;
  }

  async function renderTopic(parts) {
    const kind = parts[0], name = parts.slice(1).join("/");
    const [t, recent] = await Promise.all([
      api("topics/" + kind + "/" + enc(name)),
      api("search", { [kind === "beat" ? "beat" : "topic"]: name, limit: 50, facets: "false" }),
    ]);
    const key = kind === "beat" ? "beat" : "topic";
    view.innerHTML = `
      <a class="back" href="#/topics${kind === "topic" ? "?kind=topic" : ""}">← Topics</a>
      <h1>${h(name)}</h1>
      <p class="muted">${num(t.edits)} edits · ${kind === "beat" ? "beat" : "Wikimedia topic"}</p>
      ${chartBox("Edits per month", bars(t.by_month, { all: monthRange(META.first, META.last), labels: (k) => k.slice(2), h: 110 }))}
      <div class="three">
        <div><h2>Addresses</h2>${flist(t.ips, (i) => ipLink(i.k), (i) => i.k)}</div>
        <div><h2>Ranges</h2>${flist(t.labels, (i) => searchLink({ [key]: name, label: i.k }))}</div>
        <div><h2>Pages</h2>${flist(t.pages.map((x) => ({ k: x.display_title || x.title, n: x.n, t: x.title })), (i) => searchLink({ [key]: name, title: i.t }))}</div>
      </div>
      <h2>Recent edits</h2>
      <ul class="edits">${recent.items.map(editItem).join("")}</ul>
      ${recent.total > recent.items.length ? `<p><a class="btn" href="${searchLink({ [key]: name })}">All ${num(recent.total)} edits →</a></p>` : ""}`;
  }

  // --- ranges ----------------------------------------------------------------
  async function renderRanges(_, params) {
    const r = await api("ranges", { year: params.year });
    view.innerHTML = `
      <h1>Ranges</h1>
      <p class="lede">Edits grouped by the label of the most specific matching IP range. Department labels come from the 2014–16 @gccaedits list and may be stale; "Shared Services Canada" labels mean the department can't be determined from the address.</p>
      <div class="chips">${META.years.map((y) => `<a class="chip ${params.year === y.year ? "active" : ""}" href="#/ranges${params.year === y.year ? "" : "?year=" + y.year}">${h(y.year)}</a>`).join("")}</div>
      <div class="tablewrap"><table>
        <thead><tr><th>Range label</th><th>Edits</th><th>Addresses</th><th>Pages</th><th>First</th><th>Last</th><th>Main beats</th></tr></thead>
        <tbody>${r.items.map((i) => `<tr>
          <td><a href="${searchLink({ label: i.k, year: params.year || "" })}">${h(i.k)}</a></td>
          <td class="n">${num(i.n)}</td>
          <td class="n"><a href="#/ips?${new URLSearchParams({ label: i.k })}">${num(i.ips)}</a></td>
          <td class="n">${num(i.pages)}</td>
          <td class="mono small">${h(day(i.first))}</td><td class="mono small">${h(day(i.last))}</td>
          <td><span class="tags">${i.beats.map((b) => `<span class="tag">${h(b.k)} ${b.n}</span>`).join("")}</span></td>
        </tr>`).join("")}</tbody>
      </table></div>`;
  }

  // --- provenance ------------------------------------------------------------
  async function renderProvenance() {
    const p = await api("provenance");
    const ok = p.verified !== false;
    const otsLabel = { bitcoin: "anchored in Bitcoin", pending: "pending confirmation", missing: "no proof", unknown: "unrecognised proof" };
    const short = (x) => (x ? x.slice(0, 16) + "…" : "");
    const fileHref = (f) => (STATIC ? "./" + f : "/api/provenance/file/" + f);
    view.innerHTML = `
      <h1>Provenance</h1>
      <p class="lede">Every data pull is recorded in a manifest with the SHA-256 of each API response received and each data file produced. Manifests are hash-chained, so changing or removing any past pull breaks every later link, and each manifest is timestamped in the Bitcoin blockchain with OpenTimestamps.</p>
      <div class="verdict ${p.chain_length ? (ok ? "ok" : "bad") : ""}">
        <span class="icon">${p.chain_length ? (ok ? "✓" : "✕") : "…"}</span>
        <div>
          <b>${p.chain_length ? (ok ? (STATIC ? "Data verified in your browser" : `Chain of ${p.chain_length} manifests verified on load`) : "Verification failed") : "No signed pulls yet"}</b>
          <div class="small">${p.problems && p.problems.length ? p.problems.map(h).join("<br>")
            : STATIC ? "Every data file this page loaded was hashed with SHA-256 in your browser and matched data/SHA256SUMS, which matches the latest signed manifest."
            : "Manifest hashes, chain links, response logs and data checksums all match."}</div>
          ${STATIC && p.chain_length ? '<button class="btn" id="vchain" style="margin-top:8px">Verify the full chain in this browser</button> <span id="vres" class="small"></span>' : ""}
        </div>
      </div>
      ${STATIC && p.checked ? `<details><summary class="small muted">Files checked (${p.checked.length})</summary><ul class="flist">${p.checked.map((c) => `<li><span class="mono">${h(c.file)}</span><span class="hash">${h(c.sha256.slice(0, 16))}…</span></li>`).join("")}</ul></details>` : ""}
      ${p.items.length ? `<div class="tablewrap"><table>
        <thead><tr><th>#</th><th>Finished (UTC)</th><th>Kind</th><th>Counts</th><th>Responses</th><th>Manifest SHA-256</th><th>Timestamp</th><th>Files</th></tr></thead>
        <tbody>${p.items.map((m) => `<tr>
          <td class="n">${m.seq}</td>
          <td class="mono small">${h(when(m.finished))}</td>
          <td>${h(m.kind)}${m.workflow_run ? ` <a class="small" href="${h(m.workflow_run.url)}" target="_blank" rel="noopener">run</a>` : ""}</td>
          <td class="small">${h(Object.entries(m.counts || {}).map(([k, v]) => k.replace("edits_", "") + " " + v).join(", "))}</td>
          <td class="n">${num(m.responses && m.responses.count)}</td>
          <td><span class="hash copy" title="${h(m.sha256)} (click to copy)" data-copy="${h(m.sha256)}">${h(short(m.sha256))}</span></td>
          <td><span class="badge ${h(m.ots)}">${h(otsLabel[m.ots] || m.ots)}</span></td>
          <td class="small"><a href="${fileHref(m.manifest)}">manifest</a> · <a href="${fileHref(m.ots_file)}">.ots</a></td>
        </tr>`).join("")}</tbody>
      </table></div>` : ""}
      <h2>Verify it yourself</h2>
      <div class="prose">
        <p>Clone the repository and run the bot's verifier, which recomputes every hash and walks the chain:</p>
        <pre class="code">git clone https://github.com/primerogueinc/wikisscbot
cd wikisscbot
python -m wikisscbot verify          # chain + SHA256SUMS
sha256sum -c data/SHA256SUMS         # same check with standard tools</pre>
        <p>To check a blockchain timestamp, install the OpenTimestamps client and verify a manifest against its proof. Proofs start as "pending" and are upgraded to a Bitcoin attestation within a few hours by the daily workflow.</p>
        <pre class="code">pip install opentimestamps-client
ots verify data/manifests/&lt;run&gt;.json.ots</pre>
        <p>Each manifest names the exact code commit and GitHub Actions run that produced it, the parameters used, the SHA-256 of every config file, and a rolling hash over every API response. The response log lists each request URL with the hash of the bytes received.</p>
      </div>`;
    const vb = $("#vchain");
    if (vb) vb.addEventListener("click", async () => {
      vb.disabled = true;
      $("#vres").textContent = "checking…";
      const r = await window.WIKISSC_ENGINE.verifyChain();
      $("#vres").textContent = r.problems.length ? "✕ " + r.problems.join("; ") : `✓ all ${r.links} links intact`;
    });
    view.querySelectorAll("[data-copy]").forEach((el) => el.addEventListener("click", () => {
      navigator.clipboard && navigator.clipboard.writeText(el.dataset.copy);
      el.textContent = "copied";
      setTimeout(() => (el.textContent = short(el.dataset.copy)), 1200);
    }));
  }

  // --- profiles (hand-written analyst pages in static/profiles/) -------------
  async function loadProfiles() {
    const r = await fetch("static/profiles/index.json");
    if (!r.ok) throw new Error("profiles index " + r.status);
    return r.json();
  }
  async function renderProfiles() {
    const list = await loadProfiles();
    view.innerHTML = `
      <h1>Profiles</h1>
      <p class="lede">Analyst write-ups on recurring editors, traced by government address range. Each describes network exit points, not people.</p>
      <div class="grid">${list.map((p) => `
        <a class="card" href="#/profile/${enc(p.slug)}"><h3>${h(p.codename)}</h3>
          <p class="muted small">${h(p.summary)}</p>
          <p class="mono small">${p.ips.map(h).join(" · ")}</p>
          <p class="muted small">Updated ${h(p.updated)}</p></a>`).join("")}</div>`;
  }
  async function renderProfile(parts) {
    const slug = parts[0] || "";
    const list = await loadProfiles();
    const p = list.find((x) => x.slug === slug);
    if (!p) { view.innerHTML = '<div class="empty">No such profile.</div>'; return; }
    const r = await fetch("static/profiles/" + enc(p.slug) + ".html");
    if (!r.ok) throw new Error("profile " + r.status);
    view.innerHTML = `
      <a class="back" href="#/profiles">← Profiles</a>
      <div class="ipline"><h1>${h(p.codename)}</h1><span class="muted">profile · updated ${h(p.updated)}</span></div>
      ${await r.text()}`;
  }

  // --- about -----------------------------------------------------------------
  async function renderAbout() {
    view.innerHTML = `
      <h1>About</h1>
      <div class="prose">
        <p>wikisscbot archives Wikipedia, Wikidata and Commons edits made while logged out from IP ranges belonging to the Government of Canada, in the tradition of the @gccaedits bot (2014).</p>
        <h2>How the data is collected</h2>
        <p>The collector asks the MediaWiki API for every contribution from each government address block over a time window. The ranges are the public-domain @gccaedits department list plus every prefix announced in the period by Shared Services Canada's networks (AS2665, AS2675, AS2682, AS25689, AS806). Each address is labelled by its most specific department range; addresses only covered by an SSC announcement are labelled as SSC network, department unknown.</p>
        <h2>Limits</h2>
        <ul>
          <li>On 4 November 2025 English Wikipedia replaced public IP attribution with temporary accounts, and other Wikimedia wikis followed. Logged-out edits since then are not public and are not collected; nothing here attempts to reveal them.</li>
          <li>Department labels come from a decade-old crowd-sourced list. Shared Services Canada operates shared gateways for dozens of departments, so most addresses identify a network, not a department.</li>
          <li>An edit from a government network is not evidence of who made it, or that it was made in an official capacity. Address profiles describe exit points that can serve hundreds of people.</li>
          <li>Beats are keyword rules and will mislabel some pages. Wikimedia topics come from Wikimedia's article-topic model.</li>
        </ul>
        <h2>Source</h2>
        <p>Code, data, manifests and the build log: <a href="https://github.com/primerogueinc/wikisscbot" target="_blank" rel="noopener">github.com/primerogueinc/wikisscbot</a>. A project of Prime Rogue Inc.</p>
      </div>`;
  }

  // --- boot ------------------------------------------------------------------
  (async function boot() {
    try {
      META = await api("meta");
      setSeal(META);
    } catch (e) {
      view.innerHTML = `<div class="empty">The data service isn't responding (${h(e.message)}).</div>`;
      return;
    }
    route();
  })();
})();
