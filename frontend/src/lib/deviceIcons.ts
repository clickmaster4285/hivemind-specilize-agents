/* Open device-icon vocabulary for the network diagram.
 *
 * The model names a device in plain words ("wireless access point", "NAS", "IP camera"). resolve() maps the
 * name to an icon and NEVER drops or mislabels it:
 *   1. a hand-drawn icon  — crisp, export-safe, no external load; the common gear;
 *   2. an alias / keyword → a bundled Iconify icon (mdi: 7600+ icons, logos: brand logos), loaded lazily;
 *   3. a neutral labelled box — so an unknown device still appears, as itself.
 *
 * This file is pure (no DOM / no imports of the packs); the component lazy-loads the pack JSON and hands the
 * parsed objects to extractPackIcon(), so the big icon JSON never ships unless a diagram needs it.
 */

export type ResolvedIcon =
  | { kind: "hand"; body: string }          // hand-drawn, 0..24 box, fill=currentColor
  | { kind: "pack"; ref: string }           // "mdi:router-wireless" | "logos:aws" — body fetched lazily
  | { kind: "text" };                       // draw a labelled box (unknown device)

export type PackIcon = { body: string; w: number; h: number; mono: boolean };

/* ── hand-drawn bodies (0..24 viewBox, monochrome via currentColor) ───────────────────────────────── */
export const HAND: Record<string, string> = {
  office:
    '<rect x="3" y="2.5" width="12" height="19" rx="1"/><rect x="15" y="8" width="6" height="13.5" rx="1"/>' +
    '<g fill="#fff"><rect x="5" y="4.5" width="2.4" height="2.4"/><rect x="8.6" y="4.5" width="2.4" height="2.4"/>' +
    '<rect x="5" y="8" width="2.4" height="2.4"/><rect x="8.6" y="8" width="2.4" height="2.4"/>' +
    '<rect x="5" y="11.5" width="2.4" height="2.4"/><rect x="8.6" y="11.5" width="2.4" height="2.4"/>' +
    '<rect x="16.5" y="10.5" width="3" height="2.2"/><rect x="16.5" y="14" width="3" height="2.2"/></g>',
  router:
    '<rect x="2" y="9.5" width="20" height="7" rx="2.2"/><circle cx="6" cy="13" r="1.1" fill="#fff"/>' +
    '<circle cx="9.5" cy="13" r="1.1" fill="#fff"/><g stroke="#fff" stroke-width="1.4" fill="none" stroke-linecap="round">' +
    '<path d="M14 7 L19 7 M17 5 L19 7 L17 9"/><path d="M19 12.5 L14 12.5 M16 10.5 L14 12.5 L16 14.5"/></g>',
  switch:
    '<rect x="2" y="8.5" width="20" height="7" rx="1.6"/><g stroke="#fff" stroke-width="1.4" fill="none" stroke-linecap="round">' +
    '<path d="M6 6.5 L18 6.5 M16 4.5 L18 6.5 L16 8.5"/><path d="M18 17.5 L6 17.5 M8 15.5 L6 17.5 L8 19.5"/></g>',
  server:
    '<rect x="4" y="2.5" width="16" height="8" rx="1.4"/><rect x="4" y="13" width="16" height="8" rx="1.4"/>' +
    '<circle cx="7.5" cy="6.5" r="1" fill="#fff"/><circle cx="7.5" cy="17" r="1" fill="#fff"/>' +
    '<g stroke="#fff" stroke-width="1.2"><path d="M11 6.5 H17"/><path d="M11 17 H17"/></g>',
  firewall:
    '<rect x="2.5" y="3" width="19" height="18" rx="1.4"/><g stroke="#fff" stroke-width="1.1" fill="none">' +
    '<path d="M2.5 9 H21.5 M2.5 15 H21.5 M8.5 3 V9 M15.5 3 V9 M5 9 V15 M12 9 V15 M19 9 V15 M8.5 15 V21 M15.5 15 V21"/></g>',
  cloud:
    '<path d="M6.5 19 Q2 19 2 14.5 Q2 10.5 6.5 10.3 Q7.5 5.5 12.5 5.5 Q17 5.5 17.8 10 Q22 10 22 14.5 Q22 19 17.5 19 Z"/>',
  pc:
    '<rect x="2.5" y="4" width="19" height="12" rx="1.4"/><rect x="4.3" y="5.8" width="15.4" height="8.4" rx="0.6" fill="#fff"/>' +
    '<path d="M9 19 H15 L16 21 H8 Z"/>',
  lock:
    '<rect x="4.5" y="10.5" width="15" height="11" rx="2"/><path d="M7.5 10.5 V7.5 A4.5 4.5 0 0 1 16.5 7.5 V10.5" ' +
    'fill="none" stroke="currentColor" stroke-width="2.2"/><circle cx="12" cy="15.5" r="1.7" fill="#fff"/>',
  database:
    '<path d="M4 6 Q4 3 12 3 Q20 3 20 6 L20 18 Q20 21 12 21 Q4 21 4 18 Z"/>' +
    '<path d="M4 6 Q4 9 12 9 Q20 9 20 6" fill="none" stroke="#fff" stroke-width="1.2"/>' +
    '<path d="M4 12 Q4 15 12 15 Q20 15 20 12" fill="none" stroke="#fff" stroke-width="1.2"/>',
  users:
    '<circle cx="8.5" cy="8" r="3.2"/><path d="M3 20 Q3 13.5 8.5 13.5 Q14 13.5 14 20 Z"/>' +
    '<circle cx="16.5" cy="9" r="2.6"/><path d="M13.5 20 Q13.5 15 16.5 15 Q21 15 21 20 Z"/>',
  printer:
    '<rect x="6" y="2.5" width="12" height="6" rx="1"/><rect x="2.5" y="8.5" width="19" height="8.5" rx="1.5"/>' +
    '<rect x="6.5" y="14" width="11" height="6.5" rx="0.6" fill="#fff"/><circle cx="18" cy="11.5" r="1" fill="#fff"/>',
  wifi:
    '<circle cx="12" cy="18.6" r="1.7"/><path d="M6 13 Q12 8 18 13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>' +
    '<path d="M3 9.5 Q12 2 21 9.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>',
  phone:
    '<rect x="6.5" y="2.5" width="11" height="19" rx="2"/><rect x="8.3" y="5" width="7.4" height="11" rx="0.6" fill="#fff"/>' +
    '<circle cx="12" cy="18.6" r="1" fill="#fff"/>',
  camera:
    '<rect x="2.5" y="7" width="14" height="8" rx="1.4"/><path d="M16.5 9.5 L21 7 L21 15 L16.5 12.5 Z"/>' +
    '<path d="M6 15 L6 20 M4 20 H10" stroke="currentColor" stroke-width="1.6" fill="none" stroke-linecap="round"/>',
};

