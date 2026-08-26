import { expect, test } from "@playwright/test";

import { consumeVerifiedReferenceRelease } from "./reference-client";

test("a real signed release reaches the browser and reference client", async ({
  page,
  request,
}) => {
  const response = await page.goto("/en/explore/foods/community/rajma-masala");

  expect(response?.status()).toBe(200);
  await expect(page.getByRole("heading", { level: 1, name: "Rajma masala" })).toBeVisible();
  const trustCard = page.getByRole("complementary", { name: "Published with provenance" });
  await expect(trustCard).toBeVisible();
  await expect(trustCard.getByText("Release version", { exact: true })).toBeVisible();
  await expect(trustCard.getByText("1.0.0.0", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: /See provenance/ })).toHaveAttribute(
    "href",
    "/api/v1/public/releases/1.0.0.0/foods/community/rajma-masala/provenance",
  );

  const reference = await consumeVerifiedReferenceRelease(request);

  expect(reference.latest.record.name).toBe("Rajma masala");
  expect(reference.exact).toEqual(reference.latest);
  expect(reference.manifestKeyId).toBe("acceptance-manifest-v1");
  expect(reference.receiptKeyId).toBe("acceptance-receipt-v1");
  expect(reference.provenance).toContain("Verified evidence");
});
