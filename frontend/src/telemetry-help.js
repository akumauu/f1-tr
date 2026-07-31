const TOOLTIP_ID = "workbenchHelpTooltip";
const boundRoots = new WeakMap();

function finiteOr(value, fallback) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

export function createWorkbenchHelpTopics(comparisonRules = {}) {
  const tyreAgeGap = Number(
    comparisonRules.maximumReferenceTyreAgeGapLaps,
  );
  const phaseGap = Number(
    comparisonRules.maximumPhaseMidpointFractionGap,
  );
  const comparisonGate = Number.isFinite(tyreAgeGap) && Number.isFinite(phaseGap)
    ? `参考胎龄差不超过 ${tyreAgeGap} 圈，比赛阶段中点差不超过 ${phaseGap.toFixed(2)}`
    : "参考胎龄差与比赛阶段差均通过当前冻结阈值";
  return Object.freeze({
    report_navigation:
      "切换产品或赛季会重置后续筛选。分站按赛历顺序从新到旧，同一分站的报告版本从新到旧。",
    race_pace_direction:
      "排位单圈与正赛长距离分别计算，不合并为一个速度结论。时间越小越快；相对差值为负表示比基准更快。",
    event_context:
      "事件账本先记录交通、进出站、非绿旗、事故代理与计时质量，再决定圈是否保留、降权、单列或删失。事件可重叠，合计暴露圈次不是独立圈数。",
    comparable_window:
      `直接比较只接纳同配方且有效的 Stint；${comparisonGate}。窗口内点估计最小者置顶；单行窗口不构成直接比较。`,
    conditional_pace:
      "条件配速使用同一套公共燃油敏感性场景，并控制可观测的配方、胎龄与比赛阶段。它不是实测剩余油量净化后的圈速。",
    curve_projection:
      "v17 只读取后端发布的逐圈点、冻结主拟合、删连续圈块稳定性范围与条件平衡门，浏览器不重新拟合。v16 只能由代表配速与稳健胎龄斜率重建汇总线性投影，界面会明确标成 summary-only。",
    curve_scatter:
      "散点纵轴是条件调整后的圈时，不是平均速度。实心点进入冻结加权主拟合，空心点只作样本审计；点的大小固定，只有透明度反映分析权重。实线是后端发布的加权 Theil–Sen 主拟合，两条细点线是删除连续圈块后的稳定性敏感边界，不是置信区间或新圈预测区间。聚焦视窗只改变纵轴显示，超界圈仍以边缘三角保留；确认后曲线仅是已通过模型家族的描述性全段重拟合。",
    curve_ranges:
      "共同胎龄副图只展示当前 comparable Stint 对，或完整 comparable clique。观测中间 80% 来自发布的经验残差范围；删块稳定性 80% 衡量局部连续圈段对拟合的影响；low/base/high 以三个离散情景点显示，不连接为连续区间。三者都不是统计置信区间或新圈预测区间。",
    curve_pairwise:
      "直接秒差和快慢排序只允许使用 sidecar 中 status=comparable 的成对结果。样本圈数、Kish 有效样本量与条件画像都按该对 Stint 的共同胎龄支持重新计算；warning、NOT_TESTED、not_comparable 或缺失配对只能显示审计信息，不参与全序排名。条件平衡仍是公开可观测上下文代理，不识别真实天气、赛道温度或车辆状态。",
    curve_extremes:
      "最快、最慢与衰减极值都是当前同配方、同窗口、共同胎龄下的条件点估计。它们不等同赛车绝对排序、轮胎物理磨损或车手长期保胎能力。",
    fuel_scenario:
      "低、中、高燃油输入是共同敏感性情景，不是各车真实油量，也不是统计置信区间。情景排序重叠时，衰减快慢不能确认。",
    effective_weight:
      "有效权重是样本外残差与先验质量形成的可靠性质量总和，不是实际圈数、事件概率或真实车辆状态。",
    data_funnel:
      "全体参赛车手先进入同一比赛的控制样本，主报告车队只定义发布对象。漏斗依次报告物理可读候选、可靠性权重、严格复核与 Stint 门；阶段分母随上一层合同变化，有效权重不是整数圈数。",
    strict_confirmation:
      "严格复核圈与全量训练层隔离，只用于确认结论与曲线准入；未通过不代表数据文件缺失。",
    classification_proxy:
      "classification_proxy 是本地同场结果相容性代理，不是 FIA 最终分类。没有接入 FIA 分类与相邻车辆官方计时差时，不声称最终名次因果变化。",
    seconds_decomposition:
      "各项以秒数闭合到观测累计差。负值表示相对基线节省时间；不输出伪精确贡献百分比，也不把当场兑现偏差当作长期纯车手能力。",
    fuel_state:
      "有公开来源或冻结代理时才显示数值；燃油、SOC、胎温、胎压、真实磨损、动力模式和具体损伤等未观测状态保持不可识别。",
    control_sample:
      "全体参赛车手先进入同一比赛的条件模型与事件账本；主报告车队只定义发布对象，不用于缩小控制样本。",
    pit_cycle_loss:
      "进站周期代理混合限速区、进站路径、停车和出站暖胎，不等同静止换胎时间，也不是完整策略推荐。",
    legacy_curve_unavailable:
      "v15 及更早报告没有共同参考胎龄的代表配速与支持区间，因此不能生成曲线。v16 有汇总代表配速、斜率与支持区间，只能显示明确标注的 summary-only 线性投影；没有逐圈散点、稳定性带或成对条件平衡门。v17 sidecar 缺失时禁止退回浏览器拟合。",
  });
}

function escapeAttribute(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "'": "&#39;",
    '"': "&quot;",
  })[character]);
}

