let accessToken: string | null = null;
let redirectingToLogin = false;

export const OIDC_ENABLED = process.env.NEXT_PUBLIC_AUTH_MODE === "oidc";

export function getAccessToken(): string | null {
  return accessToken;
}

export function setAccessToken(token: string | null) {
  accessToken = token;
}

export function websocketProtocols(ticket: string): string[] {
  return ["campusvoice", `campusvoice.ticket.${ticket}`];
}

export function handleUnauthorized(apiBaseUrl: string) {
  setAccessToken(null);
  window.dispatchEvent(new CustomEvent("campusvoice:unauthorized"));
  if (redirectingToLogin) return;
  const redirectUrl = loginRedirectForUnauthorized(apiBaseUrl, window.location.href);
  if (!redirectUrl) return;
  redirectingToLogin = true;
  window.location.assign(redirectUrl);
}

export function loginRedirectForUnauthorized(
  apiBaseUrl: string,
  currentUrl: string,
  oidcEnabled = OIDC_ENABLED,
): string | null {
  if (!oidcEnabled || new URL(currentUrl).searchParams.has("auth_error")) return null;
  return `${apiBaseUrl}/api/auth/login`;
}
