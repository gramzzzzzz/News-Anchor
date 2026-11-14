(() => {
  const D = window.NA_DATA;
  const $ = (s, r = document) => r.querySelector(s);
  const body = document.body;
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const BAND = { high: "Highly reliable", reliable: "Reliable", mixed: "Mixed", low: "Low", un: "Unrated" };

  const SCOPES = {
    global: { label: "World", crumbs: [["global", "Global"]] },
    india: { label: "India", crumbs: [["global", "Global"], ["asia", "Asia-Pacific"], ["india", "India"]] },
    chennai: { label: "Chennai", crumbs: [["global", "Global"], ["asia", "Asia-Pacific"], ["india", "India"], ["tn", "Tamil Nadu"], ["chennai", "Chennai"]] },
  };
  const TOPICS = ["Politics", "Business", "Finance and markets", "Economy", "Technology", "Science", "Health", "Environment", "Conflict", "Crime and justice", "Sports", "Video games", "Entertainment", "Society"];

  const state = {
    scope: localStorage.getItem("na.scope") || "india",
    topic: null, tab: "top", selected: null, view: "briefing", breakingDismissed: false,
  };

  // ---------- helpers ----------
  const ago = (iso) => {
    const m = Math.max(1, Math.round((Date.now() - new Date(iso)) / 60000));
    if (m < 60) return `${m} min ago`;
    const h = Math.round(m / 60);
    return h < 48 ? `${h} h ago` : `${Math.round(h / 24)} days ago`;
  };
  const srcOf = (aid) => D.SOURCES[D.A[aid].src];
  const latest = (s) => s.arts.map((a) => D.A[a].t).sort().pop();
  const seeded = (str) => { let h = 2166136261; for (const c of str) h = Math.imul(h ^ c.charCodeAt(0), 16777619); return () => ((h = Math.imul(h ^ (h >>> 15), 2246822507) >>> 0) / 4294967296); };

  function strip(s) {
    const r = seeded(s.id);
    const segs = s.arts.map((a) => srcOf(a).band);
    const outlets = new Set(s.arts.map((a) => D.A[a].src)).size;
    for (let i = 0; i < s.more; i++) { const x = r(); segs.push(x < 0.32 ? "high" : x < 0.86 ? "reliable" : x < 0.96 ? "mixed" : "un"); }
    const shown = segs.slice(0, 24).map((b) => `<i class="${b}"></i>`).join("");
    const extra = segs.length > 24 ? `<span class="more">+${segs.length - 24}</span>` : "";
    return { html: `<div class="strip" aria-label="${outlets + s.more} outlets cover this story">${shown}${extra}</div>`, total: outlets + s.more };
  }

  // ---------- scope bar ----------
  function renderCrumbs() {
    const sc = SCOPES[state.scope];
    $("#whereCrumbs").innerHTML = sc.crumbs.map(([id, name]) => `<li><button class="crumb" data-where="${id}">${esc(name)}</button></li>`).join("") +
      `<li class="add"><button class="crumb-add" data-where-add>${state.scope === "chennai" ? "" : "Narrow"}</button></li>`;
    if (state.scope === "chennai") $("#whereCrumbs li.add").remove();
    $("#whatCrumbs").innerHTML = `<li><button class="crumb" data-what="all">All topics</button></li>` + (state.topic ? `<li><button class="crumb" data-what="${esc(state.topic)}">${esc(state.topic)}</button></li>` : "");
  }

  function popover(anchor, html) {
    const p = $("#popover");
    p.innerHTML = html; p.hidden = false;
    const r = anchor.getBoundingClientRect();
    p.style.left = Math.min(r.left, innerWidth - p.offsetWidth - 12) + "px";
    p.style.top = r.bottom + 6 + "px";
    p.querySelector("button:not(:disabled)")?.focus();
  }
  const closePopover = () => { $("#popover").hidden = true; };

  function wherePopover(anchor, level) {
    const row = (id, name, n, ok = true) => `<button ${ok ? `data-go="${id}"` : "disabled"}><span>${esc(name)}</span><span class="n">${ok ? n : "not in prototype"}</span></button>`;
    let html = "";
    if (level === "global" || level === "asia") {
      html = `<h6>Regions</h6>${row("asia", "Asia-Pacific", "", false)}${row("x", "Europe", "", false)}${row("x", "Middle East", "", false)}${row("x", "Africa", "", false)}<h6>Countries</h6>${row("india", "India", "10 stories")}${row("x", "United States", "", false)}${row("x", "United Kingdom", "", false)}<h6>Whole world</h6>${row("global", "Global", "7 stories")}`;
    } else if (level === "india" || level === "add-india") {
      html = `<h6>States</h6>${row("chennai", "Tamil Nadu, then Chennai", "4 stories")}${row("x", "Karnataka", "", false)}${row("x", "Kerala", "", false)}${row("x", "Maharashtra", "", false)}<h6>Cities</h6>${row("chennai", "Chennai", "4 stories")}${row("x", "Bengaluru", "", false)}${row("x", "Mumbai", "", false)}`;
    } else {
      html = `<h6>Cities in Tamil Nadu</h6>${row("chennai", "Chennai", "4 stories")}${row("x", "Coimbatore", "", false)}${row("x", "Madurai", "", false)}`;
    }
    popover(anchor, html);
  }
  function whatPopover(anchor) {
    const counts = {}; (D.STORIES[state.scope] || []).forEach((s) => (counts[s.topic] = (counts[s.topic] || 0) + 1));
    popover(anchor, `<h6>Categories</h6><button data-topic="all"><span>All topics</span><span class="n">${(D.STORIES[state.scope] || []).length}</span></button>` +
      TOPICS.map((t) => `<button data-topic="${esc(t)}"><span>${esc(t)}</span><span class="n">${counts[t] || 0}</span></button>`).join(""));
  }

  // ---------- list ----------
  function stories() {
    let list = D.STORIES[state.scope] || [];
    if (state.topic) list = list.filter((s) => s.topic === state.topic);
    if (state.tab === "trending") list = [...list].sort((a, b) => latest(b).localeCompare(latest(a)));
    return list;
  }

  function renderList() {
    const el = $("#list");
    if (state.tab === "following" || state.tab === "saved") {
      el.innerHTML = state.tab === "following"
        ? `<div class="empty"><b>Nothing followed yet</b>Follow a place or topic with F, and its new stories collect here with an unread count.</div>`
        : `<div class="empty"><b>No saved stories</b>Press S on any story to keep it for later.</div>`;
      return;
    }
    let list = stories();
    if (!list.length) { el.innerHTML = `<div class="empty"><b>No ${esc(state.topic)} stories in ${esc(SCOPES[state.scope].label)} in the last 24 hours</b>Widen the place or the time window, or choose another topic.</div>`; return; }
    // breaking stories are pinned first and marked, never repeated in a separate banner
    if (state.tab === "top") list = [...list.filter((s) => s.breaking), ...list.filter((s) => !s.breaking)];
    let html = "";
    html += list.map((s, i) => {
      const lead = D.A[s.arts[0]];
      const src = D.SOURCES[lead.src];
      const st = strip(s);
      return `<article class="story${state.selected === s.id ? " is-selected" : ""}${s.breaking ? " is-breaking" : ""}" data-id="${s.id}" tabindex="0" aria-label="${s.breaking ? "Breaking: " : ""}${esc(D.TITLES[s.id])}">
        <span class="rank">${i + 1}</span>
        <div>
          <h3>${s.breaking ? `<span class="brk">Breaking</span>` : ""}${esc(D.TITLES[s.id])}</h3>
          <p class="lead">${esc(s.lead)}</p>
          <div class="meta">
            <span class="src">${esc(src.name)}<button class="info" data-src="${lead.src}" aria-label="About ${esc(src.name)}">${infoIcon}</button></span>
            <span>and ${st.total - 1} more outlets</span>
            <span>${ago(latest(s))}</span>
            ${s.developing ? `<span class="mark-dev">Developing</span>` : ""}
            ${s.kind ? `<span class="kind">${s.kind}</span>` : ""}
            <span>${esc(s.topic)}</span>
          </div>
          ${st.html}
        </div>
      </article>`;
    }).join("");
    el.innerHTML = html;
  }
  const infoIcon = `<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><circle cx="12" cy="12" r="8.5" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M12 11v5" stroke="currentColor" stroke-width="1.6"/><circle cx="12" cy="8" r="1" fill="currentColor"/></svg>`;

  // ---------- reading pane ----------
  let citeNo = {};
  function cites(ids) {
    (ids || []).forEach((a) => { if (!(a in citeNo)) citeNo[a] = Object.keys(citeNo).length + 1; });
    return [...(ids || [])].sort((x, y) => citeNo[x] - citeNo[y]).map((a) => {
      const s = srcOf(a);
      return `<a class="cite ${s.band}" href="${esc(D.A[a].url)}" target="_blank" rel="noopener" data-cite="${a}" aria-label="Source ${citeNo[a]}: ${esc(s.name)}">${citeNo[a]}</a>`;
    }).join("");
  }

  function renderBriefing() {
    const b = D.BRIEFINGS[state.scope];
    citeNo = {};
    const big = b.big.map((x) => `${esc(x.t)} ${cites(x.c)}`).join(" ");
    const storiesHtml = b.stories.map((s) => `<section>
        <h3><button data-open="${s.id}">${esc(s.h)}</button></h3>
        <p class="body">${esc(s.t)} ${cites(s.c)}</p>
        ${s.differ ? `<p class="differ"><b>Where reports differ</b>${esc(s.differ)} ${cites(s.dc)}</p>` : ""}
      </section>`).join("");
    const watch = b.watch.length ? `<h4>What to watch</h4><ul class="points">${b.watch.map((w) => `<li>${esc(w.t)} ${cites(w.c)}</li>`).join("")}</ul>` : "";
    const unknown = b.unknown.length ? `<h4>Not known yet</h4><ul class="points">${b.unknown.map((u) => `<li>${esc(u)}</li>`).join("")}</ul>` : "";
    const n = Object.keys(citeNo).length;
    $("#paneTitle").innerHTML = `<b>Briefing</b> <span>${esc(b.title)}</span>`;
    $("#panePeek").textContent = b.big[0].t;
    $("#paneBody").innerHTML = `<div class="reading">
      <h2>${esc(b.title)}</h2>
      <p class="sub">${b.stories.length} stories from ${n} articles${state.topic ? `. Covers all topics; a ${esc(state.topic)} briefing is generated when you open one` : ""}</p>
      <p class="big">${big}</p>
      ${storiesHtml}
      <section>${watch}${unknown}</section>
      <p class="gen"><span>Sample written by hand for this prototype; in the app the AI writes it.</span><button class="ghost">Regenerate</button></p>
    </div>`;
    $("#paneBody").scrollTop = 0;
  }

  function renderStory(id) {
    const s = Object.values(D.STORIES).flat().find((x) => x.id === id);
    const brief = D.BRIEFS[id];
    citeNo = {};
    const st = strip(s);
    const briefHtml = brief ? `<h4>AI brief</h4>
      <p class="body">${esc(brief.summary)} ${cites([s.arts[0]])}</p>
      <ul class="points">${brief.points.map((p) => `<li>${esc(p.t)} ${cites(p.c)}</li>`).join("")}</ul>
      ${brief.open.length ? `<h4>Not known yet</h4><ul class="points">${brief.open.map((o) => `<li>${esc(o)}</li>`).join("")}</ul>` : ""}`
      : `<h4>AI brief</h4><p class="body muted">The AI brief of this story appears here: key points, who said what, what is disputed, each point tied to its source. Not written for this item in the prototype.</p>`;
    const cov = s.arts.map((a) => { const A = D.A[a]; const S = D.SOURCES[A.src]; return `<li>
        <div class="who"><span class="src">${esc(S.name)}<button class="info" data-src="${A.src}" aria-label="About ${esc(S.name)}">${infoIcon}</button></span><span>${esc(S.type)}</span><span>${ago(A.t)}</span></div>
        <span class="ext">Opens ${esc(A.src)}</span>
        <a class="title" href="${esc(A.url)}" target="_blank" rel="noopener">${esc(A.title)}</a></li>`; }).join("");
    $("#paneTitle").innerHTML = `<b>Story</b> <span>${esc(s.topic)}</span>`;
    $("#panePeek").textContent = D.TITLES[id];
    $("#paneBody").innerHTML = `<div class="reading">
      <button class="back" data-back>${backIcon} Back to briefing</button>
      <h2>${esc(D.TITLES[id])}</h2>
      <p class="sub">${st.total} outlets, last update ${ago(latest(s))}</p>
      ${st.html}
      ${briefHtml}
      <h4>Coverage</h4><ul class="coverage">${cov}</ul>
      <p class="sub">and ${st.total - new Set(s.arts.map((a) => D.A[a].src)).size} more outlets (illustrative count)</p>
    </div>`;
    $("#paneBody").scrollTop = 0;
  }
  const backIcon = `<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path d="M15 6l-6 6 6 6" fill="none" stroke="currentColor" stroke-width="1.6"/></svg>`;

  function renderPane() { state.view === "briefing" ? renderBriefing() : renderStory(state.view); }

  // ---------- pane state ----------
  const minKey = () => `na.min.${body.dataset.layout}`;
  function setMin(min) {
    body.classList.toggle("pane-min", min);
    $("#paneToggle").setAttribute("aria-expanded", String(!min));
    $("#paneToggle").setAttribute("aria-label", min ? "Expand reading pane" : "Minimise reading pane");
    localStorage.setItem(minKey(), min ? "1" : "0");
  }
  function openStory(id) {
    state.selected = id; state.view = id;
    if (body.dataset.layout === "page") body.classList.add("page-reading"), setTab(null);
    setMin(false); renderList(); renderPane();
  }
  function setLayout(l) {
    body.dataset.layout = l;
    document.querySelectorAll("#layoutSeg button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.layout === l)));
    body.classList.remove("page-reading");
    const saved = localStorage.getItem(minKey());
    setMin(saved ? saved === "1" : l === "drawer" || innerWidth < 1100);  // tablets and phones start with the list in full view
    if (l !== "page" && state.tab === "briefing") setTab("top");
    localStorage.setItem("na.layout", l);
  }
  function setTab(tab) {
    if (tab) state.tab = tab;
    document.querySelectorAll("#tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === state.tab && !(body.dataset.layout === "page" && body.classList.contains("page-reading") && state.view !== "briefing"))));
    if (tab === "briefing") { body.classList.add("page-reading"); state.view = "briefing"; renderPane(); return; }
    if (tab) body.classList.remove("page-reading");
    renderList();
  }
  function setScope(id) {
    if (!SCOPES[id]) return;
    state.scope = id; state.topic = null; state.selected = null; state.view = "briefing"; state.breakingDismissed = false;
    localStorage.setItem("na.scope", id);
    renderCrumbs(); renderList(); renderPane();
  }

  // ---------- divider drag ----------
  const div = $("#divider");
  const savedW = localStorage.getItem("na.paneW");
  if (savedW) document.documentElement.style.setProperty("--pane-w", savedW);
  const setW = (px) => { const w = Math.round(Math.max(340, Math.min(px, $("#work").clientWidth * 0.68))); const v = w + "px"; document.documentElement.style.setProperty("--pane-w", v); localStorage.setItem("na.paneW", v); };
  div.addEventListener("pointerdown", (e) => {
    div.setPointerCapture(e.pointerId); div.classList.add("is-drag");
    const right = $("#work").getBoundingClientRect().right;
    const move = (ev) => setW(right - ev.clientX - 4);
    const up = () => { div.classList.remove("is-drag"); div.removeEventListener("pointermove", move); div.removeEventListener("pointerup", up); };
    div.addEventListener("pointermove", move); div.addEventListener("pointerup", up);
  });
  div.addEventListener("dblclick", () => { document.documentElement.style.setProperty("--pane-w", "42%"); localStorage.removeItem("na.paneW"); });
  div.addEventListener("keydown", (e) => {
    const cur = $("#pane").getBoundingClientRect().width;
    if (e.key === "ArrowLeft") setW(cur + 32), e.preventDefault();
    if (e.key === "ArrowRight") setW(cur - 32), e.preventDefault();
  });

  // ---------- phone sheet drag ----------
  let sheetY = null;
  $("#pane").addEventListener("touchstart", (e) => { if (e.target.closest(".pane-head")) sheetY = e.touches[0].clientY; }, { passive: true });
  $("#pane").addEventListener("touchend", (e) => { if (sheetY == null) return; const dy = e.changedTouches[0].clientY - sheetY; if (Math.abs(dy) > 30) setMin(dy > 0); sheetY = null; });

  // ---------- source card ----------
  const card = $("#sourceCard");
  let cardTimer;
  function showCard(anchor, dom) {
    const S = D.SOURCES[dom];
    card.innerHTML = `<h5>${esc(S.name)}</h5><span class="dom">${esc(dom)}</span>
      <dl>
        <dt>Reliability</dt><dd><span class="band ${S.band}">${BAND[S.band]}</span>, high confidence</dd>
        <dt>Type</dt><dd>${esc(S.type)}</dd>
        <dt>Based in</dt><dd>${esc(S.country)}</dd>
        <dt>Owner</dt><dd>${esc(S.owner)}</dd>
        <dt>Expert ratings</dt><dd>2 of 2 rating sets rate it reliable</dd>
        <dt>Fact checks</dt><dd>No content rated false in 90 days</dd>
        <dt>Corroboration</dt><dd>9 in 10 of its lead stories carried by 2 or more reliable outlets</dd>
        <dt>Political lean</dt><dd>Not rated by a named rating set</dd>
      </dl>
      <p class="ill">Prototype: values are illustrative. In the app each line names its origin and date.</p>`;
    card.hidden = false;
    const r = anchor.getBoundingClientRect();
    const top = r.bottom + 8 + card.offsetHeight > innerHeight ? r.top - card.offsetHeight - 8 : r.bottom + 8;
    card.style.left = Math.max(12, Math.min(r.left - 20, innerWidth - card.offsetWidth - 12)) + "px";
    card.style.top = Math.max(12, top) + "px";
  }
  const hideCard = () => { cardTimer = setTimeout(() => (card.hidden = true), 120); };
  document.addEventListener("mouseover", (e) => {
    const t = e.target.closest("[data-src],[data-cite]");
    if (t) { clearTimeout(cardTimer); showCard(t, t.dataset.src || D.A[t.dataset.cite].src); }
    else if (e.target.closest("#sourceCard")) clearTimeout(cardTimer);
  });
  document.addEventListener("mouseout", (e) => { if (e.target.closest("[data-src],[data-cite],#sourceCard")) hideCard(); });
  document.addEventListener("focusin", (e) => { const t = e.target.closest("[data-src],[data-cite]"); if (t) showCard(t, t.dataset.src || D.A[t.dataset.cite].src); });
  document.addEventListener("focusout", (e) => { if (e.target.closest("[data-src],[data-cite]")) card.hidden = true; });

  // ---------- events ----------
  document.addEventListener("click", (e) => {
    const t = e.target;
    if (t.closest("#layoutSeg button")) return setLayout(t.closest("button").dataset.layout);
    if (t.closest("#themeBtn")) { const light = document.documentElement.dataset.theme === "dark"; document.documentElement.dataset.theme = light ? "light" : "dark"; $("#themeBtn").textContent = light ? "Dark theme" : "Light theme"; return; }
    if (t.closest("[data-src]")) { e.stopPropagation(); const b = t.closest("[data-src]"); card.hidden ? showCard(b, b.dataset.src) : (card.hidden = true); return; }
    if (t.closest("[data-dismiss]")) { state.breakingDismissed = true; return renderList(); }
    if (t.closest("[data-open]")) return openStory(t.closest("[data-open]").dataset.open);
    if (t.closest("[data-back]")) { state.view = "briefing"; state.selected = null; if (body.dataset.layout === "page") setTab("briefing"); renderList(); return renderPane(); }
    if (t.closest(".story")) return openStory(t.closest(".story").dataset.id);
    if (t.closest("#tabs button")) return setTab(t.closest("button").dataset.tab);
    if (t.closest("#paneToggle") || t.closest("#paneGrip")) return setMin(!body.classList.contains("pane-min"));
    if (body.dataset.layout === "split" && body.classList.contains("pane-min") && t.closest("#pane")) return setMin(false);
    if (t.closest("#drawerBtn")) { state.view = "briefing"; renderPane(); return setMin(!body.classList.contains("pane-min")); }
    if (t.closest("[data-where]")) { const w = t.closest("[data-where]").dataset.where; return wherePopover(t.closest("button"), w === "tn" ? "tn" : w === "india" ? "india" : w === "chennai" ? "tn" : "global"); }
    if (t.closest("[data-where-add]")) return wherePopover(t.closest("button"), state.scope === "india" ? "india" : "global");
    if (t.closest("[data-what]")) return whatPopover(t.closest("button"));
    if (t.closest("[data-go]")) { closePopover(); return setScope(t.closest("[data-go]").dataset.go); }
    if (t.closest("[data-topic]")) { const v = t.closest("[data-topic]").dataset.topic; state.topic = v === "all" ? null : v; closePopover(); renderCrumbs(); return renderList(); }
    if (!t.closest("#popover")) closePopover();
  });

  document.addEventListener("keydown", (e) => {
    if (e.target.matches("input, textarea")) return;
    const rows = [...document.querySelectorAll(".story")];
    const i = rows.findIndex((r) => r.dataset.id === state.selected);
    if (e.key === "j" || e.key === "k") { const n = rows[Math.max(0, Math.min(rows.length - 1, i + (e.key === "j" ? 1 : -1)))]; if (n) { openStory(n.dataset.id); document.querySelector(`.story[data-id="${n.dataset.id}"]`)?.focus(); } }
    if (e.key === "Enter" && e.target.classList.contains("story")) openStory(e.target.dataset.id);
    if (e.key === "b") setMin(!body.classList.contains("pane-min"));
    if (e.key === "Escape") { closePopover(); card.hidden = true; if (body.dataset.layout === "drawer") setMin(true); }
    if (e.key === "/") { e.preventDefault(); $("#searchBtn").focus(); }
  });

  // ---------- start ----------
  const params = new URLSearchParams(location.search);
  if (params.get("scope")) state.scope = params.get("scope");
  setLayout(params.get("layout") || localStorage.getItem("na.layout") || "split");
  if (params.get("min")) setMin(params.get("min") === "1");
  if (params.get("theme")) document.documentElement.dataset.theme = params.get("theme");
  renderCrumbs(); renderList(); renderPane();
  if (params.get("story")) openStory(params.get("story"));
  if (params.get("tab")) setTab(params.get("tab"));
})();
