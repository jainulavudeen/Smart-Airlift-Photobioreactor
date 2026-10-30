(() => {
  const $ = (id) => document.getElementById(id);
  let active = null;
  let skew = 0;
  let orgFilled = false;

  function renderActive(run) {
    active = run;
    $("activeCard").classList.toggle("hidden", !run);
    $("startCard").classList.toggle("hidden", !!run);
    if (!run) return;
    $("aId").textContent = run.run_id;
    $("aName").textContent = run.name;
    $("aOrg").innerHTML = `<i>${PBR.esc(run.organism || "--")}</i>`;
    $("aStart").textContent = PBR.fmtDateTime(run.start_ts);
    $("aSrc").innerHTML = PBR.sourceBadge(run.source);
    $("aNotes").textContent = run.notes || "--";
    $("aDataLink").href = `/history?run_id=${encodeURIComponent(run.run_id)}`;
    tick();
  }

  function tick() {
    if (active) $("aTimer").textContent = PBR.fmtDuration(Date.now() / 1000 + skew - active.start_ts, true);
  }

  async function loadRuns() {
    const runs = await PBR.api("GET", "/api/runs");
    $("histCount").textContent = `${runs.length} run${runs.length === 1 ? "" : "s"} recorded`;
    const now = Date.now() / 1000 + skew;
    $("runs").innerHTML = runs.map((r) => `<tr>
      <td class="mono"><b>${PBR.esc(r.run_id)}</b></td>
      <td>${PBR.esc(r.name)}</td>
      <td><i>${PBR.esc(r.organism || "")}</i></td>
      <td class="num">${PBR.fmtDateTime(r.start_ts)}</td>
      <td class="num">${r.end_ts ? PBR.fmtDateTime(r.end_ts) : "—"}</td>
      <td class="num">${PBR.fmtDuration((r.end_ts || now) - r.start_ts)}</td>
      <td>${r.status === "active" ? '<span class="badge ok">● active</span>' : '<span class="badge neutral">completed</span>'}</td>
      <td class="num">${r.n_readings}</td>
      <td>${PBR.sourceBadge(r.source)}</td>
      <td><a href="/history?run_id=${encodeURIComponent(r.run_id)}">View</a> ·
          <a href="/api/export/readings.csv?run_id=${encodeURIComponent(r.run_id)}">CSV</a></td>
    </tr>`).join("") || '<tr class="empty-row"><td colspan="10">No culture runs yet – start one above.</td></tr>';
  }

  PBR.on("status", (s) => {
    skew = s.server_time - Date.now() / 1000;
    if (!orgFilled) { $("organism").value = s.culture_organism; orgFilled = true; }
    const id = s.active_run ? s.active_run.run_id : null;
    if (id !== (active && active.run_id)) { renderActive(s.active_run); loadRuns(); }
  });
  PBR.on("run_update", () => loadRuns());

  $("startForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const { run } = await PBR.api("POST", "/api/runs/start", {
        name: $("name").value, organism: $("organism").value, notes: $("notes").value,
      });
      PBR.toast(`Run ${run.run_id} started`);
      $("name").value = ""; $("notes").value = "";
      renderActive(run); loadRuns();
    } catch (err) { PBR.toast(err.message, "error"); }
  });

  $("stopBtn").addEventListener("click", async () => {
    if (!active || !confirm(`Stop culture run ${active.run_id}?`)) return;
    try {
      const { run } = await PBR.api("POST", `/api/runs/${encodeURIComponent(active.run_id)}/stop`, { notes: $("endNotes").value });
      PBR.toast(`Run ${run.run_id} stopped after ${PBR.fmtDuration(run.end_ts - run.start_ts)}`);
      $("endNotes").value = "";
      renderActive(null); loadRuns();
    } catch (err) { PBR.toast(err.message, "error"); }
  });

  setInterval(tick, 1000);
  loadRuns().catch((e) => PBR.toast(e.message, "error"));
})();
