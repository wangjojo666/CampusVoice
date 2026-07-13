# ADR 0004: Server-side campus OIDC Authorization Code with PKCE

## Status

Accepted for the v0.3 campus pilot.

## Decision

CampusVoice adds `oidc` beside the existing `demo` and bearer `jwt` adapters. The API is the OIDC
relying party. It discovers the campus provider, generates one-time `state`, `nonce`, and PKCE
`code_verifier` values, stores only an opaque flow handle in an `HttpOnly` cookie, and performs the
code exchange itself. A configured client secret is sent only from the API to the token endpoint.

The callback atomically consumes the flow before exchanging the code, validates the provider
issuer, JWKS signature, audience, expiry, required claims, and nonce, then maps issuer/subject to the
existing internal user ID. The browser never receives or persists the access token. It receives an
opaque `HttpOnly`, `Secure` production session cookie scoped to `/api`; its hash and bounded expiry
are stored in SQLite. Logout revokes that session and returns the provider logout URL when
advertised. Expired or
revoked sessions produce `401`; the OIDC web build redirects those responses to `/api/auth/login`.
Provider callback descriptions are not reflected to the browser; only bounded error codes are.
Logout additionally requires an explicitly configured browser `Origin`, preventing a sibling-site
form or fetch from revoking the current session.
Authentication and callback responses are `no-store` and use `Referrer-Policy: no-referrer`, so
consumed authorization response parameters are not forwarded to the post-login page.

## Consequences

- Production demo mode remains forbidden. Selecting OIDC without complete HTTPS endpoints,
  `openid` scope, client ID, issuer, and asymmetric ID-token algorithms fails startup.
- The web app and API must be deployed on the same site (normally behind one HTTPS reverse proxy)
  so `SameSite=Lax` cookies and explicit credentialed CORS remain valid.
- The ingress must rate-limit `/api/auth/login` and `/api/auth/callback`; expired flow cleanup is not
  a substitute for denial-of-service protection.
- Session revocation is local. Coordinated campus single logout depends on the provider publishing
  `end_session_endpoint`; otherwise CampusVoice still clears its local session.
- Bearer JWT mode remains available for non-browser API clients and v0.2 compatibility.
