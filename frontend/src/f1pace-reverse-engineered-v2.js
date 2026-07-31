const $ = (id) => document.getElementById(id);

const state = {
  report: null,
  target: null,
  event: "abu",
  view: "visual",
  labels: "percent",
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function formatNumber(value, digits = 2) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(digits) : "—";
}

function formatPercent(value, digits = 1) {
  return Number.isFinite(Number(value))
    ? `${(Number(value) * 100).toFixed(digits)}%`
    : "—";
}

async function sha256(buffer) {
  const digest = await crypto.subtle.digest("SHA-256", buffer);
  return Array.from(
    new Uint8Array(digest),
    (value) => value.toString(16).padStart(2, "0"),
  ).join("");
}

async function loadJson(path, expectedHash = null) {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) throw new Error(`${path} · HTTP ${response.status}`);
  const buffer = await response.arrayBuffer();
  if (expectedHash && await sha256(buffer) !== expectedHash) {
    throw new Error(`${path} · SHA-256 不匹配`);
  }
  return JSON.parse(new TextDecoder().decode(buffer));
}

function eventConfig() {
  if (state.event === "qatar") {
    return {
      name: "2025 Qatar · 外部验证",
      cells: state.report.visual_replication.qatar_external_cells,
      metrics: state.report.calibration.external_validation,
      rawMetrics: state.report.calibration.external_audited_raw_validation,
      role: "未参与拟合，模型参数完全冻结",
      laps: 57,
    };
  }
  return {
    name: "2025 Abu Dhabi · 校准事件",
    cells: state.report.visual_replication.abu_dhabi_cells,
    metrics: state.report.calibration.abu_dhabi_apparent_validation,
    rawMetrics: state.report.calibration.abu_dhabi_audited_raw_validation,
    role: "只用于读图校准；内部留出按整名车手分组",
    laps: 58,
  };
}

function heatColor(value) {
  const ratio = Math.max(0, Math.min(1, Number(value) || 0));
  const low = [235, 239, 245];
  const high = [165, 31, 48];
  return `rgb(${low.map((entry, index) => Math.round(
    entry + (high[index] - entry) * ratio,
  )).join(",")})`;
}

function residualColor(value) {
  const residual = Math.max(-25, Math.min(25, Number(value) || 0)) / 25;
  if (residual < 0) {
    const alpha = Math.abs(residual);
    return `rgb(${Math.round(225 - 170 * alpha)},${Math.round(
      231 - 97 * alpha,
    )},${Math.round(239 - 41 * alpha)})`;
  }
  return `rgb(${Math.round(225 - 12 * residual)},${Math.round(
    231 - 157 * residual,
  )},${Math.round(239 - 150 * residual)})`;
}

function labelForCell(row, ratio, residual = false) {
  if (!row) return "";
  if (state.labels === "compact") {
    if (residual) {
      return Math.abs(row.residual_pp) >= 10
        ? `${row.residual_pp > 0 ? "+" : ""}${Math.round(row.residual_pp)}`
        : "";
    }
    return ratio > 1 / 3 ? "●" : "";
  }
  if (residual) {
    return `${row.residual_pp > 0 ? "+" : ""}${Math.round(row.residual_pp)}`;
  }
  return ratio > .9 ? ">90" : `${Math.round(ratio * 100)}`;
}

function rowsByDriver(cells) {
  const map = new Map();
  for (const row of cells) {
    if (!map.has(row.driver)) map.set(row.driver, []);
    map.get(row.driver).push(row);
  }
  return map;
}

function renderHeatmap(containerId, cells, laps, mode) {
  const grouped = rowsByDriver(cells);
  const headers = Array.from({ length: laps }, (_, index) => {
    const lap = index + 1;
    return `<th>${lap === 1 || lap % 5 === 0 ? lap : "·"}</th>`;
  }).join("");
  const body = Array.from(grouped.entries()).map(([driver, rows]) => {
    const lookup = new Map(rows.map((row) => [Number(row.lap), row]));
    const columns = Array.from({ length: laps }, (_, index) => {
      const lap = index + 1;
      const row = lookup.get(lap);
      if (!row) {
        return `<td class="missing" title="${escapeHtml(
          `${driver} · L${lap} · 无遥测单元`,
        )}"></td>`;
      }
      const ratio = Number(
        state.view === "audit"
          ? row.audited_analysis_ratio
          : row.visual_replication_ratio,
      );
      if (mode === "residual") {
        return `<td style="background:${residualColor(
          row.residual_pp,
        )}" title="${escapeHtml(
          `${driver} · L${lap} · 模型 ${formatPercent(
            row.visual_replication_ratio,
          )} · 参考 ${formatPercent(
            row.reference_traffic_ratio,
          )} · 残差 ${formatNumber(row.residual_pp, 1)}pp`,
        )}">${labelForCell(row, ratio, true)}</td>`;
      }
      const traffic = ratio > 1 / 3 ? "traffic" : "";
      return `<td class="${traffic}" style="background:${heatColor(
        ratio,
      )}" title="${escapeHtml(
        `${driver} · L${lap} · ${
          state.view === "audit" ? "物理 2 秒" : "视觉校准"
        } ${formatPercent(ratio)} · 参考 ${formatPercent(
          row.reference_traffic_ratio,
        )} · missing ${formatPercent(row.missing)}`,
      )}">${labelForCell(row, ratio)}</td>`;
    }).join("");
    return `<tr><th>${escapeHtml(driver)}</th>${columns}</tr>`;
  }).join("");
  $(containerId).innerHTML = `<table class="heatmapTable"><thead><tr><th>车手 / 圈</th>${headers}</tr></thead><tbody>${body}</tbody></table>`;
}

