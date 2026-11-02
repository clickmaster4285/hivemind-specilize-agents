"use client";

import { setThemePref, useThemePref, type ThemePref } from "@/lib/theme";

const NEXT: Record<ThemePref, ThemePref> = { system: "light", light: "dark", dark: "system" };
const LABEL: Record<ThemePref, string> = { system: "System theme", light: "Light theme", dark: "Dark theme" };

/** Navbar button: cycles System → Light → Dark. The icon shows the current choice. */
export default function ThemeToggle() {
  const pref = useThemePref();
  return (
    <button
      type="button"
      className="icon-btn"
      onClick={() => setThemePref(NEXT[pref])}
      title={`${LABEL[pref]} — click for ${LABEL[NEXT[pref]].toLowerCase()}`}
      aria-label={`${LABEL[pref]}. Switch to ${LABEL[NEXT[pref]].toLowerCase()}`}
    >
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
        {pref === "light" ? (
          <>
            <circle cx="12" cy="12" r="4" />
            <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
          </>
        ) : pref === "dark" ? (
          <path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z" />
        ) : (
          <>
            <rect x="3" y="4" width="18" height="12" rx="2" />
            <path d="M8 20h8M12 16v4" />
          </>
        )}
      </svg>
    </button>
  );
}
