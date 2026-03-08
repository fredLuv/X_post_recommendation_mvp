from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from x_trend_idea_mvp.database import Base, engine, get_db
from x_trend_idea_mvp.models import (
    AudienceProfile,
    Recommendation,
    RecommendationFeedback,
    TopicCluster,
    TrackedQuery,
)
from x_trend_idea_mvp.schemas import (
    AudienceCreate,
    AudienceRead,
    ClusterBuildRequest,
    ClusterRead,
    FeedbackCreate,
    FixtureIngestRequest,
    IngestRequest,
    IngestResponse,
    RecommendationGenerateRequest,
    RecommendationRead,
    TrackedQueryCreate,
    TrackedQueryRead,
)
from x_trend_idea_mvp.services.clustering import build_clusters
from x_trend_idea_mvp.services.fixture_ingestion import ingest_fixture_file
from x_trend_idea_mvp.services.ingestion import ingest_recent_posts
from x_trend_idea_mvp.services.recommendations import generate_recommendations
from x_trend_idea_mvp.services.x_api import XApiConfigurationError


app = FastAPI(title="X Trend Idea MVP")
Base.metadata.create_all(bind=engine)
project_dir = Path(__file__).parent
frontend_dist_dir = project_dir / "web" / "dist"
fallback_web_dir = project_dir / "web"

if (frontend_dist_dir / "assets").exists():
    app.mount("/assets", StaticFiles(directory=frontend_dist_dir / "assets"), name="assets")
elif (fallback_web_dir / "assets").exists():
    app.mount("/assets", StaticFiles(directory=fallback_web_dir / "assets"), name="assets")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    if (frontend_dist_dir / "index.html").exists():
        return FileResponse(frontend_dist_dir / "index.html")
    return FileResponse(fallback_web_dir / "index.html")


@app.get("/favicon.svg", include_in_schema=False)
def favicon() -> FileResponse:
    if (frontend_dist_dir / "favicon.svg").exists():
        return FileResponse(frontend_dist_dir / "favicon.svg")
    return FileResponse(fallback_web_dir / "assets" / "favicon.svg")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/audiences", response_model=AudienceRead)
def create_audience(payload: AudienceCreate, db: Session = Depends(get_db)) -> AudienceProfile:
    audience = AudienceProfile(**payload.model_dump())
    db.add(audience)
    db.commit()
    db.refresh(audience)
    return audience


@app.post("/queries", response_model=TrackedQueryRead)
def create_query(payload: TrackedQueryCreate, db: Session = Depends(get_db)) -> TrackedQuery:
    if db.get(AudienceProfile, payload.audience_profile_id) is None:
        raise HTTPException(status_code=404, detail="Audience profile not found.")
    tracked_query = TrackedQuery(**payload.model_dump())
    db.add(tracked_query)
    db.commit()
    db.refresh(tracked_query)
    return tracked_query


@app.post("/ingest/x", response_model=IngestResponse)
def ingest_x(payload: IngestRequest, db: Session = Depends(get_db)) -> IngestResponse:
    query_stmt = select(TrackedQuery).where(
        TrackedQuery.audience_profile_id == payload.audience_profile_id,
        TrackedQuery.active.is_(True),
    )
    if payload.tracked_query_ids:
        query_stmt = query_stmt.where(TrackedQuery.id.in_(payload.tracked_query_ids))
    tracked_queries = db.scalars(query_stmt).all()
    if not tracked_queries:
        raise HTTPException(status_code=404, detail="No active tracked queries found.")
    try:
        stats = ingest_recent_posts(
            db,
            tracked_queries=tracked_queries,
            lookback_days=payload.lookback_days,
            max_pages_per_query=payload.max_pages_per_query,
        )
    except XApiConfigurationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return IngestResponse(**stats.__dict__)


