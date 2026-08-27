import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PublicFoodSearch } from "@/components/foods/public-food-search";
import { DailyLogApp } from "@/components/log/daily-log-app";
import { AccountApp } from "@/components/tracker/account-app";
import { localCalendarDate, OnboardingPanel } from "@/components/tracker/onboarding-panel";
import { RecordsApp } from "@/components/tracker/records-app";
import { RecoveryCodeGate } from "@/components/tracker/recovery-code-gate";
import { RecoverySetupGate } from "@/components/tracker/recovery-setup-gate";
import { TrendsApp } from "@/components/trends/trends-app";
import { sessionState as adaptSessionState } from "@/lib/api/adapters/auth";

const mocks = vi.hoisted(() => ({
  push: vi.fn(),
  sessionState: vi.fn(),
  login: vi.fn(),
  register: vi.fn(),
  recover: vi.fn(),
  logout: vi.fn(),
  replaceTargets: vi.fn(),
  updateAccountSettings: vi.fn(),
  changePassword: vi.fn(),
  rotateRecoveryCode: vi.fn(),
  deleteAccount: vi.fn(),
  createBodyMetric: vi.fn(),
  searchExercises: vi.fn(),
  createWorkout: vi.fn(),
  searchFoods: vi.fn(),
}));

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: mocks.push }) }));
vi.mock("@/lib/api", () => ({ api: mocks }));

const user = {
  id: "4c683fc5-548a-4772-a090-b26ea0951d50",
  email: "launch@example.com",
  onboarding_completed: false,
  recovery_configured: true,
  preferred_units: "us" as const,
};
const completeUser = { ...user, onboarding_completed: true };

afterEach(cleanup);

beforeEach(() => {
  vi.clearAllMocks();
  mocks.sessionState.mockResolvedValue({ authenticated: true, user: completeUser });
  mocks.logout.mockResolvedValue(undefined);
  mocks.replaceTargets.mockResolvedValue({});
  mocks.updateAccountSettings.mockImplementation(async (input) => ({ ...completeUser, ...input }));
  mocks.changePassword.mockResolvedValue(undefined);
  mocks.rotateRecoveryCode.mockResolvedValue({ recovery_code: "replacement-recovery-code" });
  mocks.deleteAccount.mockResolvedValue(undefined);
  mocks.createBodyMetric.mockResolvedValue({});
  mocks.searchExercises.mockResolvedValue({
    items: [{ id: "exercise:squat", name: "Back squat", attribution: { attribution_text: "Open exercise data" } }],
  });
  mocks.createWorkout.mockResolvedValue({});
  mocks.searchFoods.mockResolvedValue({ items: [] });
});

