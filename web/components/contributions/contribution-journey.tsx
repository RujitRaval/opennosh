"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";

import { api, ApiError } from "@/lib/api";
import { contributionCatalog, contributionMessage } from "@/lib/contributions/catalog";
import type {
  ContributionFields, ContributionReceipt, ContributionStage, DuplicateCandidate,
  LocalContributionDraft,
} from "@/lib/contributions/domain";
import {
  localAccessibleStages, localCompletedStages, localContributionStorageKey,
  localStageBlockers, newLocalContributionDraft, readLocalContributionDraft,
} from "@/lib/contributions/local-draft";
import {
  contributionStageHref, contributionStageList, contributionStageRegistry,
  isContributionStage,
} from "@/lib/contributions/stage-registry";
import type { ContributionFieldName, ContributionFieldPatch } from "@/lib/generated/client/types.gen";
import { routes, type InterfaceLanguage } from "@/lib/routes";

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

function Receipt({ receipt }: { receipt: ContributionReceipt }) {
  const due = new Intl.DateTimeFormat("en", { dateStyle: "long", timeStyle: "short" }).format(new Date(receipt.acknowledgementDueAt));
  return <section className="contribution-receipt" aria-labelledby="receipt-title">
    <div className="receipt-mark" aria-hidden="true"><i /><i /></div>
    <p className="mono">Received for review</p>
    <h1 id="receipt-title">Handed to the commons</h1>
    <p className="receipt-lead">Your proposal is safely in the review queue. It is not published or counted as accepted yet.</p>
    <dl>
      <div><dt>Submission</dt><dd>{receipt.submissionId}</dd></div>
      <div><dt>Public credit</dt><dd>{receipt.attribution}</dd></div>
      <div><dt>Acknowledgement expected</dt><dd>{due}</dd></div>
    </dl>
    <p>A steward may approve it, ask for changes, dispute the evidence, or prepare it for a future release. Publication is the separate event that adds it to the accepted commons.</p>
    <Link className="contribution-primary" href={receipt.statusHref}>View stable status</Link>
  </section>;
}

