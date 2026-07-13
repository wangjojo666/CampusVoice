# Persistence and reliable-action API contract

The generated OpenAPI document at `/openapi.json` is the field-level source of truth. This note
records the behavioral contract that is not fully expressible in JSON Schema.

## Notice version radar and impact migrations

All routes below use the authenticated current user and return `404` for foreign IDs. List endpoints are bounded and all request models forbid unknown fields.

- `POST/GET /api/notice-radar/series` creates or lists explicit notice series.
- `POST /api/notice-radar/series/{id}/versions` requires a revision number and, after v1, the exact current `supersedes_document_id`. It extracts deterministic claims and idempotently creates a change set.
- `GET /api/notice-radar/series/{id}/timeline` returns the ordered version chain.
- `GET /api/notice-radar/documents/{id}/claims` returns raw/normalized values plus chunk and Unicode evidence interval. `POST .../reanalyze` is version-preserving and idempotent.
- `GET /api/notice-radar/changes/{id}` returns before/after evidence, confidence, severity, and review state. `PATCH /changes/items/{id}/review` resolves uncertain items.
- `POST /changes/{id}/impacts/detect` and `GET /impacts` detect/list impacts without duplicating rows. Automatic patches require the exact old claim ID or a claim-specific business value that still equals the normalized before value; an old document ID alone never matches. Applicability transitions return `keep`, `cancel`, or `manual_review` plus `requires_manual_review` instead of disappearing.
- `POST /changes/{id}/migration-preview` freezes entity versions, proposed patches, primary/supporting sources, and stable-sorted calendar conflicts without changing tasks or events. It returns `generation`; an unchanged ready plan may be reused, while rejected/undone/invalidated work requires a later generation.
- `POST /migrations/{id}/execute` requires `plan_version`, `idempotency_key`, `allow_conflicts`, and `confirmation_stages`. A server challenge binds that exact body. The claimed transaction revalidates review, applicability, entity version, exact old-claim dependency and current conflicts before any write. Conflicts are blocked unless explicitly overridden through two separate UI interactions: issue/advance without mutation, then the final-stage business request.
- `GET /migrations/{id}` exposes both immutable operation receipts and item-level execute/undo verification. After a response interruption, the client first fetches the latest plan. Only `applied|verification_failed` or `undo_applied|undo_verification_failed` may repeat the corresponding POST with the same plan version and idempotency key; the service opens a fresh database session and resumes only verification. `verified|undone` reads `/receipt?operation=execute|undo`, while `ready` must repeat the normal confirmation flow. The receipt endpoint returns not-found before one exists.
- `POST /migrations/{id}/undo` requires the same separated two-stage interaction and restores the entire group before re-verifying it.

Stable failures include `version_confirmation_required`, `ambiguous_version_chain`, `change_review_required`, `calendar_conflict`, `calendar_conflicts_changed`, `migration_plan_stale`, `migration_not_executable`, `entity_version_conflict`, `migration_execution_conflict`, `migration_undo_conflict`, `invalid_source_lineage`, and `invalid_write_challenge`. A changed preview condition or mid-bundle exception leaves the group at zero business writes and does not permanently reserve a failed idempotency key.

## Campus OIDC browser session

- `GET /api/auth/login` creates a short-lived flow and redirects to the discovered authorization
  endpoint with `state`, `nonce`, and an S256 PKCE challenge.
- `GET /api/auth/callback` consumes the flow, handles bounded provider errors, exchanges the code
  from the API, validates the ID token and sets an opaque `HttpOnly` session cookie. Access tokens
  and client secrets are never returned.
- `GET /api/auth/session` returns the current bounded principal or `401` for missing, revoked, or
  expired sessions.
- `POST /api/auth/logout` revokes the local session, clears its cookie and returns the configured or
  discovered logout URL; it requires an `Origin` from the configured browser allowlist.

All OIDC responses are `no-store`. Production cookies are `Secure`; the supported browser
deployment is same-site HTTPS behind a reverse proxy.

## Permission boundary and errors

Every `/api/**` business endpoint resolves `AuthPrincipal` from the configured authenticator.
Development and tests may explicitly use demo auth. Production forbids demo. Browser OIDC verifies
state, PKCE, nonce, issuer, audience, JWKS signature, expiry and required claims before mapping
issuer/subject to an opaque server-side session; Bearer JWT remains available for API clients.
internal user ID. Endpoints do not accept `user_id` in a path, query, body or trusted ad-hoc header;
all repository lookups are constrained by the server-derived current user. Cross-user object access
uses the same `404` result as a missing object.

Domain errors use this shape:

```json
{
  "error": {
    "code": "invalid_action_state",
    "message": "Action must receive all required confirmations before execution",
    "details": { "state": "awaiting_confirmation" }
  }
}
```

