const INDEX_URL = "data/reference-analysis-lab/v3/manifest.json";

const TARGET_META = {
  f1pace: {
    label: "A · F1pace",
    title: "Race pace、traffic ratio 与 pairwise delta",
    visual:
      "冻结逐点时间权重交通语义：前车距离 ≤ 速度 × 2 秒，且一圈严格超过 33% 才是 traffic lap；Abu Dhabi 与 Qatar 的公开图只用于 v1/v2 再验。",
    audit:
      "用 2023–2025 整场留出比较固定 universal Huber、可观测赛道族群与同赛道模型；2025 Abu Dhabi/Qatar 从所有训练折排除，pairwise delta 保持 70 场严格反对称。",
    foot:
      "MAE/P90/max 的单位是 traffic ratio fraction；方向一致率按固定 1/3 阈值判断。外部参考图没有进入 v3 拟合。",
  },
  deltadata: {
    label: "B · DeltaData",
    title: "Clean-air pace、燃油情景、衰减与同队比较",
    visual:
      "冻结 clean-air 与燃油情景语义：逐点 2 秒交通比例不高于 20%，公开燃油情景固定 0.032 秒/圈；2026 Mercedes 公开值继续标记 reference-informed。",
    audit:
      "目标是 v17 条件调整与原始圈时之差；模型只用训练折选择，按赛道报告有效圈、Kish ESS、配方/胎龄支持、衰减方向与同队同配方方向，不把不可比车队排成全序。",
    foot:
      "误差单位为条件调整秒数；方向一致率同时汇总同 Stint 衰减方向与同队同配方方向，不能解释为燃油或物理胎耗真值。",
  },
  fdataanalysis: {
    label: "C · FDataAnalysis",
    title: "距离轴、弯角/直道代理与跨年稳定性",
    visual:
      "每场独立建立 0–1 距离轴；连续信号线性插值、离散信号最近邻。P 编号和分段仅是数据驱动代理，不称官方弯号。",
    audit:
      "候选轴密度与平滑策略只在训练事件选择；测试事件完全留出。布局变化只称 observable geometry/sampling discontinuity proxy，并可直接禁止 track 模型。",
    foot:
      "误差单位为归一化单圈距离比例；起终点误差必须为零，峰值 P90/max 与跨年轮廓稳定性分别报告。",
  },
  f1telemetrydata: {
    label: "D · F1TelemetryData",
    title: "公开同场图表逐项数值对照",
    visual:
      "冻结刹车/lift/full/partial 互斥优先级、99% 全油门阈值、采样口径与 lane_duration/stop_duration 区别；仅 2025 Abu Dhabi 有同场公开参考。",
    audit:
      "七类图表任务各自显式列出 universal/cluster/track。单一 reference-informed 事件不能做外层模型选择；Q1/Q2/Q3 无冻结数值真值时三类候选均保持 NOT_TESTED 空值。",
    foot:
      "页面按图表任务保留原单位；没有同场公开参考的 69 场不以跨事件差异伪造 MAE。",
  },
  gptempo: {
    label: "E · GP Tempo",
    title: "官方 sector 端点与段内插值边界",
    visual:
      "连续信号线性、离散信号最近邻，段内时间按三个官方 sector 仿射缩放；401 点只控制显示采样，不改变官方端点。",
    audit:
      "2025 Abu Dhabi 的 270 个 sector 端点必须严格零误差。没有第二个冻结官方事件，cluster/track 不测试；没有公开逐点真值，segment MAE 永远为空。",
    foot:
      "max 显示官方 sector 端点绝对误差；段内 MAE 没有公开逐点真值，因此显示“—”而不是 0。",
  },
};

