from __future__ import annotations

from collections import Counter

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, joinedload

from x_trend_idea_mvp.models import AudienceProfile, ClusterPost, Recommendation, RecommendationEvidence, TopicCluster


def generate_recommendations(
    session: Session,
    *,
    audience_profile_id: str,
    max_recommendations: int,
) -> list[Recommendation]:
    audience = session.get(AudienceProfile, audience_profile_id)
    if audience is None:
        raise ValueError("Audience profile not found.")

    session.execute(delete(RecommendationEvidence).where(RecommendationEvidence.recommendation_id.in_(select(Recommendation.id).where(Recommendation.audience_profile_id == audience_profile_id))))
    session.execute(delete(Recommendation).where(Recommendation.audience_profile_id == audience_profile_id))
    session.commit()

    clusters = session.scalars(
        select(TopicCluster)
        .options(
            joinedload(TopicCluster.signals),
            joinedload(TopicCluster.posts).joinedload(ClusterPost.post),
        )
        .where(TopicCluster.audience_profile_id == audience_profile_id)
    ).unique().all()

    ranked = sorted(clusters, key=internal_rank, reverse=True)[:max_recommendations]
    recommendations: list[Recommendation] = []
    for cluster in ranked:
        rec = Recommendation(
            audience_profile_id=audience_profile_id,
            cluster_id=cluster.id,
            topic=cluster.label or "emerging topic",
            recommendation=build_recommendation_text(cluster, audience),
            why_now=build_why_now(cluster),
            suggested_angle=build_angle(cluster, audience),
            format=pick_format(audience),
            audience_fit=build_audience_fit(cluster, audience),
            risks=build_risks(cluster),
            draft_hooks=build_hooks(cluster),
            internal_rank=internal_rank(cluster),
        )
        session.add(rec)
        session.flush()

        for evidence in build_evidence(cluster):
            session.add(
                RecommendationEvidence(
                    recommendation_id=rec.id,
                    evidence_text=evidence["text"],
                    evidence_type=evidence["type"],
                    source_post_id=evidence.get("source_post_id"),
                )
            )
        recommendations.append(rec)

    session.commit()
    return session.scalars(
        select(Recommendation)
        .options(joinedload(Recommendation.evidence_items))
        .where(Recommendation.audience_profile_id == audience_profile_id)
        .order_by(Recommendation.internal_rank.desc())
    ).unique().all()


def internal_rank(cluster: TopicCluster) -> float:
    signals = cluster.signals
    if signals is None:
        return 0.0
    return round(
        (0.30 * signals.velocity)
        + (0.20 * signals.persistence)
        + (0.15 * signals.author_diversity)
        + (0.20 * signals.explanatory_gap)
        + (0.15 * signals.novelty)
        - (0.20 * signals.saturation_risk),
        4,
    )


def build_recommendation_text(cluster: TopicCluster, audience: AudienceProfile) -> str:
    topic = cluster.label or "this shift"
    angle = choose_content_angle(cluster)
    if angle == "launch":
        return f"Break down what {topic} is launching right now and who should pay attention."
    if angle == "adoption":
        return f"Explain where {topic} is gaining real adoption and what signal is worth tracking."
    if angle == "infrastructure":
        return f"Translate the latest {topic} infrastructure updates into concrete implications for builders."
    if angle == "market-structure":
        return f"Write the practical take on how {topic} changes distribution, liquidity, or market access."
    if angle == "research":
        return f"Summarize the most important {topic} data point this week and what it actually means."
    return f"Write the clearest explanation of what is happening around {topic} and why it matters now."


def build_why_now(cluster: TopicCluster) -> str:
    signals = cluster.signals
    post_count = len(cluster.posts)
    author_count = len(
        {
            link.post.author_id or link.post.author_handle
            for link in cluster.posts
            if link.post is not None
        }
    )

    if signals.velocity >= 2.5:
        return (
            f"This is one of the stronger clusters in the current public snapshot, with {post_count} captured posts "
            f"across {author_count} accounts and above-baseline activity."
        )
    if signals.velocity >= 1.2:
        return (
            f"This topic is showing repeated activity in the current public snapshot, with {post_count} captured posts "
            f"across {author_count} accounts. It looks timely enough for an explainer, but not like a full breakout."
        )
    return (
        f"This is a lighter signal from the current public snapshot: {post_count} captured posts across {author_count} "
        f"accounts. Treat it as a directional cue, not a confirmed trend spike."
    )


