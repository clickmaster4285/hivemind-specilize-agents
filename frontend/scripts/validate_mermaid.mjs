// Headless Mermaid validator for the model benchmark (src/llm/bench_models.py).
// Reads a JSON file [{id, code}, ...] (path as argv[2]) and prints JSON
// [{id, rawValid, sanValid, error}] — validating each diagram as written AND after the app's
// sanitizeMermaid(), so the bench can report both "raw valid %" and "valid after safety net %".
//
//   node scripts/validate_mermaid.mjs <input.json>
//
// Mermaid needs a DOM, so we stand up jsdom globals before importing it (parse() only runs the
// grammar — no rendering — so this is fast and needs no browser).

import { readFileSync } from "node:fs";
import { JSDOM } from "jsdom";

const dom = new JSDOM("<!doctype html><html><body></body></html>", { pretendToBeVisual: true });
globalThis.window = dom.window;
globalThis.document = dom.window.document;
try {
  Object.defineProperty(globalThis, "navigator", { value: dom.window.navigator, configurable: true });
} catch {
  /* navigator already present */
}

const mermaid = (await import("mermaid")).default;
mermaid.initialize({ startOnLoad: false, securityLevel: "strict", theme: "neutral" });

// Keep in lockstep with sanitizeMermaid() in src/components/MermaidDiagram.tsx.
function sanitizeMermaid(code) {
  return code
    .split("\n")
    .map((line) => {
      if (/^\s*%%/.test(line)) return line;
      return line.replace(/\|([^|]*)\|/g, (whole, inner) => {
        const t = inner.trim();
        if (!t) return whole;
        const safe = t.replace(/["()[\]{}]/g, " ").replace(/\s+/g, " ").trim();
        return safe === t ? whole : `|${safe}|`;
      });
    })
    .join("\n");
}

async function valid(code) {
  try {
    await mermaid.parse(code);
    return { ok: true, error: "" };
  } catch (e) {
    return { ok: false, error: String((e && e.message) || e).split("\n")[0] };
  }
}

const inputPath = process.argv[2];
if (!inputPath) {
  console.error("usage: node scripts/validate_mermaid.mjs <input.json>");
  process.exit(2);
}

const items = JSON.parse(readFileSync(inputPath, "utf8"));
const out = [];
for (const { id, code } of items) {
  const raw = await valid(code);
  const san = raw.ok ? raw : await valid(sanitizeMermaid(code));
  out.push({ id, rawValid: raw.ok, sanValid: san.ok, error: san.ok ? "" : raw.error });
}
process.stdout.write(JSON.stringify(out));
