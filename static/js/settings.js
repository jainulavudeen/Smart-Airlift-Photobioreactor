(() => {
  const $ = (id) => document.getElementById(id);
  let settings = {};

  async function loadPorts(selected) {
    const ports = await PBR.api("GET", "/api/serial/ports");
    const opts = ports.map((p) => `<option value="${PBR.esc(p.device)}">${PBR.esc(p.device)}${p.description && p.description !== "n/a" ? " – " + PBR.esc(p.description) : ""}</option>`);
    if (selected && !ports.some((p) => p.device === selected)) opts.unshift(`<option value="${PBR.esc(selected)}">${PBR.esc(selected)} (not detected)</option>`);
    $("portSel").innerHTML = '<option value="">— select port —</option>' + opts.join("");
    $("portSel").value = selected || "";
    if (!ports.length) PBR.toast("No serial ports detected. Is the Arduino plugged in?", "warning");
  }

  function fill(s) {
    document.querySelectorAll("[data-form] [name]").forEach((el) => {
      if (el.type === "radio") el.checked = el.value === s[el.name];
      else if (el.name !== "serial_port" && s[el.name] !== undefined) el.value = s[el.name];
    });
  }

  async function load() {
    settings = await PBR.api("GET", "/api/settings");
    fill(settings);
    await loadPorts(settings.serial_port);
  }

  document.querySelectorAll("[data-form]").forEach((form) =>
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const body = {};
      new FormData(form).forEach((v, k) => (body[k] = v));
      if (body.data_mode === "serial" && !body.serial_port) return PBR.toast("Select a serial port first", "error");
      try {
        const r = await PBR.api("PUT", "/api/settings", body);
        PBR.toast(r.changed && r.changed.length ? "Settings saved" : "No changes to save");
        settings = await PBR.api("GET", "/api/settings");
      } catch (err) { PBR.toast(err.message, "error"); }
    })
  );

  $("refreshPorts").addEventListener("click", () => loadPorts($("portSel").value));
  $("reconnect").addEventListener("click", async () => {
    await PBR.api("POST", "/api/connection/reconnect");
    PBR.toast("Reconnecting…");
  });

  // ---- connection status
  PBR.on("status", (s) => {
    const hw = s.data_mode === "serial";
    $("connDetail").innerHTML = s.connected
      ? `<span class="badge ok"><span class="dot"></span>${hw ? "Connected" : "Simulator running"}</span>`
      : `<span class="badge crit"><span class="dot"></span>Not connected</span>`;
    $("connText").textContent = s.connected
      ? `${hw ? s.port : "Virtual device"} · firmware ${s.firmware || "?"} · last data ${PBR.ago(s.last_seen)}`
      : s.error || "Connecting…";
  });

  // ---- serial console
  const con = $("console");
  function addLog(e) {
    const div = document.createElement("div");
    div.className = e.dir;
    const arrow = e.dir === "tx" ? "→" : e.dir === "rx" ? "←" : "•";
    div.innerHTML = `<span class="t">${PBR.fmtTime(e.ts)}</span>${arrow} ${PBR.esc(e.text)}`;
    const atBottom = con.scrollTop + con.clientHeight >= con.scrollHeight - 20;
    con.appendChild(div);
    while (con.childNodes.length > 300) con.removeChild(con.firstChild);
    if (atBottom) con.scrollTop = con.scrollHeight;
  }
  PBR.api("GET", "/api/serial/log").then((log) => log.forEach(addLog));
  PBR.on("serial_log", addLog);
  $("clearLog").addEventListener("click", () => (con.innerHTML = ""));
  $("sendForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    try { await PBR.api("POST", "/api/serial/send", { line: $("cmd").value }); $("cmd").value = ""; }
    catch (err) { PBR.toast(err.message, "error"); }
  });

  // ---- data management
  $("purgeSim").addEventListener("click", async () => {
    if (!confirm("Permanently delete ALL simulated readings, lighting records, alerts and completed simulated runs?")) return;
    try {
      const r = await PBR.api("DELETE", "/api/data/simulated");
      const d = r.deleted;
      PBR.toast(`Deleted ${d.readings} readings, ${d.lighting_events} lighting records, ${d.alerts} alerts, ${d.runs} runs`);
    } catch (err) { PBR.toast(err.message, "error"); }
  });

  load().catch((e) => PBR.toast(e.message, "error"));
})();
