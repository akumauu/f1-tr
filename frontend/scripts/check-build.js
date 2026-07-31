import fs from "node:fs";
import path from "node:path";
import { createHash } from "node:crypto";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const dist = path.join(root, "dist");

function isInside(parent, candidate) {
  const relative = path.relative(parent, candidate);
  return relative !== "" && !relative.startsWith("..") && !path.isAbsolute(relative);
}

function sha256(file) {
  return createHash("sha256").update(fs.readFileSync(file)).digest("hex");
}

function listJsonFiles(directory, prefix) {
  if (!fs.existsSync(directory)) return [];
  const paths = [];
  const visit = (current) => {
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const candidate = path.join(current, entry.name);
      if (entry.isDirectory()) {
        visit(candidate);
      } else if (entry.isFile() && entry.name.endsWith(".json")) {
        paths.push(
          path.posix.join(
            prefix,
            path.relative(directory, candidate).split(path.sep).join("/"),
          ),
        );
      }
    }
  };
  visit(directory);
  return paths.sort();
}

const required = [
  "index.html",
  "styles.css",
  "app.js",
  "data-page-entry.js",
  "official-team-colours.js",
  "live-review.html",
  "live-review.css",
  "live-review.js",
  "telemetry-workbench.html",
  "telemetry-workbench.css",
  "telemetry-workbench.js",
  "telemetry-help.js",
  "reference-analysis-lab.html",
  "reference-analysis-lab.css",
  "reference-analysis-lab.js",
  "delta-data-pilot.html",
  "delta-data-pilot.css",
  "delta-data-pilot.js",
  "fdataanalysis-pilot.html",
  "fdataanalysis-pilot.css",
  "fdataanalysis-pilot.js",
  "f1pace-reverse-engineered-v2.html",
  "f1pace-reverse-engineered-v2.css",
  "f1pace-reverse-engineered-v2.js",
  "deltadata-reverse-engineered-v2.html",
  "deltadata-reverse-engineered-v2.css",
  "deltadata-reverse-engineered-v2.js",
  "fdataanalysis-reverse-engineered-v2.html",
  "fdataanalysis-reverse-engineered-v2.css",
  "fdataanalysis-reverse-engineered-v2.js",
  "f1telemetrydata-reverse-engineered-v1.html",
  "f1telemetrydata-reverse-engineered-v1.css",
  "f1telemetrydata-reverse-engineered-v1.js",
  "gptempo-reverse-engineered-v1.html",
  "gptempo-reverse-engineered-v1.css",
  "gptempo-reverse-engineered-v1.js",
  "track-validation-v3.html",
  "track-validation-v3.css",
  "track-validation-v3.js",
  "data/manifest.json",
  "data/live-review/timeline.json",
  "data/telemetry-workbench/manifest.json",
  "data/reference-analysis-lab/v1/manifest.json",
  "data/reference-analysis-lab/v2/manifest.json",
  "data/reference-analysis-lab/v3/manifest.json",
];

for (const rel of required) {
  const file = path.join(dist, rel);
  if (!fs.existsSync(file)) {
    console.error(`Missing build artifact: ${rel}`);
    process.exit(1);
  }
}

const officialColours = await import(
  `${pathToFileURL(path.join(dist, "official-team-colours.js")).href}?catalog=${Date.now()}`
);
const expectedOfficialColours = {
  2023: [
    ["Alfa Romeo", "#C92D4B"],
    ["AlphaTauri", "#5E8FAA"],
    ["Alpine", "#2293D1"],
    ["Aston Martin", "#358C75"],
    ["Ferrari", "#F91536"],
    ["Haas F1 Team", "#B6BABD"],
    ["McLaren", "#F58020"],
    ["Mercedes", "#6CD3BF"],
    ["Red Bull Racing", "#3671C6"],
    ["Williams", "#37BEDD"],
  ],
  2024: [
    ["Alpine", "#0093CC"],
    ["Aston Martin", "#229971"],
    ["Ferrari", "#E80020"],
    ["Haas F1 Team", "#B6BABD"],
    ["Kick Sauber", "#52E252"],
    ["McLaren", "#FF8000"],
    ["Mercedes", "#27F4D2"],
    ["RB", "#6692FF"],
    ["Red Bull Racing", "#3671C6"],
    ["Williams", "#64C4FF"],
  ],
  2025: [
    ["Alpine", "#00A1E8"],
    ["Aston Martin", "#229971"],
    ["Ferrari", "#ED1131"],
    ["Haas F1 Team", "#9C9FA2"],
    ["Kick Sauber", "#01C00E"],
    ["McLaren", "#F47600"],
    ["Mercedes", "#00D7B6"],
    ["Racing Bulls", "#6C98FF"],
    ["Red Bull Racing", "#4781D7"],
    ["Williams", "#1868DB"],
  ],
  2026: [
    ["Alpine", "#00A1E8"],
    ["Aston Martin", "#229971"],
    ["Audi", "#FF2D00"],
    ["Cadillac", "#AAAAAD"],
    ["Ferrari", "#E8002D"],
    ["Haas F1 Team", "#DEE1E2"],
    ["McLaren", "#FF8000"],
    ["Mercedes", "#27F4D2"],
    ["Racing Bulls", "#6692FF"],
    ["Red Bull Racing", "#3671C6"],
    ["Williams", "#1868DB"],
  ],
};
for (const [yearText, rows] of Object.entries(expectedOfficialColours)) {
  const year = Number(yearText);
  const catalog = officialColours.officialTeamColoursForSeason(year);
  if (catalog.length !== rows.length) {
    console.error(`Official team colour catalog size mismatch: ${year}`);
    process.exit(1);
  }
  for (const [teamName, expectedColour] of rows) {
    const identity = officialColours.resolveOfficialTeamIdentity(year, teamName);
    if (
      identity.status !== "official"
      || identity.colour !== expectedColour
      || identity.source?.provider !== "Formula 1"
      || identity.source?.field !== "teamColourCode"
      || identity.source?.url !== `https://www.formula1.com/en/results/${year}/team`
    ) {
      console.error(`Official team colour mismatch: ${year} · ${teamName}`);
      process.exit(1);
    }
  }
}
for (const [year, teamName] of [
  [2025, ""],
  [2025, "Fake Ferrari"],
  [2025, "Test Team"],
  [2027, "Ferrari"],
]) {
  const identity = officialColours.resolveOfficialTeamIdentity(year, teamName);
  if (identity.status !== "unmapped" || identity.colour !== null) {
    console.error(`Unknown team colour must fail closed: ${year} · ${teamName}`);
    process.exit(1);
  }
}

