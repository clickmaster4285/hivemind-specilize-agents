"use client";

import { useEffect, useMemo, useState } from "react";

import { makeDrawIcon, neededPacks } from "@/lib/deviceIcons";
import { buildGraphSvg, graphIconNames, parseGraphSpec } from "@/lib/graphdiagram";
import { buildNetworkSvg, iconNames, looksCompleteJson, parseNetSpec } from "@/lib/netdiagram";

import SvgPanZoom from "./SvgPanZoom";

/* Renders a ```netdiagram block (hub-and-spoke: sites → HQ) as a designed network infographic.
 * The layout/icons live in lib/netdiagram.ts + lib/deviceIcons.ts (pure SVG-string builders), so PNG/SVG
 * export draws exactly what is on screen and the builder is verifiable outside React. Devices are named in
 * free text; only the icon packs a given diagram actually needs are lazy-loaded (most use hand-drawn icons). */

// eslint-disable-next-line @typescript-eslint/no-explicit-any -- the Iconify icons.json is a big untyped blob
type Pack = any;
const PACK_LOADERS: Record<string, () => Promise<{ default: Pack }>> = {
  mdi: () => import("@iconify-json/mdi/icons.json"),
  logos: () => import("@iconify-json/logos/icons.json"),
};

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
      const scale = 2;
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

export default function NetworkDiagram({ code }: { code: string }) {
  // a ```netdiagram block is EITHER a generic topology graph (nodes+links) or the hub-spoke shape (sites+hq)
  const graph = useMemo(() => parseGraphSpec(code), [code]);
  const net = useMemo(() => (graph ? null : parseNetSpec(code)), [graph, code]);
  const [packs, setPacks] = useState<Record<string, Pack>>({});
  const [err, setErr] = useState("");

  // Lazy-load only the icon packs this diagram needs (most diagrams use hand-drawn icons → none).
  useEffect(() => {
    const names = graph ? graphIconNames(graph) : net ? iconNames(net) : null;
    if (!names) return;
    const want = neededPacks(names);
    const missing = [...want].filter((p) => !packs[p] && PACK_LOADERS[p]);
    if (!missing.length) return;
    let cancelled = false;
    Promise.all(missing.map((p) => PACK_LOADERS[p]().then((m) => [p, m.default] as const).catch(() => null)))
      .then((loaded) => {
        if (cancelled) return;
        const add: Record<string, Pack> = {};
        for (const entry of loaded) if (entry) add[entry[0]] = entry[1];
        if (Object.keys(add).length) setPacks((prev) => ({ ...prev, ...add }));
      });
    return () => { cancelled = true; };
  }, [graph, net, packs]);

  const svg = useMemo(() => {
    const draw = makeDrawIcon(packs);
    if (graph) return buildGraphSvg(graph, draw);
    if (net) return buildNetworkSvg(net, draw);
    return "";
  }, [graph, net, packs]);

  if (!graph && !net) {
    // while the answer is still streaming the JSON is incomplete — show a placeholder, not an error
    if (!looksCompleteJson(code)) {
      return (
        <div className="mermaid-card">
          <div className="mermaid-loading">Drawing the network diagram…</div>
        </div>
      );
    }
    // complete but not parseable — fall back to showing the code, so nothing is lost
    return (
      <div className="mermaid-card">
        <div className="mermaid-error">Could not render this network diagram (invalid spec).</div>
        <pre className="md-pre mermaid-code">{code}</pre>
      </div>
    );
  }

  return (
    <div className="mermaid-card">
      <SvgPanZoom svg={svg} />
      <div className="mermaid-actions">
        <button type="button" className="btn ghost sm"
          onClick={() => {
            const url = URL.createObjectURL(new Blob([svg], { type: "image/svg+xml" }));
            window.open(url, "_blank", "noopener");
            setTimeout(() => URL.revokeObjectURL(url), 60_000);
          }}>
          Open full size
        </button>
        <button type="button" className="btn ghost sm"
          onClick={() => download("network-diagram.svg", new Blob([svg], { type: "image/svg+xml" }))}>
          Download SVG
        </button>
        <button type="button" className="btn ghost sm"
          onClick={() => svgToPng(svg).then((b) => download("network-diagram.png", b)).catch((e) => setErr(String(e)))}>
          Download PNG
        </button>
      </div>
      {err ? <div className="mermaid-error">{err}</div> : null}
    </div>
  );
}
