/* Layout eval for the network-diagram renderers (lib/netdiagram.ts hub-spoke + lib/graphdiagram.ts graph).
 *
 *   node scripts/netdiagram_eval.mjs            # run all samples, print a table
 *   node scripts/netdiagram_eval.mjs --svg out  # also write each sample's SVG to out/<name>.svg
 *
 * For each sample topology it checks: the spec parses, the SVG renders (viewBox, no NaN), every device
 * resolves to a real icon (counts text-box fallbacks), and no two node boxes overlap. Exits non-zero if any
 * sample fails, so it can gate changes to the diagram builders.
 */
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { pathToFileURL, fileURLToPath } from "node:url";
import { dirname, resolve as presolve } from "node:path";

const root = presolve(dirname(fileURLToPath(import.meta.url)), "..");
const L = (p) => pathToFileURL(presolve(root, p)).href;
const nd = await import(L("src/lib/netdiagram.ts"));
const gd = await import(L("src/lib/graphdiagram.ts"));
const di = await import(L("src/lib/deviceIcons.ts"));

const packs = {
  mdi: JSON.parse(readFileSync(presolve(root, "node_modules/@iconify-json/mdi/icons.json"), "utf8")),
  logos: JSON.parse(readFileSync(presolve(root, "node_modules/@iconify-json/logos/icons.json"), "utf8")),
};
const draw = di.makeDrawIcon(packs);

const argSvg = process.argv.indexOf("--svg");
const svgDir = argSvg >= 0 ? process.argv[argSvg + 1] : null;
if (svgDir) mkdirSync(svgDir, { recursive: true });

const site = (i, devices) => ({ label: `Site ${i}`, sub: "Branch Office", ...(devices ? { devices } : {}) });
const node = (id, icon, label, tier) => ({ id, icon, label, tier });

