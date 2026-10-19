import { useCallback, useMemo, useState } from "react";

import { useLocalStorage } from "@/hooks/useLocalStorage";
import {
  createSpecialist,
  deleteSpecialist,
  listSpecialists,
  resetSpecialists,
  specialistOptions,
  updateSpecialist,
} from "@/lib/api";
import type { Specialist, SpecialistInput, SpecialistOptions } from "@/lib/types";

/** The chat bar's specialist choice survives a reload (per browser; storage may be blocked). */
const SPECIALIST_KEY = "multeagent_specialist";

/**
 * Domain specialists (src/specialists.py) and the chat bar's choice:
 * "auto" = the assistant picks per message · "none" = no specialist · or a specialist id.
 */
export function useSpecialists() {
  const [specialists, setSpecialists] = useState<Specialist[]>([]);
  const [options, setOptions] = useState<SpecialistOptions>({ tools: [], collections: [] });
  const [stored, setChoice] = useLocalStorage(SPECIALIST_KEY, "auto");
  const choice = stored || "auto";

  const label = useMemo(() => {
    if (choice === "auto") return "specialist: auto";
    if (choice === "none") return "no specialist";
    const s = specialists.find((x) => x.id === choice);
    return s ? `${s.name} specialist` : "specialist: auto";
  }, [choice, specialists]);

  const load = useCallback(async () => {
    setSpecialists(await listSpecialists());
    try {
      setOptions(await specialistOptions());
    } catch {
      /* the edit form then shows no tool list; chatting is unaffected */
    }
  }, []);

  const clear = useCallback(() => setSpecialists([]), []);

  const save = useCallback(
    async (id: string | null, form: SpecialistInput) => {
      if (id) await updateSpecialist(id, form);
      else await createSpecialist(form);
      await load();
    },
    [load],
  );

  const remove = useCallback(
    async (id: string) => {
      await deleteSpecialist(id);
      if (choice === id) setChoice("auto");
      await load();
    },
    [choice, setChoice, load],
  );

  const reset = useCallback(async () => {
    await resetSpecialists();
    await load();
  }, [load]);

  return { specialists, options, choice, setChoice, label, load, clear, save, remove, reset };
}

export type SpecialistsApi = ReturnType<typeof useSpecialists>;
