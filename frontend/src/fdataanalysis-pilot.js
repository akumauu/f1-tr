const MANIFEST_PATH = "data/reference-analysis-lab/v1/manifest.json";
const TARGET_ID = "fdataanalysis";
const TARGET_STATUS = "PASS";
const DISTANCE_AXIS_ROLE = "distance_axis_observed_feature_proxy";
const state = { payload: null, manifest: null, view: "visual" };
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

function number(value, digits = 1, suffix = "") {
  const n = finite(value);
  return n === null ? "—" : `${n.toFixed(digits)}${suffix}`;
}

function percent(value, digits = 1) {
  const n = finite(value);
  return n === null ? "—" : `${(n * 100).toFixed(digits)}%`;
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
  if (expectedHash && actualHash !== expectedHash) throw new Error(`实验产物 SHA-256 不匹配：${path}`);
  return JSON.parse(new TextDecoder().decode(buffer));
}

function list(id, values) {
  $(id).innerHTML = (values || []).map((value) => `<li>${escapeHtml(value)}</li>`).join("");
}

function renderMetrics(payload) {
  const validation = payload.validation;
  const ledger = payload.exclusion_ledger;
  const metrics = [
    ["原始逐点样本", validation.raw_points, "真实 telemetry parquet"],
    ["有效逐点样本", validation.valid_points, `${ledger.valid_laps} 个有效圈`],
    ["距离轴 bin", validation.axis_bins, "0.005 rel_distance"],
    ["弯道代理", validation.corner_proxy_count, "速度局部极小值"],
    ["直道代理", validation.straight_proxy_count, "相邻弯道距离窗口"],
    ["同队基线", validation.same_team_baseline_pairs, "反向 pair 保留"],
  ];
  $("metricGrid").innerHTML = metrics.map(([label, value, detail]) => `<article class="metricCard"><div class="metricLabel">${escapeHtml(label)}</div><div class="metricValue">${escapeHtml(String(value))}</div><div class="metricDetail">${escapeHtml(detail)}</div></article>`).join("");
}

function renderSegments(payload) {
  const segments = payload.visual_replication.track_segments;
  $("cornerTable").innerHTML = `<thead><tr><th>代理</th><th>入口</th><th>最低点</th><th>出口</th><th>全场中位最低速度</th></tr></thead><tbody>${segments.corner_proxies.map((row) => `<tr><td><strong>${escapeHtml(row.corner_id)}</strong></td><td>${number(row.entry_rel_distance, 3)}</td><td>${number(row.minimum_rel_distance, 3)}</td><td>${number(row.exit_rel_distance, 3)}</td><td>${number(row.global_proxy_speed_kph, 1, " km/h")}</td></tr>`).join("")}</tbody>`;
  $("straightTable").innerHTML = `<thead><tr><th>代理</th><th>起点</th><th>终点</th><th>窗口长度</th><th>定义</th></tr></thead><tbody>${segments.straight_proxies.map((row) => `<tr><td><strong>${escapeHtml(row.straight_id)}</strong></td><td>${escapeHtml(row.from_corner)}</td><td>${escapeHtml(row.to_corner)}</td><td>${number(row.span_rel_distance, 3)}</td><td><span class="tag warn">distance proxy</span></td></tr>`).join("")}</tbody>`;
}

function renderDriverFeatures(rows) {
  $("driverFeatureTable").innerHTML = `<thead><tr><th>车手</th><th>车队</th><th>有效圈</th><th>速度 P50</th><th>速度 P90</th><th>全油门</th><th>制动</th><th>DRS</th><th>中位挡位</th><th>圈时/胎龄</th><th>速度/胎龄</th></tr></thead><tbody>${rows.map((row) => `<tr><td><strong>${escapeHtml(row.driver)}</strong></td><td>${escapeHtml(row.team)}</td><td>${row.valid_laps}</td><td>${number(row.speed_distribution.p50_kph, 1, " km/h")}</td><td>${number(row.speed_distribution.p90_kph, 1, " km/h")}</td><td>${percent(row.control_usage.full_throttle_share)}</td><td>${percent(row.control_usage.brake_share)}</td><td>${percent(row.control_usage.drs_share)}</td><td>${number(row.control_usage.median_gear, 1)}</td><td>${number(row.tyre_age_effects.lap_time_s_per_tyre_lap, 3, " s")}</td><td>${number(row.tyre_age_effects.median_speed_kph_per_tyre_lap, 3, " km/h")}</td></tr>`).join("")}</tbody>`;
}

