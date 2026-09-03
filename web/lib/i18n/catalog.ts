import { pseudoLanguage, type InterfaceLanguage, type ShippedLanguage } from "@/lib/routes";

export { pseudoLanguage } from "@/lib/routes";

type PluralMessage = Readonly<{ one: string; other: string }>;
type MessageValue = string | PluralMessage;
type WidenCatalog<T> = {
  readonly [K in keyof T]: T[K] extends string
    ? string
    : T[K] extends readonly (infer U)[]
      ? readonly WidenCatalog<U>[]
      : T[K] extends PluralMessage
        ? PluralMessage
        : WidenCatalog<T[K]>;
};

export const fallbackLanguage = "en" as const;

export const enCatalog = {
  metadata: {
    homeTitle: "Food data belongs to everyone - opennosh",
    homeDescription: "Search, verify, improve, and reuse an open, versioned food-data commons.",
    foodTitle: "Food record - opennosh",
    foodDescription: "Inspect nutrition together with its source, version, license, and provenance.",
    noticesTitle: "Licenses and data notices - opennosh",
    noticesDescription: "The software and dataset terms that apply to opennosh.",
  },
  common: {
    home: "Home",
    explore: "Explore",
    contribute: "Contribute",
    commons: "Commons",
    build: "Build",
    status: "Status",
    foodRecord: "Food record",
    skipToContent: "Skip to content",
    opennoshHome: "opennosh home",
    breadcrumb: "Breadcrumb",
  },
  shell: {
    interfaceLanguage: "English",
    languageCode: "EN",
    interfaceLabel: "Interface language: {language}",
    interfaceCompact: "Interface / {code}",
    foodLocaleIndependent: "Food locale is selected independently in Explore",
    menu: "Menu",
    close: "Close",
    tracker: "Tracker",
    mobileTracker: "Open private tracker",
    primaryNavigation: "Primary navigation",
    mobileNavigation: "Mobile navigation",
    utilities: "Utilities",
    contributionContext: "Contribution",
    nextAction: "Next / {action}",
    footerNavigation: "Footer navigation",
    licenses: "Licenses + notices",
    source: "Source",
    privateTracker: "Private tracker",
    footerStatement: "Open infrastructure for food knowledge.",
    footerStatementSecond: "Built in public.",
  },
  navigation: {
    hubLabel: "Hub {index} / {label}",
    guides: "What guides this hub",
    availableNow: "Available now",
    inside: "Inside {label}",
    quiet: "This hub is in place. Its first public tool will appear here when that capability is ready; the navigation will not advertise unfinished work.",
    hubs: {
      explore: {
        label: "Explore",
        description: "Find food knowledge with its source, preparation, portion, locale, and uncertainty still attached.",
        action: "See how records work",
        compactAction: "Records",
        principles: ["Public by default", "Context beside numbers", "Provenance in the open"],
      },
      contribute: {
        label: "Contribute",
        description: "Document a missing food without flattening the place, preparation, or people that give it meaning.",
        action: "Start a contribution",
        compactAction: "Start",
        principles: ["Name the context", "Keep original units", "Publish through review"],
      },
      commons: {
        label: "Commons",
        description: "Inspect the rules, sources, versions, and stewardship that let shared food data earn trust in public.",
        action: "Read licenses and notices",
        compactAction: "Notices",
        principles: ["Visible stewardship", "Versioned releases", "Licenses stay attached"],
      },
      build: {
        label: "Build",
        description: "Use inspectable schemas, packs, APIs, and source code to make food knowledge useful elsewhere.",
        action: "View the source repository",
        compactAction: "Source",
        principles: ["Portable schemas", "Reusable public data", "Open-source tools"],
      },
    },
    children: {
      search: { label: "Search foods", description: "Search public food records without an account." },
      start: { label: "Start a contribution", description: "Begin a guided food record contribution." },
      missions: { label: "Commons missions", description: "Follow measurable food-data gaps and verified accepted progress." },
      packs: { label: "Browse data packs", description: "Inspect public, versioned food-data releases." },
      api: { label: "API reference", description: "Use the public contract in another product." },
      notices: { label: "Licenses + notices", description: "Understand the terms attached to each source and export." },
    },
  },
  home: {
    foods: ["Jollof rice", "Masala dosa", "Mole poblano", "Gaeng keow wan", "Ful medames", "Feijoada"],
    ribbonLabel: "Foods the commons should represent",
    heroMeta: "The open food commons",
    heroTerms: "CC0 · public · versioned",
    heroLine1: "Food data",
    heroLine2: "belongs to",
    heroLine3: "everyone",
    open: "OPEN",
    byDesign: "by design",
    heroLead: "Search it. Verify it. Add what is missing.",
    heroLeadSecond: "Reuse it anywhere.",
    start: "Start",
    exploreIndex: "01 / Explore",
    exploreTitle: "Find the food.",
    exploreTitleSecond: "See the source.",
    exploreDescription: "Food records should show where the information came from, what it describes, and how confidently it can be reused.",
    explorerStatusLabel: "Public explorer status",
    publicExplorer: "Public explorer",
    searchNext: "Search is the next surface.",
    inDevelopment: "Search is live",
    principles: [
      { title: "Anonymous by default", description: "Look up public food knowledge without creating an account." },
      { title: "Context beside numbers", description: "Preparations, portions, locale, and uncertainty stay attached." },
      { title: "Provenance in the open", description: "Sources, versions, licenses, and contributors remain visible." },
    ],
    commonsIndex: "02 / Commons",
    commonsTitle: "A commons earns trust in public.",
    commonsDescription: "Accepted changes will become movement: new foods, verified portions, source additions, and published packs—drawn only from real repository events.",
    contributeIndex: "03 / Contribute",
    contributeTitle: "What is missing",
    contributeTitleSecond: "belongs here too.",
    contributeDescription: "Regional, restaurant, and home-cooked foods deserve records that preserve context instead of flattening it.",
    chapters: [
      { label: "Chapter 01", title: "Name + context", description: "Describe the food, preparation, and locale in your own words." },
      { label: "Chapter 02", title: "Ingredients + portions", description: "Keep original units while nutrition is calculated in canonical grams." },
      { label: "Chapter 03", title: "Sources + review", description: "Show the evidence, review the record, and publish through Git." },
    ],
    contributionGuide: "Read the contribution guide",
    buildIndex: "04 / Build",
    buildTitle: "Take the data.",
    buildTitleSecond: "Make it useful.",
    buildDescription: "The schema, packs, API, and code are inspectable. The tracker is one proof—not the boundary of what can be built.",
    buildItems: [
      { title: "Food-pack schema", detail: "JSON Schema" },
      { title: "Versioned packs", detail: "CC0 data" },
      { title: "Source repository", detail: "MIT software" },
      { title: "Private tracker", detail: "Self-hosted utility" },
    ],
    closingLead: "The commons begins with what we can document together.",
    closingTitle: "Open the",
    closingTitleSecond: "record.",
    joinGitHub: "Join on GitHub",
  },
  search: {
    liveLabel: "Starter catalog / live",
    title: "Search starter food records.",
    description: "165 validated community records are available in the database-backed starter catalog. Signed Commons publication remains a separate verified release.",
    foodName: "Food name",
    placeholder: "Try dal, paneer, tofu…",
    searching: "Searching…",
    submit: "Search records",
    errorFallback: "Search could not be completed.",
    empty: "No matching starter record yet. You can help add one through Contribute.",
    categoryFallback: "Community food",
    packFallback: "community",
  },
  truth: {
    verifiedRelease: "Verified commons release",
    verifiedRecords: { one: "verified record", other: "verified records" },
    release: "release {version}",
    staleSuffix: " · stale",
    activityActions: "Commons activity actions",
    searchRecords: "Search verified records",
    contributeFood: "Contribute a food",
    sourceCommit: "View source commit for {summary}",
    heading: "Accepted activity / last 24h",
    statuses: {
      live: "Verified release",
      quiet: "Quiet · verified",
      stale: "Stale snapshot",
      partial: "Partial snapshot",
      illustrative: "Illustrative data",
      unavailable: "Unavailable",
      loading: "Loading",
    },
    eventLabels: { food: "Food", source: "Source", portion: "Portion", pack: "Pack" },
    acceptedChanges: { one: "{count} accepted change", other: "{count} accepted changes" },
    throughRelease: "Through {date} · release {version}",
    quietTitle: "No accepted changes in the last 24 hours.",
    recent: "Most recently verified: {name}, {locale}, on {date}.",
    recentPrefix: "Most recently verified:",
    recentOn: "on",
    noRecent: "The signed release contains no earlier verified record to show here.",
    staleTitle: "Activity is temporarily stale.",
    frozen: "Frozen at the last verified release {version}.",
    staleSince: " Stale since {date}.",
    staleUnknown: " The stale time is unavailable.",
    retried: " Verification last retried {date}.",
    partialTitle: "Accepted activity is still catching up.",
    partialBody: "The record count is verified for release {version}; this event list may be incomplete.",
    illustrativeLabel: "Illustrative data",
    illustrativeTitle: "This preview is not production activity.",
    illustrativeBody: "Sample events and counts are visibly separated from verified commons facts.",
    unavailableTitle: "Accepted activity is unavailable.",
    unavailableBody: "No verified release snapshot is available, so opennosh is not showing a count or invented activity.",
    legend: {
      food: { term: "Food", description: "Accepted new record" },
      source: { term: "Source", description: "Evidence attached" },
      portion: { term: "Portion", description: "Verified serving" },
      pack: { term: "Pack", description: "Version published" },
    },
    checking: "Checking the latest accepted events.",
    checkingBody: "No speculative pulses or counts are shown while the signed release is resolved.",
  },
  missions: {
    eyebrow: "Commons missions / verified progress",
    title: "Fill a gap the commons can measure.",
    lead: "Each public mission names a food-data gap, its acceptance rule, and progress drawn only from verified publication events.",
    catalogLabel: "Public mission catalog",
    states: {
      unavailable: "Unavailable",
      zero: "No public missions",
      live: "Verified missions",
    },
    disabledTitle: "Public missions are not open yet.",
    disabledBody: "The mission catalog is disabled, so no proposal or progress is presented as public fact.",
    unavailableTitle: "Mission proof is unavailable.",
    unavailableBody: "The catalog could not verify its lifecycle and checkpoint proof. No mission progress is shown.",
    zeroTitle: "No moderated missions are public yet.",
    zeroBody: "New mission proposals stay private until a steward approves an exact versioned definition.",
    gapKinds: {
      cuisine: "Cuisine gap",
      locale: "Locale gap",
      institution: "Institution gap",
      dataset: "Dataset gap",
      missing_field: "Missing field",
    },
    definition: "Definition v{version}",
    target: "Target",
    targetValue: "{count} accepted records",
    destination: "Destination",
    acceptanceRule: "Acceptance rule",
    publicReason: "Steward decision",
    progress: "Verified progress",
    progressUnavailable: "Progress proof unavailable",
    progressZero: "No accepted records yet",
    progressCount: "{accepted} of {target} accepted",
    progressReached: "Target reached · {accepted} accepted",
    progressStale: "Stale · {accepted} accepted at the last verified checkpoint",
    progressPaused: "Paused · {accepted} accepted",
    progressCompleted: "Completed · {accepted} accepted",
    progressReleased: "Released · {accepted} accepted",
    progressClosed: "Closed · {accepted} accepted",
    reviewAt: "Review scheduled {date}",
    releaseProof: "Release receipt {digest}",
    activityEyebrow: "Broad-region activity / privacy threshold 10",
    activityTitle: "Where accepted mission records add up.",
    activityLead: "Only country or macroregion cohorts with at least ten verified accepted records appear. This surface has no contributor location, hidden total, filters, or timestamp.",
    activityLabel: "Mission activity by broad pack locale",
    activityStates: {
      unavailable: "Unavailable",
      zero: "No qualifying regions",
      live: "Verified cohorts",
    },
    activityDisabledTitle: "The geographic activity surface is not open yet.",
    activityDisabledBody: "Regional activity is disabled, so the page does not infer or display any location.",
    activityUnavailableTitle: "Regional proof is unavailable.",
    activityUnavailableBody: "The activity projection could not verify every eligible record. The whole map stays hidden.",
    activityZeroTitle: "No region meets the privacy threshold yet.",
    activityZeroBody: "Regions remain absent until one independently contains at least {count} verified accepted records.",
    country: "Country",
    macroregion: "Macroregion",
    regionAccepted: { one: "{count} accepted record", other: "{count} accepted records" },
    loadingTitle: "Checking mission proof.",
    loadingBody: "No mission, progress count, or region appears until both public responses pass validation.",
  },
  contribution: {
    chapters: {
      begin: { label: "Begin the record", promise: "Start with the source and describe only what it supports." },
      verify: { label: "Verify the claim", promise: "Check for an existing record and make its origin explicit." },
      send: { label: "Send to the commons", promise: "Inspect the exact proposal before handing it to review." },
    },
    stages: {
      evidence: { heading: "Start with the source", description: "Tell us what supports this food. A public reference is enough to begin; automated extraction never replaces your confirmation." },
      details: { heading: "Describe what the source says", description: "Keep the food name, preparation, original portion, and canonical gram weight together." },
      duplicates: { heading: "Check what already exists", description: "A real commons improves existing records when it can. We check the current public food index before creating another claim." },
      provenance: { heading: "Keep its origin attached", description: "Choose the pack, date, public credit, and source terms that will travel with this proposal." },
      review: { heading: "Review the exact proposal", description: "This sends a reviewable contribution. It does not publish the food or count it as accepted." },
    },
    actions: { back: "Back", continue: "Continue", submit: "Hand to review", viewAll: "View all steps", hideAll: "Hide all steps", edit: "Edit" },
    progress: "Contribution progress",
    stepCount: "Step {step} of 5",
    stageCount: "{chapter} · {step} / 05",
    savedServer: "Synced",
    savedDevice: "Saved on this device",
    saveScheduled: "Saved on this device · sync scheduled",
    saveSyncing: "Syncing",
    saveOffline: "Saved on this device · sync paused",
    saveConflict: "Saved on this device · review a conflicting edit",
    conflictTitle: "Choose which conflicting value to keep",
    conflictBody: "Another session changed this field. Nothing will sync until you choose.",
    localValue: "This device",
    serverValue: "Latest server value",
    keepLocal: "Keep this device value",
    useServer: "Use server value",
    saveRepair: "Saved on this device · sign in or repair sync",
    saveStorageFailed: "Device save failed · copy your work before leaving",
    retrySync: "Retry sync",
    syncBeforeSubmit: "Your work is safe on this device, but it must sync before review.",
    errorsOne: "There is one thing to fix",
    errorsMany: "There are {count} things to fix",
    evidenceLegend: "What kind of source supports this record?",
    evidenceTypes: { packaging: "Packaging label", government: "Government database", document: "Public document", attestation: "Maintainer attestation" },
    evidenceTrust: {
      unselected: "Choose a source class to see the proof required before publication.",
      packaging: "Publication requires an independently durable sanitized copy with a matching digest. After verification, the public record says evidence preserved.",
      government: "Publication requires the exact dataset release and record identity, a signed manifest, and a preserved snapshot when the license permits it. After verification, the public record says source verified.",
      document: "Publication preserves an archived copy when rights permit it. Otherwise it preserves only the citation manifest and observed digest, and says reference only.",
      attestation: "Publication preserves the signed maintainer statement and labels it attested. It is never presented as preserved primary evidence.",
    },
    sourceUrl: "Public source URL",
    sourceUrlHint: "Use a durable HTTPS page that a reviewer can inspect.",
    rights: "I can reference this source, and its terms will remain attached to the proposal.",
    evidenceUpload: {
      privateLabel: "Private evidence intake",
      title: "Add a packaging-label image",
      body: "The original goes to short-lived private quarantine. Only a verified, metadata-free copy can be attached to this draft.",
      remoteRequired: "Sign in and save this draft to the server before uploading private evidence.",
      publicFallback: "You can keep using the public source URL above; your device draft remains available.",
      packagingOnly: "Private review handoff currently accepts verified packaging-label images. Keep this draft and use the public-reference path until its evidence adapter opens.",
      attachBeforeReview: "Attach a verified packaging-label image to this exact draft version before handing it to review.",
      choose: "Take a photo or choose JPEG, PNG, or WebP",
      selected: "Image selected",
      upload: "Upload privately",
      uploading: "Uploading to private quarantine…",
      working: "Working…",
      processing: "Upload complete. Verifying and removing metadata…",
      status: "Evidence status",
      failed: "The image could not be prepared safely",
      description: "Describe what this label shows",
      redaction: "Personal information redaction",
      redactionNone: "No redaction needed",
      redactionApplied: "I applied a redaction before upload",
      redactionReviewed: "I reviewed the image for personal information",
      attach: "Attach verified copy",
      attaching: "Attaching verified evidence…",
      preservationPending: "Attached. Independent preservation is pending.",
      preserved: "Evidence preserved independently.",
      preservationFailed: "Independent preservation failed. Choose another image or use the public source reference.",
      retryStatus: "Check status again",
      retry: "That step did not finish. Your draft is safe; please retry.",
      startAgain: "Choose another image",
      restartRequired: "The private upload permission is no longer available. Choose the image again.",
      resumeUnavailable: "The previous private upload is no longer available. You can start again safely.",
      invalidType: "Choose a JPEG, PNG, or WebP image.",
      invalidSize: "Choose an image no larger than 10 MB.",
      rightsRequired: "Confirm the source-reference terms above before uploading.",
      noExtraction: "OpenNosh does not read nutrition facts from this image or change your draft automatically.",
    },
    fields: {
      name: "Food name", nameLocal: "Name in its original language (optional)", locale: "Locale", category: "Category",
      portionDescription: "Portion description", amount: "Amount", originalUnit: "Original unit", canonicalGrams: "Canonical weight in grams",
      localePlaceholder: "en-US", portionPlaceholder: "1 cup",
      canonicalHint: "Original units remain visible; grams make records comparable.", energy: "Energy", protein: "Protein", fat: "Fat", carbohydrate: "Carbohydrate",
      ingredients: "Ingredients or preparation notes (optional)", pack: "Target data pack", packHint: "A stable collection identifier, for example global-core.",
      sourceDate: "Source date", attribution: "Public contributor credit", attributionHint: "This credit remains attached even if the account is later deleted.",
    },
    units: { grams: "grams (g)", ounces: "ounces (oz)", pounds: "pounds (lb)", serving: "serving" },
    duplicateTitle: "Search the live index",
    duplicateLead: "We will search for {food} in {locale}.",
    yourFood: "your food",
    itsLocale: "its locale",
    checking: "Checking…",
    checkAgain: "Check again",
    checkIndex: "Check current food index",
    possibleMatches: "Possible matches · {count}",
    duplicateConfirm: "I reviewed these records. This proposal is still needed or adds meaning they do not contain.",
    noMatches: "No current matches found. The server will check once more at handoff.",
    licenseLegend: "Source license",
    licenses: { original: "My original documentation", cc0: "CC0 1.0", publicDomain: "Public domain" },
    reviewWarning: "Review, not instant publication",
    reviewWarningBody: "A steward checks evidence, duplicates, terms, and fit. Approval and publication are recorded separately and visibly.",
    evidenceHandoffGateTitle: "Review handoff is not open yet",
    evidenceHandoffGateBody: "Your draft stays saved on this device. Handoff opens only after the secure evidence-preservation service is activated; OpenNosh will not accept an unverified proposal into review.",
    evidenceHandoffGateAction: "Evidence handoff coming next",
    reviewConfirm: "I confirm this proposal, its public attribution, source terms, and the review process.",
    accountLabel: "Account required only for handoff",
    accountTitle: "Keep a responsible author attached",
    accountBody: "Your device draft stays local until you submit. Account deletion does not erase the public attribution or license attached to accepted data.",
    signIn: "Sign in",
    createAccount: "Create account",
    email: "Email",
    password: "Password",
    handing: "Handing over…",
    signInHandoff: "Sign in and hand over",
    createHandoff: "Create account and hand over",
    draftUnavailable: "Draft unavailable",
    draftOpenTitle: "We could not open this contribution",
    returnDraft: "Return to your device draft",
    opening: "Opening your contribution…",
    receiptLabel: "Received for review",
    receiptTitle: "Handed to the commons",
    receiptLead: "Your proposal is safely in the review queue. It is not published or counted as accepted yet.",
    submission: "Submission",
    publicCredit: "Public credit",
    acknowledgement: "Acknowledgement expected",
    receiptBody: "A steward may approve it, ask for changes, dispute the evidence, or prepare it for a future release. Publication is the separate event that adds it to the accepted commons.",
    stableStatus: "View stable status",
    statusUnavailable: "Status unavailable",
    verifyDraftTitle: "We could not verify this draft",
    verifyDraftBody: "Sign in with the account that owns it, or return to your device draft.",
    returnContribution: "Return to contribution",
    verifying: "Verifying server record",
    openingStatus: "Opening status…",
    stableContribution: "Stable contribution status",
    draftNotSubmitted: "Draft not handed over",
    receivedBody: "This proposal was received for review. Approval and publication are separate public events; a queued submission is not accepted data.",
    draftBody: "This food record remains a draft. It has not entered review, been approved, or been published.",
    draftReference: "Draft reference",
    verifiedState: "Verified state",
    reviewHistory: "Open accountable review history",
    governed: "How the commons is governed",
    resume: "Resume this draft",
    serverDuplicate: "The server found a possible existing record that was not in your earlier check. Review it before handing this proposal over.",
    serverReceiptMissing: "The server did not return a submission receipt.",
    handoffFallback: "The contribution could not be handed over.",
    safeDeviceCopy: "{message} Your device copy is still safe. Review the duplicate check and try again.",
    draftOpenFallback: "This server draft could not be opened.",
    indexFallback: "The food index could not be checked.",
    statusFallback: "This status could not be verified.",
    validation: {
      evidence_type: "Choose the source type.", source_uri: "Add a public HTTPS source URL.", rights_acknowledged: "Confirm the source-reference terms.",
      name: "Add the food name.", locale: "Add the food locale.", category: "Add a category.", portion_description: "Describe the portion.",
      portion_amount: "Add the original portion amount.", portion_grams: "Add the canonical gram weight.", energy_kcal: "Add energy per portion.",
      protein_g: "Add protein per portion.", fat_g: "Add fat per portion.", carbohydrate_g: "Add carbohydrate per portion.",
      duplicate_check_required: "Check the current food index before continuing.", candidate_unresolved: "Review the possible matches and confirm this proposal is still needed.",
      pack_id: "Choose a target pack.", source_date: "Add the source date.", attribution: "Add the public contributor credit.",
      source_license: "Choose the source license.", review_acknowledged: "Confirm the attribution, CC0 terms, and review process.",
    },
  },
  food: {
    loadingLabel: "Checking the published record",
    loadingTitle: "Loading food data and its source…",
    notFoundLabel: "Record not found / {source}:{sourceId}",
    notFoundTitle: "This published food record is not available.",
    notFoundBody: "The source or pack may have changed. Search Explore before beginning a correction.",
    returnExplore: "Return to Explore",
    unavailableLabel: "Verified read unavailable",
    unavailableTitle: "We cannot verify this record right now.",
    unavailableBody: "The page will not show cached or invented nutrition without its trust context.",
    tryAgain: "Try again",
    reference: "Reference / {reference}",
    identity: "Food record / {source}:{sourceId}",
    preparation: "Preparation",
    recordLocale: "Record locale",
    notSpecified: "Not specified in this release",
    pack: "Pack",
    sourceCollection: "Source collection",
    foodLocalePreference: "Food locale preference",
    globalLocale: "Global / no preference",
    verificationState: "Verification state",
    sourceClass: "Source class",
    releaseVersion: "Release version",
    lastVerified: "Last verified",
    notSupplied: "Not supplied by this release",
    license: "License",
    selectedPortion: "Selected portion",
    portionUnits: "Portion units",
    metric: "Metric",
    us: "US",
    canonical: "Canonical {mass}",
    keyNutrients: "Key nutrients for {portion}",
    sourceBesideValues: "Source beside the values",
    uncertainty: "Uncertainty:",
    version: "Version",
    recordLicense: "Record license",
    sourceLicense: "Source license",
    actions: "Food record actions",
    provenanceAction: "See provenance",
    compareVariants: "Compare variants",
    relatedAction: "Check related records",
    correct: "Correct this record",
    correctionTitle: "Correction: {name}",
    correctionBody: "Food record: {id}\n\nWhat should be corrected?\n",
    openTracker: "Open tracker",
    fullNutrients: "Full nutrients",
    fullTitle: "The complete published profile",
    fullBody: "Values recalculate for the selected portion. Macronutrients stay in grams in both unit modes.",
    evidenceLedger: "Evidence ledger",
    provenanceTitle: "Where this record comes from",
    provenanceBody: "Missing details stay visible. They are not converted into a confidence score.",
    source: "Source",
    openSource: "Open source",
    noSourceUrl: "No public source URL supplied",
    provenance: "Provenance",
    noProvenance: "No separate provenance note supplied",
    publication: "Publication",
    sourceVersionMissing: "Source collection / version not supplied",
    credit: "Credit",
    noCredit: "No public contributor credit supplied",
    relatedRecords: "Related records",
    sameFood: "Same food, attached context",
    noRelated: "No related records are linked",
    variantsSeparate: "Variants remain separate when preparation, evidence, values, or licensing differ.",
    variantsNoGuess: "opennosh waits for an explicit source relationship instead of guessing from similar names.",
    conflicting: "Conflicting published values",
    aligned: "Values align across these records",
    conflictBody: "Compare the source and license before choosing a record. opennosh does not average disagreements into one score.",
    alignedBody: "The records remain independently sourced even where their key values agree.",
    energy100: "Energy / 100 g",
    noVariants: "No explicitly linked variants are published for this record. It remains source-qualified on its own.",
    recordHistory: "Record history",
    historyTitle: "What this release can prove",
    historyBody: "Publication facts stay explicit even when a full revision feed is not part of the current contract.",
    currentRelease: "Current release",
    earlierRevisions: "Earlier revisions",
    stableId: "Stable record ID",
    reuse: "Reuse this record",
    reuseTitle: "The data stays attached to its terms.",
    reuseBody: "Record license: {license}. Use the public API response to preserve the source identifier and attribution.",
    apiResponse: "View API response",
  },
  notices: {
    sourceTransparency: "Source transparency",
    title: "Licenses and data notices",
    lead: "opennosh keeps software, community food packs, public reference data, share-alike data, and private account data separate. These notices do not relicense any dataset.",
    software: "Software",
    softwarePrefix: "Original opennosh software and documentation are licensed under the",
    mit: "MIT License",
    foodData: "Food data",
    communityPacks: "Community food packs",
    communityBody: "CC0 1.0 Universal. Contributor credit stays visible as a community promise, not an extra legal restriction.",
    usda: "USDA FoodData Central",
    usdaBody: "CC0 1.0 Universal, with FoodData Central retained as the source.",
    off: "Open Food Facts",
    offBody: "Database rights under ODbL 1.0 and individual contents under DbCL 1.0. The optional cache stays separate, and product images are not used.",
    exercise: "Exercise data",
    exerciseBody: "Accepted wger exercise entries retain their exact per-entry attribution and CC BY-SA 3.0 terms. ShareAlike requirements remain attached to the separate exercise export.",
    privateData: "Private account data",
    privateBody: "Your custom foods, logs, recipes, targets, body metrics, and workouts are private account data. They are not included in any public food or exercise dataset export.",
    readPrefix: "Read the",
    completeNotice: "complete distribution notice",
    readSuffix: "for operative links and packaging details.",
  },
} as const;

