/* Small render helpers reused by several pages. */
const Widgets = {
  alertItem(a, withAck = false) {
    const cls = PBR.alertClass(a.level);
    return `<div class="alert-item ${cls} ${a.acknowledged ? "acked" : ""}" data-id="${a.id}">
      <span class="icon">${PBR.alertIcon(a.level)}</span>
      <div class="body">
        <div class="msg">${PBR.esc(a.message)}</div>
        <div class="when"><span>${PBR.fmtDateTime(a.ts)}</span>${PBR.sourceBadge(a.source)}
          ${a.run_id ? `<span class="mono">${PBR.esc(a.run_id)}</span>` : ""}
          ${a.acknowledged ? '<span class="badge neutral">acknowledged</span>' : ""}</div>
      </div>
      ${withAck && !a.acknowledged ? `<button class="btn sm" data-ack="${a.id}">Acknowledge</button>` : ""}
    </div>`;
  },

  lightRow(e) {
    return `<tr><td class="num">${PBR.fmtDateTime(e.ts)}</td>
      <td>${e.state ? '<span class="badge warn">● ON</span>' : '<span class="badge neutral">○ OFF</span>'}</td>
      <td>${PBR.esc(e.trigger)}</td><td>${PBR.sourceBadge(e.source)}</td></tr>`;
  },

  // Convert ON/OFF event list into [start,end] ms intervals for chart shading.
  ledBands(events, fromMs, toMs) {
    const evs = [...events].sort((a, b) => a.ts - b.ts);
    const bands = [];
    let onAt = null;
    evs.forEach((e) => {
      const t = e.ts * 1000;
      if (e.state && onAt === null) onAt = Math.max(t, fromMs);
      if (!e.state && onAt !== null) { if (t > fromMs) bands.push([onAt, t]); onAt = null; }
    });
    if (onAt !== null) bands.push([onAt, toMs]);
    return bands;
  },
};
