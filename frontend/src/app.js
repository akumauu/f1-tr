// F1 TR Review — static race-analysis dashboard.
// Vanilla ES module, no dependencies. Reads the static JSON export described in
// data/manifest.json and renders headline stats, four interactive canvas charts,
// a team-grouped driver selector and enriched timing tables.

const DATA_KEYS = [
  "summary",
  "drivers",
  "laps",
  "lap_deltas",
  "stints",
  "degradation",
  "radio",
  "positions",
  "pit_stops",
  "race_control",
];

const COMPOUND_COLORS = {
  SOFT: "#ff4d4d",
  MEDIUM: "#f7d54a",
  HARD: "#e8edf5",
  INTERMEDIATE: "#4ad36b",
  WET: "#4aa8ff",
};

const CHART_HINTS = {
  positions: "Each line is a driver's track position, lap by lap — higher on the chart is a better position. Hover any point for detail.",
  pace: "Lap time per lap (lower = faster). The dashed line marks the fastest lap currently in view; pit-out laps are dropped.",
  strategy: "Every bar is a stint, coloured by tyre compound and drawn across the race distance. Hover for tyre age and stint length.",
  degradation: "Modelled pace loss per lap inside each stint (lower = better tyre management). Bars are coloured by compound.",
};

const INK = "#eef2f8";
const MUTED = "#7f8aa0";
const GRID = "rgba(255,255,255,0.06)";
const AXIS = "rgba(255,255,255,0.18)";
const CANVAS_BG = "#0d1119";

const state = {
  manifest: null,
  selectedYear: null,
  selectedMeetingKey: null,
  selectedSessionKey: null,
  datasets: {},
  missingFiles: [],
  selectedDrivers: new Set(),
  lapMin: null,
  lapMax: null,
  search: "",
  activeChart: "positions",
  derived: null,
  hoverKey: null,
};

let chartHits = { points: [], rects: [] };

const $ = (id) => document.getElementById(id);
const els = {};

document.addEventListener("DOMContentLoaded", boot);
if (document.readyState !== "loading") boot();

let booted = false;
function boot() {
  if (booted) return;
  booted = true;
  Object.assign(els, {
    yearSelect: $("yearSelect"),
    meetingSelect: $("meetingSelect"),
    sessionSelect: $("sessionSelect"),
    searchInput: $("searchInput"),
    lapMin: $("lapMin"),
    lapMax: $("lapMax"),
    resetFilters: $("resetFilters"),
    driverGrid: $("driverGrid"),
    driverCount: $("driverCount"),
    hero: $("heroStats"),
    chart: $("chart"),
    chartStage: $("chartStage"),
    chartTip: $("chartTip"),
    chartLegend: $("chartLegend"),
    chartCaption: $("chartCaption"),
    chartHint: $("chartHint"),
    chartTabs: $("chartTabs"),
    sessionMeta: $("sessionMeta"),
    loadStatus: $("loadStatus"),
    lapRows: $("lapRows"),
    lapCount: $("lapCount"),
    stintRows: $("stintRows"),
    stintCount: $("stintCount"),
    radioList: $("radioList"),
    radioCount: $("radioCount"),
    extraPanels: $("extraPanels"),
  });

  init().catch((error) => {
    setStatus("Load failed", "warn");
    els.sessionMeta.textContent = error.message;
    drawEmptyChart("Data could not be loaded");
  });
}

async function init() {
  setStatus("Loading", "");
  state.manifest = await fetchJSON("data/manifest.json");
  initializeSelection();
  bindEvents();
  renderControls();
  await loadSelectedSession();
}

function bindEvents() {
  els.yearSelect.addEventListener("change", async () => {
    state.selectedYear = Number(els.yearSelect.value);
    const meeting = meetingsForYear(state.selectedYear)[0];
    state.selectedMeetingKey = meeting?.meeting_key ?? null;
    const session = pickDefaultSession(sessionsForMeeting(state.selectedMeetingKey));
    state.selectedSessionKey = session?.session_key ?? null;
    renderControls();
    await loadSelectedSession();
  });

  els.meetingSelect.addEventListener("change", async () => {
    state.selectedMeetingKey = Number(els.meetingSelect.value);
    const session = pickDefaultSession(sessionsForMeeting(state.selectedMeetingKey));
    state.selectedSessionKey = session?.session_key ?? null;
    renderControls();
    await loadSelectedSession();
  });

  els.sessionSelect.addEventListener("change", async () => {
    state.selectedSessionKey = Number(els.sessionSelect.value);
    await loadSelectedSession();
  });

  els.searchInput.addEventListener("input", () => {
    state.search = els.searchInput.value.trim().toLowerCase();
    renderRadio();
  });

  els.lapMin.addEventListener("input", () => {
    state.lapMin = numberOrNull(els.lapMin.value);
    render();
  });
  els.lapMax.addEventListener("input", () => {
    state.lapMax = numberOrNull(els.lapMax.value);
    render();
  });

  els.resetFilters.addEventListener("click", () => {
    resetFilters();
    renderDriverSelector();
    render();
  });

  for (const btn of els.chartTabs.querySelectorAll(".tab")) {
    btn.addEventListener("click", () => {
      state.activeChart = btn.dataset.chart;
      state.hoverKey = null;
      syncTabs();
      renderChart();
    });
  }

  for (const btn of document.querySelectorAll("[data-pick]")) {
    btn.addEventListener("click", () => {
      applyQuickPick(btn.dataset.pick);
      renderDriverSelector();
      render();
    });
  }

  els.chart.addEventListener("mousemove", onChartHover);
  els.chart.addEventListener("mouseleave", () => {
    state.hoverKey = null;
    hideTip();
    renderChart();
  });

  let resizeTimer = null;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(renderChart, 120);
  });
}

function initializeSelection() {
  const manifest = state.manifest;
  const defaultSession = manifest.sessions.find((s) => s.session_key === manifest.default_session_key)
    || manifest.sessions[0];
  if (!defaultSession) throw new Error("No sessions are listed in manifest.json");
  const meeting = manifest.meetings.find((m) => m.meeting_key === defaultSession.meeting_key);
  state.selectedSessionKey = defaultSession.session_key;
  state.selectedMeetingKey = defaultSession.meeting_key;
  state.selectedYear = meeting?.year ?? manifest.years[0]?.year ?? null;
}

