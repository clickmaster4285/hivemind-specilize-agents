"use client";

import { useEffect, useState } from "react";

import { getUsage } from "@/lib/api";
import { errorText } from "@/lib/chat";
import { compactCount, formatSeconds } from "@/lib/format";
import type { UsageSummary, UsageTotals } from "@/lib/types";

const PERIODS = [
  { days: 1, label: "Today" },
  { days: 7, label: "7 days" },
  { days: 30, label: "30 days" },
  { days: 90, label: "90 days" },
];

const th = "px-3 py-2 text-left text-[0.72rem] font-medium uppercase tracking-[0.04em] text-muted";
const td = "px-3 py-2 tabular-nums";

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-0.5 rounded-lg border border-line bg-panel-2 px-3 py-2.5">
      <span className="text-[0.72rem] text-muted">{label}</span>
      <span className="text-lg font-semibold tabular-nums text-text-strong">{value}</span>
    </div>
  );
}

function Cells({ u }: { u: UsageTotals }) {
  return (
    <>
      <td className={td}>{compactCount(u.prompt_tokens)}</td>
      <td className={td}>{compactCount(u.output_tokens)}</td>
      <td className={td}>{u.calls}</td>
      <td className={td}>{formatSeconds(u.seconds)}</td>
    </>
  );
}

/** Settings → Usage: tokens the model read (in) and wrote (out), model calls and model time — per user
 *  (admins see everyone, members themselves) and per step of the app (BACKLOG L11). */
export default function UsageSection() {
  const [days, setDays] = useState(30);
  const [data, setData] = useState<UsageSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getUsage(days)
      .then((d) => {
        if (cancelled) return;
        setData(d);
        setError(null);
      })
      .catch((err) => {
        if (!cancelled) setError(errorText(err));
      });
    return () => {
      cancelled = true;
    };
  }, [days]);

  const total = data?.total;
  const answers = data?.users.reduce((n, u) => n + u.answers, 0) ?? 0;

  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h4 className="text-base font-semibold [font-family:var(--font-display)]">Usage</h4>
        <div className="flex gap-1" role="radiogroup" aria-label="Period">
          {PERIODS.map((p) => (
            <button
              key={p.days}
              type="button"
              role="radio"
              aria-checked={days === p.days}
              onClick={() => setDays(p.days)}
              className={`rounded-full border px-3 py-1 text-xs ${
                days === p.days
                  ? "border-copper text-text"
                  : "border-line text-muted hover:text-text"
              }`}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>
      <p className="text-xs leading-relaxed text-muted">
        <strong>In</strong> = tokens the model read (your question, chat history, sources); <strong>out</strong> =
        tokens it wrote. Counted by Ollama for every model call. {data?.all_users ? "All users." : "Your own usage."}
      </p>
      {error ? <p className="text-sm text-danger-text">{error}</p> : null}
      {total ? (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <Stat label="Tokens in" value={compactCount(total.prompt_tokens)} />
          <Stat label="Tokens out" value={compactCount(total.output_tokens)} />
          <Stat label="Answers" value={String(answers)} />
          <Stat label="Model time" value={formatSeconds(total.seconds)} />
        </div>
      ) : null}
      {data && !data.users.length ? <p className="text-sm text-muted">No model calls in this period.</p> : null}
      {data?.all_users && data.users.length ? (
        <div className="overflow-x-auto rounded-lg border border-line">
          <table className="w-full text-sm">
            <thead className="bg-panel-2">
              <tr>
                <th className={th}>User</th>
                <th className={th}>Answers</th>
                <th className={th}>In</th>
                <th className={th}>Out</th>
                <th className={th}>Calls</th>
                <th className={th}>Time</th>
              </tr>
            </thead>
            <tbody>
              {data.users.map((u) => (
                <tr key={u.user_id ?? "none"} className="border-t border-line">
                  <td className="px-3 py-2">{u.username}</td>
                  <td className={td}>{u.answers}</td>
                  <Cells u={u} />
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {data?.steps.length ? (
        <div className="overflow-x-auto rounded-lg border border-line">
          <table className="w-full text-sm">
            <thead className="bg-panel-2">
              <tr>
                <th className={th}>Step</th>
                <th className={th}>In</th>
                <th className={th}>Out</th>
                <th className={th}>Calls</th>
                <th className={th}>Time</th>
              </tr>
            </thead>
            <tbody>
              {data.steps.map((s) => (
                <tr key={s.step} className="border-t border-line">
                  <td className="px-3 py-2 font-mono text-[0.8rem]">{s.step}</td>
                  <Cells u={s} />
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </>
  );
}
