import { createHash, createPublicKey, verify } from "node:crypto";
import type { APIRequestContext } from "@playwright/test";

import type { PublicFoodRecordContract } from "../../lib/api/adapters/foods";

const MANIFEST_KEY_ID = "acceptance-manifest-v1";
const MANIFEST_VERIFYING_KEY = "DA0jKzf9uc8nFvAVYisKb4L8PmPIHwA_eZLnTRtNH1c";
const RECEIPT_KEY_ID = "acceptance-receipt-v1";
const RECEIPT_VERIFYING_KEY = "ALKuPFTKPQrs_XfTNdyRlw0W0IHVg_P9dNzVFEw-lq8";
const RECEIPT_DOMAIN = Buffer.from("opennosh:publication-receipt:1.0\0", "utf8");

type JsonObject = Record<string, unknown>;
type ArtifactDescriptor = {
  objectKey: string;
  digest: string;
  sizeBytes: number;
};

export type VerifiedReferenceRelease = {
  latest: PublicFoodRecordContract;
  exact: PublicFoodRecordContract;
  manifestKeyId: string;
  receiptKeyId: string;
  provenance: string;
};

export async function consumeVerifiedReferenceRelease(
  request: APIRequestContext,
): Promise<VerifiedReferenceRelease> {
  const latestResponse = await request.get(
    "/api/v1/public/foods/community/rajma-masala",
  );
  requireResponse(latestResponse.ok(), `Latest reference read failed with ${latestResponse.status()}`);
  const latest = requirePublicFood(await latestResponse.json());
  requireHeader(latestResponse.headers(), "x-opennosh-release-state", "verified");
  requireHeader(
    latestResponse.headers(),
    "x-opennosh-release-version",
    latest.release.release_version,
  );

  const exactResponse = await request.get(latest.immutable_url);
  requireResponse(exactResponse.ok(), `Immutable reference read failed with ${exactResponse.status()}`);
  const exact = requirePublicFood(await exactResponse.json());
  if (!exactResponse.headers()["cache-control"]?.includes("immutable")) {
    throw new Error("Immutable reference response is missing immutable cache policy");
  }
  if (canonicalJson(exact) !== canonicalJson(latest)) {
    throw new Error("Latest and immutable public food responses do not match");
  }

  const manifestResponse = await request.get(
    `/api/v1/public/releases/${latest.release.release_version}/manifest`,
  );
  requireResponse(manifestResponse.ok(), `Signed manifest read failed with ${manifestResponse.status()}`);
  const manifestBytes = await manifestResponse.body();
  const manifestEnvelope = requireObject(parseJson(manifestBytes, "manifest"), "manifest envelope");
  const manifestKeyId = requireString(manifestEnvelope.key_id, "manifest key ID");
  const manifestPayload = requireObject(manifestEnvelope.payload, "manifest payload");
  const manifestSignature = requireString(manifestEnvelope.signature, "manifest signature");
  requireExact(manifestKeyId, MANIFEST_KEY_ID, "manifest key ID");
  requireCanonicalEnvelope(manifestEnvelope, manifestBytes, "manifest");
  requireSignature(
    MANIFEST_VERIFYING_KEY,
    Buffer.from(canonicalJson(manifestPayload), "utf8"),
    manifestSignature,
    "manifest",
  );

  const manifestVersion = requireString(manifestPayload.release_version, "manifest release version");
  const manifestPublishedAt = requireString(manifestPayload.published_at, "manifest publication time");
  requireExact(manifestVersion, latest.release.release_version, "manifest release version");
  requireExact(manifestPublishedAt, latest.release.published_at, "manifest publication time");
  const foodArtifact = requireFoodArtifact(manifestPayload);
  const recordDigest = sha256(Buffer.from(canonicalJson(exact.record), "utf8"));
  requireExact(recordDigest, foodArtifact.record.digest, "record artifact digest");
  requireExact(
    Buffer.byteLength(canonicalJson(exact.record), "utf8"),
    foodArtifact.record.sizeBytes,
    "record artifact size",
  );

  const provenanceResponse = await request.get(latest.provenance_url);
  requireResponse(provenanceResponse.ok(), `Provenance read failed with ${provenanceResponse.status()}`);
  const contentSecurityPolicy = provenanceResponse.headers()["content-security-policy"];
  if (!contentSecurityPolicy?.includes("default-src \x27none\x27")) {
    throw new Error("Provenance response is missing its isolation policy");
  }
  const provenanceBytes = await provenanceResponse.body();
  requireExact(sha256(provenanceBytes), foodArtifact.provenance.digest, "provenance digest");
  requireExact(provenanceBytes.byteLength, foodArtifact.provenance.sizeBytes, "provenance size");

  const receiptKey = requireString(
    manifestPayload.publication_receipt_key,
    "publication receipt key",
  );
  if (!/^receipts\/v1\/[0-9a-f-]{36}\.json$/.test(receiptKey)) {
    throw new Error("Manifest publication receipt key is invalid");
  }
  const artifactOrigin = process.env.VERTICAL_ARTIFACT_ORIGIN_URL ?? "http://127.0.0.1:3101/";
  const receiptResponse = await request.get(new URL(receiptKey, artifactOrigin).toString());
  requireResponse(receiptResponse.ok(), `Receipt read failed with ${receiptResponse.status()}`);
  const receiptBytes = await receiptResponse.body();
  const receiptEnvelope = requireObject(parseJson(receiptBytes, "receipt"), "receipt envelope");
  const receiptKeyId = requireString(
    receiptEnvelope.signature_key_id,
    "receipt signature key ID",
  );
  const receipt = requireObject(receiptEnvelope.receipt, "signed receipt");
  const receiptSignature = requireString(receiptEnvelope.signature, "receipt signature");
  requireExact(receiptKeyId, RECEIPT_KEY_ID, "receipt signature key ID");
  requireCanonicalEnvelope(receiptEnvelope, receiptBytes, "receipt");
  requireSignature(
    RECEIPT_VERIFYING_KEY,
    Buffer.concat([RECEIPT_DOMAIN, Buffer.from(canonicalJson(receipt), "utf8")]),
    receiptSignature,
    "receipt",
  );

  const manifestDigest = sha256(manifestBytes);
  requireExact(
    requireString(receipt.signed_release_metadata_digest, "receipt manifest digest"),
    manifestDigest,
    "receipt manifest digest",
  );
  const approvedPayloadDigest = requireDigest(
    receipt.approved_payload_digest,
    "receipt approved payload digest",
  );
  requireExact(
    requireString(receipt.release_version, "receipt release version"),
    manifestVersion,
    "receipt release version",
  );
  requireExact(
    requireString(receipt.published_at, "receipt publication time"),
    manifestPublishedAt,
    "receipt publication time",
  );
  requireReceiptProofs(receipt, manifestDigest, approvedPayloadDigest);

  return {
    latest,
    exact,
    manifestKeyId,
    receiptKeyId,
    provenance: provenanceBytes.toString("utf8"),
  };
}

