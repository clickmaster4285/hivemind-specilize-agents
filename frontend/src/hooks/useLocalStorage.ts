import { useCallback, useSyncExternalStore } from "react";

const CHANGE_EVENT = "multeagent:local-storage";
// used when the browser blocks storage (private mode, policies): the value lasts until reload
const memory = new Map<string, string>();

function read(key: string, fallback: string): string {
  try {
    const stored = localStorage.getItem(key);
    if (stored !== null) return stored;
  } catch {
    /* storage blocked */
  }
  return memory.get(key) ?? fallback;
}

/**
 * A string kept in localStorage, safe with server rendering (the server and the first render use
 * `fallback`), and shared by every component that uses the same key — also across browser tabs.
 */
export function useLocalStorage(key: string, fallback: string): [string, (value: string) => void] {
  const subscribe = useCallback(
    (onChange: () => void) => {
      const listener = (event: Event) => {
        if (!(event instanceof StorageEvent) || event.key === key) onChange();
      };
      window.addEventListener("storage", listener);
      window.addEventListener(CHANGE_EVENT, listener);
      return () => {
        window.removeEventListener("storage", listener);
        window.removeEventListener(CHANGE_EVENT, listener);
      };
    },
    [key],
  );
  const value = useSyncExternalStore(
    subscribe,
    () => read(key, fallback),
    () => fallback,
  );
  const setValue = useCallback(
    (next: string) => {
      memory.set(key, next);
      try {
        localStorage.setItem(key, next);
      } catch {
        /* storage blocked: kept in memory until reload */
      }
      window.dispatchEvent(new Event(CHANGE_EVENT));
    },
    [key],
  );
  return [value, setValue];
}
