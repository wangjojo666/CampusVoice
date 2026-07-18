import { expect, test, type Page, type Request } from "@playwright/test";

const now = new Date();
const nowIso = now.toISOString();
const laterIso = new Date(now.getTime() + 60 * 60_000).toISOString();

function task(id: string, title: string, course = "机器学习") {
  return {
    id,
    course_id: null,
    title,
    description: `${title}的说明`,
    course,
    due_at: laterIso,
    reminder_at: null,
    priority: "medium",
    status: "pending",
    source_type: "manual",
    source_document_id: null,
    created_at: nowIso,
    updated_at: nowIso,
    version: 1,
  };
}

function calendarEvent(id: string, title: string) {
  return {
    id,
    course_id: null,
    title,
    description: null,
    course: "机器学习",
    start_at: nowIso,
    end_at: laterIso,
    location: "教学楼 A302",
    reminder_minutes: 30,
    source_type: "manual",
    source_document_id: null,
    created_at: nowIso,
    updated_at: nowIso,
    version: 1,
  };
}

function documentRecord(id: string, title: string) {
  return {
    id,
    metadata: {
      title,
      department: "教务处",
      publish_date: "2026-07-01",
      applicable_group: "全校学生",
      source_url: null,
      version: "v1",
      file_type: "txt",
    },
    status: "ready",
    chunk_count: 2,
    created_at: nowIso,
  };
}

const citation = {
  document_id: "doc-1",
  chunk_id: "chunk-1",
  original_text: "奖学金申请截止时间为 2026 年 7 月 31 日。",
  page_number: null,
  similarity: 0.93,
  file_title: "奖学金申请通知",
  publish_date: "2026-07-01",
  version: "v1",
};

type ApiCall = {
  method: string;
  path: string;
  headers: Record<string, string>;
  body: unknown;
};

type MockOptions = {
  tasks?: ReturnType<typeof task>[];
  events?: ReturnType<typeof calendarEvent>[];
  documents?: ReturnType<typeof documentRecord>[];
  conflict?: boolean;
  sufficientAnswer?: boolean;
};

async function bodyFrom(request: Request) {
  try {
    return request.postDataJSON();
  } catch {
    return request.postData();
  }
}

