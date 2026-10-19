import { useCallback, useEffect, useState } from "react";

import { useWindowEvent } from "@/hooks/useWindowEvent";
import { getToken, login, me, SESSION_EXPIRED_EVENT, setToken } from "@/lib/api";
import { errorText } from "@/lib/chat";
import type { AuthUser } from "@/lib/types";

type AuthEvents = {
  /** After sign-in or a restored session: load the user's data (fresh = just signed in). */
  onSignedIn: (fresh: boolean) => Promise<void> | void;
  /** "logout" = the user signed out (the token is still valid during this call); "expired" = a 401 dropped it. */
  onSignedOut: (reason: "logout" | "expired") => void;
};

/**
 * The signed-in user. When any API call is refused with 401 (lib/api.ts sends SESSION_EXPIRED_EVENT),
 * the user is signed out and the login shows again. onSignedIn / onSignedOut must be stable (useCallback).
 */
export function useAuth({ onSignedIn, onSignedOut }: AuthEvents) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [error, setError] = useState<string | null>(null);

  // a saved token: restore the session
  useEffect(() => {
    if (!getToken()) return;
    me()
      .then(async (u) => {
        setUser(u);
        await onSignedIn(false);
      })
      .catch(() => {
        setToken(null);
        setUser(null);
      });
  }, [onSignedIn]);

  // a request was refused (401) and the token is gone: leave the chat and show the login
  useWindowEvent(SESSION_EXPIRED_EVENT, () => {
    if (!user) return;
    setUser(null);
    onSignedOut("expired");
  });

  const signIn = useCallback(
    async (username: string, password: string) => {
      setError(null);
      try {
        setUser(await login(username.trim(), password));
        await onSignedIn(true);
      } catch (err) {
        setError(errorText(err));
      }
    },
    [onSignedIn],
  );

  const signOut = useCallback(() => {
    onSignedOut("logout");
    setToken(null);
    setUser(null);
  }, [onSignedOut]);

  return { user, error, signIn, signOut };
}
