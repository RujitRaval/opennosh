"use client";

import { useEffect } from "react";
import { useReportWebVitals } from "next/web-vitals";

import { resolvePublicMotionPolicy } from "@/lib/public-motion-policy";

const MOTION_GATE_MARKER = "opennosh:motion-gate:v1";

interface NetworkInformationLike {
  effectiveType?: string;
  saveData?: boolean;
}

interface NavigatorWithDeviceSignals extends Navigator {
  connection?: NetworkInformationLike;
  deviceMemory?: number;
}

interface PerformanceWindow extends Window {
  __OPENNOSH_WEB_VITALS__?: PublicWebVitalSample[];
}

export interface PublicWebVitalSample {
  id: string;
  name: string;
  navigationType: string;
  rating: string;
  value: number;
}

export function PublicPerformanceSignals({
  decorationsEnabled,
}: {
  decorationsEnabled: boolean;
}) {
  useReportWebVitals((metric) => {
    const browserWindow = window as PerformanceWindow;
    const samples = browserWindow.__OPENNOSH_WEB_VITALS__ ?? [];
    const sample: PublicWebVitalSample = {
      id: metric.id,
      name: metric.name,
      navigationType: metric.navigationType,
      rating: metric.rating,
      value: metric.value,
    };
    browserWindow.__OPENNOSH_WEB_VITALS__ = [...samples.slice(-19), sample];
    window.dispatchEvent(new CustomEvent("opennosh:web-vital", { detail: sample }));
  });

  useEffect(() => {
    const root = document.documentElement;
    root.dataset.motionGate = MOTION_GATE_MARKER;
    const browserWindow = window as PerformanceWindow;
    const device = navigator as NavigatorWithDeviceSignals;
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    const decision = resolvePublicMotionPolicy({
      decorationsEnabled,
      reducedMotion: reducedMotion.matches,
      saveData: device.connection?.saveData === true,
      effectiveType: device.connection?.effectiveType,
      hardwareConcurrency: device.hardwareConcurrency,
      deviceMemory: device.deviceMemory,
    });

    root.dataset.motion = decision.mode;
    root.dataset.motionReason = decision.reason;
    root.dataset.motionState = "paused";
    if (!decision.loadOptionalRuntime) return;

    let cancelled = false;
    let disposeRuntime: (() => void) | undefined;
    let timeoutHandle: ReturnType<typeof setTimeout> | undefined;
    let idleHandle: number | undefined;

    const loadRuntime = async () => {
      try {
        const { startPublicMotion } = await import("@/lib/public-motion-runtime");
        if (cancelled) return;
        disposeRuntime = startPublicMotion(root);
      } catch {
        if (cancelled) return;
        root.dataset.motion = "limited";
        root.dataset.motionReason = "runtime-unavailable";
        root.dataset.motionState = "paused";
      }
    };

    if (browserWindow.requestIdleCallback) {
      idleHandle = browserWindow.requestIdleCallback(() => void loadRuntime(), { timeout: 1_200 });
    } else {
      timeoutHandle = setTimeout(() => void loadRuntime(), 1);
    }

    return () => {
      cancelled = true;
      disposeRuntime?.();
      delete root.dataset.motionGate;
      if (idleHandle !== undefined) browserWindow.cancelIdleCallback?.(idleHandle);
      if (timeoutHandle !== undefined) clearTimeout(timeoutHandle);
    };
  }, [decorationsEnabled]);

  return null;
}
