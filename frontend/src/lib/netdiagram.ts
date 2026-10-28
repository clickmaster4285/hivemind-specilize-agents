/* Pure (no React / DOM) builder for the designed network infographic (hub-and-spoke: sites → HQ), rendered
 * from the JSON spec the model emits in a ```netdiagram block.
 *
 * Device icons are OPEN: every node/device is named in plain words and drawn via an injected `drawIcon`
 * (lib/deviceIcons.ts resolver → hand-drawn / bundled Iconify pack / neutral labelled box). So an AP, NAS,
 * IP camera, VoIP phone — anything — shows as itself and is never dropped. Kept free of React so it can be
 * verified headlessly and the SVG string is exactly what PNG/SVG export draws.
 *
 * Each site card draws its OWN internal chain (e.g. router — switch — PCs — printer) with links + labels,
 * so "how each site connects to its devices" is visible, plus the VPN tunnel to HQ.
 */

/** Draws one device icon by free-text name: (name, x, y, size, color) → SVG string (box fallback inside). */
export type DrawIcon = (name: string, x: number, y: number, size: number, color?: string) => string;

export type NetSite = { label: string; sub: string; devices?: string[] };
export type HqNode = { icon: string; label: string };

export type NetSpec = {
  title: string;
  subtitle: string;
  cloud: string;          // the internet/cloud node label at the top
  linkLabel: string;      // e.g. "VPN Tunnel (IPsec/SSL)"
  hq: { label: string; nodes: HqNode[] };
  sites: NetSite[];
  /** the typical site LAN chain, used by every site that doesn't list its own devices */
  siteTemplate?: { label: string; devices: string[] };
  legend: string[];
  features: string[];
  notes: string[];
};

const C = {
  bg: "#ffffff", ink: "#1f2937", sub: "#64748b", line: "#e2e8f0", card: "#f8fafc",
  hqBg: "#f1f5f9", hqLine: "#cbd5e1", icon: "#334155", link: "#94a3b8", vpn: "#16a34a", accent: "#2563eb",
};

const DEFAULT_CHAIN = ["router", "switch", "pc"];

function esc(s: string): string {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}
function clip(s: string, n: number): string {
  const t = String(s);
  return t.length > n ? t.slice(0, n - 1) + "…" : t;
}
/** Short, human label for a device named in free text ("wireless access point (AP)" → "wireless access…"). */
function short(name: string, n = 14): string {
  const t = String(name || "").trim().replace(/\s*\(.*?\)\s*/g, " ").trim();
  return clip(t, n);
}

function wrap(text: string, max: number): string[] {
  const words = String(text).split(/\s+/);
  const lines: string[] = [];
  let cur = "";
  for (const w of words) {
    if (!cur) cur = w;
    else if ((cur + " " + w).length <= max) cur += " " + w;
    else { lines.push(cur); cur = w; }
  }
  if (cur) lines.push(cur);
  return lines;
}

/** A device chain inside a card: icons left→right joined by links, each labelled with its own name. */
function drawChain(parts: string[], draw: DrawIcon, devs: string[], x: number, w: number, cy: number): void {
  const list = devs.slice(0, 5);
  const n = list.length;
  if (!n) return;
  const cellW = Math.min(96, w / n);
  const sx = x + (w - cellW * n) / 2;
  const half = 12;
  list.forEach((d, i) => {
    const cx = sx + i * cellW + cellW / 2;
    if (i < n - 1) {
      const nx = sx + (i + 1) * cellW + cellW / 2;
      parts.push(`<line x1="${cx + half + 3}" y1="${cy}" x2="${nx - half - 3}" y2="${cy}" stroke="${C.link}" stroke-width="2" stroke-linecap="round"/>`);
    }
    parts.push(draw(d, cx - half, cy - half, half * 2, /pc|user|laptop|phone|tablet/i.test(d) ? C.accent : C.icon));
    parts.push(`<text x="${cx}" y="${cy + 25}" text-anchor="middle" font-size="10.5" fill="${C.sub}">${esc(short(d, 11))}</text>`);
  });
}

const CARD_W = 310, CARD_H = 104, V_GAP = 14, PAD = 28, W = 1180;

