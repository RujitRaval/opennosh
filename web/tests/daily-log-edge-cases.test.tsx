import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import Home from "@/app/page";
import type { LogEntry } from "@/lib/types";

const user = { id: "4c683fc5-548a-4772-a090-b26ea0951d50", email: "alex@example.com" };
const emptyLog = {
  day: "2026-08-20",
  timezone: "America/New_York",
  items: [],
  limit: 100,
  offset: 0,
  has_more: false,
};
const emptyTotals = {
  day: "2026-08-20",
  timezone: "America/New_York",
  entry_count: 0,
  grams: "0.00",
  nutrients: {},
};
const chicken: LogEntry = {
  id: "3fd6633d-c6fa-446d-a0e2-89fc3ef69b9d",
  logged_at: "2026-08-20T16:00:00Z",
  meal_slot: "Lunch",
  food: { source: "usda", source_id: "171077", name: "Chicken breast" },
  quantity: { amount: "2", unit: "portion", portion_name: "bowl" },
  snapshot: {
    basis: "computed",
    grams: "150.00",
    nutrients: {
      energy_kcal: "248.00",
      protein_g: "46.50",
      carbohydrate_g: "0.00",
      fat_g: "5.40",
      fiber_g: "1.25",
    },
  },
};
const foodSearch = {
  items: [
    {
      id: "usda:171077",
      source: "usda" as const,
      source_id: "171077",
      name: "Chicken breast",
      name_local: null,
      category: "Poultry",
      attribution: { license: "CC0-1.0", contributed_by: null },
    },
  ],
  limit: 12,
  offset: 0,
  has_more: false,
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

type Override = (url: string, init?: RequestInit) => Response | Promise<Response> | undefined;

function dailyFetch(override?: Override) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const overridden = override?.(url, init);
    if (overridden) return await overridden;
    if (url === "/api/v1/auth/session") return json(user);
    if (url.startsWith("/api/v1/logs/daily-totals?")) return json(emptyTotals);
    if (url.startsWith("/api/v1/logs?")) return json(emptyLog);
    if (url.startsWith("/api/v1/targets/resolve?")) return json({ detail: "Target not found" }, 404);
    throw new Error(`Unexpected request: ${url}`);
  });
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
}

async function openFoodDialog() {
  await screen.findByRole("heading", { name: /nutrition at a glance/i });
  fireEvent.click(screen.getByRole("button", { name: /^add food$/i }));
  return screen.getByRole("dialog", { name: /find a food/i });
}

async function selectChicken() {
  await openFoodDialog();
  fireEvent.change(screen.getByLabelText(/search the food catalogue/i), {
    target: { value: "chicken" },
  });
  fireEvent.click(screen.getByRole("button", { name: /^search$/i }));
  fireEvent.click(await screen.findByRole("radio", { name: /chicken breast/i }));
}

beforeEach(() => {
  vi.stubGlobal("fetch", dailyFetch());
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  document.cookie = "opennosh_csrf=; Max-Age=0; Path=/";
});

