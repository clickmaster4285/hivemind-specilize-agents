import { useCallback, useEffect, useState } from "react";

import { useLiveTopic } from "@/hooks/useLiveEvents";
import { createUser, deactivateUser, deleteUser, listUsers, unlockUser, updateUser } from "@/lib/api";
import type { ManagedUser, UserCreateInput, UserUpdateInput } from "@/lib/types";

/**
 * Local accounts, for the admin Users panel. `active` = load the list (Settings is open and the
 * user is an admin). The list never includes password hashes. `create` adds a user and reloads.
 */
export function useUsers(active: boolean) {
  const [users, setUsers] = useState<ManagedUser[]>([]);
  const [loaded, setLoaded] = useState(false);   // the first list arrived (a user's own page waits for it)

  const load = useCallback(async () => {
    try {
      setUsers(await listUsers());
    } catch {
      setUsers([]);
    }
  }, []);

  // another admin, a sign-up or a lock changed the list: reload it (live notice, BACKLOG L29)
  useLiveTopic(["users", "signup"], load, active);

  // the list loads when Settings opens; a reply arriving after it closed is ignored
  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    listUsers()
      .then((list) => !cancelled && setUsers(list))
      .catch(() => !cancelled && setUsers([]))
      .finally(() => !cancelled && setLoaded(true));
    return () => {
      cancelled = true;
    };
  }, [active]);

  const create = useCallback(
    async (payload: UserCreateInput) => {
      await createUser(payload);
      await load();
    },
    [load],
  );

  const update = useCallback(
    async (id: number, payload: UserUpdateInput) => {
      await updateUser(id, payload);
      await load();
    },
    [load],
  );

  const deactivate = useCallback(
    async (id: number) => {
      await deactivateUser(id);
      await load();
    },
    [load],
  );

  // end a lock after failed passwords at once (BACKLOG L16 S2b)
  const unlock = useCallback(
    async (id: number) => {
      await unlockUser(id);
      await load();
    },
    [load],
  );

  // for good: the account and everything theirs (Deactivate is the undoable one)
  const remove = useCallback(
    async (id: number, confirm: string) => {
      const done = await deleteUser(id, confirm);
      await load();
      return done;
    },
    [load],
  );

  return { users, loaded, create, update, deactivate, remove, unlock };
}

export type UsersApi = ReturnType<typeof useUsers>;
