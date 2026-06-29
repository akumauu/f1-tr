import fs from "node:fs";
import path from "node:path";

const ROOT = process.cwd();
const INTERVAL_MS = Number(process.env.MULTIVIEWER_SPLIT_INTERVAL_MS ?? 15000);
const WATCH = process.argv.includes("--watch");

const TR_FILE = path.join(ROOT, "data", "raw", "multiviewer-ai-radio", "ai-radio-live.json");
const LAPS_FILE = path.join(
  ROOT,
  "data",
  "raw",
  "multiviewer-live-timing",
  "laps-tyres-live.json",
);
const LAPS_BY_LAP_FILE = path.join(
  ROOT,
  "data",
  "raw",
  "multiviewer-live-timing",
  "laps-tyres-by-lap-live.json",
);
const OUT_DIR = path.join(ROOT, "data", "raw", "by-driver");

const DRIVER_NAME_TO_TLA = {
  "Alexander Albon": "ALB",
  "Fernando Alonso": "ALO",
  "Kimi Antonelli": "ANT",
  "Oliver Bearman": "BEA",
  "Valtteri Bottas": "BOT",
  "Gabriel Bortoleto": "BOR",
  "Franco Colapinto": "COL",
  "Pierre Gasly": "GAS",
  "Isack Hadjar": "HAD",
  "Lewis Hamilton": "HAM",
  "Nico Hulkenberg": "HUL",
  "Charles Leclerc": "LEC",
  "Arvid Lindblad": "LIN",
  "Liam Lawson": "LAW",
  "Lando Norris": "NOR",
  "Esteban Ocon": "OCO",
  "Sergio Perez": "PER",
  "Oscar Piastri": "PIA",
  "George Russell": "RUS",
  "Carlos Sainz": "SAI",
  "Lance Stroll": "STR",
  "Max Verstappen": "VER",
};

function readJson(file) {
  if (!fs.existsSync(file)) return [];
  return JSON.parse(fs.readFileSync(file, "utf8"));
}

function csvEscape(value) {
  const text = String(value ?? "");
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

function writeJson(file, rows) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify(rows, null, 2), "utf8");
}

function writeCsv(file, headers, rows) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(
    file,
    [
      headers.join(","),
      ...rows.map((row) => headers.map((header) => csvEscape(row[header])).join(",")),
    ].join("\n"),
    "utf8",
  );
}

function groupBy(rows, getKey) {
  const grouped = new Map();
  for (const row of rows) {
    const key = getKey(row);
    if (!key) continue;
    if (!grouped.has(key)) grouped.set(key, []);
    grouped.get(key).push(row);
  }
  return grouped;
}

function splitOnce() {
  const trRows = readJson(TR_FILE).map((row) => ({
    ...row,
    driver_tla: DRIVER_NAME_TO_TLA[row.driver] ?? null,
  }));
  const lapRows = readJson(LAPS_FILE);
  const byLapRows = readJson(LAPS_BY_LAP_FILE);

  const trByDriver = groupBy(trRows, (row) => row.driver_tla ?? row.driver);
  const lapsByDriver = groupBy(lapRows, (row) => row.driver_tla);
  const byLapByDriver = groupBy(byLapRows, (row) => row.driver_tla);
  const drivers = new Set([...trByDriver.keys(), ...lapsByDriver.keys(), ...byLapByDriver.keys()]);

  for (const driver of drivers) {
    const driverDir = path.join(OUT_DIR, driver);
    const tr = trByDriver.get(driver) ?? [];
    const laps = lapsByDriver.get(driver) ?? [];
    const byLap = byLapByDriver.get(driver) ?? [];

    writeJson(path.join(driverDir, "ai-radio.json"), tr);
    writeCsv(path.join(driverDir, "ai-radio.csv"), ["timestamp_text", "driver", "driver_tla", "message"], tr);

    writeJson(path.join(driverDir, "laps-tyres.json"), laps);
    writeCsv(
      path.join(driverDir, "laps-tyres.csv"),
      [
        "captured_at",
        "race_clock",
        "race_lap",
        "total_laps",
        "position",
        "driver_tla",
        "tyre_compound",
        "tyre_age_laps",
      ],
      laps,
    );

    writeJson(path.join(driverDir, "laps-tyres-by-lap.json"), byLap);
    writeCsv(
      path.join(driverDir, "laps-tyres-by-lap.csv"),
      [
        "captured_at",
        "race_clock",
        "race_lap",
        "total_laps",
        "position",
        "driver_tla",
        "tyre_compound",
        "tyre_age_laps",
      ],
      byLap,
    );
  }

  const summary = [...drivers].sort().map((driver) => ({
    driver_tla: driver,
    ai_radio_rows: trByDriver.get(driver)?.length ?? 0,
    tyre_snapshot_rows: lapsByDriver.get(driver)?.length ?? 0,
    tyre_by_lap_rows: byLapByDriver.get(driver)?.length ?? 0,
  }));
  writeJson(path.join(OUT_DIR, "summary.json"), summary);
  writeCsv(
    path.join(OUT_DIR, "summary.csv"),
    ["driver_tla", "ai_radio_rows", "tyre_snapshot_rows", "tyre_by_lap_rows"],
    summary,
  );
  return summary;
}

async function main() {
  if (WATCH) {
    console.log(`开始按车手分流，每 ${INTERVAL_MS}ms 更新一次。输出目录：${OUT_DIR}`);
    while (true) {
      try {
        const summary = splitOnce();
        console.log(`[${new Date().toLocaleTimeString()}] 已更新 ${summary.length} 位车手`);
      } catch (error) {
        console.error(`[${new Date().toLocaleTimeString()}] 分流失败：${error.message}`);
      }
      await new Promise((resolve) => setTimeout(resolve, INTERVAL_MS));
    }
  }

  const summary = splitOnce();
  console.log(`已按车手分流 ${summary.length} 位车手：${OUT_DIR}`);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
