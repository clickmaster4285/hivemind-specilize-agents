"use client";

import {
  useRef,
  useState,
  type ClipboardEvent,
  type DragEvent,
  type FormEvent,
  type KeyboardEvent,
  type ReactNode,
  type RefObject,
} from "react";

import { MemoryBanner, MemoryMeter, type MemoryBusy } from "@/components/chat/ChatMemory";
import { IconSend, IconStop } from "@/components/chat/Icons";
import SkillMenu, { useSkillMenu } from "@/components/chat/SkillMenu";
import { useAutoResize } from "@/hooks/useAutoResize";
import type { SendOptions } from "@/hooks/useChat";
import type { PendingImagesApi } from "@/hooks/usePendingImages";
import type { ImageProfile } from "@/lib/api";
import type { ChatMemory, EditStrength } from "@/lib/chat";
import { usageLine } from "@/lib/format";
import type { Health, RunMode, Skill, UsageTotals } from "@/lib/types";
import { iconBtn, msgAction } from "@/lib/ui";

type Props = {
  query: string;
  onQuery: (text: string) => void;
  inputRef: RefObject<HTMLTextAreaElement | null>;
  images: PendingImagesApi;
  editing: boolean;
  onCancelEdit: () => void;
  running: boolean;
  canStop: boolean;
  onStop: () => void;
  onSend: (opts?: SendOptions) => void;
  deep: boolean;
  onToggleDeep: () => void;
  runMode: RunMode;
  onRunMode: (mode: RunMode) => void;
  specialistLabel: string;
  /** Skills the user can run by typing /name (BACKLOG L14). */
  skills: Skill[];
  onOpenSettings: () => void;
  imageProfile: ImageProfile;
  onImageProfile: (profile: ImageProfile) => void;
  editStrength: EditStrength;
  onEditStrength: (strength: EditStrength) => void;
  imagePipeline: Health["image_pipeline"];
  /** What this chat has used so far (all its answers added up), shown under the message bar. */
  chatUsage?: UsageTotals | null;
  /** How full the model's memory of this chat is (BACKLOG L12), and the "getting long" banner's actions. */
  chatId?: string | null;
  memory?: ChatMemory | null;
  memoryBusy?: MemoryBusy;
  onContinueChat?: () => void;
  onSaveSummary?: () => void;
};

const roundBtn = "shrink-0 grid place-items-center w-[2.1rem] h-[2.1rem] border-0 rounded-full cursor-pointer";
const composerIcon = `${roundBtn} bg-transparent text-muted hover:bg-hover hover:text-text`;
const sendBtn = `${roundBtn} bg-[linear-gradient(180deg,var(--copper)_0%,var(--copper-deep)_100%)] text-on-copper enabled:hover:brightness-[1.06] disabled:opacity-45 disabled:cursor-not-allowed`;
const stopBtn = `${roundBtn} bg-danger text-white`;
const menuSelect =
  "w-auto min-w-0 m-0 px-[0.35rem] py-[0.2rem] text-[0.72rem] rounded-[0.4rem] border border-line bg-panel-2 text-text";

const RUN_MODES: { value: RunMode; label: string; title: string }[] = [
  { value: "auto", label: "Auto", title: "The assistant picks how to answer each message" },
  { value: "chat", label: "Chat", title: "A direct answer" },
  { value: "agentic", label: "Agentic", title: "A multi-step agent loop toward a goal" },
];

const imageFiles = (files: FileList | null | undefined) => [...(files || [])].filter((f) => f.type.startsWith("image/"));

function Plus() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path d="M12 5v14M5 12h14" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

function MenuItem({
  icon,
  label,
  onClick,
  disabled,
}: {
  icon: ReactNode;
  label: string;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-[0.85rem] text-text hover:bg-hover disabled:cursor-not-allowed disabled:opacity-40"
    >
      <span className="w-5 text-center">{icon}</span>
      <span className="flex-1">{label}</span>
    </button>
  );
}