export type MessageCatalog = WidenCatalog<typeof enCatalog>;

const shippedCatalogs = { en: enCatalog } satisfies Record<ShippedLanguage, MessageCatalog>;

const ACCENTS: Record<string, string> = {
  a: "à", b: "ƀ", c: "ç", d: "ð", e: "ë", f: "ƒ", g: "ğ", h: "ħ", i: "ï", j: "ĵ",
  k: "ķ", l: "ŀ", m: "ɱ", n: "ñ", o: "ö", p: "þ", q: "զ", r: "ŕ", s: "š", t: "ŧ",
  u: "ü", v: "ṽ", w: "ŵ", x: "ẋ", y: "ÿ", z: "ž",
};

export function pseudoLocalize(value: string): string {
  const expanded = value
    .split(/(\{[a-zA-Z0-9_]+\})/g)
    .map((part) => part.startsWith("{")
      ? part
      : part.replace(/[A-Za-z]/g, (character) => {
          const replacement = ACCENTS[character.toLowerCase()] ?? character;
          return character === character.toUpperCase() ? replacement.toUpperCase() : replacement;
        }))
    .join("");
  return "［" + expanded + " ···］";
}

function transformCatalog<T>(value: T): T {
  if (typeof value === "string") return pseudoLocalize(value) as T;
  if (Array.isArray(value)) return value.map((item) => transformCatalog(item)) as T;
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).map(([key, entry]) => [key, transformCatalog(entry)]),
    ) as T;
  }
  return value;
}

