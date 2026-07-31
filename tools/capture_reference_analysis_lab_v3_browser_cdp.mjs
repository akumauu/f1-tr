#!/usr/bin/env node
/**
 * 用 Chrome DevTools Protocol 强制真实 CSS viewport，串行验收 v3。
 */

import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const CACHE_ROOT = path.join(
  ROOT,
  ".runtime-cache",
  "reference-analysis-lab-v3",
  "browser-acceptance",
);
const RECORDS_ROOT = path.join(
  ROOT,
  "research",
  "records",
  "reference_analysis_lab_v3_browser_acceptance",
);
const baseUrl = String(
  process.argv[2] || "http://127.0.0.1:5187",
).replace(/\/+$/, "");
const revision = "r2-cdp-css390";
const chromeCandidates = [
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
];
const chrome = chromeCandidates.find((candidate) => fs.existsSync(candidate));
if (!chrome) throw new Error("未找到 Chrome/Edge");

function sha256Bytes(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

function sha256File(file) {
  return sha256Bytes(fs.readFileSync(file));
}

function relative(file) {
  return path.relative(ROOT, file).split(path.sep).join("/");
}

function writeJson(file, payload) {
  const bytes = Buffer.from(`${JSON.stringify(payload, null, 2)}\n`, "utf8");
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, bytes);
  return sha256Bytes(bytes);
}

async function fetchVerifiedJson(url, expectedHash = null) {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) throw new Error(`HTTP ${response.status} · ${url}`);
  const text = await response.text();
  const actualHash = sha256Bytes(Buffer.from(text, "utf8"));
  if (expectedHash && actualHash !== expectedHash) {
    throw new Error(`HTTP 产物 SHA-256 不一致 · ${url}`);
  }
  return {
    value: JSON.parse(text),
    sha256: actualHash,
    bytes: Buffer.byteLength(text, "utf8"),
  };
}

function pngDimensions(file) {
  const bytes = fs.readFileSync(file);
  if (bytes.subarray(0, 8).toString("hex") !== "89504e470d0a1a0a") {
    throw new Error(`不是有效 PNG：${file}`);
  }
  return {
    width: bytes.readUInt32BE(16),
    height: bytes.readUInt32BE(20),
  };
}

class CdpClient {
  constructor(url) {
    this.url = url;
    this.socket = null;
    this.nextId = 1;
    this.pending = new Map();
    this.events = [];
  }

  async connect() {
    this.socket = new WebSocket(this.url);
    await new Promise((resolve, reject) => {
      this.socket.addEventListener("open", resolve, { once: true });
      this.socket.addEventListener("error", reject, { once: true });
    });
    this.socket.addEventListener("message", (event) => {
      const message = JSON.parse(event.data);
      if (message.id) {
        const pending = this.pending.get(message.id);
        if (!pending) return;
        this.pending.delete(message.id);
        if (message.error) pending.reject(new Error(message.error.message));
        else pending.resolve(message.result || {});
      } else {
        this.events.push(message);
      }
    });
  }

  send(method, params = {}) {
    const id = this.nextId;
    this.nextId += 1;
    const promise = new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
    });
    this.socket.send(JSON.stringify({ id, method, params }));
    return promise;
  }

  close() {
    this.socket?.close();
  }
}

async function waitForFile(file, timeoutMs = 15_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (fs.existsSync(file)) return;
    await delay(100);
  }
  throw new Error(`等待 Chrome 调试端口超时：${file}`);
}

async function waitForReady(client, expectedTarget, timeoutMs = 20_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const result = await client.send("Runtime.evaluate", {
      expression: `(() => {
        const root = document.documentElement;
        const body = document.body;
        return {
          ready: root.dataset.ready || null,
          innerWidth: window.innerWidth,
          innerHeight: window.innerHeight,
          documentScrollWidth: root.scrollWidth,
          bodyScrollWidth: body ? body.scrollWidth : null,
          runText: document.getElementById("runIdentity")?.textContent || "",
          selectedTarget: document.getElementById("targetSelect")?.value || "",
          selectedTask: document.getElementById("taskSelect")?.value || "",
          taskControlDisplay: document.getElementById("taskControl")
            ? getComputedStyle(document.getElementById("taskControl")).display
            : null,
          failureVisible: (body?.innerText || "").includes(
            "冻结产物未通过页面身份校验"
          ),
        };
      })()`,
      returnByValue: true,
    });
    const value = result.result?.value;
    if (
      value?.ready === "true"
      && value.selectedTarget === expectedTarget
    ) {
      return value;
    }
    if (value?.ready === "error") {
      throw new Error(`${expectedTarget} 页面进入 data-ready=error`);
    }
    await delay(200);
  }
  throw new Error(`${expectedTarget} 页面 ready 超时`);
}

