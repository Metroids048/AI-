"""B-level macro calendar ingestion and planned risk windows."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from shared.config import settings
from services.data.repository import DataRepository
from shared.models import RiskEvent, RiskEventType, RiskSeverity

HIGH_IMPACT_TERMS = {"fomc", "cpi", "ppi", "nonfarm", "nfp", "fed", "interest rate", "gdp", "pmi"}


class MacroCalendarService:
    """Persist macro events and create pause windows for high-impact items."""

    def __init__(self, *, data_repo: DataRepository, client: httpx.Client | None = None) -> None:
        self.data_repo = data_repo
        self.client = client

    def poll_configured_sources(self) -> dict[str, Any]:
        if not settings.forexfactory_rss_url and not settings.trading_economics_api_key:
            return {"captured": 0, "risk_events": 0, "disabled": True}
        captured = 0
        risk_events = 0
        if settings.forexfactory_rss_url:
            for event in self.fetch_forexfactory_rss(settings.forexfactory_rss_url):
                captured += 1
                stored = self.data_repo.store_macro_event(event)
                if self._within_pause_window(stored) and str(stored.get("impact")) in {"high", "critical"}:
                    self.data_repo.store_risk_event(
                        RiskEvent(
                            event_type=RiskEventType.MACRO_EVENT,
                            severity=RiskSeverity.HIGH,
                            source="macro_calendar",
                            description=f"Macro event window: {stored['event_name']}",
                            affected_scope=stored.get("affected_symbols") or ["BTC/USDT"],
                            recommended_action="pause_strategy",
                            occurred_at=datetime.now(UTC),
                            expires_at=_parse_datetime(stored["scheduled_at"])
                            + timedelta(minutes=settings.macro_event_pause_after_minutes),
                        )
                    )
                    risk_events += 1
        return {"captured": captured, "risk_events": risk_events, "disabled": False}

    def fetch_forexfactory_rss(self, url: str) -> list[dict[str, Any]]:
        close_client = self.client is None
        client = self.client or httpx.Client(timeout=10.0, follow_redirects=True)
        try:
            response = client.get(url, headers={"user-agent": "ai-quant-research-platform/0.1"})
            response.raise_for_status()
            return _parse_macro_rss(response.text)
        finally:
            if close_client:
                client.close()

    @staticmethod
    def _within_pause_window(event: dict[str, Any]) -> bool:
        scheduled_at = _parse_datetime(event["scheduled_at"])
        now = datetime.now(UTC)
        return scheduled_at - timedelta(minutes=settings.macro_event_pause_before_minutes) <= now <= (
            scheduled_at + timedelta(minutes=settings.macro_event_pause_after_minutes)
        )


def _parse_macro_rss(body: str) -> list[dict[str, Any]]:
    root = ET.fromstring(body)
    events = []
    for node in root.findall(".//item")[:50]:
        title = _text(node, "title") or "macro event"
        scheduled_at = _parse_rss_date(_text(node, "pubDate"))
        impact = "high" if any(term in title.lower() for term in HIGH_IMPACT_TERMS) else "low"
        events.append(
            {
                "event_name": title[:80],
                "source": "forexfactory",
                "impact": impact,
                "scheduled_at": scheduled_at,
                "affected_symbols": ["BTC/USDT"] if impact == "high" else None,
                "notes": _text(node, "description"),
            }
        )
    return events


def _text(node: ET.Element, name: str) -> str:
    child = node.find(name)
    return (child.text or "").strip() if child is not None else ""


def _parse_rss_date(value: str) -> datetime:
    if not value:
        return datetime.now(UTC)
    parsed = parsedate_to_datetime(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _parse_datetime(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
