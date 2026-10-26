"""Gate-closed lines under Gemma's candidates (Gemma 2026-10-25, choice A)."""

import asyncio
from types import SimpleNamespace

from scanner.execution import satellite_feed as sf

MID_SHUT = sf.Gates(open={"large": True, "mid": False, "small": True},
                    vs_average={"large": 5.0, "mid": -0.4, "small": 1.5})


def test_open_gate_gives_no_line():
    assert sf.gate_tag("large", MID_SHUT) is None


def test_closed_gate_line_names_the_gate_and_the_distance():
    line = sf.gate_tag("mid", MID_SHUT)
    assert "GATE CLOSED" in line and "MDY" in line and "-0.4%" in line


def test_unknown_gate_counts_as_closed():
    g = sf.Gates(open={"large": True, "mid": None, "small": True}, vs_average={"mid": None})
    assert "no data" in sf.gate_tag("mid", g)


def test_tags_only_the_names_behind_a_closed_gate(monkeypatch):
    members = [SimpleNamespace(symbol="OKTA", index="S&P MidCap 400"),
               SimpleNamespace(symbol="CRWD", index="S&P 500"),
               SimpleNamespace(symbol="SMCI", index="S&P SmallCap 600")]

    async def load(client):
        return members

    monkeypatch.setattr(sf.composite, "load_universe", load)
    tags = asyncio.run(sf.gate_tags(None, ["OKTA", "CRWD", "SMCI", "XYZ"], MID_SHUT))
    assert list(tags) == ["OKTA"]


def test_all_gates_open_skips_the_index_lookup(monkeypatch):
    async def boom(client):
        raise AssertionError("not needed")

    monkeypatch.setattr(sf.composite, "load_universe", boom)
    g = sf.Gates(open={"large": True, "mid": True, "small": True}, vs_average={})
    assert asyncio.run(sf.gate_tags(None, ["OKTA"], g)) == {}
