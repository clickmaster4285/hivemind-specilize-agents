import { useCallback, useEffect, useState } from "react";

import { useWindowEvent } from "@/hooks/useWindowEvent";
import { ApiError, login, logout, me, SESSION_EXPIRED_EVENT } from "@/lib/api";
import { errorText } from "@/lib/chat";
import type { AuthUser, CaptchaAnswer } from "@/lib/types";

type AuthEvents = {
  /** After sign-in or a restored session: load the user's data (fresh = just signed in). The user is
   *  passed so per-user state (history pointers, migration) is scoped to the right account. */
  onSignedIn: (fresh: boolean, user: AuthUser) => Promise<void> | void;
  /** "logout" = the user signed out (the session ends once the returned promise settles); "expired" = a
   *  401 the refresh could not fix. */
  onSignedOut: (reason: "logout" | "expired") => Promise<void> | void;
};

/**
 * The signed-in user. When the session is over (lib/api.ts sends SESSION_EXPIRED_EVENT), the user is
 * signed out and the login shows again. onSignedIn / onSignedOut must be stable (useCallback).
 */
export function useAuth({ onSignedIn, onSignedOut }: AuthEvents) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [error, setError] = useState<string | null>(null);
  // after a few failed passwords the API wants a CAPTCHA with every try (BACKLOG L16 S2b)
  const [captchaRequired, setCaptchaRequired] = useState(false);

  // the session cookies (unreadable to scripts) may still be valid: ask who is signed in
  useEffect(() => {
    me()
      .then(async (u) => {
        setUser(u);
        await onSignedIn(false, u);
      })
      .catch(() => setUser(null));
  }, [onSignedIn]);

  // the session is over: leave the chat and show the login
  useWindowEvent(SESSION_EXPIRED_EVENT, () => {
    if (!user) return;
    setUser(null);
    onSignedOut("expired");
  });

  const signIn = useCallback(
    async (username: string, password: string, captcha?: CaptchaAnswer) => {
      setError(null);
      try {
        const u = await login(username.trim(), password, captcha);
        setCaptchaRequired(false);
        setUser(u);
        await onSignedIn(true, u);
      } catch (err) {
        if (err instanceof ApiError && err.captchaRequired) setCaptchaRequired(true);
        setError(errorText(err));
      }
    },
    [onSignedIn],
  );

  const signOut = useCallback(async () => {
    const pending = onSignedOut("logout");
    setUser(null);
    await pending;                      // its calls still need the session
    await logout().catch(() => undefined);
  }, [onSignedOut]);

  return { user, error, captchaRequired, signIn, signOut };
}
