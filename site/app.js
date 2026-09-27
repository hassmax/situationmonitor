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
    arms_transfer: "Arms transfer",
    legal: "Legal step",
  };
  const MODE = { air: "by air", sea: "by sea", land: "overland", unspecified: "" };
  const ICE = [205, 228, 255];
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
  // Lowest arc height that still clears the globe's curvature (for sea lanes and ship tracks).
  const hugAlt = (distKm) => 1.25 * (1 - Math.cos(distKm / 6371 / 2)) + 0.004;
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
  const isFlat = (e) => isDiplomacy(e) || e.type === "legal";
  const isBridge = (e) => e.type === "arms_transfer" && ((e.reports || []).length >= 3 || (e.transfer && e.transfer.flights >= 3));
  const typeLabel = (e) => {
    if (e.wave) return "Attack wave";
    if (isBridge(e)) return e.transfer.mode === "sea" ? "Sea bridge" : "Air bridge";
    return TYPES[e.type] || "Event";
  };
  const originsOf = (e) => (e.origins && e.origins.length ? e.origins : e.origin ? [e.origin] : []);
  const isKey = (e) => e.severity >= 3 && e.status === "corroborated";

  // ------------------------------------------------------------------ state
  const S = {
    data: null,
    theaters: FALLBACK_THEATERS,
    windowH: 24,
    theaterOn: new Set(FALLBACK_THEATERS.map((t) => t.id)),
    statusOn: new Set(Object.keys(STATUS)),
    layers: { arcs: true, transfers: true, carriers: true, diplomacy: true, legal: true },
    fleet: [],
    selectedHull: null,
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
    return (0.16 + d.severity * 0.07) * zoomK * sel * (isFlat(d) ? 1.5 : 1);
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
    const meta = metaLine(e);
    const extra = e.wave && e.targets && e.targets.length > 1 ? `<span>${e.targets.length} locations</span>` : "";
    return `<div class="tip">
      <div class="tip-meta"><b>${esc(typeLabel(e))}</b><span>${esc(meta)}</span></div>
      <div class="tip-sum">${esc(e.summary)}</div>
      <div class="tip-foot">${markHtml(e.status)}<span>${esc(STATUS[e.status].label)}</span>${extra}<span>${esc(ago(e._t))}</span></div>
    </div>`;
  }
  function metaLine(e) {
    if (e.wave) return `${countryName(e.attacker)} → ${countryName(e.country)}`;
    if (e.type === "arms_transfer" && e.transfer) return `${countryName(e.transfer.supplier)} → ${countryName(e.transfer.recipient)}`;
    return e.place || "";
  }
  function tipCarrier(c) {
    return `<div class="tip">
      <div class="tip-meta"><b>${esc(c.name)}</b><span>${esc(c.hull)}</span></div>
      <div class="tip-sum">${esc(carrierStatus(c))}${c.place ? `, ${esc(c.place)}` : ""}</div>
      <div class="tip-foot"><span>As of ${esc(fmtDay(c._asOf))}</span>${c.heading_to ? `<span>heading to ${esc(c.heading_to.place || "a stated destination")}</span>` : ""}</div>
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
    .pointAltitude((d) => (d.secondary ? 0.006 : isFlat(d) ? 0.003 : 0.01 + d.severity * 0.016))
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

  const ARC = {
    strike: { stroke: 0.32, dash: 0.45, gap: 0.2, ms: 2300 },
    strikeApprox: { stroke: 0.2, dash: 0.45, gap: 0.2, ms: 3400 },
    transfer: { stroke: 0.5, dash: 0.05, gap: 0.05, ms: 1700 },
    track: { stroke: 0.4, dash: 0.06, gap: 0.04, ms: 5200 },
    plan: { stroke: 0.26, dash: 0.2, gap: 0.14, ms: 8000 },
  };
  world
    .arcStartLat("sLat")
    .arcStartLng("sLng")
    .arcEndLat("eLat")
    .arcEndLng("eLng")
    .arcColor((a) => {
      if (a.kind === "track") return [rgba(ICE, 0.12), rgba(ICE, 0.95)];
      if (a.kind === "plan") return [rgba(ICE, 0.55), rgba(ICE, 0.08)];
      const c = STATUS[a.status].rgb;
      if (a.kind === "transfer") return [rgba(c, 0.25), rgba(c, 1)];
      return [rgba(c, a.kind === "strikeApprox" ? 0.02 : 0.06), rgba(c, a.kind === "strikeApprox" ? 0.42 : 0.92)];
    })
    .arcStroke((a) => ARC[a.kind].stroke)
    .arcDashLength((a) => ARC[a.kind].dash)
    .arcDashGap((a) => ARC[a.kind].gap)
    .arcDashInitialGap(() => Math.random())
    .arcDashAnimateTime((a) => (reduceMotion ? 0 : ARC[a.kind].ms))
    .arcAltitude((a) => (a.alt === undefined ? null : a.alt))
    .arcAltitudeAutoScale(0.36)
    .arcLabel((a) => (a.carrier ? tipCarrier(a.carrier) : tipHtml(a.ref)))
    .onArcHover((a) => { globeEl.style.cursor = a ? "pointer" : ""; })
    .onArcClick((a) => (a.carrier ? selectCarrier(a.carrier.hull, true) : select(a.ref.id, true)));

  // ------------------------------------------------------------------ carriers
  // Icons are small HTML buttons so they stay crisp and are easy to click.
  const carrierEls = new Map();
  const CARRIER_SVG = `<svg viewBox="0 0 28 14" aria-hidden="true"><path d="M1.5 9.2 4 4.8h17.2l5.3 2.6v2.4l-2.4 2.2H3.6z" fill="currentColor"/><path d="M6 6.4h12.5M10 11.2 21 5.4" stroke="rgba(8,22,39,.75)" stroke-width="0.9"/><rect x="17.2" y="9.3" width="3.4" height="2.3" rx="0.4" fill="rgba(8,22,39,.8)"/></svg>`;
  function carrierEl(c) {
    let el = carrierEls.get(c.hull);
    if (!el) {
      el = document.createElement("button");
      el.type = "button";
      el.className = "cvn";
      el.innerHTML = `${CARRIER_SVG}<span></span>`;
      el.addEventListener("click", (ev) => { ev.stopPropagation(); selectCarrier(c.hull, true); });
      el.addEventListener("pointerdown", (ev) => ev.stopPropagation());
      carrierEls.set(c.hull, el);
    }
    el.querySelector("span").textContent = c.short || c.hull;
    el.className = `cvn cvn--${carrierTone(c)}${c.hull === S.selectedHull ? " is-selected" : ""}`;
    el.setAttribute("aria-label", `${c.name}, ${carrierStatus(c)}${c.place ? ", " + c.place : ""}`);
    return el;
  }
  world
    .htmlLat("lat")
    .htmlLng("lon")
    .htmlAltitude(0.012)
    .htmlElement((c) => carrierEl(c))
    .htmlElementVisibilityModifier((el, visible) => {
      el.style.opacity = visible ? "1" : "0";
      el.style.pointerEvents = visible ? "auto" : "none";
    });

  function carrierStatus(c) {
    switch (c.status) {
      case "departed": return "Just departed";
      case "underway": return c.deployed ? "Deployed, underway" : "Underway";
      case "operating": return c.deployed ? "Deployed" : "Operating";
      case "arrived": return "Arrived";
      case "in port": return c.maintenance ? "In maintenance" : "In port";
      default: return "Reported";
    }
  }
  function carrierTone(c) {
    if (c.status === "departed" || c.status === "underway") return "underway";
    if (c.status === "in port") return "port";
    return "deployed";
  }
  const fmtDay = (ms) => new Date(ms).toLocaleDateString(undefined, { month: "short", day: "numeric" });

  // Great-circle interpolation for the "sailing" animation.
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
  const animated = new Set();
  let sailing = false;
  function sailRecentMoves() {
    if (reduceMotion || !S.layers.carriers) return;
    const movers = S.fleet.filter((c) => c.prev && c._moved && Date.now() - c._moved < 7 * 86400e3 && !animated.has(c.hull + c.as_of));
    if (!movers.length) return;
    movers.forEach((c) => { animated.add(c.hull + c.as_of); c._from = { lat: c.prev.lat, lon: c.prev.lon }; c._to = { lat: c._lat, lon: c._lon }; });
    const t0 = performance.now(), dur = 4200;
    sailing = true;
    const ease = (t) => (t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2);
    const step = (now) => {
      const t = Math.min(1, (now - t0) / dur);
      movers.forEach((c) => { const p = slerp(c._from, c._to, ease(t)); c.lat = p.lat; c.lon = p.lon; });
      world.htmlElementsData(S.layers.carriers ? S.fleet : []);
      if (t < 1) requestAnimationFrame(step);
      else sailing = false;
    };
    requestAnimationFrame(step);
  }

  // Clicks that land on the globe, a country dot, a border, or a heat hexagon still pick
  // the nearest event, so small markers don't need pixel-perfect aim.
  world
    .onGlobeClick((coords, ev) => pickNear(ev, coords))
    .onHexPolygonClick((_, ev, coords) => pickNear(ev, coords))
    .onPathClick((_, ev, coords) => pickNear(ev, coords));

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
      (data.fleet || []).forEach((c) => {
        ["as_of", "moved_at", "departed_at"].forEach((k) => { if (c[k]) c[k] = move(c[k]); });
        if (c.prev && c.prev.as_of) c.prev.as_of = move(c.prev.as_of);
        (c.track || []).forEach((t) => { if (t.time) t.time = move(t.time); });
      });
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
    // Carriers: keep the same objects across refreshes so their icons persist.
    const byHull = new Map(S.fleet.map((c) => [c.hull, c]));
    S.fleet = (data.fleet || []).filter((c) => isFinite(c.lat) && isFinite(c.lon)).map((c) => {
      const d = byHull.get(c.hull) || {};
      Object.assign(d, c);
      d._lat = c.lat; d._lon = c.lon;
      d._asOf = Date.parse(c.as_of);
      d._moved = c.moved_at ? Date.parse(c.moved_at) : 0;
      d._fresh = (c.status === "departed" || c.status === "underway") && Date.now() - d._asOf < 72 * 3600e3;
      return d;
    }).sort((a, b) => (carrierTone(a) === "port") - (carrierTone(b) === "port") || a.hull.localeCompare(b.hull));
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
    renderFleetList();
    updateFreshness();
    sailRecentMoves();

    if (S.firstLoad) {
      S.firstLoad = false;
      const fromHash = decodeURIComponent(location.hash.slice(1));
      if (fromHash && data.events.some((e) => e.id === fromHash)) {
        select(fromHash, true);
      } else if (/^CVN-\d{2}$/.test(fromHash) && S.fleet.some((c) => c.hull === fromHash)) {
        selectCarrier(fromHash, true);
      } else {
        const alt = isMobile() ? 3.0 : 2.25;
        world.pointOfView({ lat: 27, lng: 40, altitude: alt }, reduceMotion ? 0 : 2600);
      }
    } else if (S.selectedId) {
      const still = data.events.find((e) => e.id === S.selectedId);
      if (still) renderDetail(still, true);
    } else if (S.selectedHull) {
      const c = S.fleet.find((x) => x.hull === S.selectedHull);
      if (c) renderCarrierDetail(c, true);
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
    if (!S.layers.legal && e.type === "legal") return false;
    if (!S.layers.transfers && e.type === "arms_transfer") return false;
    const q = S.query.trim().toLowerCase();
    if (q && !e._search.includes(q)) return false;
    return true;
  }

  function visibleEvents() {
    return S.data ? S.data.events.filter((e) => passesBase(e)).sort((a, b) => b._t - a._t) : [];
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
    const arcs = [];
    const push = (e, o, d, approx) => {
      const dist = km(o.lat, o.lon, d.lat, d.lon);
      if (dist < 25 || (approx && dist > 1800)) return;
      arcs.push({ ref: e, sLat: o.lat, sLng: o.lon, eLat: d.lat, eLng: d.lon, status: e.status, kind: approx ? "strikeApprox" : "strike" });
    };
    for (const e of events) {
      if (arcs.length >= 240) break;
      if (e.type === "arms_transfer") {
        const t = e.transfer;
        if (!S.layers.transfers || !t || !t.from || !t.to) continue;
        const dist = km(t.from.lat, t.from.lon, t.to.lat, t.to.lon);
        if (dist < 25) continue;
        const alt = t.mode === "sea" || t.mode === "land" ? hugAlt(dist) : Math.min(0.55, 0.06 + dist / 22000);
        arcs.push({ ref: e, sLat: t.from.lat, sLng: t.from.lon, eLat: t.to.lat, eLng: t.to.lon, status: e.status, kind: "transfer", alt });
        continue;
      }
      if (!S.layers.arcs) continue;
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
    if (S.layers.carriers) {
      for (const c of S.fleet) {
        if (c.prev && c._moved && Date.now() - c._moved < 14 * 86400e3 && km(c.prev.lat, c.prev.lon, c._lat, c._lon) > 100) {
          arcs.push({ carrier: c, sLat: c.prev.lat, sLng: c.prev.lon, eLat: c._lat, eLng: c._lon, kind: "track", alt: hugAlt(km(c.prev.lat, c.prev.lon, c._lat, c._lon)) });
        }
        if (c.heading_to && km(c._lat, c._lon, c.heading_to.lat, c.heading_to.lon) > 100) {
          arcs.push({ carrier: c, sLat: c._lat, sLng: c._lon, eLat: c.heading_to.lat, eLng: c.heading_to.lon, kind: "plan", alt: hugAlt(km(c._lat, c._lon, c.heading_to.lat, c.heading_to.lon)) });
        }
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
    if (!sailing) world.htmlElementsData(S.layers.carriers ? S.fleet : []);
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
      if (isFlat(e) || e.type === "arms_transfer") continue;
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
    if (S.layers.carriers && !reduceMotion) {
      for (const c of S.fleet) {
        if (c._fresh) rings.push({ lat: c._lat, lon: c._lon, rgb: ICE, alpha: 0.6, max: 3.2, speed: 0.9, period: 2600 });
      }
    }
    const selC = S.selectedHull && S.fleet.find((c) => c.hull === S.selectedHull);
    if (selC) rings.push({ lat: selC._lat, lon: selC._lon, rgb: [234, 240, 246], alpha: 0.9, max: 4, speed: reduceMotion ? 0 : 2.2, period: 1200 });
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
    const meta = metaLine(e);
    const extra = [];
    if (e.wave && e.targets.length) extra.push(`${e.targets.length} ${e.targets.length === 1 ? "location" : "locations"}`);
    if (e.wave && e.launched) extra.push(`${e.launched} launched`);
    if (e.type === "arms_transfer" && e.transfer) {
      if (MODE[e.transfer.mode]) extra.push(MODE[e.transfer.mode]);
      if (e.transfer.flights) extra.push(`${e.transfer.flights} ${e.transfer.mode === "sea" ? "ships" : "flights"}`);
    }
    if (e.legal_basis) extra.push("Legal basis stated");
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
    S.selectedHull = null;
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
    S.selectedHull = null;
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
    if (e.type === "arms_transfer" && e.transfer) {
      const t = e.transfer;
      facts.push(`<span>From <b>${esc(countryName(t.supplier))}</b> to <b>${esc(countryName(t.recipient))}</b>${MODE[t.mode] ? " " + esc(MODE[t.mode]) : ""}</span>`);
      if (t.from && t.to) facts.push(`<span>Route <b>${esc(t.from.place || "origin")}</b> → <b>${esc(t.to.place || "destination")}</b></span>`);
      if (t.what) facts.push(`<span>Cargo <b>${esc(t.what)}</b></span>`);
      if (t.flights) facts.push(`<span><b>${t.flights}</b> ${t.mode === "sea" ? "sailings" : "flights"} (reported)</span>`);
    }
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
      ${e.legal_basis ? `<div class="legal-basis"><span>Stated legal basis</span><strong>${esc(e.legal_basis)}</strong><p>As reported by the sources below. The dashboard records claimed justifications; it does not assess them.</p></div>` : ""}
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

  function selectCarrier(hull, fly) {
    const c = S.fleet.find((x) => x.hull === hull);
    if (!c) return;
    S.selectedHull = hull;
    S.selectedId = null;
    history.replaceState(null, "", "#" + encodeURIComponent(hull));
    controls.autoRotate = false;
    if (fly) world.pointOfView({ lat: c._lat, lng: c._lon, altitude: Math.max(1.3, Math.min(world.pointOfView().altitude, 1.8)) }, reduceMotion ? 0 : 1300);
    renderCarrierDetail(c);
    render();
    renderFleetList();
    if (isMobile()) { toggleFilters(false); if (S.sheet < 2) setSheet(2); }
  }

  function renderCarrierDetail(c, refresh = false) {
    const keepScroll = refresh ? $("#detail").scrollTop : 0;
    const nearby = visibleEvents().filter((e) => km(e.lat, e.lon, c._lat, c._lon) <= 600).slice(0, 6);
    const track = (c.track || []).slice().reverse();
    $("#detail").innerHTML = `
      <button class="back" type="button" id="backBtn">
        <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M10 3 5 8l5 5"/></svg>
        Back to the list
      </button>
      <div class="detail-type">Carrier strike group</div>
      <h3>${esc(c.name)} <span class="hull">${esc(c.hull)}</span></h3>
      <p class="detail-where">${esc(carrierStatus(c))}${c.place ? `, ${esc(c.place)}` : ""}<br>
        Last reported ${esc(fmtDay(c._asOf))} (${esc(ago(c._asOf))})</p>
      ${c.heading_to ? `<div class="verdict verdict--ice"><span class="mark mark--ice" aria-hidden="true"></span><div><strong>Heading to ${esc(c.heading_to.place || "a stated destination")}</strong><p>Destination as stated in reporting. The map does not estimate positions between reports.</p></div></div>` : ""}
      ${c.prev ? `<p class="muted">Previously ${esc(c.prev.place || "elsewhere")}${c.prev.as_of ? `, ${esc(fmtDay(Date.parse(c.prev.as_of)))}` : ""}.</p>` : ""}
      ${track.length > 1 ? `<h2 class="reports-title">Recent positions</h2><ul class="targets">${track.map((t) => `
        <li><button class="target" type="button" data-lat="${t.lat}" data-lon="${t.lon}"><span>${esc(t.place || "At sea")}</span><span class="target-meta">${esc(fmtDay(Date.parse(t.time)))}</span></button></li>`).join("")}</ul>` : ""}
      <h2 class="reports-title">Events within 600 km (${nearby.length})</h2>
      ${nearby.length ? `<ul class="targets">${nearby.map((e) => `
        <li><button class="target" type="button" data-event="${esc(e.id)}"><span>${esc(e.summary)}</span><span class="target-meta">${esc(agoShort(e._t))}</span></button></li>`).join("")}</ul>` : '<p class="muted">None in the current time window.</p>'}
      <h2 class="reports-title">Source</h2>
      <p class="muted">${esc(c.source || "Reporting")}${c.url ? ` <a href="${esc(safeUrl(c.url))}" target="_blank" rel="noopener noreferrer">Open</a>` : ""}</p>
    `;
    $("#feedList").hidden = true;
    $("#feedHead").hidden = true;
    $("#detail").hidden = false;
    $("#detail").scrollTop = keepScroll;
    $("#backBtn").addEventListener("click", () => { closeDetail(false); renderFleetList(); });
    $("#detail").querySelectorAll("[data-lat]").forEach((b) => b.addEventListener("click", () => {
      world.pointOfView({ lat: Number(b.dataset.lat), lng: Number(b.dataset.lon), altitude: Math.min(world.pointOfView().altitude, 1.4) }, reduceMotion ? 0 : 900);
    }));
    $("#detail").querySelectorAll("[data-event]").forEach((b) => b.addEventListener("click", () => select(b.dataset.event, true)));
    if (!isMobile() && !refresh) $("#backBtn").focus({ preventScroll: true });
  }

  function renderFleetList() {
    const list = $("#fleetList");
    if (!list) return;
    $("#fleetNote").textContent = S.fleet.length ? `${S.fleet.filter((c) => carrierTone(c) !== "port").length} at sea` : "";
    if (!S.fleet.length) {
      list.innerHTML = '<li class="muted small">No positions yet. They come from USNI News\u2019 weekly Fleet and Marine Tracker and daily movement reports.</li>';
      return;
    }
    list.innerHTML = S.fleet.map((c) => `
      <li><button class="fleet-row${c.hull === S.selectedHull ? " is-selected" : ""}" type="button" data-hull="${esc(c.hull)}">
        <span class="fleet-dot fleet-dot--${carrierTone(c)}" aria-hidden="true"></span>
        <span class="fleet-name">${esc(c.short || c.name)}</span>
        <span class="fleet-place">${esc(c.heading_to ? `→ ${c.heading_to.place || "en route"}` : c.place || "")}</span>
        <span class="fleet-age">${esc(agoShort(c._asOf))}</span>
      </button></li>`).join("");
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

    const layer = (key, swatch, label) => `
      <li><label class="check">
        <input type="checkbox" data-layer="${key}" ${S.layers[key] ? "checked" : ""}>
        ${swatch}
        <span class="label">${label}</span><span class="count"></span>
      </label></li>`;
    $("#layerList").innerHTML = [
      layer("arcs", '<svg class="swatch-arc" viewBox="0 0 16 14" fill="none" stroke="currentColor" stroke-width="1.6" stroke-dasharray="2.5 2" aria-hidden="true"><path d="M1.5 12.5C3 4 13 4 14.5 12.5"/></svg>', "Launch paths"),
      layer("transfers", '<svg class="swatch-arc" viewBox="0 0 16 14" fill="none" stroke="currentColor" stroke-width="2" stroke-dasharray="0.8 1.8" stroke-linecap="round" aria-hidden="true"><path d="M1.5 12.5C3 3 13 3 14.5 12.5"/></svg>', "Arms transfers"),
      layer("carriers", '<svg class="swatch-cvn" viewBox="0 0 28 14" aria-hidden="true"><path d="M1.5 9.2 4 4.8h17.2l5.3 2.6v2.4l-2.4 2.2H3.6z" fill="currentColor"/></svg>', "Carrier strike groups"),
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
      const row = ev.target.closest("[data-hull]");
      if (row) { selectCarrier(row.dataset.hull, true); return; }
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
        if (S.selectedId || S.selectedHull || S.spot) closeDetail(false);
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
