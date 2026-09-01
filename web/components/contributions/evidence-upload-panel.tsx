"use client";

import { useCallback, useEffect, useRef, useState, type ChangeEvent } from "react";

import { api } from "@/lib/api";
import {
  uploadEvidenceBytes,
  type EvidenceUploadSessionResponse,
  type RedactionState,
} from "@/lib/api/evidence-upload";
import { contributionCatalog } from "@/lib/contributions/catalog";
import type { InterfaceLanguage } from "@/lib/routes";

const maxBytes = 10_485_760;
const allowedTypes = new Set(["image/jpeg", "image/png", "image/webp"]);
const pollableStates = new Set(["uploaded", "sanitizing", "attached"]);

type Props = {
  enabled: boolean;
  draftId: string;
  sourceDraftVersion: number;
  rightsAcknowledged: boolean;
  language: InterfaceLanguage;
};

type SafeResume = {
  uploadId: string;
  state: EvidenceUploadSessionResponse["state"];
  sourceDescription: string;
  redactionState: RedactionState;
};

function resumeKey(draftId: string): string {
  return `opennosh:contribution:${draftId}:evidence-upload:v1`;
}

function readResume(draftId: string): SafeResume | null {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(resumeKey(draftId)) ?? "null") as Partial<SafeResume> | null;
    if (!parsed?.uploadId || !parsed.state) return null;
    return {
      uploadId: parsed.uploadId,
      state: parsed.state,
      sourceDescription: parsed.sourceDescription ?? "",
      redactionState: parsed.redactionState ?? "not_required",
    };
  } catch {
    return null;
  }
}