describe("T31 launch readiness", () => {
  it("requires recovery proof, saves both target days, and completes onboarding", async () => {
    const onComplete = vi.fn();
    const onLogout = vi.fn();
    render(<OnboardingPanel user={user} recoveryCode="one-time-code" onComplete={onComplete} onLogout={onLogout} />);

    expect(screen.getByRole("button", { name: "Open my tracker" })).toBeDisabled();
    fireEvent.click(screen.getByRole("radio", { name: /Metric/ }));
    fireEvent.click(screen.getByRole("radio", { name: /US customary/ }));
    fireEvent.click(screen.getByRole("radio", { name: /Metric/ }));
    fireEvent.click(screen.getByLabelText("Set my own nutrition targets now."));
    ["2450", "160", "240", "70", "2000", "150", "200", "65"].forEach((value, index) => {
      fireEvent.change(screen.getAllByRole("spinbutton")[index], { target: { value } });
    });
    fireEvent.click(screen.getByLabelText("I saved this code somewhere private."));
    fireEvent.click(screen.getByRole("button", { name: "Open my tracker" }));

    await waitFor(() => expect(mocks.replaceTargets).toHaveBeenCalledOnce());
    expect(mocks.replaceTargets.mock.calls[0][0].items).toEqual(expect.arrayContaining([
      expect.objectContaining({ day_type: "training", kcal: "2450" }),
      expect.objectContaining({ day_type: "rest" }),
    ]));
    expect(mocks.updateAccountSettings).toHaveBeenCalledWith({ preferred_units: "metric", onboarding_completed: true });
    await waitFor(() => expect(onComplete).toHaveBeenCalledWith(expect.objectContaining({ onboarding_completed: true, preferred_units: "metric" })));
  });

  it("preserves existing targets when setup is skipped", async () => {
    const onComplete = vi.fn();
    render(<OnboardingPanel user={user} onComplete={onComplete} onLogout={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Open my tracker" }));
    await waitFor(() => expect(onComplete).toHaveBeenCalledOnce());
    expect(mocks.replaceTargets).not.toHaveBeenCalled();
  });

  it("surfaces configured-target save errors", async () => {
    mocks.replaceTargets.mockRejectedValueOnce(new Error("Targets are unavailable."));
    render(<OnboardingPanel user={user} onComplete={vi.fn()} onLogout={vi.fn()} />);
    fireEvent.click(screen.getByLabelText("Set my own nutrition targets now."));
    screen.getAllByRole("spinbutton").forEach((input) => fireEvent.change(input, { target: { value: "100" } }));
    fireEvent.click(screen.getByRole("button", { name: "Open my tracker" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Targets are unavailable.");
    expect(screen.getByRole("button", { name: "Open my tracker" })).toBeEnabled();
  });

  it("uses the browser-local calendar day for target activation", () => {
    const browserDate = { getFullYear: () => 2026, getMonth: () => 7, getDate: () => 26 } as Date;
    expect(localCalendarDate(browserDate)).toBe("2026-08-26");
  });

  it("shows real public result provenance, empty results, and failures", async () => {
    mocks.searchFoods.mockResolvedValueOnce({
      items: [{
        id: "community:rajma",
        source: "community",
        source_id: "rajma",
        name: "Rajma masala",
        name_local: "राजमा मसाला",
        category: "Punjabi home-style preparation",
        attribution: { license: "CC0-1.0", pack_id: "indian-staples-north" },
      }],
    });
    const { unmount } = render(<PublicFoodSearch language="en" />);
    fireEvent.change(screen.getByLabelText("Food name"), { target: { value: "rajma" } });
    fireEvent.click(screen.getByRole("button", { name: "Search records" }));
    const link = await screen.findByRole("link", { name: /Rajma masala/ });
    expect(link).toHaveAttribute("href", "/en/explore/foods/community/rajma");
    expect(link).toHaveTextContent("CC0-1.0 · indian-staples-north");

    mocks.searchFoods.mockRejectedValueOnce(new Error("Replacement search is resting."));
    fireEvent.change(screen.getByLabelText("Food name"), { target: { value: "tofu" } });
    fireEvent.submit(screen.getByRole("search"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Replacement search is resting.");
    expect(screen.queryByRole("link", { name: /Rajma masala/ })).not.toBeInTheDocument();
    unmount();

    mocks.searchFoods.mockResolvedValueOnce({ items: [] });
    const empty = render(<PublicFoodSearch language="en" />);
    fireEvent.change(screen.getByLabelText("Food name"), { target: { value: "missing" } });
    fireEvent.submit(screen.getByRole("search"));
    expect(await screen.findByText(/No matching starter record yet/)).toBeVisible();
    empty.unmount();

    mocks.searchFoods.mockRejectedValueOnce(new Error("Search is resting."));
    render(<PublicFoodSearch language="en" />);
    fireEvent.change(screen.getByLabelText("Food name"), { target: { value: "dal" } });
    fireEvent.submit(screen.getByRole("search"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Search is resting.");
  });

  it("records US body measurements and a source-visible strength set", async () => {
    render(<RecordsApp strengthEntryEnabled />);
    expect(await screen.findByRole("heading", { name: "Body and strength, in context." })).toBeVisible();

    fireEvent.change(screen.getByLabelText("Value (lb)"), { target: { value: "176.4" } });
    fireEvent.click(screen.getByRole("button", { name: "Save body record" }));
    await waitFor(() => expect(mocks.createBodyMetric).toHaveBeenCalledWith(expect.objectContaining({ value: "176.4", unit: "lb" })));
    expect(await screen.findByText("Body record saved.")).toBeVisible();

    fireEvent.change(screen.getByLabelText("Measurement"), { target: { value: "body_fat_percentage" } });
    expect(screen.getByLabelText("Value (percent)")).toBeVisible();
    fireEvent.change(screen.getByLabelText("Find exercise"), { target: { value: "squat" } });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    const exercise = await screen.findByLabelText(/Back squat/);
    fireEvent.click(exercise);
    fireEvent.change(screen.getByLabelText("Load (lb)"), { target: { value: "225" } });
    fireEvent.click(screen.getByRole("button", { name: "Save strength set" }));
    await waitFor(() => expect(mocks.createWorkout).toHaveBeenCalledWith(expect.objectContaining({
      sets: [expect.objectContaining({ exercise_id: "exercise:squat", load_value: "225", load_unit: "lb" })],
    })));
    expect(await screen.findByText("Strength set saved.")).toBeVisible();
  });

  it("supports every self-service account lifecycle action", async () => {
    render(<AccountApp />);
    expect(await screen.findByRole("heading", { name: "Your data. Your account." })).toBeVisible();

    fireEvent.change(screen.getByLabelText("Preferred units"), { target: { value: "metric" } });
    fireEvent.click(screen.getByRole("button", { name: "Save units" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Units updated.");

    fireEvent.change(screen.getByLabelText("Current password"), { target: { value: "current-password" } });
    fireEvent.change(screen.getByLabelText("New password"), { target: { value: "replacement-password" } });
    fireEvent.click(screen.getByRole("button", { name: "Change password" }));
    await waitFor(() => expect(mocks.changePassword).toHaveBeenCalledWith("current-password", "replacement-password"));

    fireEvent.change(screen.getByLabelText("Confirm password", { selector: "#recovery-password" }), { target: { value: "current-password" } });
    fireEvent.click(screen.getByRole("button", { name: "Generate new code" }));
    expect(await screen.findByRole("heading", { name: "Save your new recovery code." })).toBeVisible();
    expect(screen.getByRole("status", { name: "Recovery code" })).toHaveTextContent("replacement-recovery-code");
    fireEvent.click(screen.getByLabelText("I saved this code somewhere private."));
    fireEvent.click(screen.getByRole("button", { name: "Continue to my tracker" }));

    fireEvent.click(screen.getByRole("button", { name: "Reopen guided setup" }));
    await waitFor(() => expect(mocks.push).toHaveBeenCalledWith("/tracker"));
  });

  it("changes password errors and permanently deletes confirmed accounts", async () => {
    mocks.changePassword.mockRejectedValueOnce(new Error("Current password is wrong."));
    render(<AccountApp />);
    await screen.findByRole("heading", { name: "Your data. Your account." });
    fireEvent.change(screen.getByLabelText("Current password"), { target: { value: "wrong-password" } });
    fireEvent.change(screen.getByLabelText("New password"), { target: { value: "replacement-password" } });
    fireEvent.click(screen.getByRole("button", { name: "Change password" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Current password is wrong.");

    fireEvent.change(screen.getByLabelText("Type DELETE"), { target: { value: "DELETE" } });
    const danger = screen.getByRole("heading", { name: "Delete account" }).closest("section")!;
    fireEvent.change(within(danger).getByLabelText("Confirm password"), { target: { value: "current-password" } });
    fireEvent.click(screen.getByRole("button", { name: "Delete my account and data" }));
    await waitFor(() => expect(mocks.deleteAccount).toHaveBeenCalledWith("current-password"));
    expect(await screen.findByRole("heading", { name: /Sign in to your log/i })).toBeVisible();
  });

  it("preserves the one-time recovery code when registering from Account", async () => {
    mocks.sessionState.mockResolvedValueOnce({ authenticated: false, user: null });
    mocks.register.mockResolvedValueOnce({ user, recovery_code: "account-entry-recovery-code-1234567890" });
    render(<AccountApp />);

    await screen.findByRole("heading", { name: "Sign in to your log" });
    fireEvent.click(screen.getByRole("button", { name: "New to opennosh? Create an account" }));
    await screen.findByRole("heading", { name: "Create your account" });
    fireEvent.change(screen.getByLabelText("Email address"), { target: { value: user.email } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "a-private-launch-password" } });
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));

    expect(await screen.findByRole("heading", { name: "Save your recovery code" })).toBeVisible();
    expect(screen.getByRole("status", { name: "Recovery code" })).toHaveTextContent("account-entry-recovery-code");
    fireEvent.click(screen.getByLabelText("I saved this code somewhere private."));
    fireEvent.click(screen.getByRole("button", { name: "Open my tracker" }));
    await waitFor(() => expect(mocks.push).toHaveBeenCalledWith("/tracker"));
  });

  it("supports sign-in, sign-out, and recovery from Records without bypassing setup", async () => {
    mocks.sessionState.mockResolvedValueOnce({ authenticated: false, user: null });
    mocks.login.mockResolvedValueOnce({ user: completeUser });
    const first = render(<RecordsApp />);

    await screen.findByRole("heading", { name: "Sign in to your log" });
    fireEvent.change(screen.getByLabelText("Email address"), { target: { value: user.email } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "a-private-launch-password" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("heading", { name: "Body and strength, in context." })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(await screen.findByRole("heading", { name: "Sign in to your log" })).toBeVisible();
    first.unmount();

    mocks.sessionState.mockResolvedValueOnce({ authenticated: false, user: null });
    mocks.recover.mockResolvedValueOnce({ user: completeUser, recovery_code: "rotated-code" });
    render(<RecordsApp />);
    await screen.findByRole("heading", { name: "Sign in to your log" });
    fireEvent.click(screen.getByRole("button", { name: "Forgot your password?" }));
    fireEvent.change(screen.getByLabelText("Email address"), { target: { value: user.email } });
    fireEvent.change(screen.getByLabelText("Recovery code"), { target: { value: "saved-recovery-code-that-is-long-enough" } });
    fireEvent.change(screen.getByLabelText("New password"), { target: { value: "a-new-private-password" } });
    fireEvent.click(screen.getByRole("button", { name: "Reset password" }));
    expect(await screen.findByRole("heading", { name: "Save your new recovery code." })).toBeVisible();
    expect(screen.getByRole("status", { name: "Recovery code" })).toHaveTextContent("rotated-code");
    fireEvent.click(screen.getByLabelText("I saved this code somewhere private."));
    fireEvent.click(screen.getByRole("button", { name: "Continue to my tracker" }));
    expect(await screen.findByRole("heading", { name: "Body and strength, in context." })).toBeVisible();
  });

  it("keeps account-load failures recoverable at sign in", async () => {
    mocks.sessionState.mockRejectedValueOnce(new Error("Account service is resting."));
    render(<AccountApp />);
    expect(await screen.findByText("Account service is resting.")).toHaveAttribute("role", "status");
    expect(screen.getByRole("heading", { name: "Sign in to your log" })).toBeVisible();
  });


  it("supports account sign-in, sign-out, and password recovery", async () => {
    mocks.sessionState.mockResolvedValueOnce({ authenticated: false, user: null });
    mocks.login.mockResolvedValueOnce({ user: completeUser });
    const loginView = render(<AccountApp />);
    await screen.findByRole("heading", { name: "Sign in to your log" });
    fireEvent.change(screen.getByLabelText("Email address"), { target: { value: user.email } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "a-private-launch-password" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("heading", { name: "Your data. Your account." })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(await screen.findByRole("heading", { name: "Sign in to your log" })).toBeVisible();
    loginView.unmount();

    mocks.sessionState.mockResolvedValueOnce({ authenticated: false, user: null });
    mocks.recover.mockResolvedValueOnce({ user: completeUser, recovery_code: "account-rotated-code" });
    render(<AccountApp />);
    await screen.findByRole("heading", { name: "Sign in to your log" });
    fireEvent.click(screen.getByRole("button", { name: "Forgot your password?" }));
    fireEvent.change(screen.getByLabelText("Email address"), { target: { value: user.email } });
    fireEvent.change(screen.getByLabelText("Recovery code"), { target: { value: "saved-recovery-code-that-is-long-enough" } });
    fireEvent.change(screen.getByLabelText("New password"), { target: { value: "a-new-private-password" } });
    fireEvent.click(screen.getByRole("button", { name: "Reset password" }));
    expect(await screen.findByRole("heading", { name: "Save your new recovery code." })).toBeVisible();
    expect(screen.getByRole("status", { name: "Recovery code" })).toHaveTextContent("account-rotated-code");
    fireEvent.click(screen.getByLabelText("I saved this code somewhere private."));
    fireEvent.click(screen.getByRole("button", { name: "Continue to my tracker" }));
    expect(await screen.findByRole("heading", { name: "Your data. Your account." })).toBeVisible();
  });

  it("preserves recovery and finishes setup when registering from Records", async () => {
    mocks.sessionState.mockResolvedValueOnce({ authenticated: false, user: null });
    mocks.register.mockResolvedValueOnce({ user, recovery_code: "records-entry-recovery-code-1234567890" });
    render(<RecordsApp />);
    await screen.findByRole("heading", { name: "Sign in to your log" });
    fireEvent.click(screen.getByRole("button", { name: "New to opennosh? Create an account" }));
    fireEvent.change(screen.getByLabelText("Email address"), { target: { value: user.email } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "a-private-launch-password" } });
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));

    expect(await screen.findByRole("status", { name: "Recovery code" })).toHaveTextContent("records-entry-recovery-code");
    fireEvent.click(screen.getByLabelText("I saved this code somewhere private."));
    fireEvent.click(screen.getByRole("button", { name: "Open my tracker" }));
    expect(await screen.findByRole("heading", { name: "Body and strength, in context." })).toBeVisible();
  });


  it("signs out safely from an incomplete account on Account", async () => {
    mocks.sessionState.mockResolvedValueOnce({ authenticated: true, user });
    render(<AccountApp />);
    expect(await screen.findByRole("heading", { name: "Make the tracker yours." })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(await screen.findByRole("heading", { name: "Sign in to your log" })).toBeVisible();
  });

  it("keeps Records session failures recoverable", async () => {
    mocks.sessionState.mockRejectedValueOnce(new Error("Records service is resting."));
    render(<RecordsApp />);
    expect(await screen.findByText("Records service is resting.")).toHaveAttribute("role", "status");
    expect(screen.getByRole("heading", { name: "Sign in to your log" })).toBeVisible();
  });
  it("forces legacy accounts to create and acknowledge recovery protection", async () => {
    mocks.sessionState.mockResolvedValueOnce({ authenticated: true, user: { ...completeUser, recovery_configured: false } });
    render(<AccountApp />);
    expect(await screen.findByRole("heading", { name: "Add account recovery before continuing." })).toBeVisible();
    fireEvent.change(screen.getByLabelText("Confirm password"), { target: { value: "current-password" } });
    fireEvent.click(screen.getByRole("button", { name: "Create recovery code" }));
    expect(await screen.findByRole("heading", { name: "Save your new recovery code." })).toBeVisible();
    fireEvent.click(screen.getByLabelText("I saved this code somewhere private."));
    fireEvent.click(screen.getByRole("button", { name: "Continue to my tracker" }));
    expect(await screen.findByRole("heading", { name: "Your data. Your account." })).toBeVisible();
  });

  it("gates rotated recovery codes on Daily and Trends entry routes", async () => {
    for (const App of [DailyLogApp, TrendsApp]) {
      mocks.sessionState.mockResolvedValueOnce({ authenticated: false, user: null });
      mocks.recover.mockResolvedValueOnce({ user: completeUser, recovery_code: "route-rotated-code" });
      const view = render(<App />);
      await screen.findByRole("heading", { name: "Sign in to your log" });
      fireEvent.click(screen.getByRole("button", { name: "Forgot your password?" }));
      fireEvent.change(screen.getByLabelText("Email address"), { target: { value: user.email } });
      fireEvent.change(screen.getByLabelText("Recovery code"), { target: { value: "saved-recovery-code-that-is-long-enough" } });
      fireEvent.change(screen.getByLabelText("New password"), { target: { value: "a-new-private-password" } });
      fireEvent.click(screen.getByRole("button", { name: "Reset password" }));
      expect(await screen.findByRole("heading", { name: "Save your new recovery code." })).toBeVisible();
      expect(screen.getByRole("status", { name: "Recovery code" })).toHaveTextContent("route-rotated-code");
      view.unmount();
    }
  });

  it("honestly gates strength entry until attributed exercises are loaded", async () => {
    render(<RecordsApp />);
    expect(await screen.findByRole("heading", { name: "Exercise catalogue is not live yet" })).toBeVisible();
    expect(screen.queryByLabelText("Find exercise")).not.toBeInTheDocument();
  });

  it("surfaces Records and Account action failures without unhandled dead ends", async () => {
    mocks.createBodyMetric.mockRejectedValueOnce(new Error("Body save is resting."));
    mocks.searchExercises.mockRejectedValueOnce(new Error("Exercise search is resting."));
    const records = render(<RecordsApp strengthEntryEnabled />);
    await screen.findByRole("heading", { name: "Body and strength, in context." });
    fireEvent.change(screen.getByLabelText("Value (lb)"), { target: { value: "176" } });
    fireEvent.click(screen.getByRole("button", { name: "Save body record" }));
    expect(await screen.findByText("Body save is resting.")).toHaveAttribute("role", "alert");
    fireEvent.change(screen.getByLabelText("Find exercise"), { target: { value: "squat" } });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    expect(await screen.findByText("Exercise search is resting.")).toHaveAttribute("role", "alert");
    records.unmount();

    mocks.updateAccountSettings.mockRejectedValueOnce(new Error("Units are resting."));
    mocks.rotateRecoveryCode.mockRejectedValueOnce(new Error("Recovery is resting."));
    mocks.deleteAccount.mockRejectedValueOnce(new Error("Deletion is resting."));
    render(<AccountApp />);
    await screen.findByRole("heading", { name: "Your data. Your account." });
    fireEvent.change(screen.getByLabelText("Preferred units"), { target: { value: "metric" } });
    fireEvent.click(screen.getByRole("button", { name: "Save units" }));
    expect(await screen.findByText("Units are resting.")).toHaveAttribute("role", "alert");
    fireEvent.change(screen.getByLabelText("Confirm password", { selector: "#recovery-password" }), { target: { value: "current-password" } });
    fireEvent.click(screen.getByRole("button", { name: "Generate new code" }));
    expect(await screen.findByText("Recovery is resting.")).toHaveAttribute("role", "alert");
    fireEvent.change(screen.getByLabelText("Type DELETE"), { target: { value: "DELETE" } });
    const danger = screen.getByRole("heading", { name: "Delete account" }).closest("section")!;
    fireEvent.change(within(danger).getByLabelText("Confirm password"), { target: { value: "current-password" } });
    fireEvent.click(screen.getByRole("button", { name: "Delete my account and data" }));
    expect(await screen.findByText("Deletion is resting.")).toHaveAttribute("role", "alert");
  });

  it("adapts legacy session payloads with safe recovery rollout defaults", () => {
    expect(adaptSessionState({ id: user.id, email: user.email } as never)).toEqual({
      authenticated: true,
      user: expect.objectContaining({ recovery_configured: true }),
    });
  });

  it("acknowledges rotated codes and recovers from gate sign-out failures", async () => {
    const onSaved = vi.fn();
    const onLogout = vi.fn().mockRejectedValue(new Error("Sign out is resting."));
    render(<RecoveryCodeGate recoveryCode="one-time-rotated-code" onSaved={onSaved} onLogout={onLogout} />);
    fireEvent.click(screen.getByLabelText("I saved this code somewhere private."));
    fireEvent.click(screen.getByRole("button", { name: "Continue to my tracker" }));
    expect(onSaved).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Sign out is resting.");
  });

  it("recovers from recovery-bootstrap generation and sign-out failures", async () => {
    mocks.rotateRecoveryCode.mockRejectedValueOnce(new Error("Code creation is resting."));
    const onLogout = vi.fn().mockRejectedValue(new Error("Bootstrap sign out is resting."));
    render(<RecoverySetupGate onGenerated={vi.fn()} onLogout={onLogout} />);
    fireEvent.change(screen.getByLabelText("Confirm password"), { target: { value: "current-password" } });
    fireEvent.click(screen.getByRole("button", { name: "Create recovery code" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Code creation is resting.");
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Bootstrap sign out is resting.");
  });

});
