from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import re

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, joinedload

from x_trend_idea_mvp.models import AudienceProfile, ClusterPost, Recommendation, RecommendationEvidence, TopicCluster
from x_trend_idea_mvp.services.text import STOPWORDS, clean_text, normalize_keyword

try:
    from sklearn.feature_extraction.text import TfidfVectorizer
except ImportError:  # pragma: no cover - local development fallback
    TfidfVectorizer = None


MAX_EVIDENCE_ITEMS = 4
MAX_HOOKS = 2
MAX_TOP_PHRASES = 4
MAX_TFIDF_FEATURES = 256
MAX_REPRESENTATIVE_WORDS = 16
MIN_FOCUS_PHRASE_WORDS = 1
PHRASE_MAX_DOC_FRACTION = 0.85
TOKEN_PATTERN = r"(?u)\b[a-zA-Z][a-zA-Z]{2,}\b"
TFIDF_SCORE_FLOOR = 0.10

GENERIC_FOCUS_TERMS = {
    "",
    "account",
    "accounts",
    "agent",
    "agents",
    "base",
    "binance",
    "builder",
    "builders",
    "chain",
    "chainlink",
    "coinbase",
    "conversation",
    "crypto",
    "data",
    "growth",
    "market",
    "messari",
    "operator",
    "operators",
    "post",
    "posts",
    "researcher",
    "researchers",
    "signal",
    "signals",
    "solana",
    "story",
    "topic",
    "update",
    "volume",
}

PROMOTIONAL_MARKERS = {
    "apply",
    "claim",
    "comment",
    "follow",
    "giving away",
    "introducing",
    "join",
    "launching",
    "learn more",
    "read the full report",
    "reserve",
    "spotlight",
    "submit",
    "try here",
    "win",
}

FRAME_MARKERS = {
    "launch": {"announce", "announced", "apply", "batches", "introducing", "launch", "launching", "release", "track"},
    "adoption": {"adoption", "growth", "merchant", "payment", "usage", "user", "users", "volume"},
    "infrastructure": {"audit", "ccip", "hackathon", "infra", "integration", "integrations", "plugin", "sdk", "stack", "tooling"},
    "market-structure": {"access", "brokerage", "custody", "dex", "distribution", "institutional", "liquidity", "pay", "prime", "trading"},
    "research": {"chart", "dashboard", "data", "datapoint", "metrics", "plugin", "report", "spreadsheets", "state of"},
}

FRAME_DESCRIPTIONS = {
    "launch": "what is actually new, who it is for, and what would make it matter beyond announcement day",
    "adoption": "whether this reflects real usage or just headline-friendly growth",
    "infrastructure": "which workflow or integration surface is changing underneath the announcement",
    "market-structure": "where access, liquidity, or institutional distribution shifts if this sticks",
    "research": "what the datapoint says, what it does not prove yet, and what to watch next",
    "general": "what the cluster is really about, what is still unproven, and why it is worth tracking",
}

TFIDF_STOPWORDS = STOPWORDS | {
    "amp",
    "crypto",
    "full",
    "read",
    "quote",
    "today",
    "week",
    "year",
}


@dataclass(slots=True)
class ClusterInsight:
    focus: str
    supporting_phrases: list[str]
    frame: str
    representative_snippet: str | None
    author_handles: list[str]
    promotional_ratio: float


def generate_recommendations(
    session: Session,
    *,
    audience_profile_id: str,
    max_recommendations: int,
) -> list[Recommendation]:
    audience = session.get(AudienceProfile, audience_profile_id)
    if audience is None:
        raise ValueError("Audience profile not found.")

    session.execute(
        delete(RecommendationEvidence).where(
            RecommendationEvidence.recommendation_id.in_(
                select(Recommendation.id).where(Recommendation.audience_profile_id == audience_profile_id)
            )
        )
    )
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
    insights = analyze_clusters(ranked)

    recommendations: list[Recommendation] = []
    for cluster in ranked:
        insight = insights[cluster.id]
        rec = Recommendation(
            audience_profile_id=audience_profile_id,
            cluster_id=cluster.id,
            topic=cluster.label or "emerging topic",
            recommendation=build_recommendation_text(cluster, insight),
            why_now=build_snapshot_read(cluster, insight),
            suggested_angle=build_angle(cluster, audience, insight),
            format=pick_format(audience),
            audience_fit=build_audience_fit(cluster, audience, insight),
            risks=build_risks(cluster, insight),
            draft_hooks=build_hooks(cluster, insight),
            internal_rank=internal_rank(cluster),
        )
        session.add(rec)
        session.flush()

        for evidence in build_evidence(cluster, insight):
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


