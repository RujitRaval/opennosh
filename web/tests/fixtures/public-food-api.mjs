import { createServer } from "node:http";
import { readFile } from "node:fs/promises";

const port = Number(process.argv[2] ?? "8001");
const fixture = JSON.parse(
  await readFile(new URL("./contracts/foods/v1-detail-community.json", import.meta.url), "utf8"),
);
const publicFoodFixture = {
  schema_version: "1.0",
  record: fixture,
  release: {
    release_version: "0.52.0.0",
    published_at: "2026-08-25T12:00:00Z",
    state: "verified",
    stale_age_seconds: 0,
  },
  immutable_url: "/api/v1/public/releases/0.52.0.0/foods/community/rajma-masala",
  provenance_url: "/api/v1/public/releases/0.52.0.0/foods/community/rajma-masala/provenance",
};
const commonsFixtures = JSON.parse(
  await readFile(new URL("./contracts/public/commons-states.json", import.meta.url), "utf8"),
);
let commonsState = "unavailable";
const publicMissionsFixture = {
  schema_version: "1.0",
  state: "live",
  reason: null,
  missions: [{
    mission_id: "11111111-1111-4111-8111-111111111111",
    definition_id: "22222222-2222-4222-8222-222222222222",
    definition_version: 3,
    gap_kind: "locale",
    title: "Document Caribbean breakfast staples",
    summary: "Add source-backed records that preserve preparation and locale context.",
    target_pack_id: "caribbean-community",
    target_dataset: "foods",
    acceptance_target: 10,
    acceptance_criteria: "A record must be accepted into the signed target pack with eligible source proof.",
    lifecycle_state: "active",
    progress_state: "partial",
    public_reason: "The current pack has a measurable preparation gap.",
    next_review_at: null,
    accepted_count: 4,
    matched_event_count: 4,
    checkpoint_id: "33333333-3333-4333-8333-333333333333",
    checkpoint_built_at: "2026-09-02T18:00:00Z",
    release_receipt_digest: null,
  }],
};
const publicMissionActivityFixture = {
  schema_version: "1.0",
  state: "live",
  reason: null,
  minimum_cohort: 10,
  regions: [
    { region_code: "JM", level: "country", accepted_count: 14 },
    { region_code: "419", level: "macroregion", accepted_count: 10 },
  ],
};

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
  if (url.pathname === "/api/v1/public/missions") {
    response.setHeader("Cache-Control", "no-store");
    response.end(JSON.stringify(publicMissionsFixture));
    return;
  }
  if (url.pathname === "/api/v1/public/missions/activity") {
    response.setHeader("Cache-Control", "no-store");
    response.end(JSON.stringify(publicMissionActivityFixture));
    return;
  }
  if ([
    "/api/v1/foods/community/rajma-masala",
    "/api/v1/public/foods/community/rajma-masala",
  ].includes(url.pathname)) {
    response.end(JSON.stringify(
      url.pathname.startsWith("/api/v1/public/") ? publicFoodFixture : fixture,
    ));
    return;
  }
  if ([
    "/api/v1/foods/community/missing-food",
    "/api/v1/public/foods/community/missing-food",
  ].includes(url.pathname)) {
    response.statusCode = 404;
    response.end(JSON.stringify({ detail: "Food not found" }));
    return;
  }
  if ([
    "/api/v1/foods/community/unavailable-food",
    "/api/v1/public/foods/community/unavailable-food",
  ].includes(url.pathname)) {
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
