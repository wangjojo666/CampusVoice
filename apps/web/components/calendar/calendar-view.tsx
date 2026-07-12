"use client";

import type { CalendarEvent } from "@campusvoice/shared-types";
import zhCnLocale from "@fullcalendar/core/locales/zh-cn";
import dayGridPlugin from "@fullcalendar/daygrid";
import interactionPlugin from "@fullcalendar/interaction";
import FullCalendar from "@fullcalendar/react";
import timeGridPlugin from "@fullcalendar/timegrid";

export function CalendarView({
  events,
  onEventClick,
  onDateClick,
}: Readonly<{
  events: CalendarEvent[];
  onEventClick: (event: CalendarEvent) => void;
  onDateClick: (date: Date) => void;
}>) {
  const byId = new Map(events.map((event) => [event.id, event]));
  return (
    <FullCalendar
      plugins={[dayGridPlugin, timeGridPlugin, interactionPlugin]}
      initialView="dayGridMonth"
      locale={zhCnLocale}
      firstDay={1}
      height="auto"
      nowIndicator
      selectable
      dayMaxEvents
      headerToolbar={{
        left: "prev,next today",
        center: "title",
        right: "dayGridMonth,timeGridWeek",
      }}
      buttonText={{ today: "今天", month: "月", week: "周" }}
      events={events.map((event) => ({
        id: event.id,
        title: event.title,
        start: event.start_at,
        end: event.end_at ?? undefined,
        backgroundColor: event.course ? "#159b82" : "#52636f",
        borderColor: event.course ? "#159b82" : "#52636f",
      }))}
      eventClick={({ event }) => {
        const source = byId.get(event.id);
        if (source) onEventClick(source);
      }}
      dateClick={({ date }) => onDateClick(date)}
    />
  );
}
