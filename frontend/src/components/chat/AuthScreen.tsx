"use client";

import { useEffect, useState, useSyncExternalStore, type FormEvent } from "react";

import { signUp, signupInfo } from "@/lib/api";
import type { Health, SignupInfo } from "@/lib/types";

type Props = {
  health: Health | null;
  healthError: string | null;
  error: string | null;
  onSignIn: (username: string, password: string) => void | Promise<void>;
};

type Mode = "signin" | "signup";

const noSubscribe = () => () => undefined;
// the API is reached through the page's own origin (lib/api.ts)
const apiAddress = () => `${window.location.origin}/api`;
// an invite link is this page with ?invite=<token> (Settings → Users → Invite someone)
const INVITE_PARAM = "invite";
const inviteToken = () => new URLSearchParams(window.location.search).get(INVITE_PARAM);

const field =
  "w-full rounded-[0.55rem] border border-[var(--line)] bg-[var(--panel-2)] px-[0.7rem] py-[0.55rem] font-[inherit] text-[var(--text)] outline-none";
const labelCol = "flex flex-col gap-1 text-[0.82rem] text-[var(--muted)]";
const errorBox =
  "m-0 rounded-[0.7rem] border border-[color-mix(in_srgb,var(--danger)_40%,var(--line))] bg-[var(--danger-soft)] px-[0.9rem] py-[0.7rem] text-[var(--danger-text)]";
const noteBox = "m-0 rounded-[0.7rem] border border-[var(--line)] bg-[var(--panel-2)] px-[0.9rem] py-[0.7rem] text-sm";

/** The sign-in card shown when nobody is signed in; it turns into the sign-up form (BACKLOG L16 S7). */
export default function AuthScreen({ health, healthError, error, onSignIn }: Props) {
  // the API address and the invite depend on the page's address: empty on the server, filled in the
  // browser (rendering them on the server gave different markup → React hydration error)
  const apiOrigin = useSyncExternalStore(noSubscribe, apiAddress, () => "");
  const invite = useSyncExternalStore(noSubscribe, inviteToken, () => null);
  const [chosen, setChosen] = useState<Mode | null>(null);
  const mode: Mode = chosen ?? (invite ? "signup" : "signin");
  const [info, setInfo] = useState<SignupInfo | null>(null);

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [repeat, setRepeat] = useState("");
  const [signupError, setSignupError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    signupInfo(invite)
      .then((found) => {
        if (cancelled) return;
        setInfo(found);
        if (found.invite?.email) setEmail(found.invite.email);
      })
      .catch(() => !cancelled && setInfo(null));
    return () => {
      cancelled = true;
    };
  }, [invite]);

  const inviteUsable = Boolean(invite) && info?.invite_valid === true;
  const mayCreate = Boolean(info?.open) || inviteUsable;

  function switchTo(next: Mode) {
    setChosen(next);
    setSignupError(null);
    setNotice(null);
    setPassword("");
    setRepeat("");
  }

  function submitSignIn(event: FormEvent) {
    event.preventDefault();
    void onSignIn(username, password);
  }

  async function submitSignUp(event: FormEvent) {
    event.preventDefault();
    setSignupError(null);
    if (password !== repeat) {
      setSignupError("The two passwords are not the same");
      return;
    }
    setBusy(true);
    try {
      await signUp({
        username: username.trim(),
        email: email.trim(),
        phone: phone.trim() || null,
        password,
        invite: inviteUsable ? invite : null,
      });
      // the invite is spent: take it off the address so a reload does not offer it again
      if (invite) window.history.replaceState(null, "", window.location.pathname);
      switchTo("signin");
      setNotice("Account created. An admin must approve it before you can sign in.");
    } catch (err) {
      setSignupError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const signingUp = mode === "signup";
  const shownError = signingUp ? signupError : error;

  return (
    <div className="grid min-h-screen place-items-center p-4">
      <form
        className="flex w-full max-w-[420px] flex-col gap-3 rounded-2xl border border-[var(--line)] bg-[var(--panel)] p-5 shadow-[var(--shadow)]"
        onSubmit={signingUp ? submitSignUp : submitSignIn}
      >
        <div className="flex items-center gap-[0.85rem]">
          <span className="brand-mark" aria-hidden />
          <div>
            <h1 className="m-0 text-[1.65rem] leading-none tracking-[-0.03em] [font-family:var(--font-display)]">
              MulteAgent
            </h1>
            <p className="mt-[0.2rem] text-[0.85rem] text-[var(--muted)]">
              {signingUp ? "Create your account" : "Sign in to continue"}
            </p>
          </div>
        </div>

        {signingUp && invite && info && !inviteUsable ? (
          <p className={errorBox}>This invite link has expired or was already used — ask an admin for a new one.</p>
        ) : null}
        {signingUp && inviteUsable ? <p className={noteBox}>You were invited. Your account still needs an admin&apos;s approval.</p> : null}
        {notice ? <p className={noteBox}>{notice}</p> : null}

        <label className={labelCol}>
          {signingUp ? "Username" : "Username or email"}
          <input
            className={field}
            name="username"
            autoComplete="username"
            placeholder={signingUp ? "e.g. sara.khan" : "username or name@example.com"}
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
          />
        </label>
        {signingUp ? (
          <>
            <label className={labelCol}>
              Email
              <input
                className={field}
                type="email"
                name="email"
                autoComplete="email"
                value={email}
                readOnly={Boolean(info?.invite?.email)}
                onChange={(e) => setEmail(e.target.value)}
                required
              />
            </label>
            <label className={labelCol}>
              Phone (optional)
              <input
                className={field}
                type="tel"
                name="phone"
                autoComplete="tel"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
              />
            </label>
          </>
        ) : null}
        <label className={labelCol}>
          Password
          <input
            className={field}
            type="password"
            name="password"
            autoComplete={signingUp ? "new-password" : "current-password"}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </label>
        {signingUp ? (
          <label className={labelCol}>
            Password again
            <input
              className={field}
              type="password"
              name="repeat"
              autoComplete="new-password"
              value={repeat}
              onChange={(e) => setRepeat(e.target.value)}
              required
            />
          </label>
        ) : null}

        {shownError ? <p className={errorBox}>{shownError}</p> : null}
        {signingUp ? null : (
          <div className={`status ${health?.llm_reachable ? "ok" : "bad"}`}>
            <span className="dot" />
            <span>
              {health
                ? health.llm_reachable
                  ? `${health.model} ready`
                  : "LLM offline"
                : healthError || `API · ${apiOrigin}`}
            </span>
          </div>
        )}
        <button type="submit" className="btn primary full" disabled={busy || (signingUp && !mayCreate)}>
          {signingUp ? (busy ? "Creating…" : "Create account") : "Sign in"}
        </button>
        {signingUp ? (
          <button type="button" className="btn ghost full" onClick={() => switchTo("signin")}>
            I already have an account
          </button>
        ) : mayCreate ? (
          <button type="button" className="btn ghost full" onClick={() => switchTo("signup")}>
            Create account
          </button>
        ) : null}
      </form>
    </div>
  );
}
