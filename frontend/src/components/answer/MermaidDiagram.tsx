"use client";

import type { MermaidConfig } from "mermaid";
import { useEffect, useId, useMemo, useState } from "react";

import SvgPanZoom from "@/components/answer/SvgPanZoom";
import { useResolvedTheme, type ResolvedTheme } from "@/lib/theme";
import { btn, diagram } from "@/lib/ui";

type Props = { code: string };

/** Conservative safety net for model-written Mermaid: fix the common parse-breakers before rendering so
 *  a small mistake draws instead of erroring into a "Fix diagram" round-trip. Deliberately minimal — it
 *  only touches things that are invalid anyway, so it never mangles a already-valid diagram.
 *  Rule 1: edge labels `|...|` can't contain brackets/quotes (e.g. `|Forwarding (MAC Table)|` broke the
 *  parser); strip `(){}[]"` from the label text and collapse spaces, keeping a plain multi-word label. */
function sanitizeMermaid(code: string): string {
  return code
    .split("\n")
    .map((line) => {
      if (/^\s*%%/.test(line)) return line; // leave directives/comments alone
      return line.replace(/\|([^|]*)\|/g, (whole, inner: string) => {
        const t = inner.trim();
        if (!t) return whole;
        const safe = t.replace(/["()[\]{}]/g, " ").replace(/\s+/g, " ").trim();
        return safe === t ? whole : `|${safe}|`;
      });
    })
    .join("\n");
}

type Mermaid = typeof import("mermaid").default;
let ready: Promise<Mermaid> | null = null;

/** Per app theme: Mermaid 12's colour theme, the card colour (also the background of the exports and the
 *  full-size view, so white text in the dark theme never lands on white), and the colour of architecture icons. */
const LOOK: Record<ResolvedTheme, { theme: MermaidConfig["theme"]; background: string; ink: string }> = {
  light: { theme: "redux-color", background: "#f7f5f0", ink: "#1f2937" },
  dark: { theme: "redux-dark-color", background: "#1a1f27", ink: "#e7e2d8" },
};

/** Load Mermaid once, only when a diagram is on screen (it is large). */
function loadMermaid(): Promise<Mermaid> {
  ready ??= import("mermaid").then(({ default: mermaid }) => {
    // Icon packs for `architecture-beta` diagrams — bundled locally (no CDN), lazy-loaded with the
    // diagram. Icons render as inline SVG, so the PNG/SVG export still works. Referenced as
    // `logos:aws` (tech/brand logos) or `mdi:server` (generic shapes) in service(...) annotations.
    // cast: the @iconify-json packages type their JSON default as `{} | IconifyJSON`, which does not
    // match Mermaid's `loader: () => Promise<IconifyJSON>`; the data is a valid pack at runtime.
    mermaid.registerIconPacks([
      { name: "logos", loader: () => import("@iconify-json/logos/icons.json").then((m) => m.default) },
      { name: "mdi", loader: () => import("@iconify-json/mdi/icons.json").then((m) => m.default) },
    ] as Parameters<typeof mermaid.registerIconPacks>[0]);
    return mermaid;
  });
  return ready;
}

/** Mermaid's settings for the app's theme, set before each drawing (the theme can change between two). */
function configure(mermaid: Mermaid, theme: ResolvedTheme): void {
  const look = LOOK[theme];
  mermaid.initialize({
    startOnLoad: false,
    securityLevel: "strict",
    // Mermaid 12's look (ISSUES_LOG O62): `neo` and a colour theme that follows the app's light / dark mode,
    // compared in headless Chrome on 7 diagram types against our old `neutral` / `classic` / `dagre`. No
    // `layout`: Mermaid's default is ELK for flowchart, class, state, ER, requirement and use-case diagrams
    // (groups no longer overlap their edges), and each other type keeps its own; forcing ELK broke mindmaps.
    look: "neo",
    theme: look.theme,
    // plain SVG text instead of HTML labels, so the PNG export can draw it on a canvas (a canvas holding
    // HTML may not be exported). Top level: Mermaid 11 ignores the old `flowchart.htmlLabels` (ISSUES_LOG O45)
    htmlLabels: false,
    // a font every browser has: boxes are sized with it, and "Open full size" / PNG export draw
    // without the app's web font — with IBM Plex the labels came out wider than their boxes
    fontFamily: "Arial, Helvetica, sans-serif",
    themeVariables: { fontFamily: "Arial, Helvetica, sans-serif" },
    // architecture-beta icons use `fill: currentColor`, and the diagram defaults `color` to white, so mdi/logos
    // icons drew white-on-white on the light card. Set the theme's ink. Travels in the SVG <style>, so
    // full-size + PNG keep it.
    themeCSS: `.architecture-service, g[class*="architecture"] { color: ${look.ink}; }`,
  });
}

/** The SVG with the card's colour behind it, for the downloads and the full-size view. */
function withBackground(svg: string, background: string): string {
  const doc = new DOMParser().parseFromString(svg, "image/svg+xml");
  const root = doc.documentElement;
  if (doc.querySelector("parsererror") || root.nodeName !== "svg") return svg;
  root.setAttribute("style", `${root.getAttribute("style") ?? ""};background-color:${background}`);
  return new XMLSerializer().serializeToString(root);
}

function download(name: string, blob: Blob) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// PNG pixels per diagram unit: 3× the diagram's own size stays sharp when zoomed in a document or a slide
const PNG_SCALE = 3;
// the longest PNG side: browsers blank out or refuse larger canvases (Safari limits the area, others a side)
const PNG_MAX_SIDE = 8192;

/** Mermaid writes `width="100%"`, so as an <img> the SVG has no size of its own and the browser draws it
 *  ~300 px wide (a 1,824-unit mindmap came out 600 px, blurry). Give it the viewBox size. */
function sizedSvg(svg: string): { text: string; width: number; height: number } | null {
  const doc = new DOMParser().parseFromString(svg, "image/svg+xml");
  const root = doc.documentElement;
  if (doc.querySelector("parsererror") || root.nodeName !== "svg") return null;
  const [, , width, height] = (root.getAttribute("viewBox") ?? "").trim().split(/[\s,]+/).map(Number);
  if (!(width > 0 && height > 0)) return null;
  root.setAttribute("width", String(width));
  root.setAttribute("height", String(height));
  return { text: new XMLSerializer().serializeToString(root), width, height };
}

function svgToPng(svg: string, background: string): Promise<Blob> {
  return new Promise((resolve, reject) => {
    const sized = sizedSvg(svg);
    const img = new Image();
    const url = URL.createObjectURL(new Blob([sized?.text ?? svg], { type: "image/svg+xml;charset=utf-8" }));
    img.onload = () => {
      const width = sized?.width ?? img.naturalWidth;
      const height = sized?.height ?? img.naturalHeight;
      const scale = Math.min(PNG_SCALE, PNG_MAX_SIDE / Math.max(width, height));
      const canvas = document.createElement("canvas");
      canvas.width = Math.round(width * scale);
      canvas.height = Math.round(height * scale);
      const ctx = canvas.getContext("2d");
      if (!ctx) return reject(new Error("no canvas"));
      ctx.fillStyle = background;
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
      URL.revokeObjectURL(url);
      canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("PNG export failed"))), "image/png");
    };
    img.onerror = () => reject(new Error("could not load the SVG"));
    img.src = url;
  });
}

