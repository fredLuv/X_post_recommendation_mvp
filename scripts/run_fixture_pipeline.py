from __future__ import annotations

import argparse

from sqlalchemy import select

from x_trend_idea_mvp.constants import DEFAULT_FIXTURE_LOOKBACK_DAYS, MAX_FIXTURE_LOOKBACK_DAYS
from x_trend_idea_mvp.database import Base, SessionLocal, engine
from x_trend_idea_mvp.models import AudienceProfile, TrackedQuery
from x_trend_idea_mvp.scripts.seed_audience import PRESETS
from x_trend_idea_mvp.services.clustering import build_clusters
from x_trend_idea_mvp.services.fixture_ingestion import ingest_fixture_file
from x_trend_idea_mvp.services.recommendations import generate_recommendations


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed an audience, import a fixture, and generate recommendations.")
    parser.add_argument("--preset", choices=sorted(PRESETS), default="crypto-signals")
    parser.add_argument("--fixture-path", required=True)
    parser.add_argument("--audience-name")
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_FIXTURE_LOOKBACK_DAYS)
    parser.add_argument("--min-cluster-size", type=int, default=2)
    parser.add_argument("--max-recommendations", type=int, default=5)
    args = parser.parse_args()

    preset = PRESETS[args.preset]
    audience_name = args.audience_name or args.preset
    Base.metadata.create_all(bind=engine)

    with SessionLocal() as session:
        audience = session.scalar(select(AudienceProfile).where(AudienceProfile.name == audience_name))
        if audience is None:
            audience = AudienceProfile(
                name=audience_name,
                niche=preset["niche"],
                description=preset["description"],
                preferred_formats=preset["preferred_formats"],
            )
            session.add(audience)
            session.flush()

        existing_queries = {
            row.query
            for row in session.scalars(
                select(TrackedQuery).where(TrackedQuery.audience_profile_id == audience.id)
            )
        }
        for query in preset["queries"]:
            if query in existing_queries:
                continue
            session.add(
                TrackedQuery(
                    audience_profile_id=audience.id,
                    query=query,
                    kind="keyword",
                    active=True,
                )
            )
        session.commit()

        tracked_queries = session.scalars(
            select(TrackedQuery).where(
                TrackedQuery.audience_profile_id == audience.id,
                TrackedQuery.active.is_(True),
            )
        ).all()
        if not tracked_queries:
            raise SystemExit("No active tracked queries found.")

        ingest_stats = ingest_fixture_file(
            session,
            tracked_queries=tracked_queries,
            fixture_path=args.fixture_path,
            lookback_days=min(args.lookback_days, MAX_FIXTURE_LOOKBACK_DAYS),
            default_tracked_query_id=tracked_queries[0].id,
            clear_existing_links=True,
        )
        clusters = build_clusters(
            session,
            audience_profile_id=audience.id,
            lookback_days=min(args.lookback_days, MAX_FIXTURE_LOOKBACK_DAYS),
            min_cluster_size=args.min_cluster_size,
        )
        recommendations = generate_recommendations(
            session,
            audience_profile_id=audience.id,
            max_recommendations=args.max_recommendations,
        )
        print(
            {
                "audience_id": audience.id,
                "ingested_posts": ingest_stats.posts_inserted + ingest_stats.posts_updated,
                "cluster_count": len(clusters),
                "recommendations": [
                    {
                        "topic": item.topic,
                        "recommendation": item.recommendation,
                    }
                    for item in recommendations
                ],
            }
        )


if __name__ == "__main__":
    main()
