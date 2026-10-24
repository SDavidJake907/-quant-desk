"""Gemma's tool calling: the loop (llm/tool_loop.py), the tools (advisor/stock_tools.py), the map."""

import asyncio

from scanner.advisor import stock_tools
from scanner.llm import tool_loop
from scanner.ops import market_map as mm


def run(coro):
    return asyncio.run(coro)


def fake_chat(script):
    """A chat function that replays `script` (one message per call) and records what it was sent."""
    sent = []

    async def chat(client, messages, tools, max_tokens, model):
        sent.append({"messages": list(messages), "tools": tools})
        return script[min(len(sent) - 1, len(script) - 1)]
    return chat, sent


def call(name, **args):
    return {"content": "", "tool_calls": [{"function": {"name": name, "arguments": args}}]}


def test_loop_runs_tools_then_returns_the_answer():
    chat, sent = fake_chat([call("get_quote", symbol="NVDA"), {"content": "NVDA is down 4%."}])
    seen = []

    async def execute(name, args):
        seen.append((name, args))
        return "NVDA: last 180.00, -4.4%"
    text, calls = run(tool_loop.run("q", stock_tools.TOOLS, execute, None, chat=chat))
    assert text == "NVDA is down 4%." and seen == [("get_quote", {"symbol": "NVDA"})]
    assert calls[0]["result"].startswith("NVDA") and sent[1]["messages"][-1]["role"] == "tool"


def test_loop_stops_at_the_lookup_budget_and_asks_for_notes():
    chat, sent = fake_chat([call("market_map")] * 2 + [{"content": ""}, {"content": "notes"}])

    async def execute(name, args):
        return "map"
    text, calls = run(tool_loop.run("q", stock_tools.TOOLS, execute, None, max_calls=2, chat=chat))
    assert len(calls) == 2 and text == "notes"
    assert sent[2]["tools"] is None                        # no tools offered once the budget is spent


def test_tools_are_read_only_and_well_formed():
    names = {t["function"]["name"] for t in stock_tools.TOOLS}
    assert names == set(stock_tools.HANDLERS)
    assert not any(w in n for n in names for w in ("buy", "sell", "order", "cancel", "submit"))
    for t in stock_tools.TOOLS:
        assert t["type"] == "function" and t["function"]["parameters"]["type"] == "object"


def test_execute_never_raises():
    out = run(stock_tools.execute("place_order", {"symbol": "NVDA"}, client=None, broker=None))
    assert out.startswith("Unknown tool")
    out = run(stock_tools.execute("get_quote", {}, client=None, broker=None))
    assert "bad arguments" in out or "failed" in out


def test_market_map_reads_rotation_and_dollars():
    rows = [mm.Row(symbol="BIG", sector="Information Technology", pct=-5.0, cap=4e12),
            *[mm.Row(symbol=f"S{i}", sector="Consumer Staples", pct=1.0, cap=1e11) for i in range(9)]]
    m = mm.Map(rows=tuple(rows))
    assert round(m.total_dollars / 1e9) == -191 and m.up == 9 and m.rotation()
    text = mm.text(m, held=["BIG"])
    assert "ROTATION" in text and "BIG -5.0% (-$200B" in text and "Your holdings: BIG" in text
    assert "Consumer Staples" in mm.sector_text(m, "consumer staples")


def test_stock_story_joins_chart_news_and_earnings_and_survives_a_failure(monkeypatch):
    async def chart(client, broker, symbol):
        return f"{symbol} chart"

    async def news(client, broker, symbol):
        raise RuntimeError("down")

    async def earnings(client, broker, symbol):
        return f"{symbol} reports soon"
    monkeypatch.setattr(stock_tools, "get_chart", chart)
    monkeypatch.setattr(stock_tools, "get_news", news)
    monkeypatch.setattr(stock_tools, "get_earnings", earnings)
    out = run(stock_tools.execute("stock_story", {"symbol": "MU"}, client=None, broker=None))
    assert out == "MU chart\nnews unavailable (RuntimeError)\nMU reports soon"
