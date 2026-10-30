import { useCallback, useEffect, useState } from "react";

import { createUser, listUsers } from "@/lib/api";
import type { ManagedUser, UserCreateInput } from "@/lib/types";

/**
 * Local accounts, for the admin Users panel. `active` = load the list (Settings is open and the
 * user is an admin). The list never includes password hashes. `create` adds a user and reloads.
 */
export function useUsers(active: boolean) {
  const [users, setUsers] = useState<ManagedUser[]>([]);

  const load = useCallback(async () => {
    try {
      setUsers(await listUsers());
    } catch {
      setUsers([]);
    }
  }, []);

  // the list loads when Settings opens; a reply arriving after it closed is ignored
  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    listUsers()
      .then((list) => !cancelled && setUsers(list))
      .catch(() => !cancelled && setUsers([]));
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

  return { users, create };
}

export type UsersApi = ReturnType<typeof useUsers>;
