import type {
  AuthenticatedUser as TransportUser,
  SessionResponse as TransportSession,
} from "@/lib/generated/client/types.gen";
import type { AuthenticatedUser, SessionResponse } from "@/lib/types";

export function authenticatedUser(value: TransportUser): AuthenticatedUser {
  return { id: value.id, email: value.email };
}

export function sessionResponse(value: TransportSession): SessionResponse {
  return { user: authenticatedUser(value.user), csrf_token: value.csrf_token };
}
