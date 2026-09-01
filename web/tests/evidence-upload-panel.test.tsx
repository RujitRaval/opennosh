import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { EvidenceUploadPanel } from "@/components/contributions/evidence-upload-panel";
import { api } from "@/lib/api";
import { uploadEvidenceBytes } from "@/lib/api/evidence-upload";

vi.mock("@/lib/api", () => ({
  api: {
    createEvidenceUpload: vi.fn(), completeEvidenceUpload: vi.fn(), evidenceUpload: vi.fn(),
    attachEvidenceUpload: vi.fn(), contributionEvidence: vi.fn(),
  },
}));
vi.mock("@/lib/api/evidence-upload", () => ({ uploadEvidenceBytes: vi.fn() }));

const draftId = "018f5316-4f4e-7d79-b9f6-88c11a68a497";
const uploadId = "018f5316-4f4e-7d79-b9f6-88c11a68a498";
const sanitized = {
  upload_id: uploadId, state: "sanitized" as const, source_draft_version: 3,
  media_type: "image/png" as const, declared_byte_length: 8, observed_byte_length: 8,
  observed_sha256: "a".repeat(64), expires_at: "2026-09-01T12:00:00Z",
  uploaded_at: "2026-09-01T11:55:00Z", sanitized_at: "2026-09-01T11:56:00Z",
  attached_at: null, preserved_at: null, evidence_id: null, failure_code: null,
};

const storageValues = new Map<string, string>();
const storage: Storage = {
  get length() { return storageValues.size; },
  clear: () => storageValues.clear(),
  getItem: (key) => storageValues.get(key) ?? null,
  key: (index) => [...storageValues.keys()][index] ?? null,
  removeItem: (key) => { storageValues.delete(key); },
  setItem: (key, value) => { storageValues.set(key, value); },
};
Object.defineProperty(window, "localStorage", { configurable: true, value: storage });

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  vi.clearAllMocks();
});

describe("private evidence upload panel", () => {
  it("rejects unsafe files before contacting the API", () => {
    render(<EvidenceUploadPanel enabled draftId={draftId} sourceDraftVersion={3} rightsAcknowledged language="en" />);
    fireEvent.change(screen.getByLabelText(/take a photo/i), {
      target: { files: [new File(["text"], "notes.txt", { type: "text/plain" })] },
    });
    expect(screen.getByText(/choose a jpeg, png, or webp/i)).toBeVisible();
    expect(api.createEvidenceUpload).not.toHaveBeenCalled();
  });

  it("uploads, attaches the sanitized copy, and persists no capability data", async () => {
    vi.mocked(api.createEvidenceUpload).mockResolvedValue({
      upload_id: uploadId, state: "initiated", expires_at: "2026-09-01T12:00:00Z",
      max_byte_length: 10_485_760, completion_capability: "sensitive-capability".padEnd(43, "x"),
      upload: { method: "PUT", url: "https://uploads.example.test/sensitive-url", headers: { "Content-Type": "image/png" } },
    });
    vi.mocked(api.completeEvidenceUpload).mockResolvedValue(sanitized);
    vi.mocked(api.attachEvidenceUpload).mockResolvedValue({
      ...sanitized, state: "attached", attached_at: "2026-09-01T11:57:00Z", evidence_id: uploadId,
    });
    vi.mocked(uploadEvidenceBytes).mockResolvedValue();
    render(<EvidenceUploadPanel enabled draftId={draftId} sourceDraftVersion={3} rightsAcknowledged language="en" />);
    const file = new File(["evidence"], "private-label.png", { type: "image/png" });

    fireEvent.change(screen.getByLabelText(/take a photo/i), { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: /upload privately/i }));
    await screen.findByText(/evidence status · sanitized/i);
    fireEvent.change(screen.getByLabelText(/describe what this label shows/i), { target: { value: "Front nutrition label" } });
    fireEvent.change(screen.getByLabelText(/personal information redaction/i), { target: { value: "reviewed" } });
    fireEvent.click(screen.getByRole("button", { name: /attach verified copy/i }));

    await waitFor(() => expect(api.attachEvidenceUpload).toHaveBeenCalledWith(draftId, uploadId, {
      source_draft_version: 3, source_description: "Front nutrition label", rights_acknowledged: true, redaction_state: "reviewed",
    }));
    const persisted = [...storageValues.values()].join(" ");
    expect(persisted).not.toContain("sensitive-capability");
    expect(persisted).not.toContain("sensitive-url");
    expect(persisted).not.toContain("private-label.png");
  });

  it("resumes a safe failed status and lets the contributor start again", async () => {
    storage.setItem(`opennosh:contribution:${draftId}:evidence-upload:v1`, JSON.stringify({
      uploadId, state: "uploaded", sourceDescription: "Front panel", redactionState: "reviewed",
    }));
    vi.mocked(api.evidenceUpload).mockResolvedValue({
      ...sanitized, state: "failed", failure_code: "decode_failed",
      sanitized_at: null, observed_sha256: null,
    });

    render(<EvidenceUploadPanel enabled draftId={draftId} sourceDraftVersion={3} rightsAcknowledged language="en" />);
    expect(await screen.findByText(/could not be prepared safely/i)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: /choose another image/i }));

    expect(storage.getItem(`opennosh:contribution:${draftId}:evidence-upload:v1`)).toBeNull();
    expect(screen.getByText(/permission is no longer available/i)).toBeVisible();
  });

  it("stops polling and offers recovery when attached evidence preservation fails", async () => {
    storage.setItem(`opennosh:contribution:${draftId}:evidence-upload:v1`, JSON.stringify({
      uploadId, state: "attached", sourceDescription: "Front panel", redactionState: "reviewed",
    }));
    vi.mocked(api.evidenceUpload).mockResolvedValue({
      ...sanitized, state: "attached", attached_at: "2026-09-01T11:57:00Z", evidence_id: uploadId,
    });
    vi.mocked(api.contributionEvidence).mockResolvedValue({
      evidence_id: uploadId,
      evidence_class: "sanitized_media",
      source_draft_version: 3,
      public_state: null,
      preservation_pending: false,
      preservation_failed: true,
      preservation_failure_code: "storage_unavailable",
    });

    render(<EvidenceUploadPanel enabled draftId={draftId} sourceDraftVersion={3} rightsAcknowledged language="en" />);

    expect(await screen.findByText(/independent preservation failed/i)).toBeVisible();
    expect(screen.queryByRole("button", { name: /check status again/i })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /choose another image/i }));
    expect(storage.getItem(`opennosh:contribution:${draftId}:evidence-upload:v1`)).toBeNull();
  });
});
