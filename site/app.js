(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const params = new URLSearchParams(location.search);
  const DEMO = params.has("demo");
  const REFRESH_MS = 5 * 60 * 1000;
  const isMobile = () => window.innerWidth < 860;

  // ------------------------------------------------------------------ vocabulary
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
    { id: "horn", name: "Sudan and Horn of Africa", camera: { lat: 11, lng: 36, altitude: 1.2 }, highlight: [] },
    { id: "drc_sahel", name: "Eastern DRC and Sahel", camera: { lat: 8, lng: 10, altitude: 1.6 }, highlight: [] },
    { id: "indopac", name: "Indo-Pacific", camera: { lat: 22, lng: 118, altitude: 1.5 }, highlight: [] },
    { id: "latam", name: "Latin America", camera: { lat: 14, lng: -76, altitude: 1.4 }, highlight: [] },
  ];

  // ISO 3166 alpha-2 -> numeric (the ids used by the country shapes)
  const ISO_NUM = new Map("AD020,AE784,AF004,AG028,AI660,AL008,AM051,AO024,AQ010,AR032,AS016,AT040,AU036,AW533,AX248,AZ031,BA070,BB052,BD050,BE056,BF854,BG100,BH048,BI108,BJ204,BL652,BM060,BN096,BO068,BQ535,BR076,BS044,BT064,BV074,BW072,BY112,BZ084,CA124,CC166,CD180,CF140,CG178,CH756,CI384,CK184,CL152,CM120,CN156,CO170,CR188,CU192,CV132,CW531,CX162,CY196,CZ203,DE276,DJ262,DK208,DM212,DO214,DZ012,EC218,EE233,EG818,EH732,ER232,ES724,ET231,FI246,FJ242,FK238,FM583,FO234,FR250,GA266,GB826,GD308,GE268,GF254,GG831,GH288,GI292,GL304,GM270,GN324,GP312,GQ226,GR300,GS239,GT320,GU316,GW624,GY328,HK344,HM334,HN340,HR191,HT332,HU348,ID360,IE372,IL376,IM833,IN356,IO086,IQ368,IR364,IS352,IT380,JE832,JM388,JO400,JP392,KE404,KG417,KH116,KI296,KM174,KN659,KP408,KR410,KW414,KY136,KZ398,LA418,LB422,LC662,LI438,LK144,LR430,LS426,LT440,LU442,LV428,LY434,MA504,MC492,MD498,ME499,MF663,MG450,MH584,MK807,ML466,MM104,MN496,MO446,MP580,MQ474,MR478,MS500,MT470,MU480,MV462,MW454,MX484,MY458,MZ508,NA516,NC540,NE562,NF574,NG566,NI558,NL528,NO578,NP524,NR520,NU570,NZ554,OM512,PA591,PE604,PF258,PG598,PH608,PK586,PL616,PM666,PN612,PR630,PS275,PT620,PW585,PY600,QA634,RE638,RO642,RS688,RU643,RW646,SA682,SB090,SC690,SD729,SE752,SG702,SH654,SI705,SJ744,SK703,SL694,SM674,SN686,SO706,SR740,SS728,ST678,SV222,SX534,SY760,SZ748,TC796,TD148,TF260,TG768,TH764,TJ762,TK772,TL626,TM795,TN788,TO776,TR792,TT780,TV798,TW158,TZ834,UA804,UG800,UM581,US840,UY858,UZ860,VA336,VC670,VE862,VG092,VI850,VN704,VU548,WF876,WS882,YE887,YT175,ZA710,ZM894,ZW716".split(",").map((s) => [s.slice(0, 2), s.slice(2)]));
  let regionNames = null;
  try { regionNames = new Intl.DisplayNames(["en"], { type: "region" }); } catch (_) { /* old browser */ }
  const countryName = (code) => {
    if (!code) return "";
    try { return (regionNames && regionNames.of(code)) || code; } catch (_) { return code; }
  };

  // Known launch areas, used only when a report doesn't name one. Lines drawn from these
  // are faint and labeled approximate.
  const ANCHORS = {
    RU: [["Kursk", 51.73, 36.19], ["Oryol", 52.97, 36.06], ["Bryansk", 53.24, 34.36], ["Belgorod", 50.6, 36.6],
      ["Millerovo", 48.92, 40.4], ["Primorsko-Akhtarsk", 46.05, 38.17], ["Hvardiiske, Crimea", 45.12, 33.97]],
    UA: [["Sumy", 50.91, 34.8], ["Chernihiv", 51.5, 31.29], ["Kharkiv", 49.99, 36.23], ["Zaporizhzhia", 47.84, 35.14],
      ["Mykolaiv", 46.97, 32.0]],
    IR: [["Kermanshah", 34.31, 47.07], ["Tabriz", 38.08, 46.29], ["Isfahan", 32.65, 51.67], ["Bandar Abbas", 27.18, 56.27]],
    YE: [["Sanaa", 15.37, 44.19], ["Hodeidah", 14.8, 42.95], ["Saada", 16.94, 43.76]],
    LB: [["Nabatieh", 33.38, 35.48], ["Tyre", 33.27, 35.2]],
    IL: [["southern Israel", 31.25, 34.79], ["northern Israel", 32.8, 35.1]],
  };
  Object.keys(ANCHORS).forEach((k) => { ANCHORS[k] = ANCHORS[k].map(([place, lat, lon]) => ({ place, lat, lon })); });

  const rgba = (c, a = 1) => `rgba(${c[0]},${c[1]},${c[2]},${a})`;
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
  const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? u : "#");
  const toRad = (d) => (d * Math.PI) / 180;
  function km(a, b, c, d) {
    const x = Math.sin(toRad(c - a) / 2) ** 2 + Math.cos(toRad(a)) * Math.cos(toRad(c)) * Math.sin(toRad(d - b) / 2) ** 2;
    return 12742 * Math.asin(Math.min(1, Math.sqrt(x)));
  }
  function nearest(list, p) {
    let best = null, bd = Infinity;
    for (const o of list || []) {
      const d = km(o.lat, o.lon, p.lat, p.lon);
      if (d < bd) { best = o; bd = d; }
    }
    return best;
  }
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
  const isDiplomacy = (e) => e.type === "diplomacy" || e.type === "ceasefire";
  const typeLabel = (e) => (e.wave ? "Attack wave" : TYPES[e.type] || "Event");
  const originsOf = (e) => (e.origins && e.origins.length ? e.origins : e.origin ? [e.origin] : []);
  const isKey = (e) => e.severity >= 3 && e.status === "corroborated";

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
    spot: null,
    hot: new Set(),
    active: new Set(),
    activeKey: "",
    points: [],
    firstLoad: true,
    lastFocus: null,
    sheet: 1,
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
  controls.minDistance = 150; // altitude 0.5: closer than this the land pattern gets coarse
  controls.maxDistance = 650;
  controls.addEventListener("start", () => { controls.autoRotate = false; });

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
    if (S.active.has(f.id)) return "#7aaddd";
    return S.hot.has(f.id) ? "#3b6893" : "#264769";
  }
  function borderColor(id) {
    if (S.active.has(id)) return "rgba(255,166,122,0.85)";
    return S.hot.has(id) ? "rgba(150,195,235,0.42)" : "rgba(150,190,230,0.18)";
  }

  fetch("assets/countries-110m.json")
    .then((r) => r.json())
    .then((topo) => {
      const land = topojson.feature(topo, topo.objects.countries).features
        .filter((f) => f.properties.name !== "Antarctica")
        .map(sanitize)
        .filter(Boolean);
      const borders = [];
      for (const f of land) for (const poly of f.geometry.coordinates) for (const ring of poly) borders.push({ fid: f.id, pts: ring });
      world
        .hexPolygonsData(land)
        .hexPolygonResolution(3)
        .hexPolygonMargin(0.3)
        .hexPolygonUseDots(true)
        .hexPolygonAltitude(0.002)
        .hexPolygonColor((f) => landColor(f))
        .pathsData(borders)
        .pathPoints("pts")
        .pathPointLat((p) => p[1])
        .pathPointLng((p) => p[0])
        .pathPointAlt(0.0045)
        .pathTransitionDuration(0)
        .pathColor((p) => borderColor(p.fid));
    })
    .catch(() => {});

  // Keep markers a similar size on screen as the camera zooms in and out.
  let zoomK = 1.6;
  function pointRadius(d) {
    const sel = (d.ref || d).id === S.selectedId ? 1.5 : 1;
    if (d.secondary) return 0.13 * zoomK * sel;
    return (0.16 + d.severity * 0.07) * zoomK * sel * (isDiplomacy(d) ? 1.5 : 1);
  }
  world.onZoom(({ altitude }) => {
    const k = Math.max(0.6, Math.min(2.6, altitude)) / 1.25;
    if (Math.abs(k - zoomK) / zoomK > 0.12) {
      zoomK = k;
      world.pointRadius((d) => pointRadius(d));
      world.ringMaxRadius((r) => r.max * zoomK);
    }
  });

  function tipHtml(e) {
    const meta = e.wave ? `${countryName(e.attacker)} → ${countryName(e.country)}` : e.place || "";
    const extra = e.wave && e.targets && e.targets.length > 1 ? `<span>${e.targets.length} locations</span>` : "";
    return `<div class="tip">
      <div class="tip-meta"><b>${esc(typeLabel(e))}</b><span>${esc(meta)}</span></div>
      <div class="tip-sum">${esc(e.summary)}</div>
      <div class="tip-foot">${markHtml(e.status)}<span>${esc(STATUS[e.status].label)}</span>${extra}<span>${esc(ago(e._t))}</span></div>
    </div>`;
  }
  function tipTarget(d) {
    const e = d.ref;
    return `<div class="tip">
      <div class="tip-meta"><b>${esc(d.place || "Location")}</b><span>part of an attack wave</span></div>
      <div class="tip-sum">${esc(e.summary)}</div>
      <div class="tip-foot">${markHtml(e.status)}<span>${esc(countryName(e.attacker))} → ${esc(countryName(e.country))}</span></div>
    </div>`;
  }

  world
    .pointLat("lat")
    .pointLng("lon")
    .pointAltitude((d) => (d.secondary ? 0.006 : isDiplomacy(d) ? 0.003 : 0.01 + d.severity * 0.016))
    .pointRadius((d) => pointRadius(d))
    .pointColor((d) => rgba(STATUS[d.status].rgb, d.secondary ? 0.75 : d.status === "unconfirmed" ? 0.75 : 0.95))
    .pointResolution(10)
    .pointLabel((d) => (d.secondary ? tipTarget(d) : tipHtml(d)))
    .onPointHover((d) => { globeEl.style.cursor = d ? "pointer" : ""; })
    .onPointClick((d) => select((d.ref || d).id, true));

  world
    .ringLat("lat")
    .ringLng("lon")
    .ringColor((r) => (t) => rgba(r.rgb, Math.max(0, 1 - t) * r.alpha))
    .ringMaxRadius((r) => r.max * zoomK)
    .ringPropagationSpeed((r) => r.speed)
    .ringRepeatPeriod((r) => r.period)
    .ringAltitude(0.006);

  world
    .arcStartLat("sLat")
    .arcStartLng("sLng")
    .arcEndLat("eLat")
    .arcEndLng("eLng")
    .arcColor((a) => [rgba(STATUS[a.status].rgb, a.approx ? 0.02 : 0.06), rgba(STATUS[a.status].rgb, a.approx ? 0.42 : 0.92)])
    .arcStroke((a) => (a.approx ? 0.2 : 0.32))
    .arcDashLength(0.45)
    .arcDashGap(0.2)
    .arcDashInitialGap(() => Math.random())
    .arcDashAnimateTime((a) => (reduceMotion ? 0 : a.approx ? 3400 : 2300))
    .arcAltitudeAutoScale(0.36)
    .arcLabel((a) => tipHtml(a.ref))
    .onArcHover((a) => { globeEl.style.cursor = a ? "pointer" : ""; })
    .onArcClick((a) => select(a.ref.id, true));

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

  // Clicks that land on the globe, a country dot, a border, or a heat hexagon still pick
  // the nearest event, so small markers don't need pixel-perfect aim.
  world
    .onGlobeClick((coords, ev) => pickNear(ev, coords))
    .onHexPolygonClick((_, ev, coords) => pickNear(ev, coords))
    .onPathClick((_, ev, coords) => pickNear(ev, coords))
    .onHexClick((_, ev, coords) => pickNear(ev, coords));

  function pickNear(ev, coords) {
    if (!S.points.length) return;
    const rect = globeEl.getBoundingClientRect();
    const pov = world.pointOfView();
    const horizonDeg = (Math.acos(1 / (1 + pov.altitude)) * 180) / Math.PI - 2;
    const radiusPx = isMobile() ? 34 : 24;
    const havePx = ev && Number.isFinite(ev.clientX);
    const hits = [];
    for (const d of S.points) {
      if (km(pov.lat, pov.lng, d.lat, d.lon) / 111.2 > horizonDeg) continue;
      let dist;
      if (havePx) {
        const s = world.getScreenCoords(d.lat, d.lon, 0.01);
        if (!s) continue;
        dist = Math.hypot(s.x + rect.left - ev.clientX, s.y + rect.top - ev.clientY);
        if (dist > radiusPx) continue;
      } else if (coords) {
        dist = km(coords.lat, coords.lng, d.lat, d.lon);
        if (dist > pov.altitude * 250) continue;
      } else continue;
      hits.push({ id: (d.ref || d).id, dist });
    }
    const ids = [...new Set(hits.sort((a, b) => a.dist - b.dist).map((h) => h.id))];
    if (ids.length === 1) select(ids[0], true);
    else if (ids.length > 1) showSpot(ids, coords);
  }

  function showSpot(ids, coords) {
    S.spot = ids;
    closeDetail(true);
    if (coords) {
      const alt = Math.max(0.55, world.pointOfView().altitude * 0.6);
      controls.autoRotate = false;
      world.pointOfView({ lat: coords.lat, lng: coords.lng, altitude: alt }, reduceMotion ? 0 : 900);
    }
    render();
    if (isMobile() && S.sheet === 0) setSheet(1);
    $("#feedList").scrollTop = 0;
  }

  function layout() {
    world.width(window.innerWidth).height(window.innerHeight);
    if (isMobile()) {
      const sheet = $("#feed").getBoundingClientRect();
      const top = $(".brand").getBoundingClientRect().bottom;
      world.globeOffset([0, (top - (window.innerHeight - sheet.top)) / 2]);
    } else if (document.body.classList.contains("panels-hidden")) {
      world.globeOffset([0, 0]);
    } else {
      const left = $(".filters").getBoundingClientRect().right;
      const right = window.innerWidth - $("#feed").getBoundingClientRect().left;
      world.globeOffset([(left - right) / 2, 0]);
    }
  }
  window.addEventListener("resize", () => {
    if (!isMobile()) $("#feed").style.height = "";
    else setSheet(S.sheet, true);
    layout();
  });

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
        (e.targets || []).forEach((t) => { if (t.time) t.time = move(t.time); });
      });
      (data.heat || []).forEach((c) => { c.first = move(c.first); c.last = move(c.last); });
    }
    data.events = (data.events || []).filter((e) => STATUS[e.status] && isFinite(e.lat) && isFinite(e.lon));
    for (const e of data.events) {
      e._t = Date.parse(e.updated || e.time);
      e._t0 = Date.parse(e.time || e.updated);
      e.targets = Array.isArray(e.targets) ? e.targets.filter((t) => isFinite(t.lat) && isFinite(t.lon)) : [];
      const places = e.targets.map((t) => t.place).join(" ");
      e._search = [e.summary, e.place, places, typeLabel(e), countryName(e.attacker), countryName(e.country),
        ...(e.reports || []).map((r) => r.source)].join(" ").toLowerCase();
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
        const alt = isMobile() ? 3.0 : 2.25;
        world.pointOfView({ lat: 27, lng: 40, altitude: alt }, reduceMotion ? 0 : 2600);
      }
    } else if (S.selectedId) {
      const still = data.events.find((e) => e.id === S.selectedId);
      if (still) renderDetail(still, true);
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
  function buildPoints(events) {
    const pts = [];
    for (const e of events) {
      pts.push(e);
      if (e.wave) {
        e.targets.slice(0, 40).forEach((t, i) => {
          if (Math.abs(t.lat - e.lat) < 1e-4 && Math.abs(t.lon - e.lon) < 1e-4) return;
          pts.push({ id: `${e.id}~${i}`, ref: e, lat: t.lat, lon: t.lon, place: t.place, severity: 1, status: e.status, secondary: true });
        });
      }
    }
    return pts.slice(0, 1500);
  }

  function buildArcs(events) {
    if (!S.layers.arcs) return [];
    const arcs = [];
    const push = (e, o, d, approx) => {
      const dist = km(o.lat, o.lon, d.lat, d.lon);
      if (dist < 25 || (approx && dist > 1800)) return;
      arcs.push({ ref: e, sLat: o.lat, sLng: o.lon, eLat: d.lat, eLng: d.lon, status: e.status, approx });
    };
    for (const e of events) {
      if (arcs.length >= 220) break;
      const origins = originsOf(e);
      if (e.wave) {
        const dests = e.targets.length ? e.targets.slice(0, 20) : [e];
        for (const d of dests) {
          const o = nearest(origins, d);
          if (o) push(e, o, d, false);
          else if (ANCHORS[e.attacker]) push(e, nearest(ANCHORS[e.attacker], d), d, true);
        }
      } else if (origins.length) {
        origins.slice(0, 3).forEach((o) => push(e, o, e, false));
      } else if (e.type === "missile_drone" && ANCHORS[e.attacker]) {
        push(e, nearest(ANCHORS[e.attacker], e), e, true);
      }
    }
    return arcs;
  }

  function render() {
    const events = visibleEvents();
    S.points = buildPoints(events);
    world.pointsData(S.points);
    world.pointRadius((d) => pointRadius(d));
    world.arcsData(buildArcs(events));
    world.hexBinPointsData(visibleHeat());
    renderRings(events);
    updateActiveCountries(events);
    renderFeed(events);
    renderCounts();
    renderTally(events);
  }

  // Countries with events in view, plus the countries attacking them, get warm borders
  // and brighter land.
  function updateActiveCountries(events) {
    const active = new Set();
    for (const e of events) {
      if (isDiplomacy(e)) continue;
      [e.country, e.attacker].forEach((c) => { const n = ISO_NUM.get(c); if (n) active.add(n); });
    }
    const key = [...active].sort().join(",");
    if (key === S.activeKey) return;
    S.activeKey = key;
    S.active = active;
    world.hexPolygonColor((f) => landColor(f));
    world.pathColor((p) => borderColor(p.fid));
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

  function itemHtml(e, names) {
    const meta = e.wave ? `${countryName(e.attacker)} → ${countryName(e.country)}` : e.place || "";
    const extra = [];
    if (e.wave && e.targets.length) extra.push(`${e.targets.length} ${e.targets.length === 1 ? "location" : "locations"}`);
    if (e.wave && e.launched) extra.push(`${e.launched} launched`);
    return `
      <li><button class="item sev-${e.severity}${e.wave ? " is-wave" : ""}" type="button" data-id="${esc(e.id)}" ${e.id === S.selectedId ? 'aria-current="true"' : ""}>
        ${markHtml(e.status)}
        <span>
          <span class="item-meta">
            <span class="item-type">${esc(typeLabel(e))}</span>
            <span class="item-place">${esc(meta)}</span>
            <time datetime="${esc(e.updated)}">${esc(agoShort(e._t))}</time>
          </span>
          <span class="item-summary">${esc(e.summary)}</span>
          <span class="item-foot">
            <span class="sr">${esc(STATUS[e.status].label)}.</span>
            <span>${esc(names[e.theater] || e.theater)}</span>
            ${extra.map((x) => `<span>${esc(x)}</span>`).join("")}
            <span>${e.sources_count} ${e.sources_count === 1 ? "source" : "sources"}</span>
          </span>
        </span>
      </button></li>`;
  }

  function renderFeed(events) {
    const list = $("#feedList");
    if (!S.data) return;
    const names = Object.fromEntries(S.theaters.map((t) => [t.id, t.name]));
    let shown = events;
    let head = "";
    if (S.spot) {
      const ids = new Set(S.spot);
      shown = events.filter((e) => ids.has(e.id));
      head = `<li class="spotbar"><span>${shown.length} events near this spot</span><button class="linkish" type="button" data-clear-spot>Show all</button></li>`;
    }
    $("#feedCount").textContent = S.spot ? "" : `${events.length}`;
    if (!shown.length) {
      const anyAtAll = S.data.events.length > 0;
      list.innerHTML = head + (anyAtAll
        ? `<li class="empty"><strong>Nothing matches these filters.</strong>Widen the time window or turn more theaters and confidence levels back on.</li>`
        : `<li class="empty"><strong>No events in the last 7 days yet.</strong>The pipeline is running. New events appear here as sources report them.</li>`);
      return;
    }
    const key = S.spot ? [] : shown.filter(isKey).slice(0, 4);
    const keyIds = new Set(key.map((e) => e.id));
    const rest = shown.filter((e) => !keyIds.has(e.id)).slice(0, 250);
    list.innerHTML = head
      + (key.length ? `<li class="group">Key developments</li>${key.map((e) => itemHtml(e, names)).join("")}<li class="group">Everything else</li>` : "")
      + rest.map((e) => itemHtml(e, names)).join("");
  }

  const CROSSHAIR = `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><circle cx="8" cy="8" r="4.5"/><path d="M8 1v3M8 12v3M1 8h3M12 8h3"/></svg>`;

  function renderTheaters() {
    $("#theaterList").innerHTML = S.theaters.map((t) => `
      <li>
        <label class="check check--theater">
          <input type="checkbox" data-theater="${esc(t.id)}" ${S.theaterOn.has(t.id) ? "checked" : ""}>
          <span class="box" aria-hidden="true"></span>
          <span class="label">${esc(t.name)}</span>
          <span class="spark" data-spark="${esc(t.id)}" aria-hidden="true"></span>
          <span class="count" data-count="${esc(t.id)}"></span>
        </label>
        ${t.camera ? `<button class="fly" type="button" data-fly="${esc(t.id)}" aria-label="Fly to ${esc(t.name)}" title="Fly to ${esc(t.name)}">${CROSSHAIR}</button>` : '<span class="fly" aria-hidden="true"></span>'}
      </li>`).join("");
  }

  function sparkSvg(counts) {
    const max = Math.max(1, ...counts);
    const bars = counts.map((c, i) => {
      const h = c ? Math.max(1.5, (c / max) * 12) : 0.8;
      return `<rect x="${i * 4}" y="${12 - h}" width="2.8" height="${h}" rx="0.7"${i === counts.length - 1 ? ' class="today"' : ""}/>`;
    }).join("");
    return `<svg viewBox="0 0 27 12" width="27" height="12">${bars}</svg>`;
  }

  function renderCounts() {
    if (!S.data) return;
    const counts = {};
    for (const e of S.data.events) if (passesBase(e, true)) counts[e.theater] = (counts[e.theater] || 0) + 1;
    document.querySelectorAll("[data-count]").forEach((el) => { el.textContent = counts[el.dataset.count] || 0; });

    // 7-day tempo per theater (all confidence levels), oldest day first
    const day = 86400e3;
    const now = Date.now();
    const tempo = {};
    for (const e of S.data.events) {
      const idx = 6 - Math.floor((now - e._t0) / day);
      if (idx < 0 || idx > 6) continue;
      (tempo[e.theater] = tempo[e.theater] || [0, 0, 0, 0, 0, 0, 0])[idx] += 1;
    }
    document.querySelectorAll("[data-spark]").forEach((el) => {
      const c = tempo[el.dataset.spark] || [0, 0, 0, 0, 0, 0, 0];
      el.innerHTML = sparkSvg(c);
      el.parentElement.title = `Events per day, last 7 days: ${c.join(", ")}`;
    });

    const byStatus = {};
    for (const e of S.data.events) {
      if (e._t < now - S.windowH * 3600e3 || !S.theaterOn.has(e.theater)) continue;
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
      const want = e.wave && e.targets.length > 3 ? 1.6 : 1.15;
      const alt = Math.min(world.pointOfView().altitude, want);
      world.pointOfView({ lat: e.lat, lng: e.lon, altitude: alt }, reduceMotion ? 0 : 1300);
    }
    renderDetail(e);
    render();
    if (isMobile() && S.sheet < 2) setSheet(2);
  }

  function closeDetail(keepSpot) {
    S.selectedId = null;
    if (!keepSpot) S.spot = null;
    history.replaceState(null, "", location.pathname + location.search);
    $("#detail").hidden = true;
    $("#feedList").hidden = false;
    $("#feedHead").hidden = false;
    render();
    if (S.lastFocus && !keepSpot) {
      const again = document.querySelector(`.item[data-id="${CSS.escape(S.lastFocus)}"]`);
      if (again) again.focus();
    }
  }

  function nearbyNews(e) {
    if (!S.data) return [];
    return S.data.heat.filter((c) => km(e.lat, e.lon, c.lat, c.lon) <= (e.approx ? 60 : 30)).flatMap((c) => c.urls || []).slice(0, 4);
  }

  function renderDetail(e, refresh = false) {
    const keepScroll = refresh ? $("#detail").scrollTop : 0;
    const theaterName = (S.theaters.find((t) => t.id === e.theater) || {}).name || e.theater;
    const facts = [];
    if (e.launched != null) facts.push(`<span>Launched <b>${e.launched}</b> (reported)</span>`);
    if (e.intercepted != null) facts.push(`<span>Intercepted <b>${e.intercepted}</b> (reported)</span>`);
    if (e.killed != null) facts.push(`<span>Killed <b>${e.killed}</b> (reported)</span>`);
    if (e.injured != null) facts.push(`<span>Injured <b>${e.injured}</b> (reported)</span>`);
    const origins = originsOf(e);
    if (!e.wave && origins.length) facts.push(`<span>Launched from <b>${esc(origins.map((o) => o.place || "an unnamed site").join(", "))}</b></span>`);
    const news = nearbyNews(e);
    const reports = (e.reports || []).slice().sort((a, b) => Date.parse(b.time) - Date.parse(a.time));

    const where = e.wave
      ? `${esc(countryName(e.attacker))} → ${esc(countryName(e.country))}, ${esc(theaterName)}`
      : `${esc(e.place || "Unnamed location")}, ${esc(theaterName)} ${e.approx ? '<span class="approx">(approximate location)</span>' : ""}`;

    const waveBlock = e.wave ? `
      <h2 class="reports-title">Locations (${e.targets.length})</h2>
      ${e.targets.length ? `<ul class="targets">${e.targets.map((t, i) => `
        <li><button class="target" type="button" data-target="${i}">
          <span>${esc(t.place || "Unnamed place")}</span>
          <span class="target-meta">${t.reports} ${t.reports === 1 ? "report" : "reports"}${t.killed ? `, ${t.killed} killed` : ""}</span>
        </button></li>`).join("")}</ul>` : `<p class="muted">No specific locations reported yet.</p>`}
      <h2 class="reports-title">Launch areas</h2>
      <p class="muted">${origins.length
        ? esc(origins.map((o) => o.place || "unnamed site").join(", "))
        : "Not named in the reports so far. Lines on the map start from the nearest known launch area and are drawn faint."}</p>` : "";

    $("#detail").innerHTML = `
      <button class="back" type="button" id="backBtn">
        <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M10 3 5 8l5 5"/></svg>
        Back to the list
      </button>
      <div class="detail-type">${esc(e.wave ? "Drone and missile attack wave" : typeLabel(e))}</div>
      <h3>${esc(e.summary)}</h3>
      <p class="detail-where">${where}<br>
        First reported ${esc(fmtTime(e._t0))}, last update ${esc(ago(e._t))}</p>
      <div class="verdict">
        ${markHtml(e.status)}
        <div><strong>${esc(STATUS[e.status].label)}</strong><p>${esc(STATUS[e.status].note(e))}</p></div>
      </div>
      ${facts.length ? `<div class="facts">${facts.join("")}</div>` : ""}
      ${waveBlock}
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
    $("#detail").scrollTop = keepScroll;
    $("#backBtn").addEventListener("click", () => closeDetail(false));
    $("#detail").querySelectorAll("[data-target]").forEach((b) => b.addEventListener("click", () => {
      const t = e.targets[Number(b.dataset.target)];
      if (t) world.pointOfView({ lat: t.lat, lng: t.lon, altitude: Math.min(world.pointOfView().altitude, 0.9) }, reduceMotion ? 0 : 900);
    }));
    if (!isMobile() && !refresh) $("#backBtn").focus({ preventScroll: true });
  }

  // ------------------------------------------------------------------ mobile sheet
  // Three heights: collapsed (title only), half, and nearly full. Drag the bar or the title
  // row, or tap them to toggle between collapsed and half.
  const sheetHeights = () => [78, Math.round(window.innerHeight * 0.42), Math.round(window.innerHeight * 0.8)];
  function setSheet(n, instant) {
    if (!isMobile()) return;
    S.sheet = n;
    const feed = $("#feed");
    if (instant) feed.style.transition = "none";
    feed.style.height = sheetHeights()[n] + "px";
    feed.classList.toggle("sheet-collapsed", n === 0);
    $("#sheetHandle").setAttribute("aria-expanded", String(n > 0));
    $("#sheetLabel").textContent = n > 0 ? "Collapse the event list" : "Expand the event list";
    if (instant) { void feed.offsetHeight; feed.style.transition = ""; }
    setTimeout(layout, instant ? 0 : 320);
  }
  function wireSheet() {
    const feed = $("#feed");
    // re-center the globe once the sheet has finished moving (slow devices can lag the timer)
    feed.addEventListener("transitionend", (ev) => { if (ev.propertyName === "height" && isMobile()) layout(); });
    let drag = null;
    const start = (ev) => {
      if (!isMobile()) return;
      drag = { y: ev.clientY, h: feed.getBoundingClientRect().height, moved: false, id: ev.pointerId, el: ev.currentTarget };
      ev.currentTarget.setPointerCapture(ev.pointerId);
      feed.style.transition = "none";
    };
    const move = (ev) => {
      if (!drag || ev.pointerId !== drag.id) return;
      const dy = ev.clientY - drag.y;
      if (Math.abs(dy) > 6) drag.moved = true;
      if (drag.moved) feed.style.height = Math.max(70, Math.min(window.innerHeight * 0.86, drag.h - dy)) + "px";
    };
    const end = (ev) => {
      if (!drag || ev.pointerId !== drag.id) return;
      feed.style.transition = "";
      const dy = ev.clientY - drag.y;
      if (!drag.moved) {
        setSheet(S.sheet === 0 ? 1 : 0);
      } else {
        const h = feed.getBoundingClientRect().height;
        const hs = sheetHeights();
        let n = hs.reduce((best, v, i) => (Math.abs(v - h) < Math.abs(hs[best] - h) ? i : best), 0);
        if (Math.abs(dy) > 50 && n === S.sheet) n = Math.max(0, Math.min(2, S.sheet + (dy > 0 ? -1 : 1)));
        setSheet(n);
      }
      drag = null;
    };
    [$("#sheetHandle"), $("#feedTop")].forEach((el) => {
      el.addEventListener("pointerdown", start);
      el.addEventListener("pointermove", move);
      el.addEventListener("pointerup", end);
      el.addEventListener("pointercancel", end);
    });
    $("#sheetHandle").addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); setSheet(S.sheet === 0 ? 1 : 0); }
    });
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

  function setPanelsHidden(hidden) {
    document.body.classList.toggle("panels-hidden", hidden);
    $("#panelsToggle").setAttribute("aria-pressed", String(hidden));
    $("#panelsToggle").title = hidden ? "Show panels (H)" : "Hide panels (H)";
    $("#panelsToggleLabel").textContent = hidden ? "Show panels" : "Hide panels";
    layout();
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
      if (!fly) return;
      const t = S.theaters.find((x) => x.id === fly.dataset.fly);
      if (t && t.camera) {
        controls.autoRotate = false;
        world.pointOfView(t.camera, reduceMotion ? 0 : 1500);
        if (isMobile()) toggleFilters(false);
      }
    });

    $("#sourcesToggle").addEventListener("click", () => {
      const list = $("#sourcesList");
      list.hidden = !list.hidden;
      $("#sourcesToggle").setAttribute("aria-expanded", String(!list.hidden));
    });

    $("#feedList").addEventListener("click", (ev) => {
      if (ev.target.closest("[data-clear-spot]")) { S.spot = null; render(); return; }
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
    $("#panelsToggle").addEventListener("click", () => setPanelsHidden(!document.body.classList.contains("panels-hidden")));
    wireSheet();

    document.addEventListener("keydown", (ev) => {
      const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement && document.activeElement.tagName);
      if (ev.key === "Escape") {
        if (S.selectedId || S.spot) closeDetail(false);
        else if ($("#filters").classList.contains("open")) toggleFilters(false);
      }
      if (typing) return;
      if (ev.key === "/") {
        ev.preventDefault();
        if (S.selectedId) closeDetail(false);
        $("#search").focus();
      }
      if ((ev.key === "h" || ev.key === "H") && !isMobile()) setPanelsHidden(!document.body.classList.contains("panels-hidden"));
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
  if (isMobile()) setSheet(1, true);
  layout();
  if (DEMO) $("#demoBanner").hidden = false;
  load();
  if (!DEMO) setInterval(load, REFRESH_MS);
  setInterval(() => {
    updateFreshness();
    document.querySelectorAll("#feedList time").forEach((t) => { t.textContent = agoShort(Date.parse(t.dateTime)); });
  }, 30e3);
})();
