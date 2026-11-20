"use client";

import { useState, type FormEvent } from "react";

import { errorText } from "@/lib/chat";
import { btn, formField, formLabel } from "@/lib/ui";

type Props = {
  /** ask for the current password too (My account, the first-password screen); not for a reset link */
  needsCurrent: boolean;
  submitLabel: string;
  /** set it; a thrown error is shown under the form (the API's own words: too short, wrong current one…) */
  onSubmit: (current: string, next: string) => Promise<void>;
};

/** A new password, typed twice, with show / hide — the one form for My account, the first sign-in and reset links
 *  (BACKLOG L30 T2). The rules (length…) are the API's (src/core/accounts.py), shown when it refuses. */
export default function PasswordForm({ needsCurrent, submitLabel, onSubmit }: Props) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [repeat, setRepeat] = useState("");
  const [shown, setShown] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const type = shown ? "text" : "password";

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    if (next !== repeat) {
      setError("The two new passwords are not the same");
      return;
    }
    setBusy(true);
    try {
      await onSubmit(current, next);
      setCurrent("");
      setNext("");
      setRepeat("");
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="flex flex-col gap-3" onSubmit={submit}>
      {needsCurrent ? (
        <label className={formLabel}>
          Current password
          <input className={formField} type={type} autoComplete="current-password" required value={current}
                 onChange={(e) => setCurrent(e.target.value)} />
        </label>
      ) : null}
      <label className={formLabel}>
        New password
        <input className={formField} type={type} autoComplete="new-password" required value={next}
               onChange={(e) => setNext(e.target.value)} />
      </label>
      <label className={formLabel}>
        New password again
        <input className={formField} type={type} autoComplete="new-password" required value={repeat}
               onChange={(e) => setRepeat(e.target.value)} />
      </label>
      <label className="flex items-center gap-2 text-xs text-muted">
        <input type="checkbox" checked={shown} onChange={(e) => setShown(e.target.checked)} />
        Show passwords
      </label>
      {error ? <p role="alert" className="m-0 text-xs text-danger">{error}</p> : null}
      <button type="submit" className={btn("primary")} disabled={busy}>
        {busy ? "Saving…" : submitLabel}
      </button>
    </form>
  );
}
