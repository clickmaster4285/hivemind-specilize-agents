/** Save an object URL as a file in the browser, then free it. */
export function saveUrl(url: string, filename: string) {
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1500);
}

/** Save text (e.g. a Markdown summary) as a file. */
export function saveText(text: string, filename: string, mime = "text/markdown") {
  saveUrl(URL.createObjectURL(new Blob([text], { type: `${mime};charset=utf-8` })), filename);
}

/** One CSV cell (RFC 4180): quoted when it holds a comma, a quote or a line break; a quote inside is doubled.
 *  A cell starting with = + - @ is prefixed with ' so a spreadsheet never runs it as a formula (OWASP CSV injection). */
function csvCell(value: string | number | null | undefined): string {
  let text = value === null || value === undefined ? "" : String(value);
  if (/^[=+\-@]/.test(text)) text = `'${text}`;
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

/** Save rows as a CSV file (a header row first); opens in Excel, LibreOffice and Google Sheets. */
export function saveCsv(header: string[], rows: (string | number | null | undefined)[][], filename: string) {
  const lines = [header, ...rows].map((row) => row.map(csvCell).join(","));
  // the byte-order mark makes Excel read UTF-8 (names with accents, Urdu) instead of guessing the code page
  saveText(`﻿${lines.join("\r\n")}\r\n`, filename, "text/csv");
}