async function loadSelectedSession() {
  const session = currentSession();
  if (!session) {
    setStatus("No session", "warn");
    return;
  }

  setStatus("Loading", "");
  state.datasets = {};
  state.missingFiles = [];

  await Promise.all(DATA_KEYS.map(async (key) => {
    const ref = session.files?.[key];
    if (!ref?.path) {
      state.datasets[key] = key === "summary" ? null : [];
      if (key !== "summary") state.missingFiles.push(key);
      return;
    }
    try {
      state.datasets[key] = await fetchJSON(`data/${ref.path}`);
    } catch {
      state.datasets[key] = key === "summary" ? null : [];
      state.missingFiles.push(key);
    }
  }));

  buildDerived();
  resetFilters();
  renderControls();
  renderHero();
  renderDriverSelector();
  syncTabs();
  render();
}

// ---------- Derived data ----------

function buildDerived() {
  const drivers = dataset("drivers");
  const byNumber = new Map(drivers.map((d) => [Number(d.driver_number), d]));

  const laps = dataset("laps");
  const lapNumbers = laps.map((l) => Number(l.lap_number)).filter(Number.isFinite);
  const maxLap = lapNumbers.length ? Math.max(...lapNumbers) : 0;

  // Position events sorted by time, grouped per driver.
  const events = dataset("positions")
    .map((p) => ({ n: Number(p.driver_number), pos: Number(p.position), t: Date.parse(p.date) }))
    .filter((e) => Number.isFinite(e.t) && Number.isFinite(e.pos) && Number.isFinite(e.n))
    .sort((a, b) => a.t - b.t);
  const eventsByDriver = groupBy(events, (e) => e.n);

  const gridPos = new Map();
  const finalPos = new Map();
  for (const [n, evs] of eventsByDriver) {
    gridPos.set(n, evs[0].pos);
    finalPos.set(n, evs[evs.length - 1].pos);
  }
  const maxPos = events.length ? Math.max(...events.map((e) => e.pos)) : Math.max(drivers.length, 20);

  // Position-by-lap series per driver.
  const lapsByDriver = groupBy(laps, (l) => Number(l.driver_number));
  const positionByLap = new Map();
  for (const [n, rows] of lapsByDriver) {
    const evs = eventsByDriver.get(n);
    if (!evs || !evs.length) continue;
    const sorted = rows.slice().sort((a, b) => Number(a.lap_number) - Number(b.lap_number));
    const pts = [];
    let i = 0;
    for (const lap of sorted) {
      const t = Date.parse(lap.date_start);
      if (Number.isFinite(t)) {
        while (i + 1 < evs.length && evs[i + 1].t <= t) i += 1;
      }
      const pos = evs[i].t > t && Number.isFinite(t) ? evs[0].pos : evs[i].pos;
      pts.push({ lap: Number(lap.lap_number), pos });
    }
    positionByLap.set(n, pts);
  }

  // Session fastest lap.
  let fastest = null;
  for (const lap of laps) {
    if (!validLapDuration(lap.lap_duration) || lap.is_pit_out_lap) continue;
    if (!fastest || Number(lap.lap_duration) < Number(fastest.lap_duration)) fastest = lap;
  }

  // Compound + delta per (driver, lap) from lap_deltas.
  const lapMeta = new Map();
  for (const row of dataset("lap_deltas")) {
    lapMeta.set(`${Number(row.driver_number)}:${Number(row.lap_number)}`, {
      compound: row.compound,
      delta: row.delta_to_prev,
    });
  }

  // Biggest position gain (grid -> final).
  let biggestMover = null;
  for (const [n, grid] of gridPos) {
    const fin = finalPos.get(n);
    if (!Number.isFinite(fin)) continue;
    const gained = grid - fin;
    if (!biggestMover || gained > biggestMover.gained) biggestMover = { n, grid, fin, gained };
  }

  // Driver ordering: by final position, then grid, then number.
  const orderValue = (n) => finalPos.get(n) ?? gridPos.get(n) ?? (1000 + n);
  const driversSorted = drivers.slice().sort((a, b) =>
    orderValue(Number(a.driver_number)) - orderValue(Number(b.driver_number)));

  // Team groups ordered by best finishing driver.
  const teamMap = new Map();
  for (const d of driversSorted) {
    const team = d.team_name || "—";
    if (!teamMap.has(team)) teamMap.set(team, { name: team, color: teamColor(d), drivers: [] });
    teamMap.get(team).drivers.push(d);
  }
  const teamGroups = [...teamMap.values()];

  state.derived = {
    byNumber,
    maxLap,
    maxPos,
    gridPos,
    finalPos,
    positionByLap,
    fastest,
    lapMeta,
    biggestMover,
    driversSorted,
    teamGroups,
  };
}

// ---------- Filters ----------

function resetFilters() {
  const drivers = dataset("drivers");
  state.selectedDrivers = new Set(drivers.map((d) => Number(d.driver_number)));
  const laps = dataset("laps");
  const lapNumbers = laps.map((l) => Number(l.lap_number)).filter(Number.isFinite);
  state.lapMin = lapNumbers.length ? Math.min(...lapNumbers) : null;
  state.lapMax = lapNumbers.length ? Math.max(...lapNumbers) : null;
  state.search = "";
  els.searchInput.value = "";
  els.lapMin.value = state.lapMin ?? "";
  els.lapMax.value = state.lapMax ?? "";
}

function applyQuickPick(kind) {
  const d = state.derived;
  const all = dataset("drivers").map((x) => Number(x.driver_number));
  if (kind === "all") {
    state.selectedDrivers = new Set(all);
  } else if (kind === "none") {
    state.selectedDrivers = new Set();
  } else if (kind === "podium" || kind === "points") {
    const limit = kind === "podium" ? 3 : 10;
    const ranked = all
      .filter((n) => Number.isFinite(d.finalPos.get(n)))
      .sort((a, b) => d.finalPos.get(a) - d.finalPos.get(b))
      .slice(0, limit);
    state.selectedDrivers = new Set(ranked.length ? ranked : all.slice(0, limit));
  }
}

// ---------- Rendering ----------

function render() {
  const session = currentSession();
  const meeting = currentMeeting();
  const missing = state.missingFiles.length;
  setStatus(missing ? `Partial · ${missing} missing` : "Ready", missing ? "warn" : "ready");
  els.sessionMeta.textContent = session && meeting
    ? `${meeting.meeting_name} · ${session.session_name} · ${formatDate(session.date_start)}`
    : "No session selected";

  const total = dataset("drivers").length;
  els.driverCount.textContent = `${state.selectedDrivers.size}/${total} selected`;

  renderChart();
  renderLapTable();
  renderStintTable();
  renderRadio();
  renderExtraPanels();
}

