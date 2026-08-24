import { createServer } from "node:http";
import { readFile } from "node:fs/promises";

const port = Number(process.argv[2] ?? "8001");
const fixture = JSON.parse(
  await readFile(new URL("./contracts/foods/v1-detail-community.json", import.meta.url), "utf8"),
);
const commonsFixtures = JSON.parse(
  await readFile(new URL("./contracts/public/commons-states.json", import.meta.url), "utf8"),
);
let commonsState = "unavailable";

const server = createServer((request, response) => {
  const url = new URL(request.url ?? "/", `http://${request.headers.host ?? "127.0.0.1"}`);
  response.setHeader("Content-Type", "application/json");
  if (url.pathname === "/health") {
    response.end(JSON.stringify({ ok: true }));
    return;
  }
  if (request.method === "POST" && url.pathname === "/__visual/commons-state") {
    const requestedState = url.searchParams.get("state");
    if (!requestedState || !(requestedState in commonsFixtures)) {
      response.statusCode = 400;
      response.end(JSON.stringify({ detail: "Unknown visual commons state" }));
      return;
    }
    commonsState = requestedState;
    response.end(JSON.stringify({ state: commonsState }));
    return;
  }
  if (url.pathname === "/api/v1/public/commons-snapshot") {
    response.setHeader("Cache-Control", "no-store");
    response.end(JSON.stringify(commonsFixtures[commonsState]));
    return;
  }
  if (url.pathname === "/api/v1/foods/community/rajma-masala") {
    response.end(JSON.stringify(fixture));
    return;
  }
  if (url.pathname === "/api/v1/foods/community/missing-food") {
    response.statusCode = 404;
    response.end(JSON.stringify({ detail: "Food not found" }));
    return;
  }
  if (url.pathname === "/api/v1/foods/community/unavailable-food") {
    response.statusCode = 503;
    response.end(JSON.stringify({ detail: "Temporarily unavailable" }));
    return;
  }
  response.statusCode = 404;
  response.end(JSON.stringify({ detail: "Not found" }));
});

server.listen(port, "127.0.0.1");

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => server.close(() => process.exit(0)));
}