async function installApiMocks(page: Page, options: MockOptions = {}) {
  const state = {
    tasks: [...(options.tasks ?? [task("task-1", "复习机器学习")])],
    events: [...(options.events ?? [calendarEvent("event-1", "机器学习研讨课")])],
    documents: [...(options.documents ?? [documentRecord("doc-1", "奖学金申请通知")])],
    hotwords: [
      {
        id: "hotword-1",
        term: "机器学习",
        category: "course",
        source: "seed",
        weight: 1,
        is_active: true,
        created_at: nowIso,
      },
    ],
    calls: [] as ApiCall[],
    pending: null as null | {
      id: string;
      action: string;
      targetId: string | null;
      payload: Record<string, unknown>;
      confirmations: number;
      required: number;
    },
  };

  const fulfill = async (route: Parameters<Parameters<Page["route"]>[1]>[0], body: unknown) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(body),
    });

  const wireAction = () => {
    if (!state.pending) throw new Error("No pending action in REST mock");
    const pending = state.pending;
    const awaiting =
      pending.confirmations === 0
        ? "awaiting_confirmation"
        : pending.confirmations < pending.required
          ? "awaiting_second_confirmation"
          : "ready";
    return {
      id: pending.id,
      action_type: pending.action,
      entity_type: pending.action.endsWith("event") ? "event" : "task",
      target_id: pending.targetId,
      payload: pending.payload,
      state: awaiting,
      risk_level: pending.required === 2 ? "high" : "medium",
      risk_factors: pending.required === 2 ? ["删除数据难以撤销", "需要第二次确认"] : ["写入数据"],
      missing_fields: [],
      ambiguities: [],
      blocking_reasons: [],
      diagnostics: {},
      required_confirmations: pending.required,
      confirmations_received: pending.confirmations,
      expires_at: new Date(Date.now() + 10 * 60_000).toISOString(),
      attempt_count: 0,
      max_attempts: 2,
      last_error: null,
    };
  };

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    const body = await bodyFrom(request);
    state.calls.push({ method, path, headers: await request.allHeaders(), body });

    if (path === "/api/health")
      return fulfill(route, { status: "ok", service: "CampusVoice API", version: "0.3.0" });

    if (path === "/api/auth/ws-ticket" && method === "POST")
      return fulfill(route, {
        ticket: "e2e-websocket-ticket",
        expires_at: new Date(Date.now() + 60_000).toISOString(),
      });

    if (path === "/api/auth/write-challenges" && method === "POST") {
      const descriptor = body as { method?: unknown; path?: unknown };
      const requiredStages =
        descriptor.method === "DELETE" &&
        typeof descriptor.path === "string" &&
        descriptor.path.startsWith("/api/hotwords/")
          ? 2
          : 1;
      return fulfill(route, {
        challenge: `write-stage-1-${state.calls.length}`,
        stage: 1,
        required_stages: requiredStages,
        expires_at: new Date(Date.now() + 60_000).toISOString(),
      });
    }
    if (path === "/api/auth/write-challenges/advance" && method === "POST")
      return fulfill(route, {
        challenge: `write-stage-2-${state.calls.length}`,
        stage: 2,
        required_stages: 2,
        expires_at: new Date(Date.now() + 60_000).toISOString(),
      });

    if (path === "/api/tasks" && method === "GET")
      return fulfill(route, { items: state.tasks, total: state.tasks.length });
    if (path === "/api/tasks" && method === "POST") {
      const payload = body as Record<string, unknown>;
      const created = {
        ...task(`task-${state.tasks.length + 1}`, String(payload.title)),
        ...payload,
      };
      state.tasks.push(created as ReturnType<typeof task>);
      return fulfill(route, {
        success: true,
        action: "create_task",
        record_id: created.id,
        verified_fields: { title: true },
        side_effects: [],
        message: "待办已保存并验证",
        record: created,
      });
    }
    if (path.startsWith("/api/tasks/") && method === "PATCH") {
      const id = decodeURIComponent(path.split("/").at(-1) ?? "");
      const payload = body as Record<string, unknown>;
      const index = state.tasks.findIndex((item) => item.id === id);
      const current = state.tasks[index];
      if (!current) throw new Error(`Unknown task in REST mock: ${id}`);
      state.tasks[index] = { ...current, ...payload, version: 2 };
      return fulfill(route, {
        success: true,
        action: "update_task",
        record_id: id,
        verified_fields: { status: true },
        side_effects: [],
        message: "待办已更新并验证",
        record: state.tasks[index],
      });
    }

    if (path === "/api/events/check-conflict" && method === "POST")
      return fulfill(route, {
        has_conflict: Boolean(options.conflict),
        conflicts: options.conflict ? state.events : [],
      });
    if (path === "/api/events" && method === "GET")
      return fulfill(route, { items: state.events, total: state.events.length });
    if (path === "/api/events" && method === "POST") {
      const payload = body as Record<string, unknown>;
      const created = {
        ...calendarEvent(`event-${state.events.length + 1}`, String(payload.title)),
        ...payload,
      };
      state.events.push(created as ReturnType<typeof calendarEvent>);
      return fulfill(route, {
        success: true,
        action: "create_event",
        record_id: created.id,
        verified_fields: { title: true, start_at: true },
        side_effects: [],
        message: "日程已保存并验证",
        record: created,
      });
    }

    if (path === "/api/notice-radar" && method === "GET")
      return fulfill(route, { items: [], total: 0 });

    if (path === "/api/documents" && method === "GET") return fulfill(route, state.documents);
    if (path === "/api/documents" && method === "POST") {
      const created = documentRecord(`doc-${state.documents.length + 1}`, "合成评测通知");
      state.documents.push(created);
      return fulfill(route, created);
    }
    if (path === "/api/knowledge/ask" && method === "POST")
      return fulfill(route, {
        answer: options.sufficientAnswer === false ? "" : "申请截止到 2026 年 7 月 31 日。",
        sufficient_evidence: options.sufficientAnswer !== false,
        insufficiency_reason:
          options.sufficientAnswer === false ? "现有证据不足，无法确认最终截止时间。" : null,
        citations: [citation],
        version_conflicts: [],
      });
    if (path === "/api/knowledge/search" && method === "POST")
      return fulfill(route, { results: [citation] });

    if (path === "/api/settings" && method === "GET")
      return fulfill(route, {
        major: "人工智能",
        grade: "2024 级",
        current_courses: [{ id: "course-1", name: "机器学习" }],
        teacher_names: ["张老师"],
        default_reminder_minutes: 30,
        timezone: "Asia/Shanghai",
        asr_provider: "funasr",
        asr_model: "paraformer-zh-streaming",
        asr_device: "cpu",
      });
    if (path === "/api/settings" && method === "PATCH")
      return fulfill(route, {
        settings: {
          ...(body as Record<string, unknown>),
          asr_provider: "funasr",
          asr_model: "paraformer-zh-streaming",
          asr_device: "cpu",
        },
        success: true,
        verified_fields: { major: true },
        message: "设置已更新并验证",
      });
    if (path === "/api/hotwords" && method === "GET")
      return fulfill(route, { items: state.hotwords, total: state.hotwords.length });
    if (path === "/api/hotwords" && method === "POST") {
      const payload = body as Record<string, unknown>;
      const created = {
        id: `hotword-${state.hotwords.length + 1}`,
        term: String(payload.term),
        category: String(payload.category),
        source: "user",
        weight: 1,
        is_active: true,
        created_at: nowIso,
      };
      state.hotwords.unshift(created);
      return fulfill(route, {
        success: true,
        action: "create_hotword",
        record_id: created.id,
        verified_fields: { term: true },
        side_effects: [],
        message: "热词已添加",
        record: created,
      });
    }
    if (/^\/api\/hotwords\/[^/]+$/.test(path) && method === "DELETE") {
      const id = decodeURIComponent(path.split("/").at(-1) ?? "");
      state.hotwords = state.hotwords.filter((item) => item.id !== id);
      return fulfill(route, {
        success: true,
        action: "delete_hotword",
        record_id: null,
        verified_fields: { deleted: true },
        side_effects: [],
        message: "热词已删除并验证",
        record: null,
      });
    }

    if (path === "/api/correction/preview" && method === "POST") {
      const text = String((body as Record<string, unknown>).text);
      return fulfill(route, {
        record: {
          id: "correction-1",
          original_text: text,
          corrected_text: text,
          modifications: [],
          candidates: [],
        },
        requires_user_input: false,
      });
    }
    if (path === "/api/intent/parse" && method === "POST") {
      const text = String((body as Record<string, unknown>).text);
      return fulfill(route, {
        intent: "create_task",
        confidence: 0.96,
        slots: { title: "提交奖学金申请", priority: "medium" },
        missing_fields: [],
        ambiguities: [],
        source_text: text,
        requires_confirmation: true,
      });
    }

    if (path === "/api/actions/prepare" && method === "POST") {
      const payload = body as Record<string, unknown>;
      const action = String(payload.action);
      state.pending = {
        id: "action-1",
        action,
        targetId: typeof payload.target_id === "string" ? payload.target_id : null,
        payload: (payload.payload as Record<string, unknown>) ?? {},
        confirmations: 0,
        required: action.startsWith("delete_") ? 2 : 1,
      };
      return fulfill(route, wireAction());
    }
    if (/^\/api\/actions\/[^/]+\/challenge$/.test(path) && method === "POST") {
      if (!state.pending) throw new Error("challenge called without prepare");
      const stage = state.pending.confirmations + 1;
      if (stage > state.pending.required)
        throw new Error("challenge called after action was ready");
      return fulfill(route, {
        challenge: `action-stage-${stage}-${"x".repeat(32)}`,
        stage,
        expires_at: new Date(Date.now() + 60_000).toISOString(),
      });
    }
    if (/^\/api\/actions\/[^/]+\/confirm$/.test(path) && method === "POST") {
      if (!state.pending) throw new Error("confirm called without prepare");
      const expectedStage = state.pending.confirmations + 1;
      const confirmation = body as { challenge?: unknown };
      if (confirmation.challenge !== `action-stage-${expectedStage}-${"x".repeat(32)}`)
        throw new Error("confirm called without its server-issued action challenge");
      state.pending.confirmations += 1;
      return fulfill(route, wireAction());
    }
    if (/^\/api\/actions\/[^/]+\/execute$/.test(path) && method === "POST") {
      if (!state.pending) throw new Error("execute called without prepare");
      const { action, targetId, payload } = state.pending;
      if (action === "delete_task")
        state.tasks = state.tasks.filter((item) => item.id !== targetId);
      if (action === "delete_event")
        state.events = state.events.filter((item) => item.id !== targetId);
      return fulfill(route, {
        success: true,
        action,
        record_id: action.startsWith("delete_") ? null : "task-from-voice",
        verified_fields: action.startsWith("delete_") ? { deleted: true } : { title: true },
        side_effects: [],
        message: action.startsWith("delete_") ? "记录已删除并验证" : "操作已写入并重新查询验证",
        record: action.startsWith("delete_")
          ? null
          : task("task-from-voice", String(payload.title)),
      });
    }
    if (/^\/api\/actions\/[^/]+\/(?:cancel|undo)$/.test(path) && method === "POST")
      return fulfill(route, {
        success: true,
        action: state.pending?.action ?? "unknown",
        record_id: null,
        verified_fields: {},
        side_effects: [],
        message: "操作状态已验证",
      });
    if (path === "/api/action-logs" && method === "GET")
      return fulfill(route, {
        items: [
          {
            id: "log-1",
            pending_action_id: "action-old",
            action_type: "create_task",
            risk_level: "medium",
            user_confirmed: true,
            success: true,
            error_message: null,
            before_snapshot: null,
            verification_result: { message: "待办写入已验证" },
            created_at: nowIso,
          },
        ],
        total: 1,
      });

    return route.fulfill({
      status: 404,
      contentType: "application/json",
      body: JSON.stringify({ message: `Unhandled E2E mock: ${method} ${path}` }),
    });
  });

  return state;
}

