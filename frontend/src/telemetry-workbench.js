import {
  bindHelpTooltips,
  createWorkbenchHelpTopics,
  dismissHelpTooltip,
  helpButton,
} from "./telemetry-help.js";
import {
  requireOfficialTeamIdentity,
} from "./official-team-colours.js?v=20260726-official-v1";

const MANIFEST_PATH = "data/telemetry-workbench/manifest.json";

// 这四个旧版遥测报告没有正式 round 字段。这里只复用仓库冻结四站配置中的
// 事件先后顺序，标签会明确写成“四站序”，不冒充 FIA 正式分站轮次。
const FROZEN_WORKBENCH_EVENT_ORDER = new Map([
  ["telemetry-explanation-2026-australia-ferrari-v7", 1],
  ["telemetry-explanation-2026-china-ferrari-v7", 2],
  ["telemetry-explanation-2026-japan-ferrari-v7", 3],
  ["telemetry-explanation-2026-miami-ferrari-v7", 4],
]);

const COMPOUND_PRESENTATION = {
  SOFT: { label: "软胎 · SOFT", order: 0, className: "compoundSoft" },
  MEDIUM: { label: "中性胎 · MEDIUM", order: 1, className: "compoundMedium" },
  HARD: { label: "硬胎 · HARD", order: 2, className: "compoundHard" },
  INTERMEDIATE: { label: "半雨胎 · INTERMEDIATE", order: 3, className: "compoundIntermediate" },
  WET: { label: "全雨胎 · WET", order: 4, className: "compoundWet" },
  UNKNOWN: { label: "配方未知 · UNKNOWN", order: 99, className: "compoundUnknown" },
};

const STINT_COMPARISON_RULES = {
  maximumReferenceTyreAgeGapLaps: 2,
  maximumPhaseMidpointFractionGap: 0.2,
  representativeStatuses: new Set([
    "identified_conditional_proxy",
    "level_only_tyre_age_slope_not_identified",
  ]),
};
const WORKBENCH_HELP_TOPICS = createWorkbenchHelpTopics(
  STINT_COMPARISON_RULES,
);

const STINT_CURVE_DASH_PATTERNS = [
  [],
  [8, 5],
  [2, 4],
  [11, 4, 2, 4],
];

const REPORT_PRODUCT_PRESENTATION = {
  race_dossier: { label: "Race Dossier", order: 0 },
  telemetry_explanation: { label: "探索性遥测档案", order: 1 },
};

const state = {
  manifest: null,
  report: null,
  curveEvidenceState: {
    status: "not_loaded",
    data: null,
    reason: null,
  },
  reportFilters: {
    product: null,
    year: null,
    meetingKey: null,
  },
  loadRequestId: 0,
  lastSuccessfulReportId: null,
};
const $ = (id) => document.getElementById(id);

let booted = false;
document.addEventListener("DOMContentLoaded", boot);
if (document.readyState !== "loading") boot();

function boot() {
  if (booted) return;
  booted = true;
  void init();
}

let stintCurveResizeTimer = null;
if (typeof window.addEventListener === "function") {
  window.addEventListener("resize", () => {
    if (stintCurveResizeTimer !== null) clearTimeout(stintCurveResizeTimer);
    stintCurveResizeTimer = setTimeout(() => {
      stintCurveResizeTimer = null;
      if (
        state.report
        && $("stintCurveExplorer")
        && !$("stintCurveExplorer").hidden
      ) {
        renderStintCurveExplorer(
          state.report,
          undefined,
          undefined,
          state.curveEvidenceState,
        );
      }
    }, 120);
  });
}

async function init() {
  try {
    bindHelpTooltips(WORKBENCH_HELP_TOPICS);
    state.manifest = await fetchJSON(MANIFEST_PATH);
    const reports = sortReportsByTime(state.manifest.reports || []);
    if (!reports.length) throw new Error("尚无可用分析报告");
    state.manifest.reports = reports;
    const initialReport = reports.find(
      (row) => (
        row.product === "race_dossier"
        && String(row.id || "").endsWith("-v17")
      ),
    ) || reports.find((row) => row.product === "race_dossier") || reports[0];
    setReportFiltersFromRef(initialReport);
    renderReportFilters(initialReport.id);
    bindReportFilterEvents();
    await loadReport(initialReport.id);
  } catch (error) {
    showLoadError(error);
  }
}

function showLoadError(error) {
  const message = error instanceof Error ? error.message : String(error);
  const lastRef = state.manifest?.reports?.find(
    (row) => row.id === state.lastSuccessfulReportId,
  );
  const banner = $("releaseBanner");
  if (lastRef && state.report) {
    setReportFiltersFromRef(lastRef);
    renderReportFilters(lastRef.id);
    document.querySelector?.("main")?.classList.remove("reportUnavailable");
    setStatus("已回退");
    banner.textContent = `报告加载失败：${message}。已回退到上一份成功报告 ${reportVersionLabel(lastRef)}，页面没有混用失败筛选与旧数据。`;
  } else {
    state.report = null;
    $("scope").textContent = "";
    $("summaryGrid").innerHTML = "";
    document.querySelector?.("main")?.classList.add("reportUnavailable");
    setStatus("不可用");
    banner.textContent = `报告数据不可用：${message}`;
  }
  banner.classList.add("visible");
}

function bindReportFilterEvents() {
  $("productSelect").addEventListener("change", () => {
    state.reportFilters = {
      product: $("productSelect").value,
      year: null,
      meetingKey: null,
    };
    void loadFirstFilteredReport().catch(showLoadError);
  });
  $("yearSelect").addEventListener("change", () => {
    state.reportFilters.year = finiteInteger($("yearSelect").value);
    state.reportFilters.meetingKey = null;
    void loadFirstFilteredReport().catch(showLoadError);
  });
  $("meetingSelect").addEventListener("change", () => {
    state.reportFilters.meetingKey = $("meetingSelect").value;
    void loadFirstFilteredReport().catch(showLoadError);
  });
  $("reportSelect").addEventListener("change", () => {
    void loadReport($("reportSelect").value).catch(showLoadError);
  });
}

async function loadFirstFilteredReport() {
  const model = buildReportFilterModel(
    state.manifest?.reports || [],
    state.reportFilters,
  );
  const ref = model.reports[0];
  if (!ref) throw new Error("当前筛选条件下没有可用报告");
  setReportFiltersFromRef(ref);
  renderReportFilters(ref.id);
  await loadReport(ref.id);
}

function setReportFiltersFromRef(ref) {
  state.reportFilters = {
    product: String(ref?.product || ""),
    year: finiteInteger(ref?.year),
    meetingKey: reportEventKey(ref),
  };
}

async function fetchJSON(path) {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) throw new Error(`${path} 加载失败（${response.status}）`);
  const contentType = response.headers.get("content-type") || "";
  if (!contentType.toLocaleLowerCase().includes("application/json")) {
    throw new Error(
      `${path} 返回了非 JSON 内容（${contentType || "未声明类型"}）；通常表示发布数据文件缺失`,
    );
  }
  try {
    return await response.json();
  } catch (error) {
    throw new Error(`${path} 不是有效 JSON：${error.message}`);
  }
}

function isV17CurveReport(report) {
  return /^race-dossier-v(?:1[7-9]|[2-9]\d)/i.test(
    String(report?.schema_version || ""),
  ) || Boolean(report?.stint_curve_evidence);
}

function curveEvidenceUnavailableState(report, status = "missing", reason = null) {
  if (!isV17CurveReport(report)) {
    return {
      status: "summary_only",
      data: null,
      reason: (
        "当前版本只发布 Stint 汇总代表配速、胎龄斜率与支持区间；"
        + "下图是汇总字段的线性投影，不是逐圈散点回归。"
      ),
    };
  }
  return {
    status,
    data: null,
    reason: reason || (
      "v17 报告声明逐圈曲线证据，但当前没有可读取的 sidecar；"
      + "禁止退回浏览器拟合或把 v16 汇总投影冒充逐圈证据。"
    ),
  };
}

function normalizeCurveEvidenceState(report, value = null) {
  if (
    value
    && typeof value === "object"
    && Object.hasOwn(value, "status")
    && (Object.hasOwn(value, "data") || !value.schema_version)
  ) {
    if (value.status !== "ready") {
      return curveEvidenceUnavailableState(
        report,
        String(value.status || "invalid"),
        value.reason || null,
      );
    }
    value = value.data;
  }
  if (!value) return curveEvidenceUnavailableState(report);
  const expectedReportId = String(report?.report_id || "");
  const actualReportId = String(value?.report_id || "");
  const contract = value?.contract || {};
  if (
    value?.status !== "available"
    || !String(value?.schema_version || "").includes("v17")
    || !Array.isArray(value?.stints)
    || !Array.isArray(value?.pairwise_comparisons)
    || contract.browser_refits_models !== false
    || contract.support_extrapolation_allowed !== false
    || (expectedReportId && actualReportId !== expectedReportId)
  ) {
    return curveEvidenceUnavailableState(
      report,
      "invalid",
      "逐圈曲线 sidecar 与 v17 只读发布合同不一致，曲线证据已停止渲染。",
    );
  }
  return { status: "ready", data: value, reason: null };
}

function safeCurveEvidencePath(value) {
  const path = String(value || "").trim().replace(/\\/g, "/");
  if (
    !path
    || path.startsWith("/")
    || /^[a-z][a-z0-9+.-]*:/i.test(path)
    || path.split("/").some((part) => part === "..")
  ) {
    return null;
  }
  return path;
}

async function loadStintCurveEvidence(report) {
  if (!isV17CurveReport(report)) return curveEvidenceUnavailableState(report);
  const reference = report?.stint_curve_evidence;
  if (reference?.status !== "available") {
    return curveEvidenceUnavailableState(
      report,
      "missing",
      "v17 报告没有发布可用的逐圈曲线 sidecar 引用。",
    );
  }
  const relativePath = safeCurveEvidencePath(reference.frontend_path);
  if (!relativePath) {
    return curveEvidenceUnavailableState(
      report,
      "invalid",
      "v17 逐圈曲线 sidecar 的 frontend_path 缺失或越出工作台数据目录。",
    );
  }
  try {
    const payload = await fetchJSON(`data/telemetry-workbench/${relativePath}`);
    return normalizeCurveEvidenceState(report, payload);
  } catch (error) {
    return curveEvidenceUnavailableState(
      report,
      "load_error",
      `逐圈曲线 sidecar 加载失败：${error instanceof Error ? error.message : String(error)}`,
    );
  }
}

function finiteInteger(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isInteger(number) && number >= 0 ? number : null;
}

function explicitReportEventOrder(row) {
  for (const value of [
    row.round,
    row.event_order,
    row.eventOrder,
    row.race_round,
  ]) {
    const order = finiteInteger(value);
    if (order !== null) return order;
  }
  const match = String(row.source_path || "").match(
    /(?:^|[/\\])round=(\d+)(?=[/\\]|$)/i,
  );
  return match ? finiteInteger(match[1]) : null;
}

function reportEventKey(row) {
  const year = finiteInteger(row.year);
  const meeting = String(row.meeting || "").trim().toLocaleLowerCase();
  return year !== null && meeting ? `${year}|${meeting}` : "";
}

function knownReportEventOrders(reports) {
  const known = new Map();
  for (const row of reports) {
    const key = reportEventKey(row);
    const order = explicitReportEventOrder(row);
    if (key && order !== null) known.set(key, order);
  }
  return known;
}

function resolvedReportEventOrder(row, knownOrders) {
  const explicit = explicitReportEventOrder(row);
  if (explicit !== null) return { value: explicit, source: "official_round" };
  const inferred = knownOrders.get(reportEventKey(row));
  if (inferred !== undefined) {
    return { value: inferred, source: "same_event_round" };
  }
  const frozen = FROZEN_WORKBENCH_EVENT_ORDER.get(String(row.id || ""));
  if (frozen !== undefined) {
    return { value: frozen, source: "frozen_four_event_sequence" };
  }
  return { value: null, source: "unknown" };
}

function reportEventTimestamp(row) {
  for (const value of [
    row.event_date,
    row.race_date,
    row.start_date,
    row.start_time,
  ]) {
    if (!value) continue;
    const timestamp = Date.parse(String(value));
    if (Number.isFinite(timestamp)) return timestamp;
  }
  return null;
}

function reportVersion(row) {
  const match = String(row.id || "").match(/-v(\d+)$/);
  return match ? finiteInteger(match[1]) ?? -1 : -1;
}

function compareText(left, right) {
  const a = String(left || "");
  const b = String(right || "");
  if (a === b) return 0;
  return a < b ? -1 : 1;
}

function sortReportsByTime(reports) {
  const rows = Array.isArray(reports) ? reports.slice() : [];
  const knownOrders = knownReportEventOrders(rows);
  const enriched = rows.map((row, originalIndex) => ({
    row,
    originalIndex,
    year: finiteInteger(row.year) ?? -1,
    timestamp: reportEventTimestamp(row),
    eventOrder: resolvedReportEventOrder(row, knownOrders).value,
    version: reportVersion(row),
  }));
  enriched.sort((a, b) => {
    if (a.year !== b.year) return b.year - a.year;
    if (
      a.timestamp !== null
      && b.timestamp !== null
      && a.timestamp !== b.timestamp
    ) {
      return b.timestamp - a.timestamp;
    }
    if (
      a.eventOrder !== null
      && b.eventOrder !== null
      && a.eventOrder !== b.eventOrder
    ) {
      return b.eventOrder - a.eventOrder;
    }
    if (a.eventOrder === null && b.eventOrder !== null) return 1;
    if (a.eventOrder !== null && b.eventOrder === null) return -1;
    const meeting = compareText(a.row.meeting, b.row.meeting);
    if (meeting !== 0) return meeting;
    if (a.version !== b.version) return b.version - a.version;
    const product = compareText(a.row.product, b.row.product);
    if (product !== 0) return product;
    const id = compareText(a.row.id, b.row.id);
    return id !== 0 ? id : a.originalIndex - b.originalIndex;
  });
  return enriched.map(({ row }) => row);
}

function productPresentation(product) {
  return REPORT_PRODUCT_PRESENTATION[product] || {
    label: String(product || "其他报告"),
    order: 90,
  };
}

function buildReportFilterModel(reports, filters = {}) {
  // 必须先对完整 manifest 排序，再逐级筛选。这样同场 v16 即使自身没有
  // round 字段，仍能从 v15 同场报告继承 R24，而不会在筛选后退化为字母序。
  const sorted = sortReportsByTime(Array.isArray(reports) ? reports : []);
  const knownOrders = knownReportEventOrders(sorted);
  const productKeys = [...new Set(sorted.map((row) => String(row.product || "")))]
    .filter(Boolean)
    .sort((a, b) => (
      productPresentation(a).order - productPresentation(b).order
      || compareText(a, b)
    ));
  const requestedProduct = String(filters.product || "");
  const product = productKeys.includes(requestedProduct)
    ? requestedProduct
    : productKeys[0] || "";
  const productRows = sorted.filter((row) => String(row.product || "") === product);
  const years = [...new Set(productRows
    .map((row) => finiteInteger(row.year))
    .filter((value) => value !== null))]
    .sort((a, b) => b - a);
  const requestedYear = finiteInteger(filters.year);
  const year = years.includes(requestedYear) ? requestedYear : years[0] ?? null;
  const yearRows = productRows.filter((row) => finiteInteger(row.year) === year);
  const meetingSeen = new Set();
  const meetings = [];
  for (const row of yearRows) {
    const key = reportEventKey(row);
    if (!key || meetingSeen.has(key)) continue;
    meetingSeen.add(key);
    meetings.push({
      key,
      ref: row,
      label: meetingOptionLabel(row, knownOrders),
    });
  }
  const requestedMeetingKey = String(filters.meetingKey || "");
  const meetingKey = meetings.some((entry) => entry.key === requestedMeetingKey)
    ? requestedMeetingKey
    : meetings[0]?.key || "";
  const reportRows = yearRows.filter((row) => reportEventKey(row) === meetingKey);
  const products = productKeys.map((value) => ({
    value,
    label: productPresentation(value).label,
    count: sorted.filter((row) => String(row.product || "") === value).length,
  }));
  const yearOptions = years.map((value) => {
    const rows = productRows.filter((row) => finiteInteger(row.year) === value);
    return {
      value,
      reports: rows.length,
      events: new Set(rows.map(reportEventKey).filter(Boolean)).size,
    };
  });
  return {
    sorted,
    knownOrders,
    product,
    products,
    productRows,
    year,
    years: yearOptions,
    yearRows,
    meetingKey,
    meetings,
    reports: reportRows,
  };
}

function reportVersionLabel(row) {
  const version = String(row.id || "").match(/-(v\d+)$/)?.[1] || "未标版本";
  if (row.product === "race_dossier" && version === "v17") {
    return `${version} · 70 场逐圈散点与条件平衡正式批次`;
  }
  if (row.product === "race_dossier" && version === "v16") {
    return `${version} · 单场试点，非全量发布`;
  }
  if (row.product === "race_dossier" && version === "v15") {
    return `${version} · 70 场批次（逐场发布门）`;
  }
  if (row.product === "telemetry_explanation") {
    return `${version} · 探索性遥测档案`;
  }
  return version;
}

function meetingOptionLabel(row, knownOrders) {
  const chronology = resolvedReportEventOrder(row, knownOrders);
  const prefix = chronology.value === null
    ? ""
    : chronology.source === "frozen_four_event_sequence"
      ? `四站序 ${String(chronology.value).padStart(2, "0")} · `
      : `R${String(chronology.value).padStart(2, "0")} · `;
  return `${prefix}${row.meeting || row.title || row.id}`;
}

function renderReportFilters(selectedReportId) {
  const model = buildReportFilterModel(
    state.manifest?.reports || [],
    state.reportFilters,
  );
  state.reportFilters = {
    product: model.product,
    year: model.year,
    meetingKey: model.meetingKey,
  };
  $("productSelect").innerHTML = model.products
    .map((entry) => `<option value="${escapeHTML(entry.value)}">${escapeHTML(`${entry.label}（${entry.count} 份）`)}</option>`)
    .join("");
  $("productSelect").value = model.product;
  $("yearSelect").innerHTML = model.years
    .map((entry) => `<option value="${entry.value}">${entry.value} 赛季 · ${entry.events} 场</option>`)
    .join("");
  $("yearSelect").value = String(model.year ?? "");
  $("meetingSelect").innerHTML = model.meetings
    .map((entry) => `<option value="${escapeHTML(entry.key)}">${escapeHTML(entry.label)}</option>`)
    .join("");
  $("meetingSelect").value = model.meetingKey;
  renderSelect(model.reports, selectedReportId);
  const activeId = model.reports.some((row) => row.id === selectedReportId)
    ? selectedReportId
    : model.reports[0]?.id || "";
  $("reportSelect").value = activeId;
  $("filterSummary").textContent = [
    `${model.yearRows.length} 份赛季报告`,
    `${model.meetings.length} 场比赛`,
    `当前分站 ${model.reports.length} 个版本`,
  ].join(" · ");
  return model;
}

function renderSelect(reports, selectedReportId = null) {
  $("reportSelect").innerHTML = reports
    .map((row) => {
      const selected = row.id === selectedReportId ? " selected" : "";
      return `<option value="${escapeHTML(row.id)}"${selected}>${escapeHTML(reportVersionLabel(row))}</option>`;
    })
    .join("");
}

async function loadReport(id) {
  const ref = state.manifest.reports.find((row) => row.id === id);
  if (!ref) throw new Error(`报告不存在：${id}`);
  const requestId = ++state.loadRequestId;
  setStatus("加载中");
  const report = await fetchJSON(`data/telemetry-workbench/${ref.path}`);
  const curveEvidenceState = await loadStintCurveEvidence(report);
  if (requestId !== state.loadRequestId) return;
  state.report = report;
  state.curveEvidenceState = curveEvidenceState;
  state.lastSuccessfulReportId = id;
  setReportFiltersFromRef(ref);
  renderReportFilters(id);
  render(ref, state.report, curveEvidenceState);
  setStatus("已加载");
}

function isRaceDossier(report) {
  return String(report.schema_version || "").startsWith("race-dossier-");
}

function render(
  ref,
  report,
  curveEvidenceState = normalizeCurveEvidenceState(report),
) {
  dismissHelpTooltip();
  document.querySelector?.("main")?.classList.remove("reportUnavailable");
  const scope = report.scope || ref;
  validateReportTeamColours(ref, report);
  $("sectionNav").dataset.product = isRaceDossier(report)
    ? "race_dossier"
    : "telemetry_explanation";
  $("scope").textContent = [scope.year, scope.meeting, scope.session, scope.team]
    .filter(Boolean)
    .join(" · ");
  renderReleaseBanner(report);
  renderSummary(report);
  renderRaceConclusion(report);
  renderDataFunnel(report);
  renderFuelState(report);
  renderStintDossiers(report, curveEvidenceState);
  renderDecomposition(report);
  renderSegmentChart(segmentRows(report));
  renderAnomalies(report);
  renderModes(report);
  renderRatings(report);
  renderEpisodes(report);
  renderBoundaries(report);
}

function renderReleaseBanner(report) {
  const release = report.release_status || report.methodological_status || "exploratory";
  const gate = report.publication_gate;
  const banner = $("releaseBanner");
  if (isRaceDossier(report)) {
    const reasons = gate?.reasons?.length ? ` 未通过项：${gate.reasons.join("；")}` : "";
    if (report.validation_scope === "single_event_real_data_pilot_not_full_2023_2025_release") {
      banner.textContent = `发布边界：单场真实数据 pilot，只验收 v16 Stint 代表配速；不是 2023–2025 共 70 场正式发布。${gate?.passed ? "本场完整比赛覆盖门通过。" : `本场仍为审计代理。${reasons}`}`;
    } else if (gate?.passed) {
      banner.textContent = `发布状态：${release}。完整四队比赛覆盖门已通过。`;
    } else if (gate?.partial_team_conclusion_allowed) {
      const teams = gate.publishable_teams?.length ? gate.publishable_teams.join("、") : "通过模块门的车队";
      banner.textContent = `发布边界：${release}。完整四队门未通过；仅 ${teams} 可发布车队模块结论，其余内容保持审计代理。${reasons}`;
    } else {
      banner.textContent = `发布边界：${release}。本页仅展示审计代理，不发布整场性能结论。${reasons}`;
    }
  } else {
    banner.textContent = `发布边界：${release}。遥测差异是条件代理，不是物理故障诊断。`;
  }
  banner.classList.add("visible");
}

