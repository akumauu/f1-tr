import fs from "node:fs";
import path from "node:path";
import { requireOfficialTeamIdentity } from "../frontend/src/official-team-colours.js";

const ROOT = process.cwd();
const RAW_DIR = path.join(ROOT, "data", "raw");
const TIMING_DIR = path.join(RAW_DIR, "multiviewer-live-timing");
const OUT_DIR = path.join(ROOT, "frontend", "public", "data", "live-review");

const MONTHS = {
  Jan: 0, Feb: 1, Mar: 2, Apr: 3, May: 4, Jun: 5,
  Jul: 6, Aug: 7, Sep: 8, Oct: 9, Nov: 10, Dec: 11,
};
const COMPOUNDS = new Set(["S", "M", "H", "I", "W", "N", "?"]);
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
const DRIVER_TEAMS_2026_AUSTRIA = Object.freeze({
  ALB: "Williams",
  ALO: "Aston Martin",
  ANT: "Mercedes",
  BEA: "Haas F1 Team",
  BOR: "Audi",
  BOT: "Cadillac",
  COL: "Alpine",
  GAS: "Alpine",
  HAD: "Red Bull Racing",
  HAM: "Ferrari",
  HUL: "Audi",
  LAW: "Racing Bulls",
  LEC: "Ferrari",
  LIN: "Racing Bulls",
  NOR: "McLaren",
  OCO: "Haas F1 Team",
  PER: "Cadillac",
  PIA: "McLaren",
  RUS: "Mercedes",
  SAI: "Williams",
  STR: "Aston Martin",
  VER: "Red Bull Racing",
});

function officialDriverIdentity(tla) {
  const teamName = DRIVER_TEAMS_2026_AUSTRIA[tla];
  if (!teamName) {
    throw new Error(`2026 奥地利站缺少车手车队身份：${tla}`);
  }
  const identity = requireOfficialTeamIdentity(2026, teamName);
  return {
    tla,
    team: identity.name,
    team_key: identity.key,
  };
}

function readJson(file) {
  return JSON.parse(fs.readFileSync(file, "utf8"));
}

function parseLocalTimestamp(text) {
  const match = String(text).match(/^(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4}),\s+(\d{2}):(\d{2}):(\d{2})$/);
  if (!match) return null;
  const [, d, mon, y, h, m, s] = match;
  const month = MONTHS[mon];
  if (month == null) return null;
  return Date.UTC(Number(y), month, Number(d), Number(h) - 8, Number(m), Number(s));
}

function linesOf(text) {
  return text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
}

function findNextDriverRow(lines, startIndex, position) {
  const expected = String(position);
  for (let i = startIndex; i < lines.length - 1; i += 1) {
    if (lines[i] === expected && /^[A-Z]{3}$/.test(lines[i + 1])) return i;
  }
  return -1;
}

function valueAfter(block, label) {
  const idx = block.indexOf(label);
  return idx >= 0 ? block[idx + 1] ?? null : null;
}

function parseDriverBlock(block) {
  const position = Number(block[0]);
  const driver = block[1];
  let compoundIndex = -1;
  for (let i = block.length - 2; i >= 0; i -= 1) {
    if (COMPOUNDS.has(block[i]) && /^\d+$/.test(block[i + 1])) {
      compoundIndex = i;
      break;
    }
  }
  const numericSplits = block
    .slice(0, compoundIndex >= 0 ? compoundIndex : block.length)
    .filter((value) => /^\d{1,2}\.\d{3}$/.test(value));
  const sectors = numericSplits.slice(-3);
  while (sectors.length < 3) sectors.push(null);
  return {
    position,
    driver,
    last_lap: valueAfter(block, "LAST"),
    best_lap: valueAfter(block, "BEST"),
    s1: sectors[0],
    s2: sectors[1],
    s3: sectors[2],
    tyre_compound: compoundIndex >= 0 ? block[compoundIndex] : null,
    tyre_age_laps: compoundIndex >= 0 ? Number(block[compoundIndex + 1]) : null,
  };
}

function capturedAtFromSnapshotKey(key) {
  const match = String(key).match(/^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})$/);
  if (!match) return null;
  const [, y, mo, d, h, mi, s] = match;
  return new Date(Number(y), Number(mo) - 1, Number(d), Number(h), Number(mi), Number(s)).toISOString();
}

function parseTimingSnapshot(file) {
  const text = fs.readFileSync(file, "utf8");
  const header = text.match(/(?<clock>(?:\d+:)?\d{2}:\d{2})\s+Lap:\s*(?<lap>\d+)\/(?<total>\d+)/);
  if (!header) return null;
  const lines = linesOf(text);
  const raceIndex = lines.indexOf("RACE");
  if (raceIndex < 0) return null;
  const rows = [];
  let cursor = raceIndex + 1;
  for (let position = 1; position <= 30; position += 1) {
    const rowStart = findNextDriverRow(lines, cursor, position);
    if (rowStart < 0) break;
    const nextRow = findNextDriverRow(lines, rowStart + 2, position + 1);
    const rowEnd = nextRow < 0 ? lines.length : nextRow;
    rows.push(parseDriverBlock(lines.slice(rowStart, rowEnd)));
    cursor = rowEnd;
  }
  const base = path.basename(file, ".txt").replace("laps-tyres-", "");
  return {
    file: path.basename(file),
    snapshot_key: base,
    captured_at: capturedAtFromSnapshotKey(base),
    race_clock: header.groups.clock,
    race_lap: Number(header.groups.lap),
    total_laps: Number(header.groups.total),
    rows,
  };
}

