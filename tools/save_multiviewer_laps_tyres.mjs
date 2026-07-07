import fs from "node:fs";
import path from "node:path";

const DEBUG_BASE = process.env.MULTIVIEWER_DEBUG_URL ?? "http://127.0.0.1:9223";
const OUT_DIR =
  process.env.MULTIVIEWER_CAPTURE_DIR ??
  path.join(process.cwd(), "data", "raw", "multiviewer-live-timing");
const WATCH = process.argv.includes("--watch");
const INTERVAL_MS = Number(process.env.MULTIVIEWER_CAPTURE_INTERVAL_MS ?? 5000);
const COMPOUNDS = new Set(["S", "M", "H", "I", "W", "N", "?"]);

function pad(value) {
  return String(value).padStart(2, "0");
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
  ].join("");
}

function csvEscape(value) {
  const text = String(value ?? "");
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

function writeRows(base, rows) {
  fs.writeFileSync(`${base}.json`, JSON.stringify(rows, null, 2), "utf8");
  fs.writeFileSync(
    `${base}.csv`,
    [
      [
        "captured_at",
        "race_clock",
        "race_lap",
        "total_laps",
        "position",
        "driver_tla",
        "tyre_compound",
        "tyre_age_laps",
      ].join(","),
      ...rows.map((row) =>
        [
          row.captured_at,
          row.race_clock,
          row.race_lap,
          row.total_laps,
          row.position,
          row.driver_tla,
          row.tyre_compound,
          row.tyre_age_laps,
        ]
          .map(csvEscape)
          .join(","),
      ),
    ].join("\n"),
    "utf8",
  );
}

async function getPages() {
  const response = await fetch(`${DEBUG_BASE}/json/list`);
  if (!response.ok) {
    throw new Error(`无法读取 MultiViewer 调试页面：HTTP ${response.status}`);
  }
  return response.json();
}

async function evalInPage(webSocketDebuggerUrl, expression) {
  const ws = new WebSocket(webSocketDebuggerUrl);
  let id = 0;

  function send(method, params = {}) {
    return new Promise((resolve, reject) => {
      const msgId = ++id;
      const onMessage = (event) => {
        const data = JSON.parse(event.data);
        if (data.id !== msgId) return;
        ws.removeEventListener("message", onMessage);
        if (data.error) reject(new Error(JSON.stringify(data.error)));
        else resolve(data.result);
      };
      ws.addEventListener("message", onMessage);
      ws.send(JSON.stringify({ id: msgId, method, params }));
    });
  }

  await new Promise((resolve, reject) => {
    ws.addEventListener("open", resolve, { once: true });
    ws.addEventListener("error", reject, { once: true });
  });

  try {
    const result = await send("Runtime.evaluate", {
      expression,
      returnByValue: true,
      awaitPromise: true,
    });
    return result.result?.value ?? "";
  } finally {
    ws.close();
  }
}

function findNextDriverRow(lines, startIndex, position) {
  const expected = String(position);
  for (let i = startIndex; i < lines.length - 1; i += 1) {
    if (lines[i] === expected && /^[A-Z]{3}$/.test(lines[i + 1])) {
      return i;
    }
  }
  return -1;
}

function parseDriverBlock(block) {
  const driverTla = block[1];
  for (let i = block.length - 2; i >= 0; i -= 1) {
    if (COMPOUNDS.has(block[i]) && /^\d+$/.test(block[i + 1] ?? "")) {
      return {
        driver_tla: driverTla,
        tyre_compound: block[i],
        tyre_age_laps: Number(block[i + 1]),
      };
    }
  }
  return {
    driver_tla: driverTla,
    tyre_compound: null,
    tyre_age_laps: null,
  };
}

function parseLiveTimingText(text, capturedAt = new Date()) {
  const header = text.match(/(?<clock>(?:\d+:)?\d{2}:\d{2})\s+Lap:\s*(?<lap>\d+)\/(?<total>\d+)/);
  const raceClock = header?.groups?.clock ?? null;
  const raceLap = header ? Number(header.groups.lap) : null;
  const totalLaps = header ? Number(header.groups.total) : null;

  const lines = text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);

  const raceIndex = lines.indexOf("RACE");
  if (raceIndex < 0) return [];

  const rows = [];
  let cursor = raceIndex + 1;
  for (let position = 1; position <= 30; position += 1) {
    const rowStart = findNextDriverRow(lines, cursor, position);
    if (rowStart < 0) break;
    const nextRow = findNextDriverRow(lines, rowStart + 2, position + 1);
    const rowEnd = nextRow < 0 ? lines.length : nextRow;
    const block = lines.slice(rowStart, rowEnd);
    const parsed = parseDriverBlock(block);
    rows.push({
      captured_at: capturedAt.toISOString(),
      race_clock: raceClock,
      race_lap: raceLap,
      total_laps: totalLaps,
      position,
      ...parsed,
    });
    cursor = rowEnd;
  }

  return rows;
}