function renderSummary(report) {
  let metrics;
  if (isRaceDossier(report)) {
    const coverage = report.coverage || {};
    const stints = report.stint_dossiers || [];
    const validStints = stints.filter((row) => row.status === "valid").length;
    const strictConfirmedStints = stints.filter((row) => row.strict_confirmation?.status === "confirmed").length;
    const inclusive = report.inclusive_robust_model_audit || {};
    const inclusiveEnabled = Boolean(inclusive.status && inclusive.status !== "not_enabled");
    const conditionalLaps = Number(report.simulation_proxy_audit?.ablation?.conditional_proxy_eligible_laps);
    const observedFieldLaps = Number(coverage.observed_field_laps);
    const reportingTeams = coverage.teams || [];
    const ledgerClosed = report.event_ledger?.primary_disposition_closure?.passed;
    if (inclusiveEnabled) {
      const candidateLaps = Number(inclusive.candidate_laps);
      const effectiveLaps = Number(inclusive.effective_weight_mass);
      const strictLaps = Number(inclusive.strict_confirmation_laps);
      metrics = [
        ["全场观测圈", formatNumber(observedFieldLaps), "全场输入", "control_sample"],
        ["物理可读候选圈", formatNumber(candidateLaps), `${formatPercent(safeRatio(candidateLaps, observedFieldLaps))} 可读`, "effective_weight"],
        ["有效权重 / 严格复核", `${formatNumber(effectiveLaps)} / ${formatNumber(strictLaps)}`, "可靠性质量 / 独立确认", "effective_weight"],
        ["有效 / 严格确认 Stint", `${formatNumber(validStints)} / ${formatNumber(strictConfirmedStints)}`, ledgerClosed ? "账本闭合" : "账本未闭合", "strict_confirmation"],
      ];
    } else {
      metrics = [
        ["全场观测圈", formatNumber(observedFieldLaps), "全场输入", "control_sample"],
        ["条件可分析圈", formatNumber(conditionalLaps), `${formatPercent(safeRatio(conditionalLaps, observedFieldLaps))} 可分析`, "conditional_pace"],
        ["全场有效 Stint", `${formatNumber(validStints)} / ${formatNumber(stints.length)}`, "逐 Stint 门控", "strict_confirmation"],
        ["四队发布模块", `${formatNumber(report.publication_gate?.publishable_teams?.length || 0)} / ${formatNumber(reportingTeams.length)}`, ledgerClosed ? "账本闭合" : "账本未闭合", "control_sample"],
      ];
    }
  } else {
    const quality = report.data_quality || {};
    const comparison = report.team_difference?.comparability || report.comparability || {};
    metrics = [
      ["可比圈对", formatNumber(comparison.matched_pairs), comparison.status || "匹配后的条件代理"],
      ["赛段覆盖", formatPercent(quality.segment_coverage ?? comparison.segment_coverage), "距离域有效覆盖"],
      ["异常 Episode", String(anomalyRows(report).length), "需要持续性与多证据支持"],
      ["策略模式", String(modeRows(report).length), "中性标签，不等于真实内部状态"],
    ];
  }
  $("summaryGrid").innerHTML = metrics
    .map(([label, value, note, topic]) => `<div class="metric"><div class="label">${escapeHTML(label)}</div><div class="value">${escapeHTML(value)}</div><div class="note"><span>${escapeHTML(note)}</span>${topic ? helpButton(topic, `解释${label}`) : ""}</div></div>`)
    .join("");
}

function renderRaceConclusion(report) {
  const panel = $("raceConclusionPanel");
  if (!isRaceDossier(report)) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;
  const qualifying = report.qualifying_analysis?.team_order || [];
  const race = report.race_analysis?.descriptive_team_order || [];
  const resultRows = report.result_impact_audit?.classification_proxy_comparison || [];
  const resultByTeam = new Map(resultRows.map((row) => [row.team, row]));
  const gate = report.publication_gate || {};
  const inclusive = report.inclusive_robust_model_audit || {};
  const qualifyingLeader = qualifying[0];
  const raceLeader = race[0];
  const qualifyingSecond = qualifying[1];
  const raceSecond = race[1];
  const outcomeSummary = resultRows.length
    ? resultRows.map((row) => `${row.team}：${resultAuditLabel(row)}`).join("；")
    : "没有可用的成绩代理比较；FIA 最终分类尚未接入本报告。";
  const releaseText = gate.passed
    ? "完整四队发布门通过，可发布本场排位与正赛条件结论。"
    : gate.partial_team_conclusion_allowed
      ? `只允许 ${gate.publishable_teams?.join("、") || "已通过模块"} 的部分结论。`
      : "整场发布门未过；下列数值只用于审计，不能发布车辆全序。";
  const qualifyingGap = qualifyingLeader && qualifyingSecond
    ? Number(qualifyingSecond.value) - Number(qualifyingLeader.value)
    : null;
  const raceGap = raceLeader && raceSecond
    ? Number(raceSecond.value) - Number(raceLeader.value)
    : null;
  $("raceConclusion").innerHTML = [
    conclusionCard("发布判断", releaseText),
    conclusionCard(
      "排位潜力",
      qualifyingLeader
        ? `${qualifyingLeader.team} 的代表单圈最快${Number.isFinite(qualifyingGap) ? `，领先第二名 ${qualifyingGap.toFixed(3)} 秒` : ""}；排位不与正赛合并。`
        : "没有足够排位数据。",
    ),
    conclusionCard(
      "正赛同条件配速",
      raceLeader
        ? `${raceLeader.team} 的长距离条件配速最快${Number.isFinite(raceGap) ? `，第二名差 ${raceGap.toFixed(3)} 秒/圈` : ""}。`
        : "没有足够正赛条件配速数据。",
      "conditional_pace",
    ),
    conclusionCard(
      "成绩兑现审计",
      `${outcomeSummary}。classification_proxy（非 FIA 分类）；主要因素声明 ${report.result_impact_audit?.major_factor_claims?.length || 0} 条。`,
      "classification_proxy",
    ),
  ].join("");

  const qualifyingLeaderValue = qualifying.length
    ? Math.min(...qualifying.map((row) => Number(row.value)).filter(Number.isFinite))
    : null;
  $("qualifyingTable").innerHTML = qualifying.length
    ? `<table><thead><tr><th>排名</th><th>车队</th><th>代表圈时</th><th>距最快</th><th>80% 排名区间</th><th>P1 概率</th></tr></thead><tbody>${qualifying.map((row) => `<tr><td>P${formatNumber(row.rank)}</td><td>${escapeHTML(row.team)}</td><td>${formatSeconds(row.value, 3)}</td><td>${Number.isFinite(qualifyingLeaderValue) ? `+${(Number(row.value) - qualifyingLeaderValue).toFixed(3)}s` : "—"}</td><td>${escapeHTML(formatIntervalRaw(row.rank_interval_80))}</td><td>${formatPercent(row.p_rank_1)}</td></tr>`).join("")}</tbody></table>`
    : emptyEvidence("没有足够排位单圈。正赛结果不会代替排位潜力。 ");

  const raceLeaderValue = race.length
    ? Math.min(...race.map((row) => Number(row.value)).filter(Number.isFinite))
    : null;
  $("raceOutcomeTable").innerHTML = race.length
    ? `<table><thead><tr><th>性能排名</th><th>车队</th><th>条件配速</th><th>距最快</th><th>80% 排名区间</th><th>成绩代理</th><th>兑现差异</th></tr></thead><tbody>${race.map((row) => {
      const result = resultByTeam.get(row.team);
      return `<tr><td>P${formatNumber(row.rank)}</td><td>${escapeHTML(row.team)}</td><td>${formatSigned(row.value, 3)}s/圈</td><td>${Number.isFinite(raceLeaderValue) ? `+${(Number(row.value) - raceLeaderValue).toFixed(3)}s/圈` : "—"}</td><td>${escapeHTML(formatIntervalRaw(row.rank_interval_80))}</td><td>${result ? `P${formatNumber(result.classification_proxy_rank)}` : "—"}</td><td>${escapeHTML(result ? resultAuditLabel(result) : "无成绩代理")}</td></tr>`;
    }).join("")}</tbody></table>`
    : emptyEvidence("没有足够正赛长距离条件配速。 ");

  panel.dataset.modelStatus = inclusive.status || "unknown";
}

function renderFuelState(report) {
  const panel = $("fuelStatePanel");
  if (!isRaceDossier(report)) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;
  const fuel = report.simulation_proxy_audit?.fuel || {};
  const assumptions = fuel.assumptions || {};
  const fuelKg = assumptions.initial_fuel_kg || {};
  const timePerKg = assumptions.lap_time_s_per_kg || {};
  const stints = report.stint_dossiers || [];
  const validStints = stints.filter((row) => row.status === "valid").length;
  const strictStints = stints.filter((row) => row.strict_confirmation?.status === "confirmed").length;
  const damageEvidence = (report.event_ledger?.entries || []).filter((row) => /damage|collision|accident/i.test(String(row.event_type)));
  $("fuelStateGrid").innerHTML = [
    stateCard(
      "燃油质量补偿",
      fuel.enabled ? "已启用数据代理" : "不可用",
      fuel.enabled
        ? `起步质量场景 ${formatNumber(fuelKg.low)}/${formatNumber(fuelKg.base)}/${formatNumber(fuelKg.high)} kg；圈时敏感性 ${formatNumber(timePerKg.low)}/${formatNumber(timePerKg.base)}/${formatNumber(timePerKg.high)} 秒/kg；基准补偿 ${formatRange(fuel.base_correction_range_s, 3)} 秒，敏感性全包络 ${formatRange(fuel.sensitivity_correction_range_s, 3)} 秒。`
        : "没有可用燃油代理。",
      fuel.enabled ? "proxy" : "unavailable",
      "fuel_scenario",
    ),
    stateCard(
      "轮胎配速与衰减",
      "已启用数据代理",
      `${formatNumber(validStints)} 个 Stint 有条件配速、衰减和累计秒差；${formatNumber(strictStints)} 个通过严格确认。`,
      "proxy",
      "fuel_state",
    ),
    stateCard(
      "车辆损伤 / 事故",
      damageEvidence.length ? "仅有间接事件代理" : "无直接数据源",
      damageEvidence.length
        ? `账本记录 ${damageEvidence.length} 条相关事件代理；具体损伤不可识别。`
        : "没有可识别的直接损伤来源。",
      damageEvidence.length ? "proxy" : "unavailable",
      "fuel_state",
    ),
    stateCard(
      "ERS / SOC / 动力模式",
      "不可识别",
      "无公开逐圈输入；保留在未解释残差中。",
      "unavailable",
      "fuel_state",
    ),
  ].join("");
  $("fuelComparisonNote").innerHTML = `<strong>比较口径：</strong>同油量条件代理 ${helpButton("conditional_pace", "解释同油量条件比较")}`;
}

function primaryReportTeams(report) {
  return new Set((report.coverage?.teams || [])
    .map((row) => String(row.team || "").trim())
    .filter(Boolean));
}

function teamScopeOptions(report) {
  const rows = report.stint_dossiers || [];
  const primaryTeams = primaryReportTeams(report);
  const teams = [...new Set(rows.map((row) => String(row.team || "").trim()).filter(Boolean))]
    .sort(compareText);
  const options = [];
  if (primaryTeams.size) {
    options.push({
      value: "PRIMARY",
      label: `主报告车队（${primaryTeams.size} 队）`,
    });
  }
  options.push({ value: "ALL", label: `全场所有车队（${teams.length} 队）` });
  teams.forEach((team) => options.push({ value: team, label: team }));
  return options;
}

function teamScopeLabel(report, value) {
  if (value === "PRIMARY") return `主报告 ${primaryReportTeams(report).size} 队`;
  if (value === "ALL") return "全场所有车队";
  return value || "当前范围";
}

function rowsInTeamScope(rows, report, teamScope) {
  if (teamScope === "ALL") return rows.slice();
  if (teamScope === "PRIMARY") {
    const primaryTeams = primaryReportTeams(report);
    return rows.filter((row) => primaryTeams.has(String(row.team || "")));
  }
  return rows.filter((row) => String(row.team || "") === String(teamScope || ""));
}

function filterStintsForDisplay(report, teamScope = "PRIMARY", driver = "ALL") {
  const scoped = rowsInTeamScope(report.stint_dossiers || [], report, teamScope);
  return driver === "ALL"
    ? scoped
    : scoped.filter((row) => String(row.driver || "") === String(driver));
}

function renderTeamScopeSelect(select, report, selectedValue = "PRIMARY") {
  const options = teamScopeOptions(report);
  select.innerHTML = options
    .map((entry) => `<option value="${escapeHTML(entry.value)}">${escapeHTML(entry.label)}</option>`)
    .join("");
  select.value = options.some((entry) => entry.value === selectedValue)
    ? selectedValue
    : options[0]?.value || "ALL";
}

function refreshStintDriverSelect(report, selectedDriver = "ALL") {
  const teamScope = $("stintTeamSelect").value;
  const rows = rowsInTeamScope(report.stint_dossiers || [], report, teamScope);
  const drivers = [...new Map(rows
    .sort((a, b) => compareText(a.team, b.team) || compareText(a.driver, b.driver))
    .map((row) => [row.driver, row.team])).entries()];
  const selector = $("stintDriverSelect");
  selector.innerHTML = `<option value="ALL">范围内全部车手（${drivers.length} 人）</option>${drivers
    .map(([driver, team]) => `<option value="${escapeHTML(driver)}">${escapeHTML(driver)} · ${escapeHTML(team)}</option>`)
    .join("")}`;
  selector.value = drivers.some(([driver]) => driver === selectedDriver)
    ? selectedDriver
    : "ALL";
}

function renderStintDossiers(
  report,
  curveEvidenceState = normalizeCurveEvidenceState(report),
) {
  const panel = $("stintPanel");
  if (!isRaceDossier(report)) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;
  const teamSelector = $("stintTeamSelect");
  const driverSelector = $("stintDriverSelect");
  const orderSelector = $("stintOrderSelect");
  renderTeamScopeSelect(teamSelector, report, "PRIMARY");
  refreshStintDriverSelect(report, "ALL");
  orderSelector.value = "RACE_SEQUENCE";
  teamSelector.onchange = () => {
    refreshStintDriverSelect(report, "ALL");
    renderStintTable(report);
    renderStintCurveExplorer(
      report,
      undefined,
      undefined,
      curveEvidenceState,
    );
  };
  driverSelector.onchange = () => renderStintTable(report);
  orderSelector.onchange = () => renderStintTable(report);
  renderStintCurveExplorer(
    report,
    undefined,
    undefined,
    curveEvidenceState,
  );
  renderStintTable(report);
}

function normalizedCompound(value) {
  const compound = String(value || "UNKNOWN").trim().toUpperCase();
  return compound || "UNKNOWN";
}

function compoundPresentation(compound) {
  return COMPOUND_PRESENTATION[compound] || {
    label: `其他配方 · ${compound}`,
    order: 90,
    className: "compoundUnknown",
  };
}

function stintDisplayPace(row) {
  const referencePace = Number(row.representative_tyre_age_pace_s);
  if (Number.isFinite(referencePace) && referencePace > 0) return referencePace;
  const legacyPace = Number(row.stable_pace_s);
  return Number.isFinite(legacyPace) && legacyPace > 0
    ? legacyPace
    : Number.POSITIVE_INFINITY;
}

function groupStintsForDisplay(rows) {
  const grouped = new Map();
  for (const row of Array.isArray(rows) ? rows : []) {
    const compound = normalizedCompound(row.compound);
    if (!grouped.has(compound)) grouped.set(compound, []);
    grouped.get(compound).push(row);
  }
  return [...grouped.entries()]
    .map(([compound, compoundRows]) => ({
      compound,
      presentation: compoundPresentation(compound),
      rows: compoundRows.slice().sort((a, b) => {
        const paceA = stintDisplayPace(a);
        const paceB = stintDisplayPace(b);
        if (paceA !== paceB) return paceA < paceB ? -1 : 1;
        const age = Number(a.representative_tyre_age_laps)
          - Number(b.representative_tyre_age_laps);
        if (Number.isFinite(age) && age !== 0) return age;
        const team = compareText(a.team, b.team);
        if (team !== 0) return team;
        const driver = compareText(a.driver, b.driver);
        if (driver !== 0) return driver;
        return Number(a.stint_number) - Number(b.stint_number);
      }),
    }))
    .sort((a, b) => (
      a.presentation.order - b.presentation.order
      || compareText(a.compound, b.compound)
    ));
}

function stintComparisonSnapshot(row, raceLapSpan) {
  if (row?.status !== "valid") {
    return { row, reason: "Stint 自身发布门未通过" };
  }
  const pace = Number(row.representative_tyre_age_pace_s);
  const age = Number(row.representative_tyre_age_laps);
  if (!Number.isFinite(pace) || pace <= 0 || !Number.isFinite(age)) {
    return { row, reason: "缺少 v16 代表配速或参考胎龄" };
  }
  if (
    !STINT_COMPARISON_RULES.representativeStatuses.has(
      String(row.representative_tyre_age_pace_status || ""),
    )
  ) {
    return { row, reason: "代表配速状态不满足 v16 比较门" };
  }
  const lapStart = Number(row.lap_start);
  const lapEnd = Number(row.lap_end);
  if (
    !Number.isFinite(lapStart)
    || !Number.isFinite(lapEnd)
    || !Number.isFinite(raceLapSpan)
    || raceLapSpan <= 0
  ) {
    return { row, reason: "比赛阶段不可计算" };
  }
  return {
    row,
    pace,
    age,
    phase: ((lapStart + lapEnd) / 2) / raceLapSpan,
    reason: null,
  };
}

function canJoinStintComparisonWindow(windowItems, candidate) {
  const items = [...windowItems, candidate];
  const ages = items.map((item) => item.age);
  const phases = items.map((item) => item.phase);
  return (
    Math.max(...ages) - Math.min(...ages)
      <= STINT_COMPARISON_RULES.maximumReferenceTyreAgeGapLaps
    && Math.max(...phases) - Math.min(...phases)
      <= STINT_COMPARISON_RULES.maximumPhaseMidpointFractionGap
  );
}

function buildComparableStintWindows(report, rows) {
  const raceLapSpan = Number(report?.coverage?.race_lap_span);
  return groupStintsForDisplay(rows).map((compoundGroup) => {
    const snapshots = compoundGroup.rows.map(
      (row) => stintComparisonSnapshot(row, raceLapSpan),
    );
    const eligible = snapshots
      .filter((item) => item.reason === null)
      .sort((a, b) => (
        a.phase - b.phase
        || a.age - b.age
        || a.pace - b.pace
        || compareText(a.row.team, b.row.team)
        || compareText(a.row.driver, b.row.driver)
        || Number(a.row.stint_number) - Number(b.row.stint_number)
      ));
    const rawWindows = [];
    for (const candidate of eligible) {
      const window = rawWindows.find(
        (items) => canJoinStintComparisonWindow(items, candidate),
      );
      if (window) window.push(candidate);
      else rawWindows.push([candidate]);
    }
    const windows = rawWindows
      .map((items) => {
        const sorted = items.slice().sort((a, b) => (
          a.pace - b.pace
          || compareText(a.row.team, b.row.team)
          || compareText(a.row.driver, b.row.driver)
          || Number(a.row.stint_number) - Number(b.row.stint_number)
        ));
        const ages = sorted.map((item) => item.age);
        const phases = sorted.map((item) => item.phase);
        return {
          rows: sorted.map((item) => item.row),
          baseline: sorted[0].row,
          baselinePace: sorted[0].pace,
          ageSpan: Math.max(...ages) - Math.min(...ages),
          phaseSpan: Math.max(...phases) - Math.min(...phases),
        };
      })
      .sort((a, b) => (
        a.baselinePace - b.baselinePace
        || Number(a.baseline.stint_number) - Number(b.baseline.stint_number)
        || compareText(a.baseline.driver, b.baseline.driver)
      ))
      .map((window, index, allWindows) => ({
        ...window,
        index: index + 1,
        count: allWindows.length,
      }));
    return {
      ...compoundGroup,
      windows,
      auditRows: snapshots.filter((item) => item.reason !== null),
    };
  });
}

function medianFinite(values) {
  const finite = (Array.isArray(values) ? values : [])
    .map(Number)
    .filter(Number.isFinite)
    .sort((a, b) => a - b);
  if (!finite.length) return null;
  const middle = Math.floor(finite.length / 2);
  return finite.length % 2
    ? finite[middle]
    : (finite[middle - 1] + finite[middle]) / 2;
}

function clampNumber(value, minimum, maximum) {
  return Math.min(maximum, Math.max(minimum, value));
}

