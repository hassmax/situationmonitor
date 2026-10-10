(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const params = new URLSearchParams(location.search);
  const DEMO = params.has("demo");
  // Freeze the asset root before event selection changes the address. Shared pages use ../../.
  const APP_ROOT = new URL(".", document.baseURI);
  const baseElement = document.querySelector("base");
  if (baseElement) baseElement.href = APP_ROOT.href;
  const eventIdFrom = (value) => {
    try { return decodeURIComponent(value || "").trim().replace(/,+$/, ""); } catch { return ""; }
  };
  const INITIAL_EVENT_ID = eventIdFrom(location.hash.slice(1)) || (location.pathname.match(/\/events\/([a-f0-9]{12})\/$/) || [])[1] || "";
  const eventLink = (id) => /^[a-f0-9]{12}$/.test(id) && !DEMO
    ? new URL(`events/${id}/`, APP_ROOT).href : `${APP_ROOT.href}${DEMO ? "?demo" : ""}#${encodeURIComponent(id)}`;
  let sharedSnapshot = null;
  try { sharedSnapshot = JSON.parse(document.getElementById("sharedEvent")?.textContent || "null"); } catch { /* live data still opens */ }
  // This page's own version: the commit stamped into its script address by the update workflow
  // (app.js?v=<commit>); empty when run locally. See checkBuild.
  const OWN_BUILD = (() => {
    const m = document.currentScript && /[?&]v=([^&]+)/.exec(document.currentScript.getAttribute("src") || "");
    return m && m[1] !== "__BUILD__" ? m[1] : "";
  })();
  if (params.has("v")) history.replaceState(null, "", location.pathname + location.hash);  // the address a self-reload used
  const REFRESH_MS = 5 * 60 * 1000;
  const HOUR = 3600e3, DAY = 86400e3;
  const LIVE_MS = 6 * HOUR;          // events this recent animate
  const MAX_ANIMATED = 20;           // but only this many at once, newest first, so busy nights stay smooth
  // Phones get lighter limits: fewer markers and animations, and a lower render resolution.
  const PHONE = window.matchMedia("(max-width: 859px), (pointer: coarse)").matches;
  const MAX_MARKERS = PHONE ? 160 : 320;
  const MAX_ANIMATED_NOW = PHONE ? 8 : MAX_ANIMATED;
  // Asked many times a frame (marker layout while the globe turns): reading the window's width after
  // markers moved forced the browser to lay the whole page out again each time (half a second a
  // second of dragging on a slow phone, 2026-10-05), so it is read once and on resize.
  let mobileNow = window.innerWidth < 1024;
  window.addEventListener("resize", () => { mobileNow = window.innerWidth < 1024; });
  const isMobile = () => mobileNow;
  // Phones and tablets have no pointer to hover with: a first tap on a marker shows the card that
  // pointing shows on desktop, a second tap (or a tap on the card) opens it.
  const hoverMq = window.matchMedia("(hover: hover) and (pointer: fine)");
  const canHover = () => hoverMq.matches;

  // ------------------------------------------------------------------ vocabulary
  // Confidence is shown by the marker's style (solid, outline, dashed); color and icon show what happened.
  const STATUS = {
    corroborated: { label: "Corroborated", rank: 3, conf: "solid", alpha: 0.95,
      note: (n, news) => `Reported by ${n} independent sources${news >= 3 ? ", including nearby news coverage" : ""}.` },
    unconfirmed: { label: "Single source", rank: 2, conf: "outline", alpha: 0.7, note: () => "Only one source so far. Treat it as unverified." },
    claimed: { label: "One side's claim", rank: 1, conf: "dashed", alpha: 0.6, note: () => "Based on statements or reports from one side of the conflict." },
  };
  const TYPES = {
    airstrike: "Airstrike", missile_drone: "Drone or missile attack", artillery: "Shelling", ground: "Ground fighting",
    territory: "Territorial change", air_defense: "Air defense", naval: "Naval incident", explosion: "Explosion",
    deployment: "Deployment or exercise", diplomacy: "Diplomacy", ceasefire: "Diplomacy", hybrid: "Sabotage or hybrid attack",
    incursion: "Airspace or border incursion", arms_transfer: "Arms transfer", legal: "Legal step",
    production: "Arms production",
  };
  // type -> [category, icon, animation]
  const CAT = {
    missile_drone: ["strike", "missile", "impact"], airstrike: ["strike", "air", "drop"], air_defense: ["strike", "missile", ""],
    explosion: ["strike", "blast", "flash"], artillery: ["ground", "artillery", "shell"], ground: ["ground", "ground", "clash"],
    territory: ["ground", "territory", "pulse"], naval: ["naval", "naval", "ripple"], deployment: ["deploy", "deploy", ""],
    hybrid: ["hybrid", "hybrid", "pulse"], incursion: ["hybrid", "incursion", "pulse"], diplomacy: ["diplo", "diplo", ""],
    ceasefire: ["diplo", "diplo", ""], legal: ["diplo", "legal", ""], arms_transfer: ["supply", "crate", ""],
    production: ["supply", "factory", ""],
  };
  const CAT_RGB = { strike: [255, 91, 58], ground: [245, 165, 36], naval: [76, 195, 255], deploy: [159, 184, 212],
    hybrid: [177, 140, 255], diplo: [233, 238, 245], supply: [63, 193, 201], aid: [96, 214, 122], fleet: [205, 228, 255] };
  const ICONS = {
    missile: '<path d="m12 2 2 2-5 7-4-4zM5 7l-3 1 2 2M9 11l-1 3-2-2M4 12l-2 2M10 4l2 2"/>',
    air: '<path d="M8 1v12M8 4l6 5v2l-6-2-6 2V9zM5 14l3-2 3 2"/>',
    shield: '<path d="m8 1 6 3v4c0 3-3 5-6 7-3-2-6-4-6-7V4zM5 8l2 2 4-4"/>',
    blast: '<path d="M2 5V2h3M11 2h3v3M14 11v3h-3M5 14H2v-3M8 5v6M5 8h6"/><circle cx="8" cy="8" r="3"/>',
    artillery: '<path d="M2 12h12M4 10l7-7 2 2-7 7M2 14l3-4M12 2l2 2"/><circle cx="5" cy="12" r="2"/>',
    ground: '<path d="M2 3h12v10H2zM2 3l12 10M14 3 2 13"/>',
    territory: '<path d="M3 14V2h10l-2 3 2 3H3M1 14h4"/>',
    naval: '<path d="M3 7h10l-2 5H5zM6 7V4h4v3M8 4V1M1 14l3-1 4 1 4-1 3 1"/>',
    deploy: '<path d="m3 7 5-4 5 4M3 12l5-4 5 4M3 15h10"/>',
    hybrid: '<path d="M5 5h6v6H5zM2 2l3 3M11 5l3-3M2 14l3-3M11 11l3 3M8 1v2M8 13v2M1 8h2M13 8h2"/>',
    incursion: '<path d="M2 8h9M8 5l3 3-3 3M13 2v4M13 10v4"/>',
    diplo: '<path d="m2 6 3-3 6 10 3-3M2 10l3 3L11 3l3 3M1 8h4M11 8h4"/>',
    legal: '<path d="M8 2v12M4 14h8M2 4h12M3 4l-2 6h4zM13 4l-2 6h4z"/>',
    coin: '<path d="m8 1 6 3v8l-6 3-6-3V4zM10 5H6v3h4v3H6M8 3v2M8 11v2"/>',
    crate: '<path d="m2 5 6-3 6 3v6l-6 3-6-3zM2 5l6 3 6-3M8 8v6M5 3.5l6 3v3"/>',
    factory: '<path d="M2 14V8l4-3v3l4-3v3h4v6zM11 8V2h3v6M5 11v1M8 11v1M11 11v1"/>',
    carrier: '<path d="m1 9 2-3h10l2 2-2 4H3zM5 8h6M9 6V3h3v3M5 14h6"/>',
    alert: '<path d="m8 2 7 12H1zM8 6v4M8 12v.1"/>',
  };
  const svgIcon = (name) => `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.25" stroke-linecap="square" stroke-linejoin="miter" aria-hidden="true">${ICONS[name] || ICONS.blast}</svg>`;
  // "On the map": each entry is also a filter. The key is the marker's icon (see legendKey).
  const LEGEND = [
    ["missile", "strike", "Drone or missile"], ["air", "strike", "Airstrike"],
    ["blast", "strike", "Explosion"], ["artillery", "ground", "Shelling"],
    ["ground", "ground", "Ground fighting"], ["territory", "ground", "Territory change"], ["naval", "naval", "Naval"],
    ["hybrid", "hybrid", "Hybrid attack"], ["incursion", "hybrid", "Incursion"], ["deploy", "deploy", "Deployment"],
    ["diplo", "diplo", "Diplomacy, legal"], ["crate", "supply", "Arms or forces moved"], ["factory", "supply", "Arms production"], ["coin", "aid", "Financial aid"],
    ["carrier", "fleet", "US carrier at sea"],
  ];
  const MODE = { air: "by air", sea: "by sea", land: "overland", unspecified: "" };
  const PLATFORM = { bluesky: "Bluesky", telegram: "Telegram", rss: "News feed", gdelt: "GDELT", map: "Map data", maproom: "ISW map", adsb: "Flight tracking" };
  const KIND = { official: "Official", partisan: "Partisan", osint: "OSINT", news: "News", analysis: "Analysis" };
  const WINDOWS = [["6h", 6], ["24h", 24], ["3d", 72], ["7d", 168]];
  const ACCENT = [255, 90, 54];  // live, new and selected (styles.css --accent)
  const FALLBACK_THEATERS = [
    { id: "ukraine", name: "Russia–Ukraine", camera: { lat: 48.5, lng: 34, altitude: 0.85 }, highlight: ["804"] },
    { id: "nato_east", name: "NATO flank and hybrid", camera: { lat: 56, lng: 22, altitude: 1.1 }, highlight: ["233", "428", "440", "246", "616"] },
    { id: "mideast", name: "Middle East", camera: { lat: 28.5, lng: 45, altitude: 1.25 }, highlight: [] },
    { id: "horn", name: "Sudan and Horn of Africa", camera: { lat: 11, lng: 36, altitude: 1.2 }, highlight: [] },
    { id: "drc_sahel", name: "Eastern DRC and Sahel", camera: { lat: 8, lng: 10, altitude: 1.6 }, highlight: [] },
    { id: "indopac", name: "Indo-Pacific", camera: { lat: 22, lng: 118, altitude: 1.5 }, highlight: [] },
    { id: "latam", name: "Latin America", camera: { lat: 14, lng: -76, altitude: 1.4 }, highlight: [] },
    { id: "global", name: "Worldwide", camera: { lat: 30, lng: 10, altitude: 2.6 }, highlight: [], listed: false },
  ];

  const ISO_NUM = new Map("AD020,AE784,AF004,AG028,AI660,AL008,AM051,AO024,AQ010,AR032,AS016,AT040,AU036,AW533,AX248,AZ031,BA070,BB052,BD050,BE056,BF854,BG100,BH048,BI108,BJ204,BL652,BM060,BN096,BO068,BQ535,BR076,BS044,BT064,BV074,BW072,BY112,BZ084,CA124,CC166,CD180,CF140,CG178,CH756,CI384,CK184,CL152,CM120,CN156,CO170,CR188,CU192,CV132,CW531,CX162,CY196,CZ203,DE276,DJ262,DK208,DM212,DO214,DZ012,EC218,EE233,EG818,EH732,ER232,ES724,ET231,FI246,FJ242,FK238,FM583,FO234,FR250,GA266,GB826,GD308,GE268,GF254,GG831,GH288,GI292,GL304,GM270,GN324,GP312,GQ226,GR300,GS239,GT320,GU316,GW624,GY328,HK344,HM334,HN340,HR191,HT332,HU348,ID360,IE372,IL376,IM833,IN356,IO086,IQ368,IR364,IS352,IT380,JE832,JM388,JO400,JP392,KE404,KG417,KH116,KI296,KM174,KN659,KP408,KR410,KW414,KY136,KZ398,LA418,LB422,LC662,LI438,LK144,LR430,LS426,LT440,LU442,LV428,LY434,MA504,MC492,MD498,ME499,MF663,MG450,MH584,MK807,ML466,MM104,MN496,MO446,MP580,MQ474,MR478,MS500,MT470,MU480,MV462,MW454,MX484,MY458,MZ508,NA516,NC540,NE562,NF574,NG566,NI558,NL528,NO578,NP524,NR520,NU570,NZ554,OM512,PA591,PE604,PF258,PG598,PH608,PK586,PL616,PM666,PN612,PR630,PS275,PT620,PW585,PY600,QA634,RE638,RO642,RS688,RU643,RW646,SA682,SB090,SC690,SD729,SE752,SG702,SH654,SI705,SJ744,SK703,SL694,SM674,SN686,SO706,SR740,SS728,ST678,SV222,SX534,SY760,SZ748,TC796,TD148,TF260,TG768,TH764,TJ762,TK772,TL626,TM795,TN788,TO776,TR792,TT780,TV798,TW158,TZ834,UA804,UG800,UM581,US840,UY858,UZ860,VA336,VC670,VE862,VG092,VI850,VN704,VU548,WF876,WS882,YE887,YT175,ZA710,ZM894,ZW716".split(",").map((s) => [s.slice(0, 2), s.slice(2)]));
  let regionNames = null;
  try { regionNames = new Intl.DisplayNames(["en"], { type: "region" }); } catch (_) { /* old browser */ }
  // Region words for search: events store countries, but people search for "Europe" or "Middle East".
  const EUROPE = new Set("AL AD AT BY BE BA BG HR CY CZ DK EE FI FR DE GR HU IS IE IT XK LV LI LT LU MT MD MC ME NL MK NO PL PT RO SM RS SK SI ES SE CH UA GB VA".split(" "));
  const THEATER_WORDS = { mideast: "middle east", horn: "africa", drc_sahel: "africa", indopac: "asia", latam: "latin america" };
  const regionWords = (e) => [EUROPE.has(e.country) || (e.theater === "nato_east" && !e.country) ? "europe european" : "", THEATER_WORDS[e.theater] || ""].join(" ");
  const countryName = (code) => { if (!code) return ""; try { return (regionNames && regionNames.of(code)) || code; } catch (_) { return code; } };

  // Known launch areas, used only when a report doesn't name one (those lines are drawn faint).
  const ANCHORS = {
    RU: [["Kursk", 51.73, 36.19], ["Oryol", 52.97, 36.06], ["Bryansk", 53.24, 34.36], ["Belgorod", 50.6, 36.6],
      ["Millerovo", 48.92, 40.4], ["Primorsko-Akhtarsk", 46.05, 38.17], ["Hvardiiske, Crimea", 45.12, 33.97]],
    UA: [["Sumy", 50.91, 34.8], ["Chernihiv", 51.5, 31.29], ["Kharkiv", 49.99, 36.23], ["Zaporizhzhia", 47.84, 35.14], ["Mykolaiv", 46.97, 32.0]],
    IR: [["Kermanshah", 34.31, 47.07], ["Tabriz", 38.08, 46.29], ["Isfahan", 32.65, 51.67], ["Bandar Abbas", 27.8, 56.25]],
    YE: [["Sanaa", 15.37, 44.19], ["Hodeidah", 14.8, 42.95], ["Saada", 16.94, 43.76]],
    LB: [["Nabatieh", 33.38, 35.48], ["Tyre", 33.27, 35.2]],
    IL: [["southern Israel", 31.25, 34.79], ["northern Israel", 32.8, 35.1]],
    // North Korea's usual launch areas: Sunan (Pyongyang), the Wonsan coast, Sohae, Sinpo
    KP: [["Pyongyang", 39.2, 125.67], ["Wonsan", 39.17, 127.48], ["Sohae", 39.66, 124.71], ["Sinpo", 40.03, 128.18]],
  };
  Object.keys(ANCHORS).forEach((k) => { ANCHORS[k] = ANCHORS[k].map(([place, lat, lon]) => ({ place, lat, lon })); });

  // Watched airbases are extra approximate origins for airstrikes near the attacker's territory.
  // Coordinates match pipeline/config/flights.yaml; they are not evidence of a specific sortie.
  const AIRBASES = {
    GB: [["RAF Fairford", 51.682, -1.790], ["RAF Lakenheath", 52.409, 0.561], ["RAF Mildenhall", 52.362, 0.486], ["RAF Brize Norton", 51.750, -1.584]],
    DE: [["Ramstein Air Base", 49.437, 7.600], ["Spangdahlem Air Base", 49.973, 6.692]],
    IT: [["Aviano Air Base", 46.032, 12.597], ["Sigonella", 37.402, 14.922]],
    ES: [["Morón Air Base", 37.175, -5.616], ["Rota", 36.645, -6.349]],
    GR: [["Souda Bay", 35.533, 24.150]],
    PL: [["Rzeszów-Jasionka", 50.110, 22.019]],
    RO: [["Mihail Kogălniceanu", 44.362, 28.488]],
    NO: [["Ørland", 63.699, 9.604]],
    TR: [["Incirlik Air Base", 37.002, 35.426]],
    CY: [["RAF Akrotiri", 34.590, 32.988]],
    IL: [["Nevatim Air Base", 31.208, 35.012]],
    JO: [["Muwaffaq Salti Air Base", 31.827, 36.782]],
    SA: [["Prince Sultan Air Base", 24.063, 47.580]],
    QA: [["Al Udeid Air Base", 25.117, 51.315]],
    AE: [["Al Dhafra Air Base", 24.248, 54.548]],
    KW: [["Ali Al Salem Air Base", 29.347, 47.521]],
    DJ: [["Camp Lemonnier", 11.547, 43.159]],
    US: [["Eielson Air Force Base", 64.666, -147.101], ["Joint Base Pearl Harbor-Hickam", 21.319, -157.922]],
    JP: [["Kadena Air Base", 26.356, 127.768], ["Misawa Air Base", 40.703, 141.368], ["Yokota Air Base", 35.749, 139.348]],
    KR: [["Osan Air Base", 37.090, 127.030]],
    AU: [["RAAF Base Tindal", -14.521, 132.378], ["RAAF Base Amberley", -27.640, 152.712]],
  };
  Object.keys(AIRBASES).forEach((k) => { AIRBASES[k] = AIRBASES[k].map(([place, lat, lon]) => ({ place, lat, lon })); });
  const launchAreas = (e) => e.type === "airstrike"
    ? [...(ANCHORS[e.attacker] || []), ...(AIRBASES[e.attacker] || [])]
    : (ANCHORS[e.attacker] || []);
  const REP_POINT = {
    RU: { lat: 55.75, lon: 37.62 }, US: { lat: 38.9, lon: -77.0 }, CN: { lat: 34.3, lon: 113.6 }, CA: { lat: 45.4, lon: -75.7 },
    AU: { lat: -33.9, lon: 151.2 }, BR: { lat: -15.8, lon: -47.9 }, IN: { lat: 28.6, lon: 77.2 }, KZ: { lat: 51.2, lon: 71.4 },
  };

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
  const hugAlt = (distKm) => 1.25 * (1 - Math.cos(distKm / 6371 / 2)) + 0.004;
  function nearest(list, p) {
    let best = null, bd = Infinity;
    for (const o of list || []) { const d = km(o.lat, o.lon, p.lat, p.lon); if (d < bd) { best = o; bd = d; } }
    return best;
  }
  // the n nearest of a list to a point
  const nearestN = (list, p, n) => (list || []).map((o) => [km(o.lat, o.lon, p.lat, p.lon), o]).sort((a, b) => a[0] - b[0]).slice(0, n).map((x) => x[1]);
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
  const fmtEvidenceTime = (ms) => new Date(ms).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", timeZoneName: "short" });
  const fmtDay = (ms) => new Date(ms).toLocaleDateString(undefined, { month: "short", day: "numeric" });
  const fmtMoney = (v) => (v >= 1e9 ? `$${(v / 1e9).toFixed(1)} billion` : v >= 1e6 ? `$${Math.round(v / 1e6)} million` : `$${Math.round(v).toLocaleString()}`);
  // Alerts (drones or missiles reported in flight, grouped per country per day) get a siren and never animate.
  // Money (grants, loans, funds, compensation) is financial aid, shown in green apart from weapons.
  // The model marks it (transfer.money); events stored before that are recognized by their wording.
  const MONEY_RE = /\b(loans?|grants?|fund(s|ing)?|financ\w*|compensat\w*|reimburs\w*|budget|allocat\w*|credit)\b/i;
  const isMoney = (e) => { const t = e.transfer || {}; return typeof t.money === "boolean" ? t.money : MONEY_RE.test(`${t.what || ""} ${e.summary || ""}`); };
  const catOf = (e) => (e.alert ? ["strike", "alert", ""] : e.type === "arms_transfer" && isMoney(e) ? ["aid", "coin", ""] : CAT[e.type] || ["strike", "blast", ""]);
  // Which "On the map" entry an event belongs to (legal steps share the diplomacy entry).
  // Drone and missile attacks, interceptions, and warnings of drones in flight are one filter.
  const legendKey = (e) => { const icon = catOf(e)[1]; return icon === "legal" ? "diplo" : icon === "alert" ? "missile" : icon; };
  const alertsText = (e) => { const n = e.alerts || (e.reports || []).length || 1; return `${n} ${n === 1 ? "alert" : "alerts"}`; };
  const isDiplomacy = (e) => e.type === "diplomacy" || e.type === "ceasefire" || e.type === "legal";
  const tkind = (e) => (e.transfer && e.transfer.kind) || "delivery";
  const typeLabel = (e) => {
    if (e.alert) return "Drone and missile alerts";
    if (e.wave) return "Drone and missile attack wave";
    if (e.type === "arms_transfer") {
      if (e.transfer && e.transfer.supplier && e.transfer.supplier === e.transfer.recipient) return "Forces moved";
      return { pledge: "Pledged aid", interdiction: "Intercepted shipment" }[tkind(e)] || "Arms delivery";
    }
    return TYPES[e.type] || "Event";
  };
  const wholeCountry = (o, cc) => {
    const p = String((o && o.place) || "").trim().toLowerCase().replace(/^the\s+/, "");
    return !!cc && (!p || p === cc.toLowerCase() || p === countryName(cc).toLowerCase());
  };
  const originsOf = (e) => (e.origins && e.origins.length ? e.origins : e.origin ? [e.origin] : []);
  const bestStatus = (list) => list.reduce((b, e) => (STATUS[e.status].rank > STATUS[b].rank ? e.status : b), "claimed");
  const metaLine = (e) => (e.wave ? `${countryName(e.attacker)} → ${countryName(e.country)}`
    : e.alert ? countryName(e.country) || e.place || ""
    : e.type === "arms_transfer" && e.transfer ? `${transferFrom(e.transfer)} → ${transferTo(e.transfer)}` : e.place || "");
  const flowBadge = (f) => (f.money ? iconBadge("coin", "aid", STATUS[f.status].conf) : iconBadge("crate", "supply", STATUS[f.status].conf));
  // A country moving its own forces (supplier = recipient) is labeled by places, not "United States →
  // United States": the region or base it left and the one it went to.
  const own = (t) => t.supplier && t.supplier === t.recipient;
  const transferFrom = (t) => (t.from && (t.from.region || own(t)) && t.from.place ? t.from.place : countryName(t.supplier));
  const transferTo = (t) => (t.to && (t.to.region || own(t)) && t.to.place ? t.to.place : countryName(t.recipient));
  const flowFrom = (f) => f.fromLabel || (f.own && f.from && f.from.place) || countryName(f.supplier);
  const flowTo = (f) => f.toLabel || (f.own && f.to && f.to.place) || countryName(f.recipient);
  // A marker-style icon for lists and the legend, matching the globe.
  const iconBadge = (icon, cat, conf = "solid", extra = "") => `<span class="ico cat-${cat} conf-${conf} ${extra}" aria-hidden="true">${svgIcon(icon)}</span>`;
  const eventIcon = (e) => { const [cat, icon] = catOf(e); return iconBadge(icon, cat, STATUS[e.status].conf); };
  let lastSeen = 0;
  try { lastSeen = Number(localStorage.getItem("gsm_lastSeen")) || 0; } catch (_) { /* storage blocked */ }
  // Events you have opened stop pulsing and lose the "new" mark (remembered in this browser for a week).
  let viewed = {};
  try { viewed = JSON.parse(localStorage.getItem("gsm_viewed") || "{}") || {}; } catch (_) { viewed = {}; }
  function markViewed(id) {
    viewed[id] = Date.now();
    const cutoff = Date.now() - 7 * DAY;
    for (const k of Object.keys(viewed)) if (!(viewed[k] > cutoff)) delete viewed[k];
    try { localStorage.setItem("gsm_viewed", JSON.stringify(viewed)); } catch (_) { /* storage blocked */ }
  }
  const isNew = (e) => !e._archived && !viewed[e.id] && !e.possibly_old && (e._t > Date.now() - HOUR || (lastSeen && e._t > lastSeen && e._t > Date.now() - DAY));
  const isLive = (e) => !e._archived && !viewed[e.id] && (Date.now() - e._t < LIVE_MS || isNew(e));

  // ------------------------------------------------------------------ state
  const S = {
    data: null,
    theaters: FALLBACK_THEATERS,
    windowH: 24,
    region: "world",  // worldwide, one theater id, or null (all regions hidden)
    statusOn: new Set(Object.keys(STATUS)),
    layers: { paths: true, supply: true, carriers: true },
    off: new Set(),  // "On the map" entries switched off
    query: "",
    feedLimit: 250,
    briefOpen: new Set(),
    selectedId: null,
    arrived: null,      // ids of events that just arrived (they slide into the feed once)
    selectedHull: null,
    selectedFlow: null,
    hot: new Set(),
    active: new Set(),
    activeKey: "",
    html: [],
    fleet: [],
    fleetMeta: {},
    supply: { flows: [], pledges: [] },
    firstLoad: true,
    lastFocus: null,
    sheet: 0,
  };

  const reportFiles = new Map();  // reports files by bucket, for the loaded copy of the data (reportsFor)
  const shownReports = new Map();  // the reports last shown per opened event, kept through a data update

  // ------------------------------------------------------------------ globe
  const globeEl = $("#globe");
  const world = new Globe(globeEl, { animateIn: !reduceMotion });
  world
    .backgroundColor("rgba(0,0,0,0)")
    .showGraticules(false)
    .showAtmosphere(true)
    .atmosphereColor("#2f8fe0")
    .atmosphereAltitude(0.16)
    .pointOfView({ lat: 18, lng: 10, altitude: 3.1 });
  const mat = world.globeMaterial();
  mat.color.set("#05090f");
  if (mat.emissive) mat.emissive.set("#04080d");
  mat.shininess = 5;
  const controls = world.controls();
  controls.autoRotate = false;
  controls.minDistance = 106; // globe radius is 100: close to city scale (the painted land is coarse this close)
  controls.maxDistance = 650;
  // Bound WebGL pixel cost on high-density displays. Moving scenes use one device pixel
  // per CSS pixel; static scenes restore detail. HTML text and symbols stay native resolution.
  const fullRatio = () => Math.min(PHONE ? 1.25 : 1.5, window.devicePixelRatio || 1);
  const setRatio = (r) => { const rd = world.renderer(); if (Math.abs(rd.getPixelRatio() - r) > 0.01) rd.setPixelRatio(r); };
  const updateRenderRatio = () => setRatio((moving || controls.autoRotate) ? Math.min(1, fullRatio()) : fullRatio());
  setRatio(fullRatio());
  // Hover testing: 20 times a second, even with the pointer still, the library tests the pointer
  // against every object on the globe (and toGlobeCoords does on every pointer move), hidden ones
  // included. The globe itself, a sphere of 8,000 triangles, was tested triangle by triangle: it
  // gets an exact sphere test instead. Lines (country borders, control outlines, rings, the hidden
  // grid), the atmosphere and the library's hidden map-tile sphere (32,000 triangles) never answer:
  // nothing on the page listens for them. Runs after renders until the borders exist.
  const noHit = () => {};
  let linesQuiet = false, hoverLight = false;
  function lightenHover() {
    if (hoverLight) return;
    const R = world.getGlobeRadius();
    let globeDone = false;
    world.scene().traverse((o) => {
      if (o.isLine && !linesQuiet) { Object.getPrototypeOf(o).raycast = noHit; linesQuiet = o.type === "Line"; }
      if (!o.isMesh) return;
      const inGlobe = o.parent && o.parent.__globeObjType === "globe";
      if (o._light) { globeDone = globeDone || inGlobe; return; }
      let shown = true;
      for (let x = o; x; x = x.parent) if (!x.visible) shown = false;
      if (inGlobe && shown) {
        o._light = true;
        globeDone = true;
        o.raycast = function (raycaster, hits) {
          const { origin: p, direction: d } = raycaster.ray;  // direction has length 1
          const b = p.x * d.x + p.y * d.y + p.z * d.z, c = p.x * p.x + p.y * p.y + p.z * p.z - R * R, disc = b * b - c;
          if (disc < 0) return;
          const t = -b - Math.sqrt(disc);
          if (t < raycaster.near || t > raycaster.far) return;
          hits.push({ distance: t, point: p.clone().addScaledVector(d, t), object: this });
        };
      } else if ((o.material && o.material.side === 1) || !shown) {  // the atmosphere (drawn on its inner side); hidden tiles
        o._light = true;
        o.raycast = noHit;
      }
    });
    hoverLight = linesQuiet && globeDone;
  }
  // On touch screens nothing hovers, but the library still tests the last touch point against every
  // marker and line about 20 times a second, long after the finger lifted. There, dots and lines are
  // tested only while a finger is down and just after (when a tap is answered).
  let tapOpen = 0, touchQuiet = false;
  function quietTouchHover() {
    if (canHover() || touchQuiet) return;
    let Mesh = null;
    world.scene().traverse((o) => { if (!Mesh && o.isMesh && o.type === "Mesh") Mesh = Object.getPrototypeOf(o); });
    if (!Mesh || !Mesh.raycast) return;   // the scene isn't built yet: tried again after the next render
    touchQuiet = true;
    const raycast = Mesh.raycast;
    Mesh.raycast = function (raycaster, hits) {
      if (canHover() || performance.now() < tapOpen) return raycast.call(this, raycaster, hits);
    };
  }
  globeEl.addEventListener("pointerdown", () => { tapOpen = Infinity; }, true);
  globeEl.addEventListener("pointerup", () => { tapOpen = performance.now() + 700; }, true);
  globeEl.addEventListener("pointercancel", () => { tapOpen = performance.now() + 700; }, true);
  setTimeout(quietTouchHover, 0);
  setTimeout(lightenHover, 0);

  // While the globe is being dragged or pinched, markers stop sliding into place and heavier
  // updates wait until the gesture ends (the camera keeps easing briefly after release).
  let moving = false, settleTimer = null;
  controls.addEventListener("start", () => {
    if (typeof hideTip === "function") { hideTip(); closeFly(); }
    clearTimeout(settleTimer);
    moving = true;
    document.body.classList.add("moving");
    updateRenderRatio();
  });
  controls.addEventListener("end", () => {
    clearTimeout(settleTimer);
    settleTimer = setTimeout(() => {
      moving = false;
      document.body.classList.remove("moving");
      updateRenderRatio();
      applyZoomScale();
      queueDeclutter();
    }, 350);
  });

  // Idle rotation is driven by manual start/end events, never by camera change events.
  function setupIdleRotation(orbit, button) {
    let timer = null, interacting = false, enabled = !reduceMotion;
    orbit.autoRotateSpeed = 0.18;
    function stop() {
      clearTimeout(timer);
      const wasRotating = orbit.autoRotate;
      orbit.autoRotate = false;
      if (wasRotating) updateRenderRatio();
      button.dataset.rotating = "false";
    }
    function arm() {
      stop();
      if (!enabled || interacting || document.hidden) return;
      timer = setTimeout(() => {
        orbit.autoRotate = true;
        updateRenderRatio();
        button.dataset.rotating = "true";
      }, 30000);
    }
    function label() {
      button.setAttribute("aria-pressed", String(enabled));
      button.setAttribute("aria-label", enabled ? "Pause automatic rotation" : "Enable automatic rotation");
      button.title = enabled ? "Automatic rotation after 30 seconds idle" : "Automatic rotation paused";
    }
    orbit.addEventListener("start", () => { interacting = true; stop(); });
    orbit.addEventListener("end", () => { interacting = false; arm(); });
    document.addEventListener("visibilitychange", arm);
    document.addEventListener("pointerdown", arm);
    document.addEventListener("keydown", arm);
    button.addEventListener("click", () => { enabled = !enabled; label(); arm(); });
    label();
    arm();
  }

  // ------------------------------------------------------------------ land, borders, country centers
  const centers = new Map();
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
  const countryCenter = (iso2) => REP_POINT[iso2] || centers.get(ISO_NUM.get(iso2)) || null;
  const landColor = (f) => (S.active.has(f.id) ? "#1f3448" : S.hot.has(f.id) ? "#152536" : "#0e1620");
  const OCEAN = "#05090f";
  // Territorial control (see controlNote): the source's shapes, painted onto the globe with the land.
  const CONTROL_FILL = { occupied: "rgba(214,174,110,0.55)", advance: "rgba(245,165,36,0.9)" };
  const CONTROL_LINE = "rgba(236,212,160,0.75)";
  // The site's own front-line areas (pipeline/frontline/) carry their holder's colour.
  const hexRgb = (h) => [1, 3, 5].map((i) => parseInt(String(h || "#999999").slice(i, i + 2), 16));
  const AREA_ALPHA = { occupied: 0.5, claimed: 0.22 };
  const pathColorOf = (p) => (p.control ? (p.color ? rgba(hexRgb(p.color), p.faint ? 0.45 : 0.85) : CONTROL_LINE) : borderColor(p.fid));
  const BORDER_RGBA = { active: [255, 120, 82, 0.9], hot: [120, 190, 245, 0.45], base: [120, 180, 235, 0.22] };
  const borderRgba = (id) => (S.active.has(id) ? BORDER_RGBA.active : S.hot.has(id) ? BORDER_RGBA.hot : BORDER_RGBA.base);
  const borderColor = (id) => { const c = borderRgba(id); return `rgba(${c[0]},${c[1]},${c[2]},${c[3]})`; };

  // Country borders: one object holding every border segment, drawn in a single go, instead of one
  // line per ring (290 lines: 290 draws every frame, and all 290 redone on every filter change).
  // Colors are per point, as the library's own lines have them; when the active countries change,
  // only the color list is rewritten. Made from the library's own building blocks (it doesn't
  // export them); if one can't be found, the borders stay one line per ring, as before.
  let borderLines = null;
  function makeBorderLines(land) {
    let grid = null, globeMesh = null, shaderMat = null;
    world.scene().traverse((o) => {
      if (!grid && o.type === "LineSegments" && o.material) grid = o;
      if (!globeMesh && o.isMesh && o.visible && o.parent && o.parent.__globeObjType === "globe" && o.geometry && o.geometry.getAttribute && o.geometry.getAttribute("position")) globeMesh = o;
      if (!shaderMat && o.isMesh && o.material && o.material.type === "ShaderMaterial") shaderMat = o.material;
    });
    if (!grid || !globeMesh || !shaderMat) return null;
    const Geometry = Object.getPrototypeOf(globeMesh.geometry.constructor), Attr = globeMesh.geometry.getAttribute("position").constructor;
    if (typeof Geometry !== "function" || !Geometry.prototype || typeof Geometry.prototype.setAttribute !== "function") return null;
    const pos = [], ranges = [];
    const add = (p) => { const c = world.getCoords(p.lat, p.lon, 0.0045); pos.push(c.x, c.y, c.z); };
    for (const f of land) {
      const from = pos.length / 3;
      for (const poly of f.geometry.coordinates) for (const ring of poly) for (let i = 0; i + 1 < ring.length; i++) {
        const a = { lat: ring[i][1], lon: ring[i][0] }, b = { lat: ring[i + 1][1], lon: ring[i + 1][0] };
        const n = Math.max(1, Math.ceil(km(a.lat, a.lon, b.lat, b.lon) / 110));  // about a degree a piece, so long sides follow the curve
        let prev = a;
        for (let k = 1; k <= n; k++) { const q = k === n ? b : slerp(a, b, k / n); add(prev); add(q); prev = q; }
      }
      ranges.push([f.id, from, pos.length / 3]);
    }
    const geom = new Geometry();
    geom.setAttribute("position", new Attr(new Float32Array(pos), 3));
    geom.setAttribute("color", new Attr(new Float32Array((pos.length / 3) * 4), 4));
    const colors = geom.getAttribute("color").array;  // the attribute keeps its own copy
    // the same shader as the library's lines (colors as given, no dashes)
    const material = new shaderMat.constructor({
      transparent: true,
      vertexShader: "#include <common>\n#include <logdepthbuf_pars_vertex>\nattribute vec4 color;\nvarying vec4 vColor;\nvoid main() {\n  vColor = color;\n  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);\n  #include <logdepthbuf_vertex>\n}",
      fragmentShader: "#include <logdepthbuf_pars_fragment>\nvarying vec4 vColor;\nvoid main() {\n  gl_FragColor = vColor;\n  #include <logdepthbuf_fragment>\n}",
    });
    const obj = new grid.constructor(geom, material);
    obj.raycast = noHit;
    world.scene().add(obj);
    return { obj, ranges, colors, attr: geom.getAttribute("color") };
  }
  function colorBorders() {
    if (!borderLines) return;
    const { ranges, colors, attr } = borderLines;
    for (const [fid, from, to] of ranges) {
      const c = borderRgba(fid);
      for (let v = from; v < to; v++) { colors[v * 4] = c[0] / 255; colors[v * 4 + 1] = c[1] / 255; colors[v * 4 + 2] = c[2] / 255; colors[v * 4 + 3] = c[3]; }
    }
    attr.needsUpdate = true;
  }

  const landReq = window.__land || fetch("assets/countries-110m.json");  // started early in index.html
  window.__land = null;
  landReq
    .then((r) => r.json())
    .then((topo) => {
      const land = topojson.feature(topo, topo.objects.countries).features
        .filter((f) => f.properties.name !== "Antarctica").map(sanitize).filter(Boolean);
      land.forEach((f) => { if (f.id) centers.set(f.id, centerOf(f)); });
      landShapes = land;
      // the line settings first: paintLand hands the lines over (borders, if not merged, and the
      // control outlines), and they were lost when the event data had come in first
      world
        .pathPoints("pts").pathPointLat((p) => p[1]).pathPointLng((p) => p[0]).pathPointAlt(0.0045)
        .pathTransitionDuration(0).pathColor(pathColorOf)
        // traced (approximate) areas get a dashed edge; dash sizes are fractions of the ring's length
        .pathDashLength((p) => (p.approx ? 0.006 : 1)).pathDashGap((p) => (p.approx ? 0.004 : 0))
        // front lines (the dashed edges of held ground) flow slowly along their length
        .pathDashAnimateTime((p) => (p.control && p.approx && !reduceMotion ? 90000 : 0));
      paintLand();
      if (S.data) render();
      // the borders, once the library has built its scene (a frame or two after it starts); if it
      // never does, one line per ring as before
      let tries = 0;
      const setupBorders = () => {
        try { borderLines = makeBorderLines(land); } catch (err) { console.error(err); borderLines = null; tries = 1e9; }
        if (borderLines) { colorBorders(); return; }
        if (++tries < 120) { requestAnimationFrame(setupBorders); return; }
        for (const f of land) for (const poly of f.geometry.coordinates) for (const ring of poly) borderPaths.push({ fid: f.id, pts: ring });
        world.pathsData([...borderPaths, ...controlOutlines]);
      };
      setupBorders();
    })
    .catch((err) => console.error(err));

  // Land is painted onto the globe's own surface (an ocean-and-countries picture), not drawn as a
  // separate layer floating just above it: phone graphics chips can't tell two surfaces that close
  // apart, and the ocean showed through the land in dark streaks while moving.
  let landShapes = [], landUrl = null, borderPaths = [], controlOutlines = [], hatch = null;
  // always shown, not a filter: published control maps, then the site's own front-line areas
  const CONTROL_THEATER = { ukraine: "ukraine", yemen: "mideast", israel: "mideast",
    sudan: "horn", ethiopia: "horn", somalia: "horn", drc: "drc_sahel", sahel: "drc_sahel", myanmar: "indopac" };
  function controlInRegion(layer) {
    if (S.region === null) return false;
    const listed = S.theaters.filter((t) => t.listed !== false);
    const theaterId = CONTROL_THEATER[layer.conflict];
    const countryId = ISO_NUM.get(layer.country) || layer.country;
    return listed.some((t) => (S.region === "world" || S.region === t.id) &&
      (theaterId ? t.id === theaterId : countryId && (t.highlight || []).includes(countryId)));
  }
  const controlLayers = () => [...((S.data && S.data.control) || []), ...((S.data && S.data.frontline && S.data.frontline.areas) || [])].filter(controlInRegion);
  // Infiltration (forces present, not in control) is hatched rather than filled.
  function hatchPattern(g) {
    if (hatch) return hatch;
    const c = document.createElement("canvas");
    c.width = c.height = 4;
    const h = c.getContext("2d");
    h.fillStyle = "rgba(214,174,110,0.25)";
    h.fillRect(0, 0, 4, 4);
    h.fillStyle = "rgba(236,212,160,0.9)";
    for (let i = 0; i < 4; i++) h.fillRect(i, 3 - i, 1, 1);
    return (hatch = g.createPattern(c, "repeat"));
  }
  const OUTLINE_MIN_KM2 = 500;
  function ringKm2(ring) {   // planar area of a (lon, lat) ring, good enough to tell patches from fronts
    let a = 0;
    const k = 111.32 * Math.cos((ring[0][1] * Math.PI) / 180);
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) a += (ring[j][0] * k) * (ring[i][1] * 110.57) - (ring[i][0] * k) * (ring[j][1] * 110.57);
    return Math.abs(a) / 2;
  }
  function paintControl(g, X, Y, outlinesToo = true) {
    // one path per style and colour, filled "nonzero", so overlapping layers of one kind don't double up
    const byStyle = new Map();
    for (const L of controlLayers()) {
      const k = `${L.style}|${L.style === "infiltration" ? "" : L.color || ""}`;
      (byStyle.get(k) || byStyle.set(k, []).get(k)).push(L);
    }
    const order = { claimed: 0, occupied: 1, infiltration: 2, advance: 3 };
    for (const k of [...byStyle.keys()].sort((a, b) => order[a.split("|")[0]] - order[b.split("|")[0]])) {
      const layers = byStyle.get(k);
      const [style, color] = k.split("|");
      g.fillStyle = style === "infiltration" ? hatchPattern(g) : color ? rgba(hexRgb(color), AREA_ALPHA[style] || 0.5) : CONTROL_FILL[style];
      g.beginPath();
      for (const L of layers) for (const poly of L.polygons) for (const ring of poly) {
        ring.forEach(([lon, lat], i) => (i ? g.lineTo(X(lon), Y(lat)) : g.moveTo(X(lon), Y(lat))));
        g.closePath();
      }
      g.fill("nonzero");
    }
    if (!outlinesToo) return;
    // crisp outlines of held ground, drawn as lines like the borders. Each ring is a line the globe
    // redraws every frame (its dashes flow). The site's own areas are one broad shape per side
    // (2026-10-05: about 40 rings in all, down from 319 patches around single towns); a ring under
    // OUTLINE_MIN_KM2 (a lone confirmed capture) keeps its fill without an outline.
    const outlines = controlOutlines = [];
    for (const L of controlLayers()) {
      if (L.style !== "occupied" && !(L.assessment && L.style === "claimed")) continue;
      for (const poly of L.polygons) for (const ring of poly) {
        if (L.assessment && ringKm2(ring) < OUTLINE_MIN_KM2) continue;
        outlines.push({ control: true, approx: !!(L.approx || L.assessment), color: L.color, faint: L.style === "claimed", pts: ring });
      }
    }
    world.pathsData([...borderPaths, ...outlines]);
  }
  const landCanvas = document.createElement("canvas");
  landCanvas.width = PHONE ? 2048 : 4096;
  landCanvas.height = landCanvas.width / 2;
  // A country's outline, unwrapped once so it stays continuous across the 180° line, added to the
  // current path three times: in place and one turn left and right.
  function countryPath(g, f, X, Y) {
    if (!f._rings) {
      f._rings = [];
      for (const poly of f.geometry.coordinates) for (const ring of poly) {
        let prev = ring[0][0];
        f._rings.push(ring.map(([lon, lat]) => {
          while (lon - prev > 180) lon -= 360;
          while (lon - prev < -180) lon += 360;
          prev = lon;
          return [lon, lat];
        }));
      }
    }
    for (const pts of f._rings) for (const shift of [-360, 0, 360]) {
      pts.forEach(([lon, lat], i) => (i ? g.lineTo(X(lon + shift), Y(lat)) : g.moveTo(X(lon + shift), Y(lat))));
      g.closePath();
    }
  }
  // The whole picture is painted when the control areas or the theaters' countries change (a data
  // update at most). A filter or time-window change only changes which countries are lit as
  // active: then just those countries are repainted (with the control areas over them), not the
  // whole 4096 x 2048 picture, which made every filter click lag.
  let paintedBase = "";
  const paintedColor = new Map();
  function paintLand() {
    if (!landShapes.length) return;
    const base = [...S.hot].sort().join(",") + "|" + controlLayers().map((l) => l.id + l.as_of + (l.settlements || "")).join(",");
    const W = landCanvas.width, H = landCanvas.height, g = landCanvas.getContext("2d");
    const X = (lon) => ((lon + 180) / 360) * W, Y = (lat) => ((90 - lat) / 180) * H;
    if (base === paintedBase) {
      const changed = landShapes.filter((f) => paintedColor.get(f) !== landColor(f));
      if (!changed.length) return;
      g.save();
      g.beginPath();
      changed.forEach((f) => countryPath(g, f, X, Y));
      g.clip("evenodd");
      for (const f of changed) {
        g.fillStyle = landColor(f);
        g.beginPath();
        countryPath(g, f, X, Y);
        g.fill("evenodd");
        paintedColor.set(f, landColor(f));
      }
      paintControl(g, X, Y, false);
      g.restore();
    } else {
      paintedBase = base;
      g.fillStyle = OCEAN;
      g.fillRect(0, 0, W, H);
      // a fine 10° grid on the ocean, painted into the same picture (no extra layer)
      g.strokeStyle = "rgba(70, 130, 190, 0.16)";
      g.lineWidth = W / 4096;
      g.beginPath();
      for (let lon = -180; lon <= 180; lon += 10) { g.moveTo(X(lon), 0); g.lineTo(X(lon), H); }
      for (let lat = -80; lat <= 80; lat += 10) { g.moveTo(0, Y(lat)); g.lineTo(W, Y(lat)); }
      g.stroke();
      for (const f of landShapes) {
        g.fillStyle = landColor(f);
        g.beginPath();
        countryPath(g, f, X, Y);
        g.fill("evenodd");
        paintedColor.set(f, landColor(f));
      }
      paintControl(g, X, Y);
    }
    if (mat.map) { mat.map.image = landCanvas; mat.map.needsUpdate = true; return; }  // later repaints: no reload
    landCanvas.toBlob((blob) => {
      if (!blob) return;
      if (landUrl) URL.revokeObjectURL(landUrl);
      landUrl = URL.createObjectURL(blob);
      world.globeImageUrl(landUrl);  // the library drops the globe's own tint once the picture loads
    });
  }

  // ------------------------------------------------------------------ 3D layers: wave target dots, impact rings, lines
  let zoomK = 1.6;
  // Line widths are in globe units, so zoomed in close they would turn into wide bands: below
  // about altitude 1 they narrow with the zoom, like the dots.
  const arcStroke = (a) => (a.stroke == null ? null : a.stroke * Math.min(1, zoomK / 0.9));
  const flDay = (iso) => (iso ? new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", timeZone: "UTC" }) : "");
  world
    .pointLat("lat").pointLng("lon")
    .pointAltitude(0.005)
    .pointRadius((d) => (d.alert ? 0.09 : 0.13) * zoomK)
    .pointColor((d) => rgba(CAT_RGB.strike, (d.alert ? 0.5 : STATUS[d.ref.status].alpha) * dimOf(d.ref.id === S.selectedId, true)))
    .pointResolution(8)
    .pointLabel((d) => `<div class="tip"><div class="tip-meta"><b>${esc(d.place || "Location")}</b><span>${d.alert ? "named in an alert" : "part of an attack wave"}</span></div><div class="tip-sum">${esc(d.ref.summary)}</div></div>`)
    .onPointHover((d) => { globeEl.style.cursor = d ? "pointer" : ""; })
    .onPointClick((d) => select(d.ref.id, true));
  world
    .ringLat("lat").ringLng("lon")
    .ringColor((r) => (t) => rgba(r.rgb, Math.max(0, 1 - t) * r.alpha))
    .ringMaxRadius((r) => r.max * zoomK)
    .ringPropagationSpeed((r) => (r.once ? r.speed * zoomK : r.speed))  // a one-off ring lasts the same time at any zoom
    .ringRepeatPeriod((r) => r.period)
    .ringAltitude(0.006);
  const ARC = {
    strike: { dash: 0.5, gap: 0.18 }, strikeApprox: { dash: 0.34, gap: 0.28 },
    barrage: { dash: 0.035, gap: 0.05 },  // a massive barrage: a stream of small dashes from each usual launch area
    flow: { dash: 0.14, gap: 0.35 }, flowDashed: { dash: 0.09, gap: 0.4 },
    routeShot: { dash: 0.14, gap: 4 },  // one short pulse when a reported route is opened
    track: { dash: 0.06, gap: 0.04 }, plan: { dash: 0.2, gap: 0.14 }, hit: { dash: 1, gap: 0 },
    shot: { dash: 0.14, gap: 4 },  // one bright dash that runs a launch line once (playLaunches)
  };
  // Routes and carrier lines are thin, so hovering meant being exactly on them: each gets an
  // invisible, wider twin that answers hover and taps for it.
  const hitArcs = (arcs) => arcs.filter((a) => (a.flow || a.carrier) && a.kind !== "routeShot")
    .map((a) => keyed({ ...a, kind: "hit", color: "rgba(0,0,0,0)", stroke: Math.max(2.8, a.stroke * 6), ms: 0, seed: 0 }, a._k && `hit|${a._k}`));
  world
    .arcStartLat("sLat").arcStartLng("sLng").arcEndLat("eLat").arcEndLng("eLng")
    .arcColor((a) => a.color).arcStroke(arcStroke)
    .arcDashLength((a) => ARC[a.kind].dash).arcDashGap((a) => ARC[a.kind].gap)
    .arcDashInitialGap((a) => ((a.kind === "shot" || a.kind === "routeShot") ? 1 : a.seed))
    .arcDashAnimateTime((a) => (reduceMotion ? 0 : a.ms || 0))
    .arcAltitude((a) => (a.alt === undefined ? null : a.alt))
    .arcAltitudeAutoScale(0.36)
    .arcLabel((a) => (a.carrier ? tipCarrier(a.carrier) : a.flow ? tipFlow(a.flow) : a.ref ? tipEvent(a.ref) : ""))
    .onArcHover((a) => { globeEl.style.cursor = a ? "pointer" : ""; })
    .onArcClick((a) => { if (a.carrier) selectCarrier(a.carrier.hull, true); else if (a.flow) selectFlow(a.flow.key); else if (a.ref) select(a.ref.id, true); })
    // a click on bare globe (no marker, line or dot) ends the focus on an opened event, carrier or route
    .onGlobeClick(() => { if (focused() && performance.now() - lineTapAt > 600) closeDetail(); });

  // Dot and ring sizes follow the zoom, but are only rebuilt once a gesture ends.
  function applyZoomScale() {
    const k = clamp(world.pointOfView().altitude, 0.12, 2.6) / 1.1;
    if (Math.abs(k - zoomK) / zoomK > 0.12) {
      zoomK = k;
      world.pointRadius((d) => (d.alert ? 0.09 : 0.13) * zoomK);
      world.ringMaxRadius((r) => r.max * zoomK);
      world.arcStroke(arcStroke);
    }
  }
  world.onZoom(() => {
    if (!moving) applyZoomScale();
    queueDeclutter();
  });

  // Lines, dots and rings keep their identity from one render to the next (same key, same object),
  // so ones already drawn don't rise from the ground again and rings don't restart; new ones still
  // rise in.
  const stableSets = { arcs: {}, dots: {}, rings: {} };
  Object.values(stableSets).forEach((st) => { st.prev = new Map(); st.next = new Map(); });
  function stable(kind, key, obj) {
    const st = stableSets[kind];
    if (st.next.has(key)) return obj;  // the same key twice in one render: a new object
    const old = st.prev.get(key);
    if (old) {
      if ("seed" in old) obj.seed = old.seed;
      if ("period" in old) obj.period = old.period;
      Object.assign(old, obj);
    }
    st.next.set(key, old || obj);
    return old || obj;
  }
  const keyed = (obj, key) => (key ? stable("arcs", key, obj) : obj);
  const newFrame = () => Object.values(stableSets).forEach((st) => { st.prev = st.next; st.next = new Map(); });
  // Short-lived extras drawn on top of what render() builds: arrival and impact rings, the ring
  // under a hovered feed item, and the dashes that run a wave's launch lines once.
  let baseRings = [], baseArcs = [];
  const extraRings = new Set(), extraArcs = new Set();
  const pushRings = () => world.ringsData(extraRings.size ? [...baseRings, ...extraRings] : baseRings);
  const pushArcs = () => world.arcsData(extraArcs.size ? [...baseArcs, ...extraArcs] : baseArcs);
  // One ring that spreads once from a spot (a new event landing, a launch line reaching its target).
  function flashRing(lat, lon, rgb, max = 4.5, ms = 1800) {
    if (reduceMotion) return;
    const r = { lat, lon, rgb, alpha: 0.95, max, speed: max / (ms / 1000), period: 0, once: true };
    extraRings.add(r);
    pushRings();
    setTimeout(() => { extraRings.delete(r); pushRings(); }, ms + 200);
  }

  // ------------------------------------------------------------------ HTML markers
  // Each marker is an outer anchor (positioned by the globe) holding an inner button that can be
  // nudged sideways when markers overlap, with a thin line back to the true location.
  world
    .htmlLat("lat").htmlLng("lon")
    .htmlAltitude((d) => d.hAlt || 0.012)
    .htmlElement((d) => d.el)
    .htmlElementVisibilityModifier((el, visible) => {
      // called for every marker whenever the camera moves: touch the page only when it changes
      if (el._vis === visible) return;
      el._vis = visible;
      el.style.opacity = visible ? "1" : "0";
      el.style.pointerEvents = visible ? "auto" : "none";
      el.dataset.visible = visible ? "1" : "0";
    });
  const elCache = new Map();
  function markerEl(key) {
    let el = elCache.get(key);
    if (!el) {
      el = document.createElement("div");
      el.className = "mk";
      el.innerHTML = '<button type="button" class="mk-in"></button>';
      el.firstChild.addEventListener("pointerdown", (ev) => ev.stopPropagation());
      elCache.set(key, el);
    }
    return el;
  }

  // The bigger an incident's reported numbers (killed, injured, drones or missiles launched), the
  // bigger its marker, on top of the size its severity gives: +4px at 10, +8px at 100, +12px at 1,000.
  const SIZE_PX = { sm: 20, md: 24, lg: 31 };
  const magnitude = (e) => (e.killed || 0) + 0.5 * (e.injured || 0) + 0.25 * (e.launched || 0);
  const markerPx = (e, size) => Math.min(PHONE ? 40 : 46, SIZE_PX[size] + 4 * Math.log10(1 + magnitude(e)));

  // Marker classes set by the layout (declutter), kept when a render rewrites the others.
  const layoutClasses = (el) => ["spread", "cluster-lead", "clustered"].filter((c) => el.classList.contains(c)).map((c) => ` ${c}`).join("");
  // The same data object for a marker from one render to the next: the globe library binds its
  // marker to that object, so a new object each render made it take every marker off the page and
  // put it back (restarting their animations) on every filter change.
  const markerData = new Map();
  const markerDatum = (key, fields) => {
    const d = markerData.get(key) || {};
    markerData.set(key, Object.assign(d, fields));
    return d;
  };
  const setHtml = (el, html) => { if (el._html !== html) { el._html = html; el.firstChild.innerHTML = html; } };

  function eventMarker(e, labelIt, animate) {
    const [cat, icon, fx] = catOf(e);
    const el = markerEl(`ev:${e.id}`);
    const live = animate && !reduceMotion && !e.possibly_old;
    const size = e.alert ? "md" : e.severity >= 3 ? "lg" : e.severity === 2 ? "md" : "sm";
    el.style.setProperty("--s", `${markerPx(e, size).toFixed(1)}px`);
    el._baseLabel = `${typeLabel(e)}, ${metaLine(e)}. ${STATUS[e.status].label}.`;
    el.className = `mk cat-${cat} conf-${STATUS[e.status].conf} size-${size}${live && fx ? ` fx-${fx}` : ""}${e.id === S.selectedId ? " is-selected" : ""}${e.id === hotId ? " is-hot" : ""}${layoutClasses(el)}`;
    el.style.setProperty("--fade", String(fade(e)));
    const label = labelIt ? (e.alert ? alertsText(e) : e.wave ? (e.launched ? `${e.launched} launched` : `${e.targets.length} places hit`) : e.place || "") : "";
    const btn = el.firstChild;
    // the count bubble's number (set by declutter) is kept unless the marker itself changed
    setHtml(el, `${svgIcon(icon)}${label ? `<span class="mk-label">${esc(label)}</span>` : ""}<span class="mk-count" aria-hidden="true"></span>`);
    btn.setAttribute("aria-label", `${typeLabel(e)}, ${metaLine(e)}. ${STATUS[e.status].label}.`);
    const open = () => {
      if (el.classList.contains("cluster-lead")) { closeFly(); showFly(el, tipCluster(el._members, e), true); fly.classList.add("is-pinned"); }
      else select(e.id, true);
    };
    // pointing at a marker slides a card open beside it (a count bubble's lists every event it holds)
    // and lights its row in the feed
    const peek = () => {
      if (fly.classList.contains("is-pinned")) return;
      const members = el.classList.contains("cluster-lead") && el._members;
      if (members) showFly(el, tipCluster(members, e), true);
      else showFly(el, tipEvent(e, true), placesOf(e).length > 1, open);
      if (!members) prefetchReports(e.id);
    };
    btn.onclick = (ev) => {
      ev.stopPropagation();
      if (el.classList.contains("cluster-lead")) { open(); return; }
      if (!canHover() && !(flyFor === el && !fly.hidden)) { peek(); return; }  // first tap on a phone: the card
      closeFly();
      open();
    };
    btn.onmouseenter = () => { if (!canHover()) return; peek(); hotRow(e.id); };
    btn.onmouseleave = () => { if (!canHover()) return; hideFly(); hotRow(null); };
    return markerDatum(`ev:${e.id}`, { key: `ev:${e.id}`, ev: e, el, lat: e.lat, lon: e.lon, hAlt: 0.014, isEvent: true, prio: e.severity * 10 + (isNew(e) ? 5 : 0) + Math.log10(1 + magnitude(e)) + (e._t / 1e13) });
  }

  function carrierMarker(c) {
    const el = markerEl(`cvn:${c.hull}`);
    const tone = c.at_home ? "port" : c.status === "departed" || c.status === "underway" ? "underway" : "deployed";
    el.className = `mk mk-cvn cvn-${tone}${c.hull === S.selectedHull ? " is-selected" : ""}${layoutClasses(el)}`;
    const btn = el.firstChild;
    setHtml(el, `${svgIcon("carrier")}<span class="mk-label">${esc(c.short || c.hull)}</span>`);
    btn.setAttribute("aria-label", `${c.name}, ${carrierStatus(c)}${c.place ? ", " + c.place : ""}`);
    const open = () => selectCarrier(c.hull, true);
    btn.onclick = (ev) => {
      ev.stopPropagation();
      if (!canHover() && !(flyFor === el && !fly.hidden)) { showFly(el, tipCarrier(c, true), false, open); return; }
      closeFly();
      open();
    };
    btn.onmouseenter = () => { if (canHover()) showFly(el, tipCarrier(c)); };
    btn.onmouseleave = () => { if (canHover()) hideFly(); };
    c.el = el; c.key = `cvn:${c.hull}`; c.hAlt = 0.012; c.prio = 1;
    return c;
  }

  // Simple tooltip for HTML markers (3D layers use globe.gl's own).
  const tipBox = document.createElement("div");
  tipBox.className = "html-tip";
  tipBox.hidden = true;
  document.body.appendChild(tipBox);
  const setTip = (html) => { if (tipBox._html !== html || tipBox.hidden) { tipBox.innerHTML = html; tipBox._html = html; } tipBox.hidden = false; };
  function hideTip() { tipBox.hidden = true; }

  // Pointing at a marker (desktop) slides a card open from its side, toward the open map (away
  // from the list). Moving to a neighbouring marker glides the open card over instead of opening a
  // new one. A count bubble's card lists every event it holds, and an alert group's or attack
  // wave's the places it names; those cards can be pointed at (a place rings on the globe) and
  // clicked, so they stay open while the pointer crosses into them.
  const fly = document.createElement("div");
  fly.className = "mk-fly";
  fly.hidden = true;
  document.body.appendChild(fly);
  let flyFor = null, flyTimer = 0, flyRing = null;
  // `act` opens what the card shows: on phones a tap on the card does that
  function showFly(el, html, live = false, act = null) {
    clearTimeout(flyTimer);
    if (fly.classList.contains("is-pinned") && flyFor !== el) return;
    hideTip();
    const over = !fly.hidden && flyFor && flyFor !== el;
    flyFor = el;
    fly._act = act;
    if (fly._html !== html) { fly.innerHTML = `<span class="fly-tail" aria-hidden="true"></span>${html}`; fly._html = html; fly.scrollTop = 0; }
    const touch = !canHover();
    fly.classList.toggle("is-live", live || touch);
    fly.classList.toggle("on-touch", touch);
    if (!over) fly.classList.remove("glide", "open");
    fly.hidden = false;
    const r = el.firstChild.getBoundingClientRect();
    const w = fly.offsetWidth, h = fly.offsetHeight;
    if (touch) {
      // a phone: above the marker if there is room under the header, else below it, and never
      // under the list sheet; no side tail
      const top = ($(".brand") || document.body).getBoundingClientRect().bottom + 8;
      const sheet = isMobile() ? $("#feed").getBoundingClientRect().top - 8 : window.innerHeight - 8;
      let y = r.top - h - 12;
      if (y < top) y = r.bottom + 12;
      y = clamp(y, top, Math.max(top, sheet - h));
      fly.classList.remove("to-left");
      fly.style.left = `${clamp(r.left + r.width / 2 - w / 2, 12, Math.max(12, window.innerWidth - w - 12))}px`;
      fly.style.top = `${y}px`;
      if (over) fly.classList.add("glide");
      else { void fly.offsetWidth; fly.classList.add("open"); }
      return;
    }
    const list = $("#feed").getBoundingClientRect();
    const edge = list.width && list.left > r.right ? list.left : window.innerWidth;
    const right = r.right + 12 + w <= edge - 8 || r.left - 12 - w < 8;
    const cy = r.top + r.height / 2;
    const y = clamp(cy - 26, 8, Math.max(8, window.innerHeight - h - 8));
    fly.classList.toggle("to-left", !right);
    fly.style.left = `${right ? r.right + 12 : r.left - 12 - w}px`;
    fly.style.top = `${y}px`;
    fly.style.setProperty("--tail", `${clamp(cy - y, 14, h - 14)}px`);
    if (over) fly.classList.add("glide");
    else { void fly.offsetWidth; fly.classList.add("open"); }
  }
  function placeRing(t) {
    if (flyRing) { extraRings.delete(flyRing); flyRing = null; }
    if (t && !reduceMotion) { flyRing = { lat: t.lat, lon: t.lon, rgb: ACCENT, alpha: 0.85, max: 2.4, speed: 2.6, period: 1000 }; extraRings.add(flyRing); }
    pushRings();
  }
  function closeFly() {
    clearTimeout(flyTimer);
    if (fly.hidden) return;
    fly.hidden = true;
    fly.classList.remove("glide", "open", "is-pinned");
    flyFor = null;
    if (flyRing) placeRing(null);
    if (fly.classList.contains("is-live")) hotEvent(null);
  }
  // a short wait, so the pointer can reach a list card, or the next marker can take the card over
  function hideFly() { if (fly.classList.contains("is-pinned")) return; clearTimeout(flyTimer); flyTimer = setTimeout(closeFly, fly.classList.contains("is-live") ? 350 : 90); }
  // a phone: touching anything but the card or a marker puts the card away
  window.addEventListener("pointerdown", (ev) => {
    if (!fly.hidden && !canHover() && !fly.contains(ev.target) && !(ev.target.closest && ev.target.closest(".mk"))) closeFly();
  }, { capture: true, passive: true });
  fly.addEventListener("mouseenter", () => { if (fly.classList.contains("is-live")) clearTimeout(flyTimer); });
  fly.addEventListener("mouseleave", () => { placeRing(null); hideFly(); hotRow(null); });
  fly.addEventListener("mouseover", (ev) => {
    const row = ev.target.closest("[data-id], [data-place]");
    if (!row || row._lit) return;
    fly.querySelectorAll(".is-lit").forEach((x) => { x.classList.remove("is-lit"); x._lit = false; });
    row.classList.add("is-lit");
    row._lit = true;
    // the list beside the map follows: the event pointed at, or for a place of an alert group or
    // attack wave, the group (or, with its details open, that place in its locations)
    if (row.dataset.id) { hotEvent(row.dataset.id); hotRow(row.dataset.id); }
    else { const [lat, lon] = row.dataset.place.split(",").map(Number); placeRing({ lat, lon }); hotRow(row.dataset.open, row.dataset.place); }
  });
  fly.addEventListener("click", (ev) => {
    if (ev.target.closest("[data-close-cluster]")) { const source = flyFor; closeFly(); source?.firstChild.focus(); return; }
    const b = ev.target.closest("[data-id], [data-zoom], [data-open]");
    if (!b) {
      // a phone: the card itself opens what it shows
      if (fly._act && fly.classList.contains("on-touch")) { const act = fly._act; closeFly(); act(); }
      return;
    }
    const id = b.dataset.id || b.dataset.open;
    if (b.dataset.zoom) { const [lat, lon] = b.dataset.zoom.split(",").map(Number); zoomTo(lat, lon, 0.35); }
    closeFly();
    if (id) select(id, true);
  });

  // Map and feed answer each other (desktop): pointing at an event in the feed lights its marker
  // and rings its spot on the globe; pointing at a marker lights its row, scrolled into view if the
  // pointer stays a moment.
  let hotId = null, hotMarker = null, hotRing = null, hotRowEl = null, hotScroll = 0;
  function hotEvent(id) {
    hotId = id || null;
    prefetchReports(hotId);
    if (hotMarker) { hotMarker.classList.remove("is-hot"); hotMarker = null; }
    if (hotRing) { extraRings.delete(hotRing); hotRing = null; }
    const e = id && S.data && S.data.events.find((x) => x.id === id);
    if (e) {
      hotMarker = elCache.get(`ev:${id}`) || null;
      // folded into a count bubble: light the bubble
      if (hotMarker && hotMarker.classList.contains("clustered")) {
        hotMarker = [...elCache.values()].find((el) => el.classList.contains("cluster-lead") && (el._members || []).includes(e)) || null;
      }
      if (hotMarker) hotMarker.classList.add("is-hot");
      if (!reduceMotion) { hotRing = { lat: e.lat, lon: e.lon, rgb: ACCENT, alpha: 0.85, max: 3, speed: 3.2, period: 1100 }; extraRings.add(hotRing); }
    }
    pushRings();
  }
  function hotRow(id, place = null) {
    clearTimeout(hotScroll);
    if (hotRowEl) { hotRowEl.classList.remove("is-hot"); hotRowEl = null; }
    if (!id || isMobile()) return;
    if (!$("#detail").hidden) {
      // details open: the matching row in them (a place in this event's locations, or a listed event)
      hotRowEl = (place && S.selectedId === id && $("#detail").querySelector(`.target[data-goto="${CSS.escape(place)}"]`))
        || $("#detail").querySelector(`.target[data-event="${CSS.escape(id)}"]`);
    } else {
      hotRowEl = document.querySelector(`#feedList .item[data-id="${CSS.escape(id)}"]`);
    }
    if (!hotRowEl) return;
    hotRowEl.classList.add("is-hot");
    const row = hotRowEl;
    hotScroll = setTimeout(() => row.scrollIntoView({ block: "nearest", behavior: reduceMotion ? "auto" : "smooth" }), 350);
  }

  // ------------------------------------------------------------------ declutter
  // Screen-space grouping and shared collision spacing keep dense areas readable.
  // Rotation emits the same camera events as dragging. Bound layout work for both,
  // including programmatic flights; CSS marker positions still follow every rendered frame.
  let declutterQueued = false, lastDeclutter = 0;
  const declutterInterval = () => controls.autoRotate ? (PHONE ? 250 : 200) : moving ? (PHONE ? 180 : 60) : 100;
  function queueDeclutter() {
    if (declutterQueued) return;
    declutterQueued = true;
    const wait = Math.max(0, lastDeclutter + declutterInterval() - performance.now());
    const run = () => requestAnimationFrame(() => { declutterQueued = false; lastDeclutter = performance.now(); declutter(); });
    if (wait) setTimeout(run, wait); else run();
  }
  function setOffset(d, dx, dy) {
    const spread = Math.abs(dx) + Math.abs(dy) > 0.5;
    if (d._dx === Math.round(dx) && d._dy === Math.round(dy)) return;
    d._dx = Math.round(dx); d._dy = Math.round(dy);
    const el = d.el;
    el.style.setProperty("--dx", `${d._dx}px`);
    el.style.setProperty("--dy", `${d._dy}px`);
    el.style.setProperty("--len", `${Math.hypot(dx, dy).toFixed(1)}px`);
    el.style.setProperty("--ang", `${Math.atan2(dy, dx)}rad`);
    el.classList.toggle("spread", spread);
  }
  function setCluster(d, count) {
    const el = d.el;
    const lead = count > 0;
    if (el.classList.contains("cluster-lead") !== lead) el.classList.toggle("cluster-lead", lead);
    if (lead) {
      const c = el.querySelector(".mk-count");
      if (c && c.textContent !== String(count)) c.textContent = String(count);
      const title = `${count} nearby events. Open event list.`;
      if (el.firstChild.title !== title) el.firstChild.title = title;
      if (el.firstChild.getAttribute("aria-label") !== title) el.firstChild.setAttribute("aria-label", title);
    } else if (el.firstChild.title) { el.firstChild.title = ""; el.firstChild.setAttribute("aria-label", el._baseLabel || "Event"); }
  }
  function setHidden(d, hidden) { if (d.el.classList.contains("clustered") !== hidden) d.el.classList.toggle("clustered", hidden); }

  // Group nearby events at overview scale, and keep dense locations grouped at every zoom.
  let clusterMode = false;
  const CLUSTER_ON = 0.55, CLUSTER_OFF = 0.45;
  function declutter() {
    if (!S.html.length) return;
    const viewportW = window.innerWidth, viewportH = window.innerHeight;
    const pov = world.pointOfView();
    const horizon = (Math.acos(1 / (1 + pov.altitude)) * 180) / Math.PI - 1;
    const R = isMobile() ? 52 : 42; // Leave room for touch targets and count chips.
    // Count bubbles appear above 0.55 and go away below 0.45, so a pinch near the line doesn't flicker.
    clusterMode = clusterMode ? pov.altitude > CLUSTER_OFF : pov.altitude > CLUSTER_ON;
    const vis = [];
    for (const d of S.html) {
      if (!d.el) continue;
      if (km(pov.lat, pov.lng, d.lat, d.lon) / 111.2 > horizon) { setOffset(d, 0, 0); if (d.isEvent) setCluster(d, 0); setHidden(d, true); continue; }
      const s = world.getScreenCoords(d.lat, d.lon, d.hAlt || 0.012);
      if (!s || s.x < -60 || s.x > viewportW + 60 || s.y < -60 || s.y > viewportH + 60) { setHidden(d, true); continue; }
      vis.push({ d, x: s.x, y: s.y });
    }
    vis.sort((a, b) => Number(b.d.key === `ev:${S.selectedId}`) - Number(a.d.key === `ev:${S.selectedId}`) || b.d.prio - a.d.prio || a.d.key.localeCompare(b.d.key));
    const group = (items, radius = R) => {
      const groups = [];
      for (const v of items) {
        const g = groups.find((gg) => Math.abs(gg.x - v.x) < radius && Math.abs(gg.y - v.y) < radius);
        if (g) g.m.push(v); else groups.push({ x: v.x, y: v.y, m: [v] });
      }
      return groups;
    };
    // Pass 1: nearby events collapse into an explorable count chip.
    const hidden = new Set(), counts = new Map();
    {
      // Dense locations stay grouped even when zoomed in; selected events stay visible.
      for (const g of group(vis.filter((v) => v.d.isEvent && v.d.key !== `ev:${S.selectedId}`), R * (clusterMode ? 1.5 : 1))) {
        if (g.m.length < (clusterMode ? 2 : 3)) continue;
        g.m.forEach((v, i) => { if (i === 0) { counts.set(v.d, g.m.length); v.d.el._members = g.m.map((x) => x.d.ev); } else hidden.add(v); });
      }
    }
    vis.forEach((v) => { if (v.d.isEvent) setCluster(v.d, counts.get(v.d) || 0); setHidden(v.d, hidden.has(v)); });
    // Pack the whole visible set together, rather than fanning each group into its neighbours.
    // A spatial index keeps collision checks local; no DOM measurements in this pass.
    const cell = 80, grid = new Map(), gap = isMobile() ? 14 : 12;
    const cells = (r) => {
      const keys = [];
      for (let x = Math.floor(r.l / cell); x <= Math.floor(r.r / cell); x++)
        for (let y = Math.floor(r.t / cell); y <= Math.floor(r.b / cell); y++) keys.push(`${x}:${y}`);
      return keys;
    };
    const collides = (r) => cells(r).some(k => (grid.get(k) || []).some(b => r.l < b.r && r.r > b.l && r.t < b.b && r.b > b.t));
    for (const v of vis.filter(v => !hidden.has(v))) {
      const w = counts.has(v.d) ? (isMobile() ? 76 : 68) : 46;
      const h = 46;
      let placed = null, dx = 0, dy = 0;
      // Deterministic rings keep positions stable and always preserve a leader line to the location.
      for (let ring = 0; ring <= 12 && !placed; ring++) {
        const n = ring ? ring * 8 : 1, radius = ring * (isMobile() ? 30 : 26);
        for (let i = 0; i < n; i++) {
          const a = -Math.PI / 2 + i * 2 * Math.PI / n;
          dx = Math.round(Math.max((w + gap)/2 - v.x, Math.min(viewportW - (w + gap)/2 - v.x, Math.cos(a) * radius)));
          dy = Math.round(Math.max((h + gap)/2 - v.y, Math.min(viewportH - (h + gap)/2 - v.y, Math.sin(a) * radius)));
          const r = {l:v.x + dx - (w + gap)/2, r:v.x + dx + (w + gap)/2, t:v.y + dy - (h + gap)/2, b:v.y + dy + (h + gap)/2};
          if (!collides(r)) { placed = r; break; }
        }
      }
      if (placed) for (const k of cells(placed)) { if (!grid.has(k)) grid.set(k, []); grid.get(k).push(placed); }
      setOffset(v.d, dx, dy);
    }
    hidden.forEach((v) => setOffset(v.d, 0, 0));
  }

  // ------------------------------------------------------------------ tooltips
  // The places an alert group names, or an attack wave hit, latest first.
  const placesOf = (e) => ((e.alert || e.wave) && e.targets ? e.targets.filter((t) => t.lat != null && t.lon != null) : []);
  // a place's latest alert or report (older data has only its first, `time`); lists go newest first
  const latestOf = (t) => t.last || t.time || "";
  const byLatest = (list) => [...list].sort((a, b) => latestOf(b).localeCompare(latestOf(a)));
  function tipEvent(e, quick = false) {
    const extra = e.alert ? `<span>${alertsText(e)}</span>`
      : e.wave && e.targets && e.targets.length > 1 ? `<span>${e.targets.length} locations</span>` : "";
    const n = e.sources_count || (e.reports || []).length;
    const places = quick ? placesOf(e) : [];
    // the quick look breaks an alert group or wave out into the places it names
    const list = places.length > 1 ? `<div class="fly-sub">${e.alert ? "Places named" : "Places hit"}</div>
      <ul class="fly-rows">${byLatest(places).map((t, i) => `<li style="--i:${Math.min(i, 10)}"><button type="button" data-open="${esc(e.id)}" data-place="${t.lat},${t.lon}"><span><b>${esc(t.place)}</b>${t.reports > 1 ? ` ${t.reports} ${e.alert ? "alerts" : "reports"}` : ""}</span><time>${latestOf(t) ? esc(agoShort(Date.parse(latestOf(t)))) : ""}</time></button></li>`).join("")}</ul>` : "";
    return `<div class="tip"><div class="tip-meta">${eventIcon(e)}<b>${esc(typeLabel(e))}</b><span>${esc(metaLine(e))}</span></div>
      <div class="tip-sum">${esc(e.summary)}</div>
      <div class="tip-foot"><span class="conf-text conf-${STATUS[e.status].conf}">${esc(STATUS[e.status].label)}</span>${e.possibly_old ? "<span>Possibly an old story</span>" : ""}${extra}${quick && n ? `<span>${n} ${n === 1 ? "source" : "sources"}</span>` : ""}<span>${esc(ago(e._t))}</span>${hasFollowup(e) ? `<span>Updated ${esc(ago(e._tu))}</span>` : ""}</div>
      ${list}${quick ? `<div class="tip-hint">${canHover() ? "Click for details" : "Tap for details"}</div>` : ""}</div>`;
  }
  // A count bubble: every event it holds, most important first, each one a button that opens it.
  function tipCluster(evs, lead) {
    const recentFirst = evs.filter(Boolean).sort((a, b) => b._t - a._t);
    return `<div class="tip tip-list"><div class="tip-meta"><b>${evs.length} nearby events</b><button type="button" class="cluster-close" data-close-cluster aria-label="Close event group">×</button></div>
      <p class="cluster-context">${esc(lead.place || metaLine(lead))} and surrounding area</p>
      <ul class="fly-rows">${recentFirst.map((e) => `<li><button type="button" data-id="${esc(e.id)}">${eventIcon(e)}<span><b>${esc(typeLabel(e))} · ${esc(e.place || metaLine(e))}</b><span class="cluster-summary">${esc(e.summary || "")}</span><span class="cluster-evidence">${esc(STATUS[e.status].label)}</span></span><time>${esc(agoShort(e._t))}</time></button></li>`).join("")}</ul><button type="button" class="fly-zoom" data-zoom="${lead.lat},${lead.lon}">Explore this area ↗</button></div>`;
  }
  function tipCarrier(c, quick = false) {
    return `<div class="tip"><div class="tip-meta">${iconBadge("carrier", "fleet")}<b>${esc(c.name)}</b><span>${esc(c.hull)}</span></div>
      <div class="tip-sum">${esc(carrierStatus(c))}${c.place ? `, ${esc(c.place)}` : ""}</div>
      <div class="tip-foot"><span>${c._asOf ? `As of ${esc(fmtDay(c._asOf))}` : "No position reports yet"}</span>${c.heading_to ? `<span>heading to ${esc(c.heading_to.place || "a stated destination")}</span>` : ""}</div>${quick ? '<div class="tip-hint">Tap for details</div>' : ""}</div>`;
  }
  function tipFlow(f) {
    return `<div class="tip"><div class="tip-meta">${flowBadge(f)}<b>${esc(flowFrom(f))} → ${esc(flowTo(f))}</b></div>
      <div class="tip-sum">${f.deliveries} ${f.deliveries === 1 ? "delivery" : "deliveries"} reported in 30 days${f.cargo.length ? `: ${esc(f.cargo.slice(0, 2).join(", "))}` : ""}</div>
      <div class="tip-foot"><span>${f.active ? "Active" : "Quiet"}</span><span>last ${esc(ago(f.last))}</span></div></div>`;
  }

  // Territorial control: pointing at held ground (tapping it on phones) names it ("Russian-occupied"),
  // with the source and its date. The shapes are painted into the globe's picture, so the point
  // under the pointer is tested against them here. Advances and infiltration outrank the areas
  // they lie in; among occupied layers, the first configured (held since 2014) wins.
  const CONTROL_RANK = { advance: 0, infiltration: 1, occupied: 2, claimed: 3 };
  const ctlBoxes = new WeakMap();
  function polyBox(poly) {
    let b = ctlBoxes.get(poly);
    if (!b) {
      b = [180, 90, -180, -90];
      for (const [x, y] of poly[0]) { b[0] = Math.min(b[0], x); b[1] = Math.min(b[1], y); b[2] = Math.max(b[2], x); b[3] = Math.max(b[3], y); }
      ctlBoxes.set(poly, b);
    }
    return b;
  }
  function inPoly(poly, x, y) {
    let inside = false;  // even-odd over all rings, so holes count as outside
    for (const ring of poly) for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const [xi, yi] = ring[i], [xj, yj] = ring[j];
      if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
    }
    return inside;
  }
  function controlAt(lat, lng) {
    let best = null;
    controlLayers().forEach((L, i) => {
      const rank = (CONTROL_RANK[L.style] ?? 3) * 100 + i;
      if (best && best.rank <= rank) return;
      for (const poly of L.polygons) {
        const b = polyBox(poly);
        if (lng >= b[0] && lng <= b[2] && lat >= b[1] && lat <= b[3] && inPoly(poly, lng, lat)) { best = { L, rank }; break; }
      }
    });
    return best && best.L;
  }
  // For the site's own areas: the nearest settlement the assessment rests on, and its evidence.
  function nearestPlace(L, lat, lng) {
    const places = (S.data && S.data.frontline && S.data.frontline.places) || [];
    const kx = 111.32 * Math.cos(lat * Math.PI / 180);
    let best = null, bestD = (L.area_km || 10) * 1.5;
    for (const p of places) {
      if (p.conflict !== L.conflict) continue;
      const d = Math.hypot((p.lon - lng) * kx, (p.lat - lat) * 110.57);
      if (d < bestD) { best = p; bestD = d; }
    }
    return best;
  }
  // The site's own front-line areas: just who controls the ground ("Russian-controlled").
  function tipArea(L) {
    return `<div class="tip"><div class="tip-meta"><b>${esc(L.label)}</b></div></div>`;
  }
  function tipControl(L, lat, lng) {
    if (L.assessment) return tipArea(L);
    const day = L.as_of ? new Date(L.as_of).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" }) : "";
    return `<div class="tip"><div class="tip-meta"><span class="control-swatch${L.style === "infiltration" ? " hatched" : L.style === "advance" ? " advance" : ""}" aria-hidden="true"></span><b>${esc(L.label)}</b></div>
      <div class="tip-foot"><span>${L.approx ? `Approximate: traced from the ${esc(L.source || "source")} map` : esc(L.source || "Source map")}${day ? `${L.approx ? " of" : ", as of"} ${esc(day)}` : ""}</span></div></div>`;
  }
  function showTipAt(x, y, html) {
    setTip(html);
    const w = tipBox.offsetWidth;
    tipBox.style.left = `${clamp(x - w / 2, 8, window.innerWidth - w - 8)}px`;
    tipBox.style.top = `${Math.max(8, y - tipBox.offsetHeight - 14)}px`;
  }
  const globeCanvas = world.renderer().domElement;
  let ctlTip = false, ctlQueued = null, ctlTimer = null, ctlDown = null;
  function controlUnder(x, y) {
    if (!controlLayers().length) return null;
    const r = globeCanvas.getBoundingClientRect();
    const at = world.toGlobeCoords(x - r.left, y - r.top);
    const L = at && controlAt(at.lat, at.lng);
    return L ? { L, lat: at.lat, lng: at.lng } : null;
  }
  function hideControlTip() { if (ctlTip) { ctlTip = false; hideTip(); } }
  globeCanvas.addEventListener("pointermove", (ev) => {
    if (ev.pointerType !== "mouse") return;
    const first = !ctlQueued;
    ctlQueued = ev;
    if (first) requestAnimationFrame(() => {
      const e = ctlQueued;
      ctlQueued = null;
      const hit = !moving && !e.buttons ? controlUnder(e.clientX, e.clientY) : null;
      if (hit) { showTipAt(e.clientX, e.clientY, tipControl(hit.L, hit.lat, hit.lng)); ctlTip = true; } else hideControlTip();
    });
  });
  globeCanvas.addEventListener("pointerleave", hideControlTip);
  // a tap (not a drag) on held ground shows its name for a few seconds
  globeCanvas.addEventListener("pointerdown", (ev) => { ctlDown = { x: ev.clientX, y: ev.clientY, t: performance.now() }; });
  globeCanvas.addEventListener("pointerup", (ev) => {
    const d = ctlDown;
    ctlDown = null;
    if (!d || Math.hypot(ev.clientX - d.x, ev.clientY - d.y) > 8 || performance.now() - d.t > 500) return;
    // a finger: a tap close to a supply route or carrier line opens it (the lines are thin to hit)
    const line = ev.pointerType !== "mouse" ? lineNear(ev.clientX, ev.clientY) : null;
    if (line) {
      lineTapAt = performance.now();
      hideControlTip();
      if (line.carrier) selectCarrier(line.carrier.hull, true); else selectFlow(line.flow.key);
      return;
    }
    // a click away from everything (empty space, or bare globe: onGlobeClick) ends the focus on an opened event
    if (focused() && !world.toGlobeCoords(ev.clientX - globeCanvas.getBoundingClientRect().left, ev.clientY - globeCanvas.getBoundingClientRect().top)) closeDetail();
    if (ev.pointerType === "mouse") return;
    const hit = controlUnder(ev.clientX, ev.clientY);
    clearTimeout(ctlTimer);
    if (!hit) return hideControlTip();
    showTipAt(ev.clientX, ev.clientY, tipControl(hit.L, hit.lat, hit.lng));
    ctlTip = true;
    ctlTimer = setTimeout(hideControlTip, 3500);
  });

  // Supply routes and carrier lines are thin and float a little above the ground, so a finger
  // rarely lands on them: a tap within TAP_PX of one, on screen, counts as a tap on it. The curve
  // is the globe library's own: a cubic from each end on the ground through points a quarter and
  // three quarters along, raised to 1.5 times the line's height.
  const TAP_PX = 24;
  let lineTapAt = 0;
  function arcOnScreen(a, cam, R) {
    const lift = (a.alt == null ? 0 : a.alt) * 1.5;
    const s = { lat: a.sLat, lon: a.sLng }, e = { lat: a.eLat, lon: a.eLng };
    const m1 = slerp(s, e, 0.25), m2 = slerp(s, e, 0.75);
    const P = [world.getCoords(s.lat, s.lon, 0), world.getCoords(m1.lat, m1.lon, lift), world.getCoords(m2.lat, m2.lon, lift), world.getCoords(e.lat, e.lon, 0)];
    const out = [];
    for (let i = 0; i <= 16; i++) {
      const t = i / 16, u = 1 - t;
      const k = [u * u * u, 3 * u * u * t, 3 * u * t * t, t * t * t];
      const x = k[0] * P[0].x + k[1] * P[1].x + k[2] * P[2].x + k[3] * P[3].x;
      const y = k[0] * P[0].y + k[1] * P[1].y + k[2] * P[2].y + k[3] * P[3].y;
      const z = k[0] * P[0].z + k[1] * P[1].z + k[2] * P[2].z + k[3] * P[3].z;
      if (x * cam.x + y * cam.y + z * cam.z < R * R) { out.push(null); continue; }  // behind the globe
      const g = world.toGeoCoords({ x, y, z });
      out.push(g ? world.getScreenCoords(g.lat, g.lng, g.altitude) : null);
    }
    return out;
  }
  function segDist(px, py, a, b) {
    const dx = b.x - a.x, dy = b.y - a.y, len = dx * dx + dy * dy;
    const t = len ? clamp(((px - a.x) * dx + (py - a.y) * dy) / len, 0, 1) : 0;
    return Math.hypot(px - a.x - t * dx, py - a.y - t * dy);
  }
  function lineNear(x, y) {
    const r = globeCanvas.getBoundingClientRect();
    const px = x - r.left, py = y - r.top;
    const cam = world.camera().position, R = world.getGlobeRadius();
    let best = null, bestD = TAP_PX;
    for (const a of baseArcs) {
      if (!(a.flow || a.carrier) || a.kind === "hit" || a.kind === "routeShot") continue;
      const pts = arcOnScreen(a, cam, R);
      for (let i = 1; i < pts.length; i++) {
        if (!pts[i - 1] || !pts[i]) continue;
        const dd = segDist(px, py, pts[i - 1], pts[i]);
        if (dd < bestD) { bestD = dd; best = a; }
      }
    }
    return best;
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
    queueDeclutter();
  }
  window.addEventListener("resize", () => {
    mobileNow = window.innerWidth < 1024;
    if (!isMobile()) $("#feed").style.height = "";
    else setSheet(S.sheet, true);
    layout();
  });
  if (window.visualViewport) {
    window.visualViewport.addEventListener("resize", () => {
      if (isMobile()) setSheet(S.sheet, true);
      layout();
    });
  }

  // ------------------------------------------------------------------ data
  // Live updates: every minute (and whenever the page comes back into view) a light request asks
  // whether events.json has changed, by its ETag or Last-Modified header; the file itself is
  // downloaded only when it has. Without those headers, the full file is fetched every REFRESH_MS.
  let dataStamp = null, lastFull = 0;
  const stampOf = (res) => res.headers.get("etag") || res.headers.get("last-modified");
  // A page left open while the site's code is updated would go on running the old code against
  // newer data (on 2026-10-04 that left every event without its reports). The data says which
  // version is live (`build`); when it differs from this page's own, the page reloads: at once if
  // it is in the background or untouched for two minutes, else when the note is clicked or it
  // next goes idle. Once per version: a cached copy of the page may still be served for a while.
  let lastInput = Date.now(), pendingBuild = "";
  ["pointerdown", "keydown", "wheel"].forEach((t) => window.addEventListener(t, () => { lastInput = Date.now(); }, { capture: true, passive: true }));
  function reloadFor(build) {
    try { sessionStorage.setItem("gsm_reload", build); } catch (_) { /* storage blocked */ }
    location.replace(`${location.pathname}?v=${encodeURIComponent(build)}${location.hash}`);  // a new address, past any cached copy
  }
  function checkBuild(data) {
    const live = data && data.build;
    if (DEMO || !OWN_BUILD || !live || live === OWN_BUILD) return;
    let tried = "";
    try { tried = sessionStorage.getItem("gsm_reload") || ""; } catch (_) { /* storage blocked */ }
    if (tried === live) return;
    pendingBuild = live;
    if (document.hidden || Date.now() - lastInput > 120e3) { reloadFor(live); return; }
    let note = document.getElementById("updateNote");
    if (!note) {
      note = document.createElement("div");
      note.id = "updateNote";
      note.className = "live-note update-note is-on";
      note.setAttribute("role", "status");
      note.innerHTML = 'The site was updated. <button class="linkish" type="button">Reload</button>';
      note.querySelector("button").addEventListener("click", () => reloadFor(pendingBuild));
      document.body.appendChild(note);
    }
  }
  const reloadIfIdle = () => { if (pendingBuild && (document.hidden || Date.now() - lastInput > 120e3)) reloadFor(pendingBuild); };

  async function checkForUpdate() {
    reloadIfIdle();
    if (DEMO || document.hidden) return;
    try {
      const res = await fetch(`data/events.json?t=${Date.now()}`, { method: "HEAD", cache: "no-store" });
      const stamp = res.ok ? stampOf(res) : null;
      if (stamp ? stamp !== dataStamp : Date.now() - lastFull >= REFRESH_MS) load();
    } catch (_) { /* offline for a moment: try again next time */ }
  }

  async function load() {
    const url = DEMO ? "data/demo-events.json" : `data/events.json?t=${Date.now()}`;
    let data;
    try {
      // the first load uses the download index.html already started (and may have previewed)
      const early = window.__events;
      window.__events = null;
      const got = early ? await early : { res: await fetch(url, { cache: "no-store" }) };
      const res = got.res;
      if (!res.ok) throw new Error(res.status === 404 ? "missing" : `HTTP ${res.status}`);
      dataStamp = stampOf(res);
      lastFull = Date.now();
      data = got.data || await res.json();
    } catch (err) {
      showLoadError(err);
      return;
    }
    // A share page also carries the published snapshot, so old links still have their sources.
    const wanted = S.selectedId || (S.firstLoad && INITIAL_EVENT_ID);
    if (!DEMO && wanted && !(data.events || []).some((e) => e.id === wanted)) {
      try {
        if (sharedSnapshot?.id !== wanted && /^[a-f0-9]{12}$/.test(wanted)) {
          const res = await fetch(new URL(`events/${wanted}/event.json`, APP_ROOT));
          sharedSnapshot = res.ok ? await res.json() : null;
        }
        if (sharedSnapshot?.id === wanted) data.events = [...(data.events || []), { ...sharedSnapshot, _archived: true }];
      } catch { /* a missing snapshot must not prevent the live dashboard from loading */ }
    }
    try { ingest(data); } catch (err) { fatal(err); }
  }

  // A short note when a live update brings new events (read out by screen readers, too).
  let announceTimer = 0;
  function announce(text) {
    let el = document.getElementById("liveNote");
    if (!el) {
      el = document.createElement("div");
      el.id = "liveNote";
      el.className = "live-note";
      el.setAttribute("role", "status");
      document.body.appendChild(el);
    }
    el.textContent = text;
    el.classList.add("is-on");
    clearTimeout(announceTimer);
    announceTimer = setTimeout(() => el.classList.remove("is-on"), 6000);
  }

  // If anything breaks while starting or drawing, say so on the page instead of hanging on "Loading".
  function fatal(err) {
    console.error(err);
    const msg = (err && (err.stack || err.message)) || String(err);
    const ft = document.getElementById("freshText");
    if (ft) ft.textContent = "The dashboard hit an error";
    const b = document.getElementById("beacon");
    if (b) b.className = "beacon dead";
    const list = document.getElementById("feedList");
    if (list) {
      list.hidden = false;
      list.innerHTML = `<li class="empty"><strong>The dashboard didn't start properly.</strong>Try a hard refresh: Cmd + Shift + R on a Mac, Ctrl + Shift + R on Windows. Right after an update, a browser can mix old and new files. If this message stays, send this error text:<pre class="err">${esc(String(msg).slice(0, 600))}</pre></li>`;
    }
  }

  function shiftDemo(data) {
    const shift = Date.now() - Date.parse(data.generated_at);
    const move = (s) => (s ? new Date(Date.parse(s) + shift).toISOString() : s);
    data.generated_at = move(data.generated_at);
    if (data.analysis) data.analysis.generated_at = move(data.analysis.generated_at);
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
      e._t = Date.parse(e.time || e.updated);   // original occurrence: sorting, animation, and "new"
      e._tu = Date.parse(e.updated || e.time);  // latest report, shown separately from the event date
      e.targets = Array.isArray(e.targets) ? e.targets.filter((t) => isFinite(t.lat) && isFinite(t.lon)) : [];
      const t = e.transfer || {};
      e._search = [e.summary, e.place, e.targets.map((x) => x.place).join(" "), typeLabel(e), countryName(e.attacker),
        countryName(e.country), countryName(t.supplier), countryName(t.recipient), t.what,
        regionWords(e), ...(e.src || (e.reports || []).map((r) => r.source))].join(" ").toLowerCase();
    }
    // a new copy of the data: reports files are fetched afresh, and markers of events that are gone are let go
    reportFiles.clear();
    const ids = new Set(data.events.map((e) => `ev:${e.id}`));
    for (const k of [...elCache.keys()]) if (k.startsWith("ev:") && !ids.has(k)) { elCache.delete(k); markerData.delete(k); }
    data.heat = (data.heat || []).map((c) => ({ ...c, _t: Date.parse(c.last) }));
    const byHull = new Map(S.fleet.map((c) => [c.hull, c]));
    S.fleet = (data.fleet || []).filter((c) => isFinite(c.lat) && isFinite(c.lon)).map((c) => {
      const d = byHull.get(c.hull) || {};
      Object.assign(d, c);
      d._lat = c.lat; d._lon = c.lon;
      d._asOf = c.as_of && !c.as_of.startsWith("1970") ? Date.parse(c.as_of) : 0;
      d._moved = c.moved_at ? Date.parse(c.moved_at) : 0;
      d._fresh = (c.status === "departed" || c.status === "underway") && d._asOf && Date.now() - d._asOf < 72 * HOUR;
      return d;
    }).sort((a, b) => (a.at_home === b.at_home ? a.hull.localeCompare(b.hull) : a.at_home ? 1 : -1));
    S.fleetMeta = data.fleet_meta || {};
    const theaters = Array.isArray(data.theaters) && data.theaters.length ? data.theaters : FALLBACK_THEATERS;
    S.theaters = theaters;
    if (S.region !== "world" && S.region !== null && !selectedRegion()) S.region = "world";
    syncRegionControls();
    checkBuild(data);
    const before = S.data ? new Set(S.data.events.map((e) => e.id)) : null;
    S.data = data;
    if (before) {
      const fresh = data.events.filter((e) => !before.has(e.id) && onMap(e) && passes(e) && isNew(e));
      if (fresh.length) {
        announce(`${fresh.length} new ${fresh.length === 1 ? "event" : "events"} added`);
        // they slide into the feed; only on this render, so later redraws don't replay it
        S.arrived = new Set(fresh.map((e) => e.id));
        setTimeout(() => { S.arrived = null; }, 1500);
        // a serious new event marks its arrival with one ring spreading from its spot on the globe
        fresh.filter((e) => passes(e) && e.severity >= 2).slice(0, 8).forEach((e, i) => setTimeout(() => { if (passes(e)) landRing(e); }, i * 250));
      }
    }

    renderTheaters();
    renderSources();
    render();
    updateFreshness();
    sailRecentMoves();

    if (S.firstLoad) {
      S.firstLoad = false;
      // Opens on the last 24 hours; if that's empty, widen to 3 days so the first view isn't blank.
      if (!data.events.some((e) => onMap(e) && e._t >= Date.now() - 24 * HOUR)) { setWindow(72); render(); }
      const hash = INITIAL_EVENT_ID;
      if (hash && data.events.some((e) => e.id === hash)) select(hash, true);
      else if (/^CVN-\d{2}$/.test(hash) && S.fleet.some((c) => c.hull === hash)) selectCarrier(hash, true);
      else focusHotspot();
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

  // ------------------------------------------------------------------ opening view
  // The busiest, most serious area in the current window: events score by severity and confidence,
  // and each event's score counts everything within 400 km. The camera flies there on load.
  const SEV_W = { 1: 1, 2: 2.5, 3: 6 };
  const CONF_W = { corroborated: 1, unconfirmed: 0.6, claimed: 0.4 };
  function hotspot() {
    const evs = visibleEvents().filter((e) => onMap(e) && !isDiplomacy(e));
    if (!evs.length) return null;
    const w = (e) => SEV_W[e.severity] * CONF_W[e.status] + (e.wave ? Math.min(4, e.targets.length * 0.4) : 0);
    let best = null;
    // Alerts add weight to an area but don't headline it when anything actually happened there.
    const leads = evs.some((e) => !e.alert) ? evs.filter((e) => !e.alert) : evs;
    for (const e of leads) {
      const near = evs.filter((o) => km(e.lat, e.lon, o.lat, o.lon) <= 400);
      const score = near.reduce((n, o) => n + w(o), 0);
      if (!best || score > best.score) best = { e, near, score };
    }
    let x = 0, y = 0, z = 0, tw = 0;
    best.near.forEach((o) => { const k = w(o); x += k * Math.cos(toRad(o.lat)) * Math.cos(toRad(o.lon)); y += k * Math.cos(toRad(o.lat)) * Math.sin(toRad(o.lon)); z += k * Math.sin(toRad(o.lat)); tw += k; });
    const lat = (Math.atan2(z, Math.hypot(x, y)) * 180) / Math.PI, lon = (Math.atan2(y, x) * 180) / Math.PI;
    const spread = Math.max(...best.near.map((o) => km(lat, lon, o.lat, o.lon)));
    const theater = (S.theaters.find((t) => t.id === best.e.theater) || {}).name || "";
    const inTheater = evs.filter((o) => o.theater === best.e.theater).length;
    return { lat, lon, altitude: clamp(0.75 + spread / 1800, 0.85, 1.35), n: inTheater, theater, top: best.e };
  }
  function focusHotspot() {
    const h = hotspot();
    if (!h) { world.pointOfView({ lat: 30, lng: 38, altitude: isMobile() ? 4.2 : 2.2 }, reduceMotion ? 0 : 2600); return; }
    world.pointOfView({ lat: h.lat, lng: h.lon, altitude: isMobile() ? 4.2 : 2.2 }, reduceMotion ? 0 : 1800);
    const span = { 6: "6 hours", 24: "24 hours", 72: "3 days", 168: "7 days" }[S.windowH];
    const toast = $("#focusToast");
    toast.innerHTML = `<span class="focus-k">Most active now</span> <strong>${esc(h.theater)}</strong> <span>${h.n} ${h.n === 1 ? "event" : "events"} in the last ${span}, led by ${esc(typeLabel(h.top).toLowerCase())}${h.top.place ? ` near ${esc(h.top.place)}` : ""}</span>`;
    toast.hidden = false;
    toast.classList.remove("is-out");
    setTimeout(() => toast.classList.add("is-out"), 7000);
    setTimeout(() => { toast.hidden = true; }, 7800);
  }

  // ------------------------------------------------------------------ filtering
  // Search matches each word on its own, anywhere in the event ("hybrid attacks europe" finds a
  // sabotage or hybrid attack in Germany). A trailing "s" is dropped, so plurals match too.
  let wordsFor = null, wordsList = [];
  const words = () => {
    if (wordsFor !== S.query) { wordsFor = S.query; wordsList = S.query.toLowerCase().split(/[^\p{L}\p{N}-]+/u).filter(Boolean).map((w) => (w.length > 3 ? w.replace(/s$/, "") : w)); }
    return wordsList;
  };
  const matches = (e) => words().every((w) => e._search.includes(w));
  const selectedRegion = () => S.theaters.find((t) => t.id === S.region && t.listed !== false);
  const theaterShown = (id) => S.region === "world" || (S.region !== null && S.region === id);
  // Every time window follows when the event happened, even when new coverage arrives later.
  const hasFollowup = (e) => !e.alert && !e.possibly_old && onMap(e) && e._tu >= e._t + DAY;
  function passes(e, ignoreTheater = false) {
    if (e._archived) return false; // shared history never enters live counts or the latest list
    if (e._t < Date.now() - S.windowH * HOUR) return false;
    if (!ignoreTheater && !theaterShown(e.theater)) return false;
    if (!S.statusOn.has(e.status)) return false;
    if (S.off.has(legendKey(e))) return false;
    if (!matches(e)) return false;
    return true;
  }
  const visibleEvents = () => (S.data ? S.data.events.filter((e) => passes(e)).sort((a, b) => b._t - a._t) : []);
  // Deliveries and pledges are drawn as supply routes, not as markers. One with no route (the
  // report named no supplier or recipient: "Ukraine received a new batch of NASAMS missiles") has
  // nothing to draw a route with, so it gets a marker at its own place instead of vanishing.
  const onMap = (e) => e.type !== "arms_transfer" || !e.transfer || tkind(e) === "interdiction";
  function fade(e) {
    const doubt = e.possibly_old ? 0.55 : 1; // may be an old story: shown, but quieter
    if (S.windowH <= 6) return doubt;
    const age = Date.now() - e._t;
    return doubt * clamp(1 - ((age - 6 * HOUR) / (S.windowH * HOUR - 6 * HOUR)) * 0.6, 0.4, 1);
  }

  // One ring from an event's spot, in its own color: a new event arriving.
  const landRing = (e, max = 4.5) => flashRing(e.lat, e.lon, CAT_RGB[catOf(e)[0]] || CAT_RGB.strike, max, 1800);

  // Opening a drone or missile attack runs its launch lines once: a bright dash travels each line
  // from the launch area to the place hit, one after another, and a small ring marks the arrival.
  // Same lines as on the map (attackPaths), so faint assumed launch areas stay as faint as before.
  let launchTimers = [];
  const launchShots = new Set();
  function stopLaunches() {
    launchTimers.forEach(clearTimeout);
    launchTimers = [];
    if (!launchShots.size) return;
    launchShots.forEach((a) => extraArcs.delete(a));
    launchShots.clear();
    pushArcs();
  }
  function playLaunches(e, flight) {
    stopLaunches();
    if (reduceMotion || !S.layers.paths) return;
    const all = attackPaths([e]);
    const barrage = all.some((a) => a.barrage);
    const paths = all.slice(0, PHONE ? 4 : barrage ? 32 : 16);
    if (!paths.length) return;
    const lead = Math.max(150, flight - 100);  // let the camera arrive first
    const gap = barrage ? 70 : 160;            // a barrage's lines fire close together
    paths.forEach((a, i) => {
      const ms = clamp(700 + (a.dist || 500) * 0.8, 900, 2400);
      launchTimers.push(setTimeout(() => {
        const shot = { sLat: a.sLat, sLng: a.sLng, eLat: a.eLat, eLng: a.eLng, kind: "shot", ms, seed: 1,
          color: rgba([255, 226, 204], a.kind === "strike" ? 1 : 0.75), stroke: a.kind === "strike" ? 1.3 : 0.9 };
        launchShots.add(shot);
        extraArcs.add(shot);
        pushArcs();
        launchTimers.push(setTimeout(() => {
          launchShots.delete(shot);
          extraArcs.delete(shot);
          pushArcs();
          flashRing(a.eLat, a.eLng, CAT_RGB.strike, 1.8, 900);
        }, ms * 1.04));
      }, lead + i * gap));
    });
  }

  // Focus: with an event, carrier or route open, everything else on the globe dims (markers in
  // styles.css, under body.focus; lines, dots and rings here), so what you opened stands out.
  const focused = () => !!(S.selectedId || S.selectedHull || S.selectedFlow);
  const FOCUS_DIM = 0.28;
  const dimOf = (isSelected, inRegion = false) => ((!isSelected && (focused() || (selectedRegion() && !inRegion))) ? FOCUS_DIM : 1);

  // ------------------------------------------------------------------ supply routes (the time window)
  // Routes follow the time filter like everything else: a route shows, and is active, when a
  // delivery on it falls in the selected window (6 hours to 7 days).
  const WINDOW_TEXT = { 6: "last 6 hours", 24: "last 24 hours", 72: "last 3 days", 168: "last 7 days" };
  const windowText = () => WINDOW_TEXT[S.windowH] || `last ${S.windowH} hours`;
  function buildSupply() {
    const out = { flows: [], pledges: [] };
    if (!S.data || (S.off.has("crate") && S.off.has("coin"))) return out;
    const since = Date.now() - S.windowH * HOUR;
    const flows = new Map(), pledges = new Map();
    for (const e of S.data.events) {
      const t = e.transfer;
      if (e._archived || e.type !== "arms_transfer" || !t || e._t < since || !theaterShown(e.theater) || !S.statusOn.has(e.status)) continue;
      if (!matches(e)) continue;
      const kind = tkind(e);
      if (kind === "interdiction") continue;
      const money = isMoney(e);
      if (S.off.has(money ? "coin" : "crate")) continue;
      // forces sent to a region (a US command's area) form their own route, labeled with the region
      // (and forces leaving one, like tankers flying home from CENTCOM bases)
      const region = t.to && t.to.region ? t.to.place : null, fromRegion = t.from && t.from.region ? t.from.place : null;
      // a country moving its own forces: one route per pair of named places (US aircraft into Ramstein
      // and US forces leaving Iraq are different moves)
      const ends = own(t) ? `|${(t.from && t.from.place) || ""}>${(t.to && t.to.place) || ""}` : "";
      const key = `${fromRegion ? `${fromRegion}>` : ""}${t.supplier}>${region || t.recipient}${money ? "|aid" : ""}${ends}`;
      const bucket = kind === "pledge" ? pledges : flows;
      if (!bucket.has(key)) bucket.set(key, { key, supplier: t.supplier, recipient: t.recipient, toLabel: region, fromLabel: fromRegion, own: own(t), money, events: [] });
      bucket.get(key).events.push(e);
    }
    const summarize = (f) => {
      const ev = f.events.sort((a, b) => b._t - a._t);
      const top = (get) => {
        const m = new Map();
        ev.forEach((x) => { const v = get(x); if (v) { const k = v.place || `${v.lat},${v.lon}`; m.set(k, { v, n: (m.get(k) || { n: 0 }).n + 1 }); } });
        return ([...m.values()].sort((a, b) => b.n - a.n)[0] || {}).v || null;
      };
      f.from = top((e) => e.transfer.from);
      f.to = top((e) => e.transfer.to);
      f.via = (ev.find((e) => (e.transfer.via || []).length) || { transfer: { via: [] } }).transfer.via || [];
      f.deliveries = ev.reduce((n, e) => n + Math.max(1, e.transfer.flights || 1), 0);
      f.value = ev.reduce((n, e) => n + (e.transfer.value_usd || 0), 0);
      f.modes = [...new Set(ev.map((e) => e.transfer.mode).filter((m) => m && m !== "unspecified"))];
      const cargo = new Map();
      ev.forEach((e) => { if (e.transfer.what) cargo.set(e.transfer.what, (cargo.get(e.transfer.what) || 0) + 1); });
      f.cargo = [...cargo.entries()].sort((a, b) => b[1] - a[1]).map(([w]) => w);
      f.status = bestStatus(ev);
      f.last = ev[0]._t;
      f.active = Date.now() - f.last < S.windowH * HOUR;
      const bins = new Array(15).fill(0), binMs = (S.windowH * HOUR) / 15;
      ev.forEach((e) => { const i = 14 - Math.floor((Date.now() - e._t) / binMs); if (i >= 0 && i < 15) bins[i] += Math.max(1, e.transfer.flights || 1); });
      f.bins = bins;
      return f;
    };
    out.flows = joinRoutes([...flows.values()].map(summarize), summarize).sort((a, b) => (b.active - a.active) || b.deliveries - a.deliveries);
    out.pledges = joinRoutes([...pledges.values()].map(summarize), summarize).sort((a, b) => b.last - a.last);
    return out;
  }

  // One line per move. The same move gets reported with different spellings or detail: B-1s leaving
  // "RAF Fairford" and "Fairford", landing in "South Dakota", "the United States", or nowhere named
  // (2026-10-05: four lines out of Fairford). Routes of the same kind between the same sides join when
  // they start within ROUTE_JOIN_KM of each other and end within it, or one end is just the country
  // (or unnamed) while the other names a place; or when neither names a start and they end together.
  const ROUTE_JOIN_KM = 150;
  function joinRoutes(list, summarize) {
    const near = (a, b) => a && b && km(a.lat, a.lon, b.lat, b.lon) < ROUTE_JOIN_KM;
    const broad = (v, cc) => !v || (!v.region && (!v.place || v.place === countryName(cc)));
    const sameEnd = (a, b, cc) => near(a, b) || broad(a, cc) || broad(b, cc);
    const out = [];
    for (const f of list) {
      const g = out.find((o) => o.supplier === f.supplier && o.recipient === f.recipient && o.money === f.money
        && o.toLabel === f.toLabel && o.fromLabel === f.fromLabel
        // at least one end must actually match: two moves that name nothing aren't the same move
        && ((near(o.from, f.from) && sameEnd(o.to, f.to, f.recipient))
          || (broad(o.from, o.supplier) && broad(f.from, f.supplier) && near(o.to, f.to))));
      if (!g) { out.push(f); continue; }
      g.events.push(...f.events);
      const specific = (v, cc) => (broad(v, cc) ? null : v);
      const keepTo = specific(g.to, g.recipient) || specific(f.to, f.recipient);
      const keepFrom = specific(g.from, g.supplier) || specific(f.from, f.supplier);
      if (f.events.length > g.events.length - f.events.length) g.key = f.key;   // the bigger one names it
      summarize(g);
      g.to = specific(g.to, g.recipient) || keepTo || g.to;     // a named place beats "the United States"
      g.from = specific(g.from, g.supplier) || keepFrom || g.from;
    }
    return out;
  }

  // ------------------------------------------------------------------ carriers
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
  // On the globe: carriers at sea, carriers that moved this week (so a return home is visible), and the one you selected.
  const carrierOnMap = (c) => S.region !== null && S.layers.carriers && (!c.at_home || (c._moved && Date.now() - c._moved < 7 * DAY) || c.hull === S.selectedHull);
  const sailed = new Set();
  let sailing = false;
  function sailRecentMoves() {
    if (reduceMotion) return;
    const movers = S.fleet.filter((c) => carrierOnMap(c) && c.prev && c._moved && Date.now() - c._moved < 7 * DAY && !sailed.has(c.hull + c.as_of));
    if (!movers.length) return;
    movers.forEach((c) => { sailed.add(c.hull + c.as_of); c._from = { lat: c.prev.lat, lon: c.prev.lon }; c._to = { lat: c._lat, lon: c._lon }; });
    const t0 = performance.now(), dur = 4200;
    const ease = (t) => (t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2);
    sailing = true;
    const step = (now) => {
      const t = Math.min(1, (now - t0) / dur);
      movers.forEach((c) => { const p = slerp(c._from, c._to, ease(t)); c.lat = p.lat; c.lon = p.lon; });
      world.htmlElementsData(S.html);
      queueDeclutter();
      if (t < 1) requestAnimationFrame(step);
      else { sailing = false; movers.forEach((c) => { c.lat = c._lat; c.lon = c._lon; }); render(); }
    };
    requestAnimationFrame(step);
  }

  // ------------------------------------------------------------------ build layers
  // Each 25 reported launches adds another faint dashed path, within the device budget.
  // Repeated paths convey volume from known origins; they are not observed flight tracks.
  const LAUNCHED = new Set(["missile_drone", "air_defense", "airstrike"]);   // kinds drawn with launch lines
  const MAX_BARRAGE_LINES = PHONE ? 8 : 40;   // per attack
  const MAX_LAUNCH_PATHS = PHONE ? 24 : 180;  // hard limit across all attacks
  // A reported launch count adds one visual path per 25 items, capped to keep busy maps readable.
  const launchPathCount = (e) => Number.isFinite(Number(e.launched)) && Number(e.launched) > 0
    ? clamp(Math.ceil(Number(e.launched) / 25), 1, MAX_BARRAGE_LINES) : 1;
  function attackPaths(events) {
    const arcs = [];
    if (!S.layers.paths) return arcs;
    const push = (e, o, d, approx, barrage, line = 0) => {
      if (arcs.length >= MAX_LAUNCH_PATHS) return;
      const dist = km(o.lat, o.lon, d.lat, d.lon);
      if (dist < 25 || (approx && dist > 1800)) return;
      const dim = dimOf(e.id === S.selectedId, true);
      const a = Math.min(1, STATUS[e.status].alpha * fade(e) * (approx ? 0.65 : 1.15)) * dim;
      const pathKey = `atk|${e.id}|${o.lat},${o.lon}>${d.lat},${d.lon}`;
      arcs.push(keyed({ ref: e, sLat: o.lat, sLng: o.lon, eLat: d.lat, eLng: d.lon, kind: barrage ? "barrage" : approx ? "strikeApprox" : "strike",
        color: [rgba(CAT_RGB.strike, 0.12 * dim), rgba(CAT_RGB.strike, a)], stroke: barrage ? 0.3 : approx ? 0.26 : 0.42,
        ms: barrage ? 2600 + Math.random() * 1600 : approx ? 3600 : 2200, seed: (Math.random() + line * 0.618) % 1, dist, barrage: !!barrage },
        barrage ? `${pathKey}|${line}` : pathKey));
    };
    // Repeated dashed paths show reported volume; they remain approximate routes, not observed tracks.
    const assumed = (e, d, count = launchPathCount(e), first = 0, barrage = count > 1) => {
      const anchors = launchAreas(e);
      const from = nearestN(anchors, d, Math.min(count, anchors.length));
      if (!from.length) return;
      for (let i = 0; i < count && arcs.length < MAX_LAUNCH_PATHS; i++) push(e, from[i % from.length], d, true, barrage, first + i);
    };
    // An opened event keeps its paths even when the phone's small budget is full.
    const selected = PHONE && events.find((e) => e.id === S.selectedId);
    const ordered = selected ? [selected, ...events.filter((e) => e !== selected)] : events;
    for (const e of ordered) {
      if (arcs.length >= MAX_LAUNCH_PATHS) break;
      // Drone/missile waves and airstrikes can use configured nearby launch areas when none are named.
      if (!LAUNCHED.has(e.type) && !originsOf(e).length) continue;
      // A launch "from Yemen" names no site: skip the country center and use known Houthi areas instead.
      const origins = originsOf(e).filter((o) => !wholeCountry(o, e.attacker));
      const start = arcs.length;
      if (e.wave) {
        const targets = (e.targets.length ? e.targets.slice(0, 16) : [e]);
        const total = Math.min(MAX_BARRAGE_LINES, MAX_LAUNCH_PATHS - arcs.length, targets.length + Math.max(0, launchPathCount(e) - 1));
        const barrage = launchPathCount(e) > 1;
        for (let i = 0; i < total; i++) {
          // Sample actual reported targets across the wave when the phone cap is smaller.
          const d = targets[Math.floor(i * targets.length / Math.min(total, targets.length)) % targets.length];
          const o = nearest(origins, d);
          if (o) push(e, o, d, false, barrage, i);
          else assumed(e, d, 1, i, barrage);
        }
      } else if (origins.length) {
        const named = nearestN(origins, e, Math.min(3, origins.length));
        const total = Math.min(MAX_BARRAGE_LINES, MAX_LAUNCH_PATHS - arcs.length, named.length + Math.max(0, launchPathCount(e) - 1));
        const barrage = launchPathCount(e) > 1;
        for (let i = 0; i < total; i++) push(e, named[i % named.length], e, false, barrage, i);
      } else if (e.type === "missile_drone" || e.type === "airstrike") assumed(e, e);
      if (arcs.length - start > MAX_BARRAGE_LINES) arcs.length = start + MAX_BARRAGE_LINES;
    }
    return arcs;
  }

  // Long routes are drawn as a chain of short segments that follow the surface, so they
  // never bulge off the edge of the globe.
  function surfaceArcs(a, b, base, lift) {
    const dist = km(a.lat, a.lon, b.lat, b.lon);
    const n = Math.max(1, Math.ceil(dist / 1300));
    const out = [];
    let prev = a;
    for (let i = 1; i <= n; i++) {
      const p = i === n ? b : slerp(a, b, i / n);
      const k = base._k && `${base._k}|${i}`;
      out.push(keyed({ ...base, _k: k, sLat: prev.lat, sLng: prev.lon, eLat: p.lat, eLng: p.lon, alt: hugAlt(km(prev.lat, prev.lon, p.lat, p.lon)) + lift }, k));
      prev = p;
    }
    return out;
  }

  const flowRgb = (f) => f.money ? CAT_RGB.aid : [76, 159, 255];
  function supplyArcs(flows) {
    const arcs = [];
    for (const f of flows) {
      const named = f.from && f.to && !f.to.region && !f.from.region; // a route to or from a whole region is drawn faint
      const start = f.from || countryCenter(f.supplier), end = f.to || countryCenter(f.recipient);
      if (!start || !end) continue;
      const pts = [start, ...(named ? f.via : []), end];
      const stroke = clamp(0.22 + 0.2 * Math.log2(1 + f.deliveries), 0.22, 1.1);
      const alpha = (named ? 0.75 : 0.4) * (S.selectedFlow === f.key ? 1.3 : 1) * dimOf(S.selectedFlow === f.key, true);
      const sea = f.modes.length === 1 && f.modes[0] === "sea";
      for (let i = 0; i < pts.length - 1; i++) {
        const a = pts[i], b = pts[i + 1];
        if (km(a.lat, a.lon, b.lat, b.lon) < 25) continue;
        const lift = sea ? 0.002 : 0.012;
        arcs.push(...surfaceArcs(a, b, { _k: `sup|${f.key}|${i}`, flow: f, kind: f.status === "corroborated" ? "flow" : "flowDashed", color: rgba(flowRgb(f), Math.min(1, alpha)), stroke, ms: PHONE ? 1600 : 2200, seed: 0 }, lift));
      }
    }
    return arcs;
  }

  // Routes use short moving dashes; opening one adds a single brighter pass along its segments.
  // No separate solid route or particle layer, and no pulse for aid that is only pledged.
  let routeTimers = [], routeBurst = null;
  const routeShots = new Set();
  function stopRouteBurst() {
    routeTimers.forEach(clearTimeout);
    routeTimers = [];
    routeBurst = null;
    if (!routeShots.size) return;
    routeShots.forEach((a) => extraArcs.delete(a));
    routeShots.clear();
    pushArcs();
  }
  function playRouteBurst(f, flight = 0) {
    stopRouteBurst();
    if (reduceMotion || !f.active || S.off.has(f.money ? "coin" : "crate")
        || !S.supply.flows.some((x) => x.key === f.key)) return;
    const arcs = supplyArcs([f]);
    const distances = arcs.map((a) => km(a.sLat, a.sLng, a.eLat, a.eLng));
    const distance = distances.reduce((sum, d) => sum + d, 0);
    if (!arcs.length || !(distance > 0)) return;
    routeBurst = f;
    const duration = PHONE ? 1600 : 2200;
    const lead = Math.max(150, flight - 100);
    const rgb = flowRgb(f);
    const named = f.from && f.to && !f.from.region && !f.to.region;
    const alpha = STATUS[f.status].alpha * (named ? 1 : 0.6);
    let offset = 0;
    arcs.forEach((a, i) => {
      const ms = duration * distances[i] / distance;
      routeTimers.push(setTimeout(() => {
        if (S.selectedFlow !== f.key || S.off.has(f.money ? "coin" : "crate")) return;
        const shot = { ...a, kind: "routeShot", ms, seed: 1,
          color: [rgba(rgb, 0.2 * alpha), rgba(rgb, alpha)], stroke: Math.max(0.45, a.stroke * 0.9) };
        routeShots.add(shot);
        extraArcs.add(shot);
        pushArcs();
        routeTimers.push(setTimeout(() => {
          routeShots.delete(shot);
          extraArcs.delete(shot);
          pushArcs();
        }, ms * 1.04));
      }, lead + offset));
      offset += ms;
    });
    routeTimers.push(setTimeout(stopRouteBurst, lead + duration * 1.04 + 50));
  }

  // ------------------------------------------------------------------ sea lanes for carrier lines
  // A carrier line between two points is drawn along the usual sea lanes (open ocean, straits,
  // canals a carrier can use), not straight over land: San Diego to the Arabian Sea crosses the
  // Pacific and goes through Malacca, not over Russia. Carriers can't use the Panama Canal. The
  // route shows the likely way, not a reported track.
  const SEA = {
    SD: [32.3, -118.5], CAL: [37, -124.5], PNW: [48.3, -125.5], NEP: [42, -140], HAW: [20.5, -158.5],
    GUAM: [13.3, 144.3], YOK: [34.5, 140.2], JPS: [31, 135], PHS: [18, 132], ECS: [29, 125.5], KOR: [33.5, 128.5],
    LUZ: [20.8, 121.5], SCSN: [18, 115.5], SCS: [12, 113], SING: [1.8, 105.2], SSTR: [1.2, 103.8],
    MALS: [2.5, 101], MALN: [6.2, 97.2], SRI: [5.2, 81], DIEGO: [-7, 72.5], ARB: [15, 63], OMN: [21, 61],
    GOM: [25.3, 57.6], HOR: [26.55, 56.65], PGE: [26.2, 54], PG: [27.2, 51.2], ADEN: [12.8, 48.5],
    BAB: [12.6, 43.3], RSS: [16, 41.5], RSN: [26, 35.2], SUEZ: [29.8, 32.6], PSAID: [31.8, 32.3],
    EMED: [33.8, 28], CRETE: [34.6, 23], IONIAN: [36, 17], SICILY: [36.8, 11.2], WMED: [38.2, 5],
    GIB: [35.95, -5.8], ATLE: [38, -15], FIN: [44, -10.5], CHAN: [49.8, -3], DOV: [51.1, 1.6], NSEA: [57, 3],
    AZO: [38.5, -28], NOR: [36.7, -74.5], WAF1: [10, -20], WAF2: [-12, 2], CAPE: [-36.5, 19], SMAD: [-28, 47],
  };
  const SEA_LANES = ("SD-CAL CAL-PNW PNW-NEP NEP-HAW SD-HAW SD-YOK PNW-YOK HAW-GUAM HAW-YOK HAW-PHS GUAM-PHS GUAM-YOK " +
    "PHS-JPS JPS-YOK JPS-ECS ECS-KOR ECS-LUZ PHS-LUZ LUZ-SCSN SCSN-SCS SCS-SING SING-SSTR SSTR-MALS MALS-MALN " +
    "MALN-SRI SRI-ARB SRI-DIEGO DIEGO-ARB ARB-OMN OMN-GOM GOM-HOR HOR-PGE PGE-PG ARB-ADEN ADEN-BAB BAB-RSS " +
    "RSS-RSN RSN-SUEZ SUEZ-PSAID PSAID-EMED EMED-CRETE CRETE-IONIAN IONIAN-SICILY SICILY-WMED WMED-GIB GIB-ATLE " +
    "GIB-AZO ATLE-AZO AZO-NOR ATLE-FIN FIN-CHAN CHAN-DOV DOV-NSEA ATLE-WAF1 WAF1-WAF2 WAF2-CAPE CAPE-SMAD SMAD-DIEGO")
    .split(" ").map((p) => p.split("-"));
  const seaCache = new Map();
  // A coarse land map (half a degree), painted once from the same country shapes as the globe, to
  // tell whether a straight line stays at sea.
  let landMask = null;
  function isLand(lat, lon) {
    if (!landMask) {
      if (!landShapes.length) return false;
      const c = document.createElement("canvas");
      c.width = 720; c.height = 360;
      const g = c.getContext("2d", { willReadFrequently: true });
      g.fillStyle = "#000"; g.fillRect(0, 0, 720, 360); g.fillStyle = "#fff";
      for (const f of landShapes) {
        g.beginPath();
        for (const poly of f.geometry.coordinates) for (const ring of poly) {
          ring.forEach(([lo, la], i) => (i ? g.lineTo((lo + 180) * 2, (90 - la) * 2) : g.moveTo((lo + 180) * 2, (90 - la) * 2)));
          g.closePath();
        }
        g.fill("evenodd");
      }
      landMask = g.getImageData(0, 0, 720, 360).data;
      seaCache.clear();
    }
    const x = Math.min(719, Math.max(0, Math.floor(((((lon + 180) % 360) + 360) % 360) * 2))), y = Math.min(359, Math.max(0, Math.floor((90 - lat) * 2)));
    return landMask[(y * 720 + x) * 4] > 127;
  }
  // Does the straight (great-circle) line from a to b stay at sea? The first and last 80 km are
  // not checked: ports and coasts sit next to land on a map this coarse.
  function atSea(a, b) {
    const d = km(a.lat, a.lon, b.lat, b.lon), n = Math.ceil(d / 50);
    for (let i = 1; i < n; i++) {
      if (d * (i / n) < 80 || d * (1 - i / n) < 80) continue;
      const p = slerp(a, b, i / n);
      if (isLand(p.lat, p.lon)) return false;
    }
    return true;
  }
  // Points from a to b along the sea lanes (a and b included). A line that stays at sea is drawn
  // straight; otherwise each end joins the network at the waypoints it can reach at sea.
  function seaPath(a, b) {
    const direct = [a, b];
    const d0 = km(a.lat, a.lon, b.lat, b.lon);
    if (d0 < 300) return direct;
    // until the country shapes load there is no land map: draw straight, remember nothing
    if (!landShapes.length) return direct;
    const key = [a.lat, a.lon, b.lat, b.lon].map((x) => x.toFixed(2)).join(",");
    if (seaCache.has(key)) return seaCache.get(key);
    if (atSea(a, b)) { seaCache.set(key, direct); return direct; }
    const pt = (n) => ({ lat: SEA[n][0], lon: SEA[n][1] });
    const adj = new Map(Object.keys(SEA).map((n) => [n, []]));
    for (const [x, y] of SEA_LANES) {
      const d = km(SEA[x][0], SEA[x][1], SEA[y][0], SEA[y][1]);
      adj.get(x).push([y, d]); adj.get(y).push([x, d]);
    }
    // each end joins the network at every waypoint it can reach at sea (or its three nearest)
    const near = (p) => {
      const all = Object.keys(SEA).map((n) => [n, km(p.lat, p.lon, SEA[n][0], SEA[n][1])]).sort((u, v) => u[1] - v[1]);
      const open = all.filter(([n]) => atSea(p, { lat: SEA[n][0], lon: SEA[n][1] }));
      return open.length ? open : all.slice(0, 3);
    };
    adj.set("A", near(a)); near(b).forEach(([n, d]) => adj.get(n).push(["B", d]));
    const dist = new Map([["A", 0]]), prev = new Map(), done = new Set();
    while (true) {
      let u = null;
      for (const [n, d] of dist) if (!done.has(n) && (u === null || d < dist.get(u))) u = n;
      if (u === null || u === "B") break;
      done.add(u);
      for (const [v, w] of adj.get(u) || []) {
        const nd = dist.get(u) + w;
        if (!dist.has(v) || nd < dist.get(v)) { dist.set(v, nd); prev.set(v, u); }
      }
    }
    let path = direct;
    if (prev.has("B")) {
      const nodes = [];
      for (let n = prev.get("B"); n !== "A"; n = prev.get(n)) nodes.unshift(n);
      path = [a, ...nodes.map(pt), b];
    }
    seaCache.set(key, path);
    return path;
  }
  const alongSea = (a, b, base, lift) => {
    const pts = seaPath(a, b), out = [];
    for (let i = 0; i < pts.length - 1; i++) out.push(...surfaceArcs(pts[i], pts[i + 1], base._k ? { ...base, _k: `${base._k}|${i}` } : base, lift));
    return out;
  };

  function fleetArcs() {
    const arcs = [];
    for (const c of S.fleet) {
      if (!carrierOnMap(c)) continue;
      const dim = dimOf(c.hull === S.selectedHull);
      // where it came from: faint and still
      if (c.prev && c._moved && Date.now() - c._moved < 14 * DAY && km(c.prev.lat, c.prev.lon, c._lat, c._lon) > 100) {
        arcs.push(...alongSea({ lat: c.prev.lat, lon: c.prev.lon }, { lat: c._lat, lon: c._lon },
          { _k: `cvn|${c.hull}|track`, carrier: c, kind: "track", color: rgba(CAT_RGB.fleet, 0.28 * dim), stroke: 0.2, ms: 0, seed: 0 }, 0.002));
      }
      // where it is headed: dashes flow from the last reported position toward the stated destination
      if (c.heading_to && km(c._lat, c._lon, c.heading_to.lat, c.heading_to.lon) > 100) {
        arcs.push(...alongSea({ lat: c._lat, lon: c._lon }, c.heading_to,
          { _k: `cvn|${c.hull}|plan`, carrier: c, kind: "plan", color: rgba(CAT_RGB.fleet, 0.7 * dim), stroke: 0.3, ms: 6000, seed: 0 }, 0.002));
      }
    }
    return arcs;
  }

  // With more events than markers (a busy week), keep the most serious and best-confirmed ones and
  // the last few hours', not just the newest: a corroborated pipeline sabotage from two days ago
  // dropped off the map behind a day of minor reports. Diplomacy discs rank a step lower than
  // incidents. The feed says how many aren't drawn (mapLeftOut).
  const CONF_RANK = { corroborated: 2, unconfirmed: 1, claimed: 0 };
  let mapLeftOut = 0;
  function markerPick(evs) {
    mapLeftOut = Math.max(0, evs.length - MAX_MARKERS);
    if (!mapLeftOut) return evs;
    const now = Date.now();
    const score = (e) => 2 * e.severity + CONF_RANK[e.status] + (now - e._t < 6 * HOUR ? 2 : 0) - (isDiplomacy(e) ? 1 : 0);
    const keep = new Set([...evs].sort((a, b) => score(b) - score(a) || b._t - a._t).slice(0, MAX_MARKERS).map((e) => e.id));
    if (S.selectedId) keep.add(S.selectedId);
    return evs.filter((e) => keep.has(e.id));
  }

  // ------------------------------------------------------------------ render
  // A render has two halves: the lists (feed, counts, tally, side lists), and the globe (markers,
  // lines, dots, rings, land). render() does both at once. The controls (time window, "On the
  // map", theaters, confidence, search) use renderSoon(): the pressed button paints first, the
  // lists on the next frame and the globe on the one after, so a click answers at once even when
  // the globe takes a moment on a phone. A newer click or a full render supersedes a pending one.
  function frameData() {
    const events = visibleEvents();
    const onGlobe = events.filter(onMap);
    const mapEvents = markerPick(onGlobe);
    S.supply = buildSupply();
    return { events, mapEvents };
  }
  function renderLists({ events }) {
    renderCounts();
    renderTally(events);
    renderSideLists();
    if ($("#detail").hidden) renderFeed(events);
  }
  let renderGen = 0;
  const afterPaint = (fn) => requestAnimationFrame(() => setTimeout(fn, 0));
  function renderSoon() {
    if (!S.data) return;
    const gen = ++renderGen;
    afterPaint(() => {
      if (gen !== renderGen) return;
      renderLists(frameData());
      afterPaint(() => { if (gen === renderGen) renderGlobe(frameData()); });
    });
  }
  function render() {
    if (!S.data) return;
    renderGen++;  // anything pending is covered by this
    const d = frameData();
    renderGlobe(d);
    renderLists(d);
  }
  function renderGlobe({ events, mapEvents }) {
    if (S.selectedId && !events.some((e) => e.id === S.selectedId)) stopLaunches();
    if (routeBurst && !S.supply.flows.some((f) => f.key === routeBurst.key)) stopRouteBurst();
    newFrame();
    document.body.classList.toggle("focus", focused());
    document.body.classList.toggle("region-focused", !!selectedRegion());

    // HTML markers: events (labels on the most important, and on alert groups), carriers
    const labelled = new Set(mapEvents.filter((e) => e.severity >= 3 || e.wave).sort((a, b) => b.severity - a.severity || b._t - a._t).slice(0, 5).map((e) => e.id));
    mapEvents.forEach((e) => { if (e.alert) labelled.add(e.id); });
    const animated = new Set(mapEvents.filter((e) => isLive(e) && catOf(e)[2]).slice(0, MAX_ANIMATED_NOW).map((e) => e.id));
    const html = mapEvents.map((e) => eventMarker(e, labelled.has(e.id), animated.has(e.id)));
    S.fleet.filter(carrierOnMap).forEach((c) => html.push(carrierMarker(c)));
    S.html = html;
    if (!sailing) world.htmlElementsData(html);

    // wave target dots (the main target carries the icon); a selected alert group shows its places faintly
    const dots = [];
    for (const e of mapEvents) {
      const alert = !!e.alert && e.id === S.selectedId;
      if (!e.wave && !alert) continue;
      e.targets.slice(0, 40).forEach((t, i) => {
        if (Math.abs(t.lat - e.lat) < 1e-4 && Math.abs(t.lon - e.lon) < 1e-4) return;
        dots.push(stable("dots", `${e.id}|${i}|${alert}`, { lat: t.lat, lon: t.lon, place: t.place, ref: e, alert }));
      });
    }
    world.pointsData(dots);

    // rings: impacts at recent wave targets, the selected item
    const rings = [];
    if (!reduceMotion) {
      for (const e of mapEvents) {
        if (!e.wave || !isLive(e)) continue;
        e.targets.slice(0, 12).forEach((t, i) => {
          if (rings.length >= 30) return;
          rings.push(stable("rings", `wave|${e.id}|${i}`, { lat: t.lat, lon: t.lon, rgb: CAT_RGB.strike, alpha: 0.55 * dimOf(e.id === S.selectedId, true), max: 1.6, speed: 1.2, period: 1500 + Math.random() * 1500 }));
        });
      }
    }
    const sel = S.selectedId && events.find((e) => e.id === S.selectedId);
    if (sel) rings.push(stable("rings", `sel|${sel.id}`, { lat: sel.lat, lon: sel.lon, rgb: ACCENT, alpha: 0.9, max: 4, speed: reduceMotion ? 0 : 2.2, period: 1200 }));
    const selC = S.selectedHull && S.fleet.find((c) => c.hull === S.selectedHull);
    if (selC) rings.push(stable("rings", `selc|${selC.hull}`, { lat: selC._lat, lon: selC._lon, rgb: ACCENT, alpha: 0.9, max: 4, speed: reduceMotion ? 0 : 2.2, period: 1200 }));
    baseRings = rings;
    pushRings();

    const routeArcs = [...supplyArcs(S.supply.flows), ...(S.layers.carriers ? fleetArcs() : [])];
    baseArcs = [...attackPaths(mapEvents), ...routeArcs, ...hitArcs(routeArcs)];
    pushArcs();
    updateActive(mapEvents);
    queueDeclutter();
    if (!hoverLight) requestAnimationFrame(() => setTimeout(lightenHover, 0));  // once the new objects exist
    if (!touchQuiet) requestAnimationFrame(() => setTimeout(quietTouchHover, 0));
  }

  // Who drew the control shapes, and when: credited under the legend, with a link to the source's map.
  function renderControlNote() {
    const el = $("#controlNote");
    const layers = controlLayers().filter((L) => !L.assessment);  // the site's own areas need no note
    el.hidden = !layers.length;
    if (el.hidden) return;
    const bySource = new Map();
    const traced = layers.filter((L) => L.approx && !L.assessment);
    for (const L of layers.filter((x) => !x.approx && !x.assessment)) {
      const s = bySource.get(L.source) || bySource.set(L.source, { link: L.link, asOf: "", labels: [] }).get(L.source);
      if (L.as_of && L.as_of > s.asOf) s.asOf = L.as_of;
      s.labels.push(L.label);
    }
    const day = (iso) => new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", timeZone: "UTC" });
    let names = null;
    try { names = new Intl.DisplayNames(["en"], { type: "region" }); } catch (_) { /* older browsers: codes */ }
    const country = (c) => (c ? (names ? names.of(c) : c) : "");
    const exact = layers.filter((L) => !L.approx && !L.assessment);
    const where = [...new Set(exact.map((L) => L.country).filter(Boolean))].map(country).join(", ");
    const hatched = exact.some((L) => L.style === "infiltration");
    const parts = [];
    if (exact.length) parts.push(`Territorial control${where ? ` in ${esc(where)}` : ""}: ` + [...bySource].map(([source, s]) =>
      `the <a href="${esc(s.link || "#")}" target="_blank" rel="noopener">${esc(source)}</a> assessment${s.asOf ? `, last edited ${esc(day(s.asOf))}` : ""}`).join("; ")
      + `. The shapes are theirs, simplified${hatched ? "; hatched areas have forces present but not in control" : ""}.`);
    for (const L of traced) parts.push(`${esc(L.label)}${L.country ? ` ${esc(country(L.country))}` : ""} (dashed edge) is approximate: traced by this site from the `
      + `<a href="${esc(L.link || "#")}" target="_blank" rel="noopener">${esc(L.source || "source")} map</a>${L.as_of ? ` of ${esc(day(L.as_of))}` : ""}, which is published only as a picture.`);
    el.innerHTML = parts.join(" ");
  }

  function updateActive(events) {
    const active = new Set();
    for (const e of events) {
      if (isDiplomacy(e) || e.type === "arms_transfer") continue;
      [e.country, e.attacker].forEach((c) => { const n = ISO_NUM.get(c); if (n) active.add(n); });
    }
    const key = [...active].sort().join(",") + "|" + [...S.hot].sort().join(",");
    if (key !== S.activeKey) {
      S.activeKey = key;
      S.active = active;
      if (borderLines) colorBorders();
      else world.pathColor(pathColorOf);
    }
    paintLand();  // repaints only when active or highlighted countries changed (or the control layer)
    renderControlNote();
  }

  // The running UTC clock in the header.
  function tickClock() {
    const d = new Date(), p = (n) => String(n).padStart(2, "0");
    const el = document.getElementById("utcClock");
    if (el) el.textContent = `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}:${p(d.getUTCSeconds())} UTC`;
  }
  tickClock();
  setInterval(tickClock, 1000);

  let tallyWas = null;
  function renderTally(events) {
    const span = { 6: "6 hours", 24: "24 hours", 72: "3 days", 168: "7 days" }[S.windowH];
    const fighting = events.filter(onMap);
    const now = [fighting.length, fighting.filter((e) => e.status === "corroborated").length];
    $("#tally").innerHTML = `<span class="stat"><strong>${now[0]}</strong><span>Mapped events <small>${span}</small></span></span><span class="stat"><strong>${now[1]}</strong><span>Corroborated <small>Independent evidence</small></span></span>`;
    // counts tick up or down to their new values
    if (tallyWas && !reduceMotion && (tallyWas[0] !== now[0] || tallyWas[1] !== now[1])) {
      const els = [...$("#tally").querySelectorAll("strong")], from = tallyWas.slice(), t0 = performance.now();
      els.forEach((el) => el.classList.add("ticked"));
      const run = (t) => {
        const k = Math.min(1, (t - t0) / 650), e = 1 - Math.pow(1 - k, 3);
        els.forEach((el, i) => { el.textContent = String(Math.round(from[i] + (now[i] - from[i]) * e)); });
        if (k < 1) requestAnimationFrame(run); else setTimeout(() => els.forEach((el) => el.classList.remove("ticked")), 300);
      };
      requestAnimationFrame(run);
    }
    tallyWas = now;
  }

  // ------------------------------------------------------------------ feed and side lists
  function itemHtml(e, names) {
    const extra = [];
    if (hasFollowup(e)) extra.push(`Updated ${ago(e._tu)}`);
    if (e.alert) extra.push(alertsText(e));
    if ((e.wave || e.alert) && e.targets.length) extra.push(`${e.targets.length} ${e.targets.length === 1 ? "location" : "locations"}`);
    if (e.wave && e.launched) extra.push(`${e.launched} launched`);
    if (e.legal_basis) extra.push("Legal basis stated");
    return `<li><button class="item sev-${e.severity}${isNew(e) ? " is-new" : ""}${S.arrived && S.arrived.has(e.id) ? " arrive" : ""}" type="button" data-id="${esc(e.id)}" ${e.id === S.selectedId ? 'aria-current="true"' : ""}>
      ${eventIcon(e)}
      <span>
        <span class="item-meta"><span class="item-type">${esc(typeLabel(e))}</span><span class="item-place">${esc(metaLine(e))}</span><time datetime="${esc(e.time)}">${esc(agoShort(e._t))}</time></span>
        <span class="item-summary">${esc(e.summary)}</span>
        <span class="item-foot"><span class="conf-text conf-${STATUS[e.status].conf}">${esc(STATUS[e.status].label)}</span><span>${esc(names[e.theater] || e.theater)}</span>
          ${extra.map((x) => `<span>${esc(x)}</span>`).join("")}<span>${e.sources_count} ${e.sources_count === 1 ? "source" : "sources"}</span></span>
      </span></button></li>`;
  }

  // Regional analysis (pipeline/analyst.py): what is changing in each region, machine-written from
  // the map's own events, independent of the filters. Confidence comes from the cited events.
  const TREND = { escalating: ["▲", "Escalating"], "de-escalating": ["▼", "De-escalating"], shifting: ["◆", "Shifting"], steady: ["●", "Steady"] };
  const CONF_WORDS = { higher: "Higher confidence", moderate: "Moderate confidence", low: "Low confidence" };
  // Keep the overview geographically varied. All judgments remain in the regional disclosure.
  function briefHighlights(regions, byId) {
    const confidence = { higher: 2, moderate: 1, low: 0 };
    const latest = (j) => Math.max(0, ...(j.ids || []).map((id) => byId.get(id)?._t || 0));
    const compare = (a, b) => Number(b.trend !== "steady") - Number(a.trend !== "steady")
      || (confidence[b.confidence] || 0) - (confidence[a.confidence] || 0) || latest(b) - latest(a);
    return regions.flatMap((region) => {
      const judgment = [...(region.judgments || [])].sort(compare)[0];
      return judgment ? [{ region, judgment }] : [];
    }).sort((a, b) => compare(a.judgment, b.judgment)).slice(0, 3);
  }
  function briefHtml() {
    const a = S.data && S.data.analysis;
    if (!a || !a.generated_at || S.region === null) return "";
    const byId = new Map(S.data.events.map((e) => [e.id, e]));
    const cites = (ids) => {
      const found = (ids || []).filter((i) => byId.has(i));
      return found.length ? `<span class="cites">${found.map((i) => {
        const e = byId.get(i);
        const label = (e.place || typeLabel(e)).split(",")[0].slice(0, 22);
        return `<button class="cite" type="button" data-id="${esc(i)}" title="Open: ${esc(e.summary)}">${esc(label)}</button>`;
      }).join("")}</span>` : "";
    };
    const basis = (t) => [t.corroborated && `${t.corroborated} corroborated`, t.single_source && `${t.single_source} single-source`,
      t.claimed && `${t.claimed} one-sided ${t.claimed === 1 ? "claim" : "claims"}`,
      t.tracked && `${t.tracked} tracked ${t.tracked === 1 ? "flight" : "flights"}`].filter(Boolean).join(", ");
    // flights the judgment cites: what each aircraft's transponder showed, linked to it on adsb.lol
    const flown = (list) => (list || []).length ? `<span class="cites">${list.map((f) =>
      `<a class="cite cite-flight" href="${esc(safeUrl(f.url))}" target="_blank" rel="noopener noreferrer" title="${esc(f.what)}">${esc(f.label)}</a>`).join("")}</span>` : "";
    const regions = (a.regions || []).filter((r) => theaterShown(r.theater) && (r.judgments || []).length);
    if (!regions.length && S.region !== "world") return "";
    const scope = selectedRegion()?.name || "All regions";
    const judgmentHtml = (j, compact = false) => {
      const [mark, word] = TREND[j.trend] || TREND.steady;
      return `<div class="an-item trend-${esc(j.trend)}">
        <p class="an-head"><span class="an-trend"><span aria-hidden="true">${mark}</span> ${esc(word)}</span><b>${esc(j.headline)}</b></p>
        ${!compact && j.text ? `<p class="an-text">${esc(j.text)}</p>` : ""}
        <p class="an-foot"><span class="an-conf conf-${esc(j.confidence)}">${esc(CONF_WORDS[j.confidence] || "Low confidence")}</span>${basis(j.tally || {}) ? `<span>Based on ${esc(basis(j.tally || {}))}</span>` : ""}${cites(j.ids)}${flown(j.flights)}</p>
      </div>`;
    };
    const highlights = briefHighlights(regions, byId);
    const lead = highlights.length ? `<ol class="brief-highlights">${highlights.map(({ region, judgment }) => `<li>
      <button class="an-name" type="button" data-fly="${esc(region.theater)}" title="Fly to ${esc(region.name)}">${esc(region.name)}</button>
      ${judgmentHtml(judgment, true)}</li>`).join("")}</ol>`
      : `<p class="an-empty">No clear change in ${esc(scope.toLowerCase())} in the last ${esc(a.window_hours || 6)} hours.</p>`;
    const body = regions.map((r) => `
      <div class="an-region">
        <button class="an-name" type="button" data-fly="${esc(r.theater)}" title="Fly to ${esc(r.name)}">${esc(r.name)}</button>
        ${r.judgments.map((j) => judgmentHtml(j)).join("")}
      </div>`).join("");
    const written = Date.parse(a.generated_at);
    const stale = Number.isFinite(written) && Date.now() - written > 3 * HOUR;
    return `<li class="brief analysis"><section aria-labelledby="briefTitle">
      <div class="brief-head"><h3 id="briefTitle">Situation brief</h3>
        <time datetime="${esc(a.generated_at)}">Written ${esc(ago(Date.parse(a.generated_at)))}</time></div>
      <p class="an-sub">${highlights.length ? `${highlights.length} selected ${highlights.length === 1 ? "development" : "developments"} · ` : ""}${esc(scope)} · last ${esc(a.window_hours || 6)} hours</p>
      <p class="brief-note">Machine-written. Expand the regional analysis for the reasoning.</p>
      ${stale ? '<p class="brief-stale" role="status">This analysis is over 3 hours old. Check the latest events below for newer reporting.</p>' : ""}
      ${lead}
      ${regions.length ? `<details class="brief-disclosure" data-brief-section="regions"${S.briefOpen.has("regions") ? " open" : ""}>
        <summary>${S.region === "world" ? "All regional analysis" : "Regional analysis"} (${regions.reduce((n, r) => n + r.judgments.length, 0)})</summary>${body}</details>` : ""}
      <details class="brief-disclosure brief-method" data-brief-section="method"${S.briefOpen.has("method") ? " open" : ""}>
        <summary>How to read this brief</summary>
        <p class="brief-note">Highlights prioritize changes, then confidence, then the latest cited event, with one development per region. The full regional analysis includes every judgment. The region selection applies to this brief. Its analysis uses its own ${esc(a.window_hours || 6)}-hour window against the ${esc(a.context_days || 3)} days before; time, confidence, type and search filters do not change it.</p>
        <p class="brief-note">Machine-written analysis of this map's own events${a.by ? ` by ${esc(a.by)}` : ""}. Confidence comes from the cited events and tracked flights. Transponders show where aircraft went, not why. Open the evidence before relying on a judgment.</p>
      </details>
      ${a.flight_credit ? `<p class="brief-note"><a href="${esc(safeUrl(a.flight_credit.url || "https://opendatacommons.org/licenses/odbl/1-0/"))}" target="_blank" rel="noopener noreferrer">${esc(a.flight_credit.text || "Flight data: adsb.lol contributors")}</a>.</p>` : ""}
    </section></li>`;
  }

  const FEED_PAGE = 250;  // the list is built in pages; a busy week has 1,000+ events
  function renderFeed(events) {
    const list = $("#feedList");
    const names = Object.fromEntries(S.theaters.map((t) => [t.id, t.name]));
    const top = briefHtml();
    $("#feedCount").textContent = `${events.length}`;
    if (!events.length) {
      if (S.region === null) {
        list.innerHTML = '<li class="empty"><strong>All regions are hidden.</strong>Choose a theater or Worldwide to show events.</li>';
        return;
      }
      list.innerHTML = top + (S.data.events.length
        ? `<li class="empty"><strong>Nothing matches these filters.</strong>Widen the time window, choose Worldwide, or show more event types or confidence levels.</li>`
        : `<li class="empty"><strong>No events in the last 7 days yet.</strong>The pipeline is running. New events appear here as sources report them.</li>`);
      return;
    }
    // The brief above already sums up what matters; the list below it is simply newest first.
    const shown = events.slice(0, S.feedLimit);
    const capNote = mapLeftOut ? `<li class="map-note">The map draws the ${MAX_MARKERS} most serious and best-confirmed of these events (${mapLeftOut} left off). Search, or hide some kinds under “On the map”, to see the rest on the map.</li>` : "";
    const more = events.length - shown.length;
    list.innerHTML = top + capNote + shown.map((e) => itemHtml(e, names)).join("")
      + (more > 0 ? `<li class="more"><button class="linkish" type="button" data-more>Show ${Math.min(FEED_PAGE, more)} more (${more} not shown)</button></li>` : "");
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

  function renderSideLists() {
    // Carrier positions are global context, dimmed in regional focus.
    const fleet = S.region === null ? [] : S.fleet;
    const at = fleet.filter((c) => !c.at_home).length;
    $("#fleetNote").textContent = fleet.length ? `${at} of ${fleet.length} at sea` : "";
    $("#fleetList").innerHTML = fleet.length ? fleet.map((c) => `
      <li><button class="side-row${c.hull === S.selectedHull ? " is-selected" : ""}${c.at_home ? " is-home" : ""}" type="button" data-hull="${esc(c.hull)}">
        ${iconBadge("carrier", "fleet", c.at_home ? "outline" : "solid", "ico-sm")}
        <span class="side-name">${esc(c.short || c.name)}</span>
        <span class="side-meta">${esc(c.heading_to ? `→ ${c.heading_to.place || "en route"}` : c.at_home ? (c.place || "").split(/[,(]/)[0].trim() : c.place || "")}</span>
      </button></li>`).join("")
      : S.region === null ? '<li class="muted small">Regions are hidden. Choose a theater or Worldwide to show positions.</li>'
        : '<li class="muted small">No positions yet. They come from USNI News\u2019 daily Fleet and Marine Tracker.</li>';
    // supply routes
    const rows = [];
    S.supply.flows.forEach((f) => rows.push(`
      <li><button class="side-row${S.selectedFlow === f.key ? " is-selected" : ""}" type="button" data-flow="${esc(f.key)}">
        <span class="flow-dot${f.money ? " is-money" : ""}${f.active ? " is-active" : ""}${f.status === "corroborated" ? "" : " is-dashed"}" aria-hidden="true"></span>
        <span class="side-name">${esc(flowFrom(f))} → ${esc(flowTo(f))}</span>
        <span class="side-meta">${f.deliveries}${f.active ? ' <b class="live">active</b>' : ""}</span>
      </button></li>`));
    S.supply.pledges.forEach((f) => rows.push(`
      <li><button class="side-row" type="button" data-flow="${esc(f.key)}" data-pledge="1">
        <span class="flow-dot flow-dot--pledge${f.money ? " is-money" : ""}" aria-hidden="true"></span>
        <span class="side-name">${esc(flowFrom(f))} → ${esc(flowTo(f))}</span>
        <span class="side-meta">pledged</span>
      </button></li>`));
    $("#supplyList").innerHTML = rows.join("") || `<li class="muted small">No transfers reported in the ${windowText()}.</li>`;
    const note = $("#supplyNote");
    if (note) note.textContent = windowText();
  }

  function syncRegionControls() {
    const region = selectedRegion();
    S.hot = new Set((S.region === "world" ? S.theaters : region ? [region] : []).flatMap((t) => t.highlight || []));
    document.querySelectorAll("[data-region]").forEach((b) => {
      const active = b.dataset.region === (S.region === null ? "none" : S.region);
      b.setAttribute("aria-pressed", String(active));
    });
    $("#regionFocus").hidden = !region;
    $("#regionName").textContent = region ? region.name : "";
  }

  function renderTheaters() {
    // Unlisted worldwide commitments appear only in Worldwide, not in a regional focus.
    $("#theaterList").innerHTML = S.theaters.filter((t) => t.listed !== false).map((t) => `
      <li><button class="region-btn" type="button" data-region="${esc(t.id)}" aria-pressed="${S.region === t.id}" aria-label="Focus on ${esc(t.name)}">
        <span class="region-mark" aria-hidden="true"></span><span class="region-label">${esc(t.name)}</span>
        <span class="spark" data-spark="${esc(t.id)}" aria-hidden="true"></span><span class="count" data-count="${esc(t.id)}" aria-hidden="true"></span>
      </button></li>`).join("");
    syncRegionControls();
  }

  function selectRegion(id) {
    const region = id === "none" ? null : id;
    const theater = S.theaters.find((t) => t.id === region && t.listed !== false);
    if (region !== null && region !== "world" && !theater) return;
    S.region = region;
    S.lastFocus = null;
    closeFly();
    hotEvent(null);
    syncRegionControls();
    closeDetail();  // stops event/route playback, clears the share hash, and returns to the list
    $("#feedList").scrollTop = 0;
    $("#focusToast").hidden = true;
    const camera = theater ? theater.camera : region === "world" ? { lat: 25, lng: 10, altitude: 2.6 } : null;
    if (camera) {
      const altitude = isMobile() ? camera.altitude + 0.5 : camera.altitude;
      world.pointOfView({ ...camera, altitude }, reduceMotion ? 0 : flyMs(camera.lat, camera.lng, altitude));
    }
    if (isMobile()) toggleFilters(false);
  }

  function renderCounts() {
    if (!S.data) return;
    const counts = {};
    for (const e of S.data.events) if (onMap(e) && passes(e, true)) counts[e.theater] = (counts[e.theater] || 0) + 1;
    document.querySelectorAll("[data-count]").forEach((el) => { el.textContent = counts[el.dataset.count] || 0; });
    const now = Date.now(), tempo = {};
    for (const e of S.data.events) {
      if (e._archived || !onMap(e)) continue;
      const idx = 6 - Math.floor((now - e._t) / DAY);
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
      if (e._archived || !onMap(e) || e._t < now - S.windowH * HOUR || !theaterShown(e.theater)) continue;
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
    }).join("") + wikiCredits();
  }

  // Wikipedia's conflict maps, read once to fill gaps in the front-line shading where the agents have
  // no evidence (pipeline/frontline/wikipedia.py): credited here, as their licence (CC BY-SA) asks.
  function wikiCredits() {
    const credits = (S.data && S.data.frontline && S.data.frontline.credits) || [];
    const day = (iso) => new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
    return credits.map((c) => `<li><span class="dot quiet" aria-hidden="true"></span><span><span class="name">`
      + `<a href="${esc(safeUrl(c.url))}" target="_blank" rel="noopener noreferrer">Wikipedia: ${esc(c.title)}</a></span>`
      + `<span class="meta">Read once to fill gaps in ${esc(c.name)}'s shaded territory where this site has no evidence of its own`
      + `${c.edited ? `; map last edited ${esc(day(c.edited))}` : ""}; <a href="${esc(safeUrl(c.license_url))}" target="_blank" rel="noopener noreferrer">${esc(c.license)}</a></span></span></li>`).join("");
  }

  function updateFreshness() {
    if (!S.data) return;
    $("#liveTag").hidden = true;
    if (DEMO) { $("#beacon").className = "beacon stale"; $("#freshText").textContent = "Demo data. None of these events are real."; return; }
    const t = Date.parse(S.data.generated_at), age = Date.now() - t;
    $("#liveTag").hidden = age >= 45 * 60e3;   // "Live" only while the data is fresh
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
    $("#detail").querySelectorAll("[data-event]").forEach((b) => b.addEventListener("click", () => select(b.dataset.event, true)));
    if (!isMobile() && !refresh) $("#backBtn").focus({ preventScroll: true });
    if (isMobile() && !refresh) peekDetail();
  }
  // On a phone, opening an event raises the list only as far as its headline and place; drag it
  // up for the rest. A sheet the reader already raised further stays where it is.
  function peekDetail() {
    const feed = $("#feed"), last = $("#detail .detail-where") || $("#detail h3");
    if (!last) { if (S.sheet < 1) setSheet(1); return; }
    const was = feed.getBoundingClientRect().height;
    feed.classList.remove("sheet-collapsed"); // collapsed hides the detail, which then can't be measured
    const need = Math.round(last.getBoundingClientRect().bottom - feed.getBoundingClientRect().top + 14);
    const h = clamp(need, 150, Math.round(window.innerHeight * 0.6));
    if (S.sheet > 0 && was >= h - 4) return;
    S.sheet = 1;
    feed.style.height = h + "px";
    $("#sheetHandle").setAttribute("aria-expanded", "true");
    $("#sheetLabel").textContent = "Collapse the list";
    setTimeout(layout, 320);
  }
  function hideDetail() { $("#detail").hidden = true; $("#feedList").hidden = false; $("#feedHead").hidden = false; }
  function closeDetail() {
    stopLaunches();
    stopRouteBurst();
    S.selectedId = null; S.selectedHull = null; S.selectedFlow = null;
    history.replaceState(null, "", APP_ROOT.pathname + location.search);
    document.title = "Global Situation Monitor";
    hideDetail();
    render();
    if (S.lastFocus) { const again = document.querySelector(`[data-id="${CSS.escape(S.lastFocus)}"]`); if (again) again.focus(); }
  }
  // Camera moves take as long as the trip: a short hop is quick, a flight across the world slower
  // (it was 1.1 s for every move).
  function flyMs(lat, lng, altitude) {
    const pov = world.pointOfView();
    const zoom = Math.abs(Math.log2(Math.max(0.05, altitude) / Math.max(0.05, pov.altitude)));
    return Math.round(clamp(420 + 0.11 * km(pov.lat, pov.lng, lat, lng) + 260 * zoom, 420, 1900));
  }
  const zoomTo = (lat, lng, altitude) => world.pointOfView({ lat, lng, altitude }, reduceMotion ? 0 : flyMs(lat, lng, altitude));

  // The reports behind each event are published apart from the events (pipeline/publish.py), in
  // REPORT_BUCKETS small files chosen by a hash of the event id, and fetched when an event is opened
  // (or pointed at, so they are usually there by the click). Demo data carries them inline.
  const REPORT_BUCKETS = 64;  // pipeline/publish.py: REPORT_BUCKETS
  const bucketOf = (id) => { let h = 0; for (const c of String(id)) h = (Math.imul(h, 31) + c.codePointAt(0)) >>> 0; return h % REPORT_BUCKETS; };
  function reportsFor(e) {
    if (e.reports) return Promise.resolve(e.reports);
    const b = bucketOf(e.id);
    if (!reportFiles.has(b)) {
      const v = encodeURIComponent((S.data && S.data.generated_at) || "");
      reportFiles.set(b, fetch(`data/reports/${String(b).padStart(2, "0")}.json?v=${v}`)
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
        .then((j) => j.reports || {})
        .catch((err) => { reportFiles.delete(b); throw err; }));
    }
    return reportFiles.get(b).then((m) => m[e.id] || null);
  }
  let prefetchTimer = 0;
  const prefetchReports = (id) => {  // once the pointer rests on an event, not for every row it crosses
    clearTimeout(prefetchTimer);
    if (!id || DEMO) return;
    prefetchTimer = setTimeout(() => {
      const e = S.data && S.data.events.find((x) => x.id === id);
      if (e) reportsFor(e).catch(() => {});
    }, 150);
  };
  // Fills the reports section of an open event once they are in.
  function fillReports(e) {
    const slot = $("#reportsSlot");
    if (!slot) return;
    reportsFor(e).then((reps) => {
      if (reps) { if (shownReports.size > 40) shownReports.clear(); shownReports.set(e.id, reps); }
      if (S.selectedId !== e.id || !document.body.contains(slot)) return;
      slot.innerHTML = reps ? reportsHtml(reps, e)
        : '<p class="muted">The reports for this event were just updated. Close it and open it again in a moment.</p>';
    }).catch(() => {
      if (S.selectedId !== e.id || !document.body.contains(slot)) return;
      slot.innerHTML = '<p class="muted">Couldn\u2019t load the reports. <button class="linkish" type="button" id="reportsRetry">Try again</button></p>';
      $("#reportsRetry").addEventListener("click", () => { slot.innerHTML = '<p class="muted">Loading reports\u2026</p>'; fillReports(e); });
    });
  }

  // Report counts and publication order describe the evidence, not independent corroboration.
  const reportTime = (r) => { const t = Date.parse(r.time); return Number.isFinite(t) ? t : 0; };
  function evidenceSummary(reports) {
    const names = new Map();
    for (const r of reports) if (r.source) names.set(r.source, Boolean(r.side) || names.get(r.source) || false);
    return { reports: reports.length, sources: names.size, aligned: [...names.values()].filter(Boolean).length };
  }
  function reportsHtml(reports, e) {
    const info = evidenceSummary(reports);
    const ordered = [...reports].sort((a, b) => reportTime(b) - reportTime(a));
    const dated = ordered.filter(reportTime).reverse();
    const timestamp = (r, exact = false) => reportTime(r)
      ? `<time datetime="${esc(r.time)}" title="${esc(new Date(reportTime(r)).toUTCString())}">${esc(exact ? fmtEvidenceTime(reportTime(r)) : ago(reportTime(r)))}</time>`
      : '<span>Report time unknown</span>';
    const cards = (list) => `<ul class="reports">${list.map((r) => `
      <li class="report ${r.side || r.claim_source === "idf" ? "sided" : ""}"><div class="report-head"><span class="report-src">${esc(r.source || "Unnamed source")}</span><span>${esc(PLATFORM[r.platform] || r.platform)}</span>
        <span>${esc(KIND[r.kind] || r.kind)}${r.side ? `, aligned with ${esc(r.side)}` : ""}${r.claim_source === "idf" ? ", IDF statement" : ""}</span>${timestamp(r)}</div>
        <p>${esc(r.summary)}</p><a href="${esc(safeUrl(r.url))}" target="_blank" rel="noopener noreferrer">Open the original report</a></li>`).join("")}</ul>`;
    return `<section class="event-evidence" aria-label="Event evidence">
      <h2 class="reports-title">Evidence</h2>
      <dl class="evidence-stats"><div><dt>Published reports</dt><dd>${info.reports}</dd></div><div><dt>Named sources</dt><dd>${info.sources}</dd></div></dl>
      <p class="evidence-note">${info.aligned ? `${info.aligned} ${info.aligned === 1 ? "source is" : "sources are"} marked as aligned with a side. ` : ""}${reports.some((r) => r.claim_source === "idf") ? "Reports labelled IDF statement count together as Israel's side, even through different publishers. " : ""}Different source names may share a newsroom or repeat the same report. The confidence label above accounts for source independence.</p>
      ${dated.length ? `<details class="report-timeline"><summary>Reporting timeline (${dated.length})</summary>
        <p class="evidence-note">Publication times, earliest first. Later coverage does not change ${e?.alert ? "the first warning's" : "the event's"} date.</p>
        <ol class="report-timeline-list">${dated.map((r) => `<li>${timestamp(r, true)}<a href="${esc(safeUrl(r.url))}" target="_blank" rel="noopener noreferrer">${esc(r.source || "Unnamed source")}</a></li>`).join("")}</ol>
      </details>` : ""}
      <h2 class="reports-title">Latest reports</h2>${ordered.length ? cards(ordered.slice(0, 5)) : '<p class="muted">No published reports available yet.</p>'}
      ${ordered.length > 5 ? `<details class="earlier-reports"><summary>Show ${ordered.length - 5} earlier reports</summary>${cards(ordered.slice(5))}</details>` : ""}
    </section>`;
  }

  function select(id, fly) {
    const e = S.data && S.data.events.find((x) => x.id === id);
    if (!e) return;
    stopRouteBurst();
    hotEvent(null);
    const fresh = id !== S.selectedId;
    S.selectedId = id; S.selectedHull = null; S.selectedFlow = null;
    markViewed(id);
    history.replaceState(null, "", eventLink(id));
    let flight = 0;
    if (fly) {
      const alt = Math.min(world.pointOfView().altitude, (e.wave || e.alert) && e.targets.length > 3 ? 1.45 : 1.15);
      flight = reduceMotion ? 0 : flyMs(e.lat, e.lon, alt);
      world.pointOfView({ lat: e.lat, lng: e.lon, altitude: alt }, flight);
    }
    if (fresh && passes(e)) playLaunches(e, flight);
    else if (fresh) stopLaunches();
    renderEventDetail(e);
    renderSoon();  // the details show at once; the globe follows a frame later
  }

  function renderEventDetail(e, refresh = false) {
    document.title = `${e.summary} | Global Situation Monitor`;
    const theaterName = (S.theaters.find((t) => t.id === e.theater) || {}).name || e.theater;
    const facts = [];
    if (e.launched != null) facts.push(`<span>Launched <b>${e.launched}</b> (reported)</span>`);
    if (e.intercepted != null) facts.push(`<span>Intercepted <b>${e.intercepted}</b> (reported)</span>`);
    if (e.killed != null) facts.push(`<span>Killed <b>${e.killed}</b> (reported)</span>`);
    if (e.injured != null) facts.push(`<span>Injured <b>${e.injured}</b> (reported)</span>`);
    const t = e.transfer;
    if (t) {
      facts.push(`<span>From <b>${esc(transferFrom(t))}</b> to <b>${esc(transferTo(t))}</b>${MODE[t.mode] ? " " + esc(MODE[t.mode]) : ""}</span>`);
      if (t.from || t.to) facts.push(`<span>Route <b>${esc((t.from && t.from.place) || "not named")}</b> → <b>${esc((t.to && t.to.place) || "not named")}</b></span>`);
      if (t.what) facts.push(`<span>Cargo <b>${esc(t.what)}</b></span>`);
      if (t.value_usd) facts.push(`<span>Value <b>${esc(fmtMoney(t.value_usd))}</b> (reported)</span>`);
    }
    const origins = originsOf(e);
    if (!e.wave && origins.length) facts.push(`<span>Launched from <b>${esc(origins.map((o) => o.place || "an unnamed site").join(", "))}</b></span>`);
    if (!e.wave && !origins.length && (e.type === "missile_drone" || e.type === "airstrike")) {
      const assumed = nearest(launchAreas(e), e);
      const distance = assumed && km(assumed.lat, assumed.lon, e.lat, e.lon);
      if (assumed && distance >= 25 && distance <= 1800) facts.push(`<span>Approximate path from <b>${esc(assumed.place)}</b></span>`);
    }
    const news = S.data.heat.filter((c) => km(e.lat, e.lon, c.lat, c.lon) <= (e.approx ? 60 : 30)).flatMap((c) => c.urls || []).slice(0, 4);
    const where = e.wave || e.alert ? `${esc(metaLine(e))}, ${esc(theaterName)}`
      : `${esc(e.place || "Unnamed location")}, ${esc(theaterName)} ${e.approx ? '<span class="approx">(approximate location)</span>' : ""}`;
    const waveBlock = e.wave ? `
      <h2 class="reports-title">Locations (${e.targets.length})</h2>
      ${e.targets.length ? `<ul class="targets">${byLatest(e.targets).map((x) => `<li><button class="target" type="button" data-goto="${x.lat},${x.lon}"><span>${esc(x.place || "Unnamed place")}</span>
        <span class="target-meta">${x.reports} ${x.reports === 1 ? "report" : "reports"}${x.killed ? `, ${x.killed} killed` : ""}</span></button></li>`).join("")}</ul>` : `<p class="muted">No specific locations reported yet.</p>`}
      <h2 class="reports-title">Launch areas</h2>
      <p class="muted">${origins.length ? esc(origins.map((o) => o.place || "unnamed site").join(", ")) : "No launch area was named. Faint paths start from the attacker's configured launch areas and end at reported targets; they are approximate, not tracked flights. Each 25 reported drones or missiles adds another path, up to 40 total."}</p>` : "";
    const alertBlock = e.alert ? `
      <p class="muted">Warnings that drones or missiles were in flight, grouped into one marker per country per day. They show where a threat was reported heading, not what was hit. Strikes and interceptions appear as their own events.</p>
      <h2 class="reports-title">Places named (${e.targets.length})</h2>
      ${e.targets.length ? `<ul class="targets">${byLatest(e.targets).map((x) => `<li><button class="target" type="button" data-goto="${x.lat},${x.lon}"><span>${esc(x.place || "Unnamed place")}</span>
        <span class="target-meta">${x.reports} ${x.reports === 1 ? "alert" : "alerts"}${latestOf(x) ? `, latest ${esc(agoShort(Date.parse(latestOf(x))))}` : ""}</span></button></li>`).join("")}</ul>` : `<p class="muted">No specific places named.</p>`}` : "";
    showDetail(`
      <div class="detail-type">${eventIcon(e)}${esc(typeLabel(e))}</div>
      ${(e.corrected || []).length ? `<div class="corrected"><span class="corrected-tag">Corrected</span><ul>${e.corrected.map((c) => `<li>${esc(c.change)}: ${esc(c.note)}</li>`).join("")}</ul></div>` : ""}
      <h3>${esc(e.summary)}</h3>
      <p class="detail-where">${where}</p>
      <p><button class="linkish" type="button" id="copyEventLink">Copy event link</button>
        <a class="linkish" href="${esc(eventLink(e.id))}" id="eventShareLink" hidden>Open share link</a></p>
      ${e._archived ? '<p class="muted">Archived event. This published snapshot is separate from the current live feed.</p>' : ""}
      <dl class="event-times"><div><dt>${e.alert ? "First warning" : "Event time"}</dt><dd><time datetime="${esc(e.time || e.updated)}" title="${esc(new Date(e._t).toUTCString())}">${esc(fmtEvidenceTime(e._t))}</time></dd></div>
        <div><dt>Latest report</dt><dd><time datetime="${esc(e.updated || e.time)}" title="${esc(new Date(e._tu).toUTCString())}">${esc(fmtEvidenceTime(e._tu))}</time></dd></div></dl>
      ${hasFollowup(e) ? '<p class="muted">Later reports about this same event are grouped here. The time filter uses when the event happened, not when the latest report arrived.</p>' : ""}
      ${e.possibly_old ? `<div class="verdict verdict--doubt"><span class="conf-swatch conf-dashed" aria-hidden="true"></span><div><strong>Possibly an old story</strong><p>Only one outlet has this, and a news search found earlier coverage of the same topic but nothing current from other outlets. It may be an old article republished with a new date. It stays on the map, quieter, and is confirmed if another source reports it.</p></div></div>` : ""}
      <div class="verdict"><span class="conf-swatch conf-${STATUS[e.status].conf}" aria-hidden="true"></span><div><strong>${esc(STATUS[e.status].label)}</strong><p>${esc(STATUS[e.status].note(e.sources_count, e.news_nearby))}</p></div></div>
      ${facts.length ? `<div class="facts">${facts.join("")}</div>` : ""}
      ${e.legal_basis ? `<div class="legal-basis"><span>Stated legal basis</span><strong>${esc(e.legal_basis)}</strong><p>As reported by the sources below. The dashboard records claimed justifications; it does not assess them.</p></div>` : ""}
      ${waveBlock}${alertBlock}
      <div id="reportsSlot">${e.reports || shownReports.has(e.id) ? reportsHtml(e.reports || shownReports.get(e.id), e)
        : '<h2 class="reports-title">Reports</h2><p class="muted">Loading reports\u2026</p>'}</div>
      ${news.length ? `<h2 class="reports-title">News coverage nearby (${e.news_nearby || news.length} outlets)</h2>
        <ul class="news-links">${news.map((u) => `<li><a href="${esc(safeUrl(u))}" target="_blank" rel="noopener noreferrer">${esc(u.replace(/^https?:\/\/(www\.)?/, "").slice(0, 80))}</a></li>`).join("")}</ul>` : ""}
      <p class="event-id">Event id <code>${esc(e.id)}</code></p>
    `, refresh);
    $("#copyEventLink").addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(eventLink(e.id));
        announce("Event link copied");
      } catch {
        $("#eventShareLink").hidden = false;
        announce("Open the share link and copy its address");
      }
    });
    if (!e.reports) fillReports(e);
  }

  function selectFlow(key, pledge = false) {
    history.replaceState(null, "", APP_ROOT.pathname + location.search);
    document.title = "Global Situation Monitor";
    const s = S.supply;
    const f = (pledge ? s.pledges : s.flows).find((x) => x.key === key) || s.flows.find((x) => x.key === key) || s.pledges.find((x) => x.key === key);
    if (!f) return;
    const isPledge = s.pledges.includes(f);
    const fresh = S.selectedFlow !== key;
    stopLaunches();
    if (fresh || isPledge) stopRouteBurst();
    S.selectedFlow = key; S.selectedId = null; S.selectedHull = null;
    const a = f.from || countryCenter(f.supplier), b = f.to || countryCenter(f.recipient);
    let flight = 0;
    if (a && b) {
      const mid = slerp(a, b, 0.5), altitude = clamp(0.6 + km(a.lat, a.lon, b.lat, b.lon) / 5000, 1.1, 2.6);
      flight = reduceMotion ? 0 : flyMs(mid.lat, mid.lon, altitude);
      world.pointOfView({ lat: mid.lat, lng: mid.lon, altitude }, flight);
    }
    const route = f.from && f.to ? `${esc(f.from.place || "origin")}${f.via.length ? ` → ${f.via.map((v) => esc(v.place || "hub")).join(" → ")}` : ""} → ${esc(f.to.place || "destination")}`
      : "Not named in reports. The line runs between the two countries and is drawn faint.";
    showDetail(`
      <div class="detail-type">${flowBadge(f)}${f.money ? (isPledge ? "Financial aid pledged" : `Financial aid, ${windowText()}`) : isPledge ? "Pledged aid" : `Supply route, ${windowText()}`}</div>
      <h3>${esc(flowFrom(f))} → ${esc(flowTo(f))}</h3>
      <p class="detail-where">${isPledge ? `${f.events.length} ${f.events.length === 1 ? "announcement" : "announcements"}` : `${f.deliveries} ${f.deliveries === 1 ? "delivery" : "deliveries"} reported`}, last ${esc(ago(f.last))}</p>
      <div class="verdict"><span class="conf-swatch conf-${STATUS[f.status].conf}" aria-hidden="true"></span><div><strong>${esc(STATUS[f.status].label)}</strong><p>Best confidence among the reports below. On the map, broader moving dashes are corroborated; finer dashes show uncorroborated reports. Blue is supply, green is financial aid.</p></div></div>
      <div class="facts">
        ${f.modes.length ? `<span>Mode <b>${esc(f.modes.map((m) => MODE[m]).join(", "))}</b></span>` : ""}
        ${f.value ? `<span>Value <b>${esc(fmtMoney(f.value))}</b> (reported)</span>` : ""}
        ${f.cargo.length ? `<span>Cargo <b>${esc(f.cargo.slice(0, 5).join(", "))}</b></span>` : ""}
      </div>
      ${isPledge ? "" : `<h2 class="reports-title">Route</h2><p class="muted">${route}</p>
        <h2 class="reports-title">Deliveries over the ${windowText()}</h2><div class="flow-chart">${sparkSvg(f.bins, 9, 3, 34, "flow-bars")}</div>`}
      <h2 class="reports-title">${isPledge ? "Announcements" : "Reported deliveries"} (${f.events.length})</h2>
      <ul class="targets">${f.events.map((e) => `<li><button class="target" type="button" data-event="${esc(e.id)}"><span>${esc(e.summary)}</span><span class="target-meta">${esc(agoShort(e._t))}</span></button></li>`).join("")}</ul>
    `);
    if (fresh && !isPledge) playRouteBurst(f, flight);
    renderSoon();
  }

  function selectCarrier(hull, fly) {
    const c = S.fleet.find((x) => x.hull === hull);
    if (!c) return;
    stopLaunches();
    stopRouteBurst();
    S.selectedHull = hull; S.selectedId = null; S.selectedFlow = null;
    history.replaceState(null, "", APP_ROOT.pathname + location.search + "#" + encodeURIComponent(hull));
    document.title = "Global Situation Monitor";
    if (fly) zoomTo(c._lat, c._lon, clamp(world.pointOfView().altitude, 1.3, 1.8));
    renderCarrierDetail(c);
    renderSoon();
    if (isMobile()) toggleFilters(false);
  }

  function renderCarrierDetail(c, refresh = false) {
    const nearby = S.data.events.filter((e) => onMap(e) && e._t > Date.now() - 3 * DAY && km(e.lat, e.lon, c._lat, c._lon) <= 600)
      .sort((a, b) => b._t - a._t).slice(0, 6);
    const track = (c.track || []).slice().reverse();
    showDetail(`
      <div class="detail-type">${iconBadge("carrier", "fleet", c.at_home ? "outline" : "solid")}Aircraft carrier</div>
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

  // ------------------------------------------------------------------ mobile sheet
  const viewportHeight = () => Math.round(window.visualViewport?.height || window.innerHeight);
  const sheetHeights = () => {
    const height = viewportHeight();
    const short = height < 500;
    return [short ? 86 : 94, Math.round(height * (short ? 0.48 : 0.42)), Math.round(height * (short ? 0.9 : 0.8))];
  };
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
      if (drag.moved) feed.style.height = clamp(drag.h - dy, sheetHeights()[0], viewportHeight() * 0.92) + "px";
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
  function legendChanged() {
    const items = document.querySelectorAll("[data-legend]");
    items.forEach((b) => b.setAttribute("aria-pressed", String(!S.off.has(b.dataset.legend))));
    S.layers = { paths: !S.off.has("paths"), supply: !S.off.has("crate"), carriers: !S.off.has("carrier") };
    if (!S.layers.paths) stopLaunches();
    if (routeBurst && S.off.has(routeBurst.money ? "coin" : "crate")) stopRouteBurst();
    $("#legendReset").hidden = !S.off.size;
    $("#legendNone").hidden = S.off.size >= items.length;
    renderSoon();
  }

  function buildStaticControls() {
    const item = (key, swatch, label) => `<li><button class="legend-item" type="button" data-legend="${key}" aria-pressed="true" title="Show or hide ${esc(label.toLowerCase())}">${swatch}<span>${esc(label)}</span></button></li>`;
    $("#legend").innerHTML = LEGEND.map(([icon, cat, label]) => item(icon, iconBadge(icon, cat, "solid", "ico-sm"), label)).join("")
      + item("paths", '<span class="line-swatch line-strike" aria-hidden="true"></span>', "Launch path");
    $("#windowSeg").innerHTML = WINDOWS.map(([label, h]) => `<button type="button" data-window="${h}" aria-pressed="${h === S.windowH}">${label}</button>`).join("");
    $("#windowSeg").style.setProperty("--seg-i", String(Math.max(0, WINDOWS.findIndex(([, x]) => x === S.windowH))));
    $("#statusList").innerHTML = Object.entries(STATUS).map(([id, s]) => `
      <li><label class="check"><input type="checkbox" data-status="${id}" checked><span class="conf-swatch conf-${s.conf}" aria-hidden="true"></span><span class="label">${esc(s.label)}</span><span class="count" data-status-count="${id}"></span></label></li>`).join("");
  }

  function setWindow(h) {
    S.windowH = h;
    document.querySelectorAll("[data-window]").forEach((x) => x.setAttribute("aria-pressed", String(Number(x.dataset.window) === h)));
    $("#windowSeg").style.setProperty("--seg-i", String(Math.max(0, WINDOWS.findIndex(([, x]) => x === h))));  // the sliding highlight
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
    setupIdleRotation(controls, $("#rotationBtn"));
    $("#overviewBtn").addEventListener("click", () => {
      selectRegion("world");
      world.pointOfView({ lat: 22, lng: 28, altitude: isMobile() ? 4.2 : 2.4 }, reduceMotion ? 0 : 900);
    });
    [ ["#zoomInBtn", 0.72], ["#zoomOutBtn", 1.4] ].forEach(([id, scale]) => {
      $(id).addEventListener("click", () => {
        const pov = world.pointOfView();
        world.pointOfView({ ...pov, altitude: clamp(pov.altitude * scale, 0.12, 5.4) }, reduceMotion ? 0 : 300);
      });
    });
    $("#windowSeg").addEventListener("click", (ev) => {
      const b = ev.target.closest("[data-window]");
      if (b) { setWindow(Number(b.dataset.window)); renderSoon(); }
    });
    $("#filters").addEventListener("change", (ev) => {
      const t = ev.target;
      if (t.dataset.status) t.checked ? S.statusOn.add(t.dataset.status) : S.statusOn.delete(t.dataset.status);
      renderSoon();
    });
    $("#legend").addEventListener("click", (ev) => {
      const b = ev.target.closest("[data-legend]");
      if (!b) return;
      const key = b.dataset.legend;
      S.off.has(key) ? S.off.delete(key) : S.off.add(key);
      legendChanged();
    });
    // "Hide everything" then tapping one entry shows only that kind.
    $("#legendReset").addEventListener("click", () => { S.off.clear(); legendChanged(); });
    $("#legendNone").addEventListener("click", () => {
      document.querySelectorAll("[data-legend]").forEach((b) => S.off.add(b.dataset.legend));
      legendChanged();
    });
    $("#regionFocus").addEventListener("click", (ev) => {
      if (ev.target.closest("[data-region]")) selectRegion("world");
    });
    $("#filters").addEventListener("click", (ev) => {
      const region = ev.target.closest("[data-region]");
      if (region) { selectRegion(region.dataset.region); return; }
      const hull = ev.target.closest("[data-hull]");
      if (hull) { selectCarrier(hull.dataset.hull, true); return; }
      const flow = ev.target.closest("[data-flow]");
      if (flow) { selectFlow(flow.dataset.flow, !!flow.dataset.pledge); if (isMobile()) toggleFilters(false); return; }
      const fly = ev.target.closest("[data-fly]");
      if (!fly) return;
      const t = S.theaters.find((x) => x.id === fly.dataset.fly);
      if (t) selectRegion(t.id);
    });
    $("#sourcesToggle").addEventListener("click", () => {
      const list = $("#sourcesList");
      list.hidden = !list.hidden;
      $("#sourcesToggle").setAttribute("aria-expanded", String(!list.hidden));
    });
    $("#feedList").addEventListener("pointerover", (ev) => {
      if (ev.pointerType !== "mouse") return;
      const b = ev.target.closest(".item[data-id]");
      if (b && b.dataset.id !== hotId) hotEvent(b.dataset.id);
    });
    $("#feedList").addEventListener("pointerleave", () => hotEvent(null));
    // Keep reader-opened analysis sections open through filtering and live refreshes.
    $("#feedList").addEventListener("toggle", (ev) => {
      const section = ev.target.dataset && ev.target.dataset.briefSection;
      if (section && $("#feedList").contains(ev.target)) {
        if (ev.target.open) S.briefOpen.add(section); else S.briefOpen.delete(section);
      }
    }, true);
    $("#feedList").addEventListener("click", (ev) => {
      if (ev.target.closest("[data-more]")) { S.feedLimit += FEED_PAGE; render(); return; }
      const b = ev.target.closest("[data-id]");
      if (b) { S.lastFocus = b.dataset.id; select(b.dataset.id, true); return; }
      const fly = ev.target.closest("[data-fly]");
      const t = fly && S.theaters.find((x) => x.id === fly.dataset.fly);
      if (t) selectRegion(t.id);
    });
    let searchTimer;
    $("#search").addEventListener("focus", () => { if (isMobile()) setSheet(2); });
    $("#search").addEventListener("input", (ev) => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => { S.query = ev.target.value; renderSoon(); }, 120);
    });
    $("#filtersToggle").addEventListener("click", () => toggleFilters());
    document.addEventListener("pointerdown", (ev) => {
      if (isMobile() && $("#filters").classList.contains("open") &&
          !$("#filters").contains(ev.target) && !$("#filtersToggle").contains(ev.target)) toggleFilters(false);
    });
    $("#panelsToggle").addEventListener("click", () => setPanelsHidden(!document.body.classList.contains("panels-hidden")));
    wireSheet();
    document.addEventListener("keydown", (ev) => {
      const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement && document.activeElement.tagName);
      if (ev.key === "Escape") {
        if (!fly.hidden) { closeFly(); return; }
        if (!$("#detail").hidden) closeDetail();
        else if ($("#filters").classList.contains("open")) toggleFilters(false);
        else if (selectedRegion()) selectRegion("world");
      }
      if (typing) return;
      if (ev.key === "/") { ev.preventDefault(); if (!$("#detail").hidden) closeDetail(); $("#search").focus(); }
      if ((ev.key === "h" || ev.key === "H") && !isMobile()) setPanelsHidden(!document.body.classList.contains("panels-hidden"));
    });
  }

  // ------------------------------------------------------------------ boot
  const markSeen = () => { try { localStorage.setItem("gsm_lastSeen", String(Date.now())); } catch (_) { /* ignore */ } };
  window.addEventListener("pagehide", markSeen);
  setInterval(markSeen, 10 * 60e3);
  window.__appReady = true;  // the early preview in index.html stands down
  try {
    buildStaticControls();
    renderTheaters();
    wire();
    if (isMobile()) setSheet(0, true);
    layout();
    load();
  } catch (err) {
    fatal(err);
  }
  const syncAnimationVisibility = () => {
    if (document.hidden) world.pauseAnimation();
    else world.resumeAnimation();
  };
  document.addEventListener("visibilitychange", syncAnimationVisibility);
  if (document.hidden) world.pauseAnimation();
  if (!DEMO) {
    setInterval(checkForUpdate, 60e3);
    document.addEventListener("visibilitychange", () => { if (document.hidden) reloadIfIdle(); else checkForUpdate(); });
  }
  setInterval(() => {
    updateFreshness();
    document.querySelectorAll("#feedList time").forEach((t) => { t.textContent = agoShort(Date.parse(t.dateTime)); });
  }, 30e3);
})();
