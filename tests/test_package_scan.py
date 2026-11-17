"""V3b package_scan (doc/SECURITY_TOOLS_DECISION.md §13): a server's package list → a CycloneDX SBOM in Trivy's own
shape → findings grouped per source package. Offline: Trivy is never run here (the accuracy against Trivy's own
scans was measured by hand: §13)."""

from __future__ import annotations

import pytest

from src.servers import package_scan as ps
from src.servers.image_scan import ImageReport, Vuln

DPKG = (
    "ii \tbsdutils\t1:2.38.1-5+deb12u3\tamd64\tutil-linux\t2.38.1-5+deb12u3\n"
    "ii \tlibssl3\t3.0.17-1~deb12u2\tamd64\topenssl\t3.0.17-1~deb12u2\n"
    "ii \tbash\t5.2.15-2+b8\tamd64\t\t\n"                       # source = itself
    "rc \told-removed\t1.0-1\tamd64\told\t1.0-1\n"               # removed, config left: not installed
    "ii \tlinux-headers-7.0.0-42-generic\t7.0.0-42.42\tamd64\tlinux\t7.0.0-42.42\n"
)
RPM = (
    "openssl-libs\t1\t3.5.9\t1.el9_9\tx86_64\topenssl-3.5.9-1.el9_9.src.rpm\n"
    "bash\t0\t5.1.8\t9.el9\tx86_64\tbash-5.1.8-9.el9.src.rpm\n"
    "gpg-pubkey\t0\tabc\tdef\t(none)\t(none)\n"
)


def test_dpkg_lines_keep_epoch_and_source_package():
    pkgs = ps.parse_dpkg(DPKG)
    assert [p.name for p in pkgs] == ["bsdutils", "libssl3", "bash", "linux-headers-7.0.0-42-generic"]
    b = pkgs[0]
    assert (b.epoch, b.version, b.src_name, b.src_version, b.src_release) == (
        1, "2.38.1-5+deb12u3", "util-linux", "2.38.1", "5+deb12u3")
    assert (pkgs[2].src_name, pkgs[2].src_version, pkgs[2].src_release) == ("bash", "5.2.15", "2+b8")


def test_rpm_lines_take_the_source_from_the_src_rpm():
    pkgs = ps.parse_rpm(RPM)
    assert [p.name for p in pkgs] == ["openssl-libs", "bash"]
    o = pkgs[0]
    assert (o.epoch, o.version, o.src_name, o.src_version, o.src_release, o.src_epoch) == (
        1, "3.5.9-1.el9_9", "openssl", "3.5.9", "1.el9_9", 1)


def test_the_os_as_trivy_names_it():
    debian = 'PRETTY_NAME="Debian GNU/Linux 12 (bookworm)"\nID=debian\nVERSION_ID="12"\n'
    assert ps.os_identity(debian, "12.11\n") == ("debian", "12.11", "Debian GNU/Linux 12 (bookworm)")
    assert ps.os_identity('ID=almalinux\nVERSION_ID="9.2"\n')[:2] == ("alma", "9.2")
    assert ps.os_identity("ID=ubuntu\nVERSION_ID=\"24.04\"\n", "trixie/sid")[:2] == ("ubuntu", "24.04")
    assert ps.os_identity('ID=arch\nPRETTY_NAME="Arch Linux"\n')[0] == ""


