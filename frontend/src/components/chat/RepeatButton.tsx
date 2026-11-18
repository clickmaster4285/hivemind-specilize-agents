"use client";

/** Offering a schedule after a run (ISSUES_LOG O99, step 5): the 🔁 action under every answer, and under a check whose
 *  result changes over time (a scan, an audit, a ranking — Tool.repeatable) one line that says what it is for. Both open
 *  the Repeat dialog with the question that got this answer. */
import { useState } from "react";

import RepeatDialog from "@/components/app/RepeatDialog";
import type { ScheduleEvery } from "@/lib/api";

const icon = (
  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"
       strokeLinejoin="round" aria-hidden>
    <path d="M17 2l4 4-4 4" /><path d="M3 11V9a3 3 0 0 1 3-3h15" /><path d="M7 22l-4-4 4-4" /><path d="M21 13v2a3 3 0 0 1-3 3H3" />
  </svg>
);
/** a check worth repeating is usually wanted weekly (the research for O99: re-checks, digests) */
const HINT_EVERY: ScheduleEvery = "week";

export function RepeatButton({ prompt }: { prompt: string }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        type="button"
        title="Repeat this automatically — every hour, day or week"
        aria-label="Repeat this automatically"
        onClick={() => setOpen(true)}
        className="inline-flex h-[1.9rem] w-[1.9rem] items-center justify-center rounded-[0.4rem] text-muted hover:bg-panel-2 hover:text-text"
      >
        {icon}
      </button>
      {open ? <RepeatDialog prompt={prompt} onClose={() => setOpen(false)} onSaved={() => setOpen(false)} /> : null}
    </>
  );
}

export function RepeatHint({ prompt }: { prompt: string }) {
  const [open, setOpen] = useState(false);
  return (
    <p className="m-0 mt-[0.5rem] flex flex-wrap items-center gap-x-1 text-[0.8rem] text-muted">
      <span aria-hidden>🔁</span> Results like this change over time.
      <button type="button" className="cursor-pointer border-0 bg-transparent p-0 font-medium text-copper hover:underline"
              onClick={() => setOpen(true)}>
        Check again every week
      </button>
      and hear only what changed.
      {open ? (
        <RepeatDialog prompt={prompt} every={HINT_EVERY} onClose={() => setOpen(false)} onSaved={() => setOpen(false)} />
      ) : null}
    </p>
  );
}