def analyze_clusters(clusters: list[TopicCluster]) -> dict[str, ClusterInsight]:
    documents = [cluster_document(cluster) for cluster in clusters]
    distinctive_phrases = extract_distinctive_phrases(clusters, documents)
    insights: dict[str, ClusterInsight] = {}

    for cluster, phrases in zip(clusters, distinctive_phrases, strict=True):
        repeated_phrases = repeated_cluster_phrases(cluster)
        cleaned_phrases = filter_phrases(cluster, phrases)
        fallback_terms = cluster_terms(cluster)
        supporting_phrases = merge_ranked_phrases(cleaned_phrases, fallback_terms, repeated_phrases)
        focus = choose_focus(cluster, supporting_phrases)
        frame = infer_frame(cluster, supporting_phrases)
        author_handles = sorted({link.post.author_handle for link in cluster.posts if link.post is not None})
        promotional_posts = sum(
            1
            for link in cluster.posts
            if link.post is not None and is_promotional_text(link.post.body)
        )
        snippet = representative_snippet(cluster)

        insights[cluster.id] = ClusterInsight(
            focus=focus,
            supporting_phrases=supporting_phrases,
            frame=frame,
            representative_snippet=snippet,
            author_handles=author_handles,
            promotional_ratio=promotional_posts / max(len(cluster.posts), 1),
        )

    return insights


def extract_distinctive_phrases(clusters: list[TopicCluster], documents: list[str]) -> list[list[str]]:
    if not clusters or not any(doc.strip() for doc in documents) or TfidfVectorizer is None:
        return [[] for _ in clusters]

    vectorizer = TfidfVectorizer(
        ngram_range=(1, 2),
        max_features=MAX_TFIDF_FEATURES,
        stop_words=sorted(TFIDF_STOPWORDS),
        strip_accents="unicode",
        sublinear_tf=True,
        token_pattern=TOKEN_PATTERN,
        max_df=PHRASE_MAX_DOC_FRACTION,
    )
    matrix = vectorizer.fit_transform(documents)
    feature_names = vectorizer.get_feature_names_out()

    phrases_per_cluster: list[list[str]] = []
    for index in range(matrix.shape[0]):
        row = matrix.getrow(index)
        if row.nnz == 0:
            phrases_per_cluster.append([])
            continue

        ranked_indices = row.indices[row.data.argsort()[::-1]]
        phrases: list[str] = []
        for feature_index in ranked_indices:
            if row[0, feature_index] < TFIDF_SCORE_FLOOR:
                continue
            phrase = prettify_phrase(feature_names[feature_index])
            if phrase in phrases:
                continue
            phrases.append(phrase)
            if len(phrases) >= MAX_TFIDF_FEATURES:
                break
        phrases_per_cluster.append(phrases)

    return phrases_per_cluster


