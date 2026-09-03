import { renderEmbed } from "@/lib/server/embed";

export async function GET(
  request: Request,
  context: { params: Promise<{ source: string; sourceId: string }> },
) {
  const { source, sourceId } = await context.params;
  return renderEmbed(request, { kind: "food", source, sourceId });
}
