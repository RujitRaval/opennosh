import { timingSafeEqual } from "node:crypto";

import { revalidateTag } from "next/cache";

const operationsTokenHeader = "x-opennosh-proxy-token";

function tokenMatches(supplied: string | null, expected: string | undefined): boolean {
  if (!supplied || !expected) return false;
  const suppliedBytes = Buffer.from(supplied);
  const expectedBytes = Buffer.from(expected);
  return (
    suppliedBytes.length === expectedBytes.length &&
    timingSafeEqual(suppliedBytes, expectedBytes)
  );
}

export async function POST(request: Request): Promise<Response> {
  if (
    !tokenMatches(
      request.headers.get(operationsTokenHeader),
      process.env.PUBLIC_COMMONS_REVALIDATION_TOKEN,
    )
  ) {
    return new Response(null, { status: 404, headers: { "Cache-Control": "no-store" } });
  }

  revalidateTag("public-commons", "max");
  return Response.json(
    { revalidated: true },
    { headers: { "Cache-Control": "no-store" } },
  );
}
