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
  UserSettingsUpdate,
  VerificationResult,
} from "@campusvoice/shared-types";

import { mergeContextHotwords } from "@/lib/asr/context-hotwords";
import { getAccessToken, handleUnauthorized } from "@/lib/auth";

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

interface WriteChallenge {
  challenge: string;
  stage: number;
  required_stages: number;
  expires_at: string;
}

export interface RadarCard {
  card_type: "new_notice" | "version_change" | "upcoming_deadline" | "needs_review";
  change_set_id: string | null;
  series_id: string;
  document_id: string | null;
  title: string;
  from_revision: number;
  to_revision: number;
  change_count: number;
  affected_tasks: number;
  affected_events: number;
  needs_review: boolean;
  message: string;
  deadline_at: string | null;
  applicability: "applicable" | "not_applicable" | "needs_review";
  applicability_reason: string | null;
  created_at: string;
}

export interface NoticeSeries {
  id: string;
  canonical_key: string;
  normalized_title: string;
  department: string | null;
  source_key: string | null;
  version_count: number;
  current_document_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface NoticeClaim {
  id: string;
  document_id: string;
  chunk_id: string;
  claim_key: string;
  claim_type: string;
  value: Record<string, unknown>;
  normalized_value: Record<string, unknown>;
  audience_rule: Record<string, unknown>;
  confidence: number;
  evidence_text: string;
  evidence_start: number;
  evidence_end: number;
  extractor_version: string;
  review_state: string;
}

export interface NoticeVersion {
  id: string;
  series_id: string;
  supersedes_document_id: string | null;
  revision_number: number;
  title: string;
  version_label: string;
  effective_at: string | null;
  publish_date: string | null;
  is_current: boolean;
  ingest_source: string;
  claims: NoticeClaim[];
  created_at: string;
}

export interface NoticeTimeline {
  series: NoticeSeries;
  versions: NoticeVersion[];
}

export interface NoticeSeriesCreate {
  canonical_key: string;
  title: string;
  department?: string | null;
  source_key?: string | null;
}

export interface NoticeVersionCreate {
  title: string;
  content: string;
  revision_number: number;
  version_label: string;
  supersedes_document_id: string | null;
  department?: string | null;
  publish_date?: string | null;
  effective_at?: string | null;
  applicable_group?: string | null;
  source_url?: string | null;
  ingest_source: "manual" | "seed" | "upload" | "api";
}

export interface ChangeEvidence {
  claim_id: string;
  document_id: string;
  chunk_id: string;
  value: Record<string, unknown>;
  normalized_value: Record<string, unknown>;
  evidence_text: string;
  evidence_start: number;
  evidence_end: number;
}

export interface NoticeChangeItem {
  id: string;
  claim_key: string;
  change_type: "added" | "removed" | "changed";
  severity: "low" | "medium" | "high";
  confidence: number;
  review_state: string;
  before: ChangeEvidence | null;
  after: ChangeEvidence | null;
}

export interface NoticeChangeSet {
  id: string;
  series_id: string;
  from_document_id: string;
  to_document_id: string;
  algorithm_version: string;
  status: string;
  items: NoticeChangeItem[];
  created_at: string;
}

export interface ImpactCase {
  id: string;
  change_item_id: string;
  entity_type: "task" | "event";
  entity_id: string;
  entity_version: number;
  reason: string;
  severity: string;
  current_snapshot: Record<string, unknown>;
  proposed_patch: Record<string, unknown>;
  recommended_action: "apply" | "keep" | "cancel" | "manual_review";
  requires_manual_review: boolean;
  status: string;
  migration_plan_id: string | null;
}

export interface MigrationItem {
  id: string;
  entity_type: "task" | "event";
  entity_id: string;
  expected_version: number;
  before: Record<string, unknown>;
  after: Record<string, unknown>;
  source_claim_ids: string[];
  verification: Record<string, unknown>;
  execute_verification: Record<string, unknown>;
  undo_verification: Record<string, unknown>;
}

export interface MigrationPlan {
  id: string;
  change_set_id: string;
  status: string;
  risk_level: "low" | "medium" | "high";
  required_confirmations: 1 | 2;
  conflicts: Array<Record<string, unknown>>;
  items: MigrationItem[];
  verification: Record<string, unknown>;
  execute_receipt: Record<string, unknown>;
  undo_receipt: Record<string, unknown>;
  generation: number;
  version: number;
  executed_at: string | null;
  undone_at: string | null;
}

export interface MigrationReceipt {
  plan_id: string;
  status: string;
  operation: "execute" | "undo";
  verified_count: number;
  total_count: number;
  all_verified: boolean;
  items: MigrationItem[];
  verified_at: string;
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
    if (this.status === 401) return "登录状态已失效，请重新登录。";
    if (this.status === 403) return this.message || "当前账户无权执行该操作。";
    if (this.status === 400 || this.status === 422)
      return this.message || "提交的信息不完整，请检查后重试。";
    if (this.status === 404) return this.message || "没有找到对应的数据。";
    if (this.status === 409) return this.message || "操作与现有数据冲突，请检查后再试。";
    if (this.status === 410) return "该操作已过期，请重新发起。";
    if (this.status === 428) return this.message || "该操作还需要用户确认。";
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
  const accessToken = getAccessToken();
  if (accessToken && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${accessToken}`);
  }

  try {
    const response = await fetch(`${API_BASE_URL}${path}`, {
      ...options,
      credentials: options.credentials ?? "include",
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
      if (response.status === 401) handleUnauthorized(API_BASE_URL);
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
          details: errorBody.detail ?? nested.details,
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

function idempotencyHeaders(): HeadersInit {
  return { "Idempotency-Key": crypto.randomUUID() };
}

const migrationIdempotencyFallback = new Map<string, string>();

function migrationIdempotencyKey(operation: "execute" | "undo", planId: string): string {
  const storageKey = `campusvoice:migration:${operation}:${planId}`;
  try {
    if (typeof window !== "undefined") {
      const existing = window.sessionStorage.getItem(storageKey);
      if (existing) {
        migrationIdempotencyFallback.set(storageKey, existing);
        return existing;
      }
      const fallback = migrationIdempotencyFallback.get(storageKey);
      if (fallback) {
        window.sessionStorage.setItem(storageKey, fallback);
        return fallback;
      }
      const generated = crypto.randomUUID();
      migrationIdempotencyFallback.set(storageKey, generated);
      window.sessionStorage.setItem(storageKey, generated);
      return generated;
    }
  } catch {
    // Some privacy modes expose sessionStorage but reject access. Keep the key
    // stable in this module so separated confirmation clicks bind one payload.
  }
  const existing = migrationIdempotencyFallback.get(storageKey);
  if (existing) return existing;
  const generated = crypto.randomUUID();
  migrationIdempotencyFallback.set(storageKey, generated);
  return generated;
}

async function issueWriteChallenge(
  method: "POST" | "PATCH" | "DELETE",
  path: string,
  body: unknown,
): Promise<WriteChallenge> {
  return request<WriteChallenge>("/api/auth/write-challenges", {
    method: "POST",
    ...jsonBody({ method, path, body }),
  });
}

async function confirmedJsonRequest<T>(
  method: "POST" | "PATCH",
  path: string,
  body: unknown,
  headers?: HeadersInit,
): Promise<T> {
  const issued = await issueWriteChallenge(method, path, body);
  if (issued.stage !== 1 || issued.required_stages !== 1) {
    throw new ApiError("写入确认策略与请求不匹配，操作未执行。", {
      status: 409,
      details: issued,
    });
  }
  const confirmedHeaders = new Headers(headers);
  confirmedHeaders.set("X-Write-Challenge", issued.challenge);
  return request<T>(path, {
    method,
    headers: confirmedHeaders,
    ...jsonBody(body),
  });
}

async function challengedJsonRequest<T>(
  method: "POST" | "PATCH",
  path: string,
  body: unknown,
): Promise<T> {
  const issued = await issueWriteChallenge(method, path, body);
  if (issued.stage !== 1 || issued.required_stages !== 1) {
    throw new ApiError("写入确认阶段不完整，操作未执行。", { status: 409, details: issued });
  }
  return request<T>(path, {
    method,
    headers: { "X-Write-Challenge": issued.challenge },
    ...jsonBody(body),
  });
}

/**
 * Resume database verification after the business transaction was already
 * committed. This may rebuild a two-stage challenge automatically because the
 * idempotency key can only return/repair the existing receipt; it cannot apply
 * the migration a second time. Initial high-risk writes must use begin/finish.
 */
async function recoveryJsonRequest<T>(
  method: "POST" | "PATCH",
  path: string,
  body: unknown,
): Promise<T> {
  let issued = await issueWriteChallenge(method, path, body);
  if (issued.required_stages === 2) {
    issued = await request<WriteChallenge>("/api/auth/write-challenges/advance", {
      method: "POST",
      ...jsonBody({ challenge: issued.challenge }),
    });
  }
  if (issued.stage !== issued.required_stages) {
    throw new ApiError("恢复验证的写入确认阶段不完整，操作未执行。", {
      status: 409,
      details: issued,
    });
  }
  return request<T>(path, {
    method,
    headers: { "X-Write-Challenge": issued.challenge },
    ...jsonBody(body),
  });
}

async function beginTwoStageWrite(
  method: "POST" | "PATCH" | "DELETE",
  path: string,
  body: unknown,
): Promise<WriteChallenge> {
  const first = await issueWriteChallenge(method, path, body);
  if (first.stage !== 1 || first.required_stages !== 2) {
    throw new ApiError("高风险操作未获得两阶段确认，操作未执行。", {
      status: 409,
      details: first,
    });
  }
  const second = await request<WriteChallenge>("/api/auth/write-challenges/advance", {
    method: "POST",
    ...jsonBody({ challenge: first.challenge }),
  });
  if (second.stage !== 2 || second.required_stages !== 2) {
    throw new ApiError("高风险操作的第二阶段确认无效，操作未执行。", {
      status: 409,
      details: second,
    });
  }
  return second;
}

function finishTwoStageWrite<T>(
  method: "POST" | "PATCH" | "DELETE",
  path: string,
  body: unknown,
  challenge: string,
): Promise<T> {
  return request<T>(path, {
    method,
    headers: { "X-Write-Challenge": challenge },
    ...jsonBody(body),
  });
}

function migrationExecuteBody(plan: MigrationPlan, allowConflicts: boolean) {
  return {
    plan_version: plan.version,
    idempotency_key: migrationIdempotencyKey("execute", plan.id),
    allow_conflicts: allowConflicts,
    confirmation_stages: plan.required_confirmations,
  };
}

function migrationUndoBody(plan: MigrationPlan) {
  return {
    plan_version: plan.version,
    idempotency_key: migrationIdempotencyKey("undo", plan.id),
    confirmation_stages: 2 as const,
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

async function prepareDestructive(
  action: "delete_task" | "delete_event",
  targetId: string,
): Promise<PendingAction> {
  return normalizePendingAction(
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
}

export const api = {
  auth: {
    session: () =>
      request<{
        authenticated: boolean;
        user_id: string;
        display_name: string;
        roles: string[];
        expires_at: string | null;
      }>("/api/auth/session"),
    logout: () => request<{ logout_url: string }>("/api/auth/logout", { method: "POST" }),
    websocketTicket: () =>
      request<{ ticket: string; expires_at: string }>("/api/auth/ws-ticket", { method: "POST" }),
  },

  health: () => request<HealthResponse>("/api/health", { timeoutMs: 5_000 }),

  radar: {
    list: (limit = 20) =>
      request<{ items: RadarCard[]; total: number }>(`/api/notice-radar${asQuery({ limit })}`),
    series: (limit = 100, offset = 0) =>
      request<NoticeSeries[]>(`/api/notice-radar/series${asQuery({ limit, offset })}`),
    createSeries: (body: NoticeSeriesCreate) =>
      challengedJsonRequest<NoticeSeries>("POST", "/api/notice-radar/series", body),
    timeline: (seriesId: string) =>
      request<NoticeTimeline>(`/api/notice-radar/series/${encodeURIComponent(seriesId)}/timeline`),
    addVersion: (seriesId: string, body: NoticeVersionCreate) =>
      challengedJsonRequest<NoticeVersion>(
        "POST",
        `/api/notice-radar/series/${encodeURIComponent(seriesId)}/versions`,
        body,
      ),
    changeSet: (id: string) =>
      request<NoticeChangeSet>(`/api/notice-radar/changes/${encodeURIComponent(id)}`),
    reviewChange: (itemId: string, decision: "approved" | "rejected") => {
      const path = `/api/notice-radar/changes/items/${encodeURIComponent(itemId)}/review`;
      return challengedJsonRequest<NoticeChangeItem>("PATCH", path, { decision });
    },
    impacts: (changeSetId: string, status?: "open" | "resolved" | "dismissed") =>
      request<{ items: ImpactCase[]; total: number }>(
        `/api/notice-radar/impacts${asQuery({ change_set_id: changeSetId, status })}`,
      ),
    detectImpacts: (changeSetId: string) => {
      const path = `/api/notice-radar/changes/${encodeURIComponent(changeSetId)}/impacts/detect`;
      return challengedJsonRequest<{ items: ImpactCase[]; total: number }>("POST", path, null);
    },
    preview: (changeSetId: string) => {
      const path = `/api/notice-radar/changes/${encodeURIComponent(changeSetId)}/migration-preview`;
      return challengedJsonRequest<MigrationPlan>("POST", path, null);
    },
    plan: (planId: string) =>
      request<MigrationPlan>(`/api/notice-radar/migrations/${encodeURIComponent(planId)}`),
    execute: (plan: MigrationPlan, allowConflicts: boolean) => {
      const path = `/api/notice-radar/migrations/${encodeURIComponent(plan.id)}/execute`;
      return challengedJsonRequest<MigrationReceipt>(
        "POST",
        path,
        migrationExecuteBody(plan, allowConflicts),
      );
    },
    beginExecute: (plan: MigrationPlan, allowConflicts: boolean) => {
      const path = `/api/notice-radar/migrations/${encodeURIComponent(plan.id)}/execute`;
      return beginTwoStageWrite("POST", path, migrationExecuteBody(plan, allowConflicts));
    },
    finishExecute: (plan: MigrationPlan, allowConflicts: boolean, challenge: string) => {
      const path = `/api/notice-radar/migrations/${encodeURIComponent(plan.id)}/execute`;
      return finishTwoStageWrite<MigrationReceipt>(
        "POST",
        path,
        migrationExecuteBody(plan, allowConflicts),
        challenge,
      );
    },
    resumeExecute: async (plan: MigrationPlan, allowConflicts: boolean) => {
      if (!["applied", "verification_failed"].includes(plan.status)) {
        throw new ApiError("迁移尚未进入可恢复的执行后验证状态。", {
          status: 409,
          details: { plan_id: plan.id, status: plan.status },
        });
      }
      const path = `/api/notice-radar/migrations/${encodeURIComponent(plan.id)}/execute`;
      return recoveryJsonRequest<MigrationReceipt>(
        "POST",
        path,
        migrationExecuteBody(plan, allowConflicts),
      );
    },
    receipt: (planId: string, operation: "execute" | "undo") =>
      request<MigrationReceipt>(
        `/api/notice-radar/migrations/${encodeURIComponent(planId)}/receipt${asQuery({ operation })}`,
      ),
    resumeUndo: async (plan: MigrationPlan) => {
      if (!["undo_applied", "undo_verification_failed"].includes(plan.status)) {
        throw new ApiError("迁移尚未进入可恢复的撤销后验证状态。", {
          status: 409,
          details: { plan_id: plan.id, status: plan.status },
        });
      }
      const path = `/api/notice-radar/migrations/${encodeURIComponent(plan.id)}/undo`;
      return recoveryJsonRequest<MigrationReceipt>("POST", path, migrationUndoBody(plan));
    },
    beginUndo: (plan: MigrationPlan) => {
      const path = `/api/notice-radar/migrations/${encodeURIComponent(plan.id)}/undo`;
      return beginTwoStageWrite("POST", path, migrationUndoBody(plan));
    },
    finishUndo: (plan: MigrationPlan, challenge: string) => {
      const path = `/api/notice-radar/migrations/${encodeURIComponent(plan.id)}/undo`;
      return finishTwoStageWrite<MigrationReceipt>(
        "POST",
        path,
        migrationUndoBody(plan),
        challenge,
      );
    },
  },

  tasks: {
    list: (
      filters: {
        status?: string;
        course?: string;
        due_from?: string;
        due_to?: string;
        limit?: number;
        offset?: number;
      } = {},
    ) => listRequest<Task>(`/api/tasks${asQuery(filters)}`),
    create: (data: TaskCreate) =>
      confirmedJsonRequest<MutationResult<Task>>("POST", "/api/tasks", data, idempotencyHeaders()),
    update: (id: string, data: TaskUpdate) =>
      confirmedJsonRequest<MutationResult<Task>>(
        "PATCH",
        `/api/tasks/${encodeURIComponent(id)}`,
        data,
        idempotencyHeaders(),
      ),
    remove: (id: string) => prepareDestructive("delete_task", id),
  },

  events: {
    list: (
      filters: {
        start?: string;
        end?: string;
        course?: string;
        limit?: number;
        offset?: number;
      } = {},
    ) =>
      listRequest<CalendarEvent>(
        `/api/events${asQuery({
          starts_after: filters.start,
          starts_before: filters.end,
          course: filters.course,
          limit: filters.limit,
          offset: filters.offset,
        })}`,
      ),
    create: (data: CalendarEventCreate) =>
      confirmedJsonRequest<MutationResult<CalendarEvent>>(
        "POST",
        "/api/events",
        data,
        idempotencyHeaders(),
      ),
    update: (id: string, data: CalendarEventUpdate) =>
      confirmedJsonRequest<MutationResult<CalendarEvent>>(
        "PATCH",
        `/api/events/${encodeURIComponent(id)}`,
        data,
        idempotencyHeaders(),
      ),
    remove: (id: string) => prepareDestructive("delete_event", id),
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
    create: (data: Pick<Hotword, "value" | "category">) => {
      const body = { term: data.value, category: data.category, source: "user", weight: 1 };
      return confirmedJsonRequest<MutationResult<WireHotword>>(
        "POST",
        "/api/hotwords",
        body,
        idempotencyHeaders(),
      ).then((payload) => {
        if (!payload.record)
          throw new ApiError("热词写入后未能查询到记录。", { status: 409, details: payload });
        return normalizeHotword(payload.record);
      });
    },
    beginRemove: async (id: string) => {
      const path = `/api/hotwords/${encodeURIComponent(id)}`;
      const first = await issueWriteChallenge("DELETE", path, null);
      if (first.stage !== 1 || first.required_stages !== 2) {
        throw new ApiError("热词删除未获得两阶段确认，操作未执行。", {
          status: 409,
          details: first,
        });
      }
      const second = await request<WriteChallenge>("/api/auth/write-challenges/advance", {
        method: "POST",
        ...jsonBody({ challenge: first.challenge }),
      });
      if (second.stage !== 2 || second.required_stages !== 2) {
        throw new ApiError("热词删除的第二阶段确认无效，操作未执行。", {
          status: 409,
          details: second,
        });
      }
      return second;
    },
    finishRemove: (id: string, challenge: string) =>
      request<MutationResult<WireHotword>>(`/api/hotwords/${encodeURIComponent(id)}`, {
        method: "DELETE",
        headers: { "X-Write-Challenge": challenge },
      }),
  },

  settings: {
    get: () => request<UserSettings>("/api/settings"),
    update: (data: UserSettingsUpdate) =>
      confirmedJsonRequest<{ settings: UserSettings }>(
        "PATCH",
        "/api/settings",
        data,
        idempotencyHeaders(),
      ).then((response) => response.settings),
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
    confirm: async (id: string, confirmed: boolean) => {
      const issued = await request<{ challenge: string; stage: number; expires_at: string }>(
        `/api/actions/${encodeURIComponent(id)}/challenge`,
        { method: "POST" },
      );
      return request<WirePendingAction>(`/api/actions/${encodeURIComponent(id)}/confirm`, {
        method: "POST",
        ...jsonBody({ confirmed, challenge: issued.challenge }),
      }).then(normalizePendingAction);
    },
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
