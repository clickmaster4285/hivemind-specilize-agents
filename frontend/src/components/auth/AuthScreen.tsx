"use client";

import { useCallback, useEffect, useState, useSyncExternalStore, type FormEvent, type KeyboardEvent } from "react";

import Captcha from "@/components/auth/Captcha";

import { signUp, signupInfo } from "@/lib/api";
import type { CaptchaAnswer, SignupInfo } from "@/lib/types";
import { brandMark, btn, formField, formLabel, iconBtn } from "@/lib/ui";

type Props = {
  error: string | null;
  /** the API wants a CAPTCHA with the next sign-in (after a few failed passwords) */
  captchaRequired: boolean;
  onSignIn: (username: string, password: string, captcha?: CaptchaAnswer) => void | Promise<void>;
};

type Mode = "signin" | "signup";

const noSubscribe = () => () => undefined;
// an invite link is this page with ?invite=<token> (Settings → Users → Invite someone)
const INVITE_PARAM = "invite";
const inviteToken = () => new URLSearchParams(window.location.search).get(INVITE_PARAM);

/** The password's show / hide toggle, in the app's line-icon style (components/app/NavIcons.tsx). */
function EyeIcon({ open }: { open: boolean }) {
  return (
    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
         strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z" />
      <circle cx="12" cy="12" r="3" />
      {open ? null : <path d="M4 4l16 16" />}
    </svg>
  );
}

const errorBox =
  "m-0 rounded-[0.7rem] border border-[color-mix(in_srgb,var(--danger)_40%,var(--line))] bg-danger-soft px-[0.9rem] py-[0.7rem] text-danger-text";
const noteBox = "m-0 rounded-[0.7rem] border border-line bg-panel-2 px-[0.9rem] py-[0.7rem] text-sm";

/** The sign-in card shown when nobody is signed in; it turns into the sign-up form (BACKLOG L16 S7). */
export default function AuthScreen({ error, captchaRequired, onSignIn }: Props) {
  // the invite depends on the page's address: empty on the server, filled in the browser (rendering it on the
  // server gave different markup → React hydration error)
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
  const [showPassword, setShowPassword] = useState(false);
  const [capsLock, setCapsLock] = useState(false);
  const [captcha, setCaptcha] = useState<CaptchaAnswer | null>(null);
  const [captchaRound, setCaptchaRound] = useState(0);     // a CAPTCHA works once: new picture per try
  const onCaptcha = useCallback((answer: CaptchaAnswer | null) => setCaptcha(answer), []);

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
    setShowPassword(false);
  }

  /** Caps Lock turns a right password into a wrong one: say so while typing it. */
  function watchCaps(event: KeyboardEvent<HTMLInputElement>) {
    setCapsLock(event.getModifierState("CapsLock"));
  }

  async function submitSignIn(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    await onSignIn(username, password, captchaRequired && captcha ? captcha : undefined);
    setBusy(false);
    setCaptchaRound((n) => n + 1);       // still here = it failed; its CAPTCHA (if any) is used up
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
        ...captcha,
      });
      // the invite is spent: take it off the address so a reload does not offer it again
      if (invite) window.history.replaceState(null, "", window.location.pathname);
      switchTo("signin");
      setNotice("Account created. An admin must approve it before you can sign in.");
    } catch (err) {
      setSignupError(err instanceof Error ? err.message : String(err));
      setCaptchaRound((n) => n + 1);
    } finally {
      setBusy(false);
    }
  }

  const signingUp = mode === "signup";
  const shownError = signingUp ? signupError : error;

  return (
    <div className="grid min-h-screen place-items-center p-4">
      <form
        className="flex w-full max-w-[400px] flex-col gap-4 rounded-2xl border border-line bg-panel px-6 py-7 shadow-shadow"
        onSubmit={signingUp ? submitSignUp : submitSignIn}
      >
        <div className="mb-1 flex items-center gap-[0.85rem]">
          <span className={brandMark()} aria-hidden />
          <div>
            <h1 className="m-0 text-[1.65rem] leading-none tracking-[-0.03em] [font-family:var(--font-display)]">
              MulteAgent
            </h1>
            <p className="mt-[0.2rem] text-[0.85rem] text-muted">
              {signingUp ? "Create your account" : "Sign in to continue"}
            </p>
          </div>
        </div>

        {signingUp && invite && info && !inviteUsable ? (
          <p className={errorBox}>This invite link has expired or was already used — ask an admin for a new one.</p>
        ) : null}
        {signingUp && inviteUsable ? <p className={noteBox}>You were invited. Your account still needs an admin&apos;s approval.</p> : null}
        {notice ? <p className={noteBox}>{notice}</p> : null}

        <label className={formLabel}>
          {signingUp ? "Username" : "Username or email"}
          <input
            className={formField}
            name="username"
            autoComplete="username"
            placeholder={signingUp ? "e.g. sara.khan" : "username or name@example.com"}
            autoFocus
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
          />
        </label>
        {signingUp ? (
          <>
            <label className={formLabel}>
              Email
              <input
                className={formField}
                type="email"
                name="email"
                autoComplete="email"
                value={email}
                readOnly={Boolean(info?.invite?.email)}
                onChange={(e) => setEmail(e.target.value)}
                required
              />
            </label>
            <label className={formLabel}>
              Phone (optional)
              <input
                className={formField}
                type="tel"
                name="phone"
                autoComplete="tel"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
              />
            </label>
          </>
        ) : null}
        <label className={formLabel}>
          Password
          <span className="relative block">
            <input
              className={`${formField} pr-10`}
              type={showPassword ? "text" : "password"}
              name="password"
              autoComplete={signingUp ? "new-password" : "current-password"}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              onKeyUp={watchCaps}
              onBlur={() => setCapsLock(false)}
              required
            />
            <button
              type="button"
              className={`${iconBtn()} absolute right-1.5 top-1/2 grid -translate-y-1/2 place-items-center`}
              aria-label={showPassword ? "Hide password" : "Show password"}
              aria-pressed={showPassword}
              title={showPassword ? "Hide password" : "Show password"}
              onClick={() => setShowPassword((v) => !v)}
            >
              <EyeIcon open={!showPassword} />
            </button>
          </span>
          {capsLock ? <span className="text-[0.78rem] text-copper">Caps Lock is on</span> : null}
        </label>
        {signingUp ? (
          <label className={formLabel}>
            Password again
            <input
              className={formField}
              type={showPassword ? "text" : "password"}
              name="repeat"
              autoComplete="new-password"
              value={repeat}
              onChange={(e) => setRepeat(e.target.value)}
              required
            />
          </label>
        ) : null}

        {signingUp || captchaRequired ? <Captcha round={captchaRound} onChange={onCaptcha} /> : null}
        {shownError ? <p role="alert" className={errorBox}>{shownError}</p> : null}
        <button type="submit" className={`${btn("primary", "full")} mt-1`} disabled={busy || (signingUp && !mayCreate)}>
          {signingUp ? (busy ? "Creating…" : "Create account") : busy ? "Signing in…" : "Sign in"}
        </button>
        {signingUp ? (
          <button type="button" className={btn("ghost", "full")} onClick={() => switchTo("signin")}>
            I already have an account
          </button>
        ) : mayCreate ? (
          <p className="m-0 text-center text-[0.85rem] text-muted">
            New here?{" "}
            <button type="button" className="cursor-pointer border-0 bg-transparent p-0 font-medium text-copper hover:underline"
                    onClick={() => switchTo("signup")}>
              Create an account
            </button>
          </p>
        ) : null}
      </form>
    </div>
  );
}