def repeated_cluster_phrases(cluster: TopicCluster) -> list[str]:
    if TfidfVectorizer is None:
        return []

    documents = [cluster_post_document(link) for link in cluster.posts if cluster_post_document(link)]
    if not documents:
        return []

    min_df = 2 if len(documents) >= 4 else 1
    vectorizer = TfidfVectorizer(
        ngram_range=(2, 3),
        stop_words=sorted(TFIDF_STOPWORDS),
        strip_accents="unicode",
        sublinear_tf=True,
        token_pattern=TOKEN_PATTERN,
        min_df=min_df,
        max_df=PHRASE_MAX_DOC_FRACTION,
    )
    try:
        matrix = vectorizer.fit_transform(documents)
    except ValueError:
        return []
    if matrix.nnz == 0:
        return []

    feature_names = vectorizer.get_feature_names_out()
    scores = matrix.sum(axis=0).A1
    ranked_indices = scores.argsort()[::-1]
    phrases: list[str] = []
    for feature_index in ranked_indices:
        if scores[feature_index] < TFIDF_SCORE_FLOOR:
            continue
        phrase = prettify_phrase(feature_names[feature_index])
        if phrase in phrases:
            continue
        phrases.append(phrase)
        if len(phrases) >= MAX_TOP_PHRASES:
            break
    return phrases


def build_recommendation_text(cluster: TopicCluster, insight: ClusterInsight) -> str:
    focus = preferred_focus(cluster, insight)
    topic = cluster.label or "this cluster"
    frame = insight.frame

    if not has_confident_focus(cluster, insight):
        if frame == "research":
            return f"Write a short evidence-led brief on {topic}: which datapoint is surfacing, what it may mean, and what still needs confirmation."
        if frame == "infrastructure":
            return f"Write a short explainer on {topic}: which infrastructure change keeps surfacing, and who it could actually change workflows for."
        if frame == "adoption":
            return f"Write a short read on {topic}: which usage claim is surfacing, and what would distinguish real adoption from temporary noise."
        if insight.promotional_ratio >= 0.6:
            return f"Write a short read on {topic}: separate the campaign mechanics from any real usage or market signal underneath them."
        return f"Write a clean explainer on {topic}: what the visible posts are actually pointing to, and what still looks unproven."

    if frame == "launch":
        return f"Turn {focus} into a short brief: what is actually new, who it is for, and what would make it stick beyond launch-week chatter."
    if frame == "adoption":
        return f"Use {focus} as the lead proof point, then explain whether this looks like durable usage or a short-lived growth spike."
    if frame == "infrastructure":
        return f"Explain the infrastructure change underneath {focus}, and spell out which workflow it meaningfully changes for operators or builders."
    if frame == "market-structure":
        return f"Write the market-structure read on {focus}: where access, liquidity, or distribution shifts if this cluster is real."
    if frame == "research":
        return f"Convert {focus} into an evidence-led note: what the datapoint suggests, what it does not prove yet, and what would confirm it next."
    return f"Write a clean explainer on {topic} anchored in {focus}, with more interpretation than recap."


def build_snapshot_read(cluster: TopicCluster, insight: ClusterInsight) -> str:
    post_count = len(cluster.posts)
    author_count = len(cluster_authors(cluster))
    phrase_text = join_phrases(insight.supporting_phrases[:2])

    if cluster.signals and cluster.signals.velocity >= 2.5:
        strength = "This is one of the stronger snapshot clusters."
    elif cluster.signals and cluster.signals.velocity >= 1.2:
        strength = "This is a middling but timely snapshot cluster."
    else:
        strength = "This is a light snapshot signal."

    detail = f"It is based on {post_count} captured posts from {author_count} accounts"
    if phrase_text:
        detail += f", with repeated emphasis on {phrase_text}"
    detail += "."
    return f"{strength} {detail} Treat it as a directional read, not a full-network conclusion."


def build_angle(cluster: TopicCluster, audience: AudienceProfile, insight: ClusterInsight) -> str:
    focus = preferred_focus(cluster, insight)
    secondary = best_secondary_phrase(cluster, insight, focus)
    frame_detail = FRAME_DESCRIPTIONS[insight.frame]
    if not has_confident_focus(cluster, insight):
        return "Start with the topic, pull out one concrete claim from the visible posts, add the takeaway your audience should care about, and note what would strengthen the case."
    if secondary:
        return f"Start with {focus}, connect it to {secondary}, then land the takeaway: {frame_detail}."
    return f"Start with {focus}, then make the takeaway clear: {frame_detail}."


def pick_format(audience: AudienceProfile) -> str:
    if audience.preferred_formats:
        return audience.preferred_formats[0]
    return "short_thread"