function buildSnapshotRows() {
  const files = fs.readdirSync(TIMING_DIR)
    .filter((name) => /^laps-tyres-\d{8}_\d{6}\.txt$/.test(name))
    .sort()
    .map((name) => path.join(TIMING_DIR, name));
  return files.map(parseTimingSnapshot).filter(Boolean);
}

function buildLapRows(snapshots, cleanByLap) {
  const byLapDriver = new Map();
  for (const snapshot of snapshots) {
    for (const row of snapshot.rows) {
      byLapDriver.set(`${snapshot.race_lap}|${row.driver}`, {
        ...row,
        race_lap: snapshot.race_lap,
        total_laps: snapshot.total_laps,
        race_clock: snapshot.race_clock,
        snapshot_key: snapshot.snapshot_key,
      });
    }
  }
  const cleanIndex = new Map(cleanByLap.map((row) => [`${row.race_lap}|${row.driver_tla}`, row]));
  const rows = [];
  for (const [key, parsed] of byLapDriver) {
    const clean = cleanIndex.get(key);
    rows.push({
      ...parsed,
      captured_at: clean?.captured_at ?? null,
      position: clean?.position ?? parsed.position,
      tyre_compound: clean?.tyre_compound ?? parsed.tyre_compound,
      tyre_age_laps: clean?.tyre_age_laps ?? parsed.tyre_age_laps,
    });
  }
  return rows.sort((a, b) => a.race_lap - b.race_lap || a.position - b.position);
}

function buildRadios(snapshotRows) {
  const trRows = readJson(path.join(RAW_DIR, "multiviewer-ai-radio", "ai-radio-live.json"));
  const timeAnchors = snapshotRows
    .map((row) => ({ t: Date.parse(row.captured_at), lap: row.race_lap, clock: row.race_clock }))
    .filter((row) => Number.isFinite(row.t) && Number.isFinite(row.lap))
    .sort((a, b) => a.t - b.t);
  const first = timeAnchors[0]?.t ?? null;
  const last = timeAnchors.at(-1)?.t ?? null;
  return trRows.map((row, index) => {
    const t = parseLocalTimestamp(row.timestamp_text);
    let lap = null;
    let raceClock = null;
    if (t != null && first != null && last != null && t >= first && t <= last) {
      let lo = 0;
      let hi = timeAnchors.length - 1;
      while (lo <= hi) {
        const mid = Math.floor((lo + hi) / 2);
        if (timeAnchors[mid].t <= t) lo = mid + 1;
        else hi = mid - 1;
      }
      const anchor = timeAnchors[Math.max(0, hi)];
      lap = anchor?.lap ?? null;
      raceClock = anchor?.clock ?? null;
    }
    return {
      id: index + 1,
      timestamp_text: row.timestamp_text,
      timestamp_ms: t,
      driver: row.driver,
      driver_tla: DRIVER_NAME_TO_TLA[row.driver] ?? null,
      message: row.message,
      race_lap: lap,
      race_clock: raceClock,
    };
  });
}

function main() {
  const cleanByLap = readJson(path.join(TIMING_DIR, "laps-tyres-by-lap-live-clean.json"));
  const cleanSnapshots = readJson(path.join(TIMING_DIR, "laps-tyres-live-clean.json"));
  const snapshots = buildSnapshotRows();
  const lapRows = buildLapRows(snapshots, cleanByLap);
  const radioAnchors = snapshots.some((row) => row.captured_at) ? snapshots : cleanSnapshots;
  const radios = buildRadios(radioAnchors);
  const drivers = [...new Set([
    ...lapRows.map((row) => row.driver),
    ...radios.map((row) => row.driver_tla).filter(Boolean),
  ])].sort();
  const laps = [...new Set(lapRows.map((row) => row.race_lap))].sort((a, b) => a - b);

  const payload = {
    generated_at: new Date().toISOString(),
    year: 2026,
    title: "2026 Austrian Grand Prix",
    session: "Race",
    quality_note: "圈速/三段来自 MultiViewer Live Timing 文本快照重建；早段缺口通常来自采集启动时间，后段已兼容两段式倒计时。",
    drivers: drivers.map(officialDriverIdentity),
    laps,
    lap_rows: lapRows,
    radios,
    unmapped_radios: radios.filter((row) => row.race_lap == null),
    stats: {
      lap_row_count: lapRows.length,
      radio_count: radios.length,
      mapped_radio_count: radios.filter((row) => row.race_lap != null).length,
      unmapped_radio_count: radios.filter((row) => row.race_lap == null).length,
      lap_min: laps[0] ?? null,
      lap_max: laps.at(-1) ?? null,
    },
  };

  fs.mkdirSync(OUT_DIR, { recursive: true });
  fs.writeFileSync(path.join(OUT_DIR, "timeline.json"), JSON.stringify(payload, null, 2), "utf8");
  console.log(JSON.stringify(payload.stats, null, 2));
}

main();
