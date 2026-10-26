"""BUY CHECKS lines under Gemma's candidates (Gemma 2026-10-25, choice A2)."""

import asyncio

from scanner.execution import satellite_feed as sf

G = sf.Gates(open={"large": True, "mid": False, "small": True}, vs_average={})


def test_lines_pass_or_fail_with_the_reason(monkeypatch):
    async def refusals(client, buys, held, g, audit_path, today=None, cumulative=True):
        assert cumulative is False and held == ["ZBRA"]
        return {"OKTA": "mid caps (gate 2, MDY) gate closed"}

    monkeypatch.setattr(sf, "refusals", refusals)
    lines = asyncio.run(sf.check_lines(None, ["OKTA", "CRWD"], ["ZBRA"], G, "audit.jsonl"))
    assert lines == {"OKTA": "*** BUY CHECKS: FAIL - mid caps (gate 2, MDY) gate closed ***",
                     "CRWD": "BUY CHECKS: PASS"}
