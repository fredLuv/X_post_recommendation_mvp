from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from x_trend_idea_mvp.database import Base


def uuid_str() -> str:
    return str(uuid.uuid4())


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AudienceProfile(TimestampMixin, Base):
    __tablename__ = "audience_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    niche: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text())
    preferred_formats: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    excluded_topics: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

    tracked_queries: Mapped[list["TrackedQuery"]] = relationship(back_populates="audience_profile", cascade="all, delete-orphan")
    recommendations: Mapped[list["Recommendation"]] = relationship(back_populates="audience_profile", cascade="all, delete-orphan")


class TrackedQuery(TimestampMixin, Base):
    __tablename__ = "tracked_queries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    audience_profile_id: Mapped[str] = mapped_column(ForeignKey("audience_profiles.id", ondelete="CASCADE"), nullable=False)
    query: Mapped[str] = mapped_column(String(500), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    audience_profile: Mapped["AudienceProfile"] = relationship(back_populates="tracked_queries")


class Post(TimestampMixin, Base):
    __tablename__ = "posts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    author_handle: Mapped[str] = mapped_column(String(200), nullable=False)
    author_id: Mapped[str | None] = mapped_column(String(64))
    body: Mapped[str] = mapped_column(Text(), nullable=False)
    lang: Mapped[str | None] = mapped_column(String(16))
    posted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    like_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reply_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    repost_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    quote_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    impression_count: Mapped[int | None] = mapped_column(Integer)
    url: Mapped[str] = mapped_column(Text(), nullable=False)
    raw_json: Mapped[dict] = mapped_column(JSON, nullable=False)

    features: Mapped["PostFeature | None"] = relationship(back_populates="post", cascade="all, delete-orphan", uselist=False)
    query_links: Mapped[list["PostQueryLink"]] = relationship(back_populates="post", cascade="all, delete-orphan")
    cluster_links: Mapped[list["ClusterPost"]] = relationship(back_populates="post", cascade="all, delete-orphan")


class PostQueryLink(Base):
    __tablename__ = "post_query_links"
    __table_args__ = (UniqueConstraint("post_id", "tracked_query_id", name="uq_post_query_link"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    post_id: Mapped[str] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), nullable=False)
    tracked_query_id: Mapped[str] = mapped_column(ForeignKey("tracked_queries.id", ondelete="CASCADE"), nullable=False)

    post: Mapped["Post"] = relationship(back_populates="query_links")
    tracked_query: Mapped["TrackedQuery"] = relationship()


class PostFeature(TimestampMixin, Base):
    __tablename__ = "post_features"

    post_id: Mapped[str] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), primary_key=True)
    clean_text: Mapped[str] = mapped_column(Text(), nullable=False)
    hashtags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    mentions: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    urls: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    keywords: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

    post: Mapped["Post"] = relationship(back_populates="features")


class TopicCluster(TimestampMixin, Base):
    __tablename__ = "topic_clusters"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    label: Mapped[str | None] = mapped_column(String(255))
    summary: Mapped[str | None] = mapped_column(Text())
    audience_profile_id: Mapped[str] = mapped_column(ForeignKey("audience_profiles.id", ondelete="CASCADE"), nullable=False)
    time_window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    time_window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    posts: Mapped[list["ClusterPost"]] = relationship(back_populates="cluster", cascade="all, delete-orphan")
    signals: Mapped["ClusterSignal | None"] = relationship(back_populates="cluster", cascade="all, delete-orphan", uselist=False)
    recommendations: Mapped[list["Recommendation"]] = relationship(back_populates="cluster", cascade="all, delete-orphan")


class ClusterPost(Base):
    __tablename__ = "cluster_posts"
    __table_args__ = (UniqueConstraint("cluster_id", "post_id", name="uq_cluster_post"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    cluster_id: Mapped[str] = mapped_column(ForeignKey("topic_clusters.id", ondelete="CASCADE"), nullable=False)
    post_id: Mapped[str] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), nullable=False)
    is_representative: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    cluster: Mapped["TopicCluster"] = relationship(back_populates="posts")
    post: Mapped["Post"] = relationship(back_populates="cluster_links")


class ClusterSignal(Base):
    __tablename__ = "cluster_signals"

    cluster_id: Mapped[str] = mapped_column(ForeignKey("topic_clusters.id", ondelete="CASCADE"), primary_key=True)
    post_count_24h: Mapped[int] = mapped_column(Integer, nullable=False)
    baseline_post_count_7d: Mapped[float] = mapped_column(Float, nullable=False)
    velocity: Mapped[float] = mapped_column(Float, nullable=False)
    persistence: Mapped[float] = mapped_column(Float, nullable=False)
    author_diversity: Mapped[float] = mapped_column(Float, nullable=False)
    novelty: Mapped[float] = mapped_column(Float, nullable=False)
    saturation_risk: Mapped[float] = mapped_column(Float, nullable=False)
    explanatory_gap: Mapped[float] = mapped_column(Float, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    cluster: Mapped["TopicCluster"] = relationship(back_populates="signals")


class Recommendation(TimestampMixin, Base):
    __tablename__ = "recommendations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    audience_profile_id: Mapped[str] = mapped_column(ForeignKey("audience_profiles.id", ondelete="CASCADE"), nullable=False)
    cluster_id: Mapped[str] = mapped_column(ForeignKey("topic_clusters.id", ondelete="CASCADE"), nullable=False)
    topic: Mapped[str] = mapped_column(String(255), nullable=False)
    recommendation: Mapped[str] = mapped_column(Text(), nullable=False)
    why_now: Mapped[str] = mapped_column(Text(), nullable=False)
    suggested_angle: Mapped[str] = mapped_column(Text(), nullable=False)
    format: Mapped[str] = mapped_column(String(50), nullable=False)
    audience_fit: Mapped[str] = mapped_column(Text(), nullable=False)
    risks: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    draft_hooks: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    internal_rank: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    audience_profile: Mapped["AudienceProfile"] = relationship(back_populates="recommendations")
    cluster: Mapped["TopicCluster"] = relationship(back_populates="recommendations")
    evidence_items: Mapped[list["RecommendationEvidence"]] = relationship(back_populates="recommendation", cascade="all, delete-orphan")


class RecommendationEvidence(Base):
    __tablename__ = "recommendation_evidence"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    recommendation_id: Mapped[str] = mapped_column(ForeignKey("recommendations.id", ondelete="CASCADE"), nullable=False)
    evidence_text: Mapped[str] = mapped_column(Text(), nullable=False)
    evidence_type: Mapped[str] = mapped_column(String(20), nullable=False)
    source_post_id: Mapped[str | None] = mapped_column(ForeignKey("posts.id", ondelete="SET NULL"))

    recommendation: Mapped["Recommendation"] = relationship(back_populates="evidence_items")


class RecommendationFeedback(TimestampMixin, Base):
    __tablename__ = "recommendation_feedback"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    recommendation_id: Mapped[str] = mapped_column(ForeignKey("recommendations.id", ondelete="CASCADE"), nullable=False)
    feedback_type: Mapped[str] = mapped_column(String(20), nullable=False)
    note: Mapped[str | None] = mapped_column(Text())