/** Build the full diagram as an SVG string. Pure — no React, no DOM. `draw` renders device icons by name. */
export function buildNetworkSvg(spec: NetSpec, draw: DrawIcon): string {
  const sites = spec.sites.slice(0, 16);
  const leftCount = Math.ceil(sites.length / 2);
  const left = sites.slice(0, leftCount);
  const right = sites.slice(leftCount);
  const chainDefault = spec.siteTemplate?.devices?.length ? spec.siteTemplate.devices : DEFAULT_CHAIN;

  const contentTop = 108;
  const rowStep = CARD_H + V_GAP;
  const rows = Math.max(left.length, right.length, 1);
  const hqW = 288, nodeH = 52, nodeGap = 14;
  const nNodes = spec.hq.nodes.length;
  const hqH = nNodes * nodeH + Math.max(nNodes - 1, 0) * nodeGap + 56;
  const rowsH = Math.max(rows * rowStep - V_GAP, hqH + 84 + 10);

  const centerX = W / 2;
  const leftInner = PAD + CARD_W;
  const rightX = W - PAD - CARD_W;
  const hqX = centerX - hqW / 2;
  const hqTop = contentTop + Math.max((rowsH - hqH) / 2, 84);
  const hqMidY = hqTop + hqH / 2;

  const parts: string[] = [];

  // ── Internet cloud above HQ, with the tunnel-type pill beside it
  parts.push(`<line x1="${centerX}" y1="${contentTop + 64}" x2="${centerX}" y2="${hqTop}" stroke="${C.hqLine}" stroke-width="2"/>`);
  parts.push(draw(spec.cloud || "cloud", centerX - 26, contentTop - 8, 52, C.accent));
  parts.push(`<text x="${centerX}" y="${contentTop + 60}" text-anchor="middle" font-size="14" font-weight="600" fill="${C.ink}">${esc(clip(spec.cloud, 22))}</text>`);
  if (spec.linkLabel) {
    const pw = Math.min(200, spec.linkLabel.length * 6.4 + 40);
    parts.push(
      `<g transform="translate(${centerX + 44} ${contentTop + 8})"><rect x="0" y="0" width="${pw}" height="24" rx="12" fill="#f0fdf4" stroke="${C.vpn}"/>` +
        draw("vpn", 8, 4, 16, C.vpn) +
        `<text x="30" y="16" font-size="11.5" fill="${C.vpn}" font-weight="600">${esc(clip(spec.linkLabel, 28))}</text></g>`,
    );
  }

  // ── VPN connectors (drawn first, behind the cards)
  const connector = (x1: number, y1: number, x2: number, y2: number) => {
    const c1 = x1 < x2 ? x1 + 56 : x1 - 56;
    const c2 = x1 < x2 ? x2 - 56 : x2 + 56;
    parts.push(`<path d="M${x1} ${y1} C${c1} ${y1}, ${c2} ${y2}, ${x2} ${y2}" fill="none" stroke="${C.vpn}" stroke-width="2.4" opacity="0.85"/>`);
    const mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
    parts.push(`<circle cx="${mx}" cy="${my}" r="10" fill="#fff" stroke="${C.vpn}" stroke-width="1.6"/>`);
    parts.push(draw("vpn", mx - 7, my - 7, 14, C.vpn));
  };
  left.forEach((_, i) => connector(leftInner, contentTop + i * rowStep + CARD_H / 2, hqX, hqMidY));
  right.forEach((_, i) => connector(rightX, contentTop + i * rowStep + CARD_H / 2, hqX + hqW, hqMidY));

  // ── site cards: header + VPN badge + the site's own device chain
  const card = (x: number, y: number, s: NetSite) => {
    parts.push(`<rect x="${x}" y="${y}" width="${CARD_W}" height="${CARD_H}" rx="10" fill="${C.card}" stroke="${C.line}"/>`);
    parts.push(draw("office", x + 14, y + 11, 28, C.accent));
    parts.push(`<text x="${x + 52}" y="${y + 27}" font-size="15" font-weight="700" fill="${C.ink}">${esc(clip(s.label, 22))}</text>`);
    if (s.sub) parts.push(`<text x="${x + 52}" y="${y + 44}" font-size="12" fill="${C.sub}">${esc(clip(s.sub, 26))}</text>`);
    parts.push(`<rect x="${x + CARD_W - 58}" y="${y + 14}" width="44" height="20" rx="10" fill="#f0fdf4" stroke="${C.vpn}"/>`);
    parts.push(`<text x="${x + CARD_W - 36}" y="${y + 28}" text-anchor="middle" font-size="10.5" font-weight="700" fill="${C.vpn}">VPN</text>`);
    parts.push(`<line x1="${x + 14}" y1="${y + 54}" x2="${x + CARD_W - 14}" y2="${y + 54}" stroke="${C.line}"/>`);
    drawChain(parts, draw, s.devices?.length ? s.devices : chainDefault, x + 14, CARD_W - 28, y + 73);
  };
  left.forEach((s, i) => card(PAD, contentTop + i * rowStep, s));
  right.forEach((s, i) => card(rightX, contentTop + i * rowStep, s));

  // ── HQ box: devices chained top → bottom
  parts.push(`<rect x="${hqX}" y="${hqTop}" width="${hqW}" height="${hqH}" rx="12" fill="${C.hqBg}" stroke="${C.hqLine}" stroke-width="1.5"/>`);
  parts.push(`<text x="${centerX}" y="${hqTop + 28}" text-anchor="middle" font-size="14.5" font-weight="700" fill="${C.accent}">${esc(clip(spec.hq.label, 30))}</text>`);
  if (nNodes > 1) {
    const y1 = hqTop + 42 + nodeH / 2;
    const y2 = hqTop + 42 + (nNodes - 1) * (nodeH + nodeGap) + nodeH / 2;
    parts.push(`<line x1="${centerX}" y1="${y1}" x2="${centerX}" y2="${y2}" stroke="${C.link}" stroke-width="2.4"/>`);
  }
  spec.hq.nodes.forEach((n, i) => {
    const ny = hqTop + 42 + i * (nodeH + nodeGap);
    parts.push(`<rect x="${hqX + 18}" y="${ny}" width="${hqW - 36}" height="${nodeH}" rx="8" fill="#fff" stroke="${C.line}"/>`);
    parts.push(draw(n.icon, hqX + 30, ny + (nodeH - 26) / 2, 26, C.icon));
    parts.push(`<text x="${hqX + 68}" y="${ny + nodeH / 2 + 5}" font-size="13" font-weight="600" fill="${C.ink}">${esc(clip(n.label, 28))}</text>`);
  });

  // ── header
  parts.unshift(
    `<text x="${PAD}" y="42" font-size="26" font-weight="800" fill="${C.ink}">${esc(clip(spec.title, 52))}</text>` +
      `<text x="${PAD}" y="68" font-size="14.5" fill="${C.sub}">${esc(clip(spec.subtitle, 90))}</text>`,
  );

  // ── bottom panels: Legend / Key Features / Notes
  const panelsTop = contentTop + rowsH + 36;
  const panelW = (W - 2 * PAD - 2 * 24) / 3;
  const panelData = [
    { title: "Legend", items: spec.legend, kind: "legend" as const },
    { title: "Key Features", items: spec.features, kind: "check" as const },
    { title: "Notes", items: spec.notes, kind: "dot" as const },
  ];
  const lineH = 21, WRAP = 44;
  const maxLines = Math.max(1, ...panelData.map((p) => p.items.reduce((n, it) => n + wrap(it, WRAP).length, 0)));
  const panelsH = 46 + maxLines * lineH + 12;
  const H = panelsTop + panelsH + PAD;

  panelData.forEach((p, pi) => {
    const px = PAD + pi * (panelW + 24);
    parts.push(`<rect x="${px}" y="${panelsTop}" width="${panelW}" height="${panelsH}" rx="10" fill="${C.card}" stroke="${C.line}"/>`);
    parts.push(`<text x="${px + 16}" y="${panelsTop + 27}" font-size="14" font-weight="700" fill="${C.ink}">${esc(p.title)}</text>`);
    let ly = panelsTop + 52;
    for (const it of p.items) {
      wrap(it, WRAP).forEach((ln, li) => {
        if (li === 0) {
          if (p.kind === "legend") parts.push(draw(it, px + 14, ly - 13, 17, /vpn|tunnel/i.test(it) ? C.vpn : /pc|user|office|site/i.test(it) ? C.accent : C.icon));
          else if (p.kind === "check")
            parts.push(`<path d="M${px + 16} ${ly - 4} l3 3 l5 -6" fill="none" stroke="${C.vpn}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>`);
          else parts.push(`<circle cx="${px + 19}" cy="${ly - 4}" r="2.4" fill="${C.sub}"/>`);
        }
        parts.push(`<text x="${px + 38}" y="${ly}" font-size="12.5" fill="${C.ink}">${esc(ln)}</text>`);
        ly += lineH;
      });
    }
  });

  return (
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" width="100%" ` +
    `font-family="Arial, Helvetica, sans-serif" style="max-width:${W}px">` +
    `<rect x="0" y="0" width="${W}" height="${H}" fill="${C.bg}"/>` +
    parts.join("") +
    `</svg>`
  );
}

