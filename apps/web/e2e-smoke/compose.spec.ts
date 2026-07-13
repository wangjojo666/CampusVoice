import { expect, test, type APIRequestContext, type APIResponse } from "@playwright/test";

const apiBaseUrl = process.env.CAMPUSVOICE_SMOKE_API_URL ?? "http://127.0.0.1:8000";

async function expectOk(response: APIResponse): Promise<void> {
  expect(
    response.ok(),
    `${response.url()} returned ${response.status()}: ${await response.text()}`,
  ).toBe(true);
}

async function confirmedWrite(
  request: APIRequestContext,
  method: "POST" | "PATCH",
  path: string,
  data: unknown,
  headers: Record<string, string> = {},
): Promise<APIResponse> {
  const issued = await request.post(`${apiBaseUrl}/api/auth/write-challenges`, {
    data: { method, path, body: data },
  });
  await expectOk(issued);
  let challenge = await issued.json();
  if (challenge.required_stages === 2) {
    const advanced = await request.post(`${apiBaseUrl}/api/auth/write-challenges/advance`, {
      data: { challenge: challenge.challenge },
    });
    await expectOk(advanced);
    challenge = await advanced.json();
  }
  return request.fetch(`${apiBaseUrl}${path}`, {
    method,
    data,
    headers: { ...headers, "X-Write-Challenge": challenge.challenge },
  });
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

test("real stack compiles a notice change, migrates, verifies, and group-undoes", async ({
  page,
  request,
}) => {
  const runId = `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const settings = await confirmedWrite(request, "PATCH", "/api/settings", {
    major: "人工智能",
    grade: "2024",
  });
  await expectOk(settings);
  const seriesResponse = await confirmedWrite(request, "POST", "/api/notice-radar/series", {
    canonical_key: `compose-ai-exam-${runId}`,
    title: `2026 人工智能专业考试安排 ${runId}`,
    department: "Compose smoke",
    source_key: `compose/${runId}`,
  });
  await expectOk(seriesResponse);
  const series = await seriesResponse.json();
  const versionPath = `/api/notice-radar/series/${series.id}/versions`;
  const v1Response = await confirmedWrite(request, "POST", versionPath, {
    title: `2026 人工智能专业考试安排 ${runId}`,
    content:
      "适用于 2024 级人工智能专业。\n考试时间：2026-07-18 09:00–11:00。\n地点：教学楼 A302。\n要求携带校园卡。",
    revision_number: 1,
    version_label: "v1",
    supersedes_document_id: null,
    applicable_group: "2024 级人工智能专业",
    ingest_source: "seed",
  });
  await expectOk(v1Response);
  const v1 = await v1Response.json();
  const source = v1.claims.find(
    (item: { claim_key: string }) => item.claim_key === "event.start_at",
  );
  const materialsSource = v1.claims.find(
    (item: { claim_key: string }) => item.claim_key === "required_materials",
  );
  expect(source).toBeTruthy();
  expect(materialsSource).toBeTruthy();

  const lineage = {
    source_type: "document",
    source_document_id: v1.id,
    source_chunk_id: source.chunk_id,
    source_claim_id: source.id,
  };
  const eventResponse = await confirmedWrite(
    request,
    "POST",
    "/api/events",
    {
      title: `人工智能专业考试 ${runId}`,
      start_at: "2026-07-18T09:00:00+08:00",
      end_at: "2026-07-18T11:00:00+08:00",
      location: "教学楼 A302",
      reminder_minutes: 1440,
      ...lineage,
    },
    { "Idempotency-Key": `compose-radar-event-${runId}` },
  );
  await expectOk(eventResponse);
  for (const [index, dueAt] of [
    "2026-07-17T20:00:00+08:00",
    "2026-07-18T08:00:00+08:00",
  ].entries()) {
    const taskResponse = await confirmedWrite(
      request,
      "POST",
      "/api/tasks",
      {
        title: `考试复习任务 ${index + 1} ${runId}`,
        due_at: dueAt,
        reminder_at: "2026-07-17T08:00:00+08:00",
        priority: "high",
        ...lineage,
      },
      { "Idempotency-Key": `compose-radar-task-${index}-${runId}` },
    );
    await expectOk(taskResponse);
  }
  const campusCardResponse = await confirmedWrite(
    request,
    "POST",
    "/api/tasks",
    {
      title: `携带校园卡参加人工智能专业考试 ${runId}`,
      description: "考试材料提醒：请在入场时携带校园卡。",
      due_at: "2026-07-18T09:00:00+08:00",
      reminder_at: "2026-07-18T08:00:00+08:00",
      priority: "high",
      source_type: "document",
      source_document_id: v1.id,
      source_chunk_id: materialsSource.chunk_id,
      source_claim_id: materialsSource.id,
    },
    { "Idempotency-Key": `compose-radar-campus-card-${runId}` },
  );
  await expectOk(campusCardResponse);

  const v2Response = await confirmedWrite(request, "POST", versionPath, {
    title: `2026 人工智能专业考试安排 ${runId}`,
    content:
      "适用于 2024 级人工智能专业同学。\n考试时间：2026-07-18 14:00–16:00。\n地点改为：教学楼 B205。\n请按时参加，要求携带校园卡。",
    revision_number: 2,
    version_label: "v2",
    supersedes_document_id: v1.id,
    applicable_group: "2024 级人工智能专业",
    ingest_source: "seed",
  });
  await expectOk(v2Response);

  const radarResponse = await request.get(`${apiBaseUrl}/api/notice-radar`);
  await expectOk(radarResponse);
  const radar = await radarResponse.json();
  const card = radar.items.find((item: { series_id: string }) => item.series_id === series.id);
  expect(card).toMatchObject({ affected_events: 1, affected_tasks: 3 });

  await page.goto(`/radar/${card.change_set_id}`);
  await expect(page.getByText("v1 → v2 结构化变化")).toBeVisible();
  await page.getByRole("button", { name: /生成迁移预览/ }).click();
  await expect(page.getByText("确认前预览，不会写入真实安排")).toBeVisible();
  await page.getByRole("button", { name: "确认迁移" }).click();
  await expect(page.getByText(/4\/4 项安排已更新并通过数据库验证/)).toBeVisible();
  await page.getByRole("button", { name: "撤销整个迁移" }).click();
  await page.getByRole("button", { name: /再次确认撤销整组迁移/ }).click();
  await expect(page.getByText(/4\/4 项安排已恢复并通过数据库验证/)).toBeVisible();
});
