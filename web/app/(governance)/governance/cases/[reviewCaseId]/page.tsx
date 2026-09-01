import { GovernanceCase } from "@/components/governance/governance-case";

export default async function GovernanceCasePage({
  params,
}: {
  params: Promise<{ reviewCaseId: string }>;
}) {
  const { reviewCaseId } = await params;
  return <GovernanceCase reviewCaseId={reviewCaseId} />;
}