async function installSyntheticAudioAndWebSocket(page: Page) {
  await page.addInitScript(() => {
    const telemetry = { events: [] as string[], audioBytes: 0 };
    const NativeWebSocket = window.WebSocket;
    Object.defineProperty(window, "__campusvoiceE2E", { value: telemetry, configurable: true });

    const track = { stop: () => telemetry.events.push("media-track:stop") };
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: {
        getUserMedia: async () => {
          telemetry.events.push("media:get-user-media");
          return { getTracks: () => [track] };
        },
      },
    });

    class ConnectableNode {
      connect<T>(next: T) {
        return next;
      }
      disconnect() {}
    }

    type PortMessage = { data: { type: string; level?: number; buffer?: ArrayBuffer } };
    const port = {
      onmessage: null as ((message: PortMessage) => void) | null,
      postMessage: (message: { type?: string }) =>
        telemetry.events.push(`worklet:${message.type ?? "message"}`),
    };

    class MockAudioWorkletNode extends ConnectableNode {
      port = port;
      constructor(_context: unknown, name: string) {
        super();
        telemetry.events.push(`worklet-node:${name}`);
      }
    }

    class MockAudioContext {
      state = "suspended";
      destination = {};
      audioWorklet = {
        addModule: async (path: string) => telemetry.events.push(`worklet-module:${path}`),
      };
      createMediaStreamSource(stream: unknown) {
        void stream;
        return new ConnectableNode();
      }
      createGain() {
        const node = new ConnectableNode() as ConnectableNode & { gain: { value: number } };
        node.gain = { value: 1 };
        return node;
      }
      async resume() {
        this.state = "running";
        telemetry.events.push("audio-context:resume");
      }
      async suspend() {
        this.state = "suspended";
        telemetry.events.push("audio-context:suspend");
      }
      async close() {
        this.state = "closed";
        telemetry.events.push("audio-context:close");
      }
    }

    class MockWebSocket {
      static readonly CONNECTING = 0;
      static readonly OPEN = 1;
      static readonly CLOSING = 2;
      static readonly CLOSED = 3;
      readonly url: string;
      readyState = MockWebSocket.CONNECTING;
      binaryType = "";
      onopen: (() => void) | null = null;
      onmessage: ((event: { data: string }) => void) | null = null;
      onerror: (() => void) | null = null;
      onclose: ((event: { code: number; wasClean: boolean }) => void) | null = null;

      constructor(url: string, protocols?: string | string[]) {
        this.url = url;
        telemetry.events.push(`ws:construct:${url}`);
        const offeredProtocols = Array.isArray(protocols)
          ? protocols
          : protocols
            ? [protocols]
            : [];
        telemetry.events.push(`ws:protocols:${offeredProtocols.join(",")}`);
        window.setTimeout(() => {
          this.readyState = MockWebSocket.OPEN;
          telemetry.events.push("ws:open");
          this.onopen?.();
        }, 5);
      }

      private emit(message: Record<string, unknown>) {
        this.onmessage?.({ data: JSON.stringify(message) });
      }

      send(data: string | ArrayBuffer) {
        if (typeof data !== "string") {
          telemetry.audioBytes += data.byteLength;
          telemetry.events.push(`ws:pcm:${data.byteLength}`);
          return;
        }
        const message = JSON.parse(data) as { type: string };
        telemetry.events.push(`ws:control:${message.type}`);
        if (message.type === "start") {
          window.setTimeout(() => {
            this.emit({ type: "ready", session_id: "voice-e2e-1" });
            this.emit({ type: "speech_start", timestamp_ms: 10 });
            this.emit({
              type: "interim",
              text: "周五上午九点",
              confidence: 0.76,
              latency_ms: 80,
            });
            const samples = new Int16Array([0, 512, -512, 1024, -1024, 0]);
            port.onmessage?.({ data: { type: "level", level: 0.42 } });
            port.onmessage?.({ data: { type: "audio", buffer: samples.buffer } });
          }, 10);
        }
        if (message.type === "flush")
          window.setTimeout(() => this.emit({ type: "speech_end", timestamp_ms: 90 }), 1);
        if (message.type === "stop") {
          window.setTimeout(
            () =>
              this.emit({
                type: "final",
                text: "周五上午九点有机器学习考试。",
                confidence: 0.94,
                latency_ms: 120,
              }),
            5,
          );
          window.setTimeout(() => {
            this.readyState = MockWebSocket.CLOSED;
            telemetry.events.push("ws:close:1000");
            this.onclose?.({ code: 1000, wasClean: true });
          }, 10);
        }
      }

      close(code = 1000, reason?: string) {
        void reason;
        if (this.readyState === MockWebSocket.CLOSED) return;
        this.readyState = MockWebSocket.CLOSED;
        telemetry.events.push(`ws:close:${code}`);
        this.onclose?.({ code, wasClean: code === 1000 });
      }
    }

    Object.defineProperty(window, "AudioContext", { value: MockAudioContext, configurable: true });
    Object.defineProperty(window, "AudioWorkletNode", {
      value: MockAudioWorkletNode,
      configurable: true,
    });
    const RoutedWebSocket = new Proxy(NativeWebSocket, {
      construct(target, argumentsList) {
        const url = String(argumentsList[0]);
        if (url.endsWith("/ws/asr"))
          return new MockWebSocket(url, argumentsList[1] as string | string[] | undefined);
        return Reflect.construct(target, argumentsList);
      },
    });
    Object.defineProperty(window, "WebSocket", { value: RoutedWebSocket, configurable: true });
  });
}

