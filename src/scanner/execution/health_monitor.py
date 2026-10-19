"""Real-time health monitoring for the trading system.

Monitors broker connectivity, model availability, rejection rates, and
other runtime conditions. When thresholds are breached, triggers the
automated kill switch to halt new entries until a human investigates.

Designed to be called from the trading loop after each order/submission
attempt, and from a periodic watchdog during idle periods.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

from scanner.config import settings
from scanner.execution import safety


class ModelHealth:
    """Result of probing the Ollama model endpoint."""

    def __init__(self, reachable: bool, model_present: bool, model: str,
                 checked_at: datetime | None = None):
        self.reachable = reachable
        self.model_present = model_present
        self.model = model
        self.checked_at = checked_at or datetime.now(timezone.utc)

    @property
    def ok(self) -> bool:
        return self.reachable and self.model_present


class BrokerHealth:
    """Result of probing the Alpaca broker endpoint."""

    def __init__(self, reachable: bool, paper: bool, account_status: str,
                 checked_at: datetime | None = None):
        self.reachable = reachable
        self.paper = paper
        self.account_status = account_status
        self.checked_at = checked_at or datetime.now(timezone.utc)

    @property
    def ok(self) -> bool:
        return self.reachable and self.paper and self.account_status == "ACTIVE"


class HealthMonitor:
    """Tracks health of model and broker over time, triggers kill switch
    when thresholds are breached.

    State is kept in memory only (per-process). For cross-process
    visibility, the kill_switch file and rejection counter on disk are the
    source of truth across processes.
    """

    def __init__(
        self,
        kill_switch_path: str,
        max_consecutive_rejections: int = 5,
        model_timeout_s: float = 10.0,
        broker_timeout_s: float = 10.0,
    ):
        self.kill_switch_path = kill_switch_path
        self.max_consecutive_rejections = max_consecutive_rejections
        self.model_timeout_s = model_timeout_s
        self.broker_timeout_s = broker_timeout_s
        self.consecutive_rejections = 0
        self.model_health: ModelHealth | None = None
        self.broker_health: BrokerHealth | None = None
        self.last_check_at: datetime | None = None

    # ---- model health -----------------------------------------------------

    async def probe_model(self, client: httpx.AsyncClient) -> ModelHealth:
        """Ask Ollama which models are installed. Never raises."""
        wanted = settings.triage_model
        try:
            response = await client.get(
                f"{settings.ollama_host}/api/tags",
                timeout=self.model_timeout_s,
            )
            response.raise_for_status()
            names = {
                m.get("name", "") for m in (response.json().get("models") or [])
            }
            reachable = True
            model_present = any(
                n == wanted or n.startswith(wanted + ":") for n in names
            )
        except Exception:
            reachable = False
            model_present = False
        self.model_health = ModelHealth(
            reachable=reachable,
            model_present=model_present,
            model=wanted,
            checked_at=datetime.now(timezone.utc),
        )
        return self.model_health

    # ---- broker health ----------------------------------------------------

    async def probe_broker(self, client: httpx.AsyncClient) -> BrokerHealth:
        """Ask Alpaca for account info. Never raises."""
        from scanner.execution.alpaca import Alpaca

        try:
            broker = Alpaca(
                settings.alpaca_key_id, settings.alpaca_secret_key, client
            )
            account = await broker.account()
            reachable = True
            paper = broker.host == "https://paper-api.alpaca.markets"
            account_status = account.status
        except Exception:
            reachable = False
            paper = False
            account_status = "UNKNOWN"
        self.broker_health = BrokerHealth(
            reachable=reachable,
            paper=paper,
            account_status=account_status,
            checked_at=datetime.now(timezone.utc),
        )
        return self.broker_health

    # ---- rejection tracking ----------------------------------------------

    def record_rejection(self) -> int:
        """Increment the on-disk rejection counter (shared by every process)
        and return the new total."""
        self.consecutive_rejections = safety.record_rejection(self.kill_switch_path)
        return self.consecutive_rejections

    def reset_rejections(self) -> None:
        """Reset consecutive rejection counter after a successful order."""
        self.consecutive_rejections = 0
        safety.reset_rejections(self.kill_switch_path)

    def should_auto_activate(self) -> tuple[bool, str]:
        """Decide whether kill switch should auto-activate based on current
        health and rejection count."""
        return safety.should_auto_activate(
            self.kill_switch_path,
            consecutive_rejections=self.consecutive_rejections,
            model_env_complete=self.model_health.ok
            if self.model_health
            else False,
            broker_reachable=self.broker_health.reachable
            if self.broker_health
            else False,
        )

    # ---- combined check ----------------------------------------------------

    async def run_all(
        self, client: httpx.AsyncClient
    ) -> tuple[ModelHealth, BrokerHealth, bool, str]:
        """Run all health checks. Returns (model_health, broker_health,
        should_block, reason)."""
        self.model_health = await self._model_health_check(client)
        self.broker_health = await self._broker_env_check(client)
        should_block, reason = self.should_auto_activate()
        self.last_check_at = datetime.now(timezone.utc)
        return self.model_health, self.broker_health, should_block, reason

    # aliases for backward-compat naming used elsewhere
    _model_health_check = probe_model
    _broker_env_check = probe_broker


def format_health(model: ModelHealth, broker: BrokerHealth) -> str:
    """One-line summary of health status."""
    m = "OK" if model.ok else "DOWN"
    b = "OK" if broker.ok else "DOWN"
    return f"models:{m} broker:{b}"


# convenience function for quick one-off checks
async def quick_preflight(
    client: httpx.AsyncClient,
    kill_switch_path: str,
) -> tuple[bool, str]:
    """Run a quick pre-flight check. Returns (ok, reason)."""
    monitor = HealthMonitor(kill_switch_path)
    model, broker, blocked, reason = await monitor.run_all(client)
    if blocked:
        return False, reason
    return True, "preflight OK"
