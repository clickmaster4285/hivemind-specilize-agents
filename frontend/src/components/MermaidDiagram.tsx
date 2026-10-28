"use client";

import { useEffect, useId, useMemo, useState } from "react";

import SvgPanZoom from "./SvgPanZoom";

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

let ready: Promise<typeof import("mermaid").default> | null = null;

/** Load Mermaid once, only when a diagram is on screen (it is large). */
function loadMermaid() {
  ready ??= import("mermaid").then(({ default: mermaid }) => {
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: "strict",
      theme: "neutral",
      // plain SVG text instead of HTML labels, so the PNG export can draw it on a canvas.
      // `step` = orthogonal right-angle edges (the clean org-chart / taxonomy look). `basis` drew wavy
      // curves that bunched and crossed on wide trees and read as messy vs mermaid.live's straight lines.
      flowchart: { htmlLabels: false, curve: "step" },
      // a font every browser has: boxes are sized with it, and "Open full size" / PNG export draw
      // without the app's web font — with IBM Plex the labels came out wider than their boxes
      fontFamily: "Arial, Helvetica, sans-serif",
      themeVariables: { fontFamily: "Arial, Helvetica, sans-serif" },
      // architecture-beta icons use `fill: currentColor`, and the diagram defaults `color` to white,
      // so mdi/logos icons render white-on-white (invisible) on our light card. Force a dark colour on
      // the architecture groups so the icons show. Travels in the SVG <style>, so full-size + PNG keep it.
      themeCSS: '.architecture-service, g[class*="architecture"] { color: #1f2937; }',
    });
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

function download(name: string, blob: Blob) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function svgToPng(svg: string): Promise<Blob> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    const url = URL.createObjectURL(new Blob([svg], { type: "image/svg+xml;charset=utf-8" }));
    img.onload = () => {
      const scale = 2; // sharp in documents and slides
      const canvas = document.createElement("canvas");
      canvas.width = img.naturalWidth * scale;
      canvas.height = img.naturalHeight * scale;
      const ctx = canvas.getContext("2d");
      if (!ctx) return reject(new Error("no canvas"));
      ctx.fillStyle = "#ffffff";
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

  useEffect(() => {
    let cancelled = false;
    loadMermaid()
      .then((mermaid) => mermaid.render(id, clean))
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
  }, [clean, id]);

  const copy = async () => {
    await navigator.clipboard.writeText(clean);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  const codeVisible = showCode || Boolean(error);

  return (
    <div className="mermaid-card">
      {error ? (
        <div className="mermaid-error">
          The diagram could not be drawn: {error.split("\n")[0]}. Ask me to fix it, or paste the code into{" "}
          <a href="https://mermaid.live" target="_blank" rel="noreferrer">
            mermaid.live
          </a>
          .
        </div>
      ) : svg ? (
        <SvgPanZoom svg={svg} />
      ) : (
        <div className="mermaid-loading">Drawing…</div>
      )}
      <div className="mermaid-actions">
        {error && (
          <button type="button" className="btn sm" onClick={() => askToFix(error)}>
            Fix diagram
          </button>
        )}
        <button type="button" className="btn ghost sm" disabled={!svg} onClick={() => openFullSize(svg)}>
          Open full size
        </button>
        <button type="button" className="btn ghost sm" disabled={!svg}
          onClick={() => download("diagram.svg", new Blob([svg], { type: "image/svg+xml" }))}>
          Download SVG
        </button>
        <button type="button" className="btn ghost sm" disabled={!svg}
          onClick={() => svgToPng(svg).then((b) => download("diagram.png", b)).catch((e) => setError(String(e)))}>
          Download PNG
        </button>
        <button type="button" className="btn ghost sm" onClick={() => void copy()}>
          {copied ? "Copied" : "Copy code"}
        </button>
        {!error && (
          <button type="button" className="btn ghost sm" onClick={() => setShowCode((v) => !v)}>
            {showCode ? "Hide code" : "Show code"}
          </button>
        )}
      </div>
      {codeVisible && <pre className="md-pre mermaid-code">{clean}</pre>}
    </div>
  );
}
