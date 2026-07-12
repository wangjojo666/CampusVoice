import type {
  ActionLog,
  ActionPrepareRequest,
  CalendarEvent,
  CalendarEventCreate,
  CalendarEventUpdate,
  CorrectionResult,
  DocumentRecord,
  EventConflict,
  HealthResponse,
  Hotword,
  IntentResult,
  KnowledgeAnswer,
  KnowledgeEvidence,
  KnowledgeSearchResult,
  ListResponse,
  MutationResult,
  PendingAction,
  Task,
  TaskCreate,
  TaskUpdate,
  UserSettings,
  VerificationResult,
} from "@campusvoice/shared-types";

import { mergeContextHotwords } from "@/lib/asr/context-hotwords";

interface WirePendingAction {
  id: string;
  action_type: PendingAction["action"];
  entity_type: "task" | "event";
  target_id: string | null;
  payload: Record<string, unknown>;
  state: PendingAction["status"];
  risk_level: PendingAction["risk_level"];
  risk_factors: string[];
  missing_fields: string[];
  ambiguities: string[];
  blocking_reasons: string[];
  diagnostics: Record<string, unknown>;
  required_confirmations: number;
  confirmations_received: number;
  expires_at: string;
  attempt_count: number;
  max_attempts: number;
  last_error: string | null;
}

interface WireHotword {
  id: string;
  term: string;
  category: Hotword["category"];
  source: string;
  weight: number;
  is_active: boolean;
  created_at: string;
}

interface WireDocument {
  id: string;
  metadata: {
    title: string;
    department: string | null;
    publish_date: string | null;
    applicable_group: string | null;
    source_url: string | null;
    version: string | null;
    file_type: DocumentRecord["file_type"];
  };
  status: DocumentRecord["status"];
  chunk_count: number;
  created_at: string;
}

interface WireCitation {
  document_id: string;
  chunk_id: string;
  original_text: string;
  page_number: number | null;
  similarity: number;
  file_title: string;
  publish_date: string | null;
  version: string | null;
  applicable_group: string | null;
}

interface WireVersionConflict {
  title: string;
  versions: string[];
}

interface WireApplicabilityConflict {
  title: string;
  applicable_groups: string[];
}

interface WireActionLog {
  id: string;
  pending_action_id: string | null;
  voice_session_id: string | null;
  transcription_id: string | null;
  action_type: ActionLog["action"];
  risk_level: ActionLog["risk_level"];
  user_confirmed: boolean;
  success: boolean;
  error_message: string | null;
  source_text: string | null;
  corrected_text: string | null;
  before_snapshot: Record<string, unknown> | null;
  verification_result: Record<string, unknown>;
  created_at: string;
}

const configuredBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "");
export const API_BASE_URL = configuredBaseUrl || "http://localhost:8000";

export class ApiError extends Error {
  readonly status: number;
  readonly code?: string;
  readonly requestId?: string;
  readonly details?: unknown;

  constructor(
    message: string,
    options: { status: number; code?: string; requestId?: string; details?: unknown },
  ) {
    super(message);
    this.name = "ApiError";
    this.status = options.status;
    this.code = options.code;
    this.requestId = options.requestId;
    this.details = options.details;
  }

  get userMessage() {
    if (this.status === 0) return "无法连接服务，请确认后端已启动并检查网络。";
    if (this.status === 400 || this.status === 422)
      return this.message || "提交的信息不完整，请检查后重试。";
    if (this.status === 404) return this.message || "没有找到对应的数据。";
    if (this.status === 409) return this.message || "操作与现有数据冲突，请检查后再试。";
    if (this.status === 410) return "该操作已过期，请重新发起。";
    if (this.status >= 500) return "服务暂时不可用，请稍后重试。";
    return this.message || "请求失败，请重试。";
  }
}

type RequestOptions = RequestInit & { timeoutMs?: number };

function validationMessage(detail: unknown): string | null {
  if (typeof detail === "string") return detail;
  if (!Array.isArray(detail)) return null;
  const messages = detail
    .map((item) => {
      if (!item || typeof item !== "object") return null;
      const record = item as { loc?: Array<string | number>; msg?: string };
      const field = record.loc?.filter((part) => part !== "body").join(".");
      return record.msg ? `${field ? `${field}：` : ""}${record.msg}` : null;
    })
    .filter((item): item is string => Boolean(item));
  return messages.length > 0 ? messages.join("；") : null;
}