const indexResult = await fetchVerifiedJson(
  `${baseUrl}/data/reference-analysis-lab/v3/manifest.json`,
);
const index = indexResult.value;
const latestRows = (index.targets || []).filter(
  (row) => row.run_id === index.latest_run_id,
);
if (
  index.schema_version !== "reference-analysis-lab-index-v3"
  || latestRows.length !== 1
  || latestRows[0].status !== "PASS_WITH_NOT_TESTED_GAPS"
) {
  throw new Error("HTTP v3 latest 索引身份不唯一或未通过");
}
const publicManifestResult = await fetchVerifiedJson(
  `${baseUrl}/${latestRows[0].manifest}`,
  latestRows[0].manifest_sha256,
);
const publicManifest = publicManifestResult.value;
const reportResult = await fetchVerifiedJson(
  `${baseUrl}/${publicManifest.report.path}`,
  publicManifest.report.sha256,
);
const report = reportResult.value;
if (
  report.run_id !== index.latest_run_id
  || report.coverage?.events !== 70
  || report.coverage?.tracks !== 24
) {
  throw new Error("HTTP report run/coverage 身份不闭合");
}

const runId = report.run_id;
const acceptanceId = `${runId}-${revision}`;
const outputDir = path.join(CACHE_ROOT, `run=${acceptanceId}`);
const recordDir = path.join(RECORDS_ROOT, `run=${acceptanceId}-browser`);
if (fs.existsSync(outputDir) || fs.existsSync(recordDir)) {
  throw new Error("CDP 浏览器验收采用追加式身份；拒绝覆盖已有 run");
}
fs.mkdirSync(outputDir, { recursive: true });
const profileDir = path.join(outputDir, "chrome-profile");
fs.mkdirSync(profileDir, { recursive: true });
const activePortFile = path.join(profileDir, "DevToolsActivePort");
const chromeProcess = spawn(
  chrome,
  [
    "--headless=new",
    "--disable-gpu",
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-default-apps",
    "--disable-sync",
    "--hide-scrollbars",
    "--metrics-recording-only",
    "--no-first-run",
    "--remote-debugging-address=127.0.0.1",
    "--remote-debugging-port=0",
    `--user-data-dir=${profileDir}`,
    "about:blank",
  ],
  {
    cwd: ROOT,
    stdio: ["ignore", "pipe", "pipe"],
    windowsHide: true,
  },
);

const cases = [
  { target: "f1pace", track: "abu-dhabi-grand-prix", task: null },
  { target: "deltadata", track: "abu-dhabi-grand-prix", task: null },
  { target: "fdataanalysis", track: "australian-grand-prix", task: null },
  {
    target: "f1telemetrydata",
    track: "abu-dhabi-grand-prix",
    task: "qualifying_q1_q2_q3_phase",
  },
  { target: "gptempo", track: "abu-dhabi-grand-prix", task: null },
];
const viewports = [
  { name: "desktop", width: 1440, height: 1100, mobile: false },
  { name: "mobile-390", width: 390, height: 1400, mobile: true },
];
const screenshots = [];
const browserChecks = [];