test("01 今天面板展示真实任务与日程，并渐进披露已验证操作", async ({ page }) => {
  await installApiMocks(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "今天先把最重要的事接住" })).toBeVisible();
  await expect(page.getByRole("region", { name: "今天" })).toBeVisible();
  await expect(page.getByText("复习机器学习", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("机器学习研讨课", { exact: true })).toBeVisible();
  await expect(page.getByText("暂无需要处理的版本变化")).toBeVisible();
  await expect(page.getByText("待办写入已验证", { exact: true })).toBeHidden();
  await page.getByRole("button", { name: "查看执行详情" }).click();
  await expect(page.getByText("待办写入已验证", { exact: true })).toBeVisible();

  const desktopNavigation = page.getByRole("navigation", { name: "主导航" });
  await expect(desktopNavigation.getByRole("link", { name: "今天" })).toHaveAttribute(
    "aria-current",
    "page",
  );
  await expect(desktopNavigation.getByRole("link", { name: "问声程" })).toBeVisible();

  await page.setViewportSize({ width: 375, height: 812 });
  const mobileNavigation = page.getByRole("navigation", { name: "移动端主导航" });
  await expect(mobileNavigation).toBeVisible();
  await expect(mobileNavigation.getByRole("link", { name: "校园情报" })).toBeVisible();
});

