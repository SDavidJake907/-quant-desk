"""The global index universe: (symbol, display name, region, country)."""

from __future__ import annotations

INDICES: tuple[tuple[str, str, str, str], ...] = (
    # --- Asia-Pacific ---
    ("^N225", "Nikkei 225", "Asia-Pacific", "Japan"),
    # TOPIX deliberately omitted: ^TOPX resolves to an unrelated US-listed
    # instrument (~105, America/New_York) and 998405.T 404s. A wrong number is
    # worse than a missing one.
    ("^HSI", "Hang Seng", "Asia-Pacific", "Hong Kong"),
    ("000001.SS", "Shanghai Composite", "Asia-Pacific", "China"),
    ("399001.SZ", "Shenzhen Component", "Asia-Pacific", "China"),
    ("^KS11", "KOSPI", "Asia-Pacific", "South Korea"),
    ("^TWII", "TAIEX", "Asia-Pacific", "Taiwan"),
    ("^AXJO", "ASX 200", "Asia-Pacific", "Australia"),
    ("^BSESN", "BSE Sensex", "Asia-Pacific", "India"),
    ("^NSEI", "Nifty 50", "Asia-Pacific", "India"),
    ("^STI", "Straits Times", "Asia-Pacific", "Singapore"),
    # --- Europe ---
    ("^FTSE", "FTSE 100", "Europe", "United Kingdom"),
    ("^FTMC", "FTSE 250", "Europe", "United Kingdom"),
    ("^GDAXI", "DAX", "Europe", "Germany"),
    ("^FCHI", "CAC 40", "Europe", "France"),
    ("^STOXX50E", "Euro Stoxx 50", "Europe", "Eurozone"),
    ("^STOXX", "STOXX Europe 600", "Europe", "Europe"),
    ("^IBEX", "IBEX 35", "Europe", "Spain"),
    ("FTSEMIB.MI", "FTSE MIB", "Europe", "Italy"),
    ("^SSMI", "SMI", "Europe", "Switzerland"),
    ("^AEX", "AEX", "Europe", "Netherlands"),
    # --- Americas ---
    ("^GSPC", "S&P 500", "Americas", "United States"),
    ("^DJI", "Dow Jones", "Americas", "United States"),
    ("^IXIC", "Nasdaq Composite", "Americas", "United States"),
    ("^NDX", "Nasdaq 100", "Americas", "United States"),
    ("^RUT", "Russell 2000", "Americas", "United States"),
    ("^VIX", "VIX (volatility)", "Americas", "United States"),
    ("^GSPTSE", "S&P/TSX", "Americas", "Canada"),
    ("^BVSP", "Bovespa", "Americas", "Brazil"),
    ("^MXX", "IPC Mexico", "Americas", "Mexico"),
)

REGION_ORDER: tuple[str, ...] = ("Asia-Pacific", "Europe", "Americas")
