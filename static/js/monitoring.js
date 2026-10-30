(() => {
  const $ = (id) => document.getElementById(id);
  const chart = PBR.tempChart($("chart"), { sources: ["hardware", "simulated"] });
  let windowMin = 15;
  let paused = false;
  let alertFilter = "unacked";
  let lightEvents = [];
  let limitsLoaded = false;
  let st = null;

  const windowMs = () => windowMin * 60 * 1000;

  function refreshStats() {
    const ys = chart.data.datasets.flatMap((d) => d.data.map((p) => p.y));
    $("sN").textContent = ys.length;
    if (!ys.length) { ["sMin", "sMax", "sAvg"].forEach((k) => ($(k).textContent = "--")); return; }
    $("sMin").textContent = Math.min(...ys).toFixed(2);
    $("sMax").textContent = Math.max(...ys).toFixed(2);
    $("sAvg").textContent = (ys.reduce((a, b) => a + b, 0) / ys.length).toFixed(2);
    $("chartEmpty").classList.toggle("hidden", ys.length > 0);
  }

  function redraw() {
    const now = Date.now();
    chart.options.plugins.ledBands.bands = Widgets.ledBands(lightEvents, now - windowMs(), now);
    chart.options.scales.x.min = now - windowMs();
    chart.options.scales.x.max = now;
    chart.update("none");
    refreshStats();
  }

  async function loadWindow() {
    const hours = windowMin / 60;
    const [r, ev] = await Promise.all([
      PBR.api("GET", `/api/readings?hours=${hours}`),
      PBR.api("GET", `/api/lighting/events?hours=${hours + 24}`),
    ]);
    PBR.setReadings(chart, r.readings);
    lightEvents = ev;
    redraw();
  }

  async function loadAlerts() {
    const a = await PBR.api("GET", `/api/alerts?limit=100${alertFilter === "unacked" ? "&unacked=1" : ""}`);
    $("alertList").innerHTML = a.map((x) => Widgets.alertItem(x, true)).join("") ||
      `<div class="empty">${alertFilter === "unacked" ? "No unacknowledged alerts ✓" : "No alerts recorded"}</div>`;
  }

  // ---- live updates
  PBR.on("status", (s) => {
    st = s;
    $("temp").textContent = PBR.fmtTemp(s.temperature);
    $("tempState").innerHTML = PBR.tempStateBadge(s.connected ? s.temp_state : "no_data") +
      `<span class="badge neutral num">Limits ${s.temp_min} – ${s.temp_max} °C</span>`;
    $("srcBadge").innerHTML = PBR.sourceBadge(s.source || (s.data_mode === "serial" ? "hardware" : "simulated"));
    $("lastSeen").textContent = PBR.ago(s.last_seen);
    $("interval").textContent = s.sample_interval;
    if (!limitsLoaded) { $("tmin").value = s.temp_min; $("tmax").value = s.temp_max; limitsLoaded = true; }
    PBR.fitLimits(chart, s.temp_min, s.temp_max);
    if (!paused) redraw();
  });

  PBR.on("reading", (r) => {
    PBR.pushReading(chart, r, windowMs());
    if (!paused) redraw();
  });
  PBR.on("led_event", (e) => { lightEvents.unshift(e); });
  PBR.on("alert", () => loadAlerts());

  // ---- controls
  $("windowSeg").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    document.querySelectorAll("#windowSeg button").forEach((x) => x.classList.toggle("active", x === b));
    windowMin = Number(b.dataset.min);
    loadWindow().catch((err) => PBR.toast(err.message, "error"));
  });

  $("pauseBtn").addEventListener("click", () => {
    paused = !paused;
    $("pauseBtn").textContent = paused ? "▶ Resume" : "❚❚ Pause";
    if (!paused) redraw();
  });

  $("limitsForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await PBR.api("PUT", "/api/settings", { temp_min: $("tmin").value, temp_max: $("tmax").value });
      PBR.toast("Temperature limits saved", "success");
    } catch (err) { PBR.toast(err.message, "error"); }
  });

  $("alertFilter").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    document.querySelectorAll("#alertFilter button").forEach((x) => x.classList.toggle("active", x === b));
    alertFilter = b.dataset.f;
    loadAlerts();
  });

  $("alertList").addEventListener("click", async (e) => {
    const b = e.target.closest("[data-ack]"); if (!b) return;
    await PBR.api("POST", "/api/alerts/ack", { ids: [Number(b.dataset.ack)] });
    loadAlerts();
  });

  $("ackAll").addEventListener("click", async () => {
    await PBR.api("POST", "/api/alerts/ack", {});
    PBR.toast("All alerts acknowledged");
    loadAlerts();
  });

  loadWindow().catch((e) => PBR.toast(e.message, "error"));
  loadAlerts().catch((e) => PBR.toast(e.message, "error"));
})();
