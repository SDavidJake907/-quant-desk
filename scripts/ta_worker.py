"""Run TradingAgents on one ticker. Runs in the TradingAgents venv, not ours.

    <TradingAgents>/.venv/Scripts/python.exe scripts/ta_worker.py < request.json

Request (stdin, JSON):
    {"ticker": "SNPS", "date": "2026-10-05", "model": "gemma4:12b",
     "deadline": "2026-10-05T06:32:00-08:00",
     "report_dir": "logs/tradingagents/2026-10-05",
     "portfolio": {"cash": 1000.0, "currency": "USD",
                   "positions": [{"ticker": "AON", "quantity": 3, "average_price": 350.0}]}}

Prints exactly one JSON object as its LAST line; anything before it is log.
Imports nothing from `scanner`: the two venvs share no packages, and that is
the point -- TradingAgents' langgraph/pandas 3 never reach the trading path.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

#: Market + news only: fundamentals and social cost several more agent turns
#: each on a local 12B, and the strategy holds for days, not quarters.
ANALYSTS = ["market", "news"]

#: Report fields saved beside the rating, for reading the reasoning later.
FIELDS = ("market_report", "news_report", "investment_plan",
          "trader_investment_plan", "final_trade_decision")

#: "Action: Buy" from structured output, "**Action**: Buy" from the free-text
#: fallback. Both name the trader's direction.
#: Must equal scanner.advisor.tradingagents.DEADLINE_EXIT; this file cannot
#: import it (different venv). The driver reads 3 as "paused", not "failed".
DEADLINE_EXIT = 3

#: Resolved decisions kept in TradingAgents' memory log (default: unlimited).
MEMORY_ENTRIES = 200

_ACTION = re.compile(r"\**Action\**:\s*\**\s*(Buy|Hold|Sell)\b", re.IGNORECASE)

#: The risk team (three debaters) and the Portfolio Manager changed the
#: trader's call in 0 of 196 runs (2026-10-05..08, WORKLIST §53) and cost about
#: 4 of every ~10 model turns. The run now ends at the trader; its Buy/Hold/
#: Sell is the rating, which is all the grade reads (a direction).
RISK_STAGE = ("Aggressive Analyst", "Neutral Analyst", "Conservative Analyst",
              "Portfolio Manager")


def _trader_decision(state: dict) -> dict:
    """End of the shortened run: the trader's own direction becomes the rating."""
    plan = str(state.get("trader_investment_plan") or "")
    found = _ACTION.search(plan) or re.search(
        r"FINAL TRANSACTION PROPOSAL:\s*\**\s*(BUY|HOLD|SELL)", plan, re.IGNORECASE)
    rating = found.group(1).title() if found else "REVIEW"
    return {"final_trade_decision": f"Rating: {rating}\n\n{plan}", "final_rating": rating}


def _stop_at_trader() -> None:
    """Rewire TradingAgents' graph, in this process only: Trader -> decision -> END.

    Raises if the graph no longer has the Trader -> risk-team edge, so an
    upgrade that reshapes it fails loudly instead of running the full chain.
    """
    from langgraph.graph import END
    from tradingagents.graph import setup as ta_setup

    original = ta_setup.GraphSetup.setup_graph

    def setup_graph(self, *args, **kwargs):
        workflow = original(self, *args, **kwargs)
        if ("Trader", "Aggressive Analyst") not in workflow.edges:
            raise RuntimeError("TradingAgents graph changed: re-check the trader-only cut")
        workflow.edges = {e for e in workflow.edges
                          if e[0] not in RISK_STAGE and e != ("Trader", "Aggressive Analyst")}
        for node in RISK_STAGE:
            workflow.nodes.pop(node, None)
            workflow.branches.pop(node, None)
        workflow.add_node("Trader Decision", _trader_decision)
        workflow.add_edge("Trader", "Trader Decision")
        workflow.add_edge("Trader Decision", END)
        return workflow

    ta_setup.GraphSetup.setup_graph = setup_graph


