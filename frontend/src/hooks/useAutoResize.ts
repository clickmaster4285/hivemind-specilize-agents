import { useCallback, useEffect, type RefObject } from "react";

/** Grow a textarea with its text (between minPx and maxPx); re-measures whenever `value` changes. */
export function useAutoResize(
  ref: RefObject<HTMLTextAreaElement | null>,
  value: string,
  { minPx = 35, maxPx = 256 }: { minPx?: number; maxPx?: number } = {},
) {
  const resize = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.max(Math.min(el.scrollHeight, maxPx), minPx)}px`;
  }, [ref, minPx, maxPx]);

  useEffect(() => {
    requestAnimationFrame(resize);
  }, [value, resize]);

  return resize;
}
