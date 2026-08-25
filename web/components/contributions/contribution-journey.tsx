"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";

import { api, ApiError } from "@/lib/api";
import {
  browserAutosaveMetric,
  ContributionAutosave,
  draftFromCapability,
} from "@/lib/contributions/autosave";
import { contributionCatalog, contributionMessage } from "@/lib/contributions/catalog";
import type {
  ContributionFieldName, ContributionFieldPatch, ContributionFields,
  ContributionReceipt, ContributionStage, DuplicateCandidate,
  LocalContributionDraft,
} from "@/lib/contributions/domain";
import {
  contributionDraftStorageKey, localAccessibleStages, localCompletedStages,
  localStageBlockers, newLocalContributionDraft, readLocalContributionDraft,
  serializeLocalContributionDraft,
  serverCandidatesNeedReview,
} from "@/lib/contributions/local-draft";
import {
  contributionStageHref, contributionStageList, contributionStageRegistry,
  isContributionStage,
} from "@/lib/contributions/stage-registry";
import { routes, type InterfaceLanguage } from "@/lib/routes";
import { fallbackLanguage, formatMessage, pseudoLanguage } from "@/lib/i18n/catalog";

type Props = { language: InterfaceLanguage; routeDraftId: string; requestedStage: string };
type AuthMode = "login" | "register";

const stageFields: Record<ContributionStage, (keyof ContributionFields)[]> = {
  evidence: ["evidence_type", "source_uri", "rights_acknowledged"],
  details: ["name", "name_local", "locale", "category", "portion_description", "portion_amount", "portion_unit", "portion_grams", "energy_kcal", "protein_g", "fat_g", "carbohydrate_g", "ingredients"],
  duplicates: ["duplicates_resolved"],
  provenance: ["pack_id", "source_date", "attribution", "source_license"],
  review: ["review_acknowledged"],
};

function Field({ name, label, hint, children }: { name: keyof ContributionFields; label: string; hint?: string; children: ReactNode }) {
  return <div className="contribution-field" data-field={name}>
    <label htmlFor={`contribution-${name}`}>{label}</label>
    {hint ? <p id={`contribution-${name}-hint`}>{hint}</p> : null}
    {children}
  </div>;
}

function Receipt({ receipt, language }: { receipt: ContributionReceipt; language: InterfaceLanguage }) {
  const copy = contributionCatalog(language);
  const due = new Intl.DateTimeFormat(language === pseudoLanguage ? fallbackLanguage : language, { dateStyle: "long", timeStyle: "short" }).format(new Date(receipt.acknowledgementDueAt));
  return <section className="contribution-receipt" aria-labelledby="receipt-title">
    <div className="receipt-mark" aria-hidden="true"><i /><i /></div>
    <p className="mono">{copy.receiptLabel}</p>
    <h1 id="receipt-title">{copy.receiptTitle}</h1>
    <p className="receipt-lead">{copy.receiptLead}</p>
    <dl>
      <div><dt>{copy.submission}</dt><dd>{receipt.submissionId}</dd></div>
      <div><dt>{copy.publicCredit}</dt><dd>{receipt.attribution}</dd></div>
      <div><dt>{copy.acknowledgement}</dt><dd>{due}</dd></div>
    </dl>
    <p>{copy.receiptBody}</p>
    <Link className="contribution-primary" href={receipt.statusHref}>{copy.stableStatus}</Link>
  </section>;
}

