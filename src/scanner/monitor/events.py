"""One vocabulary for news events, shared by every prompt that labels them.

Adopted from scripts/news_event_test.py's labels (2,421 headlines already
labelled with these names, 2026-10-04) plus `offering` and `halt`, which a
held position must not miss. Monitor v2 (scanner.monitor.review), the daily
momentum watch, TRIALS.md #45 and the planned Gemma sentiment factor all use
this list, so a flag recorded today can be compared with a label from last
week. The news gate (scanner.llm.triage) keeps its own coarser categories: it
is frozen and measured (14/14), and it answers a different question.
"""

from __future__ import annotations

EVENTS = ("earnings_beat", "earnings_miss", "guidance_up", "guidance_down",
          "analyst_upgrade", "analyst_downgrade", "mna", "legal_regulatory",
          "offering", "halt", "product", "management", "macro_sector", "other")

#: Events that can break the reason for holding a long position. `mna` covers
#: the company being acquired (its price then tracks the deal, not the trade);
#: `management` covers an abrupt executive exit; `legal_regulatory` covers
#: lawsuits, probes and fraud allegations.
MATERIAL_NEGATIVE = ("earnings_miss", "guidance_down", "analyst_downgrade", "mna",
                     "legal_regulatory", "offering", "halt", "management")

#: Plain meanings, shown to the model beside each name.
GLOSS = {"earnings_miss": "results below forecast", "guidance_down": "outlook cut or withdrawn",
         "analyst_downgrade": "rating or target cut", "mna": "THIS company being acquired",
         "legal_regulatory": "lawsuit, probe, fraud allegation", "offering": "new shares sold",
         "halt": "trading halted", "management": "abrupt executive exit"}

assert set(MATERIAL_NEGATIVE) <= set(EVENTS) and set(GLOSS) == set(MATERIAL_NEGATIVE)
