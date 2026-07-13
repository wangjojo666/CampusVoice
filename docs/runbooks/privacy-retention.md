# Privacy retention, SQLite WAL, backup, and retry runbook

## Schedule and execute

Apply Alembic migrations first. In the locked Python 3.11 environment, prefer one external
scheduled process:

```powershell
Set-Location services/api
python -m app.jobs.retention
```

The command exits nonzero after `CAMPUSVOICE_RETENTION_SCHEDULER_MAX_RETRIES` retries, with delays
starting at `CAMPUSVOICE_RETENTION_SCHEDULER_RETRY_BASE_SECONDS` and doubling each time. Its JSON
stdout contains only processed-user and per-table aggregate counts. Schedule only one instance.
The API deliberately has no in-process timer: create exactly one cron, Task Scheduler job, systemd
timer, or Kubernetes CronJob. Treat overlapping executions as an alert even though deletes are
idempotent, because they add SQLite lock contention.

## Failure response

1. Preserve the request ID, timestamp, exception class, database free space, and readiness output;
   do not copy user text, tokens, cookies, OIDC verifier/nonce values, or database rows into tickets.
2. Confirm no second scheduler is active. Check filesystem permissions, disk space, database lock
   holders, and `PRAGMA integrity_check` before retrying manually.
3. Re-run the same one-shot command. Retention deletes are idempotent, so a prior partial user set
   may safely be processed again.
4. After the configured retries fail, stop automated retries, alert the data owner, and preserve the
   failed backup/DB unchanged for controlled recovery. Do not `VACUUM`, delete WAL files, or replace
   the database while writers are active.

The business-data clear endpoint is broader than age-based retention. Its verified scope includes
the complete v0.3 notice graph (`notice_series`, `notice_claims`, `notice_change_sets`,
`notice_change_items`, `impact_cases`, `impact_migration_plans`, and
`impact_migration_items`) together with source tasks/events and documents. A success response means
a fresh database session found zero current-user rows in every scoped table while preserving the
SSO identity; it does not mean historical backups have already expired.

## WAL and physical cleanup

Application connections use WAL. Logical deletion can remain in free pages and WAL frames. During
a maintenance window, stop API writers, verify a current restorable backup, then run through a
trusted SQLite client:

```sql
PRAGMA wal_checkpoint(TRUNCATE);
PRAGMA integrity_check;
PRAGMA secure_delete=ON;
VACUUM;
PRAGMA wal_checkpoint(TRUNCATE);
PRAGMA integrity_check;
```

Never delete `-wal` or `-shm` files by hand. A busy checkpoint means a reader/writer is still active;
the first integer returned by `wal_checkpoint` must be `0`, and the final checkpoint should report
no remaining log frames. Identify active readers/writers or reschedule the window otherwise.
`VACUUM` needs temporary free space roughly comparable to the database and should not run on every
daily retention cycle.

## Backup and restore

- Create a consistent online snapshot with the bundled utility; its destination must not exist and
  its JSON result records SHA-256, size, and `integrity_check`:

  ```powershell
  python -m app.jobs.sqlite_backup create C:\campusvoice\campusvoice.db `
    E:\encrypted-backups\campusvoice-2026-07-13.db
  python -m app.jobs.sqlite_backup verify `
    E:\encrypted-backups\campusvoice-2026-07-13.db
  ```

  This uses SQLite's online backup API and includes committed WAL content. A plain copy of only the
  main file in WAL mode is unsafe.

- Pilot default: encrypted daily backups retained 7 days, weekly restore points retained 28 days,
  RPO at most 24 hours, and RTO at most 4 hours. The campus data owner must approve any longer
  retention before launch. A privacy deletion is not complete across history until every containing
  backup expires or is securely destroyed under policy.
- For a restore drill, stop API writers, verify the chosen backup, use the same `create` command with
  the backup as source and a new isolated restore path as destination, point
  `CAMPUSVOICE_DATABASE_URL` at that path, then run `alembic current`, `/health/ready`, and a
  documented sample read. Include a user-scoped sample across the notice series/claim/change/impact
  graph so a restore cannot appear healthy while omitting v0.3 tables or foreign-key edges. Test
  quarterly and after migration or storage changes; record start/end timestamps to prove RTO.
- Never restore an older backup over the live database without first isolating the live files and
  accounting for data created after the backup.
