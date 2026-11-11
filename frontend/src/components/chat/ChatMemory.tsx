"use client";

/** Chat memory (BACKLOG L12): how full the model's memory of this chat is, and — once it is getting long —
 *  a banner to summarise and carry on in a fresh chat, or save the summary. */
import { useState } from "react";

import { compactCount } from "@/lib/format";
import { MEMORY_WARN_PERCENT, type ChatMemory } from "@/lib/chat";

export type MemoryBusy = "continue" | "save" | null;

/** A small bar + percentage under the message bar. Past 100% the oldest messages are summarised. */
export function MemoryMeter({ memory }: { memory: ChatMemory }) {
  const full = Math.min(100, memory.percent);
  const tone =
    memory.percent >= 100 ? "var(--danger)" : memory.percent >= MEMORY_WARN_PERCENT ? "var(--copper)" : "var(--teal)";
  const title =
    `About ${compactCount(memory.tokens)} of ${compactCount(memory.budget)} tokens of chat memory.` +
    (memory.percent >= 100
      ? " The oldest messages are now summarized so the model keeps the gist."
      : " Past 100%, the oldest messages are summarized automatically.");
  return (
    <span className="inline-flex cursor-default items-center gap-1.5" title={title}>
      <span className="relative h-[0.3rem] w-[3.2rem] overflow-hidden rounded-full bg-line" aria-hidden>
        <span className="absolute inset-y-0 left-0 rounded-full" style={{ width: `${full}%`, background: tone }} />
      </span>
      <span>Memory {memory.percent >= 100 ? "full · summarizing" : `${memory.percent}%`}</span>
    </span>
  );
}

/** Shown from MEMORY_WARN_PERCENT: the chat is getting long — continue in a new chat from a summary, or
 *  save the summary. Dismissed per chat. */
export function MemoryBanner({
  chatId,
  memory,
  busy,
  onContinue,
  onSave,
}: {
  chatId: string | null;
  memory: ChatMemory;
  busy: MemoryBusy;
  onContinue: () => void;
  onSave: () => void;
}) {
  const [dismissedFor, setDismissedFor] = useState<string | null>(null);
  if (memory.percent < MEMORY_WARN_PERCENT || (chatId && dismissedFor === chatId)) return null;
  const btn =
    "rounded-full border px-[0.7rem] py-1 text-[0.76rem] disabled:cursor-wait disabled:opacity-60";
  return (
    <div
      role="status"
      className="mb-[0.45rem] flex flex-wrap items-center gap-2 rounded-[0.7rem] border border-[color-mix(in_srgb,var(--copper)_45%,var(--line))] bg-[var(--copper-soft)] px-3 py-2 text-[0.8rem] text-text"
    >
      <span className="min-w-0 flex-1">
        <strong>This chat is getting long</strong> ({memory.percent}% of the model&apos;s memory).{" "}
        {memory.percent >= 100
          ? "Older messages are being summarized, so details from the start may be lost."
          : "Soon the oldest messages will be summarized."}{" "}
        For best answers, continue in a new chat that starts from a summary.
      </span>
      <button
        type="button"
        disabled={busy !== null}
        onClick={onContinue}
        className={`${btn} border-copper bg-copper text-on-copper hover:brightness-[1.06]`}
      >
        {busy === "continue" ? "Summarizing…" : "Summarize & continue in a new chat"}
      </button>
      <button
        type="button"
        disabled={busy !== null}
        onClick={onSave}
        className={`${btn} border-line text-text hover:bg-hover`}
      >
        {busy === "save" ? "Summarizing…" : "Save summary"}
      </button>
      <button
        type="button"
        aria-label="Dismiss"
        title="Dismiss for this chat"
        onClick={() => setDismissedFor(chatId)}
        className="grid h-6 w-6 place-items-center rounded-full text-muted hover:bg-hover hover:text-text"
      >
        ×
      </button>
    </div>
  );
}
