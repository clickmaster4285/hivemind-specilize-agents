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

import { IconSend, IconStop } from "@/components/chat/Icons";
import { useAutoResize } from "@/hooks/useAutoResize";
import type { SendOptions } from "@/hooks/useChat";
import type { PendingImagesApi } from "@/hooks/usePendingImages";
import type { ImageProfile } from "@/lib/api";
import type { EditStrength } from "@/lib/chat";
import type { Health, RunMode } from "@/lib/types";

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
  wantOptions: boolean;
  onToggleOptions: () => void;
  runMode: RunMode;
  specialistLabel: string;
  onOpenSettings: () => void;
  imageProfile: ImageProfile;
  onImageProfile: (profile: ImageProfile) => void;
  editStrength: EditStrength;
  onEditStrength: (strength: EditStrength) => void;
  imagePipeline: Health["image_pipeline"];
};

const iconBtn = "shrink-0 grid place-items-center w-[2.1rem] h-[2.1rem] border-0 rounded-full cursor-pointer";
const composerIcon = `${iconBtn} bg-transparent text-[var(--muted)] hover:bg-white/[0.06] hover:text-[var(--text)]`;
const sendBtn = `${iconBtn} bg-[linear-gradient(180deg,var(--copper)_0%,var(--copper-deep)_100%)] text-[#1a1208] enabled:hover:brightness-[1.06] disabled:opacity-45 disabled:cursor-not-allowed`;
const stopBtn = `${iconBtn} bg-[var(--danger)] text-white`;
const menuSelect =
  "w-auto min-w-0 m-0 px-[0.35rem] py-[0.2rem] text-[0.72rem] rounded-[0.4rem] border border-[var(--line)] bg-[var(--panel-2)] text-[var(--text)]";

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
      className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-[0.85rem] text-[var(--text)] hover:bg-white/[0.06] disabled:cursor-not-allowed disabled:opacity-40"
    >
      <span className="w-5 text-center">{icon}</span>
      <span className="flex-1">{label}</span>
    </button>
  );
}

/** The message box: a "+" menu (attach / generate / web / document / image ops), the text, and Send/Stop. */
export default function Composer(props: Props) {
  const { query, onQuery, inputRef, images, editing, onCancelEdit, running, canStop, onStop, onSend } = props;
  const { deep, onToggleDeep, wantOptions, onToggleOptions } = props;
  const toggleCls = (on: boolean) =>
    `shrink-0 flex h-[2.1rem] items-center gap-1 rounded-full border px-[0.6rem] text-[0.75rem] cursor-pointer ${
      on
        ? "border-[var(--teal)] bg-[color-mix(in_srgb,var(--teal)_18%,transparent)] text-[var(--text)]"
        : "border-[var(--line)] bg-transparent text-[var(--muted)] hover:text-[var(--text)]"
    }`;
  const { imageProfile, onImageProfile, editStrength, onEditStrength, imagePipeline } = props;
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const resize = useAutoResize(inputRef, query);
  const [menuOpen, setMenuOpen] = useState(false);

  const hasImages = images.images.length > 0;
  const hasText = Boolean(query.trim());

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    onSend();
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
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
      className="mx-auto w-full max-w-[820px] shrink-0 border-t border-[var(--line)] bg-[rgba(14,17,22,0.92)] px-4 pt-[0.55rem] pb-[0.7rem]"
      onSubmit={onSubmit}
      onDragOver={(e) => e.preventDefault()}
      onDrop={onDrop}
    >
      {editing ? (
        <div className="mb-[0.4rem] flex items-center justify-between gap-2 px-[0.15rem] py-[0.25rem] text-[0.78rem] text-[var(--muted)]">
          <span>Editing previous message</span>
          <button type="button" className="msg-action" onClick={onCancelEdit}>
            Cancel
          </button>
        </div>
      ) : null}
      {hasImages ? (
        <div className="mb-[0.45rem] flex flex-wrap items-start gap-[0.4rem]">
          {images.images.map((img) => (
            <div
              key={img.id}
              className="relative block h-[4.4rem] w-[4.4rem] overflow-hidden rounded-[0.45rem] border border-[var(--line)] bg-black/25"
            >
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={img.url} alt={img.file.name} className="block h-full w-full object-cover" />
              <button
                type="button"
                className="icon-btn danger absolute right-[0.1rem] top-[0.1rem] h-[1.35rem] w-[1.35rem] bg-black/55 text-white"
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
      <div className="flex items-end gap-[0.15rem] rounded-[1.15rem] border border-[var(--line)] bg-[var(--panel-2)] py-[0.2rem] pl-[0.15rem] pr-[0.25rem]">
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
              <div className="absolute bottom-full left-0 z-50 mb-2 w-[16rem] rounded-xl border border-[var(--line)] bg-[var(--panel)] p-1.5 shadow-[var(--shadow)]">
                <MenuItem
                  icon="📎"
                  label="Attach image"
                  onClick={() => {
                    setMenuOpen(false);
                    fileInputRef.current?.click();
                  }}
                />
                <div className="my-1 border-t border-[var(--line)]" />
                <MenuItem icon="🖼️" label="Generate image" onClick={() => act({ forceIntent: "generate" })} />
                <MenuItem icon="🌐" label="Web search" disabled={!hasText} onClick={() => act({ forceIntent: "web_search" })} />
                <MenuItem icon="📄" label="Create document" disabled={!hasText} onClick={() => act({ forceIntent: "doc_gen" })} />
                {hasImages ? (
                  <>
                    <div className="my-1 border-t border-[var(--line)]" />
                    <MenuItem icon="👁️" label="Describe image" onClick={() => act({ forceIntent: "describe" })} />
                    <MenuItem icon="✏️" label="Edit image" onClick={() => act({ forceIntent: "edit" })} />
                    <MenuItem icon="🩹" label="Inpaint" onClick={() => act({ forceIntent: "inpaint" })} />
                  </>
                ) : null}
                <div className="my-1 border-t border-[var(--line)]" />
                <div className="flex items-center justify-between gap-2 px-2.5 py-1.5 text-[0.72rem] text-[var(--muted)]">
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
                <div className="flex items-center justify-between gap-2 px-2.5 py-1.5 text-[0.72rem] text-[var(--muted)]">
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
                  <p className="px-2.5 pb-1 pt-0.5 text-[0.68rem] capitalize text-[var(--muted)]">
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
          className="m-0 min-h-[2.2rem] max-h-[16rem] flex-1 resize-none border-0 bg-transparent px-[0.35rem] py-2 text-[0.9rem] leading-[1.35] font-[inherit] text-[var(--text)] shadow-none outline-none overflow-y-auto placeholder:text-[var(--muted)]"
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
        <button
          type="button"
          onClick={onToggleOptions}
          aria-pressed={wantOptions}
          title="Options — offer a few ways to answer this, then you pick one"
          className={toggleCls(wantOptions)}
        >
          <span aria-hidden>⚖️</span> Options
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
    </form>
  );
}
