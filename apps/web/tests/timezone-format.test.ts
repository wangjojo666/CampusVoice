import { afterEach, describe, expect, it } from "vitest";

import {
  formatDate,
  formatDateTime,
  fromLocalInputValue,
  sameDayInTimeZone,
  toLocalInputValue,
} from "@/lib/format";
import { DEFAULT_USER_SETTINGS, setCurrentUserSettings } from "@/lib/user-settings";

afterEach(() => setCurrentUserSettings(DEFAULT_USER_SETTINGS));

describe("user timezone formatting", () => {
  it("round-trips datetime-local values in a non-Shanghai timezone", () => {
    const instant = fromLocalInputValue("2026-07-18T09:30", "America/New_York");

    expect(instant).toBe("2026-07-18T13:30:00.000Z");
    expect(toLocalInputValue(instant, "America/New_York")).toBe("2026-07-18T09:30");
    expect(formatDateTime(instant, { timeZone: "America/New_York" })).toContain("09:30");
  });

  it("compares calendar days in the selected timezone instead of the browser timezone", () => {
    const reference = new Date("2026-07-18T00:30:00.000Z");

    expect(sameDayInTimeZone("2026-07-17T23:00:00.000Z", reference, "Asia/Shanghai")).toBe(true);
    expect(sameDayInTimeZone("2026-07-17T23:00:00.000Z", reference, "America/New_York")).toBe(true);
    expect(sameDayInTimeZone("2026-07-18T05:00:00.000Z", reference, "America/New_York")).toBe(
      false,
    );
  });

  it("uses the latest shared user timezone when callers omit an override", () => {
    setCurrentUserSettings({ ...DEFAULT_USER_SETTINGS, timezone: "America/New_York" });

    expect(toLocalInputValue("2026-07-18T13:30:00.000Z")).toBe("2026-07-18T09:30");
  });

  it("does not shift a date-only value across days", () => {
    expect(formatDate("2026-07-18", "America/Los_Angeles")).toContain("2026年7月18日");
  });
});