def _thinking_off() -> None:
    """Send reasoning_effort="none" to Ollama, in this process only.

    Gemma 4 thinks by default, and through TradingAgents its structured
    outputs (Research Manager, Trader) came back unparsed and fell to free
    text. Ollama's /v1 honours reasoning_effort="none" (2 tokens vs 41 on a
    one-word reply, 2026-10-04), but TradingAgents forwards that field only
    for native OpenAI models. Patched here rather than in its install, so an
    upgrade of TradingAgents cannot silently carry or lose the change.
    """
    try:
        from tradingagents.llm_clients import factory, openai_client
        original = factory.build_llm_kwargs
        openai_client._supports_reasoning_effort  # noqa: B018 -- must exist
    except (ImportError, AttributeError) as exc:
        print(f"thinking stays on, TradingAgents changed shape: {exc}")
        return

    def build(config):
        kwargs = original(config)
        if str(config.get("llm_provider", "")).lower() == "ollama":
            kwargs = {**kwargs, "reasoning_effort": "none"}
        return kwargs

    factory.build_llm_kwargs = build
    openai_client._supports_reasoning_effort = lambda model: True


def _structured_json_schema() -> None:
    """Ask Ollama for structured output by JSON schema, not function calling.

    Every run on 2026-10-05 logged "structured-output invocation failed" for
    the Research Manager, Trader and Portfolio Manager and fell back to free
    text, so each rating was regex-parsed from prose. Probed 2026-10-06 in
    this venv on gemma4:12b: function calling parses a short prompt but the
    long agent prompts drift into prose; method="json_schema" uses Ollama's
    constrained decoding, so the reply cannot fail to parse (7.6s vs 15.0s on
    the probe). Patched here, like _thinking_off, so a TradingAgents upgrade
    cannot silently carry or lose it.
    """
    try:
        from tradingagents.llm_clients import openai_client
        local = openai_client.LocalCompatibleChatOpenAI
        original = local.with_structured_output
    except (ImportError, AttributeError) as exc:
        print(f"structured output stays on function calling, TradingAgents changed shape: {exc}")
        return

    def with_structured_output(self, schema, *, method=None, **kwargs):
        return original(self, schema, method=method or "json_schema", **kwargs)

    local.with_structured_output = with_structured_output


def _exit_at(deadline_iso: str | None) -> None:
    """Hard-exit at the deadline, whatever the parent is doing.

    If Task Scheduler kills the driver at its time limit, this process is
    orphaned and would keep the GPU into the next scan. os._exit closes the
    socket mid-request, and Ollama abandons a generation whose client is gone.
    The checkpoint keeps every finished step for the next pass.
    """
    if not deadline_iso:
        return
    from datetime import datetime
    seconds = (datetime.fromisoformat(deadline_iso)
               - datetime.now().astimezone()).total_seconds()
    timer = threading.Timer(max(seconds, 0.0), lambda: (
        print(f"deadline {deadline_iso} reached, exiting", flush=True), os._exit(DEADLINE_EXIT)))
    timer.daemon = True
    timer.start()


def _portfolio(raw):
    if raw is None:
        return None
    from tradingagents.portfolio import PortfolioContext
    return PortfolioContext.model_validate(raw)


def main() -> int:
    request = json.loads(sys.stdin.read())
    _exit_at(request.get("deadline"))
    ticker = str(request["ticker"]).upper()
    model = str(request["model"])

    from tradingagents.default_config import DEFAULT_CONFIG
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    _thinking_off()
    _structured_json_schema()
    _stop_at_trader()
    config = DEFAULT_CONFIG.copy()
    # checkpoint_enabled is what makes this run in pieces: every finished
    # agent step is saved, so a run stopped for a scan resumes at the next
    # pass from the last finished step instead of starting over. A completed
    # run clears its own checkpoint.
    config.update(llm_provider="ollama", quick_think_llm=model, deep_think_llm=model,
                  max_debate_rounds=1, max_risk_discuss_rounds=1,
                  checkpoint_enabled=True,
                  # The log only ever feeds 5 same-ticker + 3 cross-ticker
                  # entries into a prompt; this bounds the file on disk.
                  memory_log_max_entries=MEMORY_ENTRIES)
    started = time.monotonic()
    graph = TradingAgentsGraph(debug=False, config=config, selected_analysts=ANALYSTS)
    state, rating = graph.propagate(ticker, request["date"],
                                    portfolio=_portfolio(request.get("portfolio")))

    action = _ACTION.search(str(state.get("trader_investment_plan") or ""))
    report = Path(request["report_dir"]) / f"{ticker}.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({k: state.get(k) for k in FIELDS} | {"rating": rating},
                                 indent=2, default=str), encoding="utf-8")

    print(json.dumps({"ticker": ticker, "rating": str(rating),
                      "trader": action.group(1).title() if action else "",
                      "seconds": round(time.monotonic() - started, 1),
                      "report": report.as_posix(), "model": model}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