function nestedErrorDetail(payload: Record<string, unknown>) {
  const detail = payload.detail;
  if (detail && typeof detail === "object" && !Array.isArray(detail))
    return detail as Record<string, unknown>;
  const error = payload.error;
  if (error && typeof error === "object" && !Array.isArray(error))
    return error as Record<string, unknown>;
  return {};
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), options.timeoutMs ?? 20_000);
  const headers = new Headers(options.headers);
  const bodyIsForm = options.body instanceof FormData;
  if (options.body && !bodyIsForm && !headers.has("Content-Type"))
    headers.set("Content-Type", "application/json");
  headers.set("Accept", "application/json");

  try {
    const response = await fetch(`${API_BASE_URL}${path}`, {
      ...options,
      headers,
      signal: options.signal ?? controller.signal,
    });
    const contentType = response.headers.get("content-type") ?? "";
    const payload: unknown =
      response.status === 204
        ? null
        : contentType.includes("application/json")
          ? await response.json()
          : await response.text();

    if (!response.ok) {
      const errorBody =
        payload && typeof payload === "object" ? (payload as Record<string, unknown>) : {};
      const nested = nestedErrorDetail(errorBody);
      const detailMessage = validationMessage(errorBody.detail);
      const fallback = typeof payload === "string" && payload ? payload : response.statusText;
      throw new ApiError(
        (typeof errorBody.message === "string" ? errorBody.message : null) ??
          (typeof nested.message === "string" ? nested.message : null) ??
          detailMessage ??
          fallback,
        {
          status: response.status,
          code:
            typeof errorBody.code === "string"
              ? errorBody.code
              : typeof nested.code === "string"
                ? nested.code
                : undefined,
          requestId:
            typeof errorBody.request_id === "string"
              ? errorBody.request_id
              : (response.headers.get("x-request-id") ?? undefined),
          details: errorBody.detail,
        },
      );
    }

    return payload as T;
  } catch (reason) {
    if (reason instanceof ApiError) throw reason;
    const message =
      reason instanceof DOMException && reason.name === "AbortError" ? "请求超时" : "网络连接失败";
    throw new ApiError(message, { status: 0, details: reason });
  } finally {
    window.clearTimeout(timeoutId);
  }
}

function asQuery(values: Record<string, string | number | boolean | null | undefined>) {
  const search = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => {
    if (value !== null && value !== undefined && value !== "") search.set(key, String(value));
  });
  const query = search.toString();
  return query ? `?${query}` : "";
}

async function listRequest<T>(path: string): Promise<ListResponse<T>> {
  const payload = await request<ListResponse<T> | T[]>(path);
  return Array.isArray(payload) ? { items: payload, total: payload.length } : payload;
}

function jsonBody(value: unknown): Pick<RequestInit, "body"> {
  return { body: JSON.stringify(value) };
}

function confirmationHeaders(second = false): HeadersInit {
  return {
    "X-User-Confirmed": "true",
    ...(second ? { "X-Second-Confirmation": "true" } : {}),
    "Idempotency-Key": crypto.randomUUID(),
  };
}

function normalizePendingAction(action: WirePendingAction): PendingAction {
  const clarification = action.diagnostics.clarification_question;
  return {
    id: action.id,
    action: action.action_type,
    title: {
      create_task: "创建待办",
      update_task: "修改待办",
      delete_task: "删除待办",
      create_event: "创建日历事件",
      update_event: "修改日历事件",
      delete_event: "删除日历事件",
      search_notice: "查询校园通知",
      query_schedule: "查询日程",
      unknown: "未知操作",
    }[action.action_type],
    risk_level: action.risk_level,
    risk_reasons: action.risk_factors,
    payload: action.payload,
    status: action.state,
    requires_second_confirmation: action.required_confirmations > 1,
    confirmation_count: action.confirmations_received,
    confirmations_required: action.required_confirmations,
    expires_at: action.expires_at,
    missing_fields: action.missing_fields,
    ambiguities: action.ambiguities,
    blocking_reasons: action.blocking_reasons,
    diagnostics: action.diagnostics,
    clarification_question: typeof clarification === "string" ? clarification : null,
  };
}

function normalizeVerification(
  payload: VerificationResult & { error?: string | null },
): VerificationResult {
  return {
    ...payload,
    failure_reason: payload.failure_reason ?? payload.error ?? null,
  };
}

function normalizeHotword(word: WireHotword): Hotword {
  return {
    id: word.id,
    value: word.term,
    category: word.category,
    source: word.source,
    active: word.is_active,
    created_at: word.created_at,
  };
}

