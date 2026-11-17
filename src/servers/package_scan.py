"""Known vulnerabilities in a Linux server's installed packages, by Trivy (doc/SECURITY_TOOLS_DECISION.md §13, V3b).

Nothing is installed or written on the server. Over the read-only SSH audit login we read the package list
(`dpkg-query` / `rpm -q`, no root needed) and /etc/os-release; here we write it as a CycloneDX SBOM in the exact
form Trivy writes itself — source package name, version and epoch included, because Debian's and Ubuntu's fix
lists are keyed by the source package — and run `trivy sbom` on it. Trivy then uses the distributions' own fix
lists, so a fix the vendor backported counts (unlike V1's "possible" match by version number).

Accuracy was measured against Trivy's own scans before this was used (§13): the same CVEs, package by package.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from src.servers.image_scan import PRIVATE_FLAGS, ImageReport, parse_report

logger = logging.getLogger(__name__)

# FIXED commands (never built from user or model text); none needs root
COMMANDS = {
    "os": "cat /etc/os-release",
    "debian_version": "cat /etc/debian_version",
    "dpkg": r"dpkg-query -W -f='${db:Status-Abbrev}\t${Package}\t${Version}\t${Architecture}\t${source:Package}\t"
            r"${source:Version}\n'",
    "rpm": r"rpm -qa --qf '%{NAME}\t%{EPOCHNUM}\t%{VERSION}\t%{RELEASE}\t%{ARCH}\t%{SOURCERPM}\n'",
}
# os-release ID → Trivy's name for the distribution (and its purl namespace); others Trivy cannot check by SBOM
TRIVY_OS = {"debian": "debian", "ubuntu": "ubuntu", "rhel": "redhat", "centos": "centos", "rocky": "rocky",
            "almalinux": "alma", "fedora": "fedora", "amzn": "amazon", "ol": "oracle"}
DEB_OS = {"debian", "ubuntu"}
TRIVY_TIMEOUT_SECONDS = 300
PACKAGES_MAX = 20000          # a server with more is not a server's package list


class PackageScanError(RuntimeError):
    """The package list could not be read or checked."""


@dataclass
class Package:
    name: str
    epoch: int          # 0 = none
    version: str        # without the epoch: deb "2.38.1-5+deb12u3", rpm "3.5.9-1.el9_9"
    arch: str
    src_name: str
    src_version: str    # deb: upstream part ("2.38.1"); rpm: VERSION
    src_release: str    # deb: Debian revision ("5+deb12u3"); rpm: RELEASE
    src_epoch: int


def available() -> bool:
    return shutil.which("trivy") is not None


# ── reading what the server printed ─────────────────────────────────────
def parse_os_release(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            out[key.strip()] = value.strip().strip('"\'')
    return out


def os_identity(os_release: str, debian_version: str = "") -> tuple[str, str, str]:
    """→ (Trivy's distro name or "", its version, the name a person reads). Debian's point release comes from
    /etc/debian_version (os-release says only "12"), as Trivy itself reads it."""
    info = parse_os_release(os_release)
    os_id = info.get("ID", "").lower()
    version = info.get("VERSION_ID", "")
    if os_id == "debian" and re.match(r"\d+\.\d+", debian_version.strip()):
        version = debian_version.strip()
    return TRIVY_OS.get(os_id, ""), version, info.get("PRETTY_NAME") or os_id or "unknown Linux"


def _split_deb(version: str) -> tuple[int, str, str]:
    """"1:2.38.1-5+deb12u3" → (1, "2.38.1", "5+deb12u3")."""
    epoch, sep, rest = version.partition(":")
    if not sep or not epoch.isdigit():
        epoch, rest = "0", version
    upstream, dash, revision = rest.rpartition("-")
    return int(epoch), (upstream if dash else rest), (revision if dash else "")


def parse_dpkg(text: str) -> list[Package]:
    """dpkg-query lines → installed packages (status "ii"/"hi"; removed-but-configured ones are skipped)."""
    out = []
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) != 6 or parts[0][1:2] != "i":      # status letter 2: i = installed
            continue
        _, name, version, arch, src_name, src_version = (p.strip() for p in parts)
        if not name or not version:
            continue
        epoch, upstream, revision = _split_deb(version)
        src_epoch, src_up, src_rev = _split_deb(src_version or version)
        out.append(Package(name, epoch, version.split(":", 1)[1] if epoch else version, arch, src_name or name,
                           src_up, src_rev, src_epoch))
    return out


_SRPM = re.compile(r"^(?P<name>.+)-(?P<version>[^-]+)-(?P<release>[^-]+)\.(?:no)?src\.rpm$")


def parse_rpm(text: str) -> list[Package]:
    """rpm -qa lines → packages; the source name comes from the .src.rpm file name (gpg-pubkey has none)."""
    out = []
    for line in text.splitlines():
        parts = [p.strip() for p in line.split("\t")]
        if len(parts) != 6 or parts[0] == "gpg-pubkey" or not parts[2]:
            continue
        name, epoch, version, release, arch, srpm = parts
        m = _SRPM.match(srpm)
        e = int(epoch) if epoch.isdigit() else 0
        out.append(Package(name, e, f"{version}-{release}", arch,
                           m["name"] if m else name, m["version"] if m else version,
                           m["release"] if m else release, e))
    return out


# ── the SBOM ─────────────────────────────────────────────────────────────
def _purl(kind: str, distro: str, os_version: str, p: Package) -> str:
    q = f"arch={quote(p.arch, safe='')}&distro={distro}-{os_version}" + (f"&epoch={p.epoch}" if p.epoch else "")
    return f"pkg:{kind}/{distro}/{quote(p.name, safe='')}@{quote(p.version, safe='')}?{q}"


def cyclonedx(distro: str, os_version: str, packages: list[Package]) -> dict:
    """A CycloneDX SBOM in the shape Trivy writes for an OS (its own properties), so `trivy sbom` reads it as
    exactly as it reads its own."""
    kind = "deb" if distro in DEB_OS else "rpm"
    os_ref = str(uuid.uuid4())
    components, refs = [], []
    for p in packages:
        full = f"{p.epoch}:{p.version}" if p.epoch else p.version
        purl = _purl(kind, distro, os_version, p)
        if purl in refs:
            continue
        props = {"PkgID": f"{p.name}@{full}" + (f".{p.arch}" if kind == "rpm" else ""), "PkgType": distro,
                 "SrcName": p.src_name, "SrcVersion": p.src_version, "SrcRelease": p.src_release}
        if p.src_epoch:
            props["SrcEpoch"] = str(p.src_epoch)
        components.append({"bom-ref": purl, "type": "library", "name": p.name, "version": full, "purl": purl,
                           "properties": [{"name": f"aquasecurity:trivy:{k}", "value": v}
                                          for k, v in props.items() if v]})
        refs.append(purl)
    components.append({"bom-ref": os_ref, "type": "operating-system", "name": distro, "version": os_version,
                       "properties": [{"name": "aquasecurity:trivy:Class", "value": "os-pkgs"},
                                      {"name": "aquasecurity:trivy:Type", "value": distro}]})
    return {"bomFormat": "CycloneDX", "specVersion": "1.6", "serialNumber": f"urn:uuid:{uuid.uuid4()}", "version": 1,
            # the top component is the server itself — not "operating-system": Trivy takes the FIRST such component as
            # the OS, and one without a version finds nothing (measured: 0 of 747)
            "metadata": {"component": {"bom-ref": "server", "type": "application", "name": "server"}},
            "components": components,
            "dependencies": [{"ref": "server", "dependsOn": [os_ref]}, {"ref": os_ref, "dependsOn": refs},
                             *({"ref": r, "dependsOn": []} for r in refs)]}


def command(sbom_path: str) -> list[str]:
    return ["trivy", "sbom", "--quiet", "--format", "json", "--scanners", "vuln", *PRIVATE_FLAGS,
            "--timeout", f"{TRIVY_TIMEOUT_SECONDS}s", sbom_path]


def check_packages(label: str, distro: str, os_version: str, packages: list[Package]) -> ImageReport:
    """The package list → Trivy's findings (worst first), as for a Docker image."""
    with tempfile.TemporaryDirectory(prefix="multeagent-sbom-") as tmp:
        path = Path(tmp) / "server.cdx.json"
        path.write_text(json.dumps(cyclonedx(distro, os_version, packages)), encoding="utf-8")
        try:
            done = subprocess.run(command(str(path)), capture_output=True, text=True,
                                  timeout=TRIVY_TIMEOUT_SECONDS + 30, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return ImageReport(label, error=f"Trivy did not finish: {exc}")
    if done.returncode != 0:
        return ImageReport(label, error=(done.stderr.strip().splitlines() or ["Trivy failed"])[-1][:200])
    report = parse_report(label, done.stdout)
    report.os = f"{distro} {os_version}"
    return report


def read_packages(run) -> tuple[str, str, str, list[Package]]:
    """`run(key) → (code, out, err)` on the server → (distro, version, readable OS name, packages)."""
    distro, version, pretty = os_identity(run("os")[1], run("debian_version")[1])
    if not distro:
        raise PackageScanError(f"Trivy cannot check {pretty} by its package list (Debian, Ubuntu, RHEL, "
                               "Rocky, Alma, CentOS, Fedora, Amazon and Oracle Linux can be)")
    code, out, err = run("dpkg" if distro in DEB_OS else "rpm")
    packages = parse_dpkg(out) if distro in DEB_OS else parse_rpm(out)
    if not packages:
        raise PackageScanError(f"the package list could not be read ({err.strip()[:80] or f'exit {code}'})")
    return distro, version, pretty, packages[:PACKAGES_MAX]


# ── what the chat shows: one row per source package, CVEs counted once ───
_KERNEL_IN_NAME = re.compile(r"(\d+\.\d+\.\d+-\d+)")
SEVERITY_ORDER = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN")


@dataclass
class PackageGroup:
    """One source package ("openssl", "linux") and its CVEs. A CVE in 12 kernel packages counts once here; every
    package's row is in the Excel file."""
    source: str
    installed: str
    worst: str                 # worst severity among the CVEs with a fix ("" = none has a fix)
    cves: set[str]
    fixable: set[str]
    serious_fixable: set[str]  # critical / high with a fix
    fixed: str                 # the version that fixes them ("" = no fix yet)
    old_kernel: bool           # an installed kernel that is not the one running: removing it is the fix


def _is_old_kernel(name: str, running_kernel: str) -> bool:
    m = _KERNEL_IN_NAME.search(name)
    return bool(m and running_kernel and not running_kernel.startswith(m.group(1)))


def group(report: ImageReport, packages: list[Package], running_kernel: str = "") -> list[PackageGroup]:
    """Trivy's rows (one per CVE and binary package) → one group per source package, worst first."""
    source_of = {p.name: p.src_name for p in packages}
    groups: dict[tuple[str, bool], PackageGroup] = {}
    for v in report.vulns:
        old = _is_old_kernel(v.package, running_kernel)
        key = (source_of.get(v.package, v.package), old)
        g = groups.setdefault(key, PackageGroup(key[0], v.installed, "", set(), set(), set(), "", old))
        g.cves.add(v.id)
        if v.fixed:
            g.fixable.add(v.id)
            g.fixed = g.fixed or v.fixed
            if not g.worst or SEVERITY_ORDER.index(v.severity) < SEVERITY_ORDER.index(g.worst):
                g.worst = v.severity
            if v.severity in ("CRITICAL", "HIGH"):
                g.serious_fixable.add(v.id)
    return sorted(groups.values(), key=lambda g: (g.old_kernel, SEVERITY_ORDER.index(g.worst) if g.worst else 9,
                                                  -len(g.serious_fixable), -len(g.fixable), g.source))


def upgrade_command(distro: str) -> str:
    return "sudo apt update && sudo apt upgrade" if distro in DEB_OS else "sudo dnf upgrade"


def old_kernel_command(distro: str) -> str:
    return "sudo apt autoremove --purge" if distro in DEB_OS else "sudo dnf remove --oldinstallonly"
