/* HeatShield rider app: plain JS, no build step. */
(() => {
  const CFG = window.HEAT_CONFIG || {};
  const API = (CFG.API_URL || "").replace(/\/$/, "");
  const $ = (id) => document.getElementById(id);
  const params = new URLSearchParams(location.search);

  // ------------------------------------------------------------ text
  const T = {
    en: {
      heat: "Heat load", redMin: "min in red", breaks: "breaks", start: "Start shift", end: "End shift",
      unwell: "I feel unwell", hint: "Long-press the map to add water or shade.", go: "Directions", ok: "OK",
      why: "Know when the heat is too much, and where the nearest water is. We use your location only during a shift.",
      language: "Language", vehicle: "Vehicle", bike: "Motorbike", cycle: "Bicycle", continue: "Continue",
      summary: "Shift summary", summaryHint: "Your record of heat exposure. Share it with your union or platform when asking for paid heat breaks.",
      addTitle: "Add a point here?", water: "Water", shade: "Shade", rest: "Rest stop", cancel: "Cancel",
      low: "Low", moderate: "Moderate", high: "High", critical: "Critical", green: "Safe", yellow: "Caution", red: "Danger", unknown: "No data",
      kind_idle: "While you wait", kind_onroute: "On your way", kind_critical: "Stop now", kind_break: "Break logged", kind_unwell: "First aid",
      away: (d, c) => `${d} m ${c}`, noPoint: "No water point nearby yet. Add one when you find it.",
      sMinutes: "minutes on shift", sRed: "minutes in red zones", sBreaks: "breaks taken", sPeak: "hottest WBGT °C",
      added: "Point added. Another rider will confirm it.", locDenied: "Location is needed during a shift.",
      noApi: "API_URL is not set in config.js.",
    },
    hi: {
      heat: "गर्मी का असर", redMin: "मिनट लाल ज़ोन में", breaks: "ब्रेक", start: "शिफ्ट शुरू करें", end: "शिफ्ट खत्म करें",
      unwell: "मेरी तबीयत ठीक नहीं", hint: "पानी या छाया जोड़ने के लिए मैप को दबाकर रखें।", go: "रास्ता देखें", ok: "ठीक है",
      why: "जानिए गर्मी कब बहुत ज़्यादा है और सबसे पास पानी कहाँ है। आपकी लोकेशन सिर्फ़ शिफ्ट के दौरान ली जाती है।",
      language: "भाषा", vehicle: "वाहन", bike: "मोटरसाइकिल", cycle: "साइकिल", continue: "आगे बढ़ें",
      summary: "शिफ्ट का सारांश", summaryHint: "यह आपकी गर्मी का रिकॉर्ड है। पेड हीट ब्रेक माँगते समय यूनियन या प्लेटफ़ॉर्म को दिखाएँ।",
      addTitle: "यहाँ जगह जोड़ें?", water: "पानी", shade: "छाया", rest: "आराम की जगह", cancel: "रद्द करें",
      low: "कम", moderate: "मध्यम", high: "ज़्यादा", critical: "ख़तरनाक", green: "सुरक्षित", yellow: "सावधान", red: "ख़तरा", unknown: "डेटा नहीं",
      kind_idle: "इंतज़ार के दौरान", kind_onroute: "रास्ते में", kind_critical: "अभी रुकें", kind_break: "ब्रेक दर्ज", kind_unwell: "प्राथमिक उपचार",
      away: (d, c) => `${d} मीटर ${c}`, noPoint: "पास में अभी कोई पानी की जगह नहीं है। मिले तो जोड़ दें।",
      sMinutes: "मिनट शिफ्ट", sRed: "मिनट लाल ज़ोन में", sBreaks: "ब्रेक लिए", sPeak: "सबसे ज़्यादा WBGT °C",
      added: "जगह जुड़ गई। कोई और राइडर इसकी पुष्टि करेगा।", locDenied: "शिफ्ट के दौरान लोकेशन ज़रूरी है।",
      noApi: "config.js में API_URL नहीं है।",
    },
  };
  const NUDGE_KIND = { idle: "kind_idle", "on-route": "kind_onroute", critical: "kind_critical", "break-logged": "kind_break", unwell: "kind_unwell" };
  const BAND_COLOR = { green: "#2f8f4e", yellow: "#e0a100", red: "#d7301f", unknown: "#9a9083" };

  // ------------------------------------------------------------ state
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
  };
  const S = {
    riderId: store.get("riderId") || (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random()),
    lang: store.get("lang") || "hi",
    vehicle: store.get("vehicle") || "bike",
    server: null, index: null, shift: null, lastPos: null, watchId: null, timer: null,
    hourKey: null, audio: {}, wakeLock: null, replay: false, demoRunning: false, addAt: null, marker: null,
  };
  store.set("riderId", S.riderId);
  const t = (k) => (T[S.lang] && T[S.lang][k]) || T.en[k] || k;

  // ------------------------------------------------------------ api
  async function api(path, body, query) {
    if (!API) { toast(t("noApi")); throw new Error("no api"); }
    const url = API + path + (query ? "?" + new URLSearchParams(query) : "");
    // text/plain keeps POSTs "simple", so the browser sends no CORS preflight
    const res = await fetch(url, body ? { method: "POST", headers: { "content-type": "text/plain" }, body: JSON.stringify(body) } : {});
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || res.status);
    return data;
  }

  // ------------------------------------------------------------ map
  const style = CFG.LOCATION_API_KEY
    ? `https://maps.geo.${CFG.LOCATION_REGION}.amazonaws.com/v2/styles/Standard/descriptor?key=${CFG.LOCATION_API_KEY}`
    : { version: 8, sources: { osm: { type: "raster", tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"], tileSize: 256,
        attribution: "© OpenStreetMap contributors" } }, layers: [{ id: "osm", type: "raster", source: "osm" }] };
  const map = new maplibregl.Map({ container: "map", style, center: CFG.CENTER || [78.4867, 17.385], zoom: 12.5, attributionControl: { compact: true } });

  map.on("load", () => {
    map.addSource("heat", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    map.addLayer({ id: "heat-fill", type: "fill", source: "heat", paint: {
      "fill-color": ["match", ["get", "band"], "red", BAND_COLOR.red, "yellow", BAND_COLOR.yellow, BAND_COLOR.green],
      "fill-opacity": 0.38 } });
    map.addSource("points", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    map.addLayer({ id: "points", type: "circle", source: "points", paint: {
      "circle-radius": 7, "circle-stroke-width": 2, "circle-stroke-color": "#fff",
      "circle-color": ["match", ["get", "kind"], "water", "#1f6fd1", "shade", "#2f8f4e", "fuel", "#14110f", "#6b6257"] } });
    map.addSource("guide", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    map.addLayer({ id: "guide", type: "line", source: "guide", paint: { "line-color": "#1f6fd1", "line-width": 4, "line-dasharray": [1.5, 1] } });
    map.addSource("path", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    map.addLayer({ id: "path", type: "circle", source: "path", paint: {
      "circle-radius": 4, "circle-color": ["match", ["get", "band"], "red", BAND_COLOR.red, "yellow", BAND_COLOR.yellow, "green", BAND_COLOR.green, BAND_COLOR.unknown] } });
    boot();
  });

  async function boot() {
    applyLang();
    if (!store.get("onboarded")) showOnboarding();
    if (params.has("demo")) $("demo").hidden = false;
    try {
      S.server = await api("/config");
      S.index = await api("/grid/index");
      $("replayDateLbl").textContent = S.server.replayDate;
      S.replay = params.has("replay");
      $("replayToggle").checked = S.replay;
      await refreshGrid();
      renderForecast();
      loadPoints();
    } catch (e) { console.warn(e); }
    map.on("moveend", debounce(loadPoints, 400));
    setInterval(() => { if (!S.shift) refreshGrid(); }, 10 * 60 * 1000);
  }

  function istHourKey(ms) {
    const off = (S.server ? S.server.timezoneOffsetMin : 330) * 60000;
    return new Date(ms + off).toISOString().slice(0, 13);
  }

  async function refreshGrid(key) {
    const hourKey = key || (S.replay ? `R${S.server.replayDate}T${String(S.server.replayStartHour).padStart(2, "0")}` : istHourKey(Date.now()));
    if (hourKey === S.hourKey && key) return;
    S.hourKey = hourKey;
    const fc = await api("/grid", null, { hour: hourKey });
    if (map.getSource("heat")) map.getSource("heat").setData(fc);
    const b = $("replayBadge");
    b.hidden = !hourKey.startsWith("R");
    if (!b.hidden) b.textContent = `Replay ${hourKey.slice(1, 11)} · ${hourKey.slice(-2)}:00`;
  }

  function renderForecast() {
    const box = $("forecast");
    box.innerHTML = "";
    const hours = S.replay ? (S.index.replay || {})[S.server.replayDate] || {} : S.index.forecast || {};
    Object.keys(hours).sort().slice(0, 4).forEach((k) => {
      const h = hours[k];
      const band = h.red / h.cells > 0.3 ? "red" : (h.red + h.yellow) / h.cells > 0.3 ? "yellow" : "green";
      const el = document.createElement("span");
      el.className = "fc " + band;
      el.textContent = `${k.slice(-2)}:00 ${t(band)}`;
      box.appendChild(el);
    });
  }

  async function loadPoints() {
    const c = map.getCenter();
    try {
      const r = await api("/points/near", null, { lat: c.lat.toFixed(5), lon: c.lng.toFixed(5), limit: 40 });
      S.points = r.points;
      map.getSource("points").setData({ type: "FeatureCollection", features: r.points.map((p) => ({
        type: "Feature", geometry: { type: "Point", coordinates: [p.lon, p.lat] }, properties: { kind: p.kind, name: p.name } })) });
    } catch (e) { console.warn(e); }
  }

  // ------------------------------------------------------------ audio
  async function preloadAudio() {
    const clips = ["shift-start", "idle", "on-route", "critical", "break-logged", "unwell"];
    await Promise.all(clips.map(async (c) => {
      const key = S.lang + "/" + c;
      if (S.audio[key]) return;
      try {
        const r = await api("/audio", null, { clip: c, lang: S.lang });
        const bytes = Uint8Array.from(atob(r.b64), (ch) => ch.charCodeAt(0));
        S.audio[key] = { url: URL.createObjectURL(new Blob([bytes], { type: r.mime })), text: r.text };
      } catch (e) { console.warn("audio", c, e); }
    }));
  }
  function play(clip) {
    const a = S.audio[S.lang + "/" + clip];
    if (!a) return;
    const p = $("player");
    p.src = a.url;
    p.play().catch(() => {});
  }
  function clipText(clip) { return (S.audio[S.lang + "/" + clip] || {}).text || ""; }

  // ------------------------------------------------------------ shift
  async function startShift(opts = {}) {
    $("player").play().catch(() => {}); // unlock audio inside the user tap
    const startTs = opts.ts || Date.now();
    const r = await api("/shifts/start", { riderId: S.riderId, lang: S.lang, vehicle: S.vehicle, ts: startTs,
      replayDate: S.replay ? S.server.replayDate : null });
    S.shift = { id: r.shiftId, startTs };
    $("startBtn").hidden = true; $("endBtn").hidden = false; $("unwellBtn").hidden = false;
    map.getSource("path").setData({ type: "FeatureCollection", features: [] });
    await preloadAudio();
    play("shift-start");
    try { S.wakeLock = await navigator.wakeLock.request("screen"); } catch { /* not supported */ }
    if (opts.demo) return;
    if (!navigator.geolocation) return toast(t("locDenied"));
    S.watchId = navigator.geolocation.watchPosition(
      (pos) => { S.lastPos = { lat: pos.coords.latitude, lon: pos.coords.longitude, acc: pos.coords.accuracy }; },
      () => toast(t("locDenied")), { enableHighAccuracy: true, maximumAge: 15000 });
    setTimeout(sendLivePing, 4000);
    S.timer = setInterval(sendLivePing, 60000);
  }

  function sendLivePing() {
    if (S.lastPos) ping({ lat: S.lastPos.lat, lon: S.lastPos.lon, accuracyM: S.lastPos.acc, ts: Date.now(), online: true });
  }

  async function ping(p) {
    if (!S.shift) return;
    const r = await api("/shifts/ping", { riderId: S.riderId, shiftId: S.shift.id, ...p });
    moveRider(p.lat, p.lon);
    addPathDot(p.lon, p.lat, r.band);
    if (r.hourKey && r.hourKey !== S.hourKey) refreshGrid(r.hourKey);
    renderGauge(r);
    if (r.nudge) showNudge(r.nudge, p);
    return r;
  }

  function renderGauge(r) {
    const color = r.gauge >= 100 ? BAND_COLOR.red : r.gauge >= 50 ? BAND_COLOR.yellow : BAND_COLOR.green;
    $("gaugeFill").style.width = Math.max(3, r.gauge) + "%";
    $("gaugeFill").style.background = color;
    $("gaugeMeter").setAttribute("aria-valuenow", r.gauge);
    $("levelWord").textContent = t(r.level);
    $("redMin").textContent = r.redMin;
    $("breaks").textContent = r.breakCount;
    if (r.wbgt != null) $("wbgt").textContent = Number(r.wbgt).toFixed(1);
    if (r.band) { const c = $("bandChip"); c.className = "band-chip " + r.band; c.textContent = t(r.band); }
  }

  function moveRider(lat, lon) {
    if (!S.marker) {
      const el = document.createElement("div"); el.className = "rider-dot";
      S.marker = new maplibregl.Marker({ element: el }).setLngLat([lon, lat]).addTo(map);
    } else S.marker.setLngLat([lon, lat]);
    if (S.demoRunning) map.easeTo({ center: [lon, lat], duration: 600 });
  }

  const pathFeatures = [];
  function addPathDot(lon, lat, band) {
    pathFeatures.push({ type: "Feature", geometry: { type: "Point", coordinates: [lon, lat] }, properties: { band } });
    map.getSource("path").setData({ type: "FeatureCollection", features: pathFeatures });
  }

  function showNudge(n, from) {
    const box = $("nudge");
    box.className = "nudge " + n.id;
    $("nudgeKind").textContent = t(NUDGE_KIND[n.id] || n.id);
    $("nudgeText").textContent = clipText(n.id);
    $("firstAid").hidden = !n.firstAid;
    if (n.firstAid) $("firstAid").innerHTML = n.firstAid.map((x) => `<li>${x}</li>`).join("");
    const p = n.point;
    $("nudgePoint").textContent = p ? `${p.name || t(p.kind)} · ${t("away")(p.dist, p.compass)}` : (n.id === "break-logged" ? "" : t("noPoint"));
    $("nudgeGo").hidden = !p;
    if (p) {
      $("nudgeGo").href = `https://www.google.com/maps/dir/?api=1&destination=${p.lat},${p.lon}&travelmode=two-wheeler`;
      map.getSource("guide").setData({ type: "Feature", geometry: { type: "LineString", coordinates: [[from.lon, from.lat], [p.lon, p.lat]] } });
    } else map.getSource("guide").setData({ type: "FeatureCollection", features: [] });
    box.hidden = false;
    play(n.id);
    if (n.id === "break-logged") setTimeout(() => { box.hidden = true; }, 5000);
  }

  async function unwell() {
    const p = S.lastPos || S.demoPos;
    if (!p || !S.shift) return;
    const r = await api("/shifts/unwell", { riderId: S.riderId, shiftId: S.shift.id, lat: p.lat, lon: p.lon, ts: S.demoTs || Date.now() });
    renderGauge(r);
    showNudge({ ...r.nudge, firstAid: r.firstAid }, p);
  }

  async function endShift() {
    if (!S.shift) return;
    clearInterval(S.timer);
    if (S.watchId != null) navigator.geolocation.clearWatch(S.watchId);
    try { S.wakeLock && S.wakeLock.release(); } catch { /* ignore */ }
    const s = await api("/shifts/end", { riderId: S.riderId, shiftId: S.shift.id });
    S.shift = null; S.demoRunning = false;
    $("startBtn").hidden = false; $("endBtn").hidden = true; $("unwellBtn").hidden = true;
    $("nudge").hidden = true;
    $("summaryGrid").innerHTML = [
      [s.minutes, t("sMinutes")], [s.redMin, t("sRed")], [s.breaks, t("sBreaks")],
      [s.peakWbgt != null ? Number(s.peakWbgt).toFixed(1) : "—", t("sPeak")],
    ].map(([v, l]) => `<div><b>${v}</b>${l}</div>`).join("");
    $("summary").hidden = false;
    if (s.path.length) {
      const b = new maplibregl.LngLatBounds();
      s.path.forEach(([lon, lat]) => b.extend([lon, lat]));
      map.fitBounds(b, { padding: 80, duration: 800 });
    }
  }

  // ------------------------------------------------------------ demo ride
  // Same script as backend/tests/test_heatshield.py::demo_track. One ping per simulated minute.
  function demoTrack() {
    const out = [];
    for (let m = 0; m < 16; m++) out.push([m, 1500 - (m % 6) * 200]);       // ride, far from water
    for (let m = 16; m < 22; m++) out.push([m, 250]);                         // wait for an order near water
    for (let m = 22; m < 34; m++) out.push([m, 1500 - (m % 4) * 200]);       // ride away
    for (let i = 0, m = 34; m < 40; m++, i++) out.push([m, 1500 - i * 300]); // ride toward water
    for (let m = 40; m < 53; m++) out.push([m, 15]);                          // rest at the water point
    for (let i = 0, m = 53; m < 60; m++, i++) out.push([m, -200 - i * 250]); // back to work
    return out;
  }

  async function runDemo() {
    if (S.demoRunning) return;
    const status = $("demoStatus");
    S.replay = $("replayToggle").checked;
    await refreshGrid();
    renderForecast();
    await loadPoints();
    const target = (S.points || []).find((p) => ["water", "rest", "fuel", "shade"].includes(p.kind));
    if (!target) { status.textContent = "Seed points first (no water point near map centre)."; return; }
    S.demoRunning = true;
    map.easeTo({ center: [target.lon, target.lat], zoom: 14.5 });
    const t0 = Date.now();
    await startShift({ demo: true, ts: t0 });
    const metresPerDegLon = 111320 * Math.cos(target.lat * Math.PI / 180);
    const speed = Number(params.get("speed") || 1000);
    for (const [m, west] of demoTrack()) {
      if (!S.demoRunning) break;
      const p = { lat: target.lat, lon: target.lon - west / metresPerDegLon, ts: t0 + m * 60000, online: true };
      S.demoPos = p; S.demoTs = p.ts;
      status.textContent = `sim minute ${m}`;
      try { await ping(p); } catch (e) { status.textContent = String(e); break; }
      await new Promise((res) => setTimeout(res, speed));
    }
    status.textContent = "Demo done: tap I feel unwell, then End shift.";
  }

  // ------------------------------------------------------------ add point (long press)
  let pressTimer;
  const startPress = (e) => { pressTimer = setTimeout(() => { S.addAt = e.lngLat; $("addPoint").hidden = false; }, 650); };
  const cancelPress = () => clearTimeout(pressTimer);
  map.on("mousedown", startPress); map.on("touchstart", startPress);
  ["mouseup", "touchend", "dragstart", "movestart"].forEach((ev) => map.on(ev, cancelPress));
  map.on("contextmenu", (e) => { S.addAt = e.lngLat; $("addPoint").hidden = false; });
  $("kindSeg").addEventListener("click", async (e) => {
    const kind = e.target.dataset.v;
    if (!kind || !S.addAt) return;
    $("addPoint").hidden = true;
    await api("/points", { lat: S.addAt.lat, lon: S.addAt.lng, kind, riderId: S.riderId });
    toast(t("added"));
    loadPoints();
  });
  $("addCancel").onclick = () => { $("addPoint").hidden = true; };

  // ------------------------------------------------------------ onboarding and language
  function showOnboarding() {
    const pick = (seg, val) => [...$(seg).children].forEach((b) => b.classList.toggle("on", b.dataset.v === val));
    pick("langSeg", S.lang); pick("vehSeg", S.vehicle);
    $("langSeg").onclick = (e) => { if (e.target.dataset.v) { S.lang = e.target.dataset.v; pick("langSeg", S.lang); applyLang(); } };
    $("vehSeg").onclick = (e) => { if (e.target.dataset.v) { S.vehicle = e.target.dataset.v; pick("vehSeg", S.vehicle); } };
    $("onboard").hidden = false;
  }
  $("onboardGo").onclick = () => {
    store.set("lang", S.lang); store.set("vehicle", S.vehicle); store.set("onboarded", "1");
    $("onboard").hidden = true;
  };
  function applyLang() {
    document.body.lang = S.lang;
    document.querySelectorAll("[data-i18n]").forEach((el) => { const v = t(el.dataset.i18n); if (typeof v === "string") el.textContent = v; });
    $("langBtn").textContent = S.lang === "hi" ? "English" : "हिंदी";
    if (S.index) renderForecast();
  }
  $("langBtn").onclick = async () => {
    S.lang = S.lang === "hi" ? "en" : "hi"; store.set("lang", S.lang); applyLang();
    if (S.shift) preloadAudio();
  };

  // ------------------------------------------------------------ wiring
  $("startBtn").onclick = () => startShift().catch((e) => toast(String(e.message || e)));
  $("endBtn").onclick = () => endShift().catch((e) => toast(String(e.message || e)));
  $("unwellBtn").onclick = () => unwell().catch((e) => toast(String(e.message || e)));
  $("nudgeClose").onclick = () => { $("nudge").hidden = true; };
  $("summaryClose").onclick = () => { $("summary").hidden = true; };
  $("demoRide").onclick = () => runDemo().catch((e) => { $("demoStatus").textContent = String(e); S.demoRunning = false; });
  $("replayToggle").onchange = async (e) => { S.replay = e.target.checked; await refreshGrid(); renderForecast(); };

  function toast(msg) {
    const el = $("toast"); el.textContent = msg; el.hidden = false;
    clearTimeout(el._t); el._t = setTimeout(() => { el.hidden = true; }, 3500);
  }
  function debounce(fn, ms) { let h; return (...a) => { clearTimeout(h); h = setTimeout(() => fn(...a), ms); }; }
})();