const REASON_LABELS = {
  track_and_cluster_candidates_failed_publication_gate:
    "单赛道与族群候选均未同时通过留出改善、尾部误差、覆盖、方向与 ESS 门，回退冻结 universal。",
  track_candidate_failed_publication_gate:
    "单赛道候选未通过全部发布门；赛道族群候选通过，发布 cluster。",
  NO_PUBLIC_SAME_EVENT_REFERENCE_FOR_CLUSTER_OR_TRACK_SELECTION:
    "没有冻结同场公开参考，无法选择 cluster/track，保留 universal 方法。",
  UNIVERSAL_REFERENCE_INFORMED_ONLY_NO_OUTER_HOLDOUT:
    "只有单一 reference-informed 事件，没有外层参考留出，保留 universal。",
  SINGLE_REFERENCE_EVENT_NO_CLUSTER_OR_TRACK_SELECTION:
    "只有一个官方端点事件，无法选择 cluster/track，保留 universal。",
  MISSING_FROZEN_OFFICIAL_SECTOR_INPUT:
    "缺少冻结官方 sector times 与 car channel，保持 universal/NOT_TESTED。",
};

const state = {
  index: null,
  publicManifest: null,
  report: null,
  combinedRegistry: null,
  targetId: "f1pace",
  trackKey: "abu-dhabi-grand-prix",
  taskId: null,
  layer: "audited_analysis",
  trackOrder: [],
};

const elements = Object.fromEntries(
  [
    "loadStatus",
    "runIdentity",
    "manifestStatus",
    "metricGrid",
    "targetSelect",
    "trackSelect",
    "taskControl",
    "taskSelect",
    "layerSelect",
    "selectionEyebrow",
    "selectionTitle",
    "selectionReason",
    "selectionPill",
    "selectionGate",
    "comparisonBody",
    "comparisonFoot",
    "coverageGrid",
    "supportList",
    "trackFamilyTitle",
    "trackMeta",
    "layerStatus",
    "methodVisual",
    "methodAudit",
    "visualMethodCard",
    "auditMethodCard",
    "boundaryList",
    "registryBody",
    "failureLedger",
    "identityFooter",
  ].map((id) => [id, document.getElementById(id)]),
);

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function sha256Text(text) {
  const bytes = new TextEncoder().encode(text);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

async function fetchJsonWithHash(url, expectedHash = null) {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`HTTP ${response.status} · ${url}`);
  }
  const text = await response.text();
  if (expectedHash) {
    const actualHash = await sha256Text(text);
    if (actualHash !== expectedHash) {
      throw new Error(`SHA-256 不一致 · ${url}`);
    }
  }
  return JSON.parse(text);
}

function number(value, digits = null) {
  if (value === null || value === undefined || value === "") return "—";
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "—";
  if (numeric === 0) return "0";
  const decimals = digits ?? (
    Math.abs(numeric) >= 100 ? 0
      : Math.abs(numeric) >= 10 ? 2
        : Math.abs(numeric) >= 1 ? 3
          : 6
  );
  return numeric.toLocaleString("zh-CN", {
    minimumFractionDigits: 0,
    maximumFractionDigits: decimals,
  });
}

function percent(value, digits = 1) {
  if (value === null || value === undefined || value === "") return "—";
  return `${number(Number(value) * 100, digits)}%`;
}

function sum(rows, key) {
  return rows.reduce((total, row) => total + (Number(row[key]) || 0), 0);
}

