import { useEffect, useRef } from "react";

/** Listen to a window CustomEvent; the handler always sees the latest render's values. */
export function useWindowEvent<T>(name: string, handler: (detail: T) => void) {
  const latest = useRef(handler);
  useEffect(() => {
    latest.current = handler;
  });
  useEffect(() => {
    const listener = (event: Event) => latest.current((event as CustomEvent<T>).detail);
    window.addEventListener(name, listener);
    return () => window.removeEventListener(name, listener);
  }, [name]);
}
