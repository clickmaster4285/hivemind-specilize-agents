import { useCallback, useMemo, useState } from "react";

import { listSkills } from "@/lib/api";
import type { Skill } from "@/lib/types";

/** The user's skills (team + own, src/llm/skills.py) — typed as /name in the message box (BACKLOG L14). */
export function useSkills() {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [canManageTeam, setCanManageTeam] = useState(false);

  const load = useCallback(async () => {
    try {
      const found = await listSkills();
      setSkills(found.skills);
      setCanManageTeam(found.can_manage_team);
    } catch {
      setSkills([]);          // an API without skills: the chat works as before, the / menu stays empty
    }
  }, []);

  /** What /name can run: switched-on skills, the user's own winning over a team skill of the same name (as the
   * server picks). */
  const usable = useMemo(() => {
    const byName = new Map<string, Skill>();
    for (const s of skills) if (s.enabled && (!byName.has(s.name) || !s.team)) byName.set(s.name, s);
    return [...byName.values()];
  }, [skills]);

  const clear = useCallback(() => {
    setSkills([]);
    setCanManageTeam(false);
  }, []);

  return { skills, usable, canManageTeam, load, clear };
}
