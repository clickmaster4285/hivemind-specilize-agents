"use client";

import { useRef, type ClipboardEvent, type DragEvent, type FormEvent, type KeyboardEvent, type RefObject } from "react";

import { IconPaperclip, IconSend, IconStop } from "@/components/chat/Icons";
import { useAutoResize } from "@/hooks/useAutoResize";
import type { SendOptions } from "@/hooks/useChat";
import type { PendingImagesApi } from "@/hooks/usePendingImages";
import type { ImageForceIntent, ImageProfile } from "@/lib/api";
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
  runMode: RunMode;
  specialistLabel: string;
  onOpenSettings: () => void;
  imageProfile: ImageProfile;
  onImageProfile: (profile: ImageProfile) => void;
  editStrength: EditStrength;
  onEditStrength: (strength: EditStrength) => void;
  imagePipeline: Health["image_pipeline"];
};

// Inline Tailwind building blocks (shared classes — msg-action, icon-btn, btn — stay global utilities).
const iconBtn = "shrink-0 grid place-items-center w-[2.1rem] h-[2.1rem] border-0 rounded-full cursor-pointer";
const composerIcon = `${iconBtn} bg-transparent text-[var(--muted)] hover:bg-white/[0.06] hover:text-[var(--text)]`;
const sendBtn = `${iconBtn} bg-[linear-gradient(180deg,var(--copper)_0%,var(--copper-deep)_100%)] text-[#1a1208] enabled:hover:brightness-[1.06] disabled:opacity-45 disabled:cursor-not-allowed`;
const stopBtn = `${iconBtn} bg-[var(--danger)] text-white`;
const preset = "inline-flex items-center gap-[0.3rem] text-[0.72rem] text-[var(--muted)]";
const presetSelect =
  "w-auto min-w-0 m-0 px-[0.35rem] py-[0.2rem] text-[0.72rem] rounded-[0.4rem] border border-[var(--line)] bg-[var(--panel-2)] text-[var(--text)]";

const imageFiles = (files: FileList | null | undefined) => [...(files || [])].filter((f) => f.type.startsWith("image/"));

/** The message box: text, attached images, Send / Stop, and the image / tool buttons below it. */
export default function Composer(props: Props) {
  const { query, onQuery, inputRef, images, editing, onCancelEdit, running, canStop, onStop, onSend } = props;
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const resize = useAutoResize(inputRef, query);

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

  const hasImages = images.images.length > 0;
  const placeholder = hasImages
    ? "Ask about the image… (optional)"
    : props.runMode === "agentic"
      ? "Describe a goal for the agentic loop…"
      : "Message MulteAgent — paste or attach an image";

  return (
    <form
      className="mx-auto w-full max-w-[860px] shrink-0 border-t border-[var(--line)] bg-[rgba(14,17,22,0.92)] px-4 pt-[0.55rem] pb-[0.7rem]"
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
        <button
          type="button"
          className={composerIcon}
          title="Attach image"
          aria-label="Attach image"
          onClick={() => fileInputRef.current?.click()}
        >
          <IconPaperclip />
        </button>
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
      <ToolButtons {...props} hasImages={hasImages} />
    </form>
  );
}

/** One-click intents under the message box: image profile / strength, Describe, Generate, … */
function ToolButtons({
  query,
  running,
  onSend,
  specialistLabel,
  onOpenSettings,
  imageProfile,
  onImageProfile,
  editStrength,
  onEditStrength,
  imagePipeline,
  hasImages,
}: Props & { hasImages: boolean }) {
  const hasText = Boolean(query.trim());
  const buttons: { intent: ImageForceIntent; label: string; title: string; disabled: boolean }[] = [
    { intent: "describe", label: "Describe", title: "Vision: describe the attached image (needs Ollama)", disabled: !hasImages },
    { intent: "generate", label: "Generate", title: "Force local SDXL text-to-image", disabled: false },
    { intent: "edit", label: "Edit", title: "Force img2img from the attached image", disabled: !hasImages },
    { intent: "inpaint", label: "Inpaint", title: "Inpaint: attach source + a mask_* image (white = repaint)", disabled: !hasImages },
    { intent: "web_search", label: "Web", title: "Search the web with our local SearXNG and answer with sources", disabled: !hasText },
    { intent: "doc_gen", label: "Document", title: "Create downloadable Word/PDF/PPT/HTML/Excel via agentic tools", disabled: !hasText },
  ];
  return (
    <div className="flex flex-wrap items-center justify-start gap-x-2 gap-y-[0.35rem] pt-[0.35rem]">
      <button type="button" className="btn ghost sm" onClick={onOpenSettings}>
        {specialistLabel}
      </button>
      <label className={preset}>
        Profile
        <select className={presetSelect} value={imageProfile} onChange={(e) => onImageProfile(e.target.value as ImageProfile)}>
          <option value="fast">Fast (~30 s)</option>
          <option value="balanced">Balanced, sharper (~1.5 min)</option>
          <option value="quality">Quality (~3 min)</option>
        </select>
      </label>
      <label className={preset}>
        Edit strength
        <select className={presetSelect} value={editStrength} onChange={(e) => onEditStrength(e.target.value as EditStrength)}>
          <option value="close">Keep close</option>
          <option value="medium">Medium</option>
          <option value="restyle">Restyle</option>
        </select>
      </label>
      {buttons.map((b) => (
        <button
          key={b.intent}
          type="button"
          className="btn ghost sm"
          disabled={running || b.disabled}
          title={b.title}
          onClick={() => onSend({ forceIntent: b.intent })}
        >
          {b.label}
        </button>
      ))}
      {imagePipeline?.state ? (
        <span className="ml-auto text-[0.7rem] capitalize text-[var(--muted)]" title={imagePipeline.error || ""}>
          Image model: {imagePipeline.state}
        </span>
      ) : null}
    </div>
  );
}
