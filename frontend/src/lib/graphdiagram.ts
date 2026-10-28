/* Generic network-TOPOLOGY renderer (pure, no React / DOM).
 *
 * The hub-and-spoke infographic (netdiagram.ts) covers "N sites → HQ". This renders any OTHER topology the
 * model describes as a graph: nodes placed in tiers (internet → edge → core → distribution → access …),
 * typed links between them (lan / wan / vpn / wireless / internet), and optional dashed group zones. Icons
 * are the same open vocabulary (deviceIcons.resolve via the injected `draw`), so any device shows as itself.
 *
 * A tiered layout is used (not a generic force/elk layout) because network diagrams are almost always
 * hierarchical and tiers keep the card aesthetic clean and predictable; it is deterministic and verifiable.
 */

import type { DrawIcon } from "./netdiagram";

type LinkKind = "lan" | "wan" | "vpn" | "wireless" | "internet";
export type GNode = { id: string; icon: string; label: string; sub?: string; tier?: number; group?: string };
export type GLink = { from: string; to: string; kind?: LinkKind; label?: string };
export type GGroup = { id: string; label: string; nodes: string[] };
export type GraphSpec = {
  title: string;
  subtitle: string;
  nodes: GNode[];
  links: GLink[];
  groups: GGroup[];
  legend: string[];
  features: string[];
  notes: string[];
};

const C = {
  bg: "#ffffff", ink: "#1f2937", sub: "#64748b", line: "#e2e8f0", card: "#f8fafc",
  group: "#cbd5e1", groupBg: "rgba(226,232,240,0.28)", icon: "#334155", accent: "#2563eb",
};
const LINK: Record<LinkKind, { stroke: string; dash: string; w: number }> = {
  internet: { stroke: "#64748b", dash: "0", w: 2.4 },
  wan: { stroke: "#2563eb", dash: "0", w: 2.4 },
  lan: { stroke: "#94a3b8", dash: "0", w: 2 },
  vpn: { stroke: "#16a34a", dash: "6 5", w: 2.4 },
  wireless: { stroke: "#0891b2", dash: "4 4", w: 2.2 },
};

function esc(s: string): string {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}
function clip(s: string, n: number): string {
  const t = String(s);
  return t.length > n ? t.slice(0, n - 1) + "…" : t;
}
function wrap(text: string, max: number): string[] {
  const words = String(text).split(/\s+/);
  const out: string[] = [];
  let cur = "";
  for (const w of words) {
    if (!cur) cur = w;
    else if ((cur + " " + w).length <= max) cur += " " + w;
    else { out.push(cur); cur = w; }
  }
  if (cur) out.push(cur);
  return out.slice(0, 2);
}

/** Assign a tier (row, 0 = top) to every node: use the given tier, else longest path from a source. */
function assignTiers(nodes: GNode[], links: GLink[]): Map<string, number> {
  const ids = new Set(nodes.map((n) => n.id));
  const tier = new Map<string, number>();
  nodes.forEach((n) => { if (typeof n.tier === "number" && isFinite(n.tier)) tier.set(n.id, Math.max(0, Math.floor(n.tier))); });
  if (tier.size === nodes.length) return tier;

  const out = new Map<string, string[]>();
  const indeg = new Map<string, number>();
  nodes.forEach((n) => { out.set(n.id, []); indeg.set(n.id, 0); });
  for (const l of links) {
    if (!ids.has(l.from) || !ids.has(l.to) || l.from === l.to) continue;
    out.get(l.from)!.push(l.to);
    indeg.set(l.to, (indeg.get(l.to) || 0) + 1);
  }
  const depth = new Map<string, number>();
  const seen = new Set<string>();
  const visit = (id: string, d: number): void => {
    if (seen.has(id)) { depth.set(id, Math.max(depth.get(id) || 0, d)); return; }
    seen.add(id);
    depth.set(id, Math.max(depth.get(id) || 0, d));
    for (const nx of out.get(id) || []) visit(nx, d + 1);
    seen.delete(id);
  };
  nodes.filter((n) => (indeg.get(n.id) || 0) === 0).forEach((n) => visit(n.id, 0));
  nodes.forEach((n) => { if (!depth.has(n.id)) visit(n.id, 0); });
  nodes.forEach((n) => { if (!tier.has(n.id)) tier.set(n.id, depth.get(n.id) || 0); });
  return tier;
}