function normalizeDocument(document: WireDocument): DocumentRecord {
  return {
    id: document.id,
    title: document.metadata.title,
    department: document.metadata.department,
    publish_date: document.metadata.publish_date,
    applicable_group: document.metadata.applicable_group,
    source_url: document.metadata.source_url,
    version: document.metadata.version,
    file_type: document.metadata.file_type,
    status: document.status,
    chunk_count: document.chunk_count,
    created_at: document.created_at,
  };
}

function normalizeCitation(citation: WireCitation): KnowledgeEvidence {
  return {
    document_id: citation.document_id,
    chunk_id: citation.chunk_id,
    content: citation.original_text,
    page: citation.page_number,
    similarity: citation.similarity,
    document_title: citation.file_title,
    publish_date: citation.publish_date,
    version: citation.version,
    applicable_group: citation.applicable_group,
  };
}

async function executeDestructive<T>(
  action: "delete_task" | "delete_event",
  targetId: string,
): Promise<MutationResult<T>> {
  let pending = normalizePendingAction(
    await request<WirePendingAction>("/api/actions/prepare", {
      method: "POST",
      ...jsonBody({
        action,
        target_id: targetId,
        payload: {},
        hard_to_undo: true,
        idempotency_key: crypto.randomUUID(),
      }),
    }),
  );
  for (
    let step = 0;
    step < 2 && ["awaiting_confirmation", "awaiting_second_confirmation"].includes(pending.status);
    step += 1
  ) {
    pending = normalizePendingAction(
      await request<WirePendingAction>(`/api/actions/${encodeURIComponent(pending.id)}/confirm`, {
        method: "POST",
        ...jsonBody({ confirmed: true, confirmation_token: crypto.randomUUID() }),
      }),
    );
  }
  if (pending.status !== "ready") {
    throw new ApiError("删除操作未获得全部确认，未执行。", { status: 409, details: pending });
  }
  const result = normalizeVerification(
    await request<VerificationResult & { error?: string | null }>(
      `/api/actions/${encodeURIComponent(pending.id)}/execute`,
      { method: "POST" },
    ),
  );
  return result as MutationResult<T>;
}

