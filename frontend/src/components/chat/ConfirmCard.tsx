"use client";

import type { FeedItem } from "@/lib/types";

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
    <div className={`confirm-card ${answered ? "answered" : ""}`}>
      <p className="confirm-head">
        Run this on <strong>{c.server}</strong>?
      </p>
      <pre className="confirm-command">{c.command}</pre>
      {answered ? (
        <p className="confirm-done">{answered === "run" ? "✓ You approved it." : "✕ You cancelled it."}</p>
      ) : (
        <div className="confirm-actions">
          <button type="button" className="btn primary sm" onClick={() => onConfirm(c.commandId, true)}>
            Run
          </button>
          <button type="button" className="btn ghost sm" onClick={() => onConfirm(c.commandId, false)}>
            Cancel
          </button>
        </div>
      )}
    </div>
  );
}
