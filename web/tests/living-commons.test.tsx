import { createHash } from "node:crypto";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PublicImpactSurface, PublicOperationsSurface, PublicReuseSurface } from "@/components/public/living-commons-surfaces";
import { resolvePublicImpact, resolvePublicOperations, resolvePublicReuse } from "@/lib/living-commons";

afterEach(() => cleanup());
const sha = "a".repeat(64);
const declarationId = "00000000-0000-4000-8000-000000000001";

function stable(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(stable);
  if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).sort(([a], [b]) => a.localeCompare(b)).map(([key, item]) => [key, stable(item)]));
  return value;
}

function reuse() {
  return {
    registry: { schema_version: "1.0", declarations: [{ schema_version: "1.0", id: declarationId, organization_name: "Commons Lab", project_name: "Meal Atlas", project_url: "https://example.org/atlas", use_case: "Uses versioned food packs for public-interest research.", region_level: "country", region_code: "US", verification_label: "verified", revision: 2, updated_at: "2026-09-04T12:00:00Z", evidence: { source_url: "https://example.org/proof", observed_at: "2026-09-04T11:00:00Z", content_sha256: sha } }] },
    dependencies: { schema_version: "1.0", dependencies: [{ declaration_id: declarationId, project_label: "Meal Atlas", source_pack_id: "community", source_release_id: "0.93.0.0", source_artifact_digest: sha, dependency_kind: "data", verification_label: "verified", evidence_observed_on: "2026-09-04" }] },
  };
}

function impact() {
  const value = { schema_version: "1.0", state: "live", reason: null, metric_definition_version: "1.0", observed_at: "2026-09-04T12:00:00Z", source_checkpoint_id: "release-0.93.0.0", minimum_cohort: 10, global: { verified_adopters: 12, community_declarations: 3, accepted_contributions: 20, pack_installs: 4, api_reads: 50, artifact_downloads: 7 }, regions: [{ region_code: "US", level: "country", verified_adopters: 10, community_declarations: 2, accepted_contributions: 12 }] };
  return { ...value, digest: createHash("sha256").update(JSON.stringify(stable(value))).digest("hex") };
}

function operations() {
  const ids = ["api", "contributions", "downloads", "evidence-processing", "publication", "reuse-registry", "search", "tracker"];
  return {
    status: { schema_version: "1.0", configuration_digest: sha, components: ids.map((component_id) => ({ component_id, display_name: component_id.replaceAll("-", " "), state: "operational", reason: null, observed_at: "2026-09-04T12:00:00Z", freshness_window_seconds: 300, evidence_digest: sha, affected_versions: [] })) },
    incidents: { schema_version: "1.0", incidents: [] },
  };
}

describe("Living Commons server adapters", () => {
  it("reads all five public endpoints without caching", async () => {
    const fixtures = { ...reuse(), impact: impact(), ...operations() };
    const fetcher = vi.fn<typeof fetch>().mockImplementation((url) => {
      const path = new URL(String(url)).pathname;
      const value = path.endsWith("/reuse/dependencies") ? fixtures.dependencies : path.endsWith("/reuse") ? fixtures.registry : path.endsWith("/impact") ? fixtures.impact : path.endsWith("/status") ? fixtures.status : fixtures.incidents;
      return Promise.resolve(Response.json(value));
    });
    await expect(resolvePublicReuse(fetcher)).resolves.toMatchObject({ state: "available" });
    await expect(resolvePublicImpact(fetcher)).resolves.toMatchObject({ state: "live" });
    await expect(resolvePublicOperations(fetcher)).resolves.toMatchObject({ state: "available" });
    expect(fetcher).toHaveBeenCalledTimes(5);
    expect(fetcher.mock.calls.every(([, init]) => init?.cache === "no-store")).toBe(true);
  });

  it("fails private, malformed, or digest-mismatched payloads closed", async () => {
    const privateOps = operations();
    privateOps.status.components[0]!.display_name = "db.internal token=secret";
    const fetcher = vi.fn<typeof fetch>().mockImplementation((url) => Promise.resolve(Response.json(String(url).endsWith("/incidents") ? privateOps.incidents : privateOps.status)));
    await expect(resolvePublicOperations(fetcher)).resolves.toMatchObject({ state: "unavailable", components: [] });
    await expect(resolvePublicImpact(vi.fn<typeof fetch>().mockResolvedValue(Response.json({ ...impact(), digest: sha })))).resolves.toMatchObject({ state: "unavailable", reason: "proof_unavailable" });
  });
});

describe("Living Commons public surfaces", () => {
  it("renders proof labels and keeps contributor URLs as inert text", async () => {
    const data = reuse();
    const snapshot = await resolvePublicReuse(vi.fn<typeof fetch>().mockImplementation((url) => Promise.resolve(Response.json(String(url).endsWith("dependencies") ? data.dependencies : data.registry))));
    render(<PublicReuseSurface language="en" snapshot={snapshot} />);
    expect(screen.getAllByText("Verified").length).toBeGreaterThan(0);
    expect(screen.getByText(/https:\/\/example\.org\/atlas/)).toBeVisible();
    expect(screen.queryByRole("link", { name: /example\.org/ })).toBeNull();
  });

  it("renders only thresholded broad impact and the fixed status inventory", async () => {
    const impactSnapshot = await resolvePublicImpact(vi.fn<typeof fetch>().mockResolvedValue(Response.json(impact())));
    const ops = operations();
    const operationsSnapshot = await resolvePublicOperations(vi.fn<typeof fetch>().mockImplementation((url) => Promise.resolve(Response.json(String(url).endsWith("incidents") ? ops.incidents : ops.status))));
    const { rerender } = render(<PublicImpactSurface language="en" snapshot={impactSnapshot} />);
    expect(screen.getByText("country / US")).toBeVisible();
    expect(screen.getByText("10 verified adopters")).toBeVisible();
    expect(screen.queryByRole("textbox")).toBeNull();
    rerender(<PublicOperationsSurface language="en" snapshot={operationsSnapshot} />);
    expect(screen.getAllByText("Operational")).toHaveLength(8);
    expect(screen.getByText("No public incidents are recorded.")).toBeVisible();
  });
});
