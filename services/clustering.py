from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, joinedload

from x_trend_idea_mvp.models import ClusterPost, ClusterSignal, Post, PostQueryLink, TopicCluster
from x_trend_idea_mvp.services.text import normalize_keyword


GENERIC_CLUSTER_TERMS = {
    "across",
    "according",
    "agent",
    "adoption",
    "build",
    "builder",
    "chain",
    "company",
    "com",
    "conversation",
    "crypto",
    "data",
    "day",
    "future",
    "growth",
    "integration",
    "idea",
    "mar",
    "market",
    "million",
    "platform",
    "quote",
    "read",
    "report",
    "service",
    "standard",
    "system",
    "token",
    "update",
    "volume",
    "week",
    "women",
}

BRANDED_CLUSTER_TERMS = {
    "a16zcrypto",
    "base",
    "binance",
    "chainlink",
    "coinbase",
    "defillama",
    "ethereum",
    "krakenfx",
    "messari",
    "messaricrypto",
    "solana",
    "tokenterminal",
}


def build_clusters(
    session: Session,
    *,
    audience_profile_id: str,
    lookback_days: int,
    min_cluster_size: int,
) -> list[TopicCluster]:
    end_time = datetime.now(tz=UTC)
    start_time = end_time - timedelta(days=lookback_days)

    posts = session.scalars(
        select(Post).options(
            joinedload(Post.features),
            joinedload(Post.query_links).joinedload(PostQueryLink.tracked_query),
        )
    ).unique().all()
    filtered_posts = [
        post
        for post in posts
        if ensure_utc(post.posted_at) >= start_time
        and any(link.tracked_query.audience_profile_id == audience_profile_id for link in post.query_links)
        and post.features
        and post.features.keywords
    ]

    session.execute(
        delete(ClusterSignal).where(
            ClusterSignal.cluster_id.in_(
                select(TopicCluster.id).where(TopicCluster.audience_profile_id == audience_profile_id)
            )
        )
    )
    session.execute(delete(ClusterPost).where(ClusterPost.cluster_id.in_(select(TopicCluster.id).where(TopicCluster.audience_profile_id == audience_profile_id))))
    session.execute(delete(TopicCluster).where(TopicCluster.audience_profile_id == audience_profile_id))
    session.commit()

    grouped = group_posts_by_theme(filtered_posts, min_cluster_size=min_cluster_size)

    created: list[TopicCluster] = []
    for label, group in grouped.items():
        if len(group) < min_cluster_size:
            continue
        summary = summarize_cluster(group)
        cluster = TopicCluster(
            audience_profile_id=audience_profile_id,
            label=label,
            summary=summary,
            time_window_start=start_time,
            time_window_end=end_time,
        )
        session.add(cluster)
        session.flush()

        representative_id = max(
            group,
            key=lambda post: (post.like_count + post.reply_count + post.repost_count + post.quote_count),
        ).id
        for post in group:
            session.add(
                ClusterPost(
                    cluster_id=cluster.id,
                    post_id=post.id,
                    is_representative=post.id == representative_id,
                )
            )

        signal = compute_signals(group)
        session.add(
            ClusterSignal(
                cluster_id=cluster.id,
                post_count_24h=signal["post_count_24h"],
                baseline_post_count_7d=signal["baseline_post_count_7d"],
                velocity=signal["velocity"],
                persistence=signal["persistence"],
                author_diversity=signal["author_diversity"],
                novelty=signal["novelty"],
                saturation_risk=signal["saturation_risk"],
                explanatory_gap=signal["explanatory_gap"],
            )
        )
        created.append(cluster)

    session.commit()
    return session.scalars(
        select(TopicCluster)
        .options(joinedload(TopicCluster.posts), joinedload(TopicCluster.signals))
        .where(TopicCluster.audience_profile_id == audience_profile_id)
    ).unique().all()


def summarize_cluster(posts: list[Post]) -> str:
    terms = Counter()
    for post in posts:
        terms.update(post.features.keywords[:5])
    top_terms = [term for term, _ in terms.most_common(5)]
    return f"Posts repeatedly discuss {' / '.join(top_terms)}."


