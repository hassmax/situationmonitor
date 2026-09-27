(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const params = new URLSearchParams(location.search);
  const DEMO = params.has("demo");
  const REFRESH_MS = 5 * 60 * 1000;
  const HOUR = 3600e3, DAY = 86400e3;
  const isMobile = () => window.innerWidth < 860;

  // ------------------------------------------------------------------ vocabulary
  const STATUS = {
    corroborated: {
      label: "Corroborated", rank: 3, rgb: [255, 91, 58],
      note: (n, news) => `Reported by ${n} independent sources${news >= 3 ? ", including nearby news coverage" : ""}.`,
    },
    unconfirmed: { label: "Single source", rank: 2, rgb: [243, 193, 75], note: () => "Only one source so far. Treat it as unverified." },
    claimed: { label: "One side's claim", rank: 1, rgb: [169, 150, 255], note: () => "Reported only by sources aligned with one side of the conflict." },
  };
  const TYPES = {
    airstrike: "Airstrike", missile_drone: "Missile or drone attack", artillery: "Shelling", ground: "Ground fighting",
    territory: "Territorial change", air_defense: "Air defense", naval: "Naval incident", explosion: "Explosion",
    deployment: "Deployment or exercise", diplomacy: "Diplomacy", ceasefire: "Diplomacy", hybrid: "Hybrid attack",
    incursion: "Airspace or border incursion", arms_transfer: "Arms transfer", legal: "Legal step",
  };
  const MODE = { air: "by air", sea: "by sea", land: "overland", unspecified: "" };
  const PLATFORM = { bluesky: "Bluesky", telegram: "Telegram", rss: "News feed", gdelt: "GDELT" };
  const KIND = { official: "Official", partisan: "Partisan", osint: "OSINT", news: "News" };
  const WINDOWS = [["6h", 6], ["24h", 24], ["3d", 72], ["7d", 168]];
  const ICE = [205, 228, 255];
  const TEAL = [63, 193, 201];
  const SUPPLY_DAYS = 30;
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

  // Known launch areas, used only when a report doesn't name one (drawn faint, labeled approximate).
  const ANCHORS = {
    RU: [["Kursk", 51.73, 36.19], ["Oryol", 52.97, 36.06], ["Bryansk", 53.24, 34.36], ["Belgorod", 50.6, 36.6],
      ["Millerovo", 48.92, 40.4], ["Primorsko-Akhtarsk", 46.05, 38.17], ["Hvardiiske, Crimea", 45.12, 33.97]],
    UA: [["Sumy", 50.91, 34.8], ["Chernihiv", 51.5, 31.29], ["Kharkiv", 49.99, 36.23], ["Zaporizhzhia", 47.84, 35.14], ["Mykolaiv", 46.97, 32.0]],
    IR: [["Kermanshah", 34.31, 47.07], ["Tabriz", 38.08, 46.29], ["Isfahan", 32.65, 51.67], ["Bandar Abbas", 27.18, 56.27]],
    YE: [["Sanaa", 15.37, 44.19], ["Hodeidah", 14.8, 42.95], ["Saada", 16.94, 43.76]],
    LB: [["Nabatieh", 33.38, 35.48], ["Tyre", 33.27, 35.2]],
    IL: [["southern Israel", 31.25, 34.79], ["northern Israel", 32.8, 35.1]],
  };
  Object.keys(ANCHORS).forEach((k) => { ANCHORS[k] = ANCHORS[k].map(([place, lat, lon]) => ({ place, lat, lon })); });

  // ------------------------------------------------------------------ helpers
  const rgba = (c, a = 1) => `rgba(${c[0]},${c[1]},${c[2]},${a})`;
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
  const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? u : "#");
  const toRad = (d) => (d * Math.PI) / 180;
  const clamp = (x, a, b) => Math.max(a, Math.min(b, x));
  function km(a, b, c, d) {
    const x = Math.sin(toRad(c - a) / 2) ** 2 + Math.cos(toRad(a)) * Math.cos(toRad(c)) * Math.sin(toRad(d - b) / 2) ** 2;
    return 12742 * Math.asin(Math.min(1, Math.sqrt(x)));
  }
  // lowest arc height that still clears the globe's curvature (sea lanes, ship tracks)
  const hugAlt = (distKm) => 1.25 * (1 - Math.cos(distKm / 6371 / 2)) + 0.004;
  function nearest(list, p) {
    let best = null, bd = Infinity;
    for (const o of list || []) { const d = km(o.lat, o.lon, p.lat, p.lon); if (d < bd) { best = o; bd = d; } }
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
  const fmtDay = (ms) => new Date(ms).toLocaleDateString(undefined, { month: "short", day: "numeric" });
  const fmtMoney = (v) => (v >= 1e9 ? `$${(v / 1e9).toFixed(1)} billion` : v >= 1e6 ? `$${Math.round(v / 1e6)} million` : `$${Math.round(v).toLocaleString()}`);
  const markHtml = (status) => `<span class="mark mark--${status}" aria-hidden="true"></span>`;
  const isDiplomacy = (e) => e.type === "diplomacy" || e.type === "ceasefire";
  const isFlat = (e) => isDiplomacy(e) || e.type === "legal";
  const typeLabel = (e) => {
    if (e.wave) return "Attack wave";
    if (e.type === "arms_transfer" && e.transfer) return { pledge: "Pledged aid", interdiction: "Intercepted shipment" }[e.transfer.kind] || "Arms delivery";
    return TYPES[e.type] || "Event";
  };
  const originsOf = (e) => (e.origins && e.origins.length ? e.origins : e.origin ? [e.origin] : []);
  const isKey = (e) => e.severity >= 3 && e.status === "corroborated";
  const bestStatus = (list) => list.reduce((b, e) => (STATUS[e.status].rank > STATUS[b].rank ? e.status : b), "claimed");
  const metaLine = (e) => (e.wave ? `${countryName(e.attacker)} → ${countryName(e.country)}` : e.place || "");
  let lastSeen = 0;
  try { lastSeen = Number(localStorage.getItem("gsm_lastSeen")) || 0; } catch (_) { /* storage blocked */ }

  // ------------------------------------------------------------------ state
  const S = {
    data: null,
    lens: "fight",
    theaters: FALLBACK_THEATERS,
    windowH: 24,
    theaterOn: new Set(FALLBACK_THEATERS.map((t) => t.id)),
    statusOn: new Set(Object.keys(STATUS)),
    layers: { arcs: true, diplomacy: true, legal: true },
    query: "",
    selectedId: null,
    selectedHull: null,
    selectedFlow: null,
    spot: null,
    fleetSpot: null,
    hot: new Set(),
    active: new Set(),
    activeKey: "",
    points: [],
    html: [],
    fleet: [],
    fleetMeta: {},
    supply: { flows: [], pledges: [], seizures: [] },
    alt: 3,
    detail: false,
    firstLoad: true,
    lastFocus: null,
    sheet: 1,
  };
  try { const l = localStorage.getItem("gsm_lens"); if (["fight", "supply", "fleet"].includes(l)) S.lens = l; } catch (_) { /* ignore */ }
  if (["fight", "supply", "fleet"].includes(params.get("lens"))) S.lens = params.get("lens");

  // ------------------------------------------------------------------ globe
  const globeEl = $("#globe");
  const world = new Globe(globeEl, { animateIn: !reduceMotion });
  world
    .backgroundColor("rgba(0,0,0,0)")
    .showGraticules(false)
    .showAtmosphere(true)
    .atmosphereColor("#5aaee6")
    .atmosphereAltitude(0.16)
    .pointOfView({ lat: 18, lng: 10, altitude: 3.1 });
  const mat = world.globeMaterial();
  mat.color.set("#0b1f36");
  if (mat.emissive) mat.emissive.set("#06121f");
  mat.shininess = 5;
  const controls = world.controls();
  controls.autoRotate = false;
  controls.minDistance = 150; // altitude 0.5: any closer and the land pattern gets coarse
  controls.maxDistance = 650;

  // ------------------------------------------------------------------ land, borders, country centers
  const centers = new Map(); // ISO numeric -> {lat, lon}
  function sanitize(f) {
    const g = f.geometry;
    if (!g) return null;
    const distinct = (ring) => new Set(ring.map((p) => p[0].toFixed(4) + "," + p[1].toFixed(4))).size;
    const polys = (g.type === "Polygon" ? [g.coordinates] : g.coordinates).filter((p) => p[0] && distinct(p[0]) >= 3);
    if (!polys.length) return null;
    return { ...f, geometry: { type: "MultiPolygon", coordinates: polys } };
  }
  function centerOf(f) {
    let best = null, bestN = 0;
    for (const poly of f.geometry.coordinates) if (poly[0].length > bestN) { best = poly[0]; bestN = poly[0].length; }
    let x = 0, y = 0, z = 0;
    for (const [lon, lat] of best) { x += Math.cos(toRad(lat)) * Math.cos(toRad(lon)); y += Math.cos(toRad(lat)) * Math.sin(toRad(lon)); z += Math.sin(toRad(lat)); }
    return { lat: (Math.atan2(z, Math.hypot(x, y)) * 180) / Math.PI, lon: (Math.atan2(y, x) * 180) / Math.PI };
  }
  // For very large countries the geometric center is misleading (Russia's is in Siberia), so
  // country-level supply lines use a representative point in the populated core instead.
  const REP_POINT = {
    RU: { lat: 55.75, lon: 37.62 }, US: { lat: 38.9, lon: -77.0 }, CN: { lat: 34.3, lon: 113.6 }, CA: { lat: 45.4, lon: -75.7 },
    AU: { lat: -33.9, lon: 151.2 }, BR: { lat: -15.8, lon: -47.9 }, IN: { lat: 28.6, lon: 77.2 }, KZ: { lat: 51.2, lon: 71.4 },
  };
  const countryCenter = (iso2) => REP_POINT[iso2] || centers.get(ISO_NUM.get(iso2)) || null;

  function landColor(f) {
    if (S.active.has(f.id)) return S.lens === "supply" ? "#5fa6b4" : "#7aaddd";
    return S.hot.has(f.id) && S.lens === "fight" ? "#35618b" : "#244465";
  }
  function borderColor(id) {
    if (S.active.has(id)) return S.lens === "supply" ? "rgba(90,210,218,0.8)" : "rgba(255,166,122,0.82)";
    return S.hot.has(id) && S.lens === "fight" ? "rgba(150,195,235,0.3)" : "rgba(150,190,230,0.12)";
  }

  fetch("assets/countries-110m.json")
    .then((r) => r.json())
    .then((topo) => {
      const land = topojson.feature(topo, topo.objects.countries).features
        .filter((f) => f.properties.name !== "Antarctica").map(sanitize).filter(Boolean);
      land.forEach((f) => { if (f.id) centers.set(f.id, centerOf(f)); });
      const borders = [];
      for (const f of land) for (const poly of f.geometry.coordinates) for (const ring of poly) borders.push({ fid: f.id, pts: ring });
      world
        .hexPolygonsData(land).hexPolygonResolution(3).hexPolygonMargin(0.3).hexPolygonUseDots(true).hexPolygonAltitude(0.002)
        .hexPolygonColor((f) => landColor(f))
        .pathsData(borders).pathPoints("pts").pathPointLat((p) => p[1]).pathPointLng((p) => p[0]).pathPointAlt(0.0045)
        .pathTransitionDuration(0).pathColor((p) => borderColor(p.fid));
      if (S.data) render();
    })
    .catch(() => {});

  // ------------------------------------------------------------------ zoom: marker size and level of detail
  let zoomK = 1.6;
  world.onZoom(({ altitude }) => {
    S.alt = altitude;
    const detail = S.detail ? altitude < 1.7 : altitude < 1.5; // hysteresis avoids flicker at the threshold
    const k = clamp(altitude, 0.9, 2.6) / 1.1;
    let changed = false;
    if (Math.abs(k - zoomK) / zoomK > 0.12) { zoomK = k; changed = true; }
    if (detail !== S.detail) { S.detail = detail; render(); return; }
    if (changed) { world.pointRadius((d) => pointRadius(d)); world.ringMaxRadius((r) => r.max * zoomK); }
  });

  // Age fade: in longer windows, older events recede instead of carrying equal weight.
  function fade(e) {
    if (S.windowH <= 6) return 1;
    const age = Date.now() - e._t;
    return clamp(1 - ((age - 6 * HOUR) / (S.windowH * HOUR - 6 * HOUR)) * 0.62, 0.38, 1);
  }
  function pointRadius(d) {
    if (d.faint) return 0.12 * zoomK;
    const sel = (d.ref || d).id === S.selectedId ? 1.5 : 1;
    if (d.secondary) return 0.12 * zoomK * sel;
    const base = d.severity >= 3 ? 0.32 : d.severity === 2 ? 0.26 : 0.2;
    return base * zoomK * sel * (isFlat(d) ? 1.4 : 1);
  }
  function pointAltitude(d) {
    if (d.faint || d.secondary) return 0.004;
    if (isFlat(d) || d.severity <= 1) return 0.004;
    return d.severity >= 3 ? 0.06 : 0.022;
  }
  function pointColor(d) {
    const s = STATUS[d.status];
    if (d.faint) return rgba(s.rgb, 0.18);
    const base = d.status === "unconfirmed" ? 0.78 : 0.95;
    return rgba(s.rgb, (d.secondary ? 0.72 : base) * fade(d.ref || d));
  }

  function tipHtml(e) {
    const extra = e.wave && e.targets && e.targets.length > 1 ? `<span>${e.targets.length} locations</span>` : "";
    return `<div class="tip"><div class="tip-meta"><b>${esc(typeLabel(e))}</b><span>${esc(metaLine(e))}</span></div>
      <div class="tip-sum">${esc(e.summary)}</div>
      <div class="tip-foot">${markHtml(e.status)}<span>${esc(STATUS[e.status].label)}</span>${extra}<span>${esc(ago(e._t))}</span></div></div>`;
  }
  function tipTarget(d) {
    const e = d.ref;
    return `<div class="tip"><div class="tip-meta"><b>${esc(d.place || "Location")}</b><span>part of an attack wave</span></div>
      <div class="tip-sum">${esc(e.summary)}</div>
      <div class="tip-foot">${markHtml(e.status)}<span>${esc(countryName(e.attacker))} → ${esc(countryName(e.country))}</span></div></div>`;
  }
  function tipCarrier(c) {
    return `<div class="tip"><div class="tip-meta"><b>${esc(c.name)}</b><span>${esc(c.hull)}</span></div>
      <div class="tip-sum">${esc(carrierStatus(c))}${c.place ? `, ${esc(c.place)}` : ""}</div>
      <div class="tip-foot"><span>${c._asOf ? `As of ${esc(fmtDay(c._asOf))}` : "No position reports yet"}</span>${c.heading_to ? `<span>heading to ${esc(c.heading_to.place || "a stated destination")}</span>` : ""}</div></div>`;
  }
  function tipFlow(f) {
    return `<div class="tip"><div class="tip-meta"><b>${esc(countryName(f.supplier))} → ${esc(countryName(f.recipient))}</b><span>${f.active ? "active" : "quiet"}</span></div>
      <div class="tip-sum">${f.deliveries} ${f.deliveries === 1 ? "delivery" : "deliveries"} reported in 30 days${f.cargo.length ? `: ${esc(f.cargo.slice(0, 2).join(", "))}` : ""}</div>
      <div class="tip-foot">${markHtml(f.status)}<span>${esc(STATUS[f.status].label)}</span><span>last ${esc(ago(f.last))}</span></div></div>`;
  }

  world
    .pointLat("lat").pointLng("lon")
    .pointAltitude((d) => pointAltitude(d))
    .pointRadius((d) => pointRadius(d))
    .pointColor((d) => pointColor(d))
    .pointResolution(10)
    .pointLabel((d) => (d.faint ? "" : d.secondary ? tipTarget(d) : tipHtml(d)))
    .onPointHover((d) => { globeEl.style.cursor = d && !d.faint ? "pointer" : ""; })
    .onPointClick((d) => { if (!d.faint) select((d.ref || d).id, true); });

  world
    .ringLat("lat").ringLng("lon")
    .ringColor((r) => (t) => rgba(r.rgb, Math.max(0, 1 - t) * r.alpha))
    .ringMaxRadius((r) => r.max * zoomK)
    .ringPropagationSpeed((r) => r.speed)
    .ringRepeatPeriod((r) => r.period)
    .ringAltitude(0.006);

  // One arc layer, styled by kind.
  const ARC = {
    strike: { dash: 0.45, gap: 0.2 },
    strikeApprox: { dash: 0.45, gap: 0.2 },
    flow: { dash: 1, gap: 0 },
    flowDashed: { dash: 0.12, gap: 0.07 },
    particles: { dash: 0.012, gap: 0.11 },
    track: { dash: 0.06, gap: 0.04 },
    plan: { dash: 0.2, gap: 0.14 },
  };
  world
    .arcStartLat("sLat").arcStartLng("sLng").arcEndLat("eLat").arcEndLng("eLng")
    .arcColor((a) => a.color)
    .arcStroke((a) => a.stroke)
    .arcDashLength((a) => ARC[a.kind].dash)
    .arcDashGap((a) => ARC[a.kind].gap)
    .arcDashInitialGap((a) => (a.kind === "flow" ? 0 : Math.random()))
    .arcDashAnimateTime((a) => (reduceMotion ? 0 : a.ms || 0))
    .arcAltitude((a) => (a.alt === undefined ? null : a.alt))
    .arcAltitudeAutoScale(0.36)
    .arcLabel((a) => (a.carrier ? tipCarrier(a.carrier) : a.flow ? tipFlow(a.flow) : a.ref ? tipHtml(a.ref) : ""))
    .onArcHover((a) => { globeEl.style.cursor = a ? "pointer" : ""; })
    .onArcClick((a) => {
      if (a.carrier) selectCarrier(a.carrier.hull, true);
      else if (a.flow) selectFlow(a.flow.key);
      else if (a.ref) select(a.ref.id, true);
    });

  // HTML layer: cluster badges, carrier icons, pledge badges, interdiction marks.
  world
    .htmlLat("lat").htmlLng("lon")
    .htmlAltitude((d) => d.hAlt || 0.012)
    .htmlElement((d) => d.el)
    .htmlElementVisibilityModifier((el, visible) => {
      el.style.opacity = visible ? "1" : "0";
      el.style.pointerEvents = visible ? "auto" : "none";
    });
  const elCache = new Map();
  function htmlEl(key, build) {
    let el = elCache.get(key);
    if (!el) {
      el = document.createElement("button");
      el.type = "button";
      el.addEventListener("pointerdown", (ev) => ev.stopPropagation());
      elCache.set(key, el);
    }
    build(el);
    return el;
  }

  // Clicks on the globe, a land dot, or a border still pick the nearest event.
  world
    .onGlobeClick((coords, ev) => pickNear(ev, coords))
    .onHexPolygonClick((_, ev, coords) => pickNear(ev, coords))
    .onPathClick((_, ev, coords) => pickNear(ev, coords));

  function pickNear(ev, coords) {
    if (S.lens !== "fight" || !S.detail || !S.points.length) return;
    const rect = globeEl.getBoundingClientRect();
    const pov = world.pointOfView();
    const horizonDeg = (Math.acos(1 / (1 + pov.altitude)) * 180) / Math.PI - 2;
    const radiusPx = isMobile() ? 34 : 24;
    const havePx = ev && Number.isFinite(ev.clientX);
    const hits = [];
    for (const d of S.points) {
      if (d.faint || km(pov.lat, pov.lng, d.lat, d.lon) / 111.2 > horizonDeg) continue;
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
    hideDetail();
    if (coords) world.pointOfView({ lat: coords.lat, lng: coords.lng, altitude: Math.max(0.55, world.pointOfView().altitude * 0.6) }, reduceMotion ? 0 : 900);
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

  function shiftDemo(data) {
    const shift = Date.now() - Date.parse(data.generated_at);
    const move = (s) => (s ? new Date(Date.parse(s) + shift).toISOString() : s);
    data.generated_at = move(data.generated_at);
    (data.events || []).forEach((e) => {
      e.time = move(e.time); e.updated = move(e.updated);
      (e.reports || []).forEach((r) => { r.time = move(r.time); });
      (e.targets || []).forEach((t) => { t.time = move(t.time); });
    });
    (data.heat || []).forEach((c) => { c.first = move(c.first); c.last = move(c.last); });
    (data.fleet || []).forEach((c) => {
      ["as_of", "moved_at", "departed_at"].forEach((k) => { c[k] = move(c[k]); });
      if (c.prev) c.prev.as_of = move(c.prev.as_of);
      (c.track || []).forEach((t) => { t.time = move(t.time); });
    });
    if (data.fleet_meta) data.fleet_meta.tracker_time = move(data.fleet_meta.tracker_time);
  }

  function ingest(data) {
    if (DEMO && data.generated_at) shiftDemo(data);
    data.events = (data.events || []).filter((e) => STATUS[e.status] && isFinite(e.lat) && isFinite(e.lon));
    for (const e of data.events) {
      e._t = Date.parse(e.updated || e.time);
      e._t0 = Date.parse(e.time || e.updated);
      e.targets = Array.isArray(e.targets) ? e.targets.filter((t) => isFinite(t.lat) && isFinite(t.lon)) : [];
      const t = e.transfer || {};
      e._search = [e.summary, e.place, e.targets.map((x) => x.place).join(" "), typeLabel(e), countryName(e.attacker),
        countryName(e.country), countryName(t.supplier), countryName(t.recipient), t.what,
        ...(e.reports || []).map((r) => r.source)].join(" ").toLowerCase();
    }
    data.heat = (data.heat || []).map((c) => ({ ...c, _t: Date.parse(c.last) }));
    // Carriers keep the same objects across refreshes so their icons persist.
    const byHull = new Map(S.fleet.map((c) => [c.hull, c]));
    S.fleet = (data.fleet || []).filter((c) => isFinite(c.lat) && isFinite(c.lon)).map((c) => {
      const d = byHull.get(c.hull) || {};
      Object.assign(d, c);
      d._lat = c.lat; d._lon = c.lon;
      d._asOf = c.as_of && !c.as_of.startsWith("1970") ? Date.parse(c.as_of) : 0;
      d._moved = c.moved_at ? Date.parse(c.moved_at) : 0;
      d._fresh = (c.status === "departed" || c.status === "underway") && d._asOf && Date.now() - d._asOf < 72 * HOUR;
      d._search = [c.name, c.short, c.hull, c.place, c.heading_to && c.heading_to.place].join(" ").toLowerCase();
      return d;
    }).sort((a, b) => (a.at_home === b.at_home ? a.hull.localeCompare(b.hull) : a.at_home ? 1 : -1));
    S.fleetMeta = data.fleet_meta || {};

    const theaters = Array.isArray(data.theaters) && data.theaters.length ? data.theaters : FALLBACK_THEATERS;
    if (S.firstLoad) S.theaterOn = new Set(theaters.map((t) => t.id));
    else theaters.forEach((t) => { if (!S.theaters.some((x) => x.id === t.id)) S.theaterOn.add(t.id); });
    S.theaters = theaters;
    S.hot = new Set(theaters.flatMap((t) => t.highlight || []));
    S.data = data;

    renderTheaters();
    renderSources();
    render();
    updateFreshness();
    sailRecentMoves();

    if (S.firstLoad) {
      S.firstLoad = false;
      const hash = decodeURIComponent(location.hash.slice(1));
      if (hash && data.events.some((e) => e.id === hash)) select(hash, true);
      else if (/^CVN-\d{2}$/.test(hash) && S.fleet.some((c) => c.hull === hash)) { setLens("fleet", true); selectCarrier(hash, true); }
      else world.pointOfView({ lat: 27, lng: 40, altitude: isMobile() ? 3.0 : 2.25 }, reduceMotion ? 0 : 2600);
    } else if (S.selectedId) {
      const e = data.events.find((x) => x.id === S.selectedId);
      if (e) renderEventDetail(e, true);
    } else if (S.selectedHull) {
      const c = S.fleet.find((x) => x.hull === S.selectedHull);
      if (c) renderCarrierDetail(c, true);
    }
  }

  function showLoadError(err) {
    if (S.data) { updateFreshness(); return; }
    $("#freshText").textContent = "No data yet";
    $("#beacon").className = "beacon dead";
    $("#feedList").innerHTML = err.message === "missing"
      ? `<li class="empty"><strong>No data yet.</strong>The first update runs within about 15 minutes of setup. If this persists, open your repository's Actions tab and check the "Update conflict data" workflow. Add <code>?demo</code> to this page's address to preview the layout with sample data.</li>`
      : `<li class="empty"><strong>Couldn't load events.</strong>${esc(err.message)}. The page will try again in 5 minutes.</li>`;
  }

  // ------------------------------------------------------------------ filtering
  const q = () => S.query.trim().toLowerCase();
  function passesFight(e, ignoreTheater = false) {
    if (e.type === "arms_transfer") return false;
    if (e._t < Date.now() - S.windowH * HOUR) return false;
    if (!ignoreTheater && !S.theaterOn.has(e.theater)) return false;
    if (!S.statusOn.has(e.status)) return false;
    if (!S.layers.diplomacy && isDiplomacy(e)) return false;
    if (!S.layers.legal && e.type === "legal") return false;
    if (q() && !e._search.includes(q())) return false;
    return true;
  }
  const fightEvents = () => (S.data ? S.data.events.filter((e) => passesFight(e)).sort((a, b) => b._t - a._t) : []);

  // ------------------------------------------------------------------ supply flows (30 days)
  function buildSupply() {
    const out = { flows: [], pledges: [], seizures: [] };
    if (!S.data) return out;
    const since = Date.now() - SUPPLY_DAYS * DAY;
    const flows = new Map(), pledges = new Map();
    for (const e of S.data.events) {
      const t = e.transfer;
      if (e.type !== "arms_transfer" || !t || e._t < since || !S.theaterOn.has(e.theater) || !S.statusOn.has(e.status)) continue;
      if (q() && !e._search.includes(q())) continue;
      const kind = t.kind || "delivery";
      if (kind === "interdiction") { out.seizures.push(e); continue; }
      const key = `${t.supplier}>${t.recipient}`;
      const bucket = kind === "pledge" ? pledges : flows;
      if (!bucket.has(key)) bucket.set(key, { key, supplier: t.supplier, recipient: t.recipient, events: [] });
      bucket.get(key).events.push(e);
    }
    const summarize = (f) => {
      const ev = f.events.sort((a, b) => b._t - a._t);
      const count = (list, get) => {
        const m = new Map();
        list.forEach((x) => { const v = get(x); if (v) { const k = v.place || JSON.stringify(v); m.set(k, { v, n: (m.get(k) || { n: 0 }).n + 1 }); } });
        return [...m.values()].sort((a, b) => b.n - a.n).map((x) => x.v);
      };
      f.from = count(ev, (e) => e.transfer.from)[0] || null;
      f.to = count(ev, (e) => e.transfer.to)[0] || null;
      f.via = (ev.find((e) => (e.transfer.via || []).length) || { transfer: { via: [] } }).transfer.via || [];
      f.deliveries = ev.reduce((n, e) => n + Math.max(1, e.transfer.flights || 1), 0);
      f.value = ev.reduce((n, e) => n + (e.transfer.value_usd || 0), 0);
      f.modes = [...new Set(ev.map((e) => e.transfer.mode).filter((m) => m && m !== "unspecified"))];
      const cargo = new Map();
      ev.forEach((e) => { if (e.transfer.what) cargo.set(e.transfer.what, (cargo.get(e.transfer.what) || 0) + 1); });
      f.cargo = [...cargo.entries()].sort((a, b) => b[1] - a[1]).map(([w]) => w);
      f.status = bestStatus(ev);
      f.last = ev[0]._t;
      f.active = Date.now() - f.last < 72 * HOUR;
      const bins = new Array(15).fill(0);
      ev.forEach((e) => { const i = 14 - Math.floor((Date.now() - e._t) / (2 * DAY)); if (i >= 0 && i < 15) bins[i] += Math.max(1, e.transfer.flights || 1); });
      f.bins = bins;
      f.theater = ev[0].theater;
      return f;
    };
    out.flows = [...flows.values()].map(summarize).sort((a, b) => (b.active - a.active) || b.deliveries - a.deliveries);
    out.pledges = [...pledges.values()].map(summarize).sort((a, b) => b.last - a.last);
    out.seizures.sort((a, b) => b._t - a._t);
    return out;
  }

  // ------------------------------------------------------------------ carriers
  const CARRIER_SVG = `<svg viewBox="0 0 28 14" aria-hidden="true"><path d="M1.5 9.2 4 4.8h17.2l5.3 2.6v2.4l-2.4 2.2H3.6z" fill="currentColor"/><path d="M6 6.4h12.5M10 11.2 21 5.4" stroke="rgba(8,22,39,.75)" stroke-width="0.9"/><rect x="17.2" y="9.3" width="3.4" height="2.3" rx="0.4" fill="rgba(8,22,39,.8)"/></svg>`;
  function carrierStatus(c) {
    if (c.at_home || c.status === "home") return c.maintenance || /overhaul|shipyard/i.test(c.place || "") ? "In the shipyard" : "Home waters";
    switch (c.status) {
      case "departed": return "Just departed";
      case "underway": return c.deployed ? "Deployed, underway" : "Underway";
      case "operating": return c.deployed ? "Deployed" : "Operating";
      case "arrived": return "Arrived";
      case "in port": return c.maintenance ? "In maintenance" : "In port";
      default: return "Reported";
    }
  }
  const carrierTone = (c) => (c.at_home || c.status === "home" || c.status === "in port" ? "port" : c.status === "departed" || c.status === "underway" ? "underway" : "deployed");

  function slerp(a, b, t) {
    const [la1, lo1, la2, lo2] = [a.lat, a.lon, b.lat, b.lon].map(toRad);
    const d = 2 * Math.asin(Math.sqrt(Math.sin((la2 - la1) / 2) ** 2 + Math.cos(la1) * Math.cos(la2) * Math.sin((lo2 - lo1) / 2) ** 2));
    if (d < 1e-6) return { lat: b.lat, lon: b.lon };
    const A = Math.sin((1 - t) * d) / Math.sin(d), B = Math.sin(t * d) / Math.sin(d);
    const x = A * Math.cos(la1) * Math.cos(lo1) + B * Math.cos(la2) * Math.cos(lo2);
    const y = A * Math.cos(la1) * Math.sin(lo1) + B * Math.cos(la2) * Math.sin(lo2);
    const z = A * Math.sin(la1) + B * Math.sin(la2);
    return { lat: (Math.atan2(z, Math.hypot(x, y)) * 180) / Math.PI, lon: (Math.atan2(y, x) * 180) / Math.PI };
  }
  const sailed = new Set();
  let sailing = false;
  // Carriers that moved in the last 7 days glide from their previous position once per page view.
  function sailRecentMoves() {
    if (reduceMotion || S.lens === "supply") return;
    const movers = S.fleet.filter((c) => c.prev && c._moved && Date.now() - c._moved < 7 * DAY && !sailed.has(c.hull + c.as_of)
      && (S.lens === "fleet" || !c.at_home));
    if (!movers.length) return;
    movers.forEach((c) => { sailed.add(c.hull + c.as_of); c._from = { lat: c.prev.lat, lon: c.prev.lon }; c._to = { lat: c._lat, lon: c._lon }; });
    const t0 = performance.now(), dur = 4200;
    const ease = (t) => (t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2);
    sailing = true;
    const step = (now) => {
      const t = Math.min(1, (now - t0) / dur);
      movers.forEach((c) => { const p = slerp(c._from, c._to, ease(t)); c.lat = p.lat; c.lon = p.lon; });
      world.htmlElementsData(S.html);
      if (t < 1) requestAnimationFrame(step);
      else { sailing = false; movers.forEach((c) => { c.lat = c._lat; c.lon = c._lon; }); render(); }
    };
    requestAnimationFrame(step);
  }

  // ------------------------------------------------------------------ build layers per lens
  function eventPoints(events) {
    const pts = [];
    for (const e of events) {
      pts.push(e);
      if (e.wave && S.detail) {
        e.targets.slice(0, 40).forEach((t, i) => {
          if (Math.abs(t.lat - e.lat) < 1e-4 && Math.abs(t.lon - e.lon) < 1e-4) return;
          pts.push({ id: `${e.id}~${i}`, ref: e, lat: t.lat, lon: t.lon, place: t.place, severity: 1, status: e.status, secondary: true });
        });
      }
    }
    return pts.slice(0, 1500);
  }

  // Whole-globe view: nearby events collapse into one numbered badge.
  function clusterEvents(events) {
    const cells = new Map();
    const singles = [];
    for (const e of events) {
      if (isFlat(e)) { singles.push(e); continue; }
      const key = `${Math.floor(e.lat / 5)}:${Math.floor(e.lon / 5)}`;
      if (!cells.has(key)) cells.set(key, []);
      cells.get(key).push(e);
    }
    const badges = [];
    for (const [key, list] of cells) {
      if (list.length === 1) { singles.push(list[0]); continue; }
      let x = 0, y = 0, z = 0;
      list.forEach((e) => { x += Math.cos(toRad(e.lat)) * Math.cos(toRad(e.lon)); y += Math.cos(toRad(e.lat)) * Math.sin(toRad(e.lon)); z += Math.sin(toRad(e.lat)); });
      const lat = (Math.atan2(z, Math.hypot(x, y)) * 180) / Math.PI, lon = (Math.atan2(y, x) * 180) / Math.PI;
      const status = bestStatus(list);
      const n = list.length;
      const hasNew = list.some((e) => isNew(e));
      const d = { kind: "cluster", key, lat, lon, hAlt: 0.02, n, status, ids: list.map((e) => e.id) };
      d.el = htmlEl(`cluster:${key}`, (el) => {
        el.className = `cluster cluster--${status}${hasNew ? " is-new" : ""}`;
        el.style.setProperty("--size", `${Math.round(clamp(22 + Math.sqrt(n) * 5, 24, 46))}px`);
        el.textContent = n;
        el.setAttribute("aria-label", `${n} events here, ${STATUS[status].label.toLowerCase()} at most. Zoom in.`);
        el.onclick = (ev) => { ev.stopPropagation(); zoomTo(d.lat, d.lon, 1.2); };
      });
      badges.push(d);
    }
    return { singles, badges };
  }
  const isNew = (e) => e._t > Date.now() - HOUR || (lastSeen && e._t > lastSeen && e._t > Date.now() - DAY);
  function zoomTo(lat, lng, altitude) { world.pointOfView({ lat, lng, altitude }, reduceMotion ? 0 : 1100); }

  function strikeArcs(events) {
    const arcs = [];
    const push = (e, o, d, approx) => {
      const dist = km(o.lat, o.lon, d.lat, d.lon);
      if (dist < 25 || (approx && dist > 1800)) return;
      const c = STATUS[e.status].rgb;
      const live = e.id === S.selectedId || isNew(e);
      arcs.push({ ref: e, sLat: o.lat, sLng: o.lon, eLat: d.lat, eLng: d.lon, kind: approx ? "strikeApprox" : "strike",
        color: [rgba(c, approx ? 0.02 : 0.05), rgba(c, (approx ? 0.4 : 0.85) * fade(e))], stroke: approx ? 0.18 : 0.3,
        ms: live ? (approx ? 3400 : 2300) : 0 });
    };
    // Launch paths: only when zoomed into a theater, or for the event you opened.
    const show = events.filter((e) => e.id === S.selectedId || (S.detail && S.layers.arcs));
    for (const e of show) {
      if (arcs.length >= 160) break;
      const origins = originsOf(e);
      if (e.wave) {
        for (const d of (e.targets.length ? e.targets.slice(0, 20) : [e])) {
          const o = nearest(origins, d);
          if (o) push(e, o, d, false);
          else if (ANCHORS[e.attacker]) push(e, nearest(ANCHORS[e.attacker], d), d, true);
        }
      } else if (origins.length) origins.slice(0, 3).forEach((o) => push(e, o, e, false));
      else if (e.type === "missile_drone" && ANCHORS[e.attacker]) push(e, nearest(ANCHORS[e.attacker], e), e, true);
    }
    return arcs;
  }

  function flowArcs(flows) {
    const arcs = [];
    for (const f of flows) {
      const named = f.from && f.to;
      const start = f.from || countryCenter(f.supplier);
      const end = f.to || countryCenter(f.recipient);
      if (!start || !end) continue;
      const pts = [start, ...(named ? f.via : []), end];
      const stroke = clamp(0.25 + 0.22 * Math.log2(1 + f.deliveries), 0.25, 1.3);
      const alpha = named ? 0.85 : 0.45;
      const sea = f.modes.length === 1 && f.modes[0] === "sea";
      const selected = S.selectedFlow === f.key;
      for (let i = 0; i < pts.length - 1; i++) {
        const a = pts[i], b = pts[i + 1];
        const dist = km(a.lat, a.lon, b.lat, b.lon);
        if (dist < 25) continue;
        const alt = sea ? hugAlt(dist) : Math.min(0.28, hugAlt(dist) + 0.04 + dist / 60000);
        const base = { flow: f, sLat: a.lat, sLng: a.lon, eLat: b.lat, eLng: b.lon, alt };
        arcs.push({ ...base, kind: f.status === "corroborated" ? "flow" : "flowDashed",
          color: rgba(TEAL, selected ? 1 : alpha), stroke: selected ? stroke * 1.3 : stroke, ms: 0 });
        if (f.active) {
          arcs.push({ ...base, kind: "particles", color: [rgba([220, 250, 252], 0.2), rgba([220, 250, 252], 0.95)], stroke: Math.max(0.3, stroke * 0.8), ms: 2200 });
        }
      }
    }
    return arcs;
  }

  function supplyBadges(sup) {
    const html = [];
    for (const p of sup.pledges) {
      const at = p.to || countryCenter(p.recipient);
      if (!at) continue;
      const n = p.events.length;
      const d = { kind: "pledge", lat: at.lat, lon: at.lon, hAlt: 0.03 };
      d.el = htmlEl(`pledge:${p.key}`, (el) => {
        el.className = "chip-badge chip-badge--pledge";
        el.innerHTML = `<span>${esc(countryName(p.supplier))}</span> pledged${n > 1 ? ` ×${n}` : ""}`;
        el.setAttribute("aria-label", `${countryName(p.supplier)} pledged aid to ${countryName(p.recipient)}, ${n} announcements`);
        el.onclick = (ev) => { ev.stopPropagation(); selectFlow(p.key, true); };
      });
      html.push(d);
    }
    for (const e of sup.seizures) {
      const d = { kind: "seizure", lat: e.lat, lon: e.lon, hAlt: 0.012 };
      d.el = htmlEl(`seize:${e.id}`, (el) => {
        el.className = "seize-mark";
        el.innerHTML = `<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/></svg>`;
        el.setAttribute("aria-label", `Intercepted shipment: ${e.summary}`);
        el.title = e.summary;
        el.onclick = (ev) => { ev.stopPropagation(); select(e.id, true); };
      });
      html.push(d);
    }
    return html;
  }

  function fleetHtml(labels) {
    const html = [];
    const ports = new Map();
    for (const c of S.fleet) {
      // A carrier that just returned home sails in on its own before joining its port's badge.
      const arriving = c.prev && c._moved && Date.now() - c._moved < 7 * DAY && !sailed.has(c.hull + c.as_of);
      if (c.at_home && S.lens === "fleet" && !sailing && !arriving) {
        // group carriers within 60 km of each other (Norfolk and the Newport News yard become one badge)
        let key = [...ports.keys()].find((k) => { const p0 = ports.get(k)[0]; return km(p0._lat, p0._lon, c._lat, c._lon) < 60; });
        if (!key) { key = c.hull; ports.set(key, []); }
        ports.get(key).push(c);
        continue;
      }
      if (S.lens === "fight" && c.at_home) continue;
      c.el = htmlEl(`cvn:${c.hull}`, (el) => {
        el.className = `cvn cvn--${carrierTone(c)}${labels ? "" : " cvn--mini"}${c.hull === S.selectedHull ? " is-selected" : ""}`;
        el.innerHTML = `${CARRIER_SVG}${labels ? `<span>${esc(c.short || c.hull)}</span>` : ""}`;
        el.setAttribute("aria-label", `${c.name}, ${carrierStatus(c)}${c.place ? ", " + c.place : ""}`);
        el.title = labels ? "" : `${c.name}: ${carrierStatus(c)}`;
        el.onclick = (ev) => { ev.stopPropagation(); if (S.lens !== "fleet") setLens("fleet"); selectCarrier(c.hull, true); };
      });
      c.hAlt = 0.012;
      html.push(c);
    }
    for (const [key, list] of ports) {
      const c0 = list[0];
      const d = { kind: "port", lat: c0._lat, lon: c0._lon, hAlt: 0.012 };
      d.el = htmlEl(`port:${key}`, (el) => {
        el.className = `cvn cvn--port${list.some((c) => c.hull === S.selectedHull) ? " is-selected" : ""}`;
        el.innerHTML = `${CARRIER_SVG}<span>${list.length > 1 ? `${esc((c0.place || "").split(/[,(]/)[0].trim())} ×${list.length}` : esc(c0.short)}</span>`;
        el.setAttribute("aria-label", `${list.map((c) => c.name).join(", ")} in ${c0.place}`);
        el.onclick = (ev) => {
          ev.stopPropagation();
          if (list.length === 1) selectCarrier(c0.hull, true);
          else { S.fleetSpot = list.map((c) => c.hull); hideDetail(); renderPanel(); }
        };
      });
      html.push(d);
    }
    return html;
  }

  function fleetArcs() {
    const arcs = [];
    for (const c of S.fleet) {
      if (c.prev && c._moved && Date.now() - c._moved < 14 * DAY && km(c.prev.lat, c.prev.lon, c._lat, c._lon) > 100) {
        const recent = Date.now() - c._moved < 3 * DAY;
        arcs.push({ carrier: c, sLat: c.prev.lat, sLng: c.prev.lon, eLat: c._lat, eLng: c._lon, kind: "track",
          alt: hugAlt(km(c.prev.lat, c.prev.lon, c._lat, c._lon)), color: [rgba(ICE, 0.1), rgba(ICE, 0.9)], stroke: 0.4, ms: recent ? 5200 : 0 });
      }
      if (c.heading_to && km(c._lat, c._lon, c.heading_to.lat, c.heading_to.lon) > 100) {
        arcs.push({ carrier: c, sLat: c._lat, sLng: c._lon, eLat: c.heading_to.lat, eLng: c.heading_to.lon, kind: "plan",
          alt: hugAlt(km(c._lat, c._lon, c.heading_to.lat, c.heading_to.lon)), color: [rgba(ICE, 0.55), rgba(ICE, 0.06)], stroke: 0.26, ms: 0 });
      }
    }
    return arcs;
  }

  // ------------------------------------------------------------------ render
  function render() {
    if (!S.data) return;
    const fight = fightEvents();
    let points = [], arcs = [], html = [], rings = [];
    if (S.lens === "fight") {
      if (S.detail) points = eventPoints(fight);
      else {
        const { singles, badges } = clusterEvents(fight);
        points = singles;
        html = badges;
      }
      arcs = strikeArcs(fight);
      html = html.concat(fleetHtml(false));
      rings = fightRings(fight);
      S.supply = { flows: [], pledges: [], seizures: [] };
    } else {
      points = fight.filter((e) => !isFlat(e)).slice(0, 600).map((e) => ({ id: e.id, lat: e.lat, lon: e.lon, status: e.status, severity: 1, faint: true }));
      if (S.lens === "supply") {
        S.supply = buildSupply();
        arcs = flowArcs(S.supply.flows);
        html = supplyBadges(S.supply);
      } else {
        arcs = fleetArcs();
        html = fleetHtml(true);
        rings = fleetRings();
      }
    }
    S.points = points;
    S.html = html;
    world.pointsData(points);
    world.pointRadius((d) => pointRadius(d));
    world.arcsData(arcs);
    if (!sailing) world.htmlElementsData(html);
    world.ringsData(rings);
    updateActive(fight);
    renderCounts();
    renderTally(fight);
    renderPanel(fight);
  }

  // Motion budget: pulse only what's new (last hour, or since your last visit), at most 12.
  function fightRings(events) {
    const rings = [];
    if (!reduceMotion) {
      for (const e of events) {
        if (rings.length >= 12) break;
        if (isNew(e) && !isFlat(e)) rings.push({ lat: e.lat, lon: e.lon, rgb: STATUS[e.status].rgb, alpha: 0.7, max: 2 + e.severity, speed: 1.4, period: 1900 });
      }
    }
    const sel = S.selectedId && events.find((e) => e.id === S.selectedId);
    if (sel) rings.push({ lat: sel.lat, lon: sel.lon, rgb: [234, 240, 246], alpha: 0.9, max: 4.5, speed: reduceMotion ? 0 : 2.4, period: 1100 });
    return rings;
  }
  function fleetRings() {
    const rings = [];
    if (!reduceMotion) S.fleet.forEach((c) => { if (c._fresh) rings.push({ lat: c._lat, lon: c._lon, rgb: ICE, alpha: 0.55, max: 3.2, speed: 0.9, period: 2600 }); });
    const sel = S.selectedHull && S.fleet.find((c) => c.hull === S.selectedHull);
    if (sel) rings.push({ lat: sel._lat, lon: sel._lon, rgb: [234, 240, 246], alpha: 0.9, max: 4, speed: reduceMotion ? 0 : 2.2, period: 1200 });
    return rings;
  }

  // Warm borders for countries in the fighting (or, in Supply, the suppliers and recipients).
  function updateActive(fight) {
    const active = new Set();
    const add = (c) => { const n = ISO_NUM.get(c); if (n) active.add(n); };
    if (S.lens === "fight") {
      for (const e of fight) if (!isFlat(e)) { add(e.country); add(e.attacker); }
    } else if (S.lens === "supply") {
      S.supply.flows.concat(S.supply.pledges).forEach((f) => { add(f.supplier); add(f.recipient); });
    }
    const key = S.lens + ":" + [...active].sort().join(",");
    if (key === S.activeKey) return;
    S.activeKey = key;
    S.active = active;
    world.hexPolygonColor((f) => landColor(f));
    world.pathColor((p) => borderColor(p.fid));
  }

  function renderTally(fight) {
    const el = $("#tally");
    if (S.lens === "fight") {
      const span = { 6: "6 hours", 24: "24 hours", 72: "3 days", 168: "7 days" }[S.windowH];
      el.innerHTML = `<strong>${fight.length}</strong> events in the last ${span}, <strong>${fight.filter((e) => e.status === "corroborated").length}</strong> corroborated`;
    } else if (S.lens === "supply") {
      const f = S.supply.flows;
      el.innerHTML = `<strong>${f.filter((x) => x.active).length}</strong> active supply routes, <strong>${f.reduce((n, x) => n + x.deliveries, 0)}</strong> deliveries in 30 days`;
    } else {
      const at = S.fleet.filter((c) => !c.at_home).length;
      el.innerHTML = `<strong>${at}</strong> of ${S.fleet.length || 11} carriers away from home port`;
    }
  }

  // ------------------------------------------------------------------ right panel
  function renderPanel(fight) {
    if (!S.data || !$("#detail").hidden) return;
    if (S.lens === "supply") return renderSupplyPanel();
    if (S.lens === "fleet") return renderFleetPanel();
    renderFeed(fight || fightEvents());
  }

  function itemHtml(e, names) {
    const extra = [];
    if (e.wave && e.targets.length) extra.push(`${e.targets.length} ${e.targets.length === 1 ? "location" : "locations"}`);
    if (e.wave && e.launched) extra.push(`${e.launched} launched`);
    if (e.legal_basis) extra.push("Legal basis stated");
    return `<li><button class="item sev-${e.severity}${e.wave ? " is-wave" : ""}${isNew(e) ? " is-new" : ""}" type="button" data-id="${esc(e.id)}" ${e.id === S.selectedId ? 'aria-current="true"' : ""}>
      ${markHtml(e.status)}
      <span>
        <span class="item-meta"><span class="item-type">${esc(typeLabel(e))}</span><span class="item-place">${esc(metaLine(e))}</span><time datetime="${esc(e.updated)}">${esc(agoShort(e._t))}</time></span>
        <span class="item-summary">${esc(e.summary)}</span>
        <span class="item-foot"><span class="sr">${esc(STATUS[e.status].label)}.</span><span>${esc(names[e.theater] || e.theater)}</span>
          ${extra.map((x) => `<span>${esc(x)}</span>`).join("")}<span>${e.sources_count} ${e.sources_count === 1 ? "source" : "sources"}</span></span>
      </span></button></li>`;
  }

  function renderFeed(events) {
    $("#feedTitle").textContent = "Latest";
    const list = $("#feedList");
    const names = Object.fromEntries(S.theaters.map((t) => [t.id, t.name]));
    let shown = events, head = "";
    if (S.spot) {
      const ids = new Set(S.spot);
      shown = events.filter((e) => ids.has(e.id));
      head = `<li class="spotbar"><span>${shown.length} events near this spot</span><button class="linkish" type="button" data-clear-spot>Show all</button></li>`;
    }
    $("#feedCount").textContent = S.spot ? "" : `${events.length}`;
    if (!shown.length) {
      list.innerHTML = head + (S.data.events.length
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

  function sparkSvg(counts, w = 3, gap = 1.1, h = 12, cls = "") {
    const max = Math.max(1, ...counts);
    const bars = counts.map((c, i) => {
      const bh = c ? Math.max(1.5, (c / max) * h) : 0.8;
      return `<rect x="${(i * (w + gap)).toFixed(1)}" y="${(h - bh).toFixed(1)}" width="${w}" height="${bh.toFixed(1)}" rx="0.7"${i === counts.length - 1 ? ' class="today"' : ""}/>`;
    }).join("");
    const W = counts.length * (w + gap) - gap;
    return `<svg class="${cls}" viewBox="0 0 ${W.toFixed(1)} ${h}" width="${W.toFixed(1)}" height="${h}" aria-hidden="true">${bars}</svg>`;
  }

  function flowRow(f, kind) {
    const modes = f.modes.map((m) => MODE[m]).filter(Boolean).join(", ");
    const tags = [];
    if (kind === "pledge") tags.push(`${f.events.length} ${f.events.length === 1 ? "announcement" : "announcements"}`);
    else tags.push(`${f.deliveries} ${f.deliveries === 1 ? "delivery" : "deliveries"}`);
    if (modes) tags.push(modes);
    if (f.value) tags.push(fmtMoney(f.value));
    return `<li><button class="flow-row${S.selectedFlow === f.key ? " is-selected" : ""}" type="button" data-flow="${esc(f.key)}" data-flow-kind="${kind}">
      <span class="flow-top">
        ${kind === "pledge" ? '<span class="flow-dot flow-dot--pledge" aria-hidden="true"></span>' : `<span class="flow-dot${f.active ? " is-active" : ""}${f.status === "corroborated" ? "" : " is-dashed"}" aria-hidden="true"></span>`}
        <span class="flow-name">${esc(countryName(f.supplier))} → ${esc(countryName(f.recipient))}</span>
        ${kind === "pledge" ? "" : sparkSvg(f.bins, 2.2, 0.9, 12, "flow-spark")}
      </span>
      <span class="flow-meta">${tags.map((t) => `<span>${esc(t)}</span>`).join("")}${f.active && kind !== "pledge" ? '<span class="flow-live">Active</span>' : `<span>${esc(agoShort(f.last))}</span>`}</span>
      ${f.cargo.length ? `<span class="flow-cargo">${esc(f.cargo.slice(0, 3).join(", "))}</span>` : ""}
    </button></li>`;
  }

  function renderSupplyPanel() {
    $("#feedTitle").textContent = "Supply flows";
    $("#feedCount").textContent = "last 30 days";
    const s = S.supply;
    const parts = [];
    if (s.flows.length) parts.push(`<li class="group">Deliveries</li>${s.flows.map((f) => flowRow(f, "delivery")).join("")}`);
    if (s.pledges.length) parts.push(`<li class="group">Pledged, not yet reported delivered</li>${s.pledges.map((f) => flowRow(f, "pledge")).join("")}`);
    if (s.seizures.length) {
      const names = Object.fromEntries(S.theaters.map((t) => [t.id, t.name]));
      parts.push(`<li class="group">Intercepted shipments</li>${s.seizures.map((e) => itemHtml(e, names)).join("")}`);
    }
    $("#feedList").innerHTML = parts.join("") || `<li class="empty"><strong>No arms transfers in the last 30 days.</strong>Deliveries, pledges, and intercepted shipments appear here as sources report them.</li>`;
  }

  function fleetRow(c) {
    return `<li><button class="fleet-row${c.hull === S.selectedHull ? " is-selected" : ""}" type="button" data-hull="${esc(c.hull)}">
      <span class="fleet-dot fleet-dot--${carrierTone(c)}" aria-hidden="true"></span>
      <span class="fleet-main"><span class="fleet-name">${esc(c.name)} <span class="hull">${esc(c.hull)}</span></span>
        <span class="fleet-place">${esc(carrierStatus(c))}${c.heading_to ? `, heading to ${esc(c.heading_to.place || "a stated destination")}` : c.place ? `, ${esc(c.place)}` : ""}</span></span>
      <span class="fleet-age">${c._asOf ? esc(agoShort(c._asOf)) : ""}</span>
    </button></li>`;
  }

  function renderFleetPanel() {
    $("#feedTitle").textContent = "Carrier strike groups";
    let list = S.fleet;
    if (q()) list = list.filter((c) => c._search.includes(q()));
    let head = "";
    if (S.fleetSpot) {
      const set = new Set(S.fleetSpot);
      list = list.filter((c) => set.has(c.hull));
      head = `<li class="spotbar"><span>${list.length} carriers here</span><button class="linkish" type="button" data-clear-fleet>Show all</button></li>`;
    }
    const away = list.filter((c) => !c.at_home), home = list.filter((c) => c.at_home);
    $("#feedCount").textContent = `${S.fleet.filter((c) => !c.at_home).length} of ${S.fleet.length} away`;
    const tt = S.fleetMeta.tracker_time ? Date.parse(S.fleetMeta.tracker_time) : 0;
    $("#feedList").innerHTML = head
      + (away.length ? `<li class="group">At sea or deployed</li>${away.map(fleetRow).join("")}` : "")
      + (home.length ? `<li class="group">Home waters</li>${home.map(fleetRow).join("")}` : "")
      + (list.length ? "" : `<li class="empty"><strong>No carrier positions yet.</strong>They come from USNI News' weekly Fleet and Marine Tracker and daily movement reports.</li>`)
      + `<li class="panel-note">Positions come from USNI News' weekly Fleet and Marine Tracker${tt ? ` (latest ${esc(fmtDay(tt))})` : ""} plus departure and arrival reports. Carriers the tracker doesn't list as deployed are shown at home port. Positions are never estimated between reports.</li>`;
  }

  // ------------------------------------------------------------------ left panel
  const CROSSHAIR = `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><circle cx="8" cy="8" r="4.5"/><path d="M8 1v3M8 12v3M1 8h3M12 8h3"/></svg>`;
  function renderTheaters() {
    $("#theaterList").innerHTML = S.theaters.map((t) => `
      <li><label class="check check--theater">
          <input type="checkbox" data-theater="${esc(t.id)}" ${S.theaterOn.has(t.id) ? "checked" : ""}>
          <span class="box" aria-hidden="true"></span><span class="label">${esc(t.name)}</span>
          <span class="spark" data-spark="${esc(t.id)}" aria-hidden="true"></span><span class="count" data-count="${esc(t.id)}"></span>
        </label>
        ${t.camera ? `<button class="fly" type="button" data-fly="${esc(t.id)}" aria-label="Fly to ${esc(t.name)}" title="Fly to ${esc(t.name)}">${CROSSHAIR}</button>` : '<span class="fly" aria-hidden="true"></span>'}
      </li>`).join("");
  }

  function renderCounts() {
    if (!S.data) return;
    const counts = {};
    for (const e of S.data.events) if (passesFight(e, true)) counts[e.theater] = (counts[e.theater] || 0) + 1;
    document.querySelectorAll("[data-count]").forEach((el) => { el.textContent = counts[el.dataset.count] || 0; });
    const now = Date.now(), tempo = {};
    for (const e of S.data.events) {
      if (e.type === "arms_transfer") continue;
      const idx = 6 - Math.floor((now - e._t0) / DAY);
      if (idx < 0 || idx > 6) continue;
      (tempo[e.theater] = tempo[e.theater] || [0, 0, 0, 0, 0, 0, 0])[idx] += 1;
    }
    document.querySelectorAll("[data-spark]").forEach((el) => {
      const c = tempo[el.dataset.spark] || [0, 0, 0, 0, 0, 0, 0];
      el.innerHTML = sparkSvg(c, 2.8, 1.2);
      el.parentElement.title = `Events per day, last 7 days: ${c.join(", ")}`;
    });
    const byStatus = {};
    for (const e of S.data.events) {
      if (e.type === "arms_transfer" || e._t < now - S.windowH * HOUR || !S.theaterOn.has(e.theater)) continue;
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
      const quiet = s.ok && s.last_post && Date.now() - Date.parse(s.last_post) > 3 * DAY;
      const meta = s.ok ? `${PLATFORM[s.platform] || s.platform}, ${last}` : `${PLATFORM[s.platform] || s.platform}: ${s.error}`;
      return `<li><span class="dot ${s.ok ? (quiet ? "quiet" : "") : "bad"}" aria-hidden="true"></span><span><span class="name">${esc(s.name)}</span><span class="meta">${esc(meta)}</span></span></li>`;
    }).join("");
  }

  function updateFreshness() {
    if (!S.data) return;
    if (DEMO) { $("#beacon").className = "beacon stale"; $("#freshText").textContent = "Demo data. None of these events are real."; return; }
    const t = Date.parse(S.data.generated_at), age = Date.now() - t;
    $("#beacon").className = "beacon " + (age < 45 * 60e3 ? "ok" : age < 3 * HOUR ? "stale" : "dead");
    $("#freshText").textContent = age < 3 * HOUR ? `Updated ${ago(t)}` : `Updated ${ago(t)}. The update job may be paused.`;
  }

  // ------------------------------------------------------------------ details
  function showDetail(html, refresh) {
    const keep = refresh ? $("#detail").scrollTop : 0;
    $("#detail").innerHTML = `<button class="back" type="button" id="backBtn"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M10 3 5 8l5 5"/></svg>Back to the list</button>${html}`;
    $("#feedList").hidden = true;
    $("#feedHead").hidden = true;
    $("#detail").hidden = false;
    $("#detail").scrollTop = keep;
    $("#backBtn").addEventListener("click", () => closeDetail());
    $("#detail").querySelectorAll("[data-goto]").forEach((b) => b.addEventListener("click", () => {
      const [lat, lon] = b.dataset.goto.split(",").map(Number);
      zoomTo(lat, lon, Math.min(world.pointOfView().altitude, 1.2));
    }));
    $("#detail").querySelectorAll("[data-event]").forEach((b) => b.addEventListener("click", () => { if (S.lens !== "fight" && !b.dataset.keepLens) setLens("fight", true); select(b.dataset.event, true); }));
    if (!isMobile() && !refresh) $("#backBtn").focus({ preventScroll: true });
    if (isMobile() && S.sheet < 2) setSheet(2);
  }
  function hideDetail() {
    $("#detail").hidden = true;
    $("#feedList").hidden = false;
    $("#feedHead").hidden = false;
  }
  function closeDetail() {
    S.selectedId = null; S.selectedHull = null; S.selectedFlow = null; S.spot = null; S.fleetSpot = null;
    history.replaceState(null, "", location.pathname + location.search);
    hideDetail();
    render();
    if (S.lastFocus) { const again = document.querySelector(`[data-id="${CSS.escape(S.lastFocus)}"]`); if (again) again.focus(); }
  }

  const reportsHtml = (reports) => `<h2 class="reports-title">Reports (${reports.length})</h2><ul class="reports">${reports.map((r) => `
    <li class="report ${r.side ? "sided" : ""}"><div class="report-head"><span class="report-src">${esc(r.source)}</span><span>${esc(PLATFORM[r.platform] || r.platform)}</span>
      <span>${esc(KIND[r.kind] || r.kind)}${r.side ? `, aligned with ${esc(r.side)}` : ""}</span><span>${esc(ago(Date.parse(r.time)))}</span></div>
      <p>${esc(r.summary)}</p><a href="${esc(safeUrl(r.url))}" target="_blank" rel="noopener noreferrer">Open the original post</a></li>`).join("")}</ul>`;

  function select(id, fly) {
    const e = S.data && S.data.events.find((x) => x.id === id);
    if (!e) return;
    if (e.type === "arms_transfer" && S.lens !== "supply" && (e.transfer || {}).kind !== "interdiction") setLens("supply", true);
    S.selectedId = id; S.selectedHull = null; S.selectedFlow = null;
    history.replaceState(null, "", "#" + encodeURIComponent(id));
    if (fly) {
      const want = e.wave && e.targets.length > 3 ? 1.45 : 1.15;
      zoomTo(e.lat, e.lon, Math.min(world.pointOfView().altitude, want));
    }
    renderEventDetail(e);
    render();
  }

  function renderEventDetail(e, refresh = false) {
    const theaterName = (S.theaters.find((t) => t.id === e.theater) || {}).name || e.theater;
    const facts = [];
    if (e.launched != null) facts.push(`<span>Launched <b>${e.launched}</b> (reported)</span>`);
    if (e.intercepted != null) facts.push(`<span>Intercepted <b>${e.intercepted}</b> (reported)</span>`);
    if (e.killed != null) facts.push(`<span>Killed <b>${e.killed}</b> (reported)</span>`);
    if (e.injured != null) facts.push(`<span>Injured <b>${e.injured}</b> (reported)</span>`);
    const t = e.transfer;
    if (t) {
      facts.push(`<span>From <b>${esc(countryName(t.supplier))}</b> to <b>${esc(countryName(t.recipient))}</b>${MODE[t.mode] ? " " + esc(MODE[t.mode]) : ""}</span>`);
      if (t.from || t.to) facts.push(`<span>Route <b>${esc((t.from && t.from.place) || "not named")}</b> → <b>${esc((t.to && t.to.place) || "not named")}</b></span>`);
      if (t.what) facts.push(`<span>Cargo <b>${esc(t.what)}</b></span>`);
      if (t.value_usd) facts.push(`<span>Value <b>${esc(fmtMoney(t.value_usd))}</b> (reported)</span>`);
    }
    const origins = originsOf(e);
    if (!e.wave && origins.length) facts.push(`<span>Launched from <b>${esc(origins.map((o) => o.place || "an unnamed site").join(", "))}</b></span>`);
    const news = S.data.heat.filter((c) => km(e.lat, e.lon, c.lat, c.lon) <= (e.approx ? 60 : 30)).flatMap((c) => c.urls || []).slice(0, 4);
    const reports = (e.reports || []).slice().sort((a, b) => Date.parse(b.time) - Date.parse(a.time));
    const where = e.wave ? `${esc(metaLine(e))}, ${esc(theaterName)}`
      : `${esc(e.place || "Unnamed location")}, ${esc(theaterName)} ${e.approx ? '<span class="approx">(approximate location)</span>' : ""}`;
    const waveBlock = e.wave ? `
      <h2 class="reports-title">Locations (${e.targets.length})</h2>
      ${e.targets.length ? `<ul class="targets">${e.targets.map((x) => `<li><button class="target" type="button" data-goto="${x.lat},${x.lon}"><span>${esc(x.place || "Unnamed place")}</span>
        <span class="target-meta">${x.reports} ${x.reports === 1 ? "report" : "reports"}${x.killed ? `, ${x.killed} killed` : ""}</span></button></li>`).join("")}</ul>` : `<p class="muted">No specific locations reported yet.</p>`}
      <h2 class="reports-title">Launch areas</h2>
      <p class="muted">${origins.length ? esc(origins.map((o) => o.place || "unnamed site").join(", ")) : "Not named in the reports so far. Lines on the map start from the nearest known launch area and are drawn faint."}</p>` : "";
    showDetail(`
      <div class="detail-type">${esc(e.wave ? "Drone and missile attack wave" : typeLabel(e))}</div>
      <h3>${esc(e.summary)}</h3>
      <p class="detail-where">${where}<br>First reported ${esc(fmtTime(e._t0))}, last update ${esc(ago(e._t))}</p>
      <div class="verdict">${markHtml(e.status)}<div><strong>${esc(STATUS[e.status].label)}</strong><p>${esc(STATUS[e.status].note(e.sources_count, e.news_nearby))}</p></div></div>
      ${facts.length ? `<div class="facts">${facts.join("")}</div>` : ""}
      ${e.legal_basis ? `<div class="legal-basis"><span>Stated legal basis</span><strong>${esc(e.legal_basis)}</strong><p>As reported by the sources below. The dashboard records claimed justifications; it does not assess them.</p></div>` : ""}
      ${waveBlock}
      ${reportsHtml(reports)}
      ${news.length ? `<h2 class="reports-title">News coverage nearby (${e.news_nearby || news.length} outlets)</h2>
        <ul class="news-links">${news.map((u) => `<li><a href="${esc(safeUrl(u))}" target="_blank" rel="noopener noreferrer">${esc(u.replace(/^https?:\/\/(www\.)?/, "").slice(0, 80))}</a></li>`).join("")}</ul>` : ""}
    `, refresh);
  }

  function selectFlow(key, pledge = false) {
    if (S.lens !== "supply") setLens("supply", true);
    const s = S.supply.flows.length || S.supply.pledges.length ? S.supply : buildSupply();
    const f = (pledge ? s.pledges : s.flows).find((x) => x.key === key) || s.flows.find((x) => x.key === key) || s.pledges.find((x) => x.key === key);
    if (!f) return;
    S.selectedFlow = key; S.selectedId = null; S.selectedHull = null;
    const a = f.from || countryCenter(f.supplier), b = f.to || countryCenter(f.recipient);
    if (a && b) {
      const mid = slerp(a, b, 0.5);
      zoomTo(mid.lat, mid.lon, clamp(0.6 + km(a.lat, a.lon, b.lat, b.lon) / 5000, 1.1, 2.6));
    }
    const isPledge = s.pledges.includes(f);
    const route = f.from && f.to ? `${esc(f.from.place || "origin")}${f.via.length ? ` → ${f.via.map((v) => esc(v.place || "hub")).join(" → ")}` : ""} → ${esc(f.to.place || "destination")}`
      : "Not named in reports. The line runs between the two countries and is drawn faint.";
    showDetail(`
      <div class="detail-type">${isPledge ? "Pledged aid" : "Supply flow, last 30 days"}</div>
      <h3>${esc(countryName(f.supplier))} → ${esc(countryName(f.recipient))}</h3>
      <p class="detail-where">${isPledge ? `${f.events.length} ${f.events.length === 1 ? "announcement" : "announcements"}` : `${f.deliveries} ${f.deliveries === 1 ? "delivery" : "deliveries"} reported`}, last ${esc(ago(f.last))}${!isPledge && f.active ? ". Active in the last 72 hours." : ""}</p>
      <div class="verdict">${markHtml(f.status)}<div><strong>${esc(STATUS[f.status].label)}</strong><p>Best confidence among the reports below. On the map, solid lines are corroborated and dashed lines rest on single sources.</p></div></div>
      <div class="facts">
        ${f.modes.length ? `<span>Mode <b>${esc(f.modes.map((m) => MODE[m]).join(", "))}</b></span>` : ""}
        ${f.value ? `<span>Value <b>${esc(fmtMoney(f.value))}</b> (reported)</span>` : ""}
        ${f.cargo.length ? `<span>Cargo <b>${esc(f.cargo.slice(0, 5).join(", "))}</b></span>` : ""}
      </div>
      ${isPledge ? "" : `<h2 class="reports-title">Route</h2><p class="muted">${route}</p>
        <h2 class="reports-title">Deliveries every 2 days</h2><div class="flow-chart">${sparkSvg(f.bins, 9, 3, 34, "flow-bars")}</div>`}
      <h2 class="reports-title">${isPledge ? "Announcements" : "Reported deliveries"} (${f.events.length})</h2>
      <ul class="targets">${f.events.map((e) => `<li><button class="target" type="button" data-event="${esc(e.id)}" data-keep-lens="1"><span>${esc(e.summary)}</span><span class="target-meta">${esc(agoShort(e._t))}</span></button></li>`).join("")}</ul>
    `);
    render();
  }

  function selectCarrier(hull, fly) {
    const c = S.fleet.find((x) => x.hull === hull);
    if (!c) return;
    S.selectedHull = hull; S.selectedId = null; S.selectedFlow = null;
    history.replaceState(null, "", "#" + encodeURIComponent(hull));
    if (fly) zoomTo(c._lat, c._lon, clamp(world.pointOfView().altitude, 1.3, 1.8));
    renderCarrierDetail(c);
    render();
    if (isMobile()) toggleFilters(false);
  }

  function renderCarrierDetail(c, refresh = false) {
    const nearby = S.data.events.filter((e) => e.type !== "arms_transfer" && e._t > Date.now() - 3 * DAY && km(e.lat, e.lon, c._lat, c._lon) <= 600)
      .sort((a, b) => b._t - a._t).slice(0, 6);
    const track = (c.track || []).slice().reverse();
    showDetail(`
      <div class="detail-type">Carrier strike group</div>
      <h3>${esc(c.name)} <span class="hull">${esc(c.hull)}</span></h3>
      <p class="detail-where">${esc(carrierStatus(c))}${c.place ? `, ${esc(c.place)}` : ""}<br>${c._asOf ? `Last reported ${esc(fmtDay(c._asOf))} (${esc(ago(c._asOf))})` : "No position reports yet"}</p>
      ${c.at_home ? `<div class="verdict verdict--ice"><span class="mark mark--ice" aria-hidden="true"></span><div><strong>Shown at home port</strong><p>Not listed as deployed in USNI News' latest Fleet Tracker, so it is in home waters: in port, in maintenance, or training locally.</p></div></div>` : ""}
      ${c.heading_to ? `<div class="verdict verdict--ice"><span class="mark mark--ice" aria-hidden="true"></span><div><strong>Heading to ${esc(c.heading_to.place || "a stated destination")}</strong><p>Destination as stated in reporting. The map does not estimate positions between reports.</p></div></div>` : ""}
      ${c.prev ? `<p class="muted">Previously ${esc(c.prev.place || "elsewhere")}${c.prev.as_of && !c.prev.as_of.startsWith("1970") ? `, ${esc(fmtDay(Date.parse(c.prev.as_of)))}` : ""}.</p>` : ""}
      ${track.length > 1 ? `<h2 class="reports-title">Recent positions</h2><ul class="targets">${track.map((t) => `<li><button class="target" type="button" data-goto="${t.lat},${t.lon}"><span>${esc(t.place || "At sea")}</span><span class="target-meta">${t.time && !t.time.startsWith("1970") ? esc(fmtDay(Date.parse(t.time))) : ""}</span></button></li>`).join("")}</ul>` : ""}
      <h2 class="reports-title">Events within 600 km, last 3 days (${nearby.length})</h2>
      ${nearby.length ? `<ul class="targets">${nearby.map((e) => `<li><button class="target" type="button" data-event="${esc(e.id)}"><span>${esc(e.summary)}</span><span class="target-meta">${esc(agoShort(e._t))}</span></button></li>`).join("")}</ul>` : '<p class="muted">None reported.</p>'}
      <h2 class="reports-title">Source</h2>
      <p class="muted">${esc(c.source || "Reporting")}${c.url ? ` <a href="${esc(safeUrl(c.url))}" target="_blank" rel="noopener noreferrer">Open</a>` : ""}</p>
    `, refresh);
  }

  // ------------------------------------------------------------------ lens switch
  function lensChrome(lens) {
    document.body.dataset.lens = lens;
    document.querySelectorAll("[data-lens]").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.lens === lens)));
    $("#timeNote").hidden = lens !== "supply";
    $("#layerSection").hidden = lens !== "fight";
    $("#search").placeholder = lens === "fight" ? "Search places, sources, text" : lens === "supply" ? "Search countries or cargo" : "Search carriers or places";
  }
  function setLens(lens, keepDetail) {
    if (lens === S.lens && !keepDetail) return;
    S.lens = lens;
    try { localStorage.setItem("gsm_lens", lens); } catch (_) { /* ignore */ }
    lensChrome(lens);
    if (!keepDetail) { S.selectedId = null; S.selectedHull = null; S.selectedFlow = null; S.spot = null; S.fleetSpot = null; hideDetail(); }
    render();
    sailRecentMoves();
  }

  // ------------------------------------------------------------------ mobile sheet
  const sheetHeights = () => [78, Math.round(window.innerHeight * 0.42), Math.round(window.innerHeight * 0.8)];
  function setSheet(n, instant) {
    if (!isMobile()) return;
    S.sheet = n;
    const feed = $("#feed");
    if (instant) feed.style.transition = "none";
    feed.style.height = sheetHeights()[n] + "px";
    feed.classList.toggle("sheet-collapsed", n === 0);
    $("#sheetHandle").setAttribute("aria-expanded", String(n > 0));
    $("#sheetLabel").textContent = n > 0 ? "Collapse the list" : "Expand the list";
    if (instant) { void feed.offsetHeight; feed.style.transition = ""; }
    setTimeout(layout, instant ? 0 : 320);
  }
  function wireSheet() {
    const feed = $("#feed");
    feed.addEventListener("transitionend", (ev) => { if (ev.propertyName === "height" && isMobile()) layout(); });
    let drag = null;
    const start = (ev) => {
      if (!isMobile()) return;
      drag = { y: ev.clientY, h: feed.getBoundingClientRect().height, moved: false, id: ev.pointerId };
      ev.currentTarget.setPointerCapture(ev.pointerId);
      feed.style.transition = "none";
    };
    const move = (ev) => {
      if (!drag || ev.pointerId !== drag.id) return;
      const dy = ev.clientY - drag.y;
      if (Math.abs(dy) > 6) drag.moved = true;
      if (drag.moved) feed.style.height = clamp(drag.h - dy, 70, window.innerHeight * 0.86) + "px";
    };
    const end = (ev) => {
      if (!drag || ev.pointerId !== drag.id) return;
      feed.style.transition = "";
      const dy = ev.clientY - drag.y;
      if (!drag.moved) setSheet(S.sheet === 0 ? 1 : 0);
      else {
        const h = feed.getBoundingClientRect().height, hs = sheetHeights();
        let n = hs.reduce((best, v, i) => (Math.abs(v - h) < Math.abs(hs[best] - h) ? i : best), 0);
        if (Math.abs(dy) > 50 && n === S.sheet) n = clamp(S.sheet + (dy > 0 ? -1 : 1), 0, 2);
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
    $("#windowSeg").innerHTML = WINDOWS.map(([label, h]) => `<button type="button" data-window="${h}" aria-pressed="${h === S.windowH}">${label}</button>`).join("");
    $("#statusList").innerHTML = Object.entries(STATUS).map(([id, s]) => `
      <li><label class="check"><input type="checkbox" data-status="${id}" checked>${markHtml(id)}<span class="label">${esc(s.label)}</span><span class="count" data-status-count="${id}"></span></label></li>`).join("");
    const layer = (key, swatch, label) => `<li><label class="check"><input type="checkbox" data-layer="${key}" ${S.layers[key] ? "checked" : ""}>${swatch}<span class="label">${label}</span><span class="count"></span></label></li>`;
    $("#layerList").innerHTML = [
      layer("arcs", '<svg class="swatch-arc" viewBox="0 0 16 14" fill="none" stroke="currentColor" stroke-width="1.6" stroke-dasharray="2.5 2" aria-hidden="true"><path d="M1.5 12.5C3 4 13 4 14.5 12.5"/></svg>', "Launch paths"),
      layer("diplomacy", '<span class="swatch-diplo" aria-hidden="true"></span>', "Diplomacy"),
      layer("legal", '<span class="swatch-diplo swatch-legal" aria-hidden="true"></span>', "Legal steps"),
    ].join("");
  }

  function setPanelsHidden(hidden) {
    document.body.classList.toggle("panels-hidden", hidden);
    $("#panelsToggle").setAttribute("aria-pressed", String(hidden));
    $("#panelsToggle").title = hidden ? "Show panels (H)" : "Hide panels (H)";
    $("#panelsToggleLabel").textContent = hidden ? "Show panels" : "Hide panels";
    layout();
  }

  function toggleFilters(force) {
    const panel = $("#filters");
    const open = force === undefined ? !panel.classList.contains("open") : force;
    panel.classList.toggle("open", open);
    $("#filtersToggle").setAttribute("aria-expanded", String(open));
  }

  function wire() {
    $("#lensBar").addEventListener("click", (ev) => { const b = ev.target.closest("[data-lens]"); if (b) setLens(b.dataset.lens); });
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
      if (t && t.camera) { world.pointOfView(t.camera, reduceMotion ? 0 : 1500); if (isMobile()) toggleFilters(false); }
    });
    $("#sourcesToggle").addEventListener("click", () => {
      const list = $("#sourcesList");
      list.hidden = !list.hidden;
      $("#sourcesToggle").setAttribute("aria-expanded", String(!list.hidden));
    });
    $("#feedList").addEventListener("click", (ev) => {
      if (ev.target.closest("[data-clear-spot]")) { S.spot = null; render(); return; }
      if (ev.target.closest("[data-clear-fleet]")) { S.fleetSpot = null; render(); return; }
      const hull = ev.target.closest("[data-hull]");
      if (hull) { S.lastFocus = null; selectCarrier(hull.dataset.hull, true); return; }
      const flow = ev.target.closest("[data-flow]");
      if (flow) { selectFlow(flow.dataset.flow, flow.dataset.flowKind === "pledge"); return; }
      const b = ev.target.closest("[data-id]");
      if (b) { S.lastFocus = b.dataset.id; select(b.dataset.id, true); }
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
        if (!$("#detail").hidden || S.spot || S.fleetSpot) closeDetail();
        else if ($("#filters").classList.contains("open")) toggleFilters(false);
      }
      if (typing) return;
      if (ev.key === "/") { ev.preventDefault(); if (!$("#detail").hidden) closeDetail(); $("#search").focus(); }
      if ((ev.key === "h" || ev.key === "H") && !isMobile()) setPanelsHidden(!document.body.classList.contains("panels-hidden"));
      if (ev.key === "1") setLens("fight");
      if (ev.key === "2") setLens("supply");
      if (ev.key === "3") setLens("fleet");
    });
  }

  // ------------------------------------------------------------------ boot
  // Remember this visit so the next one can mark what's new since.
  const markSeen = () => { try { localStorage.setItem("gsm_lastSeen", String(Date.now())); } catch (_) { /* ignore */ } };
  window.addEventListener("pagehide", markSeen);
  setInterval(markSeen, 10 * 60e3);
  buildStaticControls();
  renderTheaters();
  wire();
  lensChrome(S.lens);
  if (isMobile()) setSheet(1, true);
  layout();
  load();
  if (!DEMO) setInterval(load, REFRESH_MS);
  setInterval(() => {
    updateFreshness();
    document.querySelectorAll("#feedList time").forEach((t) => { t.textContent = agoShort(Date.parse(t.dateTime)); });
  }, 30e3);
})();
