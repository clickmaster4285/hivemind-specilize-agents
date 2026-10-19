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
    <p className="live-status" role="status" aria-live="polite">
      <span className="live-dot" aria-hidden />
      {text || "Working…"}
      {seconds !== null ? <span className="live-time"> · {seconds} s</span> : null}
    </p>
  );
}
