import { createServer } from "node:http";
import { readFile } from "node:fs/promises";

const files = {
  "/": ["index.html", "text/html; charset=utf-8"],
  "/app.js": ["app.js", "text/javascript; charset=utf-8"],
  "/styles.css": ["styles.css", "text/css; charset=utf-8"],
  "/benchmark.json": ["benchmark.json", "application/json; charset=utf-8"],
  "/sensitivity.json": ["sensitivity.json", "application/json; charset=utf-8"],
  "/validation.json": ["validation.json", "application/json; charset=utf-8"],
};

createServer(async (request, response) => {
  const entry = files[request.url];
  if (!entry || request.method !== "GET") {
    response.writeHead(404).end("Not found");
    return;
  }
  try {
    const body = await readFile(new URL(`./web/${entry[0]}`, import.meta.url));
    response.writeHead(200, { "Content-Type": entry[1] }).end(body);
  } catch {
    response.writeHead(500).end("Preview file unavailable");
  }
}).listen(Number(process.env.PORT || 8000), "127.0.0.1", () => console.log(`Incident Atlas: http://localhost:${process.env.PORT || 8000}`));
