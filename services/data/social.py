"""D-level social-source polling for configured watch accounts."""

from __future__ import annotations

from typing import Any

import httpx

from shared.config import settings
from services.data.news import NewsIngestionService


class SocialIngestionService:
    """Poll configured Twitter/X watch users when credentials are present."""

    def __init__(self, *, news_service: NewsIngestionService, client: httpx.Client | None = None) -> None:
        self.news_service = news_service
        self.client = client

    def poll_twitter_watchlist(self) -> dict[str, Any]:
        if not settings.twitter_bearer_token:
            return {"captured": 0, "risk_events": 0, "disabled": True, "reason": "twitter bearer token missing"}
        user_ids = [item.strip() for item in settings.twitter_watch_user_ids.split(",") if item.strip()]
        captured = 0
        for user_id in user_ids:
            for item in self._fetch_user_tweets(user_id):
                captured += 1
                self.news_service.data_repo.store_news_item(item)
        return {"captured": captured, "risk_events": 0, "disabled": False}

    def _fetch_user_tweets(self, user_id: str) -> list[dict[str, Any]]:
        close_client = self.client is None
        client = self.client or httpx.Client(timeout=10.0)
        try:
            response = client.get(
                f"https://api.twitter.com/2/users/{user_id}/tweets",
                headers={"authorization": f"Bearer {settings.twitter_bearer_token}"},
                params={"max_results": 5, "tweet.fields": "created_at"},
            )
            response.raise_for_status()
            payload = response.json()
        finally:
            if close_client:
                client.close()
        return [
            {
                "id": f"twitter:{tweet.get('id')}",
                "source": "twitter",
                "title": str(tweet.get("text", ""))[:160],
                "summary": str(tweet.get("text", "")),
                "url": f"https://twitter.com/i/web/status/{tweet.get('id')}",
                "published_at": tweet.get("created_at"),
                "raw_payload": tweet,
                "relevance_status": "captured",
            }
            for tweet in payload.get("data", [])
        ]
