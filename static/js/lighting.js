(() => {
  const $ = (id) => document.getElementById(id);
  let st = null;
  let formLoaded = false;

  const toMin = (hhmm) => { const [h, m] = hhmm.split(":").map(Number); return h * 60 + m; };
  const fmtHM = (min) => `${Math.floor(min / 60)} h${min % 60 ? " " + (min % 60) + " min" : ""}`;
  const lightMinutes = (a, b) => (a === b ? 0 : (b - a + 1440) % 1440);

  function renderTimeline(start, stop) {
    const tl = $("timeline");
    tl.querySelectorAll(".on-seg").forEach((n) => n.remove());
    const a = toMin(start), b = toMin(stop);
    const segs = a === b ? [] : a < b ? [[a, b]] : [[a, 1440], [0, b]];
    segs.forEach(([s, e]) => {
      const d = document.createElement("div");
      d.className = "on-seg";
      d.style.left = `${(s / 1440) * 100}%`;
      d.style.width = `${((e - s) / 1440) * 100}%`;
      tl.insertBefore(d, $("nowLine"));
    });
  }

  function renderClock() {
    if (!st) return;
    const now = new Date();
    const nowMin = now.getHours() * 60 + now.getMinutes();
    $("nowLine").style.left = `${((nowMin + now.getSeconds() / 60) / 1440) * 100}%`;
    if (st.led_mode !== "auto") { $("nextChange").textContent = "Schedule paused (manual mode)"; return; }
    const a = toMin(st.led_start), b = toMin(st.led_stop);
    if (a === b) { $("nextChange").textContent = "Start = stop: LED stays OFF"; return; }
    const toA = (a - nowMin + 1440) % 1440 || 1440, toB = (b - nowMin + 1440) % 1440 || 1440;
    $("nextChange").textContent = toA < toB
      ? `Next: ON at ${st.led_start} (in ${fmtHM(toA)})`
      : `Next: OFF at ${st.led_stop} (in ${fmtHM(toB)})`;
  }

  function updatePhotoperiod() {
    const l = lightMinutes(toMin($("start").value || "00:00"), toMin($("stop").value || "00:00"));
    $("photoperiod").innerHTML = `Photoperiod <b class="num">${(l / 60).toFixed(1)} h light : ${((1440 - l) / 60).toFixed(1)} h dark</b>`;
  }

  async function loadEvents() {
    const ev = await PBR.api("GET", "/api/lighting/events?limit=200");
    $("events").innerHTML = ev.map(Widgets.lightRow).join("") ||
      '<tr class="empty-row"><td colspan="4">No lighting events yet</td></tr>';
  }

  PBR.on("status", (s) => {
    st = s;
    const on = s.led_state === 1;
    $("lamp").classList.toggle("on", on);
    $("ledText").textContent = s.led_state == null ? "Unknown" : on ? "ON" : "OFF";
    $("ledSub").textContent = s.connected ? `Reported by device · ${PBR.ago(s.last_seen)}` : "Device not connected";
    $("srcBadge").innerHTML = PBR.sourceBadge(s.source || (s.data_mode === "serial" ? "hardware" : "simulated"));
    document.querySelectorAll("#modeSeg button").forEach((b) => b.classList.toggle("active", b.dataset.mode === s.led_mode));
    $("modeHelp").innerHTML = s.led_mode === "auto"
      ? `The device switches the LED automatically: <b>ON at ${s.led_start}</b>, <b>OFF at ${s.led_stop}</b> (RTC time).`
      : "Schedule is paused. The LED stays in the state you set below until you return to automatic mode.";
    $("onBtn").className = `btn lg ${on ? "primary" : ""}`;
    $("offBtn").className = `btn lg ${!on && s.led_state != null ? "primary" : ""}`;
    if (!formLoaded) {
      $("start").value = s.led_start; $("stop").value = s.led_stop;
      formLoaded = true; updatePhotoperiod();
    }
    renderTimeline(s.led_start, s.led_stop);
    renderClock();
  });
  PBR.on("led_event", () => loadEvents());

  async function act(fn, msg) {
    try { await fn(); if (msg) PBR.toast(msg); } catch (e) { PBR.toast(e.message, "error"); }
  }

  $("modeSeg").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    act(() => PBR.api("POST", "/api/lighting/mode", { mode: b.dataset.mode }),
      b.dataset.mode === "auto" ? "Automatic schedule enabled" : "Manual mode enabled");
  });
  $("onBtn").addEventListener("click", () => act(() => PBR.api("POST", "/api/lighting/led", { state: "on" }), "LED ON command sent"));
  $("offBtn").addEventListener("click", () => act(() => PBR.api("POST", "/api/lighting/led", { state: "off" }), "LED OFF command sent"));
  ["start", "stop"].forEach((id) => $(id).addEventListener("input", updatePhotoperiod));
  $("schedForm").addEventListener("submit", (e) => {
    e.preventDefault();
    act(() => PBR.api("POST", "/api/lighting/schedule", { start: $("start").value, stop: $("stop").value }),
      "Schedule saved and sent to device");
  });

  setInterval(renderClock, 15000);
  loadEvents().catch((e) => PBR.toast(e.message, "error"));
})();
