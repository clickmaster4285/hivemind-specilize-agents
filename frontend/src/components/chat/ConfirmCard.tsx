"use client";

import type { FeedItem } from "@/lib/types";
import { btn } from "@/lib/ui";

type Props = {
  item: FeedItem;
  onConfirm: (commandId: string, approved: boolean) => void;
};

/** operate_server: a command the agent wants to run on a server, waiting for Run / Cancel. */
export default function ConfirmCard({ item, onConfirm }: Props) {
  const c = item.confirm;
  if (!c) return null;
  const answered = c.answered;
  return (
    <div className={`my-[0.6rem] rounded-[10px] border border-[color-mix(in_srgb,var(--danger)_50%,var(--line))] bg-[color-mix(in_srgb,var(--danger)_8%,var(--panel-2))] px-[0.9rem] py-[0.7rem] ${answered ? "opacity-70" : ""}`}>
      <p className="mb-[0.4rem] text-text">
        Run this on <strong>{c.server}</strong>?
      </p>
      <pre className="mb-[0.6rem] whitespace-pre-wrap break-all rounded-md bg-panel px-[0.7rem] py-2 text-[0.82rem] [font-family:ui-monospace,SFMono-Regular,Consolas,monospace]">{c.command}</pre>
      {answered ? (
        <p className="text-[0.85rem] text-muted">{answered === "run" ? "✓ You approved it." : "✕ You cancelled it."}</p>
      ) : (
        <div className="flex gap-2">
          <button type="button" className={btn("primary", "sm")} onClick={() => onConfirm(c.commandId, true)}>
            Run
          </button>
          <button type="button" className={btn("ghost", "sm")} onClick={() => onConfirm(c.commandId, false)}>
            Cancel
          </button>
        </div>
      )}
    </div>
  );
}
