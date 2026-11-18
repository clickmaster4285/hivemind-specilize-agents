/** Scheduled tasks in words (BACKLOG L26 H1): "Every day at 08:00", shared by the Repeat dialog and /schedules. */

import type { Schedule, ScheduleEvery } from "@/lib/api";

export const EVERY_LABELS: Record<ScheduleEvery, string> = { hour: "Every hour", day: "Every day", week: "Every week" };
/** weekday 0 = Monday, as Python's date.weekday() (src/jobs/schedules.py) */
export const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const MINUTES_PER_HOUR = 60;

const pad = (n: number) => String(n).padStart(2, "0");

/** minutes after midnight → "08:05" (the value an <input type="time"> takes) */
export function clockText(minutes: number): string {
  return `${pad(Math.floor(minutes / MINUTES_PER_HOUR))}:${pad(minutes % MINUTES_PER_HOUR)}`;
}

/** "08:05" → minutes after midnight, or null */
export function clockMinutes(text: string): number | null {
  const match = /^(\d{1,2}):(\d{2})$/.exec(text.trim());
  if (!match) return null;
  const [h, m] = [Number(match[1]), Number(match[2])];
  return h < 24 && m < MINUTES_PER_HOUR ? h * MINUTES_PER_HOUR + m : null;
}

export function describeSchedule(s: Pick<Schedule, "every" | "at_minute" | "weekday">): string {
  if (s.every === "hour") return `Every hour at :${pad(s.at_minute)}`;
  if (s.every === "week") return `Every ${WEEKDAYS[s.weekday ?? 0]} at ${clockText(s.at_minute)}`;
  return `Every day at ${clockText(s.at_minute)}`;
}

/** The browser's own zone: the schedule's times are wall-clock times in it. */
export function browserTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}
