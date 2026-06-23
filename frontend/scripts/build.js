import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const dist = path.join(root, "dist");
const src = path.join(root, "src");
const bundledPublic = path.join(root, "public");
const generatedData = path.resolve(root, "..", "public", "data");

fs.rmSync(dist, { recursive: true, force: true });
fs.mkdirSync(dist, { recursive: true });
fs.cpSync(src, dist, { recursive: true });

if (fs.existsSync(bundledPublic)) {
  fs.cpSync(bundledPublic, dist, { recursive: true });
}

if (fs.existsSync(path.join(generatedData, "manifest.json"))) {
  fs.rmSync(path.join(dist, "data"), { recursive: true, force: true });
  fs.cpSync(generatedData, path.join(dist, "data"), { recursive: true });
}

console.log(`Built ${dist}`);
