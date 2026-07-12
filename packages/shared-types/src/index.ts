export type ISODateTime = string;

export type TaskStatus = "pending" | "in_progress" | "completed" | "cancelled";
export type TaskPriority = "low" | "medium" | "high";
export type SourceType = "manual" | "voice" | "document" | "system";

export interface Task {
  id: string;
  course_id?: string | null;
  title: string;
  description: string | null;
  course: string | null;
  due_at: ISODateTime | null;
  reminder_at: ISODateTime | null;
  priority: TaskPriority;
  status: TaskStatus;
  source_type: SourceType;
  source_document_id: string | null;
  created_at: ISODateTime;
  updated_at: ISODateTime;
  version: number;
}

export type TaskCreate = Pick<Task, "title"> &
  Partial<
    Pick<
      Task,
      | "description"
      | "course"
      | "due_at"
      | "reminder_at"
      | "priority"
      | "source_type"
      | "source_document_id"
    >
  >;
export type TaskUpdate = Partial<Omit<TaskCreate, "source_type"> & Pick<Task, "status">> & {
  expected_version?: number;
};

export interface CalendarEvent {
  id: string;
  course_id?: string | null;
  title: string;
  description: string | null;
  course: string | null;
  start_at: ISODateTime;
  end_at: ISODateTime | null;
  location: string | null;
  reminder_minutes: number | null;
  source_type: SourceType;
  source_document_id: string | null;
  created_at: ISODateTime;
  updated_at: ISODateTime;
  version: number;
}

export type CalendarEventCreate = Pick<CalendarEvent, "title" | "start_at"> &
  Partial<
    Pick<
      CalendarEvent,
      | "description"
      | "course"
      | "end_at"
      | "location"
      | "reminder_minutes"
      | "source_type"
      | "source_document_id"
    >
  >;
export type CalendarEventUpdate = Partial<Omit<CalendarEventCreate, "source_type">> & {
  expected_version?: number;
};

export interface EventConflict {
  event_id: string;
  title: string;
  start_at: ISODateTime;
  end_at: ISODateTime | null;
  overlap_minutes?: number;
}

export interface DocumentRecord {
  id: string;
  title: string;
  department: string | null;
  publish_date: string | null;
  applicable_group: string | null;
  source_url: string | null;
  version: string | null;
  file_type: "pdf" | "docx" | "txt" | "md";
  status?: "uploaded" | "processing" | "ready" | "failed";
  chunk_count?: number;
  created_at: ISODateTime;
}

export interface KnowledgeEvidence {
  document_id: string;
  chunk_id: string;
  content: string;
  page: number | null;
  similarity: number;
  document_title: string;
  publish_date: string | null;
  version?: string | null;
  applicable_group?: string | null;
}

export interface KnowledgeVersionConflict {
  document_title: string;
  versions: string[];
}

export interface KnowledgeApplicabilityConflict {
  document_title: string;
  applicable_groups: string[];
}

export interface KnowledgeSearchResult {
  evidence: KnowledgeEvidence[];
  version_conflicts: KnowledgeVersionConflict[];
  applicability_conflicts: KnowledgeApplicabilityConflict[];
}

export interface KnowledgeAnswer {
  answer: string | null;
  sufficient: boolean;
  evidence: KnowledgeEvidence[];
  version_conflicts?: KnowledgeVersionConflict[];
  applicability_conflicts?: KnowledgeApplicabilityConflict[];
  message?: string;
}

export interface Hotword {
  id: string;
  value: string;
  category: "course" | "course_code" | "teacher" | "ai_term" | "custom" | "document";
  source?: string | null;
  active?: boolean;
  created_at?: ISODateTime;
}

export interface CoursePreference {
  id?: string | null;
  code?: string | null;
  name?: string | null;
  teacher?: string | null;
}

export interface UserSettings {
  major: string | null;
  grade: string | null;
  current_courses: CoursePreference[];
  teacher_names: string[];
  default_reminder_minutes: number;
  timezone: string;
  asr_provider: string;
  asr_model: string;
  asr_device: string;
  updated_at?: ISODateTime;
}

export type IntentName =
  | "create_task"
  | "update_task"
  | "delete_task"
  | "create_event"
  | "update_event"
  | "delete_event"
  | "search_notice"
  | "query_schedule"
  | "unknown";