const samples = {
  "hub-spoke-10-sites": {
    title: "10 Sites to HQ", subtitle: "secure VPN", cloud: "Internet", linkLabel: "VPN Tunnel (IPsec/SSL)",
    hq: { label: "Head Office (HQ)", nodes: [
      { icon: "firewall", label: "Firewall / UTM" }, { icon: "load balancer", label: "Load Balancer" },
      { icon: "core switch", label: "Core Switch" }, { icon: "server", label: "App / DB Servers" }, { icon: "NAS", label: "SAN / NAS" }] },
    sites: [...Array(10)].map((_, i) => site(i + 1, i === 2 ? ["router", "switch", "Wi-Fi AP", "IP camera", "VoIP phone"] : null)),
    siteTemplate: { label: "Inside each branch site", devices: ["router", "switch", "PCs", "printer"] },
    legend: ["Site / Office", "Router", "Firewall", "Wi-Fi AP", "VPN Tunnel"], features: ["Secure VPN"], notes: ["Static IPs"],
  },
  "campus-three-tier": {
    title: "Campus Network", subtitle: "Core / distribution / access",
    nodes: [node("net", "internet", "Internet", 0), node("fw", "firewall", "Edge Firewall", 1),
      node("c1", "core switch", "Core 1", 2), node("c2", "core switch", "Core 2", 2),
      node("d1", "switch", "Dist A", 3), node("d2", "switch", "Dist B", 3), node("srv", "server", "Servers", 3),
      node("a1", "access switch", "Access 1", 4), node("a2", "access switch", "Access 2", 4), node("nas", "NAS", "NAS", 4),
      node("pcs", "PCs", "Workstations", 5), node("ap", "Wi-Fi AP", "Wi-Fi APs", 5)],
    links: [{ from: "net", to: "fw", kind: "internet" }, { from: "fw", to: "c1", kind: "wan" }, { from: "fw", to: "c2", kind: "wan" },
      { from: "c1", to: "d1", kind: "lan" }, { from: "c2", to: "d2", kind: "lan" }, { from: "c1", to: "srv", kind: "lan" },
      { from: "d1", to: "a1", kind: "lan" }, { from: "d2", to: "a2", kind: "lan" }, { from: "srv", to: "nas", kind: "lan" },
      { from: "a1", to: "pcs", kind: "lan" }, { from: "a2", to: "ap", kind: "wireless" }],
    groups: [{ label: "Core", nodes: ["c1", "c2"] }, { label: "Distribution", nodes: ["d1", "d2", "srv"] }, { label: "Access", nodes: ["a1", "a2", "nas"] }],
    legend: ["Firewall", "Switch", "Server", "Wi-Fi AP"], features: ["Three-tier"], notes: ["L3 core"],
  },
  "dmz-and-cloud": {
    title: "Perimeter with DMZ + Cloud", subtitle: "Internet edge, DMZ and internal LAN",
    nodes: [node("net", "internet", "Internet", 0), node("fw", "firewall", "NGFW", 1),
      node("web", "web server", "Web Server", 2), node("proxy", "reverse proxy", "Reverse Proxy", 2),
      node("core", "core switch", "Core Switch", 3), node("app", "server", "App Servers", 4), node("db", "database", "Database", 4),
      node("cloud", "cloud", "Cloud (O365)", 1)],
    links: [{ from: "net", to: "fw", kind: "internet" }, { from: "fw", to: "web", kind: "wan" }, { from: "fw", to: "proxy", kind: "wan" },
      { from: "fw", to: "core", kind: "lan" }, { from: "core", to: "app", kind: "lan" }, { from: "core", to: "db", kind: "lan" },
      { from: "fw", to: "cloud", kind: "vpn", label: "VPN" }],
    groups: [{ label: "DMZ", nodes: ["web", "proxy"] }, { label: "Internal LAN", nodes: ["app", "db"] }],
    legend: ["Firewall", "Web Server", "Database", "VPN Tunnel"], features: ["DMZ isolation"], notes: ["Cloud via VPN"],
  },
  "home-network": {
    title: "Home Network", subtitle: "A typical home LAN",
    nodes: [node("net", "internet", "Internet", 0), node("rt", "router", "Wi-Fi Router", 1),
      node("sw", "switch", "Switch", 2), node("pc", "PCs", "Desktops", 3), node("lap", "laptop", "Laptops", 3),
      node("tv", "smart device", "Smart TV", 3), node("print", "printer", "Printer", 3), node("nas", "NAS", "NAS", 3)],
    links: [{ from: "net", to: "rt", kind: "internet" }, { from: "rt", to: "sw", kind: "lan" }, { from: "rt", to: "lap", kind: "wireless" },
      { from: "rt", to: "tv", kind: "wireless" }, { from: "sw", to: "pc", kind: "lan" }, { from: "sw", to: "print", kind: "lan" }, { from: "sw", to: "nas", kind: "lan" }],
    groups: [], legend: ["Router", "Switch", "PCs", "Printer"], features: ["Wired + Wi-Fi"], notes: ["One subnet"],
  },
  "datacenter-spine-leaf": {
    title: "Data Centre — Spine / Leaf", subtitle: "Two-tier Clos fabric",
    nodes: [node("sp1", "core switch", "Spine 1", 0), node("sp2", "core switch", "Spine 2", 0),
      node("lf1", "switch", "Leaf 1", 1), node("lf2", "switch", "Leaf 2", 1), node("lf3", "switch", "Leaf 3", 1), node("lf4", "switch", "Leaf 4", 1),
      node("s1", "server", "Rack 1", 2), node("s2", "server", "Rack 2", 2), node("s3", "server", "Rack 3", 2), node("s4", "server", "Rack 4", 2)],
    links: [...["lf1", "lf2", "lf3", "lf4"].flatMap((lf) => [{ from: "sp1", to: lf, kind: "lan" }, { from: "sp2", to: lf, kind: "lan" }]),
      { from: "lf1", to: "s1", kind: "lan" }, { from: "lf2", to: "s2", kind: "lan" }, { from: "lf3", to: "s3", kind: "lan" }, { from: "lf4", to: "s4", kind: "lan" }],
    groups: [{ label: "Spine", nodes: ["sp1", "sp2"] }, { label: "Leaf", nodes: ["lf1", "lf2", "lf3", "lf4"] }],
    legend: ["Switch", "Server"], features: ["Non-blocking fabric"], notes: ["ECMP"],
  },
  "small-office-star": {
    title: "Small Office Network", subtitle: "Single switch star",
    nodes: [node("net", "internet", "Internet", 0), node("fw", "firewall", "Firewall", 1), node("sw", "switch", "Switch", 2),
      node("pc", "PCs", "PCs", 3), node("print", "printer", "Printer", 3), node("nas", "NAS", "NAS", 3), node("ap", "Wi-Fi AP", "Wi-Fi AP", 3)],
    links: [{ from: "net", to: "fw", kind: "internet" }, { from: "fw", to: "sw", kind: "lan" },
      { from: "sw", to: "pc", kind: "lan" }, { from: "sw", to: "print", kind: "lan" }, { from: "sw", to: "nas", kind: "lan" }, { from: "sw", to: "ap", kind: "wireless" }],
    groups: [], legend: ["Firewall", "Switch", "PCs", "Wi-Fi AP"], features: ["Simple star"], notes: ["One VLAN"],
  },
};

