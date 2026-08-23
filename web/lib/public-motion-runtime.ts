const MOTION_RUNTIME_MARKER = "opennosh:motion-runtime:v1";
const MAX_ACTIVE_REGIONS = 2;
const MAX_LONG_TASK_MS = 50;
const MAX_P95_FRAME_MS = 20;
const FRAME_SAMPLE_SIZE = 180;

interface MotionRegion {
  element: HTMLElement;
  ratio: number;
}

function percentile95(values: readonly number[]) {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((left, right) => left - right);
  return sorted[Math.min(sorted.length - 1, Math.ceil(sorted.length * 0.95) - 1)];
}

export function startPublicMotion(root: HTMLElement) {
  const regions = new Map<HTMLElement, MotionRegion>();
  let frameHandle: number | undefined;
  let previousFrame: number | undefined;
  let frameTimes: number[] = [];
  let disabled = false;

  const pauseAll = () => {
    for (const region of regions.values()) region.element.dataset.motionVisible = "false";
    root.dataset.motionState = "paused";
    if (frameHandle !== undefined) cancelAnimationFrame(frameHandle);
    frameHandle = undefined;
    previousFrame = undefined;
    frameTimes = [];
  };

  const disableDecoration = (reason: string) => {
    if (disabled) return;
    disabled = true;
    pauseAll();
    root.dataset.motion = "limited";
    root.dataset.motionReason = reason;
  };

  const sampleFrame = (timestamp: number) => {
    if (disabled || document.visibilityState === "hidden") return;
    if (previousFrame !== undefined) frameTimes.push(timestamp - previousFrame);
    previousFrame = timestamp;
    if (frameTimes.length >= FRAME_SAMPLE_SIZE) {
      const p95 = percentile95(frameTimes);
      root.dataset.motionFrameP95 = p95.toFixed(1);
      frameHandle = undefined;
      if (p95 >= MAX_P95_FRAME_MS) disableDecoration("frame-budget");
      return;
    }
    frameHandle = requestAnimationFrame(sampleFrame);
  };

  const startFrameSample = () => {
    if (disabled || frameHandle !== undefined) return;
    frameTimes = [];
    previousFrame = undefined;
    frameHandle = requestAnimationFrame(sampleFrame);
  };

  const applyVisibleRegions = () => {
    if (disabled || document.visibilityState === "hidden") {
      pauseAll();
      return;
    }
    const active = [...regions.values()]
      .filter((region) => region.ratio > 0)
      .sort((left, right) => right.ratio - left.ratio)
      .slice(0, MAX_ACTIVE_REGIONS);
    const activeElements = new Set(active.map((region) => region.element));
    for (const region of regions.values()) {
      region.element.dataset.motionVisible = activeElements.has(region.element) ? "true" : "false";
    }
    root.dataset.motionState = active.length > 0 ? "running" : "paused";
    if (active.length > 0) startFrameSample();
  };

  const regionElements = document.querySelectorAll<HTMLElement>("[data-motion-region]");
  for (const element of regionElements) {
    element.dataset.motionVisible = "false";
    regions.set(element, { element, ratio: 0 });
  }

  if (!("IntersectionObserver" in window)) {
    disableDecoration("observer-unavailable");
    return () => pauseAll();
  }

  const intersectionObserver = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        const element = entry.target as HTMLElement;
        const region = regions.get(element);
        if (region) region.ratio = entry.isIntersecting ? entry.intersectionRatio : 0;
      }
      applyVisibleRegions();
    },
    { rootMargin: "12% 0px", threshold: [0, 0.05, 0.25, 0.5, 0.75, 1] },
  );
  for (const element of regionElements) intersectionObserver.observe(element);

  let longTaskObserver: PerformanceObserver | undefined;
  if ("PerformanceObserver" in window) {
    try {
      longTaskObserver = new PerformanceObserver((list) => {
        const longestTask = Math.max(0, ...list.getEntries().map((entry) => entry.duration));
        root.dataset.motionLongestTask = longestTask.toFixed(1);
        if (longestTask > MAX_LONG_TASK_MS) disableDecoration("long-task-budget");
      });
      longTaskObserver.observe({ type: "longtask", buffered: false });
    } catch {
      longTaskObserver = undefined;
    }
  }

  const onVisibilityChange = () => applyVisibleRegions();
  document.addEventListener("visibilitychange", onVisibilityChange);
  root.dataset.motion = "active";
  root.dataset.motionReason = "eligible";
  root.dataset.motionRuntime = MOTION_RUNTIME_MARKER;
  applyVisibleRegions();

  return () => {
    pauseAll();
    intersectionObserver.disconnect();
    longTaskObserver?.disconnect();
    document.removeEventListener("visibilitychange", onVisibilityChange);
    delete root.dataset.motionRuntime;
  };
}
