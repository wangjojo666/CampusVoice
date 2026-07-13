# ADR 0005: Lease-based shared ASR concurrency quota

## Status

Accepted for the v0.3 campus pilot.

## Decision

Single-worker deployments retain the process-local registry. Any configured worker count greater
than one must select the Redis backend, provide a Redis URL, and inject the same confirmation
secret of at least 32 characters into every worker; configuration otherwise fails closed. This
prevents separately generated process-local HMAC keys from making write-challenge issue, advance,
and consume requests fail nondeterministically behind a load balancer. The multi-worker Compose
override passes that count to Uvicorn and waits for Redis health. The synthetic smoke override uses
an explicit test-only shared secret; production must use a secret-manager value.
The Redis registry hashes the internal user ID into a bounded key and uses one Lua invocation to
read Redis time, remove expired leases, check the sorted-set cardinality, and add a random lease
atomically. Lease expiry is the maximum ASR session duration plus a grace interval, so
a crashed worker releases capacity without operator action. Normal WebSocket cleanup removes the
exact lease idempotently.

Redis unavailability during startup fails readiness/startup rather than silently giving each worker
its own quota, and runtime loss makes readiness fail. Redis stores only hashed user keys and random lease IDs; it does not receive audio,
transcripts, access tokens, or campus identifiers.

## Consequences

- The shared quota is correct across workers and resilient to worker crashes, at the cost of Redis
  and a shared confirmation secret being required for a multi-worker deployment.
- The local backend is an explicit single-process degradation mode, not an automatic production
  fallback.
- CI exercises two independent registry instances against a real Redis service.