describe("daily log recovery and edge cases", () => {
  it("surfaces an unexpected session-check failure and lets sign-in retry", async () => {
    let loginAttempts = 0;
    vi.stubGlobal(
      "fetch",
      dailyFetch((url, init) => {
        if (url === "/api/v1/auth/session") return new Response("gateway failure", { status: 503 });
        if (url === "/api/v1/auth/login" && init?.method === "POST") {
          loginAttempts += 1;
          return loginAttempts === 1
            ? json({ detail: "Sign in is temporarily unavailable" }, 503)
            : json({ user, csrf_token: "csrf" });
        }
        return undefined;
      }),
    );
    render(<Home />);

    expect(await screen.findByText(/could not reach the server/i)).toBeVisible();
    fireEvent.change(screen.getByLabelText(/email address/i), { target: { value: user.email } });
    fireEvent.change(screen.getByLabelText(/^password$/i), { target: { value: "a-long-test-password" } });
    fireEvent.click(screen.getByRole("button", { name: /^sign in$/i }));
    expect(await screen.findByText(/temporarily unavailable/i)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: /^sign in$/i }));
    expect(await screen.findByRole("heading", { name: /nutrition at a glance/i })).toBeVisible();
  });

  it("reloads for every date control and target-type changes and announces truncated days", async () => {
    const fetchMock = dailyFetch((url) => {
      if (url.startsWith("/api/v1/logs?")) return json({ ...emptyLog, has_more: true });
      return undefined;
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<Home />);

    expect(await screen.findByText(/first 100 entries/i)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: /previous day/i }));
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([input]) => String(input).includes("day=2026-08-19"))).toBe(true),
    );
    fireEvent.click(screen.getByRole("button", { name: /next day/i }));
    await waitFor(() =>
      expect(fetchMock.mock.calls.filter(([input]) => String(input).includes("day=2026-08-20")).length).toBeGreaterThan(1),
    );
    fireEvent.change(screen.getByLabelText(/^log date$/i), { target: { value: "2026-08-17" } });
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([input]) => String(input).includes("day=2026-08-17"))).toBe(true),
    );
    fireEvent.click(screen.getByRole("button", { name: /^today$/i }));
    await waitFor(() =>
      expect(fetchMock.mock.calls.filter(([input]) => String(input).includes("day=2026-08-20")).length).toBeGreaterThan(2),
    );
    fireEvent.click(screen.getByRole("radio", { name: /rest day/i }));
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([input]) => String(input).includes("day_type=rest"))).toBe(true),
    );
  });

  it("renders named portions and the full nutrient table", async () => {
    vi.stubGlobal(
      "fetch",
      dailyFetch((url) => {
        if (url.startsWith("/api/v1/logs/daily-totals?")) {
          return json({ ...emptyTotals, entry_count: 1, nutrients: chicken.snapshot.nutrients });
        }
        if (url.startsWith("/api/v1/logs?")) return json({ ...emptyLog, items: [chicken] });
        return undefined;
      }),
    );
    render(<Home />);

    expect(await screen.findByText("2 × bowl")).toBeVisible();
    const table = screen.getByRole("table");
    expect(within(table).getByRole("rowheader", { name: "fiber g" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Nutrients" })).toBeVisible();
  });

  it("ignores a stale day response that finishes after a newer request", async () => {
    const older = deferred<Response>();
    const newer = deferred<Response>();
    const olderEntry = { ...chicken, id: "older", food: { ...chicken.food, name: "Older food" } };
    const newerEntry = { ...chicken, id: "newer", food: { ...chicken.food, name: "Newer food" } };
    vi.stubGlobal(
      "fetch",
      dailyFetch((url) => {
        if (url.startsWith("/api/v1/logs?") && url.includes("day=2026-08-19")) return older.promise;
        if (url.startsWith("/api/v1/logs?") && url.includes("day=2026-08-18")) return newer.promise;
        return undefined;
      }),
    );
    render(<Home />);

    await screen.findByRole("heading", { name: /nutrition at a glance/i });
    fireEvent.click(screen.getByRole("button", { name: /previous day/i }));
    await screen.findByRole("heading", { name: /wednesday, august 19/i });
    fireEvent.click(screen.getByRole("button", { name: /previous day/i }));
    newer.resolve(json({ ...emptyLog, day: "2026-08-18", items: [newerEntry] }));
    expect(await screen.findByText("Newer food")).toBeVisible();
    older.resolve(json({ ...emptyLog, day: "2026-08-19", items: [olderEntry] }));
    await waitFor(() => expect(screen.queryByText("Older food")).not.toBeInTheDocument());
    expect(screen.getByText("Newer food")).toBeVisible();
  });

  it("wraps keyboard focus inside the add-food dialog", async () => {
    render(<Home />);
    const dialog = await openFoodDialog();
    const close = screen.getByRole("button", { name: /close add food dialog/i });
    const lastControl = screen.getByRole("radio", { name: /community/i });

    close.focus();
    fireEvent.keyDown(dialog, { key: "Tab", shiftKey: true });
    expect(lastControl).toHaveFocus();
    fireEvent.keyDown(dialog, { key: "Tab" });
    expect(close).toHaveFocus();
  });

  it("handles zero food results and a later search failure without closing the dialog", async () => {
    let searches = 0;
    vi.stubGlobal(
      "fetch",
      dailyFetch((url) => {
        if (url.startsWith("/api/v1/foods/search?")) {
          searches += 1;
          return searches === 1
            ? json({ ...foodSearch, items: [] })
            : json({ detail: "Food search is temporarily unavailable" }, 503);
        }
        return undefined;
      }),
    );
    render(<Home />);
    await openFoodDialog();

    fireEvent.change(screen.getByLabelText(/search the food catalogue/i), { target: { value: "zz" } });
    fireEvent.click(screen.getByRole("button", { name: /^search$/i }));
    expect(await screen.findByRole("heading", { name: /no matching foods/i })).toBeVisible();
    fireEvent.change(screen.getByLabelText(/search the food catalogue/i), { target: { value: "chicken" } });
    fireEvent.click(screen.getByRole("button", { name: /^search$/i }));
    expect(await screen.findByText(/food search is temporarily unavailable/i)).toBeVisible();
    expect(screen.getByRole("dialog", { name: /find a food/i })).toBeVisible();
  });

  it("recovers from an add failure and prevents duplicate rapid submissions", async () => {
    document.cookie = "opennosh_csrf=test-csrf; Path=/";
    let addAttempts = 0;
    const pendingAdd = deferred<Response>();
    const fetchMock = dailyFetch((url, init) => {
      if (url.startsWith("/api/v1/foods/search?")) return json(foodSearch);
      if (url === "/api/v1/logs" && init?.method === "POST") {
        addAttempts += 1;
        return addAttempts === 1
          ? json({ detail: "The food could not be saved" }, 503)
          : pendingAdd.promise;
      }
      return undefined;
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<Home />);
    await selectChicken();

    const addForm = screen.getByRole("button", { name: /add chicken breast/i }).closest("form");
    expect(addForm).not.toBeNull();
    fireEvent.submit(addForm as HTMLFormElement);
    expect(await screen.findByText(/food could not be saved/i)).toBeVisible();
    fireEvent.submit(addForm as HTMLFormElement);
    fireEvent.submit(addForm as HTMLFormElement);
    expect(addAttempts).toBe(2);
    pendingAdd.resolve(json(chicken, 201));
    expect(await screen.findByText(/chicken breast was added/i)).toBeVisible();
  });

  it("cancels deletion, reports a server error, and recovers from an expired delete session", async () => {
    let deletes = 0;
    vi.stubGlobal(
      "fetch",
      dailyFetch((url, init) => {
        if (url.startsWith("/api/v1/logs?") && init?.method !== "DELETE") {
          return json({ ...emptyLog, items: [chicken] });
        }
        if (url === `/api/v1/logs/${chicken.id}` && init?.method === "DELETE") {
          deletes += 1;
          return deletes === 1
            ? json({ detail: "Delete is temporarily unavailable" }, 503)
            : json({ detail: "Session expired" }, 401);
        }
        return undefined;
      }),
    );
    render(<Home />);

    const deleteButton = await screen.findByRole("button", { name: /delete chicken breast from lunch/i });
    fireEvent.click(deleteButton);
    fireEvent.click(screen.getByRole("button", { name: /^cancel$/i }));
    expect(deletes).toBe(0);
    fireEvent.click(deleteButton);
    fireEvent.click(screen.getByRole("button", { name: /^delete entry$/i }));
    expect(await screen.findByText(/delete is temporarily unavailable/i)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: /^retry$/i }));
    const retryDelete = await screen.findByRole("button", { name: /delete chicken breast from lunch/i });
    fireEvent.click(retryDelete);
    fireEvent.click(screen.getByRole("button", { name: /^delete entry$/i }));
    expect(await screen.findByText(/your session ended/i)).toBeVisible();
    expect(screen.getByRole("heading", { name: /sign in to your log/i })).toBeVisible();
  });

  it("moves focus to Meals after deleting the final entry without depending on animation-frame timing", async () => {
    let deleted = false;
    vi.stubGlobal("requestAnimationFrame", vi.fn(() => 1));
    vi.stubGlobal(
      "fetch",
      dailyFetch((url, init) => {
        if (url === `/api/v1/logs/${chicken.id}` && init?.method === "DELETE") {
          deleted = true;
          return new Response(null, { status: 204 });
        }
        if (url.startsWith("/api/v1/logs?") && init?.method !== "DELETE") {
          return json({ ...emptyLog, items: deleted ? [] : [chicken] });
        }
        return undefined;
      }),
    );
    render(<Home />);

    fireEvent.click(await screen.findByRole("button", { name: /delete chicken breast from lunch/i }));
    fireEvent.click(screen.getByRole("button", { name: /^delete entry$/i }));

    expect(await screen.findByRole("heading", { name: /nothing logged for this day/i })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Meals" })).toHaveFocus();
  });

  it("prevents duplicate deletes and does not reload an old day after navigation", async () => {
    const pendingDelete = deferred<Response>();
    let deletes = 0;
    const fetchMock = dailyFetch((url, init) => {
      if (url.startsWith("/api/v1/logs?") && init?.method !== "DELETE") {
        return json({ ...emptyLog, items: [chicken] });
      }
      if (url === `/api/v1/logs/${chicken.id}` && init?.method === "DELETE") {
        deletes += 1;
        return pendingDelete.promise;
      }
      return undefined;
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<Home />);

    fireEvent.click(await screen.findByRole("button", { name: /delete chicken breast from lunch/i }));
    const confirm = screen.getByRole("button", { name: /^delete entry$/i });
    fireEvent.click(confirm);
    fireEvent.click(confirm);
    expect(deletes).toBe(1);

    fireEvent.click(screen.getByRole("button", { name: /previous day/i }));
    expect(await screen.findByRole("heading", { name: /wednesday, august 19/i })).toBeVisible();
    const originalDayLoadsBeforeDeleteFinishes = fetchMock.mock.calls.filter(([input]) =>
      String(input).startsWith("/api/v1/logs?day=2026-08-20"),
    ).length;
    pendingDelete.resolve(new Response(null, { status: 204 }));

    await waitFor(() => expect(screen.getByRole("heading", { name: /wednesday, august 19/i })).toBeVisible());
    expect(
      fetchMock.mock.calls.filter(([input]) => String(input).startsWith("/api/v1/logs?day=2026-08-20")),
    ).toHaveLength(originalDayLoadsBeforeDeleteFinishes);
  });

  it("signs out successfully and handles both retryable and expired-session failures", async () => {
    let logoutAttempts = 0;
    vi.stubGlobal(
      "fetch",
      dailyFetch((url, init) => {
        if (url === "/api/v1/auth/logout" && init?.method === "POST") {
          logoutAttempts += 1;
          if (logoutAttempts === 1) return json({ detail: "Sign out is temporarily unavailable" }, 503);
          return json({ detail: "Session expired" }, 401);
        }
        return undefined;
      }),
    );
    render(<Home />);

    const signOut = await screen.findByRole("button", { name: /sign out/i });
    await screen.findByRole("heading", { name: /nutrition at a glance/i });
    fireEvent.click(signOut);
    expect(await screen.findByText(/sign out is temporarily unavailable/i)).toBeVisible();
    fireEvent.click(signOut);
    expect(await screen.findByText(/your session ended/i)).toBeVisible();

    cleanup();
    vi.stubGlobal(
      "fetch",
      dailyFetch((url, init) => {
        if (url === "/api/v1/auth/logout" && init?.method === "POST") return new Response(null, { status: 204 });
        return undefined;
      }),
    );
    render(<Home />);
    fireEvent.click(await screen.findByRole("button", { name: /sign out/i }));
    const message = await screen.findByText(/you’re signed out/i);
    expect(message).toHaveFocus();
  });
});
