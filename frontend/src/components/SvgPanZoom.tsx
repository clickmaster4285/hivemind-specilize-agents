"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/** A pan + zoom viewport for a rendered SVG string, like the Mermaid Live canvas: drag to move the diagram
 *  around, wheel or the +/- buttons to zoom (toward the cursor), double-click or ⟲ to fit it again, ⤢ for
 *  fullscreen. The box takes the diagram's own aspect ratio and the view starts FITTED, so the whole diagram
 *  is visible without panning. The SVG itself is unchanged, so PNG/SVG export and "Open full size" still use
 *  the original. */

const MIN_K = 0.2;
const MAX_K = 10;
const clampK = (k: number) => Math.min(MAX_K, Math.max(MIN_K, k));

type Transform = { x: number; y: number; k: number };

/** width / height from the svg's viewBox, or null when it has none. */
function aspectOf(svg: string): { w: number; h: number } | null {
  const m = svg.match(/viewBox="\s*[-\d.]+[\s,]+[-\d.]+[\s,]+([\d.]+)[\s,]+([\d.]+)\s*"/);
  if (!m) return null;
  const w = parseFloat(m[1]);
  const h = parseFloat(m[2]);
  return w > 0 && h > 0 ? { w, h } : null;
}

export default function SvgPanZoom({ svg }: { svg: string }) {
  const box = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const [t, setT] = useState<Transform>({ x: 0, y: 0, k: 1 });
  const drag = useRef<{ px: number; py: number; x: number; y: number } | null>(null);
  const touched = useRef(false); // once the user moves/zooms, stop auto-fitting on resize
  const [panning, setPanning] = useState(false);
  const aspect = aspectOf(svg);

  /** The transform that shows the WHOLE diagram, centred (never zooms in past 1:1). */
  const fit = useCallback((): Transform => {
    const el = box.current;
    const inner = content.current;
    if (!el || !inner) return { x: 0, y: 0, k: 1 };
    const cw = el.clientWidth;
    const ch = el.clientHeight;
    const nw = inner.offsetWidth;
    const nh = inner.offsetHeight;
    if (!nw || !nh) return { x: 0, y: 0, k: 1 };
    const k = Math.min(1, cw / nw, ch / nh);
    return { k, x: (cw - nw * k) / 2, y: (ch - nh * k) / 2 };
  }, []);

  // Fit when a diagram loads and whenever the box is resized (until the user takes over).
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    touched.current = false;
    const ro = new ResizeObserver(() => {
      if (!touched.current) setT(fit());
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [svg, fit]);

  // native, non-passive wheel listener so we can preventDefault (the page must not scroll while zooming)
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      touched.current = true;
      const r = el.getBoundingClientRect();
      const cx = e.clientX - r.left;
      const cy = e.clientY - r.top;
      setT((p) => {
        const k = clampK(p.k * (e.deltaY < 0 ? 1.12 : 1 / 1.12));
        const ratio = k / p.k; // keep the point under the cursor fixed (transform-origin 0 0)
        return { k, x: cx - (cx - p.x) * ratio, y: cy - (cy - p.y) * ratio };
      });
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  const down = (e: React.PointerEvent) => {
    (e.currentTarget as Element).setPointerCapture?.(e.pointerId);
    touched.current = true;
    drag.current = { px: e.clientX, py: e.clientY, x: t.x, y: t.y };
    setPanning(true);
  };
  const move = (e: React.PointerEvent) => {
    const d = drag.current;
    if (!d) return;
    setT((p) => ({ ...p, x: d.x + (e.clientX - d.px), y: d.y + (e.clientY - d.py) }));
  };
  const up = () => {
    drag.current = null;
    setPanning(false);
  };

  const zoomBy = (f: number) => {
    touched.current = true;
    setT((p) => {
      const el = box.current;
      const cx = el ? el.clientWidth / 2 : 0;
      const cy = el ? el.clientHeight / 2 : 0;
      const k = clampK(p.k * f);
      const ratio = k / p.k;
      return { k, x: cx - (cx - p.x) * ratio, y: cy - (cy - p.y) * ratio };
    });
  };
  const refit = () => {
    touched.current = false;
    setT(fit());
  };
  const fullscreen = () => void box.current?.requestFullscreen?.().catch(() => undefined);

  const btn =
    "flex h-[1.75rem] w-[1.75rem] items-center justify-center rounded-[0.35rem] border border-[var(--line)] " +
    "bg-[color-mix(in_srgb,var(--panel)_88%,transparent)] text-[0.95rem] leading-none text-[var(--text)] hover:bg-[var(--panel-2)]";

  return (
    <div
      ref={box}
      className="relative overflow-hidden rounded-[0.5rem] border border-[var(--line)] bg-white [&:fullscreen]:!aspect-auto [&:fullscreen]:!h-screen [&:fullscreen]:!max-h-none"
      style={{
        // the box matches the diagram's shape (so nothing is cropped); capped so a very tall one can't take over the chat.
        // width must be explicit: with width:auto, aspect-ratio + max-height shrinks the box's WIDTH instead of its height.
        width: "100%",
        ...(aspect ? { aspectRatio: `${aspect.w} / ${aspect.h}` } : { height: "22rem" }),
        maxHeight: "80vh",
        minHeight: "11rem",
        touchAction: "none",
        cursor: panning ? "grabbing" : "grab",
      }}
      onPointerDown={down}
      onPointerMove={move}
      onPointerUp={up}
      onPointerLeave={up}
      onDoubleClick={refit}
    >
      <div
        ref={content}
        className="[&>svg]:block" // an inline svg leaves a few px of baseline gap under it, which skews the fit
        style={{ transform: `translate(${t.x}px, ${t.y}px) scale(${t.k})`, transformOrigin: "0 0", width: "100%" }}
        dangerouslySetInnerHTML={{ __html: svg }}
      />
      <div
        className="absolute right-[0.4rem] top-[0.4rem] flex gap-[0.25rem]"
        onPointerDown={(e) => e.stopPropagation()}
        onDoubleClick={(e) => e.stopPropagation()}
      >
        <button type="button" title="Zoom in" aria-label="Zoom in" className={btn} onClick={() => zoomBy(1.25)}>+</button>
        <button type="button" title="Zoom out" aria-label="Zoom out" className={btn} onClick={() => zoomBy(1 / 1.25)}>−</button>
        <button type="button" title="Fit to view" aria-label="Fit to view" className={btn} onClick={refit}>⟲</button>
        <button type="button" title="Fullscreen" aria-label="Fullscreen" className={btn} onClick={fullscreen}>⤢</button>
      </div>
      <div className="pointer-events-none absolute bottom-[0.4rem] left-[0.5rem] text-[0.68rem] text-[var(--muted)]">
        drag to move · scroll to zoom · double-click to fit
      </div>
    </div>
  );
}
