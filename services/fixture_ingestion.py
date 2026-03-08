from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from x_trend_idea_mvp.models import Post, PostFeature, PostQueryLink, TrackedQuery
from x_trend_idea_mvp.services.ingestion import upsert_post_payload


@dataclass
class FixtureIngestStats:
    posts_inserted: int
    posts_updated: int
    query_count: int
    started_at: datetime
    ended_at: datetime


def ingest_fixture_file(
    session: Session,
    *,
    tracked_queries: list[TrackedQuery],
    fixture_path: str,
    lookback_days: int,
    default_tracked_query_id: str | None = None,
    clear_existing_links: bool = True,
) -> FixtureIngestStats:
    started_at = datetime.now(tz=UTC)
    payload = json.loads(Path(fixture_path).read_text())
    rows: list[dict] = payload["posts"]
    query_map = {query.id: query for query in tracked_queries}
    cutoff = started_at - timedelta(days=lookback_days)

    if clear_existing_links:
        tracked_query_ids = [query.id for query in tracked_queries]
        session.execute(delete(PostQueryLink).where(PostQueryLink.tracked_query_id.in_(tracked_query_ids)))
        orphan_posts = session.scalars(select(Post.id).where(~Post.query_links.any())).all()
        if orphan_posts:
            session.execute(delete(Post).where(Post.id.in_(orphan_posts)))
        session.execute(delete(PostFeature).where(~PostFeature.post.has()))
        session.commit()

    inserted = 0
    updated = 0
    for row in rows:
        created_at = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
        if created_at < cutoff:
            continue

        tracked_query_id = row.get("tracked_query_id")
        if tracked_query_id not in query_map:
            tracked_query_id = default_tracked_query_id
        if tracked_query_id not in query_map:
            continue

        api_payload = {
            "id": row["id"],
            "text": row["text"],
            "author_id": row.get("author_id"),
            "created_at": row["created_at"],
            "lang": row.get("lang", "en"),
            "public_metrics": {
                "like_count": row.get("like_count", 0),
                "reply_count": row.get("reply_count", 0),
                "retweet_count": row.get("repost_count", 0),
                "quote_count": row.get("quote_count", 0),
                "impression_count": row.get("impression_count"),
            },
        }
        is_new, _ = upsert_post_payload(
            session,
            tracked_query_id=tracked_query_id,
            payload=api_payload,
            author_handle=row["author_handle"],
            post_url=row.get("url", f"https://x.com/{row['author_handle']}/status/{row['id']}"),
        )
        if is_new:
            inserted += 1
        else:
            updated += 1

    session.commit()
    ended_at = datetime.now(tz=UTC)
    return FixtureIngestStats(
        posts_inserted=inserted,
        posts_updated=updated,
        query_count=len(tracked_queries),
        started_at=started_at,
        ended_at=ended_at,
    )