function requirePublicFood(value: unknown): PublicFoodRecordContract {
  const envelope = requireObject(value, "public food response");
  const record = requireObject(envelope.record, "public food record");
  const attribution = requireObject(record.attribution, "food attribution");
  const nutrients = requireObject(record.nutrients, "food nutrients");
  const nutrientValues = requireObject(nutrients.nutrients, "nutrient values");
  const release = requireObject(envelope.release, "public release metadata");
  requireExact(requireString(envelope.schema_version, "food schema version"), "1.0", "food schema version");
  const source = requireString(record.source, "food source");
  const sourceId = requireString(record.source_id, "food source ID");
  requireExact(source, "community", "food source");
  requireExact(sourceId, "rajma-masala", "food source ID");
  requireExact(requireString(record.id, "food ID"), `${source}:${sourceId}`, "food ID");
  requireString(record.name, "food name");
  requireOptionalStringOrNull(record.name_local, "food local name");
  requireOptionalStringOrNull(record.category, "food category");
  requireExact(requireString(attribution.source, "attribution source"), source, "attribution source");
  requireString(attribution.license, "attribution license");
  for (const field of [
    "contributed_by",
    "pack_id",
    "pack_version",
    "provenance",
    "source_license",
    "source_uri",
  ]) {
    requireOptionalStringOrNull(attribution[field], `attribution ${field}`);
  }
  requireString(nutrients.basis, "nutrient basis");
  for (const [name, nutrient] of Object.entries(nutrientValues)) {
    requireDecimalString(nutrient, `nutrient ${name}`);
  }
  for (const [index, value] of requireArray(record.portions, "food portions").entries()) {
    const portion = requireObject(value, `food portion ${index}`);
    requireString(portion.name, `food portion ${index} name`);
    requireDecimalString(portion.grams, `food portion ${index} grams`);
  }

  const immutableUrl = requireApiPath(envelope.immutable_url, "immutable URL");
  const provenanceUrl = requireApiPath(envelope.provenance_url, "provenance URL");
  const releaseVersion = requireString(release.release_version, "release version");
  if (!/^\d+\.\d+\.\d+\.\d+$/.test(releaseVersion)) {
    throw new Error("Release version is invalid");
  }
  requireExact(
    immutableUrl,
    `/api/v1/public/releases/${releaseVersion}/foods/${source}/${sourceId}`,
    "immutable URL",
  );
  requireExact(provenanceUrl, `${immutableUrl}/provenance`, "provenance URL");
  requireTimestamp(release.published_at, "release publication time");
  const state = requireString(release.state, "release state");
  if (state !== "verified" && state !== "stale") throw new Error("Public release state is invalid");
  const staleAge = release.stale_age_seconds;
  if (!Number.isSafeInteger(staleAge) || Number(staleAge) < 0) {
    throw new Error("Release stale age is invalid");
  }
  return value as PublicFoodRecordContract;
}