/** Big diagrams are hard to read in the chat column: show the SVG alone in a new tab (zoomable). */
function openFullSize(svg: string) {
  const url = URL.createObjectURL(new Blob([svg], { type: "image/svg+xml" }));
  window.open(url, "_blank", "noopener");
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

/** Send the browser's exact Mermaid error back to the assistant (AppProvider listens). The previous
 *  answer is a diagram, so the router keeps this follow-up on the diagram writer. */
function askToFix(error: string) {
  const text = `Fix the diagram above: Mermaid could not draw it. Error: ${error.split("\n").slice(0, 3).join(" ")}`;
  window.dispatchEvent(new CustomEvent("multeagent:ask", { detail: text }));
}

/** Renders a ```mermaid block as a diagram, with SVG/PNG download and the code behind it. */
export default function MermaidDiagram({ code }: Props) {
  const id = `mmd-${useId().replace(/[^a-zA-Z0-9]/g, "")}`;
  const [svg, setSvg] = useState("");
  const [error, setError] = useState("");
  const [showCode, setShowCode] = useState(false);
  const [copied, setCopied] = useState(false);
  const clean = useMemo(() => sanitizeMermaid(code), [code]);
  const theme = useResolvedTheme();
  const { background } = LOOK[theme];

  useEffect(() => {
    let cancelled = false;
    loadMermaid()
      .then((mermaid) => {
        configure(mermaid, theme);
        return mermaid.render(id, clean);
      })
      .then(({ svg: out }) => {
        if (!cancelled) {
          setSvg(out);
          setError("");
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
        document.getElementById(`d${id}`)?.remove(); // Mermaid leaves an error box in <body>
      });
    return () => {
      cancelled = true;
    };
  }, [clean, id, theme]);

  const copy = async () => {
    await navigator.clipboard.writeText(clean);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  const codeVisible = showCode || Boolean(error);

  return (
    <div className={theme === "dark" ? diagram.cardDark : diagram.card}>
      {error ? (
        <div className={diagram.error}>
          The diagram could not be drawn: {error.split("\n")[0]}. Ask me to fix it, or paste the code into{" "}
          <a href="https://mermaid.live" target="_blank" rel="noreferrer">
            mermaid.live
          </a>
          .
        </div>
      ) : svg ? (
        <SvgPanZoom svg={svg} />
      ) : (
        <div className={diagram.loading}>Drawing…</div>
      )}
      <div className={theme === "dark" ? diagram.actionsDark : diagram.actions}>
        {error && (
          <button type="button" className={btn("sm")} onClick={() => askToFix(error)}>
            Fix diagram
          </button>
        )}
        <button type="button" className={btn("ghost", "sm")} disabled={!svg}
          onClick={() => openFullSize(withBackground(svg, background))}>
          Open full size
        </button>
        <button type="button" className={btn("ghost", "sm")} disabled={!svg}
          onClick={() => download("diagram.svg",
            new Blob([withBackground(svg, background)], { type: "image/svg+xml" }))}>
          Download SVG
        </button>
        <button type="button" className={btn("ghost", "sm")} disabled={!svg}
          onClick={() => svgToPng(svg, background)
            .then((b) => download("diagram.png", b))
            .catch((e) => setError(String(e)))}>
          Download PNG
        </button>
        <button type="button" className={btn("ghost", "sm")} onClick={() => void copy()}>
          {copied ? "Copied" : "Copy code"}
        </button>
        {!error && (
          <button type="button" className={btn("ghost", "sm")} onClick={() => setShowCode((v) => !v)}>
            {showCode ? "Hide code" : "Show code"}
          </button>
        )}
      </div>
      {codeVisible && <pre className={diagram.code}>{clean}</pre>}
    </div>
  );
}
