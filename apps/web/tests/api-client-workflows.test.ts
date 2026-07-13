import type { PendingAction, UserSettings } from "@campusvoice/shared-types";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, api } from "@/lib/api-client";
import { setAccessToken } from "@/lib/auth";

function jsonResponse(body: unknown, status = 200, headers: HeadersInit = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...headers },
  });
}

function textResponse(body: string, status: number, statusText = "Request failed") {
  return new Response(body, {
    status,
    statusText,
    headers: { "content-type": "text/plain" },
  });
}

function wireAction(
  overrides: Partial<{
    id: string;
    action_type: PendingAction["action"];
    entity_type: "task" | "event";
    target_id: string | null;
    payload: Record<string, unknown>;
    state: PendingAction["status"];
    risk_level: PendingAction["risk_level"];
    risk_factors: string[];
    diagnostics: Record<string, unknown>;
    required_confirmations: number;
    confirmations_received: number;
  }> = {},
) {
  return {
    id: "action-1",
    action_type: "create_task",
    entity_type: "task",
    target_id: null,
    payload: { title: "复习机器学习" },
    state: "awaiting_confirmation",
    risk_level: "medium",
    risk_factors: ["writes_data"],
    missing_fields: [],
    ambiguities: [],
    blocking_reasons: [],
    diagnostics: {},
    required_confirmations: 1,
    confirmations_received: 0,
    expires_at: "2026-07-12T12:02:00Z",
    attempt_count: 0,
    max_attempts: 2,
    last_error: null,
    ...overrides,
  };
}

const settings: UserSettings = {
  major: "人工智能",
  grade: "2024级",
  current_courses: [{ id: "course-1", code: "AI301", name: "机器学习", teacher: "张老师" }],
  teacher_names: ["张老师"],
  default_reminder_minutes: 30,
  timezone: "Asia/Shanghai",
  asr_provider: "disabled",
  asr_model: "paraformer-zh-streaming",
  asr_device: "cpu",
};

