const MANIFEST_PATH = "data/reference-analysis-lab/v1/manifest.json";
const TARGET_STATUS = "METHOD_EQUIVALENT_ONLY";
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

function seconds(value) {
  const number = finite(value);
  return number === null ? "—" : `${number.toFixed(3)}s`;
}

function percent(value) {
  const number = finite(value);
  return number === null ? "—" : `${(number * 100).toFixed(1)}%`;
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
  const ledger = payload.exclusion_ledger;
  const audit = payload.audited_analysis.rank_status;
  const metrics = [
    ["Stint 覆盖", ledger.stints, "v17 reporting teams"],
    ["质量拟合点", ledger.quality_fit_points, `观察点 ${ledger.observed_points}`],
    ["clean-air 点", ledger.clean_air_points, `Kish ESS sum ${payload.visual_replication.sample_disclosure.clean_air_kish_ess_sum.toFixed(1)}`],
    ["直接可比对", audit.directly_comparable_pairs, `warning ${audit.balance_warning_pairs}`],
    ["不可比对", audit.not_comparable_pairs, "保持审计，不形成全序"],
    ["合成点", payload.validation.synthetic_points, "必须为 0"],
  ];
  $("metricGrid").innerHTML = metrics.map(([label, value, detail]) => `<article class="metricCard"><div class="metricLabel">${escapeHtml(label)}</div><div class="metricValue">${escapeHtml(value)}</div><div class="metricDetail">${escapeHtml(detail)}</div></article>`).join("");
}

function renderTeamTable(rows) {
  $("teamTable").innerHTML = `<thead><tr><th>描述性顺序</th><th>车队</th><th>Stint</th><th>有效点</th><th>clean 点</th><th>ESS</th><th>base 参考配速</th></tr></thead><tbody>${rows.map((row, index) => `<tr><td class="rank">${index + 1}</td><td><strong>${escapeHtml(row.team)}</strong></td><td>${row.stints}</td><td>${row.quality_fit_points}</td><td>${row.clean_air_points}</td><td>${finite(row.clean_air_kish_ess)?.toFixed(1) ?? "—"}</td><td>${seconds(row.base_reference_pace_s)}</td></tr>`).join("")}</tbody>`;
}

function renderDriverTable(rows) {
  $("driverTable").innerHTML = `<thead><tr><th>描述性顺序</th><th>车手</th><th>车队</th><th>Stint</th><th>有效点</th><th>clean 点</th><th>base 参考配速</th></tr></thead><tbody>${rows.map((row, index) => `<tr><td class="rank">${index + 1}</td><td><strong>${escapeHtml(row.driver)}</strong></td><td>${escapeHtml(row.team)}</td><td>${row.stints}</td><td>${row.quality_fit_points}</td><td>${row.clean_air_points}</td><td>${seconds(row.base_reference_pace_s)}</td></tr>`).join("")}</tbody>`;
}

function renderSensitivity(rows) {
  $("sensitivityTable").innerHTML = `<thead><tr><th>车手</th><th>车队</th><th>low 参考</th><th>base 参考</th><th>high 参考</th><th>low 斜率</th><th>base 斜率</th><th>high 斜率</th><th>clean share</th></tr></thead><tbody>${rows.map((row) => `<tr><td><strong>${escapeHtml(row.driver)}</strong></td><td>${escapeHtml(row.team)}</td><td>${seconds(row.fuel_scenarios.low.reference_pace_s)}</td><td>${seconds(row.fuel_scenarios.base.reference_pace_s)}</td><td>${seconds(row.fuel_scenarios.high.reference_pace_s)}</td><td>${seconds(row.tyre_degradation.low_s_per_tyre_lap)}</td><td>${seconds(row.tyre_degradation.base_s_per_tyre_lap)}</td><td>${seconds(row.tyre_degradation.high_s_per_tyre_lap)}</td><td>${percent(row.clean_air_weighted_share)}</td></tr>`).join("")}</tbody>`;
}

function renderSample(payload) {
  const sample = payload.visual_replication.sample_disclosure;
  const ledger = payload.exclusion_ledger;
  $("sampleCallout").innerHTML = `<strong>真实数据账本：</strong>${sample.stints} Stints、${sample.points} 观察点、${sample.quality_fit_points} 个质量合格拟合点、${sample.clean_air_points} 个 clean-air 点；clean-air ESS sum ${sample.clean_air_kish_ess_sum.toFixed(1)}。交通模型不足/质量缺失等处置均保留，不为了排名静默删除。`;
  const rows = Object.entries(ledger.analysis_disposition_counts || {}).map(([key, value]) => [key, value]);
  $("gapTable").innerHTML = `<thead><tr><th>处置字段</th><th>点数</th><th>解释</th></tr></thead><tbody>${rows.map(([key, value]) => `<tr><td><strong>${escapeHtml(key)}</strong></td><td>${value}</td><td>${key === "traffic_model_insufficient" ? "交通状态存在，但当前模型不足以把该点当作 clean-air 直接证据" : key === "quality_missing" ? "质量字段缺失，保留在排除账本" : "v17 sidecar 的发布处置"}</td></tr>`).join("")}</tbody>`;
}