function Progress({ language, pathDraftId, draft, stage }: { language: InterfaceLanguage; pathDraftId: string; draft: LocalContributionDraft; stage: ContributionStage }) {
  const [expanded, setExpanded] = useState(false);
  const completed = new Set(localCompletedStages(draft));
  const accessible = new Set(localAccessibleStages(draft));
  const catalog = contributionCatalog(language);
  const stages = contributionStageList();
  return <nav className="contribution-progress" aria-label={catalog.progress}>
    <div className="contribution-progress-compact"><span className="mono">{formatMessage(catalog.stepCount, { step: contributionStageRegistry[stage].order })}</span><button type="button" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>{expanded ? catalog.actions.hideAll : catalog.actions.viewAll}</button></div>
    <ol className={expanded ? "is-expanded" : undefined}>{stages.map((item, index) => {
      const current = item.slug === stage;
      const allowed = accessible.has(item.slug);
      const content = <><span>{String(item.order).padStart(2, "0")}</span><strong>{contributionMessage(language, item.headingKey)}</strong><i aria-hidden="true">{completed.has(item.slug) ? "✓" : current ? "●" : "○"}</i></>;
      return <li key={item.slug} className={current ? "is-current" : undefined}>
        {index === 0 || stages[index - 1]?.chapter !== item.chapter ? <p className="contribution-chapter mono">{catalog.chapters[item.chapter].label}</p> : null}
        {allowed ? <Link href={contributionStageHref(language, pathDraftId, item.slug)} aria-current={current ? "step" : undefined}>{content}</Link> : <span aria-disabled="true">{content}</span>}
      </li>;
    })}</ol>
  </nav>;
}

function allFieldPatches(
  fields: ContributionFields,
  baseFields: ContributionFields,
  baseVersion: number,
): ContributionFieldPatch[] {
  return (Object.entries(fields) as [ContributionFieldName, ContributionFields[keyof ContributionFields]][])
    .filter(([field]) => field !== "duplicates_resolved")
    .map(([field, value]) => ({
      field,
      value,
      baseValue: baseFields[field],
      baseVersion,
    }));
}

