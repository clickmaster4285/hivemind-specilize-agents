"""The diagram tool's network path: detect a network-diagram (HLD) request and turn a JSON-only model reply
into a validated ```netdiagram spec — either the hub-spoke shape (sites+hq) or a generic topology graph
(nodes+links), rendered by NetworkDiagram.tsx — with None on bad input."""

from __future__ import annotations

import json

import pytest

from src.tools.diagram_gen import _is_network_diagram, generate_netdiagram

HUB = {
    "title": "10 Sites to HQ",
    "cloud": "Internet",
    "hq": {"label": "HQ", "nodes": [{"icon": "firewall", "label": "FW"}]},
    "sites": [{"label": "Site 1", "sub": "Branch"}, {"label": "Site 2", "sub": "Branch"}],
}
GRAPH = {
    "title": "Campus Network",
    "nodes": [{"id": "net", "icon": "internet", "label": "Internet", "tier": 0},
              {"id": "fw", "icon": "firewall", "label": "Firewall", "tier": 1}],
    "links": [{"from": "net", "to": "fw", "kind": "internet"}],
}


@pytest.mark.parametrize("q", [
    "i have 10 sites and they have to connect to the ho create me a network diagram",
    "draw a network diagram for 8 branches connecting to head office",
    "network diagram: 5 offices to HQ",
    "create a campus network diagram with core distribution access",
    "draw the network topology for our data center",
    "show me a DMZ network diagram",
    "home network diagram",
])
def test_detects_network_diagram(q):
    assert _is_network_diagram(q) is True


@pytest.mark.parametrize("q", [
    "draw a flowchart of the boot process",
    "architecture diagram of a web app",
    "give me the LLD with VLAN and IP addressing plan for 10 sites",  # LLD → tables, not netdiagram
    "subnet plan for the branches",
])
def test_rejects_non_network(q):
    assert _is_network_diagram(q) is False


def test_generate_parses_hubspoke_json():
    out = generate_netdiagram("10 sites to HQ", lambda **kw: json.dumps(HUB))
    assert out is not None
    assert json.loads(out)["sites"][0]["label"] == "Site 1"


def test_generate_parses_graph_json():
    out = generate_netdiagram("campus network", lambda **kw: json.dumps(GRAPH))
    assert out is not None
    assert json.loads(out)["nodes"][0]["id"] == "net"


def test_generate_extracts_json_from_surrounding_prose():
    chat = lambda **kw: "Here you go:\n```json\n" + json.dumps(HUB) + "\n```\nHope it helps"  # noqa: E731
    assert generate_netdiagram("10 sites", chat) is not None


def test_generate_returns_none_on_garbage():
    assert generate_netdiagram("x", lambda **kw: "sorry, I can't") is None


def test_generate_returns_none_when_empty_hubspoke():
    bad = {**HUB, "sites": []}
    assert generate_netdiagram("x", lambda **kw: json.dumps(bad)) is None


def test_generate_returns_none_when_graph_too_small():
    bad = {"title": "x", "nodes": [{"id": "a", "label": "A"}], "links": []}
    assert generate_netdiagram("x", lambda **kw: json.dumps(bad)) is None
