from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from x_trend_idea_mvp.constants import (
    DEFAULT_FIXTURE_LOOKBACK_DAYS,
    DEFAULT_LIVE_LOOKBACK_DAYS,
    MAX_FIXTURE_LOOKBACK_DAYS,
    MAX_LIVE_LOOKBACK_DAYS,
)


class AudienceCreate(BaseModel):
    name: str
    niche: str
    description: str | None = None
    preferred_formats: list[str] = Field(default_factory=list)
    excluded_topics: list[str] = Field(default_factory=list)


class AudienceRead(AudienceCreate):
    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: datetime


class TrackedQueryCreate(BaseModel):
    audience_profile_id: str
    query: str
    kind: str = "keyword"
    active: bool = True


class TrackedQueryRead(TrackedQueryCreate):
    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: datetime


class IngestRequest(BaseModel):
    audience_profile_id: str
    tracked_query_ids: list[str] | None = None
    lookback_days: int = Field(default=DEFAULT_LIVE_LOOKBACK_DAYS, ge=1, le=MAX_LIVE_LOOKBACK_DAYS)
    max_pages_per_query: int = Field(default=3, ge=1, le=20)


class IngestResponse(BaseModel):
    posts_inserted: int
    posts_updated: int
    query_count: int
    started_at: datetime
    ended_at: datetime


class FixtureIngestRequest(BaseModel):
    audience_profile_id: str
    fixture_path: str
    tracked_query_ids: list[str] | None = None
    lookback_days: int = Field(default=DEFAULT_FIXTURE_LOOKBACK_DAYS, ge=1, le=MAX_FIXTURE_LOOKBACK_DAYS)
    default_tracked_query_id: str | None = None


class ClusterBuildRequest(BaseModel):
    audience_profile_id: str
    lookback_days: int = Field(default=DEFAULT_FIXTURE_LOOKBACK_DAYS, ge=1, le=MAX_FIXTURE_LOOKBACK_DAYS)
    min_cluster_size: int = Field(default=3, ge=2, le=50)


class ClusterRead(BaseModel):
    id: str
    label: str | None
    summary: str | None
    time_window_start: datetime
    time_window_end: datetime
    post_count: int


class RecommendationGenerateRequest(BaseModel):
    audience_profile_id: str
    max_recommendations: int = Field(default=10, ge=1, le=20)


class RecommendationEvidenceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    evidence_text: str
    evidence_type: str
    source_post_id: str | None


class RecommendationRead(BaseModel):
    id: str
    topic: str
    recommendation: str
    why_now: str
    suggested_angle: str
    format: str
    audience_fit: str
    risks: list[str]
    draft_hooks: list[str]
    generated_at: datetime
    evidence: list[RecommendationEvidenceRead]


class FeedbackCreate(BaseModel):
    feedback_type: str
    note: str | None = None
