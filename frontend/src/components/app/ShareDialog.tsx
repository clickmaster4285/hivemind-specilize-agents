"use client";

import { useEffect, useState } from "react";

import { getShareLink, makeShareLink, stopShareLink, type ShareLinkState } from "@/lib/api";
import { errorText } from "@/lib/chat";
import { shareLinkPath } from "@/lib/routes";
import { btn, formFieldCompact, iconBtn } from "@/lib/ui";

type Props = {
  kind: "chat" | "project";
  id: string;
  title: string;
  onClose: () => void;
};

/** Share a chat or a whole project by link (BACKLOG L30 T4; the user's choice: "just share and send the link").
 *  Anyone signed in who opens it can read it and continue in a chat of their own; nothing of yours changes. */
export default function ShareDialog({ kind, id, title, onClose }: Props) {
  const [state, setState] = useState<ShareLinkState | null>(null);
  const [link, setLink] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getShareLink(kind, id)
      .then((s) => !cancelled && setState(s))
      .catch((err: unknown) => !cancelled && setNote(errorText(err)));
    return () => {
      cancelled = true;
    };
  }, [kind, id]);

  async function act(work: () => Promise<ShareLinkState>) {
    setBusy(true);
    setNote(null);
    try {
      const next = await work();
      setState(next);
      if (next.token) {
        const url = `${window.location.origin}${shareLinkPath(next.token)}`;
        setLink(url);
        if (navigator.clipboard) {
          await navigator.clipboard.writeText(url);
          setNote("Link copied — send it to anyone who should read this.");
        }
      } else {
        setLink(null);
      }
    } catch (err) {
      setNote(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-backdrop p-4 backdrop-blur-xs" onClick={onClose}>
      <div role="dialog" aria-modal="true" aria-label={`Share ${title}`}
           className="flex w-[min(28rem,100%)] flex-col gap-3 rounded-2xl border border-line bg-panel p-5 shadow-shadow"
           onClick={(e) => e.stopPropagation()}>
        <header className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h3 className="m-0 truncate text-base font-semibold [font-family:var(--font-display)]">Share “{title}”</h3>
            <p className="m-0 mt-1 text-xs text-muted">
              Anyone signed in who opens the link can read {kind === "project" ? "every chat in this project" : "this chat"}
              {" "}and continue in a chat of their own. Nothing of yours changes.
            </p>
          </div>
          <button type="button" className={iconBtn()} onClick={onClose} aria-label="Close">×</button>
        </header>

        {link ? (
          <input className={`${formFieldCompact} font-[var(--font-mono)]`} readOnly value={link} aria-label="Share link"
                 onFocus={(e) => e.target.select()} />
        ) : state?.shared ? (
          <p className="m-0 text-xs text-muted">Shared by link. For safety the link is shown only when made — make a new
            one to copy it again (the old one then stops working).</p>
        ) : null}
        {state?.readers.length ? (
          <p className="m-0 text-xs text-muted">Opened by: {state.readers.join(", ")}</p>
        ) : null}
        {note ? <p role="status" className="m-0 text-xs text-muted">{note}</p> : null}

        <footer className="flex flex-wrap justify-end gap-2">
          {state?.shared ? (
            <button type="button" className={btn("danger", "ghost", "sm")} disabled={busy}
                    onClick={() => confirm("Stop sharing? The link stops working and everyone who opened it loses access.")
                      && void act(() => stopShareLink(kind, id))}>
              Stop sharing
            </button>
          ) : null}
          <button type="button" className={btn("primary", "sm")} disabled={busy || state === null}
                  onClick={() => void act(() => makeShareLink(kind, id))}>
            {busy ? "…" : state?.shared ? "New link" : "Create link"}
          </button>
        </footer>
      </div>
    </div>
  );
}
