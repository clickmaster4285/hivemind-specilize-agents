"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { createSchedule, type ScheduleEvery } from "@/lib/api";
import { errorText } from "@/lib/chat";
import { SCHEDULES_PATH } from "@/lib/routes";
import { browserTimeZone, clockMinutes, EVERY_LABELS, WEEKDAYS } from "@/lib/schedules";
import { btn, formField, formLabel, iconBtn } from "@/lib/ui";

/** A new schedule starts at a morning time: a daily digest is the usual ask */
const DEFAULT_AT = "08:00";
const MINUTES_PER_HOUR = 60;

/** "Repeat…" (a chat's menu, an answer's 🔁, the hint under a check — BACKLOG L26 H1): ask a question again every hour,
 *  day or week, in the browser's time zone. Each answer goes to the schedule's own chat; the run is unattended, so it
 *  changes nothing on servers. */
export default function RepeatDialog({
  prompt: initialPrompt,
  every: initialEvery = "day",
  onClose,
  onSaved,
}: {
  prompt: string;
  every?: ScheduleEvery;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [prompt, setPrompt] = useState(initialPrompt);
  const [every, setEvery] = useState<ScheduleEvery>(initialEvery);
  const [at, setAt] = useState(DEFAULT_AT);
  const [minute, setMinute] = useState(0);
  const [weekday, setWeekday] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const zone = browserTimeZone();
  const router = useRouter();

  async function save(e: React.FormEvent) {
    e.preventDefault();
    // results come to the user (ISSUES_LOG O99): ask once, inside this click, as browsers require
    if (typeof Notification !== "undefined" && Notification.permission === "default") {
      void Notification.requestPermission();
    }
    const atMinute = every === "hour" ? minute : clockMinutes(at);
    if (atMinute === null) {
      setError("Pick a time.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await createSchedule({
        prompt: prompt.trim(), every, at_minute: atMinute,
        weekday: every === "week" ? weekday : null, time_zone: zone,
      });
      onSaved();
      router.push(SCHEDULES_PATH);          // the new schedule is there, with its next run
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-backdrop p-4 backdrop-blur-[4px]" onClick={onClose}>
      <form
        className="flex w-[min(520px,100%)] flex-col gap-3 rounded-2xl border border-line bg-panel px-[1.1rem] pt-4 pb-[1.1rem] shadow-shadow"
        onClick={(e) => e.stopPropagation()}
        onSubmit={save}
      >
        <header className="flex items-center justify-between gap-3">
          <h3 className="m-0 text-[1.15rem] [font-family:var(--font-display)]">Repeat this question</h3>
          <button type="button" className={iconBtn()} aria-label="Close" onClick={onClose}>✕</button>
        </header>
        <label className={formLabel}>
          Question to ask
          <textarea className={formField} rows={3} value={prompt} required onChange={(e) => setPrompt(e.target.value)} />
        </label>
        <div className="grid gap-3 sm:grid-cols-3">
          <label className={formLabel}>
            How often
            <select className={formField} value={every} onChange={(e) => setEvery(e.target.value as ScheduleEvery)}>
              {(Object.keys(EVERY_LABELS) as ScheduleEvery[]).map((k) => (
                <option key={k} value={k}>{EVERY_LABELS[k]}</option>
              ))}
            </select>
          </label>
          {every === "week" ? (
            <label className={formLabel}>
              On
              <select className={formField} value={weekday} onChange={(e) => setWeekday(Number(e.target.value))}>
                {WEEKDAYS.map((d, i) => <option key={d} value={i}>{d}</option>)}
              </select>
            </label>
          ) : null}
          {every === "hour" ? (
            <label className={formLabel}>
              Minutes past the hour
              <input className={formField} type="number" min={0} max={MINUTES_PER_HOUR - 1} value={minute}
                     onChange={(e) => setMinute(Math.min(MINUTES_PER_HOUR - 1, Math.max(0, Number(e.target.value) || 0)))} />
            </label>
          ) : (
            <label className={formLabel}>
              At
              <input className={formField} type="time" value={at} required onChange={(e) => setAt(e.target.value)} />
            </label>
          )}
        </div>
        <p className="m-0 text-[0.78rem] text-muted">
          Times are in {zone}. Each answer goes to its own chat, “🔁 {prompt.trim() || "…"}”, so you can see every
          run together. Nobody is there to confirm, so a scheduled run only reads and reports: it never changes a
          server and never asks you questions.
        </p>
        {error ? <p className="m-0 text-[0.85rem] text-danger-text">{error}</p> : null}
        <div className="flex justify-end gap-2">
          <button type="button" className={btn("ghost")} onClick={onClose}>Cancel</button>
          <button type="submit" className={btn("primary")} disabled={busy || !prompt.trim()}>
            {busy ? "Saving…" : "Save schedule"}
          </button>
        </div>
      </form>
    </div>
  );
}
