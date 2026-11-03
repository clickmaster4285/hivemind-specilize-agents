import { useCallback, useState } from "react";

import { listSkills } from "@/lib/api";
import type { Skill } from "@/lib/types";

/** The user's skills (team + own, src/llm/skills.py) and what /name can run in the message box (BACKLOG L14). */
export function useSkills() {
  const [skills, setSkills] = useState<Skill[]>([]);
  // one per name, as the server picks it: the user's own, else the team's, else the built-in one
  const [usable, setUsable] = useState<Skill[]>([]);
  const [canManageTeam, setCanManageTeam] = useState(false);

  const load = useCallback(async () => {
    try {
      const found = await listSkills();
      setSkills(found.skills);
      setUsable(found.usable);
      setCanManageTeam(found.can_manage_team);
    } catch {
      setSkills([]);          // an API without skills: the chat works as before, the / menu stays empty
      setUsable([]);
    }
  }, []);

  const clear = useCallback(() => {
    setSkills([]);
    setUsable([]);
    setCanManageTeam(false);
  }, []);

  return { skills, usable, canManageTeam, load, clear };
}
