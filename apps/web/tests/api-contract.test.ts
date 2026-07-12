import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, api } from "@/lib/api-client";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("API wire contract adapters", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  it("normalizes the backend pending-action field names for the confirmation UI", async () => {
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse(
        {
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
          expires_at: "2026-07-12T12:00:00Z",
          attempt_count: 0,
          max_attempts: 2,
          last_error: null,
        },
        201,
      ),
    );

    const result = await api.actions.prepare({
      action: "create_task",
      payload: { title: "复习机器学习" },
    });
    expect(result).toMatchObject({
      action: "create_task",
      status: "awaiting_confirmation",
      risk_level: "medium",
      risk_reasons: ["writes_data"],
      confirmations_required: 1,
    });
  });

  it("surfaces the backend domain error message instead of a false success", async () => {
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse(
        {
          error: { code: "post_commit_verification_failed", message: "数据库最终状态验证失败" },
        },
        409,
      ),
    );

    const expected: Partial<ApiError> = {
      code: "post_commit_verification_failed",
      message: "数据库最终状态验证失败",
    };
    await expect(api.actions.execute("action-1")).rejects.toMatchObject(expected);
  });

  it("maps knowledge citations without inventing page numbers", async () => {
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse({
        question: "什么时候截止？",
        answer: "现有证据不足",
        sufficient_evidence: false,
        insufficiency_reason: "没有找到明确截止日期",
        citations: [
          {
            document_id: "doc-1",
            chunk_id: "chunk-1",
            original_text: "报名安排另行通知。",
            page_number: null,
            similarity: 0.73,
            file_title: "报名通知",
            publish_date: "2026-07-01",
            version: "v1",
            applicable_group: "2024级",
          },
        ],
        version_conflicts: [],
        applicability_conflicts: [{ title: "报名通知", applicable_groups: ["2023级", "2024级"] }],
      }),
    );

    const result = await api.knowledge.ask("什么时候截止？", {
      version: "v1",
      applicable_group: "2024级",
    });
    expect(result.sufficient).toBe(false);
    expect(result.message).toBe("没有找到明确截止日期");
    expect(result.evidence[0]).toMatchObject({
      page: null,
      document_title: "报名通知",
      applicable_group: "2024级",
    });
    expect(result.applicability_conflicts?.[0]?.applicable_groups).toEqual(["2023级", "2024级"]);
    expect(JSON.parse(String(vi.mocked(fetch).mock.calls[0]?.[1]?.body))).toMatchObject({
      version: "v1",
      applicable_group: "2024级",
    });
  });

  it("sends explicit confirmation and unwraps verified settings updates", async () => {
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse({
        success: true,
        verified_fields: { major: true },
        message: "设置已更新并验证",
        settings: {
          major: "人工智能",
          grade: "2024级",
          current_courses: [{ id: "course-1", code: "AI301", name: "机器学习", teacher: "张老师" }],
          teacher_names: ["张老师"],
          default_reminder_minutes: 30,
          timezone: "Asia/Shanghai",
          asr_provider: "funasr",
          asr_model: "paraformer-zh-streaming",
          asr_device: "cuda:0",
        },
      }),
    );

    const settings = await api.settings.update({
      major: "人工智能",
      current_courses: [{ code: "AI301", name: "机器学习", teacher: "张老师" }],
      asr_device: "cuda:0",
    });

    expect(settings.major).toBe("人工智能");
    expect(settings.current_courses[0]).toEqual({
      id: "course-1",
      code: "AI301",
      name: "机器学习",
      teacher: "张老师",
    });
    const options = vi.mocked(fetch).mock.calls[0]?.[1];
    expect(new Headers(options?.headers).get("X-User-Confirmed")).toBe("true");
    expect(JSON.parse(String(options?.body))).toMatchObject({
      current_courses: [{ code: "AI301", name: "机器学习", teacher: "张老师" }],
      asr_device: "cuda:0",
    });
  });

  it("persists an explicit terminology decision before continuing", async () => {
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse({
        id: "correction-1",
        corrected_text: "复习机器学习重点",
        user_confirmed: true,
      }),
    );

    const decision = await api.correction.decide("correction-1", "复习机器学习重点", true);

    expect(decision.user_confirmed).toBe(true);
    expect(vi.mocked(fetch).mock.calls[0]?.[0]).toContain("/api/correction/correction-1/decision");
  });

  it("sends persisted conversation and title-resolution fields", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      jsonResponse({
        intent: "delete_task",
        confidence: 0.8,
        slots: { title: "机器学习作业" },
        missing_fields: [],
        ambiguities: [],
        source_text: "删除机器学习作业",
        requires_confirmation: true,
        conversation_id: "cnv-1",
      }),
    );

    const parsed = await api.intent.parse("机器学习作业", [], 0.76, "cnv-1");
    expect(parsed.conversation_id).toBe("cnv-1");
    expect(JSON.parse(String(vi.mocked(fetch).mock.calls[0]?.[1]?.body))).toMatchObject({
      asr_confidence: 0.76,
      conversation_id: "cnv-1",
    });

    vi.mocked(fetch).mockResolvedValueOnce(
      jsonResponse(
        {
          id: "action-1",
          action_type: "delete_task",
          entity_type: "task",
          target_id: "task-1",
          payload: {},
          state: "awaiting_confirmation",
          risk_level: "high",
          risk_factors: ["deletes_data"],
          missing_fields: [],
          ambiguities: [],
          blocking_reasons: [],
          diagnostics: { target_resolution: "unique_title_match" },
          required_confirmations: 2,
          confirmations_received: 0,
          expires_at: "2026-07-12T12:00:00Z",
          attempt_count: 0,
          max_attempts: 2,
          last_error: null,
        },
        201,
      ),
    );
    await api.actions.prepare({
      action: "delete_task",
      target_title: "机器学习作业",
      payload: {},
    });
    expect(JSON.parse(String(vi.mocked(fetch).mock.calls[1]?.[1]?.body))).toMatchObject({
      target_title: "机器学习作业",
    });
  });

  it("adds settings context and the durable transcription id to correction preview", async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse({ items: [], total: 0 }))
      .mockResolvedValueOnce(
        jsonResponse({
          major: "人工智能",
          grade: "2024",
          current_courses: [{ code: "AI301", name: "机器学习", teacher: "张老师" }],
          teacher_names: ["李老师"],
          default_reminder_minutes: 30,
          timezone: "Asia/Shanghai",
          asr_provider: "funasr",
          asr_model: "paraformer-zh-streaming",
          asr_device: "cpu",
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          record: {
            id: "correction-2",
            original_text: "复习机器学习",
            corrected_text: "复习机器学习",
            modifications: [],
            candidates: [],
          },
          requires_user_input: false,
        }),
      );

    await api.correction.preview("复习机器学习", 0.9, "trn-2");

    const body = JSON.parse(String(vi.mocked(fetch).mock.calls[2]?.[1]?.body)) as {
      transcription_id: string;
      current_courses: string[];
      terms: Array<{ term: string; source: string }>;
    };
    expect(body.transcription_id).toBe("trn-2");
    expect(body.current_courses).toEqual(["机器学习", "AI301"]);
    expect(body.terms).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ term: "机器学习", source: "course" }),
        expect.objectContaining({ term: "张老师", source: "teacher" }),
        expect.objectContaining({ term: "李老师", source: "teacher" }),
      ]),
    );
  });

  it("exposes persisted voice lineage and undo state from action logs", async () => {
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse({
        total: 1,
        items: [
          {
            id: "log-1",
            pending_action_id: "action-1",
            voice_session_id: "voice-1",
            transcription_id: "trn-1",
            action_type: "create_event",
            risk_level: "medium",
            user_confirmed: true,
            success: true,
            error_message: null,
            source_text: "原始转写",
            corrected_text: "纠正文本",
            before_snapshot: null,
            verification_result: { message: "已验证", undone: true },
            created_at: "2026-07-12T12:00:00Z",
          },
        ],
      }),
    );

    const log = (await api.actionLogs.list()).items[0];
    expect(log).toMatchObject({
      voice_session_id: "voice-1",
      transcription_id: "trn-1",
      source_text: "原始转写",
      corrected_text: "纠正文本",
      undone: true,
      undoable: false,
    });
  });
});
