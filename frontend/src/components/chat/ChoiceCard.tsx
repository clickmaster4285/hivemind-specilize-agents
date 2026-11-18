"use client";

import { useState } from "react";

import type { ChoiceAnswer, FeedItem } from "@/lib/types";
import { btn } from "@/lib/ui";

type Props = {
  item: FeedItem;
  onChoose: (choiceId: string, picked: string[]) => void;
};

/** What happened, once the card is answered (shown on a reloaded chat too). */
const OUTCOME: Record<Exclude<ChoiceAnswer, "picked">, string> = {
  timeout: "No answer in time. The deploy stopped here.",
  closed: "The job ended before an answer.",
};

const mono = "[font-family:ui-monospace,SFMono-Regular,Consolas,monospace]";
/** The option that ends the deploy (src/servers/deploy.py STOP): a button beside Continue, not a tick box. */
const STOP_ID = "stop";

/** The question's **bold** parts (branch names, the current branch) as <strong>; the rest as text. */
function Bolded({ text }: { text: string }) {
  return (
    <>
      {text.split(/\*\*(.+?)\*\*/g).map((part, i) => (i % 2 ? <strong key={i}>{part}</strong> : part))}
    </>
  );
}

/** deploy_project: a question with options — one click for a single choice, ticks + Continue for several
 *  (which folders to install, build, restart). Each option shows the command it runs. */
export default function ChoiceCard({ item, onChoose }: Props) {
  const c = item.choice;
  const [ticked, setTicked] = useState<string[]>(() => c?.options.filter((o) => o.checked).map((o) => o.id) ?? []);
  if (!c) return null;
  const answered = c.answered;
  const picked = new Set(c.picked ?? []);
  const stop = c.options.find((o) => o.id === STOP_ID);
  const ticks = c.options.filter((o) => o.id !== STOP_ID);

  return (
    <div className={`my-[0.6rem] rounded-[10px] border border-[color-mix(in_srgb,var(--copper)_50%,var(--line))] bg-[color-mix(in_srgb,var(--copper)_7%,var(--panel-2))] px-[0.9rem] py-[0.7rem] ${answered ? "opacity-75" : ""}`}>
      <p className="mb-[0.5rem] text-text">
        <Bolded text={c.question} />
        {c.server ? <span className="ml-1 text-[0.8rem] text-muted">· {c.server}</span> : null}
      </p>
      {answered ? (
        <p className="m-0 text-[0.85rem] text-muted">
          {answered === "picked"
            ? `✓ ${c.options.filter((o) => picked.has(o.id)).map((o) => o.label).join(", ") || "Nothing picked"}`
            : OUTCOME[answered]}
        </p>
      ) : c.multi ? (
        <>
          <div className="flex flex-col gap-1.5">
            {ticks.map((o) => (
              <label key={o.id} className="flex cursor-pointer items-start gap-2 rounded-md px-1 py-0.5 hover:bg-hover">
                <input
                  type="checkbox"
                  className="mt-[0.3rem]"
                  checked={ticked.includes(o.id)}
                  onChange={(e) => setTicked((prev) => (e.target.checked ? [...prev, o.id] : prev.filter((id) => id !== o.id)))}
                />
                <span className="min-w-0">
                  <span className="text-[0.9rem] text-text">{o.label}</span>
                  {o.detail ? <code className={`block break-all text-[0.76rem] text-muted ${mono}`}>{o.detail}</code> : null}
                </span>
              </label>
            ))}
          </div>
          <div className="mt-[0.6rem] flex gap-2">
            <button type="button" className={btn("primary", "sm")} onClick={() => onChoose(c.choiceId, ticked)}>
              {ticked.length ? "Continue" : "Skip this step"}
            </button>
            {stop ? (
              <button type="button" className={btn("ghost", "sm")} onClick={() => onChoose(c.choiceId, [STOP_ID])}>
                {stop.label}
              </button>
            ) : null}
          </div>
        </>
      ) : (
        <div className="flex flex-col gap-1.5">
          {c.options.map((o) => (
            <button
              key={o.id}
              type="button"
              className={`${btn("sm")} text-left`}
              onClick={() => onChoose(c.choiceId, [o.id])}
            >
              <span className="block text-[0.88rem]">{o.label}</span>
              {o.detail ? <span className="block text-[0.76rem] font-normal text-muted">{o.detail}</span> : null}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
