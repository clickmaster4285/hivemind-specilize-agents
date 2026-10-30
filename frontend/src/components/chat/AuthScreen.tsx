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

const field =
  "w-full rounded-[0.55rem] border border-[var(--line)] bg-[var(--panel-2)] px-[0.7rem] py-[0.55rem] font-[inherit] text-[var(--text)] outline-none";
const labelCol = "flex flex-col gap-1 text-[0.82rem] text-[var(--muted)]";

/** The sign-in card shown when nobody is signed in. */
export default function AuthScreen({ health, healthError, error, onSignIn }: Props) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  // the API address depends on how the browser reached the page: empty on the server, filled in the
  // browser (rendering it on the server gave a different address → React hydration error)
  const apiOrigin = useSyncExternalStore(noSubscribe, getApiOrigin, () => "");

  function submit(event: FormEvent) {
    event.preventDefault();
    void onSignIn(username, password);
  }

  return (
    <div className="grid min-h-screen place-items-center p-4">
      <form
        className="flex w-full max-w-[420px] flex-col gap-3 rounded-2xl border border-[var(--line)] bg-[var(--panel)] p-5 shadow-[var(--shadow)]"
        onSubmit={submit}
      >
        <div className="flex items-center gap-[0.85rem]">
          <span className="brand-mark" aria-hidden />
          <div>
            <h1 className="m-0 text-[1.65rem] leading-none tracking-[-0.03em] [font-family:var(--font-display)]">
              MulteAgent
            </h1>
            <p className="mt-[0.2rem] text-[0.85rem] text-[var(--muted)]">Sign in to continue</p>
          </div>
        </div>
        <label className={labelCol}>
          Username or email
          <input
            className={field}
            name="username"
            autoComplete="username"
            placeholder="username or name@example.com"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
          />
        </label>
        <label className={labelCol}>
          Password
          <input
            className={field}
            type="password"
            name="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </label>
        {error ? (
          <p className="m-0 rounded-[0.7rem] border border-[color-mix(in_srgb,var(--danger)_40%,var(--line))] bg-[rgba(214,90,74,0.12)] px-[0.9rem] py-[0.7rem] text-[#ffb4aa]">
            {error}
          </p>
        ) : null}
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
