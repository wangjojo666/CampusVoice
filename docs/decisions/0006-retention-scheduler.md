# ADR 0006: Schedulable privacy retention executor

## Status

Accepted for the v0.3 campus pilot.

## Decision

The existing user-scoped, idempotent retention service is wrapped by a one-shot executor with
bounded exponential retry. Operators schedule `python -m app.jobs.retention` from cron, systemd,
Task Scheduler, or a singleton Kubernetes CronJob. It processes every account sequentially,
removes expired OIDC transactions/sessions, and emits aggregate counts without user IDs.

The executor retries the complete idempotent run after transient failures. The API does not start a
hidden per-worker timer, so scaling API replicas cannot accidentally duplicate the schedule.
Operational WAL checkpointing, online backups, restore verification, retry escalation, and
secure-erasure limits are defined in the privacy retention runbook.

## Consequences

- Scheduling is deployment-controlled and observable; application startup does not unexpectedly
  delete data.
- The deployment must create exactly one external schedule; overlapping accidental runs remain
  logically idempotent but can add SQLite lock contention and are an alert condition.
- Logical retention does not by itself erase SQLite free pages, WAL remnants, or older backups.