export function ContributionJourney({ language, routeDraftId, requestedStage }: Props) {
  const copy = contributionCatalog(language);
  const router = useRouter();
  const [draft, setDraft] = useState<LocalContributionDraft | null>(null);
  const [receipt, setReceipt] = useState<ContributionReceipt | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [checkingDuplicates, setCheckingDuplicates] = useState(false);
  const [authRequired, setAuthRequired] = useState(false);
  const [authMode, setAuthMode] = useState<AuthMode>("login");
  const [mobileActionsVisible, setMobileActionsVisible] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const errorSummary = useRef<HTMLDivElement>(null);
  const stageHeading = useRef<HTMLElement>(null);
  const inlineActions = useRef<HTMLDivElement>(null);
  const hydratedRemoteDraftId = useRef<string | null>(null);
  const autosave = useRef<ContributionAutosave | null>(null);
  const rawStage = isContributionStage(requestedStage) ? requestedStage : "evidence";
  const stage = draft && localAccessibleStages(draft).includes(rawStage)
    ? rawStage : draft ? localAccessibleStages(draft).at(-1) ?? "evidence" : rawStage;

  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      const storageKey = contributionDraftStorageKey(routeDraftId);
      const startAutosave = (nextDraft: LocalContributionDraft) => {
        autosave.current?.dispose();
        autosave.current = new ContributionAutosave(nextDraft, {
          storage: window.localStorage,
          storageKey,
          patch: (draftId, input) => api.patchContributionDraft(draftId, input),
          reload: (draftId, requested) => api.contributionDraft(draftId, requested),
          onDraft: setDraft,
          onMetric: browserAutosaveMetric,
        });
      };
      if (routeDraftId === "local") {
        const stored = readLocalContributionDraft(window.localStorage.getItem(storageKey));
        let nextDraft = stored ?? newLocalContributionDraft();
        try {
          window.localStorage.setItem(storageKey, serializeLocalContributionDraft(nextDraft));
        } catch {
          nextDraft = { ...nextDraft, saveState: "repair_required", repairReason: "storage_failed" };
        }
        startAutosave(nextDraft);
        setDraft(nextDraft);
        return;
      }
      if (hydratedRemoteDraftId.current === routeDraftId) return;
      hydratedRemoteDraftId.current = routeDraftId;
      api.contributionDraft(routeDraftId, requestedStage).then((remote) => {
        const stored = readLocalContributionDraft(
          window.localStorage.getItem(storageKey),
        );
        let nextDraft = draftFromCapability(remote, stored);
        try {
          window.localStorage.setItem(storageKey, serializeLocalContributionDraft(nextDraft));
        } catch {
          nextDraft = { ...nextDraft, saveState: "repair_required", repairReason: "storage_failed" };
        }
        startAutosave(nextDraft);
        setDraft(nextDraft);
        if (remote.resolvedStage !== requestedStage) {
          router.replace(contributionStageHref(language, routeDraftId, remote.resolvedStage));
        }
      }).catch((caught) => {
        hydratedRemoteDraftId.current = null;
        setErrors([caught instanceof Error ? caught.message : copy.draftOpenFallback]);
      });
    });
    return () => cancelAnimationFrame(frame);
  }, [copy.draftOpenFallback, language, requestedStage, routeDraftId, router]);

  useEffect(() => () => autosave.current?.dispose(), []);

  useEffect(() => {
    const storageKey = contributionDraftStorageKey(routeDraftId);
    const resume = () => {
      if (navigator.onLine && document.visibilityState === "visible") {
        void autosave.current?.retry();
      }
    };
    const receive = (event: StorageEvent) => {
      if (event.key !== storageKey) return;
      const external = readLocalContributionDraft(event.newValue);
      if (external) autosave.current?.acceptExternalDraft(external);
    };
    window.addEventListener("online", resume);
    document.addEventListener("visibilitychange", resume);
    window.addEventListener("storage", receive);
    return () => {
      window.removeEventListener("online", resume);
      document.removeEventListener("visibilitychange", resume);
      window.removeEventListener("storage", receive);
    };
  }, [routeDraftId]);

  useEffect(() => {
    if (!draft) return;
    if (requestedStage !== stage) {
      router.replace(contributionStageHref(language, routeDraftId, stage));
    }
  }, [draft, language, requestedStage, routeDraftId, router, stage]);

  useEffect(() => {
    if (!draft || !stageHeading.current || !inlineActions.current) return;
    let headingVisible = true;
    let actionsVisible = false;
    const updateVisibility = () => setMobileActionsVisible(!headingVisible && !actionsVisible);
    const observer = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (entry.target === stageHeading.current) headingVisible = entry.isIntersecting;
        if (entry.target === inlineActions.current) actionsVisible = entry.isIntersecting;
      }
      updateVisibility();
    });
    observer.observe(stageHeading.current);
    observer.observe(inlineActions.current);
    return () => observer.disconnect();
  }, [draft, stage]);

  function update<K extends keyof ContributionFields>(field: K, value: ContributionFields[K]) {
    if (!draft) return;
    const invalidatesDuplicates = field === "name" || field === "locale";
    autosave.current?.edit(
      field,
      value,
      invalidatesDuplicates ? { duplicates_resolved: false } : {},
      invalidatesDuplicates ? { duplicateCandidates: [], duplicateQuery: null } : {},
    );
    setErrors([]);
  }

  function showErrors(messages: string[]) {
    setErrors(messages);
    requestAnimationFrame(() => errorSummary.current?.focus());
  }

  function navigate(target: ContributionStage) {
    void autosave.current?.flush(target);
    router.push(contributionStageHref(language, routeDraftId, target));
    window.scrollTo({ top: 0, behavior: "instant" });
  }

  function continueJourney() {
    if (!draft) return;
    const blockers = localStageBlockers(draft, stage);
    if (blockers.length) return showErrors(blockers.map((item) => {
      const key = item.code === "required" ? item.field : item.code;
      return key && key in copy.validation
        ? copy.validation[key as keyof typeof copy.validation]
        : item.message;
    }));
    const next = contributionStageRegistry[stage].next;
    if (next) navigate(next);
  }

  async function checkDuplicates() {
    if (!draft) return;
    const detailsBlockers = localStageBlockers(draft, "details");
    if (detailsBlockers.length) return showErrors(detailsBlockers.map((item) => {
      const key = item.code === "required" ? item.field : item.code;
      return key && key in copy.validation
        ? copy.validation[key as keyof typeof copy.validation]
        : item.message;
    }));
    setCheckingDuplicates(true);
    try {
      const result = await api.searchFoods(draft.fields.name.trim(), draft.fields.locale.trim());
      const candidates: DuplicateCandidate[] = result.items.map((item) => ({
        source: item.source, sourceId: item.source_id, name: item.name, locale: null,
      }));
      autosave.current?.edit("duplicates_resolved", candidates.length === 0, {}, {
        duplicateCandidates: candidates,
        duplicateQuery: `${draft.fields.name.trim()}|${draft.fields.locale.trim()}`,
      });
      setErrors([]);
    } catch (caught) {
      showErrors([caught instanceof Error ? caught.message : copy.indexFallback]);
    } finally { setCheckingDuplicates(false); }
  }

  async function submit(auth?: { mode: AuthMode; email: string; password: string }) {
    if (!draft) return;
    const blockers = localStageBlockers(draft, "review");
    if (blockers.length) return showErrors(blockers.map((item) => item.message));
    setBusy(true);
    try {
      try { await api.session(); }
      catch (caught) {
        if (!(caught instanceof ApiError) || caught.status !== 401) throw caught;
        if (!auth) { setAuthRequired(true); return; }
        if (auth.mode === "login") await api.login(auth.email, auth.password);
        else await api.register(auth.email, auth.password);
      }
      const syncReady = await autosave.current?.settle(stage);
      if (syncReady === false) {
        showErrors([copy.syncBeforeSubmit]);
        return;
      }
      let remote = routeDraftId === "local"
        ? await api.createContributionDraft({ client_draft_id: draft.clientDraftId })
        : await api.contributionDraft(routeDraftId, stage);
      if (routeDraftId === "local") {
        remote = await api.patchContributionDraft(remote.draftId, {
          expected_draft_version: remote.draftVersion,
          operation_id: crypto.randomUUID(),
          requested_stage: stage,
          patches: allFieldPatches(draft.fields, remote.fields, remote.draftVersion).map((patch) => ({
            field: patch.field,
            value: patch.value,
            base_value: patch.baseValue,
            base_version: patch.baseVersion,
          })),
        });
      }
      if (serverCandidatesNeedReview(draft, remote.duplicateCandidates)) {
        autosave.current?.edit("duplicates_resolved", false, {}, {
          duplicateCandidates: [...remote.duplicateCandidates],
          duplicateQuery: `${draft.fields.name.trim()}|${draft.fields.locale.trim()}`,
        });
        showErrors([
          copy.serverDuplicate,
        ]);
        navigate("duplicates");
        return;
      }
      if (remote.duplicateCandidates.length > 0 && draft.fields.duplicates_resolved) {
        remote = await api.patchContributionDraft(remote.draftId, {
          expected_draft_version: remote.draftVersion, operation_id: crypto.randomUUID(),
          requested_stage: "review",
          patches: [{
            field: "duplicates_resolved",
            value: true,
            base_value: remote.fields.duplicates_resolved,
            base_version: remote.draftVersion,
          }],
        });
      }
      const submitted = await api.submitContributionDraft(remote.draftId, {
        expected_draft_version: remote.draftVersion, idempotency_key: crypto.randomUUID(),
      });
      if (!submitted.receipt) throw new Error(copy.serverReceiptMissing);
      window.localStorage.removeItem(contributionDraftStorageKey(routeDraftId));
      setReceipt(submitted.receipt);
      setAuthRequired(false);
      router.replace(routes.contributionStatus(language, submitted.draftId));
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : copy.handoffFallback;
      showErrors([formatMessage(copy.safeDeviceCopy, { message })]);
    } finally { setBusy(false); }
  }

  if (!draft && errors.length) return <main id="main-content" className="contribution-status-page contribution-status-state" role="alert">
    <p className="mono">{copy.draftUnavailable}</p>
    <h1>{copy.draftOpenTitle}</h1>
    <p>{errors[0]}</p>
    <Link className="contribution-primary" href={routes.contributionStart(language)}>{copy.returnDraft}</Link>
  </main>;
  if (!draft) return <main id="main-content" className="contribution-loading" aria-busy="true">{copy.opening}</main>;
  if (receipt) return <main id="main-content" className="contribution-receipt-page"><Receipt receipt={receipt} language={language} /></main>;

  const stageMeta = contributionStageRegistry[stage];
  const fields = draft.fields;
  const blockerFields = new Set(localStageBlockers(draft, stage).map((item) => item.field));
  const saveLabel = {
    saved_on_device: copy.savedDevice,
    sync_scheduled: copy.saveScheduled,
    syncing: copy.saveSyncing,
    synced: copy.savedServer,
    offline: copy.saveOffline,
    conflict: copy.saveConflict,
    repair_required: draft.repairReason === "storage_failed"
      ? copy.saveStorageFailed
      : copy.saveRepair,
  }[draft.saveState];
  const described = (name: keyof ContributionFields) => errors.length > 0 && blockerFields.has(name) ? { "aria-invalid": true as const } : {};

  function authSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void submit({ mode: authMode, email, password });
  }

  return <main id="main-content" className="contribution-page">
    <Progress language={language} pathDraftId={routeDraftId} draft={draft} stage={stage} />
    <article className="contribution-workspace">
      <header ref={stageHeading} className="contribution-stage-heading">
        <p className="mono">{formatMessage(copy.stageCount, { chapter: copy.chapters[stageMeta.chapter].label, step: String(stageMeta.order).padStart(2, "0") })}</p>
        <h1 id={stageMeta.headingAnchor}>{contributionMessage(language, stageMeta.headingKey)}</h1>
        <p>{contributionMessage(language, stageMeta.descriptionKey)}</p>
        <span className="contribution-save mono" role="status">● {saveLabel}</span>
        {draft.saveState === "offline" ? <button type="button" className="contribution-save-retry" onClick={() => void autosave.current?.retry()}>{copy.retrySync}</button> : null}
      </header>

      {draft.conflictFields.length > 0 ? <section className="contribution-conflicts" aria-labelledby="contribution-conflict-title">
        <h2 id="contribution-conflict-title">{copy.conflictTitle}</h2>
        <p>{copy.conflictBody}</p>
        <ul>{draft.conflictFields.map((field) => <li key={field}>
          <strong className="mono">{field.replaceAll("_", " ")}</strong>
          <dl>
            <div><dt>{copy.localValue}</dt><dd>{String(draft.fields[field] ?? "—")}</dd></div>
            <div><dt>{copy.serverValue}</dt><dd>{String(draft.serverFields?.[field] ?? "—")}</dd></div>
          </dl>
          <div>
            <button type="button" onClick={() => autosave.current?.resolveConflict(field, "local")}>{copy.keepLocal}</button>
            <button type="button" onClick={() => autosave.current?.resolveConflict(field, "server")}>{copy.useServer}</button>
          </div>
        </li>)}</ul>
      </section> : null}

      {errors.length ? <div ref={errorSummary} tabIndex={-1} className="contribution-errors" role="alert" id={stageMeta.validationAnchor}>
        <strong>{errors.length === 1 ? copy.errorsOne : formatMessage(copy.errorsMany, { count: errors.length })}</strong>
        <ul>{errors.map((error) => <li key={error}>{error}</li>)}</ul>
      </div> : null}

      <div className="contribution-form">
        {stage === "evidence" ? <>
          <fieldset><legend>{copy.evidenceLegend}</legend><div className="contribution-choice-grid">
            {([["packaging_label", copy.evidenceTypes.packaging], ["government_database", copy.evidenceTypes.government], ["public_document", copy.evidenceTypes.document], ["maintainer_attestation", copy.evidenceTypes.attestation]] as const).map(([value, label]) =>
              <label key={value}><input type="radio" name="evidence_type" value={value} checked={fields.evidence_type === value} onChange={() => update("evidence_type", value)} /><span>{label}</span></label>)}
          </div></fieldset>
          <Field name="source_uri" label={copy.sourceUrl} hint={copy.sourceUrlHint}><input id="contribution-source_uri" type="url" inputMode="url" value={fields.source_uri} onChange={(event) => update("source_uri", event.target.value)} {...described("source_uri")} /></Field>
          <label className="contribution-check"><input id="contribution-rights_acknowledged" type="checkbox" checked={fields.rights_acknowledged} onChange={(event) => update("rights_acknowledged", event.target.checked)} /><span>{copy.rights}</span></label>
        </> : null}

        {stage === "details" ? <>
          <Field name="name" label={copy.fields.name}><input id="contribution-name" value={fields.name} onChange={(event) => update("name", event.target.value)} {...described("name")} /></Field>
          <Field name="name_local" label={copy.fields.nameLocal}><input id="contribution-name_local" value={fields.name_local} onChange={(event) => update("name_local", event.target.value)} /></Field>
          <div className="contribution-form-pair">
            <Field name="locale" label={copy.fields.locale}><input id="contribution-locale" placeholder={copy.fields.localePlaceholder} value={fields.locale} onChange={(event) => update("locale", event.target.value)} {...described("locale")} /></Field>
            <Field name="category" label={copy.fields.category}><input id="contribution-category" value={fields.category} onChange={(event) => update("category", event.target.value)} {...described("category")} /></Field>
          </div>
          <div className="contribution-form-triplet">
            <Field name="portion_description" label={copy.fields.portionDescription}><input id="contribution-portion_description" placeholder={copy.fields.portionPlaceholder} value={fields.portion_description} onChange={(event) => update("portion_description", event.target.value)} {...described("portion_description")} /></Field>
            <Field name="portion_amount" label={copy.fields.amount}><input id="contribution-portion_amount" inputMode="decimal" value={fields.portion_amount} onChange={(event) => update("portion_amount", event.target.value)} {...described("portion_amount")} /></Field>
            <Field name="portion_unit" label={copy.fields.originalUnit}><select id="contribution-portion_unit" value={fields.portion_unit} onChange={(event) => update("portion_unit", event.target.value as ContributionFields["portion_unit"])}><option value="g">{copy.units.grams}</option><option value="oz">{copy.units.ounces}</option><option value="lb">{copy.units.pounds}</option><option value="serving">{copy.units.serving}</option></select></Field>
          </div>
          <Field name="portion_grams" label={copy.fields.canonicalGrams} hint={copy.fields.canonicalHint}><input id="contribution-portion_grams" inputMode="decimal" value={fields.portion_grams} onChange={(event) => update("portion_grams", event.target.value)} {...described("portion_grams")} /></Field>
          <div className="contribution-nutrients">
            {([["energy_kcal", copy.fields.energy, "kcal"], ["protein_g", copy.fields.protein, "g"], ["fat_g", copy.fields.fat, "g"], ["carbohydrate_g", copy.fields.carbohydrate, "g"]] as const).map(([name, label, unit]) =>
              <Field key={name} name={name} label={label}><div className="unit-input"><input id={`contribution-${name}`} inputMode="decimal" value={fields[name]} onChange={(event) => update(name, event.target.value)} {...described(name)} /><span>{unit}</span></div></Field>)}
          </div>
          <Field name="ingredients" label={copy.fields.ingredients}><textarea id="contribution-ingredients" value={fields.ingredients} onChange={(event) => update("ingredients", event.target.value)} /></Field>
        </> : null}
        {stage === "duplicates" ? <section className="duplicate-check" aria-labelledby="duplicate-title">
          <h2 id="duplicate-title">{copy.duplicateTitle}</h2>
          <p>{formatMessage(copy.duplicateLead, { food: fields.name || copy.yourFood, locale: fields.locale || copy.itsLocale })}</p>
          <button className="contribution-secondary" type="button" onClick={() => void checkDuplicates()} disabled={checkingDuplicates}>{checkingDuplicates ? copy.checking : draft.duplicateQuery ? copy.checkAgain : copy.checkIndex}</button>
          {draft.duplicateQuery ? draft.duplicateCandidates.length ? <div className="duplicate-results">
            <p className="mono">{formatMessage(copy.possibleMatches, { count: draft.duplicateCandidates.length })}</p>
            <ul>{draft.duplicateCandidates.map((candidate) => <li key={`${candidate.source}-${candidate.sourceId}`}><strong>{candidate.name}</strong><span>{candidate.source} / {candidate.sourceId}</span></li>)}</ul>
            <label className="contribution-check"><input id="contribution-duplicates_resolved" type="checkbox" checked={fields.duplicates_resolved} onChange={(event) => update("duplicates_resolved", event.target.checked)} /><span>{copy.duplicateConfirm}</span></label>
          </div> : <p className="duplicate-clear" role="status">{copy.noMatches}</p> : null}
        </section> : null}

        {stage === "provenance" ? <>
          <Field name="pack_id" label={copy.fields.pack} hint={copy.fields.packHint}><input id="contribution-pack_id" value={fields.pack_id} onChange={(event) => update("pack_id", event.target.value)} {...described("pack_id")} /></Field>
          <Field name="source_date" label={copy.fields.sourceDate}><input id="contribution-source_date" type="date" value={fields.source_date} onChange={(event) => update("source_date", event.target.value)} {...described("source_date")} /></Field>
          <Field name="attribution" label={copy.fields.attribution} hint={copy.fields.attributionHint}><input id="contribution-attribution" value={fields.attribution} onChange={(event) => update("attribution", event.target.value)} {...described("attribution")} /></Field>
          <fieldset><legend>{copy.licenseLegend}</legend><div className="contribution-choice-grid">
            {([["contributor-original", copy.licenses.original], ["CC0-1.0", copy.licenses.cc0], ["public-domain", copy.licenses.publicDomain]] as const).map(([value, label]) => <label key={value}><input type="radio" name="source_license" checked={fields.source_license === value} onChange={() => update("source_license", value)} /><span>{label}</span></label>)}
          </div></fieldset>
        </> : null}

        {stage === "review" ? <section className="contribution-review">
          {contributionStageList().slice(0, 4).map((item) => <div className="review-section" key={item.slug}>
            <p className="mono">{String(item.order).padStart(2, "0")} · {contributionMessage(language, item.headingKey)}</p>
            <dl>{stageFields[item.slug].filter((name) => !["rights_acknowledged", "duplicates_resolved"].includes(name)).map((name) => fields[name] ? <div key={name}><dt>{name.replaceAll("_", " ")}</dt><dd>{String(fields[name])}</dd></div> : null)}</dl>
            <button type="button" onClick={() => navigate(item.slug)}>{copy.actions.edit}</button>
          </div>)}
          <div className="review-warning"><strong>{copy.reviewWarning}</strong><p>{copy.reviewWarningBody}</p></div>
          <label className="contribution-check"><input id="contribution-review_acknowledged" type="checkbox" checked={fields.review_acknowledged} onChange={(event) => update("review_acknowledged", event.target.checked)} /><span>{copy.reviewConfirm}</span></label>
          {authRequired ? <form className="contribution-auth" onSubmit={authSubmit}>
            <p className="mono">{copy.accountLabel}</p><h2>{copy.accountTitle}</h2>
            <p>{copy.accountBody}</p>
            <div className="auth-mode"><button type="button" aria-pressed={authMode === "login"} onClick={() => setAuthMode("login")}>{copy.signIn}</button><button type="button" aria-pressed={authMode === "register"} onClick={() => setAuthMode("register")}>{copy.createAccount}</button></div>
            <label>{copy.email}<input required type="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} /></label>
            <label>{copy.password}<input required minLength={12} type="password" autoComplete={authMode === "login" ? "current-password" : "new-password"} value={password} onChange={(event) => setPassword(event.target.value)} /></label>
            <button className="contribution-primary" type="submit" disabled={busy}>{busy ? copy.handing : authMode === "login" ? copy.signInHandoff : copy.createHandoff}</button>
          </form> : null}
        </section> : null}
      </div>
      <div ref={inlineActions} className="contribution-actions contribution-actions-inline">
        {stageMeta.previous ? <button type="button" className="contribution-back" onClick={() => navigate(stageMeta.previous!)}>← {copy.actions.back}</button> : <span />}
        {stageMeta.next ? <button type="button" className="contribution-primary" onClick={continueJourney}>{copy.actions.continue} →</button> : <button type="button" className="contribution-primary" onClick={() => void submit()} disabled={busy}>{busy ? copy.handing : copy.actions.submit + " →"}</button>}
      </div>
    </article>
    <div className={`contribution-actions contribution-actions-mobile${mobileActionsVisible ? " is-visible" : ""}`}>
      {stageMeta.previous ? <button type="button" className="contribution-back" onClick={() => navigate(stageMeta.previous!)}>← {copy.actions.back}</button> : <span />}
      {stageMeta.next ? <button type="button" className="contribution-primary" onClick={continueJourney}>{copy.actions.continue} →</button> : <button type="button" className="contribution-primary" onClick={() => void submit()} disabled={busy}>{busy ? copy.handing : copy.actions.submit + " →"}</button>}
    </div>
  </main>;
}
