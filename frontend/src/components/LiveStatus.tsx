"use client";

import { useEffect, useState } from "react";

type Props = { text?: string; startedAt?: number };

/** "Writing… · 12 s" under a running answer: what the step is doing now, and for how long. */
export default function LiveStatus({ text, startedAt }: Props) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  const seconds = startedAt ? Math.max(0, Math.round((now - startedAt) / 1000)) : null;
  return (
    <p className="mt-[0.35rem] flex items-center gap-[0.45rem] text-[0.8rem] text-[var(--muted)]" role="status" aria-live="polite">
      <span className="h-[0.5rem] w-[0.5rem] rounded-full bg-[var(--copper)]" aria-hidden />
      {text || "Working…"}
      {seconds !== null ? <span className="[font-variant-numeric:tabular-nums]"> · {seconds} s</span> : null}
    </p>
  );
}