function renderControls() {
  fillSelect(els.yearSelect, state.manifest.years, (i) => i.year, (i) => String(i.year), state.selectedYear);
  fillSelect(els.meetingSelect, meetingsForYear(state.selectedYear), (i) => i.meeting_key, (i) => i.meeting_name, state.selectedMeetingKey);
  fillSelect(els.sessionSelect, sessionsForMeeting(state.selectedMeetingKey),
    (i) => i.session_key, (i) => i.session_name || i.session_type || i.session_key, state.selectedSessionKey);
}

function renderHero() {
  const d = state.derived;
  const cards = [];

  const winnerNum = [...d.finalPos.entries()].find(([, p]) => p === 1)?.[0];
  cards.push(heroCard("Winner", winnerNum != null ? driverAcr(winnerNum) : "—",
    winnerNum != null ? driverFull(winnerNum) : "No position data", teamColorOf(winnerNum)));

  const poleNum = [...d.gridPos.entries()].find(([, p]) => p === 1)?.[0];
  cards.push(heroCard("Started P1", poleNum != null ? driverAcr(poleNum) : "—",
    poleNum != null ? driverFull(poleNum) : "—", teamColorOf(poleNum)));

  const f = d.fastest;
  cards.push(heroCard("Fastest lap", f ? formatLapTime(f.lap_duration) : "—",
    f ? `${driverAcr(f.driver_number)} · Lap ${f.lap_number}` : "—",
    f ? teamColorOf(f.driver_number) : MUTED));

  cards.push(heroCard("Race distance", d.maxLap ? String(d.maxLap) : "—", "laps completed", "#3b66ff"));

  const m = d.biggestMover;
  cards.push(heroCard("Biggest mover", m && m.gained > 0 ? `+${m.gained}` : "—",
    m && m.gained > 0 ? `${driverAcr(m.n)} · P${m.grid}→P${m.fin}` : "Held position",
    m ? teamColorOf(m.n) : MUTED));

  els.hero.replaceChildren(...cards);
}

function heroCard(label, value, sub, color) {
  const node = document.createElement("div");
  node.className = "heroCard";
  node.style.setProperty("--accent-bar", color || "var(--accent)");
  node.innerHTML = `<span class="label"></span><span class="value"></span><span class="sub"></span>`;
  node.querySelector(".label").textContent = label;
  node.querySelector(".value").textContent = value;
  node.querySelector(".sub").textContent = sub;
  return node;
}

function renderDriverSelector() {
  const groups = state.derived.teamGroups;
  els.driverGrid.replaceChildren();
  if (!groups.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "No driver data";
    els.driverGrid.appendChild(empty);
    return;
  }

  for (const group of groups) {
    const box = document.createElement("div");
    box.className = "teamGroup";
    box.style.setProperty("--team", group.color);

    const head = document.createElement("div");
    head.className = "teamName";
    head.innerHTML = `<span class="teamDot"></span><span class="teamLabel"></span>`;
    head.querySelector(".teamLabel").textContent = group.name;
    head.title = `Toggle ${group.name}`;
    head.addEventListener("click", () => {
      const nums = group.drivers.map((x) => Number(x.driver_number));
      const allOn = nums.every((n) => state.selectedDrivers.has(n));
      for (const n of nums) {
        if (allOn) state.selectedDrivers.delete(n);
        else state.selectedDrivers.add(n);
      }
      renderDriverSelector();
      render();
    });
    box.appendChild(head);

    for (const driver of group.drivers) {
      box.appendChild(driverChip(driver, group.color));
    }
    els.driverGrid.appendChild(box);
  }
}

function driverChip(driver, color) {
  const num = Number(driver.driver_number);
  const on = state.selectedDrivers.has(num);
  const chip = document.createElement("div");
  chip.className = `driverChip ${on ? "on" : "off"}`;
  chip.style.setProperty("--team", color);

  const avatar = document.createElement("div");
  avatar.className = "driverAvatar";
  avatar.textContent = driver.name_acronym || num;
  if (driver.headshot_url) {
    const img = document.createElement("img");
    img.loading = "lazy";
    img.alt = "";
    img.src = driver.headshot_url;
    img.addEventListener("error", () => img.remove());
    avatar.appendChild(img);
  }

  const meta = document.createElement("div");
  meta.className = "meta";
  meta.innerHTML = `<span class="acr"></span><span class="full"></span>`;
  meta.querySelector(".acr").textContent = driver.name_acronym || `#${num}`;
  meta.querySelector(".full").textContent = titleCaseName(driver.full_name);

  const number = document.createElement("span");
  number.className = "num";
  number.textContent = num;

  chip.append(avatar, meta, number);
  chip.addEventListener("click", () => {
    if (state.selectedDrivers.has(num)) state.selectedDrivers.delete(num);
    else state.selectedDrivers.add(num);
    chip.classList.toggle("on");
    chip.classList.toggle("off");
    render();
  });
  return chip;
}

function syncTabs() {
  for (const btn of els.chartTabs.querySelectorAll(".tab")) {
    btn.classList.toggle("active", btn.dataset.chart === state.activeChart);
  }
  els.chartHint.textContent = CHART_HINTS[state.activeChart] || "";
}

// ---------- Charts ----------

function renderChart() {
  const drawers = {
    positions: drawPositions,
    pace: drawPace,
    strategy: drawStrategy,
    degradation: drawDegradation,
  };
  chartHits = { points: [], rects: [] };
  (drawers[state.activeChart] || drawPositions)();
}

function prepCanvas() {
  const canvas = els.chart;
  const w = Math.max(320, canvas.clientWidth || 900);
  const h = Math.max(260, canvas.clientHeight || 440);
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(w * dpr);
  canvas.height = Math.round(h * dpr);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = CANVAS_BG;
  roundRect(ctx, 0, 0, w, h, 8);
  ctx.fill();
  ctx.font = "12px Inter, system-ui, sans-serif";
  ctx.textBaseline = "alphabetic";
  return { ctx, w, h };
}

function selectedDriverNumbers() {
  return state.derived.driversSorted
    .map((d) => Number(d.driver_number))
    .filter((n) => state.selectedDrivers.has(n));
}

function xLapTicks(ctx, pad, plotW, plotH, top, minLap, maxLap) {
  const span = Math.max(1, maxLap - minLap);
  const step = span <= 20 ? 5 : span <= 45 ? 10 : 15;
  ctx.textAlign = "center";
  ctx.fillStyle = MUTED;
  for (let lap = Math.ceil(minLap / step) * step; lap <= maxLap; lap += step) {
    const x = pad.left + ((lap - minLap) / span) * plotW;
    ctx.strokeStyle = GRID;
    ctx.beginPath();
    ctx.moveTo(x, top);
    ctx.lineTo(x, top + plotH);
    ctx.stroke();
    ctx.fillText(`L${lap}`, x, top + plotH + 18);
  }
}