export interface IntentResult {
  intent: IntentName;
  confidence: number;
  slots: Record<string, string | number | boolean | null | string[]>;
  missing_fields: string[];
  ambiguities: string[];
  source_text: string;
  requires_confirmation: boolean;
  conversation_id?: string | null;
}

export interface CorrectionChange {
  start: number;
  end: number;
  original: string;
  corrected: string;
  candidates: string[];
  reason: string;
  confidence: number;
  requires_confirmation: boolean;
}

export interface CorrectionResult {
  record_id: string;
  original_text: string;
  corrected_text: string;
  changes: CorrectionChange[];
  requires_user_input?: boolean;
}

export type RiskLevel = "low" | "medium" | "high";
export type PendingActionStatus =
  | "needs_input"
  | "awaiting_confirmation"
  | "awaiting_second_confirmation"
  | "ready"
  | "executing"
  | "executed"
  | "cancelled"
  | "failed"
  | "undone"
  | "expired";

export interface PendingAction {
  id: string;
  action: IntentName;
  title?: string;
  summary?: string;
  risk_level: RiskLevel;
  risk_reasons: string[];
  payload: Record<string, unknown>;
  status: PendingActionStatus;
  requires_second_confirmation?: boolean;
  confirmation_count?: number;
  confirmations_required?: number;
  confirmation_token?: string;
  expires_at?: ISODateTime;
  missing_fields?: string[];
  ambiguities?: string[];
  clarification_question?: string | null;
  blocking_reasons?: string[];
  diagnostics?: Record<string, unknown>;
}

export interface VerificationResult {
  success: boolean;
  action: IntentName | string;
  record_id: string | null;
  verified_fields: Record<string, boolean>;
  side_effects: string[];
  message: string;
  failure_reason?: string | null;
  retryable?: boolean;
  record?: Task | CalendarEvent | null;
}

export interface ActionPrepareRequest {
  action: IntentName;
  target_id?: string;
  target_title?: string;
  payload: Record<string, unknown>;
  asr_confidence?: number;
  missing_fields?: string[];
  ambiguities?: string[];
  batch_size?: number;
  overwrite_existing?: boolean;
  hard_to_undo?: boolean;
  idempotency_key?: string;
  source_text?: string;
  corrected_text?: string;
  voice_session_id?: string;
  transcription_id?: string;
}

export interface MutationResult<T> extends Omit<VerificationResult, "record"> {
  record?: T | null;
}

export interface ListResponse<T> {
  items: T[];
  total: number;
}

export interface ActionLog {
  id: string;
  action_id?: string;
  action: IntentName | string;
  voice_session_id?: string | null;
  transcription_id?: string | null;
  source_text?: string | null;
  corrected_text?: string | null;
  risk_level: RiskLevel;
  confirmed: boolean;
  success: boolean | null;
  message: string | null;
  error_message?: string | null;
  undoable?: boolean;
  undone?: boolean;
  created_at: ISODateTime;
}

export interface HealthResponse {
  status: "ok" | "degraded" | "error" | string;
  service?: string;
  version?: string;
  timestamp?: ISODateTime;
  checks?: Record<string, "ok" | "degraded" | "error" | string>;
}

export interface ApiErrorBody {
  detail?: string | Array<{ loc?: Array<string | number>; msg: string; type?: string }>;
  message?: string;
  code?: string;
  request_id?: string;
}

export interface AsrStartMessage {
  type: "start";
  sample_rate_hz: 16000;
  channels: 1;
  sample_width_bytes: 2;
  language: "zh";
  hotwords: string[];
}

export type AsrClientMessage = AsrStartMessage | { type: "flush" | "stop" | "ping" };

interface AsrServerMetadata {
  session_id?: string;
  sequence?: number;
  provider?: string;
}

export interface AsrTranscriptReference {
  sessionId: string | null;
  transcriptionId: string | null;
  originalText: string;
}

export type AsrServerMessage = AsrServerMetadata &
  (
    | { type: "ready" }
    | { type: "speech_start"; timestamp_ms?: number }
    | {
        type: "interim" | "final";
        text: string;
        confidence?: number;
        latency_ms?: number;
        transcription_id?: string;
      }
    | { type: "speech_end"; timestamp_ms?: number }
    | { type: "completed" }
    | { type: "error"; code?: string; message: string; retryable?: boolean }
  );
