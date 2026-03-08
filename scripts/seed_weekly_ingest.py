from __future__ import annotations

import argparse

from sqlalchemy import select

from x_trend_idea_mvp.constants import DEFAULT_LIVE_LOOKBACK_DAYS, MAX_LIVE_LOOKBACK_DAYS
from x_trend_idea_mvp.database import SessionLocal
from x_trend_idea_mvp.models import AudienceProfile, TrackedQuery
from x_trend_idea_mvp.services.ingestion import ingest_recent_posts


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest up to one week of X data for an audience.")
    parser.add_argument("--audience-id", required=True)
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_LIVE_LOOKBACK_DAYS)
    parser.add_argument("--max-pages-per-query", type=int, default=3)
    args = parser.parse_args()

    with SessionLocal() as session:
        audience = session.get(AudienceProfile, args.audience_id)
        if audience is None:
            raise SystemExit("Audience profile not found.")
        tracked_queries = session.scalars(
            select(TrackedQuery).where(
                TrackedQuery.audience_profile_id == args.audience_id,
                TrackedQuery.active.is_(True),
            )
        ).all()
        if not tracked_queries:
            raise SystemExit("No active tracked queries found.")
        stats = ingest_recent_posts(
            session,
            tracked_queries=tracked_queries,
            lookback_days=min(args.lookback_days, MAX_LIVE_LOOKBACK_DAYS),
            max_pages_per_query=args.max_pages_per_query,
        )
        print(
            {
                "posts_inserted": stats.posts_inserted,
                "posts_updated": stats.posts_updated,
                "query_count": stats.query_count,
                "started_at": stats.started_at.isoformat(),
                "ended_at": stats.ended_at.isoformat(),
            }
        )


if __name__ == "__main__":
    main()
