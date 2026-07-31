import fs from "node:fs";
import path from "node:path";
import { createHash } from "node:crypto";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const dist = path.join(root, "dist");
const src = path.join(root, "src");
const bundledPublic = path.join(root, "public");
const generatedData = path.resolve(root, "..", "public", "data");

function isInside(parent, candidate) {
  const relative = path.relative(parent, candidate);
  return relative !== "" && !relative.startsWith("..") && !path.isAbsolute(relative);
}

function sha256(file) {
  return createHash("sha256").update(fs.readFileSync(file)).digest("hex");
}

function copyPublishedWorkbenchReports(sourceRoot) {
  const manifestPath = path.join(sourceRoot, "manifest.json");
  if (!fs.existsSync(manifestPath)) return;
  const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
  if (!Array.isArray(manifest.reports)) {
    throw new Error("Telemetry workbench manifest must contain reports");
  }
  const rows = manifest.reports;
  const destinationRoot = path.join(dist, "data", "telemetry-workbench");
  const copied = new Map();

  function copyManifestFile(reference, expectedHash, label) {
    const relativePath = String(reference || "");
    const source = path.resolve(sourceRoot, relativePath);
    if (
      !relativePath
      || !isInside(sourceRoot, source)
      || !fs.existsSync(source)
      || !fs.statSync(source).isFile()
    ) {
      throw new Error(`Invalid telemetry workbench ${label} path: ${reference}`);
    }
    const declaredHash = String(expectedHash || "");
    if (!/^[0-9a-f]{64}$/.test(declaredHash)) {
      throw new Error(`Missing telemetry workbench ${label} SHA-256: ${reference}`);
    }
    const sourceHash = sha256(source);
    if (sourceHash !== declaredHash) {
      throw new Error(
        `Telemetry workbench ${label} SHA-256 mismatch: ${reference}`,
      );
    }
    const destination = path.resolve(destinationRoot, path.relative(sourceRoot, source));
    if (!isInside(destinationRoot, destination)) {
      throw new Error(`Telemetry workbench destination escapes dist: ${reference}`);
    }
    const destinationKey = path.normalize(destination).toLowerCase();
    const previousHash = copied.get(destinationKey);
    if (previousHash && previousHash !== declaredHash) {
      throw new Error(`Conflicting telemetry workbench manifest path: ${reference}`);
    }
    if (previousHash) {
      return;
    }
    fs.mkdirSync(path.dirname(destination), { recursive: true });
    fs.copyFileSync(source, destination);
    if (sha256(destination) !== declaredHash) {
      throw new Error(
        `Copied telemetry workbench ${label} SHA-256 mismatch: ${reference}`,
      );
    }
    copied.set(destinationKey, declaredHash);
  }

  for (const row of rows) {
    copyManifestFile(row.path, row.export_sha256, "report");
    if (row.stint_curve_evidence) {
      copyManifestFile(
        row.stint_curve_evidence.path,
        row.stint_curve_evidence.export_sha256,
        "curve evidence",
      );
    }
  }
}

fs.rmSync(dist, { recursive: true, force: true });
fs.mkdirSync(dist, { recursive: true });
fs.cpSync(src, dist, { recursive: true });

if (fs.existsSync(bundledPublic)) {
  fs.cpSync(bundledPublic, dist, {
    recursive: true,
    filter: (source) => {
      const relative = path.relative(bundledPublic, source);
      return relative !== path.join("data", "race-dossier")
        && !relative.startsWith(`${path.join("data", "race-dossier")}${path.sep}`)
        && relative !== path.join("data", "telemetry-workbench", "reports")
        && !relative.startsWith(`${path.join("data", "telemetry-workbench", "reports")}${path.sep}`)
        && relative !== path.join("data", "telemetry-workbench", "curve-evidence")
        && !relative.startsWith(
          `${path.join("data", "telemetry-workbench", "curve-evidence")}${path.sep}`,
        );
    },
  });
  copyPublishedWorkbenchReports(
    path.join(bundledPublic, "data", "telemetry-workbench"),
  );
}

if (fs.existsSync(path.join(generatedData, "manifest.json"))) {
  fs.rmSync(path.join(dist, "data"), { recursive: true, force: true });
  fs.cpSync(generatedData, path.join(dist, "data"), {
    recursive: true,
    filter: (source) => {
      const relative = path.relative(generatedData, source);
      return relative !== path.join("telemetry-workbench", "reports")
        && !relative.startsWith(
          `${path.join("telemetry-workbench", "reports")}${path.sep}`,
        )
        && relative !== path.join("telemetry-workbench", "curve-evidence")
        && !relative.startsWith(
          `${path.join("telemetry-workbench", "curve-evidence")}${path.sep}`,
        );
    },
  });
  copyPublishedWorkbenchReports(
    path.join(generatedData, "telemetry-workbench"),
  );
}

console.log(`Built ${dist}`);