/** The message box: a "+" menu (attach / generate / web / document / image ops), the text, and Send/Stop. */
export default function Composer(props: Props) {
  const { query, onQuery, inputRef, images, editing, onCancelEdit, running, canStop, onStop, onSend } = props;
  const { deep, onToggleDeep } = props;
  const toggleCls = (on: boolean) =>
    `shrink-0 flex h-[2.1rem] items-center gap-1 rounded-full border px-[0.6rem] text-[0.75rem] cursor-pointer ${
      on
        ? "border-teal bg-[color-mix(in_srgb,var(--teal)_18%,transparent)] text-text"
        : "border-line bg-transparent text-muted hover:text-text"
    }`;
  const { imageProfile, onImageProfile, editStrength, onEditStrength, imagePipeline } = props;
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const resize = useAutoResize(inputRef, query);
  const [menuOpen, setMenuOpen] = useState(false);
  const skillMenu = useSkillMenu(query, props.skills, (skill) => {
    onQuery(`/${skill.name} `);
    inputRef.current?.focus();
  });

  const hasImages = images.images.length > 0;
  const hasText = Boolean(query.trim());

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    onSend();
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (skillMenu.onKey(event)) return;
    if (event.key === "Escape" && editing) {
      event.preventDefault();
      onCancelEdit();
      return;
    }
    if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) return;
    event.preventDefault();
    onSend();
  }

  function onPaste(event: ClipboardEvent<HTMLTextAreaElement>) {
    const files = imageFiles(event.clipboardData?.files);
    if (files.length === 0) return;
    event.preventDefault();
    images.add(files);
  }

  function onDrop(event: DragEvent<HTMLFormElement>) {
    event.preventDefault();
    const files = imageFiles(event.dataTransfer?.files);
    if (files.length) images.add(files);
  }

  function act(opts: SendOptions) {
    setMenuOpen(false);
    onSend(opts);
  }

  const placeholder = hasImages
    ? "Ask about the image… (optional)"
    : props.runMode === "agentic"
      ? "Describe a goal for the agentic loop…"
      : "Message MulteAgent — paste or attach an image";

  return (
    <form
      className="mx-auto w-full max-w-[820px] shrink-0 bg-transparent px-4 pt-[0.55rem] pb-[0.7rem]"
      onSubmit={onSubmit}
      onDragOver={(e) => e.preventDefault()}
      onDrop={onDrop}
    >
      {props.memory && props.onContinueChat && props.onSaveSummary ? (
        <MemoryBanner
          chatId={props.chatId ?? null}
          memory={props.memory}
          busy={props.memoryBusy ?? null}
          onContinue={props.onContinueChat}
          onSave={props.onSaveSummary}
        />
      ) : null}
      {editing ? (
        <div className="mb-[0.4rem] flex items-center justify-between gap-2 px-[0.15rem] py-1 text-[0.78rem] text-muted">
          <span>Editing previous message</span>
          <button type="button" className={msgAction()} onClick={onCancelEdit}>
            Cancel
          </button>
        </div>
      ) : null}
      {hasImages ? (
        <div className="mb-[0.45rem] flex flex-wrap items-start gap-[0.4rem]">
          {images.images.map((img) => (
            <div
              key={img.id}
              className="relative block h-[4.4rem] w-[4.4rem] overflow-hidden rounded-[0.45rem] border border-line bg-sunken"
            >
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={img.url} alt={img.file.name} className="block h-full w-full object-cover" />
              <button
                type="button"
                className={`${iconBtn("danger", "overlay")} absolute right-[0.1rem] top-[0.1rem]`}
                title="Remove image"
                onClick={() => images.remove(img.id)}
              >
                ×
              </button>
            </div>
          ))}
        </div>
      ) : null}
      <input
        ref={fileInputRef}
        type="file"
        accept="image/png,image/jpeg,image/webp,image/gif"
        multiple
        hidden
        onChange={(e) => {
          images.add([...(e.target.files || [])]);
          e.target.value = "";
        }}
      />
      <div className="relative flex items-end gap-[0.15rem] rounded-[1.15rem] border border-line bg-panel-2 py-[0.2rem] pl-[0.15rem] pr-1">
        <SkillMenu menu={skillMenu} />
        <div className="relative">
          <button
            type="button"
            className={composerIcon}
            title="Attach and tools"
            aria-label="Attach and tools"
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen((o) => !o)}
          >
            <Plus />
          </button>
          {menuOpen ? (
            <>
              <button
                type="button"
                className="fixed inset-0 z-40 cursor-default"
                aria-hidden
                tabIndex={-1}
                onClick={() => setMenuOpen(false)}
              />
              <div className="absolute bottom-full left-0 z-50 mb-2 w-[16rem] rounded-xl border border-line bg-panel p-1.5 shadow-shadow">
                <div className="px-2.5 pb-1 pt-1 text-[0.7rem] font-medium uppercase tracking-[0.05em] text-muted">Mode</div>
                <div className="mx-1.5 mb-1 grid grid-cols-3 gap-[0.2rem] rounded-lg border border-line bg-panel-2 p-[0.2rem]" role="radiogroup" aria-label="Mode">
                  {RUN_MODES.map((m) => (
                    <button
                      key={m.value}
                      type="button"
                      role="radio"
                      aria-checked={props.runMode === m.value}
                      title={m.title}
                      onClick={() => props.onRunMode(m.value)}
                      className={`rounded-md px-1 py-[0.3rem] text-[0.78rem] ${
                        props.runMode === m.value
                          ? "bg-panel font-semibold text-text-strong shadow-[0_1px_2px_rgba(0,0,0,0.15)]"
                          : "text-muted hover:text-text"
                      }`}
                    >
                      {m.label}
                    </button>
                  ))}
                </div>
                <div className="my-1 border-t border-line" />
                <MenuItem
                  icon="📎"
                  label="Attach image"
                  onClick={() => {
                    setMenuOpen(false);
                    fileInputRef.current?.click();
                  }}
                />
                <MenuItem
                  icon="⚡"
                  label="Use a skill"
                  disabled={!props.skills.length}
                  onClick={() => {
                    setMenuOpen(false);
                    onQuery("/");
                    inputRef.current?.focus();
                  }}
                />
                <div className="my-1 border-t border-line" />
                <MenuItem icon="🖼️" label="Generate image" onClick={() => act({ forceIntent: "generate" })} />
                <MenuItem icon="🌐" label="Web search" disabled={!hasText} onClick={() => act({ forceIntent: "web_search" })} />
                <MenuItem icon="⚖️" label="Answer options" disabled={!hasText} onClick={() => act({ options: true })} />
                <MenuItem icon="📄" label="Create document" disabled={!hasText} onClick={() => act({ forceIntent: "doc_gen" })} />
                {hasImages ? (
                  <>
                    <div className="my-1 border-t border-line" />
                    <MenuItem icon="👁️" label="Describe image" onClick={() => act({ forceIntent: "describe" })} />
                    <MenuItem icon="✏️" label="Edit image" onClick={() => act({ forceIntent: "edit" })} />
                    <MenuItem icon="🩹" label="Inpaint" onClick={() => act({ forceIntent: "inpaint" })} />
                  </>
                ) : null}
                <div className="my-1 border-t border-line" />
                <div className="flex items-center justify-between gap-2 px-2.5 py-1.5 text-[0.72rem] text-muted">
                  <span>Image profile</span>
                  <select
                    className={menuSelect}
                    value={imageProfile}
                    onChange={(e) => onImageProfile(e.target.value as ImageProfile)}
                  >
                    <option value="fast">Fast (~30 s)</option>
                    <option value="balanced">Balanced (~1.5 min)</option>
                    <option value="quality">Quality (~3 min)</option>
                  </select>
                </div>
                <div className="flex items-center justify-between gap-2 px-2.5 py-1.5 text-[0.72rem] text-muted">
                  <span>Edit strength</span>
                  <select
                    className={menuSelect}
                    value={editStrength}
                    onChange={(e) => onEditStrength(e.target.value as EditStrength)}
                  >
                    <option value="close">Keep close</option>
                    <option value="medium">Medium</option>
                    <option value="restyle">Restyle</option>
                  </select>
                </div>
                {imagePipeline?.state ? (
                  <p className="px-2.5 pb-1 pt-0.5 text-[0.68rem] capitalize text-muted">
                    Image model: {imagePipeline.state}
                  </p>
                ) : null}
              </div>
            </>
          ) : null}
        </div>
        <textarea
          ref={inputRef}
          rows={1}
          value={query}
          onChange={(e) => onQuery(e.target.value)}
          onInput={resize}
          onPaste={onPaste}
          onKeyDown={onKeyDown}
          placeholder={placeholder}
          required={!hasImages}
          className="m-0 min-h-[2.2rem] max-h-[16rem] flex-1 resize-none border-0 bg-transparent px-[0.35rem] py-2 text-[0.9rem] leading-[1.35] font-[inherit] text-text shadow-none outline-none overflow-y-auto placeholder:text-muted"
        />
        <button
          type="button"
          onClick={onToggleDeep}
          aria-pressed={deep}
          title="Deep research — plan sub-queries and search many sources in depth (slower, ~1–2 min)"
          className={toggleCls(deep)}
        >
          <span aria-hidden>🔬</span> Deep
        </button>
        {canStop ? (
          // Send turns into Stop: ends the model mid-answer and keeps what was written
          <button type="button" className={stopBtn} title="Stop" aria-label="Stop" onClick={onStop}>
            <IconStop />
          </button>
        ) : (
          <button
            type="submit"
            className={sendBtn}
            disabled={running}
            title={running ? "Running…" : "Send (Enter)"}
            aria-label={running ? "Running" : "Send"}
          >
            <IconSend />
          </button>
        )}
      </div>
      {props.memory || props.chatUsage ? (
        <div className="mt-[0.3rem] flex flex-wrap items-center justify-center gap-x-3 gap-y-1 text-[0.7rem] tabular-nums text-muted">
          {props.memory ? <MemoryMeter memory={props.memory} /> : null}
          {props.chatUsage ? (
            <span className="cursor-default" title={`${props.chatUsage.calls} model calls in this chat`}>
              This chat: {usageLine(props.chatUsage)}
            </span>
          ) : null}
        </div>
      ) : null}
    </form>
  );
}
