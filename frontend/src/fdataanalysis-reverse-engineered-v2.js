import { resolveOfficialTeamIdentity } from "./official-team-colours.js";

const MANIFEST_PATH = "data/reference-analysis-lab/v2/manifest.json";
const TARGET_ID = "fdataanalysis-reverse-engineered-v2";
const TARGET_STATUS = "METHOD_EQUIVALENT_INTERNALLY_VALIDATED";
const REFERENCE_NUMERIC_STATUS = "NOT_COMPARABLE_DIFFERENT_EVENT_AND_SESSION";
const state = { payload: null, manifest: null, view: "visual", driver: "VER" };
const $ = (id) => document.getElementById(id);

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
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function number(value, digits = 2, suffix = "") {
  const resolved = finite(value);
  return resolved === null ? "—" : `${resolved.toFixed(digits)}${suffix}`;
}

function signed(value, digits = 3, suffix = "") {
  const resolved = finite(value);
  if (resolved === null) return "—";
  return `${resolved > 0 ? "+" : ""}${resolved.toFixed(digits)}${suffix}`;
}

function percent(value, digits = 1) {
  const resolved = finite(value);
  return resolved === null ? "—" : `${(resolved * 100).toFixed(digits)}%`;
}

function teamColor(team) {
  const identity = resolveOfficialTeamIdentity(2025, team);
  return identity?.status === "official" ? identity.colour : null;
}