const W = 1180, PAD = 32, NW = 150, NH = 82, TIER_GAP = 150, GROUP_PAD = 18;

/** Build the topology as an SVG string. Pure. `draw` renders device icons by free-text name. */
export function buildGraphSvg(spec: GraphSpec, draw: DrawIcon): string {
  const nodes = spec.nodes.slice(0, 40);
  const idSet = new Set(nodes.map((n) => n.id));
  const links = spec.links.filter((l) => idSet.has(l.from) && idSet.has(l.to) && l.from !== l.to).slice(0, 80);
  const tier = assignTiers(nodes, links);

  const byTier = new Map<number, GNode[]>();
  nodes.forEach((n) => {
    const t = tier.get(n.id) ?? 0;
    (byTier.get(t) ?? byTier.set(t, []).get(t)!).push(n);
  });
  const tiers = [...byTier.keys()].sort((a, b) => a - b);
  const tierIndex = new Map(tiers.map((t, i) => [t, i]));

  const top = 92;
  const pos = new Map<string, { x: number; y: number }>();
  tiers.forEach((t) => {
    const row = byTier.get(t)!;
    const n = row.length;
    const span = W - 2 * PAD;
    const step = span / n;
    row.forEach((node, i) => {
      pos.set(node.id, { x: PAD + step * (i + 0.5), y: top + (tierIndex.get(t)! * TIER_GAP) + NH / 2 });
    });
  });
  const rowsBottom = top + (tiers.length - 1) * TIER_GAP + NH;

  const parts: string[] = [];

  // ── group zones (behind everything): bounding box of member nodes
  for (const g of spec.groups) {
    const pts = g.nodes.map((id) => pos.get(id)).filter(Boolean) as { x: number; y: number }[];
    if (pts.length < 1) continue;
    const minX = Math.min(...pts.map((p) => p.x)) - NW / 2 - GROUP_PAD;
    const maxX = Math.max(...pts.map((p) => p.x)) + NW / 2 + GROUP_PAD;
    const minY = Math.min(...pts.map((p) => p.y)) - NH / 2 - GROUP_PAD - 6;
    const maxY = Math.max(...pts.map((p) => p.y)) + NH / 2 + GROUP_PAD;
    parts.push(`<rect x="${minX}" y="${minY}" width="${maxX - minX}" height="${maxY - minY}" rx="12" fill="${C.groupBg}" stroke="${C.group}" stroke-width="1.3" stroke-dasharray="6 5"/>`);
    parts.push(`<text x="${minX + 12}" y="${minY + 18}" font-size="12.5" font-weight="700" fill="${C.sub}">${esc(clip(g.label, 40))}</text>`);
  }

  // ── links (behind nodes): curved between tiers, with optional short label
  for (const l of links) {
    const a = pos.get(l.from)!;
    const b = pos.get(l.to)!;
    const st = LINK[l.kind as LinkKind] || LINK.lan;
    let d: string;
    if (a.y === b.y) {
      const my = a.y - 26 - Math.min(40, Math.abs(a.x - b.x) * 0.12);
      d = `M${a.x} ${a.y} C${a.x} ${my}, ${b.x} ${my}, ${b.x} ${b.y}`;
    } else {
      const ay = a.y < b.y ? a.y + NH / 2 - 4 : a.y - NH / 2 + 4;
      const by = a.y < b.y ? b.y - NH / 2 + 4 : b.y + NH / 2 - 4;
      const mid = (ay + by) / 2;
      d = `M${a.x} ${ay} C${a.x} ${mid}, ${b.x} ${mid}, ${b.x} ${by}`;
    }
    parts.push(`<path d="${d}" fill="none" stroke="${st.stroke}" stroke-width="${st.w}" stroke-dasharray="${st.dash}" opacity="0.9"/>`);
    if (l.kind === "vpn") {
      const mx = (a.x + b.x) / 2, mY = (a.y + b.y) / 2;
      parts.push(`<circle cx="${mx}" cy="${mY}" r="9" fill="#fff" stroke="${st.stroke}" stroke-width="1.5"/>`);
      parts.push(draw("vpn", mx - 6, mY - 6, 12, st.stroke));
    }
    if (l.label) {
      const mx = (a.x + b.x) / 2, mY = (a.y + b.y) / 2;
      const tw = l.label.length * 6 + 10;
      parts.push(`<rect x="${mx - tw / 2}" y="${mY - 9}" width="${tw}" height="16" rx="4" fill="#fff" opacity="0.9"/>`);
      parts.push(`<text x="${mx}" y="${mY + 3}" text-anchor="middle" font-size="10.5" fill="${C.sub}">${esc(clip(l.label, 18))}</text>`);
    }
  }

  // ── nodes
  for (const node of nodes) {
    const p = pos.get(node.id)!;
    const x = p.x - NW / 2, y = p.y - NH / 2;
    parts.push(`<rect x="${x}" y="${y}" width="${NW}" height="${NH}" rx="10" fill="${C.card}" stroke="${C.line}"/>`);
    parts.push(draw(node.icon || node.label, p.x - 17, y + 8, 34, /pc|user|laptop|phone|tablet|office|site/i.test(node.icon || node.label) ? C.accent : C.icon));
    const lines = wrap(node.label, 20);
    lines.forEach((ln, i) =>
      parts.push(`<text x="${p.x}" y="${y + 52 + i * 14}" text-anchor="middle" font-size="12.5" font-weight="600" fill="${C.ink}">${esc(ln)}</text>`),
    );
    if (node.sub && lines.length < 2)
      parts.push(`<text x="${p.x}" y="${y + 66}" text-anchor="middle" font-size="11" fill="${C.sub}">${esc(clip(node.sub, 22))}</text>`);
  }

  // ── header
  parts.unshift(
    `<text x="${PAD}" y="40" font-size="25" font-weight="800" fill="${C.ink}">${esc(clip(spec.title, 56))}</text>` +
      (spec.subtitle ? `<text x="${PAD}" y="64" font-size="14" fill="${C.sub}">${esc(clip(spec.subtitle, 95))}</text>` : ""),
  );

  // ── bottom panels (optional)
  const panelData = [
    { title: "Legend", items: spec.legend, kind: "legend" as const },
    { title: "Key Features", items: spec.features, kind: "check" as const },
    { title: "Notes", items: spec.notes, kind: "dot" as const },
  ].filter((p) => p.items.length);
  let H = rowsBottom + PAD;
  if (panelData.length) {
    const panelsTop = rowsBottom + 34;
    const panelW = (W - 2 * PAD - (panelData.length - 1) * 24) / panelData.length;
    const lineH = 20, WRAP = 42;
    const maxLines = Math.max(1, ...panelData.map((p) => p.items.reduce((n, it) => n + wrap(it, WRAP).length, 0)));
    const panelsH = 44 + maxLines * lineH + 12;
    panelData.forEach((p, pi) => {
      const px = PAD + pi * (panelW + 24);
      parts.push(`<rect x="${px}" y="${panelsTop}" width="${panelW}" height="${panelsH}" rx="10" fill="${C.card}" stroke="${C.line}"/>`);
      parts.push(`<text x="${px + 16}" y="${panelsTop + 26}" font-size="14" font-weight="700" fill="${C.ink}">${esc(p.title)}</text>`);
      let ly = panelsTop + 50;
      for (const it of p.items) {
        wrap(it, WRAP).forEach((ln, li) => {
          if (li === 0) {
            if (p.kind === "legend") parts.push(draw(it, px + 14, ly - 13, 17, /vpn|tunnel/i.test(it) ? "#16a34a" : /pc|user|office|site/i.test(it) ? C.accent : C.icon));
            else if (p.kind === "check") parts.push(`<path d="M${px + 16} ${ly - 4} l3 3 l5 -6" fill="none" stroke="#16a34a" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>`);
            else parts.push(`<circle cx="${px + 19}" cy="${ly - 4}" r="2.4" fill="${C.sub}"/>`);
          }
          parts.push(`<text x="${px + 38}" y="${ly}" font-size="12.5" fill="${C.ink}">${esc(ln)}</text>`);
          ly += lineH;
        });
      }
    });
    H = panelsTop + panelsH + PAD;
  }

  return (
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" width="100%" ` +
    `font-family="Arial, Helvetica, sans-serif" style="max-width:${W}px">` +
    `<rect x="0" y="0" width="${W}" height="${H}" fill="${C.bg}"/>` +
    parts.join("") +
    `</svg>`
  );
}

