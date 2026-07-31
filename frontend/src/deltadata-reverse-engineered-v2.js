const $ = (id) => document.getElementById(id);

const state = {
  report: null,
  manifest: null,
  view: "visual",
  ranking: "team",
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function finite(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function number(value, digits = 3) {
  const numeric = finite(value);
  return numeric === null ? "—" : numeric.toFixed(digits);
}

function signed(value, digits = 3, suffix = "") {
  const numeric = finite(value);
  if (numeric === null) return "—";
  return `${numeric > 0 ? "+" : ""}${numeric.toFixed(digits)}${suffix}`;
}

function percent(value, digits = 1) {
  const numeric = finite(value);
  return numeric === null ? "—" : `${(numeric * 100).toFixed(digits)}%`;
}

async function sha256Hex(buffer) {
  const digest = await crypto.subtle.digest("SHA-256", buffer);
  return [...new Uint8Array(digest)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

async function loadJson(path, expectedHash = null) {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) throw new Error(`${path} · HTTP ${response.status}`);
  const buffer = await response.arrayBuffer();
  if (expectedHash && await sha256Hex(buffer) !== expectedHash) {
    throw new Error(`${path} · SHA-256 不匹配`);
  }
  return JSON.parse(new TextDecoder().decode(buffer));
}

function renderMetrics() {
  const report = state.report;
  const validation = report.validation;
  const model = report.model;
  const audit = report.audited_analysis;
  const metrics = [
    ["公开图基准 MAE", `${number(validation.reference_benchmark_mae_pp, 3)} pp`, "3 个已见 2026 H2H"],
    ["方向准确率", percent(validation.reference_benchmark_direction_accuracy), "3/3 Antonelli 更快"],
    ["原始逐点", validation.pilot_raw_points.toLocaleString("en-US"), "2025 Abu Dhabi Race"],
    ["clean 候选", validation.clean_air_candidates, "至少 80% 时间 clean"],
    ["Huber 代表圈", validation.representative_laps, `候选 ${model.candidate_rows}`],
    ["审计可比对", `${audit.comparable_teammate_pairs}/${audit.all_teammate_pairs}`, "不形成全场总序"],
    ["公开燃油情景", "0.032 s/L", "不是 2025 真实燃油"],
    ["精确源码", "不可获得", "SKIPPED_OPAQUE_METHOD"],
    ["合成圈", validation.synthetic_laps, "必须为 0"],
    ["v1 共享顺序", "8/8", "共享车手完全一致"],
    ["模型残差 MAE", `${number(model.fit_metrics.representative_mae_s, 3)} s`, "Huber inliers"],
    ["物理交通", "逐点", "同位置 crossing headway"],
  ];
  $("metricGrid").innerHTML = metrics.map(([label, value, detail]) => `
    <article class="metric">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
      <small>${escapeHtml(detail)}</small>
    </article>
  `).join("");
}

function pairSample(row, driver) {
  if (row.left_driver === driver) return row.left_sample || {};
  if (row.right_driver === driver) return row.right_sample || {};
  return {};
}

function renderBenchmark() {
  const rows = state.report.reference_benchmark.tested_rows;
  $("benchmarkCards").innerHTML = rows.map((row) => `
    <article class="benchmarkCard">
      <header>
        <strong>${escapeHtml(row.event)}</strong>
        <span class="tag ${row.audited_status === "COMPARABLE" ? "ok" : "warn"}">${escapeHtml(row.audited_status)}</span>
      </header>
      <div class="comparison">
        <div><label>公开图</label><b>${signed(row.reference_delta_pct, 3, "%")}</b></div>
        <div><label>本模型</label><b>${signed(row.model_delta_pct, 3, "%")}</b></div>
      </div>
      <footer>
        <span>|误差| ${number(row.absolute_error_pp, 3)} pp</span>
        <span>ANT ${row.antonelli_representative_laps} / RUS ${row.russell_representative_laps} 圈</span>
      </footer>
    </article>
  `).join("");
  $("benchmarkTable").innerHTML = `
    <thead><tr><th>事件</th><th>公开 ANT-RUS</th><th>模型 ANT-RUS</th><th>误差</th><th>方向</th><th>审计</th><th>门控失败</th></tr></thead>
    <tbody>${rows.map((row) => `
      <tr>
        <td><strong>${escapeHtml(row.event)}</strong></td>
        <td>${signed(row.reference_delta_pct, 3, "%")}</td>
        <td>${signed(row.model_delta_pct, 3, "%")}</td>
        <td class="${Math.abs(row.error_pp) <= .1 ? "good" : "warn"}">${signed(row.error_pp, 3, " pp")}</td>
        <td class="${row.direction_match ? "good" : "bad"}">${row.direction_match ? "一致" : "不一致"}</td>
        <td><span class="tag ${row.audited_status === "COMPARABLE" ? "ok" : "warn"}">${escapeHtml(row.audited_status)}</span></td>
        <td>${escapeHtml((row.gate_failures || []).join(" · ") || "通过")}</td>
      </tr>
    `).join("")}</tbody>
  `;
}

function rankingRows() {
  return state.ranking === "team"
    ? state.report.visual_replication.team_ranking
    : state.report.visual_replication.driver_ranking;
}

function renderRanking() {
  const rows = rankingRows();
  const key = state.ranking === "team" ? "team" : "driver";
  const finiteGaps = rows
    .map((row) => finite(row.delta_to_fastest_pct))
    .filter((value) => value !== null);
  const maximum = Math.max(...finiteGaps, .01);
  $("rankingTitle").textContent = state.ranking === "team"
    ? "车队方法等价顺序"
    : "车手方法等价顺序";
  $("rankingBars").innerHTML = rows.map((row) => {
    const gap = finite(row.delta_to_fastest_pct);
    const missing = gap === null;
    const width = missing ? 0 : Math.max(2, gap / maximum * 100);
    return `
      <div class="rankRow ${missing ? "missing" : ""}">
        <span class="rank">${row.visual_rank ?? "—"}</span>
        <span class="identity" title="${escapeHtml(row[key])}">${escapeHtml(row[key])}</span>
        <span class="rankTrack"><i style="width:${width}%"></i></span>
        <span class="rankValue">${missing ? "不排名" : `+${number(gap, 3)}%`}</span>
      </div>
    `;
  }).join("");
}

function renderSamples() {
  const ledger = state.report.representative_ledger;
  const model = state.report.model;
  const summary = [
    ["模型车手", `${ledger.drivers_in_model}/20`],
    ["候选圈", ledger.candidate_laps],
    ["代表圈", ledger.representative_laps],
    ["下权异常", ledger.downweighted_outliers],
    ["Kish ESS", number(ledger.representative_kish_ess, 1)],
    ["Huber scale", `${number(model.scale_s, 3)} s`],
  ];
  $("sampleSummary").innerHTML = summary.map(([label, value]) => `
    <div class="summaryCell"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>
  `).join("");
  const drivers = state.report.visual_replication.driver_ranking;
  $("sampleTable").innerHTML = `
    <thead><tr><th>车手</th><th>候选</th><th>代表</th><th>ESS</th><th>交通均值</th><th>P90</th><th>状态</th></tr></thead>
    <tbody>${drivers.map((row) => `
      <tr>
        <td><strong>${escapeHtml(row.driver)}</strong></td>
        <td>${row.sample.candidate_laps}</td>
        <td>${row.sample.representative_laps}</td>
        <td>${number(row.sample.kish_ess, 1)}</td>
        <td>${percent(row.sample.traffic_distribution?.mean)}</td>
        <td>${percent(row.sample.traffic_distribution?.p90)}</td>
        <td><span class="tag ${row.status === "PUBLISHABLE_VISUAL_PROXY" ? "ok" : "fail"}">${escapeHtml(row.status)}</span></td>
      </tr>
    `).join("")}</tbody>
  `;
}

function renderVisualPairs() {
  const rows = state.report.visual_replication.teammate_h2h;
  $("visualPairTable").innerHTML = `
    <thead><tr><th>车队</th><th>左</th><th>右</th><th>visual 左−右</th><th>更快</th><th>左/右代表圈</th><th>左/右 ESS</th><th>审计状态</th></tr></thead>
    <tbody>${rows.map((row) => `
      <tr>
        <td><strong>${escapeHtml(row.team)}</strong></td>
        <td>${escapeHtml(row.left_driver)}</td>
        <td>${escapeHtml(row.right_driver)}</td>
        <td>${signed(row.visual_left_minus_right_pct, 3, "%")}</td>
        <td>${escapeHtml(row.visual_faster_driver || "—")}</td>
        <td>${row.left_sample?.representative_laps ?? "—"} / ${row.right_sample?.representative_laps ?? "—"}</td>
        <td>${number(row.left_sample?.kish_ess, 1)} / ${number(row.right_sample?.kish_ess, 1)}</td>
        <td><span class="tag ${row.audited_status === "COMPARABLE" ? "ok" : "warn"}">${escapeHtml(row.audited_status)}</span></td>
      </tr>
    `).join("")}</tbody>
  `;
}

function renderAudit() {
  const audit = state.report.audited_analysis;
  $("auditStatus").textContent = audit.status;
  $("auditStatus").className = `statusPill ${audit.direct_total_order_allowed ? "" : "warning"}`;
  $("auditPairTable").innerHTML = `
    <thead><tr><th>车队</th><th>配对</th><th>审计 delta</th><th>共同配方</th><th>胎龄重叠</th><th>阶段重叠</th><th>交通差</th><th>状态/门控</th></tr></thead>
    <tbody>${audit.pairwise.map((row) => `
      <tr>
        <td><strong>${escapeHtml(row.team)}</strong></td>
        <td>${escapeHtml(row.left_driver)} − ${escapeHtml(row.right_driver)}</td>
        <td class="${row.audited_status === "COMPARABLE" ? "good" : "warn"}">${signed(row.audited_left_minus_right_pct, 3, "%")}</td>
        <td>${escapeHtml((row.common_support?.compounds || []).join(" / ") || "—")}</td>
        <td>${number(row.common_support?.tyre_age_overlap_span_laps, 1)} L</td>
        <td>${percent(row.common_support?.phase_overlap_ratio)}</td>
        <td>${percent(row.common_support?.traffic_mean_gap)}</td>
        <td><span class="tag ${row.audited_status === "COMPARABLE" ? "ok" : "warn"}">${escapeHtml(row.audited_status)}</span> ${escapeHtml((row.gate_failures || []).join(" · ") || "通过")}</td>
      </tr>
    `).join("")}</tbody>
  `;
}

function renderFuelSensitivity() {
  const rows = state.report.fuel_and_threshold_sensitivity.pair_summary;
  $("fuelTable").innerHTML = `
    <thead><tr><th>车队</th><th>配对</th><th>public .032</th><th>v17 low</th><th>v17 base</th><th>v17 high</th><th>范围</th><th>方向</th></tr></thead>
    <tbody>${rows.map((row) => {
      const values = row.fuel_scenario_delta_pct || {};
      const range = row.range_pct || [];
      return `
        <tr>
          <td><strong>${escapeHtml(row.team)}</strong></td>
          <td>${escapeHtml(row.left_driver)} − ${escapeHtml(row.right_driver)}</td>
          <td>${signed(values.public_2026, 3, "%")}</td>
          <td>${signed(values.v17_low, 3, "%")}</td>
          <td>${signed(values.v17_base, 3, "%")}</td>
          <td>${signed(values.v17_high, 3, "%")}</td>
          <td>${range.length ? `${signed(range[0], 3)}…${signed(range[1], 3)}` : "—"}</td>
          <td class="${row.direction_stable ? "good" : "warn"}">${row.direction_stable ? "稳定" : "翻转"}</td>
        </tr>
      `;
    }).join("")}</tbody>
  `;
}

function renderLegacy() {
  const legacy = state.report.legacy_comparison;
  const summary = legacy.summary;
  const cells = [
    ["共享车手顺序", summary.driver_order_exact_match ? "完全一致" : "有变化"],
    ["共享车队顺序", summary.team_order_exact_match ? "完全一致" : "有变化"],
    ["车手 gap MAE", `${number(summary.driver_centered_gap_mae_s, 3)} s`],
    ["车队 gap MAE", `${number(summary.team_centered_gap_mae_s, 3)} s`],
  ];
  $("legacySummary").innerHTML = cells.map(([label, value]) => `
    <div class="summaryCell"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>
  `).join("");
  $("legacyTable").innerHTML = `
    <thead><tr><th>车手</th><th>v1 gap</th><th>v2 gap</th><th>v2−v1</th></tr></thead>
    <tbody>${legacy.driver_rows.map((row) => `
      <tr>
        <td><strong>${escapeHtml(row.driver)}</strong></td>
        <td>${number(row.v1_centered_gap_s, 3)} s</td>
        <td>${number(row.v2_centered_gap_s, 3)} s</td>
        <td class="${Math.abs(row.v2_minus_v1_gap_s) <= .15 ? "good" : "warn"}">${signed(row.v2_minus_v1_gap_s, 3, " s")}</td>
      </tr>
    `).join("")}</tbody>
  `;
}

function renderExclusions() {
  const ledger = state.report.exclusion_ledger;
  const rows = Object.entries(ledger.selection_disposition_counts || {});
  const headline = [
    ["all_laps", ledger.all_laps],
    ["clean_air_candidates", ledger.clean_air_candidates],
    ["excluded_laps", ledger.excluded_laps],
  ];
  $("exclusionGrid").innerHTML = [...headline, ...rows].map(([label, value]) => `
    <div class="exclusionCell"><span>${escapeHtml(label)}</span><strong>${Number(value).toLocaleString("en-US")}</strong></div>
  `).join("");
}

function renderModel() {
  const model = state.report.model;
  const rows = model.identity.feature_names.map((feature, index) => ({
    feature,
    coefficient: finite(model.coefficients[index]),
  })).sort((left, right) => Math.abs(right.coefficient) - Math.abs(left.coefficient));
  $("coefficientTable").innerHTML = `
    <thead><tr><th>特征</th><th>系数</th><th>方向</th></tr></thead>
    <tbody>${rows.map((row) => `
      <tr>
        <td><code>${escapeHtml(row.feature)}</code></td>
        <td>${signed(row.coefficient, 5)}</td>
        <td class="${row.coefficient < 0 ? "good" : "warn"}">${row.coefficient < 0 ? "降低预测圈时" : "提高预测圈时"}</td>
      </tr>
    `).join("")}</tbody>
  `;
}

function renderMethodAndGaps() {
  const card = state.report.method_card;
  const fillList = (id, values) => {
    $(id).innerHTML = (values || []).map((value) => `<li>${escapeHtml(value)}</li>`).join("");
  };
  fillList("publicMethodList", card.publicly_stated_method);
  fillList("equivalentMethodList", card.reverse_engineered_or_reimplemented);
  fillList("boundaryList", card.not_identifiable);
  $("dataGapTable").innerHTML = `
    <thead><tr><th>目标字段</th><th>当前字段</th><th>缺口</th><th>替代代理</th><th>发布</th></tr></thead>
    <tbody>${state.report.data_gap_audit.map((row) => `
      <tr>
        <td><strong>${escapeHtml(row.required_field)}</strong></td>
        <td>${escapeHtml(row.repository_field)}</td>
        <td>${escapeHtml(row.gap)}</td>
        <td>${escapeHtml(row.substitute)}</td>
        <td><span class="tag ${row.publication.startsWith("ALLOW") ? "ok" : row.publication.includes("NOT") || row.publication.includes("NO_") ? "fail" : "warn"}">${escapeHtml(row.publication)}</span></td>
      </tr>
    `).join("")}</tbody>
  `;
}

function applyView() {
  const audit = state.view === "audit";
  $("visualPanel").hidden = audit;
  $("auditPanel").hidden = !audit;
  $("rankingSelect").disabled = audit;
  $("viewNote").textContent = audit
    ? "审计视图不形成全场总序；门控失败时 audited delta 为空。"
    : "视觉顺序用于复核方法效果，不等于因果排名，也不会自动进入 Race Dossier。";
}

function renderAll() {
  renderMetrics();
  renderBenchmark();
  renderRanking();
  renderSamples();
  renderVisualPairs();
  renderAudit();
  renderFuelSensitivity();
  renderLegacy();
  renderExclusions();
  renderModel();
  renderMethodAndGaps();
  applyView();
  $("identityFooter").textContent = `${state.report.run_id} · report ${state.manifest.report_sha256.slice(0, 16)}… · v17 ${state.report.source_identity.v17_manifest.run_id}`;
}

async function init() {
  try {
    const index = await loadJson("data/reference-analysis-lab/v2/manifest.json");
    const target = index.targets.find(
      (row) => row.target_id === "deltadata-reverse-engineered-v2",
    );
    if (!target) throw new Error("v2 索引中缺少 DeltaData target");
    const manifest = await loadJson(target.manifest);
    const base = target.manifest.slice(0, target.manifest.lastIndexOf("/") + 1);
    const report = await loadJson(
      `${base}${manifest.report}`,
      manifest.report_sha256,
    );
    if (report.status !== "METHOD_EQUIVALENT_REFERENCE_BENCHMARKED") {
      throw new Error(`冻结状态不符：${report.status}`);
    }
    if (report.validation.synthetic_laps !== 0) {
      throw new Error("检测到合成圈，拒绝展示为真实 pilot");
    }
    if (report.validation.reference_benchmark_is_blind_holdout !== false) {
      throw new Error("参考知情基准身份错误");
    }
    state.report = report;
    state.manifest = manifest;
    $("runStatus").textContent = report.status;
    $("loadStatus").textContent = "冻结产物已校验";
    $("loadStatus").classList.add("good");
    renderAll();
    window.__F1TR_DELTADATA_V2_READY__ = true;
  } catch (error) {
    console.error(error);
    $("loadStatus").textContent = "加载失败";
    $("loadStatus").classList.add("bad");
    $("metricGrid").innerHTML = `<article class="metric"><span>错误</span><strong>FAIL</strong><small>${escapeHtml(error.message)}</small></article>`;
    window.__F1TR_DELTADATA_V2_READY__ = false;
  }
}

$("viewSelect").addEventListener("change", (event) => {
  state.view = event.target.value;
  applyView();
});
$("rankingSelect").addEventListener("change", (event) => {
  state.ranking = event.target.value;
  renderRanking();
});

const params = new URLSearchParams(window.location.search);
state.view = params.get("view") === "audit" ? "audit" : "visual";
state.ranking = params.get("ranking") === "driver" ? "driver" : "team";
$("viewSelect").value = state.view;
$("rankingSelect").value = state.ranking;
init();