function renderPairs(payload) {
  const pairs = payload.audited_analysis.pairwise;
  $("pairTable").innerHTML = `<thead><tr><th>状态</th><th>左</th><th>右</th><th>配方</th><th>direct delta</th><th>ESS 左/右</th><th>traffic TVD</th><th>燃油 delta L/B/H</th><th>门控</th></tr></thead><tbody>${pairs.map((row) => { const tag = row.status === "comparable" ? "ok" : row.status === "audit_only_balance_warning" ? "warn" : "fail"; const fuel = ["low", "base", "high"].map((key) => row.fuel_sensitivity_delta_s[key] == null ? "—" : row.fuel_sensitivity_delta_s[key].toFixed(3)).join(" / "); return `<tr><td><span class="tag ${tag}">${escapeHtml(row.status)}</span></td><td><strong>${escapeHtml(row.left_driver)}</strong></td><td><strong>${escapeHtml(row.right_driver)}</strong></td><td>${escapeHtml(row.compound)}</td><td>${seconds(row.direct_comparison_left_minus_right_pace_s)}</td><td>${row.kish_ess.left?.toFixed(1) ?? "—"} / ${row.kish_ess.right?.toFixed(1) ?? "—"}</td><td>${row.traffic_tvd == null ? "—" : row.traffic_tvd.toFixed(3)}</td><td>${fuel}</td><td>${escapeHtml((row.gate_failures || []).join(" · ") || "通过")}</td></tr>`; }).join("")}</tbody>`;
}

function renderDataGap(payload) {
  $("dataGapTable").innerHTML = `<thead><tr><th>目标字段</th><th>当前字段</th><th>状态</th><th>替代代理</th><th>发布规则</th></tr></thead><tbody>${payload.data_gap_audit.map((row) => { const kind = row.status === "AVAILABLE" || row.status === "AVAILABLE_PROXY" ? "ok" : "warn"; return `<tr><td><strong>${escapeHtml(row.field)}</strong></td><td>${escapeHtml(row.current_field)}</td><td><span class="tag ${kind}">${escapeHtml(row.status)}</span></td><td>${escapeHtml(row.substitute)}</td><td>${escapeHtml(row.publication)}</td></tr>`; }).join("")}</tbody>`;
}

function render(payload, manifest) {
  state.payload = payload;
  state.manifest = manifest;
  renderMetrics(payload);
  list("publicMethodList", payload.method_card.public_method);
  list("equivalentMethodList", payload.method_card.equivalent_method);
  list("notIdentifiableList", payload.method_card.not_identifiable);
  renderTeamTable(payload.visual_replication.clean_air_pace.team_ranking);
  renderDriverTable(payload.visual_replication.clean_air_pace.driver_ranking);
  renderSensitivity(payload.visual_replication.clean_air_pace.driver_ranking);
  renderSample(payload);
  renderPairs(payload);
  renderDataGap(payload);
  $("auditCallout").textContent = `${payload.audited_analysis.reason} 当前仅 ${payload.audited_analysis.rank_status.directly_comparable_pairs} 对可比，${payload.audited_analysis.rank_status.not_comparable_pairs} 对不可比；任何未通过门控的 direct delta 都保持空值。`;
  $("identityFooter").textContent = `run=${manifest.run_id} · report SHA-256 ${manifest.report.sha256.slice(0, 16)}… · v17 sidecar ${payload.source_identity.sidecar.sha256.slice(0, 16)}…`;
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
    const target = (index.targets || []).find((row) => row.target_id === "deltadata");
    if (!target?.manifest) throw new Error("参考分析实验室索引中没有 deltadata 目标");
    const targetManifestPath = String(target.manifest).replaceAll("\\", "/");
    const targetResponse = await fetch(targetManifestPath, { cache: "no-store" });
    if (!targetResponse.ok) throw new Error(`读取 DeltaData manifest 失败：${targetResponse.status}`);
    const manifest = await targetResponse.json();
    const reportPath = String(manifest.report.path || "").replaceAll("\\", "/");
    const payload = await fetchJsonWithHash(reportPath, manifest.report.sha256);
    if (payload.status !== TARGET_STATUS || manifest.status !== TARGET_STATUS) throw new Error(`目标状态不满足：${TARGET_STATUS}`);
    render(payload, manifest);
    $("loadStatus").textContent = "PASS · 哈希已校验";
    $("loadStatus").classList.add("ok");
  } catch (error) {
    console.error(error);
    $("loadStatus").textContent = "FAIL · 读取失败";
    $("loadStatus").classList.add("fail");
    $("visualPanel").innerHTML = `<article class="deltaPanel"><div class="callout"><strong>实验产物未加载：</strong>${escapeHtml(error.message)}<br>请通过 <code>npm run build</code> 与 <code>npm run dev</code> 后访问本地 HTTP URL。</div></article>`;
  }
}

$("visualTab").addEventListener("click", () => setView("visual"));
$("auditTab").addEventListener("click", () => setView("audit"));
setView(new URLSearchParams(window.location.search).get("view") === "audit" ? "audit" : "visual");
load();
