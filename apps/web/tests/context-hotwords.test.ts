import type { UserSettings } from "@campusvoice/shared-types";
import { describe, expect, it } from "vitest";

import { asrHotwordValues, mergeContextHotwords } from "@/lib/asr/context-hotwords";

const settings: UserSettings = {
  major: "人工智能",
  grade: "2024",
  current_courses: [{ code: "AI301", name: "机器学习", teacher: "张老师" }],
  teacher_names: ["张老师", "李老师"],
  default_reminder_minutes: 30,
  timezone: "Asia/Shanghai",
  asr_provider: "funasr",
  asr_model: "paraformer-zh-streaming",
  asr_device: "cpu",
};

describe("settings context hotwords", () => {
  it("merges courses, course codes and teachers without duplicates", () => {
    const personal = [{ value: "机器学习", category: "custom" as const }];

    expect(asrHotwordValues(personal, settings)).toEqual(["机器学习", "AI301", "张老师", "李老师"]);
    expect(mergeContextHotwords(personal, settings)).toContainEqual({
      value: "AI301",
      category: "course_code",
    });
  });
});
