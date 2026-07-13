# ADR 0002: User-scoped privacy export, retention, and deletion

## Status

Accepted for the v0.2 internal-test boundary.

## Decision

CampusVoice keeps the stable SSO-derived `users` row as the account boundary. Authenticated users
can export their own business data, run bounded retention cleanup, or clear their business data.
Data clearing removes settings, tasks, calendar entries, documents and chunks, voice metadata and
transcripts, corrections, conversation context, pending actions, action logs, undo records, and
outstanding action/WebSocket credentials. It does not delete the SSO identity row.

The clear operation uses a dedicated server-issued opaque challenge. The database stores only its
SHA-256 digest together with user, fixed `business_data` scope, expiry, and consumption timestamp.
Confirmation atomically consumes the challenge before deleting data, which prevents replay and
cross-user use. A fresh session verifies that the identity remains and every scoped table is empty.

Exports are explicit allowlists. They omit vector embeddings, internal storage paths,
confirmation history/nonces, WebSocket tickets, credentials, and secret-like keys nested in JSON.
Sensitive responses use `Cache-Control: no-store`.

Retention is user-scoped and deletes only records older than configured windows. Pending actions
are eligible only after reaching executed, cancelled, undone, or expired states. Active operations
are retained. Expired WebSocket tickets and generic write/deletion challenges are also removed;
live credentials remain. Raw audio persistence remains unsupported and startup rejects attempts to
enable it.

## Consequences

- Logical deletion is immediate in the application database, but SQLite free pages, WAL files, and
  backups require a separate operational retention and secure-erasure policy.
- A later account-deletion feature must coordinate with the campus identity provider; it must not
  silently reuse the business-data clear endpoint.
- Retention runs are explicit in v0.2. A future scheduler may call the same idempotent service.
