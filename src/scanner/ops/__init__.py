"""Operations around open positions: phone alerts and account-level risk.

Outside the trading path by construction: nothing here places, cancels or
sizes an order. risk_monitor.py may create the kill-switch file only with
--enforce, which stays off until funding (GO_LIVE.md).
"""
