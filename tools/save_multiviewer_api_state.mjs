import fs from "node:fs";
import path from "node:path";

const DEFAULT_API_BASE = process.env.MULTIVIEWER_API_URL ?? "http://127.0.0.1:10101";
const OUT_DIR =
  process.env.MULTIVIEWER_API_CAPTURE_DIR ??
  path.join(process.cwd(), "data", "raw", "multiviewer-api-live-timing");
const DEFAULT_INTERVAL_MS = Number(process.env.MULTIVIEWER_API_CAPTURE_INTERVAL_MS ?? 5000);
const REQUEST_TIMEOUT_MS = Number(process.env.MULTIVIEWER_API_TIMEOUT_MS ?? 4000);

const TOPIC_GROUPS = {
  core: [
    "SessionInfo",
    "SessionStatus",
    "LapCount",
    "DriverList",
    "TimingData",
    "TimingAppData",
    "TimingStats",
    "LapSeries",
    "PitLaneTimeCollection",
    "PitStopTimeCollection",
    "RaceControlMessages",
    "TeamRadio",
    "TrackStatus",
    "WeatherData",
    "WeatherDataSeries",
    "TopThree",
  ],
  heavy: ["CarData", "Position", "DriverTracker"],
};

const GRAPHQL_SUPPORTED_TOPICS = new Set([
  "ArchiveStatus",
  "AudioStreams",
  "CarData",
  "ChampionshipPrediction",
  "ContentStreams",
  "DriverList",
  "ExtrapolatedClock",
  "Heartbeat",
  "LapCount",
  "LapSeries",
  "PitLaneTimeCollection",
  "Position",
  "RaceControlMessages",
  "SessionData",
  "SessionInfo",
  "SessionStatus",
  "TeamRadio",
  "TimingAppData",
  "TimingData",
  "TimingStats",
  "TopThree",
  "TrackStatus",
  "WeatherData",
  "WeatherDataSeries",
]);

function pad(value, size = 2) {
  return String(value).padStart(size, "0");
}

function stamp(date = new Date()) {
  return [
    date.getFullYear(),
    pad(date.getMonth() + 1),
    pad(date.getDate()),
    "_",
    pad(date.getHours()),
    pad(date.getMinutes()),
    pad(date.getSeconds()),
    "_",
    pad(date.getMilliseconds(), 3),
  ].join("");
}

function parseArgs(argv) {
  const args = {
    apiBase: DEFAULT_API_BASE,
    outDir: OUT_DIR,
    intervalMs: DEFAULT_INTERVAL_MS,
    mode: process.env.MULTIVIEWER_API_CAPTURE_MODE ?? "rest",
    topics: process.env.MULTIVIEWER_API_TOPICS ?? "core",
    watch: false,
    scanPorts: process.env.MULTIVIEWER_API_SCAN_PORTS ?? "10101-10110",
    waitApi: process.env.MULTIVIEWER_API_WAIT === "1",
    waitMs: Number(process.env.MULTIVIEWER_API_WAIT_MS ?? 5000),
    fullState: false,
  };

  for (const arg of argv) {
    if (arg === "--watch") args.watch = true;
    else if (arg === "--wait-api") args.waitApi = true;
    else if (arg === "--rest") args.mode = "rest";
    else if (arg === "--graphql") args.mode = "graphql";
    else if (arg === "--all") args.fullState = true;
    else if (arg === "--include-heavy") args.topics = `${args.topics},heavy`;
    else if (arg === "--no-scan") args.scanPorts = "";
    else if (arg === "--help" || arg === "-h") args.help = true;
    else if (arg.startsWith("--api-base=")) args.apiBase = arg.slice("--api-base=".length);
    else if (arg.startsWith("--out-dir=")) args.outDir = arg.slice("--out-dir=".length);
    else if (arg.startsWith("--interval-ms=")) args.intervalMs = Number(arg.slice("--interval-ms=".length));
    else if (arg.startsWith("--topics=")) args.topics = arg.slice("--topics=".length);
    else if (arg.startsWith("--scan-ports=")) args.scanPorts = arg.slice("--scan-ports=".length);
    else if (arg.startsWith("--wait-ms=")) args.waitMs = Number(arg.slice("--wait-ms=".length));
    else throw new Error(`未知参数：${arg}`);
  }

  if (!Number.isFinite(args.intervalMs) || args.intervalMs < 500) {
    throw new Error("--interval-ms 必须是 >= 500 的数字");
  }
  if (!["rest", "graphql"].includes(args.mode)) {
    throw new Error("--mode 只支持 rest 或 graphql");
  }
  if (!Number.isFinite(args.waitMs) || args.waitMs < 1000) {
    throw new Error("--wait-ms 必须是 >= 1000 的数字");
  }
  return args;
}