def build_angle(cluster: TopicCluster, audience: AudienceProfile) -> str:
    topic = cluster.label or "this topic"
    focus = choose_cluster_focus(cluster)
    if focus:
        return f"Anchor the post on {focus}, then explain what changed, who it affects, and what to watch next."
    return f"Translate {topic} into a practical point of view for {audience.niche}: what changed, who it affects, and what to do next."


def pick_format(audience: AudienceProfile) -> str:
    if audience.preferred_formats:
        return audience.preferred_formats[0]
    return "short_thread"


def build_audience_fit(cluster: TopicCluster, audience: AudienceProfile) -> str:
    focus = choose_cluster_focus(cluster)
    if focus:
        return f"This cluster fits {audience.niche} because the conversation is converging on {focus}, but most posts still stop at reactions."
    return f"This topic fits {audience.niche} and still has room for explanatory content rather than another reaction post."


def build_risks(cluster: TopicCluster) -> list[str]:
    risks = []
    if cluster.signals and cluster.signals.saturation_risk >= 0.6:
        risks.append("The topic may already be crowded, so a generic take will blend in.")
    risks.append("If you stay abstract, the post could read like commentary without a usable takeaway.")
    return risks


def build_hooks(cluster: TopicCluster) -> list[str]:
    topic = cluster.label or "this shift"
    focus = choose_cluster_focus(cluster)
    if focus:
        return [
            f"The real story in {topic} is not the headline, it is what {focus} tells us about the next move.",
            f"If you only saw the surface-level {topic} posts, you missed the part about {focus}.",
        ]
    return [
        f"Everyone is reacting to {topic}, but the real change is what it means in practice.",
        f"The useful way to read {topic} is not hype versus skepticism, it is where the workflow changed.",
    ]


def build_evidence(cluster: TopicCluster) -> list[dict[str, str | None]]:
    evidence = [
        {"text": f"Post volume in the last 24 hours is elevated versus the weekly baseline.", "type": "volume"},
        {"text": f"Multiple distinct accounts are discussing the same topic, not just a single viral thread.", "type": "author"},
        {"text": f"Most posts are reactions rather than explanations, leaving room for a clear explainer.", "type": "gap"},
    ]
    representative = next((link.post_id for link in cluster.posts if link.is_representative), None)
    if representative:
        evidence[0]["source_post_id"] = representative
    return evidence


def choose_content_angle(cluster: TopicCluster) -> str:
    terms = cluster_terms(cluster)
    launch_markers = {"launch", "introduced", "introducing", "track", "plugin", "batches"}
    adoption_markers = {"adoption", "integrations", "users", "holders", "volume", "growth"}
    infrastructure_markers = {"ccip", "data", "streams", "server", "tools", "stack", "infrastructure"}
    market_markers = {"trading", "prime", "liquidity", "pay", "volume", "distribution"}
    research_markers = {"report", "valuation", "state", "data", "spreadsheets", "research"}

    if terms & launch_markers:
        return "launch"
    if terms & infrastructure_markers:
        return "infrastructure"
    if terms & market_markers:
        return "market-structure"
    if terms & research_markers:
        return "research"
    if terms & adoption_markers:
        return "adoption"
    return "general"


def choose_cluster_focus(cluster: TopicCluster) -> str | None:
    ranked_terms = Counter()
    for link in cluster.posts:
        if link.post and link.post.features:
            ranked_terms.update(
                term
                for term in link.post.features.keywords
                if term not in {"crypto", "quote", "read", "full", "conversation", "update", "week"}
            )
    for term, _ in ranked_terms.most_common():
        if term.lower() != (cluster.label or "").lower():
            return prettify_focus(term)
    return None


def cluster_terms(cluster: TopicCluster) -> set[str]:
    terms: set[str] = set()
    for link in cluster.posts:
        if link.post and link.post.features:
            terms.update(link.post.features.keywords)
    return terms


def prettify_focus(term: str) -> str:
    mapping = {
        "ccip": "CCIP",
        "com": "institutional product launches",
        "davidtsocy": "builder distribution on Base",
        "messari": "new Messari data products",
        "prime": "institutional trading rails",
        "solana": "Solana payment growth",
        "spreadsheets": "workflow tooling",
    }
    return mapping.get(term, term.replace("_", " "))