export function helpButton(topic, label = "查看含义") {
  return `<button type="button" class="helpButton" data-help-topic="${escapeAttribute(topic)}" aria-label="${escapeAttribute(label)}">?</button>`;
}

function closestHelpTrigger(target) {
  return typeof target?.closest === "function"
    ? target.closest("[data-help-topic]")
    : null;
}

function tooltipFor(root) {
  let tooltip = root.getElementById?.(TOOLTIP_ID);
  if (!tooltip && root.createElement && root.body) {
    tooltip = root.createElement("div");
    tooltip.id = TOOLTIP_ID;
    tooltip.className = "helpTooltip";
    tooltip.setAttribute("role", "tooltip");
    tooltip.hidden = true;
    root.body.append(tooltip);
  }
  return tooltip;
}

function clamp(value, minimum, maximum) {
  return Math.min(Math.max(value, minimum), maximum);
}

export function bindHelpTooltips(
  topics,
  root = globalThis.document,
  view = root?.defaultView || globalThis.window,
) {
  if (!root?.addEventListener) return () => {};
  const previous = boundRoots.get(root);
  if (previous) {
    previous.topics = topics;
    return previous.destroy;
  }
  const tooltip = tooltipFor(root);
  if (!tooltip) return () => {};
  const state = {
    active: null,
    pinned: false,
    topics,
    hideTimer: null,
    hide: null,
    destroy: null,
  };

  const position = () => {
    if (!state.active || tooltip.hidden) return;
    const rect = state.active.getBoundingClientRect();
    const tooltipRect = tooltip.getBoundingClientRect();
    const viewportWidth = finiteOr(view?.innerWidth, root.documentElement?.clientWidth || 0);
    const viewportHeight = finiteOr(view?.innerHeight, root.documentElement?.clientHeight || 0);
    const margin = 12;
    const gap = 9;
    const left = clamp(
      rect.left + rect.width / 2 - tooltipRect.width / 2,
      margin,
      Math.max(margin, viewportWidth - tooltipRect.width - margin),
    );
    const below = rect.bottom + gap;
    const above = rect.top - tooltipRect.height - gap;
    const top = below + tooltipRect.height <= viewportHeight - margin
      ? below
      : Math.max(margin, above);
    tooltip.style.left = `${Math.round(left)}px`;
    tooltip.style.top = `${Math.round(top)}px`;
  };

  const hide = () => {
    if (state.hideTimer !== null) {
      view?.clearTimeout?.(state.hideTimer);
      state.hideTimer = null;
    }
    if (state.active) state.active.removeAttribute("aria-describedby");
    state.active = null;
    state.pinned = false;
    tooltip.hidden = true;
    tooltip.textContent = "";
  };
  state.hide = hide;

  const show = (trigger, pinned = false) => {
    const topic = String(trigger?.dataset?.helpTopic || "");
    const text = state.topics?.[topic];
    if (!text) return;
    if (state.hideTimer !== null) {
      view?.clearTimeout?.(state.hideTimer);
      state.hideTimer = null;
    }
    if (state.active && state.active !== trigger) {
      state.active.removeAttribute("aria-describedby");
    }
    state.active = trigger;
    state.pinned = pinned;
    tooltip.textContent = text;
    tooltip.hidden = false;
    trigger.setAttribute("aria-describedby", TOOLTIP_ID);
    view?.requestAnimationFrame?.(position) || position();
  };

  const scheduleHide = () => {
    if (state.pinned) return;
    if (state.hideTimer !== null) view?.clearTimeout?.(state.hideTimer);
    state.hideTimer = view?.setTimeout?.(hide, 80) ?? null;
  };

  const onPointerOver = (event) => {
    if (event.pointerType === "touch") return;
    const trigger = closestHelpTrigger(event.target);
    if (trigger) show(trigger, false);
  };
  const onPointerOut = (event) => {
    const trigger = closestHelpTrigger(event.target);
    if (!trigger || trigger.contains(event.relatedTarget)) return;
    scheduleHide();
  };
  const onFocusIn = (event) => {
    const trigger = closestHelpTrigger(event.target);
    if (trigger) show(trigger, false);
  };
  const onFocusOut = (event) => {
    const trigger = closestHelpTrigger(event.target);
    if (trigger && !state.pinned) scheduleHide();
  };
  const onClick = (event) => {
    const trigger = closestHelpTrigger(event.target);
    if (!trigger) {
      if (state.pinned) hide();
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    if (state.active === trigger && state.pinned) hide();
    else show(trigger, true);
  };
  const onKeyDown = (event) => {
    if (event.key === "Escape" && state.active) {
      hide();
      event.stopPropagation();
    }
  };

  root.addEventListener("pointerover", onPointerOver);
  root.addEventListener("pointerout", onPointerOut);
  root.addEventListener("focusin", onFocusIn);
  root.addEventListener("focusout", onFocusOut);
  root.addEventListener("click", onClick);
  root.addEventListener("keydown", onKeyDown);
  view?.addEventListener?.("resize", position);
  view?.addEventListener?.("scroll", position, true);

  state.destroy = () => {
    hide();
    root.removeEventListener("pointerover", onPointerOver);
    root.removeEventListener("pointerout", onPointerOut);
    root.removeEventListener("focusin", onFocusIn);
    root.removeEventListener("focusout", onFocusOut);
    root.removeEventListener("click", onClick);
    root.removeEventListener("keydown", onKeyDown);
    view?.removeEventListener?.("resize", position);
    view?.removeEventListener?.("scroll", position, true);
    boundRoots.delete(root);
  };
  boundRoots.set(root, state);
  return state.destroy;
}

export function dismissHelpTooltip(root = globalThis.document) {
  const state = boundRoots.get(root);
  state?.hide?.();
}
