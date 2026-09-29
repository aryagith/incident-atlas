import { createServer } from "node:http";
import { execFile } from "node:child_process";
import { existsSync } from "node:fs";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

const localPython = fileURLToPath(new URL(process.platform === "win32" ? "./.venv/Scripts/python.exe" : "./.venv/bin/python", import.meta.url));
const python = process.env.PYTHON || (existsSync(localPython) ? localPython : process.platform === "win32" ? "python" : "python3");
const analyzer = fileURLToPath(new URL("./analyze.py", import.meta.url));
const root = fileURLToPath(new URL(".", import.meta.url));
const json = (response, status, value) => response.writeHead(status, { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" }).end(JSON.stringify(value));

const files = {
  "/": ["index.html", "text/html; charset=utf-8"],
  "/app.js": ["app.js", "text/javascript; charset=utf-8"],
  "/styles.css": ["styles.css", "text/css; charset=utf-8"],
  "/benchmark.json": ["benchmark.json", "application/json; charset=utf-8"],
  "/sensitivity.json": ["sensitivity.json", "application/json; charset=utf-8"],
  "/validation.json": ["validation.json", "application/json; charset=utf-8"],
  "/validation-cases.json": ["validation-cases.json", "application/json; charset=utf-8"],
  "/example.csv": ["example.csv", "text/csv; charset=utf-8"],
};

createServer(async (request, response) => {
  response.setHeader("X-Content-Type-Options", "nosniff");
  if (request.url === "/api/analyze" && request.method === "POST") {
    if (!request.headers["content-type"]?.startsWith("application/json")) return json(response, 415, { error: "Send application/json" });
    const chunks = [];
    let size = 0;
    try {
      for await (const chunk of request) {
        size += chunk.length;
        if (size > 5_000_000) return json(response, 413, { error: "Request exceeds 5 MB" });
        chunks.push(chunk);
      }
    } catch {
      return json(response, 400, { error: "Incomplete upload" });
    }
    const child = execFile(python, [analyzer], { cwd: root, windowsHide: true, timeout: 30_000, maxBuffer: 10_000_000 }, (error, stdout, stderr) => {
      if (error && ![2, 3].includes(Number(error.code))) {
        console.error(stderr || error);
        return json(response, error.killed ? 504 : 500, { error: error.killed ? "Analysis timed out" : "Analysis failed" });
      }
      try {
        json(response, error ? Number(error.code) === 2 ? 400 : 503 : 200, JSON.parse(stdout));
      } catch {
        console.error(stderr || stdout);
        json(response, 500, { error: "Analysis returned an invalid response" });
      }
    });
    child.stdin.on("error", () => {});
    child.stdin.end(Buffer.concat(chunks));
    return;
  }
  const entry = files[request.url];
  if (!entry || request.method !== "GET") {
    response.writeHead(404).end("Not found");
    return;
  }
  try {
    const body = await readFile(new URL(`./web/${entry[0]}`, import.meta.url));
    response.writeHead(200, { "Content-Type": entry[1], "Cache-Control": "no-store" }).end(body);
  } catch {
    response.writeHead(500).end("Preview file unavailable");
  }
}).listen(Number(process.env.PORT || 8000), "127.0.0.1", () => console.log(`Incident Atlas: http://localhost:${process.env.PORT || 8000}`));
