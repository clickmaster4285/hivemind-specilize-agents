import { useCallback, useState } from "react";

import { MAX_CHAT_IMAGES, MAX_IMAGE_BYTES, type PendingImage } from "@/lib/chat";

/** Images attached in the composer (paste, drop, paperclip), with their object URLs managed. */
export function usePendingImages() {
  const [images, setImages] = useState<PendingImage[]>([]);

  const add = useCallback((files: File[]) => {
    const accepted: PendingImage[] = [];
    for (const file of files) {
      if (!file.type.startsWith("image/")) continue;
      if (file.size > MAX_IMAGE_BYTES) {
        alert(`“${file.name}” is over 5MB.`);
        continue;
      }
      accepted.push({
        id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
        file,
        url: URL.createObjectURL(file),
        mime: file.type || "image/png",
      });
    }
    if (accepted.length === 0) return;
    setImages((prev) => {
      const next = [...prev, ...accepted];
      if (next.length > MAX_CHAT_IMAGES) {
        next.slice(MAX_CHAT_IMAGES).forEach((img) => URL.revokeObjectURL(img.url));
        return next.slice(0, MAX_CHAT_IMAGES);
      }
      return next;
    });
  }, []);

  const remove = useCallback((id: string) => {
    setImages((prev) => {
      const hit = prev.find((img) => img.id === id);
      if (hit) URL.revokeObjectURL(hit.url);
      return prev.filter((img) => img.id !== id);
    });
  }, []);

  /** After sending: the URLs stay alive — the user's chat bubble still shows them. */
  const clear = useCallback(() => setImages([]), []);

  /** Cancel / replace: the URLs are no longer shown anywhere. */
  const discard = useCallback(() => {
    setImages((prev) => {
      prev.forEach((img) => URL.revokeObjectURL(img.url));
      return [];
    });
  }, []);

  return { images, add, remove, clear, discard, set: setImages };
}

export type PendingImagesApi = ReturnType<typeof usePendingImages>;