export const api = {
  health: () => request<HealthResponse>("/api/health", { timeoutMs: 5_000 }),

  tasks: {
    list: (
      filters: { status?: string; course?: string; due_from?: string; due_to?: string } = {},
    ) => listRequest<Task>(`/api/tasks${asQuery(filters)}`),
    create: (data: TaskCreate) =>
      request<MutationResult<Task>>("/api/tasks", {
        method: "POST",
        headers: confirmationHeaders(),
        ...jsonBody(data),
      }),
    update: (id: string, data: TaskUpdate) =>
      request<MutationResult<Task>>(`/api/tasks/${encodeURIComponent(id)}`, {
        method: "PATCH",
        headers: confirmationHeaders(),
        ...jsonBody(data),
      }),
    remove: (id: string) => executeDestructive<Task>("delete_task", id),
  },

  events: {
    list: (filters: { start?: string; end?: string; course?: string } = {}) =>
      listRequest<CalendarEvent>(
        `/api/events${asQuery({ starts_after: filters.start, starts_before: filters.end, course: filters.course })}`,
      ),
    create: (data: CalendarEventCreate) =>
      request<MutationResult<CalendarEvent>>("/api/events", {
        method: "POST",
        headers: confirmationHeaders(),
        ...jsonBody(data),
      }),
    update: (id: string, data: CalendarEventUpdate) =>
      request<MutationResult<CalendarEvent>>(`/api/events/${encodeURIComponent(id)}`, {
        method: "PATCH",
        headers: confirmationHeaders(),
        ...jsonBody(data),
      }),
    remove: (id: string) => executeDestructive<CalendarEvent>("delete_event", id),
    checkConflict: (data: { start_at: string; end_at: string; exclude_event_id?: string }) =>
      request<{ conflicts: CalendarEvent[]; has_conflict: boolean }>("/api/events/check-conflict", {
        method: "POST",
        ...jsonBody(data),
      }).then((response) => ({
        has_conflict: response.has_conflict,
        conflicts: response.conflicts.map((event): EventConflict => ({
          event_id: event.id,
          title: event.title,
          start_at: event.start_at,
          end_at: event.end_at,
        })),
      })),
  },

  documents: {
    list: () =>
      request<WireDocument[]>("/api/documents").then((items) => ({
        items: items.map(normalizeDocument),
        total: items.length,
      })),
    upload: (file: File, metadata: Partial<DocumentRecord>) => {
      const form = new FormData();
      form.append("file", file);
      Object.entries(metadata).forEach(([key, value]) => {
        if (value !== null && value !== undefined) form.append(key, String(value));
      });
      return request<WireDocument>("/api/documents", {
        method: "POST",
        body: form,
        timeoutMs: 60_000,
      }).then(normalizeDocument);
    },
  },

  knowledge: {
    search: (
      query: string,
      limit = 8,
      filters: { version?: string; applicable_group?: string } = {},
    ) =>
      request<{
        results: WireCitation[];
        version_conflicts?: WireVersionConflict[];
        applicability_conflicts?: WireApplicabilityConflict[];
      }>("/api/knowledge/search", {
        method: "POST",
        ...jsonBody({ query, top_k: limit, ...filters }),
      }).then((payload): KnowledgeSearchResult => ({
        evidence: payload.results.map(normalizeCitation),
        version_conflicts: (payload.version_conflicts ?? []).map((item) => ({
          document_title: item.title,
          versions: item.versions,
        })),
        applicability_conflicts: (payload.applicability_conflicts ?? []).map((item) => ({
          document_title: item.title,
          applicable_groups: item.applicable_groups,
        })),
      })),
    ask: (question: string, filters: { version?: string; applicable_group?: string } = {}) =>
      request<{
        answer: string;
        sufficient_evidence: boolean;
        insufficiency_reason: string | null;
        citations: WireCitation[];
        version_conflicts?: WireVersionConflict[];
        applicability_conflicts?: WireApplicabilityConflict[];
      }>("/api/knowledge/ask", {
        method: "POST",
        ...jsonBody({ question, ...filters }),
      }).then((payload): KnowledgeAnswer => ({
        answer: payload.answer || null,
        sufficient: payload.sufficient_evidence,
        evidence: payload.citations.map(normalizeCitation),
        message: payload.insufficiency_reason ?? undefined,
        version_conflicts: (payload.version_conflicts ?? []).map((item) => ({
          document_title: item.title,
          versions: item.versions,
        })),
        applicability_conflicts: (payload.applicability_conflicts ?? []).map((item) => ({
          document_title: item.title,
          applicable_groups: item.applicable_groups,
        })),
      })),
  },

  hotwords: {
    list: () =>
      request<ListResponse<WireHotword>>("/api/hotwords").then((payload) => ({
        items: payload.items.map(normalizeHotword),
        total: payload.total,
      })),
    create: (data: Pick<Hotword, "value" | "category">) =>
      request<MutationResult<WireHotword>>("/api/hotwords", {
        method: "POST",
        headers: confirmationHeaders(),
        ...jsonBody({ term: data.value, category: data.category, source: "user", weight: 1 }),
      }).then((payload) => {
        if (!payload.record)
          throw new ApiError("热词写入后未能查询到记录。", { status: 409, details: payload });
        return normalizeHotword(payload.record);
      }),
    remove: (id: string) =>
      request<MutationResult<WireHotword>>(`/api/hotwords/${encodeURIComponent(id)}`, {
        method: "DELETE",
        headers: confirmationHeaders(true),
      }),
  },

  settings: {
    get: () => request<UserSettings>("/api/settings"),
    update: (data: Partial<UserSettings>) =>
      request<{ settings: UserSettings }>("/api/settings", {
        method: "PATCH",
        headers: confirmationHeaders(),
        ...jsonBody(data),
      }).then((response) => response.settings),
  },

  intent: {
    parse: (text: string, context?: string[], asrConfidence?: number, conversationId?: string) =>
      request<IntentResult>("/api/intent/parse", {
        method: "POST",
        ...jsonBody({
          text,
          context,
          asr_confidence: asrConfidence,
          conversation_id: conversationId,
        }),
      }),
  },

  correction: {
    preview: async (
      text: string,
      asrConfidence = 1,
      transcriptionId?: string,
    ): Promise<CorrectionResult> => {
      let hotwords: Hotword[] = [];
      let settings: UserSettings | null = null;
      const [hotwordResult, settingsResult] = await Promise.allSettled([
        request<ListResponse<WireHotword>>("/api/hotwords"),
        request<UserSettings>("/api/settings"),
      ]);
      if (hotwordResult.status === "fulfilled")
        hotwords = hotwordResult.value.items.map(normalizeHotword);
      if (settingsResult.status === "fulfilled") settings = settingsResult.value;
      const contextHotwords = mergeContextHotwords(hotwords, settings);
      const sourceByCategory: Record<Hotword["category"], string> = {
        course: "course",
        course_code: "course_code",
        teacher: "teacher",
        ai_term: "ai_term",
        document: "document",
        custom: "user",
      };
      const criticalSpans = [
        ...text.matchAll(/\d{1,4}[年/-]\d{1,2}(?:[月/-]\d{1,2}日?)?|\d{1,2}[:：]\d{2}/g),
      ].map((match) => ({
        start: match.index,
        end: match.index + match[0].length,
        kind: "date_or_time",
      }));
      const response = await request<{
        record: {
          id: string;
          original_text: string;
          corrected_text: string;
          modifications: Array<{
            start: number;
            end: number;
            original: string;
            replacement: string;
            policy: "auto_apply" | "suggest" | "clarify" | "unchanged";
            confidence: number;
            reason: string;
          }>;
          candidates: Array<{ start: number; end: number; replacement: string }>;
        };
        requires_user_input: boolean;
      }>("/api/correction/preview", {
        method: "POST",
        ...jsonBody({
          transcription_id: transcriptionId,
          text,
          asr_confidence: asrConfidence,
          terms: contextHotwords.map((word) => ({
            term: word.value,
            source: sourceByCategory[word.category],
            aliases: [],
            context_keywords: [],
          })),
          critical_spans: criticalSpans,
          current_courses: contextHotwords
            .filter((word) => word.category === "course" || word.category === "course_code")
            .map((word) => word.value),
          document_terms: contextHotwords
            .filter((word) => word.category === "document")
            .map((word) => word.value),
          recent_context: [],
        }),
      });
      return {
        record_id: response.record.id,
        original_text: response.record.original_text,
        corrected_text: response.record.corrected_text,
        requires_user_input: response.requires_user_input,
        changes: response.record.modifications.map((change) => ({
          start: change.start,
          end: change.end,
          original: change.original,
          corrected: change.replacement,
          candidates: response.record.candidates
            .filter((candidate) => candidate.start === change.start && candidate.end === change.end)
            .map((candidate) => candidate.replacement),
          reason: change.reason,
          confidence: change.confidence,
          requires_confirmation: change.policy === "suggest" || change.policy === "clarify",
        })),
      };
    },
    decide: (recordId: string, correctedText: string, confirmed: boolean) =>
      request<{ id: string; corrected_text: string; user_confirmed: boolean }>(
        `/api/correction/${encodeURIComponent(recordId)}/decision`,
        {
          method: "POST",
          ...jsonBody({ corrected_text: correctedText, confirmed }),
        },
      ),
  },

  actions: {
    prepare: (data: ActionPrepareRequest) =>
      request<WirePendingAction>("/api/actions/prepare", {
        method: "POST",
        ...jsonBody(data),
      }).then(normalizePendingAction),
    confirm: (id: string, confirmed: boolean, confirmationToken: string) =>
      request<WirePendingAction>(`/api/actions/${encodeURIComponent(id)}/confirm`, {
        method: "POST",
        ...jsonBody({ confirmed, confirmation_token: confirmationToken }),
      }).then(normalizePendingAction),
    execute: (id: string) =>
      request<VerificationResult & { error?: string | null }>(
        `/api/actions/${encodeURIComponent(id)}/execute`,
        {
          method: "POST",
        },
      ).then(normalizeVerification),
    cancel: (id: string) =>
      request<WirePendingAction>(`/api/actions/${encodeURIComponent(id)}/cancel`, {
        method: "POST",
        ...jsonBody({}),
      }).then(normalizePendingAction),
    undo: (id: string) =>
      request<VerificationResult & { error?: string | null }>(
        `/api/actions/${encodeURIComponent(id)}/undo`,
        {
          method: "POST",
        },
      ).then(normalizeVerification),
  },

  actionLogs: {
    list: (limit = 20) =>
      request<ListResponse<WireActionLog>>(`/api/action-logs${asQuery({ limit })}`).then(
        (payload) => ({
          total: payload.total,
          items: payload.items.map((log): ActionLog => ({
            id: log.id,
            action_id: log.pending_action_id ?? undefined,
            action: log.action_type,
            voice_session_id: log.voice_session_id,
            transcription_id: log.transcription_id,
            source_text: log.source_text,
            corrected_text: log.corrected_text,
            risk_level: log.risk_level,
            confirmed: log.user_confirmed,
            success: log.success,
            message:
              typeof log.verification_result.message === "string"
                ? log.verification_result.message
                : log.error_message,
            error_message: log.error_message,
            undoable: Boolean(
              log.success && log.pending_action_id && !log.verification_result.undone,
            ),
            undone: Boolean(log.verification_result.undone),
            created_at: log.created_at,
          })),
        }),
      ),
  },
};
