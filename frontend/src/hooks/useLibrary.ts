"use client";

import { useEffect, useState } from "react";

import { listLibrary, type LibraryFile } from "@/lib/api";
import { errorText } from "@/lib/chat";

/** The caller's made files (GET /api/library), loaded once when the page or screen opens. */
export function useLibrary() {
  const [files, setFiles] = useState<LibraryFile[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    listLibrary()
      .then((rows) => {
        if (!cancelled) setFiles(rows);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(errorText(err));
      });
    return () => {
      cancelled = true;
    };
  }, []);
  return { files, error };
}