Common status codes are `401` for missing/invalid Bearer credentials (with `WWW-Authenticate:
Bearer`), `403` for an inactive user or rejected Origin, `404` for a user-scoped missing record,
`409` for duplicate/version/challenge/state conflicts, `422` for strict schema failures, `428` when
confirmation work remains, and `500` when a committed write cannot be verified. Every error carries
a request ID. A verification error never contains `success: true`.

## Authentication and request-bound write challenges

- `POST /api/auth/ws-ticket` requires the authenticated REST session and an allowed `Origin`. It
  returns a short-lived opaque ticket. The raw ticket is sent once in WebSocket subprotocol
  `campusvoice.ticket.<ticket>`; the database stores only its SHA-256 digest. It is bound to user and
  Origin and is consumed once before `/ws/asr` accepts a session. Responses carrying short-lived
  tickets or challenges include `Cache-Control: no-store` and `Pragma: no-cache`.
- `POST /api/auth/write-challenges` accepts `{method, path, body}` for an allowlisted direct mutation
  and returns `{challenge, stage, required_stages, expires_at}`. The server binds user, normalized
  method/path, canonical JSON body hash, flow, stage and expiry; only a token hash is stored.
- A one-stage mutation sends the returned opaque value in `X-Write-Challenge`. The server re-hashes
  the actual request body and atomically consumes the matching final stage before business logic.
- `POST /api/auth/write-challenges/advance` consumes a non-final stage and returns the next stage.
  Hotword deletion requires two stages. The web UI performs issue/advance on the first click without
  deleting, then sends stage two only after a second click. Replays, cross-user use, expiry, request
  changes and concurrent duplicate consumption return `409 invalid_write_challenge`.

## Tasks, events and hotwords

- `GET /api/tasks` returns `{items: TaskView[], total}` and accepts `status`, `course`, `limit`,
  and `offset`.
- `POST /api/tasks` and `PATCH /api/tasks/{id}` require a matching one-time write challenge. They return a
  verified mutation object containing `success`, `action`, `record_id`, per-field
  `verified_fields`, detected `side_effects`, `message`, and the re-queried `record`.
- `DELETE /api/tasks/{id}` never deletes immediately. It returns `428` with a high-risk
  `pending_action` in `error.details`; the caller must use the two-step action endpoints.
- `GET /api/events` returns `{items: EventView[], total}` and accepts timezone-aware range filters,
  course and pagination.
- `POST /api/events` and `PATCH /api/events/{id}` use the same write-challenge and verified response
  contract. A normal overlap is blocked. An explicit `allow_conflict: true` becomes high risk and
  returns `428 confirmation_required` with a durable `pending_action`; callers continue that ID
  through two Action challenge/confirm interactions and then execute it.
- `DELETE /api/events/{id}` returns a high-risk pending action with `428`.
- `POST /api/events/check-conflict` accepts timezone-aware `start_at`, `end_at` and optional
  `exclude_event_id`; it returns `{has_conflict, conflicts}`.
- `GET /api/hotwords` returns `{items, total}`. Creation requires a one-stage write challenge.
  Deletion requires the two-stage write flow described above; both paths re-query the database
  before returning success.
- `GET /api/action-logs` returns `{items, total}` with optional `success` and pagination filters.
- `GET /api/settings` returns the single user's major, grade, current course/teacher context,
  default reminder, timezone and flat ASR provider/model/device fields. `PATCH /api/settings`
  accepts a strict partial update, requires a matching one-time write challenge, and returns
  `{success, verified_fields, message, settings}` after a post-commit re-query.

`Idempotency-Key` is accepted on task/event direct mutations. It must contain 8–120 characters.
Reusing it with the same canonical action returns the original pending action; using it for a
different payload returns `409 idempotency_key_reused`.

## Reliable action pipeline

`POST /api/actions/prepare` accepts:

```json
{
  "action": "create_event",
  "target_id": null,
  "target_title": null,
  "payload": {
    "title": "机器学习考试",
    "start_at": "2026-07-18T09:00:00+08:00",
    "location": "A302"
  },
  "asr_confidence": 0.92,
  "missing_fields": [],
  "ambiguities": [],
  "batch_size": 1,
  "overwrite_existing": false,
  "hard_to_undo": false,
  "idempotency_key": "voice-42-action-1",
  "source_text": "把机器学习考试加到日历",
  "corrected_text": "把机器学习考试加到日历",
  "voice_session_id": null,
  "transcription_id": null
}
```

Supported values are `create_task`, `update_task`, `delete_task`, `create_event`, `update_event`
and `delete_event`. Payload keys are validated by the corresponding strict draft schema; unknown
keys return `422`. The response is a `PendingActionView` containing the canonical payload, state,
deterministic risk result, blockers/diagnostics, TTL, confirmation counts and bounded attempts.
For update/delete, callers may send `target_title` instead of an internal ID. A unique user-scoped
match is resolved to its durable ID; zero or multiple matches remain `needs_input`, and multiple
matches are returned as safe candidate diagnostics. When a voice reference is supplied, the API
verifies that `transcription_id`, `voice_session_id`, and the current user belong to one chain.
Action logs preserve those IDs plus original and corrected text for both success and failure.

