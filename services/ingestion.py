from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from x_trend_idea_mvp.models import Post, PostFeature, PostQueryLink, TrackedQuery
from x_trend_idea_mvp.services.text import clean_text, extract_hashtags, extract_keywords, extract_mentions, extract_urls
from x_trend_idea_mvp.services.x_api import XApiClient


@dataclass
class IngestStats:
    posts_inserted: int
    posts_updated: int
    query_count: int
    started_at: datetime
    ended_at: datetime


def upsert_post_payload(
    session: Session,
    *,
    tracked_query_id: str,
    payload: dict,
    author_handle: str,
    post_url: str,
) -> tuple[bool, bool]:
    post = session.get(Post, payload["id"])
    metrics = payload.get("public_metrics", {})
    is_new = post is None
    if is_new:
        post = Post(
            id=payload["id"],
            author_handle=author_handle,
            author_id=payload.get("author_id"),
            body=payload["text"],
            lang=payload.get("lang"),
            posted_at=datetime.fromisoformat(payload["created_at"].replace("Z", "+00:00")),
            like_count=metrics.get("like_count", 0),
            reply_count=metrics.get("reply_count", 0),
            repost_count=metrics.get("retweet_count", 0),
            quote_count=metrics.get("quote_count", 0),
            impression_count=metrics.get("impression_count"),
            url=post_url,
            raw_json=payload,
        )
        session.add(post)
    else:
        post.author_handle = author_handle
        post.body = payload["text"]
        post.lang = payload.get("lang")
        post.like_count = metrics.get("like_count", post.like_count)
        post.reply_count = metrics.get("reply_count", post.reply_count)
        post.repost_count = metrics.get("retweet_count", post.repost_count)
        post.quote_count = metrics.get("quote_count", post.quote_count)
        post.impression_count = metrics.get("impression_count", post.impression_count)
        post.raw_json = payload

    feature = post.features or PostFeature(post=post, post_id=post.id, clean_text="")
    feature.clean_text = clean_text(post.body)
    feature.hashtags = extract_hashtags(post.body)
    feature.mentions = extract_mentions(post.body)
    feature.urls = extract_urls(post.body)
    feature.keywords = extract_keywords(post.body)
    session.add(feature)

    link = session.scalar(
        select(PostQueryLink).where(
            PostQueryLink.post_id == post.id,
            PostQueryLink.tracked_query_id == tracked_query_id,
        )
    )
    link_created = False
    if link is None:
        session.add(PostQueryLink(post_id=post.id, tracked_query_id=tracked_query_id))
        link_created = True
    return is_new, link_created


def ingest_recent_posts(
    session: Session,
    *,
    tracked_queries: list[TrackedQuery],
    lookback_days: int,
    max_pages_per_query: int,
) -> IngestStats:
    client = XApiClient()
    started_at = datetime.now(tz=UTC)
    inserted = 0
    updated = 0
    start_time, end_time = client.week_window(lookback_days)

    for tracked_query in tracked_queries:
        next_token: str | None = None
        page_count = 0
        while page_count < max_pages_per_query:
            result = client.recent_search(
                tracked_query.query,
                start_time=start_time,
                end_time=end_time,
                next_token=next_token,
            )
            authors = {
                user["id"]: user.get("username", user["id"])
                for user in result.includes.get("users", [])
            }
            for payload in result.data:
                is_new, _ = upsert_post_payload(
                    session,
                    tracked_query_id=tracked_query.id,
                    payload=payload,
                    author_handle=authors.get(payload.get("author_id"), payload.get("author_id", "unknown")),
                    post_url=f"https://x.com/{authors.get(payload.get('author_id'), 'unknown')}/status/{payload['id']}",
                )
                if is_new:
                    inserted += 1
                else:
                    updated += 1

            session.commit()
            next_token = result.meta.get("next_token")
            page_count += 1
            if not next_token:
                break

    ended_at = datetime.now(tz=UTC)
    return IngestStats(
        posts_inserted=inserted,
        posts_updated=updated,
        query_count=len(tracked_queries),
        started_at=started_at,
        ended_at=ended_at,
    )
