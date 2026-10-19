"use client";

import type { ModelInfo, RunMode, Specialist } from "@/lib/types";

type Props = {
  title: string;
  subtitle: string;
  runMode: RunMode;
  onRunMode: (mode: RunMode) => void;
  specialists: Specialist[];
  specialistChoice: string;
  onSpecialistChoice: (choice: string) => void;
  models: ModelInfo[];
  modelOverride: string;
  onModelOverride: (model: string) => void;
  canStop: boolean;
  onStop: () => void;
};

/** Chat title, run mode, specialist and model pickers, Stop. */
export default function ChatHeader({
  title,
  subtitle,
  runMode,
  onRunMode,
  specialists,
  specialistChoice,
  onSpecialistChoice,
  models,
  modelOverride,
  onModelOverride,
  canStop,
  onStop,
}: Props) {
  return (
    <header className="chat-top">
      <div>
        <h2>{title}</h2>
        <p>{subtitle}</p>
      </div>
      <div className="chat-controls">
        <select value={runMode} onChange={(e) => onRunMode(e.target.value as RunMode)}>
          <option value="auto">Auto</option>
          <option value="chat">Chat</option>
          <option value="agentic">Agentic</option>
        </select>
        <select
          value={specialistChoice}
          onChange={(e) => onSpecialistChoice(e.target.value)}
          title="Who answers: Auto lets the assistant pick a specialist for each message"
        >
          <option value="auto">Specialist: auto</option>
          {specialists
            .filter((s) => s.enabled)
            .map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          <option value="none">No specialist</option>
        </select>
        <select value={modelOverride} onChange={(e) => onModelOverride(e.target.value)}>
          <option value="">Auto model route</option>
          {models
            .filter((m) => m.enabled && m.available !== false)
            .map((m) => (
              <option key={m.name} value={m.name}>
                {m.image_gen ? `${m.name} · image gen` : m.vision ? `${m.name} · vision` : m.name}
              </option>
            ))}
        </select>
        {canStop ? (
          <button type="button" className="btn danger ghost" onClick={onStop}>
            Stop
          </button>
        ) : null}
      </div>
    </header>
  );
}
