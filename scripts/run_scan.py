"""Demo entry point:  python scripts/run_scan.py NVDA AAPL

Prints the funnel at each stage so you can see where the noise dies.
"""

from __future__ import annotations

import asyncio
import sys

import httpx

from scanner.pipeline import load_name_index, scan_ticker


async def main(tickers: list[str]) -> None:
    async with httpx.AsyncClient() as client:
        names = await load_name_index(client)  # built once, reused per ticker
        for ticker in tickers:
            report = await scan_ticker(ticker, client, names=names)
            if report is None:
                continue

            print(f"\n=== {report.ticker} — {report.company} ===")
            print(
                f"funnel:  fetched {report.fetched}"
                f"  ->  named {report.after_prefilter}"
                f"  ->  not-roundup {report.after_roundup}"
                f"  ->  deduped {report.after_dedup}"
                f"  ->  kept {report.kept}"
                f"   ({report.noise_ratio:.0%} discarded)"
            )

            if report.filings:
                print(f"\n  SEC 8-K filings (last 30d) — no model involved:")
                for filing in report.filings:
                    stamp = (
                        filing.published_at.date().isoformat()
                        if filing.published_at
                        else "?"
                    )
                    print(f"    {stamp}  {filing.summary}")

            if not report.items:
                print("\n  (no news survived triage)")
                continue

            print(f"\n  News that passed all gates:")
            for triaged in report.items:
                v = triaged.verdict
                print(f"\n    [{v.category}/{v.direction}] {triaged.item.title}")
                print(f'      evidence: "{v.evidence_quote}"')
                if triaged.item.url:
                    print(f"      {triaged.item.url}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:] or ["NVDA", "AAPL"]))