function printHelp() {
  console.log(`用法：
  node tools/save_multiviewer_api_state.mjs [--watch] [--rest|--graphql]

常用参数：
  --watch                  持续采集；不加则只采一帧
  --topics=core            采集 topic 组或逗号分隔列表，默认 core
  --include-heavy          额外采集 CarData / Position / DriverTracker
  --all                    REST 模式下直接保存完整 state
  --api-base=http://...    MultiViewer 本地 API，默认 ${DEFAULT_API_BASE}
  --interval-ms=5000       持续采集间隔
  --out-dir=...            输出目录
  --graphql                使用 /api/graphql；默认使用 /api/v2/live-timing/state
  --scan-ports=10101-10110 API 默认端口被占用时尝试扫描
  --no-scan                不扫描端口
  --wait-api               API 未启动时持续等待并重试扫描
  --wait-ms=5000           --wait-api 的重试间隔
`);
}

function unique(values) {
  return [...new Set(values.filter(Boolean))];
}

function resolveTopics(input) {
  const tokens = String(input)
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
  const topics = [];
  for (const token of tokens.length > 0 ? tokens : ["core"]) {
    if (token === "core") topics.push(...TOPIC_GROUPS.core);
    else if (token === "heavy") topics.push(...TOPIC_GROUPS.heavy);
    else if (token === "all") return unique([...TOPIC_GROUPS.core, ...TOPIC_GROUPS.heavy]);
    else topics.push(token);
  }
  return unique(topics);
}

function parsePortScan(input) {
  if (!input) return [];
  const ports = [];
  for (const part of input.split(",")) {
    const item = part.trim();
    if (!item) continue;
    const range = item.match(/^(\d+)-(\d+)$/);
    if (range) {
      const start = Number(range[1]);
      const end = Number(range[2]);
      for (let port = start; port <= end; port += 1) ports.push(port);
    } else {
      ports.push(Number(item));
    }
  }
  return unique(ports.filter((port) => Number.isInteger(port) && port > 0 && port < 65536));
}

function withTimeout(timeoutMs) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  return { controller, done: () => clearTimeout(timeout) };
}

async function fetchResponse(url, options = {}) {
  const { controller, done } = withTimeout(REQUEST_TIMEOUT_MS);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } finally {
    done();
  }
}

async function fetchJson(url, options = {}) {
  const response = await fetchResponse(url, options);
  const text = await response.text();
  if (!response.ok) {
    throw new Error(`HTTP ${response.status} ${response.statusText}: ${text.slice(0, 300)}`);
  }
  return text ? JSON.parse(text) : null;
}

function normalizeBase(base) {
  return String(base).replace(/\/+$/, "");
}

async function canReachBase(base) {
  const urls = [
    `${base}/api/v1/app/version`,
    `${base}/api/v2/live-timing/state`,
    `${base}/api/graphql`,
  ];
  for (const url of urls) {
    try {
      const response = await fetchResponse(url, { method: "GET" });
      if (response.status >= 200 && response.status < 500) return true;
    } catch {
      // 继续尝试下一个探测端点。
    }
  }
  return false;
}

async function discoverApiBase(configuredBase, scanPorts) {
  const configured = normalizeBase(configuredBase);
  if (await canReachBase(configured)) return configured;

  const configuredUrl = new URL(configured);
  const ports = parsePortScan(scanPorts);
  const candidates = ports.map((port) => `${configuredUrl.protocol}//${configuredUrl.hostname}:${port}`);
  for (const candidate of unique(candidates)) {
    if (candidate === configured) continue;
    if (await canReachBase(candidate)) return candidate;
  }

  throw new Error(
    `无法连接 MultiViewer 本地 API。请先启动 MultiViewer 并打开 Live Timing，或用 --api-base 指定实际地址。已尝试：${[
      configured,
      ...candidates,
    ].join(", ")}`,
  );
}

async function discoverApiBaseWithWait(args) {
  while (true) {
    try {
      return await discoverApiBase(args.apiBase, args.scanPorts);
    } catch (error) {
      if (!args.waitApi) throw error;
      console.error(`[${new Date().toLocaleTimeString()}] ${error.message}`);
      console.error(`[${new Date().toLocaleTimeString()}] 等待 MultiViewer API，${args.waitMs}ms 后重试...`);
      await new Promise((resolve) => setTimeout(resolve, args.waitMs));
    }
  }
}

async function captureRest(apiBase, topics, fullState) {
  const [stateRaw, clock] = await Promise.all([
    fullState
      ? fetchJson(`${apiBase}/api/v2/live-timing/state`)
      : fetchJson(`${apiBase}/api/v2/live-timing/state/${topics.join(",")}`),
    fetchJson(`${apiBase}/api/v2/live-timing/clock`).catch(() => null),
  ]);
  const state = !fullState && topics.length === 1 ? { [topics[0]]: stateRaw } : stateRaw;
  return { source: "multiviewer-rest", state, clock };
}

