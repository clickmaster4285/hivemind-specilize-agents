/** Pure helpers for the chat screen: no React, no state. */
import { fetchJobAttachment } from "@/lib/api";
import { newId } from "@/lib/id";
import type { ChatImage, FeedItem, Job } from "@/lib/types";

export const MAX_CHAT_IMAGES = 4;
export const MAX_IMAGE_BYTES = 5 * 1024 * 1024;
export const IMAGE_QUESTION = "What's in this image?";

/** An image attached in the composer, not sent yet. */
export type PendingImage = {
  id: string;
  file: File;
  url: string;
  mime: string;
};

export type EditStrength = "close" | "medium" | "restyle";

/** How far img2img may move from the source image. */
export function strengthValue(strength: EditStrength): number {
  if (strength === "close") return 0.28;
  if (strength === "restyle") return 0.55;
  return 0.4;
}

export type ConversationTurn = { role: "user" | "assistant"; content: string };

/** Prior user/assistant turns for follow-ups ("write that as a docx"). */
export function conversationFromFeed(items: FeedItem[], maxTurns = 10): ConversationTurn[] {
  const turns: ConversationTurn[] = [];
  for (const item of items) {
    if (item.pending) continue;
    if (item.kind !== "user" && item.kind !== "agent") continue;
    const content = (item.body || "").trim();
    if (!content || content === "Working…") continue;
    // Skip planner/tool scaffolding — keep substantive assistant replies
    if (item.kind === "agent") {
      const title = (item.title || "").toLowerCase();
      if (
        title.includes("planner") ||
        title.includes("critic") ||
        title.includes("worker step") ||
        title === "downloads"
      ) {
        continue;
      }
    }
    turns.push({
      role: item.kind === "user" ? "user" : "assistant",
      content: content.length > 6000 ? `${content.slice(0, 5999)}…` : content,
    });
  }
  return turns.slice(-maxTurns);
}

export function titleFromQuery(query: string): string {
  const t = query.trim().replace(/\s+/g, " ");
  return t.length > 42 ? `${t.slice(0, 42)}…` : t || "Untitled chat";
}

/** "3 h ago", "2 days ago" — how fresh a knowledge-base source is. */
export function timeAgo(iso: string): string {
  const minutes = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  if (minutes < 60) return `${minutes} min ago`;
  if (minutes < 48 * 60) return `${Math.round(minutes / 60)} h ago`;
  return `${Math.round(minutes / 1440)} days ago`;
}

export function toggleIn(list: string[], value: string, on: boolean): string[] {
  return on ? [...new Set([...list, value])] : list.filter((x) => x !== value);
}

export function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

export function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result || "");
      const comma = result.indexOf(",");
      resolve(comma >= 0 ? result.slice(comma + 1) : result);
    };
    reader.onerror = () => reject(new Error("Could not read image"));
    reader.readAsDataURL(file);
  });
}

/** Images of an earlier message back into the composer (Edit / Retry). */
export async function chatImagesToPending(images?: ChatImage[]): Promise<PendingImage[]> {
  if (!images?.length) return [];
  const out: PendingImage[] = [];
  for (const img of images) {
    try {
      const res = await fetch(img.url);
      if (!res.ok) continue;
      const blob = await res.blob();
      const file = new File([blob], img.filename || "image.png", {
        type: img.mime || blob.type || "image/png",
      });
      out.push({ id: newId(), file, url: URL.createObjectURL(file), mime: file.type });
    } catch {
      // skip images we cannot reload
    }
  }
  return out;
}

/** The images a user attached to a job (not the generated ones), for an opened chat. */
export async function attachmentsFromJob(job: Job): Promise<ChatImage[]> {
  const raw = job.payload?.attachments;
  if (!Array.isArray(raw) || raw.length === 0) return [];
  const out: ChatImage[] = [];
  for (const item of raw) {
    if (!item || typeof item !== "object") continue;
    const kind = String((item as { kind?: string }).kind || "user");
    if (kind === "generated") continue;
    const filename = String((item as { filename?: string }).filename || "");
    if (!filename) continue;
    const mime = String((item as { mime?: string }).mime || "");
    const isImage = mime.startsWith("image/") || /\.(png|jpe?g|webp|gif)$/i.test(filename);
    if (!isImage) continue;
    try {
      const url = await fetchJobAttachment(job.id, filename);
      out.push({ url, filename, mime: mime || "image/jpeg" });
    } catch {
      // skip missing files
    }
  }
  return out;
}
