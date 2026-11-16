import { useCallback, useEffect, useState } from "react";

import { getHealth } from "@/lib/api";
import { errorText } from "@/lib/chat";
import type { Health } from "@/lib/types";

/** API / model status for the status dots. Not polled automatically: call refresh() when needed. */
export function useHealth() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setHealth(await getHealth());
      setError(null);
    } catch (err) {
      setHealth(null);
      setError(errorText(err));
    }
  }, []);

  // load once: nothing else called refresh(), so the memory meter (context.history_budget) and the image settings
  // (image_pipeline) never had their data (ISSUES_LOG O76)
  useEffect(() => {
    let cancelled = false;
    void getHealth()
      .then((h) => {
        if (!cancelled) setHealth(h);
      })
      .catch((err) => {
        if (!cancelled) setError(errorText(err));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return { health, error, refresh };
}
