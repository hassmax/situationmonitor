(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const params = new URLSearchParams(location.search);
  const DEMO = params.has("demo");
  const REFRESH_MS = 5 * 60 * 1000;

  // ------------------------------------------------------------------ vocab
  const STATUS = {
    corroborated: {
      label: "Corroborated",
      rgb: [255, 91, 58],
      note: (e) => `Reported by ${e.sources_count} independent sources${e.news_nearby >= 3 ? ", including nearby news coverage" : ""}.`,
    },
    unconfirmed: {
      label: "Single source",
      rgb: [243, 193, 75],
      note: () => "Only one source so far. Treat it as unverified.",
    },
    claimed: {
      label: "One side's claim",
      rgb: [169, 150, 255],
      note: () => "Reported only by sources aligned with one side of the conflict.",
    },
  };
  const TYPES = {
    airstrike: "Airstrike",
    missile_drone: "Missile or drone attack",
    artillery: "Shelling",
    ground: "Ground fighting",
    territory: "Territorial change",
    air_defense: "Air defense",
    naval: "Naval incident",
    explosion: "Explosion",
    deployment: "Deployment or exercise",
    diplomacy: "Diplomacy",
    ceasefire: "Diplomacy",
    hybrid: "Hybrid attack",
    incursion: "Airspace or border incursion",
  };
  const PLATFORM = { bluesky: "Bluesky", telegram: "Telegram", rss: "News feed", gdelt: "GDELT" };
  const KIND = { official: "Official", partisan: "Partisan", osint: "OSINT", news: "News" };
  const WINDOWS = [["6h", 6], ["24h", 24], ["3d", 72], ["7d", 168]];
  const NEWS_RGB = [63, 193, 201];
  const FALLBACK_THEATERS = [
    { id: "ukraine", name: "Russia–Ukraine", camera: { lat: 48.5, lng: 34, altitude: 0.85 }, highlight: ["804"] },
    { id: "nato_east", name: "NATO flank and hybrid", camera: { lat: 56, lng: 22, altitude: 1.1 }, highlight: ["233", "428", "440", "246", "616"] },
    { id: "mideast", name: "Middle East", camera: { lat: 28.5, lng: 45, altitude: 1.25 }, highlight: [] },
    { id: "horn", name: "Sudan and the Horn of Africa", camera: { lat: 11, lng: 36, altitude: 1.2 }, highlight: [] },
    { id: "drc_sahel", name: "Eastern DRC and the Sahel", camera: { lat: 8, lng: 10, altitude: 1.6 }, highlight: [] },
    { id: "indopac", name: "Indo-Pacific", camera: { lat: 22, lng: 118, altitude: 1.5 }, highlight: [] },
  ];

  const rgba = (c, a = 1) => `rgba(${c[0]},${c[1]},${c[2]},${a})`;
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
  const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? u : "#");

  function ago(ms) {
    const s = Math.max(0, (Date.now() - ms) / 1000);
    if (s < 60) return "just now";
    const m = s / 60;
    if (m < 60) return `${Math.round(m)} min ago`;
    const h = m / 60;
    if (h < 24) return `${Math.round(h)} h ago`;
    return `${Math.round(h / 24)} d ago`;
  }
  const agoShort = (ms) => ago(ms).replace(" ago", "").replace(" min", "m").replace(" h", "h").replace(" d", "d");
  const fmtTime = (ms) => new Date(ms).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  const markHtml = (status) => `<span class="mark mark--${status}" aria-hidden="true"></span>`;

  // ------------------------------------------------------------------ state
  const S = {
    data: null,
    theaters: FALLBACK_THEATERS,
    windowH: 24,
    theaterOn: new Set(FALLBACK_THEATERS.map((t) => t.id)),
    statusOn: new Set(Object.keys(STATUS)),
    layers: { heat: true, arcs: true, diplomacy: true },
    query: "",
    selectedId: null,
    hot: new Set(),
    firstLoad: true,
    lastFocus: null,
  };

  // ------------------------------------------------------------------ globe
  const globeEl = $("#globe");
  const world = new Globe(globeEl, { animateIn: !reduceMotion });

  world
    .backgroundColor("rgba(0,0,0,0)")
    .showGraticules(true)
    .showAtmosphere(true)
    .atmosphereColor("#5aaee6")
    .atmosphereAltitude(0.17)
    .pointOfView({ lat: 18, lng: 10, altitude: 3.1 });

  const mat = world.globeMaterial();
  mat.color.set("#0b1f36");
  if (mat.emissive) mat.emissive.set("#06121f");
  mat.shininess = 5;

  const controls = world.controls();
  controls.autoRotate = !reduceMotion;
  controls.autoRotateSpeed = 0.22;
  controls.minDistance = 112;
  controls.maxDistance = 650;
  controls.addEventListener("start", () => { controls.autoRotate = false; });

  // land as a dot field, theater countries brighter
  fetch("assets/countries-110m.json")
    .then((r) => r.json())
    .then((topo) => {
      const land = topojson.feature(topo, topo.objects.countries).features
        .filter((f) => f.properties.name !== "Antarctica")
        .map(sanitize)
        .filter(Boolean);
      world
        .hexPolygonsData(land)
        .hexPolygonResolution(3)
        .hexPolygonMargin(0.28)
        .hexPolygonUseDots(true)
        .hexPolygonAltitude(0.002)
        .hexPolygonColor(landColor);
    })
    .catch(() => {});

  // Zero-area rings (a few exist in the 110m shapes) make the hex tiler throw.
  function sanitize(f) {
    const g = f.geometry;
    if (!g) return null;
    const distinct = (ring) => new Set(ring.map((p) => p[0].toFixed(4) + "," + p[1].toFixed(4))).size;
    const polys = (g.type === "Polygon" ? [g.coordinates] : g.coordinates).filter((p) => p[0] && distinct(p[0]) >= 3);
    if (!polys.length) return null;
    return { ...f, geometry: { type: "MultiPolygon", coordinates: polys } };
  }

  function landColor(f) {
    return S.hot.has(f.id) ? "#86b6e3" : "#284a6e";
  }

  // Keep markers a similar size on screen as the camera zooms in and out.
  let zoomK = 1.6;
  const isDiplomacy = (e) => e.type === "diplomacy" || e.type === "ceasefire";
  const pointRadius = (e) => (0.1 + e.severity * 0.055) * zoomK * (e.id === S.selectedId ? 1.7 : 1) * (isDiplomacy(e) ? 1.5 : 1);
  world.onZoom(({ altitude }) => {
    const k = Math.max(0.4, Math.min(2.6, altitude)) / 1.3;
    if (Math.abs(k - zoomK) / zoomK > 0.12) {
      zoomK = k;
      world.pointRadius((e) => pointRadius(e));
      world.ringMaxRadius((r) => r.max * zoomK);
    }
  });

  function tipHtml(e) {
    return `<div class="tip">
      <div class="tip-meta"><b>${esc(TYPES[e.type] || "Event")}</b><span>${esc(e.place || "")}</span></div>
      <div class="tip-sum">${esc(e.summary)}</div>
      <div class="tip-foot">${markHtml(e.status)}<span>${esc(STATUS[e.status].label)}</span><span>${esc(ago(e._t))}</span></div>
    </div>`;
  }

  world
    .pointLat("lat")
    .pointLng("lon")
    .pointAltitude((e) => (isDiplomacy(e) ? 0.003 : 0.01 + e.severity * 0.016))
    .pointRadius((e) => pointRadius(e))
    .pointColor((e) => rgba(STATUS[e.status].rgb, e.status === "unconfirmed" ? 0.72 : 0.95))
    .pointResolution(10)
    .pointLabel(tipHtml)
    .onPointClick((e) => select(e.id, true));

  world
    .ringLat("lat")
    .ringLng("lon")
    .ringColor((r) => (t) => rgba(r.rgb, Math.max(0, 1 - t) * r.alpha))
    .ringMaxRadius((r) => r.max * zoomK)
    .ringPropagationSpeed((r) => r.speed)
    .ringRepeatPeriod((r) => r.period)
    .ringAltitude(0.004);

  world
    .arcStartLat((e) => e.origin.lat)
    .arcStartLng((e) => e.origin.lon)
    .arcEndLat("lat")
    .arcEndLng("lon")
    .arcColor((e) => [rgba(STATUS[e.status].rgb, 0.05), rgba(STATUS[e.status].rgb, 0.9)])
    .arcStroke(0.32)
    .arcDashLength(0.45)
    .arcDashGap(0.18)
    .arcDashInitialGap(() => Math.random())
    .arcDashAnimateTime(reduceMotion ? 0 : 2600)
    .arcAltitudeAutoScale(0.38)
    .arcLabel(tipHtml)
    .onArcClick((e) => select(e.id, true));

  world
    .hexBinPointLat("lat")
    .hexBinPointLng("lon")
    .hexBinPointWeight("w")
    .hexBinResolution(3)
    .hexMargin(0.18)
    .hexAltitude((d) => Math.min(0.06, 0.006 * Math.sqrt(d.sumWeight)))
    .hexTopColor((d) => rgba(NEWS_RGB, Math.min(0.85, 0.32 + d.sumWeight / 40)))
    .hexSideColor((d) => rgba(NEWS_RGB, Math.min(0.5, 0.14 + d.sumWeight / 80)))
    .hexBinMerge(true)
    .hexTransitionDuration(reduceMotion ? 0 : 700);

  function layout() {
    world.width(window.innerWidth).height(window.innerHeight);
    const mobile = window.innerWidth < 860;
    if (mobile) {
      const sheet = $("#feed").getBoundingClientRect();
      const top = $(".brand").getBoundingClientRect().bottom;
      world.globeOffset([0, (top - (window.innerHeight - sheet.top)) / 2]);
    } else {
      const left = $(".filters").getBoundingClientRect().right;
      const right = window.innerWidth - $("#feed").getBoundingClientRect().left;
      world.globeOffset([(left - right) / 2, 0]);
    }
  }
  window.addEventListener("resize", layout);

  // ------------------------------------------------------------------ data
  async function load() {
    const url = DEMO ? "data/demo-events.json" : `data/events.json?t=${Date.now()}`;
    try {
      const res = await fetch(url, { cache: "no-store" });
      if (!res.ok) throw new Error(res.status === 404 ? "missing" : `HTTP ${res.status}`);
      ingest(await res.json());
    } catch (err) {
      showLoadError(err);
    }
  }

  function ingest(data) {
    if (DEMO && data.generated_at) {
      // shift demo timestamps so the sample always looks recent
      const shift = Date.now() - Date.parse(data.generated_at);
      const move = (s) => new Date(Date.parse(s) + shift).toISOString();
      data.generated_at = move(data.generated_at);
      data.events.forEach((e) => {
        e.time = move(e.time); e.updated = move(e.updated);
        (e.reports || []).forEach((r) => { r.time = move(r.time); });
      });
      (data.heat || []).forEach((c) => { c.first = move(c.first); c.last = move(c.last); });
    }
    data.events = (data.events || []).filter((e) => STATUS[e.status] && isFinite(e.lat) && isFinite(e.lon));
    for (const e of data.events) {
      e._t = Date.parse(e.updated || e.time);
      e._search = [e.summary, e.place, TYPES[e.type], ...(e.reports || []).map((r) => r.source)].join(" ").toLowerCase();
    }
    data.heat = (data.heat || []).map((c) => ({ ...c, _t: Date.parse(c.last) }));
    const theaters = Array.isArray(data.theaters) && data.theaters.length ? data.theaters : FALLBACK_THEATERS;
    if (S.firstLoad) {
      S.theaterOn = new Set(theaters.map((t) => t.id));
    } else {
      theaters.forEach((t) => { if (!S.theaters.some((x) => x.id === t.id)) S.theaterOn.add(t.id); });
    }
    S.theaters = theaters;
    S.hot = new Set(theaters.flatMap((t) => t.highlight || []));
    world.hexPolygonColor((f) => landColor(f));
    S.data = data;

    renderTheaters();
    renderSources();
    render();
    updateFreshness();

    if (S.firstLoad) {
      S.firstLoad = false;
      const fromHash = decodeURIComponent(location.hash.slice(1));
      if (fromHash && data.events.some((e) => e.id === fromHash)) {
        select(fromHash, true);
      } else {
        const alt = window.innerWidth < 860 ? 3.0 : 2.25;
        world.pointOfView({ lat: 27, lng: 40, altitude: alt }, reduceMotion ? 0 : 2600);
      }
    }
  }

  function showLoadError(err) {
    const list = $("#feedList");
    if (S.data) { updateFreshness(); return; }
    $("#freshText").textContent = "No data yet";
    $("#beacon").className = "beacon dead";
    list.innerHTML = err.message === "missing"
      ? `<li class="empty"><strong>No data yet.</strong>The first update runs within about 15 minutes of setup. If this persists, open your repository's Actions tab and check the "Update conflict data" workflow. Add <code>?demo</code> to this page's address to preview the layout with sample data.</li>`
      : `<li class="empty"><strong>Couldn't load events.</strong>${esc(err.message)}. The page will try again in 5 minutes.</li>`;
  }

  // ------------------------------------------------------------------ filtering
  function passesBase(e, ignoreTheater = false) {
    if (e._t < Date.now() - S.windowH * 3600e3) return false;
    if (!ignoreTheater && !S.theaterOn.has(e.theater)) return false;
    if (!S.statusOn.has(e.status)) return false;
    if (!S.layers.diplomacy && isDiplomacy(e)) return false;
    const q = S.query.trim().toLowerCase();
    if (q && !e._search.includes(q)) return false;
    return true;
  }

  function visibleEvents() {
    return S.data ? S.data.events.filter((e) => passesBase(e)).sort((a, b) => b._t - a._t) : [];
  }

  function visibleHeat() {
    if (!S.data || !S.layers.heat) return [];
    const since = Date.now() - S.windowH * 3600e3;
    return S.data.heat.filter((c) => c._t >= since && S.theaterOn.has(c.theater));
  }

  // ------------------------------------------------------------------ render
  function render() {
    const events = visibleEvents();
    world.pointsData(events);
    world.pointRadius((e) => pointRadius(e));
    world.arcsData(S.layers.arcs ? events.filter((e) => e.origin && isFinite(e.origin.lat)) : []);
    world.hexBinPointsData(visibleHeat());
    renderRings(events);
    renderFeed(events);
    renderCounts();
    renderTally(events);
  }

  function renderRings(events) {
    const rings = [];
    if (!reduceMotion) {
      const fresh = Date.now() - 3 * 3600e3;
      for (const e of events) {
        if (e._t >= fresh) {
          rings.push({ lat: e.lat, lon: e.lon, rgb: STATUS[e.status].rgb, alpha: 0.75, max: 2 + e.severity, speed: 1.6, period: 1600 + Math.random() * 900 });
        }
      }
    }
    const sel = S.selectedId && events.find((e) => e.id === S.selectedId);
    if (sel) rings.push({ lat: sel.lat, lon: sel.lon, rgb: [234, 240, 246], alpha: 0.9, max: 4.5, speed: reduceMotion ? 0 : 2.4, period: 1100 });
    world.ringsData(rings);
  }

  function renderTally(events) {
    const label = WINDOWS.find(([, h]) => h === S.windowH)[0];
    const span = { "6h": "6 hours", "24h": "24 hours", "3d": "3 days", "7d": "7 days" }[label];
    const corroborated = events.filter((e) => e.status === "corroborated").length;
    $("#tally").innerHTML = `<strong>${events.length}</strong> events in the last ${span}, <strong>${corroborated}</strong> corroborated`;
  }

  function renderFeed(events) {
    const list = $("#feedList");
    if (!S.data) return;
    if (!events.length) {
      const anyAtAll = S.data.events.length > 0;
      list.innerHTML = anyAtAll
        ? `<li class="empty"><strong>Nothing matches these filters.</strong>Widen the time window or turn more theaters and confidence levels back on.</li>`
        : `<li class="empty"><strong>No events in the last 7 days yet.</strong>The pipeline is running. New events appear here as sources report them.</li>`;
      return;
    }
    const name = Object.fromEntries(S.theaters.map((t) => [t.id, t.name]));
    list.innerHTML = events.slice(0, 250).map((e) => `
      <li><button class="item sev-${e.severity}" type="button" data-id="${esc(e.id)}" ${e.id === S.selectedId ? 'aria-current="true"' : ""}>
        ${markHtml(e.status)}
        <span>
          <span class="item-meta">
            <span class="item-type">${esc(TYPES[e.type] || "Event")}</span>
            <span class="item-place">${esc(e.place || "")}</span>
            <time datetime="${esc(e.updated)}">${esc(agoShort(e._t))}</time>
          </span>
          <span class="item-summary">${esc(e.summary)}</span>
          <span class="item-foot">
            <span class="sr">${esc(STATUS[e.status].label)}.</span>
            <span>${esc(name[e.theater] || e.theater)}</span>
            <span>${e.sources_count} ${e.sources_count === 1 ? "source" : "sources"}</span>
          </span>
        </span>
      </button></li>`).join("");
  }

  const CROSSHAIR = `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><circle cx="8" cy="8" r="4.5"/><path d="M8 1v3M8 12v3M1 8h3M12 8h3"/></svg>`;

  function renderTheaters() {
    $("#theaterList").innerHTML = S.theaters.map((t) => `
      <li>
        <label class="check">
          <input type="checkbox" data-theater="${esc(t.id)}" ${S.theaterOn.has(t.id) ? "checked" : ""}>
          <span class="box" aria-hidden="true"></span>
          <span class="label">${esc(t.name)}</span>
          <span class="count" data-count="${esc(t.id)}"></span>
        </label>
        ${t.camera ? `<button class="fly" type="button" data-fly="${esc(t.id)}" aria-label="Fly to ${esc(t.name)}" title="Fly to ${esc(t.name)}">${CROSSHAIR}</button>` : '<span class="fly" aria-hidden="true"></span>'}
      </li>`).join("");
  }

  function renderCounts() {
    if (!S.data) return;
    const counts = {};
    for (const e of S.data.events) if (passesBase(e, true)) counts[e.theater] = (counts[e.theater] || 0) + 1;
    document.querySelectorAll("[data-count]").forEach((el) => { el.textContent = counts[el.dataset.count] || 0; });
    const byStatus = {};
    for (const e of S.data.events) {
      if (e._t < Date.now() - S.windowH * 3600e3 || !S.theaterOn.has(e.theater)) continue;
      byStatus[e.status] = (byStatus[e.status] || 0) + 1;
    }
    document.querySelectorAll("[data-status-count]").forEach((el) => { el.textContent = byStatus[el.dataset.statusCount] || 0; });
  }

  function renderSources() {
    const src = (S.data && S.data.sources) || [];
    const ok = src.filter((s) => s.ok).length;
    $("#sourcesToggle").textContent = src.length ? `Sources: ${ok} of ${src.length} reporting` : "Sources";
    $("#sourcesList").innerHTML = src.map((s) => {
      const last = s.last_post ? `last post ${ago(Date.parse(s.last_post))}` : "no posts yet";
      const quiet = s.ok && s.last_post && Date.now() - Date.parse(s.last_post) > 3 * 86400e3;
      const meta = s.ok ? `${PLATFORM[s.platform] || s.platform}, ${last}` : `${PLATFORM[s.platform] || s.platform}: ${s.error}`;
      return `<li><span class="dot ${s.ok ? (quiet ? "quiet" : "") : "bad"}" aria-hidden="true"></span>
        <span><span class="name">${esc(s.name)}</span><span class="meta">${esc(meta)}</span></span></li>`;
    }).join("");
  }

  function updateFreshness() {
    if (!S.data) return;
    const t = Date.parse(S.data.generated_at);
    const age = Date.now() - t;
    const beacon = $("#beacon");
    beacon.className = "beacon " + (age < 45 * 60e3 ? "ok" : age < 3 * 3600e3 ? "stale" : "dead");
    $("#freshText").textContent = age < 3 * 3600e3
      ? `Updated ${ago(t)}`
      : `Updated ${ago(t)}. The update job may be paused.`;
  }

  // ------------------------------------------------------------------ detail
  function select(id, fly) {
    const e = S.data && S.data.events.find((x) => x.id === id);
    if (!e) return;
    S.selectedId = id;
    history.replaceState(null, "", "#" + encodeURIComponent(id));
    controls.autoRotate = false;
    if (fly) {
      const alt = Math.min(world.pointOfView().altitude, 1.15);
      world.pointOfView({ lat: e.lat, lng: e.lon, altitude: alt }, reduceMotion ? 0 : 1300);
    }
    renderDetail(e);
    render();
    if (window.innerWidth < 860) $("#feed").classList.add("expanded");
  }

  function closeDetail() {
    S.selectedId = null;
    history.replaceState(null, "", location.pathname + location.search);
    $("#detail").hidden = true;
    $("#feedList").hidden = false;
    $("#feedHead").hidden = false;
    render();
    if (S.lastFocus) {
      const again = document.querySelector(`.item[data-id="${CSS.escape(S.lastFocus)}"]`);
      if (again) again.focus();
    }
  }

  function nearbyNews(e) {
    if (!S.data) return [];
    const toRad = (d) => (d * Math.PI) / 180;
    const dist = (a, b, c, d) => {
      const x = Math.sin(toRad(c - a) / 2) ** 2 + Math.cos(toRad(a)) * Math.cos(toRad(c)) * Math.sin(toRad(d - b) / 2) ** 2;
      return 12742 * Math.asin(Math.min(1, Math.sqrt(x)));
    };
    return S.data.heat.filter((c) => dist(e.lat, e.lon, c.lat, c.lon) <= (e.approx ? 60 : 30)).flatMap((c) => c.urls || []).slice(0, 4);
  }

  function renderDetail(e) {
    const theaterName = (S.theaters.find((t) => t.id === e.theater) || {}).name || e.theater;
    const facts = [];
    if (e.killed != null) facts.push(`<span>Killed <b>${e.killed}</b> (reported)</span>`);
    if (e.injured != null) facts.push(`<span>Injured <b>${e.injured}</b> (reported)</span>`);
    if (e.origin) facts.push(`<span>Launched from <b>${esc(e.origin.place || "an unnamed location")}</b></span>`);
    const news = nearbyNews(e);
    const reports = (e.reports || []).slice().sort((a, b) => Date.parse(b.time) - Date.parse(a.time));

    $("#detail").innerHTML = `
      <button class="back" type="button" id="backBtn">
        <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M10 3 5 8l5 5"/></svg>
        Back to the list
      </button>
      <div class="detail-type">${esc(TYPES[e.type] || "Event")}</div>
      <h3>${esc(e.summary)}</h3>
      <p class="detail-where">${esc(e.place || "Unnamed location")}, ${esc(theaterName)}
        ${e.approx ? '<span class="approx">(approximate location)</span>' : ""}<br>
        First reported ${esc(fmtTime(Date.parse(e.time)))}, last update ${esc(ago(e._t))}</p>
      <div class="verdict">
        ${markHtml(e.status)}
        <div><strong>${esc(STATUS[e.status].label)}</strong><p>${esc(STATUS[e.status].note(e))}</p></div>
      </div>
      ${facts.length ? `<div class="facts">${facts.join("")}</div>` : ""}
      <h2 class="reports-title">Reports (${reports.length})</h2>
      <ul class="reports">
        ${reports.map((r) => `
          <li class="report ${r.side ? "sided" : ""}">
            <div class="report-head">
              <span class="report-src">${esc(r.source)}</span>
              <span>${esc(PLATFORM[r.platform] || r.platform)}</span>
              <span>${esc(KIND[r.kind] || r.kind)}${r.side ? `, aligned with ${esc(r.side)}` : ""}</span>
              <span>${esc(ago(Date.parse(r.time)))}</span>
            </div>
            <p>${esc(r.summary)}</p>
            <a href="${esc(safeUrl(r.url))}" target="_blank" rel="noopener noreferrer">Open the original post</a>
          </li>`).join("")}
      </ul>
      ${news.length ? `
        <h2 class="reports-title">News coverage nearby (${e.news_nearby || news.length} outlets, via GDELT)</h2>
        <ul class="news-links">${news.map((u) => `<li><a href="${esc(safeUrl(u))}" target="_blank" rel="noopener noreferrer">${esc(u.replace(/^https?:\/\/(www\.)?/, "").slice(0, 80))}</a></li>`).join("")}</ul>` : ""}
    `;
    $("#feedList").hidden = true;
    $("#feedHead").hidden = true;
    $("#detail").hidden = false;
    $("#detail").scrollTop = 0;
    $("#backBtn").addEventListener("click", closeDetail);
    $("#backBtn").focus({ preventScroll: true });
  }

  // ------------------------------------------------------------------ controls
  function buildStaticControls() {
    $("#windowSeg").innerHTML = WINDOWS.map(([label, h]) =>
      `<button type="button" data-window="${h}" aria-pressed="${h === S.windowH}">${label}</button>`).join("");

    $("#statusList").innerHTML = Object.entries(STATUS).map(([id, s]) => `
      <li><label class="check">
        <input type="checkbox" data-status="${id}" checked>
        ${markHtml(id)}
        <span class="label">${esc(s.label)}</span>
        <span class="count" data-status-count="${id}"></span>
      </label></li>`).join("");

    $("#layerList").innerHTML = `
      <li><label class="check">
        <input type="checkbox" data-layer="heat" checked>
        <span class="swatch-news" aria-hidden="true"></span>
        <span class="label">News intensity (GDELT)</span><span class="count"></span>
      </label></li>
      <li><label class="check">
        <input type="checkbox" data-layer="arcs" checked>
        <svg class="swatch-arc" viewBox="0 0 16 14" fill="none" stroke="currentColor" stroke-width="1.6" stroke-dasharray="2.5 2" aria-hidden="true"><path d="M1.5 12.5C3 4 13 4 14.5 12.5"/></svg>
        <span class="label">Launch paths</span><span class="count"></span>
      </label></li>
      <li><label class="check">
        <input type="checkbox" data-layer="diplomacy" checked>
        <span class="swatch-diplo" aria-hidden="true"></span>
        <span class="label">Diplomacy</span><span class="count"></span>
      </label></li>`;
  }

  function wire() {
    $("#windowSeg").addEventListener("click", (ev) => {
      const b = ev.target.closest("[data-window]");
      if (!b) return;
      S.windowH = Number(b.dataset.window);
      document.querySelectorAll("[data-window]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      render();
    });

    $("#filters").addEventListener("change", (ev) => {
      const t = ev.target;
      if (t.dataset.theater) t.checked ? S.theaterOn.add(t.dataset.theater) : S.theaterOn.delete(t.dataset.theater);
      if (t.dataset.status) t.checked ? S.statusOn.add(t.dataset.status) : S.statusOn.delete(t.dataset.status);
      if (t.dataset.layer) S.layers[t.dataset.layer] = t.checked;
      render();
    });

    $("#filters").addEventListener("click", (ev) => {
      const fly = ev.target.closest("[data-fly]");
      if (fly) {
        const t = S.theaters.find((x) => x.id === fly.dataset.fly);
        if (t && t.camera) {
          controls.autoRotate = false;
          world.pointOfView(t.camera, reduceMotion ? 0 : 1500);
          if (window.innerWidth < 860) toggleFilters(false);
        }
        return;
      }
    });

    $("#sourcesToggle").addEventListener("click", () => {
      const list = $("#sourcesList");
      list.hidden = !list.hidden;
      $("#sourcesToggle").setAttribute("aria-expanded", String(!list.hidden));
    });

    $("#feedList").addEventListener("click", (ev) => {
      const b = ev.target.closest("[data-id]");
      if (!b) return;
      S.lastFocus = b.dataset.id;
      select(b.dataset.id, true);
    });

    let searchTimer;
    $("#search").addEventListener("input", (ev) => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => { S.query = ev.target.value; render(); }, 120);
    });

    $("#filtersToggle").addEventListener("click", () => toggleFilters());
    $("#sheetHandle").addEventListener("click", () => {
      const feed = $("#feed");
      feed.classList.toggle("expanded");
      $("#sheetHandle").setAttribute("aria-expanded", String(feed.classList.contains("expanded")));
      setTimeout(layout, 280);
    });

    document.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape") {
        if (S.selectedId) closeDetail();
        else if ($("#filters").classList.contains("open")) toggleFilters(false);
      }
      if (ev.key === "/" && document.activeElement !== $("#search")) {
        ev.preventDefault();
        if (S.selectedId) closeDetail();
        $("#search").focus();
      }
    });
  }

  function toggleFilters(force) {
    const panel = $("#filters");
    const open = force === undefined ? !panel.classList.contains("open") : force;
    panel.classList.toggle("open", open);
    $("#filtersToggle").setAttribute("aria-expanded", String(open));
  }

  // ------------------------------------------------------------------ boot
  buildStaticControls();
  renderTheaters();
  wire();
  layout();
  if (DEMO) $("#demoBanner").hidden = false;
  load();
  if (!DEMO) setInterval(load, REFRESH_MS);
  setInterval(() => {
    updateFreshness();
    document.querySelectorAll("#feedList time").forEach((t) => { t.textContent = agoShort(Date.parse(t.dateTime)); });
  }, 30e3);
})();
