"use client";

import { useState, type ReactNode } from "react";

import { setThemePref, useThemePref, type ThemePref } from "@/lib/theme";

const icon = (paths: ReactNode) => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    {paths}
  </svg>
);

const OPTIONS: { value: ThemePref; label: string; icon: ReactNode }[] = [
  {
    value: "light",
    label: "Light",
    icon: icon(
      <>
        <circle cx="12" cy="12" r="4" />
        <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
      </>,
    ),
  },
  { value: "dark", label: "Dark", icon: icon(<path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z" />) },
  {
    value: "system",
    label: "System",
    icon: icon(
      <>
        <rect x="3" y="4" width="18" height="12" rx="2" />
        <path d="M8 20h8M12 16v4" />
      </>,
    ),
  },
];

/** Navbar theme menu: click to open, pick Light / Dark / System. The choice is saved on this device
 *  (src/lib/theme.ts); the button shows the current one. */
export default function ThemeToggle() {
  const pref = useThemePref();
  const [open, setOpen] = useState(false);
  const current = OPTIONS.find((o) => o.value === pref) ?? OPTIONS[0];

  function pick(value: ThemePref) {
    setThemePref(value);
    setOpen(false);
  }

  return (
    <div className="relative">
      <button
        type="button"
        className="flex h-[2.1rem] items-center gap-1.5 rounded-[0.55rem] border border-[var(--line)] bg-[var(--panel-2)] px-[0.6rem] text-[0.82rem] text-[var(--text)] hover:border-[var(--copper)]"
        aria-haspopup="menu"
        aria-expanded={open}
        title="Theme"
        onClick={() => setOpen((o) => !o)}
      >
        {current.icon}
        <span>{current.label}</span>
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
          <path d="M6 9l6 6 6-6" />
        </svg>
      </button>
      {open ? (
        <>
          <button type="button" className="fixed inset-0 z-40 cursor-default" aria-hidden tabIndex={-1} onClick={() => setOpen(false)} />
          <div role="menu" className="absolute right-0 top-full z-50 mt-1.5 w-[10rem] rounded-xl border border-[var(--line)] bg-[var(--panel)] p-1.5 shadow-[var(--shadow)]">
            {OPTIONS.map((o) => (
              <button
                key={o.value}
                type="button"
                role="menuitemradio"
                aria-checked={pref === o.value}
                onClick={() => pick(o.value)}
                className={`flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-[0.85rem] hover:bg-[var(--hover)] ${
                  pref === o.value ? "font-semibold text-[var(--text-strong)]" : "text-[var(--text)]"
                }`}
              >
                <span className="text-[var(--muted)]">{o.icon}</span>
                <span className="flex-1">{o.label}</span>
                {pref === o.value ? <span className="text-[var(--copper)]" aria-hidden>✓</span> : null}
              </button>
            ))}
          </div>
        </>
      ) : null}
    </div>
  );
}
