import { fromLocalInputValue, toLocalInputValue } from "@/lib/format";

export type LocalDayWindow = {
  dateKey: string;
  startMs: number;
  endMs: number;
};

export function localDateKey(date: Date, timezone: string) {
  return toLocalInputValue(date.toISOString(), timezone).slice(0, 10);
}

export function addLocalDays(dateKey: string, days: number) {
  const value = new Date(dateKey + "T00:00:00.000Z");
  value.setUTCDate(value.getUTCDate() + days);
  return value.toISOString().slice(0, 10);
}

export function firstValidInstantOfExactLocalDay(dateKey: string, timezone: string) {
  for (let minute = 0; minute < 24 * 60; minute += 1) {
    const hourText = String(Math.floor(minute / 60)).padStart(2, "0");
    const minuteText = String(minute % 60).padStart(2, "0");
    const value = fromLocalInputValue(dateKey + "T" + hourText + ":" + minuteText, timezone);
    if (value) return new Date(value).getTime();
  }
  return null;
}

export function firstValidInstantOfLocalDay(dateKey: string, timezone: string) {
  let candidateDate = dateKey;
  for (let dayAttempt = 0; dayAttempt < 3; dayAttempt += 1) {
    const value = firstValidInstantOfExactLocalDay(candidateDate, timezone);
    if (value !== null) return value;
    candidateDate = addLocalDays(candidateDate, 1);
  }
  throw new RangeError("Unable to resolve local day " + dateKey + " in " + timezone);
}

export function buildConsecutiveLocalDayWindows(
  now: Date,
  timezone: string,
  count = 7,
): LocalDayWindow[] {
  const nowMs = now.getTime();
  if (Number.isNaN(nowMs)) throw new RangeError("Local day windows require a valid current time");
  if (!Number.isInteger(count) || count < 1 || count > 31) {
    throw new RangeError("Local day window count must be between 1 and 31");
  }

  const boundaries: Array<{ dateKey: string; startMs: number }> = [];
  let candidateDate = localDateKey(now, timezone);
  const maxAttempts = count + 32;

  for (let attempt = 0; attempt < maxAttempts && boundaries.length < count + 1; attempt += 1) {
    const startMs = firstValidInstantOfExactLocalDay(candidateDate, timezone);
    const previous = boundaries.at(-1);
    if (startMs !== null && (!previous || startMs > previous.startMs)) {
      boundaries.push({ dateKey: candidateDate, startMs });
    }
    candidateDate = addLocalDays(candidateDate, 1);
  }

  if (boundaries.length !== count + 1) {
    throw new RangeError("Unable to build consecutive local days in " + timezone);
  }

  return boundaries.slice(0, count).map((boundary, index) => ({
    dateKey: boundary.dateKey,
    startMs: boundary.startMs,
    endMs: boundaries[index + 1]!.startMs,
  }));
}
