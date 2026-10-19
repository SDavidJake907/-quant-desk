"""Wall Street, hard news only: what the Street DID, for Gemma's brief (2026-10-18).

The brief's headlines came from Yahoo RSS and were mostly opinion ("Should
You Buy...?", "3 Dividend Stocks..."). This reads Alpaca's news feed (Benzinga
wire) for the market as a whole and keeps only events:

  rating     upgrades, downgrades, initiations, price-target changes
  deal       acquisitions, mergers, buybacks, offerings, stakes
  guidance   guidance or outlook raised, cut, reaffirmed
  results    earnings beats and misses
  fed        the Fed, rates, Treasury yields, inflation and jobs data

Opinion and listicle headlines are dropped. Names Gemma can trade come first.
CONTEXT, NOT A SIGNAL: news did not separate which big moves kept going
(WORKLIST §57); the brief's rule that news is context, not a reason to swap,
applies (§52). The operator asked for this in Gemma's brief, window 34
continues (FREEZE.md log).
PURE except `fetch`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta, timezone

import httpx

NEWS_URL = "https://data.alpaca.markets/v1beta1/news"
LIMIT = 8
MAX_CHARS = 150

KINDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("rating", re.compile(r"\b(upgrade[sd]?|downgrade[sd]?|initiat\w*|price target|"
                          r"\bPT\b|reiterat\w*|outperform|underperform|overweight|"
                          r"underweight|equal[- ]weight)", re.I)),
    ("deal", re.compile(r"\b(acquir\w*|acquisition|merger|merge[sd]?|takeover|to buy|"
                        r"buyback|repurchase|offering|stake in|spin[- ]?off|divest\w*)", re.I)),
    ("guidance", re.compile(r"\b(guidance|outlook|forecast|sees (Q[1-4]|FY|full[- ]year))\b",
                            re.I)),
    ("results", re.compile(r"\b(beats?|miss(es|ed)?|tops (estimates?|expectations|views)|"
                           r"EPS|quarterly results|"
                           r"Q[1-4] (results|earnings|sales|revenue))\b", re.I)),
    ("fed", re.compile(r"\b(Fed|FOMC|Powell|Warsh|rate (cut|hike)s?|Treasury|yields?|"
                       r"inflation|CPI|PCE|jobs report|payrolls)\b", re.I)),
)

#: Opinion, listicles, weekly roundups, chart lore and crypto (stocks only).
OPINION = re.compile(
    r"(\?\s*$|should you|is it time|buy now|stocks? to (buy|watch|own)|prediction|"
    r"here'?s (why|how|what)|my top|best .*stocks|could (soar|double)|millionaire|"
    r"retire|dividend stocks|which .* better|\bvs\.? |watchlist|top \d+ |\d+ stocks|"
    r"this week in|top catalysts|and more\s*$|death cross|golden cross|pattern|index outlook|"
    r"\b(crypto|bitcoin|BTC|ETH|ethereum|solana|XRP)\b|"
    # personal-finance clickbait: hypothetical returns, "why", "history says"
    r"could be worth|would be worth|invested (right now|today)|\$[\d,]+ investment|"
    r"if i could|here is (why|what|how)|^why |game[- ]changer|history says|"
    r"better .*stock|no[- ]brainer|billionaire|magnificent|for the next \d+ years|"
    r"\b(one|two|three|four|five|\d+) (reasons?|things|ways)\b|worth buying|"
    r"what'?s in the cards|what to expect|smartest|forever|"
    r"\b\d+ (\w+ ){0,3}stocks\b|to watch\b|need to know|what (it|that)( actually)? means|"
    r"the next \w+ stock|jim cramer|top \w+ investment|proved why|biggest hurdle|"
    r"could go (in|by) \d{4}|time to be (bullish|bearish)|should know|"
    r"(looks|still) (undervalued|overvalued|cheap)|"
    r"\b(soar|skyrocket|explode)s?\b)",
    re.I)


#: Market-wide tags kept for Gemma whatever symbols they name.
GEMMA_TAGS = ("[fed]", "[macro]")
#: Most Wall Street lines in her briefing (2026-10-19 trim).
GEMMA_MAX = 6


def for_gemma(items, symbols) -> list[str]:
    """The Wall Street lines worth her tokens: ones naming a stock on her list,
    plus Fed / macro lines; opinion and listicles out."""
    symbols = set(symbols)
    out = []
    for line in items:
        tag, _, rest = str(line).partition(" ")
        names, sep, title = rest.partition(": ")
        if not sep or not tag.startswith("[") or is_opinion(title):
            continue
        if tag in GEMMA_TAGS or symbols & {n.strip() for n in names.split(",")}:
            out.append(line)
    return out[:GEMMA_MAX]


def is_opinion(headline: str) -> bool:
    """Opinion, listicle, roundup, chart lore or crypto: not something the
    Street did. Gemma's per-stock headlines and the brief's feed drop these."""
    return bool(OPINION.search(headline))


def kind_of(headline: str) -> str | None:
    """The first hard-news kind a headline matches, or None (opinion or other)."""
    if is_opinion(headline):
        return None
    for name, pattern in KINDS:
        if pattern.search(headline):
            return name
    return None


def pick(items: Iterable[Mapping], tradable: Iterable[str], limit: int = LIMIT,
         universe: Iterable[str] | None = None) -> list[str]:
    """Up to `limit` lines '[kind] SYMS: headline', names Gemma can trade first,
    newest first within each group, one line per headline. With `universe`
    (S&P 500 + 400), company news about names outside it is dropped; Fed and
    rates news is market-wide and always kept."""
    allowed = {s.upper() for s in tradable}
    members = {s.upper() for s in universe} if universe is not None else None
    seen: set[str] = set()
    mine: list[str] = []
    other: list[str] = []
    for item in items:
        headline = str(item.get("headline") or "").strip()
        key = headline.lower()
        if not headline or key in seen:
            continue
        kind = kind_of(headline)
        if kind is None:
            continue
        seen.add(key)
        syms = [s for s in item.get("symbols") or [] if isinstance(s, str)]
        if members is not None and kind != "fed":
            syms = [s for s in syms if s.upper() in members]
            if not syms:
                continue
        syms = syms[:3]
        tag = f"{','.join(syms)}: " if syms else ""
        line = f"[{kind}] {tag}{headline[:MAX_CHARS]}"
        (mine if allowed & {s.upper() for s in syms} else other).append(line)
    return (mine + other)[:limit]


def window_hours(now: datetime) -> int:
    """Back to the last session: Monday 72 hours and Sunday 48 (both reach
    Friday; Gemma also decides on Sunday evenings), any other day 24."""
    return {0: 72, 6: 48}.get(now.weekday(), 24)


async def fetch(client: httpx.AsyncClient, key_id: str, secret: str, *,
                hours: int, now: datetime | None = None, pages: int = 10) -> Sequence[Mapping]:
    """The market-wide news feed, newest first, over the last `hours` (up to
    `pages` x 50 items: a weekend alone can fill the first page)."""
    start = (now or datetime.now(timezone.utc)) - timedelta(hours=hours)
    params = {"start": start.isoformat(), "limit": 50, "sort": "desc",
              "include_content": "false"}
    headers = {"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret}
    items: list[Mapping] = []
    for _ in range(pages):
        resp = await client.get(NEWS_URL, params=params, headers=headers)
        resp.raise_for_status()
        body = resp.json()
        items += body.get("news", [])
        token = body.get("next_page_token")
        if not token:
            break
        params = {**params, "page_token": token}
    return items
