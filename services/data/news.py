"""C-level news ingestion and risk classification."""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from services.agents import AgentTaskService, build_configured_llm_runtime
from services.data.repository import DataRepository
from services.strategy_library import AgentTaskRepository, ReviewRepository, StrategyRepository
from shared.config import settings
from shared.models import AgentTaskRequest, RiskEvent, RiskEventType, RiskSeverity

HIGH_KEYWORDS = {
    "sec",
    "etf",
    "hack",
    "exploit",
    "bankruptcy",
    "insolvent",
    "liquidation",
    "depeg",
    "fomc",
    "cpi",
    "暴雷",
    "清算",
    "脱锚",
    "监管",
}


class NewsIngestionService:
    """Poll RSS/SEC-like feeds, persist raw items, and emit risk events."""

    def __init__(
        self,
        *,
        data_repo: DataRepository,
        agent_repo: AgentTaskRepository,
        strategy_repo: StrategyRepository,
        review_repo: ReviewRepository | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.data_repo = data_repo
        self.agent_repo = agent_repo
        self.strategy_repo = strategy_repo
        self.review_repo = review_repo
        self.client = client

    def poll_configured_feeds(self) -> dict[str, Any]:
        urls = {
            "jinshi": settings.jinshi_rss_url,
            "coindesk": settings.coindesk_rss_url,
            "theblock": settings.theblock_rss_url,
            "reuters_crypto": settings.reuters_crypto_rss_url,
            "sec_edgar": settings.sec_edgar_rss_url,
        }
        captured = 0
        risk_events = 0
        disabled = []
        for source, url in urls.items():
            if not url:
                disabled.append(source)
                continue
            items = self.fetch_feed(source=source, url=url)
            for item in items:
                captured += 1
                stored = self.data_repo.store_news_item(item)
                if _is_relevant(item):
                    event = self._classify_and_emit(item)
                    if event is not None:
                        risk_events += 1
                        self.data_repo.store_news_item(
                            {
                                **stored,
                                "id": stored["id"],
                                "severity": str(event.severity),
                                "sentiment": _directional_sentiment(item),
                                "relevance_status": "risk_event_emitted",
                                "affected_symbols": event.affected_scope,
                            }
                        )
        return {"captured": captured, "risk_events": risk_events, "disabled_sources": disabled}

    def fetch_feed(self, *, source: str, url: str) -> list[dict[str, Any]]:
        close_client = self.client is None
        client = self.client or httpx.Client(timeout=10.0, follow_redirects=True)
        try:
            response = client.get(url, headers={"user-agent": "ai-quant-research-platform/0.1"})
            response.raise_for_status()
            return _parse_rss(source=source, body=response.text)
        finally:
            if close_client:
                client.close()

    def _classify_and_emit(self, item: dict[str, Any]) -> RiskEvent | None:
        task = AgentTaskService(
            agent_repo=self.agent_repo,
            strategy_repo=self.strategy_repo,
            review_repo=self.review_repo,
            llm_runtime=build_configured_llm_runtime(),
        ).submit_task(
            AgentTaskRequest(
                agent_type="news_agent",
                task_type="classify_event",
                input_ref=f"news_item:{item['id']}",
                input_payload=item,
            )
        )
        classification = task.output_payload.get("classification") or {}
        severity = _normalize_severity(classification.get("severity") or _keyword_severity(item))
        if severity in {RiskSeverity.HIGH, RiskSeverity.CRITICAL}:
            return self.data_repo.store_risk_event(
                RiskEvent(
                    event_type=RiskEventType.NEWS_RISK,
                    severity=severity,
                    source=str(item.get("source", "news")),
                    description=str(classification.get("summary") or item.get("title") or "news risk event"),
                    affected_scope=["BTC/USDT"],
                    recommended_action="pause_strategy",
                    occurred_at=datetime.now(UTC),
                    expires_at=datetime.now(UTC) + timedelta(minutes=settings.news_high_severity_pause_minutes),
                )
            )
        return None


def _parse_rss(*, source: str, body: str) -> list[dict[str, Any]]:
    root = ET.fromstring(body)
    nodes = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
    items = []
    for node in nodes[:50]:
        title = _child_text(node, "title")
        url = _child_text(node, "link")
        if not url:
            link_node = node.find("{http://www.w3.org/2005/Atom}link")
            url = link_node.attrib.get("href", "") if link_node is not None else ""
        summary = _child_text(node, "description") or _child_text(node, "{http://www.w3.org/2005/Atom}summary")
        published = _parse_date(
            _child_text(node, "pubDate")
            or _child_text(node, "published")
            or _child_text(node, "{http://www.w3.org/2005/Atom}updated")
        )
        digest = hashlib.sha256(f"{source}:{url or title}".encode()).hexdigest()[:32]
        items.append(
            {
                "id": digest,
                "source": source,
                "title": title or "untitled",
                "url": url or None,
                "summary": summary,
                "published_at": published,
                "raw_payload": {"source": source},
                "relevance_status": "captured",
            }
        )
    return items


def _child_text(node: ET.Element, name: str) -> str:
    child = node.find(name)
    if child is None:
        child = node.find(f"{{http://www.w3.org/2005/Atom}}{name}")
    return (child.text or "").strip() if child is not None else ""


def _parse_date(value: str) -> datetime:
    if not value:
        return datetime.now(UTC)
    parsed = parsedate_to_datetime(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _is_relevant(item: dict[str, Any]) -> bool:
    text = f"{item.get('title', '')} {item.get('summary', '')}".lower()
    return any(keyword in text for keyword in HIGH_KEYWORDS)


def _keyword_severity(item: dict[str, Any]) -> str:
    text = f"{item.get('title', '')} {item.get('summary', '')}".lower()
    if any(keyword in text for keyword in {"hack", "exploit", "bankruptcy", "depeg", "暴雷", "脱锚"}):
        return "critical"
    if _is_relevant(item):
        return "high"
    return "low"


def _directional_sentiment(item: dict[str, Any]) -> str:
    text = f"{item.get('title', '')} {item.get('summary', '')}".lower()
    if any(word in text for word in {"approval", "approved", "inflow", "降息", "批准"}):
        return "positive"
    if any(word in text for word in {"hack", "lawsuit", "outflow", "清算", "暴雷"}):
        return "negative"
    return "neutral"


def _normalize_severity(value: object) -> RiskSeverity:
    normalized = str(value or "low").lower().replace("medium", "mid")
    try:
        return RiskSeverity(normalized)
    except ValueError:
        return RiskSeverity.LOW
