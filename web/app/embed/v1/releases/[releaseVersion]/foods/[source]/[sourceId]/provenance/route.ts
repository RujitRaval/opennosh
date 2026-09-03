import { renderEmbed } from "@/lib/server/embed";

export async function GET(
  request: Request,
  context: {
    params: Promise<{ releaseVersion: string; source: string; sourceId: string }>;
  },
) {
  const { releaseVersion, source, sourceId } = await context.params;
  return renderEmbed(request, {
    kind: "provenance",
    releaseVersion,
    source,
    sourceId,
  });
}