try {
  await waitForFile(activePortFile);
  const [portText] = fs
    .readFileSync(activePortFile, "utf8")
    .trim()
    .split(/\r?\n/);
  const port = Number(portText);
  if (!Number.isInteger(port) || port <= 0) {
    throw new Error("Chrome DevToolsActivePort 非法");
  }

  for (const testCase of cases) {
    for (const viewport of viewports) {
      const url = new URL(`${baseUrl}/track-validation-v3.html`);
      url.searchParams.set("target", testCase.target);
      url.searchParams.set("track", testCase.track);
      url.searchParams.set("layer", "audited_analysis");
      if (testCase.task) url.searchParams.set("task", testCase.task);

      const targetResponse = await fetch(
        `http://127.0.0.1:${port}/json/new?about%3Ablank`,
        { method: "PUT" },
      );
      if (!targetResponse.ok) {
        throw new Error(`创建 CDP target 失败：${targetResponse.status}`);
      }
      const target = await targetResponse.json();
      const client = new CdpClient(target.webSocketDebuggerUrl);
      await client.connect();
      try {
        await client.send("Page.enable");
        await client.send("Runtime.enable");
        await client.send("Network.enable");
        await client.send("Emulation.setDeviceMetricsOverride", {
          width: viewport.width,
          height: viewport.height,
          deviceScaleFactor: 1,
          mobile: viewport.mobile,
          screenWidth: viewport.width,
          screenHeight: viewport.height,
          screenOrientation: {
            type: "portraitPrimary",
            angle: 0,
          },
        });
        await client.send("Emulation.setTouchEmulationEnabled", {
          enabled: viewport.mobile,
          maxTouchPoints: viewport.mobile ? 5 : 1,
        });
        await client.send("Emulation.setScrollbarsHidden", {
          hidden: true,
        });
        await client.send("Page.navigate", { url: url.href });
        const ready = await waitForReady(client, testCase.target);
        await client.send("Runtime.evaluate", {
          expression:
            "new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))",
          awaitPromise: true,
        });

        const runtimeErrors = client.events.filter(
          (event) => event.method === "Runtime.exceptionThrown",
        );
        const consoleErrors = client.events.filter(
          (event) => (
            event.method === "Runtime.consoleAPICalled"
            && event.params?.type === "error"
          ),
        );
        const networkFailures = client.events.filter(
          (event) => (
            event.method === "Network.loadingFailed"
            && !event.params?.canceled
          ),
        );
        const expectedTaskDisplay = testCase.target === "f1telemetrydata"
          ? "grid"
          : "none";
        if (
          ready.innerWidth !== viewport.width
          || ready.documentScrollWidth > viewport.width
          || ready.bodyScrollWidth > viewport.width
          || !ready.runText.includes(runId)
          || ready.failureVisible
          || ready.taskControlDisplay !== expectedTaskDisplay
          || (
            testCase.task
            && ready.selectedTask !== testCase.task
          )
          || runtimeErrors.length
          || consoleErrors.length
          || networkFailures.length
        ) {
          throw new Error(
            `${testCase.target} ${viewport.name} viewport/runtime 验收失败：`
            + JSON.stringify({
              ready,
              runtime_errors: runtimeErrors.length,
              console_errors: consoleErrors.length,
              network_failures: networkFailures.length,
            }),
          );
        }

        const screenshotResult = await client.send(
          "Page.captureScreenshot",
          {
            format: "png",
            fromSurface: true,
            captureBeyondViewport: false,
          },
        );
        const screenshotPath = path.join(
          outputDir,
          `${testCase.target}-${viewport.name}.png`,
        );
        fs.writeFileSync(
          screenshotPath,
          Buffer.from(screenshotResult.data, "base64"),
        );
        const dimensions = pngDimensions(screenshotPath);
        if (
          dimensions.width !== viewport.width
          || dimensions.height !== viewport.height
        ) {
          throw new Error(
            `${testCase.target} ${viewport.name} PNG 尺寸不一致`,
          );
        }
        screenshots.push({
          target_id: testCase.target,
          viewport: viewport.name,
          css_inner_width: ready.innerWidth,
          document_scroll_width: ready.documentScrollWidth,
          body_scroll_width: ready.bodyScrollWidth,
          width: dimensions.width,
          height: dimensions.height,
          path: relative(screenshotPath),
          sha256: sha256File(screenshotPath),
          bytes: fs.statSync(screenshotPath).size,
          url: url.href,
        });
        browserChecks.push({
          target_id: testCase.target,
          viewport: viewport.name,
          css_viewport_exact: ready.innerWidth === viewport.width,
          no_page_horizontal_overflow: (
            ready.documentScrollWidth <= viewport.width
            && ready.bodyScrollWidth <= viewport.width
          ),
          run_identity_visible: ready.runText.includes(runId),
          task_control_semantics_passed: (
            ready.taskControlDisplay === expectedTaskDisplay
          ),
          runtime_exception_count: runtimeErrors.length,
          console_error_count: consoleErrors.length,
          network_failure_count: networkFailures.length,
        });
      } finally {
        client.close();
        await fetch(
          `http://127.0.0.1:${port}/json/close/${target.id}`,
        ).catch(() => {});
      }
    }
  }
} finally {
  chromeProcess.kill();
}

const acceptance = {
  schema_version: "reference-analysis-lab-v3-browser-acceptance-cdp",
  acceptance_id: acceptanceId,
  run_id: runId,
  status: "PASS",
  supersedes_acceptance: {
    id: `${runId}-browser-cli-r1`,
    reason: "CLI_PNG_WIDTH_390_BUT_CSS_VIEWPORT_CLAMPED_ABOVE_390",
  },
  base_url: baseUrl,
  page: "track-validation-v3.html",
  browser: path.basename(chrome),
  execution: {
    protocol: "Chrome DevTools Protocol",
    local_http: true,
    foreground_serial_cases: true,
    viewport_cases: browserChecks.length,
    screenshots: screenshots.length,
    exact_css_mobile_width: 390,
  },
  data_identity: {
    index_sha256: indexResult.sha256,
    public_manifest_sha256: publicManifestResult.sha256,
    report_sha256: reportResult.sha256,
  },
  browser_checks: browserChecks,
  screenshots,
};
const cacheAcceptancePath = path.join(outputDir, "browser_acceptance.json");
const cacheAcceptanceSha = writeJson(cacheAcceptancePath, acceptance);

