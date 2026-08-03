"use client";

import type { CalendarEvent } from "@campusvoice/shared-types";
import type { DatesSetArg } from "@fullcalendar/core";
import zhCnLocale from "@fullcalendar/core/locales/zh-cn";
import dayGridPlugin from "@fullcalendar/daygrid";
import interactionPlugin from "@fullcalendar/interaction";
import FullCalendar from "@fullcalendar/react";
import timeGridPlugin from "@fullcalendar/timegrid";
import { useEffect, useRef, useState } from "react";

import { fromLocalInputValue, toLocalInputValue } from "@/lib/format";
import { useUserSettings } from "@/lib/user-settings";
export type CalendarRange = {
  start: string;
  end: string;
};

export function CalendarView({
  events,
  onEventClick,
  onDateClick,
  onRangeChange,
}: Readonly<{
  events: CalendarEvent[];
  onEventClick: (event: CalendarEvent) => void;
  onDateClick: (date: Date) => void;
  onRangeChange: (range: CalendarRange) => void;
}>) {
  const userSettings = useUserSettings();
  const calendarRef = useRef<FullCalendar>(null);
  const [compact, setCompact] = useState(
    () =>
      typeof window !== "undefined" && Boolean(window.matchMedia?.("(max-width: 640px)").matches),
  );

  useEffect(() => {
    const query = window.matchMedia?.("(max-width: 640px)");
    if (!query) return;
    const update = () => setCompact(query.matches);
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);

  useEffect(() => {
    const api = calendarRef.current?.getApi();
    if (compact && api?.view.type === "timeGridWeek") api.changeView("dayGridDay");
  }, [compact]);

  const byId = new Map(events.map((event) => [event.id, event]));
  return (
    <FullCalendar
      ref={calendarRef}
      plugins={[dayGridPlugin, timeGridPlugin, interactionPlugin]}
      initialView="dayGridMonth"
      locale={zhCnLocale}
      firstDay={1}
      height="auto"
      nowIndicator
      selectable
      dayMaxEvents
      headerToolbar={
        compact
          ? { left: "prev,title,next", center: "", right: "today dayGridMonth,dayGridDay" }
          : { left: "prev,next today", center: "title", right: "dayGridMonth,timeGridWeek" }
      }
      buttonText={{ today: "今天", month: "月", week: "周", day: "日" }}
      events={events.map((event) => ({
        id: event.id,
        title: event.title,
        start: toLocalInputValue(event.start_at, userSettings.timezone),
        end: toLocalInputValue(event.end_at, userSettings.timezone) || undefined,
        backgroundColor: event.course ? "#159b82" : "#52636f",
        borderColor: event.course ? "#159b82" : "#52636f",
      }))}
      datesSet={({ startStr, endStr }: DatesSetArg) => {
        const start = fromLocalInputValue(`${startStr.slice(0, 10)}T00:00`, userSettings.timezone);
        const end = fromLocalInputValue(`${endStr.slice(0, 10)}T00:00`, userSettings.timezone);
        if (start && end) onRangeChange({ start, end });
      }}
      eventClick={({ event }) => {
        const source = byId.get(event.id);
        if (source) onEventClick(source);
      }}
      dateClick={({ dateStr }) => {
        const localValue = dateStr.length === 10 ? `${dateStr}T00:00` : dateStr.slice(0, 16);
        const instant = fromLocalInputValue(localValue, userSettings.timezone);
        if (instant) onDateClick(new Date(instant));
      }}
    />
  );
}