def test_sbom_has_one_os_component_and_trivys_own_properties():
    # measured: a second "operating-system" component (the top one) made Trivy find 0 of 747 CVEs
    bom = ps.cyclonedx("debian", "12.11", ps.parse_dpkg(DPKG))
    oses = [c for c in bom["components"] if c["type"] == "operating-system"]
    assert [(c["name"], c["version"]) for c in oses] == [("debian", "12.11")]
    assert bom["metadata"]["component"]["type"] != "operating-system"
    b = next(c for c in bom["components"] if c["name"] == "bsdutils")
    assert b["version"] == "1:2.38.1-5+deb12u3"
    assert b["purl"] == "pkg:deb/debian/bsdutils@2.38.1-5%2Bdeb12u3?arch=amd64&distro=debian-12.11&epoch=1"
    props = {p["name"].split(":")[-1]: p["value"] for p in b["properties"]}
    # as Trivy writes it: the epoch is the binary package's; its source (util-linux 2.38.1-5+deb12u3) has none
    assert props == {"PkgID": "bsdutils@1:2.38.1-5+deb12u3", "PkgType": "debian", "SrcName": "util-linux",
                     "SrcVersion": "2.38.1", "SrcRelease": "5+deb12u3"}
    os_ref = oses[0]["bom-ref"]
    deps = {d["ref"]: d["dependsOn"] for d in bom["dependencies"]}
    assert len(deps[os_ref]) == 4 and b["bom-ref"] in deps[os_ref]
    rpm = ps.cyclonedx("alma", "9.2", ps.parse_rpm(RPM))
    o = next(c for c in rpm["components"] if c["name"] == "openssl-libs")
    assert o["purl"].startswith("pkg:rpm/alma/openssl-libs@3.5.9-1.el9_9?arch=x86_64&distro=alma-9.2&epoch=1")
    assert {p["name"].split(":")[-1]: p["value"] for p in o["properties"]}["PkgID"] == "openssl-libs@1:3.5.9-1.el9_9.x86_64"


def test_trivy_runs_without_sending_anything():
    cmd = ps.command("/tmp/x.json")
    assert cmd[:2] == ["trivy", "sbom"]
    assert {"--disable-telemetry", "--skip-version-check", "--offline-scan"} <= set(cmd)


def test_read_packages_uses_dpkg_or_rpm_and_refuses_unknown_systems():
    answers = {"os": (0, 'ID=debian\nVERSION_ID="12"\nPRETTY_NAME="Debian 12"\n', ""),
               "debian_version": (0, "12.11\n", ""), "dpkg": (0, DPKG, ""), "rpm": (127, "", "not found")}
    asked = []

    def run(key):
        asked.append(key)
        return answers[key]

    distro, version, pretty, pkgs = ps.read_packages(run)
    assert (distro, version, len(pkgs)) == ("debian", "12.11", 4) and "rpm" not in asked
    answers["os"] = (0, 'ID=arch\nPRETTY_NAME="Arch Linux"\n', "")
    with pytest.raises(ps.PackageScanError, match="Arch Linux"):
        ps.read_packages(run)
    answers["os"] = (0, 'ID=ubuntu\nVERSION_ID="24.04"\n', "")
    answers["dpkg"] = (1, "", "dpkg-query: not found")
    with pytest.raises(ps.PackageScanError, match="could not be read"):
        ps.read_packages(run)


def test_rows_are_grouped_per_source_package_and_old_kernels_apart():
    pkgs = ps.parse_dpkg(DPKG + "ii \tlibssl-dev\t3.0.17-1~deb12u2\tamd64\topenssl\t3.0.17-1~deb12u2\n")
    report = ImageReport("web01", vulns=[
        Vuln("CVE-1", "libssl3", "3.0.17-1~deb12u2", "3.0.17-1~deb12u3", "HIGH", ""),
        Vuln("CVE-1", "libssl-dev", "3.0.17-1~deb12u2", "3.0.17-1~deb12u3", "HIGH", ""),   # same CVE, same source
        Vuln("CVE-2", "libssl3", "3.0.17-1~deb12u2", "", "CRITICAL", ""),                  # no fix yet
        Vuln("CVE-3", "bash", "5.2.15-2+b8", "5.2.15-3", "MEDIUM", ""),
        Vuln("CVE-4", "linux-headers-7.0.0-42-generic", "7.0.0-42.42", "7.0.0-43.43", "CRITICAL", ""),
    ])
    groups = ps.group(report, pkgs, running_kernel="7.0.0-43-generic")
    assert [(g.source, g.old_kernel) for g in groups] == [("openssl", False), ("bash", False), ("linux", True)]
    ssl = groups[0]
    assert (ssl.cves, ssl.fixable, ssl.serious_fixable, ssl.worst, ssl.fixed) == (
        {"CVE-1", "CVE-2"}, {"CVE-1"}, {"CVE-1"}, "HIGH", "3.0.17-1~deb12u3")
    # the kernel that runs is not "old"
    assert not ps.group(report, pkgs, running_kernel="7.0.0-42-generic")[-1].old_kernel
