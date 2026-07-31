import { resolveOfficialTeamIdentity } from "./official-team-colours.js";

const INDEX_PATH = "data/reference-analysis-lab/v2/manifest.json";
const TARGET_ID = "gptempo-reverse-engineered-v1";
const TARGET_STATUS = "PUBLIC_METHOD_REIMPLEMENTED_ENDPOINT_VALIDATED";
const COMPARABLE = "COMPARABLE_DEFAULT";
const CROSS_SESSION_WARNING = "WARNING_CROSS_SESSION_COMBINED_CONDITIONS";
const SHAPE_STATUS = "ESTIMATED_LINEAR_INTERPOLATION_APPROX_4HZ";
const ENDPOINT_STATUS = "PASS_EXACT_OFFICIAL_SECTOR_ENDPOINTS";
const VIEW_VISUAL = "visual_replication";
const VIEW_AUDIT = "audited_analysis";

const state = {
  payload: null,
  manifest: null,
  allLaps: [],
  lapMap: new Map(),
  comparisonMap: new Map(),
  selectedIds: [],
  reference: null,
  mode: "Qualifying",
  hoverFraction: 0.5,
  trackTransform: null,
};
const $ = (id) => document.getElementById(id);

const FALLBACK_COLOURS = ["#54a7ff", "#ff6da9", "#4ce5d1", "#ffc45f", "#bd8cff"];
const DRIVER_COLOURS = {
  VER: "#54a7ff",
  NOR: "#ff9b42",
  PIA: "#ff6da9",
  RUS: "#4ce5d1",
  LEC: "#ff6675",
  HUL: "#69d28b",
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

function colourForLap(lap, index = 0) {
  if (DRIVER_COLOURS[lap?.driver]) return DRIVER_COLOURS[lap.driver];
  const identity = resolveOfficialTeamIdentity(2025, lap?.team || "");
  return identity?.status === "official"
    ? identity.colour
    : FALLBACK_COLOURS[index % FALLBACK_COLOURS.length];
}

function safeId(value) {
  return String(value).replaceAll(/[^a-zA-Z0-9_-]/g, "_");
}

async function sha256Hex(buffer) {
  const digest = await crypto.subtle.digest("SHA-256", buffer);
  return [...new Uint8Array(digest)]
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

function interpolate(axis, values, target) {
  if (!axis?.length || !values?.length) return null;
  if (target <= axis[0]) return Number(values[0]);
  if (target >= axis[axis.length - 1]) return Number(values[values.length - 1]);
  let low = 0;
  let high = axis.length - 1;
  while (high - low > 1) {
    const middle = Math.floor((low + high) / 2);
    if (Number(axis[middle]) <= target) low = middle;
    else high = middle;
  }
  const span = Number(axis[high]) - Number(axis[low]);
  if (Math.abs(span) < 1e-12) return Number(values[low]);
  const ratio = (target - Number(axis[low])) / span;
  return Number(values[low]) + ratio * (Number(values[high]) - Number(values[low]));
}

function pathFrom(axis, values, xScale, yScale) {
  return values.map((value, index) => (
    `${index ? "L" : "M"}${xScale(Number(axis[index])).toFixed(2)},${yScale(Number(value)).toFixed(2)}`
  )).join(" ");
}

function flattenLaps(payload) {
  return Object.values(payload.visual_replication.sessions).flat();
}

function comparison(referenceId, candidateId) {
  return state.comparisonMap.get(`${referenceId}|${candidateId}`);
}

function renderMetrics(payload) {
  const validation = payload.validation;
  const metrics = [
    ["真实圈", `${validation.real_laps}`, `${validation.qualifying_laps} Q · ${validation.race_laps} Race`],
    ["原始样本", validation.raw_sample_count.toLocaleString("en-US"), "公开 car channel"],
    ["采样率中位", number(validation.sample_hz_median, 3, " Hz"), `${number(validation.sample_hz_min, 3)}–${number(validation.sample_hz_max, 3)} Hz`],
    ["有序圈对", `${validation.ordered_pair_comparisons}`, `${validation.cross_session_warning_pairs} 对跨 session`],
    ["Sector 端点", `${validation.sector_endpoint_checks}`, `max error ${number(validation.max_abs_sector_endpoint_error_s, 12, "s")}`],
    ["默认条件门", validation.default_selection_gate, "同 session / 配方 / 胎龄支持"],
  ];
  $("metricGrid").innerHTML = metrics.map(([label, value, detail]) => (
    `<article class="metricCard"><div class="metricLabel">${escapeHtml(label)}</div>` +
    `<div class="metricValue">${escapeHtml(String(value))}</div>` +
    `<div class="metricDetail">${escapeHtml(detail)}</div></article>`
  )).join("");
}

function lapLabel(lap) {
  return `${lap.session === "Qualifying" ? "Q" : "R"} · ${lap.driver} · L${lap.lap_number} · ${number(lap.lap_duration_s, 3)}s · ${lap.compound} T${number(lap.tyre_life, 0)}`;
}

function optionsForMode(mode) {
  if (mode === "Cross-session") return state.allLaps;
  return state.allLaps.filter((lap) => lap.session === mode);
}

function setSelectOptions(element, laps, selectedId, allowNone) {
  const options = [];
  if (allowNone) options.push('<option value="">— 不选择第三圈 —</option>');
  options.push(...laps.map((lap) => (
    `<option value="${escapeHtml(lap.lap_id)}">${escapeHtml(lapLabel(lap))}</option>`
  )));
  element.innerHTML = options.join("");
  element.value = selectedId || "";
}

function raceDefaultSelection() {
  const race = state.allLaps.filter((lap) => lap.session === "Race");
  for (const reference of race) {
    for (const candidate of race) {
      if (reference.lap_id === candidate.lap_id) continue;
      const pair = comparison(reference.lap_id, candidate.lap_id);
      if (pair?.condition?.status === COMPARABLE) {
        return [reference.lap_id, candidate.lap_id, ""];
      }
    }
  }
  return [race[0]?.lap_id || "", race[1]?.lap_id || "", ""];
}

function modeDefaults(mode) {
  if (mode === "Qualifying") {
    return [...state.payload.visual_replication.default_selection.lap_ids];
  }
  if (mode === "Race") return raceDefaultSelection();
  const qualifying = state.allLaps.find((lap) => lap.session === "Qualifying");
  const race = state.allLaps.find((lap) => lap.session === "Race");
  return [qualifying?.lap_id || "", race?.lap_id || "", ""];
}

function configureSelectors(reset = false) {
  const laps = optionsForMode(state.mode);
  const defaults = reset ? modeDefaults(state.mode) : [
    $("lapA").value,
    $("lapB").value,
    $("lapC").value,
  ];
  setSelectOptions($("lapA"), laps, defaults[0] || laps[0]?.lap_id, false);
  setSelectOptions($("lapB"), laps, defaults[1] || laps[1]?.lap_id, false);
  setSelectOptions($("lapC"), laps, defaults[2] || "", true);
  updateSelection();
}

function selectedLaps() {
  const ids = [$("lapA").value, $("lapB").value, $("lapC").value]
    .filter(Boolean);
  const unique = [...new Set(ids)];
  return unique.map((id) => state.lapMap.get(id)).filter(Boolean);
}

function selectedPairConditions(laps, reference) {
  return laps
    .filter((lap) => lap.lap_id !== reference.lap_id)
    .map((lap) => comparison(reference.lap_id, lap.lap_id)?.condition)
    .filter(Boolean);
}

function renderCondition(laps, reference) {
  const conditions = selectedPairConditions(laps, reference);
  const reasons = [...new Set(conditions.flatMap((item) => item.reasons || []))];
  const safe = conditions.length > 0 && conditions.every((item) => item.status === COMPARABLE);
  $("conditionBanner").classList.toggle("warning", !safe);
  if (safe) {
    $("conditionBanner").innerHTML = (
      "<strong>默认可比门通过：</strong>同 session、同配方、绿旗，胎龄差不超过 2 圈。" +
      " 结果仍是观测遥测比较，不识别燃油、SOC 或设定。"
    );
  } else {
    $("conditionBanner").innerHTML = (
      `<strong>条件警告：</strong>${escapeHtml(reasons.join(" · ") || "至少需要两条不同圈")}。` +
      " 当前 Delta 只能解释为混合条件描述，不能改名为纯车手或车辆差。"
    );
  }
  $("referencePill").textContent = `reference · ${reference.driver} ${number(reference.lap_duration_s, 3)}s`;
  $("referencePill").className = "pill ok";
}

function renderSelectedCards(laps, reference) {
  $("selectedLapCards").innerHTML = laps.map((lap, index) => {
    const isReference = lap.lap_id === reference.lap_id;
    const pair = isReference ? null : comparison(reference.lap_id, lap.lap_id);
    const status = isReference ? "REFERENCE" : pair?.condition?.status;
    return (
      `<article class="lapCard"><div class="lapHead">` +
      `<strong style="color:${colourForLap(lap, index)}">${escapeHtml(lap.driver)}</strong>` +
      `<span class="tag ${status === COMPARABLE || isReference ? "ok" : "warn"}">${escapeHtml(status)}</span></div>` +
      `<div class="lapMeta">${escapeHtml(lap.session)} · L${lap.lap_number}<br>` +
      `${number(lap.lap_duration_s, 3)}s · ${escapeHtml(lap.compound)} · tyre age ${number(lap.tyre_life, 0)}<br>` +
      `S1 ${number(lap.sector_times_s[0], 3)} · S2 ${number(lap.sector_times_s[1], 3)} · S3 ${number(lap.sector_times_s[2], 3)}<br>` +
      `${number(lap.approx_sample_hz, 3)} Hz · n=${lap.raw_sample_count}</div></article>`
    );
  }).join("");
}

function referenceSectorFractions(reference, laps) {
  const candidate = laps.find((lap) => lap.lap_id !== reference.lap_id);
  const pair = candidate ? comparison(reference.lap_id, candidate.lap_id) : null;
  return pair
    ? pair.sector_endpoints.slice(0, 2).map((row) => Number(row.reference_end_rel_distance))
    : [
      reference.sector_times_s[0] / reference.lap_duration_s,
      (reference.sector_times_s[0] + reference.sector_times_s[1]) / reference.lap_duration_s,
    ];
}

function renderTelemetry(laps, reference) {
  const width = 1200;
  const height = 680;
  const left = 58;
  const right = 20;
  const x = (value) => left + Number(value) * (width - left - right);
  const panels = {
    speed: { top: 26, bottom: 202, min: 40, max: 350, label: "SPEED km/h", key: "speed_kph" },
    throttle: { top: 224, bottom: 330, min: 0, max: 100, label: "THROTTLE %", key: "throttle_pct" },
    brake: { top: 352, bottom: 414, min: 0, max: 1, label: "BRAKE", key: "brake" },
    gear: { top: 436, bottom: 526, min: 0, max: 8, label: "GEAR", key: "gear" },
    drs: { top: 548, bottom: 618, min: 0, max: 12, label: "DRS", key: "drs" },
  };
  const y = (panel, value) => panel.bottom -
    (Number(value) - panel.min) / Math.max(panel.max - panel.min, 1e-9) *
    (panel.bottom - panel.top);
  const xGrid = [0, .2, .4, .6, .8, 1].map((tick) => (
    `<line class="gridLine" x1="${x(tick)}" x2="${x(tick)}" y1="20" y2="620"></line>` +
    `<text class="axisText" x="${x(tick)}" y="653" text-anchor="middle">${Math.round(tick * 100)}%</text>`
  )).join("");
  const panelGrid = Object.values(panels).map((panel) => (
    `<line class="gridLine" x1="${left}" x2="${width - right}" y1="${panel.bottom}" y2="${panel.bottom}"></line>` +
    `<text class="channelLabel" x="8" y="${panel.top + 12}">${panel.label}</text>`
  )).join("");
  const sectors = referenceSectorFractions(reference, laps).map((fraction, index) => (
    `<line class="sectorLine" x1="${x(fraction)}" x2="${x(fraction)}" y1="20" y2="620"></line>` +
    `<text class="axisText" x="${x(fraction) + 4}" y="34">S${index + 1}</text>`
  )).join("");
  const traces = laps.map((lap, lapIndex) => {
    const colour = colourForLap(lap, lapIndex);
    return Object.entries(panels).map(([name, panel]) => {
      const values = lap.trace[panel.key];
      return `<path class="${["brake", "gear", "drs"].includes(name) ? "discreteLine" : "traceLine"}" stroke="${colour}" opacity="${lap.lap_id === reference.lap_id ? 1 : .82}" d="${pathFrom(lap.trace.axis, values, x, (value) => y(panel, value))}"></path>`;
    }).join("");
  }).join("");
  const legend = laps.map((lap, index) => (
    `<g transform="translate(${left + index * 135},638)"><line x2="22" stroke="${colourForLap(lap, index)}" stroke-width="3"></line>` +
    `<text class="axisText" x="28" y="4">${escapeHtml(lap.driver)} ${lap.session === "Qualifying" ? "Q" : "R"}</text></g>`
  )).join("");
  $("telemetryChart").innerHTML = (
    `<svg viewBox="0 0 ${width} ${height}" aria-hidden="true">${xGrid}${panelGrid}${sectors}${traces}${legend}` +
    `<line id="telemetryCursor" class="cursorLine" x1="${x(state.hoverFraction)}" x2="${x(state.hoverFraction)}" y1="20" y2="620"></line>` +
    `<rect id="telemetryHit" class="cursorHit" x="${left}" y="20" width="${width - left - right}" height="600"></rect></svg>`
  );
  attachHover($("telemetryChart"), left / width, right / width);
}

function deltaForLap(reference, lap, fraction) {
  if (lap.lap_id === reference.lap_id) return 0;
  const pair = comparison(reference.lap_id, lap.lap_id);
  return pair ? interpolate(pair.axis, pair.delta_s, fraction) : null;
}

function renderDelta(laps, reference) {
  const width = 1200;
  const height = 330;
  const left = 58;
  const right = 20;
  const top = 28;
  const bottom = 48;
  const candidates = laps.filter((lap) => lap.lap_id !== reference.lap_id);
  const pairs = candidates.map((lap) => comparison(reference.lap_id, lap.lap_id)).filter(Boolean);
  const all = [0, ...pairs.flatMap((pair) => pair.delta_s.map(Number))];
  const min = Math.min(-.05, ...all);
  const max = Math.max(.05, ...all);
  const padding = Math.max((max - min) * .08, .03);
  const yMin = min - padding;
  const yMax = max + padding;
  const x = (value) => left + Number(value) * (width - left - right);
  const y = (value) => top + (yMax - Number(value)) /
    Math.max(yMax - yMin, .001) * (height - top - bottom);
  const xGrid = [0, .2, .4, .6, .8, 1].map((tick) => (
    `<line class="gridLine" x1="${x(tick)}" x2="${x(tick)}" y1="${top}" y2="${height - bottom}"></line>` +
    `<text class="axisText" x="${x(tick)}" y="${height - 16}" text-anchor="middle">${Math.round(tick * 100)}%</text>`
  )).join("");
  const sectors = referenceSectorFractions(reference, laps).map((fraction, index) => (
    `<line class="sectorLine" x1="${x(fraction)}" x2="${x(fraction)}" y1="${top}" y2="${height - bottom}"></line>` +
    `<text class="axisText" x="${x(fraction) + 4}" y="${top + 12}">S${index + 1} END</text>`
  )).join("");
  const lines = pairs.map((pair) => {
    const lap = state.lapMap.get(pair.candidate_lap_id);
    const index = laps.findIndex((item) => item.lap_id === lap.lap_id);
    return `<path class="traceLine" stroke="${colourForLap(lap, index)}" d="${pathFrom(pair.axis, pair.delta_s, x, y)}"></path>`;
  }).join("");
  $("deltaChart").innerHTML = (
    `<svg viewBox="0 0 ${width} ${height}" aria-hidden="true">${xGrid}${sectors}` +
    `<line class="zeroLine" x1="${left}" x2="${width - right}" y1="${y(0)}" y2="${y(0)}"></line>${lines}` +
    `<line id="deltaCursor" class="cursorLine" x1="${x(state.hoverFraction)}" x2="${x(state.hoverFraction)}" y1="${top}" y2="${height - bottom}"></line>` +
    `<rect id="deltaHit" class="cursorHit" x="${left}" y="${top}" width="${width - left - right}" height="${height - top - bottom}"></rect></svg>`
  );
  attachHover($("deltaChart"), left / width, right / width);
  $("endpointCards").innerHTML = pairs.flatMap((pair) => {
    const lap = state.lapMap.get(pair.candidate_lap_id);
    const index = laps.findIndex((item) => item.lap_id === lap.lap_id);
    return pair.sector_endpoints.map((row) => (
      `<article class="endpointCard"><span style="color:${colourForLap(lap, index)}">${escapeHtml(lap.driver)} · S${row.sector} END</span>` +
      `<strong>${signed(row.actual_cumulative_delta_s, 3, "s")}</strong>` +
      `<span>error ${number(row.endpoint_error_s, 12, "s")}</span></article>`
    ));
  }).join("");
}

function renderTrack(laps) {
  const geometry = state.payload.visual_replication.track_geometry;
  const xs = geometry.x.map(Number);
  const ys = geometry.y.map(Number);
  const width = 430;
  const height = 500;
  const pad = 24;
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const scale = Math.min(
    (width - pad * 2) / Math.max(maxX - minX, 1),
    (height - pad * 2) / Math.max(maxY - minY, 1),
  );
  const tx = (value) => pad + (value - minX) * scale;
  const ty = (value) => height - pad - (value - minY) * scale;
  state.trackTransform = { tx, ty, xs, ys };
  const path = xs.map((value, index) => (
    `${index ? "L" : "M"}${tx(value).toFixed(2)},${ty(ys[index]).toFixed(2)}`
  )).join(" ");
  const markerIndex = Math.min(xs.length - 1, Math.round(state.hoverFraction * (xs.length - 1)));
  const dots = laps.map((lap, index) => (
    `<circle id="trackDot-${safeId(lap.lap_id)}" class="trackDot" cx="${tx(xs[markerIndex]) + index * 2}" cy="${ty(ys[markerIndex]) - index * 2}" r="${lap.lap_id === state.reference.lap_id ? 7 : 5}" fill="${colourForLap(lap, index)}"></circle>`
  )).join("");
  $("trackMap").innerHTML = (
    `<svg viewBox="0 0 ${width} ${height}" aria-hidden="true"><path class="trackBase" d="${path}"></path>` +
    `<path class="trackRoute" d="${path}"></path>${dots}</svg>`
  );
}

function attachHover(container, leftFraction, rightFraction) {
  const update = (event) => {
    const rect = container.getBoundingClientRect();
    const x = Math.max(0, Math.min(rect.width, event.clientX - rect.left));
    const usableStart = leftFraction * rect.width;
    const usableEnd = rect.width - rightFraction * rect.width;
    const fraction = (x - usableStart) / Math.max(usableEnd - usableStart, 1);
    setHover(Math.max(0, Math.min(1, fraction)));
  };
  container.addEventListener("pointermove", update);
  container.addEventListener("pointerdown", update);
}

function setHover(fraction) {
  state.hoverFraction = fraction;
  const teleCursor = $("telemetryCursor");
  if (teleCursor) {
    const x = 58 + fraction * (1200 - 58 - 20);
    teleCursor.setAttribute("x1", x);
    teleCursor.setAttribute("x2", x);
  }
  const deltaCursor = $("deltaCursor");
  if (deltaCursor) {
    const x = 58 + fraction * (1200 - 58 - 20);
    deltaCursor.setAttribute("x1", x);
    deltaCursor.setAttribute("x2", x);
  }
  if (state.trackTransform) {
    const { tx, ty, xs, ys } = state.trackTransform;
    const index = Math.min(xs.length - 1, Math.round(fraction * (xs.length - 1)));
    state.selectedIds.forEach((id, lapIndex) => {
      const dot = $(`trackDot-${safeId(id)}`);
      if (!dot) return;
      dot.setAttribute("cx", tx(xs[index]) + lapIndex * 2);
      dot.setAttribute("cy", ty(ys[index]) - lapIndex * 2);
    });
  }
  renderHoverReadout();
}

function renderHoverReadout() {
  if (!state.reference) return;
  const laps = state.selectedIds.map((id) => state.lapMap.get(id)).filter(Boolean);
  const rows = laps.map((lap, index) => {
    const trace = lap.trace;
    const sampleIndex = Math.min(
      trace.axis.length - 1,
      Math.round(state.hoverFraction * (trace.axis.length - 1)),
    );
    const delta = deltaForLap(state.reference, lap, state.hoverFraction);
    return (
      `<span style="color:${colourForLap(lap, index)}">${escapeHtml(lap.driver)}</span> ` +
      `${number(trace.speed_kph[sampleIndex], 0)}km/h · T${number(trace.throttle_pct[sampleIndex], 0)}% · ` +
      `B${number(trace.brake[sampleIndex], 0)} · G${number(trace.gear[sampleIndex], 0)} · ` +
      `DRS ${Number(trace.drs[sampleIndex]) >= 10 ? "OPEN" : "closed"} · Δ ${signed(delta, 3, "s")}`
    );
  });
  $("hoverReadout").innerHTML = `<strong>${(state.hoverFraction * 100).toFixed(1)}% lap</strong> · ${rows.join("<br>")}`;
  $("trackReadout").textContent = `${(state.hoverFraction * 100).toFixed(1)}% · ${laps.map((lap) => lap.driver).join(" / ")}`;
}

function updateSelection() {
  const laps = selectedLaps();
  if (laps.length < 2) {
    $("conditionBanner").className = "conditionBanner warning";
    $("conditionBanner").textContent = "至少选择两条不同真实圈。";
    return;
  }
  const reference = [...laps].sort((a, b) => a.lap_duration_s - b.lap_duration_s)[0];
  state.selectedIds = laps.map((lap) => lap.lap_id);
  state.reference = reference;
  renderCondition(laps, reference);
  renderSelectedCards(laps, reference);
  renderTelemetry(laps, reference);
  renderDelta(laps, reference);
  renderTrack(laps);
  setHover(state.hoverFraction);
}

function validationCard(label, value, detail) {
  return `<article class="validationCard"><div class="label">${escapeHtml(label)}</div><div class="value">${escapeHtml(String(value))}</div><div class="detail">${escapeHtml(detail)}</div></article>`;
}

function renderAudit(payload) {
  const validation = payload.validation;
  $("validationGrid").innerHTML = [
    validationCard("Sector endpoints", validation.sector_endpoint_checks, `max error ${number(validation.max_abs_sector_endpoint_error_s, 12, "s")}`),
    validationCard("Finish endpoints", validation.finish_endpoint_checks, "全部等于官方总圈时差"),
    validationCard("Official sector sum", number(validation.max_official_sector_sum_error_s, 12, "s"), "三段和对圈时"),
    validationCard("Raw sample rate", number(validation.sample_hz_median, 3, " Hz"), `${number(validation.sample_hz_min, 3)}–${number(validation.sample_hz_max, 3)} Hz`),
    validationCard("Real / synthetic", `${validation.real_laps} / ${validation.synthetic_laps}`, "真实 Q/Race 圈"),
    validationCard("Default gate", validation.default_selection_gate, "同 session / compound / tyre support"),
    validationCard("Comparable ordered pairs", validation.comparable_default_pairs, `总计 ${validation.ordered_pair_comparisons}`),
    validationCard("Track geometry", validation.track_geometry_points, "共享相对距离点"),
  ].join("");

  $("comparisonTable").innerHTML = (
    "<thead><tr><th>参考</th><th>候选</th><th>终点期望</th><th>终点实际</th><th>终点误差</th><th>Sector max error</th><th>条件</th><th>原因</th></tr></thead><tbody>" +
    payload.visual_replication.comparisons.map((row) => (
      `<tr><td>${escapeHtml(row.reference_lap_id)}</td><td>${escapeHtml(row.candidate_lap_id)}</td>` +
      `<td>${signed(row.expected_finish_delta_s, 3)}</td><td>${signed(row.actual_finish_delta_s, 3)}</td>` +
      `<td>${number(row.finish_error_s, 12)}</td><td>${number(row.max_abs_sector_endpoint_error_s, 12)}</td>` +
      `<td><span class="tag ${row.condition.status === COMPARABLE ? "ok" : "warn"}">${escapeHtml(row.condition.status)}</span></td>` +
      `<td>${escapeHtml(row.condition.reasons.join(" · ") || "—")}</td></tr>`
    )).join("") + "</tbody>"
  );

  const source = payload.source_identity;
  const reference = payload.reference_identity;
  const sources = [
    ["GP Tempo About HTML", reference.about_html.sha256, `${reference.about_html.bytes} bytes · HTTP ${reference.about_html.status}`],
    ["GP Tempo method script", reference.about_script.sha256, `${reference.about_script.bytes} bytes · bundled public method`],
    ["X @f1_tempo_", reference.x_profile.sha256, `${reference.x_profile.bytes} bytes · HTTP ${reference.x_profile.status}`],
    ["Frozen source manifest", source.gptempo_source_manifest.sha256, source.gptempo_source_manifest.captured_at],
    ["Qualifying raw car data", source.raw_car_channel.qualifying.sha256, `${source.raw_car_channel.qualifying.rows} rows`],
    ["Race raw car data", source.raw_car_channel.race.sha256, `${source.raw_car_channel.race.rows} rows`],
    ["Local Qualifying geometry", source.primary_local.qualifying.sha256, `${source.primary_local.qualifying.rows.toLocaleString("en-US")} rows`],
    ["Local Race conditions", source.primary_local.race.sha256, `${source.primary_local.race.rows.toLocaleString("en-US")} rows`],
  ];
  $("sourceCards").innerHTML = sources.map(([label, hash, detail]) => (
    `<article class="sourceCard"><div class="label">${escapeHtml(label)}</div>` +
    `<span class="hash">${escapeHtml(hash.slice(0, 24))}…</span><div class="detail">${escapeHtml(detail)}</div></article>`
  )).join("");
}

function renderDataGap(payload) {
  $("dataGapTable").innerHTML = (
    "<thead><tr><th>目标字段</th><th>仓库字段</th><th>冻结补充 / 代理</th><th>发布边界</th></tr></thead><tbody>" +
    payload.data_gap_audit.map((row) => (
      `<tr><td><strong>${escapeHtml(row.required)}</strong></td><td>${escapeHtml(row.repository)}</td>` +
      `<td>${escapeHtml(row.supplement)}</td><td><span class="tag ${String(row.publish).startsWith("ALLOW") ? "ok" : "warn"}">${escapeHtml(row.publish)}</span></td></tr>`
    )).join("") + "</tbody>"
  );
}

function setView(view) {
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
  state.allLaps = flattenLaps(payload);
  state.lapMap = new Map(state.allLaps.map((lap) => [lap.lap_id, lap]));
  state.comparisonMap = new Map(
    payload.visual_replication.comparisons.map((row) => [
      `${row.reference_lap_id}|${row.candidate_lap_id}`,
      row,
    ]),
  );
  renderMetrics(payload);
  renderAudit(payload);
  renderDataGap(payload);
  $("sessionMode").value = state.mode;
  configureSelectors(true);
  $("heroStatus").textContent = `${payload.status} · validation=${payload.validation.status}`;
  $("identityFooter").textContent = `run=${manifest.run_id} · report SHA-256 ${manifest.report.sha256.slice(0, 20)}…`;
  $("loadStatus").textContent = "已验收";
  window.__F1TR_GPTEMPO_V1_READY__ = {
    status: payload.status,
    validation: payload.validation.status,
    runId: manifest.run_id,
    reportSha256: manifest.report.sha256,
    realLaps: payload.validation.real_laps,
    comparisons: payload.validation.ordered_pair_comparisons,
    endpointChecks: payload.validation.sector_endpoint_checks,
    maxEndpointError: payload.validation.max_abs_sector_endpoint_error_s,
    defaultSelectionGate: payload.validation.default_selection_gate,
    crossSessionWarning: CROSS_SESSION_WARNING,
    shapeStatus: SHAPE_STATUS,
    endpointStatus: ENDPOINT_STATUS,
    visualReplication: VIEW_VISUAL,
    auditedAnalysis: VIEW_AUDIT,
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

$("sessionMode").addEventListener("change", (event) => {
  state.mode = event.target.value;
  configureSelectors(true);
});
["lapA", "lapB", "lapC"].forEach((id) => {
  $(id).addEventListener("change", updateSelection);
});
$("visualTab").addEventListener("click", () => setView(VIEW_VISUAL));
$("auditTab").addEventListener("click", () => setView(VIEW_AUDIT));

boot().catch((error) => {
  console.error(error);
  $("loadStatus").textContent = "加载失败";
  $("heroStatus").textContent = error.message;
  document.body.dataset.runtimeError = "true";
});