describe("authenticated API workflows", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
    window.sessionStorage.clear();
    setAccessToken(null);
  });

  afterEach(() => {
    window.sessionStorage.clear();
    setAccessToken(null);
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("reuses a plan-bound migration idempotency key after a lost response", async () => {
    const plan = {
      id: "mpl-retry",
      change_set_id: "ncs-retry",
      status: "ready",
      risk_level: "low" as const,
      required_confirmations: 1 as const,
      conflicts: [],
      items: [],
      verification: {},
      execute_receipt: {},
      undo_receipt: {},
      generation: 1,
      version: 1,
      executed_at: null,
      undone_at: null,
    };
    const receipt = {
      plan_id: plan.id,
      status: "verified",
      operation: "execute",
      verified_count: 1,
      total_count: 1,
      all_verified: true,
      items: [],
      verified_at: "2026-07-13T01:00:00Z",
    };
    vi.mocked(fetch)
      .mockResolvedValueOnce(
        jsonResponse({
          challenge: "challenge-one",
          stage: 1,
          required_stages: 1,
          expires_at: "2026-07-13T01:05:00Z",
        }),
      )
      .mockResolvedValueOnce(jsonResponse(receipt))
      .mockResolvedValueOnce(
        jsonResponse({
          challenge: "challenge-two",
          stage: 1,
          required_stages: 1,
          expires_at: "2026-07-13T01:05:00Z",
        }),
      )
      .mockResolvedValueOnce(jsonResponse(receipt));

    await api.radar.execute(plan, false);
    await api.radar.execute(plan, false);

    const first = JSON.parse(String(vi.mocked(fetch).mock.calls[1]?.[1]?.body));
    const retried = JSON.parse(String(vi.mocked(fetch).mock.calls[3]?.[1]?.body));
    expect(first.idempotency_key).toMatch(/^[0-9a-f-]{36}$/i);
    expect(retried.idempotency_key).toBe(first.idempotency_key);
    expect(window.sessionStorage.getItem(`campusvoice:migration:execute:${plan.id}`)).toBe(
      first.idempotency_key,
    );
  });

  it("never auto-advances an initial high-risk migration write", async () => {
    const plan = {
      id: "mpl-high-risk-refused",
      change_set_id: "ncs-high-risk-refused",
      status: "ready",
      risk_level: "high" as const,
      required_confirmations: 2 as const,
      conflicts: [],
      items: [],
      verification: {},
      execute_receipt: {},
      undo_receipt: {},
      generation: 1,
      version: 1,
      executed_at: null,
      undone_at: null,
    };
    vi.mocked(fetch).mockResolvedValueOnce(
      jsonResponse({
        challenge: "stage-one-only",
        stage: 1,
        required_stages: 2,
        expires_at: "2026-07-13T01:05:00Z",
      }),
    );

    await expect(api.radar.execute(plan, false)).rejects.toMatchObject({ status: 409 });
    await expect(api.radar.resumeExecute(plan, false)).rejects.toMatchObject({ status: 409 });

    expect(fetch).toHaveBeenCalledTimes(1);
    expect(String(vi.mocked(fetch).mock.calls[0]?.[0])).toContain("/api/auth/write-challenges");
  });

  it("separates high-risk challenge preparation from the business write", async () => {
    const plan = {
      id: "mpl-high-risk",
      change_set_id: "ncs-high-risk",
      status: "ready",
      risk_level: "high" as const,
      required_confirmations: 2 as const,
      conflicts: [],
      items: [],
      verification: {},
      execute_receipt: {},
      undo_receipt: {},
      generation: 1,
      version: 1,
      executed_at: null,
      undone_at: null,
    };
    const receipt = {
      plan_id: plan.id,
      status: "verified",
      operation: "execute",
      verified_count: 0,
      total_count: 0,
      all_verified: true,
      items: [],
      verified_at: "2026-07-13T01:00:00Z",
    };
    vi.mocked(fetch)
      .mockResolvedValueOnce(
        jsonResponse({
          challenge: "execute-stage-one",
          stage: 1,
          required_stages: 2,
          expires_at: "2026-07-13T01:05:00Z",
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          challenge: "execute-stage-two",
          stage: 2,
          required_stages: 2,
          expires_at: "2026-07-13T01:05:00Z",
        }),
      )
      .mockResolvedValueOnce(jsonResponse(receipt));

    const prepared = await api.radar.beginExecute(plan, true);

    expect(prepared.challenge).toBe("execute-stage-two");
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(
      vi
        .mocked(fetch)
        .mock.calls.some(([url]) =>
          String(url).includes(`/api/notice-radar/migrations/${plan.id}/execute`),
        ),
    ).toBe(false);

    await api.radar.finishExecute(plan, true, prepared.challenge);

    expect(fetch).toHaveBeenCalledTimes(3);
    const [writeUrl, writeOptions] = vi.mocked(fetch).mock.calls[2] ?? [];
    expect(String(writeUrl)).toContain(`/api/notice-radar/migrations/${plan.id}/execute`);
    expect(new Headers(writeOptions?.headers).get("X-Write-Challenge")).toBe("execute-stage-two");
    const issueBody = JSON.parse(String(vi.mocked(fetch).mock.calls[0]?.[1]?.body));
    const writeBody = JSON.parse(String(writeOptions?.body));
    expect(writeBody).toEqual(issueBody.body);
    expect(writeBody.confirmation_stages).toBe(2);
  });

  it("keeps the two-stage payload stable when session storage is blocked", async () => {
    const plan = {
      id: "mpl-storage-blocked",
      change_set_id: "ncs-storage-blocked",
      status: "ready",
      risk_level: "high" as const,
      required_confirmations: 2 as const,
      conflicts: [],
      items: [],
      verification: {},
      execute_receipt: {},
      undo_receipt: {},
      generation: 1,
      version: 1,
      executed_at: null,
      undone_at: null,
    };
    vi.spyOn(window.sessionStorage, "getItem")
      .mockImplementationOnce(() => {
        throw new DOMException("Storage access denied", "SecurityError");
      })
      .mockImplementationOnce(() => null)
      .mockImplementation(() => {
        throw new DOMException("Storage access denied", "SecurityError");
      });
    vi.spyOn(window.sessionStorage, "setItem").mockImplementation(() => {
      throw new DOMException("Storage access denied", "SecurityError");
    });
    vi.mocked(fetch)
      .mockResolvedValueOnce(
        jsonResponse({
          challenge: "blocked-storage-stage-one",
          stage: 1,
          required_stages: 2,
          expires_at: "2026-07-13T01:05:00Z",
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          challenge: "blocked-storage-stage-two",
          stage: 2,
          required_stages: 2,
          expires_at: "2026-07-13T01:05:00Z",
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          plan_id: plan.id,
          status: "verified",
          operation: "execute",
          verified_count: 0,
          total_count: 0,
          all_verified: true,
          items: [],
          verified_at: "2026-07-13T01:00:00Z",
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          challenge: "blocked-storage-retry-one",
          stage: 1,
          required_stages: 2,
          expires_at: "2026-07-13T01:05:00Z",
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          challenge: "blocked-storage-retry-two",
          stage: 2,
          required_stages: 2,
          expires_at: "2026-07-13T01:05:00Z",
        }),
      );

    const prepared = await api.radar.beginExecute(plan, true);
    await api.radar.finishExecute(plan, true, prepared.challenge);
    await api.radar.beginExecute(plan, true);

    const firstIssueBody = JSON.parse(String(vi.mocked(fetch).mock.calls[0]?.[1]?.body)).body;
    const writeBody = JSON.parse(String(vi.mocked(fetch).mock.calls[2]?.[1]?.body));
    const retryIssueBody = JSON.parse(String(vi.mocked(fetch).mock.calls[3]?.[1]?.body)).body;
    expect(firstIssueBody.idempotency_key).toMatch(/^[0-9a-f-]{36}$/i);
    expect(writeBody.idempotency_key).toBe(firstIssueBody.idempotency_key);
    expect(retryIssueBody.idempotency_key).toBe(firstIssueBody.idempotency_key);
  });

  it("auto-rebuilds two-stage challenges only for an already applied verification recovery", async () => {
    const plan = {
      id: "mpl-applied-recovery",
      change_set_id: "ncs-applied-recovery",
      status: "applied",
      risk_level: "high" as const,
      required_confirmations: 2 as const,
      conflicts: [],
      items: [],
      verification: {},
      execute_receipt: {},
      undo_receipt: {},
      generation: 1,
      version: 2,
      executed_at: "2026-07-13T01:00:00Z",
      undone_at: null,
    };
    vi.mocked(fetch)
      .mockResolvedValueOnce(
        jsonResponse({
          challenge: "recovery-stage-one",
          stage: 1,
          required_stages: 2,
          expires_at: "2026-07-13T01:05:00Z",
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          challenge: "recovery-stage-two",
          stage: 2,
          required_stages: 2,
          expires_at: "2026-07-13T01:05:00Z",
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          plan_id: plan.id,
          status: "verified",
          operation: "execute",
          verified_count: 0,
          total_count: 0,
          all_verified: true,
          items: [],
          verified_at: "2026-07-13T01:01:00Z",
        }),
      );

    await api.radar.resumeExecute(plan, false);

    expect(fetch).toHaveBeenCalledTimes(3);
    expect(String(vi.mocked(fetch).mock.calls[1]?.[0])).toContain(
      "/api/auth/write-challenges/advance",
    );
    expect(new Headers(vi.mocked(fetch).mock.calls[2]?.[1]?.headers).get("X-Write-Challenge")).toBe(
      "recovery-stage-two",
    );
  });

  it("exchanges the bearer token for a one-time WebSocket ticket without URL leakage", async () => {
    setAccessToken("campus-access-token");
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse({ ticket: "ws-ticket", expires_at: "2026-07-12T12:00:30Z" }),
    );

    const ticket = await api.auth.websocketTicket();

    expect(ticket.ticket).toBe("ws-ticket");
    const [url, options] = vi.mocked(fetch).mock.calls[0] ?? [];
    expect(String(url).endsWith("/api/auth/ws-ticket")).toBe(true);
    expect(String(url)).not.toContain("campus-access-token");
    expect(options?.method).toBe("POST");
    expect(options?.body).toBeUndefined();
    expect(new Headers(options?.headers).get("Authorization")).toBe("Bearer campus-access-token");
  });

  it("normalizes task queries, legacy list responses, and verified task writes", async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(
        jsonResponse([
          {
            id: "task-1",
            title: "复习机器学习",
            status: "pending",
            priority: "high",
            tags: [],
            created_at: "2026-07-12T10:00:00Z",
            updated_at: "2026-07-12T10:00:00Z",
          },
        ]),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          challenge: "create-task-challenge",
          stage: 1,
          required_stages: 1,
          expires_at: "2026-07-12T12:02:00Z",
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          success: true,
          action: "create_task",
          record_id: "task-2",
          verified_fields: { title: true },
          side_effects: [],
          message: "待办已创建并复查",
          record: { id: "task-2", title: "提交实验报告", status: "pending", tags: [] },
        }),
      );

    const listed = await api.tasks.list({ status: "pending", course: "机器学习" });
    const created = await api.tasks.create({ title: "提交实验报告", priority: "high" });

    expect(listed).toMatchObject({ total: 1, items: [{ id: "task-1" }] });
    expect(String(vi.mocked(fetch).mock.calls[0]?.[0])).toContain(
      "/api/tasks?status=pending&course=%E6%9C%BA%E5%99%A8%E5%AD%A6%E4%B9%A0",
    );
    expect(created).toMatchObject({ success: true, record_id: "task-2" });
    const challengeBody = JSON.parse(String(vi.mocked(fetch).mock.calls[1]?.[1]?.body)) as Record<
      string,
      unknown
    >;
    expect(challengeBody).toMatchObject({
      method: "POST",
      path: "/api/tasks",
      body: { title: "提交实验报告", priority: "high" },
    });
    expect(new Headers(vi.mocked(fetch).mock.calls[2]?.[1]?.headers).get("X-Write-Challenge")).toBe(
      "create-task-challenge",
    );
  });

  it("refuses a task write when the server challenge policy does not match", async () => {
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse({
        challenge: "unexpected-stage",
        stage: 1,
        required_stages: 2,
        expires_at: "2026-07-12T12:02:00Z",
      }),
    );

    await expect(api.tasks.update("task/unsafe", { title: "不会写入" })).rejects.toMatchObject({
      status: 409,
      message: "写入确认策略与请求不匹配，操作未执行。",
    });
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(JSON.parse(String(vi.mocked(fetch).mock.calls[0]?.[1]?.body))).toMatchObject({
      path: "/api/tasks/task%2Funsafe",
    });
  });

  it("maps calendar conflicts and preserves encoded event identifiers on updates", async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(
        jsonResponse({
          has_conflict: true,
          conflicts: [
            {
              id: "event-existing",
              title: "高等数学",
              start_at: "2026-07-18T09:00:00+08:00",
              end_at: "2026-07-18T10:00:00+08:00",
            },
          ],
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          challenge: "event-update-challenge",
          stage: 1,
          required_stages: 1,
          expires_at: "2026-07-12T12:02:00Z",
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          success: true,
          action: "update_event",
          record_id: "event/1",
          verified_fields: { location: true },
          side_effects: [],
          message: "日程已更新并复查",
          record: { id: "event/1", title: "实验课", location: "A302" },
        }),
      );

    const conflict = await api.events.checkConflict({
      start_at: "2026-07-18T09:30:00+08:00",
      end_at: "2026-07-18T10:30:00+08:00",
    });
    const updated = await api.events.update("event/1", { location: "A302" });

    expect(conflict).toEqual({
      has_conflict: true,
      conflicts: [
        {
          event_id: "event-existing",
          title: "高等数学",
          start_at: "2026-07-18T09:00:00+08:00",
          end_at: "2026-07-18T10:00:00+08:00",
        },
      ],
    });
    expect(updated.verified_fields.location).toBe(true);
    expect(JSON.parse(String(vi.mocked(fetch).mock.calls[1]?.[1]?.body))).toMatchObject({
      method: "PATCH",
      path: "/api/events/event%2F1",
    });
  });

  it("normalizes document metadata and uploads file content as multipart data", async () => {
    const wireDocument = {
      id: "document-1",
      metadata: {
        title: "奖学金通知",
        department: "学生处",
        publish_date: "2026-07-01",
        applicable_group: "2024级",
        source_url: null,
        version: "v2",
        file_type: "txt",
      },
      status: "ready",
      chunk_count: 3,
      created_at: "2026-07-12T10:00:00Z",
    };
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse([wireDocument]))
      .mockResolvedValueOnce(jsonResponse(wireDocument, 201));

    const listed = await api.documents.list();
    const uploaded = await api.documents.upload(
      new File(["奖学金申请截止到 7 月 20 日"], "notice.txt", { type: "text/plain" }),
      { title: "奖学金通知", department: "学生处", source_url: null },
    );

    expect(listed).toMatchObject({
      total: 1,
      items: [{ id: "document-1", title: "奖学金通知", version: "v2", chunk_count: 3 }],
    });
    expect(uploaded.department).toBe("学生处");
    const uploadOptions = vi.mocked(fetch).mock.calls[1]?.[1];
    expect(uploadOptions?.body).toBeInstanceOf(FormData);
    const form = uploadOptions?.body as FormData;
    expect(form.get("title")).toBe("奖学金通知");
    expect(form.get("department")).toBe("学生处");
    expect(form.get("source_url")).toBeNull();
    expect(new Headers(uploadOptions?.headers).get("Content-Type")).toBeNull();
  });

  it("normalizes search evidence, version conflicts, and applicability conflicts", async () => {
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse({
        results: [
          {
            document_id: "document-1",
            chunk_id: "chunk-1",
            original_text: "申请截止到 7 月 20 日。",
            page_number: 2,
            similarity: 0.91,
            file_title: "奖学金通知",
            publish_date: "2026-07-01",
            version: "v2",
            applicable_group: "2024级",
          },
        ],
        version_conflicts: [{ title: "奖学金通知", versions: ["v1", "v2"] }],
        applicability_conflicts: [{ title: "奖学金通知", applicable_groups: ["2023级", "2024级"] }],
      }),
    );

    const result = await api.knowledge.search("奖学金截止时间", 5, {
      version: "v2",
      applicable_group: "2024级",
    });

    expect(result.evidence[0]).toMatchObject({
      content: "申请截止到 7 月 20 日。",
      page: 2,
      document_title: "奖学金通知",
    });
    expect(result.version_conflicts).toEqual([
      { document_title: "奖学金通知", versions: ["v1", "v2"] },
    ]);
    expect(result.applicability_conflicts[0]?.applicable_groups).toContain("2024级");
  });

  it("uses server challenges for confirmations and keeps execution failure reasons", async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(
        jsonResponse({
          challenge: "action-confirmation-challenge",
          stage: 1,
          expires_at: "2026-07-12T12:02:00Z",
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse(wireAction({ state: "ready", confirmations_received: 1 })),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          success: false,
          action: "create_task",
          record_id: null,
          verified_fields: { title: false },
          side_effects: [],
          message: "写后复查失败",
          error: "record_not_found_after_write",
        }),
      );

    const confirmed = await api.actions.confirm("action/1", true);
    const execution = await api.actions.execute("action/1");

    expect(confirmed).toMatchObject({ status: "ready", confirmation_count: 1 });
    expect(JSON.parse(String(vi.mocked(fetch).mock.calls[1]?.[1]?.body))).toEqual({
      confirmed: true,
      challenge: "action-confirmation-challenge",
    });
    expect(execution).toMatchObject({
      success: false,
      failure_reason: "record_not_found_after_write",
    });
  });

  it("returns safe, actionable errors for validation, plaintext, and network failures", async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(
        jsonResponse(
          {
            detail: [
              { loc: ["body", "title"], msg: "Field required" },
              { loc: ["body", "due_at"], msg: "Invalid datetime" },
            ],
          },
          422,
          { "x-request-id": "request-validation" },
        ),
      )
      .mockResolvedValueOnce(textResponse("upstream unavailable", 502))
      .mockRejectedValueOnce(new TypeError("connection refused"));

    const validation = await api.health().catch((reason: unknown) => reason);
    const upstream = await api.health().catch((reason: unknown) => reason);
    const network = await api.health().catch((reason: unknown) => reason);

    expect(validation).toMatchObject({
      status: 422,
      message: "title：Field required；due_at：Invalid datetime",
      requestId: "request-validation",
      userMessage: "title：Field required；due_at：Invalid datetime",
    });
    expect(upstream).toMatchObject({
      status: 502,
      message: "upstream unavailable",
      userMessage: "服务暂时不可用，请稍后重试。",
    });
    expect(network).toMatchObject({
      status: 0,
      userMessage: "无法连接服务，请确认后端已启动并检查网络。",
    });
  });

  it.each([
    [403, "权限不足", "权限不足"],
    [400, "", "提交的信息不完整，请检查后重试。"],
    [404, "", "没有找到对应的数据。"],
    [409, "", "操作与现有数据冲突，请检查后再试。"],
    [410, "expired", "该操作已过期，请重新发起。"],
    [428, "", "该操作还需要用户确认。"],
    [418, "teapot", "teapot"],
  ])("maps HTTP %i to a stable user-facing recovery message", (status, message, expected) => {
    expect(new ApiError(message, { status }).userMessage).toBe(expected);
  });

  it("loads settings and hotwords with normalized backend field names", async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse(settings))
      .mockResolvedValueOnce(
        jsonResponse({
          items: [
            {
              id: "hotword-1",
              term: "机器学习",
              category: "course",
              source: "settings",
              weight: 1,
              is_active: true,
              created_at: "2026-07-12T10:00:00Z",
            },
          ],
          total: 1,
        }),
      );

    expect((await api.settings.get()).major).toBe("人工智能");
    expect(await api.hotwords.list()).toMatchObject({
      total: 1,
      items: [{ value: "机器学习", active: true, source: "settings" }],
    });
  });
});
