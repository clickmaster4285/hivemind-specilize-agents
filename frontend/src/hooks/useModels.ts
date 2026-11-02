import { useCallback, useState } from "react";

import { listModels } from "@/lib/api";
import type { ModelInfo } from "@/lib/types";

/** The model list (shown in Settings). The route itself is always chosen automatically by the server. */
export function useModels() {
  const [models, setModels] = useState<ModelInfo[]>([]);

  const refresh = useCallback(async (force = false) => {
    const data = await listModels(force);
    setModels(data);
    return data;
  }, []);

  return { models, refresh };
}

export type ModelsApi = ReturnType<typeof useModels>;
