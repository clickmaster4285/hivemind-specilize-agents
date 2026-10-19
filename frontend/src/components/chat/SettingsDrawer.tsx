"use client";

import ServersSection from "@/components/chat/ServersSection";
import type { ServersApi } from "@/hooks/useServers";
import type { SpecialistsApi } from "@/hooks/useSpecialists";
import type { Specialist } from "@/lib/types";

type Props = {
  isAdmin: boolean;
  specialists: SpecialistsApi;
  servers: ServersApi;
  allowOverflow: boolean;
  onAllowOverflow: (on: boolean) => void;
  onSyncModels: () => void;
  onNewSpecialist: () => void;
  onEditSpecialist: (s: Specialist) => void;
  onClose: () => void;
};

/** Settings: specialists, the session's servers (admins), models. */
export default function SettingsDrawer({
  isAdmin,
  specialists,
  servers,
  allowOverflow,
  onAllowOverflow,
  onSyncModels,
  onNewSpecialist,
  onEditSpecialist,
  onClose,
}: Props) {
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="settings-drawer" onClick={(e) => e.stopPropagation()}>
        <header>
          <h3>Settings</h3>
          <button type="button" className="icon-btn" onClick={onClose}>
            ×
          </button>
        </header>

        <section>
          <div className="rail-section-head">
            <h4>Specialists</h4>
            {isAdmin ? (
              <button type="button" className="btn ghost sm" onClick={onNewSpecialist}>
                New specialist
              </button>
            ) : null}
          </div>
          <p className="hint">
            With &ldquo;Specialist: auto&rdquo; the assistant reads each specialist&rsquo;s &ldquo;covers&rdquo; line and
            picks one per message (or none for small talk and general questions).
          </p>
          <ul className="settings-agents">
            {specialists.specialists.map((s) => (
              <li key={s.id}>
                <span className="specialist-row">
                  <span className="specialist-dot" style={{ background: s.accent }} />
                  <span>
                    <strong>
                      {s.name}
                      {s.enabled ? "" : " (off)"}
                    </strong>
                    <small>{s.covers}</small>
                  </span>
                </span>
                <button type="button" className="btn ghost sm" onClick={() => onEditSpecialist(s)}>
                  {isAdmin ? "Edit" : "View"}
                </button>
              </li>
            ))}
          </ul>
          {isAdmin ? (
            <button
              type="button"
              className="btn ghost full"
              onClick={() => {
                if (confirm("Restore the default IT, Development and Marketing specialists?")) void specialists.reset();
              }}
            >
              Restore defaults
            </button>
          ) : null}
        </section>

        {isAdmin ? <ServersSection servers={servers} /> : null}

        <section>
          <div className="rail-section-head">
            <h4>Models</h4>
            <button type="button" className="btn ghost sm" onClick={onSyncModels}>
              Sync
            </button>
          </div>
          <label className="check">
            <input type="checkbox" checked={allowOverflow} onChange={(e) => onAllowOverflow(e.target.checked)} />
            Cloud overflow when queue waits
          </label>
          <p className="hint">Default route uses fast/strong/cloud roles. Override from the top bar anytime.</p>
        </section>
      </div>
    </div>
  );
}
