(() => {
  const WINDOW_MS = 30 * 60 * 1000;
  const $ = (id) => document.getElementById(id);
  const chart = PBR.tempChart($("chart"), { sources: ["hardware", "simulated"] });
  let clockSkew = 0;
  let lightEvents = [];
  let st = null;

  function updateEmpty() { $("chartEmpty").classList.toggle("hidden", PBR.chartHasData(chart)); }

  function redrawBands() {
    const now = Date.now();
    chart.options.plugins.ledBands.bands = Widgets.ledBands(lightEvents, now - WINDOW_MS, now);
    chart.update("none");
  }

  async function loadChart() {
    const [r, ev] = await Promise.all([
      PBR.api("GET", "/api/readings?hours=0.5"),
      PBR.api("GET", "/api/lighting/events?hours=24.5"),
    ]);
    PBR.setReadings(chart, r.readings);
    lightEvents = ev;
    $("events").innerHTML = ev.slice(0, 12).map(Widgets.lightRow).join("") ||
      '<tr class="empty-row"><td colspan="4">No lighting events yet</td></tr>';
    redrawBands();
    updateEmpty();
  }

  async function loadAlerts() {
    const a = await PBR.api("GET", "/api/alerts?limit=6");
    $("alerts").innerHTML = a.map((x) => Widgets.alertItem(x)).join("") || '<div class="empty">No alerts recorded 🎉</div>';
  }

  function tickRuntime() {
    if (!st || !st.active_run) return;
    const now = Date.now() / 1000 + clockSkew;
    $("runtime").textContent = PBR.fmtDuration(now - st.active_run.start_ts, true);
  }

  PBR.on("status", (s) => {
    st = s;
    clockSkew = s.server_time - Date.now() / 1000;
    const run = s.active_run;
    // Reactor
    $("reactorState").textContent = run ? "Running" : "Idle";
    $("reactorMeta").innerHTML = run
      ? `<span class="badge ok">● Culture active</span><span class="mono">${PBR.esc(run.run_id)}</span>`
      : `<span class="badge neutral">No culture run</span><a href="/runs">Start one →</a>`;
    // Temperature
    $("temp").textContent = PBR.fmtTemp(s.temperature);
    $("tempState").innerHTML = PBR.tempStateBadge(s.connected ? s.temp_state : "no_data");
    $("tempSource").innerHTML = s.source ? PBR.sourceBadge(s.source) : "";
    $("kpiTemp").className = `card kpi accent state-${s.temp_state}`;
    // LED
    $("led").innerHTML = s.led_state == null ? "--" : s.led_state ? '<span style="color:#b07d00">ON</span>' : "OFF";
    $("ledMeta").innerHTML = `<span class="badge ${s.led_mode === "auto" ? "info" : "warn"}">${s.led_mode === "auto" ? "Auto schedule" : "Manual"}</span>
      <span class="num">${s.led_start}–${s.led_stop}</span>`;
    // Runtime
    if (run) {
      $("runtimeMeta").innerHTML = `${PBR.esc(run.name)} · since ${PBR.fmtDateTime(run.start_ts)}`;
      tickRuntime();
    } else {
      $("runtime").textContent = "--:--:--";
      $("runtimeMeta").textContent = "No active run";
    }
    // Connection
    const hw = s.data_mode === "serial";
    $("conn").innerHTML = s.connected
      ? `<span style="color:var(--ok)">● ${hw ? "Connected" : "Simulator"}</span>`
      : `<span style="color:var(--crit)">● Disconnected</span>`;
    $("connMeta").innerHTML = hw
      ? `<span class="mono">${PBR.esc(s.port || "no port")}</span> · FW ${PBR.esc(s.firmware || "?")} · ${PBR.ago(s.last_seen)}`
        + (s.error ? `<br><span style="color:var(--crit)">${PBR.esc(s.error)}</span>` : "")
      : `Virtual device · ${PBR.ago(s.last_seen)}`;
    // Details
    $("details").innerHTML = `
      <dt>Reactor</dt><dd>${PBR.esc(s.reactor_name)}</dd>
      <dt>Organism</dt><dd><i>${PBR.esc(s.culture_organism)}</i></dd>
      <dt>Limits</dt><dd class="num">${s.temp_min} – ${s.temp_max} °C</dd>
      <dt>Sampling / recording</dt><dd class="num">${s.sample_interval} s / ${s.record_interval} s</dd>
      <dt>Device RTC</dt><dd class="num">${PBR.esc(s.rtc_time || "--")}</dd>`;
    // Range bar: limits occupy 20%..80% of the track
    $("rLo").textContent = `${s.temp_min} °C`;
    $("rHi").textContent = `${s.temp_max} °C`;
    if (s.temperature != null) {
      const pct = 20 + ((s.temperature - s.temp_min) / (s.temp_max - s.temp_min)) * 60;
      $("rangeMarker").style.left = `${Math.max(0, Math.min(100, pct))}%`;
    }
    PBR.fitLimits(chart, s.temp_min, s.temp_max);
    chart.update("none");
  });

  PBR.on("reading", (r) => { PBR.pushReading(chart, r, WINDOW_MS); updateEmpty(); redrawBands(); });
  PBR.on("led_event", () => loadChart());
  PBR.on("alert", () => loadAlerts());
  setInterval(tickRuntime, 1000);

  loadChart().catch((e) => PBR.toast(e.message, "error"));
  loadAlerts().catch((e) => PBR.toast(e.message, "error"));
})();