def build_audience_fit(cluster: TopicCluster, audience: AudienceProfile, insight: ClusterInsight) -> str:
    if insight.frame == "infrastructure":
        return f"Fits {audience.niche} because the visible posts point to tooling and integration changes, but stop short of explaining the workflow impact."
    if insight.frame == "market-structure":
        return f"Fits {audience.niche} because the cluster touches access, liquidity, and distribution rather than just brand promotion."
    if insight.frame == "research":
        return f"Fits {audience.niche} because the visible posts surface a datapoint, but leave the interpretation and caveats underexplained."
    if insight.frame == "adoption":
        return f"Fits {audience.niche} because the cluster contains usage claims that still need translation into a durable narrative."
    return f"Fits {audience.niche} because there is enough repeated signal here for an explainer, but not enough explanation in the source posts themselves."


def build_risks(cluster: TopicCluster, insight: ClusterInsight) -> list[str]:
    risks: list[str] = []
    post_count = len(cluster.posts)
    author_count = len(cluster_authors(cluster))

    if post_count <= 3:
        risks.append("The sample is thin. Write this as a snapshot read, not as a market-wide conclusion.")
    if author_count <= 1:
        risks.append("Most of the visible signal comes from one account, so do not frame it as broad consensus.")
    if insight.promotional_ratio >= 0.4:
        risks.append("A large share of the source posts are promotional or campaign-like. Separate the underlying signal from the call-to-action wrapper.")
    if insight.focus.lower() in {normalize_keyword(cluster.label or ""), "", "launch"}:
        risks.append("The focus phrase is still generic. Sharpen it with one concrete metric, customer, or workflow before posting.")
    if cluster.signals and cluster.signals.saturation_risk >= 0.6:
        risks.append("This cluster is already crowded, so a recap will blend in unless you add a sharper interpretation.")

    if not risks:
        risks.append("Do not mirror the source posts. Add the missing implication, caveat, or next signal to watch.")
    return risks[:3]


def build_hooks(cluster: TopicCluster, insight: ClusterInsight) -> list[str]:
    focus = preferred_focus(cluster, insight)
    secondary = best_secondary_phrase(cluster, insight, focus)

    if not has_confident_focus(cluster, insight):
        topic = cluster.label or "this cluster"
        return [
            f"Open with the broad claim around {topic}, then tighten it to the one source post or datapoint that actually matters.",
            f"Ask what would need to happen next for the {topic} narrative in this snapshot to become real, rather than repeating the current chatter.",
        ]

    options = [
        f"Open with the concrete detail: {focus}. Then explain why that matters beyond the headline.",
        f"Frame the post around the harder question: if {focus} is real, what changes next for the market or workflow around it?",
    ]
    if secondary:
        options[1] = f"Pair {focus} with {secondary}, then explain whether the connection is real signal or just adjacent chatter."
    return options[:MAX_HOOKS]


def build_evidence(cluster: TopicCluster, insight: ClusterInsight) -> list[dict[str, str | None]]:
    post_count = len(cluster.posts)
    author_count = len(cluster_authors(cluster))
    phrase_text = join_phrases(insight.supporting_phrases[:3])
    evidence: list[dict[str, str | None]] = [
        {
            "text": f"The visible snapshot contains {post_count} captured posts from {author_count} accounts in this cluster.",
            "type": "volume",
        }
    ]
    if phrase_text and has_confident_focus(cluster, insight):
        evidence.append(
            {
                "text": f"Repeated phrases in the cluster include {phrase_text}, which is stronger than relying on the label alone.",
                "type": "phrase",
            }
        )
    else:
        evidence.append(
            {
                "text": "The visible posts do not yet converge on a single clean phrase, so this cluster still needs analyst judgment instead of autopilot copy.",
                "type": "phrase",
            }
        )
    if insight.representative_snippet:
        evidence.append(
            {
                "text": f"The strongest visible post centers on {insight.representative_snippet}.",
                "type": "author",
                "source_post_id": representative_post_id(cluster),
            }
        )
    if insight.promotional_ratio >= 0.4:
        evidence.append(
            {
                "text": "A noticeable share of the visible posts are promotional, so the opportunity is to extract the underlying signal rather than repeat the campaign copy.",
                "type": "gap",
            }
        )
    else:
        evidence.append(
            {
                "text": "Most visible posts still stop at announcement or reaction level, leaving room for a clearer explainer.",
                "type": "gap",
            }
        )
    return evidence[:MAX_EVIDENCE_ITEMS]


