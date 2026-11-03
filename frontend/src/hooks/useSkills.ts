import { useCallback, useState } from "react";

import { addCatalogSkill, createSkill, deleteSkill, listSkills, updateSkill, uploadSkill } from "@/lib/api";
import type { Skill, SkillInput } from "@/lib/types";

/** The user's skills (team + own, src/llm/skills.py) and what /name can run in the message box (BACKLOG L14).
 *  Every change reloads both, so the "/" menu follows the Skills page. */
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

  /** Run a change on the server, then reload (errors reach the caller, which shows them). */
  const changed = useCallback(
    async (action: () => Promise<unknown>) => {
      await action();
      await load();
    },
    [load],
  );

  const save = useCallback(
    (id: number | null, form: SkillInput, team: boolean) =>
      changed(() => (id === null ? createSkill(form, team) : updateSkill(id, form))),
    [changed],
  );
  const setEnabled = useCallback((id: number, enabled: boolean) => changed(() => updateSkill(id, { enabled })), [changed]);
  const remove = useCallback((id: number) => changed(() => deleteSkill(id)), [changed]);
  const upload = useCallback((text: string, team: boolean) => changed(() => uploadSkill(text, team)), [changed]);
  const add = useCallback((name: string, team: boolean) => changed(() => addCatalogSkill(name, team)), [changed]);

  return { skills, usable, canManageTeam, load, clear, save, setEnabled, remove, upload, add };
}

export type SkillsApi = ReturnType<typeof useSkills>;