test("02 待办列表在浏览器中完成关键词和状态筛选", async ({ page }) => {
  await installApiMocks(page, {
    tasks: [task("task-1", "复习机器学习"), task("task-2", "整理数据结构笔记", "数据结构")],
  });
  await page.goto("/tasks");
  await expect(page.getByRole("heading", { name: "复习机器学习" })).toBeVisible();
  await page.getByPlaceholder("搜索标题、说明或课程").fill("数据结构");
  await expect(page.getByRole("heading", { name: "整理数据结构笔记" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "复习机器学习" })).toBeHidden();
});

test("03 新建待办经过 UI 核对并携带服务端写挑战", async ({ page }) => {
  const state = await installApiMocks(page, { tasks: [] });
  await page.goto("/tasks");
  await page.getByRole("button", { name: "新增待办" }).first().click();
  const dialog = page.getByRole("dialog", { name: "新增待办" });
  await dialog.getByLabel("标题 *").fill("完成强化学习报告");
  await dialog.getByLabel("课程").fill("强化学习");
  await dialog.getByRole("button", { name: "核对并保存" }).click();
  await expect(dialog.getByText("确认创建这项待办")).toBeVisible();
  await dialog.getByRole("button", { name: "确认并保存" }).click();
  await expect(page.getByRole("status")).toContainText("待办已保存并验证");
  await expect(page.getByRole("heading", { name: "完成强化学习报告" })).toBeVisible();
  const createCall = state.calls.find(
    (call) => call.method === "POST" && call.path === "/api/tasks",
  );
  expect(createCall?.headers["x-write-challenge"]).toMatch(/^write-stage-1-/);
  expect(
    state.calls.some(
      (call) =>
        call.path === "/api/auth/write-challenges" &&
        call.body &&
        (call.body as Record<string, unknown>).method === "POST" &&
        (call.body as Record<string, unknown>).path === "/api/tasks",
    ),
  ).toBe(true);
});