const identitySourceChecks = [
  ["app.js", ["const fallback = [\"#e10600\"", "fallback[Math.abs(Number(driver"]],
  ["telemetry-workbench.js", ["TEAM_CURVE_COLORS", "return `hsl(${hash"]],
  ["live-review.js", ["driver.color", "?? \"#7f8aa0\""]],
];
for (const [relativePath, forbiddenTokens] of identitySourceChecks) {
  const source = fs.readFileSync(path.join(dist, relativePath), "utf8");
  for (const token of forbiddenTokens) {
    if (source.includes(token)) {
      console.error(`Invented identity colour fallback remains in ${relativePath}: ${token}`);
      process.exit(1);
    }
  }
}
const liveReviewBuilderSource = fs.readFileSync(
  path.resolve(root, "..", "tools", "build_live_review_data.mjs"),
  "utf8",
);
if (
  liveReviewBuilderSource.includes("const TEAM_COLORS =")
  || liveReviewBuilderSource.includes("?? \"#7f8aa0\"")
) {
  console.error("Live Review builder still contains driver-picked identity colours");
  process.exit(1);
}
for (const cssFile of [
  "styles.css",
  "live-review.css",
  "telemetry-workbench.css",
]) {
  const source = fs.readFileSync(path.join(dist, cssFile), "utf8");
  if (/var\(--(?:team|driver|series-color)\s*,/i.test(source)) {
    console.error(
      `Identity CSS variables must not contain invented fallback colours: ${cssFile}`,
    );
    process.exit(1);
  }
}

const workbenchHtml = fs.readFileSync(path.join(dist, "telemetry-workbench.html"), "utf8");
const dataPageEntrySource = fs.readFileSync(
  path.join(dist, "data-page-entry.js"),
  "utf8",
);
for (const token of [
  'window.location.protocol !== "file:"',
  'moduleScript.type = "module"',
  "const probe = new Image()",
  "window.location.replace(targetUrl)",
  "需要通过本地服务打开",
  "document.body.replaceChildren(panel)",
]) {
  if (!dataPageEntrySource.includes(token)) {
    console.error(`Data page protocol guard is incomplete: ${token}`);
    process.exit(1);
  }
}
for (const [page, moduleName] of [
  ["index.html", "app.js"],
  ["telemetry-workbench.html", "telemetry-workbench.js"],
  ["live-review.html", "live-review.js"],
]) {
  const html = fs.readFileSync(path.join(dist, page), "utf8");
  if (
    !html.includes('src="data-page-entry.js?v=20260726-runtime-v1"')
    || !html.includes(`data-page="${page}"`)
    || !html.includes(`data-module="${moduleName}?`)
    || html.includes(`<script type="module" src="${moduleName}`)
  ) {
    console.error(`Data page must load through the protocol guard: ${page}`);
    process.exit(1);
  }
}
for (const id of [
  "productSelect",
  "yearSelect",
  "meetingSelect",
  "reportSelect",
  "sectionNav",
  "raceConclusionPanel",
  "qualifyingTable",
  "raceOutcomeTable",
  "eventLedgerPanel",
  "eventTeamSelect",
  "eventCategorySelect",
  "fuelStatePanel",
  "stintPanel",
  "stintTeamSelect",
  "stintDriverSelect",
  "stintOrderSelect",
  "stintCurveExplorer",
  "stintCurveCompoundSelect",
  "stintCurveWindowSelect",
  "stintCurveFocusSelect",
  "stintCurveScaleSelect",
  "stintCurveModeLabel",
  "stintCurveSummary",
  "stintCurveExtremes",
  "stintPrimaryCurveEyebrow",
  "stintPrimaryCurveTitle",
  "stintPrimaryCurveMeta",
  "stintDegradationChart",
  "stintDegradationEmpty",
  "stintSecondaryCurveEyebrow",
  "stintSecondaryCurveTitle",
  "stintSecondaryCurveMeta",
  "stintPaceChart",
  "stintPaceEmpty",
  "stintCurveHoverTooltip",
  "stintCurveLegend",
  "stintCurveEvidenceAudit",
  "stintCurvePairwiseAudit",
  "pacePanel",
  "decompositionPanel",
  "tyreStrategyPanel",
  "auditSection",
  "workbenchHelpTooltip",
]) {
  if (!workbenchHtml.includes(`id="${id}"`)) {
    console.error(`Missing Race Dossier visible section: ${id}`);
    process.exit(1);
  }
}
if (
  !workbenchHtml.includes('data-help-topic="report_navigation"')
  || !workbenchHtml.includes('data-help-topic="curve_projection"')
  || !workbenchHtml.includes('data-help-topic="conditional_pace"')
) {
  console.error("Workbench help triggers are missing from the static UI");
  process.exit(1);
}
const helpSource = fs.readFileSync(path.join(dist, "telemetry-help.js"), "utf8");
const workbenchCSS = fs.readFileSync(
  path.join(dist, "telemetry-workbench.css"),
  "utf8",
);
for (const topic of [
  "comparable_window",
  "curve_projection",
  "fuel_scenario",
  "classification_proxy",
  "legacy_curve_unavailable",
]) {
  if (!helpSource.includes(`${topic}:`)) {
    console.error(`Central workbench help dictionary is missing: ${topic}`);
    process.exit(1);
  }
}
if (
  !workbenchCSS.includes(".stintCurveCanvas[hidden]")
  || !workbenchCSS.includes(
    '.stintCurveExplorer[data-status="unavailable"] .stintCurveGrid',
  )
) {
  console.error("Unavailable Stint curves must hide stale canvases and controls");
  process.exit(1);
}
for (const [pattern, label] of [
  [/--status-ok\s*:/, "status variable --status-ok"],
  [/--status-warn\s*:/, "status variable --status-warn"],
  [/--status-fail\s*:/, "status variable --status-fail"],
  [/\.stintCurveEncodingLegend\b/, "Stint curve encoding legend"],
  [/\.encodingMark\b/, "Stint curve encoding mark"],
  [/\.encodingScatter\b/, "scatter encoding"],
  [/\.encodingFit\b/, "fit encoding"],
  [/\.encodingBand\b/, "band encoding"],
  [/\.encodingConfirmed\b/, "confirmed-shape encoding"],
  [/\.encodingObserved\b/, "observed-range encoding"],
  [/\.encodingStability\b/, "stability encoding"],
  [/\.encodingFuel\b/, "fuel-scenario encoding"],
  [/\.encodingEstimate\b/, "estimate encoding"],
  [/\.stintCurveHoverTooltip\b/, "Stint curve hover tooltip"],
  [/\.stintCurveHoverTooltip\[hidden\]/, "hidden Stint curve hover tooltip"],
]) {
  if (!pattern.test(workbenchCSS)) {
    console.error(`Missing Race Dossier CSS contract: ${label}`);
    process.exit(1);
  }
}
if (
  !/@media\s*\(max-width:\s*720px\)[\s\S]*?\.stintCurvePairwiseAudit\s+td::before[\s\S]*?content\s*:\s*attr\(data-label\)/.test(
    workbenchCSS,
  )
) {
  console.error("Mobile Stint pair audit must render data-label cards");
  process.exit(1);
}
const orderedWorkbenchIds = [
  "raceConclusionPanel",
  "eventLedgerPanel",
  "stintPanel",
  "pacePanel",
  "decompositionPanel",
  "tyreStrategyPanel",
  "auditSection",
];
const orderedWorkbenchOffsets = orderedWorkbenchIds.map(
  (id) => workbenchHtml.indexOf(`id="${id}"`),
);
if (
  orderedWorkbenchOffsets.some((offset) => offset < 0)
  || orderedWorkbenchOffsets.some(
    (offset, index) => index > 0 && offset <= orderedWorkbenchOffsets[index - 1],
  )
) {
  console.error(`Race Dossier reading order is invalid: ${orderedWorkbenchIds.join(" -> ")}`);
  process.exit(1);
}
for (const target of [
  "raceConclusionPanel",
  "eventLedgerPanel",
  "stintPanel",
  "pacePanel",
  "decompositionPanel",
  "auditSection",
]) {
  if (!workbenchHtml.includes(`href="#${target}"`)) {
    console.error(`Race Dossier reading navigation is missing target: ${target}`);
    process.exit(1);
  }
}

const manifest = JSON.parse(fs.readFileSync(path.join(dist, "data", "manifest.json"), "utf8"));
if (!Array.isArray(manifest.sessions) || manifest.sessions.length === 0) {
  console.error("manifest.sessions must contain at least one session");
  process.exit(1);
}

const firstSession = manifest.sessions[0];
for (const key of ["summary", "drivers", "laps", "radio"]) {
  const ref = firstSession.files?.[key];
  if (!ref?.path || !fs.existsSync(path.join(dist, "data", ref.path))) {
    console.error(`Missing data file for ${key}`);
    process.exit(1);
  }
}

const officialCoveragePairs = new Set();
const meetingByKey = new Map(
  (manifest.meetings || []).map((meeting) => [Number(meeting.meeting_key), meeting]),
);
for (const session of manifest.sessions || []) {
  const meeting = meetingByKey.get(Number(session.meeting_key));
  const year = Number(meeting?.year);
  const driverPath = session.files?.drivers?.path;
  if (!driverPath) {
    console.error(`Session lacks driver identities: ${session.session_key}`);
    process.exit(1);
  }
  const drivers = JSON.parse(
    fs.readFileSync(path.join(dist, "data", driverPath), "utf8"),
  );
  const coloursByTeam = new Map();
  for (const driver of drivers) {
    const identity = officialColours.resolveOfficialTeamIdentity(year, driver.team_name);
    if (identity.status !== "official") {
      console.error(
        `Main review driver lacks official team colour: ${year} · ${driver.team_name}`,
      );
      process.exit(1);
    }
    officialCoveragePairs.add(`${year}|${identity.key}`);
    const existing = coloursByTeam.get(identity.key);
    if (existing && existing !== identity.colour) {
      console.error(`Same-team drivers have different colours: ${year} · ${identity.key}`);
      process.exit(1);
    }
    coloursByTeam.set(identity.key, identity.colour);
  }
}

const liveReview = JSON.parse(
  fs.readFileSync(path.join(dist, "data", "live-review", "timeline.json"), "utf8"),
);
if (!Number.isInteger(Number(liveReview.year)) || !(liveReview.drivers || []).length) {
  console.error("Live Review must publish a season and driver team identities");
  process.exit(1);
}
const liveReviewColoursByTeam = new Map();
for (const driver of liveReview.drivers || []) {
  const identity = officialColours.resolveOfficialTeamIdentity(
    liveReview.year,
    driver.team,
  );
  if (
    identity.status !== "official"
    || driver.team_key !== identity.key
    || Object.hasOwn(driver, "color")
  ) {
    console.error(`Live Review driver identity is not registry-backed: ${driver.tla}`);
    process.exit(1);
  }
  const existing = liveReviewColoursByTeam.get(identity.key);
  if (existing && existing !== identity.colour) {
    console.error(`Live Review same-team colour mismatch: ${identity.key}`);
    process.exit(1);
  }
  liveReviewColoursByTeam.set(identity.key, identity.colour);
  officialCoveragePairs.add(`${Number(liveReview.year)}|${identity.key}`);
}

const workbenchManifest = JSON.parse(
  fs.readFileSync(path.join(dist, "data", "telemetry-workbench", "manifest.json"), "utf8"),
);
if (!Array.isArray(workbenchManifest.reports)) {
  console.error("Telemetry workbench manifest must contain reports");
  process.exit(1);
}
const workbenchRows = workbenchManifest.reports;
const distWorkbenchRoot = path.join(dist, "data", "telemetry-workbench");
const expectedWorkbenchReportPaths = new Set(
  workbenchRows.map((row) => String(row.path || "")),
);
const expectedCurveEvidencePaths = new Set(
  workbenchRows
    .filter((row) => row.stint_curve_evidence)
    .map((row) => String(row.stint_curve_evidence.path || "")),
);
const actualWorkbenchReportPaths = listJsonFiles(
  path.join(distWorkbenchRoot, "reports"),
  "reports",
);
const actualCurveEvidencePaths = listJsonFiles(
  path.join(distWorkbenchRoot, "curve-evidence"),
  "curve-evidence",
);
if (
  actualWorkbenchReportPaths.length !== expectedWorkbenchReportPaths.size
  || actualWorkbenchReportPaths.some(
    (reportPath) => !expectedWorkbenchReportPaths.has(reportPath),
  )
) {
  console.error("Built workbench report files must exactly match manifest references");
  process.exit(1);
}
if (
  actualCurveEvidencePaths.length !== expectedCurveEvidencePaths.size
  || actualCurveEvidencePaths.some(
    (curvePath) => !expectedCurveEvidencePaths.has(curvePath),
  )
) {
  console.error("Built curve evidence files must exactly match manifest references");
  process.exit(1);
}
const dossierRows = workbenchRows.filter(
  (row) => row.product === "race_dossier",
);
const v15DossierRows = dossierRows.filter((row) => String(row.id).endsWith("-v15"));
const v16PilotRows = dossierRows.filter((row) => String(row.id).endsWith("-v16"));
if (
  !v15DossierRows.length
  || !v16PilotRows.some(
    (row) => row.id === "race-dossier-2025-abu-dhabi-grand-prix-v16",
  )
) {
  console.error("Telemetry workbench must preserve the published v15 batch and v16 pilot");
  process.exit(1);
}
const reportById = new Map();
const curveEvidenceByReportId = new Map();
let visibleDossierChecked = false;
for (const row of workbenchRows) {
  const reportPath = path.resolve(
    distWorkbenchRoot,
    String(row.path || ""),
  );
  if (
    !isInside(distWorkbenchRoot, reportPath)
    || !fs.existsSync(reportPath)
    || !fs.statSync(reportPath).isFile()
  ) {
    console.error(`Invalid telemetry workbench report reference: ${row.path}`);
    process.exit(1);
  }
  if (
    !/^[0-9a-f]{64}$/.test(String(row.export_sha256 || ""))
    || sha256(reportPath) !== row.export_sha256
  ) {
    console.error(`Telemetry workbench report SHA-256 mismatch: ${row.path}`);
    process.exit(1);
  }
  const report = JSON.parse(fs.readFileSync(reportPath, "utf8"));
  reportById.set(row.id, report);
  if (row.stint_curve_evidence) {
    const curveReference = row.stint_curve_evidence;
    const curvePath = path.resolve(
      distWorkbenchRoot,
      String(curveReference.path || ""),
    );
    if (
      !isInside(distWorkbenchRoot, curvePath)
      || !fs.existsSync(curvePath)
      || !fs.statSync(curvePath).isFile()
    ) {
      console.error(`Invalid curve evidence reference: ${curveReference.path}`);
      process.exit(1);
    }
    const curveHash = sha256(curvePath);
    if (
      !/^[0-9a-f]{64}$/.test(String(curveReference.source_sha256 || ""))
      || !/^[0-9a-f]{64}$/.test(String(curveReference.export_sha256 || ""))
      || curveReference.source_sha256 !== curveReference.export_sha256
      || curveHash !== curveReference.export_sha256
      || Number(curveReference.bytes) !== fs.statSync(curvePath).size
    ) {
      console.error(`Curve evidence identity chain mismatch: ${curveReference.path}`);
      process.exit(1);
    }
    const curveEvidence = JSON.parse(fs.readFileSync(curvePath, "utf8"));
    const curveStints = Array.isArray(curveEvidence.stints)
      ? curveEvidence.stints
      : [];
    const curvePairs = Array.isArray(curveEvidence.pairwise_comparisons)
      ? curveEvidence.pairwise_comparisons
      : [];
    const curvePointCount = curveStints.reduce(
      (count, stint) => count + (Array.isArray(stint.points) ? stint.points.length : 0),
      0,
    );
    const directlyComparablePairs = curvePairs.filter(
      (pair) => pair.status === "comparable",
    ).length;
    if (
      curveReference.schema_version
        !== "race-dossier-stint-curve-evidence-v17"
      || curveEvidence.schema_version !== curveReference.schema_version
      || curveEvidence.status !== "available"
      || curveEvidence.contract?.browser_refits_models !== false
      || curveEvidence.contract?.support_extrapolation_allowed !== false
      || curveEvidence.report_id !== report.report_id
      || curveStints.length !== Number(curveReference.stints)
      || curvePointCount !== Number(curveReference.points)
      || directlyComparablePairs
        !== Number(curveReference.directly_comparable_pairs)
      || report.stint_curve_evidence?.frontend_path !== curveReference.path
      || report.stint_curve_evidence?.frontend_sha256
        !== curveReference.export_sha256
    ) {
      console.error(`Curve evidence schema or coverage mismatch: ${curveReference.path}`);
      process.exit(1);
    }
    curveEvidenceByReportId.set(row.id, curveEvidence);
  } else if (String(row.id).endsWith("-v17")) {
    console.error(`Race Dossier v17 must publish curve evidence: ${row.id}`);
    process.exit(1);
  }
  const reportYear = Number(report.scope?.year ?? row.year);
  const reportTeams = new Set([
    ...(report.coverage?.teams || []).map((teamRow) => teamRow.team),
    ...(report.stint_dossiers || []).map((stint) => stint.team),
  ].filter(Boolean));
  const scopedTeam = String(report.scope?.team || "").trim();
  if (!reportTeams.size && scopedTeam && !scopedTeam.includes("/")) {
    reportTeams.add(scopedTeam);
  }
  for (const teamName of reportTeams) {
    const identity = officialColours.resolveOfficialTeamIdentity(reportYear, teamName);
    if (identity.status !== "official") {
      console.error(
        `Workbench report lacks official team colour: ${reportYear} · ${teamName}`,
      );
      process.exit(1);
    }
    officialCoveragePairs.add(`${reportYear}|${identity.key}`);
  }
  if (row.product === "race_dossier") {
    if (
      !report.inclusive_robust_model_audit
      || Number(report.inclusive_robust_model_audit.candidate_laps) <= 0
      || report.ratings?.PAC !== null
      || report.ratings?.OVR !== null
    ) {
      console.error(`Invalid inclusive Race Dossier contract: ${row.path}`);
      process.exit(1);
    }
    if (
      row.id === "race-dossier-2025-abu-dhabi-grand-prix-v16"
      || (
        String(row.id).endsWith("-v17")
        && Number(row.year) === 2025
        && String(row.meeting) === "Abu Dhabi Grand Prix"
      )
    ) {
      const borStints = (report.stint_dossiers || []).filter((stint) => stint.driver === "BOR");
      if (
        !report.event_conclusion
        || !(report.race_analysis?.descriptive_team_order || []).length
        || !report.simulation_proxy_audit?.fuel?.enabled
        || !(report.tyre_strategy_envelope?.compounds || []).length
        || borStints.length !== 2
        || !borStints.every((stint) => Number.isFinite(Number(stint.representative_tyre_age_pace_s)))
        || !borStints.every((stint) => Number.isFinite(Number(stint.representative_tyre_age_laps)))
        || !borStints.every((stint) => Array.isArray(stint.representative_tyre_age_pace_fuel_sensitivity_interval_s))
        || !borStints.every((stint) => Number.isFinite(Number(stint.cumulative_delta_to_reasonable_baseline_s)))
        || !(report.vehicle_driver_decomposition || []).length
        || !(report.result_impact_audit?.classification_proxy_comparison || []).length
      ) {
        console.error("Abu Dhabi visible Race Dossier evidence contract is incomplete");
        process.exit(1);
      }
      visibleDossierChecked = true;
    }
  }
}
if (!visibleDossierChecked) {
  console.error("Visible Race Dossier fixture was not checked");
  process.exit(1);
}
if (officialCoveragePairs.size !== 41) {
  console.error(
    `Official colour coverage must include all 2023-2026 published team identities: `
      + `${officialCoveragePairs.size}/41`,
  );
  process.exit(1);
}

const elementStore = new Map();
function stubElement(id) {
  if (!elementStore.has(id)) {
    elementStore.set(id, {
      id,
      hidden: false,
      textContent: "",
      innerHTML: "",
      value: "",
      dataset: {},
      attributes: {},
      clientWidth: 1200,
      canvasOps: {
        beginPath: 0,
        moveTo: 0,
        lineTo: 0,
        stroke: 0,
        fillRect: 0,
        fillText: 0,
        strokes: [],
        fills: [],
      },
      classList: { add() {} },
      addEventListener() {},
      setAttribute(name, value) { this.attributes[name] = String(value); },
      getContext() {
        const ops = this.canvasOps;
        return {
          strokeStyle: null,
          fillStyle: null,
          lineWidth: 1,
          globalAlpha: 1,
          currentDash: [],
          scale() {}, clearRect() {},
          beginPath() { ops.beginPath += 1; },
          moveTo() { ops.moveTo += 1; },
          lineTo() { ops.lineTo += 1; },
          stroke() {
            ops.stroke += 1;
            ops.strokes.push({
              color: this.strokeStyle,
              dash: this.currentDash.slice(),
              lineWidth: this.lineWidth,
              alpha: this.globalAlpha,
            });
          },
          fillRect() {
            ops.fillRect += 1;
            ops.fills.push({
              color: this.fillStyle,
              alpha: this.globalAlpha,
            });
          },
          fillText() { ops.fillText += 1; },
          setLineDash(value) { this.currentDash = [...value]; },
          save() {}, translate() {},
          rotate() {}, restore() {},
        };
      },
    });
  }
  return elementStore.get(id);
}

function resetCanvasOps(id) {
  const ops = stubElement(id).canvasOps;
  for (const key of [
    "beginPath",
    "moveTo",
    "lineTo",
    "stroke",
    "fillRect",
    "fillText",
  ]) {
    ops[key] = 0;
  }
  ops.strokes = [];
  ops.fills = [];
}

globalThis.document = {
  addEventListener() {},
  getElementById(id) { return stubElement(id); },
};
globalThis.window = { devicePixelRatio: 1 };

const builtWorkbench = await import(
  `${pathToFileURL(path.join(dist, "telemetry-workbench.js")).href}?smoke=${Date.now()}`
);

let renderedWorkbenchReports = 0;
let visibleLegacyCurveFallbacks = 0;
let renderedTelemetryArchives = 0;
let renderedV17EvidenceReports = 0;
for (const ref of workbenchRows) {
  const reportPath = path.resolve(
    distWorkbenchRoot,
    String(ref.path || ""),
  );
  try {
    const report = reportById.get(ref.id);
    const curveEvidence = curveEvidenceByReportId.get(ref.id);
    builtWorkbench.render(
      ref,
      report,
      curveEvidence
        ? { status: "ready", data: curveEvidence, reason: null }
        : undefined,
    );
    renderedWorkbenchReports += 1;
    if (ref.product === "race_dossier" && String(ref.id).endsWith("-v15")) {
      if (
        stubElement("stintCurveExplorer").hidden
        || stubElement("stintCurveExplorer").dataset.status !== "unavailable"
        || !stubElement("stintCurveSummary").innerHTML.includes("曲线暂不可用")
        || !stubElement("stintCurveSummary").innerHTML.includes(
          'data-help-topic="legacy_curve_unavailable"',
        )
        || !stubElement("stintTable").innerHTML.includes("<table>")
      ) {
        throw new Error("v15 必须保留 Stint 表并显示曲线合同不可用原因");
      }
      visibleLegacyCurveFallbacks += 1;
    }
    if (ref.product === "telemetry_explanation") {
      if (!stubElement("stintPanel").hidden) {
        throw new Error("旧遥测档案不得误显示 Race Dossier Stint 面板");
      }
      renderedTelemetryArchives += 1;
    }
    if (String(ref.id).endsWith("-v17")) {
      if (
        stubElement("stintCurveExplorer").dataset.mode !== "v17_evidence"
        || !stubElement("stintCurveModeLabel").textContent.includes("v17")
      ) {
        throw new Error("v17 必须进入逐圈 sidecar 只读渲染模式");
      }
      renderedV17EvidenceReports += 1;
    }
  } catch (error) {
    console.error(`Workbench report render failed: ${ref.id}: ${error.message}`);
    process.exit(1);
  }
}
const expectedLegacyCurveFallbacks = v15DossierRows.length;
const expectedTelemetryArchives = workbenchRows.filter(
  (row) => row.product === "telemetry_explanation",
).length;
const expectedV17EvidenceReports = dossierRows.filter(
  (row) => String(row.id).endsWith("-v17"),
).length;
if (
  renderedWorkbenchReports !== workbenchRows.length
  || visibleLegacyCurveFallbacks !== expectedLegacyCurveFallbacks
  || renderedTelemetryArchives !== expectedTelemetryArchives
  || renderedV17EvidenceReports !== expectedV17EvidenceReports
) {
  console.error(
    `All-report render coverage is incomplete: ${renderedWorkbenchReports}/`
      + `${workbenchRows.length}, legacy=${visibleLegacyCurveFallbacks}, `
      + `telemetry=${renderedTelemetryArchives}, v17=${renderedV17EvidenceReports}`,
  );
  process.exit(1);
}

const chronologicalFixture = [
  {
    id: "race-dossier-2025-australian-grand-prix-v15",
    year: 2025,
    meeting: "Australian Grand Prix",
    product: "race_dossier",
    source_path: "research/records/race_dossier_v15/year=2025/round=1/meeting=australian-grand-prix/race_dossier.json",
  },
  {
    id: "race-dossier-2025-abu-dhabi-grand-prix-v15",
    year: 2025,
    meeting: "Abu Dhabi Grand Prix",
    product: "race_dossier",
    source_path: "research/records/race_dossier_v15/year=2025/round=24/meeting=abu-dhabi-grand-prix/race_dossier.json",
  },
  {
    id: "race-dossier-2025-abu-dhabi-grand-prix-v16",
    year: 2025,
    meeting: "Abu Dhabi Grand Prix",
    product: "race_dossier",
    source_path: "research/records/race_dossier_v16/race_dossier.json",
  },
  {
    id: "telemetry-explanation-2026-australia-ferrari-v7",
    year: 2026,
    meeting: "Australian Grand Prix",
    product: "telemetry_explanation",
  },
  {
    id: "telemetry-explanation-2026-miami-ferrari-v7",
    year: 2026,
    meeting: "Miami Grand Prix",
    product: "telemetry_explanation",
  },
];
const chronologicalIds = builtWorkbench
  .sortReportsByTime(chronologicalFixture)
  .map((row) => row.id);
const expectedChronologicalIds = [
  "telemetry-explanation-2026-miami-ferrari-v7",
  "telemetry-explanation-2026-australia-ferrari-v7",
  "race-dossier-2025-abu-dhabi-grand-prix-v16",
  "race-dossier-2025-abu-dhabi-grand-prix-v15",
  "race-dossier-2025-australian-grand-prix-v15",
];
if (JSON.stringify(chronologicalIds) !== JSON.stringify(expectedChronologicalIds)) {
  console.error(`Report chronology sort failed: ${chronologicalIds.join(", ")}`);
  process.exit(1);
}
const dossierFilterModel = builtWorkbench.buildReportFilterModel(
  chronologicalFixture,
  {
    product: "race_dossier",
    year: 2025,
    meetingKey: "2025|abu dhabi grand prix",
  },
);
if (
  dossierFilterModel.products[0]?.value !== "race_dossier"
  || dossierFilterModel.year !== 2025
  || dossierFilterModel.meetings.length !== 2
  || dossierFilterModel.meetings[0]?.ref.id
    !== "race-dossier-2025-abu-dhabi-grand-prix-v16"
  || !dossierFilterModel.meetings[0]?.label.startsWith("R24 · ")
  || dossierFilterModel.reports.map((row) => row.id).join(",")
    !== "race-dossier-2025-abu-dhabi-grand-prix-v16,race-dossier-2025-abu-dhabi-grand-prix-v15"
) {
  console.error("Cascading report filter failed to isolate product, season, event, or version");
  process.exit(1);
}
builtWorkbench.renderSelect(
  dossierFilterModel.reports,
  "race-dossier-2025-abu-dhabi-grand-prix-v16",
);
const reportSelectHtml = stubElement("reportSelect").innerHTML;
if (
  !reportSelectHtml.includes("v16 · 单场试点，非全量发布")
  || !reportSelectHtml.includes("v15 · 70 场批次（逐场发布门）")
  || reportSelectHtml.includes("Australian Grand Prix")
  || reportSelectHtml.includes("telemetry")
) {
  console.error("Version selector must only show the selected event and explicit release scope");
  process.exit(1);
}
const telemetryFilterModel = builtWorkbench.buildReportFilterModel(
  chronologicalFixture,
  { product: "telemetry_explanation" },
);
if (
  telemetryFilterModel.year !== 2026
  || telemetryFilterModel.meetings.map((entry) => entry.ref.id).join(",")
    !== "telemetry-explanation-2026-miami-ferrari-v7,telemetry-explanation-2026-australia-ferrari-v7"
) {
  console.error("Telemetry archive filter must keep its own season and frozen event order");
  process.exit(1);
}
const fullChronologicalIds = builtWorkbench
  .sortReportsByTime(workbenchRows)
  .slice(0, 6)
  .map((row) => row.id);
const reportVersion = (row) => Number(
  String(row.id || "").match(/-v(\d+)$/)?.[1] || -1,
);
const abuDhabiDossierRows = dossierRows
  .filter((row) => (
    Number(row.year) === 2025
    && String(row.meeting) === "Abu Dhabi Grand Prix"
  ))
  .slice()
  .sort((left, right) => reportVersion(right) - reportVersion(left));
const expectedFullChronologicalIds = [
  "telemetry-explanation-2026-miami-ferrari-v7",
  "telemetry-explanation-2026-japan-ferrari-v7",
  "telemetry-explanation-2026-china-ferrari-v7",
  "telemetry-explanation-2026-australia-ferrari-v7",
  ...abuDhabiDossierRows.slice(0, 2).map((row) => row.id),
];
if (JSON.stringify(fullChronologicalIds) !== JSON.stringify(expectedFullChronologicalIds)) {
  console.error(`Full workbench chronology sort failed: ${fullChronologicalIds.join(", ")}`);
  process.exit(1);
}
const fullDossierFilterModel = builtWorkbench.buildReportFilterModel(
  workbenchRows,
  { product: "race_dossier" },
);
if (
  fullDossierFilterModel.year !== 2025
  || fullDossierFilterModel.meetings.length !== 24
  || fullDossierFilterModel.meetings[0]?.label !== "R24 · Abu Dhabi Grand Prix"
  || fullDossierFilterModel.reports.map((row) => row.id).join(",")
    !== abuDhabiDossierRows.map((row) => row.id).join(",")
) {
  console.error("Full manifest must default to the latest Race Dossier season and event");
  process.exit(1);
}

const groupedStints = builtWorkbench.groupStintsForDisplay([
  {
    driver: "HARD_A",
    compound: "HARD",
    representative_tyre_age_pace_s: 90.1,
    representative_tyre_age_laps: 10,
  },
  {
    driver: "SOFT_SLOW",
    compound: "SOFT",
    representative_tyre_age_pace_s: 88.2,
    representative_tyre_age_laps: 5,
  },
  {
    driver: "SOFT_FAST",
    compound: "SOFT",
    representative_tyre_age_pace_s: 87.8,
    representative_tyre_age_laps: 5,
  },
  {
    driver: "SOFT_UNKNOWN",
    compound: "SOFT",
  },
  {
    driver: "MEDIUM_LEGACY",
    compound: "MEDIUM",
    stable_pace_s: 89.4,
  },
]);
if (
  groupedStints.map((group) => group.compound).join(",") !== "SOFT,MEDIUM,HARD"
  || groupedStints[0].rows.map((row) => row.driver).join(",")
    !== "SOFT_FAST,SOFT_SLOW,SOFT_UNKNOWN"
) {
  console.error("Stint compound grouping or within-compound pace sort failed");
  process.exit(1);
}

const comparableStint = (
  driver,
  {
    compound = "SOFT",
    pace = 90,
    age = 5,
    lapStart = 1,
    lapEnd = 9,
    status = "valid",
    paceStatus = "identified_conditional_proxy",
  } = {},
) => ({
  driver,
  team: "Test Team",
  stint_number: 1,
  compound,
  status,
  representative_tyre_age_pace_s: pace,
  representative_tyre_age_laps: age,
  representative_tyre_age_pace_status: paceStatus,
  lap_start: lapStart,
  lap_end: lapEnd,
});
const exactBoundaryWindows = builtWorkbench.buildComparableStintWindows(
  { coverage: { race_lap_span: 50 } },
  [
    comparableStint("BOUNDARY_A"),
    comparableStint("BOUNDARY_B", {
      pace: 90.2,
      age: 7,
      lapStart: 11,
      lapEnd: 19,
    }),
  ],
);
if (
  exactBoundaryWindows[0]?.windows.length !== 1
  || exactBoundaryWindows[0]?.windows[0]?.rows.length !== 2
) {
  console.error("Exact Stint comparison age/phase boundaries must remain inclusive");
  process.exit(1);
}
const overBoundaryWindows = builtWorkbench.buildComparableStintWindows(
  { coverage: { race_lap_span: 50 } },
  [
    comparableStint("OVER_A"),
    comparableStint("OVER_AGE", {
      pace: 90.1,
      age: 7.01,
      lapStart: 11,
      lapEnd: 19,
    }),
    comparableStint("OVER_PHASE", {
      pace: 90.2,
      age: 5,
      lapStart: 11.05,
      lapEnd: 19.05,
    }),
    comparableStint("LEGACY_FALLBACK", {
      pace: null,
      paceStatus: null,
    }),
    comparableStint("INVALID_STATUS", { status: "audit_only" }),
    comparableStint("OTHER_COMPOUND", { compound: "HARD" }),
  ],
);
const overSoft = overBoundaryWindows.find((group) => group.compound === "SOFT");
if (
  overSoft?.windows.length !== 3
  || overSoft?.windows.some((window) => window.rows.length !== 1)
  || overSoft?.auditRows.length !== 2
  || overBoundaryWindows.find((group) => group.compound === "HARD")?.windows.length !== 1
) {
  console.error("Stint comparison must fail closed across threshold, status, and compound");
  process.exit(1);
}

const curveReportFixture = {
  coverage: { race_lap_span: 50 },
  publication_gate: { passed: true },
  inclusive_robust_model_audit: { status: "accepted_crossfit_proxy" },
};
const curveStint = (
  driver,
  {
    team = "Ferrari",
    compound = "SOFT",
    pace = 90,
    age = 5,
    slope = 0.05,
    support = [1, 12],
    lapStart = 1,
    lapEnd = 9,
  } = {},
) => ({
  ...comparableStint(driver, {
    compound,
    pace,
    age,
    lapStart,
    lapEnd,
  }),
  team,
  degradation_s_per_tyre_lap: slope,
  representative_tyre_age_support_laps: support,
  fuel_sensitivity_degradation_interval_s_per_tyre_lap: [
    Number(slope) - 0.01,
    Number(slope) + 0.01,
  ],
  representative_tyre_age_pace_fuel_sensitivity_interval_s: [
    Number(pace) - 0.2,
    Number(pace) + 0.2,
  ],
  strict_confirmation: { status: "confirmed" },
});
const curveFixtureRows = [
  curveStint("AAA"),
  curveStint("BBB", {
    team: "Mercedes",
    pace: 89.8,
    age: 7,
    slope: 0.1,
    support: [3, 14],
    lapStart: 11,
    lapEnd: 19,
  }),
];
const curveFixtureModel = builtWorkbench.buildStintCurveExplorerModel(
  curveReportFixture,
  curveFixtureRows,
);
if (
  curveFixtureModel.status !== "ready"
  || curveFixtureModel.selectedCompound !== "SOFT"
  || curveFixtureModel.selectedWindow.distinctTeams !== 2
  || curveFixtureModel.commonAgeMinimum !== 3
  || curveFixtureModel.commonAgeMaximum !== 12
  || curveFixtureModel.anchorAge !== 6
  || curveFixtureModel.extremes.degradationSlowest?.row.driver !== "AAA"
  || curveFixtureModel.extremes.degradationFastest?.row.driver !== "BBB"
  || curveFixtureModel.extremes.paceFastest?.row.driver !== "BBB"
  || curveFixtureModel.extremes.paceSlowest?.row.driver !== "AAA"
) {
  console.error("Stint curve fixture did not preserve common support or extrema");
  process.exit(1);
}
for (const series of curveFixtureModel.series) {
  const allPoints = [...series.degradationPoints, ...series.pacePoints];
  if (
    !series.degradationPoints.length
    || !series.pacePoints.length
    || allPoints.some((point) => (
      !Number.isFinite(Number(point.x))
      || !Number.isFinite(Number(point.y))
    ))
    || allPoints.some((point) => (
      point.x < curveFixtureModel.commonAgeMinimum
      || point.x > curveFixtureModel.commonAgeMaximum
      || point.x < series.support[0]
      || point.x > series.support[1]
    ))
  ) {
    console.error("Stint curve evidence escaped common support or contains invalid points");
    process.exit(1);
  }
}
const v17CurveReportFixture = {
  ...curveReportFixture,
  report_id: "race-dossier-v17-curve-fixture",
  schema_version: "race-dossier-v17",
  scope: { year: 2025, meeting: "Test GP", session: "Race" },
  stint_dossiers: curveFixtureRows,
  stint_curve_evidence: {
    status: "available",
    schema_version: "race-dossier-stint-curve-evidence-v17",
  },
};
const v17EvidenceStints = curveFixtureRows.map((row, rowIndex) => {
  const support = rowIndex === 0 ? [1, 12] : [3, 14];
  const referenceAge = Number(row.representative_tyre_age_laps);
  const referencePace = (
    Number(row.representative_tyre_age_pace_s) + 1.25 + rowIndex * 0.2
  );
  const slope = Number(row.degradation_s_per_tyre_lap) + 0.03;
  const ages = rowIndex === 0 ? [3, 6, 9] : [4, 7, 10];
  const residuals = rowIndex === 0 ? [0.18, -0.11, 0.09] : [-0.14, 0.12, 0.03];
  const paceAt = (age) => referencePace + slope * (age - referenceAge);
  return {
    stint_key: `${row.team}|${row.driver}|${row.stint_number}|${row.compound}`,
    team: row.team,
    driver: row.driver,
    stint_number: row.stint_number,
    compound: row.compound,
    qualification: { status: "valid", gate_failures: [] },
    sample_audit: {
      fit_laps: ages.length,
      kish_effective_laps: ages.length,
      primary_fit_residual_central_range_s: [-0.15, 0.19],
    },
    condition_profile: {},
    primary_fit: {
      status: "IDENTIFIED",
      reference_tyre_age_laps: referenceAge,
      reference_pace_s: referencePace,
      slope_s_per_tyre_lap: slope,
      support_tyre_age_laps: support,
      equation: {
        reference_tyre_age_laps: referenceAge,
        reference_pace_s: referencePace,
        slope_s_per_tyre_lap: slope,
      },
      prediction_grid: support.map((age) => ({
        tyre_age_laps: age,
        pace_s: paceAt(age),
      })),
      stability_interval: {
        status: "ESTIMATED",
        prediction_band: support.map((age) => ({
          tyre_age_laps: age,
          lower_s: paceAt(age) - 0.08,
          upper_s: paceAt(age) + 0.08,
        })),
      },
      fuel_scenarios: Object.fromEntries(
        ["low", "base", "high"].map((name, index) => [
          name,
          {
            status: "identified_conditional_proxy",
            reference_pace_s: referencePace + (index - 1) * 0.1,
            slope_s_per_tyre_lap: slope,
          },
        ]),
      ),
    },
    confirmed_shape: { status: "NOT_AVAILABLE" },
    points: ages.map((age, index) => ({
      lap_number: index + 1,
      tyre_age_laps: age,
      adjusted_pace_s: paceAt(age) + residuals[index],
      fitted_primary_pace_s: paceAt(age),
      analysis_weight: 0.7 + index * 0.1,
      used_for_primary_fit: true,
    })),
  };
});
const v17EvidencePairKey = v17EvidenceStints
  .map((row) => row.stint_key)
  .sort()
  .join("~");
const v17CurveSidecarFixture = {
  schema_version: "race-dossier-stint-curve-evidence-v17",
  report_id: v17CurveReportFixture.report_id,
  status: "available",
  contract: {
    browser_refits_models: false,
    support_extrapolation_allowed: false,
  },
  stints: v17EvidenceStints,
  pairwise_comparisons: [
    {
      pair_key: v17EvidencePairKey,
      left_stint_key: v17EvidenceStints[1].stint_key,
      right_stint_key: v17EvidenceStints[0].stint_key,
      status: "comparable",
      left_minus_right_pace_s: 0.321,
    },
  ],
};
const v17CurveFixtureModel = builtWorkbench.buildStintCurveExplorerModel(
  v17CurveReportFixture,
  curveFixtureRows,
  null,
  null,
  { status: "ready", data: v17CurveSidecarFixture, reason: null },
);
const v17FirstSeries = v17CurveFixtureModel.series.find(
  (series) => series.row.driver === curveFixtureRows[0].driver,
);
const v17FirstEvidence = v17EvidenceStints[0];
const expectedPublishedAnchorPace = (
  Number(v17FirstEvidence.primary_fit.reference_pace_s)
  + Number(v17FirstEvidence.primary_fit.slope_s_per_tyre_lap)
    * (
      Number(v17CurveFixtureModel.anchorAge)
      - Number(v17FirstEvidence.primary_fit.reference_tyre_age_laps)
    )
);
const forbiddenSummaryAnchorPace = (
  Number(curveFixtureRows[0].representative_tyre_age_pace_s)
  + Number(curveFixtureRows[0].degradation_s_per_tyre_lap)
    * (
      Number(v17CurveFixtureModel.anchorAge)
      - Number(curveFixtureRows[0].representative_tyre_age_laps)
    )
);
if (
  v17CurveFixtureModel.mode !== "v17_evidence"
  || v17CurveFixtureModel.status !== "ready"
  || v17CurveFixtureModel.series.length !== 2
  || !v17CurveFixtureModel.allPairsComparable
  || !v17CurveFixtureModel.directRankingAllowed
  || v17CurveFixtureModel.pairwiseAudit[0]?.status !== "comparable"
  || v17CurveFixtureModel.pairwiseAudit[0]?.left?.evidence?.stint_key
    !== v17EvidenceStints[1].stint_key
  || v17CurveFixtureModel.pairwiseAudit[0]?.right?.evidence?.stint_key
    !== v17EvidenceStints[0].stint_key
  || v17CurveFixtureModel.series.some((series) => series.points.length !== 3)
  || !v17CurveFixtureModel.series.every((series) => series.points.some(
    (point) => Math.abs(
      Number(point.adjusted_pace_s) - Number(point.fitted_primary_pace_s),
    ) > 0.02,
  ))
  || Math.abs(
    Number(v17FirstSeries?.paceAtAnchor) - expectedPublishedAnchorPace,
  ) > 1e-12
  || Math.abs(
    Number(v17FirstSeries?.paceAtAnchor) - forbiddenSummaryAnchorPace,
  ) < 0.5
) {
  console.error(
    "v17 curve model must consume published scatter, fit, and pairwise gates without refitting",
  );
  process.exit(1);
}
const previousCurveTeamScope = stubElement("stintTeamSelect").value;
const scatterFillCountBefore = stubElement(
  "stintDegradationChart",
).canvasOps.fillRect;
stubElement("stintTeamSelect").value = "ALL";
const renderedV17CurveFixture = builtWorkbench.renderStintCurveExplorer(
  v17CurveReportFixture,
  null,
  null,
  { status: "ready", data: v17CurveSidecarFixture, reason: null },
);
stubElement("stintTeamSelect").value = previousCurveTeamScope;
if (
  renderedV17CurveFixture.mode !== "v17_evidence"
  || stubElement("stintCurveExplorer").dataset.mode !== "v17_evidence"
  || !stubElement("stintCurveModeLabel").textContent.includes("浏览器零拟合")
  || !stubElement("stintDegradationChart").attributes["aria-label"]?.includes(
    "逐圈条件配速散点",
  )
  || (
    stubElement("stintDegradationChart").canvasOps.fillRect
    - scatterFillCountBefore
  ) < v17EvidenceStints.reduce(
    (count, stint) => count + stint.points.length,
    0,
  )
  || !stubElement("stintCurvePairwiseAudit").innerHTML.includes(
    `${v17EvidenceStints[1].driver} ↔ ${v17EvidenceStints[0].driver}`,
  )
  || !stubElement("stintCurvePairwiseAudit").innerHTML.includes("+0.321s")
) {
  console.error("v17 sidecar scatter and zero-refit presentation were not rendered");
  process.exit(1);
}
const reversedCurveFixtureModel = builtWorkbench.buildStintCurveExplorerModel(
  curveReportFixture,
  curveFixtureRows.slice().reverse(),
);
if (
  reversedCurveFixtureModel.selectedWindowKey
    !== curveFixtureModel.selectedWindowKey
) {
  console.error("Stint curve window key must remain stable across input order");
  process.exit(1);
}
const nullSlopeCurveModel = builtWorkbench.buildStintCurveExplorerModel(
  curveReportFixture,
  [
    curveStint("NULL_SLOPE", { slope: null }),
    curveFixtureRows[1],
  ],
);
const nullSupportCurveModel = builtWorkbench.buildStintCurveExplorerModel(
  curveReportFixture,
  [
    curveStint("NULL_SUPPORT", { support: [null, 12] }),
    curveFixtureRows[1],
  ],
);
const singleTeamCurveModel = builtWorkbench.buildStintCurveExplorerModel(
  curveReportFixture,
  [
    curveStint("SAME_A"),
    curveStint("SAME_B", {
      team: "Ferrari",
      pace: 89.8,
      age: 7,
      slope: 0.1,
      support: [3, 14],
      lapStart: 11,
      lapEnd: 19,
    }),
  ],
);
const noCommonSupportCurveModel = builtWorkbench.buildStintCurveExplorerModel(
  curveReportFixture,
  [
    curveStint("NO_OVERLAP_A", { support: [1, 5], age: 5 }),
    curveStint("NO_OVERLAP_B", {
      team: "Team B",
      pace: 89.8,
      age: 7,
      slope: 0.1,
      support: [7, 12],
      lapStart: 11,
      lapEnd: 19,
    }),
  ],
);
if (
  nullSlopeCurveModel.status !== "unavailable"
  || nullSupportCurveModel.status !== "unavailable"
  || singleTeamCurveModel.status !== "unavailable"
  || noCommonSupportCurveModel.status !== "no_common_support"
) {
  console.error("Stint curves must fail closed for null, single-team, or no-overlap inputs");
  process.exit(1);
}

const visibleRow = dossierRows.find(
  (row) => row.id === "race-dossier-2025-abu-dhabi-grand-prix-v16",
);
const visibleReport = JSON.parse(
  fs.readFileSync(
    path.join(dist, "data", "telemetry-workbench", visibleRow.path),
    "utf8",
  ),
);

const primaryStints = builtWorkbench.filterStintsForDisplay(
  visibleReport,
  "PRIMARY",
  "ALL",
);
const allStints = builtWorkbench.filterStintsForDisplay(visibleReport, "ALL", "ALL");
const ferrariStints = builtWorkbench.filterStintsForDisplay(
  visibleReport,
  "Ferrari",
  "ALL",
);
const leclercStints = builtWorkbench.filterStintsForDisplay(
  visibleReport,
  "Ferrari",
  "LEC",
);
if (
  primaryStints.length !== 19
  || allStints.length !== 47
  || ferrariStints.length !== 6
  || leclercStints.length !== 3
) {
  console.error("Stint team and driver cascading scopes are incorrect");
  process.exit(1);
}
const primaryComparisonGroups = builtWorkbench.buildComparableStintWindows(
  visibleReport,
  primaryStints,
);
const expectedComparisonWindows = new Map([
  ["SOFT", { count: 1, anchors: "HAM#1" }],
  ["MEDIUM", { count: 4, anchors: "LEC#1,LEC#3,PIA#2,VER#1" }],
  ["HARD", { count: 6, anchors: "NOR#3,NOR#2,VER#2,PIA#1,TSU#1,RUS#2" }],
]);
const flattenedComparisonKeys = [];
for (const group of primaryComparisonGroups) {
  const expected = expectedComparisonWindows.get(group.compound);
  const anchors = group.windows
    .map((window) => `${window.baseline.driver}#${window.baseline.stint_number}`)
    .join(",");
  if (
    !expected
    || group.windows.length !== expected.count
    || anchors !== expected.anchors
    || group.auditRows.length !== 0
  ) {
    console.error(`Unexpected Abu Dhabi comparison windows for ${group.compound}`);
    process.exit(1);
  }
  for (const window of group.windows) {
    const minimumPace = Math.min(
      ...window.rows.map((row) => row.representative_tyre_age_pace_s),
    );
    if (
      window.ageSpan > 2
      || window.phaseSpan > 0.2
      || window.baselinePace !== minimumPace
    ) {
      console.error("Stint window violates frozen max-min or baseline invariant");
      process.exit(1);
    }
    for (const row of window.rows) {
      flattenedComparisonKeys.push(
        `${row.team}|${row.driver}|${row.stint_number}|${row.compound}`,
      );
    }
  }
}
if (
  flattenedComparisonKeys.length !== primaryStints.length
  || new Set(flattenedComparisonKeys).size !== primaryStints.length
) {
  console.error("Comparable Stint windows must be exhaustive and non-overlapping");
  process.exit(1);
}
const primaryCurveModel = builtWorkbench.buildStintCurveExplorerModel(
  visibleReport,
  primaryStints,
);
if (
  primaryCurveModel.status !== "ready"
  || primaryCurveModel.groups.map((group) => (
    `${group.compound}:${group.windows.length}`
  )).join(",") !== "MEDIUM:2,HARD:2"
  || primaryCurveModel.selectedCompound !== "MEDIUM"
  || primaryCurveModel.selectedWindow.distinctTeams !== 3
  || primaryCurveModel.series.map((item) => item.row.driver).join(",")
    !== "LEC,HAM,ANT,TSU"
  || primaryCurveModel.commonAgeMinimum !== 4
  || primaryCurveModel.commonAgeMaximum !== 22
  || primaryCurveModel.anchorAge !== 13.5
  || primaryCurveModel.extremes.degradationSlowest?.row.driver !== "HAM"
  || primaryCurveModel.extremes.degradationFastest?.row.driver !== "LEC"
  || primaryCurveModel.extremes.paceFastest?.row.driver !== "LEC"
  || primaryCurveModel.extremes.paceSlowest?.row.driver !== "TSU"
  || primaryCurveModel.degradationOrdering.slowest
    !== "燃油情景区间重叠，排序未确认"
  || primaryCurveModel.degradationOrdering.fastest
    !== "燃油情景区间重叠，排序未确认"
) {
  console.error("Abu Dhabi cross-team curve windows or extrema changed unexpectedly");
  process.exit(1);
}
const primaryStyles = new Map(primaryCurveModel.series.map((item) => [
  item.row.driver,
  builtWorkbench.curveSeriesLineStyle(2025, item, primaryCurveModel.series),
]));
if (
  primaryStyles.get("LEC")?.color !== "#ED1131"
  || primaryStyles.get("HAM")?.color !== "#ED1131"
  || primaryStyles.get("ANT")?.color !== "#00D7B6"
  || primaryStyles.get("TSU")?.color !== "#4781D7"
  || JSON.stringify(primaryStyles.get("LEC")?.dash)
    === JSON.stringify(primaryStyles.get("HAM")?.dash)
) {
  console.error("Same-team drivers must share the official colour and use line style only");
  process.exit(1);
}
const reversedPrimarySeries = primaryCurveModel.series.slice().reverse();
for (const item of primaryCurveModel.series) {
  const original = builtWorkbench.curveSeriesLineStyle(
    2025,
    item,
    primaryCurveModel.series,
  );
  const reversed = builtWorkbench.curveSeriesLineStyle(
    2025,
    item,
    reversedPrimarySeries,
  );
  if (
    original.color !== reversed.color
    || JSON.stringify(original.dash) !== JSON.stringify(reversed.dash)
  ) {
    console.error("Official driver line style changed after input reordering");
    process.exit(1);
  }
}
const sameDriverSeries = [
  { key: "LEC-1", row: { team: "Ferrari", driver: "LEC", stint_number: 1 } },
  { key: "LEC-2", row: { team: "Ferrari", driver: "LEC", stint_number: 2 } },
  { key: "HAM-1", row: { team: "Ferrari", driver: "HAM", stint_number: 1 } },
];
const sameDriverStyles = sameDriverSeries.map((item) => (
  builtWorkbench.curveSeriesLineStyle(2025, item, sameDriverSeries)
));
if (
  sameDriverStyles.some((style) => style.color !== "#ED1131")
  || JSON.stringify(sameDriverStyles[0].dash) !== JSON.stringify(sameDriverStyles[1].dash)
  || JSON.stringify(sameDriverStyles[0].dash) === JSON.stringify(sameDriverStyles[2].dash)
) {
  console.error("Driver line styles must be stable across multiple stints");
  process.exit(1);
}
let unknownColourFailedClosed = false;
try {
  builtWorkbench.teamCurveColor(2025, "Fake Ferrari");
} catch {
  unknownColourFailedClosed = true;
}
if (!unknownColourFailedClosed) {
  console.error("Workbench unknown teams must not receive a generated colour");
  process.exit(1);
}
const allFieldCurveModel = builtWorkbench.buildStintCurveExplorerModel(
  visibleReport,
  allStints,
);
const ferrariCurveModel = builtWorkbench.buildStintCurveExplorerModel(
  visibleReport,
  ferrariStints,
);
if (
  allFieldCurveModel.status !== "ready"
  || allFieldCurveModel.selectedWindow.distinctTeams < 2
  || allFieldCurveModel.series.length < 2
  || ferrariCurveModel.status !== "unavailable"
) {
  console.error("Cross-team curve scope must expand to all teams and fail closed for one team");
  process.exit(1);
}
const raceSequenceFixture = [
  { team: "Team B", driver: "BBB", stint_number: 2, lap_start: 20 },
  { team: "Team A", driver: "AAA", stint_number: 2, lap_start: 15 },
  { team: "Team A", driver: "AAA", stint_number: 1, lap_start: 1 },
  { team: "Team A", driver: "CCC", stint_number: 1, lap_start: 1 },
];
if (
  builtWorkbench.sortStintsByRaceSequence(raceSequenceFixture)
    .map((row) => `${row.driver}-${row.stint_number}`)
    .join(",") !== "AAA-1,AAA-2,CCC-1,BBB-2"
) {
  console.error("Race-sequence Stint ordering failed");
  process.exit(1);
}

const allEventRows = builtWorkbench.filterEventLedgerRows(
  visibleReport,
  "ALL",
  "ALL",
);
const primaryEventRows = builtWorkbench.filterEventLedgerRows(
  visibleReport,
  "PRIMARY",
  "ALL",
);
const primaryContextRows = builtWorkbench.filterEventLedgerRows(
  visibleReport,
  "PRIMARY",
  "CONTEXT",
);
const primaryQualityRows = builtWorkbench.filterEventLedgerRows(
  visibleReport,
  "PRIMARY",
  "QUALITY",
);
if (
  allEventRows.length !== 84
  || primaryEventRows.length !== 34
  || primaryContextRows.length !== 26
  || primaryQualityRows.length !== 8
  || primaryContextRows.length + primaryQualityRows.length !== primaryEventRows.length
) {
  console.error("Event ledger team or category filters are incorrect");
  process.exit(1);
}
const eventGroups = builtWorkbench.groupEventLedgerRows(primaryContextRows);
if (eventGroups.map((group) => group.presentation.key).join(",") !== "TRAFFIC,PIT,RESIDUAL") {
  console.error("Event ledger category priority is incorrect");
  process.exit(1);
}
const unmappedEventFixture = {
  coverage: { teams: [{ team: "Team A" }] },
  stint_dossiers: [{ driver: "AAA", team: "Team A" }],
  event_ledger: {
    entries: [
      { driver: "AAA", event_type: "traffic_dirty_air", lap_ranges: [[1, 2]] },
      { driver: "UNKNOWN", event_type: "traffic_dirty_air", lap_ranges: [[3, 4]] },
    ],
  },
};
if (
  builtWorkbench.filterEventLedgerRows(unmappedEventFixture, "ALL", "CONTEXT").length !== 2
  || builtWorkbench.filterEventLedgerRows(
    unmappedEventFixture,
    "PRIMARY",
    "CONTEXT",
  ).length !== 1
) {
  console.error("Unmapped event drivers must remain visible in the all-field scope");
  process.exit(1);
}
resetCanvasOps("stintDegradationChart");
resetCanvasOps("stintPaceChart");
builtWorkbench.render(visibleRow, visibleReport);

const visibleAssertions = [
  ["releaseBanner", "单场真实数据 pilot"],
  ["releaseBanner", "不是 2023–2025 共 70 场正式发布"],
  ["raceConclusion", "长距离条件配速最快"],
  ["raceOutcomeTable", "成绩代理比性能代理"],
  ["fuelStateGrid", "90/100/110 kg"],
  ["fuelComparisonNote", "比较口径"],
  ["fuelComparisonNote", 'data-help-topic="conditional_pace"'],
  ["stintSummary", "主报告 4 队"],
  ["stintSummary", "19 个 Stint"],
  ["stintTable", "按车手还原比赛进程"],
  ["stintTable", "LEC"],
  ["stintTable", "条件代理"],
  ["stintTable", "@ 胎龄 8.0 圈"],
  ["stintTable", "86.664s"],
  ["stintTable", "相对合理基线累计"],
  ["stintTable", "软胎 · SOFT"],
  ["stintTable", "中性胎 · MEDIUM"],
  ["stintTable", "硬胎 · HARD"],
  ["stintCurveSummary", "共同胎龄支持 4.0–22.0 圈"],
  ["stintCurveSummary", "共同评价胎龄 13.5 圈"],
  ["stintCurveExtremes", "衰减点估计最慢"],
  ["stintCurveExtremes", "燃油情景区间重叠，排序未确认"],
  ["stintCurveExtremes", "胎龄 13.5 圈配速最快"],
  ["stintCurveLegend", "Ferrari"],
  ["stintCurveLegend", "Mercedes"],
  ["stintCurveLegend", "Red Bull Racing"],
  ["stintCurveLegend", 'data-color-source="official"'],
  ["stintCurveLegend", "--series-color:#ED1131"],
  ["stintCurveLegend", "--series-color:#00D7B6"],
  ["stintCurveLegend", "--series-color:#4781D7"],
  ["decompositionTable", "策略/交通/赛道位置条件代理"],
  ["eventLedgerSummary", "26/84 条"],
  ["anomalyList", "交通与脏空气代理"],
  ["anomalyList", "保留；事件条件入模"],
  ["modeList", "本场进站周期中位数"],
];
for (const [id, expected] of visibleAssertions) {
  const element = stubElement(id);
  const rendered = `${element.innerHTML}\n${element.textContent}`;
  if (!rendered.includes(expected)) {
    console.error(`Rendered Race Dossier section ${id} lacks: ${expected}`);
    process.exit(1);
  }
}
if (
  stubElement("stintCurveExplorer").hidden
  || stubElement("stintDegradationChart").hidden
  || stubElement("stintPaceChart").hidden
  || stubElement("stintDegradationChart").canvasOps.lineTo < 100
  || stubElement("stintPaceChart").canvasOps.lineTo < 100
  || !stubElement("stintDegradationChart").attributes["aria-label"]?.includes(
    "轮胎衰减",
  )
  || !stubElement("stintPaceChart").attributes["aria-label"]?.includes(
    "Stint 条件配速",
  )
) {
  console.error("Stint curve canvases were not visibly rendered with accessible labels");
  process.exit(1);
}
const official2025ColourSet = new Set(
  officialColours.officialTeamColoursForSeason(2025).map((row) => row.colour),
);
for (const canvasId of ["stintDegradationChart", "stintPaceChart"]) {
  const ops = stubElement(canvasId).canvasOps;
  const identityStrokes = ops.strokes.filter(
    (stroke) => /^#[0-9A-F]{6}$/.test(String(stroke.color || "")),
  );
  const identityFills = ops.fills.filter(
    (fill) => /^#[0-9A-F]{6}$/.test(String(fill.color || "")),
  );
  if (
    identityStrokes.length < primaryCurveModel.series.length
    || identityFills.length < primaryCurveModel.series.length
    || identityStrokes.some((stroke) => !official2025ColourSet.has(stroke.color))
    || identityFills.some((fill) => !official2025ColourSet.has(fill.color))
    || !identityStrokes.some((stroke) => stroke.color === "#ED1131")
    || !identityStrokes.some((stroke) => stroke.color === "#00D7B6")
    || !identityStrokes.some((stroke) => stroke.color === "#4781D7")
  ) {
    console.error(
      `Canvas identity series escaped official colours: ${canvasId}; `
      + `strokes=${JSON.stringify([...new Set(identityStrokes.map((row) => row.color))])}; `
      + `fills=${JSON.stringify([...new Set(identityFills.map((row) => row.color))])}`,
    );
    process.exit(1);
  }
}
if (stubElement("stintTable").innerHTML.includes("<strong>BOR</strong>")) {
  console.error("Default Stint view must focus on the four reporting teams");
  process.exit(1);
}
builtWorkbench.renderStintTable(visibleReport, "PRIMARY", "ALL", "COMPARABLE");
const primaryComparableStints = stubElement("stintTable").innerHTML;
if (
  !primaryComparableStints.includes("置顶比较基准")
  || !primaryComparableStints.includes("可比窗口")
  || !primaryComparableStints.includes("s 对窗口基准")
  || !primaryComparableStints.includes(
    '<span class="stintPinnedBaselineIdentity"><strong>LEC</strong>',
  )
) {
  console.error("Primary comparable Stint windows lack pinned baselines or deltas");
  process.exit(1);
}
builtWorkbench.renderStintTable(visibleReport, "ALL", "BOR", "COMPARABLE");
const borComparableStints = stubElement("stintTable").innerHTML;
if (
  !borComparableStints.includes("<strong>BOR</strong>")
  || !borComparableStints.includes("87.056s")
  || !borComparableStints.includes("单行窗口")
  || !borComparableStints.includes("没有与它同时满足胎龄和阶段门的对象")
  || borComparableStints.includes("s 对窗口基准")
  || !borComparableStints.includes('class="stintPinnedBaseline"')
  || !borComparableStints.includes(
    '<span class="stintPinnedBaselineIdentity"><strong>BOR</strong>',
  )
  || !borComparableStints.includes('class="stintBaselineRow"')
) {
  console.error("Singleton Stint windows must stay visible without false deltas");
  process.exit(1);
}
builtWorkbench.renderEventLedgerTable(visibleReport, "PRIMARY", "QUALITY");
if (
  !stubElement("eventLedgerSummary").innerHTML.includes("仅计时与质量")
  || !stubElement("anomalyList").innerHTML.includes("计时与数据质量")
) {
  console.error("Event ledger quality-only view is not reachable");
  process.exit(1);
}

const referenceLabHtml = fs.readFileSync(
  path.join(dist, "reference-analysis-lab.html"),
  "utf8",
);
const referenceLabJs = fs.readFileSync(
  path.join(dist, "reference-analysis-lab.js"),
  "utf8",
);
const referenceLabIndex = JSON.parse(
  fs.readFileSync(
    path.join(dist, "data", "reference-analysis-lab", "v1", "manifest.json"),
    "utf8",
  ),
);
const referenceLabTarget = (referenceLabIndex.targets || []).find(
  (row) => row.target_id === "f1pace",
);
const referenceLabTargetManifestPath = String(referenceLabTarget?.manifest || "")
  .replaceAll("\\", "/");
const referenceLabManifestPath = path.join(
  dist,
  ...referenceLabTargetManifestPath.split("/"),
);
const referenceLabManifest = fs.existsSync(referenceLabManifestPath)
  ? JSON.parse(fs.readFileSync(referenceLabManifestPath, "utf8"))
  : {};
const referenceLabReportPath = String(referenceLabManifest.report?.path || "")
  .replaceAll("\\", "/");
const referenceLabReport = path.join(dist, ...referenceLabReportPath.split("/"));
if (
  referenceLabIndex.schema_version !== "reference-analysis-lab-index-v1"
  || referenceLabTarget?.target_id !== "f1pace"
  || referenceLabManifest.target_id !== "f1pace"
  || referenceLabManifest.status !== "PASS"
  || !isInside(dist, referenceLabReport)
  || !fs.existsSync(referenceLabReport)
  || sha256(referenceLabReport) !== referenceLabManifest.report.sha256
  || !referenceLabHtml.includes('data-page="reference-analysis-lab.html"')
  || !referenceLabHtml.includes('data-module="reference-analysis-lab.js?')
  || !referenceLabHtml.includes('id="trafficHeatmap"')
  || !referenceLabHtml.includes('id="pairMatrix"')
  || !referenceLabJs.includes("audit_only")
) {
  console.error("Reference analysis lab build contract failed");
  process.exit(1);
}

const deltaDataHtml = fs.readFileSync(
  path.join(dist, "delta-data-pilot.html"),
  "utf8",
);
const deltaDataJs = fs.readFileSync(
  path.join(dist, "delta-data-pilot.js"),
  "utf8",
);
const deltaDataTarget = (referenceLabIndex.targets || []).find(
  (row) => row.target_id === "deltadata",
);
const deltaDataTargetManifestPath = String(deltaDataTarget?.manifest || "")
  .replaceAll("\\", "/");
const deltaDataManifestPath = path.join(
  dist,
  ...deltaDataTargetManifestPath.split("/"),
);
const deltaDataManifest = fs.existsSync(deltaDataManifestPath)
  ? JSON.parse(fs.readFileSync(deltaDataManifestPath, "utf8"))
  : {};
const deltaDataReportPath = path.join(
  dist,
  ...String(deltaDataManifest.report?.path || "")
    .replaceAll("\\", "/")
    .split("/"),
);
const deltaDataReport = fs.existsSync(deltaDataReportPath)
  ? JSON.parse(fs.readFileSync(deltaDataReportPath, "utf8"))
  : {};
if (
  deltaDataTarget?.target_id !== "deltadata"
  || deltaDataTarget.status !== "METHOD_EQUIVALENT_ONLY"
  || deltaDataManifest.target_id !== "deltadata"
  || deltaDataManifest.status !== "METHOD_EQUIVALENT_ONLY"
  || !isInside(dist, deltaDataReportPath)
  || !fs.existsSync(deltaDataReportPath)
  || sha256(deltaDataReportPath) !== deltaDataManifest.report?.sha256
  || deltaDataReport.target_id !== "deltadata"
  || deltaDataReport.status !== "METHOD_EQUIVALENT_ONLY"
  || deltaDataReport.validation?.real_source_only !== true
  || deltaDataReport.validation?.synthetic_points !== 0
  || deltaDataReport.validation?.one_to_one_status !== "SKIPPED_OPAQUE_METHOD"
  || !deltaDataHtml.includes('data-page="delta-data-pilot.html"')
  || !deltaDataHtml.includes('src="data-page-entry.js?v=20260726-runtime-v1"')
  || !deltaDataHtml.includes('data-module="delta-data-pilot.js?')
  || !deltaDataHtml.includes('id="teamTable"')
  || !deltaDataHtml.includes('id="pairTable"')
  || !deltaDataJs.includes("METHOD_EQUIVALENT_ONLY")
  || !deltaDataJs.includes("audit_only")
) {
  console.error("DeltaData reference analysis lab build contract failed");
  process.exit(1);
}

const fdataAnalysisHtml = fs.readFileSync(
  path.join(dist, "fdataanalysis-pilot.html"),
  "utf8",
);
const fdataAnalysisJs = fs.readFileSync(
  path.join(dist, "fdataanalysis-pilot.js"),
  "utf8",
);
const fdataAnalysisTarget = (referenceLabIndex.targets || []).find(
  (row) => row.target_id === "fdataanalysis",
);
const fdataAnalysisTargetManifestPath = String(fdataAnalysisTarget?.manifest || "")
  .replaceAll("\\", "/");
const fdataAnalysisManifestPath = path.join(
  dist,
  ...fdataAnalysisTargetManifestPath.split("/"),
);
const fdataAnalysisManifest = fs.existsSync(fdataAnalysisManifestPath)
  ? JSON.parse(fs.readFileSync(fdataAnalysisManifestPath, "utf8"))
  : {};
const fdataAnalysisReportPath = path.join(
  dist,
  ...String(fdataAnalysisManifest.report?.path || "")
    .replaceAll("\\", "/")
    .split("/"),
);
const fdataAnalysisReport = fs.existsSync(fdataAnalysisReportPath)
  ? JSON.parse(fs.readFileSync(fdataAnalysisReportPath, "utf8"))
  : {};
if (
  fdataAnalysisTarget?.target_id !== "fdataanalysis"
  || fdataAnalysisTarget.status !== "PASS"
  || fdataAnalysisManifest.target_id !== "fdataanalysis"
  || fdataAnalysisManifest.status !== "PASS"
  || !isInside(dist, fdataAnalysisReportPath)
  || !fs.existsSync(fdataAnalysisReportPath)
  || sha256(fdataAnalysisReportPath) !== fdataAnalysisManifest.report?.sha256
  || fdataAnalysisReport.target_id !== "fdataanalysis"
  || fdataAnalysisReport.status !== "PASS"
  || fdataAnalysisReport.validation?.real_source_only !== true
  || fdataAnalysisReport.validation?.synthetic_points !== 0
  || fdataAnalysisReport.validation?.direct_physical_claims_allowed !== false
  || !fdataAnalysisHtml.includes('data-page="fdataanalysis-pilot.html"')
  || !fdataAnalysisHtml.includes('src="data-page-entry.js?v=20260726-runtime-v1"')
  || !fdataAnalysisHtml.includes('data-module="fdataanalysis-pilot.js?')
  || !fdataAnalysisHtml.includes('id="cornerTable"')
  || !fdataAnalysisHtml.includes('id="driverFeatureTable"')
  || !fdataAnalysisJs.includes("audit_only")
  || !fdataAnalysisJs.includes("distance_axis")
) {
  console.error("FDataAnalysis reference analysis lab build contract failed");
  process.exit(1);
}

const f1paceV2Html = fs.readFileSync(
  path.join(dist, "f1pace-reverse-engineered-v2.html"),
  "utf8",
);
const f1paceV2Js = fs.readFileSync(
  path.join(dist, "f1pace-reverse-engineered-v2.js"),
  "utf8",
);
const referenceLabV2Index = JSON.parse(
  fs.readFileSync(
    path.join(dist, "data", "reference-analysis-lab", "v2", "manifest.json"),
    "utf8",
  ),
);
const f1paceV2Target = (referenceLabV2Index.targets || []).find(
  (row) => row.target_id === "f1pace-reverse-engineered-v2",
);
const f1paceV2ManifestPath = path.join(
  dist,
  ...String(f1paceV2Target?.manifest || "")
    .replaceAll("\\", "/")
    .split("/"),
);
const f1paceV2Manifest = fs.existsSync(f1paceV2ManifestPath)
  ? JSON.parse(fs.readFileSync(f1paceV2ManifestPath, "utf8"))
  : {};
const f1paceV2ReportPath = path.join(
  path.dirname(f1paceV2ManifestPath),
  String(f1paceV2Manifest.report || ""),
);
const f1paceV2Report = fs.existsSync(f1paceV2ReportPath)
  ? JSON.parse(fs.readFileSync(f1paceV2ReportPath, "utf8"))
  : {};
if (
  referenceLabV2Index.schema_version !== "reference-analysis-lab-index-v2"
  || f1paceV2Target?.status !== "METHOD_EQUIVALENT_EXTERNALLY_VALIDATED"
  || f1paceV2Manifest.target_id !== "f1pace-reverse-engineered-v2"
  || f1paceV2Manifest.status !== "METHOD_EQUIVALENT_EXTERNALLY_VALIDATED"
  || !isInside(dist, f1paceV2ReportPath)
  || !fs.existsSync(f1paceV2ReportPath)
  || sha256(f1paceV2ReportPath) !== f1paceV2Manifest.report_sha256
  || f1paceV2Report.status !== "METHOD_EQUIVALENT_EXTERNALLY_VALIDATED"
  || f1paceV2Report.one_to_one_status !== "SKIPPED_OPAQUE_METHOD"
  || f1paceV2Report.calibration?.external_zero_refit !== true
  || f1paceV2Report.calibration?.external_validation?.rows !== 1067
  || f1paceV2Report.calibration?.external_validation?.mae_pp >= 5
  || f1paceV2Report.calibration?.external_validation?.traffic_lap_accuracy <= .95
  || f1paceV2Report.audited_analysis?.status !== "audit_only"
  || f1paceV2Report.audited_analysis?.reference_image_used_in_prediction !== false
  || f1paceV2Report.exclusion_ledger?.synthetic_points !== 0
  || !f1paceV2Html.includes('data-page="f1pace-reverse-engineered-v2.html"')
  || !f1paceV2Html.includes('data-module="f1pace-reverse-engineered-v2.js?')
  || !f1paceV2Html.includes('id="trafficHeatmap"')
  || !f1paceV2Html.includes('id="residualHeatmap"')
  || !f1paceV2Js.includes("external_zero_refit")
  || !f1paceV2Js.includes("audited_analysis_ratio")
) {
  console.error("F1pace reverse-engineered v2 build contract failed");
  process.exit(1);
}

const deltaDataV2Html = fs.readFileSync(
  path.join(dist, "deltadata-reverse-engineered-v2.html"),
  "utf8",
);
const deltaDataV2Js = fs.readFileSync(
  path.join(dist, "deltadata-reverse-engineered-v2.js"),
  "utf8",
);
const deltaDataV2Target = (referenceLabV2Index.targets || []).find(
  (row) => row.target_id === "deltadata-reverse-engineered-v2",
);
const deltaDataV2ManifestPath = path.join(
  dist,
  ...String(deltaDataV2Target?.manifest || "")
    .replaceAll("\\", "/")
    .split("/"),
);
const deltaDataV2Manifest = fs.existsSync(deltaDataV2ManifestPath)
  ? JSON.parse(fs.readFileSync(deltaDataV2ManifestPath, "utf8"))
  : {};
const deltaDataV2ReportPath = path.join(
  path.dirname(deltaDataV2ManifestPath),
  String(deltaDataV2Manifest.report || ""),
);
const deltaDataV2Report = fs.existsSync(deltaDataV2ReportPath)
  ? JSON.parse(fs.readFileSync(deltaDataV2ReportPath, "utf8"))
  : {};
if (
  deltaDataV2Target?.status !== "METHOD_EQUIVALENT_REFERENCE_BENCHMARKED"
  || deltaDataV2Manifest.target_id !== "deltadata-reverse-engineered-v2"
  || deltaDataV2Manifest.status !== "METHOD_EQUIVALENT_REFERENCE_BENCHMARKED"
  || !isInside(dist, deltaDataV2ReportPath)
  || !fs.existsSync(deltaDataV2ReportPath)
  || sha256(deltaDataV2ReportPath) !== deltaDataV2Manifest.report_sha256
  || deltaDataV2Report.status !== "METHOD_EQUIVALENT_REFERENCE_BENCHMARKED"
  || deltaDataV2Report.one_to_one_status !== "SKIPPED_OPAQUE_METHOD"
  || deltaDataV2Report.validation?.real_source_only !== true
  || deltaDataV2Report.validation?.synthetic_laps !== 0
  || deltaDataV2Report.validation?.pilot_raw_points !== 784424
  || deltaDataV2Report.validation?.reference_benchmark_events !== 3
  || deltaDataV2Report.validation?.reference_benchmark_mae_pp >= .12
  || deltaDataV2Report.validation?.reference_benchmark_direction_accuracy !== 1
  || deltaDataV2Report.validation?.reference_benchmark_is_blind_holdout !== false
  || deltaDataV2Report.visual_replication?.driver_ranking?.length !== 20
  || deltaDataV2Report.visual_replication?.team_ranking?.length !== 10
  || deltaDataV2Report.visual_replication?.teammate_h2h?.length !== 10
  || deltaDataV2Report.audited_analysis?.direct_total_order_allowed !== false
  || deltaDataV2Report.audited_analysis?.comparable_teammate_pairs !== 4
  || deltaDataV2Report.legacy_comparison?.summary?.driver_order_exact_match !== true
  || !deltaDataV2Html.includes('data-page="deltadata-reverse-engineered-v2.html"')
  || !deltaDataV2Html.includes('data-module="deltadata-reverse-engineered-v2.js?')
  || !deltaDataV2Html.includes('id="benchmarkCards"')
  || !deltaDataV2Html.includes('id="auditPairTable"')
  || !deltaDataV2Js.includes("reference_benchmark_is_blind_holdout")
  || !deltaDataV2Js.includes("audited_left_minus_right_pct")
  || !deltaDataV2Js.includes("__F1TR_DELTADATA_V2_READY__")
) {
  console.error("DeltaData reverse-engineered v2 build contract failed");
  process.exit(1);
}

const fdataV2Html = fs.readFileSync(
  path.join(dist, "fdataanalysis-reverse-engineered-v2.html"),
  "utf8",
);
const fdataV2Js = fs.readFileSync(
  path.join(dist, "fdataanalysis-reverse-engineered-v2.js"),
  "utf8",
);
const fdataV2Target = (referenceLabV2Index.targets || []).find(
  (row) => row.target_id === "fdataanalysis-reverse-engineered-v2",
);
const fdataV2ManifestPath = path.join(
  dist,
  ...String(fdataV2Target?.manifest || "")
    .replaceAll("\\", "/")
    .split("/"),
);
const fdataV2Manifest = fs.existsSync(fdataV2ManifestPath)
  ? JSON.parse(fs.readFileSync(fdataV2ManifestPath, "utf8"))
  : {};
const fdataV2ReportPath = path.join(
  dist,
  ...String(fdataV2Manifest.report?.path || "")
    .replaceAll("\\", "/")
    .split("/"),
);
const fdataV2Report = fs.existsSync(fdataV2ReportPath)
  ? JSON.parse(fs.readFileSync(fdataV2ReportPath, "utf8"))
  : {};
if (
  fdataV2Target?.status !== "METHOD_EQUIVALENT_INTERNALLY_VALIDATED"
  || fdataV2Manifest.target_id !== "fdataanalysis-reverse-engineered-v2"
  || fdataV2Manifest.status !== "METHOD_EQUIVALENT_INTERNALLY_VALIDATED"
  || !isInside(dist, fdataV2ReportPath)
  || !fs.existsSync(fdataV2ReportPath)
  || sha256(fdataV2ReportPath) !== fdataV2Manifest.report?.sha256
  || fdataV2Report.status !== "METHOD_EQUIVALENT_INTERNALLY_VALIDATED"
  || fdataV2Report.method_card?.one_to_one_status !== "SKIPPED_OPAQUE_METHOD"
  || fdataV2Report.validation?.real_source_only !== true
  || fdataV2Report.validation?.synthetic_points !== 0
  || fdataV2Report.validation?.raw_points !== 784424
  || fdataV2Report.validation?.axis_interpolated_laps !== 1082
  || fdataV2Report.validation?.axis_intervals !== 1000
  || fdataV2Report.validation?.corner_proxy_count !== 16
  || fdataV2Report.validation?.segmentation_stability?.status !== "PASS"
  || fdataV2Report.validation?.lap_time_reconstruction?.mae_s >= .25
  || fdataV2Report.validation?.lap_time_reconstruction?.p90_abs_error_s >= .5
  || fdataV2Report.validation?.teammate_matching?.max_antisymmetry_error !== 0
  || fdataV2Report.audited_analysis?.direct_total_order_allowed !== false
  || fdataV2Report.same_event_legacy_comparison?.structural_gap
    ?.v1_exit_below_reported_minimum_rows <= 0
  || fdataV2Report.same_event_legacy_comparison?.structural_gap
    ?.v2_exit_below_reported_minimum_rows !== 0
  || fdataV2Report.reference_identity?.numeric_comparison_status
    !== "NOT_COMPARABLE_DIFFERENT_EVENT_AND_SESSION"
  || fdataV2Report.reference_identity?.invalid_cached_asset?.status
    !== "INVALID_REFERENCE_ASSET_SUBJECT_MISMATCH"
  || !fdataV2Html.includes('data-page="fdataanalysis-reverse-engineered-v2.html"')
  || !fdataV2Html.includes('data-module="fdataanalysis-reverse-engineered-v2.js?')
  || !fdataV2Html.includes('id="trackChart"')
  || !fdataV2Html.includes('id="teammateAuditTable"')
  || !fdataV2Js.includes("faithful_top15_sample_mean_kph")
  || !fdataV2Js.includes("NOT_COMPARABLE_DIFFERENT_EVENT_AND_SESSION")
  || !fdataV2Js.includes("__F1TR_FDATA_V2_READY__")
) {
  console.error("FDataAnalysis reverse-engineered v2 build contract failed");
  process.exit(1);
}

const f1TelemetryDataV1Html = fs.readFileSync(
  path.join(dist, "f1telemetrydata-reverse-engineered-v1.html"),
  "utf8",
);
const f1TelemetryDataV1Js = fs.readFileSync(
  path.join(dist, "f1telemetrydata-reverse-engineered-v1.js"),
  "utf8",
);
const f1TelemetryDataV1Target = (referenceLabV2Index.targets || []).find(
  (row) => row.target_id === "f1telemetrydata-reverse-engineered-v1",
);
const f1TelemetryDataV1ManifestPath = path.join(
  dist,
  ...String(f1TelemetryDataV1Target?.manifest || "")
    .replaceAll("\\", "/")
    .split("/"),
);
const f1TelemetryDataV1Manifest = fs.existsSync(f1TelemetryDataV1ManifestPath)
  ? JSON.parse(fs.readFileSync(f1TelemetryDataV1ManifestPath, "utf8"))
  : {};
const f1TelemetryDataV1ReportPath = path.join(
  dist,
  ...String(f1TelemetryDataV1Manifest.report?.path || "")
    .replaceAll("\\", "/")
    .split("/"),
);
const f1TelemetryDataV1Report = fs.existsSync(f1TelemetryDataV1ReportPath)
  ? JSON.parse(fs.readFileSync(f1TelemetryDataV1ReportPath, "utf8"))
  : {};
if (
  f1TelemetryDataV1Target?.status !== "METHOD_EQUIVALENT_REFERENCE_BENCHMARKED"
  || f1TelemetryDataV1Manifest.target_id
    !== "f1telemetrydata-reverse-engineered-v1"
  || f1TelemetryDataV1Manifest.status
    !== "METHOD_EQUIVALENT_REFERENCE_BENCHMARKED"
  || !isInside(dist, f1TelemetryDataV1ReportPath)
  || !fs.existsSync(f1TelemetryDataV1ReportPath)
  || sha256(f1TelemetryDataV1ReportPath)
    !== f1TelemetryDataV1Manifest.report?.sha256
  || f1TelemetryDataV1Report.status
    !== "METHOD_EQUIVALENT_REFERENCE_BENCHMARKED"
  || f1TelemetryDataV1Report.one_to_one_status !== "SKIPPED_OPAQUE_METHOD"
  || f1TelemetryDataV1Report.reference_identity?.chart_count !== 13
  || f1TelemetryDataV1Report.validation?.status !== "PASS"
  || !Object.values(
    f1TelemetryDataV1Report.validation?.exact_reference_gates || {},
  ).every(Boolean)
  || f1TelemetryDataV1Report.validation?.synthetic_points !== 0
  || f1TelemetryDataV1Report.audited_analysis?.qualifying
    ?.phase_coverage?.identified !== 88
  || f1TelemetryDataV1Report.audited_analysis
    ?.direct_global_race_pace_causal_order_allowed !== false
  || f1TelemetryDataV1Report.audited_analysis
    ?.direct_physical_vehicle_claims_allowed !== false
  || f1TelemetryDataV1Report.validation?.race
    ?.pit_team_means?.mae >= .07
  || f1TelemetryDataV1Report.validation?.qualifying
    ?.lap_sections?.mae_pp >= 2.65
  || !f1TelemetryDataV1Html.includes(
    'data-page="f1telemetrydata-reverse-engineered-v1.html"',
  )
  || !f1TelemetryDataV1Html.includes(
    'data-module="f1telemetrydata-reverse-engineered-v1.js?',
  )
  || !f1TelemetryDataV1Html.includes('id="lapCompareChart"')
  || !f1TelemetryDataV1Html.includes('id="raceAuditTable"')
  || !f1TelemetryDataV1Js.includes("NOT_TESTED_OFFICIAL_GEOMETRIC_ANCHOR")
  || !f1TelemetryDataV1Js.includes("SKIPPED_OPAQUE_METHOD")
  || !f1TelemetryDataV1Js.includes("NOT_IDENTIFIABLE")
  || !f1TelemetryDataV1Js.includes("__F1TR_F1TELEMETRYDATA_V1_READY__")
) {
  console.error("F1TelemetryData reverse-engineered v1 build contract failed");
  process.exit(1);
}

const gpTempoV1Html = fs.readFileSync(
  path.join(dist, "gptempo-reverse-engineered-v1.html"),
  "utf8",
);
const gpTempoV1Js = fs.readFileSync(
  path.join(dist, "gptempo-reverse-engineered-v1.js"),
  "utf8",
);
const gpTempoV1Target = (referenceLabV2Index.targets || []).find(
  (row) => row.target_id === "gptempo-reverse-engineered-v1",
);
const gpTempoV1ManifestPath = path.join(
  dist,
  ...String(gpTempoV1Target?.manifest || "")
    .replaceAll("\\", "/")
    .split("/"),
);
const gpTempoV1Manifest = fs.existsSync(gpTempoV1ManifestPath)
  ? JSON.parse(fs.readFileSync(gpTempoV1ManifestPath, "utf8"))
  : {};
const gpTempoV1ReportPath = path.join(
  dist,
  ...String(gpTempoV1Manifest.report?.path || "")
    .replaceAll("\\", "/")
    .split("/"),
);
const gpTempoV1Report = fs.existsSync(gpTempoV1ReportPath)
  ? JSON.parse(fs.readFileSync(gpTempoV1ReportPath, "utf8"))
  : {};
if (
  gpTempoV1Target?.status
    !== "PUBLIC_METHOD_REIMPLEMENTED_ENDPOINT_VALIDATED"
  || gpTempoV1Manifest.target_id !== "gptempo-reverse-engineered-v1"
  || gpTempoV1Manifest.status
    !== "PUBLIC_METHOD_REIMPLEMENTED_ENDPOINT_VALIDATED"
  || !isInside(dist, gpTempoV1ReportPath)
  || !fs.existsSync(gpTempoV1ReportPath)
  || sha256(gpTempoV1ReportPath) !== gpTempoV1Manifest.report?.sha256
  || gpTempoV1Report.status
    !== "PUBLIC_METHOD_REIMPLEMENTED_ENDPOINT_VALIDATED"
  || gpTempoV1Report.reference_identity?.method_disclosure
    !== "EXPLICIT_PUBLIC_THREE_STEP_DELTA_METHOD"
  || gpTempoV1Report.validation?.status !== "PASS"
  || gpTempoV1Report.validation?.real_laps !== 10
  || gpTempoV1Report.validation?.synthetic_laps !== 0
  || gpTempoV1Report.validation?.ordered_pair_comparisons !== 90
  || gpTempoV1Report.validation?.sector_endpoint_checks !== 270
  || gpTempoV1Report.validation?.max_abs_sector_endpoint_error_s !== 0
  || gpTempoV1Report.validation?.default_selection_gate !== "PASS"
  || gpTempoV1Report.audited_analysis
    ?.direct_driver_or_vehicle_causal_claim_allowed !== false
  || gpTempoV1Report.visual_replication?.default_selection?.status
    !== "COMPARABLE_DEFAULT"
  || !gpTempoV1Html.includes(
    'data-page="gptempo-reverse-engineered-v1.html"',
  )
  || !gpTempoV1Html.includes(
    'data-module="gptempo-reverse-engineered-v1.js?',
  )
  || !gpTempoV1Html.includes('id="telemetryChart"')
  || !gpTempoV1Html.includes('id="deltaChart"')
  || !gpTempoV1Html.includes('id="trackMap"')
  || !gpTempoV1Js.includes("WARNING_CROSS_SESSION_COMBINED_CONDITIONS")
  || !gpTempoV1Js.includes("ESTIMATED_LINEAR_INTERPOLATION_APPROX_4HZ")
  || !gpTempoV1Js.includes("PASS_EXACT_OFFICIAL_SECTOR_ENDPOINTS")
  || !gpTempoV1Js.includes("__F1TR_GPTEMPO_V1_READY__")
) {
  console.error("GP Tempo reverse-engineered v1 build contract failed");
  process.exit(1);
}

const trackValidationV3Html = fs.readFileSync(
  path.join(dist, "track-validation-v3.html"),
  "utf8",
);
const trackValidationV3Css = fs.readFileSync(
  path.join(dist, "track-validation-v3.css"),
  "utf8",
);
const trackValidationV3Js = fs.readFileSync(
  path.join(dist, "track-validation-v3.js"),
  "utf8",
);
const referenceLabV3Index = JSON.parse(
  fs.readFileSync(
    path.join(dist, "data", "reference-analysis-lab", "v3", "manifest.json"),
    "utf8",
  ),
);
const trackValidationLatestRows = (referenceLabV3Index.targets || []).filter(
  (row) => row.run_id === referenceLabV3Index.latest_run_id,
);
const trackValidationV3Target = trackValidationLatestRows[0];
const trackValidationV3ManifestPath = path.join(
  dist,
  ...String(trackValidationV3Target?.manifest || "")
    .replaceAll("\\", "/")
    .split("/"),
);
const trackValidationV3Manifest = fs.existsSync(trackValidationV3ManifestPath)
  ? JSON.parse(fs.readFileSync(trackValidationV3ManifestPath, "utf8"))
  : {};
const trackValidationV3ReportPath = path.join(
  dist,
  ...String(trackValidationV3Manifest.report?.path || "")
    .replaceAll("\\", "/")
    .split("/"),
);
const trackValidationV3RegistryPath = path.join(
  dist,
  ...String(trackValidationV3Manifest.model_registry?.path || "")
    .replaceAll("\\", "/")
    .split("/"),
);
const trackValidationV3Report = fs.existsSync(trackValidationV3ReportPath)
  ? JSON.parse(fs.readFileSync(trackValidationV3ReportPath, "utf8"))
  : {};
const trackValidationV3Registry = fs.existsSync(trackValidationV3RegistryPath)
  ? JSON.parse(fs.readFileSync(trackValidationV3RegistryPath, "utf8"))
  : {};
const trackValidationDTasks = new Map();
for (const row of (
  trackValidationV3Report.targets?.f1telemetrydata?.model_comparison || []
)) {
  trackValidationDTasks.set(
    row.chart_task,
    (trackValidationDTasks.get(row.chart_task) || 0) + 1,
  );
}
const trackValidationGpUniversal = (
  trackValidationV3Report.targets?.gptempo?.model_comparison || []
).find((row) => row.model_type === "universal");
if (
  referenceLabV3Index.schema_version !== "reference-analysis-lab-index-v3"
  || trackValidationLatestRows.length !== 1
  || trackValidationV3Target?.status !== "PASS_WITH_NOT_TESTED_GAPS"
  || !(referenceLabV3Index.targets || []).some(
    (row) => row.status === "FAILED_POST_PUBLICATION_IDENTITY_AUDIT",
  )
  || !isInside(dist, trackValidationV3ManifestPath)
  || !fs.existsSync(trackValidationV3ManifestPath)
  || sha256(trackValidationV3ManifestPath)
    !== trackValidationV3Target.manifest_sha256
  || trackValidationV3Manifest.status !== "PASS_WITH_NOT_TESTED_GAPS"
  || trackValidationV3Manifest.run_id !== referenceLabV3Index.latest_run_id
  || !isInside(dist, trackValidationV3ReportPath)
  || !fs.existsSync(trackValidationV3ReportPath)
  || sha256(trackValidationV3ReportPath)
    !== trackValidationV3Manifest.report?.sha256
  || !isInside(dist, trackValidationV3RegistryPath)
  || !fs.existsSync(trackValidationV3RegistryPath)
  || sha256(trackValidationV3RegistryPath)
    !== trackValidationV3Manifest.model_registry?.sha256
  || trackValidationV3Report.schema_version
    !== "reference-analysis-track-validation-report-v3"
  || trackValidationV3Report.coverage?.events !== 70
  || trackValidationV3Report.coverage?.tracks !== 24
  || Object.keys(trackValidationV3Report.targets || {}).length !== 5
  || trackValidationV3Report.targets?.f1pace
    ?.selected_model_counts?.universal !== 24
  || trackValidationV3Report.targets?.deltadata
    ?.selected_model_counts?.universal !== 13
  || trackValidationV3Report.targets?.deltadata
    ?.selected_model_counts?.cluster !== 5
  || trackValidationV3Report.targets?.deltadata
    ?.selected_model_counts?.track !== 6
  || trackValidationV3Report.targets?.fdataanalysis
    ?.selected_model_counts?.universal !== 24
  || trackValidationDTasks.size !== 7
  || ![...trackValidationDTasks.values()].every((count) => count === 3)
  || trackValidationGpUniversal?.sector_endpoint_checks !== 270
  || trackValidationGpUniversal?.max_abs_sector_endpoint_error_s !== 0
  || trackValidationGpUniversal?.segment_mae !== null
  || trackValidationV3Report.layers?.visual_replication
    ?.causal_ranking_allowed !== false
  || trackValidationV3Report.layers?.audited_analysis
    ?.not_tested_is_zero !== false
  || trackValidationV3Report.boundaries?.cross_task_replication_score !== null
  || trackValidationV3Registry.cross_task_total_score !== null
  || !trackValidationV3Html.includes('data-page="track-validation-v3.html"')
  || !trackValidationV3Html.includes('data-module="track-validation-v3.js?')
  || !trackValidationV3Html.includes('id="comparisonBody"')
  || !trackValidationV3Html.includes('id="registryBody"')
  || !trackValidationV3Js.includes("__F1TR_TRACK_VALIDATION_V3_READY__")
  || !trackValidationV3Js.includes("NOT_TESTED")
  || !trackValidationV3Css.includes("@media (max-width: 410px)")
  || !referenceLabHtml.includes('href="track-validation-v3.html"')
) {
  console.error("Reference analysis track-validation v3 build contract failed");
  process.exit(1);
}

console.log("Frontend build contract OK");
