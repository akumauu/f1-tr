import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const dist = path.join(root, "dist");

const required = [
  "index.html",
  "styles.css",
  "app.js",
  "data/manifest.json",
];

for (const rel of required) {
  const file = path.join(dist, rel);
  if (!fs.existsSync(file)) {
    console.error(`Missing build artifact: ${rel}`);
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

console.log("Frontend build contract OK");