def cluster_document(cluster: TopicCluster) -> str:
    pieces: list[str] = []
    for link in cluster.posts:
        if link.post is None:
            continue
        if link.post.features and link.post.features.clean_text:
            pieces.append(link.post.features.clean_text)
        else:
            pieces.append(clean_text(link.post.body))
    return " ".join(piece for piece in pieces if piece)


def filter_phrases(cluster: TopicCluster, phrases: list[str]) -> list[str]:
    filtered: list[str] = []
    label_tokens = set(normalize_keyword(token) for token in (cluster.label or "").lower().split())

    for phrase in phrases:
        normalized_phrase = normalize_keyword(phrase.replace(" ", ""))
        phrase_words = [normalize_keyword(word) for word in phrase.split()]
        if len(phrase_words) < MIN_FOCUS_PHRASE_WORDS:
            continue
        if all(word in GENERIC_FOCUS_TERMS for word in phrase_words):
            continue
        if len(phrase_words) == 1 and phrase_words[0] in label_tokens:
            continue
        if normalized_phrase in GENERIC_FOCUS_TERMS:
            continue
        filtered.append(phrase)
        if len(filtered) >= MAX_TOP_PHRASES:
            break

    return filtered


def choose_focus(cluster: TopicCluster, phrases: list[str]) -> str:
    label = cluster.label or "the visible cluster"
    label_tokens = {normalize_keyword(token) for token in label.lower().split()}
    ranked = sorted(phrases, key=lambda phrase: focus_score(label_tokens, phrase), reverse=True)
    for phrase in ranked:
        if focus_score(label_tokens, phrase) > 0:
            return apply_label_context(label, phrase)

    fallback_terms = cluster_terms(cluster)
    if fallback_terms:
        return apply_label_context(label, fallback_terms[0])
    return label


def infer_frame(cluster: TopicCluster, phrases: list[str]) -> str:
    score = Counter()
    haystack = " ".join([cluster_document(cluster), *phrases]).lower()
    for frame, markers in FRAME_MARKERS.items():
        for marker in markers:
            score[frame] += haystack.count(marker)

    if not score:
        return "general"

    frame, value = score.most_common(1)[0]
    return frame if value > 0 else "general"


def cluster_terms(cluster: TopicCluster) -> list[str]:
    terms = Counter()
    label_tokens = set(normalize_keyword(token) for token in (cluster.label or "").lower().split())
    for link in cluster.posts:
        if link.post and link.post.features:
            for term in link.post.features.keywords:
                normalized = normalize_keyword(term)
                if not normalized or normalized in GENERIC_FOCUS_TERMS or normalized in label_tokens:
                    continue
                terms[prettify_phrase(normalized)] += 1
    return [term for term, _ in terms.most_common(MAX_TOP_PHRASES)]


def cluster_keyword_set(cluster: TopicCluster) -> set[str]:
    keywords: set[str] = set()
    for link in cluster.posts:
        if link.post and link.post.features:
            keywords.update(normalize_keyword(term) for term in link.post.features.keywords if normalize_keyword(term))
    return keywords


def merge_ranked_phrases(*groups: list[str]) -> list[str]:
    merged: list[str] = []
    for group in groups:
        for phrase in group:
            if phrase in merged:
                continue
            merged.append(phrase)
            if len(merged) >= MAX_TOP_PHRASES:
                return merged
    return merged


def cluster_authors(cluster: TopicCluster) -> set[str]:
    return {
        link.post.author_id or link.post.author_handle
        for link in cluster.posts
        if link.post is not None
    }


