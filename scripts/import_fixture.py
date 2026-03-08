from __future__ import annotations

import argparse

from sqlalchemy import select

from x_trend_idea_mvp.constants import DEFAULT_FIXTURE_LOOKBACK_DAYS, MAX_FIXTURE_LOOKBACK_DAYS
from x_trend_idea_mvp.database import SessionLocal
from x_trend_idea_mvp.models import TrackedQuery
from x_trend_idea_mvp.services.fixture_ingestion import ingest_fixture_file


def main() -> None:
    parser = argparse.ArgumentParser(description="Import a local X fixture file into the MVP database.")
    parser.add_argument("--audience-id", required=True)
    parser.add_argument("--fixture-path", required=True)
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_FIXTURE_LOOKBACK_DAYS)
    parser.add_argument("--default-tracked-query-id")
    args = parser.parse_args()

    with SessionLocal() as session:
        tracked_queries = session.scalars(
            select(TrackedQuery).where(
                TrackedQuery.audience_profile_id == args.audience_id,
                TrackedQuery.active.is_(True),
            )
        ).all()
        if not tracked_queries:
            raise SystemExit("No active tracked queries found.")
        stats = ingest_fixture_file(
            session,
            tracked_queries=tracked_queries,
            fixture_path=args.fixture_path,
            lookback_days=min(args.lookback_days, MAX_FIXTURE_LOOKBACK_DAYS),
            default_tracked_query_id=args.default_tracked_query_id,
            clear_existing_links=True,
        )
        print(
            {
                "posts_inserted": stats.posts_inserted,
                "posts_updated": stats.posts_updated,
                "query_count": stats.query_count,
            }
        )


if __name__ == "__main__":
    main()
