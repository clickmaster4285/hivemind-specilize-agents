"use client";

import { useParams, useRouter } from "next/navigation";

import { useApp } from "@/components/app/AppProvider";
import { PAGE_WIDTH } from "@/components/app/PageHeader";
import UserForm from "@/components/settings/users/UserForm";
import { useUsers } from "@/hooks/useUsers";
import { settingsTabPath } from "@/lib/routes";

const page = `mx-auto w-full ${PAGE_WIDTH} px-4 py-8`;
const panel = "rounded-xl border border-line bg-panel p-5";

/** /settings/users/<id> (BACKLOG L19b D5): one account's form as its own page; /settings/users/new creates one.
 *  Admins only; leaving it (save, cancel, back) returns to Settings → Users. */
export default function UserPage() {
  const app = useApp();
  const router = useRouter();
  const { id } = useParams<{ id: string }>();
  const isAdmin = app.auth.user?.role === "admin";
  const users = useUsers(isAdmin);
  const back = () => router.push(settingsTabPath("users"));

  if (!app.auth.user) return null; // the shell shows the login when signed out
  if (!isAdmin) return <p className={`${page} text-[0.9rem] text-muted`}>Only admins manage users.</p>;

  const creating = id === "new";
  const user = creating ? null : users.users.find((u) => String(u.id) === id);
  if (!creating && !users.loaded) return <p className={`${page} text-[0.9rem] text-muted`}>Loading…</p>;
  if (user === undefined) {
    return (
      <div className={page}>
        <p className="text-[0.9rem] text-muted">This user no longer exists.</p>
        <button type="button" className="mt-3 text-sm text-copper underline" onClick={back}>
          ← Users
        </button>
      </div>
    );
  }
  return (
    <div className={page}>
      <section className={panel}>
        {/* keyed by the account so going from one user to another starts a fresh form */}
        <UserForm key={id} users={users} user={user} onDone={back} />
      </section>
    </div>
  );
}
