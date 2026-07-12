import type { Hotword, UserSettings } from "@campusvoice/shared-types";

export type ContextHotword = Pick<Hotword, "value" | "category">;

function clean(value: string | null | undefined) {
  const term = value?.trim();
  return term ? term : null;
}

export function settingsContextHotwords(settings?: UserSettings | null): ContextHotword[] {
  if (!settings) return [];
  const items: ContextHotword[] = [];
  for (const course of settings.current_courses) {
    const name = clean(course.name);
    const code = clean(course.code);
    const teacher = clean(course.teacher);
    if (name) items.push({ value: name, category: "course" });
    if (code) items.push({ value: code, category: "course_code" });
    if (teacher) items.push({ value: teacher, category: "teacher" });
  }
  for (const teacherName of settings.teacher_names) {
    const teacher = clean(teacherName);
    if (teacher) items.push({ value: teacher, category: "teacher" });
  }
  return items;
}

export function mergeContextHotwords(
  personal: readonly ContextHotword[],
  settings?: UserSettings | null,
): ContextHotword[] {
  const merged: ContextHotword[] = [];
  const seen = new Set<string>();
  for (const item of [...personal, ...settingsContextHotwords(settings)]) {
    const value = clean(item.value);
    if (!value || seen.has(value)) continue;
    seen.add(value);
    merged.push({ value, category: item.category });
  }
  return merged;
}

export function asrHotwordValues(
  personal: readonly ContextHotword[],
  settings?: UserSettings | null,
) {
  return mergeContextHotwords(personal, settings)
    .map((item) => item.value)
    .slice(0, 500);
}