let cachedPseudoCatalog: MessageCatalog | undefined;

export function getCatalog(language: InterfaceLanguage): MessageCatalog {
  if (language === pseudoLanguage) {
    cachedPseudoCatalog ??= transformCatalog(enCatalog) as MessageCatalog;
    return cachedPseudoCatalog;
  }
  return shippedCatalogs[language] ?? shippedCatalogs[fallbackLanguage];
}

export function formatMessage(
  template: string,
  values: Readonly<Record<string, string | number>> = {},
): string {
  return template.replace(/\{([a-zA-Z0-9_]+)\}/g, (placeholder, key: string) =>
    Object.prototype.hasOwnProperty.call(values, key) ? String(values[key]) : placeholder,
  );
}

export function formatPlural(
  message: PluralMessage,
  count: number,
  language: InterfaceLanguage,
): string {
  const locale = language === pseudoLanguage ? fallbackLanguage : language;
  const category = new Intl.PluralRules(locale).select(count);
  return formatMessage(category === "one" ? message.one : message.other, { count });
}

function flattenCatalog(value: unknown, prefix = "", result = new Map<string, MessageValue>()) {
  if (typeof value === "string") {
    result.set(prefix, value);
    return result;
  }
  if (Array.isArray(value)) {
    value.forEach((entry, index) => {
      flattenCatalog(entry, prefix ? prefix + "." + index : String(index), result);
    });
    return result;
  }
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    if (typeof record.one === "string" && typeof record.other === "string") {
      result.set(prefix, { one: record.one, other: record.other });
      return result;
    }
    for (const [key, entry] of Object.entries(record)) {
      flattenCatalog(entry, prefix ? prefix + "." + key : key, result);
    }
  }
  return result;
}