Mutation actions are at least medium risk. Medium risk needs one server-issued challenge; high risk
needs two independently issued stages and two separate UI interactions. Missing required fields,
unresolved ambiguity, duplicates, or non-overridden time
conflicts produce `needs_input`, which cannot be confirmed or executed.

- `POST /api/actions/{id}/challenge` signs and returns the next challenge bound to current user,
  action ID, canonical action fingerprint, stage, nonce and expiry. It is not an execution token.
- `POST /api/actions/{id}/confirm` accepts `{"confirmed": true, "challenge": "..."}`. The server
  verifies the signature and every binding, then stores only a unique nonce hash for action/stage.
  Replay, expiry, cross-user use, payload changes and concurrent consumption fail closed.
- `POST /api/actions/{id}/execute` accepts no body. Only `ready` can execute. It commits one
  transaction, then re-queries the database and checks fields, duplicates and event overlaps.
  The returned `ExecutionResult` has `success`, `action`, `record_id`, `verified_fields`,
  `side_effects`, `message`, `error`, `retryable`, `action_id` and the re-queried `record`.
- `POST /api/actions/{id}/cancel` accepts `{"reason": "..."}`.
- `POST /api/actions/{id}/undo` applies the recorded inverse transaction and independently verifies
  the final database state. Undo is available only for an executed, unexpired action.
- `GET /api/actions/{id}` returns the durable state for reconnect/recovery.

States are `needs_input`, `awaiting_confirmation`, `awaiting_second_confirmation`, `ready`,
`executing`, `executed`, `cancelled`, `failed`, `undone`, and `expired`. Terminal-state and retry
violations return `409`; write and verification retries are capped at `max_attempts`.

## Evidence-grounded campus knowledge

- `POST /api/documents` accepts PDF, DOCX, TXT, or Markdown plus title, department, publish date,
  applicable group, source URL, and version. Parsed chunks and optional embeddings are committed
  transactionally and then re-read.
- `POST /api/knowledge/search` accepts `query`, `top_k`, `min_similarity`, and optional `version`
  / `applicable_group` filters. Every result contains document/chunk IDs, original text, natural
  page number (or null), similarity, title, publication date, version, and applicable group.
- `POST /api/knowledge/ask` uses the same filters. Without an LLM it returns the retrieved original
  excerpts. With an OpenAI-compatible provider it requires strict JSON and numbered evidence on
  every answer line; invalid, missing, or out-of-range citations fail closed to original excerpts.
- Same-title multi-version or multi-group evidence is reported in `version_conflicts` and
  `applicability_conflicts`. Until the caller supplies filters that resolve the conflict,
  `sufficient_evidence` is false and the web UI disables conversion to a task or calendar event.

## Privacy export, retention, and deletion

- `GET /api/privacy/export` returns only the authenticated user's data and always sends
  `Cache-Control: no-store`. The export includes user-authored text and audit snapshots, but omits
  embeddings, storage paths, confirmation nonces/history, WebSocket tickets, access tokens, and
  application secrets.
- `POST /api/privacy/retention/run` applies the configured current-user retention windows to old
  transcriptions, correction records, conversations, terminal pending actions, and action logs.
  It also removes expired WebSocket/write/deletion challenges. Active pending actions and live
  credentials are never removed by this endpoint.
- `POST /api/privacy/deletion-challenges` issues a short-lived opaque challenge. Only its SHA-256
  digest is stored.
- `POST /api/privacy/deletion-challenges/{id}/confirm` requires the opaque challenge, the bound
  `business_data` scope, and the exact `DELETE_MY_DATA` confirmation phrase. Consumption is atomic,
  expires, and cannot be replayed or used by another user. The transaction clears business data,
  settings, action nonces, and WebSocket tickets, then re-queries the database to verify deletion.
  The stable SSO-backed `users` row remains so the account can continue to authenticate.

Raw audio persistence is not implemented. Setting `CAMPUSVOICE_STORE_RAW_AUDIO=true` is a startup
configuration error rather than silently enabling or ignoring raw audio storage.

## Health, logging, and metrics

- `GET /health/live` checks process liveness only. `GET /health/ready` checks database connectivity,
  current Alembic revision versus head, and configured component availability without loading or
  downloading models. `/api/health`, `/api/health/live`, and `/api/health/ready` are compatibility
  routes. Docker uses readiness.
- Every HTTP response has `X-Request-ID`. Request logs contain the request ID, route template,
  method, status, duration and a process-salted user digest when available. They omit headers,
  query values, request/response bodies, entity IDs, tokens, audio and exception text.
- `GET /api/metrics` exposes bounded, process-local aggregates for HTTP route templates and ASR,
  intent, retrieval, LLM, action execution and verification latency/error outcomes. Labels cannot
  contain user IDs, entity IDs, query text or provider names.
