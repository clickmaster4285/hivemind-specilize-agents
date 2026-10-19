import { useCallback, useEffect, useState } from "react";

import { addServer, forgetServers, listServers, removeServer } from "@/lib/api";
import type { ServerInfo, ServerLoginInput } from "@/lib/types";

/**
 * Linux server logins kept in the API's memory for this session only (src/server_vault.py).
 * This hook never holds a password: the API returns only label, address, user and host key.
 * `active` = load the list (Settings is open and the user is an admin).
 */
export function useServers(active: boolean) {
  const [servers, setServers] = useState<ServerInfo[]>([]);

  const load = useCallback(async () => {
    try {
      setServers(await listServers());
    } catch {
      setServers([]);
    }
  }, []);

  // the list loads when Settings opens; a reply arriving after it closed is ignored
  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    listServers()
      .then((list) => !cancelled && setServers(list))
      .catch(() => !cancelled && setServers([]));
    return () => {
      cancelled = true;
    };
  }, [active]);

  const add = useCallback(
    async (payload: ServerLoginInput) => {
      await addServer(payload);
      await load();
    },
    [load],
  );

  const remove = useCallback(
    async (id: string) => {
      await removeServer(id).catch(() => undefined);
      await load();
    },
    [load],
  );

  /** Logout: the API forgets them; call before the login token is dropped. */
  const forget = useCallback(() => {
    void forgetServers().catch(() => undefined);
    setServers([]);
  }, []);

  return { servers, add, remove, forget };
}

export type ServersApi = ReturnType<typeof useServers>;
