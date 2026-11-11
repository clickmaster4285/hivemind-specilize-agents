"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";

import {
  createInvite,
  getSignupSettings,
  listInvites,
  listModules,
  revokeInvite,
  setSignupSettings,
} from "@/lib/api";
import type { Invite, ModuleInfo } from "@/lib/types";

const field =
  "w-full rounded-[0.55rem] border border-[var(--line)] bg-[var(--panel-2)] px-[0.7rem] py-[0.5rem] font-[inherit] text-sm text-[var(--text)] outline-none";
// how long the copy note stays on the button
const COPIED_MS = 1500;

/** The page link that opens the sign-up form for an invite (the API keeps only the token's hash). */
function inviteLink(token: string): string {
  return `${window.location.origin}/?invite=${encodeURIComponent(token)}`;
}

function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

/** Settings → Users: whether people may sign up from the page, and invite links (BACKLOG L16 S7).
 *  Every sign-up — with or without an invite — waits for an admin's approval. */
export default function InvitesPanel() {
  const [open, setOpen] = useState<boolean | null>(null);
  const [invites, setInvites] = useState<Invite[]>([]);
  const [catalog, setCatalog] = useState<ModuleInfo[]>([]);
  const [making, setMaking] = useState(false);
  const [email, setEmail] = useState("");
  const [granted, setGranted] = useState<string[]>([]);
  const [made, setMade] = useState<{ link: string; until: string } | null>(null);   // the new link, shown this once
  const [copied, setCopied] = useState<string | null>(null);
  const linkBox = useRef<HTMLInputElement>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([getSignupSettings(), listInvites(), listModules()])
      .then(([settings, list, modules]) => {
        if (cancelled) return;
        setOpen(settings.open);
        setInvites(list);
        setCatalog(modules);
        setGranted(modules.filter((m) => m.default).map((m) => m.id));
      })
      .catch((err) => !cancelled && setError(errorText(err)));
    return () => {
      cancelled = true;
    };
  }, []);

  async function toggleOpen(next: boolean) {
    setError(null);
    try {
      setOpen((await setSignupSettings(next)).open);
    } catch (err) {
      setError(errorText(err));
    }
  }

  async function make(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      const invite = await createInvite({ email: email.trim() || null, modules: granted });
      setMade({ link: inviteLink(invite.token ?? ""), until: new Date(invite.expires_at).toLocaleString() });
      setInvites(await listInvites());
      setEmail("");
      setMaking(false);
    } catch (err) {
      setError(errorText(err));
    }
  }

  async function revoke(invite: Invite) {
    if (!confirm(`Revoke this invite${invite.email ? ` for ${invite.email}` : ""}? The link stops working.`)) return;
    setError(null);
    try {
      await revokeInvite(invite.id);
      setInvites(await listInvites());
    } catch (err) {
      setError(errorText(err));
    }
  }

  // the clipboard API exists only on https or localhost pages; on the LAN's plain http the link is
  // selected instead, for Ctrl+C
  async function copy() {
    if (!made) return;
    if (navigator.clipboard) {
      await navigator.clipboard.writeText(made.link);
      setCopied("Copied");
    } else {
      linkBox.current?.select();
      setCopied("Press Ctrl+C");
    }
    window.setTimeout(() => setCopied(null), COPIED_MS);
  }

  return (
    <section className="flex flex-col gap-2 rounded-lg border border-[var(--line)] bg-[var(--panel-2)] p-3">
      <div className="flex items-center justify-between gap-3">
        <h5 className="m-0 text-sm font-semibold">Sign-up and invites</h5>
        <button type="button" className="btn ghost sm" onClick={() => setMaking((v) => !v)}>
          {making ? "Cancel" : "Invite someone"}
        </button>
      </div>
      <label className="flex items-center gap-2 text-xs text-[var(--muted)]">
        <input
          type="checkbox"
          checked={open ?? false}
          disabled={open === null}
          onChange={(e) => void toggleOpen(e.target.checked)}
        />
        Anyone who can open this page may sign up (off: invite links only). Every new account waits for your approval.
      </label>

      {making ? (
        <form className="flex flex-col gap-2" onSubmit={make}>
          <input
            className={field}
            type="email"
            placeholder="their email (optional — the sign-up must then use it)"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
            {catalog.map((m) => (
              <label key={m.id} className="flex items-center gap-1.5" title={m.description}>
                <input
                  type="checkbox"
                  checked={granted.includes(m.id)}
                  onChange={(e) =>
                    setGranted((g) => (e.target.checked ? [...g, m.id] : g.filter((id) => id !== m.id)))
                  }
                />
                {m.title}
              </label>
            ))}
          </div>
          <button type="submit" className="btn primary sm self-start">
            Make invite link
          </button>
        </form>
      ) : null}

      {made ? (
        <div className="flex flex-col gap-1.5 rounded-md border border-[var(--line)] p-2 text-xs">
          <span className="text-[var(--muted)]">
            Share this link — it is shown only now, works once and runs out on {made.until}:
          </span>
          <div className="flex items-center gap-2">
            <input
              ref={linkBox}
              className={`${field} min-w-0 flex-1`}
              readOnly
              value={made.link}
              onFocus={(e) => e.target.select()}
            />
            <button type="button" className="btn ghost sm shrink-0" onClick={() => void copy()}>
              {copied ?? "Copy"}
            </button>
          </div>
        </div>
      ) : null}

      {invites.length ? (
        <ul className="flex flex-col gap-1 text-xs">
          {invites.map((i) => (
            <li key={i.id} className="flex items-center justify-between gap-2">
              <span className="min-w-0 truncate text-[var(--muted)]">
                {i.email ?? "anyone with the link"} · by {i.created_by ?? "?"} · until{" "}
                {new Date(i.expires_at).toLocaleDateString()}
              </span>
              <button type="button" className="btn ghost sm shrink-0" onClick={() => void revoke(i)}>
                Revoke
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      {error ? <p className="m-0 text-xs text-[var(--danger)]">{error}</p> : null}
    </section>
  );
}