/* ── alias / synonym table: normalized plain words → a HAND key or a "pack:" ref ─────────────────────
 * Keep the common, high-value devices on hand-drawn icons; send the long tail to the bundled packs. */
const ALIAS: Record<string, string> = {
  // end devices
  pc: "pc", pcs: "pc", computer: "pc", computers: "pc", workstation: "pc", workstations: "pc", desktop: "pc",
  "end device": "pc", "end devices": "pc", client: "pc", clients: "pc", "local devices": "pc",
  thinclient: "pc", "thin client": "pc", "pc printers": "pc",
  laptop: "mdi:laptop", notebook: "mdi:laptop",
  tablet: "mdi:tablet", mobile: "mdi:cellphone", smartphone: "mdi:cellphone", "mobile phone": "mdi:cellphone",
  // wireless
  ap: "wifi", "access point": "wifi", "wireless ap": "wifi", "wifi ap": "wifi", "wireless access point": "wifi",
  wifi: "wifi", "wi fi": "wifi", wireless: "wifi", "wlan controller": "mdi:access-point-network",
  // storage
  nas: "mdi:nas", san: "database", storage: "mdi:nas", "file storage": "mdi:nas", "storage array": "mdi:nas",
  backup: "mdi:backup-restore", "backup server": "mdi:backup-restore", "tape backup": "mdi:tape-drive",
  // voice / video
  voip: "phone", "ip phone": "phone", "voip phone": "phone", "desk phone": "phone", phone: "phone", pbx: "phone",
  sbc: "mdi:phone-settings", cctv: "camera", camera: "camera", "ip camera": "camera", "security camera": "camera",
  // network gear
  router: "router", gateway: "router", "edge router": "router", "core router": "router", "wan router": "router",
  modem: "router", "dsl modem": "router", "cable modem": "router", ont: "router", cpe: "router",
  switch: "switch", "l2 switch": "switch", "l3 switch": "switch", "core switch": "switch",
  "access switch": "switch", "distribution switch": "switch", "poe switch": "switch",
  firewall: "firewall", utm: "firewall", ngfw: "firewall", ips: "firewall", ids: "mdi:shield-search",
  "load balancer": "mdi:scale-balance", lb: "mdi:scale-balance", "reverse proxy": "mdi:swap-horizontal",
  proxy: "mdi:swap-horizontal", vpn: "lock", "vpn concentrator": "lock", tunnel: "lock",
  // servers / services
  server: "server", "web server": "server", "app server": "server", "application server": "server",
  "mail server": "mdi:email", "file server": "server", "print server": "printer",
  dns: "mdi:dns", "dns server": "mdi:dns", dhcp: "mdi:ip-network", "dhcp server": "mdi:ip-network",
  "domain controller": "mdi:server-security", dc: "mdi:server-security", "ad server": "mdi:server-security",
  "radius server": "mdi:server-security", controller: "mdi:server-network", hypervisor: "mdi:server",
  // data
  database: "database", db: "database", "database server": "database", sql: "database", "sql server": "database",
  // cloud / internet
  cloud: "cloud", internet: "cloud", wan: "cloud", "cloud services": "cloud", saas: "cloud",
  "cloud provider": "cloud", mpls: "mdi:transit-connection-variant", "mpls cloud": "mdi:transit-connection-variant",
  // premises
  office: "office", site: "office", branch: "office", "branch office": "office", building: "office",
  "head office": "mdi:office-building", hq: "mdi:office-building", headquarters: "mdi:office-building",
  "data center": "mdi:server", "data centre": "mdi:server", datacenter: "mdi:server", warehouse: "mdi:warehouse",
  home: "mdi:home", store: "mdi:store", factory: "mdi:factory", "retail store": "mdi:store",
  // people / misc
  users: "users", user: "users", staff: "users", employees: "users",
  iot: "mdi:chip", "iot device": "mdi:chip", sensor: "mdi:access-point", plc: "mdi:chip", scada: "mdi:chip",
  ups: "mdi:power-plug", pdu: "mdi:power-plug-outline", monitor: "mdi:monitor-dashboard",
};