/** Every icon name a graph references (for lazy pack loading). */
export function graphIconNames(spec: GraphSpec): string[] {
  const names = new Set<string>(["vpn"]);
  spec.nodes.forEach((n) => names.add(n.icon || n.label));
  spec.legend.forEach((l) => names.add(l));
  return [...names];
}

/** True when a parsed object is a generic graph (nodes + links) rather than the hub-spoke (sites/hq) shape. */
export function isGraphSpec(o: unknown): boolean {
  if (!o || typeof o !== "object") return false;
  const r = o as Record<string, unknown>;
  return Array.isArray(r.nodes) && Array.isArray(r.links) && !("sites" in r && "hq" in r);
}

/** Parse + normalize a generic graph spec from the ```netdiagram JSON. null if unusable. */
export function parseGraphSpec(code: string): GraphSpec | null {
  let raw: unknown;
  try { raw = JSON.parse(code.trim()); } catch { return null; }
  if (!isGraphSpec(raw)) return null;
  const o = raw as Record<string, unknown>;
  const strs = (v: unknown, max: number): string[] =>
    Array.isArray(v) ? v.map((x) => String(x)).filter(Boolean).slice(0, max) : [];
  const nodes: GNode[] = (o.nodes as unknown[]).map((n, i) => {
    const no = (n || {}) as Record<string, unknown>;
    const id = String(no.id ?? no.label ?? `n${i}`);
    const label = String(no.label ?? no.icon ?? id).slice(0, 40);
    const node: GNode = { id, label, icon: String(no.icon ?? label) };
    if (no.sub != null) node.sub = String(no.sub).slice(0, 36);
    if (typeof no.tier === "number") node.tier = no.tier;
    return node;
  }).slice(0, 40);
  const ids = new Set(nodes.map((n) => n.id));
  if (nodes.length < 2) return null;
  const links: GLink[] = (o.links as unknown[]).map((l) => {
    const lo = (l || {}) as Record<string, unknown>;
    return { from: String(lo.from ?? ""), to: String(lo.to ?? ""), kind: lo.kind as GLink["kind"], label: lo.label != null ? String(lo.label).slice(0, 20) : undefined };
  }).filter((l) => ids.has(l.from) && ids.has(l.to)).slice(0, 80);
  const groups: GGroup[] = Array.isArray(o.groups)
    ? (o.groups as unknown[]).map((g) => {
        const go = (g || {}) as Record<string, unknown>;
        return { id: String(go.id ?? go.label ?? ""), label: String(go.label ?? "").slice(0, 40), nodes: strs(go.nodes, 40) };
      }).filter((g) => g.label && g.nodes.length).slice(0, 8)
    : [];
  return {
    title: String(o.title ?? "Network Topology").slice(0, 80),
    subtitle: String(o.subtitle ?? "").slice(0, 120),
    nodes, links, groups,
    legend: strs(o.legend, 10), features: strs(o.features, 10), notes: strs(o.notes, 10),
  };
}
