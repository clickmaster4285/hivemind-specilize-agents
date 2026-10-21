import type { CSSProperties } from "react";

type Props = {
  kind: "user" | "agent" | "system";
  title: string;
  accent?: string;
};

const base =
  "grid shrink-0 place-items-center rounded-full font-bold tracking-[0.02em] text-[#1a1208] shadow-[inset_0_0_0_1px_rgba(255,255,255,0.12)] bg-[linear-gradient(145deg,var(--copper)_0%,var(--copper-deep)_100%)]";
const byKind: Record<Props["kind"], string> = {
  user: "h-[1.45rem] w-[1.45rem] text-[0.58rem]",
  agent:
    "h-[1.45rem] w-[1.45rem] text-[0.58rem] text-[#0e1116] bg-[linear-gradient(145deg,color-mix(in_srgb,var(--accent,var(--teal))_85%,white),var(--accent,var(--teal)))]",
  system: "h-[1.1rem] w-[1.1rem] text-[0.55rem] text-[var(--muted)] shadow-none bg-white/[0.08]",
};

function initials(title: string, kind: Props["kind"]) {
  if (kind === "user") return "U";
  if (kind === "system") return "i";
  const parts = title.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "A";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return `${parts[0][0]}${parts[1][0]}`.toUpperCase();
}

export default function MessageAvatar({ kind, title, accent }: Props) {
  return (
    <div
      className={`${base} ${byKind[kind]}`}
      style={accent ? ({ ["--accent"]: accent } as CSSProperties) : undefined}
      aria-hidden
      title={title}
    >
      {initials(title, kind)}
    </div>
  );
}
