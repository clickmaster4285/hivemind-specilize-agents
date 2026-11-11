"use client";

import { useEffect, useState } from "react";

import { getCaptcha } from "@/lib/api";
import type { CaptchaAnswer, CaptchaChallenge } from "@/lib/types";
import { btn } from "@/lib/ui";

type Props = {
  /** a new picture whenever this changes: each one works once (BACKLOG L16 S2b) */
  round: number;
  onChange: (answer: CaptchaAnswer | null) => void;
};

const field =
  "w-full rounded-[0.55rem] border border-line bg-panel-2 px-[0.7rem] py-[0.55rem] font-[inherit] tracking-[0.2em] uppercase text-text outline-none";

/** The login / sign-up CAPTCHA: a picture of characters drawn by our own API, and a box to type them. */
export default function Captcha({ round, onChange }: Props) {
  const [challenge, setChallenge] = useState<CaptchaChallenge | null>(null);
  const [typed, setTyped] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [asked, setAsked] = useState(0);              // "New image" clicks

  useEffect(() => {
    let cancelled = false;
    getCaptcha()
      .then((made) => {
        if (cancelled) return;
        setChallenge(made);
        setTyped("");
        setError(null);
        onChange(null);
      })
      .catch((err) => !cancelled && setError(err instanceof Error ? err.message : String(err)));
    return () => {
      cancelled = true;
    };
  }, [round, asked, onChange]);

  function type(value: string) {
    setTyped(value);
    onChange(challenge && value.trim() ? { captcha_id: challenge.id, captcha_answer: value.trim() } : null);
  }

  return (
    <div className="flex flex-col gap-1.5 text-[0.82rem] text-muted">
      <span>Type the characters in the picture</span>
      <div className="flex items-center gap-2">
        {challenge ? (
          // eslint-disable-next-line @next/next/no-img-element -- a data URL from our API, not a file to optimise
          <img src={challenge.image} alt="CAPTCHA characters" className="h-[60px] rounded-md border border-line" />
        ) : (
          <span className="grid h-[60px] w-[180px] place-items-center rounded-md border border-line">…</span>
        )}
        <button type="button" className={btn("ghost", "sm")} onClick={() => setAsked((n) => n + 1)}>
          New image
        </button>
      </div>
      <input
        className={field}
        name="captcha"
        autoComplete="off"
        spellCheck={false}
        value={typed}
        onChange={(e) => type(e.target.value)}
        required
      />
      {error ? <span className="text-danger">{error}</span> : null}
    </div>
  );
}
