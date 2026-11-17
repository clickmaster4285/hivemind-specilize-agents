/** The /library groups (BACKLOG L24 U5): each file `kind` the server records (src/tools/artifacts.py writers,
 *  "generated" from image_gen) belongs to one group a person filters by. A kind not listed here is "Other". */
import { fetchJobAttachment, type LibraryFile } from "@/lib/api";
import { saveUrl } from "@/lib/download";

export const LIBRARY_GROUPS = ["Documents", "Spreadsheets", "Slides", "Pictures", "Other"] as const;
export type LibraryGroup = (typeof LIBRARY_GROUPS)[number];

const GROUP_OF_KIND: Record<string, LibraryGroup> = {
  document: "Documents",
  docx: "Documents",
  pdf: "Documents",
  html: "Documents",
  xlsx: "Spreadsheets",
  pptx: "Slides",
  generated: "Pictures",
};

export function libraryGroup(file: LibraryFile): LibraryGroup {
  return GROUP_OF_KIND[file.kind] || "Other";
}

/** Download one library file through its job's attachment route (which checks ownership). */
export async function downloadLibraryFile(file: LibraryFile): Promise<void> {
  saveUrl(await fetchJobAttachment(file.job_id, file.filename), file.filename);
}