function strictFiniteNumber(value) {
  if (value === null || value === undefined || value === "") return null;
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

function finiteInterval(value) {
  if (!Array.isArray(value) || value.length < 2) return null;
  const lower = strictFiniteNumber(value[0]);
  const upper = strictFiniteNumber(value[1]);
  if (lower === null || upper === null) return null;
  return lower <= upper ? [lower, upper] : [upper, lower];
}

function stintCurveSnapshot(row) {
  const pace = strictFiniteNumber(row?.representative_tyre_age_pace_s);
  const referenceAge = strictFiniteNumber(row?.representative_tyre_age_laps);
  const slope = strictFiniteNumber(row?.degradation_s_per_tyre_lap);
  const support = finiteInterval(row?.representative_tyre_age_support_laps);
  if (
    row?.status !== "valid"
    || String(row?.representative_tyre_age_pace_status || "")
      !== "identified_conditional_proxy"
    || pace === null
    || pace <= 0
    || referenceAge === null
    || slope === null
    || !support
    || support[1] <= support[0]
    || referenceAge < support[0]
    || referenceAge > support[1]
  ) {
    return null;
  }
  return {
    key: [
      row.team,
      row.driver,
      row.stint_number,
      normalizedCompound(row.compound),
    ].join("|"),
    row,
    pace,
    referenceAge,
    slope,
    support,
    fuelSlopeInterval: finiteInterval(
      row.fuel_sensitivity_degradation_interval_s_per_tyre_lap,
    ),
    fuelPaceInterval: finiteInterval(
      row.representative_tyre_age_pace_fuel_sensitivity_interval_s,
    ),
  };
}

function buildStintCurveWindowModel(compound, window) {
  const series = window.rows
    .map(stintCurveSnapshot)
    .filter(Boolean);
  const commonAgeMinimum = series.length
    ? Math.max(...series.map((item) => item.support[0]))
    : null;
  const commonAgeMaximum = series.length
    ? Math.min(...series.map((item) => item.support[1]))
    : null;
  const distinctTeams = new Set(series.map((item) => String(item.row.team || "")));
  const phaseMidpoints = series.map((item) => (
    (Number(item.row.lap_start) + Number(item.row.lap_end)) / 2
  ));
  const referenceAges = series.map((item) => item.referenceAge);
  return {
    key: `${compound}:${series.map((item) => item.key).sort(compareText).join("~")}`,
    compound,
    index: window.index,
    count: window.count,
    sourceWindow: window,
    series,
    distinctTeams: distinctTeams.size,
    commonAgeMinimum,
    commonAgeMaximum,
    commonAgeSpan: Number.isFinite(commonAgeMinimum)
      && Number.isFinite(commonAgeMaximum)
      ? commonAgeMaximum - commonAgeMinimum
      : null,
    phaseMidpointMinimum: phaseMidpoints.length ? Math.min(...phaseMidpoints) : null,
    phaseMidpointMaximum: phaseMidpoints.length ? Math.max(...phaseMidpoints) : null,
    referenceAgeMinimum: referenceAges.length ? Math.min(...referenceAges) : null,
    referenceAgeMaximum: referenceAges.length ? Math.max(...referenceAges) : null,
  };
}

function curveWindowScore(window) {
  const commonSupport = Number.isFinite(window.commonAgeSpan)
    ? Math.max(0, window.commonAgeSpan)
    : 0;
  return window.distinctTeams * 10000 + window.series.length * 100 + commonSupport;
}

function preferredCurveWindow(windows) {
  return (Array.isArray(windows) ? windows.slice() : [])
    .sort((a, b) => (
      curveWindowScore(b) - curveWindowScore(a)
      || a.index - b.index
    ))[0] || null;
}

function sampleLinearCurve(minimum, maximum, valueAt) {
  if (
    !Number.isFinite(minimum)
    || !Number.isFinite(maximum)
    || maximum <= minimum
  ) {
    return [];
  }
  const steps = 32;
  return Array.from({ length: steps + 1 }, (_, index) => {
    const x = minimum + (maximum - minimum) * index / steps;
    return { x, y: valueAt(x) };
  });
}

function extremeCurveSeries(series, key, direction) {
  return (Array.isArray(series) ? series.slice() : [])
    .filter((item) => Number.isFinite(Number(item[key])))
    .sort((a, b) => (
      direction * (Number(a[key]) - Number(b[key]))
      || compareText(a.row.team, b.row.team)
      || compareText(a.row.driver, b.row.driver)
      || Number(a.row.stint_number) - Number(b.row.stint_number)
    ))[0] || null;
}

function fuelSlopeOrderingStatus(candidate, series, direction) {
  if (!candidate?.fuelSlopeInterval) return "燃油情景排序未测试";
  const others = series.filter((item) => item !== candidate);
  if (!others.length || others.some((item) => !item.fuelSlopeInterval)) {
    return "燃油情景排序未测试";
  }
  const separated = direction === "minimum"
    ? candidate.fuelSlopeInterval[1]
      < Math.min(...others.map((item) => item.fuelSlopeInterval[0]))
    : candidate.fuelSlopeInterval[0]
      > Math.max(...others.map((item) => item.fuelSlopeInterval[1]));
  return separated
    ? "燃油情景区间分离"
    : "燃油情景区间重叠，排序未确认";
}

function buildSummaryStintCurveExplorerModel(
  report,
  rows,
  selectedCompound = null,
  selectedWindowKey = null,
) {
  const curveGroups = buildComparableStintWindows(report, rows)
    .map((group) => ({
      compound: group.compound,
      presentation: group.presentation,
      windows: group.windows
        .map((window) => buildStintCurveWindowModel(group.compound, window))
        .filter((window) => window.series.length),
    }))
    .filter((group) => group.windows.length);
  const groups = curveGroups
    .map((group) => {
      const windows = group.windows
        .filter((window) => window.distinctTeams >= 2)
        .map((window, index, allWindows) => ({
          ...window,
          crossTeamIndex: index + 1,
          crossTeamCount: allWindows.length,
        }));
      return { ...group, windows };
    })
    .filter((group) => group.windows.length);
  if (!groups.length) {
    return {
      status: "unavailable",
      reason: curveGroups.length
        ? "当前范围没有至少两个不同车队同时进入的真实可比窗口。"
        : "当前报告缺少共同参考胎龄的代表配速、衰减斜率或支持区间。",
      groups,
      series: [],
    };
  }

  const requestedCompound = selectedCompound
    ? normalizedCompound(selectedCompound)
    : null;
  let selectedGroup = groups.find((group) => group.compound === requestedCompound);
  if (!selectedGroup) {
    selectedGroup = groups.slice().sort((a, b) => (
      curveWindowScore(preferredCurveWindow(b.windows))
        - curveWindowScore(preferredCurveWindow(a.windows))
      || a.presentation.order - b.presentation.order
      || compareText(a.compound, b.compound)
    ))[0];
  }
  let selectedWindow = selectedGroup.windows.find(
    (window) => window.key === selectedWindowKey,
  );
  if (!selectedWindow) selectedWindow = preferredCurveWindow(selectedGroup.windows);

  const commonAgeMinimum = selectedWindow.commonAgeMinimum;
  const commonAgeMaximum = selectedWindow.commonAgeMaximum;
  const commonSupportAvailable = (
    Number.isFinite(commonAgeMinimum)
    && Number.isFinite(commonAgeMaximum)
    && commonAgeMaximum > commonAgeMinimum
  );
  const referenceMedian = medianFinite(
    selectedWindow.series.map((item) => item.referenceAge),
  );
  const anchorAge = commonSupportAvailable && Number.isFinite(referenceMedian)
    ? clampNumber(referenceMedian, commonAgeMinimum, commonAgeMaximum)
    : null;
  const series = commonSupportAvailable
    ? selectedWindow.series.map((item) => ({
      ...item,
      paceAtAnchor: item.pace + item.slope * (anchorAge - item.referenceAge),
      degradationPoints: sampleLinearCurve(
        commonAgeMinimum,
        commonAgeMaximum,
        (age) => item.slope * (age - anchorAge),
      ),
      pacePoints: sampleLinearCurve(
        commonAgeMinimum,
        commonAgeMaximum,
        (age) => item.pace + item.slope * (age - item.referenceAge),
      ),
    }))
    : selectedWindow.series.slice();
  const degradationSlowest = extremeCurveSeries(series, "slope", 1);
  const degradationFastest = extremeCurveSeries(series, "slope", -1);
  const paceFastest = extremeCurveSeries(series, "paceAtAnchor", 1);
  const paceSlowest = extremeCurveSeries(series, "paceAtAnchor", -1);
  const publicationReady = (
    report?.inclusive_robust_model_audit?.status === "accepted_crossfit_proxy"
    && report?.publication_gate?.passed === true
  );
  let status = "ready";
  let reason = null;
  if (!publicationReady) {
    status = "audit_only";
    reason = "本场包容性主模型或发布门未通过，只能保留曲线审计。";
  } else if (selectedWindow.distinctTeams < 2) {
    status = "single_team";
    reason = "跨车队曲线至少需要两个不同车队；请选择主报告车队或全场范围。";
  } else if (!commonSupportAvailable) {
    status = "no_common_support";
    reason = "这些 Stint 没有共同胎龄支持区间，禁止外推后比较。";
  } else if (series.length < 2) {
    status = "insufficient_series";
    reason = "当前窗口只有一条可识别斜率，无法形成最快/最慢比较。";
  }
  return {
    status,
    reason,
    groups,
    selectedGroup,
    selectedWindow,
    selectedCompound: selectedGroup.compound,
    selectedWindowKey: selectedWindow.key,
    commonAgeMinimum,
    commonAgeMaximum,
    anchorAge,
    series,
    extremes: {
      degradationSlowest,
      degradationFastest,
      paceFastest,
      paceSlowest,
    },
    degradationOrdering: {
      slowest: fuelSlopeOrderingStatus(degradationSlowest, series, "minimum"),
      fastest: fuelSlopeOrderingStatus(degradationFastest, series, "maximum"),
    },
  };
}

function curveEvidencePartialKey(row) {
  return [
    String(row?.team || row?.team_name || "").trim(),
    String(row?.driver || row?.driver_id || "").trim(),
    Number(row?.stint_number),
    normalizedCompound(row?.compound),
  ].join("|");
}

function curveEvidenceStintLookup(sidecar) {
  const lookup = new Map();
  const duplicates = new Set();
  for (const row of sidecar?.stints || []) {
    const key = curveEvidencePartialKey(row);
    if (lookup.has(key)) duplicates.add(key);
    lookup.set(key, row);
  }
  for (const key of duplicates) lookup.delete(key);
  return lookup;
}

function publishedGridValueAt(grid, age, field = "pace_s") {
  if (!Number.isFinite(Number(age))) return null;
  const points = (Array.isArray(grid) ? grid : [])
    .map((row) => ({
      x: strictFiniteNumber(row?.tyre_age_laps),
      y: strictFiniteNumber(row?.[field]),
    }))
    .filter((row) => row.x !== null && row.y !== null)
    .sort((a, b) => a.x - b.x);
  if (!points.length || age < points[0].x || age > points.at(-1).x) return null;
  const exact = points.find((row) => Math.abs(row.x - age) < 1e-9);
  if (exact) return exact.y;
  for (let index = 1; index < points.length; index += 1) {
    const left = points[index - 1];
    const right = points[index];
    if (age > right.x) continue;
    const span = right.x - left.x;
    if (span <= 0) return left.y;
    const ratio = (age - left.x) / span;
    return left.y + (right.y - left.y) * ratio;
  }
  return null;
}

function publishedBandAtAge(stability, age) {
  if (String(stability?.status || "") !== "ESTIMATED") return null;
  const lower = publishedGridValueAt(
    stability?.prediction_band,
    age,
    "lower_s",
  );
  const upper = publishedGridValueAt(
    stability?.prediction_band,
    age,
    "upper_s",
  );
  return lower === null || upper === null
    ? null
    : [Math.min(lower, upper), Math.max(lower, upper)];
}

function publishedFuelScenarioValuesAtAge(primaryFit, age) {
  const values = {};
  for (const name of ["low", "base", "high"]) {
    const scenario = primaryFit?.fuel_scenarios?.[name];
    const referencePace = strictFiniteNumber(scenario?.reference_pace_s);
    const referenceAge = strictFiniteNumber(
      primaryFit?.reference_tyre_age_laps,
    );
    const slope = strictFiniteNumber(scenario?.slope_s_per_tyre_lap);
    if (
      !String(scenario?.status || "").startsWith("identified")
      || referencePace === null
      || referenceAge === null
      || slope === null
      || !Number.isFinite(Number(age))
    ) {
      values[name] = null;
    } else {
      values[name] = referencePace + slope * (Number(age) - referenceAge);
    }
  }
  return values;
}

function finiteValuesRange(values) {
  const finite = Object.values(values || {})
    .map(strictFiniteNumber)
    .filter((value) => value !== null);
  return finite.length >= 2
    ? [Math.min(...finite), Math.max(...finite)]
    : null;
}

function evidencePairLookup(sidecar) {
  const lookup = new Map();
  const duplicates = new Set();
  for (const row of sidecar?.pairwise_comparisons || []) {
    const key = String(row?.pair_key || [
      row?.left_stint_key,
      row?.right_stint_key,
    ].filter(Boolean).sort(compareText).join("~"));
    if (lookup.has(key)) duplicates.add(key);
    lookup.set(key, row);
  }
  for (const key of duplicates) lookup.delete(key);
  return lookup;
}

function buildEvidencePairAudit(series, sidecar) {
  const lookup = evidencePairLookup(sidecar);
  const rows = [];
  for (let leftIndex = 0; leftIndex < series.length; leftIndex += 1) {
    for (let rightIndex = leftIndex + 1; rightIndex < series.length; rightIndex += 1) {
      const left = series[leftIndex];
      const right = series[rightIndex];
      const pairKey = [
        left.evidence.stint_key,
        right.evidence.stint_key,
      ].sort(compareText).join("~");
      const candidate = lookup.get(pairKey) || null;
      const leftKey = String(left.evidence.stint_key || "");
      const rightKey = String(right.evidence.stint_key || "");
      const publishedLeftKey = String(candidate?.left_stint_key || "");
      const publishedRightKey = String(candidate?.right_stint_key || "");
      const sameOrientation = Boolean(
        candidate
        && publishedLeftKey === leftKey
        && publishedRightKey === rightKey
      );
      const reversedOrientation = Boolean(
        candidate
        && publishedLeftKey === rightKey
        && publishedRightKey === leftKey
      );
      const published = sameOrientation || reversedOrientation
        ? candidate
        : null;
      rows.push({
        pairKey,
        left: reversedOrientation ? right : left,
        right: reversedOrientation ? left : right,
        published,
        status: String(published?.status || "not_comparable"),
      });
    }
  }
  return rows;
}

function buildEvidenceSeries(summarySeries, evidenceLookup, anchorAge = null) {
  return summarySeries.map((item) => {
    const evidence = evidenceLookup.get(curveEvidencePartialKey(item.row));
    if (!evidence) return { ...item, evidenceStatus: "missing", evidence: null };
    const primaryFit = evidence.primary_fit || {};
    const support = finiteInterval(primaryFit.support_tyre_age_laps);
    const primaryGrid = Array.isArray(primaryFit.prediction_grid)
      ? primaryFit.prediction_grid
      : [];
    if (
      primaryFit.status !== "IDENTIFIED"
      || !support
      || support[1] <= support[0]
      || primaryGrid.length < 2
    ) {
      return {
        ...item,
        evidenceStatus: "primary_fit_not_identified",
        evidence,
      };
    }
    const paceAtAnchor = Number.isFinite(Number(anchorAge))
      ? publishedGridValueAt(primaryGrid, Number(anchorAge))
      : null;
    const residualRange = finiteInterval(
      evidence.sample_audit?.primary_fit_residual_central_range_s,
    );
    const observedRangeAtAnchor = (
      paceAtAnchor !== null && residualRange
        ? [
          paceAtAnchor + residualRange[0],
          paceAtAnchor + residualRange[1],
        ]
        : null
    );
    const stabilityAtAnchor = paceAtAnchor !== null
      ? publishedBandAtAge(primaryFit.stability_interval, Number(anchorAge))
      : null;
    const fuelScenarioValues = paceAtAnchor !== null
      ? publishedFuelScenarioValuesAtAge(primaryFit, Number(anchorAge))
      : {};
    return {
      ...item,
      evidenceStatus: "ready",
      evidence,
      primaryFit,
      support,
      points: Array.isArray(evidence.points) ? evidence.points : [],
      confirmedShape: evidence.confirmed_shape || {},
      sampleAudit: evidence.sample_audit || {},
      conditionProfile: evidence.condition_profile || {},
      paceAtAnchor,
      observedRangeAtAnchor,
      stabilityAtAnchor,
      fuelScenarioValues,
      fuelRangeAtAnchor: finiteValuesRange(fuelScenarioValues),
    };
  });
}

function buildStintCurveExplorerModel(
  report,
  rows,
  selectedCompound = null,
  selectedWindowKey = null,
  curveEvidenceState = null,
) {
  const summary = buildSummaryStintCurveExplorerModel(
    report,
    rows,
    selectedCompound,
    selectedWindowKey,
  );
  const evidenceState = normalizeCurveEvidenceState(
    report,
    curveEvidenceState,
  );
  if (evidenceState.status === "summary_only") {
    return {
      ...summary,
      mode: "summary_projection",
      evidenceState,
      summaryOnlyReason: evidenceState.reason,
    };
  }
  if (evidenceState.status !== "ready") {
    return {
      ...summary,
      mode: "v17_evidence",
      status: "evidence_unavailable",
      reason: evidenceState.reason,
      evidenceState,
      series: [],
      pairwiseAudit: [],
      allPairsComparable: false,
      directRankingAllowed: false,
    };
  }
  if (!summary.selectedWindow || !summary.selectedGroup) {
    return {
      ...summary,
      mode: "v17_evidence",
      evidenceState,
      reason: summary.reason || "当前范围没有可连接逐圈证据的跨车队窗口。",
      pairwiseAudit: [],
      allPairsComparable: false,
      directRankingAllowed: false,
    };
  }

  const evidenceLookup = curveEvidenceStintLookup(evidenceState.data);
  const provisional = buildEvidenceSeries(
    summary.selectedWindow.series,
    evidenceLookup,
  ).filter((item) => item.evidenceStatus === "ready");
  if (provisional.length < 2) {
    return {
      ...summary,
      mode: "v17_evidence",
      status: "evidence_unavailable",
      reason: "当前窗口少于两条后端已识别的逐圈主拟合证据。",
      evidenceState,
      series: provisional,
      pairwiseAudit: [],
      allPairsComparable: false,
      directRankingAllowed: false,
    };
  }
  const commonAgeMinimum = Math.max(
    ...provisional.map((item) => item.support[0]),
  );
  const commonAgeMaximum = Math.min(
    ...provisional.map((item) => item.support[1]),
  );
  const commonSupportAvailable = (
    Number.isFinite(commonAgeMinimum)
    && Number.isFinite(commonAgeMaximum)
    && commonAgeMaximum > commonAgeMinimum
  );
  const referenceMedian = medianFinite(
    provisional.map((item) => item.primaryFit.reference_tyre_age_laps),
  );
  const anchorAge = commonSupportAvailable && Number.isFinite(referenceMedian)
    ? clampNumber(referenceMedian, commonAgeMinimum, commonAgeMaximum)
    : null;
  const series = buildEvidenceSeries(
    summary.selectedWindow.series,
    evidenceLookup,
    anchorAge,
  ).filter((item) => item.evidenceStatus === "ready");
  const pairwiseAudit = buildEvidencePairAudit(series, evidenceState.data);
  const expectedPairs = series.length * (series.length - 1) / 2;
  const allPairsComparable = (
    pairwiseAudit.length === expectedPairs
    && pairwiseAudit.every((row) => row.status === "comparable")
  );
  const publicationReady = (
    report?.inclusive_robust_model_audit?.status === "accepted_crossfit_proxy"
    && report?.publication_gate?.passed === true
  );
  const directRankingAllowed = Boolean(
    publicationReady
    && commonSupportAvailable
    && allPairsComparable,
  );
  const ageMinimum = Math.min(...series.map((item) => item.support[0]));
  const ageMaximum = Math.max(...series.map((item) => item.support[1]));
  const paceFastest = directRankingAllowed
    ? extremeCurveSeries(series, "paceAtAnchor", 1)
    : null;
  const paceSlowest = directRankingAllowed
    ? extremeCurveSeries(series, "paceAtAnchor", -1)
    : null;
  return {
    ...summary,
    mode: "v17_evidence",
    status: "ready",
    reason: !publicationReady
      ? "整场发布门未通过；逐圈证据仅供审计，不形成直接排名。"
      : !commonSupportAvailable
        ? "当前窗口没有共同胎龄支持，只展示逐圈证据，不形成共同胎龄比较。"
        : !allPairsComparable
          ? "存在平衡 warning、not_comparable 或缺失的 pairwise 门；只展示审计，不形成全序排名。"
          : null,
    evidenceState,
    commonAgeMinimum,
    commonAgeMaximum,
    commonSupportAvailable,
    evidenceAgeMinimum: ageMinimum,
    evidenceAgeMaximum: ageMaximum,
    anchorAge,
    series,
    pairwiseAudit,
    allPairsComparable,
    directRankingAllowed,
    publicationReady,
    extremes: {
      degradationSlowest: null,
      degradationFastest: null,
      paceFastest,
      paceSlowest,
    },
  };
}

function renderStintRow(row, showCompound = false, comparisonContext = null) {
  const fuelRange = row.fuel_sensitivity_degradation_interval_s_per_tyre_lap;
  const referenceFuelRange = row.representative_tyre_age_pace_fuel_sensitivity_interval_s;
  const referencePace = Number(row.representative_tyre_age_pace_s);
  const referenceAge = Number(row.representative_tyre_age_laps);
  const hasReferencePace = Number.isFinite(referencePace) && Number.isFinite(referenceAge);
  const shownPace = hasReferencePace ? referencePace : Number(row.stable_pace_s);
  const paceLabel = hasReferencePace
    ? `@ 胎龄 ${referenceAge.toFixed(1)} 圈`
    : "旧版中段值";
  const paceStatus = hasReferencePace
    ? "条件代理"
    : "旧版值";
  const curve = row.tyre_curve_model_audit || {};
  const strictStatus = row.strict_confirmation?.status === "confirmed" ? "通过" : "未通过";
  const compound = normalizedCompound(row.compound);
  const presentation = compoundPresentation(compound);
  const compoundCell = showCompound
    ? `<td><span class="compoundPill ${presentation.className}">${escapeHTML(presentation.label)}</span></td>`
    : "";
  const isBaseline = comparisonContext?.baseline === row;
  const isDirectComparison = Boolean(comparisonContext?.isDirectComparison);
  const baselineBadge = isBaseline
    ? `<span class="stintBaselineRowBadge">${isDirectComparison ? "比较基准" : "单行显示"}</span>`
    : "";
  const comparisonDelta = isDirectComparison
    ? `<span class="stintComparisonDelta">${formatSignedSpan(
      Number(shownPace) - Number(comparisonContext.baselinePace),
      3,
    )}s 对窗口基准</span><br>`
    : "";
  return `<tr class="${isBaseline ? "stintBaselineRow" : ""}"><td><strong>${escapeHTML(row.driver)}</strong>${baselineBadge}<br><span class="muted">${escapeHTML(row.team)}</span></td>${compoundCell}<td class="nowrap">#${formatNumber(row.stint_number)}<br>L${formatNumber(row.lap_start)}–${formatNumber(row.lap_end)}</td><td>${formatNumber(row.pace_adjusted_laps)}/${formatNumber(row.observed_laps)} 圈<br>有效权重 ${formatDecimal(row.effective_weight_laps, 1)}<br><span class="muted">MAD ${formatSeconds(row.variability_mad_s, 3)}</span></td><td><strong>${formatSeconds(shownPace, 3)}</strong><br>${comparisonDelta}<span class="muted">${escapeHTML(`${paceLabel} · ${paceStatus}`)}</span>${hasReferencePace ? `<br><span class="muted">燃油 ${escapeHTML(formatRange(referenceFuelRange, 3))}</span>` : ""}</td><td>${formatSignedSpan(row.degradation_s_per_tyre_lap, 4)} s/胎龄圈<br><span class="muted">燃油 ${escapeHTML(formatRange(fuelRange, 4))}</span></td><td>${formatSignedSpan(row.cumulative_delta_to_reasonable_baseline_s, 2)}s<br><span class="muted">${escapeHTML(row.cumulative_baseline_status || "不可识别")}</span></td><td><span class="${strictStatus === "通过" ? "statusGood" : "statusAudit"}">${strictStatus}</span><br>${escapeHTML(curve.selected_model || curveStatusLabel(curve.status))}</td><td>${escapeHTML(stintNarrative(row))}</td></tr>`;
}

function renderStintComparisonWindow(group, window) {
  const directComparison = window.rows.length > 1;
  const baselineAge = Number(window.baseline.representative_tyre_age_laps);
  const baselineContent = `<div class="stintPinnedBaseline" role="note"><span class="stintPinnedBaselineLabel">${directComparison ? "置顶比较基准" : "置顶显示值"}</span><span class="stintPinnedBaselineIdentity"><strong>${escapeHTML(window.baseline.driver)}</strong> · ${escapeHTML(window.baseline.team)}</span><span class="stintPinnedBaselinePace">${formatSeconds(window.baselinePace, 3)} · @ 胎龄 ${baselineAge.toFixed(1)} 圈</span><span class="stintPinnedBaselineCaveat">${directComparison ? "本窗口点估计最小；窗口内所有 Stint 均通过冻结可比门" : "当前筛选内没有与它同时满足胎龄和阶段门的对象"}</span></div>`;
  const comparisonContext = {
    baseline: window.baseline,
    baselinePace: window.baselinePace,
    isDirectComparison: directComparison,
  };
  return `<section class="stintCompoundGroup stintComparisonWindow ${directComparison ? "stintComparisonWindowDirect" : "stintComparisonWindowSingleton"}"><div class="stintCompoundHeader"><h3><span class="compoundPill ${group.presentation.className}">${escapeHTML(group.presentation.label)}</span><span class="stintWindowLabel">${directComparison ? "可比窗口" : "单行窗口"} ${window.index}/${window.count}</span></h3>${baselineContent}<span class="stintCompoundMeta">${window.rows.length} 个 Stint · 胎龄跨度 ${formatDecimal(window.ageSpan, 1)} 圈 · 阶段跨度 ${formatDecimal(window.phaseSpan, 3)}</span></div><table><thead><tr><th>车手 / 车队</th><th>Stint / 圈段</th><th>样本支持</th><th>条件代表配速 ${helpButton("conditional_pace", "解释条件代表配速")}</th><th>胎龄斜率 ${helpButton("fuel_scenario", "解释衰减燃油情景")}</th><th>累计对基线</th><th>严格复核 / 曲线 ${helpButton("strict_confirmation", "解释严格复核")}</th><th>本 Stint 结论</th></tr></thead><tbody>${window.rows.map((row) => renderStintRow(row, false, comparisonContext)).join("")}</tbody></table></section>`;
}

function renderStintComparisonAudit(group) {
  if (!group.auditRows.length) return "";
  const reasons = [...new Set(group.auditRows.map((item) => item.reason))].join("；");
  return `<section class="stintCompoundGroup stintComparisonAudit"><div class="stintCompoundHeader"><h3><span class="compoundPill ${group.presentation.className}">${escapeHTML(group.presentation.label)}</span><span class="stintWindowLabel">仅审计</span></h3><div class="stintPinnedBaseline stintPinnedBaselineUnavailable" role="note"><span class="stintPinnedBaselineLabel">不进入直接比较</span><span>${escapeHTML(reasons)}</span></div><span class="stintCompoundMeta">${group.auditRows.length} 个 Stint · 不生成比较基准</span></div><table><thead><tr><th>车手 / 车队</th><th>Stint / 圈段</th><th>样本支持</th><th>显示配速 ${helpButton("conditional_pace", "解释显示配速")}</th><th>胎龄斜率 ${helpButton("fuel_scenario", "解释衰减燃油情景")}</th><th>累计对基线</th><th>严格复核 / 曲线 ${helpButton("strict_confirmation", "解释严格复核")}</th><th>审计说明</th></tr></thead><tbody>${group.auditRows.map((item) => renderStintRow(item.row)).join("")}</tbody></table></section>`;
}

function renderStintComparisonCompound(group) {
  return [
    ...group.windows.map((window) => renderStintComparisonWindow(group, window)),
    renderStintComparisonAudit(group),
  ].join("");
}

function sortStintsByRaceSequence(rows) {
  return (Array.isArray(rows) ? rows.slice() : []).sort((a, b) => (
    compareText(a.team, b.team)
    || compareText(a.driver, b.driver)
    || Number(a.stint_number) - Number(b.stint_number)
    || Number(a.lap_start) - Number(b.lap_start)
  ));
}

function renderStintSequence(rows) {
  const sorted = sortStintsByRaceSequence(rows);
  return sorted.length
    ? `<section class="stintCompoundGroup"><div class="stintCompoundHeader"><h3>按车手还原比赛进程</h3><span class="muted">${sorted.length} 个 Stint · 同车手按 Stint 编号与起始圈排列</span></div><table><thead><tr><th>车手 / 车队</th><th>配方</th><th>Stint / 圈段</th><th>样本支持</th><th>条件代表配速 ${helpButton("conditional_pace", "解释条件代表配速")}</th><th>胎龄斜率 ${helpButton("fuel_scenario", "解释衰减燃油情景")}</th><th>累计对基线</th><th>严格复核 / 曲线 ${helpButton("strict_confirmation", "解释严格复核")}</th><th>本 Stint 结论</th></tr></thead><tbody>${sorted.map((row) => renderStintRow(row, true)).join("")}</tbody></table></section>`
    : "";
}

function renderStintTable(
  report,
  teamScope = $("stintTeamSelect").value || "PRIMARY",
  driver = $("stintDriverSelect").value || "ALL",
  order = $("stintOrderSelect").value || "RACE_SEQUENCE",
) {
  const rows = filterStintsForDisplay(report, teamScope, driver);
  const valid = rows.filter((row) => row.status === "valid").length;
  const strict = rows.filter((row) => row.strict_confirmation?.status === "confirmed").length;
  const drivers = new Set(rows.map((row) => row.driver).filter(Boolean)).size;
  const comparisonGroups = order === "RACE_SEQUENCE"
    ? []
    : buildComparableStintWindows(report, rows);
  const comparisonWindows = comparisonGroups.flatMap((group) => group.windows);
  const directWindows = comparisonWindows.filter((window) => window.rows.length > 1).length;
  const singletonWindows = comparisonWindows.length - directWindows;
  const auditRows = comparisonGroups.reduce(
    (sum, group) => sum + group.auditRows.length,
    0,
  );
  const orderingText = order === "RACE_SEQUENCE"
    ? "按比赛进程排列"
    : `${comparisonWindows.length} 个窗口：${directWindows} 个直接对照、${singletonWindows} 个单行、${auditRows} 行仅审计`;
  $("stintSummary").innerHTML = `<strong>当前范围：</strong>${escapeHTML(`${teamScopeLabel(report, teamScope)} · ${drivers} 名车手 · ${rows.length} 个 Stint · 有效 ${valid} · 严格确认 ${strict} · ${orderingText}`)} ${helpButton(order === "RACE_SEQUENCE" ? "conditional_pace" : "comparable_window", order === "RACE_SEQUENCE" ? "解释 Stint 条件配速" : "解释真实可比窗口")}`;
  const content = order === "RACE_SEQUENCE"
    ? renderStintSequence(rows)
    : comparisonGroups.map(renderStintComparisonCompound).join("");
  $("stintTable").innerHTML = content
    ? content
    : emptyEvidence("当前筛选没有 Stint。 ");
}

function reportSeason(ref, report) {
  return Number(report?.scope?.year ?? ref?.year);
}

function reportTeamNames(report) {
  const teams = new Set();
  for (const row of report?.coverage?.teams || []) {
    if (row?.team) teams.add(String(row.team).trim());
  }
  for (const row of report?.stint_dossiers || []) {
    if (row?.team) teams.add(String(row.team).trim());
  }
  const scopedTeam = String(report?.scope?.team || "").trim();
  if (!teams.size && scopedTeam && !scopedTeam.includes("/")) {
    teams.add(scopedTeam);
  }
  return [...teams].filter(Boolean);
}

function validateReportTeamColours(ref, report) {
  const year = reportSeason(ref, report);
  for (const teamName of reportTeamNames(report)) {
    requireOfficialTeamIdentity(year, teamName);
  }
}

function teamCurveColor(year, teamName) {
  return requireOfficialTeamIdentity(year, teamName).colour;
}

function curveSeriesLineStyle(year, item, series) {
  const identity = requireOfficialTeamIdentity(year, item.row.team);
  const teamDrivers = [...new Set(series
    .filter((candidate) => String(candidate.row.team) === String(item.row.team))
    .map((candidate) => String(candidate.row.driver || "")))]
    .sort(compareText);
  const index = Math.max(
    0,
    teamDrivers.indexOf(String(item.row.driver || "")),
  );
  return {
    color: identity.colour,
    teamKey: identity.key,
    colorSource: "official",
    driverIndex: index,
    markerShape: ["circle", "diamond", "square"][
      index % 3
    ],
    dash: STINT_CURVE_DASH_PATTERNS[
      index % STINT_CURVE_DASH_PATTERNS.length
    ],
  };
}

function curveSeriesIdentity(item) {
  return `${item.row.driver} · ${item.row.team} · Stint #${item.row.stint_number}`;
}

function renderStintCurveExtremeCard(type, label, item, value, note) {
  if (!item) return "";
  return `<article class="stintCurveExtreme" data-extreme="${escapeHTML(type)}"><span class="stintCurveExtremeLabel">${escapeHTML(label)}</span><strong>${escapeHTML(curveSeriesIdentity(item))}</strong><span class="stintCurveExtremeValue">${escapeHTML(value)}</span><small>${escapeHTML(note)}</small></article>`;
}

function renderStintCurveExtremes(model) {
  const anchor = formatDecimal(model.anchorAge, 1);
  const {
    degradationSlowest,
    degradationFastest,
    paceFastest,
    paceSlowest,
  } = model.extremes;
  $("stintCurveExtremes").innerHTML = [
    renderStintCurveExtremeCard(
      "degradation-slowest",
      "衰减点估计最慢",
      degradationSlowest,
      `${formatSigned(degradationSlowest?.slope, 4)} s/胎龄圈`,
      model.degradationOrdering.slowest,
    ),
    renderStintCurveExtremeCard(
      "degradation-fastest",
      "衰减点估计最快",
      degradationFastest,
      `${formatSigned(degradationFastest?.slope, 4)} s/胎龄圈`,
      model.degradationOrdering.fastest,
    ),
    renderStintCurveExtremeCard(
      "pace-fastest",
      `胎龄 ${anchor} 圈配速最快`,
      paceFastest,
      formatSeconds(paceFastest?.paceAtAnchor, 3),
      `共同胎龄 ${anchor} 圈`,
    ),
    renderStintCurveExtremeCard(
      "pace-slowest",
      `胎龄 ${anchor} 圈配速最慢`,
      paceSlowest,
      formatSeconds(paceSlowest?.paceAtAnchor, 3),
      `共同胎龄 ${anchor} 圈`,
    ),
  ].join("");
}

function stintCurveBadges(item, model) {
  const badges = [];
  if (item === model.extremes.degradationSlowest) badges.push("衰减最慢点估计");
  if (item === model.extremes.degradationFastest) badges.push("衰减最快点估计");
  if (item === model.extremes.paceFastest) badges.push("配速最快点估计");
  if (item === model.extremes.paceSlowest) badges.push("配速最慢点估计");
  if (item.row.strict_confirmation?.status === "confirmed") badges.push("严格复核通过");
  return badges;
}

function renderStintCurveLegend(model, year) {
  const ordered = model.series.slice().sort((a, b) => (
    compareText(a.row.team, b.row.team)
    || compareText(a.row.driver, b.row.driver)
    || Number(a.row.stint_number) - Number(b.row.stint_number)
  ));
  $("stintCurveLegend").innerHTML = ordered.map((item) => {
    const style = curveSeriesLineStyle(year, item, model.series);
    const badges = stintCurveBadges(item, model)
      .map((badge) => `<span class="stintCurveBadge">${escapeHTML(badge)}</span>`)
      .join("");
    const lineClass = style.dash.length ? " isDashed" : "";
    return `<article class="stintCurveLegendItem" data-team-key="${escapeHTML(style.teamKey)}" data-color-source="${escapeHTML(style.colorSource)}"><span class="stintCurveLegendLine${lineClass}" style="--series-color:${escapeHTML(style.color)}"></span><span class="stintCurveLegendText"><span class="stintCurveLegendIdentity">${escapeHTML(curveSeriesIdentity(item))}</span><span class="stintCurveLegendMetrics">β ${escapeHTML(formatSigned(item.slope, 4))} s/圈 · ${formatSeconds(item.paceAtAnchor, 3)} @ 胎龄 ${formatDecimal(model.anchorAge, 1)} · 支持 ${escapeHTML(formatRange(item.support, 1))}</span>${badges}</span></article>`;
  }).join("");
}

function configureStintCurvePresentation(mode) {
  const evidenceMode = mode === "v17_evidence";
  document.querySelectorAll?.(".stintCurveEncodingLegend").forEach((legend) => {
    legend.hidden = !evidenceMode;
  });
  $("stintCurveModeLabel").textContent = evidenceMode
    ? "v17 逐圈发布证据 · 浏览器零拟合"
    : "v16 summary-only · 汇总线性投影";
  $("stintPrimaryCurveEyebrow").textContent = evidenceMode
    ? "LAP-LEVEL EVIDENCE"
    : "NORMALIZED DEGRADATION";
  $("stintPrimaryCurveTitle").textContent = evidenceMode
    ? "逐圈散点与稳健主拟合"
    : "轮胎衰减汇总投影";
  $("stintPrimaryCurveMeta").textContent = evidenceMode
    ? "点 = 已发布逐圈证据"
    : "越平 = 汇总斜率越小";
  $("stintSecondaryCurveEyebrow").textContent = evidenceMode
    ? "COMMON-AGE PACE RANGES"
    : "CONDITIONAL STINT PACE";
  $("stintSecondaryCurveTitle").textContent = evidenceMode
    ? "共同胎龄配速范围"
    : "Stint 条件配速汇总投影";
  $("stintSecondaryCurveMeta").textContent = evidenceMode
    ? "越左 = 条件配速越快"
    : "越低 = 汇总点估计越快";
}

function primaryEquationLabel(item) {
  const equation = item?.primaryFit?.equation;
  if (!equation) return "主拟合方程未发布";
  return [
    "ŷ = ",
    formatDecimal(equation.reference_pace_s, 3),
    " ",
    formatSigned(equation.slope_s_per_tyre_lap, 4),
    " × (胎龄 − ",
    formatDecimal(equation.reference_tyre_age_laps, 1),
    ")",
  ].join("");
}

function confirmedEquationLabel(item) {
  const shape = item?.confirmedShape || {};
  if (shape.status !== "AVAILABLE") return null;
  const equation = shape.equation || {};
  if (shape.selected_model === "linear_theil_sen") {
    return `确认后：ŷ = ${formatDecimal(equation.intercept_s, 3)} ${formatSigned(equation.slope_s_per_tyre_lap, 4)} × 胎龄`;
  }
  if (shape.selected_model === "log_theil_sen") {
    return `确认后：ŷ = ${formatDecimal(equation.intercept_s, 3)} ${formatSigned(equation.slope_s, 4)} × log1p(胎龄)`;
  }
  if (shape.selected_model === "quadratic_least_squares") {
    return [
      "确认后：ŷ = ",
      formatSigned(equation.quadratic_s_per_lap2, 5),
      "z² ",
      formatSigned(equation.linear_s_per_lap, 4),
      "z + ",
      formatDecimal(equation.intercept_s, 3),
      `；z=胎龄−${formatDecimal(equation.center_tyre_age_laps, 1)}`,
    ].join("");
  }
  return `确认后：${shape.selected_model || "已发布描述性曲线"}（使用发布预测网格）`;
}

function renderStintCurveEvidenceCards(model) {
  const observed = model.series.reduce(
    (sum, item) => sum + Number(item.sampleAudit?.observed_laps || 0),
    0,
  );
  const fitted = model.series.reduce(
    (sum, item) => sum + Number(item.sampleAudit?.fit_laps || 0),
    0,
  );
  const stable = model.series.filter(
    (item) => item.primaryFit?.stability_interval?.status === "ESTIMATED",
  ).length;
  const comparable = model.pairwiseAudit.filter(
    (row) => row.status === "comparable",
  ).length;
  const totalPairs = model.pairwiseAudit.length;
  const rankText = model.directRankingAllowed
    ? "全窗口直接排序允许"
    : "只作成对/条件审计";
  $("stintCurveExtremes").innerHTML = [
    `<article class="stintCurveExtreme" data-extreme="evidence-sample"><span class="stintCurveExtremeLabel">逐圈样本</span><strong>${formatNumber(fitted)} / ${formatNumber(observed)} 圈进入主拟合</strong><span class="stintCurveExtremeValue">${model.series.length} 条 Stint 证据</span><small>散点来自 sidecar；浏览器未拟合</small></article>`,
    `<article class="stintCurveExtreme" data-extreme="evidence-stability"><span class="stintCurveExtremeLabel">删块稳定性</span><strong>${stable} / ${model.series.length} 条已估计</strong><span class="stintCurveExtremeValue">删除连续圈块 · 中间 80%</span><small>敏感性范围，不是置信区间</small></article>`,
    `<article class="stintCurveExtreme" data-extreme="evidence-pairs"><span class="stintCurveExtremeLabel">成对可比门</span><strong>${comparable} / ${totalPairs} 对 comparable</strong><span class="stintCurveExtremeValue">${escapeHTML(rankText)}</span><small>warning / not_comparable 不参与直接秒差</small></article>`,
    `<article class="stintCurveExtreme" data-extreme="evidence-intervals"><span class="stintCurveExtremeLabel">统计区间边界</span><strong>CI：NOT_TESTED · 新圈 PI：NOT_TESTED</strong><span class="stintCurveExtremeValue">观测 / 稳定性 / 燃油三类范围分列</span><small>任何范围都不改名为置信或预测区间</small></article>`,
  ].join("");
}

function renderStintCurveEvidenceLegend(model, year) {
  const chartFocus = selectedEvidenceChartFocus(model);
  const ordered = chartFocus.series.slice().sort((a, b) => (
    compareText(a.row.team, b.row.team)
    || compareText(a.row.driver, b.row.driver)
    || Number(a.row.stint_number) - Number(b.row.stint_number)
  ));
  const legend = $("stintCurveLegend");
  legend.setAttribute?.(
    "aria-label",
    `${chartFocus.label}的主拟合方程与样本摘要`,
  );
  legend.innerHTML = ordered.map((item) => {
    const style = curveSeriesLineStyle(year, item, model.series);
    const markerGlyph = {
      circle: "●",
      diamond: "◆",
      square: "■",
    }[style.markerShape] || "●";
    const audit = item.sampleAudit || {};
    const stability = item.primaryFit?.stability_interval || {};
    const confirmed = confirmedEquationLabel(item);
    const contextCoverage = strictFiniteNumber(
      item.conditionProfile?.context_coverage,
    );
    return `<article class="stintCurveLegendItem stintCurveEvidenceLegendItem" data-team-key="${escapeHTML(style.teamKey)}" data-color-source="${escapeHTML(style.colorSource)}"><span class="stintCurveLegendLine" style="--series-color:${escapeHTML(style.color)}"></span><span class="stintCurveLegendText"><span class="stintCurveLegendIdentity"><span class="stintCurveDriverGlyph" style="color:${escapeHTML(style.color)}" aria-hidden="true">${markerGlyph}</span>${escapeHTML(curveSeriesIdentity(item))}</span><span class="stintCurveLegendEquation">${escapeHTML(primaryEquationLabel(item))}</span><span class="stintCurveLegendMetrics">拟合 ${formatNumber(audit.fit_laps)}/${formatNumber(audit.observed_laps)} 圈 · 有效权重 ${formatDecimal(audit.effective_weight_laps, 1)} · Kish ${formatDecimal(audit.kish_effective_laps, 1)} · 稳定性 ${escapeHTML(stability.status || "NOT_TESTED")} · 上下文覆盖 ${contextCoverage === null ? "—" : formatPercent(contextCoverage)}</span>${confirmed ? `<span class="stintCurveLegendConfirmed">${escapeHTML(confirmed)}；仅确认后描述性重拟合</span>` : ""}</span></article>`;
  }).join("");
}

function curvePairStatusLabel(value) {
  const labels = {
    comparable: "✓ 直接可比",
    audit_only_balance_warning: "△ 平衡警告 · 仅审计",
    warning: "△ 平衡警告 · 仅审计",
    not_comparable: "× 不可比",
    NOT_TESTED: "? 未测试",
  };
  return labels[value] || value || "不可比";
}

function curveBalanceComponentLabel(value) {
  const labels = {
    lap_fraction: "比赛阶段",
    low_quality: "低质量圈占比",
    non_green: "非绿旗暴露",
    nuisance_adjustment: "干扰项调整",
    pit_boundary: "进出站边界",
    traffic_tvd: "交通状态分布",
  };
  return labels[value] || value || "未识别分量";
}

function curvePairGateFailureLabel(value) {
  const labels = {
    lap_fraction_difference_above_gate: "比赛阶段差超过门限",
    observable_condition_balance_failed: "可观测条件平衡失败",
    observable_condition_balance_warning: "可观测条件平衡警告",
    reference_tyre_age_gap_above_gate: "参考胎龄差超过门限",
  };
  return labels[value] || value;
}

function worstConditionBalanceComponent(balance) {
  const entries = Object.entries(balance?.components || {})
    .map(([key, value]) => ({
      key,
      distance: strictFiniteNumber(value?.normalized_distance),
    }))
    .filter((row) => row.distance !== null)
    .sort((a, b) => b.distance - a.distance);
  return entries[0] || null;
}

function renderStintCurveEvidenceAudit(model) {
  const audit = $("stintCurveEvidenceAudit");
  const pairPanel = $("stintCurvePairwiseAudit");
  audit.hidden = false;
  pairPanel.hidden = false;
  audit.innerHTML = `<details class="stintCurveDisclosure"><summary><span><span class="eyebrow">SAMPLE & MODEL AUDIT</span><strong>样本、拟合与范围审计</strong></span><span>${model.series.length} 条 Stint · 点击展开</span></summary><div class="stintCurveDisclosureBody"><div class="stintCurveAuditHeader"><p>完整指标只用于核对发布证据；主视图保留比较所需的最小信息。</p>${helpButton("curve_ranges", "解释三类范围")}</div><div class="stintCurveAuditGrid">${model.series.map((item) => {
    const sample = item.sampleAudit || {};
    const stability = item.primaryFit?.stability_interval || {};
    const observed = finiteInterval(sample.primary_fit_residual_central_range_s);
    const excluded = item.points.filter((point) => !point.used_for_primary_fit).length;
    const confirmed = item.points.filter(
      (point) => point.curve_validation_role === "later_confirmation_origin",
    ).length;
    return `<article class="stintCurveAuditItem"><strong>${escapeHTML(curveSeriesIdentity(item))}</strong><dl><div><dt>整段观测 / 主拟合 / 严格圈</dt><dd>${formatNumber(sample.observed_laps)} / ${formatNumber(sample.fit_laps)} / ${formatNumber(sample.strict_confirmation_laps)}</dd></div><div><dt>整段有效权重 / Kish ESS</dt><dd>${formatDecimal(sample.effective_weight_laps, 1)} / ${formatDecimal(sample.kish_effective_laps, 1)}</dd></div><div><dt>未入主拟合 / 后段确认原点</dt><dd>${formatNumber(excluded)} / ${formatNumber(confirmed)}</dd></div><div><dt>拟合误差 MAE / RMSE / MAD</dt><dd>${formatSeconds(sample.primary_fit_weighted_mae_s, 3)} / ${formatSeconds(sample.primary_fit_weighted_rmse_s, 3)} / ${formatSeconds(sample.primary_fit_residual_mad_s, 3)}</dd></div><div><dt>观测残差中间 80%</dt><dd>${escapeHTML(formatRange(observed, 3))}</dd></div><div><dt>删块稳定性 80%</dt><dd>${escapeHTML(stability.status || "NOT_TESTED")} · ${formatNumber(stability.refits)} 次重拟合</dd></div></dl></article>`;
  }).join("")}</div></div></details>`;

  const rows = model.pairwiseAudit.map((pair) => {
    const published = pair.published || {};
    const balance = published.observable_condition_balance || {};
    const commonSample = published.common_support_sample_audit || {};
    const leftSample = commonSample.left || {};
    const rightSample = commonSample.right || {};
    const commonProfiles = published.common_support_condition_profiles || {};
    const leftCoverage = strictFiniteNumber(
      commonProfiles.left?.context_coverage,
    );
    const rightCoverage = strictFiniteNumber(
      commonProfiles.right?.context_coverage,
    );
    const worst = worstConditionBalanceComponent(balance);
    const directDelta = pair.status === "comparable"
      ? strictFiniteNumber(published.left_minus_right_pace_s)
      : null;
    const comparisonAge = strictFiniteNumber(
      published.comparison_tyre_age_laps,
    );
    const commonSupport = finiteInterval(
      published.common_tyre_age_support_laps,
    );
    const rawFailures = Array.isArray(published.gate_failures)
      ? published.gate_failures
      : [];
    const failures = rawFailures.length
      ? rawFailures.map(curvePairGateFailureLabel).join("、")
      : published
        ? "全部冻结门通过"
        : "pairwise 结果缺失";
    const balanceDistance = strictFiniteNumber(
      balance.maximum_normalized_distance,
    );
    const sampleText = commonSupport
      ? [
        `胎龄 ${formatRange(commonSupport, 1)}`,
        `左 ${formatNumber(leftSample.fit_laps)}/${formatNumber(leftSample.observed_laps)} 圈`,
        `Kish ${formatDecimal(leftSample.kish_effective_laps, 1)}`,
        `右 ${formatNumber(rightSample.fit_laps)}/${formatNumber(rightSample.observed_laps)} 圈`,
        `Kish ${formatDecimal(rightSample.kish_effective_laps, 1)}`,
      ].join(" · ")
      : "—";
    const deltaText = directDelta !== null
      ? `${formatSigned(directDelta, 3)}s（左−右${comparisonAge === null ? "" : ` @ 胎龄 ${formatDecimal(comparisonAge, 1)}`}）`
      : "—";
    const coverageText = leftCoverage !== null && rightCoverage !== null
      ? ` · 覆盖 ${formatPercent(leftCoverage)}/${formatPercent(rightCoverage)}`
      : "";
    const balanceText = `${balance.status || "NOT_TESTED"}${balanceDistance !== null ? ` · D=${formatDecimal(balanceDistance, 2)}` : ""}${coverageText}${worst ? ` · 最紧 ${curveBalanceComponentLabel(worst.key)}=${formatDecimal(worst.distance, 2)}` : ""}`;
    return `<tr data-pair-status="${escapeHTML(pair.status)}"><td data-label="Stint 对">${escapeHTML(pair.left.row.driver)} ↔ ${escapeHTML(pair.right.row.driver)}</td><td data-label="状态"><span class="stintPairStatus">${escapeHTML(curvePairStatusLabel(pair.status))}</span></td><td data-label="直接秒差">${escapeHTML(deltaText)}</td><td data-label="共同支持样本">${escapeHTML(sampleText)}</td><td data-label="条件平衡" title="${escapeHTML(worst?.key || balance.status || "")}">${escapeHTML(balanceText)}</td><td data-label="门控审计" title="${escapeHTML(rawFailures.join(","))}">${escapeHTML(failures)}</td></tr>`;
  }).join("");
  pairPanel.innerHTML = `<details class="stintCurveDisclosure" open><summary><span><span class="eyebrow">PAIRWISE COMPARABILITY</span><strong>成对条件平衡与直接秒差</strong></span><span>${model.pairwiseAudit.length} 对 · 只允许 comparable 进入图表焦点</span></summary><div class="stintCurveDisclosureBody"><div class="stintCurveAuditHeader"><p>警告与不可比只保留审计记录，不产生直接秒差或全序。</p>${helpButton("curve_pairwise", "解释成对可比门")}</div>${rows ? `<div class="tableWrap"><table><thead><tr><th>Stint 对</th><th>状态</th><th>后端发布直接秒差</th><th>共同支持样本</th><th>可观测条件平衡</th><th>门控审计</th></tr></thead><tbody>${rows}</tbody></table></div>` : emptyEvidence("当前窗口没有发布成对比较记录。")}</div></details>`;
}

function hideStintCurveEvidenceAudit() {
  $("stintCurveEvidenceAudit").hidden = true;
  $("stintCurveEvidenceAudit").innerHTML = "";
  $("stintCurvePairwiseAudit").hidden = true;
  $("stintCurvePairwiseAudit").innerHTML = "";
}

function isHighlightedCurveSeries(item, model) {
  return (
    item === model.extremes.degradationSlowest
    || item === model.extremes.degradationFastest
    || item === model.extremes.paceFastest
    || item === model.extremes.paceSlowest
  );
}

function renderStintCurveChart(
  canvasId,
  emptyId,
  model,
  year,
  pointsKey,
  {
    yLabel,
    digits = 3,
    includeZero = false,
    ariaLabel,
  },
) {
  const canvas = $(canvasId);
  const empty = $(emptyId);
  const drawable = model.status === "ready"
    ? model.series.filter((item) => item[pointsKey]?.length > 1)
    : [];
  if (drawable.length < 2) {
    canvas.hidden = true;
    empty.hidden = false;
    empty.textContent = model.reason || "当前窗口没有至少两条可比曲线。";
    return;
  }
  canvas.hidden = false;
  empty.hidden = true;
  if (typeof canvas.setAttribute === "function") {
    canvas.setAttribute("aria-label", ariaLabel);
  }
  const dpr = window.devicePixelRatio || 1;
  const width = canvas.clientWidth || 640;
  const height = width < 520 ? 280 : 320;
  canvas.width = width * dpr;
  canvas.height = height * dpr;
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, width, height);
  const pad = { left: 58, right: 18, top: 28, bottom: 46 };
  const innerWidth = width - pad.left - pad.right;
  const innerHeight = height - pad.top - pad.bottom;
  const xMinimum = model.commonAgeMinimum;
  const xMaximum = model.commonAgeMaximum;
  const allY = drawable.flatMap((item) => item[pointsKey].map((point) => point.y));
  if (includeZero) allY.push(0);
  let yMinimum = Math.min(...allY);
  let yMaximum = Math.max(...allY);
  const rawYSpan = yMaximum - yMinimum;
  const yPadding = Math.max(rawYSpan * 0.12, pointsKey === "pacePoints" ? 0.04 : 0.01);
  yMinimum -= yPadding;
  yMaximum += yPadding;
  const xScale = (value) => pad.left
    + (value - xMinimum) / (xMaximum - xMinimum) * innerWidth;
  const yScale = (value) => pad.top
    + (yMaximum - value) / (yMaximum - yMinimum) * innerHeight;

  ctx.font = '10px "Segoe UI", "Microsoft YaHei", sans-serif';
  ctx.textBaseline = "middle";
  ctx.lineWidth = 1;
  for (let index = 0; index <= 4; index += 1) {
    const ratio = index / 4;
    const y = pad.top + innerHeight * ratio;
    const value = yMaximum - (yMaximum - yMinimum) * ratio;
    ctx.strokeStyle = "rgba(255,255,255,.075)";
    ctx.setLineDash?.([]);
    ctx.beginPath();
    ctx.moveTo(pad.left, y);
    ctx.lineTo(width - pad.right, y);
    ctx.stroke();
    ctx.fillStyle = "#8c98aa";
    ctx.textAlign = "right";
    ctx.fillText(value.toFixed(digits), pad.left - 8, y);
  }
  for (let index = 0; index <= 4; index += 1) {
    const ratio = index / 4;
    const value = xMinimum + (xMaximum - xMinimum) * ratio;
    const x = xScale(value);
    ctx.strokeStyle = "rgba(255,255,255,.045)";
    ctx.beginPath();
    ctx.moveTo(x, pad.top);
    ctx.lineTo(x, height - pad.bottom);
    ctx.stroke();
    ctx.fillStyle = "#8c98aa";
    ctx.textAlign = "center";
    ctx.fillText(value.toFixed(1), x, height - pad.bottom + 16);
  }
  if (includeZero && yMinimum <= 0 && yMaximum >= 0) {
    ctx.strokeStyle = "rgba(255,255,255,.28)";
    ctx.lineWidth = 1.2;
    ctx.beginPath();
    ctx.moveTo(pad.left, yScale(0));
    ctx.lineTo(width - pad.right, yScale(0));
    ctx.stroke();
  }
  const anchorX = xScale(model.anchorAge);
  ctx.strokeStyle = "rgba(246,200,95,.55)";
  ctx.lineWidth = 1.2;
  ctx.setLineDash?.([4, 4]);
  ctx.beginPath();
  ctx.moveTo(anchorX, pad.top);
  ctx.lineTo(anchorX, height - pad.bottom);
  ctx.stroke();
  ctx.setLineDash?.([]);

  const drawingOrder = drawable.slice().sort((a, b) => (
    Number(isHighlightedCurveSeries(a, model))
      - Number(isHighlightedCurveSeries(b, model))
  ));
  for (const item of drawingOrder) {
    const style = curveSeriesLineStyle(year, item, model.series);
    const points = item[pointsKey];
    ctx.strokeStyle = style.color;
    ctx.lineWidth = isHighlightedCurveSeries(item, model) ? 2.8 : 1.7;
    ctx.globalAlpha = isHighlightedCurveSeries(item, model) ? 1 : 0.78;
    ctx.setLineDash?.(style.dash);
    ctx.beginPath();
    points.forEach((point, index) => {
      const x = xScale(point.x);
      const y = yScale(point.y);
      if (index === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
    const anchorValue = pointsKey === "pacePoints" ? item.paceAtAnchor : 0;
    ctx.fillStyle = style.color;
    ctx.fillRect(anchorX - 3, yScale(anchorValue) - 3, 6, 6);
  }
  ctx.globalAlpha = 1;
  ctx.setLineDash?.([]);
  ctx.fillStyle = "#8c98aa";
  ctx.textAlign = "left";
  ctx.fillText(yLabel, 8, 12);
  ctx.textAlign = "center";
  ctx.fillText("胎龄（圈）", pad.left + innerWidth / 2, height - 10);
  ctx.fillStyle = "#f6c85f";
  ctx.fillText(`共同评价 ${formatDecimal(model.anchorAge, 1)}`, anchorX, pad.top - 12);
}

function curveChartContext(canvas, minimumHeight = 320) {
  const dpr = window.devicePixelRatio || 1;
  const width = canvas.clientWidth || 640;
  const height = Math.max(width < 520 ? 300 : 320, minimumHeight);
  canvas.width = width * dpr;
  canvas.height = height * dpr;
  if (canvas.style) canvas.style.height = `${height}px`;
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, width, height);
  ctx.font = '11px "Bahnschrift", "Segoe UI", "Microsoft YaHei", sans-serif';
  ctx.textBaseline = "middle";
  return { ctx, width, height };
}

function finiteQuantile(values, probability) {
  const ordered = (Array.isArray(values) ? values : [])
    .map(strictFiniteNumber)
    .filter((value) => value !== null)
    .sort((a, b) => a - b);
  if (!ordered.length) return null;
  if (ordered.length === 1) return ordered[0];
  const position = clampNumber(Number(probability), 0, 1)
    * (ordered.length - 1);
  const lower = Math.floor(position);
  const upper = Math.ceil(position);
  if (lower === upper) return ordered[lower];
  const fraction = position - lower;
  return ordered[lower] * (1 - fraction) + ordered[upper] * fraction;
}

function selectedCurveViewMode() {
  return $("stintCurveScaleSelect")?.value === "FULL" ? "FULL" : "FOCUS";
}

function evidenceChartFocusOptions(model) {
  const options = [];
  if (model.directRankingAllowed && model.series.length > 1) {
    options.push({
      value: "CLIQUE",
      label: `完整可比窗口 · ${model.series.length} 条 Stint`,
      series: model.series,
      kind: "clique",
    });
  }
  for (const pair of model.pairwiseAudit || []) {
    if (pair.status !== "comparable") continue;
    const age = strictFiniteNumber(
      pair.published?.comparison_tyre_age_laps,
    );
    options.push({
      value: `PAIR:${pair.pairKey}`,
      label: `${pair.left.row.driver} ↔ ${pair.right.row.driver} · 可比${age === null ? "" : ` @ 胎龄 ${formatDecimal(age, 1)}`}`,
      series: [pair.left, pair.right],
      kind: "pair",
      pair,
    });
  }
  for (const item of model.series || []) {
    options.push({
      value: `STINT:${item.evidence?.stint_key || item.key}`,
      label: `${item.row.driver} · ${item.row.team} · Stint #${item.row.stint_number} 单段审计`,
      series: [item],
      kind: "stint",
    });
  }
  return options;
}

function configureEvidenceChartFocus(model, preferredValue = null) {
  const selector = $("stintCurveFocusSelect");
  if (!selector) return null;
  const options = evidenceChartFocusOptions(model);
  selector.innerHTML = options.map((option) => (
    `<option value="${escapeHTML(option.value)}">${escapeHTML(option.label)}</option>`
  )).join("");
  const selected = options.find((option) => option.value === preferredValue)
    || options[0]
    || null;
  selector.disabled = !selected;
  selector.value = selected?.value || "";
  return selected;
}

function selectedEvidenceChartFocus(model) {
  const options = evidenceChartFocusOptions(model);
  const selectedValue = $("stintCurveFocusSelect")?.value;
  return options.find((option) => option.value === selectedValue)
    || options[0]
    || { value: "", label: "无可绘制对象", series: [], kind: "none" };
}

function evidenceFocusYValues(drawable) {
  const values = [];
  for (const item of drawable) {
    const residualRange = finiteInterval(
      item.sampleAudit?.primary_fit_residual_central_range_s,
    );
    for (const point of item.primaryFit?.prediction_grid || []) {
      const pace = strictFiniteNumber(point?.pace_s);
      if (pace === null) continue;
      values.push(pace);
      if (residualRange) {
        values.push(pace + residualRange[0], pace + residualRange[1]);
      }
    }
    for (const point of item.primaryFit?.stability_interval?.prediction_band || []) {
      for (const field of ["lower_s", "upper_s"]) {
        const value = strictFiniteNumber(point?.[field]);
        if (value !== null) values.push(value);
      }
    }
    for (const point of item.confirmedShape?.prediction_grid || []) {
      const value = strictFiniteNumber(point?.pace_s);
      if (value !== null) values.push(value);
    }
  }
  return values;
}

function attachStintCurveTooltip(canvas, targets) {
  const tooltip = $("stintCurveHoverTooltip");
  if (!canvas || !tooltip) return;
  canvas.setAttribute?.("tabindex", "0");
  canvas.setAttribute?.("aria-describedby", "stintCurveHoverTooltip");
  const hide = () => {
    tooltip.hidden = true;
  };
  canvas.onpointermove = (event) => {
    if (!targets.length || typeof canvas.getBoundingClientRect !== "function") {
      hide();
      return;
    }
    const rect = canvas.getBoundingClientRect();
    const x = (event.clientX - rect.left)
      * ((canvas.clientWidth || rect.width) / Math.max(rect.width, 1));
    const y = (event.clientY - rect.top)
      * ((canvas.clientHeight || rect.height) / Math.max(rect.height, 1));
    let nearest = null;
    let bestDistance = Infinity;
    for (const target of targets) {
      const distance = (target.x - x) ** 2 + (target.y - y) ** 2;
      if (distance < bestDistance) {
        nearest = target;
        bestDistance = distance;
      }
    }
    if (!nearest || bestDistance > (nearest.radius || 18) ** 2) {
      hide();
      return;
    }
    tooltip.innerHTML = nearest.html;
    tooltip.hidden = false;
    const viewportWidth = Number(window.innerWidth) || 1440;
    const viewportHeight = Number(window.innerHeight) || 900;
    const tooltipWidth = tooltip.offsetWidth || 260;
    const tooltipHeight = tooltip.offsetHeight || 120;
    const left = Math.min(
      Math.max(12, event.clientX + 14),
      Math.max(12, viewportWidth - tooltipWidth - 12),
    );
    const top = event.clientY + tooltipHeight + 18 < viewportHeight
      ? event.clientY + 14
      : Math.max(12, event.clientY - tooltipHeight - 14);
    tooltip.style.left = `${left}px`;
    tooltip.style.top = `${top}px`;
  };
  canvas.onpointerleave = hide;
  canvas.onblur = hide;
}

function curvePointTooltip(item, point, clippedLabel = null) {
  const fitted = strictFiniteNumber(point?.fitted_primary_pace_s);
  const adjusted = strictFiniteNumber(point?.adjusted_pace_s);
  const residual = strictFiniteNumber(point?.primary_fit_residual_s);
  const weight = strictFiniteNumber(point?.analysis_weight);
  const flags = [
    point?.traffic_state,
    point?.pit_boundary_proxy ? "pit_boundary" : null,
    point?.quality_ok === false ? "low_quality" : null,
    clippedLabel,
  ].filter(Boolean);
  return [
    `<strong>${escapeHTML(curveSeriesIdentity(item))}</strong>`,
    `<span>比赛第 ${escapeHTML(formatNumber(point?.lap_number))} 圈 · 胎龄 ${escapeHTML(formatDecimal(point?.tyre_age_laps, 1))}</span>`,
    `<span>调整圈时 ${escapeHTML(formatSeconds(adjusted, 3))} · 主拟合 ${escapeHTML(formatSeconds(fitted, 3))}</span>`,
    `<span>残差 ${escapeHTML(formatSigned(residual, 3))}s · 权重 ${weight === null ? "—" : escapeHTML(formatPercent(weight))}</span>`,
    `<small>${escapeHTML(point?.used_for_primary_fit ? "进入主拟合" : "未进入主拟合")}${flags.length ? ` · ${escapeHTML(flags.join(" · "))}` : ""}</small>`,
  ].join("");
}

function drawCurveEvidenceBand(ctx, points, xScale, yScale, color) {
  const band = (Array.isArray(points) ? points : [])
    .map((row) => ({
      x: strictFiniteNumber(row?.tyre_age_laps),
      lower: strictFiniteNumber(row?.lower_s),
      upper: strictFiniteNumber(row?.upper_s),
    }))
    .filter((row) => row.x !== null && row.lower !== null && row.upper !== null)
    .sort((a, b) => a.x - b.x);
  if (band.length < 2) return;
  ctx.save?.();
  ctx.strokeStyle = color;
  ctx.lineWidth = 1;
  ctx.globalAlpha = 0.52;
  ctx.setLineDash?.([2, 5]);
  for (const field of ["lower", "upper"]) {
    ctx.beginPath();
    band.forEach((point, index) => {
      if (index === 0) ctx.moveTo(xScale(point.x), yScale(point[field]));
      else ctx.lineTo(xScale(point.x), yScale(point[field]));
    });
    ctx.stroke();
  }
  ctx.setLineDash?.([]);
  for (const point of [band[0], band[band.length - 1]]) {
    ctx.beginPath();
    ctx.moveTo(xScale(point.x), yScale(point.lower));
    ctx.lineTo(xScale(point.x), yScale(point.upper));
    ctx.stroke();
  }
  ctx.restore?.();
}

function drawPublishedCurveLine(
  ctx,
  grid,
  xScale,
  yScale,
  {
    color,
    dash = [],
    lineWidth = 2,
    alpha = 1,
  },
) {
  const points = (Array.isArray(grid) ? grid : [])
    .map((row) => ({
      x: strictFiniteNumber(row?.tyre_age_laps),
      y: strictFiniteNumber(row?.pace_s),
    }))
    .filter((row) => row.x !== null && row.y !== null)
    .sort((a, b) => a.x - b.x);
  if (points.length < 2) return;
  ctx.strokeStyle = color;
  ctx.lineWidth = lineWidth;
  ctx.globalAlpha = alpha;
  ctx.setLineDash?.(dash);
  ctx.beginPath();
  points.forEach((point, index) => {
    if (index === 0) ctx.moveTo(xScale(point.x), yScale(point.y));
    else ctx.lineTo(xScale(point.x), yScale(point.y));
  });
  ctx.stroke();
}

function drawCurveMarker(
  ctx,
  x,
  y,
  {
    shape = "circle",
    color,
    radius = 3,
    filled = true,
    lineWidth = 1.2,
  },
) {
  const canDrawPath = (
    typeof ctx.beginPath === "function"
    && typeof ctx.moveTo === "function"
    && typeof ctx.lineTo === "function"
  );
  ctx.fillStyle = color;
  ctx.strokeStyle = color;
  ctx.lineWidth = lineWidth;
  if (
    shape === "circle"
    && typeof ctx.arc === "function"
  ) {
    ctx.beginPath();
    ctx.arc(x, y, radius, 0, Math.PI * 2);
    if (filled && typeof ctx.fill === "function") ctx.fill();
    else ctx.stroke();
    return;
  }
  if (shape === "diamond" && canDrawPath) {
    ctx.beginPath();
    ctx.moveTo(x, y - radius - 0.5);
    ctx.lineTo(x + radius + 0.5, y);
    ctx.lineTo(x, y + radius + 0.5);
    ctx.lineTo(x - radius - 0.5, y);
    ctx.closePath?.();
    if (filled && typeof ctx.fill === "function") ctx.fill();
    else ctx.stroke();
    return;
  }
  if (filled) {
    ctx.fillRect(x - radius, y - radius, radius * 2, radius * 2);
  } else if (typeof ctx.strokeRect === "function") {
    ctx.strokeRect(x - radius, y - radius, radius * 2, radius * 2);
  } else {
    ctx.fillRect(x - radius, y - radius, radius * 2, radius * 2);
  }
}

function drawCurveEndLabels(
  ctx,
  drawable,
  model,
  year,
  xScale,
  yScale,
  pad,
  width,
  height,
) {
  const labels = drawable.map((item) => {
    const points = (item.primaryFit?.prediction_grid || [])
      .map((row) => ({
        x: strictFiniteNumber(row?.tyre_age_laps),
        y: strictFiniteNumber(row?.pace_s),
      }))
      .filter((row) => row.x !== null && row.y !== null)
      .sort((a, b) => a.x - b.x);
    const endpoint = points[points.length - 1];
    if (!endpoint) return null;
    return {
      item,
      endpoint,
      x: xScale(endpoint.x),
      naturalY: yScale(endpoint.y),
      y: yScale(endpoint.y),
      style: curveSeriesLineStyle(year, item, model.series),
    };
  }).filter(Boolean).sort((a, b) => a.naturalY - b.naturalY);
  const minimumGap = 13;
  let cursor = pad.top + 7;
  for (const label of labels) {
    label.y = Math.max(label.naturalY, cursor);
    cursor = label.y + minimumGap;
  }
  const maximumY = height - pad.bottom - 7;
  if (labels.length && labels[labels.length - 1].y > maximumY) {
    labels[labels.length - 1].y = maximumY;
    for (let index = labels.length - 2; index >= 0; index -= 1) {
      labels[index].y = Math.min(
        labels[index].y,
        labels[index + 1].y - minimumGap,
      );
    }
  }
  for (const label of labels) {
    const textX = width - pad.right + 8;
    ctx.strokeStyle = label.style.color;
    ctx.globalAlpha = 0.72;
    ctx.lineWidth = 1;
    ctx.setLineDash?.([]);
    ctx.beginPath();
    ctx.moveTo(label.x + 3, label.naturalY);
    ctx.lineTo(textX - 3, label.y);
    ctx.stroke();
    ctx.fillStyle = label.style.color;
    ctx.globalAlpha = 1;
    ctx.textAlign = "left";
    ctx.fillText(
      `${label.item.row.driver}#${label.item.row.stint_number}`,
      textX,
      label.y,
    );
  }
}

function renderStintEvidenceScatterChart(canvasId, emptyId, model, year) {
  const canvas = $(canvasId);
  const empty = $(emptyId);
  const chartFocus = selectedEvidenceChartFocus(model);
  const chartAnchorAge = strictFiniteNumber(
    chartFocus.pair?.published?.comparison_tyre_age_laps,
  ) ?? strictFiniteNumber(model.anchorAge);
  const drawable = chartFocus.series.filter((item) => (
    item.primaryFit?.prediction_grid?.length > 1
    && item.points.some((point) => (
      strictFiniteNumber(point?.tyre_age_laps) !== null
      && strictFiniteNumber(point?.adjusted_pace_s) !== null
    ))
  ));
  if (!drawable.length) {
    canvas.hidden = true;
    empty.hidden = false;
    empty.textContent = "当前窗口没有可绘制的逐圈散点与后端主拟合。";
    return;
  }
  canvas.hidden = false;
  empty.hidden = true;
  canvas.setAttribute?.(
    "aria-label",
    `${model.selectedCompound} ${chartFocus.label}的逐圈条件配速散点、后端稳健主拟合、删块敏感性边界与可选确认后描述性曲线`,
  );
  const viewMode = selectedCurveViewMode();
  const tooltipTargets = [];
  const { ctx, width, height } = curveChartContext(canvas, 340);
  const pad = {
    left: width < 520 ? 52 : 62,
    right: width < 520 ? 54 : 92,
    top: 40,
    bottom: 48,
  };
  const innerWidth = width - pad.left - pad.right;
  const innerHeight = height - pad.top - pad.bottom;
  const xValues = [];
  const yValues = [];
  for (const item of drawable) {
    for (const point of item.points) {
      const x = strictFiniteNumber(point?.tyre_age_laps);
      const y = strictFiniteNumber(point?.adjusted_pace_s);
      if (x !== null && y !== null) {
        xValues.push(x);
        yValues.push(y);
      }
    }
    for (const point of item.primaryFit.prediction_grid || []) {
      const x = strictFiniteNumber(point?.tyre_age_laps);
      const y = strictFiniteNumber(point?.pace_s);
      if (x !== null && y !== null) {
        xValues.push(x);
        yValues.push(y);
      }
    }
    for (const point of item.primaryFit?.stability_interval?.prediction_band || []) {
      const age = strictFiniteNumber(point?.tyre_age_laps);
      if (age !== null) xValues.push(age);
      for (const field of ["lower_s", "upper_s"]) {
        const value = strictFiniteNumber(point?.[field]);
        if (value !== null) yValues.push(value);
      }
    }
    for (const point of item.confirmedShape?.prediction_grid || []) {
      const age = strictFiniteNumber(point?.tyre_age_laps);
      const value = strictFiniteNumber(point?.pace_s);
      if (age !== null) xValues.push(age);
      if (value !== null) yValues.push(value);
    }
  }
  if (xValues.length < 2 || yValues.length < 2) {
    canvas.hidden = true;
    empty.hidden = false;
    empty.textContent = "逐圈 sidecar 中没有足够的有限坐标。";
    return;
  }
  let yDomainValues = yValues;
  if (viewMode === "FOCUS") {
    const focusValues = evidenceFocusYValues(drawable);
    if (focusValues.length >= 2) {
      yDomainValues = focusValues;
    } else {
      const lower = finiteQuantile(yValues, 0.05);
      const upper = finiteQuantile(yValues, 0.95);
      if (lower !== null && upper !== null && upper > lower) {
        yDomainValues = [lower, upper];
      }
    }
  }
  let xMinimum = Math.min(...xValues);
  let xMaximum = Math.max(...xValues);
  let yMinimum = Math.min(...yDomainValues);
  let yMaximum = Math.max(...yDomainValues);
  if (xMaximum <= xMinimum) xMaximum = xMinimum + 1;
  if (yMaximum <= yMinimum) yMaximum = yMinimum + 0.1;
  const xPadding = Math.max((xMaximum - xMinimum) * 0.02, 0.15);
  const yPadding = Math.max((yMaximum - yMinimum) * 0.12, 0.08);
  xMinimum -= xPadding;
  xMaximum += xPadding;
  yMinimum -= yPadding;
  yMaximum += yPadding;
  const pointPaces = drawable.flatMap((item) => (
    item.points
      .map((point) => strictFiniteNumber(point?.adjusted_pace_s))
      .filter((value) => value !== null)
  ));
  const clippedAbove = pointPaces.filter((value) => value > yMaximum).length;
  const clippedBelow = pointPaces.filter((value) => value < yMinimum).length;
  canvas.dataset.viewMode = viewMode.toLowerCase();
  canvas.dataset.clippedPoints = String(clippedAbove + clippedBelow);
  const xScale = (value) => pad.left
    + (value - xMinimum) / (xMaximum - xMinimum) * innerWidth;
  const yScale = (value) => pad.top
    + (yMaximum - value) / (yMaximum - yMinimum) * innerHeight;

  ctx.lineWidth = 1;
  for (let index = 0; index <= 4; index += 1) {
    const ratio = index / 4;
    const y = pad.top + innerHeight * ratio;
    const value = yMaximum - (yMaximum - yMinimum) * ratio;
    ctx.strokeStyle = "rgba(255,255,255,.075)";
    ctx.setLineDash?.([]);
    ctx.beginPath();
    ctx.moveTo(pad.left, y);
    ctx.lineTo(width - pad.right, y);
    ctx.stroke();
    ctx.fillStyle = "#8c98aa";
    ctx.textAlign = "right";
    ctx.fillText(value.toFixed(2), pad.left - 8, y);
  }
  for (let index = 0; index <= 4; index += 1) {
    const ratio = index / 4;
    const value = xMinimum + (xMaximum - xMinimum) * ratio;
    const x = xScale(value);
    ctx.strokeStyle = "rgba(255,255,255,.045)";
    ctx.beginPath();
    ctx.moveTo(x, pad.top);
    ctx.lineTo(x, height - pad.bottom);
    ctx.stroke();
    ctx.fillStyle = "#8c98aa";
    ctx.textAlign = "center";
    ctx.fillText(value.toFixed(1), x, height - pad.bottom + 16);
  }
  if (chartAnchorAge !== null) {
    const anchorX = xScale(chartAnchorAge);
    ctx.strokeStyle = "rgba(246,200,95,.52)";
    ctx.lineWidth = 1.2;
    ctx.setLineDash?.([4, 4]);
    ctx.beginPath();
    ctx.moveTo(anchorX, pad.top);
    ctx.lineTo(anchorX, height - pad.bottom);
    ctx.stroke();
    ctx.fillStyle = "#f6c85f";
    ctx.textAlign = "center";
    ctx.fillText(
      `共同胎龄 ${formatDecimal(chartAnchorAge, 1)}`,
      anchorX,
      pad.top - 12,
    );
  }
  ctx.fillStyle = "#8f9bad";
  ctx.textAlign = "right";
  ctx.fillText(
    viewMode === "FOCUS"
      ? `聚焦主趋势${clippedAbove || clippedBelow ? ` · ↑${clippedAbove} ↓${clippedBelow} 圈边缘标记` : ""}`
      : "全部散点 · 完整纵轴",
    width - pad.right,
    12,
  );

  for (const item of drawable) {
    const style = curveSeriesLineStyle(year, item, model.series);
    drawCurveEvidenceBand(
      ctx,
      item.primaryFit?.stability_interval?.prediction_band,
      xScale,
      yScale,
      style.color,
    );
  }
  for (const item of drawable) {
    const style = curveSeriesLineStyle(year, item, model.series);
    drawPublishedCurveLine(
      ctx,
      item.primaryFit.prediction_grid,
      xScale,
      yScale,
      {
        color: "rgba(4,9,15,.82)",
        dash: [],
        lineWidth: 4.8,
      },
    );
    drawPublishedCurveLine(
      ctx,
      item.primaryFit.prediction_grid,
      xScale,
      yScale,
      {
        color: style.color,
        dash: [],
        lineWidth: 2.4,
      },
    );
    if (item.confirmedShape?.status === "AVAILABLE") {
      drawPublishedCurveLine(
        ctx,
        item.confirmedShape.prediction_grid,
        xScale,
        yScale,
        {
          color: style.color,
          dash: [3, 4],
          lineWidth: 1.3,
          alpha: 0.78,
        },
      );
    }
  }
  for (const item of drawable) {
    const style = curveSeriesLineStyle(year, item, model.series);
    for (const point of item.points) {
      const age = strictFiniteNumber(point?.tyre_age_laps);
      const pace = strictFiniteNumber(point?.adjusted_pace_s);
      if (age === null || pace === null) continue;
      const used = point?.used_for_primary_fit === true;
      const weight = clampNumber(
        strictFiniteNumber(point?.analysis_weight) ?? 0,
        0,
        1,
      );
      const radius = 3;
      const clippedLabel = pace > yMaximum
        ? "高于聚焦视窗"
        : pace < yMinimum
          ? "低于聚焦视窗"
          : null;
      const pointX = xScale(age);
      const pointY = pace > yMaximum
        ? pad.top + 5
        : pace < yMinimum
          ? height - pad.bottom - 5
          : yScale(pace);
      const contextMarker = (
        point?.pit_boundary_proxy === true
        || point?.quality_ok === false
      );
      ctx.globalAlpha = used ? 0.42 + 0.42 * weight : 0.38;
      if (
        clippedLabel
        && typeof ctx.closePath === "function"
        && typeof ctx.fill === "function"
      ) {
        const direction = pace > yMaximum ? 1 : -1;
        ctx.beginPath();
        ctx.moveTo(pointX, pointY - direction * 4);
        ctx.lineTo(pointX - 4, pointY + direction * 4);
        ctx.lineTo(pointX + 4, pointY + direction * 4);
        ctx.closePath();
        if (used) {
          ctx.fillStyle = style.color;
          ctx.fill();
        } else {
          ctx.strokeStyle = style.color;
          ctx.lineWidth = 1.2;
          ctx.stroke();
        }
      } else {
        drawCurveMarker(ctx, pointX, pointY, {
          shape: style.markerShape,
          color: style.color,
          radius,
          filled: used,
        });
        if (contextMarker) {
          ctx.strokeStyle = style.color;
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.moveTo(pointX - radius - 1, pointY - radius - 1);
          ctx.lineTo(pointX + radius + 1, pointY + radius + 1);
          ctx.moveTo(pointX + radius + 1, pointY - radius - 1);
          ctx.lineTo(pointX - radius - 1, pointY + radius + 1);
          ctx.stroke();
        }
      }
      tooltipTargets.push({
        x: pointX,
        y: pointY,
        radius: 15,
        html: curvePointTooltip(item, point, clippedLabel),
      });
    }
  }
  drawCurveEndLabels(
    ctx,
    drawable,
    model,
    year,
    xScale,
    yScale,
    pad,
    width,
    height,
  );
  ctx.globalAlpha = 1;
  ctx.setLineDash?.([]);
  ctx.fillStyle = "#8c98aa";
  ctx.textAlign = "left";
  ctx.fillText("条件调整圈时（s）", 8, 12);
  ctx.textAlign = "center";
  ctx.fillText("胎龄（圈）", pad.left + innerWidth / 2, height - 10);
  attachStintCurveTooltip(canvas, tooltipTargets);
}

function drawRangeWhisker(
  ctx,
  interval,
  y,
  xScale,
  {
    color,
    lineWidth,
    alpha,
    dash = [],
    capSize = 4,
  },
) {
  const range = finiteInterval(interval);
  if (!range) return;
  ctx.strokeStyle = color;
  ctx.lineWidth = lineWidth;
  ctx.globalAlpha = alpha;
  ctx.setLineDash?.(dash);
  ctx.beginPath();
  ctx.moveTo(xScale(range[0]), y);
  ctx.lineTo(xScale(range[1]), y);
  ctx.moveTo(xScale(range[0]), y - capSize);
  ctx.lineTo(xScale(range[0]), y + capSize);
  ctx.moveTo(xScale(range[1]), y - capSize);
  ctx.lineTo(xScale(range[1]), y + capSize);
  ctx.stroke();
}

function evidenceFocusRangeRows(chartFocus) {
  if (chartFocus.kind === "pair" && chartFocus.pair?.published) {
    const published = chartFocus.pair.published;
    return [
      {
        ...chartFocus.pair.left,
        paceAtAnchor: strictFiniteNumber(
          published.left_anchor?.point_estimate_s,
        ),
        observedRangeAtAnchor: finiteInterval(
          published.left_anchor?.weighted_empirical_central_80_s,
        ),
        stabilityAtAnchor: finiteInterval(
          published.left_anchor?.delete_block_stability_80_s,
        ),
        fuelScenarioValues: published.left_anchor?.fuel_sensitivity_s || {},
      },
      {
        ...chartFocus.pair.right,
        paceAtAnchor: strictFiniteNumber(
          published.right_anchor?.point_estimate_s,
        ),
        observedRangeAtAnchor: finiteInterval(
          published.right_anchor?.weighted_empirical_central_80_s,
        ),
        stabilityAtAnchor: finiteInterval(
          published.right_anchor?.delete_block_stability_80_s,
        ),
        fuelScenarioValues: published.right_anchor?.fuel_sensitivity_s || {},
      },
    ].filter((item) => item.paceAtAnchor !== null);
  }
  if (chartFocus.kind === "clique") {
    return chartFocus.series.filter(
      (item) => strictFiniteNumber(item.paceAtAnchor) !== null,
    );
  }
  return [];
}

function curveRangeTooltip(item, anchorAge, focusKind) {
  const fuel = item.fuelScenarioValues || {};
  return [
    `<strong>${escapeHTML(curveSeriesIdentity(item))}</strong>`,
    `<span>共同胎龄 ${escapeHTML(formatDecimal(anchorAge, 1))} · 条件点估计 ${escapeHTML(formatSeconds(item.paceAtAnchor, 3))}</span>`,
    `<span>经验残差 80% ${escapeHTML(formatRange(item.observedRangeAtAnchor, 3))}</span>`,
    `<span>删块敏感性 80% ${escapeHTML(formatRange(item.stabilityAtAnchor, 3))}</span>`,
    `<span>燃油情景 L ${escapeHTML(formatSeconds(fuel.low, 3))} · B ${escapeHTML(formatSeconds(fuel.base, 3))} · H ${escapeHTML(formatSeconds(fuel.high, 3))}</span>`,
    `<small>${focusKind === "pair" ? "仅对当前 comparable 成对比较有效" : "完整 comparable clique"}；三类范围均不是 CI / PI</small>`,
  ].join("");
}

function renderStintCommonAgeRangeChart(canvasId, emptyId, model, year) {
  const canvas = $(canvasId);
  const empty = $(emptyId);
  const chartFocus = selectedEvidenceChartFocus(model);
  const anchorAge = chartFocus.kind === "pair"
    ? strictFiniteNumber(
      chartFocus.pair?.published?.comparison_tyre_age_laps,
    )
    : strictFiniteNumber(model.anchorAge);
  const drawable = evidenceFocusRangeRows(chartFocus);
  if (anchorAge === null || drawable.length < 2) {
    canvas.hidden = true;
    empty.hidden = false;
    empty.textContent = chartFocus.kind === "stint"
      ? "单段审计不生成跨车共同胎龄范围；请选择一组 comparable Stint 对。"
      : "当前焦点没有至少两条可直接比较的共同胎龄发布证据。";
    canvas.dataset.fuelScenarioMarkers = "0";
    return;
  }
  const directOrder = chartFocus.kind === "clique";
  const ordered = directOrder
    ? drawable.slice().sort(
      (a, b) => Number(a.paceAtAnchor) - Number(b.paceAtAnchor),
    )
    : drawable;
  const focusDescription = directOrder
    ? "完整 comparable 窗口，允许在当前共同支持内排序"
    : "当前 comparable Stint 对的直接比较，不外推为跨对全序";
  const pairDelta = chartFocus.kind === "pair"
    ? strictFiniteNumber(
      chartFocus.pair?.published?.left_minus_right_pace_s,
    )
    : null;
  const pairDeltaLabel = pairDelta === null
    ? null
    : `${chartFocus.pair.left.row.driver}−${chartFocus.pair.right.row.driver} ${formatSigned(pairDelta, 3)}s`;
  canvas.hidden = false;
  empty.hidden = true;
  canvas.setAttribute?.(
    "aria-label",
    `${model.selectedCompound} ${chartFocus.label}在共同胎龄 ${formatDecimal(anchorAge, 1)} 圈的经验残差中间80%、删块敏感性80%、low/base/high 离散燃油情景与条件配速点估计；${focusDescription}`,
  );
  const minimumHeight = Math.max(320, 128 + ordered.length * 68);
  const { ctx, width, height } = curveChartContext(canvas, minimumHeight);
  const pad = {
    left: width < 520 ? 78 : 108,
    right: width < 520 ? 18 : 68,
    top: 52,
    bottom: 48,
  };
  const innerWidth = width - pad.left - pad.right;
  const innerHeight = height - pad.top - pad.bottom;
  const allValues = ordered.flatMap((item) => [
    item.paceAtAnchor,
    ...(item.observedRangeAtAnchor || []),
    ...(item.stabilityAtAnchor || []),
    ...Object.values(item.fuelScenarioValues || {}),
  ]).map(strictFiniteNumber).filter((value) => value !== null);
  let minimum = Math.min(...allValues);
  let maximum = Math.max(...allValues);
  if (maximum <= minimum) maximum = minimum + 0.1;
  const padding = Math.max((maximum - minimum) * 0.1, 0.04);
  minimum -= padding;
  maximum += padding;
  const xScale = (value) => pad.left
    + (value - minimum) / (maximum - minimum) * innerWidth;

  for (let index = 0; index <= 4; index += 1) {
    const ratio = index / 4;
    const value = minimum + (maximum - minimum) * ratio;
    const x = xScale(value);
    ctx.strokeStyle = "rgba(255,255,255,.06)";
    ctx.lineWidth = 1;
    ctx.setLineDash?.([]);
    ctx.beginPath();
    ctx.moveTo(x, pad.top);
    ctx.lineTo(x, height - pad.bottom);
    ctx.stroke();
    ctx.fillStyle = "#8c98aa";
    ctx.textAlign = "center";
    ctx.fillText(value.toFixed(3), x, height - pad.bottom + 16);
  }
  const tooltipTargets = [];
  let fuelScenarioMarkers = 0;
  ordered.forEach((item, index) => {
    const y = pad.top + (index + 0.5) / ordered.length * innerHeight;
    const style = curveSeriesLineStyle(year, item, model.series);
    if (index % 2 === 0) {
      ctx.fillStyle = "rgba(255,255,255,.022)";
      ctx.fillRect(
        pad.left,
        pad.top + index / ordered.length * innerHeight,
        innerWidth,
        innerHeight / ordered.length,
      );
    }
    if (index > 0) {
      const separatorY = pad.top + index / ordered.length * innerHeight;
      ctx.strokeStyle = "rgba(255,255,255,.055)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(pad.left, separatorY);
      ctx.lineTo(width - pad.right, separatorY);
      ctx.stroke();
    }
    ctx.fillStyle = "#cbd4e1";
    ctx.globalAlpha = 1;
    ctx.textAlign = "right";
    ctx.fillText(
      `${item.row.driver} #${item.row.stint_number}`,
      pad.left - 9,
      y - 5,
    );
    ctx.fillStyle = "#778397";
    ctx.fillText(String(item.row.team || "").slice(0, 13), pad.left - 9, y + 10);
    drawRangeWhisker(ctx, item.observedRangeAtAnchor, y - 12, xScale, {
      color: "#a8b2c1",
      lineWidth: 1.5,
      alpha: 0.72,
      capSize: 3,
    });
    drawRangeWhisker(ctx, item.stabilityAtAnchor, y, xScale, {
      color: style.color,
      lineWidth: 2.2,
      alpha: 0.9,
      capSize: 4,
    });
    ctx.globalAlpha = 1;
    ctx.setLineDash?.([]);
    drawCurveMarker(ctx, xScale(item.paceAtAnchor), y, {
      shape: "diamond",
      color: style.color,
      radius: 4,
      filled: true,
    });
    const fuelEntries = [
      ["L", strictFiniteNumber(item.fuelScenarioValues?.low)],
      ["B", strictFiniteNumber(item.fuelScenarioValues?.base)],
      ["H", strictFiniteNumber(item.fuelScenarioValues?.high)],
    ].filter((entry) => entry[1] !== null);
    fuelEntries.forEach(([label, value]) => {
      const x = xScale(value);
      drawCurveMarker(ctx, x, y + 13, {
        shape: "circle",
        color: style.color,
        radius: 2.5,
        filled: label === "B",
      });
      ctx.fillStyle = style.color;
      ctx.textAlign = "center";
      ctx.fillText(label, x, y + 24);
      fuelScenarioMarkers += 1;
      tooltipTargets.push({
        x,
        y: y + 13,
        radius: 15,
        html: curveRangeTooltip(item, anchorAge, chartFocus.kind),
      });
    });
    if (width >= 520) {
      ctx.fillStyle = style.color;
      ctx.textAlign = "left";
      ctx.fillText(
        formatDecimal(item.paceAtAnchor, 3),
        width - pad.right + 8,
        y,
      );
    }
    tooltipTargets.push({
      x: xScale(item.paceAtAnchor),
      y,
      radius: 16,
      html: curveRangeTooltip(item, anchorAge, chartFocus.kind),
    });
  });
  canvas.dataset.fuelScenarioMarkers = String(fuelScenarioMarkers);
  canvas.dataset.focusKind = chartFocus.kind;
  ctx.globalAlpha = 1;
  ctx.fillStyle = "#8c98aa";
  ctx.textAlign = "left";
  const rangeHeading = directOrder
    ? `完整可比窗口 · 允许排序 · 共同胎龄 ${formatDecimal(anchorAge, 1)}`
    : `${pairDeltaLabel || "成对直接可比"} @ 胎龄 ${formatDecimal(anchorAge, 1)} · comparable`;
  ctx.fillText(
    rangeHeading,
    pad.left,
    14,
  );
  ctx.fillText("左侧圈时更短 · L / B / H 为离散情景，不是连续区间", pad.left, 32);
  ctx.textAlign = "center";
  ctx.fillText("共同胎龄条件配速（s）", pad.left + innerWidth / 2, height - 10);
  attachStintCurveTooltip(canvas, tooltipTargets);
}

function curveWindowOptionLabel(window) {
  return [
    `跨队窗口 ${window.crossTeamIndex}/${window.crossTeamCount}`,
    `${window.distinctTeams} 队 ${window.series.length} 车`,
    `阶段中点 L${formatDecimal(window.phaseMidpointMinimum, 1)}–${formatDecimal(window.phaseMidpointMaximum, 1)}`,
    `共同胎龄 ${formatDecimal(window.commonAgeMinimum, 1)}–${formatDecimal(window.commonAgeMaximum, 1)}`,
  ].join(" · ");
}

function hideStintCurveCharts(reason) {
  for (const [canvasId, emptyId] of [
    ["stintDegradationChart", "stintDegradationEmpty"],
    ["stintPaceChart", "stintPaceEmpty"],
  ]) {
    $(canvasId).hidden = true;
    $(canvasId).dataset.clippedPoints = "0";
    $(canvasId).dataset.fuelScenarioMarkers = "0";
    $(emptyId).hidden = false;
    $(emptyId).textContent = reason;
  }
  const tooltip = $("stintCurveHoverTooltip");
  if (tooltip) tooltip.hidden = true;
}

function renderStintCurveExplorer(
  report,
  preferredCompound = $("stintCurveCompoundSelect").value || null,
  preferredWindowKey = $("stintCurveWindowSelect").value || null,
  curveEvidenceState = state.curveEvidenceState,
) {
  const explorer = $("stintCurveExplorer");
  const teamScope = $("stintTeamSelect").value || "PRIMARY";
  const rows = rowsInTeamScope(report.stint_dossiers || [], report, teamScope);
  const model = buildStintCurveExplorerModel(
    report,
    rows,
    preferredCompound,
    preferredWindowKey,
    curveEvidenceState,
  );
  const compoundSelector = $("stintCurveCompoundSelect");
  const windowSelector = $("stintCurveWindowSelect");
  const focusSelector = $("stintCurveFocusSelect");
  const scaleSelector = $("stintCurveScaleSelect");
  const preferredFocusValue = focusSelector.value || null;
  explorer.hidden = false;
  explorer.dataset.status = model.status;
  explorer.dataset.mode = model.mode || "summary_projection";
  explorer.dataset.compound = String(model.selectedCompound || "").toLowerCase();
  configureStintCurvePresentation(model.mode);
  if (!model.groups.length) {
    const unavailableLabel = model.mode === "v17_evidence"
      ? "逐圈证据不可用"
      : "曲线暂不可用";
    const helpTopic = model.mode === "v17_evidence"
      ? "curve_projection"
      : "legacy_curve_unavailable";
    compoundSelector.innerHTML = "";
    windowSelector.innerHTML = "";
    focusSelector.innerHTML = "";
    compoundSelector.disabled = true;
    windowSelector.disabled = true;
    focusSelector.disabled = true;
    scaleSelector.disabled = true;
    $("stintCurveSummary").innerHTML = `<strong>${escapeHTML(unavailableLabel)}：</strong>${escapeHTML(model.reason)} ${helpButton(helpTopic, "解释曲线证据状态")}`;
    $("stintCurveExtremes").innerHTML = "";
    $("stintCurveLegend").innerHTML = "";
    hideStintCurveEvidenceAudit();
    hideStintCurveCharts(model.reason);
    return model;
  }

  compoundSelector.disabled = false;
  windowSelector.disabled = false;
  compoundSelector.innerHTML = model.groups.map((group) => (
    `<option value="${escapeHTML(group.compound)}">${escapeHTML(group.presentation.label)} · ${group.windows.length} 个跨队窗口</option>`
  )).join("");
  compoundSelector.value = model.selectedCompound;
  windowSelector.innerHTML = model.selectedGroup.windows.map((window) => (
    `<option value="${escapeHTML(window.key)}">${escapeHTML(curveWindowOptionLabel(window))}</option>`
  )).join("");
  windowSelector.value = model.selectedWindowKey;
  let selectedFocus = null;
  if (model.mode === "v17_evidence") {
    selectedFocus = configureEvidenceChartFocus(model, preferredFocusValue);
    scaleSelector.disabled = !selectedFocus;
  } else {
    focusSelector.innerHTML = '<option value="">仅逐圈发布证据可用</option>';
    focusSelector.value = "";
    focusSelector.disabled = true;
    scaleSelector.disabled = true;
  }
  compoundSelector.onchange = () => renderStintCurveExplorer(
    report,
    compoundSelector.value,
    null,
    curveEvidenceState,
  );
  windowSelector.onchange = () => renderStintCurveExplorer(
    report,
    compoundSelector.value,
    windowSelector.value,
    curveEvidenceState,
  );
  focusSelector.onchange = () => renderStintCurveExplorer(
    report,
    compoundSelector.value,
    windowSelector.value,
    curveEvidenceState,
  );
  scaleSelector.onchange = () => renderStintCurveExplorer(
    report,
    compoundSelector.value,
    windowSelector.value,
    curveEvidenceState,
  );
  if (selectedFocus) {
    $("stintPrimaryCurveMeta").textContent = (
      `${selectedFocus.label} · 点透明度 = 分析权重`
    );
    if (selectedFocus.kind === "clique") {
      $("stintSecondaryCurveMeta").textContent = (
        "完整可比窗口 · 左侧圈时更短 · 允许排序"
      );
    } else if (selectedFocus.kind === "pair") {
      const pairDelta = strictFiniteNumber(
        selectedFocus.pair?.published?.left_minus_right_pace_s,
      );
      const pairAge = strictFiniteNumber(
        selectedFocus.pair?.published?.comparison_tyre_age_laps,
      );
      const pairText = pairDelta === null
        ? "成对 comparable"
        : `${selectedFocus.pair.left.row.driver}−${selectedFocus.pair.right.row.driver} ${formatSigned(pairDelta, 3)}s${pairAge === null ? "" : ` @ 胎龄 ${formatDecimal(pairAge, 1)}`}`;
      $("stintSecondaryCurveMeta").textContent = (
        `${pairText} · 可比对`
      );
    } else {
      $("stintSecondaryCurveMeta").textContent = (
        "单段审计 · 无跨车共同胎龄范围"
      );
    }
  }

  const strictConfirmed = model.series.filter(
    (item) => item.row.strict_confirmation?.status === "confirmed",
  ).length;
  const summary = model.mode === "v17_evidence"
    ? [
      `${teamScopeLabel(report, teamScope)} · ${model.selectedGroup.presentation.label}`,
      `${model.selectedWindow.distinctTeams} 个车队、${model.series.length} 条逐圈证据`,
      model.commonSupportAvailable
        ? `共同胎龄支持 ${formatDecimal(model.commonAgeMinimum, 1)}–${formatDecimal(model.commonAgeMaximum, 1)} 圈`
        : "共同胎龄支持不可用",
      strictFiniteNumber(model.anchorAge) !== null
        ? `共同评价胎龄 ${formatDecimal(model.anchorAge, 1)} 圈`
        : "共同评价胎龄不可识别",
      `直接可比 ${model.pairwiseAudit?.filter((row) => row.status === "comparable").length || 0}/${model.pairwiseAudit?.length || 0} 对`,
      model.directRankingAllowed ? "允许全窗口直接排序" : "仅审计，不形成全序",
      selectedFocus ? `图表焦点：${selectedFocus.label}` : "图表焦点不可用",
    ].join(" · ")
    : [
      `${teamScopeLabel(report, teamScope)} · ${model.selectedGroup.presentation.label}`,
      `${model.selectedWindow.distinctTeams} 个车队、${model.series.length} 辆车`,
      `共同胎龄支持 ${formatDecimal(model.commonAgeMinimum, 1)}–${formatDecimal(model.commonAgeMaximum, 1)} 圈`,
      `共同评价胎龄 ${formatDecimal(model.anchorAge, 1)} 圈`,
      `严格复核 ${strictConfirmed}/${model.series.length}`,
      "v16 summary-only",
    ].join(" · ");
  const summaryNotes = [
    model.reason,
    model.mode === "summary_projection" ? model.summaryOnlyReason : null,
  ].filter(Boolean);
  $("stintCurveSummary").innerHTML = `<strong>当前曲线：</strong>${escapeHTML(summary)}${summaryNotes.length ? ` · ${escapeHTML(summaryNotes.join(" · "))}` : ""} ${helpButton("curve_projection", "解释当前曲线")}`;
  if (model.status !== "ready") {
    $("stintCurveExtremes").innerHTML = "";
    $("stintCurveLegend").innerHTML = "";
    hideStintCurveEvidenceAudit();
    hideStintCurveCharts(model.reason || "当前窗口不满足曲线发布门。");
    return model;
  }
  const year = reportSeason(null, report);
  if (model.mode === "v17_evidence") {
    renderStintCurveEvidenceCards(model);
    renderStintCurveEvidenceLegend(model, year);
    renderStintCurveEvidenceAudit(model);
    renderStintEvidenceScatterChart(
      "stintDegradationChart",
      "stintDegradationEmpty",
      model,
      year,
    );
    renderStintCommonAgeRangeChart(
      "stintPaceChart",
      "stintPaceEmpty",
      model,
      year,
    );
  } else {
    hideStintCurveEvidenceAudit();
    renderStintCurveExtremes(model);
    renderStintCurveLegend(model, year);
    renderStintCurveChart(
      "stintDegradationChart",
      "stintDegradationEmpty",
      model,
      year,
      "degradationPoints",
      {
        yLabel: "相对评价点 Δ配速（s）",
        digits: 3,
        includeZero: true,
        ariaLabel: `${model.selectedCompound} 同配方跨车队轮胎衰减线性代理曲线`,
      },
    );
    renderStintCurveChart(
      "stintPaceChart",
      "stintPaceEmpty",
      model,
      year,
      "pacePoints",
      {
        yLabel: "条件配速（s）",
        digits: 3,
        ariaLabel: `${model.selectedCompound} 同配方跨车队 Stint 条件配速线性代理曲线`,
      },
    );
  }
  return model;
}

function renderDecomposition(report) {
  const panel = $("decompositionPanel");
  if (!isRaceDossier(report)) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;
  const rows = report.vehicle_driver_decomposition || [];
  $("decompositionTable").innerHTML = rows.length
    ? `<table><thead><tr><th>车队</th><th>车手</th><th>观测累计差</th><th>车辆共同基线</th><th>当场兑现偏差</th><th>策略 / 赛道位置</th><th>未解释残差</th><th>当场解读 ${helpButton("seconds_decomposition", "解释秒数拆分边界")}</th></tr></thead><tbody>${rows.map((row) => {
      const components = row.components || {};
      return `<tr><td>${escapeHTML(row.team)}</td><td><strong>${escapeHTML(row.driver)}</strong></td><td>${formatSignedSpan(row.observed_accounted_delta_s, 2)}s</td><td>${formatSignedSpan(components.vehicle_common_baseline_s, 2)}s</td><td>${formatSignedSpan(components.driver_realization_deviation_s, 2)}s</td><td>${formatSignedSpan(components.strategy_track_position_proxy_s, 2)}s</td><td>${formatSignedSpan(components.unexplained_residual_s, 2)}s</td><td>${escapeHTML(decompositionNarrative(row))}</td></tr>`;
    }).join("")}</tbody></table>`
    : emptyEvidence("当前比赛没有达到车辆—车手秒数分解门。 ");
}

function renderDataFunnel(report) {
  const panel = $("raceCoveragePanel");
  if (!isRaceDossier(report)) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;
  const coverage = report.coverage || {};
  const ablation = report.simulation_proxy_audit?.ablation || {};
  const inclusive = report.inclusive_robust_model_audit || {};
  const inclusiveEnabled = Boolean(inclusive.status && inclusive.status !== "not_enabled");
  const disposition = report.event_ledger?.primary_disposition_counts || {};
  const stints = report.stint_dossiers || [];
  const reportingTeams = new Set((coverage.teams || []).map((row) => row.team));
  const reportingStints = stints.filter((row) => reportingTeams.has(row.team));
  const controlStints = stints.filter((row) => !reportingTeams.has(row.team));
  const valid = (rows) => rows.filter((row) => row.status === "valid").length;
  const observed = Number(coverage.observed_field_laps);
  const clean = Number(ablation.clean_air_modelable_laps);
  const conditional = Number(ablation.conditional_proxy_eligible_laps);
  const strictConfirmed = (rows) => rows.filter((row) => row.strict_confirmation?.status === "confirmed").length;
  const candidate = Number(inclusive.candidate_laps);
  const effective = Number(inclusive.effective_weight_mass);
  const strict = Number(inclusive.strict_confirmation_laps);
  const stages = inclusiveEnabled
    ? [
      ["全场观测车手圈", observed, observed],
      ["物理可读候选圈", candidate, observed],
      ["样本外有效权重质量", effective, candidate],
      ["严格干净复核圈", strict, candidate],
      ["全场有效 Stint", valid(stints), stints.length],
      ["严格确认 Stint", strictConfirmed(stints), valid(stints)],
    ]
    : [
      ["全场观测车手圈", observed, observed],
      ["原始干净可建模圈", clean, observed],
      ["条件代理可分析圈", conditional, observed],
      ["全场有效 Stint", valid(stints), stints.length],
      ["四队有效 Stint", valid(reportingStints), reportingStints.length],
      ["中游/后排控制 Stint", valid(controlStints), controlStints.length],
    ];
  const dispositionLabels = inclusiveEnabled
    ? {
      model_eligible: "严格干净复核",
      traffic_separate: "交通/脏空气（软权重纳入）",
      quality_missing: "质量异常（可读则软权重纳入）",
      event_excluded: "非绿旗/比赛事件（可读则软权重纳入）",
      pit_excluded: "进出站边界（可读则软权重纳入）",
      unknown_unaccounted: "其他异常（可读则软权重纳入）",
    }
    : {
      model_eligible: "原始模型可用",
      traffic_separate: "交通单列",
      quality_missing: "质量门未过",
      event_excluded: "非绿旗/比赛事件排除",
      pit_excluded: "进出站边界排除",
      unknown_unaccounted: "异常慢圈等单列",
    };
  const dispositionText = Object.entries(disposition)
    .sort((a, b) => Number(b[1]) - Number(a[1]))
    .map(([key, value]) => `${dispositionLabels[key] || key} ${formatNumber(value)} 圈`)
    .join("；");
  const failureCounts = {};
  stints.forEach((row) => (row.gate_failures || []).forEach((reason) => {
    failureCounts[reason] = (failureCounts[reason] || 0) + 1;
  }));
  const failureLabels = {
    modelable_fraction_below_gate: "可分析圈占比不足",
    modelable_laps_below_gate: "可分析圈数不足",
    observed_laps_below_gate: "Stint 本身过短",
    tyre_age_span_below_gate: "胎龄跨度不足",
    candidate_fraction_below_gate: "物理可读候选覆盖不足",
    effective_laps_below_gate: "有效权重质量不足",
    effective_fraction_below_gate: "有效权重占比不足",
    internal_lap_continuity_below_0_90: "内部圈号连续性不足",
  };
  const failureText = Object.entries(failureCounts)
    .sort((a, b) => Number(b[1]) - Number(a[1]))
    .map(([key, value]) => `${failureLabels[key] || key} ${formatNumber(value)} 个`)
    .join("；");
  const crossfit = inclusive.crossfit_validation || {};
  const roleNote = inclusiveEnabled
    ? `全场控制样本 · ${inclusive.status} · OOF MAE ${formatNumber(crossfit.model_oof_mae_s)} 秒 · 基线改善 ${formatSigned(crossfit.mae_improvement_s, 3)} 秒`
    : "全场控制样本 · 主报告车队只定义发布对象";
  $("dataFunnel").innerHTML = `
    <div class="roleNote"><strong>样本角色：</strong>${escapeHTML(roleNote)} ${helpButton("data_funnel", "解释数据漏斗与样本角色")}</div>
    <div class="tableWrap"><table><thead><tr><th>漏斗阶段</th><th>数量</th><th>阶段覆盖</th></tr></thead><tbody>${stages.map(([label, value, denominator]) => `<tr><td>${escapeHTML(label)}</td><td>${formatNumber(value)} / ${formatNumber(denominator)}</td><td>${formatPercent(safeRatio(value, denominator))}</td></tr>`).join("")}</tbody></table></div>
    <div class="funnelNotes"><p><strong>车手圈去向：</strong>${escapeHTML(dispositionText || "无")}</p><p><strong>Stint 门失败（可重叠）：</strong>${escapeHTML(failureText || "无")}</p></div>`;
}

function segmentRows(report) {
  if (isRaceDossier(report)) {
    return (report.race_analysis?.descriptive_team_order || []).map((row) => ({
      label: row.team,
      delta_time_ms: Number(row.value) * 1000,
      confidence: report.publication_gate?.passed ? "published" : "audit_only",
    }));
  }
  return report.team_difference?.segment_profiles || report.segment_profiles || [];
}

function anomalyRows(report) {
  if (isRaceDossier(report)) {
    return (report.event_ledger?.entries || [])
      .slice()
      .sort((a, b) => Number(b.affected_laps || 0) - Number(a.affected_laps || 0))
      .slice(0, 24)
      .map((row) => ({
        primary_class: row.event_type,
        confidence: row.confidence,
        summary: `${row.driver} · ${row.affected_laps} 圈 · ${row.analysis_action}`,
        alternative_explanations: [row.source, row.directness],
      }));
  }
  return report.team_difference?.anomaly_episodes || report.anomaly_episodes || [];
}

function modeRows(report) {
  if (isRaceDossier(report)) {
    return (report.pre_race_tyre_envelope?.compounds || []).map((row) => ({
      mode_label: `${row.compound} · ${row.status}`,
      confidence: "historical_data_proxy",
      interpretation: [
        `合理 Stint ${formatIntervalRaw(row.reasonable_stint_length_laps)} 圈`,
        `历史 ${row.historical_stints} 个 Stint / ${row.historical_events} 场`,
        `衰减中位数 ${formatSigned(row.degradation_proxy_s_per_lap?.median, 4)} 秒/胎龄圈`,
      ].join("；"),
      alternative_explanations: ["真实燃油、胎温胎压、磨损量和车队内部模拟不可识别"],
    }));
  }
  return report.strategy_mode_fingerprints || report.strategy_modes || [];
}

function renderSegmentChart(rows) {
  const canvas = $("segmentChart");
  const empty = $("chartEmpty");
  const values = rows
    .map((row, index) => ({
      label: row.label || row.phase_id || row.zone || `S${index + 1}`,
      value: Number(row.delta_time_ms ?? row.time_loss_ms ?? row.external_condition_matched_gap_ms),
    }))
    .filter((row) => Number.isFinite(row.value));
  if (!values.length) {
    canvas.hidden = true;
    empty.hidden = false;
    return;
  }
  canvas.hidden = false;
  empty.hidden = true;
  const dpr = window.devicePixelRatio || 1;
  const width = canvas.clientWidth || 1100;
  const height = 360;
  canvas.width = width * dpr;
  canvas.height = height * dpr;
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, width, height);
  const pad = { left: 55, right: 20, top: 25, bottom: 78 };
  const innerW = width - pad.left - pad.right;
  const innerH = height - pad.top - pad.bottom;
  const maxAbs = Math.max(1, ...values.map((row) => Math.abs(row.value)));
  const zero = pad.top + innerH / 2;
  ctx.strokeStyle = "rgba(255,255,255,.18)";
  ctx.beginPath();
  ctx.moveTo(pad.left, zero);
  ctx.lineTo(width - pad.right, zero);
  ctx.stroke();
  const step = innerW / values.length;
  const barW = Math.max(3, step * 0.64);
  ctx.font = "10px Segoe UI";
  values.forEach((row, index) => {
    const x = pad.left + step * index + (step - barW) / 2;
    const h = Math.abs(row.value) / maxAbs * (innerH / 2 - 16);
    const y = row.value >= 0 ? zero - h : zero;
    ctx.fillStyle = row.value >= 0 ? "#ff4055" : "#55d98d";
    ctx.fillRect(x, y, barW, h);
    ctx.fillStyle = "#b7c0ce";
    ctx.textAlign = "center";
    ctx.fillText(`${row.value > 0 ? "+" : ""}${row.value.toFixed(0)}`, x + barW / 2, row.value >= 0 ? y - 6 : y + h + 13);
    ctx.save();
    ctx.translate(x + barW / 2, height - pad.bottom + 12);
    ctx.rotate(-Math.PI / 4);
    ctx.textAlign = "right";
    ctx.fillText(String(row.label).slice(0, 22), 0, 0);
    ctx.restore();
  });
  ctx.fillStyle = "#8c98aa";
  ctx.textAlign = "left";
  ctx.fillText("ms", 14, pad.top + 4);
}

function eventDriverTeamMap(report) {
  return new Map((report.stint_dossiers || [])
    .filter((row) => row.driver && row.team)
    .map((row) => [String(row.driver), String(row.team)]));
}

function isQualityEvent(row) {
  return /inaccurate|deleted|quality|missing|invalid/i.test(String(row.event_type || ""));
}

function eventGroupPresentation(row) {
  const eventType = String(row.event_type || "").toLowerCase();
  if (/non_green|safety|yellow|red|collision|accident|damage|incident/.test(eventType)) {
    return { key: "INCIDENT", label: "中断、事故与损伤代理", order: 0, open: true };
  }
  if (/traffic|dirty_air/.test(eventType)) {
    return { key: "TRAFFIC", label: "交通与脏空气代理", order: 1, open: true };
  }
  if (/pit/.test(eventType)) {
    return { key: "PIT", label: "进站与出站边界", order: 2, open: false };
  }
  if (/unexplained|residual|slow_lap/.test(eventType)) {
    return { key: "RESIDUAL", label: "未解释慢圈与残差", order: 3, open: true };
  }
  if (isQualityEvent(row)) {
    return { key: "QUALITY", label: "计时与数据质量", order: 4, open: false };
  }
  return { key: "OTHER", label: "其他可观测事件", order: 5, open: false };
}

function firstEventLap(row) {
  const starts = (Array.isArray(row.lap_ranges) ? row.lap_ranges : [])
    .map((range) => Number(Array.isArray(range) ? range[0] : range))
    .filter(Number.isFinite);
  return starts.length ? Math.min(...starts) : Number.POSITIVE_INFINITY;
}

function sortEventLedgerRows(rows) {
  return (Array.isArray(rows) ? rows.slice() : []).sort((a, b) => (
    firstEventLap(a) - firstEventLap(b)
    || compareText(a.driver, b.driver)
    || eventGroupPresentation(a).order - eventGroupPresentation(b).order
    || compareText(a.event_type, b.event_type)
  ));
}

function filterEventLedgerRows(
  report,
  teamScope = "PRIMARY",
  categoryScope = "CONTEXT",
) {
  const all = report.event_ledger?.entries || [];
  const driverTeams = eventDriverTeamMap(report);
  const primaryTeams = primaryReportTeams(report);
  return all.filter((row) => {
    const team = driverTeams.get(String(row.driver || ""));
    if (teamScope === "PRIMARY" && !primaryTeams.has(team)) return false;
    if (teamScope !== "ALL" && teamScope !== "PRIMARY" && team !== teamScope) return false;
    if (categoryScope === "QUALITY") return isQualityEvent(row);
    if (categoryScope === "CONTEXT") return !isQualityEvent(row);
    return true;
  });
}

function groupEventLedgerRows(rows) {
  const groups = new Map();
  for (const row of sortEventLedgerRows(rows)) {
    const presentation = eventGroupPresentation(row);
    if (!groups.has(presentation.key)) {
      groups.set(presentation.key, { presentation, rows: [] });
    }
    groups.get(presentation.key).rows.push(row);
  }
  return [...groups.values()].sort((a, b) => (
    a.presentation.order - b.presentation.order
    || compareText(a.presentation.key, b.presentation.key)
  ));
}

function renderEventLedgerGroup(group) {
  const affected = group.rows.reduce(
    (sum, row) => sum + Number(row.affected_laps || 0),
    0,
  );
  const open = group.presentation.open ? " open" : "";
  return `<details class="ledgerGroup"${open}><summary><span class="ledgerGroupTitle">${escapeHTML(group.presentation.label)}</span><span class="ledgerGroupMeta">${group.rows.length} 条记录 · ${formatNumber(affected)} 个重叠暴露圈次</span></summary><div class="tableWrap"><table><thead><tr><th>首个圈</th><th>车手</th><th>事件</th><th>全部圈段</th><th>暴露</th><th>主模型动作</th><th>严格复核动作</th><th>证据等级</th></tr></thead><tbody>${group.rows.map((row) => `<tr><td>L${Number.isFinite(firstEventLap(row)) ? formatNumber(firstEventLap(row)) : "—"}</td><td><strong>${escapeHTML(row.driver)}</strong></td><td>${escapeHTML(eventTypeLabel(row.event_type))}</td><td>${escapeHTML(lapRangesLabel(row.lap_ranges))}</td><td>${formatNumber(row.affected_laps)} 圈</td><td>${escapeHTML(analysisActionLabel(row.analysis_action))}</td><td>${escapeHTML(strictActionLabel(row.strict_confirmation_action))}</td><td>${escapeHTML(confidenceLabel(row.confidence))}</td></tr>`).join("")}</tbody></table></div></details>`;
}

function renderEventLedgerTable(
  report,
  teamScope = $("eventTeamSelect").value || "PRIMARY",
  categoryScope = $("eventCategorySelect").value || "CONTEXT",
) {
  const all = report.event_ledger?.entries || [];
  const rows = filterEventLedgerRows(report, teamScope, categoryScope);
  const groups = groupEventLedgerRows(rows);
  const affected = rows.reduce((sum, row) => sum + Number(row.affected_laps || 0), 0);
  const driverTeams = eventDriverTeamMap(report);
  const unmapped = all.filter((row) => !driverTeams.has(String(row.driver || ""))).length;
  const categoryLabel = categoryScope === "ALL"
    ? "完整账本"
    : categoryScope === "QUALITY"
      ? "仅计时与质量"
      : "比赛因素优先（计时质量已收起）";
  const unmappedText = unmapped
    ? `另有 ${unmapped} 条无法关联车队的记录；切到“全场所有车队”仍会保留。`
    : "";
  $("eventLedgerSummary").innerHTML = `<strong>当前范围：</strong>${escapeHTML(`${teamScopeLabel(report, teamScope)} · ${categoryLabel} · ${rows.length}/${all.length} 条 · ${affected} 个重叠暴露圈次${unmappedText ? ` · ${unmappedText}` : ""}`)} ${helpButton("event_context", "解释事件重叠与样本动作")}`;
  $("anomalyList").innerHTML = groups.length
    ? groups.map(renderEventLedgerGroup).join("")
    : emptyEvidence("当前筛选没有事件账本记录；这不等于确认比赛没有事故或损伤。 ");
}

function renderAnomalies(report) {
  const filterBar = $("eventFilterBar");
  const summary = $("eventLedgerSummary");
  if (isRaceDossier(report)) {
    filterBar.hidden = false;
    summary.hidden = false;
    const teamSelector = $("eventTeamSelect");
    const categorySelector = $("eventCategorySelect");
    renderTeamScopeSelect(teamSelector, report, "PRIMARY");
    categorySelector.value = "CONTEXT";
    teamSelector.onchange = () => renderEventLedgerTable(report);
    categorySelector.onchange = () => renderEventLedgerTable(report);
    renderEventLedgerTable(report);
    return;
  }
  filterBar.hidden = true;
  summary.hidden = true;
  const rows = anomalyRows(report);
  $("anomalyList").innerHTML = rows.length
    ? rows.map((row) => evidenceCard(
      row.primary_class || row.classification || "observed_divergence",
      row.confidence,
      `${lapRange(row)}${effectText(row)}${row.summary ? `。${row.summary}` : ""}`,
      row.alternative_explanations,
    )).join("")
    : emptyEvidence("没有通过当前门槛的事件或异常 Episode。");
}

function renderModes(report) {
  if (isRaceDossier(report)) {
    const current = report.tyre_strategy_envelope || {};
    const preRace = report.pre_race_tyre_envelope || {};
    const currentCompounds = current.compounds || [];
    const preRaceCompounds = preRace.compounds || [];
    const currentPit = report.pit_cycle_loss_proxy?.cycle_loss_s || {};
    const historicalPit = preRace.pit_cycle_loss_proxy?.cycle_loss_s || {};
    const currentWindows = `本场一停窗口 ${formatRange(current.one_stop_window_laps, 0)} 圈；两停窗口 ${(current.two_stop_window_laps || []).map((range) => formatRange(range, 0)).join(" + ") || "不可识别"}；实际模式样本一停 ${formatNumber(current.observed_driver_patterns?.one_stop)}、两停 ${formatNumber(current.observed_driver_patterns?.two_stop)}。`;
    $("modeList").innerHTML = `
      <div class="roleNote"><strong>轮胎结论：</strong>${escapeHTML(`${currentWindows} 本场进站周期中位数 ${formatDecimal(currentPit.median, 2)} 秒，历史中位数 ${formatDecimal(historicalPit.median, 2)} 秒。`)} ${helpButton("pit_cycle_loss", "解释进站周期代理")}</div>
      <h3 class="subheading">本场观测包络（赛后审计）</h3>
      ${tyreCompoundTable(currentCompounds, false)}
      <h3 class="subheading">赛前历史包络（只用此前分站）</h3>
      ${tyreCompoundTable(preRaceCompounds, true)}
      <details>
        <summary>查看一停 / 两停窗口与可识别成本分量</summary>
        ${strategyOptionTable(preRace)}
      </details>
      <details><summary>查看本场方法边界</summary><div class="funnelNotes"><p>${escapeHTML((preRace.limitations || current.limitations || []).join("；") || "无附加边界")}</p></div></details>`;
    return;
  }
  const rows = modeRows(report);
  $("modeList").innerHTML = rows.length
    ? rows.map((row) => evidenceCard(
      row.mode_label || row.label || "balanced_mode",
      row.confidence,
      row.interpretation || row.summary || "中性策略代理",
      row.alternative_explanations,
    )).join("")
    : emptyEvidence("历史数据不足，不能形成赛前轮胎策略包络。");
}

function renderRatings(report) {
  const source = report.ratings || report.driver_card || {};
  const rows = Array.isArray(source)
    ? source
    : Object.entries(source).map(([code, value]) => ({
      code,
      ...(value && typeof value === "object" ? value : { rating_mean: value }),
    }));
  $("ratingGrid").innerHTML = rows.length
    ? rows.map((row) => {
      const code = row.code || row.module_id || row.id || "—";
      const mean = Number(row.rating_mean ?? row.score);
      const interval = row.rating_interval_80 || row.interval_80 || row.rating_interval_95;
      const insufficient = !Number.isFinite(mean) || row.status === "insufficient";
      return `<div class="rating ${insufficient ? "insufficient" : ""}"><div class="code">${escapeHTML(code)}</div><div class="score">${insufficient ? "null / 未发布" : mean.toFixed(0)}</div><div class="interval">${escapeHTML(formatInterval(interval, row.confidence_grade))}</div></div>`;
    }).join("")
    : emptyEvidence("当前报告未发布能力评分。");
}

function renderEpisodes(report) {
  let rows;
  if (isRaceDossier(report)) {
    rows = report.coverage?.teams || [];
    const moduleByTeam = new Map((report.publication_gate?.module_gates?.teams || []).map((row) => [row.team, row]));
    $("episodeTable").innerHTML = rows.length
      ? `<table><thead><tr><th>车队</th><th>车手</th><th>严格复核圈</th><th>包容候选圈</th><th>有效权重质量</th><th>基线圈</th><th>有效 Stint</th><th>模块状态</th></tr></thead><tbody>${rows.map((row) => {
        const gate = moduleByTeam.get(row.team);
        const status = gate?.passed ? "可发布" : `审计：${(gate?.reasons || []).join("、") || "门未过"}`;
        return `<tr><td>${escapeHTML(row.team)}</td><td>${formatNumber(row.drivers)}</td><td>${formatNumber(row.modelable_laps)}</td><td>${formatNumber(row.pace_adjusted_laps)}</td><td>${formatDecimal(row.effective_pace_laps, 1)}</td><td>${formatNumber(row.baseline_laps)}</td><td>${formatNumber(row.valid_stints)}</td><td>${escapeHTML(status)}</td></tr>`;
      }).join("")}</tbody></table>`
      : emptyEvidence("没有覆盖审计数据。");
    return;
  }
  rows = report.opportunity_summary || report.episode_summary || [];
  $("episodeTable").innerHTML = rows.length
    ? `<table><thead><tr><th>车手</th><th>类型</th><th>机会</th><th>观测成功</th></tr></thead><tbody>${rows.map((row) => `<tr><td>${escapeHTML(row.driver)}</td><td>${escapeHTML(row.episode_type)}</td><td>${formatNumber(row.opportunities)}</td><td>${formatNumber(row.observed_successes)}</td></tr>`).join("")}</tbody></table>`
    : emptyEvidence("当前报告没有可用的 Episode 暴露汇总。");
}

function renderBoundaries(report) {
  const boundary = report.boundaries || {};
  const allowed = boundary.allowed_claims || report.allowed_claims || (isRaceDossier(report)
    ? [
      "完整比赛覆盖门通过范围内的排位潜力与正赛长距离条件配速",
      "等燃油场景、同配方、近胎龄和近比赛阶段的 Stint 比较",
      "车辆共同基线、当场兑现、策略位置与残差的加性秒数审计",
    ]
    : [
      "公开数据条件代理",
      "完整比赛覆盖审计",
      "带不确定性的描述性差异",
    ]);
  const unidentified = boundary.unidentified || [];
  const forbidden = boundary.forbidden_claims || report.forbidden_claims || [
    "具体机械故障",
    "真实燃油或轮胎内部状态",
    "伪精确车手能力贡献",
  ];
  $("boundaries").innerHTML = boundaryBlock("允许发布", allowed, "positive")
    + (unidentified.length ? boundaryBlock("仍不可识别", unidentified, "neutral") : "")
    + boundaryBlock("禁止声称", forbidden, "negative");
}

function conclusionCard(title, text, helpTopic = null) {
  return `<article class="conclusionCard"><h3>${escapeHTML(title)}${helpTopic ? helpButton(helpTopic, `解释${title}`) : ""}</h3><p>${escapeHTML(text)}</p></article>`;
}

function stateCard(title, status, text, cls, helpTopic = null) {
  return `<article class="stateCard ${escapeHTML(cls)}"><span class="stateStatus">${escapeHTML(status)}</span><h3>${escapeHTML(title)}${helpTopic ? helpButton(helpTopic, `解释${title}`) : ""}</h3><p>${escapeHTML(text)}</p></article>`;
}

function resultAuditLabel(row) {
  const difference = Number(row?.rank_difference);
  if (!Number.isFinite(difference)) return "无法比较";
  if (difference > 0) return `成绩代理比性能代理差 ${difference} 位`;
  if (difference < 0) return `成绩代理比性能代理好 ${Math.abs(difference)} 位`;
  return "成绩代理与性能代理排名一致";
}

function stintNarrative(row) {
  if (row.status !== "valid") {
    return `主层未通过：${(row.gate_failures || []).join("、") || "支持不足"}；保留审计但不发布配速结论。`;
  }
  const parts = [];
  const referencePace = Number(row.representative_tyre_age_pace_s);
  const referenceAge = Number(row.representative_tyre_age_laps);
  if (Number.isFinite(referencePace) && Number.isFinite(referenceAge)) {
    parts.push(
      `燃油与可观测条件修正后，在胎龄 ${referenceAge.toFixed(1)} 圈的代表配速为 ${referencePace.toFixed(3)} 秒`,
    );
  } else {
    parts.push("当前旧版报告尚未生成单一胎龄代表配速");
  }
  const delta = Number(row.cumulative_delta_to_reasonable_baseline_s);
  if (Number.isFinite(delta)) {
    parts.push(delta > 0
      ? `相对合理基线累计损失 ${Math.abs(delta).toFixed(2)} 秒`
      : `相对合理基线累计节省 ${Math.abs(delta).toFixed(2)} 秒`);
  } else {
    parts.push("累计合理基线不可识别");
  }
  const slope = Number(row.degradation_s_per_tyre_lap);
  if (Number.isFinite(slope)) {
    parts.push(slope > 0
      ? `胎龄每增加一圈，条件配速约慢 ${slope.toFixed(4)} 秒`
      : `未观察到正向掉速，斜率 ${slope.toFixed(4)} 秒/胎龄圈`);
  }
  if (row.materiality?.could_affect_track_position_proxy) {
    parts.push("幅度达到赛道位置代理门");
  }
  parts.push(row.strict_confirmation?.status === "confirmed"
    ? "严格复核通过"
    : "严格复核支持不足，曲线不得确认");
  if (!row.materiality?.final_position_change_identified) {
    parts.push("未证明改变最终名次");
  }
  return `${parts.join("；")}。`;
}

function decompositionNarrative(row) {
  const components = row.components || {};
  const driver = Number(components.driver_realization_deviation_s);
  const strategy = Number(components.strategy_track_position_proxy_s);
  const parts = [];
  if (Number.isFinite(driver)) {
    parts.push(driver > 0
      ? `当场车手—赛车组合兑现损失 ${driver.toFixed(2)} 秒`
      : `当场车手—赛车组合兑现节省 ${Math.abs(driver).toFixed(2)} 秒`);
  }
  if (Number.isFinite(strategy)) {
    parts.push(strategy > 0
      ? `策略/交通/赛道位置条件代理增加 ${strategy.toFixed(2)} 秒`
      : strategy < 0
        ? `策略/交通/赛道位置条件代理节省 ${Math.abs(strategy).toFixed(2)} 秒`
        : "未分配额外策略位置秒数");
  }
  return `${parts.join("；")}。`;
}

function eventTypeLabel(value) {
  return ({
    pit_in_boundary_proxy: "进站入口边界",
    pit_out_boundary_proxy: "出站边界与暖胎",
    traffic_dirty_air: "交通 / 脏空气暴露",
    inaccurate_or_deleted: "计时质量异常或删除圈",
    unexplained_slow_lap_proxy: "未解释慢圈代理",
    non_green: "非绿旗赛道状态",
  })[value] || value || "未知事件";
}

function analysisActionLabel(value) {
  return ({
    condition_and_soft_weight: "保留；事件条件入模，并按 OOF 可靠性软降权",
    condition: "保留并作为条件变量",
    exclude: "从该分析层排除",
    separate: "单独分析，不并入干净基线",
    censor: "删失该测量，不作物理归因",
  })[value] || value || "未声明";
}

function strictActionLabel(value) {
  return ({
    exclude: "严格层排除",
    separate: "严格层单列",
    censor: "严格层删失",
    include: "严格层保留",
  })[value] || value || "未声明";
}

function confidenceLabel(value) {
  return ({ high: "高", medium: "中", low: "低" })[value] || value || "未评级";
}

function lapRangesLabel(ranges) {
  if (!Array.isArray(ranges) || !ranges.length) return "—";
  return ranges.map((range) => {
    const start = Number(range?.[0]);
    const end = Number(range?.[1]);
    if (!Number.isFinite(start)) return "—";
    return Number.isFinite(end) && end !== start ? `L${start}–${end}` : `L${start}`;
  }).join("、");
}

function tyreCompoundTable(rows, historical) {
  if (!rows?.length) return emptyEvidence("该层没有可用配方包络。 ");
  const ordered = rows.slice().sort((a, b) => {
    const compoundA = normalizedCompound(a.compound);
    const compoundB = normalizedCompound(b.compound);
    return compoundPresentation(compoundA).order - compoundPresentation(compoundB).order
      || compareText(compoundA, compoundB);
  });
  return `<div class="tableWrap"><table><thead><tr><th>配方</th><th>${historical ? "历史支持" : "本场支持"}</th><th>合理长度</th><th>观测范围</th><th>衰减代理</th><th>状态</th></tr></thead><tbody>${ordered.map((row) => {
    const sample = historical
      ? `${formatNumber(row.historical_stints)} Stint / ${formatNumber(row.historical_events)} 场`
      : `${formatNumber(row.valid_stints)} Stint`;
    const validated = `${formatNumber(row.predictively_validated_curves)} 条曲线确认`;
    const degradation = row.degradation_proxy_s_per_lap
      ? `${formatDecimal(row.degradation_proxy_s_per_lap.median, 4)} s/圈<br><span class="muted">80% ${escapeHTML(formatRange(row.degradation_proxy_s_per_lap.interval_80, 4))}</span>`
      : "不可确认";
    return `<tr><td><strong>${escapeHTML(row.compound)}</strong></td><td>${sample}<br><span class="muted">${validated}</span></td><td>${escapeHTML(formatRange(row.reasonable_stint_length_laps, 0))} 圈</td><td>${escapeHTML(formatRange(row.observed_length_range_laps, 0))}${row.observed_length_range_laps ? " 圈" : ""}</td><td>${degradation}</td><td>${escapeHTML(tyreStatusLabel(row.status))}</td></tr>`;
  }).join("")}</tbody></table></div>`;
}

function strategyOptionTable(preRace) {
  const choose = (rows) => {
    const identified = (rows || []).filter((row) => row.identified_time_components_proxy);
    return (identified.length ? identified : rows || [])
      .slice()
      .sort((a, b) => {
        const windowA = a.pit_window_laps || a.first_pit_window_laps || [];
        const windowB = b.pit_window_laps || b.first_pit_window_laps || [];
        const startA = Number(windowA[0]);
        const startB = Number(windowB[0]);
        if (Number.isFinite(startA) && Number.isFinite(startB) && startA !== startB) {
          return startA - startB;
        }
        if (Number.isFinite(startA) !== Number.isFinite(startB)) {
          return Number.isFinite(startA) ? -1 : 1;
        }
        return compareText((a.sequence || []).join("|"), (b.sequence || []).join("|"));
      })
      .slice(0, 6);
  };
  const rows = [
    ...choose(preRace.one_stop_options).map((row) => ({ type: "一停", row })),
    ...choose(preRace.two_stop_options).map((row) => ({ type: "两停", row })),
  ];
  if (!rows.length) return emptyEvidence("历史支持不足，无法形成停站窗口。 ");
  return `<p class="muted strategyNote">已识别成本分量 ${helpButton("pit_cycle_loss", "解释方案截断与进站周期代理")}</p><div class="tableWrap"><table><thead><tr><th>类型</th><th>配方序列</th><th>进站窗口</th><th>衰减成本代理</th><th>进站周期代理</th><th>已识别成本合计</th></tr></thead><tbody>${rows.map(({ type, row }) => {
    const components = row.identified_time_components_proxy || {};
    const windows = type === "一停"
      ? formatRange(row.pit_window_laps, 0)
      : `${formatRange(row.first_pit_window_laps, 0)} + ${formatRange(row.second_pit_window_laps, 0)}`;
    return `<tr><td>${type}</td><td>${escapeHTML((row.sequence || []).join(" → "))}</td><td>${escapeHTML(windows)}</td><td>${formatDecimal(components.tyre_degradation_proxy_s?.median, 2)}s</td><td>${formatDecimal(components.pit_cycle_loss_proxy_s?.median, 2)}s</td><td>${formatDecimal(components.combined_identified_components_s?.median, 2)}s</td></tr>`;
  }).join("")}</tbody></table></div>`;
}

function tyreStatusLabel(value) {
  return ({
    usable_length_only: "仅长度代理可用",
    usable_length_and_curve_proxy: "长度与衰减曲线可用",
    insufficient_stints: "Stint 支持不足",
    usable_pre_race_length_only: "赛前长度代理可用",
    usable_pre_race_length_and_curve_proxy: "赛前长度与曲线可用",
    insufficient_history: "历史不足",
  })[value] || value || "未评级";
}

function curveStatusLabel(value) {
  return ({
    validated_confirmatory_proxy: "曲线经后段确认",
    audit_only_confirmation_no_gain: "后段确认无增益",
    audit_only_insufficient_confirmation_laps: "确认圈不足",
    not_run_strict_confirmation_failed: "严格确认未过，未运行曲线",
  })[value] || value || "无曲线";
}

function boundaryBlock(title, rows, cls) {
  return `<div class="boundary ${cls}"><h3>${escapeHTML(title)}</h3><ul>${rows.map((row) => `<li>${escapeHTML(typeof row === "string" ? row : JSON.stringify(row))}</li>`).join("")}</ul></div>`;
}

function evidenceCard(title, confidence, text, alternatives = []) {
  const alt = alternatives?.length ? `替代解释：${alternatives.join("；")}` : "";
  return `<div class="evidence"><div class="evidenceTitle"><span>${escapeHTML(title)}</span><span class="tag">${escapeHTML(confidence || "unrated")}</span></div><p>${escapeHTML(text || "—")}</p>${alt ? `<p>${escapeHTML(alt)}</p>` : ""}</div>`;
}

function emptyEvidence(text) { return `<div class="evidence"><p>${escapeHTML(text)}</p></div>`; }
function lapRange(row) { return row.lap_start == null ? "" : `L${row.lap_start}${row.lap_end && row.lap_end !== row.lap_start ? `–${row.lap_end}` : ""}`; }
function effectText(row) { const value = Number(row.impact_ms ?? row.delta_time_ms); return Number.isFinite(value) ? ` · ${value > 0 ? "+" : ""}${value.toFixed(0)}ms` : ""; }
function formatNumber(value) { if (value == null || value === "") return "—"; const n = Number(value); return Number.isFinite(n) ? n.toLocaleString("zh-CN") : "—"; }
function formatDecimal(value, digits = 2) { const n = Number(value); return Number.isFinite(n) ? n.toFixed(digits) : "—"; }
function formatSeconds(value, digits = 3) { const n = Number(value); return Number.isFinite(n) ? `${n.toFixed(digits)}s` : "—"; }
function formatPercent(value) { const n = Number(value); return Number.isFinite(n) ? `${(n <= 1 ? n * 100 : n).toFixed(1)}%` : "—"; }
function formatInterval(value, grade) { if (!Array.isArray(value)) return grade ? `置信 ${grade}` : ""; return `[${value.map((item) => Number(item).toFixed(0)).join(", ")}]${grade ? ` · ${grade}` : ""}`; }
function formatIntervalRaw(value) { return Array.isArray(value) ? `[${value.join(", ")}]` : "不可识别"; }
function formatRange(value, digits = 2) { return Array.isArray(value) && value.length >= 2 && value.every((item) => Number.isFinite(Number(item))) ? `[${Number(value[0]).toFixed(digits)}–${Number(value[1]).toFixed(digits)}]` : "不可识别"; }
function formatSigned(value, digits) { const n = Number(value); return Number.isFinite(n) ? `${n > 0 ? "+" : ""}${n.toFixed(digits)}` : "不可识别"; }
function formatSignedSpan(value, digits = 2) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  const normalized = Math.abs(n) < 0.5 * (10 ** -digits) ? 0 : n;
  const cls = normalized > 0 ? "numberPositive" : normalized < 0 ? "numberNegative" : "";
  return `<span class="${cls}">${normalized > 0 ? "+" : ""}${normalized.toFixed(digits)}</span>`;
}
function safeRatio(value, denominator) {
  const n = Number(value);
  const d = Number(denominator);
  return Number.isFinite(n) && Number.isFinite(d) && d > 0 ? n / d : null;
}
function setStatus(text) { $("status").textContent = text; }
function escapeHTML(value) { return String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char])); }

export {
  buildReportFilterModel,
  buildComparableStintWindows,
  buildStintCurveExplorerModel,
  curveSeriesLineStyle,
  filterEventLedgerRows,
  filterStintsForDisplay,
  groupEventLedgerRows,
  groupStintsForDisplay,
  loadStintCurveEvidence,
  normalizeCurveEvidenceState,
  publishedGridValueAt,
  render,
  renderEventLedgerTable,
  renderSelect,
  renderStintCurveExplorer,
  renderStintCommonAgeRangeChart,
  renderStintEvidenceScatterChart,
  renderStintTable,
  sortEventLedgerRows,
  sortReportsByTime,
  sortStintsByRaceSequence,
  teamCurveColor,
  validateReportTeamColours,
};