function requireFoodArtifact(payload: JsonObject): {
  record: ArtifactDescriptor;
  provenance: ArtifactDescriptor;
} {
  const foods = requireArray(payload.foods, "manifest foods");
  const matching = foods
    .map((value) => requireObject(value, "manifest food"))
    .find((value) => value.source === "community" && value.source_id === "rajma-masala");
  if (!matching) throw new Error("Manifest is missing the Rajma masala artifact");
  return {
    record: requireDescriptor(matching.record, "record descriptor"),
    provenance: requireDescriptor(matching.provenance, "provenance descriptor"),
  };
}

function requireDescriptor(value: unknown, label: string): ArtifactDescriptor {
  const descriptor = requireObject(value, label);
  const objectKey = requireString(descriptor.object_key, `${label} object key`);
  const digest = requireString(descriptor.digest, `${label} digest`);
  const sizeBytes = descriptor.size_bytes;
  if (!/^[0-9a-f]{64}$/.test(digest) || !objectKey.includes(digest)) {
    throw new Error(`${label} is not content addressed`);
  }
  if (!Number.isSafeInteger(sizeBytes) || Number(sizeBytes) <= 0) {
    throw new Error(`${label} size is invalid`);
  }
  requireString(descriptor.media_type, `${label} media type`);
  return { objectKey, digest, sizeBytes: Number(sizeBytes) };
}

function requireReceiptProofs(
  receipt: JsonObject,
  manifestDigest: string,
  approvedPayloadDigest: string,
): void {
  const proofs = requireArray(receipt.verified_steps, "receipt proofs")
    .map((value) => requireObject(value, "receipt proof"));
  const digestFor = (step: string): string => {
    const proof = proofs.find((value) => value.step === step);
    if (!proof) throw new Error(`Receipt is missing ${step} proof`);
    return requireString(proof.content_digest, `${step} proof digest`);
  };
  requireExact(
    digestFor("commit_record"),
    approvedPayloadDigest,
    "commit record proof digest",
  );
  for (const step of ["sign_release", "publish_release", "copy_release"]) {
    requireExact(digestFor(step), manifestDigest, `${step} proof digest`);
  }
}

