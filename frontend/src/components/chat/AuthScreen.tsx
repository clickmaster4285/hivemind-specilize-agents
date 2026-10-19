"use client";

import { useState, useSyncExternalStore, type FormEvent } from "react";

import { getApiOrigin } from "@/lib/api";
import type { Health } from "@/lib/types";

type Props = {
  health: Health | null;
  healthError: string | null;
  error: string | null;
  onSignIn: (username: string, password: string) => void | Promise<void>;
};

const noSubscribe = () => () => undefined;

/** The sign-in card shown when nobody is signed in. */
export default function AuthScreen({ health, healthError, error, onSignIn }: Props) {
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  // the API address depends on how the browser reached the page: empty on the server, filled in the
  // browser (rendering it on the server gave a different address → React hydration error)
  const apiOrigin = useSyncExternalStore(noSubscribe, getApiOrigin, () => "");

  function submit(event: FormEvent) {
    event.preventDefault();
    void onSignIn(username, password);
  }

  return (
    <div className="app-shell auth-shell">
      <form className="auth-card" onSubmit={submit}>
        <div className="brand">
          <span className="brand-mark" aria-hidden />
          <div>
            <h1>MulteAgent</h1>
            <p>Sign in to continue</p>
          </div>
        </div>
        <p className="hint">
          Testing login: <strong>admin / admin123</strong>
        </p>
        <label>
          Username
          <input name="username" autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} required />
        </label>
        <label>
          Password
          <input
            type="password"
            name="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </label>
        {error ? <p className="banner">{error}</p> : null}
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
        <button type="submit" className="btn primary full">
          Sign in
        </button>
      </form>
    </div>
  );
}