function renderTeamFeatures(rows) {
  $("teamFeatureTable").innerHTML = `<thead><tr><th>车队</th><th>车手</th><th>有效圈</th><th>速度 P50</th><th>速度 P90</th><th>全油门</th><th>制动</th><th>DRS</th><th>圈时/胎龄</th></tr></thead><tbody>${rows.map((row) => `<tr><td><strong>${escapeHtml(row.team)}</strong></td><td>${escapeHtml(row.drivers.join(" / "))}</td><td>${row.valid_laps}</td><td>${number(row.speed_distribution.p50_kph, 1, " km/h")}</td><td>${number(row.speed_distribution.p90_kph, 1, " km/h")}</td><td>${percent(row.control_usage.full_throttle_share)}</td><td>${percent(row.control_usage.brake_share)}</td><td>${percent(row.control_usage.drs_share)}</td><td>${number(row.tyre_age_effects.lap_time_s_per_tyre_lap, 3, " s")}</td></tr>`).join("")}</tbody>`;
}

function renderTeammates(rows) {
  $("teammateTable").innerHTML = `<thead><tr><th>车队</th><th>车手</th><th>队友</th><th>速度差</th><th>全油门差</th><th>制动差</th><th>DRS 差</th><th>圈时/胎龄差</th></tr></thead><tbody>${rows.map((row) => `<tr><td>${escapeHtml(row.team)}</td><td><strong>${escapeHtml(row.driver)}</strong></td><td>${escapeHtml(row.teammate)}</td><td>${number(row.driver_minus_teammate.median_speed_kph, 2, " km/h")}</td><td>${percent(row.driver_minus_teammate.full_throttle_share, 1)}</td><td>${percent(row.driver_minus_teammate.brake_share, 1)}</td><td>${percent(row.driver_minus_teammate.drs_share, 1)}</td><td>${number(row.driver_minus_teammate.lap_time_s_per_tyre_lap, 3, " s")}</td></tr>`).join("")}</tbody>`;
}

function renderLedger(payload) {
  const ledger = payload.exclusion_ledger;
  $("sampleCallout").innerHTML = `<strong>真实输入：</strong>${ledger.raw_points.toLocaleString()} 个原始点 → ${ledger.valid_points.toLocaleString()} 个有效点；${ledger.valid_laps} 个有效圈；排除首圈/Stint 边界圈 ${ledger.removed_first_or_stint_boundary_laps} 圈；非绿旗/黄旗圈 ${ledger.non_green_or_yellow_laps} 圈。轴重采样后保留 ${ledger.axis_rows.toLocaleString()} 行。没有合成点。`;
  $("exclusionTable").innerHTML = `<thead><tr><th>车手</th><th>车队</th><th>原始点</th><th>有效圈</th><th>有效点</th><th>距离轴行</th><th>轮胎</th></tr></thead><tbody>${ledger.per_driver.map((row) => `<tr><td><strong>${escapeHtml(row.driver)}</strong></td><td>${escapeHtml(row.team)}</td><td>${row.raw_points.toLocaleString()}</td><td>${row.valid_laps}</td><td>${row.valid_points.toLocaleString()}</td><td>${row.axis_rows.toLocaleString()}</td><td>${escapeHtml(row.compounds.join(" / "))}</td></tr>`).join("")}</tbody>`;
}

