import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { metadata } from "@/app/layout";
import Home from "@/app/page";

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

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function dailyFetch(overrides?: (url: string, init?: RequestInit) => Response | undefined) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const override = overrides?.(url, init);
    if (override) return override;
    if (url === "/api/v1/auth/session") return json(user);
    if (url.startsWith("/api/v1/logs/daily-totals?")) return json(emptyTotals);
    if (url.startsWith("/api/v1/logs?")) return json(emptyLog);
    if (url.startsWith("/api/v1/targets/resolve?")) return json({ detail: "Target not found" }, 404);
    throw new Error(`Unexpected request: ${url}`);
  });
}

beforeEach(() => {
  vi.stubGlobal("fetch", dailyFetch());
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  document.cookie = "opennosh_csrf=; Max-Age=0; Path=/";
});

describe("daily nutrition log", () => {
  it("publishes useful page metadata", () => {
    expect(metadata).toMatchObject({
      title: "Daily nutrition log · opennosh",
      description: "Accessible, self-hosted nutrition and strength tracking.",
    });
  });

  it("offers a recoverable sign-in state when there is no session", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({ detail: "Not authenticated" }, 401)));
    render(<Home />);

    expect(await screen.findByRole("heading", { name: /sign in to your log/i })).toBeVisible();
    expect(screen.getByLabelText(/email address/i)).toHaveAttribute("autocomplete", "email");
    expect(screen.getByLabelText(/^password$/i)).toHaveAttribute("autocomplete", "current-password");
    expect(screen.queryByText(/streak|failed|you went over/i)).not.toBeInTheDocument();
  });

  it("lets a first-time user create an account", async () => {
    const fetchMock = dailyFetch((url, init) => {
      if (url === "/api/v1/auth/session") return json({ detail: "Not authenticated" }, 401);
      if (url === "/api/v1/auth/register" && init?.method === "POST") {
        return json({ user, csrf_token: "registration-csrf" }, 201);
      }
      return undefined;
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<Home />);

    fireEvent.click(await screen.findByRole("button", { name: /create an account/i }));
    expect(screen.getByLabelText(/^password$/i)).toHaveAttribute("autocomplete", "new-password");
    fireEvent.change(screen.getByLabelText(/email address/i), { target: { value: user.email } });
    fireEvent.change(screen.getByLabelText(/^password$/i), { target: { value: "a-long-test-password" } });
    fireEvent.click(screen.getByRole("button", { name: /^create account$/i }));

    expect(await screen.findByRole("heading", { name: /nutrition at a glance/i })).toBeVisible();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/auth/register",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("shows neutral totals and an actionable empty state", async () => {
    render(<Home />);

    expect(await screen.findByRole("heading", { name: /nutrition at a glance/i })).toBeVisible();
    expect(screen.getByRole("heading", { name: /nothing logged for this day/i })).toBeVisible();
    expect(screen.getAllByText(/no target set/i)).toHaveLength(4);
    expect(screen.getByRole("button", { name: /add your first food/i })).toBeEnabled();
    expect(screen.queryByText(/bad|failed|over target|under target/i)).not.toBeInTheDocument();
  });

  it("closes the add-food dialog with Escape and restores focus", async () => {
    render(<Home />);

    const addButton = await screen.findByRole("button", { name: /add your first food/i });
    addButton.focus();
    fireEvent.click(addButton);
    const dialog = screen.getByRole("dialog", { name: /find a food/i });
    fireEvent.keyDown(dialog, { key: "Escape" });

    expect(screen.queryByRole("dialog", { name: /find a food/i })).not.toBeInTheDocument();
    expect(addButton).toHaveFocus();
  });

  it("guides whitespace-only food searches without calling the API", async () => {
    const fetchMock = dailyFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(<Home />);

    await screen.findByRole("heading", { name: /nutrition at a glance/i });
    fireEvent.click(screen.getByRole("button", { name: /^add food$/i }));
    fireEvent.change(screen.getByLabelText(/search the food catalogue/i), {
      target: { value: "  " },
    });
    fireEvent.submit(screen.getByRole("search"));

    expect(await screen.findByText(/enter at least two letters or numbers/i)).toBeVisible();
    expect(fetchMock.mock.calls.some(([input]) => String(input).startsWith("/api/v1/foods/search?"))).toBe(false);
  });

  it("searches and adds a food using the session CSRF token", async () => {
    document.cookie = "opennosh_csrf=test-csrf; Path=/";
    let added = false;
    const entry = {
      id: "3fd6633d-c6fa-446d-a0e2-89fc3ef69b9d",
      logged_at: "2026-08-20T16:00:00Z",
      meal_slot: "Lunch",
      food: { source: "usda", source_id: "171077", name: "Chicken breast" },
      quantity: { amount: "150", unit: "g", portion_name: null },
      snapshot: {
        basis: "computed",
        grams: "150.00",
        nutrients: {
          energy_kcal: "248.00",
          protein_g: "46.50",
          carbohydrate_g: "0.00",
          fat_g: "5.40",
        },
      },
    };
    const fetchMock = dailyFetch((url, init) => {
      if (url.startsWith("/api/v1/foods/search?")) {
        return json({
          items: [
            {
              id: "usda:171077",
              source: "usda",
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
        });
      }
      if (url === "/api/v1/logs" && init?.method === "POST") {
        added = true;
        expect(new Headers(init.headers).get("X-CSRF-Token")).toBe("test-csrf");
        return json(entry, 201);
      }
      if (added && url.startsWith("/api/v1/logs/daily-totals?")) {
        return json({ ...emptyTotals, entry_count: 1, grams: "150.00", nutrients: entry.snapshot.nutrients });
      }
      if (added && url.startsWith("/api/v1/logs?")) return json({ ...emptyLog, items: [entry] });
      return undefined;
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<Home />);

    await screen.findByRole("heading", { name: /nutrition at a glance/i });
    fireEvent.click(screen.getByRole("button", { name: /^add food$/i }));
    fireEvent.change(screen.getByLabelText(/search the food catalogue/i), {
      target: { value: "chicken" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^search$/i }));
    fireEvent.click(await screen.findByRole("radio", { name: /chicken breast/i }));
    fireEvent.change(screen.getByLabelText(/amount in grams/i), { target: { value: "150" } });
    fireEvent.click(screen.getByRole("button", { name: /add chicken breast/i }));

    expect(await screen.findByText(/chicken breast was added to the log/i)).toBeVisible();
    const lunch = screen.getByRole("heading", { name: "Lunch" }).closest("section");
    expect(lunch).not.toBeNull();
    expect(within(lunch as HTMLElement).getByText("Chicken breast")).toBeVisible();
    expect(screen.getByText("248 kcal")).toBeVisible();
  });

  it("returns to sign in when food search reports an expired session", async () => {
    vi.stubGlobal(
      "fetch",
      dailyFetch((url) => {
        if (url.startsWith("/api/v1/foods/search?")) {
          return json({ detail: "Session expired" }, 401);
        }
        return undefined;
      }),
    );
    render(<Home />);

    await screen.findByRole("heading", { name: /nutrition at a glance/i });
    fireEvent.click(screen.getByRole("button", { name: /^add food$/i }));
    fireEvent.change(screen.getByLabelText(/search the food catalogue/i), {
      target: { value: "chicken" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^search$/i }));

    const message = await screen.findByText(/your session ended/i);
    expect(screen.getByRole("heading", { name: /sign in to your log/i })).toBeVisible();
    expect(message).toHaveFocus();
  });

  it("shows contributor credit, sends locale, and recovers when adding finds an expired session", async () => {
    const fetchMock = dailyFetch((url, init) => {
      if (url.startsWith("/api/v1/foods/search?")) {
        const search = new URL(url, "http://localhost").searchParams;
        expect(search.get("locale")).toBe(navigator.language);
        return json({
          items: [
            {
              id: "community:dal",
              source: "community",
              source_id: "dal",
              name: "Dal",
              name_local: null,
              category: "Lentils",
              attribution: { license: "CC0-1.0", contributed_by: "Asha" },
            },
          ],
          limit: 12,
          offset: 0,
          has_more: false,
        });
      }
      if (url === "/api/v1/logs" && init?.method === "POST") {
        return json({ detail: "Session expired" }, 401);
      }
      return undefined;
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<Home />);

    await screen.findByRole("heading", { name: /nutrition at a glance/i });
    fireEvent.click(screen.getByRole("button", { name: /^add food$/i }));
    fireEvent.change(screen.getByLabelText(/search the food catalogue/i), {
      target: { value: "dal" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^search$/i }));
    expect(await screen.findByText(/contributed by asha/i)).toBeVisible();
    fireEvent.click(screen.getByRole("radio", { name: /dal/i }));
    fireEvent.change(screen.getByLabelText(/meal name/i), { target: { value: "   " } });
    fireEvent.click(screen.getByRole("button", { name: /add dal/i }));
    expect(await screen.findByText(/enter a meal name/i)).toBeVisible();
    fireEvent.change(screen.getByLabelText(/meal name/i), { target: { value: "Lunch" } });
    fireEvent.click(screen.getByRole("button", { name: /add dal/i }));

    const message = await screen.findByText(/your session ended/i);
    expect(screen.getByRole("heading", { name: /sign in to your log/i })).toBeVisible();
    expect(message).toHaveFocus();
  });

  it("returns to sign in when the API reports an expired session", async () => {
    vi.stubGlobal(
      "fetch",
      dailyFetch((url) => {
        if (url.startsWith("/api/v1/logs?")) return json({ detail: "Session expired" }, 401);
        return undefined;
      }),
    );
    render(<Home />);

    expect(await screen.findByText(/your session ended/i)).toBeVisible();
    expect(screen.getByRole("heading", { name: /sign in to your log/i })).toBeVisible();
  });

  it("lets the user retry a temporary API failure", async () => {
    let failed = false;
    vi.stubGlobal(
      "fetch",
      dailyFetch((url) => {
        if (!failed && url.startsWith("/api/v1/logs?")) {
          failed = true;
          return json({ detail: "Temporary problem" }, 503);
        }
        return undefined;
      }),
    );
    render(<Home />);

    expect(await screen.findByRole("heading", { name: /we couldn’t load this view/i })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: /retry/i }));
    await waitFor(() => expect(screen.getByRole("heading", { name: /nutrition at a glance/i })).toBeVisible());
  });
});
