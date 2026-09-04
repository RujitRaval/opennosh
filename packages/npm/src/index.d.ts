import type {
  FoodCapabilities,
  FoodSource,
  FoodSearchResponse,
  PublicCommonsSnapshot,
  PublicFoodRecordResponse,
  PublicMissionActivityMap,
  PublicMissionCatalog,
  PublicImpactSnapshot,
  PublicIncidentListResponse,
  PublicStatusResponse,
  ProblemCode,
  RecoveryAction,
  SignedEnvelope,
  ReusePublicDeclarationResponse,
  ReusePublicDependencyListResponse,
  ReusePublicListResponse,
} from "./generated-types.js";

export type {
  FoodCapabilities,
  FoodSource,
  FoodSearchResponse,
  PublicCommonsSnapshot,
  PublicFoodRecordResponse,
  PublicMissionActivityMap,
  PublicMissionCatalog,
  PublicImpactSnapshot,
  PublicIncidentListResponse,
  PublicStatusResponse,
  RecoveryAction,
  SignedEnvelope,
  ProblemCode,
  ReusePublicDeclarationResponse,
  ReusePublicDependencyListResponse,
  ReusePublicListResponse,
} from "./generated-types.js";

export declare const PACKAGE_VERSION: string;

export declare class OpenNoshProblem extends Error {
  readonly status: number;
  readonly code: ProblemCode | ClientProblemCode;
  readonly detail: string;
  readonly request_reference: string | null;
  readonly recovery_actions: RecoveryAction[];
  readonly retry_after_seconds: number | null;
  constructor(status: number, code: ProblemCode | ClientProblemCode, detail: string, requestReference?: string | null, recoveryActions?: RecoveryAction[], retryAfterSeconds?: number | null);
}

export type OpenNoshTarget = "hosted" | (string & {});
export type ClientProblemCode = "network_error" | "request_timeout" | "redirect_refused" | "response_too_large" | "unexpected_response";
export declare function normalizeTarget(target?: OpenNoshTarget): string;

export interface RequestOptions {
  signal?: AbortSignal;
  timeoutMs?: number;
}

export interface OpenNoshResponse<T> {
  data: T;
  status: number;
  url: string | null;
  etag: string | null;
  last_modified: string | null;
  cache_control: string | null;
  content_type: string | null;
}

export interface OpenNoshClientOptions {
  target?: OpenNoshTarget;
  fetch?: typeof globalThis.fetch;
}

export interface FoodSearchParameters {
  q: string;
  locale?: string | null;
  source?: FoodSource | null;
  pack?: string[] | null;
  limit?: number;
  cursor?: string | null;
}

export interface PublicFoodParameters {
  source: "usda" | "community";
  sourceId: string;
  version?: string | null;
}

export interface ReleaseFoodParameters {
  releaseVersion: string;
  source: "usda" | "community";
  sourceId: string;
}

export interface ReleaseParameters { releaseVersion: string; }
export interface ReuseDeclarationParameters { declarationId: string; }
export interface PackDownloadParameters extends ReleaseParameters { packId: string; packVersion: string; }
export interface CommonsSnapshotOptions extends RequestOptions { ifNoneMatch?: string; }

export declare class OpenNoshClient {
  readonly origin: string;
  constructor(targetOrOptions?: OpenNoshTarget | OpenNoshClientOptions);
  capabilities(options?: RequestOptions): Promise<OpenNoshResponse<FoodCapabilities>>;
  searchFoods(query: FoodSearchParameters, options?: RequestOptions): Promise<OpenNoshResponse<FoodSearchResponse>>;
  getCommonsSnapshot(options?: CommonsSnapshotOptions): Promise<OpenNoshResponse<PublicCommonsSnapshot | null>>;
  getPublicFood(parameters: PublicFoodParameters, options?: RequestOptions): Promise<OpenNoshResponse<PublicFoodRecordResponse>>;
  listMissions(parameters?: { limit?: number }, options?: RequestOptions): Promise<OpenNoshResponse<PublicMissionCatalog>>;
  getMissionActivity(options?: RequestOptions): Promise<OpenNoshResponse<PublicMissionActivityMap>>;
  listReuse(options?: RequestOptions): Promise<OpenNoshResponse<ReusePublicListResponse>>;
  listReuseDependencies(options?: RequestOptions): Promise<OpenNoshResponse<ReusePublicDependencyListResponse>>;
  getImpact(options?: RequestOptions): Promise<OpenNoshResponse<PublicImpactSnapshot>>;
  getStatus(options?: RequestOptions): Promise<OpenNoshResponse<PublicStatusResponse>>;
  listIncidents(options?: RequestOptions): Promise<OpenNoshResponse<PublicIncidentListResponse>>;
  getReuseDeclaration(parameters: ReuseDeclarationParameters, options?: RequestOptions): Promise<OpenNoshResponse<ReusePublicDeclarationResponse>>;
  getReleaseFood(parameters: ReleaseFoodParameters, options?: RequestOptions): Promise<OpenNoshResponse<PublicFoodRecordResponse>>;
  getProvenance(parameters: ReleaseFoodParameters, options?: RequestOptions): Promise<OpenNoshResponse<string>>;
  getReleaseManifest(parameters: ReleaseParameters, options?: RequestOptions): Promise<OpenNoshResponse<SignedEnvelope>>;
  downloadPack(parameters: PackDownloadParameters, options?: RequestOptions): Promise<OpenNoshResponse<Uint8Array>>;
}
