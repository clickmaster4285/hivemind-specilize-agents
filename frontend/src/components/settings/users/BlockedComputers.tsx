"use client";

import { useEffect, useState } from "react";

import { listLocks, unlockAddress } from "@/lib/api";
import type { Locks } from "@/lib/types";
import { btn } from "@/lib/ui";

/** Settings → Users: computers blocked after too many failed passwords, with Unlock (BACKLOG L16 S2b).
 *  Shown only while there is one. */
export default function BlockedComputers() {
  const [blocked, setBlocked] = useState<Locks["addresses"]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listLocks()
      .then((locks) => !cancelled && setBlocked(locks.addresses))
      .catch((err) => !cancelled && setError(err instanceof Error ? err.message : String(err)));
    return () => {
      cancelled = true;
    };
  }, []);

  async function unblock(address: string) {
    setError(null);
    try {
      await unlockAddress(address);
      setBlocked((await listLocks()).addresses);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  if (!blocked.length && !error) return null;
  return (
    <section className="flex flex-col gap-2 rounded-lg border border-danger bg-panel-2 p-3">
      <h5 className="m-0 text-sm font-semibold">Blocked computers</h5>
      <ul className="flex flex-col gap-1 text-xs">
        {blocked.map((b) => (
          <li key={b.address} className="flex items-center justify-between gap-2">
            <span className="min-w-0 truncate text-muted">
              {b.address} · {b.failures} failed passwords · until {new Date(b.until).toLocaleTimeString()}
            </span>
            <button type="button" className={`${btn("ghost", "sm")} shrink-0`} onClick={() => void unblock(b.address)}>
              Unlock
            </button>
          </li>
        ))}
      </ul>
      {error ? <p className="m-0 text-xs text-danger">{error}</p> : null}
    </section>
  );
}
