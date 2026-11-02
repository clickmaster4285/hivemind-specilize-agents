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