@app.post("/ingest/fixture", response_model=IngestResponse)
def ingest_fixture(payload: FixtureIngestRequest, db: Session = Depends(get_db)) -> IngestResponse:
    query_stmt = select(TrackedQuery).where(
        TrackedQuery.audience_profile_id == payload.audience_profile_id,
        TrackedQuery.active.is_(True),
    )
    if payload.tracked_query_ids:
        query_stmt = query_stmt.where(TrackedQuery.id.in_(payload.tracked_query_ids))
    tracked_queries = db.scalars(query_stmt).all()
    if not tracked_queries:
        raise HTTPException(status_code=404, detail="No active tracked queries found.")
    stats = ingest_fixture_file(
        db,
        tracked_queries=tracked_queries,
        fixture_path=payload.fixture_path,
        lookback_days=payload.lookback_days,
        default_tracked_query_id=payload.default_tracked_query_id,
    )
    return IngestResponse(**stats.__dict__)


@app.post("/clusters/build", response_model=list[ClusterRead])
def cluster_topics(payload: ClusterBuildRequest, db: Session = Depends(get_db)) -> list[ClusterRead]:
    if db.get(AudienceProfile, payload.audience_profile_id) is None:
        raise HTTPException(status_code=404, detail="Audience profile not found.")
    clusters = build_clusters(
        db,
        audience_profile_id=payload.audience_profile_id,
        lookback_days=payload.lookback_days,
        min_cluster_size=payload.min_cluster_size,
    )
    return [
        ClusterRead(
            id=cluster.id,
            label=cluster.label,
            summary=cluster.summary,
            time_window_start=cluster.time_window_start,
            time_window_end=cluster.time_window_end,
            post_count=len(cluster.posts),
        )
        for cluster in clusters
    ]


@app.post("/recommendations/generate", response_model=list[RecommendationRead])
def generate(payload: RecommendationGenerateRequest, db: Session = Depends(get_db)) -> list[RecommendationRead]:
    if db.get(AudienceProfile, payload.audience_profile_id) is None:
        raise HTTPException(status_code=404, detail="Audience profile not found.")
    try:
        recommendations = generate_recommendations(
            db,
            audience_profile_id=payload.audience_profile_id,
            max_recommendations=payload.max_recommendations,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [serialize_recommendation(item) for item in recommendations]


@app.get("/recommendations", response_model=list[RecommendationRead])
def list_recommendations(audience_id: str, db: Session = Depends(get_db)) -> list[RecommendationRead]:
    recommendations = db.scalars(
        select(Recommendation)
        .options(joinedload(Recommendation.evidence_items))
        .where(Recommendation.audience_profile_id == audience_id)
        .order_by(Recommendation.generated_at.desc())
    ).unique().all()
    return [serialize_recommendation(item) for item in recommendations]


@app.post("/recommendations/{recommendation_id}/feedback")
def submit_feedback(recommendation_id: str, payload: FeedbackCreate, db: Session = Depends(get_db)) -> dict[str, str]:
    if db.get(Recommendation, recommendation_id) is None:
        raise HTTPException(status_code=404, detail="Recommendation not found.")
    db.add(RecommendationFeedback(recommendation_id=recommendation_id, **payload.model_dump()))
    db.commit()
    return {"status": "recorded"}


@app.get("/topics")
def list_topics(audience_id: str, db: Session = Depends(get_db)) -> list[dict[str, str | int]]:
    rows = db.execute(
        select(
            TopicCluster.id,
            TopicCluster.label,
            TopicCluster.summary,
            func.count().label("post_count"),
        )
        .join(TopicCluster.posts)
        .where(TopicCluster.audience_profile_id == audience_id)
        .group_by(TopicCluster.id, TopicCluster.label, TopicCluster.summary)
        .order_by(func.count().desc())
    ).all()
    return [
        {"id": row.id, "label": row.label or "", "summary": row.summary or "", "post_count": row.post_count}
        for row in rows
    ]


def serialize_recommendation(item: Recommendation) -> RecommendationRead:
    return RecommendationRead(
        id=item.id,
        topic=item.topic,
        recommendation=item.recommendation,
        why_now=item.why_now,
        suggested_angle=item.suggested_angle,
        format=item.format,
        audience_fit=item.audience_fit,
        risks=item.risks,
        draft_hooks=item.draft_hooks,
        generated_at=item.generated_at,
        evidence=item.evidence_items,
    )
