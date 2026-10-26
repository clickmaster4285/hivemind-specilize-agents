"""ping_host (src/host_check.py): ping output, typos, target choice, the answer (no network needed)."""

from __future__ import annotations

import pytest

from src.servers import host_check as hc
from src.servers.net_scan import ScanError

WINDOWS_UP = """Pinging 192.168.88.1 with 32 bytes of data:
Reply from 192.168.88.1: bytes=32 time=1ms TTL=255
Reply from 192.168.88.1: bytes=32 time<1ms TTL=255
Request timed out.
Reply from 192.168.88.1: bytes=32 time=3ms TTL=255

Ping statistics for 192.168.88.1:
    Packets: Sent = 4, Received = 3, Lost = 1 (25% loss),"""

WINDOWS_UNREACHABLE = """Reply from 192.168.88.36: Destination host unreachable.
Request timed out."""

LINUX_UP = """64 bytes from 10.0.0.5: icmp_seq=1 ttl=64 time=0.412 ms
64 bytes from 10.0.0.5: icmp_seq=2 ttl=64 time=0.388 ms"""


def test_parse_windows_ping():
    r = hc.parse_ping(WINDOWS_UP, 4)
    assert r.times_ms == [1.0, 1.0, 3.0] and r.ttl == 255
    assert (r.received, r.loss_percent) == (3, 25)


def test_unreachable_is_not_a_reply():
    # Windows prints "Reply from …: Destination host unreachable" — no time, no TTL: not an answer
    assert hc.parse_ping(WINDOWS_UNREACHABLE, 4).received == 0


def test_parse_linux_ping():
    r = hc.parse_ping(LINUX_UP, 2)
    assert r.times_ms == [0.412, 0.388] and r.ttl == 64 and r.loss_percent == 0


def test_the_typo_from_the_chat_is_refused_with_a_suggestion():
    with pytest.raises(ScanError, match=r"Did you mean \*\*192\.168\.88\.18\*\*"):
        hc.resolve("192.16.88.18")
    with pytest.raises(ScanError, match="not a private"):
        hc.resolve("8.8.8.8")
    assert hc.resolve("192.168.88.18") == "192.168.88.18"


def test_the_address_as_typed_wins_over_the_models_silent_correction():
    assert hc.pick_target("ping 192.16.88.18 and check it is running ?", "192.168.88.18") == "192.16.88.18"
    assert hc.pick_target("is the nas up?", "nas01") == "nas01"


@pytest.mark.parametrize("ttl, hint", [(64, "Linux"), (128, "Windows"), (255, "router"), (None, "")])
def test_os_hint(ttl, hint):
    assert hint in hc.os_hint(ttl)


def _report(times=(), ports=()):
    return hc.HostReport(target="x", ip="192.168.88.66",
                         ping=hc.PingResult(sent=4, times_ms=list(times), ttl=128 if times else None),
                         name="KAMRAN", mac="aa:bb:cc:dd:ee:ff", vendor="Dell Inc.", open_ports=list(ports), seconds=3.2)


def test_answers_for_up_blocked_and_down():
    up = hc.markdown(_report(times=(1, 2, 3, 2), ports=(3389,)))
    assert up.startswith("**192.168.88.66 is up** — 4/4 replies, 0% loss, 2 ms average") and "likely Windows" in up
    assert "| Name | KAMRAN |" in up and "3389 RDP" in up
    blocked = hc.markdown(_report(ports=(445,)))
    assert "is up, but does not answer ping" in blocked
    down = hc.markdown(_report())
    assert "does not answer" in down and not _report().up


def test_registered_for_the_it_specialist_and_not_admin_only():
    from src.jobs import worker
    from src.llm.assistant import BY_NAME
    from src.llm.specialists import default_specialists

    assert BY_NAME["ping_host"].intent == "host_check" and worker.DIRECT_TOOLS["host_check"] is hc.run_host_check
    assert "host_check" not in worker.ADMIN_TOOLS                   # a ping is harmless, like network_scan
    by_id = {s.id: s for s in default_specialists()}
    assert "ping_host" in by_id["it"].tools and "ping_host" not in by_id["marketing"].tools


def test_chat_job_reports_the_typo():
    from dataclasses import replace

    from src.core.config import Config

    out, errors, _ = hc.run_host_check("ping 192.16.88.18 and check it is running ?", job_id="j",
                                       config=replace(Config.from_env(), network_scan_enabled=True),
                                       host="192.168.88.18")
    assert out.startswith("Not checked:") and "Did you mean" in out and errors