def representative_post_id(cluster: TopicCluster) -> str | None:
    representative = next((link.post_id for link in cluster.posts if link.is_representative), None)
    if representative:
        return representative
    first = next((link.post_id for link in cluster.posts if link.post_id), None)
    return first


def cluster_post_document(link: ClusterPost) -> str:
    if link.post is None:
        return ""
    if link.post.features and link.post.features.clean_text:
        return link.post.features.clean_text
    return clean_text(link.post.body)


def representative_snippet(cluster: TopicCluster) -> str | None:
    representative = next((link.post for link in cluster.posts if link.is_representative and link.post is not None), None)
    if representative is None:
        representative = next((link.post for link in cluster.posts if link.post is not None), None)
    if representative is None:
        return None

    text = representative.body
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"Quote\s+[^:]+:\s*", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"[@#](\w+)", r" \1 ", text)
    text = re.sub(r"\s+", " ", text).strip()
    words = text.split()
    if not words:
        return None
    return " ".join(words[:MAX_REPRESENTATIVE_WORDS]).rstrip(".,:;!?")


def is_promotional_text(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in PROMOTIONAL_MARKERS)


def focus_score(label_tokens: set[str], phrase: str) -> int:
    words = [normalize_keyword(word) for word in phrase.split()]
    if not words:
        return 0
    score = 0
    if len(words) > 1:
        score += 3
    for word in words:
        if word in label_tokens:
            score += 0
            continue
        if word in GENERIC_FOCUS_TERMS:
            score -= 1
            continue
        score += 2
    return score


def join_phrases(phrases: list[str]) -> str:
    if not phrases:
        return ""
    if len(phrases) == 1:
        return phrases[0]
    if len(phrases) == 2:
        return f"{phrases[0]} and {phrases[1]}"
    return f"{', '.join(phrases[:-1])}, and {phrases[-1]}"


def prettify_phrase(value: str) -> str:
    tokens = [token for token in value.replace("_", " ").split() if token]
    pretty_tokens: list[str] = []
    for token in tokens:
        lowered = token.lower()
        if lowered == "ai":
            pretty_tokens.append("AI")
        elif lowered == "ccip":
            pretty_tokens.append("CCIP")
        elif lowered == "dex":
            pretty_tokens.append("DEX")
        elif lowered == "os":
            pretty_tokens.append("OS")
        elif lowered.isdigit():
            pretty_tokens.append(lowered)
        else:
            pretty_tokens.append(lowered)
    return " ".join(pretty_tokens)


def apply_label_context(label: str, phrase: str) -> str:
    label_tokens = {normalize_keyword(token) for token in label.lower().split()}
    phrase_tokens = [normalize_keyword(token) for token in phrase.split()]
    if len(phrase_tokens) == 1 and label_tokens and phrase_tokens[0] not in label_tokens:
        return f"{label} {phrase}"
    return phrase


def has_confident_focus(cluster: TopicCluster, insight: ClusterInsight) -> bool:
    label_tokens = {normalize_keyword(token) for token in (cluster.label or "").lower().split()}
    keyword_set = cluster_keyword_set(cluster)
    phrase_tokens = [normalize_keyword(token) for token in insight.focus.split()]
    if insight.promotional_ratio >= 0.4:
        return False
    if insight.focus not in insight.supporting_phrases:
        return False
    if len(phrase_tokens) < 2:
        return False
    if focus_score(label_tokens, insight.focus) < 5:
        return False
    return any(token in keyword_set for token in phrase_tokens if token not in label_tokens)


def preferred_focus(cluster: TopicCluster, insight: ClusterInsight) -> str:
    return insight.focus if has_confident_focus(cluster, insight) else (cluster.label or "this cluster")


def best_secondary_phrase(cluster: TopicCluster, insight: ClusterInsight, focus: str) -> str | None:
    label_tokens = {normalize_keyword(token) for token in (cluster.label or "").lower().split()}
    for phrase in insight.supporting_phrases:
        if phrase == focus:
            continue
        if focus_score(label_tokens, phrase) >= 3:
            return phrase
    return None
