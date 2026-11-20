"use client";

import { useState } from "react";

import { RESET_PARAM } from "@/components/auth/PasswordScreens";
import { makeResetLink } from "@/lib/api";
import { errorText } from "@/lib/chat";
import { btn, formFieldCompact } from "@/lib/ui";

/** Settings → Users → a user: a one-time link with which they set a new password themselves (BACKLOG L30 T2) —
 *  instead of an admin choosing one and sending it. Shown once; making another cancels it. */
export default function ResetLinkPanel({ userId, username }: { userId: number; username: string }) {
  const [link, setLink] = useState<{ url: string; until: string } | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function make() {
    setBusy(true);
    setNote(null);
    try {
      const made = await makeResetLink(userId);
      const url = `${window.location.origin}/?${RESET_PARAM}=${encodeURIComponent(made.token)}`;
      setLink({ url, until: new Date(made.expires_at).toLocaleString() });
      if (navigator.clipboard) {
        await navigator.clipboard.writeText(url);
        setNote("Copied — send it to them directly.");
      }
    } catch (err) {
      setNote(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-2 rounded-xl border border-line p-4 text-xs">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-sm font-semibold">Password reset link</span>
        <button type="button" className={btn("ghost", "sm")} disabled={busy} onClick={() => void make()}>
          {link ? "Make a new link" : "Make a reset link"}
        </button>
      </div>
      <p className="m-0 text-muted">
        {username} opens it, sets their own password, and every sign-in they had ends. It works once; a new link
        cancels the old.
      </p>
      {link ? (
        <>
          <input className={`${formFieldCompact} font-[var(--font-mono)]`} readOnly value={link.url}
                 onFocus={(e) => e.target.select()} aria-label="Reset link" />
          <span className="text-muted">Valid until {link.until}. It is not shown again.</span>
        </>
      ) : null}
      {note ? <span role="status" className="text-muted">{note}</span> : null}
    </div>
  );
}
