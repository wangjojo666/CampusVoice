# ADR 0003: Authenticated user boundary and server-issued challenges

## Status

Accepted for the v0.2 internal-test boundary.

## Context

The original local MVP selected a fixed `user_demo` and treated client-generated confirmation
values and boolean headers as proof of confirmation. That is useful for a local prototype but is
not an identity or authorization boundary, and independent client values cannot prevent replay,
cross-user use, or confirmation of a changed payload.

Long-lived bearer tokens must also not be placed in a WebSocket URL, log, or database. High-risk
deletes need two visible user interactions; an API helper must not silently complete both stages.

## Decision

- Development and tests may explicitly choose `demo` authentication. Production requires `jwt`
  and fails startup unless issuer, audience, JWKS URL, asymmetric algorithms, Alembic-managed
  schema, and a sufficiently long confirmation secret are configured.
- The JWT adapter verifies signature, issuer, audience, expiry and required claims. It derives an
  internal user ID from the verified issuer and subject. Every REST repository operation uses this
  server-derived ID; client-provided user selectors are ignored or unsupported.
- The browser exchanges its in-memory bearer token for a short-lived WebSocket ticket bound to the
  current user and allowed Origin. The raw ticket is returned once and sent as a WebSocket
  subprotocol value; only its SHA-256 digest is stored and consumption is atomic.
- Reliable Actions use an HMAC-authenticated challenge bound to user, action, canonical action
  fingerprint, confirmation stage, nonce and expiry. The database stores only nonce hashes and a
  unique action/stage record.
- Direct task, event, settings and hotword mutations use an opaque write challenge stored only as a
  hash and bound to user, method, path, canonical JSON body, flow, stage and expiry. A conditional
  database update atomically consumes the final stage before the mutation executes.
- High-risk deletes do not advance both stages inside the API client. The first interaction consumes
  stage one and returns without deleting; a later interaction submits the separately issued final
  stage. Replay, expiry, cross-user use, payload/path changes and concurrent duplicate consumption
  all fail closed.

## Consequences

- A stolen valid bearer token still grants its normal session authority; these challenges provide
  intent freshness and replay resistance, not phishing or endpoint-compromise protection. The
  campus identity provider remains responsible for MFA, token lifetime and revocation.
- Process restarts invalidate unpersisted development confirmation secrets. Production must inject
  a stable secret through its secret manager, never through source control.
- The current frontend exposes an in-memory token setter as an integration seam. A future campus
  OIDC authorization-code + PKCE flow should populate that seam without introducing localStorage
  token persistence.
