import { expect, test, type APIResponse } from "@playwright/test";

const apiBaseUrl = process.env.CAMPUSVOICE_SMOKE_API_URL ?? "http://127.0.0.1:8000";

async function expectOk(response: APIResponse): Promise<void> {
  expect(
    response.ok(),
    `${response.url()} returned ${response.status()}: ${await response.text()}`,
  ).toBe(true);
}

test("real AI-disabled demo stack persists, retrieves, renders, and undoes data", async ({
  page,
  request,
}) => {
  const runId = `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const taskTitle = `compose-smoke-task-${runId}`;
  const knowledgePhrase = `campusvoice lexical smoke ${runId}`;

  const ready = await request.get(`${apiBaseUrl}/health/ready`);
  await expectOk(ready);
  expect((await ready.json()).status).toBe("ok");

  const prepared = await request.post(`${apiBaseUrl}/api/actions/prepare`, {
    data: {
      action: "create_task",
      payload: { title: taskTitle, description: "Docker Compose smoke test" },
      idempotency_key: `compose-${runId}`,
    },
  });
  await expectOk(prepared);
  const pendingAction = await prepared.json();
  expect(pendingAction.state).toBe("awaiting_confirmation");

  const issued = await request.post(`${apiBaseUrl}/api/actions/${pendingAction.id}/challenge`);
  await expectOk(issued);
  const confirmation = await issued.json();

  const confirmed = await request.post(`${apiBaseUrl}/api/actions/${pendingAction.id}/confirm`, {
    data: { confirmed: true, challenge: confirmation.challenge },
  });
  await expectOk(confirmed);
  expect((await confirmed.json()).state).toBe("ready");

  const executed = await request.post(`${apiBaseUrl}/api/actions/${pendingAction.id}/execute`);
  await expectOk(executed);
  const execution = await executed.json();
  expect(execution.success).toBe(true);
  expect(execution.record.title).toBe(taskTitle);

  const tasks = await request.get(`${apiBaseUrl}/api/tasks`);
  await expectOk(tasks);
  expect((await tasks.json()).items).toEqual(
    expect.arrayContaining([expect.objectContaining({ title: taskTitle })]),
  );

  await page.goto("/tasks");
  await expect(page.getByText(taskTitle, { exact: true })).toBeVisible();

  const uploaded = await request.post(`${apiBaseUrl}/api/documents`, {
    multipart: {
      file: {
        name: `compose-smoke-${runId}.txt`,
        mimeType: "text/plain",
        buffer: Buffer.from(`${knowledgePhrase}. This notice is exercised by the real stack.`),
      },
      title: `Compose smoke notice ${runId}`,
      department: "CI",
      version: "smoke-v1",
    },
  });
  await expectOk(uploaded);
  expect((await uploaded.json()).chunk_count).toBeGreaterThan(0);

  const searched = await request.post(`${apiBaseUrl}/api/knowledge/search`, {
    data: { query: knowledgePhrase, top_k: 5, min_similarity: 0 },
  });
  await expectOk(searched);
  const searchResult = await searched.json();
  expect(searchResult.results.length).toBeGreaterThan(0);
  expect(searchResult.results[0].original_text).toContain(knowledgePhrase);

  const undone = await request.post(`${apiBaseUrl}/api/actions/${pendingAction.id}/undo`);
  await expectOk(undone);
  expect((await undone.json()).success).toBe(true);

  const tasksAfterUndo = await request.get(`${apiBaseUrl}/api/tasks`);
  await expectOk(tasksAfterUndo);
  expect((await tasksAfterUndo.json()).items).not.toEqual(
    expect.arrayContaining([expect.objectContaining({ title: taskTitle })]),
  );
});
