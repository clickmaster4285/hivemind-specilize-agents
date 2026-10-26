"use client";

import { useEffect, useState } from "react";

type Props = { text?: string; steps?: string[]; startedAt?: number };

// A rotating playful word for the generic "the model is generating" phase (descriptive steps like
// "Searching the web…" are shown as-is; only the vague ones below get replaced).
const GENERIC = new Set(["thinking", "working", "starting"]);
const WORDS = [
  "Pondering", "Percolating", "Cooking", "Brewing", "Noodling", "Musing", "Ruminating", "Crafting",
  "Synthesizing", "Cogitating", "Mulling", "Composing", "Conjuring", "Churning", "Crunching",
  "Deliberating", "Simmering", "Stewing", "Marinating", "Incubating", "Hatching", "Concocting",
  "Orchestrating", "Harmonizing", "Calibrating", "Formulating", "Ideating", "Imagining", "Weaving",
  "Distilling", "Whisking", "Kneading", "Fermenting", "Germinating", "Sprouting", "Unfurling",
  "Swirling", "Cascading", "Finagling", "Tinkering", "Puzzling", "Contemplating", "Wrangling",
  "Vibing", "Spelunking", "Reticulating", "Moseying", "Puttering", "Scheming", "Wibbling",
  // playful ones (your picks + the fun list)
  "Whatchamacalliting", "Razzmatazzing", "Metamorphosing", "Moonwalking", "Shenaniganing",
  "Lollygagging", "Flibbertigibbeting", "Discombobulating", "Combobulating", "Dilly-dallying",
  "Gallivanting", "Levitating", "Julienning", "Zesting", "Schlepping", "Osmosing", "Canoodling",
  "Booping", "Skedaddling", "Prestidigitating", "Cerebrating", "Sublimating", "Bootstrapping",
  "Garnishing", "Jitterbugging", "Transfiguring", "Cultivating", "Pollinating", "Quantumizing",
  "Leavening", "Precipitating", "Kerfuffling", "Faffing", "Tomfoolering", "Waddling",
];

function isGeneric(message: string): boolean {
  return GENERIC.has(message.trim().replace(/[….]+$/, "").toLowerCase());
}

/** The live procedure under a running answer: each step it has done ("Searching the web…", "Reading 5
 *  pages…", "Writing…") — past steps checked, the current one pulsing, with how long it has been going. */
export default function LiveStatus({ text, steps, startedAt }: Props) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  const seconds = startedAt ? Math.max(0, Math.round((now - startedAt) / 1000)) : null;
  const list = steps && steps.length ? steps : [text || "Working…"];

  return (
    <div className="mt-[0.5rem] flex flex-col gap-[0.3rem]" role="status" aria-live="polite">
      {list.map((step, i) => {
        const current = i === list.length - 1;
        const funWord =
          current && isGeneric(step)
            ? WORDS[(Math.floor((startedAt ?? 0) / 1000) + Math.floor((seconds ?? 0) / 3) + WORDS.length) % WORDS.length]
            : null;
        return (
          <p
            key={`${i}-${step}`}
            className={`flex items-center gap-[0.5rem] text-[0.8rem] ${current ? "text-[var(--text)]" : "text-[var(--muted)]"}`}
          >
            {current ? (
              <span className="h-[0.5rem] w-[0.5rem] shrink-0 rounded-full bg-[var(--copper)] animate-pulse" aria-hidden />
            ) : (
              <span className="shrink-0 text-[0.72rem] text-[var(--teal)]" aria-hidden>
                ✓
              </span>
            )}
            <span>{funWord ? `${funWord}…` : step}</span>
            {current && seconds !== null ? (
              <span className="text-[var(--muted)] [font-variant-numeric:tabular-nums]"> · {seconds} s</span>
            ) : null}
          </p>
        );
      })}
    </div>
  );
}
