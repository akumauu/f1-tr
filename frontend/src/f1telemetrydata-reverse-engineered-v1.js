import { resolveOfficialTeamIdentity } from "./official-team-colours.js";

const INDEX_PATH = "data/reference-analysis-lab/v2/manifest.json";
const TARGET_ID = "f1telemetrydata-reverse-engineered-v1";
const TARGET_STATUS = "METHOD_EQUIVALENT_REFERENCE_BENCHMARKED";
const OPAQUE_STATUS = "SKIPPED_OPAQUE_METHOD";
const GEOMETRY_STATUS = "NOT_TESTED_OFFICIAL_GEOMETRIC_ANCHOR";
const NON_IDENTIFIABLE = "NOT_IDENTIFIABLE";
const VIEW_VISUAL = "visual_replication";
const VIEW_AUDIT = "audited_analysis";
const state = { payload: null, manifest: null, view: VIEW_VISUAL };
const $ = (id) => document.getElementById(id);

const DRIVER_COLOURS = {
  VER: "#63e6be",
  NOR: "#ff8f3d",
  PIA: "#52c7ea",
  RUS: "#b9ddff",
  LEC: "#ff6271",
  ALO: "#48d6a0",
  HAM: "#bd91ff",
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function finite(value) {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function number(value, digits = 3, suffix = "") {
  const parsed = finite(value);
  return parsed === null ? "—" : `${parsed.toFixed(digits)}${suffix}`;
}

function signed(value, digits = 3, suffix = "") {
  const parsed = finite(value);
  if (parsed === null) return "—";
  return `${parsed > 0 ? "+" : ""}${parsed.toFixed(digits)}${suffix}`;
}

function teamColour(team) {
  const identity = resolveOfficialTeamIdentity(2025, team);
  return identity?.status === "official" ? identity.colour : "#52c7ea";
}

function driverColour(driver, team = "") {
  return DRIVER_COLOURS[driver] || teamColour(team);
}

async function sha256Hex(buffer) {
  const hash = await crypto.subtle.digest("SHA-256", buffer);
  return [...new Uint8Array(hash)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

async function fetchJsonWithHash(path, expectedHash = null) {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) throw new Error(`读取失败：${response.status} ${path}`);
  const bytes = await response.arrayBuffer();
  const actualHash = await sha256Hex(bytes);
  if (expectedHash && actualHash !== expectedHash) {
    throw new Error(`SHA-256 不匹配：${path}`);
  }
  return JSON.parse(new TextDecoder().decode(bytes));
}

function pathFrom(values, axis, xScale, yScale) {
  return values.map((value, index) => {
    const x = xScale(axis[index]);
    const y = yScale(value);
    return `${index ? "L" : "M"}${x.toFixed(2)},${y.toFixed(2)}`;
  }).join(" ");
}

function renderMetrics(payload) {
  const validation = payload.validation;
  const q = validation.qualifying;
  const race = validation.race;
  const phase = payload.audited_analysis.qualifying.phase_coverage;
  const metrics = [
    ["公开图语义", "13 / 13", "同场排位 7 类 · 正赛 6 类"],
    ["Q 阶段覆盖", `${phase.identified} / ${phase.frozen_push_laps}`, `Q1 ${phase.counts.Q1} · Q2 ${phase.counts.Q2} · Q3 ${phase.counts.Q3}`],
    ["排位 Delta MAE", number(q.lap_delta.mae, 6, "s"), `${q.lap_delta.coverage}/${q.lap_delta.expected} 车手`],
    ["正赛均值差 MAE", number(race.average_gap.mae, 6, "s"), `最大 ${number(race.average_gap.max_abs_error, 6, "s")}`],
    ["控制分段 MAE", number(q.lap_sections.mae_pp, 3, "pp"), `最大 ${number(q.lap_sections.max_abs_error_pp, 3, "pp")}`],
    ["Pit 车队均值 MAE", number(race.pit_team_means.mae, 3, "s"), `最大 ${number(race.pit_team_means.max_abs_error, 3, "s")}`],
  ];
  $("metricGrid").innerHTML = metrics.map(([label, value, detail]) => (
    `<article class="metricCard"><div class="metricLabel">${escapeHtml(label)}</div>` +
    `<div class="metricValue">${escapeHtml(value)}</div>` +
    `<div class="metricDetail">${escapeHtml(detail)}</div></article>`
  )).join("");
}

function renderMethod(payload) {
  $("publicCharts").innerHTML = payload.method_identity.public_semantics_confirmed
    .map((value) => `<li>${escapeHtml(value)}</li>`)
    .join("");
}

function renderLapCompare(payload) {
  const q = payload.visual_replication.qualifying;
  const traces = q.lap_compare.traces;
  const reference = traces[0];
  const width = 1200;
  const height = 520;
  const left = 62;
  const right = 24;
  const plotWidth = width - left - right;
  const x = (value) => left + Number(value) * plotWidth;
  const speedY = (value) => 34 + (350 - Number(value)) / 290 * 170;
  const throttleY = (value) => 236 + (100 - Number(value)) / 100 * 100;
  const deltas = traces.flatMap((trace) => trace.time.map(
    (value, index) => Number(value) - Number(reference.time[index]),
  ));
  const deltaMin = Math.min(-.05, ...deltas);
  const deltaMax = Math.max(.05, ...deltas);
  const deltaY = (value) => 386 + (deltaMax - Number(value)) /
    Math.max(deltaMax - deltaMin, .001) * 104;
  const grid = [0, .2, .4, .6, .8, 1].map((tick) => (
    `<line class="gridLine" x1="${x(tick)}" x2="${x(tick)}" y1="25" y2="490"></line>` +
    `<text class="axisText" x="${x(tick)}" y="510" text-anchor="middle">${Math.round(tick * 100)}%</text>`
  )).join("");
  const labels = [
    ["速度 km/h", 27],
    ["油门 %", 229],
    ["累计 Δ s", 379],
  ].map(([label, y]) => `<text class="axisText" x="10" y="${y}">${label}</text>`).join("");
  const sectors = q.lap_compare.sector_anchor_proxy.map((anchor) => (
    `<line class="sectorLine" x1="${x(anchor.lap_fraction)}" x2="${x(anchor.lap_fraction)}" y1="25" y2="490"></line>` +
    `<text class="axisText" x="${x(anchor.lap_fraction) + 5}" y="44">${escapeHtml(anchor.anchor)}</text>`
  )).join("");
  const lines = traces.map((trace) => {
    const colour = driverColour(trace.driver, trace.team);
    const delta = trace.time.map(
      (value, index) => Number(value) - Number(reference.time[index]),
    );
    return [
      `<path class="chartLine" stroke="${colour}" d="${pathFrom(trace.speed, trace.axis, x, speedY)}"></path>`,
      `<path class="chartLine" stroke="${colour}" opacity=".86" d="${pathFrom(trace.throttle, trace.axis, x, throttleY)}"></path>`,
      `<path class="deltaLine" stroke="${colour}" d="${pathFrom(delta, trace.axis, x, deltaY)}"></path>`,
    ].join("");
  }).join("");
  const zero = `<line class="zeroLine" x1="${left}" x2="${width - right}" y1="${deltaY(0)}" y2="${deltaY(0)}"></line>`;
  const legend = traces.map((trace, index) => (
    `<g transform="translate(${left + index * 160},216)">` +
    `<line x1="0" x2="24" y1="0" y2="0" stroke="${driverColour(trace.driver, trace.team)}" stroke-width="3"></line>` +
    `<text class="axisText" x="30" y="4">${escapeHtml(trace.driver)} · ${number(trace.lap_time_s, 3, "s")}</text></g>`
  )).join("");
  $("lapCompareChart").innerHTML = (
    `<svg viewBox="0 0 ${width} ${height}" aria-hidden="true">${grid}${labels}${sectors}${zero}${lines}${legend}</svg>`
  );
  const fastest = q.lap_compare.fastest_sectors.map((row) => ({
    label: `S${row.sector} 最快`,
    value: `${row.driver} · ${number(row.time_s, 3, "s")}`,
  }));
  const leaders = q.lap_delta.slice(0, 2).map((row, index) => ({
    label: index ? "P2 差距" : "杆位",
    value: index ? `${row.driver} · ${signed(row.gap_to_fastest_s, 3, "s")}` : `${row.driver} · ${number(row.lap_time_s, 3, "s")}`,
  }));
  $("sectorCards").innerHTML = [...fastest, ...leaders].map((row) => (
    `<article class="miniCard"><span>${escapeHtml(row.label)}</span><strong>${escapeHtml(row.value)}</strong></article>`
  )).join("");
}

function renderDominance(payload) {
  const q = payload.visual_replication.qualifying;
  const dominance = q.track_dominance;
  const trace = q.lap_compare.traces[0];
  const xs = dominance.track_x.map(Number);
  const ys = dominance.track_y.map(Number);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const width = 680;
  const height = 430;
  const pad = 28;
  const scale = Math.min(
    (width - pad * 2) / Math.max(maxX - minX, 1),
    (height - pad * 2) / Math.max(maxY - minY, 1),
  );
  const tx = (value) => pad + (value - minX) * scale;
  const ty = (value) => height - pad - (value - minY) * scale;
  const basePath = xs.map((value, index) => (
    `${index ? "L" : "M"}${tx(value).toFixed(2)},${ty(ys[index]).toFixed(2)}`
  )).join(" ");
  const segments = dominance.segments.map((segment) => {
    const start = Math.max(0, Math.floor(segment.start_fraction * (xs.length - 1)));
    const end = Math.min(xs.length - 1, Math.ceil(segment.end_fraction * (xs.length - 1)));
    const points = [];
    for (let index = start; index <= end; index += 1) {
      points.push(`${index === start ? "M" : "L"}${tx(xs[index]).toFixed(2)},${ty(ys[index]).toFixed(2)}`);
    }
    const team = q.lap_delta.find((row) => row.driver === segment.driver)?.team || "";
    return `<path class="trackSegment" stroke="${driverColour(segment.driver, team)}" d="${points.join(" ")}"></path>`;
  }).join("");
  $("dominanceMap").innerHTML = (
    `<svg viewBox="0 0 ${width} ${height}" aria-hidden="true"><path class="trackBase" d="${basePath}"></path>${segments}</svg>`
  );
  $("dominanceLegend").innerHTML = Object.entries(dominance.driver_distance_shares)
    .sort((a, b) => b[1] - a[1])
    .map(([driver, share]) => {
      const team = q.lap_delta.find((row) => row.driver === driver)?.team || "";
      return `<span><i style="background:${driverColour(driver, team)}"></i>${escapeHtml(driver)} · ${(Number(share) * 100).toFixed(1)}%</span>`;
    }).join("");
  void trace;
}

function renderQualifyingBars(payload) {
  const q = payload.visual_replication.qualifying;
  const speeds = new Map(q.top_speeds.map((row) => [row.driver, row.top_speed_kph]));
  const maxGap = Math.max(...q.lap_delta.map((row) => Number(row.gap_to_fastest_s)), .001);
  $("qualiBars").innerHTML = q.lap_delta.map((row) => (
    `<div class="barRow"><span class="barLabel" style="color:${driverColour(row.driver, row.team)}">#${row.rank} ${escapeHtml(row.driver)}</span>` +
    `<span class="barTrack"><i class="barFill" style="width:${Math.max(2, Number(row.gap_to_fastest_s) / maxGap * 100)}%"></i></span>` +
    `<span class="barValue">${signed(row.gap_to_fastest_s, 3)} · ${number(speeds.get(row.driver), 0)}</span></div>`
  )).join("");
}

function renderControls(payload) {
  const q = payload.visual_replication.qualifying;
  $("controlRows").innerHTML = q.lap_sections.map((row) => (
    `<div class="controlRow"><strong style="color:${driverColour(row.driver, row.team)}">${escapeHtml(row.driver)}</strong>` +
    `<span class="controlStack" title="刹车 / 收油 / 部分油门 / 全油门">` +
    `<i class="brake" style="width:${row.braking_pct}%"></i>` +
    `<i class="lift" style="width:${row.lift_pct}%"></i>` +
    `<i class="partial" style="width:${row.partial_throttle_pct}%"></i>` +
    `<i class="full" style="width:${row.full_throttle_pct}%"></i></span>` +
    `<span class="barValue">${number(row.full_throttle_pct, 1)}% full</span></div>`
  )).join("");
  $("throttleTeamTable").innerHTML = (
    "<thead><tr><th>车队</th><th>车手</th><th>覆盖</th><th>最大油门样本均值</th></tr></thead><tbody>" +
    q.throttle_usage.map((row) => (
      `<tr><td><strong style="color:${teamColour(row.team)}">${escapeHtml(row.team)}</strong></td>` +
      `<td>${escapeHtml(row.drivers.join(" / "))}</td><td>${row.driver_count}</td>` +
      `<td>${number(row.max_throttle_sample_mean_pct, 2, "%")}</td></tr>`
    )).join("") + "</tbody>"
  );
}

function renderQualifyingTable(payload) {
  const rows = payload.visual_replication.qualifying.timings;
  $("qualiTable").innerHTML = (
    "<thead><tr><th>#</th><th>车手</th><th>阶段</th><th>圈</th><th>圈时</th><th>差距</th><th>S1</th><th>S2</th><th>S3</th><th>I1</th><th>I2</th><th>ST</th><th>配方</th></tr></thead><tbody>" +
    rows.map((row) => (
      `<tr><td>${row.rank}</td><td><strong style="color:${driverColour(row.driver, row.team)}">${escapeHtml(row.driver)}</strong></td>` +
      `<td><span class="tag ok">${escapeHtml(row.phase)}</span></td><td>${row.lap}</td>` +
      `<td>${number(row.lap_time_s, 3)}</td><td>${signed(row.gap_to_fastest_s, 3)}</td>` +
      `<td>${number(row.sector_1_s, 3)}</td><td>${number(row.sector_2_s, 3)}</td><td>${number(row.sector_3_s, 3)}</td>` +
      `<td>${number(row.i1_speed_kph, 0)}</td><td>${number(row.i2_speed_kph, 0)}</td><td>${number(row.speed_trap_kph, 0)}</td>` +
      `<td>${escapeHtml(row.compound)}</td></tr>`
    )).join("") + "</tbody>"
  );
}

function renderRacePace(payload) {
  const rows = payload.visual_replication.race.average_gap.rows;
  const profiles = new Map(
    payload.visual_replication.race.race_pace.driver_rows.map((row) => [row.driver, row]),
  );
  const maxGap = Math.max(...rows.map((row) => Number(row.gap_to_fastest_s)), .001);
  $("racePaceBars").innerHTML = rows.map((row, index) => {
    const profile = profiles.get(row.driver);
    return (
      `<div class="barRow"><span class="barLabel" style="color:${driverColour(row.driver, profile?.team)}">#${index + 1} ${escapeHtml(row.driver)}</span>` +
      `<span class="barTrack"><i class="barFill" style="width:${Math.max(2, Number(row.gap_to_fastest_s) / maxGap * 100)}%"></i></span>` +
      `<span class="barValue">${number(row.mean_lap_s, 3)} · ${signed(row.gap_to_fastest_s, 3)} · n=${row.valid_laps}</span></div>`
    );
  }).join("");
}

function renderStrategies(payload) {
  const race = payload.visual_replication.race;
  const rows = race.tyre_strategies.rows;
  const paceOrder = race.average_gap.rows.map((row) => row.driver);
  const groups = new Map();
  rows.forEach((row) => {
    if (!groups.has(row.driver)) groups.set(row.driver, []);
    groups.get(row.driver).push(row);
  });
  const maxLap = Math.max(...rows.map((row) => Number(row.lap_end)), 1);
  $("strategyChart").innerHTML = paceOrder.filter((driver) => groups.has(driver)).map((driver) => (
    `<div class="strategyRow"><strong style="color:${driverColour(driver, groups.get(driver)[0].team)}">${escapeHtml(driver)}</strong>` +
    `<div class="strategyLane">${groups.get(driver).map((stint) => {
      const left = (Number(stint.lap_start) - 1) / maxLap * 100;
      const width = (Number(stint.lap_end) - Number(stint.lap_start) + 1) / maxLap * 100;
      return `<i class="stint ${escapeHtml(stint.compound)}" style="left:${left}%;width:${width}%" title="${escapeHtml(stint.compound)} · L${stint.lap_start}–${stint.lap_end}">${stint.stint_number}</i>`;
    }).join("")}</div></div>`
  )).join("");
}

function renderPit(payload) {
  const rows = payload.visual_replication.race.pit_times.team_summary;
  const minimum = Math.min(...rows.map((row) => Number(row.lane_duration_mean_s)));
  const maximum = Math.max(...rows.map((row) => Number(row.lane_duration_mean_s)));
  $("pitBars").innerHTML = rows.map((row) => {
    const width = 18 + (Number(row.lane_duration_mean_s) - minimum) /
      Math.max(maximum - minimum, .001) * 82;
    return (
      `<div class="barRow"><span class="barLabel" style="color:${teamColour(row.team)}">${escapeHtml(row.team)}</span>` +
      `<span class="barTrack"><i class="barFill" style="width:${width}%"></i></span>` +
      `<span class="barValue">${number(row.lane_duration_mean_s, 2)}s</span></div>`
    );
  }).join("");
}

function renderRaceTimings(payload) {
  const rows = payload.visual_replication.race.timings;
  $("raceTimingTable").innerHTML = (
    "<thead><tr><th>#</th><th>车手</th><th>圈</th><th>圈时</th><th>S1</th><th>S2</th><th>S3</th><th>ST</th></tr></thead><tbody>" +
    rows.map((row) => (
      `<tr><td>${row.rank}</td><td><strong>${escapeHtml(row.driver)}</strong></td><td>${row.lap}</td>` +
      `<td>${number(row.lap_time_s, 3)}</td><td>${number(row.sector_1_s, 3)}</td>` +
      `<td>${number(row.sector_2_s, 3)}</td><td>${number(row.sector_3_s, 3)}</td>` +
      `<td>${number(row.speed_trap_kph, 0)}</td></tr>`
    )).join("") + "</tbody>"
  );
}

function renderLapTimes(payload) {
  const rows = payload.visual_replication.race.lap_times.rows;
  const width = 1200;
  const height = 340;
  const left = 58;
  const right = 20;
  const top = 25;
  const bottom = 38;
  const allPoints = rows.flatMap((row) => row.points);
  const laps = allPoints.map((row) => Number(row.lap));
  const times = allPoints.map((row) => Number(row.lap_time_s));
  const lapMin = Math.min(...laps);
  const lapMax = Math.max(...laps);
  const timeMin = Math.floor(Math.min(...times));
  const timeMax = Math.ceil(Math.max(...times));
  const x = (value) => left + (Number(value) - lapMin) / Math.max(lapMax - lapMin, 1) * (width - left - right);
  const y = (value) => top + (Number(value) - timeMin) / Math.max(timeMax - timeMin, 1) * (height - top - bottom);
  const xGrid = [10, 20, 30, 40, 50].filter((tick) => tick <= lapMax).map((tick) => (
    `<line class="gridLine" x1="${x(tick)}" x2="${x(tick)}" y1="${top}" y2="${height - bottom}"></line>` +
    `<text class="axisText" x="${x(tick)}" y="${height - 12}" text-anchor="middle">L${tick}</text>`
  )).join("");
  const yTicks = [];
  for (let tick = timeMin; tick <= timeMax; tick += 1) {
    yTicks.push(
      `<line class="gridLine" x1="${left}" x2="${width - right}" y1="${y(tick)}" y2="${y(tick)}"></line>` +
      `<text class="axisText" x="${left - 7}" y="${y(tick) + 4}" text-anchor="end">${tick}</text>`,
    );
  }
  const lines = rows.map((row) => {
    const profile = payload.visual_replication.race.race_pace.driver_rows.find((item) => item.driver === row.driver);
    const path = row.points.map((point, index) => (
      `${index ? "L" : "M"}${x(point.lap).toFixed(2)},${y(point.lap_time_s).toFixed(2)}`
    )).join(" ");
    return `<path class="chartLine" stroke="${driverColour(row.driver, profile?.team)}" d="${path}"></path>`;
  }).join("");
  const legend = rows.map((row, index) => {
    const profile = payload.visual_replication.race.race_pace.driver_rows.find((item) => item.driver === row.driver);
    return `<g transform="translate(${left + index * 96},18)"><line x2="20" stroke="${driverColour(row.driver, profile?.team)}" stroke-width="3"></line><text class="axisText" x="25" y="4">${escapeHtml(row.driver)}</text></g>`;
  }).join("");
  $("lapTimesChart").innerHTML = `<svg viewBox="0 0 ${width} ${height}" aria-hidden="true">${xGrid}${yTicks.join("")}${lines}${legend}</svg>`;
}

function differenceCard(label, value, detail, warn = false) {
  return (
    `<article class="differenceCard ${warn ? "warn" : ""}"><div class="label">${escapeHtml(label)}</div>` +
    `<div class="value">${escapeHtml(value)}</div><div class="detail">${escapeHtml(detail)}</div></article>`
  );
}

function renderDifferences(payload) {
  const q = payload.validation.qualifying;
  const race = payload.validation.race;
  $("differenceGrid").innerHTML = [
    differenceCard("Quali Lap Delta", number(q.lap_delta.mae, 6, "s"), `max ${number(q.lap_delta.max_abs_error, 6, "s")} · 20/20`),
    differenceCard("Quali Top Speeds", number(q.top_speed.mae, 3, " km/h"), `max ${number(q.top_speed.max_abs_error, 3)} · 20/20`),
    differenceCard("Top 3 Sector Times", number(q.top3_sector_mae_s, 6, "s"), `max ${number(q.top3_sector_max_abs_error_s, 6, "s")}`),
    differenceCard("Lap Sections", number(q.lap_sections.mae_pp, 3, "pp"), `max ${number(q.lap_sections.max_abs_error_pp, 3, "pp")}`, true),
    differenceCard("Team Throttle", number(q.team_throttle.mae, 3, "pp"), `max ${number(q.team_throttle.max_abs_error, 3, "pp")}`, true),
    differenceCard("Race Average Gap", number(race.average_gap.mae, 6, "s"), `max ${number(race.average_gap.max_abs_error, 6, "s")}`),
    differenceCard("Race Fastest Laps", number(race.fastest_laps.mae, 6, "s"), `max ${number(race.fastest_laps.max_abs_error, 6, "s")}`),
    differenceCard("Pit Team Mean", number(race.pit_team_means.mae, 3, "s"), `max ${number(race.pit_team_means.max_abs_error, 3, "s")}`, true),
    differenceCard("Strategy Boundaries", `${race.strategy_boundaries.local_matches}/${race.strategy_boundaries.openf1_rows}`, `公开 Top 5：${race.strategy_boundaries.top5_reference_matches}/${race.strategy_boundaries.top5_reference_expected}`),
  ].join("");
}

function renderPhaseAudit(payload) {
  const phase = payload.audited_analysis.qualifying.phase_coverage;
  const sample = payload.validation.sample_frequency_boundary;
  const cards = [
    ["Q1", phase.counts.Q1, "race_control 时间窗"],
    ["Q2", phase.counts.Q2, "race_control 时间窗"],
    ["Q3", phase.counts.Q3, "race_control 时间窗"],
    ["阶段覆盖", `${phase.identified}/${phase.frozen_push_laps}`, phase.status],
    ["原始 car channel", `≈ ${sample.openf1_raw_car_channel_documented_approx_hz} Hz`, "控制分段复刻层"],
    ["仓库 expanded-v4", `≈ ${sample.repository_expanded_v4_approx_hz} Hz`, "审计与真实赛道逐点层"],
    ["几何 sector anchor", "NOT_TESTED", GEOMETRY_STATUS],
    ["精确创作者代码", "OPAQUE", OPAQUE_STATUS],
  ];
  $("phaseAudit").innerHTML = cards.map(([label, value, detail]) => (
    `<article class="ledgerItem"><div class="label">${escapeHtml(label)}</div><div class="value">${escapeHtml(String(value))}</div><div class="detail">${escapeHtml(detail)}</div></article>`
  )).join("");
}

function renderRaceAudit(payload) {
  const rows = payload.audited_analysis.race.driver_profiles;
  $("raceAuditTable").innerHTML = (
    "<thead><tr><th>车手</th><th>全局均值</th><th>有效圈</th><th>停站</th><th>Stint</th><th>配方</th><th>胎龄范围</th><th>交通时间</th><th>结论</th></tr></thead><tbody>" +
    rows.map((row) => (
      `<tr><td><strong>${escapeHtml(row.driver)}</strong></td><td>${number(row.global_mean_s, 3)}</td>` +
      `<td>${row.valid_laps}</td><td>${row.stops}</td><td>${row.stints}</td><td>${escapeHtml(row.compounds.join(" / "))}</td>` +
      `<td>${number(row.tyre_age_range[0], 0)}–${number(row.tyre_age_range[1], 0)}</td>` +
      `<td>${number(Number(row.traffic_time_ratio) * 100, 1, "%")}</td>` +
      `<td><span class="tag warn">${escapeHtml(row.causal_rank_status)}</span></td></tr>`
    )).join("") + "</tbody>"
  );
}

function renderLedger(payload) {
  const ledger = payload.exclusion_ledger;
  const pit = payload.audited_analysis.pit;
  const cards = [
    ["Race 圈宇宙", ledger.race_lap_universe, "OpenF1 timing laps"],
    ["Race pace 有效圈", ledger.race_pace_eligible, "真实有效选圈"],
    ["首圈排除", ledger.race_first_lap_excluded, "20 位车手"],
    ["Pit 边界排除", ledger.race_pit_boundary_excluded, "进站 / 出站圈"],
    ["非绿/黄旗排除", ledger.race_non_green_yellow_excluded, "本场为 0"],
    ["删除标记保留", ledger.race_deleted_markers_retained, "公开口径保留"],
    ["Quali 准确推圈", ledger.qualifying_push_laps, "真实冻结集"],
    ["Phase 已识别", ledger.qualifying_phase_identified, "外部冻结身份"],
    ["Pit lane_duration", pit.lane_duration_coverage, "维修区通行时间"],
    ["stationary stop", pit.stationary_stop_coverage, "缺失不插补"],
  ];
  $("ledgerGrid").innerHTML = cards.map(([label, value, detail]) => (
    `<article class="ledgerItem"><div class="label">${escapeHtml(label)}</div><div class="value">${escapeHtml(String(value))}</div><div class="detail">${escapeHtml(detail)}</div></article>`
  )).join("");
}

function renderDataGap(payload) {
  $("dataGapTable").innerHTML = (
    "<thead><tr><th>目标图</th><th>所需字段</th><th>仓库字段</th><th>缺口</th><th>冻结补充 / 代理</th><th>发布规则</th></tr></thead><tbody>" +
    payload.data_gap_audit.map((row) => {
      const allowed = String(row.publish).startsWith("ALLOW");
      return (
        `<tr><td><strong>${escapeHtml(row.chart)}</strong></td><td>${escapeHtml(row.required)}</td>` +
        `<td>${escapeHtml(row.repository)}</td><td>${escapeHtml(row.gap)}</td><td>${escapeHtml(row.supplement)}</td>` +
        `<td><span class="tag ${allowed ? "ok" : "warn"}">${escapeHtml(row.publish)}</span></td></tr>`
      );
    }).join("") + "</tbody>"
  );
}

function setView(view) {
  state.view = view;
  const visual = view === VIEW_VISUAL;
  $("visualPanel").hidden = !visual;
  $("auditPanel").hidden = visual;
  $("visualTab").setAttribute("aria-selected", String(visual));
  $("auditTab").setAttribute("aria-selected", String(!visual));
}

function render(payload, manifest) {
  if (payload.status !== TARGET_STATUS) {
    throw new Error(`目标状态不符：${payload.status}`);
  }
  state.payload = payload;
  state.manifest = manifest;
  renderMetrics(payload);
  renderMethod(payload);
  renderLapCompare(payload);
  renderDominance(payload);
  renderQualifyingBars(payload);
  renderControls(payload);
  renderQualifyingTable(payload);
  renderRacePace(payload);
  renderStrategies(payload);
  renderPit(payload);
  renderRaceTimings(payload);
  renderLapTimes(payload);
  renderDifferences(payload);
  renderPhaseAudit(payload);
  renderRaceAudit(payload);
  renderLedger(payload);
  renderDataGap(payload);
  $("heroStatus").textContent = `${payload.status} · validation=${payload.validation.status}`;
  $("phasePill").textContent = `${payload.audited_analysis.qualifying.phase_coverage.identified} / ${payload.audited_analysis.qualifying.phase_coverage.frozen_push_laps} PHASED`;
  $("identityFooter").textContent = `run=${manifest.run_id} · report SHA-256 ${manifest.report.sha256.slice(0, 20)}…`;
  $("loadStatus").textContent = "已验收";
  window.__F1TR_F1TELEMETRYDATA_V1_READY__ = {
    status: payload.status,
    validation: payload.validation.status,
    runId: manifest.run_id,
    reportSha256: manifest.report.sha256,
    chartCount: payload.reference_identity.chart_count,
    qualifyingPhaseCoverage: payload.audited_analysis.qualifying.phase_coverage.identified,
    visualReplication: VIEW_VISUAL,
    auditedAnalysis: VIEW_AUDIT,
    opaqueStatus: OPAQUE_STATUS,
    geometricAnchorStatus: GEOMETRY_STATUS,
    nonIdentifiable: NON_IDENTIFIABLE,
  };
}

async function boot() {
  const index = await fetchJsonWithHash(INDEX_PATH);
  const target = index.targets.find((row) => row.target_id === TARGET_ID);
  if (!target) throw new Error(`实验索引缺少 ${TARGET_ID}`);
  const manifest = await fetchJsonWithHash(target.manifest);
  const payload = await fetchJsonWithHash(manifest.report.path, manifest.report.sha256);
  render(payload, manifest);
}

$("visualTab").addEventListener("click", () => setView(VIEW_VISUAL));
$("auditTab").addEventListener("click", () => setView(VIEW_AUDIT));

boot().catch((error) => {
  console.error(error);
  $("loadStatus").textContent = "加载失败";
  $("heroStatus").textContent = error.message;
  document.body.dataset.runtimeError = "true";
});
