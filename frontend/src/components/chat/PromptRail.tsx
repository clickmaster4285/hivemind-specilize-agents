"use client";

export type Prompt = { id: string; text: string };

type Props = { prompts: Prompt[]; active: string | null; onJump: (id: string) => void };

// The words of a question shown in the hover list: enough to recognise it, one line.
const PREVIEW_CHARS = 60;

/** The id of the invisible anchor placed before a question in the feed (MessageFeed scrolls to it). */
export function promptAnchorId(id: string): string {
  return `prompt-${id}`;
}

function preview(text: string): string {
  const line = text.replace(/\s+/g, " ").trim();
  return line.length > PREVIEW_CHARS ? `${line.slice(0, PREVIEW_CHARS)}…` : line;
}

/** Prompt navigation (ISSUES_LOG O78): a tick per question at the right edge; hovering lists the questions, the one
 *  in view is highlighted, and a click scrolls to it. Shown from two questions on. */
export default function PromptRail({ prompts, active, onJump }: Props) {
  if (prompts.length < 2) return null;
  const current = active ?? prompts[prompts.length - 1].id;
  return (
    <nav aria-label="Questions in this chat" className="group absolute right-2 top-1/2 z-10 -translate-y-1/2">
      <ul className="flex flex-col items-end gap-[0.45rem] py-2">
        {prompts.map((p) => (
          <li key={p.id}>
            <button type="button" aria-label={preview(p.text)} onClick={() => onJump(p.id)}
              className={`block h-0.5 rounded-full ${p.id === current ? "w-4 bg-copper" : "w-3 bg-muted hover:bg-text"}`} />
          </li>
        ))}
      </ul>
      <div className="invisible absolute right-6 top-1/2 max-h-[60vh] w-64 -translate-y-1/2 overflow-y-auto rounded-xl border border-line bg-panel p-1 opacity-0 shadow-[0_4px_16px_rgba(0,0,0,0.3)] transition-opacity group-hover:visible group-hover:opacity-100">
        {prompts.map((p) => (
          <button key={p.id} type="button" onClick={() => onJump(p.id)}
            className={`block w-full truncate rounded-lg px-[0.6rem] py-[0.35rem] text-left text-[0.76rem] ${
              p.id === current ? "bg-hover text-text" : "text-muted hover:bg-hover hover:text-text"}`}>
            {preview(p.text)}
          </button>
        ))}
      </div>
    </nav>
  );
}
