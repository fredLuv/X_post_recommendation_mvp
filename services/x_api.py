from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx

from x_trend_idea_mvp.config import settings


class XApiConfigurationError(RuntimeError):
    pass


@dataclass
class XSearchResult:
    data: list[dict]
    includes: dict
    meta: dict


class XApiClient:
    def __init__(self) -> None:
        if not settings.x_bearer_token:
            raise XApiConfigurationError("X_BEARER_TOKEN is not configured.")
        self.base_url = settings.x_api_base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {settings.x_bearer_token}"}

    def recent_search(
        self,
        query: str,
        *,
        start_time: datetime,
        end_time: datetime,
        next_token: str | None = None,
        max_results: int | None = None,
    ) -> XSearchResult:
        params = {
            "query": query,
            "start_time": start_time.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "end_time": end_time.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "max_results": max_results or settings.x_page_size,
            "tweet.fields": "author_id,created_at,lang,public_metrics",
            "expansions": "author_id",
            "user.fields": "username",
        }
        if next_token:
            params["next_token"] = next_token

        response = httpx.get(
            f"{self.base_url}/tweets/search/recent",
            headers=self.headers,
            params=params,
            timeout=30.0,
        )
        response.raise_for_status()
        payload = response.json()
        return XSearchResult(
            data=payload.get("data", []),
            includes=payload.get("includes", {}),
            meta=payload.get("meta", {}),
        )

    @staticmethod
    def week_window(lookback_days: int = 7) -> tuple[datetime, datetime]:
        end_time = datetime.now(tz=UTC)
        start_time = end_time - timedelta(days=lookback_days)
        return start_time, end_time