fs.mkdirSync(RECORDS_ROOT, { recursive: true });
fs.mkdirSync(recordDir, { recursive: false });
const targetManifestRefs = [];
for (const testCase of cases) {
  const targetManifest = {
    schema_version: "reference-analysis-lab-v3-target-browser-acceptance-cdp",
    acceptance_id: acceptanceId,
    run_id: runId,
    target_id: testCase.target,
    status: "PASS",
    browser_checks: browserChecks.filter(
      (row) => row.target_id === testCase.target,
    ),
    screenshots: screenshots.filter(
      (row) => row.target_id === testCase.target,
    ),
  };
  const targetManifestPath = path.join(
    recordDir,
    "targets",
    testCase.target,
    "acceptance_manifest.json",
  );
  const targetManifestSha = writeJson(targetManifestPath, targetManifest);
  targetManifestRefs.push({
    target_id: testCase.target,
    path: relative(targetManifestPath),
    sha256: targetManifestSha,
    bytes: fs.statSync(targetManifestPath).size,
  });
}
const recordAcceptancePath = path.join(recordDir, "browser_acceptance.json");
fs.copyFileSync(cacheAcceptancePath, recordAcceptancePath);
const acceptanceManifest = {
  schema_version: "reference-analysis-lab-v3-browser-acceptance-manifest-cdp",
  acceptance_id: acceptanceId,
  run_id: runId,
  status: "PASS",
  source_run_manifest: publicManifest.source_manifest,
  browser_acceptance: {
    path: relative(recordAcceptancePath),
    sha256: cacheAcceptanceSha,
    bytes: fs.statSync(recordAcceptancePath).size,
  },
  target_manifests: targetManifestRefs,
  screenshots_live_under_runtime_cache: true,
  screenshot_count: screenshots.length,
  exact_css_mobile_width: 390,
};
const sourceManifestPath = path.join(
  ROOT,
  publicManifest.source_manifest.path,
);
if (
  !fs.existsSync(sourceManifestPath)
  || sha256File(sourceManifestPath)
    !== publicManifest.source_manifest.sha256
) {
  throw new Error("CDP 验收引用的 source run manifest 身份不一致");
}
const acceptanceManifestPath = path.join(
  recordDir,
  "acceptance_manifest.json",
);
const acceptanceManifestSha = writeJson(
  acceptanceManifestPath,
  acceptanceManifest,
);

const firstRecordDir = path.join(
  RECORDS_ROOT,
  `run=${runId}-browser`,
);
const firstManifestPath = path.join(
  firstRecordDir,
  "acceptance_manifest.json",
);
if (!fs.existsSync(firstManifestPath)) {
  throw new Error("缺少需保留的首轮 CLI 浏览器验收记录");
}
const acceptanceIndex = {
  schema_version: "reference-analysis-lab-v3-browser-acceptance-index",
  latest_acceptance_id: acceptanceId,
  runs: [
    {
      acceptance_id: `${runId}-browser-cli-r1`,
      run_id: runId,
      status: "FAILED_CSS_VIEWPORT_IDENTITY_AUDIT",
      reason: "CLI_PNG_WIDTH_390_BUT_CSS_VIEWPORT_CLAMPED_ABOVE_390",
      manifest: relative(firstManifestPath),
      manifest_sha256: sha256File(firstManifestPath),
    },
    {
      acceptance_id: acceptanceId,
      run_id: runId,
      status: "PASS",
      manifest: relative(acceptanceManifestPath),
      manifest_sha256: acceptanceManifestSha,
    },
  ],
};
writeJson(path.join(RECORDS_ROOT, "manifest.json"), acceptanceIndex);

console.log(JSON.stringify({
  status: "PASS",
  acceptance_id: acceptanceId,
  run_id: runId,
  viewport_cases: browserChecks.length,
  screenshot_count: screenshots.length,
  exact_css_mobile_width: 390,
  output_dir: relative(outputDir),
  acceptance_manifest: relative(acceptanceManifestPath),
}, null, 2));
