import { resolveOfficialTeamIdentity } from "./official-team-colours.js";

const MANIFEST_PATH = "data/reference-analysis-lab/v1/manifest.json";
const root = document;
const state = { payload: null, manifest: null, view: "visual", scope: "all" };

const $ = (id) => root.getElementById(id);

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function finite(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function formatSeconds(seconds, digits = 3) {
  const value = finite(seconds);
  if (value === null) return "—";
  const minutes = Math.floor(value / 60);
  const rest = (value - minutes * 60).toFixed(digits).padStart(digits + 3, "0");
  return `${minutes}:${rest}`;
}

function formatDelta(seconds) {
  const value = finite(seconds);
  if (value === null) return "—";
  if (Math.abs(value) < 0.0005) return "0.00s";
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}s`;
}

function teamColor(team) {
  const identity = resolveOfficialTeamIdentity(2025, team);
  return identity?.status === "official" ? identity.colour : "#8393a6";
}

async function sha256Hex(buffer) {
  const digest = await crypto.subtle.digest("SHA-256", buffer);
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
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
  $(id).innerHTML = (values || []).map((value) => `<li>${escapeHtml(value)}</li>`).join("");
}

function driverRowsForScope(pace) {
  if (state.scope === "top") return pace.top_10 || [];
  if (state.scope === "bottom") return pace.bottom_10 || [];
  return pace.all_drivers || [];
}

function trafficLookup(payload) {
  const map = new Map();
  for (const row of payload.visual_replication.traffic.lap_rows || []) {
    map.set(`${row.driver}|${row.lap}`, row);
  }
  return map;
}

function renderMetrics(payload) {
  const pace = payload.visual_replication.pace;
  const ledger = payload.exclusion_ledger;
  const validation = payload.reference_identity;
  const availableImages = (validation.images || []).filter((image) => image.status === "AVAILABLE").length;
  const metrics = [
    ["有效配速圈", ledger.pace_eligible, `圈宇宙 ${ledger.lap_universe} · 首圈/边界账本已保留`],
    ["逐点遥测样本", ledger.point_samples.race_total.toLocaleString("en-US"), "速度、前车距离与时间权重"],
    ["车手覆盖", pace.all_drivers.length, "全车手含回退车 · Top/Bottom/All"],
    ["公开均值最大差", `${state.manifest.validation.mean_abs_difference_max_s.toFixed(3)}s`, `${availableImages}/7 张参考图已冻结哈希`],
  ];
  $("metricGrid").innerHTML = metrics.map(([label, value, detail]) => `
    <article class="metricCard"><div class="metricLabel">${escapeHtml(label)}</div><div class="metricValue">${escapeHtml(value)}</div><div class="metricDetail">${escapeHtml(detail)}</div></article>
  `).join("");
}

function renderPace(payload) {
  const pace = payload.visual_replication.pace;
  const rows = driverRowsForScope(pace);
  const lookup = trafficLookup(payload);
  const allPoints = rows.flatMap((row) => row.points || []).map((point) => finite(point.lap_time_s)).filter((value) => value !== null);
  const domainMin = Math.min(...allPoints, 87);
  const domainMax = Math.max(...allPoints, 92);
  const spread = Math.max(domainMax - domainMin, 0.01);
  $("paceTitle").textContent = `Race pace · ${state.scope === "all" ? "All drivers" : state.scope === "top" ? "Fastest 10" : "Slowest 10"}`;
  $("paceIntro").textContent = `均值按公开圈口径从 ${pace.eligible_laps} 个有效圈计算；每个点是逐圈 lap time。横向只表达时间，Stint jitter 用轻微垂直偏移分离，绝不在 Stint 内编码第二个数值轴。`;
  const ticks = [domainMin, domainMin + spread * .25, domainMin + spread * .5, domainMin + spread * .75, domainMax];
  $("paceAxis").innerHTML = ticks.map((tick, index) => `<span style="left:${index * 25}%">${formatSeconds(tick, 2)}</span>`).join("");
  $("paceRows").innerHTML = rows.map((row) => {
    const color = teamColor(row.team);
    const left = (finite(row.q1_lap_s) - domainMin) / spread * 100;
    const right = (finite(row.q3_lap_s) - domainMin) / spread * 100;
    const mean = (finite(row.mean_lap_s) - domainMin) / spread * 100;
    const points = (row.points || []).map((point) => {
      const time = finite(point.lap_time_s);
      const x = (time - domainMin) / spread * 100;
      const traffic = lookup.get(`${row.driver}|${point.lap}`)?.traffic_lap;
      const jitterY = (finite(point.stint_jitter) || 0) * 12;
      return `<span class="pacePoint ${traffic ? "traffic" : "clean"} ${point.deleted_marker ? "deleted" : ""}" style="left:${x}%;--team-color:${color};--jitter-y:${jitterY}px" title="${escapeHtml(`${row.driver} · L${point.lap} · ${formatSeconds(time)} · ${traffic ? "交通" : "非交通"} · ${point.compound} · Stint ${point.stint}`)}"></span>`;
    }).join("");
    return `<div class="paceRow"><div class="driverIdentity"><i class="teamChip" style="background:${color}"></i><span><strong>${escapeHtml(row.driver)}</strong><small>${escapeHtml(row.team)} · ${row.valid_laps} 圈</small></span></div><div class="pacePlot"><span class="paceBand" style="left:${left}%;width:${Math.max(right - left, .5)}%"></span><span class="paceMean" style="left:${mean}%" title="均值 ${formatSeconds(row.mean_lap_s)}"></span>${points}</div><div class="paceMeta"><strong>${formatSeconds(row.mean_lap_s)}</strong>${row.stops} stops</div></div>`;
  }).join("") || `<div class="paceEmpty">没有符合当前筛选的车手。</div>`;
}

function renderSummary(payload) {
  const rows = driverRowsForScope(payload.visual_replication.pace);
  const allPoints = rows.flatMap((row) => row.points || []).map((point) => finite(point.lap_time_s)).filter((value) => value !== null);
  const min = Math.min(...allPoints, 87);
  const max = Math.max(...allPoints, 92);
  const spread = Math.max(max - min, .01);
  $("summaryRows").innerHTML = rows.map((row) => {
    const color = teamColor(row.team);
    const x = (finite(row.mean_lap_s) - min) / spread * 100;
    return `<div class="summaryRow"><div class="summaryIdentity"><i class="teamChip" style="background:${color}"></i><strong>${escapeHtml(row.driver)}</strong></div><div class="summaryTrack" style="--team-color:${color}"><span class="summaryDistribution"></span><span class="summaryMean" style="left:${x}%" title="${formatSeconds(row.mean_lap_s)}"></span></div><div class="summaryDelta">${formatSeconds(row.mean_lap_s)}<br>${formatDelta(row.delta_to_fastest_s)}</div></div>`;
  }).join("");
}

function trafficHeatColor(value) {
  const number = Math.max(0, Math.min(1, finite(value) || 0));
  return number;
}

function renderTraffic(payload) {
  const rows = payload.visual_replication.traffic.lap_rows || [];
  const drivers = payload.visual_replication.pace.all_drivers.map((row) => row.driver);
  const byKey = new Map(rows.map((row) => [`${row.driver}|${row.lap}`, row]));
  const maxLap = Math.max(...rows.map((row) => Number(row.lap)), 1);
  const head = Array.from({ length: maxLap }, (_, index) => `<th scope="col">${(index + 1) % 5 === 0 || index === 0 ? index + 1 : "·"}</th>`).join("");
  const body = drivers.map((driver) => {
    const cells = Array.from({ length: maxLap }, (_, index) => {
      const lap = index + 1;
      const row = byKey.get(`${driver}|${lap}`);
      const ratio = trafficHeatColor(row?.traffic_ratio);
      const label = row?.traffic_ratio == null ? "无有效时间" : row.traffic_ratio > .9 ? ">90%" : `${Math.round(row.traffic_ratio * 100)}%`;
      return `<td class="heatCell ${row?.traffic_lap ? "over" : ""}" style="--heat:${ratio}" title="${escapeHtml(`${driver} · L${lap} · ${label} · ${row?.telemetry_samples || 0} samples`)}">${escapeHtml(label)}</td>`;
    }).join("");
    return `<tr><th scope="row">${escapeHtml(driver)}</th>${cells}</tr>`;
  }).join("");
  $("trafficHeatmap").innerHTML = `<table class="heatmapTable"><thead><tr><th scope="col">车手 / 圈</th>${head}</tr></thead><tbody>${body}</tbody></table>`;
  const summaries = payload.visual_replication.traffic.summary || [];
  const top = [...summaries].sort((a, b) => b.weighted_time_in_traffic - a.weighted_time_in_traffic).slice(0, 10);
  $("trafficSummary").innerHTML = top.map((row) => `<div class="trafficCard"><strong>${escapeHtml(row.driver)} · ${(row.weighted_time_in_traffic * 100).toFixed(1)}%</strong><span>${row.traffic_laps_over_33pct}/${row.traffic_laps_observed} 圈超过 33% 阈值</span></div>`).join("");
}

function renderPairwise(payload) {
  const matrix = payload.visual_replication.pairwise_mean_delta;
  const order = matrix.driver_order;
  const byDriver = new Map(matrix.matrix.map((row) => [row.driver, row.values]));
  const header = order.map((driver) => `<th scope="col">${escapeHtml(driver)}</th>`).join("");
  const body = order.map((left) => {
    const cells = order.map((right) => {
      const delta = finite(byDriver.get(left)?.[right]) || 0;
      if (left === right) return `<td class="diag">—</td>`;
      const strength = Math.min(Math.abs(delta) / 2, 1).toFixed(3);
      return `<td class="${delta < 0 ? "deltaNeg" : "deltaPos"}" style="--delta-strength:${strength}" title="${escapeHtml(`${left} − ${right} = ${formatDelta(delta)}`)}">${escapeHtml(formatDelta(delta))}</td>`;
    }).join("");
    return `<tr><th scope="row">${escapeHtml(left)}</th>${cells}</tr>`;
  }).join("");
  $("pairMatrix").innerHTML = `<table class="pairTable"><thead><tr><th scope="col">左 \ 底</th>${header}</tr></thead><tbody>${body}</tbody></table>`;
}

function renderAudit(payload) {
  const audit = payload.audited_analysis;
  $("auditReason").textContent = audit.reason;
  $("auditProfiles").innerHTML = (audit.driver_profiles || []).map((row) => `<tr><td><strong>${escapeHtml(row.driver)}</strong></td><td>${row.valid_laps}</td><td>${row.stops}</td><td>${row.stints}</td><td>${escapeHtml(row.compounds.join(" / "))}</td><td>${row.tyre_age_range.map((value) => value ?? "—").join("–")}</td><td>${row.traffic_time_ratio == null ? "—" : `${(row.traffic_time_ratio * 100).toFixed(1)}%`}</td><td><span class="tag warn">audit_only</span></td></tr>`).join("");
  $("auditPairs").innerHTML = (audit.pair_audit || []).map((row) => `<tr><td>${escapeHtml(row.left_driver)}</td><td>${escapeHtml(row.right_driver)}</td><td>${formatDelta(row.global_mean_delta_s)}</td><td><span class="tag warn">${escapeHtml(row.status)}</span></td><td>${escapeHtml(row.reasons.join(" · "))}</td></tr>`).join("");
}

function renderQualifying(payload) {
  const qualifying = payload.qualifying_analysis;
  $("qualifyingNote").innerHTML = `<strong>阶段字段：</strong>${escapeHtml(qualifying.q1_q2_q3_phase)}。当前只展示 ${escapeHtml(qualifying.interpretation)}，排位与正赛不合并。`;
  $("qualifyingRows").innerHTML = (qualifying.rows || []).map((row) => `<tr><td><strong>${escapeHtml(row.driver)}</strong></td><td>${formatSeconds(row.best_accurate_lap_proxy_s)}</td><td>L${row.lap_number}</td><td>${escapeHtml(row.compound)}</td><td><span class="tag warn">${escapeHtml(row.phase_status)}</span></td></tr>`).join("");
}

function renderDataGap(payload) {
  $("dataGapRows").innerHTML = (payload.data_gap_audit || []).map((row) => {
    const kind = row.status === "AVAILABLE" || row.status === "AVAILABLE_PROXY" ? "ok" : "warn";
    return `<tr><td><strong>${escapeHtml(row.field)}</strong></td><td>${escapeHtml(row.current_field)}</td><td><span class="tag ${kind}">${escapeHtml(row.status)}</span></td><td>${escapeHtml(row.substitute || "—")}</td><td>${escapeHtml(row.publication)}</td></tr>`;
  }).join("");
}

function renderView(payload) {
  const audit = state.view === "audit";
  $("auditPanel").hidden = !audit;
  $("pacePanel").hidden = audit;
  $("summaryPanel").hidden = audit;
  $("trafficPanel").hidden = audit;
  $("pairPanel").hidden = audit;
  $("viewNote").textContent = audit ? "审计伴随不形成全序；它解释停站、配方、胎龄和交通结构为何阻止因果比较。" : "视觉复刻可形成描述性顺序；不等于车辆/车手因果排名。";
  $("paceScopeLabel").hidden = audit;
  if (audit) renderAudit(payload);
  else {
    renderPace(payload);
    renderSummary(payload);
    renderTraffic(payload);
    renderPairwise(payload);
  }
}

function render(payload, manifest) {
  state.payload = payload;
  state.manifest = manifest;
  renderMetrics(payload);
  setList("publicMethodList", payload.method_card.public_method);
  setList("visualInferenceList", payload.method_card.visual_inference);
  setList("notIdentifiableList", payload.method_card.not_identifiable);
  $("targetStatus").textContent = manifest.status;
  $("identityFooter").textContent = `run=${manifest.run_id} · report SHA-256 ${manifest.report.sha256.slice(0, 16)}… · v17 仅作审计参照`;
  renderQualifying(payload);
  renderDataGap(payload);
  renderView(payload);
}

async function load() {
  try {
    const manifestResponse = await fetch(MANIFEST_PATH, { cache: "no-store" });
    if (!manifestResponse.ok) throw new Error(`读取实验 manifest 失败：${manifestResponse.status}`);
    const index = await manifestResponse.json();
    const targetIndex = (index.targets || []).find((row) => row.target_id === "f1pace");
    if (!targetIndex?.manifest) throw new Error("参考分析实验室索引中没有 f1pace 目标");
    const targetManifestPath = String(targetIndex.manifest).replaceAll("\\", "/");
    const targetManifestResponse = await fetch(targetManifestPath, { cache: "no-store" });
    if (!targetManifestResponse.ok) throw new Error(`读取目标 manifest 失败：${targetManifestResponse.status}`);
    const manifest = await targetManifestResponse.json();
    const reportPath = String(manifest.report.path || "").replaceAll("\\", "/");
    const payload = await fetchJsonWithHash(reportPath, manifest.report.sha256);
    render(payload, manifest);
    $("loadStatus").textContent = "PASS · 哈希已校验";
    $("loadStatus").classList.add("ok");
  } catch (error) {
    console.error(error);
    $("loadStatus").textContent = "FAIL · 读取失败";
    $("loadStatus").classList.add("fail");
    $("methodPanel").innerHTML = `<div class="auditCallout"><strong>实验产物未加载：</strong>${escapeHtml(error.message)}<br>请通过 <code>npm run build</code> 与 <code>npm run dev</code> 后访问本地 HTTP URL。</div>`;
  }
}

$("viewSelect").addEventListener("change", (event) => {
  state.view = event.target.value;
  if (state.payload) renderView(state.payload);
});
$("paceScopeSelect").addEventListener("change", (event) => {
  state.scope = event.target.value;
  if (state.payload && state.view === "visual") {
    renderPace(state.payload);
    renderSummary(state.payload);
  }
});

load();
