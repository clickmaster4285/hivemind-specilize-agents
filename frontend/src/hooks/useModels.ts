import { useCallback, useState } from "react";

import { listModels } from "@/lib/api";
import type { ModelInfo } from "@/lib/types";

/** The model list and the header's model override ("" = automatic route). */
export function useModels() {
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [override, setOverride] = useState("");

  const refresh = useCallback(async (force = false) => {
    const data = await listModels(force);
    setModels(data);
    // an override that is no longer available falls back to the automatic route
    const live = new Set(data.filter((m) => m.enabled && m.available !== false).map((m) => m.name));
    setOverride((prev) => (prev && !live.has(prev) ? "" : prev));
    return data;
  }, []);

  return { models, override, setOverride, refresh };
}