async function captureGraphql(apiBase, topics) {
  const unsupported = topics.filter((topic) => !GRAPHQL_SUPPORTED_TOPICS.has(topic));
  if (unsupported.length > 0) {
    throw new Error(`GraphQL 模式不支持这些 topic：${unsupported.join(", ")}。可改用 --rest。`);
  }
  const fields = topics.map((topic) => `      ${topic}`).join("\n");
  const query = `query F1TRLiveTimingCapture {
    f1LiveTimingClock {
      paused
      systemTime
      trackTime
      liveTimingStartTime
    }
    f1LiveTimingState {
${fields}
    }
  }`;
  const payload = await fetchJson(`${apiBase}/api/graphql`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ query }),
  });
  if (payload.errors?.length) {
    throw new Error(payload.errors.map((error) => error.message).join("; "));
  }
  return {
    source: "multiviewer-graphql",
    state: payload.data?.f1LiveTimingState ?? {},
    clock: payload.data?.f1LiveTimingClock ?? null,
  };
}

function objectCount(value) {
  if (!value || typeof value !== "object") return 0;
  return Array.isArray(value) ? value.length : Object.keys(value).length;
}

function countTopicItems(topic) {
  if (!topic || typeof topic !== "object") return 0;
  if (Array.isArray(topic)) return topic.length;
  for (const key of ["Captures", "Entries", "Messages", "Lines", "Items"]) {
    const value = topic[key];
    if (Array.isArray(value)) return value.length;
    if (value && typeof value === "object") return Object.keys(value).length;
  }
  return Object.keys(topic).length;
}

function topicShape(topic) {
  if (!topic || typeof topic !== "object") return [];
  return Object.keys(topic).sort();
}

function getSummary(state, clock) {
  const sessionInfo = state?.SessionInfo ?? {};
  const timingLines = state?.TimingData?.Lines ?? {};
  const driverList = state?.DriverList ?? {};
  const raceControlMessages = state?.RaceControlMessages?.Messages ?? [];
  const teamRadio = state?.TeamRadio ?? null;
  return {
    meeting: sessionInfo.Meeting?.Name ?? sessionInfo.Meeting?.OfficialName ?? null,
    session: sessionInfo.Name ?? sessionInfo.Type ?? null,
    session_status: state?.SessionStatus?.Status ?? null,
    current_lap: state?.LapCount?.CurrentLap ?? state?.LapCount?.CurrentLapNumber ?? null,
    total_laps: state?.LapCount?.TotalLaps ?? null,
    driver_count: objectCount(driverList),
    timing_line_count: objectCount(timingLines),
    race_control_count: objectCount(raceControlMessages),
    team_radio_count: countTopicItems(teamRadio),
    team_radio_keys: topicShape(teamRadio),
    clock_paused: clock?.paused ?? null,
  };
}

function writeSnapshot(outDir, snapshot) {
  fs.mkdirSync(outDir, { recursive: true });
  const fileBase = `multiviewer-api-${stamp(new Date(snapshot.captured_at))}`;
  const fileName = `${fileBase}.json`;
  const file = path.join(outDir, fileName);
  const body = `${JSON.stringify(snapshot)}\n`;
  fs.writeFileSync(file, body, "utf8");
  fs.writeFileSync(path.join(outDir, "latest.json"), body, "utf8");
  fs.appendFileSync(
    path.join(outDir, "capture-index.jsonl"),
    `${JSON.stringify({
      captured_at: snapshot.captured_at,
      file: fileName,
      source: snapshot.source,
      api_base: snapshot.api_base,
      topics: snapshot.topics,
      summary: snapshot.summary,
      bytes: Buffer.byteLength(body),
    })}\n`,
    "utf8",
  );
  return file;
}

async function captureOnce(args, apiBase, topics) {
  const capturedAt = new Date();
  const result =
    args.mode === "graphql"
      ? await captureGraphql(apiBase, topics)
      : await captureRest(apiBase, topics, args.fullState);
  const snapshot = {
    captured_at: capturedAt.toISOString(),
    source: result.source,
    api_base: apiBase,
    topics: args.fullState ? ["*"] : topics,
    clock: result.clock,
    summary: getSummary(result.state, result.clock),
    state: result.state,
  };
  const file = writeSnapshot(args.outDir, snapshot);
  return { file, snapshot };
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    printHelp();
    return;
  }

  const topics = resolveTopics(args.topics);
  const apiBase = await discoverApiBaseWithWait(args);
  console.log(
    `开始采集 MultiViewer API：${apiBase}，模式：${args.mode}，topic：${
      args.fullState ? "完整 state" : topics.join(",")
    }`,
  );

  if (!args.watch) {
    const { file, snapshot } = await captureOnce(args, apiBase, topics);
    console.log(`保存成功：${file}`);
    console.log(JSON.stringify(snapshot.summary, null, 2));
    return;
  }

  while (true) {
    try {
      const { file, snapshot } = await captureOnce(args, apiBase, topics);
      console.log(
        `[${new Date().toLocaleTimeString()}] 保存成功：${path.basename(file)} ` +
          `lap=${snapshot.summary.current_lap ?? "-"} drivers=${snapshot.summary.driver_count}`,
      );
    } catch (error) {
      console.error(`[${new Date().toLocaleTimeString()}] 保存失败：${error.message}`);
    }
    await new Promise((resolve) => setTimeout(resolve, args.intervalMs));
  }
}

main().catch((error) => {
  console.error(error.message);
  process.exitCode = 1;
});
