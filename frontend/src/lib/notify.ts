/** Tell someone on another tab that their answer is ready (BACKLOG L30 T3): with a queue of 10 people an answer can
 *  take minutes, and they switch away. The tab title shows it until they look; a browser notification too, when they
 *  already allowed notifications (asked when a schedule is saved — never asked here, unprompted). */

const READY_MARK = "✓ ";

export function announceIfAway(title: string, body: string): void {
  if (typeof document === "undefined" || !document.hidden) return;
  if (!document.title.startsWith(READY_MARK)) {
    const plain = document.title;
    document.title = READY_MARK + plain;
    const back = () => {
      if (document.hidden) return;
      document.title = plain;
      document.removeEventListener("visibilitychange", back);
    };
    document.addEventListener("visibilitychange", back);
  }
  if (typeof Notification === "undefined" || Notification.permission !== "granted") return;
  const note = new Notification(title, { body, tag: "multeagent-answer" });
  note.onclick = () => window.focus();
}
