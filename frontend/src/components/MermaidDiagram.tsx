"use client";

import { useEffect, useId, useState } from "react";

type Props = { code: string };

let ready: Promise<typeof import("mermaid").default> | null = null;

/** Load Mermaid once, only when a diagram is on screen (it is large). */
function loadMermaid() {
  ready ??= import("mermaid").then(({ default: mermaid }) => {
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: "strict",
      theme: "neutral",
      // plain SVG text instead of HTML labels, so the PNG export can draw it on a canvas
      flowchart: { htmlLabels: false, curve: "basis" },
      // a font every browser has: boxes are sized with it, and "Open full size" / PNG export draw
      // without the app's web font — with IBM Plex the labels came out wider than their boxes
      fontFamily: "Arial, Helvetica, sans-serif",
      themeVariables: { fontFamily: "Arial, Helvetica, sans-serif" },
    });
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

  useEffect(() => {
    let cancelled = false;
    loadMermaid()
      .then((mermaid) => mermaid.render(id, code))
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
  }, [code, id]);

  const copy = async () => {
    await navigator.clipboard.writeText(code);
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
        <div className="mermaid-svg" dangerouslySetInnerHTML={{ __html: svg }} />
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
      {codeVisible && <pre className="md-pre mermaid-code">{code}</pre>}
    </div>
  );
}