async function captureOnce() {
  const pages = await getPages();
  const liveTimingPage = pages.find((page) => page.title?.includes("Live Timing"));
  if (!liveTimingPage?.webSocketDebuggerUrl) {
    throw new Error("没有找到 Live Timing 窗口，请先在 MultiViewer 打开 Live Timing。");
  }

  const capturedAt = new Date();
  const text = await evalInPage(liveTimingPage.webSocketDebuggerUrl, "document.body.innerText");
  const rows = parseLiveTimingText(text, capturedAt);
  if (rows.length === 0) {
    throw new Error("没有解析到圈数/轮胎数据，请确认 Live Timing 处在 OVERVIEW/RACE 表格页。");
  }

  fs.mkdirSync(OUT_DIR, { recursive: true });
  const base = path.join(OUT_DIR, `laps-tyres-${stamp(capturedAt)}`);
  fs.writeFileSync(`${base}.txt`, text, "utf8");
  writeRows(base, rows);
  return { rows, filesBase: base };
}

async function main() {
  if (WATCH) {
    const liveBase = path.join(OUT_DIR, "laps-tyres-live");
    const byLapBase = path.join(OUT_DIR, "laps-tyres-by-lap-live");
    const cumulative = fs.existsSync(`${liveBase}.json`)
      ? JSON.parse(fs.readFileSync(`${liveBase}.json`, "utf8"))
      : [];
    const byLapRows = fs.existsSync(`${byLapBase}.json`)
      ? JSON.parse(fs.readFileSync(`${byLapBase}.json`, "utf8"))
      : [];
    const byLap = new Map(byLapRows.map((row) => [`${row.driver_tla}|${row.race_lap}`, row]));
    console.log(`开始持续保存圈数/轮胎数据，每 ${INTERVAL_MS}ms 保存一次。输出目录：${OUT_DIR}`);
    while (true) {
      try {
        const result = await captureOnce();
        cumulative.push(...result.rows);
        for (const row of result.rows) {
          byLap.set(`${row.driver_tla}|${row.race_lap}`, row);
        }
        writeRows(liveBase, cumulative);
        writeRows(
          byLapBase,
          [...byLap.values()].sort((a, b) => {
            if ((a.race_lap ?? 0) !== (b.race_lap ?? 0)) return (a.race_lap ?? 0) - (b.race_lap ?? 0);
            return a.position - b.position;
          }),
        );
        console.log(
          `[${new Date().toLocaleTimeString()}] 保存 ${result.rows.length} 台车：${result.filesBase}`,
        );
      } catch (error) {
        console.error(`[${new Date().toLocaleTimeString()}] 保存失败：${error.message}`);
      }
      await new Promise((resolve) => setTimeout(resolve, INTERVAL_MS));
    }
  }

  const result = await captureOnce();
  console.log(`保存 ${result.rows.length} 台车：${result.filesBase}.json / .csv / .txt`);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
