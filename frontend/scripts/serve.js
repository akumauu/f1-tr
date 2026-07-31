import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const dist = path.join(root, "dist");
const port = Number(process.env.PORT || process.argv[2] || 5173);

if (!fs.existsSync(path.join(dist, "index.html"))) {
  console.error("dist/index.html not found. Run npm run build first.");
  process.exit(1);
}

const contentTypes = new Map([
  [".html", "text/html; charset=utf-8"],
  [".css", "text/css; charset=utf-8"],
  [".js", "text/javascript; charset=utf-8"],
  [".json", "application/json; charset=utf-8"],
  [".svg", "image/svg+xml"],
]);

const server = http.createServer((req, res) => {
  const url = new URL(req.url || "/", `http://localhost:${port}`);
  const rawPath = decodeURIComponent(url.pathname === "/" ? "/index.html" : url.pathname);
  const filePath = path.normalize(path.join(dist, rawPath));

  if (!filePath.startsWith(dist)) {
    res.writeHead(403);
    res.end("Forbidden");
    return;
  }

  const requestedFileExists = (
    fs.existsSync(filePath)
    && fs.statSync(filePath).isFile()
  );
  const acceptsHTML = String(req.headers.accept || "").includes("text/html");
  const isPageRoute = !path.extname(rawPath) && acceptsHTML;
  if (!requestedFileExists && !isPageRoute) {
    res.writeHead(404, {
      "Content-Type": "text/plain; charset=utf-8",
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
    });
    res.end(`Not found: ${url.pathname}`);
    return;
  }
  const target = requestedFileExists
    ? filePath
    : path.join(dist, "index.html");

  res.writeHead(200, {
    "Content-Type": contentTypes.get(path.extname(target)) || "application/octet-stream",
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
  });
  fs.createReadStream(target).pipe(res);
});

server.listen(port, () => {
  console.log(`F1 TR frontend running at http://localhost:${port}`);
});