function requireCanonicalEnvelope(value: JsonObject, bytes: Buffer, label: string): void {
  if (canonicalJson(value) !== bytes.toString("utf8")) {
    throw new Error(`${label} envelope is not canonical JSON`);
  }
}

function requireSignature(key: string, material: Buffer, signature: string, label: string): void {
  const rawKey = Buffer.from(key, "base64url");
  const publicKey = createPublicKey({
    key: Buffer.concat([Buffer.from("302a300506032b6570032100", "hex"), rawKey]),
    format: "der",
    type: "spki",
  });
  if (!verify(null, material, publicKey, Buffer.from(signature, "base64url"))) {
    throw new Error(`${label} signature is invalid`);
  }
}

export function canonicalJson(value: unknown): string {
  return JSON.stringify(sortJson(value));
}

function sortJson(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sortJson);
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as JsonObject)
        .sort(([left], [right]) => compareUnicodeCodePoints(left, right))
        .map(([key, item]) => [key, sortJson(item)]),
    );
  }
  return value;
}

function compareUnicodeCodePoints(left: string, right: string): number {
  const leftPoints = Array.from(left);
  const rightPoints = Array.from(right);
  const sharedLength = Math.min(leftPoints.length, rightPoints.length);
  for (let index = 0; index < sharedLength; index += 1) {
    const difference =
      (leftPoints[index].codePointAt(0) ?? 0) - (rightPoints[index].codePointAt(0) ?? 0);
    if (difference !== 0) return difference;
  }
  return leftPoints.length - rightPoints.length;
}

function parseJson(bytes: Buffer, label: string): unknown {
  try {
    return JSON.parse(bytes.toString("utf8"));
  } catch (error) {
    throw new Error(`${label} is not valid JSON`, { cause: error });
  }
}

function sha256(value: Buffer): string {
  return createHash("sha256").update(value).digest("hex");
}

function requireObject(value: unknown, label: string): JsonObject {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${label} must be an object`);
  }
  return value as JsonObject;
}

function requireArray(value: unknown, label: string): unknown[] {
  if (!Array.isArray(value)) throw new Error(`${label} must be an array`);
  return value;
}

function requireString(value: unknown, label: string): string {
  if (typeof value !== "string" || value.length === 0) {
    throw new Error(`${label} must be a non-empty string`);
  }
  return value;
}

function requireDigest(value: unknown, label: string): string {
  const result = requireString(value, label);
  if (!/^[0-9a-f]{64}$/.test(result)) throw new Error(`${label} must be a SHA-256 digest`);
  return result;
}

function requireOptionalStringOrNull(value: unknown, label: string): void {
  if (value !== undefined && value !== null && typeof value !== "string") {
    throw new Error(`${label} must be a string or null when present`);
  }
}

function requireDecimalString(value: unknown, label: string): string {
  const result = requireString(value, label);
  if (!/^-?\d+(?:\.\d+)?$/.test(result)) {
    throw new Error(`${label} must be a decimal string`);
  }
  return result;
}

function requireApiPath(value: unknown, label: string): string {
  const result = requireString(value, label);
  if (!result.startsWith("/api/v1/")) throw new Error(`${label} must be a same-origin API path`);
  return result;
}

function requireTimestamp(value: unknown, label: string): string {
  const result = requireString(value, label);
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(result)) {
    throw new Error(`${label} must be an ISO 8601 timestamp`);
  }
  return result;
}

function requireResponse(condition: boolean, message: string): asserts condition {
  if (!condition) throw new Error(message);
}

function requireExact<T>(actual: T, expected: T, label: string): void {
  if (actual !== expected) throw new Error(`${label} did not match the verified release`);
}

function requireHeader(headers: Record<string, string>, name: string, expected: string): void {
  requireExact(headers[name], expected, name);
}