const NW = 150, NH = 82, PAD = 32, W = 1180, TIER_GAP = 150, TOP = 92;
function graphOverlaps(spec) {
  const byTier = new Map();
  const tierOf = new Map(spec.nodes.map((n) => [n.id, n.tier ?? 0]));
  spec.nodes.forEach((n) => { const t = tierOf.get(n.id); (byTier.get(t) || byTier.set(t, []).get(t)).push(n); });
  const tiers = [...byTier.keys()].sort((a, b) => a - b);
  const ti = new Map(tiers.map((t, i) => [t, i]));
  const boxes = [];
  tiers.forEach((t) => { const row = byTier.get(t); const step = (W - 2 * PAD) / row.length;
    row.forEach((n, i) => boxes.push({ x: PAD + step * (i + 0.5), y: TOP + ti.get(t) * TIER_GAP + NH / 2 })); });
  let o = 0;
  for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++)
    if (Math.abs(boxes[i].x - boxes[j].x) < NW + 4 && Math.abs(boxes[i].y - boxes[j].y) < NH + 4) o++;
  return o;
}

function unknownIcons(names) {
  let n = 0;
  for (const name of names) {
    const r = di.resolve(name);
    if (r.kind === "text") n++;
    else if (r.kind === "pack") { const p = r.ref.split(":")[0]; if (!di.extractPackIcon(packs[p], r.ref)) n++; }
  }
  return n;
}

let failures = 0;
const rows = [];
for (const [name, raw] of Object.entries(samples)) {
  const json = JSON.stringify(raw);
  const isGraph = gd.isGraphSpec(raw);
  const spec = isGraph ? gd.parseGraphSpec(json) : nd.parseNetSpec(json);
  const checks = { parsed: !!spec, renders: false, noNaN: false, overlaps: "-", unknownIcons: "-" };
  if (spec) {
    const svg = isGraph ? gd.buildGraphSvg(spec, draw) : nd.buildNetworkSvg(spec, draw);
    checks.renders = svg.startsWith("<svg") && /viewBox="0 0 [\d.]+ [\d.]+"/.test(svg);
    checks.noNaN = !/NaN|undefined/.test(svg);
    checks.overlaps = isGraph ? graphOverlaps(spec) : 0;
    checks.unknownIcons = unknownIcons(isGraph ? gd.graphIconNames(spec) : nd.iconNames(spec));
    if (svgDir) writeFileSync(presolve(svgDir, `${name}.svg`), svg, "utf8");
  }
  const ok = checks.parsed && checks.renders && checks.noNaN && checks.overlaps === 0 && checks.unknownIcons <= 2;
  if (!ok) failures++;
  rows.push({ sample: name, shape: isGraph ? "graph" : "hub-spoke", ...checks, PASS: ok });
}
console.table(rows);
console.log(failures ? `FAIL: ${failures} sample(s) failed` : `OK: all ${rows.length} samples passed`);
process.exit(failures ? 1 : 0);
