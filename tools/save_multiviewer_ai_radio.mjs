import fs from "node:fs";
import path from "node:path";

const DEBUG_BASE = process.env.MULTIVIEWER_DEBUG_URL ?? "http://127.0.0.1:9223";
const OUT_DIR =
  process.env.MULTIVIEWER_CAPTURE_DIR ??
  path.join(process.cwd(), "data", "raw", "multiviewer-ai-radio");
const WATCH = process.argv.includes("--watch");
const INTERVAL_MS = Number(process.env.MULTIVIEWER_CAPTURE_INTERVAL_MS ?? 5000);

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

function writeItems(base, items) {
  fs.writeFileSync(`${base}.json`, JSON.stringify(items, null, 2), "utf8");
  fs.writeFileSync(
    `${base}.csv`,
    [
      "timestamp_text,driver,message",
      ...items.map((item) =>
        [item.timestamp_text, item.driver, item.message].map(csvEscape).join(","),
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

function parseAiRadioText(text) {
  const lines = text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);

  const items = [];
  for (let i = 0; i < lines.length - 1; i += 1) {
    const meta = lines[i + 1].match(/^(.+?)\s*[·•]\s*(\d{1,2}\s+\w+\s+\d{4},\s+\d{2}:\d{2}:\d{2})$/);
    if (!meta) continue;

    const message = lines[i];
    if (
      message === "AI radio transcriptions" ||
      message === "powered by recursiveGecko" ||
      message.length < 2
    ) {
      continue;
    }

    items.push({
      message,
      driver: meta[1].trim(),
      timestamp_text: meta[2].trim(),
    });
  }

  const seen = new Set();
  return items.filter((item) => {
    const key = `${item.timestamp_text}|${item.driver}|${item.message}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

async function captureOnce() {
  const pages = await getPages();
  const pageTexts = [];

  for (const page of pages) {
    if (page.type !== "page" || !page.webSocketDebuggerUrl) continue;
    const text = await evalInPage(page.webSocketDebuggerUrl, "document.body.innerText");
    pageTexts.push({ title: page.title, url: page.url, text });
  }

  const candidates = pageTexts.filter((page) => page.text.includes("AI radio transcriptions"));
  const sourcePages = candidates.length > 0 ? candidates : pageTexts;
  const rawText = sourcePages.map((page) => page.text).join("\n\n--- PAGE BREAK ---\n\n");
  const items = parseAiRadioText(rawText);

  fs.mkdirSync(OUT_DIR, { recursive: true });
  const base = path.join(OUT_DIR, `ai-radio-${stamp()}`);
  fs.writeFileSync(`${base}.txt`, rawText, "utf8");
  writeItems(base, items);

  return { count: items.length, filesBase: base, items };
}

async function main() {
  if (WATCH) {
    console.log(`开始持续保存 MultiViewer AI TR，每 ${INTERVAL_MS}ms 保存一次。输出目录：${OUT_DIR}`);
    const cumulative = new Map();
    while (true) {
      try {
        const result = await captureOnce();
        for (const item of result.items) {
          cumulative.set(`${item.timestamp_text}|${item.driver}|${item.message}`, item);
        }
        writeItems(path.join(OUT_DIR, "ai-radio-live"), [...cumulative.values()]);
        console.log(`[${new Date().toLocaleTimeString()}] 保存 ${result.count} 条：${result.filesBase}`);
      } catch (error) {
        console.error(`[${new Date().toLocaleTimeString()}] 保存失败：${error.message}`);
      }
      await new Promise((resolve) => setTimeout(resolve, INTERVAL_MS));
    }
  }

  const result = await captureOnce();
  console.log(`保存 ${result.count} 条：${result.filesBase}.json / .csv / .txt`);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
