(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const params = new URLSearchParams(location.search);
  const DEMO = params.has("demo");
  const REFRESH_MS = 5 * 60 * 1000;
  const HOUR = 3600e3, DAY = 86400e3;
  const LIVE_MS = 6 * HOUR;          // events this recent animate
  const MAX_ANIMATED = 20;           // but only this many at once, newest first, so busy nights stay smooth
  // Phones get lighter limits: fewer markers and animations, and a lower render resolution.
  const PHONE = window.matchMedia("(max-width: 859px), (pointer: coarse)").matches;
  const MAX_MARKERS = PHONE ? 160 : 320;
  const MAX_ANIMATED_NOW = PHONE ? 8 : MAX_ANIMATED;
  const isMobile = () => window.innerWidth < 860;

  // ------------------------------------------------------------------ vocabulary
  // Confidence is shown by the marker's style (solid, outline, dashed); color and icon show what happened.
  const STATUS = {
    corroborated: { label: "Corroborated", rank: 3, conf: "solid", alpha: 0.95,
      note: (n, news) => `Reported by ${n} independent sources${news >= 3 ? ", including nearby news coverage" : ""}.` },
    unconfirmed: { label: "Single source", rank: 2, conf: "outline", alpha: 0.7, note: () => "Only one source so far. Treat it as unverified." },
    claimed: { label: "One side's claim", rank: 1, conf: "dashed", alpha: 0.6, note: () => "Reported only by sources aligned with one side of the conflict." },
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
    missile: '<path d="M13.6 2.4 7 5.4 4.6 7.9l3.5 3.5 2.5-2.4z" fill="currentColor"/><path d="M4.6 7.9l-2.1.8M8.1 11.4l-.8 2.1M5.4 10.6l-2.6 2.6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" fill="none"/>',
    air: '<path d="M8 1.3 9 5.9l5.5 2.5v1.5L9 8.8l-.4 3.1 1.6 1.3v1.2L8 13.7l-2.2.7v-1.2l1.6-1.3L7 8.8 1.5 9.9V8.4L7 5.9z" fill="currentColor"/>',
    shield: '<path d="M8 1.5l5.5 2v4.4c0 3.3-2.4 5.4-5.5 6.6-3.1-1.2-5.5-3.3-5.5-6.6V3.5z" fill="currentColor"/>',
    blast: '<circle cx="8" cy="8" r="3" fill="currentColor"/><path d="M8 1.5v2.2M8 12.3v2.2M1.5 8h2.2M12.3 8h2.2M3.4 3.4l1.5 1.5M11.1 11.1l1.5 1.5M3.4 12.6l1.5-1.5M11.1 4.9l1.5-1.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>',
    artillery: '<path d="M8 1.3l1.4 4.1 4.2-1.3-2.6 3.4 3.6 2.4-4.3.3.2 4.2L8 11l-2.5 3.4.2-4.2-4.3-.3 3.6-2.4-2.6-3.4 4.2 1.3z" fill="currentColor"/>',
    ground: '<path d="M3 3l9.4 9.4M13 3 3.6 12.4M10 13.2 13.2 10M2.8 10 6 13.2" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" fill="none"/>',
    territory: '<path d="M4 14.5V1.8" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/><path d="M4.6 2.2h8.2l-2 3.1 2 3.1H4.6z" fill="currentColor"/>',
    naval: '<circle cx="8" cy="3.3" r="1.6" stroke="currentColor" stroke-width="1.4" fill="none"/><path d="M8 4.9V14M4.6 7.2h6.8M2.4 9.4c.5 3 3 4.6 5.6 4.6s5.1-1.6 5.6-4.6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" fill="none"/>',
    deploy: '<path d="M3 9.6 8 5l5 4.6M3 13.6 8 9l5 4.6" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" fill="none"/>',
    hybrid: '<path d="M9.6 1.4 3.4 9h4l-1 5.6L12.6 7h-4z" fill="currentColor"/>',
    incursion: '<path d="M1.8 8h8.8M7.6 4.6 11 8l-3.4 3.4" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" fill="none"/><path d="M13.6 2.2v11.6" stroke="currentColor" stroke-width="1.5" stroke-dasharray="1.6 1.6"/>',
    diplo: '<circle cx="6" cy="8" r="3.4" stroke="currentColor" stroke-width="1.6" fill="none"/><circle cx="10" cy="8" r="3.4" stroke="currentColor" stroke-width="1.6" fill="none"/>',
    legal: '<path d="M8 2v11.6M4 14.4h8M2.8 4.6h10.4" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/><path d="M2.8 4.6 1 9h3.6zM13.2 4.6 11.4 9H15z" fill="currentColor"/>',
    coin: '<circle cx="8" cy="8" r="6.2" stroke="currentColor" stroke-width="1.4" fill="none"/><path d="M10.1 5.9c-.4-.7-1.2-1.1-2.1-1.1-1.2 0-2.1.6-2.1 1.5 0 2.1 4.3 1.1 4.3 3.3 0 .9-1 1.6-2.2 1.6-1 0-1.9-.4-2.3-1.2M8 3.6v1.2M8 11.3v1.2" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" fill="none"/>',
    crate: '<path d="M2 5.2 8 2.3l6 2.9v5.6L8 13.7l-6-2.9z" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round" fill="none"/><path d="M2 5.2 8 8.1l6-2.9M8 8.1v5.6" stroke="currentColor" stroke-width="1.4" fill="none"/>',
    factory: '<path d="M1.6 14.2V7.4l3.6 2.3V7.4l3.6 2.3V2.4h1.7v-.8h2.2v.8h1.7v11.8z" fill="currentColor"/>',
    carrier: '<path d="M.8 9.6 2.9 6h10.3l2.2 1.6v1.8l-1.6 1.6H2.6z" fill="currentColor"/><rect x="10.4" y="3.8" width="2.2" height="2.4" rx=".3" fill="currentColor"/>',
    alert: '<path d="M4.4 12.2V9a3.6 3.6 0 0 1 7.2 0v3.2z" fill="currentColor"/><rect x="2.6" y="12.7" width="10.8" height="1.9" rx=".6" fill="currentColor"/><path d="M8 1.4v2.1M2.8 3.6l1.5 1.5M13.2 3.6l-1.5 1.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>',
  };
  const svgIcon = (name) => `<svg viewBox="0 0 16 16" aria-hidden="true">${ICONS[name] || ICONS.blast}</svg>`;
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
  const PLATFORM = { bluesky: "Bluesky", telegram: "Telegram", rss: "News feed", gdelt: "GDELT", map: "Map data", maproom: "ISW map" };
  const KIND = { official: "Official", partisan: "Partisan", osint: "OSINT", news: "News", analysis: "Analysis" };
  const WINDOWS = [["6h", 6], ["24h", 24], ["3d", 72], ["7d", 168]];
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
    IR: [["Kermanshah", 34.31, 47.07], ["Tabriz", 38.08, 46.29], ["Isfahan", 32.65, 51.67], ["Bandar Abbas", 27.18, 56.27]],
    YE: [["Sanaa", 15.37, 44.19], ["Hodeidah", 14.8, 42.95], ["Saada", 16.94, 43.76]],
    LB: [["Nabatieh", 33.38, 35.48], ["Tyre", 33.27, 35.2]],
    IL: [["southern Israel", 31.25, 34.79], ["northern Israel", 32.8, 35.1]],
    // North Korea's usual launch areas: Sunan (Pyongyang), the Wonsan coast, Sohae, Sinpo
    KP: [["Pyongyang", 39.2, 125.67], ["Wonsan", 39.17, 127.48], ["Sohae", 39.66, 124.71], ["Sinpo", 40.03, 128.18]],
  };
  Object.keys(ANCHORS).forEach((k) => { ANCHORS[k] = ANCHORS[k].map(([place, lat, lon]) => ({ place, lat, lon })); });
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
  const isNew = (e) => !viewed[e.id] && !e.possibly_old && (e._t > Date.now() - HOUR || (lastSeen && e._t > lastSeen && e._t > Date.now() - DAY));
  const isLive = (e) => !viewed[e.id] && (Date.now() - e._t < LIVE_MS || isNew(e));

  // ------------------------------------------------------------------ state
  const S = {
    data: null,
    theaters: FALLBACK_THEATERS,
    windowH: 24,
    theaterOn: new Set(FALLBACK_THEATERS.map((t) => t.id)),
    statusOn: new Set(Object.keys(STATUS)),
    layers: { paths: true, supply: true, carriers: true },
    off: new Set(),  // "On the map" entries switched off
    query: "",
    feedLimit: 250,
    selectedId: null,
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
    sheet: 1,
  };

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
  controls.minDistance = 106; // globe radius is 100: close to city scale (the painted land is coarse this close)
  controls.maxDistance = 650;
  if (PHONE) world.renderer().setPixelRatio(Math.min(1.5, window.devicePixelRatio || 1));
  // While the globe is being dragged or pinched, markers stop sliding into place and heavier
  // updates wait until the gesture ends (the camera keeps easing briefly after release).
  let moving = false, settleTimer = null;
  controls.addEventListener("start", () => {
    if (typeof hideTip === "function") hideTip();
    clearTimeout(settleTimer);
    moving = true;
    document.body.classList.add("moving");
  });
  controls.addEventListener("end", () => {
    clearTimeout(settleTimer);
    settleTimer = setTimeout(() => {
      moving = false;
      document.body.classList.remove("moving");
      applyZoomScale();
      queueDeclutter();
    }, 350);
  });

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
  const landColor = (f) => (S.active.has(f.id) ? "#3a6a98" : S.hot.has(f.id) ? "#2a4f75" : "#1e3a59");
  const OCEAN = "#0b1f36";
  // Territorial control (see controlNote): the source's shapes, painted onto the globe with the land.
  const CONTROL_FILL = { occupied: "rgba(214,174,110,0.55)", advance: "rgba(245,165,36,0.9)" };
  const CONTROL_LINE = "rgba(236,212,160,0.75)";
  const pathColorOf = (p) => (p.control ? CONTROL_LINE : borderColor(p.fid));
  const borderColor = (id) => (S.active.has(id) ? "rgba(255,166,122,0.8)" : S.hot.has(id) ? "rgba(150,195,235,0.3)" : "rgba(150,190,230,0.12)");

  fetch("assets/countries-110m.json")
    .then((r) => r.json())
    .then((topo) => {
      const land = topojson.feature(topo, topo.objects.countries).features
        .filter((f) => f.properties.name !== "Antarctica").map(sanitize).filter(Boolean);
      land.forEach((f) => { if (f.id) centers.set(f.id, centerOf(f)); });
      const borders = [];
      for (const f of land) for (const poly of f.geometry.coordinates) for (const ring of poly) borders.push({ fid: f.id, pts: ring });
      borderPaths = borders;
      landShapes = land;
      paintLand();
      world
        .pathsData(borders).pathPoints("pts").pathPointLat((p) => p[1]).pathPointLng((p) => p[0]).pathPointAlt(0.0045)
        .pathTransitionDuration(0).pathColor(pathColorOf)
        // traced (approximate) areas get a dashed edge; dash sizes are fractions of the ring's length
        .pathDashLength((p) => (p.approx ? 0.006 : 1)).pathDashGap((p) => (p.approx ? 0.004 : 0));
      if (S.data) render();
    })
    .catch(() => {});

  // Land is painted onto the globe's own surface (an ocean-and-countries picture), not drawn as a
  // separate layer floating just above it: phone graphics chips can't tell two surfaces that close
  // apart, and the ocean showed through the land in dark streaks while moving.
  let landShapes = [], landKey = "", landUrl = null, borderPaths = [], hatch = null;
  const controlLayers = () => (S.data && S.data.control) || [];  // always shown, not a filter
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
  function paintControl(g, X, Y) {
    // one path per style, filled "nonzero", so overlapping layers of one style don't double up
    const byStyle = new Map();
    for (const L of controlLayers()) (byStyle.get(L.style) || byStyle.set(L.style, []).get(L.style)).push(L);
    for (const style of ["occupied", "infiltration", "advance"]) {
      const layers = byStyle.get(style);
      if (!layers) continue;
      g.fillStyle = style === "infiltration" ? hatchPattern(g) : CONTROL_FILL[style];
      g.beginPath();
      for (const L of layers) for (const poly of L.polygons) for (const ring of poly) {
        ring.forEach(([lon, lat], i) => (i ? g.lineTo(X(lon), Y(lat)) : g.moveTo(X(lon), Y(lat))));
        g.closePath();
      }
      g.fill("nonzero");
    }
    // crisp outlines of held ground, drawn as lines like the borders
    const outlines = [];
    for (const L of controlLayers()) if (L.style === "occupied") for (const poly of L.polygons) for (const ring of poly) outlines.push({ control: true, approx: !!L.approx, pts: ring });
    if (borderPaths.length) world.pathsData([...borderPaths, ...outlines]);
  }
  const landCanvas = document.createElement("canvas");
  landCanvas.width = PHONE ? 2048 : 4096;
  landCanvas.height = landCanvas.width / 2;
  function paintLand() {
    const key = [...S.active].sort().join(",") + "|" + [...S.hot].sort().join(",") + "|" + controlLayers().map((l) => l.id + l.as_of).join(",");
    if (!landShapes.length || key === landKey) return;
    landKey = key;
    const W = landCanvas.width, H = landCanvas.height, g = landCanvas.getContext("2d");
    const X = (lon) => ((lon + 180) / 360) * W, Y = (lat) => ((90 - lat) / 180) * H;
    g.fillStyle = OCEAN;
    g.fillRect(0, 0, W, H);
    for (const f of landShapes) {
      g.fillStyle = landColor(f);
      g.beginPath();
      for (const poly of f.geometry.coordinates) {
        for (const ring of poly) {
          // keep each outline continuous across the 180° line, then draw it again one turn left and right
          let prev = ring[0][0];
          const pts = ring.map(([lon, lat]) => {
            while (lon - prev > 180) lon -= 360;
            while (lon - prev < -180) lon += 360;
            prev = lon;
            return [lon, lat];
          });
          for (const shift of [-360, 0, 360]) {
            pts.forEach(([lon, lat], i) => (i ? g.lineTo(X(lon + shift), Y(lat)) : g.moveTo(X(lon + shift), Y(lat))));
            g.closePath();
          }
        }
      }
      g.fill("evenodd");
    }
    paintControl(g, X, Y);
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
  world
    .pointLat("lat").pointLng("lon")
    .pointAltitude(0.005)
    .pointRadius((d) => (d.alert ? 0.09 : 0.13) * zoomK)
    .pointColor((d) => rgba(CAT_RGB.strike, d.alert ? 0.5 : STATUS[d.ref.status].alpha))
    .pointResolution(8)
    .pointLabel((d) => `<div class="tip"><div class="tip-meta"><b>${esc(d.place || "Location")}</b><span>${d.alert ? "named in an alert" : "part of an attack wave"}</span></div><div class="tip-sum">${esc(d.ref.summary)}</div></div>`)
    .onPointHover((d) => { globeEl.style.cursor = d ? "pointer" : ""; })
    .onPointClick((d) => select(d.ref.id, true));
  world
    .ringLat("lat").ringLng("lon")
    .ringColor((r) => (t) => rgba(r.rgb, Math.max(0, 1 - t) * r.alpha))
    .ringMaxRadius((r) => r.max * zoomK)
    .ringPropagationSpeed((r) => r.speed)
    .ringRepeatPeriod((r) => r.period)
    .ringAltitude(0.006);
  const ARC = {
    strike: { dash: 0.5, gap: 0.18 }, strikeApprox: { dash: 0.34, gap: 0.28 },
    flow: { dash: 1, gap: 0 }, flowDashed: { dash: 0.12, gap: 0.07 }, particles: { dash: 0.012, gap: 0.11 },
    track: { dash: 0.06, gap: 0.04 }, plan: { dash: 0.2, gap: 0.14 }, hit: { dash: 1, gap: 0 },
  };
  // Routes and carrier lines are thin, so hovering meant being exactly on them: each gets an
  // invisible, wider twin that answers hover and taps for it.
  const hitArcs = (arcs) => arcs.filter((a) => (a.flow || a.carrier) && a.kind !== "particles")
    .map((a) => ({ ...a, kind: "hit", color: "rgba(0,0,0,0)", stroke: Math.max(2.8, a.stroke * 6), ms: 0, seed: 0 }));
  world
    .arcStartLat("sLat").arcStartLng("sLng").arcEndLat("eLat").arcEndLng("eLng")
    .arcColor((a) => a.color).arcStroke(arcStroke)
    .arcDashLength((a) => ARC[a.kind].dash).arcDashGap((a) => ARC[a.kind].gap)
    .arcDashInitialGap((a) => (a.kind === "flow" ? 0 : a.seed))
    .arcDashAnimateTime((a) => (reduceMotion ? 0 : a.ms || 0))
    .arcAltitude((a) => (a.alt === undefined ? null : a.alt))
    .arcAltitudeAutoScale(0.36)
    .arcLabel((a) => (a.carrier ? tipCarrier(a.carrier) : a.flow ? tipFlow(a.flow) : a.ref ? tipEvent(a.ref) : ""))
    .onArcHover((a) => { globeEl.style.cursor = a ? "pointer" : ""; })
    .onArcClick((a) => { if (a.carrier) selectCarrier(a.carrier.hull, true); else if (a.flow) selectFlow(a.flow.key); else if (a.ref) select(a.ref.id, true); });

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

  // ------------------------------------------------------------------ HTML markers
  // Each marker is an outer anchor (positioned by the globe) holding an inner button that can be
  // nudged sideways when markers overlap, with a thin line back to the true location.
  world
    .htmlLat("lat").htmlLng("lon")
    .htmlAltitude((d) => d.hAlt || 0.012)
    .htmlElement((d) => d.el)
    .htmlElementVisibilityModifier((el, visible) => {
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

  function eventMarker(e, labelIt, animate) {
    const [cat, icon, fx] = catOf(e);
    const el = markerEl(`ev:${e.id}`);
    const live = animate && !reduceMotion && !e.possibly_old;
    const size = e.alert ? "md" : e.severity >= 3 ? "lg" : e.severity === 2 ? "md" : "sm";
    el.style.setProperty("--s", `${markerPx(e, size).toFixed(1)}px`);
    el.className = `mk cat-${cat} conf-${STATUS[e.status].conf} size-${size}${live && fx ? ` fx-${fx}` : ""}${e.id === S.selectedId ? " is-selected" : ""}${el.classList.contains("spread") ? " spread" : ""}`;
    el.style.setProperty("--fade", String(fade(e)));
    const label = labelIt ? (e.alert ? alertsText(e) : e.wave ? (e.launched ? `${e.launched} launched` : `${e.targets.length} places hit`) : e.place || "") : "";
    const btn = el.firstChild;
    btn.innerHTML = `${svgIcon(icon)}${label ? `<span class="mk-label">${esc(label)}</span>` : ""}<span class="mk-count" aria-hidden="true"></span>`;
    btn.setAttribute("aria-label", `${typeLabel(e)}, ${metaLine(e)}. ${STATUS[e.status].label}.`);
    btn.onclick = (ev) => {
      ev.stopPropagation();
      if (el.classList.contains("cluster-lead")) zoomTo(e.lat, e.lon, 0.35); // a count bubble zooms in to show its events
      else select(e.id, true);
    };
    btn.onmouseenter = () => showTip(el, tipEvent(e));
    btn.onmouseleave = hideTip;
    return { key: `ev:${e.id}`, el, lat: e.lat, lon: e.lon, hAlt: 0.014, isEvent: true, prio: e.severity * 10 + (isNew(e) ? 5 : 0) + Math.log10(1 + magnitude(e)) + (e._t / 1e13) };
  }

  function carrierMarker(c) {
    const el = markerEl(`cvn:${c.hull}`);
    const tone = c.at_home ? "port" : c.status === "departed" || c.status === "underway" ? "underway" : "deployed";
    el.className = `mk mk-cvn cvn-${tone}${c.hull === S.selectedHull ? " is-selected" : ""}${el.classList.contains("spread") ? " spread" : ""}`;
    const btn = el.firstChild;
    btn.innerHTML = `${svgIcon("carrier")}<span class="mk-label">${esc(c.short || c.hull)}</span>`;
    btn.setAttribute("aria-label", `${c.name}, ${carrierStatus(c)}${c.place ? ", " + c.place : ""}`);
    btn.onclick = (ev) => { ev.stopPropagation(); selectCarrier(c.hull, true); };
    btn.onmouseenter = () => showTip(el, tipCarrier(c));
    btn.onmouseleave = hideTip;
    c.el = el; c.key = `cvn:${c.hull}`; c.hAlt = 0.012; c.prio = 1;
    return c;
  }

  // Simple tooltip for HTML markers (3D layers use globe.gl's own).
  const tipBox = document.createElement("div");
  tipBox.className = "html-tip";
  tipBox.hidden = true;
  document.body.appendChild(tipBox);
  function showTip(el, html) {
    if (isMobile()) return;
    const r = el.firstChild.getBoundingClientRect();
    tipBox.innerHTML = html;
    tipBox.hidden = false;
    const w = tipBox.offsetWidth;
    tipBox.style.left = `${clamp(r.left + r.width / 2 - w / 2, 8, window.innerWidth - w - 8)}px`;
    tipBox.style.top = `${Math.max(8, r.top - tipBox.offsetHeight - 8)}px`;
  }
  function hideTip() { tipBox.hidden = true; }

  // ------------------------------------------------------------------ declutter
  // Markers that land within ~20 px of each other fan out in a ring around the spot.
  // During a gesture the layout runs at most every 60 ms (180 ms on phones) instead of every frame.
  let declutterQueued = false, lastDeclutter = 0;
  function queueDeclutter() {
    if (declutterQueued) return;
    declutterQueued = true;
    const wait = moving ? Math.max(0, lastDeclutter + (PHONE ? 180 : 60) - performance.now()) : 0;
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
      el.firstChild.title = `${count} events here. Click to zoom in.`;
    } else if (el.firstChild.title) el.firstChild.title = "";
  }
  function setHidden(d, hidden) { if (d.el.classList.contains("clustered") !== hidden) d.el.classList.toggle("clustered", hidden); }

  // Whole-globe view: 3+ events on one spot become the most important one's icon with a count.
  // Closer in (or for 2 events): they fan out around the spot instead.
  let clusterMode = false;
  const CLUSTER_ON = 0.55, CLUSTER_OFF = 0.45;
  function declutter() {
    if (!S.html.length) return;
    const pov = world.pointOfView();
    const horizon = (Math.acos(1 / (1 + pov.altitude)) * 180) / Math.PI - 1;
    const R = isMobile() ? 30 : 27; // about one marker width
    // Count bubbles appear above 0.55 and go away below 0.45, so a pinch near the line doesn't flicker.
    clusterMode = clusterMode ? pov.altitude > CLUSTER_OFF : pov.altitude > CLUSTER_ON;
    const vis = [];
    for (const d of S.html) {
      if (!d.el) continue;
      if (km(pov.lat, pov.lng, d.lat, d.lon) / 111.2 > horizon) { setOffset(d, 0, 0); if (d.isEvent) { setCluster(d, 0); setHidden(d, false); } continue; }
      const s = world.getScreenCoords(d.lat, d.lon, d.hAlt || 0.012);
      if (!s) continue;
      vis.push({ d, x: s.x, y: s.y });
    }
    vis.sort((a, b) => b.d.prio - a.d.prio);
    const group = (items, radius = R) => {
      const groups = [];
      for (const v of items) {
        const g = groups.find((gg) => Math.abs(gg.x - v.x) < radius && Math.abs(gg.y - v.y) < radius);
        if (g) g.m.push(v); else groups.push({ x: v.x, y: v.y, m: [v] });
      }
      return groups;
    };
    // Pass 1 (zoomed out only): 3+ events on one spot collapse into a count bubble.
    const hidden = new Set();
    vis.forEach((v) => { if (v.d.isEvent) setCluster(v.d, 0); });
    if (clusterMode) {
      // the selected event always stays visible
      for (const g of group(vis.filter((v) => v.d.isEvent && v.d.key !== `ev:${S.selectedId}`), R * 1.6)) {
        if (g.m.length < 3) continue;
        g.m.forEach((v, i) => { if (i === 0) setCluster(v.d, g.m.length); else hidden.add(v); });
      }
    }
    vis.forEach((v) => { if (v.d.isEvent) setHidden(v.d, hidden.has(v)); });
    // Pass 2: everything still showing (events, count bubbles, carriers) fans out where it overlaps.
    for (const g of group(vis.filter((v) => !hidden.has(v)))) {
      const n = g.m.length;
      g.m.forEach((v, i) => {
        if (n === 1) { setOffset(v.d, 0, 0); return; }
        let r, a;
        if (n <= 8) { r = 18 + 3.2 * n; a = -Math.PI / 2 + (i * 2 * Math.PI) / n; }
        else { r = 16 + 10 * Math.sqrt(i + 1); a = i * 2.39996; }
        setOffset(v.d, g.x - v.x + Math.cos(a) * r, g.y - v.y + Math.sin(a) * r);
      });
    }
    hidden.forEach((v) => setOffset(v.d, 0, 0));
  }

  // ------------------------------------------------------------------ tooltips
  function tipEvent(e) {
    const extra = e.alert ? `<span>${alertsText(e)}</span>`
      : e.wave && e.targets && e.targets.length > 1 ? `<span>${e.targets.length} locations</span>` : "";
    return `<div class="tip"><div class="tip-meta">${eventIcon(e)}<b>${esc(typeLabel(e))}</b><span>${esc(metaLine(e))}</span></div>
      <div class="tip-sum">${esc(e.summary)}</div>
      <div class="tip-foot"><span>${esc(STATUS[e.status].label)}</span>${e.possibly_old ? "<span>Possibly an old story</span>" : ""}${extra}<span>${esc(ago(e._t))}</span></div></div>`;
  }
  function tipCarrier(c) {
    return `<div class="tip"><div class="tip-meta">${iconBadge("carrier", "fleet")}<b>${esc(c.name)}</b><span>${esc(c.hull)}</span></div>
      <div class="tip-sum">${esc(carrierStatus(c))}${c.place ? `, ${esc(c.place)}` : ""}</div>
      <div class="tip-foot"><span>${c._asOf ? `As of ${esc(fmtDay(c._asOf))}` : "No position reports yet"}</span>${c.heading_to ? `<span>heading to ${esc(c.heading_to.place || "a stated destination")}</span>` : ""}</div></div>`;
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
  const CONTROL_RANK = { advance: 0, infiltration: 1, occupied: 2 };
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
  function tipControl(L) {
    const day = L.as_of ? new Date(L.as_of).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" }) : "";
    return `<div class="tip"><div class="tip-meta"><span class="control-swatch${L.style === "infiltration" ? " hatched" : L.style === "advance" ? " advance" : ""}" aria-hidden="true"></span><b>${esc(L.label)}</b></div>
      <div class="tip-foot"><span>${L.approx ? `Approximate: traced from the ${esc(L.source || "source")} map` : esc(L.source || "Source map")}${day ? `${L.approx ? " of" : ", as of"} ${esc(day)}` : ""}</span></div></div>`;
  }
  function showTipAt(x, y, html) {
    tipBox.innerHTML = html;
    tipBox.hidden = false;
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
    return at ? controlAt(at.lat, at.lng) : null;
  }
  function hideControlTip() { if (ctlTip) { ctlTip = false; hideTip(); } }
  globeCanvas.addEventListener("pointermove", (ev) => {
    if (ev.pointerType !== "mouse") return;
    const first = !ctlQueued;
    ctlQueued = ev;
    if (first) requestAnimationFrame(() => {
      const e = ctlQueued;
      ctlQueued = null;
      const L = !moving && !e.buttons ? controlUnder(e.clientX, e.clientY) : null;
      if (L) { showTipAt(e.clientX, e.clientY, tipControl(L)); ctlTip = true; } else hideControlTip();
    });
  });
  globeCanvas.addEventListener("pointerleave", hideControlTip);
  // a tap (not a drag) on held ground shows its name for a few seconds
  globeCanvas.addEventListener("pointerdown", (ev) => { if (ev.pointerType !== "mouse") ctlDown = { x: ev.clientX, y: ev.clientY, t: performance.now() }; });
  globeCanvas.addEventListener("pointerup", (ev) => {
    const d = ctlDown;
    ctlDown = null;
    if (!d || Math.hypot(ev.clientX - d.x, ev.clientY - d.y) > 8 || performance.now() - d.t > 500) return;
    const L = controlUnder(ev.clientX, ev.clientY);
    clearTimeout(ctlTimer);
    if (!L) return hideControlTip();
    showTipAt(ev.clientX, ev.clientY, tipControl(L));
    ctlTip = true;
    ctlTimer = setTimeout(hideControlTip, 3500);
  });

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
    if (!isMobile()) $("#feed").style.height = "";
    else setSheet(S.sheet, true);
    layout();
  });

  // ------------------------------------------------------------------ data
  // Live updates: every minute (and whenever the page comes back into view) a light request asks
  // whether events.json has changed, by its ETag or Last-Modified header; the file itself is
  // downloaded only when it has. Without those headers, the full file is fetched every REFRESH_MS.
  let dataStamp = null, lastFull = 0;
  const stampOf = (res) => res.headers.get("etag") || res.headers.get("last-modified");
  async function checkForUpdate() {
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
      const res = await fetch(url, { cache: "no-store" });
      if (!res.ok) throw new Error(res.status === 404 ? "missing" : `HTTP ${res.status}`);
      dataStamp = stampOf(res);
      lastFull = Date.now();
      data = await res.json();
    } catch (err) {
      showLoadError(err);
      return;
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
    if (data.brief) data.brief.generated_at = move(data.brief.generated_at);
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
      e._t = Date.parse(e.time || e.updated);   // when it happened: drives the time window, sorting, and "new"
      e._tu = Date.parse(e.updated || e.time);  // last report, shown in the detail view
      e.targets = Array.isArray(e.targets) ? e.targets.filter((t) => isFinite(t.lat) && isFinite(t.lon)) : [];
      const t = e.transfer || {};
      e._search = [e.summary, e.place, e.targets.map((x) => x.place).join(" "), typeLabel(e), countryName(e.attacker),
        countryName(e.country), countryName(t.supplier), countryName(t.recipient), t.what,
        regionWords(e), ...(e.reports || []).map((r) => r.source)].join(" ").toLowerCase();
    }
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
    if (S.firstLoad) S.theaterOn = new Set(theaters.map((t) => t.id));
    else theaters.forEach((t) => { if (!S.theaters.some((x) => x.id === t.id)) S.theaterOn.add(t.id); });
    S.theaters = theaters;
    S.hot = new Set(theaters.flatMap((t) => t.highlight || []));
    const before = S.data ? new Set(S.data.events.map((e) => e.id)) : null;
    S.data = data;
    if (before) {
      const fresh = data.events.filter((e) => !before.has(e.id) && onMap(e) && passes(e));
      if (fresh.length) announce(`${fresh.length} new ${fresh.length === 1 ? "event" : "events"} added`);
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
      const hash = decodeURIComponent(location.hash.slice(1));
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
    if (!h) { world.pointOfView({ lat: 30, lng: 38, altitude: isMobile() ? 3.0 : 2.2 }, reduceMotion ? 0 : 2600); return; }
    world.pointOfView({ lat: h.lat, lng: h.lon, altitude: isMobile() ? h.altitude + 0.5 : h.altitude }, reduceMotion ? 0 : 2800);
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
  const words = () => S.query.toLowerCase().split(/[^\p{L}\p{N}-]+/u).filter(Boolean).map((w) => (w.length > 3 ? w.replace(/s$/, "") : w));
  const matches = (e) => words().every((w) => e._search.includes(w));
  const unlisted = () => new Set(S.theaters.filter((t) => t.listed === false).map((t) => t.id));
  const theaterShown = (id) => S.theaterOn.has(id) || unlisted().has(id);
  function passes(e, ignoreTheater = false) {
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
      if (e.type !== "arms_transfer" || !t || e._t < since || !theaterShown(e.theater) || !S.statusOn.has(e.status)) continue;
      if (!matches(e)) continue;
      const kind = tkind(e);
      if (kind === "interdiction") continue;
      const money = isMoney(e);
      if (S.off.has(money ? "coin" : "crate")) continue;
      // forces sent to a region (a US command's area) form their own route, labeled with the region
      // (and forces leaving one, like tankers flying home from CENTCOM bases)
      const region = t.to && t.to.region ? t.to.place : null, fromRegion = t.from && t.from.region ? t.from.place : null;
      const key = `${fromRegion ? `${fromRegion}>` : ""}${t.supplier}>${region || t.recipient}${money ? "|aid" : ""}`;
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
    out.flows = [...flows.values()].map(summarize).sort((a, b) => (b.active - a.active) || b.deliveries - a.deliveries);
    out.pledges = [...pledges.values()].map(summarize).sort((a, b) => b.last - a.last);
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
  const carrierOnMap = (c) => S.layers.carriers && (!c.at_home || (c._moved && Date.now() - c._moved < 7 * DAY) || c.hull === S.selectedHull);
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
  function attackPaths(events) {
    const arcs = [];
    if (!S.layers.paths) return arcs;
    const push = (e, o, d, approx) => {
      const dist = km(o.lat, o.lon, d.lat, d.lon);
      if (dist < 25 || (approx && dist > 1800)) return;
      const a = Math.min(1, STATUS[e.status].alpha * fade(e) * (approx ? 0.65 : 1.15));
      arcs.push({ ref: e, sLat: o.lat, sLng: o.lon, eLat: d.lat, eLng: d.lon, kind: approx ? "strikeApprox" : "strike",
        color: [rgba(CAT_RGB.strike, 0.12), rgba(CAT_RGB.strike, a)], stroke: approx ? 0.26 : 0.42,
        ms: approx ? 3600 : 2200, seed: Math.random() });
    };
    for (const e of events) {
      if (arcs.length >= 180) break;
      // a wave is a drone and missile attack unless corrected (a landmine blast retyped as an explosion)
      if (e.type !== "missile_drone" && e.type !== "air_defense" && !originsOf(e).length) continue;
      const origins = originsOf(e);
      if (e.wave) {
        for (const d of (e.targets.length ? e.targets.slice(0, 16) : [e])) {
          const o = nearest(origins, d);
          if (o) push(e, o, d, false);
          else if (ANCHORS[e.attacker]) push(e, nearest(ANCHORS[e.attacker], d), d, true);
        }
      } else if (origins.length) origins.slice(0, 3).forEach((o) => push(e, o, e, false));
      else if (e.type === "missile_drone" && ANCHORS[e.attacker]) push(e, nearest(ANCHORS[e.attacker], e), e, true);
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
      out.push({ ...base, sLat: prev.lat, sLng: prev.lon, eLat: p.lat, eLng: p.lon, alt: hugAlt(km(prev.lat, prev.lon, p.lat, p.lon)) + lift });
      prev = p;
    }
    return out;
  }

  function supplyArcs(flows) {
    const arcs = [];
    for (const f of flows) {
      const named = f.from && f.to && !f.to.region && !f.from.region; // a route to or from a whole region is drawn faint
      const start = f.from || countryCenter(f.supplier), end = f.to || countryCenter(f.recipient);
      if (!start || !end) continue;
      const pts = [start, ...(named ? f.via : []), end];
      const stroke = clamp(0.22 + 0.2 * Math.log2(1 + f.deliveries), 0.22, 1.1);
      const alpha = (named ? 0.75 : 0.4) * (S.selectedFlow === f.key ? 1.3 : 1);
      const sea = f.modes.length === 1 && f.modes[0] === "sea";
      for (let i = 0; i < pts.length - 1; i++) {
        const a = pts[i], b = pts[i + 1];
        if (km(a.lat, a.lon, b.lat, b.lon) < 25) continue;
        const lift = sea ? 0.002 : 0.012;
        arcs.push(...surfaceArcs(a, b, { flow: f, kind: f.status === "corroborated" ? "flow" : "flowDashed", color: rgba(f.money ? CAT_RGB.aid : CAT_RGB.supply, Math.min(1, alpha)), stroke, ms: 0, seed: 0 }, lift));
        if (f.active) arcs.push(...surfaceArcs(a, b, { flow: f, kind: "particles", color: [rgba([220, 250, 252], 0.25), rgba([220, 250, 252], 0.95)], stroke: Math.max(0.3, stroke * 0.8), ms: 1800, seed: Math.random() }, lift + 0.001));
      }
    }
    return arcs;
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
    for (let i = 0; i < pts.length - 1; i++) out.push(...surfaceArcs(pts[i], pts[i + 1], base, lift));
    return out;
  };

  function fleetArcs() {
    const arcs = [];
    for (const c of S.fleet) {
      if (!carrierOnMap(c)) continue;
      // where it came from: faint and still
      if (c.prev && c._moved && Date.now() - c._moved < 14 * DAY && km(c.prev.lat, c.prev.lon, c._lat, c._lon) > 100) {
        arcs.push(...alongSea({ lat: c.prev.lat, lon: c.prev.lon }, { lat: c._lat, lon: c._lon },
          { carrier: c, kind: "track", color: rgba(CAT_RGB.fleet, 0.28), stroke: 0.2, ms: 0, seed: 0 }, 0.002));
      }
      // where it is headed: dashes flow from the last reported position toward the stated destination
      if (c.heading_to && km(c._lat, c._lon, c.heading_to.lat, c.heading_to.lon) > 100) {
        arcs.push(...alongSea({ lat: c._lat, lon: c._lon }, c.heading_to,
          { carrier: c, kind: "plan", color: rgba(CAT_RGB.fleet, 0.7), stroke: 0.3, ms: 6000, seed: 0 }, 0.002));
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
  function render() {
    if (!S.data) return;
    const events = visibleEvents();
    const mapEvents = markerPick(events.filter(onMap));
    S.supply = buildSupply();

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
      e.targets.slice(0, 40).forEach((t) => {
        if (Math.abs(t.lat - e.lat) < 1e-4 && Math.abs(t.lon - e.lon) < 1e-4) return;
        dots.push({ lat: t.lat, lon: t.lon, place: t.place, ref: e, alert });
      });
    }
    world.pointsData(dots);

    // rings: impacts at recent wave targets, the selected item
    const rings = [];
    if (!reduceMotion) {
      for (const e of mapEvents) {
        if (!e.wave || !isLive(e)) continue;
        for (const t of e.targets.slice(0, 12)) {
          if (rings.length >= 30) break;
          rings.push({ lat: t.lat, lon: t.lon, rgb: CAT_RGB.strike, alpha: 0.55, max: 1.6, speed: 1.2, period: 1500 + Math.random() * 1500 });
        }
      }
    }
    const sel = S.selectedId && events.find((e) => e.id === S.selectedId);
    if (sel) rings.push({ lat: sel.lat, lon: sel.lon, rgb: [234, 240, 246], alpha: 0.85, max: 4, speed: reduceMotion ? 0 : 2.2, period: 1200 });
    const selC = S.selectedHull && S.fleet.find((c) => c.hull === S.selectedHull);
    if (selC) rings.push({ lat: selC._lat, lon: selC._lon, rgb: [234, 240, 246], alpha: 0.85, max: 4, speed: reduceMotion ? 0 : 2.2, period: 1200 });
    world.ringsData(rings);

    const routeArcs = [...supplyArcs(S.supply.flows), ...(S.layers.carriers ? fleetArcs() : [])];
    world.arcsData([...attackPaths(mapEvents), ...routeArcs, ...hitArcs(routeArcs)]);
    updateActive(mapEvents);
    renderCounts();
    renderTally(events);
    renderSideLists();
    if ($("#detail").hidden) renderFeed(events);
    queueDeclutter();
  }

  // Who drew the control shapes, and when: credited under the legend, with a link to the source's map.
  function renderControlNote() {
    const el = $("#controlNote");
    const layers = controlLayers();
    el.hidden = !layers.length;
    if (!layers.length) return;
    const bySource = new Map();
    const traced = layers.filter((L) => L.approx);
    for (const L of layers.filter((x) => !x.approx)) {
      const s = bySource.get(L.source) || bySource.set(L.source, { link: L.link, asOf: "", labels: [] }).get(L.source);
      if (L.as_of && L.as_of > s.asOf) s.asOf = L.as_of;
      s.labels.push(L.label);
    }
    const day = (iso) => new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", timeZone: "UTC" });
    let names = null;
    try { names = new Intl.DisplayNames(["en"], { type: "region" }); } catch (_) { /* older browsers: codes */ }
    const country = (c) => (c ? (names ? names.of(c) : c) : "");
    const exact = layers.filter((L) => !L.approx);
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
    const key = [...active].sort().join(",");
    if (key !== S.activeKey) {
      S.activeKey = key;
      S.active = active;
      world.pathColor(pathColorOf);
    }
    paintLand();  // repaints only when active or highlighted countries changed (or the control layer)
    renderControlNote();
  }

  function renderTally(events) {
    const span = { 6: "6 hours", 24: "24 hours", 72: "3 days", 168: "7 days" }[S.windowH];
    const fighting = events.filter(onMap);
    $("#tally").innerHTML = `<strong>${fighting.length}</strong> events in the last ${span}, <strong>${fighting.filter((e) => e.status === "corroborated").length}</strong> corroborated`;
  }

  // ------------------------------------------------------------------ feed and side lists
  function itemHtml(e, names) {
    const extra = [];
    if (e.alert) extra.push(alertsText(e));
    if ((e.wave || e.alert) && e.targets.length) extra.push(`${e.targets.length} ${e.targets.length === 1 ? "location" : "locations"}`);
    if (e.wave && e.launched) extra.push(`${e.launched} launched`);
    if (e.legal_basis) extra.push("Legal basis stated");
    return `<li><button class="item sev-${e.severity}${isNew(e) ? " is-new" : ""}" type="button" data-id="${esc(e.id)}" ${e.id === S.selectedId ? 'aria-current="true"' : ""}>
      ${eventIcon(e)}
      <span>
        <span class="item-meta"><span class="item-type">${esc(typeLabel(e))}</span><span class="item-place">${esc(metaLine(e))}</span><time datetime="${esc(e.time)}">${esc(agoShort(e._t))}</time></span>
        <span class="item-summary">${esc(e.summary)}</span>
        <span class="item-foot"><span class="conf-text conf-${STATUS[e.status].conf}">${esc(STATUS[e.status].label)}</span><span>${esc(names[e.theater] || e.theater)}</span>
          ${extra.map((x) => `<span>${esc(x)}</span>`).join("")}<span>${e.sources_count} ${e.sources_count === 1 ? "source" : "sources"}</span></span>
      </span></button></li>`;
  }

  // Situation brief: machine-written from the pipeline's own events, independent of the filters.
  function briefHtml() {
    const b = S.data && S.data.brief;
    if (!b || !(b.bullets || []).length) return "";
    const byId = new Map(S.data.events.map((e) => [e.id, e]));
    const cites = (ids) => {
      const found = (ids || []).filter((i) => byId.has(i));
      return found.length ? `<span class="cites">${found.map((i) => {
        const e = byId.get(i);
        const label = (e.place || typeLabel(e)).split(",")[0].slice(0, 22);
        return `<button class="cite" type="button" data-id="${esc(i)}" title="Open: ${esc(e.summary)}">${esc(label)}</button>`;
      }).join("")}</span>` : "";
    };
    return `<li class="brief"><section aria-labelledby="briefTitle">
      <div class="brief-head"><h3 id="briefTitle">What changed in the last ${esc(b.window_hours || 6)} hours</h3>
        <time datetime="${esc(b.generated_at)}">Written ${esc(ago(Date.parse(b.generated_at)))}</time></div>
      <ul class="brief-list">${(b.bullets || []).map((x) => `<li>${esc(x.text)}${cites(x.ids)}</li>`).join("")}</ul>
      <p class="brief-note">Machine-written from corroborated events only. Open the cited events before relying on it.</p>
    </section></li>`;
  }

  const FEED_PAGE = 250;  // the list is built in pages; a busy week has 1,000+ events
  function renderFeed(events) {
    const list = $("#feedList");
    const names = Object.fromEntries(S.theaters.map((t) => [t.id, t.name]));
    const top = briefHtml();
    $("#feedCount").textContent = `${events.length}`;
    if (!events.length) {
      list.innerHTML = top + (S.data.events.length
        ? `<li class="empty"><strong>Nothing matches these filters.</strong>Widen the time window, or turn more kinds of events, theaters, or confidence levels back on.</li>`
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
    // carriers: all 11, at sea first
    const at = S.fleet.filter((c) => !c.at_home).length;
    $("#fleetNote").textContent = S.fleet.length ? `${at} of ${S.fleet.length} at sea` : "";
    $("#fleetList").innerHTML = S.fleet.length ? S.fleet.map((c) => `
      <li><button class="side-row${c.hull === S.selectedHull ? " is-selected" : ""}${c.at_home ? " is-home" : ""}" type="button" data-hull="${esc(c.hull)}">
        ${iconBadge("carrier", "fleet", c.at_home ? "outline" : "solid", "ico-sm")}
        <span class="side-name">${esc(c.short || c.name)}</span>
        <span class="side-meta">${esc(c.heading_to ? `→ ${c.heading_to.place || "en route"}` : c.at_home ? (c.place || "").split(/[,(]/)[0].trim() : c.place || "")}</span>
      </button></li>`).join("")
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

  const CROSSHAIR = `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><circle cx="8" cy="8" r="4.5"/><path d="M8 1v3M8 12v3M1 8h3M12 8h3"/></svg>`;
  function renderTheaters() {
    // A theater with listed: false (worldwide treaty and sanctions steps) has no row of its own: its
    // events are diplomacy, shown and hidden with "Diplomacy, legal" in the map key.
    $("#theaterList").innerHTML = S.theaters.filter((t) => t.listed !== false).map((t) => `
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
    for (const e of S.data.events) if (onMap(e) && passes(e, true)) counts[e.theater] = (counts[e.theater] || 0) + 1;
    document.querySelectorAll("[data-count]").forEach((el) => { el.textContent = counts[el.dataset.count] || 0; });
    const now = Date.now(), tempo = {};
    for (const e of S.data.events) {
      if (!onMap(e)) continue;
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
      if (!onMap(e) || e._t < now - S.windowH * HOUR || !theaterShown(e.theater)) continue;
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
    S.selectedId = null; S.selectedHull = null; S.selectedFlow = null;
    history.replaceState(null, "", location.pathname + location.search);
    hideDetail();
    render();
    if (S.lastFocus) { const again = document.querySelector(`[data-id="${CSS.escape(S.lastFocus)}"]`); if (again) again.focus(); }
  }
  const zoomTo = (lat, lng, altitude) => world.pointOfView({ lat, lng, altitude }, reduceMotion ? 0 : 1100);

  const reportsHtml = (reports) => `<h2 class="reports-title">Reports (${reports.length})</h2><ul class="reports">${reports.map((r) => `
    <li class="report ${r.side ? "sided" : ""}"><div class="report-head"><span class="report-src">${esc(r.source)}</span><span>${esc(PLATFORM[r.platform] || r.platform)}</span>
      <span>${esc(KIND[r.kind] || r.kind)}${r.side ? `, aligned with ${esc(r.side)}` : ""}</span><span>${esc(ago(Date.parse(r.time)))}</span></div>
      <p>${esc(r.summary)}</p><a href="${esc(safeUrl(r.url))}" target="_blank" rel="noopener noreferrer">Open the original post</a></li>`).join("")}</ul>`;

  function select(id, fly) {
    const e = S.data && S.data.events.find((x) => x.id === id);
    if (!e) return;
    S.selectedId = id; S.selectedHull = null; S.selectedFlow = null;
    markViewed(id);
    history.replaceState(null, "", "#" + encodeURIComponent(id));
    if (fly) zoomTo(e.lat, e.lon, Math.min(world.pointOfView().altitude, (e.wave || e.alert) && e.targets.length > 3 ? 1.45 : 1.15));
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
      facts.push(`<span>From <b>${esc(transferFrom(t))}</b> to <b>${esc(transferTo(t))}</b>${MODE[t.mode] ? " " + esc(MODE[t.mode]) : ""}</span>`);
      if (t.from || t.to) facts.push(`<span>Route <b>${esc((t.from && t.from.place) || "not named")}</b> → <b>${esc((t.to && t.to.place) || "not named")}</b></span>`);
      if (t.what) facts.push(`<span>Cargo <b>${esc(t.what)}</b></span>`);
      if (t.value_usd) facts.push(`<span>Value <b>${esc(fmtMoney(t.value_usd))}</b> (reported)</span>`);
    }
    const origins = originsOf(e);
    if (!e.wave && origins.length) facts.push(`<span>Launched from <b>${esc(origins.map((o) => o.place || "an unnamed site").join(", "))}</b></span>`);
    const news = S.data.heat.filter((c) => km(e.lat, e.lon, c.lat, c.lon) <= (e.approx ? 60 : 30)).flatMap((c) => c.urls || []).slice(0, 4);
    const reports = (e.reports || []).slice().sort((a, b) => Date.parse(b.time) - Date.parse(a.time));
    const where = e.wave || e.alert ? `${esc(metaLine(e))}, ${esc(theaterName)}`
      : `${esc(e.place || "Unnamed location")}, ${esc(theaterName)} ${e.approx ? '<span class="approx">(approximate location)</span>' : ""}`;
    const waveBlock = e.wave ? `
      <h2 class="reports-title">Locations (${e.targets.length})</h2>
      ${e.targets.length ? `<ul class="targets">${e.targets.map((x) => `<li><button class="target" type="button" data-goto="${x.lat},${x.lon}"><span>${esc(x.place || "Unnamed place")}</span>
        <span class="target-meta">${x.reports} ${x.reports === 1 ? "report" : "reports"}${x.killed ? `, ${x.killed} killed` : ""}</span></button></li>`).join("")}</ul>` : `<p class="muted">No specific locations reported yet.</p>`}
      <h2 class="reports-title">Launch areas</h2>
      <p class="muted">${origins.length ? esc(origins.map((o) => o.place || "unnamed site").join(", ")) : "Not named in the reports so far. Lines on the map start from the nearest known launch area and are drawn faint."}</p>` : "";
    const alertBlock = e.alert ? `
      <p class="muted">Warnings that drones or missiles were in flight, grouped into one marker per country per day. They show where a threat was reported heading, not what was hit. Strikes and interceptions appear as their own events.</p>
      <h2 class="reports-title">Places named (${e.targets.length})</h2>
      ${e.targets.length ? `<ul class="targets">${e.targets.map((x) => `<li><button class="target" type="button" data-goto="${x.lat},${x.lon}"><span>${esc(x.place || "Unnamed place")}</span>
        <span class="target-meta">${x.reports} ${x.reports === 1 ? "alert" : "alerts"}</span></button></li>`).join("")}</ul>` : `<p class="muted">No specific places named.</p>`}` : "";
    showDetail(`
      <div class="detail-type">${eventIcon(e)}${esc(typeLabel(e))}</div>
      ${(e.corrected || []).length ? `<div class="corrected"><span class="corrected-tag">Corrected</span><ul>${e.corrected.map((c) => `<li>${esc(c.change)}: ${esc(c.note)}</li>`).join("")}</ul></div>` : ""}
      <h3>${esc(e.summary)}</h3>
      <p class="detail-where">${where}<br>${e.alert ? "First alert" : "Happened"} ${esc(fmtTime(e._t))}${e._tu - e._t > 30 * 60e3 ? `, latest report ${esc(ago(e._tu))}` : ""}</p>
      ${e.possibly_old ? `<div class="verdict verdict--doubt"><span class="conf-swatch conf-dashed" aria-hidden="true"></span><div><strong>Possibly an old story</strong><p>Only one outlet has this, and a news search found earlier coverage of the same topic but nothing current from other outlets. It may be an old article republished with a new date. It stays on the map, quieter, and is confirmed if another source reports it.</p></div></div>` : ""}
      <div class="verdict"><span class="conf-swatch conf-${STATUS[e.status].conf}" aria-hidden="true"></span><div><strong>${esc(STATUS[e.status].label)}</strong><p>${esc(STATUS[e.status].note(e.sources_count, e.news_nearby))}</p></div></div>
      ${facts.length ? `<div class="facts">${facts.join("")}</div>` : ""}
      ${e.legal_basis ? `<div class="legal-basis"><span>Stated legal basis</span><strong>${esc(e.legal_basis)}</strong><p>As reported by the sources below. The dashboard records claimed justifications; it does not assess them.</p></div>` : ""}
      ${waveBlock}${alertBlock}
      ${reportsHtml(reports)}
      ${news.length ? `<h2 class="reports-title">News coverage nearby (${e.news_nearby || news.length} outlets)</h2>
        <ul class="news-links">${news.map((u) => `<li><a href="${esc(safeUrl(u))}" target="_blank" rel="noopener noreferrer">${esc(u.replace(/^https?:\/\/(www\.)?/, "").slice(0, 80))}</a></li>`).join("")}</ul>` : ""}
      <p class="event-id">Event id <code>${esc(e.id)}</code></p>
    `, refresh);
  }

  function selectFlow(key, pledge = false) {
    const s = S.supply;
    const f = (pledge ? s.pledges : s.flows).find((x) => x.key === key) || s.flows.find((x) => x.key === key) || s.pledges.find((x) => x.key === key);
    if (!f) return;
    S.selectedFlow = key; S.selectedId = null; S.selectedHull = null;
    const a = f.from || countryCenter(f.supplier), b = f.to || countryCenter(f.recipient);
    if (a && b) { const mid = slerp(a, b, 0.5); zoomTo(mid.lat, mid.lon, clamp(0.6 + km(a.lat, a.lon, b.lat, b.lon) / 5000, 1.1, 2.6)); }
    const isPledge = s.pledges.includes(f);
    const route = f.from && f.to ? `${esc(f.from.place || "origin")}${f.via.length ? ` → ${f.via.map((v) => esc(v.place || "hub")).join(" → ")}` : ""} → ${esc(f.to.place || "destination")}`
      : "Not named in reports. The line runs between the two countries and is drawn faint.";
    showDetail(`
      <div class="detail-type">${flowBadge(f)}${f.money ? (isPledge ? "Financial aid pledged" : `Financial aid, ${windowText()}`) : isPledge ? "Pledged aid" : `Supply route, ${windowText()}`}</div>
      <h3>${esc(flowFrom(f))} → ${esc(flowTo(f))}</h3>
      <p class="detail-where">${isPledge ? `${f.events.length} ${f.events.length === 1 ? "announcement" : "announcements"}` : `${f.deliveries} ${f.deliveries === 1 ? "delivery" : "deliveries"} reported`}, last ${esc(ago(f.last))}</p>
      <div class="verdict"><span class="conf-swatch conf-${STATUS[f.status].conf}" aria-hidden="true"></span><div><strong>${esc(STATUS[f.status].label)}</strong><p>Best confidence among the reports below. On the map, solid lines are corroborated and dashed lines rest on single sources.</p></div></div>
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
  function legendChanged() {
    const items = document.querySelectorAll("[data-legend]");
    items.forEach((b) => b.setAttribute("aria-pressed", String(!S.off.has(b.dataset.legend))));
    S.layers = { paths: !S.off.has("paths"), supply: !S.off.has("crate"), carriers: !S.off.has("carrier") };
    $("#legendReset").hidden = !S.off.size;
    $("#legendNone").hidden = S.off.size >= items.length;
    render();
  }

  function buildStaticControls() {
    const item = (key, swatch, label) => `<li><button class="legend-item" type="button" data-legend="${key}" aria-pressed="true" title="Show or hide ${esc(label.toLowerCase())}">${swatch}<span>${esc(label)}</span></button></li>`;
    $("#legend").innerHTML = LEGEND.map(([icon, cat, label]) => item(icon, iconBadge(icon, cat, "solid", "ico-sm"), label)).join("")
      + item("paths", '<span class="line-swatch line-strike" aria-hidden="true"></span>', "Launch path");
    $("#windowSeg").innerHTML = WINDOWS.map(([label, h]) => `<button type="button" data-window="${h}" aria-pressed="${h === S.windowH}">${label}</button>`).join("");
    $("#statusList").innerHTML = Object.entries(STATUS).map(([id, s]) => `
      <li><label class="check"><input type="checkbox" data-status="${id}" checked><span class="conf-swatch conf-${s.conf}" aria-hidden="true"></span><span class="label">${esc(s.label)}</span><span class="count" data-status-count="${id}"></span></label></li>`).join("");
  }

  function setWindow(h) {
    S.windowH = h;
    document.querySelectorAll("[data-window]").forEach((x) => x.setAttribute("aria-pressed", String(Number(x.dataset.window) === h)));
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
    $("#windowSeg").addEventListener("click", (ev) => {
      const b = ev.target.closest("[data-window]");
      if (b) { setWindow(Number(b.dataset.window)); render(); }
    });
    $("#filters").addEventListener("change", (ev) => {
      const t = ev.target;
      if (t.dataset.theater) t.checked ? S.theaterOn.add(t.dataset.theater) : S.theaterOn.delete(t.dataset.theater);
      if (t.dataset.status) t.checked ? S.statusOn.add(t.dataset.status) : S.statusOn.delete(t.dataset.status);
      render();
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
    $("#filters").addEventListener("click", (ev) => {
      const hull = ev.target.closest("[data-hull]");
      if (hull) { selectCarrier(hull.dataset.hull, true); return; }
      const flow = ev.target.closest("[data-flow]");
      if (flow) { selectFlow(flow.dataset.flow, !!flow.dataset.pledge); if (isMobile()) toggleFilters(false); return; }
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
      if (ev.target.closest("[data-more]")) { S.feedLimit += FEED_PAGE; render(); return; }
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
        if (!$("#detail").hidden) closeDetail();
        else if ($("#filters").classList.contains("open")) toggleFilters(false);
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
  try {
    buildStaticControls();
    renderTheaters();
    wire();
    if (isMobile()) setSheet(1, true);
    layout();
    load();
  } catch (err) {
    fatal(err);
  }
  if (!DEMO) {
    setInterval(checkForUpdate, 60e3);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) checkForUpdate(); });
  }
  setInterval(() => {
    updateFreshness();
    document.querySelectorAll("#feedList time").forEach((t) => { t.textContent = agoShort(Date.parse(t.dateTime)); });
  }, 30e3);
})();