function drawPositions() {
  const { ctx, w, h } = prepCanvas();
  const d = state.derived;
  const nums = selectedDriverNumbers().filter((n) => d.positionByLap.has(n));
  if (!nums.length || !d.maxLap) {
    drawEmptyChart("No position data for the current selection");
    return;
  }
  const pad = { left: 42, right: 58, top: 16, bottom: 30 };
  const plotW = w - pad.left - pad.right;
  const plotH = h - pad.top - pad.bottom;
  const minLap = 1;
  const maxLap = d.maxLap;
  const maxPos = d.maxPos;
  const xs = (lap) => pad.left + ((lap - minLap) / Math.max(1, maxLap - minLap)) * plotW;
  const ys = (pos) => pad.top + ((pos - 1) / Math.max(1, maxPos - 1)) * plotH;

  // y gridlines / position labels
  ctx.textAlign = "right";
  for (const pos of [1, 5, 10, 15, 20].filter((p) => p <= maxPos)) {
    const y = ys(pos);
    ctx.strokeStyle = GRID;
    ctx.beginPath();
    ctx.moveTo(pad.left, y);
    ctx.lineTo(pad.left + plotW, y);
    ctx.stroke();
    ctx.fillStyle = MUTED;
    ctx.fillText(`P${pos}`, pad.left - 8, y + 4);
  }
  xLapTicks(ctx, pad, plotW, plotH, pad.top, minLap, maxLap);

  const hovered = state.hoverKey;
  const someHover = hovered && hovered.startsWith("positions:");
  const hoverNum = someHover ? Number(hovered.split(":")[1]) : null;

  for (const n of nums) {
    const pts = d.positionByLap.get(n);
    const color = teamColorOf(n);
    const isHover = hoverNum === n;
    ctx.globalAlpha = !someHover || isHover ? 1 : 0.18;
    ctx.strokeStyle = color;
    ctx.lineWidth = isHover ? 3.4 : 2;
    ctx.lineJoin = "round";
    ctx.beginPath();
    pts.forEach((p, idx) => {
      const x = xs(p.lap);
      const y = ys(p.pos);
      if (idx === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
      chartHits.points.push({ x, y, r: 9, key: `positions:${n}`, color,
        tip: tipHTML(color, driverAcr(n), [["Lap", p.lap], ["Position", `P${p.pos}`]]) });
    });
    ctx.stroke();

    // end label
    const last = pts[pts.length - 1];
    ctx.globalAlpha = !someHover || isHover ? 1 : 0.3;
    ctx.fillStyle = color;
    ctx.textAlign = "left";
    ctx.font = "700 11px Inter, system-ui, sans-serif";
    ctx.fillText(driverAcr(n), xs(last.lap) + 6, ys(last.pos) + 4);
    ctx.font = "12px Inter, system-ui, sans-serif";
  }
  ctx.globalAlpha = 1;
  renderDriverLegend(nums);
  els.chartCaption.textContent = `${nums.length} drivers · ${maxLap} laps`;
}

function drawPace() {
  const { ctx, w, h } = prepCanvas();
  const d = state.derived;
  const laps = filteredLaps().filter((l) => validLapDuration(l.lap_duration) && !l.is_pit_out_lap);
  if (!laps.length) {
    drawEmptyChart("No lap data for the current filters");
    return;
  }
  const pad = { left: 56, right: 52, top: 16, bottom: 30 };
  const plotW = w - pad.left - pad.right;
  const plotH = h - pad.top - pad.bottom;
  const lapNums = laps.map((l) => Number(l.lap_number));
  const times = laps.map((l) => Number(l.lap_duration));
  const minLap = Math.min(...lapNums);
  const maxLap = Math.max(...lapNums);
  const minT = Math.min(...times);
  const maxT = Math.max(...times);
  const yPad = Math.max(0.3, (maxT - minT) * 0.08);
  const yMin = minT - yPad;
  const yMax = maxT + yPad;
  const xs = (lap) => pad.left + ((lap - minLap) / Math.max(1, maxLap - minLap)) * plotW;
  const ys = (t) => pad.top + ((yMax - t) / Math.max(0.001, yMax - yMin)) * plotH;

  ctx.textAlign = "right";
  for (let i = 0; i <= 4; i += 1) {
    const t = yMax - ((yMax - yMin) * i) / 4;
    const y = pad.top + (plotH * i) / 4;
    ctx.strokeStyle = GRID;
    ctx.beginPath();
    ctx.moveTo(pad.left, y);
    ctx.lineTo(pad.left + plotW, y);
    ctx.stroke();
    ctx.fillStyle = MUTED;
    ctx.fillText(formatLapTime(t), pad.left - 8, y + 4);
  }
  xLapTicks(ctx, pad, plotW, plotH, pad.top, minLap, maxLap);

  // fastest-lap reference within view
  const fastest = laps.reduce((a, b) => (Number(b.lap_duration) < Number(a.lap_duration) ? b : a), laps[0]);
  const fy = ys(Number(fastest.lap_duration));
  ctx.strokeStyle = "rgba(180,120,255,0.7)";
  ctx.setLineDash([6, 5]);
  ctx.beginPath();
  ctx.moveTo(pad.left, fy);
  ctx.lineTo(pad.left + plotW, fy);
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle = "#b478ff";
  ctx.textAlign = "left";
  ctx.fillText(`Fastest ${formatLapTime(fastest.lap_duration)} (${driverAcr(fastest.driver_number)})`, pad.left + 6, fy - 6);

  const hovered = state.hoverKey;
  const someHover = hovered && hovered.startsWith("pace:");
  const hoverNum = someHover ? Number(hovered.split(":")[1]) : null;

  const byDriver = groupBy(laps, (l) => Number(l.driver_number));
  for (const [n, rows] of byDriver) {
    const color = teamColorOf(n);
    const isHover = hoverNum === n;
    const sorted = rows.slice().sort((a, b) => Number(a.lap_number) - Number(b.lap_number));
    ctx.globalAlpha = !someHover || isHover ? 1 : 0.14;
    ctx.strokeStyle = color;
    ctx.lineWidth = isHover ? 3 : 1.6;
    ctx.beginPath();
    sorted.forEach((lap, idx) => {
      const x = xs(Number(lap.lap_number));
      const y = ys(Number(lap.lap_duration));
      if (idx === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
      const meta = d.lapMeta.get(`${n}:${Number(lap.lap_number)}`);
      chartHits.points.push({ x, y, r: 7, key: `pace:${n}`, color,
        tip: tipHTML(color, driverAcr(n), [
          ["Lap", lap.lap_number],
          ["Time", formatLapTime(lap.lap_duration)],
          ["Tyre", meta?.compound || "—"],
        ]) });
    });
    ctx.stroke();
  }
  ctx.globalAlpha = 1;
  renderDriverLegend([...byDriver.keys()]);
  els.chartCaption.textContent = `${laps.length} laps · ${byDriver.size} drivers`;
}

function drawStrategy() {
  const { ctx, w, h } = prepCanvas();
  const d = state.derived;
  const nums = selectedDriverNumbers();
  const stints = dataset("stints");
  const rows = nums
    .map((n) => ({ n, stints: stints.filter((s) => Number(s.driver_number) === n) }))
    .filter((r) => r.stints.length);
  if (!rows.length || !d.maxLap) {
    drawEmptyChart("No stint data for the current selection");
    return;
  }
  const pad = { left: 52, right: 18, top: 14, bottom: 30 };
  const plotW = w - pad.left - pad.right;
  const plotH = h - pad.top - pad.bottom;
  const minLap = 1;
  const maxLap = d.maxLap;
  const xs = (lap) => pad.left + ((lap - minLap) / Math.max(1, maxLap - minLap)) * plotW;
  const rowH = plotH / rows.length;
  const barH = Math.min(26, rowH - 6);

  xLapTicks(ctx, pad, plotW, plotH, pad.top, minLap, maxLap);

  rows.forEach((row, idx) => {
    const yCenter = pad.top + rowH * idx + rowH / 2;
    const color = teamColorOf(row.n);
    ctx.fillStyle = color;
    ctx.textAlign = "right";
    ctx.font = "800 12px Inter, system-ui, sans-serif";
    ctx.fillText(driverAcr(row.n), pad.left - 8, yCenter + 4);
    ctx.font = "12px Inter, system-ui, sans-serif";

    for (const s of row.stints) {
      const x0 = xs(Number(s.lap_start));
      const x1 = xs(Number(s.lap_end) + 1);
      const bw = Math.max(2, x1 - x0 - 2);
      const comp = String(s.compound || "").toUpperCase();
      const cColor = COMPOUND_COLORS[comp] || MUTED;
      ctx.fillStyle = cColor;
      roundRect(ctx, x0, yCenter - barH / 2, bw, barH, 4);
      ctx.fill();
      if (bw > 18) {
        ctx.fillStyle = "#10141d";
        ctx.textAlign = "center";
        ctx.font = "900 11px Inter, system-ui, sans-serif";
        ctx.fillText(comp.slice(0, 1) || "?", x0 + bw / 2, yCenter + 4);
        ctx.font = "12px Inter, system-ui, sans-serif";
      }
      chartHits.rects.push({ x: x0, y: yCenter - barH / 2, w: bw, h: barH, key: `strat:${row.n}:${s.stint_number}`,
        color: cColor,
        tip: tipHTML(cColor, `${driverAcr(row.n)} · Stint ${s.stint_number}`, [
          ["Compound", comp || "—"],
          ["Laps", `${s.lap_start}–${s.lap_end} (${Number(s.lap_end) - Number(s.lap_start) + 1})`],
          ["Tyre age start", `${s.tyre_age_at_start ?? "—"}`],
        ]) });
    }
  });
  renderCompoundLegend(rows.flatMap((r) => r.stints.map((s) => String(s.compound || "").toUpperCase())));
  els.chartCaption.textContent = `${rows.length} drivers · ${maxLap} laps`;
}

function drawDegradation() {
  const { ctx, w, h } = prepCanvas();
  const nums = new Set(selectedDriverNumbers());
  const rows = dataset("degradation")
    .filter((r) => nums.has(Number(r.driver_number)))
    .map((r) => ({
      n: Number(r.driver_number),
      stint: Number(r.stint_number),
      compound: String(r.compound || "").toUpperCase(),
      deg: Number(r.deg_rate_sec_per_lap),
      base: Number(r.base_pace),
      best: Number(r.best_lap_time),
      avg: Number(r.avg_lap_time),
      r2: Number(r.r_squared),
      len: Number(r.stint_length),
    }))
    .filter((r) => Number.isFinite(r.deg))
    .sort((a, b) => a.deg - b.deg);
  if (!rows.length) {
    drawEmptyChart("No degradation model for the current selection (needs longer stints)");
    return;
  }
  const pad = { left: 96, right: 70, top: 14, bottom: 30 };
  const plotW = w - pad.left - pad.right;
  const plotH = h - pad.top - pad.bottom;
  const degs = rows.map((r) => r.deg);
  const lo = Math.min(0, ...degs);
  const hi = Math.max(0.05, ...degs);
  const xs = (v) => pad.left + ((v - lo) / Math.max(0.001, hi - lo)) * plotW;
  const rowH = plotH / rows.length;
  const barH = Math.min(22, rowH - 5);
  const zeroX = xs(0);

  // x ticks
  ctx.textAlign = "center";
  const xStep = (hi - lo) / 4;
  for (let i = 0; i <= 4; i += 1) {
    const v = lo + xStep * i;
    const x = xs(v);
    ctx.strokeStyle = i === 0 || Math.abs(v) < 1e-9 ? AXIS : GRID;
    ctx.beginPath();
    ctx.moveTo(x, pad.top);
    ctx.lineTo(x, pad.top + plotH);
    ctx.stroke();
    ctx.fillStyle = MUTED;
    ctx.fillText(`${v.toFixed(2)}`, x, pad.top + plotH + 18);
  }

  rows.forEach((r, idx) => {
    const yCenter = pad.top + rowH * idx + rowH / 2;
    const cColor = COMPOUND_COLORS[r.compound] || MUTED;
    const x0 = Math.min(zeroX, xs(r.deg));
    const x1 = Math.max(zeroX, xs(r.deg));
    const bw = Math.max(2, x1 - x0);
    ctx.fillStyle = cColor;
    roundRect(ctx, x0, yCenter - barH / 2, bw, barH, 4);
    ctx.fill();

    ctx.fillStyle = INK;
    ctx.textAlign = "right";
    ctx.font = "800 11px Inter, system-ui, sans-serif";
    ctx.fillText(`${driverAcr(r.n)} S${r.stint}`, pad.left - 10, yCenter + 4);
    ctx.textAlign = "left";
    ctx.fillStyle = cColor;
    ctx.fillText(`${r.deg.toFixed(3)}`, x1 + 8, yCenter + 4);
    ctx.font = "12px Inter, system-ui, sans-serif";

    chartHits.rects.push({ x: Math.min(x0, pad.left), y: yCenter - barH / 2, w: Math.max(bw, plotW), h: barH,
      key: `deg:${r.n}:${r.stint}`, color: cColor,
      tip: tipHTML(cColor, `${driverAcr(r.n)} · Stint ${r.stint} · ${r.compound}`, [
        ["Deg rate", `${r.deg.toFixed(3)} s/lap`],
        ["Base pace", formatLapTime(r.base)],
        ["Best lap", formatLapTime(r.best)],
        ["Stint length", `${r.len} laps`],
        ["Model R²", Number.isFinite(r.r2) ? r.r2.toFixed(2) : "—"],
      ]) });
  });
  renderCompoundLegend(rows.map((r) => r.compound));
  els.chartCaption.textContent = `${rows.length} stints`;
}

function drawEmptyChart(message) {
  const { ctx, w, h } = prepCanvas();
  ctx.strokeStyle = AXIS;
  roundRect(ctx, 16, 16, w - 32, h - 32, 8);
  ctx.stroke();
  ctx.fillStyle = MUTED;
  ctx.textAlign = "center";
  ctx.font = "14px Inter, system-ui, sans-serif";
  ctx.fillText(message, w / 2, h / 2);
  els.chartLegend.replaceChildren();
  els.chartCaption.textContent = "";
}

// ---------- Chart legends + hover ----------

function renderDriverLegend(nums) {
  const ordered = state.derived.driversSorted
    .map((x) => Number(x.driver_number))
    .filter((n) => nums.includes(n));
  const items = ordered.map((n) => {
    const item = document.createElement("span");
    item.className = "legendItem";
    const sw = document.createElement("span");
    sw.className = "swatch";
    sw.style.background = teamColorOf(n);
    const txt = document.createElement("span");
    txt.textContent = driverAcr(n);
    item.append(sw, txt);
    item.addEventListener("mouseenter", () => { state.hoverKey = `${state.activeChart}:${n}`; renderChart(); });
    item.addEventListener("mouseleave", () => { state.hoverKey = null; renderChart(); });
    return item;
  });
  els.chartLegend.replaceChildren(...items);
}

function renderCompoundLegend(compounds) {
  const present = [...new Set(compounds)].filter(Boolean);
  const order = ["SOFT", "MEDIUM", "HARD", "INTERMEDIATE", "WET"];
  present.sort((a, b) => order.indexOf(a) - order.indexOf(b));
  const items = present.map((c) => {
    const item = document.createElement("span");
    item.className = "legendItem";
    const sw = document.createElement("span");
    sw.className = "swatch dot";
    sw.style.background = COMPOUND_COLORS[c] || MUTED;
    const txt = document.createElement("span");
    txt.textContent = titleCase(c);
    item.append(sw, txt);
    return item;
  });
  els.chartLegend.replaceChildren(...items);
}

function onChartHover(event) {
  const rect = els.chart.getBoundingClientRect();
  const mx = event.clientX - rect.left;
  const my = event.clientY - rect.top;

  // rects first (bars / gantt)
  for (const r of chartHits.rects) {
    if (mx >= r.x && mx <= r.x + r.w && my >= r.y && my <= r.y + r.h) {
      showTip(mx, my, r.tip);
      return;
    }
  }
  // nearest point within radius
  let best = null;
  let bestDist = Infinity;
  for (const p of chartHits.points) {
    const dx = p.x - mx;
    const dy = p.y - my;
    const dist = dx * dx + dy * dy;
    if (dist < bestDist) { bestDist = dist; best = p; }
  }
  if (best && bestDist <= 14 * 14) {
    const key = best.key;
    if (key !== state.hoverKey && (key.startsWith("positions:") || key.startsWith("pace:"))) {
      state.hoverKey = key;
      renderChart();
    }
    showTip(best.x, best.y, best.tip);
  } else {
    hideTip();
    if (state.hoverKey) { state.hoverKey = null; renderChart(); }
  }
}

function showTip(x, y, html) {
  const tip = els.chartTip;
  tip.innerHTML = html;
  tip.hidden = false;
  const stage = els.chartStage;
  const left = els.chart.offsetLeft + x;
  const top = els.chart.offsetTop + y;
  tip.style.left = `${Math.max(70, Math.min(stage.clientWidth - 70, left))}px`;
  tip.style.top = `${top}px`;
}

function hideTip() {
  els.chartTip.hidden = true;
}

function tipHTML(color, head, rows) {
  const body = rows.map(([k, v]) => `<div class="tipRow">${k}: <b>${escapeHTML(v)}</b></div>`).join("");
  return `<div class="tipHead"><span class="tipDot" style="background:${color}"></span>${escapeHTML(head)}</div>${body}`;
}

// ---------- Tables ----------

function renderLapTable() {
  const d = state.derived;
  const all = filteredLaps();
  const rows = all.slice(0, 400);
  els.lapCount.textContent = all.length > rows.length ? `${rows.length}/${all.length} laps` : `${all.length} laps`;
  const fastestId = d.fastest ? `${Number(d.fastest.driver_number)}:${Number(d.fastest.lap_number)}` : null;

  renderRows(els.lapRows, rows, 9, (tr, lap) => {
    const n = Number(lap.driver_number);
    const meta = d.lapMeta.get(`${n}:${Number(lap.lap_number)}`);
    const isFastest = fastestId === `${n}:${Number(lap.lap_number)}`;
    appendCell(tr, lap.lap_number, "number");
    appendDriverCell(tr, n);
    appendCompoundCell(tr, meta?.compound);
    appendCell(tr, formatLapTime(lap.lap_duration), `number${isFastest ? " fastest" : ""}`);
    appendCell(tr, formatLapTime(lap.duration_sector_1), "number");
    appendCell(tr, formatLapTime(lap.duration_sector_2), "number");
    appendCell(tr, formatLapTime(lap.duration_sector_3), "number");
    appendDeltaCell(tr, meta?.delta);
    appendCell(tr, lap.is_pit_out_lap ? "OUT" : "");
  });
}

function renderStintTable() {
  const d = state.derived;
  const degByKey = new Map(dataset("degradation").map((r) => [`${Number(r.driver_number)}:${Number(r.stint_number)}`, r]));
  const rows = dataset("stints")
    .filter((s) => state.selectedDrivers.has(Number(s.driver_number)))
    .sort((a, b) => (d.finalPos.get(Number(a.driver_number)) ?? 99) - (d.finalPos.get(Number(b.driver_number)) ?? 99)
      || Number(a.stint_number) - Number(b.stint_number));
  els.stintCount.textContent = `${rows.length} stints`;
  renderRows(els.stintRows, rows, 6, (tr, s) => {
    const n = Number(s.driver_number);
    const deg = degByKey.get(`${n}:${Number(s.stint_number)}`);
    appendDriverCell(tr, n);
    appendCell(tr, s.stint_number, "number");
    appendCompoundCell(tr, s.compound);
    appendCell(tr, `${s.lap_start}–${s.lap_end}`);
    appendCell(tr, deg && Number.isFinite(Number(deg.deg_rate_sec_per_lap)) ? `${Number(deg.deg_rate_sec_per_lap).toFixed(3)}s` : "—", "number");
    appendCell(tr, deg && Number.isFinite(Number(deg.best_lap_time)) ? formatLapTime(deg.best_lap_time) : "—", "number");
  });
}

function renderRadio() {
  const term = state.search;
  const rows = dataset("radio")
    .filter((r) => state.selectedDrivers.has(Number(r.driver_number)))
    .filter((r) => !r.approx_lap_number || inLapRange(r.approx_lap_number))
    .filter((r) => {
      if (!term) return true;
      const hay = [r.name_acronym, r.team_name, r.transcript_en, r.transcript_zh, r.intent, r.sentiment]
        .join(" ").toLowerCase();
      return hay.includes(term);
    })
    .sort((a, b) => String(a.radio_time || "").localeCompare(String(b.radio_time || "")));

  els.radioCount.textContent = `${rows.length} messages`;
  els.radioList.replaceChildren();
  if (!rows.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "No radio messages";
    els.radioList.appendChild(empty);
    return;
  }

  for (const r of rows) {
    const n = Number(r.driver_number);
    const row = document.createElement("div");
    row.className = "radioRow";

    const meta = document.createElement("div");
    meta.className = "radioMeta";
    const bar = document.createElement("span");
    bar.className = "bar";
    bar.style.cssText = `width:4px;height:15px;border-radius:2px;background:${teamColorOf(n)}`;
    const who = document.createElement("strong");
    who.textContent = driverAcr(n);
    const lap = document.createElement("span");
    lap.className = "lapTag";
    lap.textContent = r.approx_lap_number ? `Lap ${r.approx_lap_number} · ${formatClock(r.radio_time)}` : formatClock(r.radio_time);
    const status = document.createElement("span");
    status.className = "tag";
    status.textContent = r.intent && r.intent !== "audio" ? r.intent : (r.translation_status || "audio");
    meta.append(bar, who, lap, status);
    row.appendChild(meta);

    if (r.recording_url) {
      const audio = document.createElement("audio");
      audio.controls = true;
      audio.preload = "none";
      audio.src = r.recording_url;
      row.appendChild(audio);
    }

    const text = r.transcript_en || r.transcript_zh;
    if (text) {
      const t = document.createElement("div");
      t.className = "radioText";
      t.innerHTML = `${escapeHTML(r.transcript_en || "")}${r.transcript_zh ? `<div class="zh">${escapeHTML(r.transcript_zh)}</div>` : ""}`;
      row.appendChild(t);
    }
    els.radioList.appendChild(row);
  }
}

function renderExtraPanels() {
  const pit = dataset("pit_stops").filter((r) => state.selectedDrivers.has(Number(r.driver_number)) && inLapRange(r.lap_number));
  const control = dataset("race_control").filter((r) => !r.lap_number || inLapRange(r.lap_number));
  els.extraPanels.replaceChildren();
  if (pit.length) els.extraPanels.appendChild(pitPanel(pit));
  if (control.length) els.extraPanels.appendChild(controlPanel(control));
}

function pitPanel(rows) {
  const panel = makePanel("Pit stops", `${rows.length} stops`);
  const table = makeTable(["Lap", "Driver", "Duration"]);
  renderRows(table.tbody, rows, 3, (tr, r) => {
    appendCell(tr, r.lap_number, "number");
    appendDriverCell(tr, Number(r.driver_number));
    appendCell(tr, Number.isFinite(Number(r.pit_duration)) ? `${Number(r.pit_duration).toFixed(2)}s` : "—", "number");
  });
  panel.appendChild(table.wrap);
  return panel;
}

function controlPanel(rows) {
  const panel = makePanel("Race control", `${rows.length} messages`);
  const table = makeTable(["Lap", "Flag", "Message"]);
  renderRows(table.tbody, rows, 3, (tr, r) => {
    appendCell(tr, r.lap_number ?? "—", "number");
    appendTagCell(tr, r.flag || r.category || "—");
    appendCell(tr, r.message || "—");
  });
  panel.appendChild(table.wrap);
  return panel;
}

// ---------- Filtering helpers ----------

function filteredLaps() {
  return dataset("laps")
    .filter((l) => state.selectedDrivers.has(Number(l.driver_number)))
    .filter((l) => inLapRange(l.lap_number))
    .sort((a, b) => Number(a.lap_number) - Number(b.lap_number) || Number(a.driver_number) - Number(b.driver_number));
}

function inLapRange(value) {
  const lap = Number(value);
  if (!Number.isFinite(lap)) return true;
  if (state.lapMin != null && lap < state.lapMin) return false;
  if (state.lapMax != null && lap > state.lapMax) return false;
  return true;
}

// ---------- Data accessors ----------

function dataset(key) {
  const value = state.datasets[key];
  return Array.isArray(value) ? value : [];
}

function currentSession() {
  return state.manifest.sessions.find((s) => Number(s.session_key) === Number(state.selectedSessionKey));
}
function currentMeeting() {
  return state.manifest.meetings.find((m) => Number(m.meeting_key) === Number(state.selectedMeetingKey));
}
function meetingsForYear(year) {
  return state.manifest.meetings.filter((m) => Number(m.year) === Number(year));
}
function sessionsForMeeting(meetingKey) {
  return state.manifest.sessions.filter((s) => Number(s.meeting_key) === Number(meetingKey));
}
function pickDefaultSession(sessions) {
  if (!sessions.length) return null;
  const race = sessions.filter((s) => `${s.session_name} ${s.session_type}`.toLowerCase().includes("race"));
  return (race.length ? race : sessions).slice()
    .sort((a, b) => String(b.date_start || "").localeCompare(String(a.date_start || "")))[0];
}

function driverByNumber(n) {
  return state.derived?.byNumber.get(Number(n)) || dataset("drivers").find((d) => Number(d.driver_number) === Number(n));
}
function driverAcr(n) {
  return driverByNumber(n)?.name_acronym || `#${n}`;
}
function driverFull(n) {
  return titleCaseName(driverByNumber(n)?.full_name) || driverAcr(n);
}
function teamColorOf(n) {
  return teamColor(driverByNumber(n));
}
function teamColor(driver) {
  const raw = driver?.team_colour || "";
  if (/^[0-9a-fA-F]{6}$/.test(raw)) return `#${raw}`;
  const fallback = ["#e10600", "#00a19c", "#3b66ff", "#f7a600", "#7c3aed"];
  return fallback[Math.abs(Number(driver?.driver_number || 0)) % fallback.length];
}

// ---------- Small DOM helpers ----------

function fillSelect(select, options, valueFn, labelFn, selectedValue) {
  select.replaceChildren();
  for (const option of options) {
    const node = document.createElement("option");
    node.value = valueFn(option);
    node.textContent = labelFn(option);
    node.selected = String(node.value) === String(selectedValue);
    select.appendChild(node);
  }
}

function renderRows(tbody, rows, colspan, renderRow) {
  tbody.replaceChildren();
  if (!rows.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = colspan;
    td.className = "empty";
    td.textContent = "No data";
    tr.appendChild(td);
    tbody.appendChild(tr);
    return;
  }
  for (const item of rows) {
    const tr = document.createElement("tr");
    renderRow(tr, item);
    tbody.appendChild(tr);
  }
}

function appendCell(tr, value, className = "") {
  const td = document.createElement("td");
  if (className) td.className = className;
  td.textContent = value == null || value === "" ? (className.includes("number") ? "—" : "") : String(value);
  tr.appendChild(td);
}

function appendDriverCell(tr, n) {
  const td = document.createElement("td");
  const wrap = document.createElement("span");
  wrap.className = "driverCell";
  const bar = document.createElement("span");
  bar.className = "bar";
  bar.style.background = teamColorOf(n);
  const name = document.createElement("span");
  name.textContent = driverAcr(n);
  wrap.append(bar, name);
  td.appendChild(wrap);
  tr.appendChild(td);
}

function appendCompoundCell(tr, compound) {
  const td = document.createElement("td");
  const c = String(compound || "").toUpperCase();
  if (c) {
    const pill = document.createElement("span");
    pill.className = `compound ${c.toLowerCase()}`;
    pill.textContent = c.slice(0, 1);
    pill.title = titleCase(c);
    td.appendChild(pill);
  } else {
    td.textContent = "—";
  }
  tr.appendChild(td);
}

function appendDeltaCell(tr, delta) {
  const td = document.createElement("td");
  td.className = "number";
  const v = Number(delta);
  if (Number.isFinite(v)) {
    const span = document.createElement("span");
    span.className = `delta ${v < 0 ? "neg" : v > 0 ? "pos" : ""}`;
    span.textContent = `${v > 0 ? "+" : ""}${v.toFixed(3)}`;
    td.appendChild(span);
  } else {
    td.textContent = "—";
  }
  tr.appendChild(td);
}

function appendTagCell(tr, value) {
  const td = document.createElement("td");
  const tag = document.createElement("span");
  tag.className = "tag";
  tag.textContent = value == null || value === "" ? "—" : String(value);
  td.appendChild(tag);
  tr.appendChild(td);
}

function makePanel(title, count) {
  const panel = document.createElement("div");
  panel.className = "panel";
  const head = document.createElement("div");
  head.className = "panelHeader";
  head.innerHTML = `<h2></h2><span class="muted"></span>`;
  head.querySelector("h2").textContent = title;
  head.querySelector(".muted").textContent = count;
  panel.appendChild(head);
  return panel;
}

function makeTable(headers) {
  const wrap = document.createElement("div");
  wrap.className = "tableWrap compact";
  const table = document.createElement("table");
  table.className = "dataTable";
  const thead = document.createElement("thead");
  const tr = document.createElement("tr");
  for (const h of headers) {
    const th = document.createElement("th");
    if (h === "Lap" || h === "Duration") th.className = "number";
    th.textContent = h;
    tr.appendChild(th);
  }
  thead.appendChild(tr);
  const tbody = document.createElement("tbody");
  table.append(thead, tbody);
  wrap.appendChild(table);
  return { wrap, tbody };
}

// ---------- Formatting ----------

async function fetchJSON(path) {
  const response = await fetch(new URL(path, window.location.href));
  if (!response.ok) throw new Error(`Fetch ${path}: ${response.status}`);
  return response.json();
}

function formatLapTime(value) {
  const seconds = Number(value);
  if (!Number.isFinite(seconds) || seconds <= 0) return "—";
  const minutes = Math.floor(seconds / 60);
  const rest = seconds - minutes * 60;
  return minutes > 0 ? `${minutes}:${rest.toFixed(3).padStart(6, "0")}` : `${rest.toFixed(3)}`;
}

function formatDate(value) {
  const t = Date.parse(value);
  if (!Number.isFinite(t)) return "";
  return new Date(t).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

function formatClock(value) {
  const t = Date.parse(value);
  if (!Number.isFinite(t)) return "";
  return new Date(t).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

function validLapDuration(value) {
  const s = Number(value);
  return Number.isFinite(s) && s > 0;
}

function titleCase(str) {
  const s = String(str || "").toLowerCase();
  return s ? s[0].toUpperCase() + s.slice(1) : "";
}

function titleCaseName(name) {
  if (!name) return "";
  return String(name)
    .split(" ")
    .map((part) => (part === part.toUpperCase() && part.length > 1
      ? part[0] + part.slice(1).toLowerCase()
      : part))
    .join(" ");
}

function groupBy(rows, keyFn) {
  const map = new Map();
  for (const row of rows) {
    const key = keyFn(row);
    if (!map.has(key)) map.set(key, []);
    map.get(key).push(row);
  }
  return map;
}

function numberOrNull(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function escapeHTML(value) {
  return String(value == null ? "" : value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function roundRect(ctx, x, y, w, h, r) {
  const radius = Math.min(r, w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + radius, y);
  ctx.arcTo(x + w, y, x + w, y + h, radius);
  ctx.arcTo(x + w, y + h, x, y + h, radius);
  ctx.arcTo(x, y + h, x, y, radius);
  ctx.arcTo(x, y, x + w, y, radius);
  ctx.closePath();
}

function setStatus(text, mode) {
  els.loadStatus.textContent = text;
  els.loadStatus.className = mode ? `status ${mode}` : "status";
}
