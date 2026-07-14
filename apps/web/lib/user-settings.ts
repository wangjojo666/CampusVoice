"use client";

import type { UserSettings } from "@campusvoice/shared-types";
import { useSyncExternalStore } from "react";

export const DEFAULT_USER_SETTINGS: UserSettings = {
  major: null,
  grade: null,
  current_courses: [],
  teacher_names: [],
  default_reminder_minutes: 30,
  timezone: "Asia/Shanghai",
  asr_provider: "disabled",
  asr_model: "",
  asr_device: "",
};

let currentSettings = DEFAULT_USER_SETTINGS;
const listeners = new Set<() => void>();

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function setCurrentUserSettings(settings: UserSettings) {
  currentSettings = settings;
  listeners.forEach((listener) => listener());
}

export function getCurrentUserSettings() {
  return currentSettings;
}

export function useUserSettings() {
  return useSyncExternalStore(subscribe, getCurrentUserSettings, getCurrentUserSettings);
}