async function sha256Hex(buffer) {
  const digest = await crypto.subtle.digest("SHA-256", buffer);
  return [...new Uint8Array(digest)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

async function fetchJsonWithHash(path, expectedHash) {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) throw new Error(`读取实验产物失败：${response.status} ${path}`);
  const buffer = await response.arrayBuffer();
  const actualHash = await sha256Hex(buffer);
  if (expectedHash && actualHash !== expectedHash) {
    throw new Error(`实验产物 SHA-256 不匹配：${path}`);
  }
  return JSON.parse(new TextDecoder().decode(buffer));
}

function setList(id, values) {
  $(id).innerHTML = (values || [])
    .map((value) => `<li>${escapeHtml(value)}</li>`)
    .join("");
}

function formulaCard(label, value, detail, tone = "") {
  return `<article class="formulaCard ${tone}"><div class="label">${escapeHtml(label)}</div><div class="value">${escapeHtml(value)}</div><div class="detail">${escapeHtml(detail)}</div></article>`;
}

function renderMetrics(payload) {
  const validation = payload.validation;
  const reconstruction = validation.lap_time_reconstruction;
  const matching = validation.teammate_matching;
  const metrics = [
    ["真实逐点样本", validation.raw_points.toLocaleString("en-US"), `${validation.axis_interpolated_laps} 个真实有效圈`],
    ["统一距离轴", validation.axis_intervals, "连续通道线性 · 离散通道最近邻"],
    ["动态弯角代理", validation.corner_proxy_count, "三档网格都匹配 16/16"],
    ["圈时重构 MAE", `${reconstruction.mae_s.toFixed(3)}s`, `P90 ${reconstruction.p90_abs_error_s.toFixed(3)}s`],
    ["top15 ↔ P99", `${validation.top_speed_formula_gap.driver_mae_faithful_top15_vs_audited_p99_kph.toFixed(1)} km/h`, "同一数据，不同统计公式"],
    ["可比同队", `${matching.comparable_pairs}/${matching.teams}`, `${matching.matched_lap_pairs} 个条件匹配圈对`],
  ];
  $("metricGrid").innerHTML = metrics
    .map(([label, value, detail]) => `<article class="metricCard"><div class="metricLabel">${escapeHtml(label)}</div><div class="metricValue">${escapeHtml(String(value))}</div><div class="metricDetail">${escapeHtml(detail)}</div></article>`)
    .join("");
}

function linePath(rows, xScale, yScale, field) {
  return rows
    .map((row, index) => `${index ? "L" : "M"}${xScale(row.rel_distance).toFixed(2)},${yScale(row[field]).toFixed(2)}`)
    .join(" ");
}

function renderTrack(payload) {
  const rows = payload.visual_replication.track_profile || [];
  const corners = payload.visual_replication.track_segments.corner_proxies || [];
  const width = 1200;
  const height = 330;
  const left = 48;
  const right = 18;
  const top = 30;
  const bottom = 42;
  const plotWidth = width - left - right;
  const plotHeight = height - top - bottom;
  const speedMin = 60;
  const speedMax = 330;
  const x = (value) => left + Number(value) * plotWidth;
  const ySpeed = (value) => top + (speedMax - Number(value)) / (speedMax - speedMin) * (plotHeight - 42);
  const yThrottle = (value) => height - bottom - Number(value) / 100 * 44;
  const bands = corners.map((corner) => {
    const start = x(corner.entry_rel_distance);
    const end = x(corner.exit_rel_distance);
    return `<rect class="trackBand ${escapeHtml(corner.speed_band.replace("_speed", ""))}" x="${start.toFixed(2)}" y="${top}" width="${Math.max(end - start, 2).toFixed(2)}" height="${plotHeight}"></rect>`;
  }).join("");
  const labels = corners.map((corner) => {
    const peak = x(corner.peak_lateral_rel_distance);
    return `<line class="peakLine" x1="${peak.toFixed(2)}" x2="${peak.toFixed(2)}" y1="${top}" y2="${height - bottom}"></line><text class="cornerLabel" x="${peak.toFixed(2)}" y="20">${escapeHtml(corner.corner_id)}</text>`;
  }).join("");
  const yTicks = [100, 150, 200, 250, 300]
    .map((tick) => `<text class="chartLabel" x="8" y="${(ySpeed(tick) + 3).toFixed(2)}">${tick}</text>`)
    .join("");
  const xTicks = [0, .2, .4, .6, .8, 1]
    .map((tick) => `<text class="chartLabel" x="${x(tick).toFixed(2)}" y="${height - 10}" text-anchor="middle">${Math.round(tick * 100)}%</text>`)
    .join("");
  $("trackChart").innerHTML = `<svg viewBox="0 0 ${width} ${height}" aria-hidden="true">${bands}${labels}${yTicks}${xTicks}<path class="speedPath" d="${linePath(rows, x, ySpeed, "speed_kph")}"></path><path class="throttlePath" d="${linePath(rows, x, yThrottle, "throttle_pct")}"></path></svg>`;
  $("trackLegend").innerHTML = [
    ["速度中位数", ""],
    ["油门中位数", "throttle"],
    ["动态代理窗口", "proxy"],
  ].map(([label, kind]) => `<span class="legendItem"><i class="legendSwatch ${kind}"></i>${escapeHtml(label)}</span>`).join("");
  $("segmentTable").innerHTML = `<thead><tr><th>代理</th><th>速度带</th><th>入口→峰→出口</th><th>最低速度</th><th>横向峰值</th><th>制动证据</th><th>最低点状态</th></tr></thead><tbody>${corners.map((row) => `<tr><td><strong>${escapeHtml(row.corner_id)}</strong></td><td><span class="tag">${escapeHtml(row.speed_band)}</span></td><td>${number(row.entry_rel_distance, 3)} → ${number(row.peak_lateral_rel_distance, 3)} → ${number(row.exit_rel_distance, 3)}</td><td>${number(row.global_minimum_speed_kph, 1, " km/h")}</td><td>${number(row.lateral_accel_peak_abs_mps2, 1, " m/s²")}</td><td>${percent(row.global_brake_prevalence_max, 0)}</td><td><span class="tag ${row.minimum_location_status === "INTERIOR" ? "ok" : "warn"}">${escapeHtml(row.minimum_location_status)}</span></td></tr>`).join("")}</tbody>`;
}

function driverProfile(payload, driver) {
  return (payload.visual_replication.driver_profiles || [])
    .find((row) => row.driver === driver);
}

function renderDriver(payload) {
  const profile = driverProfile(payload, state.driver)
    || payload.visual_replication.driver_profiles[0];
  if (!profile) return;
  state.driver = profile.driver;
  $("driverSelect").value = profile.driver;
  const top = profile.top_speed;
  const controls = profile.control_usage;
  const intervals = profile.speed_intervals;
  const rawTyre = profile.visual_tyre_age_change;
  $("driverFormulaCards").innerHTML = [
    formulaCard("公开公式 · 最高 15 样本", number(top.faithful_top15_sample_mean_kph, 2, " km/h"), `max ${number(top.faithful_max_sample_kph, 1, " km/h")} · 15 个逐点样本`),
    formulaCard("审计伴随 · 逐圈 P99 中位数", number(top.audited_median_lap_p99_kph, 2, " km/h"), `top15 比它高 ${signed(top.faithful_minus_audited_kph, 2, " km/h")}`, "warn"),
    formulaCard("全油门 · 样本 / 时间", `${percent(controls.faithful_sample_full_throttle_share)} / ${percent(controls.audited_time_full_throttle_share)}`, `差 ${signed(controls.sample_minus_time_full_throttle_pp, 2, "pp")}`),
    formulaCard("150–250 km/h 加速斜率", number(intervals.straight_acceleration_150_250_kph_per_100m, 2, " km/h/100m"), "观测加速代理，不命名引擎功率"),
    formulaCard("低速弯最低速度", number(intervals.corner_minimum_speed_by_band_kph.low_speed, 1, " km/h"), "P01…P16 距离代理分组"),
    formulaCard("中速弯最低速度", number(intervals.corner_minimum_speed_by_band_kph.medium_speed, 1, " km/h"), "固定 140–220 km/h 分带"),
    formulaCard("高速弯最低速度", number(intervals.corner_minimum_speed_by_band_kph.high_speed, 1, " km/h"), "输出速度，不命名真实下压力"),
    formulaCard("原始圈时 / 胎龄斜率", signed(rawTyre.raw_lap_time_s_per_tyre_lap, 3, " s/圈"), `${rawTyre.tyre_age_min_laps}–${rawTyre.tyre_age_max_laps} 胎龄圈 · 未分离燃油`),
  ].join("");
  $("driverCornerTable").innerHTML = `<thead><tr><th>代理</th><th>速度带</th><th>入口</th><th>最低</th><th>出口</th><th>刹车→最低</th><th>最低→全油门</th><th>有效圈</th></tr></thead><tbody>${profile.corner_features.map((row) => `<tr><td><strong>${escapeHtml(row.corner_id)}</strong></td><td>${escapeHtml(row.speed_band)}</td><td>${number(row.entry_speed_kph, 1, " km/h")}</td><td>${number(row.minimum_speed_kph, 1, " km/h")}</td><td>${number(row.exit_speed_kph, 1, " km/h")}</td><td>${number(row.brake_to_minimum_m, 1, " m")}</td><td>${number(row.minimum_to_throttle_recovery_m, 1, " m")}</td><td>${row.valid_laps}</td></tr>`).join("")}</tbody>`;
}

function renderTeamRanks(payload) {
  const rows = payload.visual_replication.single_feature_team_ranks || [];
  const metrics = [...new Map(rows.map((row) => [row.metric, row.label])).entries()];
  const teams = (payload.visual_replication.team_profiles || []).map((row) => row.team).sort();
  const lookup = new Map(rows.map((row) => [`${row.team}|${row.metric}`, row]));
  $("teamRankTable").innerHTML = `<thead><tr><th>车队</th>${metrics.map(([, label]) => `<th>${escapeHtml(label)}</th>`).join("")}</tr></thead><tbody>${teams.map((team) => `<tr><td><strong style="${teamColor(team) ? `color:${teamColor(team)}` : ""}">${escapeHtml(team)}</strong></td>${metrics.map(([metric]) => { const row = lookup.get(`${team}|${metric}`); return row ? `<td class="rankCell"><strong>#${row.rank}/${row.field_size}</strong><small>${number(row.value, metric.includes("share") ? 3 : 2)}</small></td>` : "<td>—</td>"; }).join("")}</tr>`).join("")}</tbody>`;
}

function renderVisualTyre(payload) {
  const rows = [...payload.visual_replication.driver_profiles]
    .sort((a, b) => (finite(a.visual_tyre_age_change.raw_lap_time_s_per_tyre_lap) ?? 999) - (finite(b.visual_tyre_age_change.raw_lap_time_s_per_tyre_lap) ?? 999));
  $("visualTyreTable").innerHTML = `<thead><tr><th>车手</th><th>车队</th><th>有效圈</th><th>胎龄支持</th><th>原始圈时斜率</th><th>原始中位速度斜率</th><th>解释</th></tr></thead><tbody>${rows.map((row) => { const tyre = row.visual_tyre_age_change; return `<tr><td><strong>${escapeHtml(row.driver)}</strong></td><td>${escapeHtml(row.team)}</td><td>${row.valid_laps}</td><td>${number(tyre.tyre_age_min_laps, 0)}–${number(tyre.tyre_age_max_laps, 0)}</td><td>${signed(tyre.raw_lap_time_s_per_tyre_lap, 3, " s/胎龄圈")}</td><td>${signed(tyre.raw_median_speed_kph_per_tyre_lap, 3, " km/h/胎龄圈")}</td><td><span class="tag warn">raw observed</span></td></tr>`; }).join("")}</tbody>`;
}

function renderReferenceGap(payload) {
  const comparison = payload.same_event_legacy_comparison;
  const structural = comparison.structural_gap;
  const validation = payload.validation;
  $("referenceGapCards").innerHTML = [
    formulaCard("距离分辨率", `${structural.axis_bins_v1} → ${structural.axis_intervals_v2}`, `${structural.distance_resolution_multiplier.toFixed(1)}× · v1 粗分箱到统一插值轴`),
    formulaCard("弯角代理", `${structural.corner_proxies_v1} → ${structural.corner_proxies_v2}`, `+${structural.corner_proxy_count_delta} 个高速/复合弯方向变化峰`),
    formulaCard("固定窗口语义错误", `${structural.v1_exit_below_reported_minimum_rows} → ${structural.v2_exit_below_reported_minimum_rows}`, "出口低于“最低速度”的行数"),
    formulaCard("top15 ↔ P99 公式差", `${validation.top_speed_formula_gap.driver_mae_faithful_top15_vs_audited_p99_kph.toFixed(2)} km/h`, `最大 ${validation.top_speed_formula_gap.driver_max_abs_gap_kph.toFixed(2)} km/h`, "warn"),
  ].join("");
  const gap = comparison.reference_chart_gap;
  $("referenceGapNote").innerHTML = `<strong>${escapeHtml(gap.status)}：</strong>${escapeHtml(gap.why)} 当前只验证公式与语义；原作者精确选圈、平滑核和车辆总评分仍为 <code>SKIPPED_OPAQUE_METHOD</code>。`;
}

function renderTeammateAudit(payload) {
  const rows = payload.audited_analysis.condition_matched_teammates || [];
  $("teammateAuditTable").innerHTML = `<thead><tr><th>车队</th><th>左 − 右</th><th>匹配圈</th><th>Kish ESS</th><th>配方</th><th>圈时差</th><th>95% CI</th><th>Top P99 差</th><th>全油门差</th><th>状态 / 门</th></tr></thead><tbody>${rows.map((row) => { const delta = row.left_minus_right; const ci = delta.lap_time_bootstrap_95_ci_s; return `<tr class="${row.audited_status === "COMPARABLE" ? "" : "auditOnlyRow"}"><td><strong>${escapeHtml(row.team)}</strong></td><td>${escapeHtml(row.left_driver)} − ${escapeHtml(row.right_driver)}</td><td>${row.matched_laps}</td><td>${number(row.kish_ess, 2)}</td><td>${escapeHtml((row.common_compounds || []).join(" / ") || "—")}</td><td>${signed(delta.lap_time_s, 3, "s")}</td><td>${ci ? `${signed(ci[0], 3)} … ${signed(ci[1], 3)}` : "—"}</td><td>${signed(delta.top_speed_p99_kph, 2, " km/h")}</td><td>${signed(delta.full_throttle_time_pp, 2, "pp")}</td><td><span class="tag ${row.audited_status === "COMPARABLE" ? "ok" : "warn"}">${escapeHtml(row.audited_status)}</span><br><small>${escapeHtml((row.gate_failures || []).join(" · "))}</small></td></tr>`; }).join("")}</tbody>`;
}

function renderTyreAudit(payload) {
  const rows = payload.audited_analysis.tyre_age_change || [];
  const scenario = (row, key) => row.fuel_scenarios?.[key]?.weighted_mean_s_per_tyre_lap;
  $("tyreAuditTable").innerHTML = `<thead><tr><th>车手</th><th>车队</th><th>支持 Stint</th><th>候选圈</th><th>ESS</th><th>public .032</th><th>v17 low</th><th>v17 base</th><th>v17 high</th><th>速度/胎龄</th><th>状态</th></tr></thead><tbody>${rows.map((row) => `<tr class="${row.status.startsWith("SUPPORTED") ? "" : "auditOnlyRow"}"><td><strong>${escapeHtml(row.driver)}</strong></td><td>${escapeHtml(row.team)}</td><td>${row.supported_stints}</td><td>${row.candidate_laps}</td><td>${number(row.aggregate_kish_ess, 1)}</td><td>${signed(scenario(row, "public_2026"), 3)}</td><td>${signed(scenario(row, "v17_low"), 3)}</td><td>${signed(scenario(row, "v17_base"), 3)}</td><td>${signed(scenario(row, "v17_high"), 3)}</td><td>${signed(row.median_speed_kph_per_tyre_lap, 3, " km/h")}</td><td><span class="tag ${row.status.startsWith("SUPPORTED") ? "ok" : "warn"}">${escapeHtml(row.status)}</span></td></tr>`).join("")}</tbody>`;
}

function renderValidation(payload) {
  const validation = payload.validation;
  const stability = validation.segmentation_stability;
  const matches = stability.runs.map((row) => `${row.axis_intervals}:${row.matched_to_baseline_within_1_5pct}/${stability.baseline_corner_count}`).join(" · ");
  $("validationGrid").innerHTML = [
    formulaCard("圈时积分", validation.lap_time_reconstruction.status, `MAE ${validation.lap_time_reconstruction.mae_s.toFixed(4)}s · P90 ${validation.lap_time_reconstruction.p90_abs_error_s.toFixed(4)}s`),
    formulaCard("分段稳定性", stability.status, matches),
    formulaCard("同队反对称性", validation.teammate_matching.status, `max |Δab+Δba| = ${validation.teammate_matching.max_antisymmetry_error}`),
    formulaCard("真实来源", "784,424 points", `synthetic=${validation.synthetic_points} · ${validation.axis_interpolated_laps} laps`),
  ].join("");
}

function renderDataGap(payload) {
  $("dataGapTable").innerHTML = `<thead><tr><th>目标字段</th><th>仓库字段</th><th>缺口</th><th>替代代理</th><th>发布规则</th></tr></thead><tbody>${payload.data_gap_audit.map((row) => `<tr><td><strong>${escapeHtml(row.required_field)}</strong></td><td>${escapeHtml(row.repository_field)}</td><td>${escapeHtml(row.gap)}</td><td>${escapeHtml(row.substitute)}</td><td><span class="tag ${row.publication.startsWith("ALLOW") ? "ok" : "warn"}">${escapeHtml(row.publication)}</span></td></tr>`).join("")}</tbody>`;
}

function renderAudit(payload) {
  $("auditReason").textContent = payload.audited_analysis.reason;
  renderTeammateAudit(payload);
  renderTyreAudit(payload);
  renderValidation(payload);
  setList("allowedClaimsList", payload.audited_analysis.allowed_claims);
  setList("forbiddenClaimsList", payload.audited_analysis.forbidden_claims);
}

function render(payload, manifest) {
  state.payload = payload;
  state.manifest = manifest;
  renderMetrics(payload);
  setList("publicMethodList", payload.method_card.publicly_observable_method);
  setList("reverseMethodList", payload.method_card.reverse_engineered_or_reimplemented);
  setList("notIdentifiableList", payload.method_card.not_identifiable);
  renderTrack(payload);
  const drivers = payload.visual_replication.driver_profiles.map((row) => row.driver);
  if (!drivers.includes(state.driver)) state.driver = drivers[0];
  $("driverSelect").innerHTML = payload.visual_replication.driver_profiles
    .map((row) => `<option value="${escapeHtml(row.driver)}">${escapeHtml(row.driver)} · ${escapeHtml(row.team)}</option>`)
    .join("");
  renderDriver(payload);
  renderTeamRanks(payload);
  renderVisualTyre(payload);
  renderReferenceGap(payload);
  renderAudit(payload);
  renderDataGap(payload);
  $("heroStatus").textContent = `${payload.status} · audited=${payload.audited_analysis.status}`;
  $("identityFooter").textContent = `run=${manifest.run_id} · report SHA-256 ${manifest.report.sha256.slice(0, 16)}… · telemetry ${payload.source_identity.telemetry.sha256.slice(0, 16)}…`;
  window.__F1TR_FDATA_V2_READY__ = {
    status: payload.status,
    runId: manifest.run_id,
    reportSha256: manifest.report.sha256,
    cornerProxyCount: payload.validation.corner_proxy_count,
    comparableTeammatePairs: payload.validation.teammate_matching.comparable_pairs,
  };
}

function setView(view) {
  state.view = view;
  const audit = view === "audit";
  $("visualPanel").hidden = audit;
  $("auditPanel").hidden = !audit;
  $("visualTab").setAttribute("aria-selected", String(!audit));
  $("auditTab").setAttribute("aria-selected", String(audit));
}

async function load() {
  try {
    const indexResponse = await fetch(MANIFEST_PATH, { cache: "no-store" });
    if (!indexResponse.ok) throw new Error(`读取 v2 实验索引失败：${indexResponse.status}`);
    const index = await indexResponse.json();
    const target = (index.targets || []).find((row) => row.target_id === TARGET_ID);
    if (!target?.manifest) throw new Error("v2 实验索引中没有 FDataAnalysis 目标");
    const targetResponse = await fetch(String(target.manifest).replaceAll("\\", "/"), { cache: "no-store" });
    if (!targetResponse.ok) throw new Error(`读取目标 manifest 失败：${targetResponse.status}`);
    const manifest = await targetResponse.json();
    const payload = await fetchJsonWithHash(String(manifest.report.path).replaceAll("\\", "/"), manifest.report.sha256);
    if (
      payload.target_id !== TARGET_ID
      || payload.status !== TARGET_STATUS
      || manifest.status !== TARGET_STATUS
      || payload.reference_identity?.numeric_comparison_status !== REFERENCE_NUMERIC_STATUS
    ) {
      throw new Error(`目标身份/状态不满足 ${TARGET_STATUS}`);
    }
    render(payload, manifest);
    $("loadStatus").textContent = "PASS · 哈希已校验";
    $("loadStatus").classList.add("ok");
  } catch (error) {
    console.error(error);
    $("loadStatus").textContent = "FAIL · 读取失败";
    $("loadStatus").classList.add("fail");
    $("visualPanel").innerHTML = `<article class="fdataPanel"><div class="callout"><strong>实验产物未加载：</strong>${escapeHtml(error.message)}<br>请运行 <code>npm run build</code> 与 <code>npm run dev</code> 后访问本地 HTTP URL。</div></article>`;
  }
}

$("visualTab").addEventListener("click", () => setView("visual"));
$("auditTab").addEventListener("click", () => setView("audit"));
$("driverSelect").addEventListener("change", (event) => {
  state.driver = event.target.value;
  if (state.payload) renderDriver(state.payload);
});
setView(new URLSearchParams(window.location.search).get("view") === "audit" ? "audit" : "visual");
load();
