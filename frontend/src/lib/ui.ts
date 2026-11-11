/** Tailwind class sets shared by many components: buttons, icon buttons, message actions, the brand mark and the
 *  diagram card. One definition each, so every button looks the same. Callers add only layout classes
 *  (shrink-0, self-start, margins); colour, border, padding and size come from here. The colour names
 *  (text-muted, bg-panel …) are the theme tokens mapped in app/globals.css. */

const join = (...parts: Array<string | false>) => parts.filter(Boolean).join(" ");

type BtnMod = "primary" | "ghost" | "danger" | "sm" | "full";

/** A text button. `primary`: copper; `ghost`: no fill; `danger`: red text and border; `sm`: compact; `full`: full width. */
export function btn(...mods: BtnMod[]): string {
  const has = (m: BtnMod) => mods.includes(m);
  const look = has("primary")
    ? "border-[color-mix(in_srgb,var(--copper)_50%,black)] bg-[linear-gradient(180deg,var(--copper)_0%,var(--copper-deep)_100%)] font-semibold text-on-copper"
    : join(
        has("danger") ? "border-[color-mix(in_srgb,var(--danger)_45%,var(--line))] text-danger-text" : "border-line text-text",
        has("ghost") ? "bg-transparent" : "bg-panel-2",
        "font-medium",
      );
  return join(
    "cursor-pointer rounded-[0.65rem] border disabled:cursor-not-allowed disabled:opacity-50",
    look,
    has("sm") ? "px-[0.55rem] py-[0.3rem] text-[0.78rem]" : "px-[0.9rem] py-[0.55rem]",
    has("full") && "w-full",
  );
}

type IconBtnMod = "on" | "danger" | "overlay";

/** A square icon button (✕, ⋯). `on`: copper while its menu is open; `danger`: red on hover;
 *  `overlay`: small dark button on top of a picture. */
export function iconBtn(...mods: IconBtnMod[]): string {
  const has = (m: IconBtnMod) => mods.includes(m);
  return join(
    "shrink-0 cursor-pointer rounded-[0.4rem] border-0 text-[1.05rem] leading-none",
    has("overlay") ? "h-[1.35rem] w-[1.35rem] bg-black/55 text-white" : "h-[1.7rem] w-[1.7rem] bg-transparent",
    !has("overlay") && (has("on") ? "text-copper" : "text-muted"),
    has("danger") ? "hover:bg-danger-soft hover:text-danger-text" : "hover:bg-hover",
    !has("danger") && !has("on") && "hover:text-text",
  );
}

type MsgActionMod = "accent" | "teal";

/** A small action under a message (Edit, Retry, Store). `accent`: the copper one that confirms; `teal`: a highlighted one. */
export function msgAction(mod?: MsgActionMod): string {
  const look =
    mod === "accent"
      ? "border-copper bg-[color-mix(in_srgb,var(--copper)_16%,var(--panel-2))] text-text"
      : join(mod === "teal" ? "border-teal text-text" : "border-line text-muted enabled:hover:text-text",
          "bg-transparent enabled:hover:bg-hover");
  return join(
    "cursor-pointer rounded-[0.4rem] border px-[0.45rem] py-[0.12rem] text-[0.72rem] disabled:cursor-not-allowed disabled:opacity-45",
    look,
  );
}

/** The copper square logo. `sm` in the sidebar. The ::after draws the chevron. */
export function brandMark(size?: "sm"): string {
  return join(
    size === "sm" ? "h-[1.9rem] w-[1.9rem] rounded-[0.45rem]" : "h-[2.4rem] w-[2.4rem] rounded-[0.55rem]",
    "relative bg-[linear-gradient(145deg,var(--copper)_0%,var(--copper-deep)_55%,#6b3d12_100%)] shadow-[inset_0_0_0_1px_rgba(255,255,255,0.15)]",
    "after:absolute after:inset-[0.55rem] after:rotate-45 after:rounded-[0.25rem] after:border-2 after:border-[rgba(18,21,26,0.55)] after:border-t-transparent after:border-l-transparent",
  );
}

/** A code block in an answer. Also used for a diagram's source code. */
export const mdPre =
  "mt-2 mb-[0.8rem] overflow-auto rounded-[0.55rem] border border-line bg-code-bg px-[0.9rem] py-3 text-[0.8rem] leading-[1.55]";

/** Diagrams (Mermaid, network) sit on a light "paper" card in both themes, so they also export cleanly:
 *  these colours are fixed on purpose, not theme tokens. */
export const diagram = {
  card: "mt-[0.4rem] mb-[0.6rem] rounded-[10px] border border-line bg-[#f7f5f0] p-3 text-[#1d232c]",
  actions:
    "mt-[0.6rem] flex flex-wrap gap-[0.4rem] border-t border-[#ddd8cc] pt-2 [&_button]:border-[#c9c2b3] [&_button]:text-[#1d232c]",
  loading: "p-[0.6rem] text-[0.85rem]",
  error: "p-[0.6rem] text-[0.85rem] text-[#9b2c1f]",
  code: `${mdPre} text-text`,
};
