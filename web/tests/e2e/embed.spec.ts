import { expect, test } from "@playwright/test";

const webOrigin = `http://127.0.0.1:${process.env.E2E_PORT || "3000"}`;
const apiOrigin = `http://127.0.0.1:${process.env.E2E_API_PORT || "8001"}`;

test("food embed is proof-bearing, keyboard usable, responsive, and tracking-free", async ({
  context,
  page,
}) => {
  const browserRequests: string[] = [];
  page.on("request", (request) => browserRequests.push(new URL(request.url()).origin));
  const navigation = await page.goto("/embed/v1/foods/community/rajma-masala");

  expect(navigation?.status()).toBe(200);
  expect(navigation?.headers()["content-security-policy"]).toContain(
    `frame-ancestors ${apiOrigin}`,
  );
  expect(navigation?.headers()["x-frame-options"]).toBeUndefined();
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  await expect(page.locator("main")).toHaveAttribute("data-embed-state", "verified");
  await expect(page.getByRole("heading", { level: 1, name: "Rajma masala" })).toBeVisible();
  await expect(page.getByText("CC0-1.0")).toBeVisible();
  await expect(page.getByText("Punjab Foods Collective")).toBeVisible();
  await expect(page.getByText("0.52.0.0")).toBeVisible();

  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: /View direct provenance/ })).toBeFocused();
  expect(await context.cookies()).toEqual([]);
  expect(
    await page.evaluate(() => ({ local: localStorage.length, session: sessionStorage.length })),
  ).toEqual({ local: 0, session: 0 });
  expect([...new Set(browserRequests)]).toEqual([webOrigin]);

  for (const width of [280, 320, 768, 1200]) {
    await page.setViewportSize({ width, height: 900 });
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth),
    ).toBeLessThanOrEqual(1);
  }
});

test("provenance embed preserves stale verification and reduced motion", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto(
    "/embed/v1/releases/0.86.0.0/foods/community/rajma-masala/provenance",
  );

  await expect(page.locator("main")).toHaveAttribute("data-embed-state", "stale-verified");
  await expect(page.locator(".embed-state")).toHaveText("Stale, cryptographically verified");
  await expect(page.getByText("Recipe analysis checked against two household preparations")).toBeVisible();
  await expect(page.getByRole("link", { name: /Open the verified provenance document/ })).toHaveAttribute(
    "href",
    "/api/v1/public/releases/0.86.0.0/foods/community/rajma-masala/provenance",
  );
  expect(await page.locator("main").evaluate((element) => getComputedStyle(element).animationName)).toBe(
    "none",
  );
});

test("sandboxed embed posts only the bounded resize contract to its referring parent", async ({
  page,
}) => {
  await page.goto(`${apiOrigin}/embed-host`);
  const frame = page.frameLocator('iframe[title="OpenNosh food"]');
  await expect(frame.getByRole("heading", { level: 1, name: "Rajma masala" })).toBeVisible();
  const output = page.locator("#message-state");
  await expect(output).not.toHaveText("waiting");
  const message = JSON.parse((await output.textContent()) ?? "null");

  expect(message).toEqual({
    schema_version: "1.0",
    type: "opennosh.embed.resize",
    height: expect.any(Number),
  });
  expect(message.height).toBeGreaterThanOrEqual(160);
  expect(message.height).toBeLessThanOrEqual(1200);
});

test("missing or unproved records fail closed", async ({ page }) => {
  const missing = await page.goto("/embed/v1/foods/community/missing-food");
  expect(missing?.status()).toBe(404);
  await expect(page.locator("main")).toHaveAttribute("data-embed-state", "unavailable");

  const unavailable = await page.goto("/embed/v1/foods/community/unavailable-food");
  expect(unavailable?.status()).toBe(503);
  await expect(page.getByRole("heading", { name: "Verified record unavailable" })).toBeVisible();
});
