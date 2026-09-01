import type {
  DisputeOpenRequest,
  ReviewCaseApproval,
  ReviewCaseDecision,
  ReviewCasePause,
  ReviewCaseRecusal,
  ReviewCaseResume,
  ReviewResponseRequest,
} from "@/lib/generated/client/types.gen";
import type {
  GovernanceReviewCase,
  GovernanceReviewQueue,
} from "@/lib/api/domain/governance";

import { request } from "./transport";

function mutationHeaders(): HeadersInit {
  return { "Idempotency-Key": crypto.randomUUID() };
}

export const governanceApi = {
  queue(packId: string): Promise<GovernanceReviewQueue> {
    return request(`/api/v1/governance/review-cases?pack_id=${encodeURIComponent(packId)}`);
  },
  reviewCase(reviewCaseId: string): Promise<GovernanceReviewCase> {
    return request(`/api/v1/governance/review-cases/${encodeURIComponent(reviewCaseId)}`);
  },
  contributorCase(draftId: string): Promise<GovernanceReviewCase> {
    return request(
      `/api/v1/governance/contributor/review-case?draft_id=${encodeURIComponent(draftId)}`,
    );
  },
  claim(reviewCaseId: string, expectedRevision: number): Promise<GovernanceReviewCase> {
    return request(`/api/v1/governance/review-cases/${encodeURIComponent(reviewCaseId)}/claim`, {
      method: "POST",
      headers: mutationHeaders(),
      body: JSON.stringify({ expected_revision: expectedRevision }),
    });
  },
  pause(reviewCaseId: string, body: ReviewCasePause): Promise<GovernanceReviewCase> {
    return request(`/api/v1/governance/review-cases/${encodeURIComponent(reviewCaseId)}/pause`, {
      method: "POST",
      headers: mutationHeaders(),
      body: JSON.stringify(body),
    });
  },
  resume(reviewCaseId: string, body: ReviewCaseResume): Promise<GovernanceReviewCase> {
    return request(`/api/v1/governance/review-cases/${encodeURIComponent(reviewCaseId)}/resume`, {
      method: "POST",
      headers: mutationHeaders(),
      body: JSON.stringify(body),
    });
  },
  recuse(reviewCaseId: string, body: ReviewCaseRecusal): Promise<GovernanceReviewCase> {
    return request(`/api/v1/governance/review-cases/${encodeURIComponent(reviewCaseId)}/recuse`, {
      method: "POST",
      headers: mutationHeaders(),
      body: JSON.stringify(body),
    });
  },
  decide(reviewCaseId: string, body: ReviewCaseDecision): Promise<unknown> {
    return request(
      `/api/v1/governance/review-cases/${encodeURIComponent(reviewCaseId)}/decision`,
      { method: "POST", headers: mutationHeaders(), body: JSON.stringify(body) },
    );
  },
  approve(reviewCaseId: string, body: ReviewCaseApproval): Promise<unknown> {
    return request(
      `/api/v1/governance/review-cases/${encodeURIComponent(reviewCaseId)}/approve`,
      { method: "POST", headers: mutationHeaders(), body: JSON.stringify(body) },
    );
  },
  respond(reviewCaseId: string, body: ReviewResponseRequest): Promise<unknown> {
    return request(
      `/api/v1/governance/review-cases/${encodeURIComponent(reviewCaseId)}/response`,
      { method: "POST", headers: mutationHeaders(), body: JSON.stringify(body) },
    );
  },
  dispute(reviewCaseId: string, body: DisputeOpenRequest): Promise<GovernanceReviewCase> {
    return request(
      `/api/v1/governance/review-cases/${encodeURIComponent(reviewCaseId)}/disputes`,
      { method: "POST", headers: mutationHeaders(), body: JSON.stringify(body) },
    );
  },
};