function placeholders(value: string) {
  return [...value.matchAll(/\{([a-zA-Z0-9_]+)\}/g)].map((match) => match[1]).sort();
}

export function validateCatalog(candidate: unknown, fallback: unknown = enCatalog): string[] {
  const expected = flattenCatalog(fallback);
  const actual = flattenCatalog(candidate);
  const errors: string[] = [];
  for (const key of expected.keys()) if (!actual.has(key)) errors.push("Missing message: " + key);
  for (const key of actual.keys()) if (!expected.has(key)) errors.push("Unexpected message: " + key);
  for (const [key, expectedValue] of expected) {
    const actualValue = actual.get(key);
    if (!actualValue) continue;
    const expectedPlural = typeof expectedValue !== "string";
    const actualPlural = typeof actualValue !== "string";
    if (expectedPlural !== actualPlural) {
      errors.push("Plural shape mismatch: " + key);
      continue;
    }
    const expectedForms = typeof expectedValue === "string" ? [expectedValue] : [expectedValue.one, expectedValue.other];
    const actualForms = typeof actualValue === "string" ? [actualValue] : [actualValue.one, actualValue.other];
    if (JSON.stringify(expectedForms.map(placeholders)) !== JSON.stringify(actualForms.map(placeholders))) {
      errors.push("Parameter mismatch: " + key);
    }
  }
  return errors.sort();
}

export function resolveCatalogValue(candidate: unknown, path: string): MessageValue | undefined {
  return flattenCatalog(candidate).get(path) ?? flattenCatalog(enCatalog).get(path);
}
