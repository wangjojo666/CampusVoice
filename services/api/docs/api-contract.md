# Persistence and reliable-action API contract

The generated OpenAPI document at `/openapi.json` is the field-level source of truth. This note
records the behavioral contract that is not fully expressible in JSON Schema.

## Permission boundary and errors

The MVP is single-user. The server resolves `CAMPUSVOICE_SINGLE_USER_ID`; none of these endpoints
accept `user_id` in a path, query or body. A client therefore cannot select another user's rows.

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

Common status codes are `404` for a user-scoped missing record, `409` for duplicate/version/state
conflicts, `422` for strict schema failures, `428` when confirmation work remains, and `500` when
a committed write cannot be verified. A verification error never contains `success: true`.

## Tasks, events and hotwords

- `GET /api/tasks` returns `{items: TaskView[], total}` and accepts `status`, `course`, `limit`,
  and `offset`.
- `POST /api/tasks` and `PATCH /api/tasks/{id}` require `X-User-Confirmed: true`. They return a
  verified mutation object containing `success`, `action`, `record_id`, per-field
  `verified_fields`, detected `side_effects`, `message`, and the re-queried `record`.
- `DELETE /api/tasks/{id}` never deletes immediately. It returns `428` with a high-risk
  `pending_action` in `error.details`; the caller must use the two-step action endpoints.
- `GET /api/events` returns `{items: EventView[], total}` and accepts timezone-aware range filters,
  course and pagination.
- `POST /api/events` and `PATCH /api/events/{id}` use the same confirmation and verified response
  contract. A normal overlap is blocked. An explicit `allow_conflict: true` becomes high risk and
  still requires the second action confirmation.
- `DELETE /api/events/{id}` returns a high-risk pending action with `428`.
- `POST /api/events/check-conflict` accepts timezone-aware `start_at`, `end_at` and optional
  `exclude_event_id`; it returns `{has_conflict, conflicts}`.
- `GET /api/hotwords` returns `{items, total}`. Creation requires `X-User-Confirmed: true`.
  Deletion requires both `X-User-Confirmed: true` and `X-Second-Confirmation: true`; both paths
  re-query the database before returning success.
- `GET /api/action-logs` returns `{items, total}` with optional `success` and pagination filters.
- `GET /api/settings` returns the single user's major, grade, current course/teacher context,
  default reminder, timezone and flat ASR provider/model/device fields. `PATCH /api/settings`
  accepts a strict partial update, requires `X-User-Confirmed: true`, and returns
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

Mutation actions are at least medium risk. Medium risk needs one unique confirmation token; high
risk needs two. Missing required fields, unresolved ambiguity, duplicates, or non-overridden time
conflicts produce `needs_input`, which cannot be confirmed or executed.

- `POST /api/actions/{id}/confirm` accepts
  `{"confirmed": true, "confirmation_token": "unique-client-token"}`. Replaying the same token is
  idempotent and never counts as the second confirmation. `confirmed: false` cancels the action.
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