def group_posts_by_theme(posts: list[Post], *, min_cluster_size: int) -> dict[str, list[Post]]:
    keyword_sets: dict[str, set[str]] = {}
    doc_freq = Counter()
    for post in posts:
        terms = {
            normalize_keyword(term)
            for term in post.features.keywords
            if normalize_keyword(term) and normalize_keyword(term) not in GENERIC_CLUSTER_TERMS
        }
        keyword_sets[post.id] = terms
        doc_freq.update(terms)

    strong_terms = {
        term
        for term, freq in doc_freq.items()
        if freq >= min_cluster_size
    }

    post_index = {post.id: post for post in posts}
    adjacency: dict[str, set[str]] = {post.id: set() for post in posts}
    for left in posts:
        left_terms = keyword_sets[left.id] & strong_terms
        if not left_terms:
            continue
        for right in posts:
            if left.id >= right.id:
                continue
            shared = left_terms & keyword_sets[right.id] & strong_terms
            if not shared:
                continue
            has_branded_anchor = any(term in BRANDED_CLUSTER_TERMS for term in shared)
            if has_branded_anchor or len(shared) >= 2:
                adjacency[left.id].add(right.id)
                adjacency[right.id].add(left.id)

    grouped: dict[str, list[Post]] = {}
    seen: set[str] = set()
    for post in posts:
        if post.id in seen:
            continue
        stack = [post.id]
        component: list[str] = []
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            component.append(current)
            stack.extend(adjacency[current] - seen)

        if len(component) < min_cluster_size:
            continue

        component_posts = [post_index[post_id] for post_id in component]
        label = choose_cluster_label(component_posts, keyword_sets)
        grouped[label] = sorted(component_posts, key=lambda item: ensure_utc(item.posted_at), reverse=True)

    return grouped


def choose_cluster_label(posts: list[Post], keyword_sets: dict[str, set[str]]) -> str:
    shared = set.intersection(*(keyword_sets[post.id] for post in posts))
    if shared:
        ranked_shared = Counter()
        for post in posts:
            ranked_shared.update(keyword_sets[post.id] & shared)
        term, _ = max(ranked_shared.items(), key=lambda item: (item[1], len(item[0]), item[0]))
        return prettify_label(term)

    ranked = Counter()
    for post in posts:
        ranked.update(keyword_sets[post.id])
    for term, _ in ranked.most_common():
        if term not in GENERIC_CLUSTER_TERMS:
            return prettify_label(term)
    return "emerging topic"


def prettify_label(term: str) -> str:
    mapping = {
        "a16zcrypto": "a16z crypto",
        "base": "Base",
        "binance": "Binance",
        "chainlink": "Chainlink",
        "coinbase": "Coinbase",
        "defillama": "DeFiLlama",
        "ethereum": "Ethereum",
        "krakenfx": "Kraken",
        "messari": "Messari",
        "messaricrypto": "Messari",
        "perp": "derivatives",
        "solana": "Solana",
        "tokenterminal": "Token Terminal",
    }
    pretty = mapping.get(term, term.replace("_", " ").strip())
    return pretty


def compute_signals(posts: list[Post]) -> dict[str, float]:
    now = datetime.now(tz=UTC)
    posts_24h = [post for post in posts if ensure_utc(post.posted_at) >= now - timedelta(hours=24)]
    unique_authors = len({post.author_id or post.author_handle for post in posts})
    avg_posts_per_day = len(posts) / 7.0
    velocity = len(posts_24h) / max(avg_posts_per_day, 1.0)
    explanatory_markers = {"why", "because", "means", "explains", "breakdown", "guide"}
    explanatory_posts = [
        post for post in posts if explanatory_markers.intersection(set(post.features.keywords))
    ]
    reaction_share = 1.0 - (len(explanatory_posts) / max(len(posts), 1))
    hashtag_density = sum(len(post.features.hashtags) for post in posts) / max(len(posts), 1)
    return {
        "post_count_24h": len(posts_24h),
        "baseline_post_count_7d": round(avg_posts_per_day, 3),
        "velocity": round(min(velocity, 10.0), 3),
        "persistence": round(min(len(posts) / 10.0, 1.0), 3),
        "author_diversity": round(min(unique_authors / max(len(posts), 1), 1.0), 3),
        "novelty": round(max(0.1, 1.0 - min(hashtag_density / 6.0, 0.9)), 3),
        "saturation_risk": round(min(len(posts) / 25.0, 1.0), 3),
        "explanatory_gap": round(max(0.1, reaction_share), 3),
    }


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
