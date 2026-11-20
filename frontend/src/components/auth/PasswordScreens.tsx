"use client";

import { useEffect, useState, type ReactNode } from "react";

import PasswordForm from "@/components/auth/PasswordForm";
import { changePassword, resetInfo, resetPassword } from "@/lib/api";
import { errorText } from "@/lib/chat";
import type { AuthUser } from "@/lib/types";
import { brandMark, btn } from "@/lib/ui";

// a reset link is the app's address with ?reset=<token> (Settings → Users → a user → Reset link)
export const RESET_PARAM = "reset";

/** The centred card of the signed-out screens, like the sign-in (components/auth/AuthScreen.tsx). */
function Card({ subtitle, children }: { subtitle: string; children: ReactNode }) {
  return (
    <div className="grid min-h-screen place-items-center p-4">
      <div className="flex w-full max-w-[25rem] flex-col gap-4 rounded-2xl border border-line bg-panel px-6 py-7 shadow-shadow">
        <div className="mb-1 flex items-center gap-[0.85rem]">
          <span className={brandMark()} aria-hidden />
          <div>
            <h1 className="m-0 text-[1.65rem] leading-none tracking-[-0.03em] [font-family:var(--font-display)]">
              MulteAgent
            </h1>
            <p className="mt-[0.2rem] text-[0.85rem] text-muted">{subtitle}</p>
          </div>
        </div>
        {children}
      </div>
    </div>
  );
}

/** Signed in with a password an admin chose: replace it before anything else (the API refuses the rest meanwhile). */
export function ChooseOwnPassword({ user, onChosen, onSignOut }: {
  user: AuthUser;
  onChosen: (user: AuthUser) => Promise<void>;
  onSignOut: () => void;
}) {
  return (
    <Card subtitle={`Welcome, ${user.username}`}>
      <p className="m-0 text-sm text-muted">
        Your password was set by an admin. Choose your own to continue — only you will know it.
      </p>
      <PasswordForm needsCurrent submitLabel="Save and continue"
                    onSubmit={async (current, next) => onChosen((await changePassword(current, next)).user)} />
      <button type="button" className={btn("ghost", "sm")} onClick={onSignOut}>Log out</button>
    </Card>
  );
}

/** Opened from a reset link while signed out: set a new password, then sign in with it. */
export function ResetPassword({ token, onDone }: { token: string; onDone: () => void }) {
  const [who, setWho] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  useEffect(() => {
    let cancelled = false;
    resetInfo(token)
      .then((r) => !cancelled && setWho(r.username))
      .catch((err: unknown) => !cancelled && setProblem(errorText(err)));
    return () => {
      cancelled = true;
    };
  }, [token]);

  return (
    <Card subtitle="Set a new password">
      {problem ? <p role="alert" className="m-0 text-sm text-danger">{problem}</p> : null}
      {done ? (
        <p className="m-0 text-sm">Your password is set, and every earlier sign-in was ended. Sign in with it now.</p>
      ) : who ? (
        <>
          <p className="m-0 text-sm text-muted">For <strong className="text-text">{who}</strong>. The link works once.</p>
          <PasswordForm needsCurrent={false} submitLabel="Set password"
                        onSubmit={async (_current, next) => {
                          await resetPassword(token, next);
                          setDone(true);
                        }} />
        </>
      ) : null}
      {done || problem ? (
        <button type="button" className={btn("primary")} onClick={onDone}>Go to sign in</button>
      ) : null}
    </Card>
  );
}