test("04 删除待办要求输入标题且 REST 状态机完成两次确认", async ({ page }) => {
  const state = await installApiMocks(page, { tasks: [task("task-delete", "删除前核对我")] });
  await page.goto("/tasks");
  await page.getByRole("button", { name: "删除删除前核对我" }).click();
  const dialog = page.getByRole("dialog", { name: "高风险：删除待办" });
  const firstButton = dialog.getByRole("button", { name: "第一次确认删除" });
  await expect(firstButton).toBeDisabled();
  await dialog.getByLabel("输入完整标题进行第一次确认").fill("删除前核对我");
  await firstButton.click();

  await expect(page.getByRole("status")).toContainText("第一次确认已记录");
  await expect(dialog).toContainText("第一次确认已完成");
  const secondButton = dialog.getByRole("button", { name: "第二次确认并删除" });
  await expect(secondButton).toBeDisabled();
  expect(state.calls.filter((call) => call.path.endsWith("/challenge"))).toHaveLength(1);
  expect(state.calls.filter((call) => call.path.endsWith("/confirm"))).toHaveLength(1);
  expect(state.calls.filter((call) => call.path.endsWith("/execute"))).toHaveLength(0);

  await dialog.getByLabel("重新输入完整标题进行第二次确认").fill("删除前核对我");
  await secondButton.click();
  await expect(page.getByRole("status")).toContainText("记录已删除并验证");
  await expect(page.getByRole("heading", { name: "删除前核对我" })).toHaveCount(0);
  expect(state.calls.filter((call) => call.path.endsWith("/challenge"))).toHaveLength(2);
  expect(state.calls.filter((call) => call.path.endsWith("/confirm"))).toHaveLength(2);
  expect(state.calls.filter((call) => call.path.endsWith("/execute"))).toHaveLength(1);
});

