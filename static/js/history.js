(() => {
  const $ = (id) => document.getElementById(id);
  const chart = PBR.tempChart($("chart"), { sources: ["hardware", "simulated"] });
  const TABLE_LIMIT = 1000;
  let tab = "readings";
  let data = { readings: [], lighting: [], runs: [], alerts: [] };

  // Pre-select a run when arriving from the Culture Runs page (?run_id=...)
  const preRun = new URLSearchParams(location.search).get("run_id");

  function filterQuery() {
    const p = new URLSearchParams();
    const r = $("range").value;
    if (r === "custom") {
      if ($("from").value) p.set("start", new Date($("from").value).getTime() / 1000);
      if ($("to").value) p.set("end", new Date($("to").value).getTime() / 1000);
    } else if (r !== "all") p.set("hours", r);
    if ($("run").value) p.set("run_id", $("run").value);
    if ($("source").value) p.set("source", $("source").value);
    return p.toString();
  }

  const COLS = {
    readings: ["Time", "Temperature (°C)", "LED", "Run ID", "Source"],
    lighting: ["Time", "State", "Trigger", "Run ID", "Source"],
    runs: ["Run ID", "Name", "Organism", "Start", "End", "Duration", "Status", "Readings", "Source"],
    alerts: ["Time", "Level", "Message", "Run ID", "Source", "Ack"],
  };
  const ROW = {
    readings: (r) => `<td class="num">${PBR.fmtDateTime(r.ts)}</td><td class="num">${PBR.fmtTemp(r.temperature)}</td>
      <td>${r.led_state ? "ON" : "OFF"}</td><td class="mono">${PBR.esc(r.run_id || "—")}</td><td>${PBR.sourceBadge(r.source)}</td>`,
    lighting: (e) => `<td class="num">${PBR.fmtDateTime(e.ts)}</td><td>${e.state ? '<span class="badge warn">● ON</span>' : '<span class="badge neutral">○ OFF</span>'}</td>
      <td>${PBR.esc(e.trigger)}</td><td class="mono">${PBR.esc(e.run_id || "—")}</td><td>${PBR.sourceBadge(e.source)}</td>`,
    runs: (r) => `<td class="mono"><b>${PBR.esc(r.run_id)}</b></td><td>${PBR.esc(r.name)}</td><td><i>${PBR.esc(r.organism || "")}</i></td>
      <td class="num">${PBR.fmtDateTime(r.start_ts)}</td><td class="num">${r.end_ts ? PBR.fmtDateTime(r.end_ts) : "—"}</td>
      <td class="num">${PBR.fmtDuration((r.end_ts || Date.now() / 1000) - r.start_ts)}</td><td>${PBR.esc(r.status)}</td>
      <td class="num">${r.n_readings}</td><td>${PBR.sourceBadge(r.source)}</td>`,
    alerts: (a) => `<td class="num">${PBR.fmtDateTime(a.ts)}</td><td><span class="badge ${PBR.alertClass(a.level)}">${PBR.esc(a.level)}</span></td>
      <td class="wrap">${PBR.esc(a.message)}</td><td class="mono">${PBR.esc(a.run_id || "—")}</td><td>${PBR.sourceBadge(a.source)}</td><td>${a.acknowledged ? "✓" : ""}</td>`,
  };

  function renderTable() {
    const q = filterQuery();
    let rows = data[tab];
    if (tab === "readings") rows = [...rows].reverse(); // newest first
    if (tab === "runs") rows = rows.filter((r) => (!$("run").value || r.run_id === $("run").value) && (!$("source").value || r.source === $("source").value));
    $("thead").innerHTML = `<tr>${COLS[tab].map((c) => `<th>${c}</th>`).join("")}</tr>`;
    $("tbody").innerHTML = rows.slice(0, TABLE_LIMIT).map((r) => `<tr>${ROW[tab](r)}</tr>`).join("") ||
      `<tr class="empty-row"><td colspan="${COLS[tab].length}">No records match these filters</td></tr>`;
    $("tableInfo").textContent = rows.length > TABLE_LIMIT
      ? `Showing newest ${TABLE_LIMIT} of ${rows.length} rows – export CSV for the full set`
      : `${rows.length} row${rows.length === 1 ? "" : "s"}`;
    $("exportBtn").href = `/api/export/${tab}.csv?${tab === "runs" ? "" : q}`;
  }

  async function load() {
    const q = filterQuery();
    try {
      const [r, l, runs, a] = await Promise.all([
        PBR.api("GET", `/api/readings?limit=200000&${q}`),
        PBR.api("GET", `/api/lighting/events?limit=100000&${q}`),
        PBR.api("GET", "/api/runs"),
        PBR.api("GET", `/api/alerts?limit=100000&${q}`),
      ]);
      data = { readings: r.readings, lighting: l, runs, alerts: a };
      const s = r.stats;
      $("sN").textContent = s.n.toLocaleString();
      $("sMin").textContent = PBR.fmtTemp(s.tmin);
      $("sMax").textContent = PBR.fmtTemp(s.tmax);
      $("sAvg").textContent = PBR.fmtTemp(s.tavg);

      PBR.setReadings(chart, r.readings);
      const xs = r.readings.map((x) => x.ts * 1000);
      if (xs.length) chart.options.plugins.ledBands.bands = Widgets.ledBands(l, xs[0], xs[xs.length - 1]);
      $("chartEmpty").classList.toggle("hidden", PBR.chartHasData(chart));
      if (PBR.status) PBR.fitLimits(chart, PBR.status.temp_min, PBR.status.temp_max);
      chart.update();
      $("exportReadings2").href = `/api/export/readings.csv?${q}`;

      // populate run selector once (keep current selection)
      const sel = $("run"), cur = sel.value || preRun || "";
      sel.innerHTML = '<option value="">All runs</option>' +
        runs.map((x) => `<option value="${PBR.esc(x.run_id)}">${PBR.esc(x.run_id)} – ${PBR.esc(x.name)}${x.source === "simulated" ? " (sim)" : ""}</option>`).join("");
      sel.value = cur;
      renderTable();
    } catch (e) { PBR.toast(e.message, "error"); }
  }

  $("range").addEventListener("change", () => {
    document.querySelectorAll(".custom").forEach((n) => n.classList.toggle("hidden", $("range").value !== "custom"));
  });
  $("apply").addEventListener("click", load);
  $("tabs").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    tab = b.dataset.tab;
    document.querySelectorAll("#tabs button").forEach((x) => x.classList.toggle("active", x === b));
    renderTable();
  });

  if (preRun) {
    $("range").value = "all";
    $("run").innerHTML = `<option value="${PBR.esc(preRun)}">${PBR.esc(preRun)}</option>`;
    $("run").value = preRun;
  }
  let lim = "";
  PBR.on("status", (s) => {
    if (lim === `${s.temp_min}/${s.temp_max}`) return;
    lim = `${s.temp_min}/${s.temp_max}`;
    PBR.fitLimits(chart, s.temp_min, s.temp_max);
    chart.update("none");
  });
  load();
})();
