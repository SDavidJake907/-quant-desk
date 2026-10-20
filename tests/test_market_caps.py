import asyncio
import json
from datetime import date

from scanner.sources import market_caps as mc


def test_parse_reads_caps_and_skips_bad_rows():
    rows = [{"symbol": "NVDA", "marketCap": "5160533000000.00"},
            {"symbol": "BRK/B", "marketCap": "1,000,000,000,000"},
            {"symbol": "ZERO", "marketCap": "0.00"},
            {"symbol": "NA", "marketCap": "NA"},
            {"symbol": "", "marketCap": "5"}]
    assert mc.parse(rows) == {"NVDA": 5.160533e12, "BRK-B": 1e12}


def test_normalize_share_classes():
    assert mc.normalize("BF^B") == "BF-B"
    assert mc.normalize(" brk/a ") == "BRK-A"


def test_load_uses_todays_cache_without_a_request(tmp_path):
    (tmp_path / "2026-10-19.json").write_text(json.dumps({"MU": 9.9e11}), encoding="utf-8")

    class NoNetwork:
        async def get(self, *a, **k):
            raise AssertionError("should read the cache")

    caps = asyncio.run(mc.load(NoNetwork(), today=date(2026, 10, 19), cache_dir=tmp_path))
    assert caps == {"MU": 9.9e11}


def test_load_returns_empty_when_nasdaq_fails(tmp_path):
    import httpx

    class Down:
        async def get(self, *a, **k):
            raise httpx.ConnectError("down")

    assert asyncio.run(mc.load(Down(), today=date(2026, 10, 19), cache_dir=tmp_path)) == {}
