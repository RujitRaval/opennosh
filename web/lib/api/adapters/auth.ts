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
    onboarding_completed: value.onboarding_completed ?? true,
    recovery_configured: value.recovery_configured ?? true,
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
  const legacy = value as unknown as TransportUser;
  if (typeof legacy.id === "string" && typeof legacy.email === "string") {
    return { authenticated: true, user: authenticatedUser(legacy) };
  }
  return {
    authenticated: value.authenticated,
    user: value.user ? authenticatedUser(value.user) : null,
  };
}
