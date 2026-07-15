import { randomUUID } from "node:crypto";

const [mode, rawApiBaseUrl, actionId] = process.argv.slice(2);
const apiBaseUrl = (rawApiBaseUrl ?? "http://127.0.0.1:8000").replace(/\/$/, "");

async function request(path, init = {}) {
  const response = await fetch(`${apiBaseUrl}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init.headers },
    signal: AbortSignal.timeout(30_000),
  });
  const text = await response.text();
  if (!response.ok) {
    throw new Error(`${init.method ?? "GET"} ${path} returned ${response.status}: ${text}`);
  }
  return text ? JSON.parse(text) : null;
}

async function createSentinel() {
  const runId = randomUUID();
  const prepared = await request("/api/actions/prepare", {
    method: "POST",
    body: JSON.stringify({
      action: "create_task",
      payload: {
        title: `compose-persistence-sentinel-${runId}`,
        description: "Must survive a forced API container recreation",
      },
      idempotency_key: `compose-persistence-${runId}`,
    }),
  });

  let action = prepared;
  for (
    let confirmation = 0;
    confirmation < 2 &&
    (action.state === "awaiting_confirmation" || action.state === "awaiting_second_confirmation");
    confirmation += 1
  ) {
    const challenge = await request(`/api/actions/${action.id}/challenge`, { method: "POST" });
    action = await request(`/api/actions/${action.id}/confirm`, {
      method: "POST",
      body: JSON.stringify({ confirmed: true, challenge: challenge.challenge }),
    });
  }
  if (action.state !== "ready") {
    throw new Error(`sentinel action did not become ready: ${action.state}`);
  }

  const executed = await request(`/api/actions/${action.id}/execute`, { method: "POST" });
  if (!executed.success || !executed.record_id) {
    throw new Error(`sentinel action did not execute successfully: ${JSON.stringify(executed)}`);
  }
  process.stdout.write(action.id);
}

async function verifySentinel(id) {
  if (!id) {
    throw new Error("verify mode requires an action id");
  }
  const action = await request(`/api/actions/${id}`);
  if (action.state !== "executed" || !action.target_id) {
    throw new Error(`sentinel action was not durably executed: ${JSON.stringify(action)}`);
  }

  const tasks = await request("/api/tasks?limit=500");
  const task = tasks.items.find((item) => item.id === action.target_id);
  if (!task || task.title !== action.payload.title) {
    throw new Error(`sentinel task ${action.target_id} was not found after container recreation`);
  }
  process.stdout.write(`${id}\n`);
}

if (mode === "create") {
  await createSentinel();
} else if (mode === "verify") {
  await verifySentinel(actionId);
} else {
  throw new Error(
    "usage: node scripts/check_compose_persistence.mjs create|verify [api-url] [action-id]",
  );
}
