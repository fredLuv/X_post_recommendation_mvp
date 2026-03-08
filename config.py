from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv(
        "DATABASE_URL", "sqlite+pysqlite:///./x_trend_idea_mvp/app.db"
    )
    x_bearer_token: str | None = os.getenv("X_BEARER_TOKEN")
    x_api_base_url: str = os.getenv("X_API_BASE_URL", "https://api.x.com/2")
    x_page_size: int = int(os.getenv("X_PAGE_SIZE", "100"))


settings = Settings()