/** Every device/icon name referenced by a spec — so the component can pre-load just the packs it needs. */
export function iconNames(spec: NetSpec): string[] {
  const names = new Set<string>(["office", "vpn", spec.cloud || "cloud"]);
  spec.hq.nodes.forEach((n) => names.add(n.icon));
  (spec.siteTemplate?.devices || []).forEach((d) => names.add(d));
  spec.sites.forEach((s) => (s.devices || []).forEach((d) => names.add(d)));
  DEFAULT_CHAIN.forEach((d) => names.add(d));
  spec.legend.forEach((l) => names.add(l));
  return [...names];
}

/** Parse + normalize the model's JSON spec. Returns null if it isn't usable. Device names stay free-text. */
export function parseNetSpec(code: string): NetSpec | null {
  let raw: unknown;
  try {
    raw = JSON.parse(code.trim());
  } catch {
    return null;
  }
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  const strs = (v: unknown, max: number): string[] =>
    Array.isArray(v) ? v.map((x) => String(x)).filter(Boolean).slice(0, max) : [];

  const sites: NetSite[] = Array.isArray(o.sites)
    ? o.sites
        .map((s) => {
          const so = (s || {}) as Record<string, unknown>;
          const d = strs(so.devices, 5);
          return { label: String(so.label ?? "Site").slice(0, 40), sub: String(so.sub ?? "").slice(0, 40), ...(d.length ? { devices: d } : {}) };
        })
        .filter((s) => s.label)
        .slice(0, 16)
    : [];
  const hqo = (o.hq || {}) as Record<string, unknown>;
  const nodes: HqNode[] = Array.isArray(hqo.nodes)
    ? hqo.nodes
        .map((n) => {
          const no = (n || {}) as Record<string, unknown>;
          // accept {icon} or the older {type}; fall back to the label for the icon name
          const iconName = String(no.icon ?? no.type ?? no.label ?? "server");
          return { icon: iconName, label: String(no.label ?? iconName).slice(0, 40) };
        })
        .slice(0, 6)
    : [];
  if (!sites.length || !nodes.length) return null;

  const stpl = (o.siteTemplate ?? o.site_template) as Record<string, unknown> | undefined;
  let siteTemplate: NetSpec["siteTemplate"];
  if (stpl && typeof stpl === "object") {
    const d = strs(stpl.devices, 5);
    if (d.length) siteTemplate = { label: String(stpl.label ?? "Inside each branch site").slice(0, 60), devices: d };
  }
  return {
    title: String(o.title ?? "Network Diagram").slice(0, 80),
    subtitle: String(o.subtitle ?? "").slice(0, 120),
    cloud: String(o.cloud ?? "Internet").slice(0, 30),
    linkLabel: String(o.linkLabel ?? "").slice(0, 40),
    hq: { label: String(hqo.label ?? "Head Office (HQ)").slice(0, 40), nodes },
    sites,
    siteTemplate,
    legend: strs(o.legend, 10),
    features: strs(o.features, 10),
    notes: strs(o.notes, 10),
  };
}

/** True when the JSON looks fully written (balanced braces) — tells "still streaming" from "broken". */
export function looksCompleteJson(code: string): boolean {
  const s = code.trim();
  if (!s.endsWith("}")) return false;
  let depth = 0;
  for (const ch of s) {
    if (ch === "{") depth++;
    else if (ch === "}") { depth--; if (depth < 0) return false; }
  }
  return depth === 0;
}
