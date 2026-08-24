import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const webRoot = fileURLToPath(new URL("../", import.meta.url));
const contract = JSON.parse(
  await readFile(join(webRoot, "performance/public-motion-budget.v1.json"), "utf8"),
);
const bundleReport = JSON.parse(
  await readFile(join(webRoot, "test-results/motion-bundle-budget.json"), "utf8"),
);
const outputArgument = process.argv.indexOf("--output");
const outputPath = outputArgument >= 0
  ? join(process.cwd(), process.argv[outputArgument + 1])
  : join(webRoot, "test-results/motion-performance.json");
const port = Number(process.env.MOTION_BENCHMARK_PORT ?? 3417);
const baseURL = `http://127.0.0.1:${port}`;

function fail(message) {
  throw new Error(`public motion benchmark: ${message}`);
}

async function waitForServer() {
  for (let attempt = 0; attempt < 80; attempt += 1) {
    try {
      const response = await fetch(`${baseURL}/en`);
      if (response.ok) return;
    } catch {
      // The production server is still starting.
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  fail("production server did not become ready");
}

const nextBinary = require.resolve("next/dist/bin/next");
const server = spawn(process.execPath, [nextBinary, "start", "--hostname", "127.0.0.1", "--port", String(port)], {
  cwd: webRoot,
  env: { ...process.env, NEXT_TELEMETRY_DISABLED: "1" },
  stdio: ["ignore", "pipe", "pipe"],
});
let serverError = "";
server.stderr.on("data", (chunk) => { serverError += String(chunk); });

const budgets = contract.budgets;
const runtimeChunkNames = bundleReport.optional_motion_chunks.map((chunk) => chunk.split("/").at(-1));
const profiles = [
  { name: "desktop", viewport: { width: 1440, height: 900 }, expectRuntime: true },
  { name: "mobile", viewport: { width: 412, height: 915 }, expectRuntime: true },
  { name: "reduced-motion", viewport: { width: 412, height: 915 }, reducedMotion: "reduce", expectRuntime: false, reason: "reduced-motion" },
  { name: "data-saver", viewport: { width: 412, height: 915 }, saveData: true, expectRuntime: false, reason: "data-saver" },
  { name: "low-power", viewport: { width: 412, height: 915 }, hardwareConcurrency: 2, deviceMemory: 2, expectRuntime: false, reason: "low-power" },
  { name: "no-javascript", viewport: { width: 412, height: 915 }, javaScriptEnabled: false, expectRuntime: false },
];
const profileNames = profiles.map((profile) => profile.name);
if (JSON.stringify(profileNames) !== JSON.stringify(contract.required_profiles)) {
  fail(`profiles ${profileNames.join(", ")} do not match the required contract profiles`);
}

let browser;
const results = [];
try {
  await waitForServer();
  browser = await chromium.launch({ headless: true });
  for (const profile of profiles) {
    const context = await browser.newContext({
      viewport: profile.viewport,
      javaScriptEnabled: profile.javaScriptEnabled ?? true,
      reducedMotion: profile.reducedMotion,
    });
    if (profile.javaScriptEnabled !== false) {
      await context.addInitScript((signals) => {
        Object.defineProperty(navigator, "connection", {
          configurable: true,
          value: { effectiveType: "4g", saveData: signals.saveData },
        });
        Object.defineProperty(navigator, "hardwareConcurrency", {
          configurable: true,
          value: signals.hardwareConcurrency,
        });
        Object.defineProperty(navigator, "deviceMemory", {
          configurable: true,
          value: signals.deviceMemory,
        });
        const supportedEntryTypes = PerformanceObserver.supportedEntryTypes ?? [];
        window.__OPENNOSH_LAB__ = {
          cls: 0,
          inp: 0,
          inpEntries: 0,
          lcp: 0,
          supports: {
            event: supportedEntryTypes.includes("event"),
            layoutShift: supportedEntryTypes.includes("layout-shift"),
            lcp: supportedEntryTypes.includes("largest-contentful-paint"),
          },
        };
        try {
          new PerformanceObserver((list) => {
            for (const entry of list.getEntries()) window.__OPENNOSH_LAB__.lcp = entry.startTime;
          }).observe({ type: "largest-contentful-paint", buffered: true });
          new PerformanceObserver((list) => {
            for (const entry of list.getEntries()) {
              if (!entry.hadRecentInput) window.__OPENNOSH_LAB__.cls += entry.value;
            }
          }).observe({ type: "layout-shift", buffered: true });
          new PerformanceObserver((list) => {
            for (const entry of list.getEntries()) {
              if (entry.interactionId > 0) {
                window.__OPENNOSH_LAB__.inpEntries += 1;
                window.__OPENNOSH_LAB__.inp = Math.max(window.__OPENNOSH_LAB__.inp, entry.duration);
              }
            }
          }).observe({ type: "event", buffered: true, durationThreshold: 16 });
        } catch {
          // Older engines still run the structural and frame gates.
        }
      }, {
        saveData: profile.saveData ?? false,
        hardwareConcurrency: profile.hardwareConcurrency ?? 8,
        deviceMemory: profile.deviceMemory ?? 8,
      });
    }
    const page = await context.newPage();
    const scripts = [];
    page.on("response", (response) => {
      if (response.request().resourceType() === "script") scripts.push(response.url());
    });
    const response = await page.goto(`${baseURL}/en`, { waitUntil: "networkidle" });
    if (!response?.ok()) fail(`${profile.name} returned ${response?.status()}`);
    const heading = page.getByRole("heading", { level: 1 });
    if (!(await heading.isVisible()) || !(await page.getByText("No accepted changes to report yet.").isVisible())) {
      fail(`${profile.name} is missing server-rendered public content`);
    }

    if (profile.javaScriptEnabled === false) {
      const serverMotionState = await page.locator("html").getAttribute("data-motion");
      if (serverMotionState !== "off") fail("the no-JavaScript route did not remain static");
      results.push({ name: profile.name, status: "pass", server_content_complete: true });
      await context.close();
      continue;
    }

    await page.waitForFunction(() => document.documentElement.dataset.motionGate !== undefined);
    if (profile.expectRuntime) {
      await page.waitForFunction(() => document.documentElement.dataset.motionRuntime !== undefined);
    } else {
      const reason = await page.locator("html").getAttribute("data-motion-reason");
      if (reason !== profile.reason) fail(`${profile.name} resolved to ${reason}`);
      await page.waitForTimeout(1_300);
    }
    const loadedOptionalRuntime = scripts.some((url) =>
      runtimeChunkNames.some((chunk) => chunk && url.includes(chunk)),
    );
    if (loadedOptionalRuntime !== profile.expectRuntime) {
      fail(`${profile.name} optional runtime request did not match its policy`);
    }

    if (!profile.expectRuntime) {
      results.push({
        name: profile.name,
        status: "pass",
        motion_reason: profile.reason,
        optional_runtime_loaded: false,
      });
      await context.close();
      continue;
    }

    const frame = await page.evaluate(async () => {
      const frameTimes = [];
      const longTasks = [];
      const supported = PerformanceObserver.supportedEntryTypes?.includes("longtask") ?? false;
      let previous;
      let observer;
      try {
        if (!supported) throw new Error("long-task observer unavailable");
        observer = new PerformanceObserver((list) => {
          for (const entry of list.getEntries()) longTasks.push(entry.duration);
        });
        observer.observe({ type: "longtask", buffered: false });
      } catch {
        observer = undefined;
      }
      await new Promise((resolve) => {
        const sample = (timestamp) => {
          if (previous !== undefined) frameTimes.push(timestamp - previous);
          previous = timestamp;
          if (frameTimes.length >= 180) resolve();
          else requestAnimationFrame(sample);
        };
        requestAnimationFrame(sample);
      });
      observer?.disconnect();
      frameTimes.sort((left, right) => left - right);
      return {
        p95: frameTimes[Math.ceil(frameTimes.length * 0.95) - 1],
        longestTask: Math.max(0, ...longTasks),
        longTaskObserverSupported: supported,
      };
    });
    if (!frame.longTaskObserverSupported) {
      fail(`${profile.name} cannot enforce the long-task budget in this browser`);
    }
    if (frame.p95 >= budgets.visible_frame_p95_ms_max) {
      fail(`${profile.name} frame p95 ${frame.p95.toFixed(1)}ms exceeds ${budgets.visible_frame_p95_ms_max}ms`);
    }
    if (frame.longestTask > budgets.motion_long_task_ms_max) {
      fail(`${profile.name} long task ${frame.longestTask.toFixed(1)}ms exceeds ${budgets.motion_long_task_ms_max}ms`);
    }

    await page.locator('[data-motion-region="contribute"]').scrollIntoViewIfNeeded();
    await page.waitForTimeout(150);
    const visibility = await page.evaluate(() => ({
      hero: document.querySelector('[data-motion-region="hero"]')?.getAttribute("data-motion-visible"),
      activeRegions: document.querySelectorAll('[data-motion-visible="true"]').length,
    }));
    if (visibility.hero !== "false") fail(`${profile.name} did not pause the offscreen hero`);
    if (visibility.activeRegions > contract.active_motion_regions_max) {
      fail(`${profile.name} activated ${visibility.activeRegions} motion regions`);
    }

    await page.evaluate(() => {
      document.querySelector(".circle-link")?.addEventListener("click", (event) => event.preventDefault(), {
        once: true,
      });
    });
    await page.locator(".circle-link").first().click();
    await page.waitForTimeout(50);

    const vitals = await page.evaluate(() => ({
      cls: window.__OPENNOSH_LAB__?.cls ?? 0,
      inp: window.__OPENNOSH_LAB__?.inp ?? 0,
      inpEntries: window.__OPENNOSH_LAB__?.inpEntries ?? 0,
      lcp: window.__OPENNOSH_LAB__?.lcp ?? 0,
      reported: window.__OPENNOSH_WEB_VITALS__ ?? [],
      supports: window.__OPENNOSH_LAB__?.supports,
      heap: performance.memory?.usedJSHeapSize ?? null,
    }));
    if (!vitals.supports?.lcp || !vitals.supports.layoutShift || !vitals.supports.event) {
      fail(`${profile.name} cannot enforce all Core Web Vitals in this browser`);
    }
    if (vitals.lcp <= 0) {
      fail(`${profile.name} did not produce an LCP measurement`);
    }
    if (vitals.lcp > budgets.field_p75_lcp_ms_max) {
      fail(`${profile.name} LCP ${vitals.lcp.toFixed(1)}ms exceeds ${budgets.field_p75_lcp_ms_max}ms`);
    }
    if (vitals.cls > budgets.field_p75_cls_max) {
      fail(`${profile.name} CLS ${vitals.cls.toFixed(3)} exceeds ${budgets.field_p75_cls_max}`);
    }
    if (vitals.inp > budgets.field_p75_inp_ms_max) {
      fail(`${profile.name} INP ${vitals.inp.toFixed(1)}ms exceeds ${budgets.field_p75_inp_ms_max}ms`);
    }
    results.push({
      name: profile.name,
      status: "pass",
      optional_runtime_loaded: true,
      frame_p95_ms: Number(frame.p95.toFixed(2)),
      longest_motion_task_ms: Number(frame.longestTask.toFixed(2)),
      lcp_ms: Number(vitals.lcp.toFixed(2)),
      cls: Number(vitals.cls.toFixed(4)),
      inp_ms: Number(vitals.inp.toFixed(2)),
      inp_event_count: vitals.inpEntries,
      web_vitals_collected: [...new Set(vitals.reported.map((metric) => metric.name))],
      heap_high_water_bytes: vitals.heap,
      active_regions_after_scroll: visibility.activeRegions,
    });
    await context.close();
  }
} finally {
  await browser?.close();
  server.kill("SIGTERM");
}

if (server.exitCode && server.exitCode !== 0) fail(serverError.trim() || `server exited ${server.exitCode}`);
const report = {
  contract_id: contract.contract_id,
  generated_at: new Date().toISOString(),
  status: "pass",
  profiles: results,
};
await mkdir(dirname(outputPath), { recursive: true });
await writeFile(outputPath, `${JSON.stringify(report, null, 2)}\n`);
process.stdout.write(`public motion benchmark: pass (${results.length}/${profiles.length} profiles)\n`);
