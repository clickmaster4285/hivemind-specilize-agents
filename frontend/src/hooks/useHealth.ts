import { useCallback, useState } from "react";

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

  return { health, error, refresh };
}
