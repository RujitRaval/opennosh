import type {
  EvidenceUploadInstructionResponse,
  EvidenceUploadSessionResponse,
  RedactionState,
} from "@/lib/generated/client/types.gen";

export type { EvidenceUploadSessionResponse, RedactionState };

const allowedHeaders = new Set(["content-type", "content-length", "if-none-match"]);

export async function uploadEvidenceBytes(
  instruction: EvidenceUploadInstructionResponse,
  file: File,
): Promise<void> {
  const url = new URL(instruction.url);
  if (
    url.protocol !== "https:" ||
    instruction.method !== "PUT" ||
    !Object.keys(instruction.headers).every((header) => allowedHeaders.has(header.toLowerCase()))
  ) {
    throw new Error("The secure upload instruction was invalid. Please start again.");
  }

  const expectedLength = Object.entries(instruction.headers).find(
    ([header]) => header.toLowerCase() === "content-length",
  )?.[1];
  if (expectedLength !== undefined && Number(expectedLength) !== file.size) {
    throw new Error("The selected file changed. Please choose it again.");
  }

  const headers = new Headers();
  for (const [header, value] of Object.entries(instruction.headers)) {
    // Browsers calculate Content-Length. Sending it explicitly is forbidden by Fetch.
    if (header.toLowerCase() !== "content-length") headers.set(header, value);
  }
  const response = await fetch(url, {
    method: "PUT",
    headers,
    body: file,
    credentials: "omit",
    cache: "no-store",
    redirect: "error",
  });
  if (!response.ok) {
    throw new Error("The private upload did not finish. Your draft is safe; please retry.");
  }
}
