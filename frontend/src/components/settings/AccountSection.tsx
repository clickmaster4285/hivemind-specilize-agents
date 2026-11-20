"use client";

import { useState, type FormEvent } from "react";

import { useApp } from "@/components/app/AppProvider";
import PasswordForm from "@/components/auth/PasswordForm";
import SessionsPanel from "@/components/settings/SessionsPanel";
import { changePassword, updateMe } from "@/lib/api";
import { errorText } from "@/lib/chat";
import { btn, formFieldCompact } from "@/lib/ui";

const heading = "m-0 text-sm font-semibold";
const label = "flex flex-col gap-1 text-xs text-muted";
const box = "flex flex-col gap-3 rounded-lg border border-line bg-panel-2 p-4";

/** Settings → My account (BACKLOG L30 T2), for everyone: my email and phone, my password, sign out everywhere. */
export default function AccountSection() {
  const app = useApp();
  const user = app.auth.user;
  const [email, setEmail] = useState(user?.email ?? "");
  const [phone, setPhone] = useState(user?.phone ?? "");
  const [saved, setSaved] = useState<{ text: string; bad: boolean } | null>(null);
  const [passwordNote, setPasswordNote] = useState<string | null>(null);
  if (!user) return null;

  async function saveProfile(event: FormEvent) {
    event.preventDefault();
    setSaved(null);
    try {
      await updateMe({ email: email.trim(), phone: phone.trim() });
      setSaved({ text: "Saved.", bad: false });
    } catch (err) {
      setSaved({ text: errorText(err), bad: true });
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h4 className="m-0 text-base font-semibold [font-family:var(--font-display)]">My account</h4>
        <p className="m-0 mt-1 text-xs text-muted">
          Signed in as <strong className="text-text">{user.username}</strong> · {user.role}
          {user.team ? ` · ${user.team}` : ""}. Your name, role and team are set by an admin.
        </p>
      </div>

      <form className={box} onSubmit={saveProfile}>
        <h5 className={heading}>Contact</h5>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <label className={label}>
            Email
            <input className={formFieldCompact} type="email" autoComplete="email" value={email}
                   onChange={(e) => setEmail(e.target.value)} />
          </label>
          <label className={label}>
            Phone
            <input className={formFieldCompact} type="tel" autoComplete="tel" value={phone}
                   onChange={(e) => setPhone(e.target.value)} />
          </label>
        </div>
        <div className="flex items-center gap-3">
          <button type="submit" className={btn("primary", "sm")}>Save</button>
          {saved ? <span className={`text-xs ${saved.bad ? "text-danger" : "text-muted"}`}>{saved.text}</span> : null}
        </div>
      </form>

      <div className={box}>
        <h5 className={heading}>Password</h5>
        <p className="m-0 text-xs text-muted">Changing it signs out your other browsers and devices; this one stays.</p>
        <div className="max-w-sm">
          <PasswordForm needsCurrent submitLabel="Change password"
                        onSubmit={async (current, next) => {
                          const done = await changePassword(current, next);
                          setPasswordNote(`Password changed. ${done.sessions_ended} other sign-in${
                            done.sessions_ended === 1 ? "" : "s"} ended.`);
                        }} />
        </div>
        {passwordNote ? <p role="status" className="m-0 text-xs text-muted">{passwordNote}</p> : null}
      </div>

      <div className={box}>
        <h5 className={heading}>Sessions</h5>
        <p className="m-0 text-xs text-muted">
          Where your account is signed in. Sign out one you do not recognise — or all of them, if you signed in on a
          shared computer or think someone has your password (then change it too).
        </p>
        <SessionsPanel />
        <button type="button" className={`${btn("danger", "ghost", "sm")} self-start`}
                onClick={() => {
                  if (confirm("Sign out of every browser and device, this one too?")) {
                    void app.auth.signOut({ everywhere: true });
                  }
                }}>
          Sign out everywhere
        </button>
      </div>
    </div>
  );
}