function renderMetrics(config) {
  const cv = state.report.calibration.grouped_cross_validation.aggregate;
  const metrics = [
    ["参考单元", config.metrics.rows.toLocaleString("en-US"), config.role],
    [
      "视觉 MAE",
      `${formatNumber(config.metrics.mae_pp, 2)} pp`,
      state.event === "qatar" ? "零重拟合主验收" : "整场表观拟合",
    ],
    [
      "视觉 P90",
      `${formatNumber(config.metrics.p90_abs_error_pp, 2)} pp`,
      "90% 单元低于此误差",
    ],
    [
      ">33% 准确率",
      `${formatNumber(config.metrics.traffic_lap_accuracy * 100, 2)}%`,
      `${config.metrics.actual_traffic_laps} 个参考交通圈`,
    ],
    [
      "纯物理 MAE",
      `${formatNumber(config.rawMetrics.mae_pp, 2)} pp`,
      "不使用参考图校准",
    ],
    [
      "整车手 CV",
      `${formatNumber(cv.mean_mae_pp, 2)} pp`,
      `最差折 ${formatNumber(cv.worst_fold_mae_pp, 2)} pp`,
    ],
    ["读图误差", "≈0.47 pp", "22 个明文标签校准；最大约 1.50 pp"],
    ["合成样本", "0", "全部来自冻结逐点遥测"],
    ["模型特征", "18", "三族可解释时间阈值比例"],
    ["一比一源码", "不可声称", "原作者精确算法未公开"],
  ];
  $("metricGrid").innerHTML = metrics.map(
    ([label, value, detail]) => `
      <article class="metric">
        <span>${escapeHtml(label)}</span>
        <strong>${escapeHtml(value)}</strong>
        <small>${escapeHtml(detail)}</small>
      </article>`,
  ).join("");
}

function renderDriverTable(config) {
  const grouped = rowsByDriver(config.cells);
  const rows = Array.from(grouped.entries()).map(([driver, cells]) => {
    const errors = cells
      .map((row) => Math.abs(Number(row.residual_pp)))
      .filter(Number.isFinite);
    const ratioKey = state.view === "audit"
      ? "audited_analysis_ratio"
      : "visual_replication_ratio";
    const ratios = cells.map((row) => Number(row[ratioKey])).filter(Number.isFinite);
    const missing = cells.map((row) => Number(row.missing)).filter(Number.isFinite);
    return {
      driver,
      laps: cells.length,
      mae: errors.reduce((sum, value) => sum + value, 0) / errors.length,
      max: Math.max(...errors),
      mean: ratios.reduce((sum, value) => sum + value, 0) / ratios.length,
      traffic: ratios.filter((value) => value > 1 / 3).length,
      missing: missing.reduce((sum, value) => sum + value, 0) / missing.length,
    };
  }).sort((left, right) => right.mae - left.mae);
  $("driverTable").innerHTML = `
    <thead><tr><th>车手</th><th>圈</th><th>图差 MAE</th><th>最大图差</th><th>平均交通</th><th>&gt;33%</th><th>missing</th></tr></thead>
    <tbody>${rows.map((row) => `
      <tr>
        <td><strong>${escapeHtml(row.driver)}</strong></td><td>${row.laps}</td>
        <td class="${row.mae > 5 ? "bad" : row.mae > 3 ? "warn" : "good"}">${formatNumber(row.mae, 2)}pp</td>
        <td>${formatNumber(row.max, 1)}pp</td><td>${formatPercent(row.mean)}</td>
        <td>${row.traffic}</td><td>${formatPercent(row.missing)}</td>
      </tr>`).join("")}</tbody>`;
}

function renderCoefficients() {
  const model = state.report.calibration.model;
  const rows = model.feature_columns.map((feature, index) => ({
    feature,
    coefficient: Number(model.coefficients[index]),
  })).sort(
    (left, right) => Math.abs(right.coefficient) - Math.abs(left.coefficient),
  );
  $("coefficientTable").innerHTML = `
    <thead><tr><th>标准化特征</th><th>权重</th><th>方向</th></tr></thead>
    <tbody>${rows.map((row) => `
      <tr><td><code>${escapeHtml(row.feature)}</code></td>
      <td>${row.coefficient >= 0 ? "+" : ""}${formatNumber(row.coefficient, 4)}</td>
      <td class="${row.coefficient >= 0 ? "good" : "bad"}">${row.coefficient >= 0 ? "提高预测比例" : "抑制预测比例"}</td></tr>`).join("")}</tbody>`;
}

