# ADR 0007: Evidence-backed notice versions and atomic impact migrations

- Status: Accepted
- Date: 2026-07-13
- Scope: CampusVoice v0.3

## Context

普通 RAG 问答只能回答“通知写了什么”，不能可靠回答“通知的下一版改变了什么、哪些个人安排依赖旧事实、如何安全落实并撤销”。直接让 LLM 修改任务或日程还会失去证据、幂等、用户隔离和事务语义。

## Decision

### Explicit version identity

`NoticeSeries` is user-scoped and identified by an explicit `canonical_key`. A successor document must name the current `supersedes_document_id` and use a monotonically increasing `revision_number`. Title similarity may suggest a match in a future UI, but never silently creates a chain. Duplicate content and duplicate revisions are idempotent or rejected with stable conflict codes.

### Claims and evidence

Each `NoticeClaim` stores the document, chunk, Unicode code-point evidence interval, raw value, deterministic normalized value, audience rule, confidence, review state, and extractor version. Dates, times, time zones, reminders, and comparable text are normalized deterministically. Text comparison applies Unicode NFKC, full-width punctuation folding, whitespace normalization and case folding without changing the original evidence offsets; `2024`, `2024级`, `2024 级` and full-width variants normalize to the same grade. Cosmetic text outside supported claims does not create a semantic change. A model may propose claims or alignments, but it has no authority to update tasks, events, plans, or execution state.

`NoticeChangeSet` is immutable per document pair and algorithm version. Re-analysis creates or selects a versioned result and never silently overwrites reviewed history. Confidence below the deterministic threshold, missing evidence, uncertain applicability, or an ambiguous version relationship blocks propagation until review.

### Idempotent impact propagation

`ImpactCase` links one reviewed change item to one user-owned task or calendar event and has a unique `(user_id, change_item_id, entity_type, entity_id)` key. Applicability is evaluated against the authenticated user's major, normalized grade, and courses. Automatic migration requires either `source_claim_id == before_claim.id` or equality between the entity's current claim-specific business field and the before claim's normalized value. Sharing an old `source_document_id` is never sufficient, so a manual edit is not overwritten and a location change cannot be inferred from a matching start time. A source document, chunk, and claim must form one user-owned lineage; cross-user and mismatched lineage is rejected.

Tasks and events retain the current primary source plus `source_history`. Unchanged same-key primary claims may roll from v1 to v2; supporting claims are appended to history but do not replace the primary claim. Preview and apply call the same dependency rule. A migration item keeps its before snapshot, proposed patch, after snapshot, and all supporting new claim IDs, so moving to a new source never destroys the old evidence chain.

Audience de-scoping and removed claims do not disappear as zero impacts. They produce explicit `keep`, `cancel`, or `manual_review` recommendations with a `requires_manual_review` flag. An empty patch that needs judgment cannot be counted as an automatically applied migration.

### Atomic migration and undo

One `ImpactMigrationPlan` generation groups one item per affected entity. Preview reads optimistic entity versions and computes a stable-sorted calendar-conflict set without mutating business data. Identical repeated previews may reuse the same ready generation; rejected review invalidates the plan and dismisses its impacts, re-approval clears stale links, and a later preview creates the next generation. `undone` and `invalidated` generations are immutable history and cannot be reused.

Execution uses a conditional status/version update to claim the plan and then, inside the transaction and lock, re-checks that the change remains approved, impacts remain executable, v2 still applies to the user, entity versions and old-claim dependencies remain current, and calendar conflicts equal the preview. A changed conflict set returns `calendar_conflicts_changed`, restores or retains `ready`, releases the failed execution key, and writes no entity. All entity writes, impact resolution, source changes, and snapshots occur in one database transaction. Any failure rolls the whole transaction back. Independent sessions with different keys cannot both claim the same ready plan, while a repeated request with the same key returns or resumes the same operation.

The existing server-issued write challenge is extended, not bypassed. It binds authenticated user, method, path, canonical body hash, stage, and expiry. Ordinary bundles use one stage. Conflict override and group undo use two independent stages and two UI interactions: the first interaction issues and advances the challenge without a business mutation, and the second sends the final bound stage. The generic mutation helper refuses to auto-loop a two-stage initial write. Challenge rows are hash-only and atomically consumed, so replay, payload tampering, and cross-user use fail.

After commit, a new SQLAlchemy session re-queries every affected row. The plan remains `applied` until that verification succeeds; if the process stops in this window, the same execution key resumes only verification and does not apply patches again. Undo uses the symmetric `undo_applied` recovery state. Before recovery, the web client re-fetches the plan and permits the recovery helper only for `applied|verification_failed` or `undo_applied|undo_verification_failed`; `verified|undone` reads the receipt, while `ready` requires fresh user confirmation. This state gate is what makes automatic challenge reconstruction safe for verification recovery without weakening initial two-stage writes. The API reports success only when all expected fields and sources match. Verification failure is a distinct state and never renders a success receipt.

Execution and undo evidence is append-separated: plans keep `execute_receipt_json` and `undo_receipt_json`, while items keep `execute_verification_json` and `undo_verification_json`. Each receipt names the operation and time and includes the expected snapshot plus the database snapshot from the fresh session. Recovery or undo never overwrites the earlier operation's receipt.

Group undo conditionally claims the executed plan, verifies every entity still has the post-migration version, restores all before snapshots in one transaction, reopens the impacts, and then verifies all rows from another session. A concurrent/manual edit blocks the entire undo rather than partially restoring data.

### Privacy and retention

Notice text remains in the existing document/chunk retention boundary. Claims keep only supported structured values and precise evidence coordinates. Migration/audit snapshots contain the minimum task/event fields needed for verification and undo. Logs never include full notice text, tokens, secrets, or unnecessary student attributes. All reads and writes derive the user from authentication and return not-found for foreign IDs.

The privacy export exposes each notice entity through an explicit field allowlist. It recursively removes secret-like JSON keys, excludes embeddings, storage paths, migration execution/undo idempotency keys, and strips credentials, query strings, and fragments from source URLs. Business-data clearing counts every v0.3 notice table before mutation, deletes the graph explicitly from leaf rows to roots in the same transaction, and re-counts it from a new session before reporting success. The stable SSO identity remains, while another user's graph is unaffected. Logical deletion still follows the physical SQLite/WAL and backup lifecycle in the privacy runbook.

## Consequences

- SQLite relations are sufficient for the MVP; no graph database is introduced.
- The first extractor intentionally covers only dates/times, locations, deadlines, audience, materials, requirements, and reminders.
- The system may recommend and preview changes but never writes without user confirmation.
- The Radar UI exposes four card types, v1/v2 evidence, a real calendar preview, migration generation and failure recovery; explicit series/version import requires predecessor confirmation when ambiguous.
- External campus systems, crawlers, and autonomous application submission remain out of scope.
