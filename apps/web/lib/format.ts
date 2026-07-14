import { getCurrentUserSettings } from "@/lib/user-settings";

function currentTimeZone() {
  return getCurrentUserSettings().timezone;
}

function dateTimeParts(date: Date, timeZone: string) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  }).formatToParts(date);
  return Object.fromEntries(parts.map((part) => [part.type, part.value]));
}

function timeZoneOffset(date: Date, timeZone: string) {
  const parts = dateTimeParts(date, timeZone);
  const localAsUtc = Date.UTC(
    Number(parts.year),
    Number(parts.month) - 1,
    Number(parts.day),
    Number(parts.hour),
    Number(parts.minute),
    Number(parts.second),
  );
  return localAsUtc - Math.floor(date.getTime() / 1000) * 1000;
}

export function formatDateTime(
  value: string | null | undefined,
  options: Intl.DateTimeFormatOptions = {},
) {
  if (!value) return "未设置";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间格式无效";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: options.timeZone ?? currentTimeZone(),
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    ...options,
  }).format(date);
}

export function formatDate(value: string | null | undefined, timeZone = currentTimeZone()) {
  if (value && /^\d{4}-\d{2}-\d{2}$/.test(value)) {
    const date = new Date(`${value}T00:00:00.000Z`);
    if (Number.isNaN(date.getTime())) return "时间格式无效";
    return new Intl.DateTimeFormat("zh-CN", {
      timeZone: "UTC",
      year: "numeric",
      month: "long",
      day: "numeric",
    }).format(date);
  }
  return formatDateTime(value, {
    timeZone,
    year: "numeric",
    month: "long",
    day: "numeric",
    hour: undefined,
    minute: undefined,
  });
}

export function formatTime(value: string | null | undefined, timeZone = currentTimeZone()) {
  return formatDateTime(value, {
    timeZone,
    month: undefined,
    day: undefined,
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function toLocalInputValue(value: string | null | undefined, timeZone = currentTimeZone()) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const parts = dateTimeParts(date, timeZone);
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`;
}

export function fromLocalInputValue(value: string, timeZone = currentTimeZone()) {
  if (!value) return null;
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(value);
  if (!match) return null;
  const [, year, month, day, hour, minute] = match;
  const localAsUtc = Date.UTC(
    Number(year),
    Number(month) - 1,
    Number(day),
    Number(hour),
    Number(minute),
  );
  let instant = localAsUtc - timeZoneOffset(new Date(localAsUtc), timeZone);
  instant = localAsUtc - timeZoneOffset(new Date(instant), timeZone);
  const result = new Date(instant);
  if (
    Number.isNaN(result.getTime()) ||
    toLocalInputValue(result.toISOString(), timeZone) !== value
  ) {
    return null;
  }
  return result.toISOString();
}

export function sameDayInTimeZone(
  value: string | null | undefined,
  day: Date,
  timeZone = currentTimeZone(),
) {
  if (!value) return false;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return false;
  const valueParts = dateTimeParts(date, timeZone);
  const dayParts = dateTimeParts(day, timeZone);
  return (
    valueParts.year === dayParts.year &&
    valueParts.month === dayParts.month &&
    valueParts.day === dayParts.day
  );
}

export function relativeTime(value: string) {
  const timestamp = new Date(value).getTime();
  if (Number.isNaN(timestamp)) return "未知时间";
  const deltaSeconds = Math.round((timestamp - Date.now()) / 1000);
  const formatter = new Intl.RelativeTimeFormat("zh-CN", { numeric: "auto" });
  if (Math.abs(deltaSeconds) < 60) return formatter.format(deltaSeconds, "second");
  const minutes = Math.round(deltaSeconds / 60);
  if (Math.abs(minutes) < 60) return formatter.format(minutes, "minute");
  const hours = Math.round(minutes / 60);
  if (Math.abs(hours) < 24) return formatter.format(hours, "hour");
  return formatter.format(Math.round(hours / 24), "day");
}