/* keyword rules: applied when there's no exact alias (any of these words anywhere in the name). First match wins. */
const KEYWORDS: [RegExp, string][] = [
  [/fire ?wall|\butm\b|ngfw|\bips\b/, "firewall"],
  [/access point|\bap\b|wireless|wi ?fi|wlan/, "wifi"],
  [/load ?balanc/, "mdi:scale-balance"],
  [/\brouter|gateway|modem|\bont\b|\bcpe\b/, "router"],
  [/\bswitch/, "switch"],
  [/backup/, "mdi:backup-restore"],
  [/\bnas\b|storage|\bsan\b/, "mdi:nas"],
  [/data ?base|\bsql\b|\bdb\b/, "database"],
  [/camera|cctv|surveillance/, "camera"],
  [/\bsbc\b|session border|\bpbx\b|phone|voip|voice/, "phone"],
  [/printer|\bmfp\b|scanner/, "printer"],
  [/laptop|notebook/, "mdi:laptop"],
  [/tablet/, "mdi:tablet"],
  [/mobile|cellphone|smartphone/, "mdi:cellphone"],
  [/cloud|internet|\bwan\b|saas|\bmpls\b/, "cloud"],
  [/\biot\b|sensor|\bplc\b|scada|smart ?device/, "mdi:chip"],
  [/\bups\b|\bpdu\b|power/, "mdi:power-plug"],
  [/\busers?\b|staff|people|employee/, "users"],
  [/office|site|branch|building|hq|head ?office|store|warehouse|factory|campus/, "office"],
  [/server|host|\bvm\b|virtual machine|hypervisor|controller/, "server"],
  [/\bpcs?\b|computer|workstation|desktop|client|end ?device|thin ?client/, "pc"],
];

export function normalize(name: string): string {
  return String(name || "").toLowerCase().replace(/[^a-z0-9]+/g, " ").replace(/\s+/g, " ").trim();
}