test("05 日历保存前检测冲突并阻止 POST 写入", async ({ page }) => {
  const state = await installApiMocks(page, {
    events: [calendarEvent("event-conflict", "已存在的机器学习课")],
    conflict: true,
  });
  await page.goto("/calendar");
  await page.getByRole("button", { name: "新建日程" }).first().click();
  const dialog = page.getByRole("dialog", { name: "新建日程" });
  await dialog.getByLabel("标题 *").fill("冲突的考试复习");
  await dialog.getByRole("button", { name: "检查冲突并核对" }).click();
  await dialog.getByRole("button", { name: "确认并保存" }).click();
  await expect(dialog.getByRole("alert")).toContainText("检测到时间冲突，已阻止保存");
  await expect(dialog.getByText("已存在的机器学习课", { exact: true })).toBeVisible();
  expect(state.calls.some((call) => call.method === "POST" && call.path === "/api/events")).toBe(
    false,
  );
});

test("06 通知问答在证据不足时显示原因和原文引用", async ({ page }) => {
  await installApiMocks(page, { sufficientAnswer: false });
  await page.goto("/notices");
  await page.getByPlaceholder("例如：奖学金申请什么时候截止？").fill("最终截止时间是什么？");
  await page.getByRole("button", { name: "基于证据回答" }).click();
  await expect(
    page.getByText("现有证据不足，无法确认最终截止时间。", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("奖学金申请截止时间为 2026 年 7 月 31 日。", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("无天然页码", { exact: false })).toBeVisible();
});

test("07 浏览器上传合成通知并刷新文档列表", async ({ page }) => {
  const state = await installApiMocks(page, { documents: [] });
  await page.goto("/notices");
  await page.getByRole("button", { name: "上传文档" }).click();
  const dialog = page.getByRole("dialog", { name: "上传校园通知" });
  await dialog.locator('input[type="file"]').setInputFiles({
    name: "synthetic-notice.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("合成通知，不包含真实个人信息。", "utf8"),
  });
  await dialog.getByLabel("文件标题 *").fill("合成评测通知");
  await dialog.getByRole("button", { name: "上传文档" }).click();
  await expect(page.getByRole("status")).toContainText("文档已上传");
  await expect(page.getByRole("heading", { name: "合成评测通知" })).toBeVisible();
  const upload = state.calls.find(
    (call) => call.method === "POST" && call.path === "/api/documents",
  );
  expect(upload?.headers["content-type"]).toContain("multipart/form-data");
});

test("08 设置与热词通过已确认 REST 请求保存并立即回显", async ({ page }) => {
  const state = await installApiMocks(page);
  await page.goto("/settings");
  await page.getByLabel("专业").fill("智能科学与技术");
  await page.getByRole("button", { name: "保存设置" }).click();
  await expect(page.getByRole("status")).toContainText("设置已保存");
  await page.getByLabel("新热词").fill("Diffusion Transformer");
  await page.getByLabel("新热词").locator("xpath=following::select[1]").selectOption("ai_term");
  await page.getByRole("button", { name: "添加热词" }).click();
  await expect(page.getByText("Diffusion Transformer", { exact: true })).toBeVisible();
  const save = state.calls.find((call) => call.method === "PATCH" && call.path === "/api/settings");
  expect(save?.headers["x-write-challenge"]).toMatch(/^write-stage-1-/);
  expect(save?.body).toMatchObject({ major: "智能科学与技术" });
  expect(
    state.calls.some(
      (call) =>
        call.path === "/api/auth/write-challenges" &&
        call.body &&
        (call.body as Record<string, unknown>).method === "PATCH" &&
        (call.body as Record<string, unknown>).path === "/api/settings",
    ),
  ).toBe(true);

  await page.getByRole("button", { name: "删除热词Diffusion Transformer" }).click();
  await expect(page.getByRole("dialog")).toContainText("第二次确认");
  expect(
    state.calls.some((call) => call.method === "DELETE" && call.path.startsWith("/api/hotwords/")),
  ).toBe(false);
  await page.getByRole("button", { name: "第二次确认并删除" }).click();
  await expect(page.getByText("Diffusion Transformer", { exact: true })).toHaveCount(0);
  const deleted = state.calls.find(
    (call) => call.method === "DELETE" && call.path.startsWith("/api/hotwords/"),
  );
  expect(deleted?.headers["x-write-challenge"]).toMatch(/^write-stage-2-/);
  expect(state.calls.some((call) => call.path === "/api/auth/write-challenges/advance")).toBe(true);
});

test("09 通知引用转为语音待办并经过纠错、意图、确认和数据库验证", async ({ page }) => {
  const state = await installApiMocks(page, { sufficientAnswer: true });
  await page.goto("/notices");
  await page.getByPlaceholder("例如：奖学金申请什么时候截止？").fill("奖学金什么时候截止？");
  await page.getByRole("button", { name: "基于证据回答" }).click();
  await page.getByRole("button", { name: "转为待办草稿" }).click();
  await expect(page).toHaveURL(/\/voice$/);
  await expect(page.getByLabel("待解析的转写文字")).toContainText("奖学金申请通知");
  await page.getByRole("button", { name: "解析并检查" }).click();
  await expect(page.getByRole("heading", { name: "创建待办" })).toBeVisible();
  await page.getByRole("button", { name: "确认操作" }).click();
  await expect(page.getByText("数据库验证成功", { exact: true })).toBeVisible();
  await expect(page.getByText("task-from-voice", { exact: true })).toBeVisible();
  const prepare = state.calls.find(
    (call) => call.method === "POST" && call.path === "/api/actions/prepare",
  );
  expect(prepare?.body).toMatchObject({ action: "create_task" });
  expect(state.calls.some((call) => call.path.endsWith("/execute"))).toBe(true);
});

test("10 合成 PCM 贯穿浏览器 AudioWorklet、WebSocket 与录音状态链", async ({ page }) => {
  await installSyntheticAudioAndWebSocket(page);
  await installApiMocks(page);
  await page.goto("/voice");
  await page.getByRole("button", { name: "开始录音" }).click();
  await expect(page.getByText("正在聆听", { exact: true })).toBeVisible();
  await expect(page.getByLabel("实时转写")).toHaveValue("周五上午九点");
  await page.getByRole("button", { name: "暂停录音" }).click();
  await expect(page.getByText("录音已暂停", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "继续" }).click();
  await expect(page.getByText("正在聆听", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "停止并完成转写" }).click();
  await expect(page.getByText("转写已完成", { exact: true })).toBeVisible();
  await expect(page.getByLabel("最终转写（可编辑）")).toHaveValue("周五上午九点有机器学习考试。");
  await expect(page.getByText("置信度：94%", { exact: false })).toBeVisible();

  const telemetry = await page.evaluate(() => {
    return (
      window as unknown as Window & {
        __campusvoiceE2E: { events: string[]; audioBytes: number };
      }
    ).__campusvoiceE2E;
  });
  expect(telemetry.audioBytes).toBeGreaterThan(0);
  expect(telemetry.events).toContain("worklet-module:/audio-processor.js");
  expect(telemetry.events).toContain(
    "ws:protocols:campusvoice,campusvoice.ticket.e2e-websocket-ticket",
  );
  expect(telemetry.events).toContain("ws:control:start");
  expect(telemetry.events).toContain("ws:control:flush");
  expect(telemetry.events).toContain("ws:control:stop");
  expect(telemetry.events.some((event) => event.startsWith("ws:pcm:"))).toBe(true);
});