function renderDataGap(payload) {
  const html = `<thead><tr><th>目标字段</th><th>当前字段</th><th>状态</th><th>替代代理</th><th>发布规则</th></tr></thead><tbody>${payload.data_gap_audit.map((row) => { const kind = row.status === "AVAILABLE" || row.status === "AVAILABLE_PROXY" ? "ok" : "warn"; return `<tr><td><strong>${escapeHtml(row.field)}</strong></td><td>${escapeHtml(row.current_field)}</td><td><span class="tag ${kind}">${escapeHtml(row.status)}</span></td><td>${escapeHtml(row.substitute)}</td><td>${escapeHtml(row.publication)}</td></tr>`; }).join("")}</tbody>`;
  $("dataGapTable").innerHTML = html;
  $("auditGapTable").innerHTML = html;
}

function renderAudit(payload) {
  $("auditCallout").textContent = `${payload.audited_analysis.reason} 当前状态为 audit_only；允许发布观测特征与具名 proxy，但不允许直接形成车辆物理强弱或车手因果全序。`;
  list("allowedClaimsList", payload.audited_analysis.allowed_claims);
  list("forbiddenClaimsList", payload.audited_analysis.forbidden_claims);
}

function render(payload, manifest) {
  state.payload = payload;
  state.manifest = manifest;
  renderMetrics(payload);
  list("publicMethodList", payload.method_card.public_method);
  list("pilotMethodList", payload.method_card.pilot_method);
  list("notIdentifiableList", payload.method_card.not_identifiable);
  renderSegments(payload);
  renderDriverFeatures(payload.visual_replication.driver_profiles);
  renderTeamFeatures(payload.visual_replication.team_profiles);
  renderTeammates(payload.visual_replication.teammate_baselines);
  renderLedger(payload);
  renderDataGap(payload);
  renderAudit(payload);
  $("identityFooter").textContent = `run=${manifest.run_id} · report SHA-256 ${manifest.report.sha256.slice(0, 16)}… · telemetry rows ${payload.source_identity.telemetry.rows.toLocaleString()} · v17=${payload.source_identity.v17_manifest.sha256.slice(0, 16)}…`;
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
    if (!indexResponse.ok) throw new Error(`读取实验索引失败：${indexResponse.status}`);
    const index = await indexResponse.json();
    const target = (index.targets || []).find((row) => row.target_id === TARGET_ID);
    if (!target?.manifest) throw new Error("参考分析复刻实验室索引中没有 FDataAnalysis 目标");
    const targetManifestPath = String(target.manifest).replaceAll("\\", "/");
    const targetResponse = await fetch(targetManifestPath, { cache: "no-store" });
    if (!targetResponse.ok) throw new Error(`读取 FDataAnalysis manifest 失败：${targetResponse.status}`);
    const manifest = await targetResponse.json();
    const reportPath = String(manifest.report.path || "").replaceAll("\\", "/");
    const payload = await fetchJsonWithHash(reportPath, manifest.report.sha256);
    if (payload.target_id !== TARGET_ID || payload.status !== TARGET_STATUS || manifest.status !== TARGET_STATUS) throw new Error(`目标状态不满足：${TARGET_STATUS}`);
    render(payload, manifest);
    $("loadStatus").textContent = "PASS · 哈希已校验";
    $("loadStatus").classList.add("ok");
  } catch (error) {
    console.error(error);
    $("loadStatus").textContent = "FAIL · 读取失败";
    $("loadStatus").classList.add("fail");
    $("visualPanel").innerHTML = `<article class="fdataPanel"><div class="callout"><strong>实验产物未加载：</strong>${escapeHtml(error.message)}<br>请通过 <code>npm run build</code> 和 <code>npm run dev</code> 后访问本地 HTTP URL。</div></article>`;
  }
}

$("visualTab").addEventListener("click", () => setView("visual"));
$("auditTab").addEventListener("click", () => setView("audit"));
setView(new URLSearchParams(window.location.search).get("view") === "audit" ? "audit" : "visual");
load();
