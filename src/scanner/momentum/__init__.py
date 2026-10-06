"""The momentum engine: a monthly, ranked, three-tranche book.

Separate from the SMC swing strategy in every mechanic (WORKLIST §6): no
setups, no stops, no targets, no intraday bars. A name is held because it sits
in the top 10 by 12-1 momentum when its tranche is rebuilt, and leaves when it
does not. What was measured is what is built -- WORKLIST §31, arm A of
scripts/momentum_guard_test.py. Changing a rule here means testing it there
first.
"""