function renderValidation() {
  const calibration = state.report.calibration;
  $("validationFlow").innerHTML = `
    <article class="flowBox"><strong>① 阿布扎比参考图</strong><span>1,156 个颜色读数代理；只用于校准。</span></article>
    <span class="flowArrow">→</span>
    <article class="flowBox"><strong>② 整车手 GroupKFold</strong><span>同一车手的相邻圈不会跨入训练与留出两侧。</span></article>
    <span class="flowArrow">→</span>
    <article class="flowBox"><strong>③ 卡塔尔事件外验证</strong><span>1,067 个真实单元；零重拟合、零阈值调整。</span></article>`;
  const folds = calibration.grouped_cross_validation.folds;
  $("foldTable").innerHTML = `
    <thead><tr><th>折</th><th>整车手留出</th><th>MAE</th><th>RMSE</th><th>P90</th><th>&gt;33% 准确率</th></tr></thead>
    <tbody>${folds.map((row) => `
      <tr><td>${row.fold}</td><td>${escapeHtml(row.held_out_drivers.join(" / "))}</td>
      <td>${formatNumber(row.mae_pp, 2)}pp</td><td>${formatNumber(row.rmse_pp, 2)}pp</td>
      <td>${formatNumber(row.p90_abs_error_pp, 2)}pp</td><td>${formatNumber(row.traffic_lap_accuracy * 100, 2)}%</td></tr>`).join("")}</tbody>`;
}

function render() {
  const config = eventConfig();
  renderMetrics(config);
  renderHeatmap("trafficHeatmap", config.cells, config.laps, "ratio");
  renderHeatmap("residualHeatmap", config.cells, config.laps, "residual");
  renderDriverTable(config);
  renderCoefficients();
  renderValidation();
  const audited = state.view === "audit";
  $("residualPanel").hidden = audited;
  $("heatmapTitle").textContent = `${config.name} · ${
    audited ? "纯物理 2 秒比例" : "稳健视觉校准比例"
  }`;
  $("heatmapIntro").textContent = audited
    ? "本视图完全不读取参考图参数：最近物理前车上一次越过本车当前位置的时间差 ≤2 秒。missing 区间保留在整圈分母，因此结果会保守下降。"
    : `本视图使用阿布扎比冻结的 Huber 参数。${config.role}；虚线单元超过公开的 33% 判定阈值。`;
  $("viewNote").textContent = audited
    ? "审计层是物理可解释代理，不追求逐格贴图，也不形成车手因果排名。"
    : "视觉层只复刻公开图的描述性交通语义；卡塔尔是未见事件验收。";
  $("identityFooter").textContent = `${
    state.report.run_id
  } · ${state.report.source_identity.abu_dhabi_calibration.source_commit} · v17 ${
    state.report.source_identity.v17_manifest.run_id
  }`;
}

async function init() {
  try {
    const index = await loadJson("data/reference-analysis-lab/v2/manifest.json");
    const target = index.targets.find(
      (row) => row.target_id === "f1pace-reverse-engineered-v2",
    );
    if (!target) throw new Error("v2 索引中缺少 F1pace target");
    const manifest = await loadJson(target.manifest);
    const base = target.manifest.slice(0, target.manifest.lastIndexOf("/") + 1);
    const report = await loadJson(
      `${base}${manifest.report}`,
      manifest.report_sha256,
    );
    if (report.calibration?.external_zero_refit !== true) {
      throw new Error("外部验证身份不是 zero-refit，拒绝展示为事件外证据");
    }
    state.target = target;
    state.report = report;
    $("runStatus").textContent = report.status;
    $("loadStatus").textContent = "冻结产物已校验";
    $("loadStatus").classList.add("good");
    $("boundaryList").innerHTML = report.method_boundaries.map(
      (value) => `<li>${escapeHtml(value)}</li>`,
    ).join("");
    render();
  } catch (error) {
    $("loadStatus").textContent = "加载失败";
    $("loadStatus").classList.add("bad");
    document.querySelector("main").insertAdjacentHTML(
      "afterbegin",
      `<section class="panel"><strong class="bad">无法读取冻结实验：</strong> ${escapeHtml(error.message)}</section>`,
    );
    throw error;
  }
}

$("eventSelect").addEventListener("change", (event) => {
  state.event = event.target.value;
  render();
});
$("viewSelect").addEventListener("change", (event) => {
  state.view = event.target.value;
  render();
});
$("labelSelect").addEventListener("change", (event) => {
  state.labels = event.target.value;
  render();
});

init();