function parseFailures(value) {
  if (!value) return [];
  if (Array.isArray(value)) return value;
  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function modelLabel(value) {
  return {
    universal: "UNIVERSAL",
    cluster: "CLUSTER",
    track: "TRACK",
  }[value] || String(value || "—").toUpperCase();
}

function statusClass(status) {
  return String(status).startsWith("PASS") ? "pass" : "notTested";
}

function calendarRound(eventId) {
  const match = String(eventId).match(/^\d{4}-(\d{2})-/);
  return match ? Number(match[1]) : 999;
}

function buildTrackOrder() {
  const coverage = state.report.targets.gptempo.coverage || [];
  const season = coverage
    .filter((row) => Number(row.year) === 2025)
    .sort((left, right) => calendarRound(left.event_id) - calendarRound(right.event_id));
  const keys = season.map((row) => row.track_key);
  const remaining = state.report.observable_track_families
    .map((row) => row.track_key)
    .filter((key) => !keys.includes(key));
  state.trackOrder = [...keys, ...remaining];
}

function targetData() {
  return state.report.targets[state.targetId];
}

function registryRow() {
  return targetData().model_registry.find(
    (row) => row.track_key === state.trackKey,
  );
}

function trackRow() {
  return state.report.observable_track_families.find(
    (row) => row.track_key === state.trackKey,
  );
}

function metricRows() {
  const rows = targetData().model_comparison || [];
  if (rows.some((row) => Object.hasOwn(row, "track_key"))) {
    return rows.filter((row) => row.track_key === state.trackKey);
  }
  if (rows.some((row) => Object.hasOwn(row, "chart_task"))) {
    return rows.filter((row) => row.chart_task === state.taskId);
  }
  return rows;
}

function coverageRows() {
  return (targetData().coverage || []).filter(
    (row) => row.track_key === state.trackKey,
  );
}

function renderGlobalMetrics() {
  const selected = Object.values(state.combinedRegistry.targets)
    .flat()
    .filter((row) => row.selected_model_type !== "universal").length;
  const failedRuns = (state.index.targets || []).filter(
    (row) => row.status === "FAILED_POST_PUBLICATION_IDENTITY_AUDIT",
  ).length;
  const cards = [
    ["真实比赛", state.report.coverage.events, "22 + 24 + 24"],
    ["赛道", state.report.coverage.tracks, "按 2025 赛历排序"],
    ["图表目标", Object.keys(state.report.targets).length, "A–E 独立量纲"],
    ["专用模型发布", selected, "仅 DeltaData 通过 11 条"],
    ["失败运行留痕", failedRuns, "append-only · 未清理"],
  ];
  elements.metricGrid.innerHTML = cards
    .map(
      ([label, value, note]) => `
        <article class="metricCard">
          <span>${escapeHtml(label)}</span>
          <strong>${escapeHtml(value)}</strong>
          <small>${escapeHtml(note)}</small>
        </article>
      `,
    )
    .join("");
}

function renderTargetOptions() {
  elements.targetSelect.innerHTML = Object.entries(TARGET_META)
    .map(
      ([id, meta]) => `
        <option value="${id}"${id === state.targetId ? " selected" : ""}>
          ${escapeHtml(meta.label)}
        </option>
      `,
    )
    .join("");

  const tracks = new Map(
    state.report.observable_track_families.map((row) => [
      row.track_key,
      row.meeting,
    ]),
  );
  if (!tracks.has(state.trackKey)) {
    state.trackKey = state.trackOrder[0];
  }
  elements.trackSelect.innerHTML = state.trackOrder
    .map(
      (key, index) => `
        <option value="${escapeHtml(key)}"${key === state.trackKey ? " selected" : ""}>
          ${String(index + 1).padStart(2, "0")} · ${escapeHtml(tracks.get(key))}
        </option>
      `,
    )
    .join("");
}

function renderTaskOptions() {
  const tasks = [
    ...new Set(
      (targetData().model_comparison || [])
        .map((row) => row.chart_task)
        .filter(Boolean),
    ),
  ];
  elements.taskControl.hidden = tasks.length === 0;
  if (!tasks.length) {
    state.taskId = null;
    return;
  }
  if (!tasks.includes(state.taskId)) state.taskId = tasks[0];
  elements.taskSelect.innerHTML = tasks
    .map(
      (task) => `
        <option value="${escapeHtml(task)}"${task === state.taskId ? " selected" : ""}>
          ${escapeHtml(task)}
        </option>
      `,
    )
    .join("");
}

function renderSelection() {
  const meta = TARGET_META[state.targetId];
  const registry = registryRow();
  const track = trackRow();
  const selected = registry?.selected_model_type || "universal";
  const reason = REASON_LABELS[registry?.fallback_reason]
    || (selected === "track"
      ? "单赛道模型在完全留出的年份上稳定改善，并通过覆盖、尾部误差、方向与 Kish ESS 发布门。"
      : selected === "cluster"
        ? "可观测赛道族群模型通过全部发布门。"
        : "没有候选获得足够的留出证据，保留冻结 universal。");
  elements.selectionEyebrow.textContent =
    `${meta.label} · ${track?.meeting || state.trackKey}`;
  elements.selectionTitle.textContent = meta.title;
  elements.selectionReason.textContent = reason;
  elements.selectionPill.textContent = modelLabel(selected);
  elements.selectionPill.className = `modelChip ${selected}`;
  elements.selectionGate.textContent = selected === "universal"
    ? "fallback / fixed method"
    : "holdout gate passed";
}

function comparisonCoverage(row) {
  if (row.coverage !== null && row.coverage !== undefined) {
    const tests = row.test_events === null || row.test_events === undefined
      ? ""
      : ` · ${number(row.test_events, 0)} 场`;
    return `${percent(row.coverage)}${tests}`;
  }
  if (row.sector_endpoint_checks !== undefined) {
    return `${number(row.sector_endpoint_checks, 0)} 端点`;
  }
  return "—";
}

function gateCell(row) {
  if (row.publication_gate_passed === true) {
    return row.model_type === "universal" ? "冻结基线" : "PASS";
  }
  if (String(row.status).startsWith("NOT_TESTED")) return "NOT_TESTED";
  const failures = parseFailures(row.gate_failures);
  return failures.length ? failures.join(" · ") : "未过发布门";
}

function renderComparison() {
  const selected = registryRow()?.selected_model_type || "universal";
  const rows = metricRows();
  elements.comparisonBody.innerHTML = rows
    .map((row) => {
      const max = row.max_abs_error ?? row.max_abs_sector_endpoint_error_s;
      const mae = row.mae ?? row.segment_mae;
      const selectedClass = row.model_type === selected ? "selected" : "";
      return `
        <tr class="${selectedClass}">
          <td><span class="modelName">${escapeHtml(modelLabel(row.model_type))}</span></td>
          <td><span class="statusChip ${statusClass(row.status)}">${escapeHtml(row.status)}</span></td>
          <td>${number(mae)}</td>
          <td>${number(row.p90_abs_error)}</td>
          <td>${number(max)}</td>
          <td>${row.direction_accuracy == null ? "—" : percent(row.direction_accuracy)}</td>
          <td>${escapeHtml(comparisonCoverage(row))}</td>
          <td class="gateFail">${escapeHtml(gateCell(row))}</td>
        </tr>
      `;
    })
    .join("");
  if (!rows.length) {
    elements.comparisonBody.innerHTML =
      '<tr><td colspan="8">该组合没有可发布比较；保持 NOT_TESTED。</td></tr>';
  }
  elements.comparisonFoot.textContent = TARGET_META[state.targetId].foot;
}

function coverageSpec() {
  const rows = coverageRows();
  const row = rows[0] || {};
  if (state.targetId === "f1pace") {
    return {
      cards: [
        ["赛道事件", row.events, row.years],
        ["有效圈", row.eligible_driver_laps, `${row.driver_laps} 总圈`],
        ["Kish ESS", number(row.kish_ess, 1), "时间覆盖权重"],
        ["交通目标缺失", percent(row.traffic_target_missing_rate), "显式缺失率"],
        ["模型排除", percent(row.model_exclusion_rate), "绿旗/覆盖/缺失"],
        ["traffic lap", percent(row.traffic_lap_share), "严格 > 33%"],
      ],
      support: [
        ["配方共同支持", row.compounds || "—"],
        ["胎龄支持", `${number(row.tyre_age_min, 0)}–${number(row.tyre_age_max, 0)} laps`],
        ["交通比例 P90", number(row.traffic_ratio_p90)],
        ["2025 外部训练排除", "Abu Dhabi + Qatar"],
      ],
    };
  }
  if (state.targetId === "deltadata") {
    return {
      cards: [
        ["赛道事件", row.events, row.years],
        ["clean-air 点", row.clean_air_points, `${row.v17_points} v17 点`],
        ["Kish ESS", number(row.kish_ess, 1), "analysis weight"],
        ["交通字段缺失", percent(row.traffic_ratio_missing_rate), "显式缺失率"],
        ["模型排除", percent(row.model_exclusion_rate), "条件支持门"],
        ["交通比例均值", percent(row.traffic_ratio_mean), "clean-air 子集"],
      ],
      support: [
        ["配方共同支持", row.compounds || "—"],
        ["胎龄支持", `${number(row.tyre_age_min, 0)}–${number(row.tyre_age_max, 0)} laps`],
        ["燃油情景", "0.032 s/lap · 具名代理"],
        ["比较边界", "同队同配方 / 同 Stint；不形成全车队全序"],
      ],
    };
  }
  if (state.targetId === "fdataanalysis") {
    return {
      cards: [
        ["赛道事件", row.event_count, row.years],
        ["代表圈", row.representative_laps, `${row.profile_events} profiles`],
        ["profile 缺失", percent(row.track_profile_missing_rate), "事件级"],
        ["弯角代理", `${row.corner_proxy_count_min}–${row.corner_proxy_count_max}`, "非官方弯号"],
        ["直道代理", `${row.straight_proxy_count_min}–${row.straight_proxy_count_max}`, "数据驱动分段"],
        ["布局代理", row.layout_proxy_status === "OBSERVABLE_PROFILE_STABLE" ? "稳定" : "边界", "非官方布局结论"],
      ],
      support: [
        ["距离轴", "每场独立 0–1；端点严格闭合"],
        ["连续 / 离散", "linear / nearest"],
        ["候选选择", "仅训练事件 · tail-first"],
        ["track gate", row.track_model_allowed_by_layout_proxy ? "布局代理允许测试" : "布局代理禁止"],
      ],
    };
  }
  if (state.targetId === "f1telemetrydata") {
    const events = rows.length;
    const publicCount = rows.filter((item) => item.public_same_event_chart_reference).length;
    return {
      cards: [
        ["赛道事件", events, "排位/正赛独立"],
        ["正赛圈记录", number(sum(rows, "race_lap_rows"), 0), "冻结 lap universe"],
        ["准确推圈代理", number(sum(rows, "qualifying_accurate_push_laps"), 0), "整场最佳代理"],
        ["同场公开参考", publicCount, "仅 2025 Abu Dhabi"],
        ["公开参考缺失", percent(sum(rows, "public_reference_missing_rate") / Math.max(events, 1)), "事件级"],
        ["Q 阶段缺失", percent(sum(rows, "q_phase_missing_rate") / Math.max(events, 1)), "缺口不写成 0"],
      ],
      support: [
        ["图表任务", state.taskId || "—"],
        ["控制互斥优先级", "brake → lift → full → partial"],
        ["全油门阈值", "99%"],
        ["pit duration", "lane_duration 主值；stop_duration 分离"],
      ],
    };
  }
  const events = rows.length;
  const official = rows.filter(
    (item) => item.official_sector_times_and_car_channel_available,
  ).length;
  return {
    cards: [
      ["赛道事件", events, "本地几何可用"],
      ["官方端点输入", official, "仅 2025 Abu Dhabi"],
      ["官方输入缺失", percent(sum(rows, "official_sector_input_missing_rate") / Math.max(events, 1)), "事件级"],
      ["逐点公开真值", 0, "segment MAE NOT_TESTED"],
      ["逐点真值缺失", "100%", "不伪造 MAE"],
      ["端点检查", 270, "max error = 0 s"],
    ],
    support: [
      ["显示采样", "401 points"],
      ["连续 / 离散", "linear / nearest"],
      ["时间缩放", "official-sector affine"],
      ["专用模型", "无第二官方事件，cluster/track NOT_TESTED"],
    ],
  };
}

function renderCoverage() {
  const spec = coverageSpec();
  elements.coverageGrid.innerHTML = spec.cards
    .map(
      ([label, value, note]) => `
        <div class="coverageItem">
          <span>${escapeHtml(label)}</span>
          <strong>${escapeHtml(value ?? "—")}</strong>
          <small>${escapeHtml(note || "")}</small>
        </div>
      `,
    )
    .join("");
  elements.supportList.innerHTML = spec.support
    .map(
      ([term, value]) => `
        <dt>${escapeHtml(term)}</dt>
        <dd>${escapeHtml(value)}</dd>
      `,
    )
    .join("");
}

function renderTrackMeta() {
  const row = trackRow() || {};
  elements.trackFamilyTitle.textContent = row.observable_cluster || "赛道族群代理";
  const values = [
    ["赛道", row.meeting || state.trackKey],
    ["覆盖年份", row.years || "—"],
    ["速度带依据", `${number(row.median_speed_kph, 1)} km/h median`],
    ["弯角密度代理", `${number(row.corner_density_per_km, 2)} / km`],
    ["全油门距离占比", percent(row.full_throttle_distance_share)],
    ["DRS 距离占比", percent(row.drs_distance_share)],
    ["轮廓审计", row.layout_proxy_status || "—"],
  ];
  elements.trackMeta.innerHTML = values
    .map(
      ([term, value]) => `
        <dt>${escapeHtml(term)}</dt>
        <dd>${escapeHtml(value)}</dd>
      `,
    )
    .join("");
}

function renderMethods() {
  const meta = TARGET_META[state.targetId];
  elements.methodVisual.textContent = meta.visual;
  elements.methodAudit.textContent = meta.audit;
  elements.layerStatus.textContent = state.layer;
  elements.visualMethodCard.classList.toggle(
    "active",
    state.layer === "visual_replication",
  );
  elements.auditMethodCard.classList.toggle(
    "active",
    state.layer === "audited_analysis",
  );
  const boundaries = [
    ...state.report.boundaries.non_identifiable.slice(0, 5),
    "reference-informed ≠ blind holdout",
    "PAC / OVR / 跨任务总分不发布",
  ];
  elements.boundaryList.innerHTML = boundaries
    .map((item) => `<li>${escapeHtml(item)}</li>`)
    .join("");
}

function renderRegistry() {
  const rows = new Map(
    targetData().model_registry.map((row) => [row.track_key, row]),
  );
  const tracks = new Map(
    state.report.observable_track_families.map((row) => [row.track_key, row]),
  );
  elements.registryBody.innerHTML = state.trackOrder
    .map((key) => {
      const row = rows.get(key);
      const track = tracks.get(key);
      const reason = REASON_LABELS[row?.fallback_reason]
        || row?.fallback_reason
        || "候选通过全部留出门";
      return `
        <tr class="${key === state.trackKey ? "focused" : ""}">
          <td>${escapeHtml(track?.meeting || key)}</td>
          <td>${escapeHtml(row?.observable_cluster || track?.observable_cluster || "—")}</td>
          <td><span class="modelChip ${escapeHtml(row?.selected_model_type)}">${escapeHtml(modelLabel(row?.selected_model_type))}</span></td>
          <td class="gateFail">${escapeHtml(reason)}</td>
        </tr>
      `;
    })
    .join("");
}

function renderFailureLedger() {
  if (state.targetId === "f1telemetrydata") {
    const notTested = targetData().coverage.filter(
      (row) => !row.public_same_event_chart_reference,
    ).length;
    elements.failureLedger.textContent =
      `${notTested} 场无同场公开参考；七项三模型表逐项保留 NOT_TESTED 空值。`;
    return;
  }
  if (state.targetId === "gptempo") {
    elements.failureLedger.textContent =
      "69 场缺官方 sector/car channel；全部 70 场缺公开逐点真值，segment MAE 不生成。";
    return;
  }
  const registry = targetData().model_registry;
  const fallback = registry.filter(
    (row) => row.selected_model_type === "universal",
  ).length;
  elements.failureLedger.textContent =
    `${fallback}/24 条赛道回退 universal；NOT_TESTED 与失败门不转换为更漂亮的评分。`;
}

function syncUrl() {
  const url = new URL(window.location.href);
  url.searchParams.set("target", state.targetId);
  url.searchParams.set("track", state.trackKey);
  url.searchParams.set("layer", state.layer);
  if (state.taskId) url.searchParams.set("task", state.taskId);
  else url.searchParams.delete("task");
  history.replaceState(null, "", url);
}

function render() {
  renderTargetOptions();
  renderTaskOptions();
  renderSelection();
  renderComparison();
  renderCoverage();
  renderTrackMeta();
  renderMethods();
  renderRegistry();
  renderFailureLedger();
  syncUrl();
}

function installControls() {
  elements.targetSelect.addEventListener("change", () => {
    state.targetId = elements.targetSelect.value;
    state.taskId = null;
    render();
  });
  elements.trackSelect.addEventListener("change", () => {
    state.trackKey = elements.trackSelect.value;
    render();
  });
  elements.taskSelect.addEventListener("change", () => {
    state.taskId = elements.taskSelect.value;
    render();
  });
  elements.layerSelect.addEventListener("change", () => {
    state.layer = elements.layerSelect.value;
    render();
  });
}

function validateRelease(index, publicManifest, report, combinedRegistry) {
  if (
    index.schema_version !== "reference-analysis-lab-index-v3"
    || publicManifest.run_id !== index.latest_run_id
    || publicManifest.status !== "PASS_WITH_NOT_TESTED_GAPS"
    || report.schema_version !== "reference-analysis-track-validation-report-v3"
    || report.run_id !== index.latest_run_id
    || report.coverage.events !== 70
    || report.coverage.tracks !== 24
    || Object.keys(report.targets || {}).length !== 5
    || report.layers.visual_replication.causal_ranking_allowed !== false
    || report.layers.audited_analysis.not_tested_is_zero !== false
    || report.boundaries.cross_task_replication_score !== null
    || combinedRegistry.cross_task_total_score !== null
    || combinedRegistry.run_id !== index.latest_run_id
  ) {
    throw new Error("v3 页面数据契约不闭合");
  }
  const latestEntries = (index.targets || []).filter(
    (row) => row.run_id === index.latest_run_id,
  );
  if (
    latestEntries.length !== 1
    || latestEntries[0].status !== "PASS_WITH_NOT_TESTED_GAPS"
  ) {
    throw new Error("v3 latest 索引身份不唯一");
  }
}

async function boot() {
  const params = new URLSearchParams(window.location.search);
  if (TARGET_META[params.get("target")]) state.targetId = params.get("target");
  if (params.get("track")) state.trackKey = params.get("track");
  if (params.get("task")) state.taskId = params.get("task");
  if (["visual_replication", "audited_analysis"].includes(params.get("layer"))) {
    state.layer = params.get("layer");
  }
  elements.layerSelect.value = state.layer;

  const index = await fetchJsonWithHash(INDEX_URL);
  const latest = (index.targets || []).find(
    (row) => row.run_id === index.latest_run_id,
  );
  if (!latest) throw new Error("v3 latest run 未登记");
  const publicManifest = await fetchJsonWithHash(
    latest.manifest,
    latest.manifest_sha256,
  );
  const [report, combinedRegistry] = await Promise.all([
    fetchJsonWithHash(
      publicManifest.report.path,
      publicManifest.report.sha256,
    ),
    fetchJsonWithHash(
      publicManifest.model_registry.path,
      publicManifest.model_registry.sha256,
    ),
  ]);
  validateRelease(index, publicManifest, report, combinedRegistry);
  state.index = index;
  state.publicManifest = publicManifest;
  state.report = report;
  state.combinedRegistry = combinedRegistry;
  buildTrackOrder();
  renderGlobalMetrics();
  render();
  installControls();

  elements.runIdentity.textContent = report.run_id;
  elements.manifestStatus.textContent = "PASS · SHA verified · append-only";
  elements.loadStatus.textContent = "70 / 70 · 已验证";
  elements.loadStatus.classList.add("ready");
  elements.identityFooter.textContent =
    `${report.run_id} · report ${publicManifest.report.sha256.slice(0, 12)}… · registry ${publicManifest.model_registry.sha256.slice(0, 12)}…`;
  document.documentElement.dataset.ready = "true";
  window.__F1TR_TRACK_VALIDATION_V3_READY__ = true;
}

boot().catch((error) => {
  console.error(error);
  elements.loadStatus.textContent = "加载失败";
  elements.loadStatus.classList.add("error");
  elements.manifestStatus.textContent = error.message;
  elements.selectionTitle.textContent = "冻结产物未通过页面身份校验";
  elements.selectionReason.textContent =
    "请先运行 npm run build，再通过 http://127.0.0.1:5173/track-validation-v3.html 打开。";
  document.documentElement.dataset.ready = "error";
  window.__F1TR_TRACK_VALIDATION_V3_READY__ = false;
});
