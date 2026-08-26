import type {
  AuthenticatedUser as TransportUser,
  RegistrationResponse as TransportRegistration,
  SessionResponse as TransportSession,
  SessionState as TransportSessionState,
} from "@/lib/generated/client/types.gen";
import type {
  AuthenticatedUser,
  RegistrationResponse,
  SessionResponse,
  SessionState,
} from "@/lib/types";

export function authenticatedUser(value: TransportUser): AuthenticatedUser {
  return {
    id: value.id,
    email: value.email,
    onboarding_completed: value.onboarding_completed ?? false,
    preferred_units: value.preferred_units ?? "metric",
  };
}

export function sessionResponse(value: TransportSession): SessionResponse {
  return { user: authenticatedUser(value.user), csrf_token: value.csrf_token };
}


export function registrationResponse(value: TransportRegistration): RegistrationResponse {
  return {
    ...sessionResponse(value),
    recovery_code: value.recovery_code,
  };
}

export function sessionState(value: TransportSessionState): SessionState {
  return {
    authenticated: value.authenticated,
    user: value.user ? authenticatedUser(value.user) : null,
  };
}