function toResolved(key: string): ResolvedIcon {
  if (HAND[key]) return { kind: "hand", body: HAND[key] };
  if (key.includes(":")) return { kind: "pack", ref: key };
  return { kind: "text" };
}

/** Resolve a free-text device name to an icon plan. Never returns null — unknown → {kind:"text"}. */
export function resolve(name: string): ResolvedIcon {
  const n = normalize(name);
  if (!n) return { kind: "text" };
  if (HAND[n]) return { kind: "hand", body: HAND[n] };
  if (ALIAS[n]) return toResolved(ALIAS[n]);
  for (const [re, key] of KEYWORDS) if (re.test(n)) return toResolved(key);
  // last resort: a bare mdi icon named exactly like the normalized words (hyphenated), resolved at load time
  return { kind: "pack", ref: `mdi:${n.replace(/ /g, "-")}` };
}

/** Which packs ("mdi" / "logos") a set of device names needs, so the component loads only those. */
export function neededPacks(names: string[]): Set<string> {
  const packs = new Set<string>();
  for (const name of names) {
    const r = resolve(name);
    if (r.kind === "pack") packs.add(r.ref.split(":")[0]);
  }
  return packs;
}

type IconifyPack = {
  prefix?: string;
  width?: number;
  height?: number;
  icons?: Record<string, { body: string; width?: number; height?: number }>;
  aliases?: Record<string, { parent: string }>;
};

/** Pull one icon's body + size from an already-loaded Iconify pack object. Follows aliases. null = absent. */
export function extractPackIcon(pack: IconifyPack, ref: string): PackIcon | null {
  if (!pack || !pack.icons) return null;
  const [prefix, rawName] = ref.includes(":") ? ref.split(":") : ["", ref];
  let name = rawName;
  // follow one alias hop (mdi puts "firewall" etc. in aliases → a real icon)
  if (!pack.icons[name] && pack.aliases && pack.aliases[name]) name = pack.aliases[name].parent;
  const ic = pack.icons[name];
  if (!ic) return null;
  const w = ic.width ?? pack.width ?? 24;
  const h = ic.height ?? pack.height ?? 24;
  // logos icons carry their own colours; mdi are monochrome (fill currentColor)
  const mono = prefix !== "logos";
  return { body: ic.body, w, h, mono };
}

/** One icon <g> at (x,y), scaled to fit `size` and centred. mono icons are painted `color`
 *  (both `fill` and `color`, so `fill="currentColor"` bodies pick it up); coloured packs keep their fills. */
function iconG(body: string, x: number, y: number, size: number, iw: number, ih: number, color: string, mono: boolean): string {
  const s = size / Math.max(iw, ih);
  const ox = x + (size - iw * s) / 2;
  const oy = y + (size - ih * s) / 2;
  const paint = mono ? ` style="color:${color}" fill="${color}"` : "";
  return `<g transform="translate(${ox} ${oy}) scale(${s})"${paint}>${body}</g>`;
}

/** Neutral generic-device glyph for an unknown device (rounded box + a node dot) — so it still appears. */
function textBox(x: number, y: number, size: number, color: string): string {
  const p = size * 0.1;
  return (
    `<rect x="${x + p}" y="${y + size * 0.2}" width="${size - 2 * p}" height="${size * 0.6}" rx="${size * 0.14}" fill="none" stroke="${color}" stroke-width="1.6"/>` +
    `<circle cx="${x + size / 2}" cy="${y + size / 2}" r="${Math.max(1.4, size * 0.09)}" fill="${color}"/>`
  );
}

/** Build a drawIcon(name,x,y,size,color) using any already-loaded packs; unknown names → the box glyph.
 *  Structurally matches netdiagram.DrawIcon (no import, so there's no module cycle). */
export function makeDrawIcon(packs: Record<string, IconifyPack>): (name: string, x: number, y: number, size: number, color?: string) => string {
  return (name, x, y, size, color = "#334155") => {
    const r = resolve(name);
    if (r.kind === "hand") return iconG(r.body, x, y, size, 24, 24, color, true);
    if (r.kind === "pack") {
      const pref = r.ref.split(":")[0];
      const ic = packs[pref] ? extractPackIcon(packs[pref], r.ref) : null;
      if (ic) return iconG(ic.body, x, y, size, ic.w, ic.h, color, ic.mono);
    }
    return textBox(x, y, size, color);
  };
}