export function EvidenceUploadPanel({
  enabled,
  draftId,
  sourceDraftVersion,
  rightsAcknowledged,
  language,
}: Props) {
  const copy = contributionCatalog(language).evidenceUpload;
  const [initialResume] = useState<SafeResume | null>(() =>
    typeof window === "undefined" || !enabled || draftId === "local" ? null : readResume(draftId),
  );
  const [file, setFile] = useState<File | null>(null);
  const [session, setSession] = useState<EvidenceUploadSessionResponse | null>(null);
  const [sourceDescription, setSourceDescription] = useState(initialResume?.sourceDescription ?? "");
  const [redactionState, setRedactionState] = useState<RedactionState>(initialResume?.redactionState ?? "not_required");
  const [message, setMessage] = useState("");
  const [preservationFailure, setPreservationFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const polling = useRef<AbortController | null>(null);
  const resumed = useRef(false);
  const resumeEnabled = useRef(true);

  const persist = useCallback((next: EvidenceUploadSessionResponse | SafeResume) => {
    if (!resumeEnabled.current) return;
    const safe: SafeResume = {
      uploadId: "upload_id" in next ? next.upload_id : next.uploadId,
      state: next.state,
      sourceDescription,
      redactionState,
    };
    try { window.localStorage.setItem(resumeKey(draftId), JSON.stringify(safe)); } catch { /* optional resume only */ }
  }, [draftId, redactionState, sourceDescription]);

  const refresh = useCallback(async (uploadId: string, signal?: AbortSignal) => {
    const next = await api.evidenceUpload(draftId, uploadId, signal);
    if (next.state === "attached" && next.evidence_id) {
      const evidence = await api.contributionEvidence(draftId, signal);
      setPreservationFailure(
        evidence.evidence_id === next.evidence_id && evidence.preservation_failed
          ? evidence.preservation_failure_code ?? "preservation_failed"
          : null,
      );
    } else {
      setPreservationFailure(null);
    }
    setSession(next);
    persist(next);
    return next;
  }, [draftId, persist]);

  useEffect(() => {
    if (!enabled || draftId === "local") return;
    if (resumed.current) return;
    resumed.current = true;
    const saved = initialResume;
    if (!saved) return;
    queueMicrotask(() => {
      void refresh(saved.uploadId).catch(() => {
        window.localStorage.removeItem(resumeKey(draftId));
        setMessage(copy.resumeUnavailable);
      });
    });
  }, [copy.resumeUnavailable, draftId, enabled, initialResume, refresh]);

  useEffect(() => {
    if (!session || preservationFailure || !pollableStates.has(session.state)) return;
    polling.current?.abort();
    const controller = new AbortController();
    polling.current = controller;
    const timer = window.setInterval(() => {
      void refresh(session.upload_id, controller.signal).then((next) => {
        if (!pollableStates.has(next.state)) window.clearInterval(timer);
      }).catch(() => { /* the explicit retry remains available */ });
    }, 2_000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [preservationFailure, refresh, session]);

  useEffect(() => {
    if (session) persist(session);
  }, [persist, session]);

  function chooseFile(event: ChangeEvent<HTMLInputElement>) {
    const next = event.target.files?.[0] ?? null;
    setMessage("");
    if (!next) return setFile(null);
    if (!allowedTypes.has(next.type)) {
      event.target.value = "";
      setFile(null);
      return setMessage(copy.invalidType);
    }
    if (next.size < 1 || next.size > maxBytes) {
      event.target.value = "";
      setFile(null);
      return setMessage(copy.invalidSize);
    }
    setFile(next);
  }

  async function startUpload() {
    if (!file || draftId === "local" || !rightsAcknowledged) return;
    resumeEnabled.current = true;
    setBusy(true);
    setMessage(copy.uploading);
    try {
      const created = await api.createEvidenceUpload(
        draftId,
        {
          source_draft_version: sourceDraftVersion,
          media_type: file.type as "image/jpeg" | "image/png" | "image/webp",
          byte_length: file.size,
        },
        crypto.randomUUID(),
      );
      if (!created.upload || !created.completion_capability) {
        throw new Error(copy.restartRequired);
      }
      await uploadEvidenceBytes(created.upload, file);
      const completed = await api.completeEvidenceUpload(draftId, created.upload_id, {
        completion_capability: created.completion_capability,
      });
      setFile(null);
      setSession(completed);
      persist(completed);
      setMessage(copy.processing);
    } catch (caught) {
      setMessage(caught instanceof Error ? caught.message : copy.retry);
    } finally {
      setBusy(false);
    }
  }

  async function attach() {
    if (!session || !sourceDescription.trim() || !rightsAcknowledged) return;
    setBusy(true);
    setMessage(copy.attaching);
    try {
      const next = await api.attachEvidenceUpload(draftId, session.upload_id, {
        source_draft_version: sourceDraftVersion,
        source_description: sourceDescription.trim(),
        rights_acknowledged: true,
        redaction_state: redactionState,
      });
      setPreservationFailure(null);
      setSession(next);
      persist(next);
      setMessage(next.state === "preserved" ? copy.preserved : copy.preservationPending);
    } catch (caught) {
      setMessage(caught instanceof Error ? caught.message : copy.retry);
    } finally {
      setBusy(false);
    }
  }

  if (!enabled) return null;
  if (draftId === "local") {
    return <section className="evidence-upload-panel" aria-labelledby="evidence-upload-title">
      <p className="mono">{copy.privateLabel}</p>
      <h2 id="evidence-upload-title">{copy.title}</h2>
      <p>{copy.remoteRequired}</p>
      <p>{copy.publicFallback}</p>
    </section>;
  }

  return <section className="evidence-upload-panel" aria-labelledby="evidence-upload-title">
    <p className="mono">{copy.privateLabel}</p>
    <h2 id="evidence-upload-title">{copy.title}</h2>
    <p>{copy.body}</p>
    <label className="evidence-file">
      <span>{copy.choose}</span>
      <input type="file" accept="image/jpeg,image/png,image/webp" capture="environment" onChange={chooseFile} disabled={busy} />
    </label>
    {file ? <p className="mono">{copy.selected} · {(file.size / 1_048_576).toFixed(1)} MB</p> : null}
    <button className="contribution-secondary" type="button" onClick={() => void startUpload()} disabled={!file || busy || !rightsAcknowledged}>
      {busy ? copy.working : copy.upload}
    </button>
    {!rightsAcknowledged ? <p>{copy.rightsRequired}</p> : null}
    {session ? <div className="evidence-upload-status">
      <p className="mono">{copy.status} · {session.state}</p>
      {session.failure_code ? <p>{copy.failed} ({session.failure_code})</p> : null}
      {preservationFailure ? <p>{copy.preservationFailed} ({preservationFailure})</p> : null}
      {session.state === "sanitized" ? <>
        <label>{copy.description}<textarea maxLength={1000} value={sourceDescription} onChange={(event) => setSourceDescription(event.target.value)} /></label>
        <label>{copy.redaction}<select value={redactionState} onChange={(event) => setRedactionState(event.target.value as RedactionState)}>
          <option value="not_required">{copy.redactionNone}</option>
          <option value="applied">{copy.redactionApplied}</option>
          <option value="reviewed">{copy.redactionReviewed}</option>
        </select></label>
        <button className="contribution-primary" type="button" onClick={() => void attach()} disabled={busy || !sourceDescription.trim() || !rightsAcknowledged}>{copy.attach}</button>
      </> : null}
      {(session.state === "failed" || session.state === "expired" || preservationFailure) ? <button className="contribution-secondary" type="button" onClick={() => {
        resumeEnabled.current = false; polling.current?.abort(); window.localStorage.removeItem(resumeKey(draftId));
        setSession(null); setPreservationFailure(null); setMessage(copy.restartRequired);
      }}>{copy.startAgain}</button> : null}
      {pollableStates.has(session.state) && !preservationFailure ? <button className="contribution-secondary" type="button" onClick={() => void refresh(session.upload_id)}>{copy.retryStatus}</button> : null}
    </div> : null}
    <p className="evidence-upload-message" aria-live="polite">{message}</p>
    <p className="evidence-upload-safety">{copy.noExtraction}</p>
    <p>{copy.publicFallback}</p>
  </section>;
}