function Progress({ language, pathDraftId, draft, stage }: { language: InterfaceLanguage; pathDraftId: string; draft: LocalContributionDraft; stage: ContributionStage }) {
  const [expanded, setExpanded] = useState(false);
  const completed = new Set(localCompletedStages(draft));
  const accessible = new Set(localAccessibleStages(draft));
  const catalog = contributionCatalog(language);
  const stages = contributionStageList();
  return <nav className="contribution-progress" aria-label="Contribution progress">
    <div className="contribution-progress-compact"><span className="mono">Step {contributionStageRegistry[stage].order} of 5</span><button type="button" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>{expanded ? catalog.actions.hideAll : catalog.actions.viewAll}</button></div>
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

function allFieldPatches(fields: ContributionFields): ContributionFieldPatch[] {
  return (Object.entries(fields) as [ContributionFieldName, ContributionFields[keyof ContributionFields]][])
    .filter(([field]) => field !== "duplicates_resolved")
    .map(([field, value]) => ({ field, value }));
}

export function ContributionJourney({ language, routeDraftId, requestedStage }: Props) {
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
  const rawStage = isContributionStage(requestedStage) ? requestedStage : "evidence";
  const stage = draft && localAccessibleStages(draft).includes(rawStage)
    ? rawStage : draft ? localAccessibleStages(draft).at(-1) ?? "evidence" : rawStage;

  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      if (routeDraftId === "local") {
        const stored = readLocalContributionDraft(window.localStorage.getItem(localContributionStorageKey));
        const nextDraft = stored ?? newLocalContributionDraft();
        window.localStorage.setItem(localContributionStorageKey, JSON.stringify(nextDraft));
        setDraft(nextDraft);
        return;
      }
      if (hydratedRemoteDraftId.current === routeDraftId) return;
      hydratedRemoteDraftId.current = routeDraftId;
      api.contributionDraft(routeDraftId, requestedStage).then((remote) => {
        setDraft({
          schemaVersion: "1",
          clientDraftId: remote.draftId,
          fields: remote.fields,
          duplicateCandidates: [...remote.duplicateCandidates],
          duplicateQuery: `${remote.fields.name.trim()}|${remote.fields.locale.trim()}`,
          savedAt: remote.savedAt,
          saveState: "synced",
        });
        if (remote.resolvedStage !== requestedStage) {
          router.replace(contributionStageHref(language, routeDraftId, remote.resolvedStage));
        }
      }).catch((caught) => {
        hydratedRemoteDraftId.current = null;
        setErrors([caught instanceof Error ? caught.message : "This server draft could not be opened."]);
      });
    });
    return () => cancelAnimationFrame(frame);
  }, [language, requestedStage, routeDraftId, router]);

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

  function persist(nextDraft: LocalContributionDraft) {
    const saved = { ...nextDraft, savedAt: new Date().toISOString(), saveState: "saved_on_device" as const };
    window.localStorage.setItem(localContributionStorageKey, JSON.stringify(saved));
    setDraft(saved);
  }

  function update<K extends keyof ContributionFields>(field: K, value: ContributionFields[K]) {
    if (!draft) return;
    const invalidatesDuplicates = field === "name" || field === "locale";
    persist({
      ...draft,
      fields: { ...draft.fields, [field]: value, ...(invalidatesDuplicates ? { duplicates_resolved: false } : {}) },
      ...(invalidatesDuplicates ? { duplicateCandidates: [], duplicateQuery: null } : {}),
    });
    setErrors([]);
  }

  function showErrors(messages: string[]) {
    setErrors(messages);
    requestAnimationFrame(() => errorSummary.current?.focus());
  }

  function navigate(target: ContributionStage) {
    router.push(contributionStageHref(language, routeDraftId, target));
    window.scrollTo({ top: 0, behavior: "instant" });
  }

  function continueJourney() {
    if (!draft) return;
    const blockers = localStageBlockers(draft, stage);
    if (blockers.length) return showErrors(blockers.map((item) => item.message));
    const next = contributionStageRegistry[stage].next;
    if (next) navigate(next);
  }

  async function checkDuplicates() {
    if (!draft) return;
    const detailsBlockers = localStageBlockers(draft, "details");
    if (detailsBlockers.length) return showErrors(detailsBlockers.map((item) => item.message));
    setCheckingDuplicates(true);
    try {
      const result = await api.searchFoods(draft.fields.name.trim(), draft.fields.locale.trim());
      const candidates: DuplicateCandidate[] = result.items.map((item) => ({
        source: item.source, sourceId: item.source_id, name: item.name, locale: null,
      }));
      persist({
        ...draft,
        duplicateCandidates: candidates,
        duplicateQuery: `${draft.fields.name.trim()}|${draft.fields.locale.trim()}`,
        fields: { ...draft.fields, duplicates_resolved: candidates.length === 0 },
      });
      setErrors([]);
    } catch (caught) {
      showErrors([caught instanceof Error ? caught.message : "The food index could not be checked."]);
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
      let remote = routeDraftId === "local"
        ? await api.createContributionDraft({ client_draft_id: draft.clientDraftId })
        : await api.contributionDraft(routeDraftId, stage);
      remote = await api.patchContributionDraft(remote.draftId, {
        expected_draft_version: remote.draftVersion, operation_id: crypto.randomUUID(),
        requested_stage: stage, patches: allFieldPatches(draft.fields),
      });
      if (remote.duplicateCandidates.length > 0 && draft.fields.duplicates_resolved) {
        remote = await api.patchContributionDraft(remote.draftId, {
          expected_draft_version: remote.draftVersion, operation_id: crypto.randomUUID(),
          requested_stage: "review", patches: [{ field: "duplicates_resolved", value: true }],
        });
      }
      const submitted = await api.submitContributionDraft(remote.draftId, {
        expected_draft_version: remote.draftVersion, idempotency_key: crypto.randomUUID(),
      });
      if (!submitted.receipt) throw new Error("The server did not return a submission receipt.");
      window.localStorage.removeItem(localContributionStorageKey);
      setReceipt(submitted.receipt);
      setAuthRequired(false);
      router.replace(routes.contributionStatus(language, submitted.draftId));
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : "The contribution could not be handed over.";
      showErrors([`${message} Your device copy is still safe. Review the duplicate check and try again.`]);
    } finally { setBusy(false); }
  }

  if (!draft) return <main id="main-content" className="contribution-loading" aria-busy="true">Opening your device draft…</main>;
  if (receipt) return <main id="main-content" className="contribution-receipt-page"><Receipt receipt={receipt} /></main>;

  const stageMeta = contributionStageRegistry[stage];
  const fields = draft.fields;
  const blockerFields = new Set(localStageBlockers(draft, stage).map((item) => item.field));
  const described = (name: keyof ContributionFields) => errors.length > 0 && blockerFields.has(name) ? { "aria-invalid": true as const } : {};

  function authSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void submit({ mode: authMode, email, password });
  }

  return <main id="main-content" className="contribution-page">
    <Progress language={language} pathDraftId={routeDraftId} draft={draft} stage={stage} />
    <article className="contribution-workspace">
      <header ref={stageHeading} className="contribution-stage-heading">
        <p className="mono">{contributionCatalog(language).chapters[stageMeta.chapter].label} · {String(stageMeta.order).padStart(2, "0")} / 05</p>
        <h1 id={stageMeta.headingAnchor}>{contributionMessage(language, stageMeta.headingKey)}</h1>
        <p>{contributionMessage(language, stageMeta.descriptionKey)}</p>
        <span className="contribution-save mono" role="status">● Saved on this device</span>
      </header>

      {errors.length ? <div ref={errorSummary} tabIndex={-1} className="contribution-errors" role="alert" id={stageMeta.validationAnchor}>
        <strong>There {errors.length === 1 ? "is one thing" : `are ${errors.length} things`} to fix</strong>
        <ul>{errors.map((error) => <li key={error}>{error}</li>)}</ul>
      </div> : null}

      <div className="contribution-form">
        {stage === "evidence" ? <>
          <fieldset><legend>What kind of source supports this record?</legend><div className="contribution-choice-grid">
            {([['packaging_label', 'Packaging label'], ['government_database', 'Government database'], ['public_document', 'Public document'], ['maintainer_attestation', 'Maintainer attestation']] as const).map(([value, label]) =>
              <label key={value}><input type="radio" name="evidence_type" value={value} checked={fields.evidence_type === value} onChange={() => update("evidence_type", value)} /><span>{label}</span></label>)}
          </div></fieldset>
          <Field name="source_uri" label="Public source URL" hint="Use a durable HTTPS page that a reviewer can inspect."><input id="contribution-source_uri" type="url" inputMode="url" value={fields.source_uri} onChange={(event) => update("source_uri", event.target.value)} {...described("source_uri")} /></Field>
          <label className="contribution-check"><input id="contribution-rights_acknowledged" type="checkbox" checked={fields.rights_acknowledged} onChange={(event) => update("rights_acknowledged", event.target.checked)} /><span>I can reference this source, and its terms will remain attached to the proposal.</span></label>
        </> : null}

        {stage === "details" ? <>
          <Field name="name" label="Food name"><input id="contribution-name" value={fields.name} onChange={(event) => update("name", event.target.value)} {...described("name")} /></Field>
          <Field name="name_local" label="Name in its original language (optional)"><input id="contribution-name_local" value={fields.name_local} onChange={(event) => update("name_local", event.target.value)} /></Field>
          <div className="contribution-form-pair">
            <Field name="locale" label="Locale"><input id="contribution-locale" placeholder="en-US" value={fields.locale} onChange={(event) => update("locale", event.target.value)} {...described("locale")} /></Field>
            <Field name="category" label="Category"><input id="contribution-category" value={fields.category} onChange={(event) => update("category", event.target.value)} {...described("category")} /></Field>
          </div>
          <div className="contribution-form-triplet">
            <Field name="portion_description" label="Portion description"><input id="contribution-portion_description" placeholder="1 cup" value={fields.portion_description} onChange={(event) => update("portion_description", event.target.value)} {...described("portion_description")} /></Field>
            <Field name="portion_amount" label="Amount"><input id="contribution-portion_amount" inputMode="decimal" value={fields.portion_amount} onChange={(event) => update("portion_amount", event.target.value)} {...described("portion_amount")} /></Field>
            <Field name="portion_unit" label="Original unit"><select id="contribution-portion_unit" value={fields.portion_unit} onChange={(event) => update("portion_unit", event.target.value as ContributionFields["portion_unit"])}><option value="g">grams (g)</option><option value="oz">ounces (oz)</option><option value="lb">pounds (lb)</option><option value="serving">serving</option></select></Field>
          </div>
          <Field name="portion_grams" label="Canonical weight in grams" hint="Original units remain visible; grams make records comparable."><input id="contribution-portion_grams" inputMode="decimal" value={fields.portion_grams} onChange={(event) => update("portion_grams", event.target.value)} {...described("portion_grams")} /></Field>
          <div className="contribution-nutrients">
            {([['energy_kcal', 'Energy', 'kcal'], ['protein_g', 'Protein', 'g'], ['fat_g', 'Fat', 'g'], ['carbohydrate_g', 'Carbohydrate', 'g']] as const).map(([name, label, unit]) =>
              <Field key={name} name={name} label={label}><div className="unit-input"><input id={`contribution-${name}`} inputMode="decimal" value={fields[name]} onChange={(event) => update(name, event.target.value)} {...described(name)} /><span>{unit}</span></div></Field>)}
          </div>
          <Field name="ingredients" label="Ingredients or preparation notes (optional)"><textarea id="contribution-ingredients" value={fields.ingredients} onChange={(event) => update("ingredients", event.target.value)} /></Field>
        </> : null}
        {stage === "duplicates" ? <section className="duplicate-check" aria-labelledby="duplicate-title">
          <h2 id="duplicate-title">Search the live index</h2>
          <p>We will search for <strong>{fields.name || "your food"}</strong> in <strong>{fields.locale || "its locale"}</strong>.</p>
          <button className="contribution-secondary" type="button" onClick={() => void checkDuplicates()} disabled={checkingDuplicates}>{checkingDuplicates ? "Checking…" : draft.duplicateQuery ? "Check again" : "Check current food index"}</button>
          {draft.duplicateQuery ? draft.duplicateCandidates.length ? <div className="duplicate-results">
            <p className="mono">Possible matches · {draft.duplicateCandidates.length}</p>
            <ul>{draft.duplicateCandidates.map((candidate) => <li key={`${candidate.source}-${candidate.sourceId}`}><strong>{candidate.name}</strong><span>{candidate.source} / {candidate.sourceId}</span></li>)}</ul>
            <label className="contribution-check"><input id="contribution-duplicates_resolved" type="checkbox" checked={fields.duplicates_resolved} onChange={(event) => update("duplicates_resolved", event.target.checked)} /><span>I reviewed these records. This proposal is still needed or adds meaning they do not contain.</span></label>
          </div> : <p className="duplicate-clear" role="status">No current matches found. The server will check once more at handoff.</p> : null}
        </section> : null}

        {stage === "provenance" ? <>
          <Field name="pack_id" label="Target data pack" hint="A stable collection identifier, for example global-core."><input id="contribution-pack_id" value={fields.pack_id} onChange={(event) => update("pack_id", event.target.value)} {...described("pack_id")} /></Field>
          <Field name="source_date" label="Source date"><input id="contribution-source_date" type="date" value={fields.source_date} onChange={(event) => update("source_date", event.target.value)} {...described("source_date")} /></Field>
          <Field name="attribution" label="Public contributor credit" hint="This credit remains attached even if the account is later deleted."><input id="contribution-attribution" value={fields.attribution} onChange={(event) => update("attribution", event.target.value)} {...described("attribution")} /></Field>
          <fieldset><legend>Source license</legend><div className="contribution-choice-grid">
            {([['contributor-original', 'My original documentation'], ['CC0-1.0', 'CC0 1.0'], ['public-domain', 'Public domain']] as const).map(([value, label]) => <label key={value}><input type="radio" name="source_license" checked={fields.source_license === value} onChange={() => update("source_license", value)} /><span>{label}</span></label>)}
          </div></fieldset>
        </> : null}

        {stage === "review" ? <section className="contribution-review">
          {contributionStageList().slice(0, 4).map((item) => <div className="review-section" key={item.slug}>
            <p className="mono">{String(item.order).padStart(2, "0")} · {contributionMessage(language, item.headingKey)}</p>
            <dl>{stageFields[item.slug].filter((name) => !["rights_acknowledged", "duplicates_resolved"].includes(name)).map((name) => fields[name] ? <div key={name}><dt>{name.replaceAll("_", " ")}</dt><dd>{String(fields[name])}</dd></div> : null)}</dl>
            <button type="button" onClick={() => navigate(item.slug)}>Edit</button>
          </div>)}
          <div className="review-warning"><strong>Review, not instant publication</strong><p>A steward checks evidence, duplicates, terms, and fit. Approval and publication are recorded separately and visibly.</p></div>
          <label className="contribution-check"><input id="contribution-review_acknowledged" type="checkbox" checked={fields.review_acknowledged} onChange={(event) => update("review_acknowledged", event.target.checked)} /><span>I confirm this proposal, its public attribution, source terms, and the review process.</span></label>
          {authRequired ? <form className="contribution-auth" onSubmit={authSubmit}>
            <p className="mono">Account required only for handoff</p><h2>Keep a responsible author attached</h2>
            <p>Your device draft stays local until you submit. Account deletion does not erase the public attribution or license attached to accepted data.</p>
            <div className="auth-mode"><button type="button" aria-pressed={authMode === "login"} onClick={() => setAuthMode("login")}>Sign in</button><button type="button" aria-pressed={authMode === "register"} onClick={() => setAuthMode("register")}>Create account</button></div>
            <label>Email<input required type="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} /></label>
            <label>Password<input required minLength={12} type="password" autoComplete={authMode === "login" ? "current-password" : "new-password"} value={password} onChange={(event) => setPassword(event.target.value)} /></label>
            <button className="contribution-primary" type="submit" disabled={busy}>{busy ? "Handing over…" : authMode === "login" ? "Sign in and hand over" : "Create account and hand over"}</button>
          </form> : null}
        </section> : null}
      </div>
      <div ref={inlineActions} className="contribution-actions contribution-actions-inline">
        {stageMeta.previous ? <button type="button" className="contribution-back" onClick={() => navigate(stageMeta.previous!)}>← Back</button> : <span />}
        {stageMeta.next ? <button type="button" className="contribution-primary" onClick={continueJourney}>Continue →</button> : <button type="button" className="contribution-primary" onClick={() => void submit()} disabled={busy}>{busy ? "Handing over…" : "Hand to review →"}</button>}
      </div>
    </article>
    <div className={`contribution-actions contribution-actions-mobile${mobileActionsVisible ? " is-visible" : ""}`}>
      {stageMeta.previous ? <button type="button" className="contribution-back" onClick={() => navigate(stageMeta.previous!)}>← Back</button> : <span />}
      {stageMeta.next ? <button type="button" className="contribution-primary" onClick={continueJourney}>Continue →</button> : <button type="button" className="contribution-primary" onClick={() => void submit()} disabled={busy}>{busy ? "Handing over…" : "Hand to review →"}</button>}
    </div>
  </main>;
}
