"""Feature-monitor API with explicit authentication and authorization seams.

The application must supply its session-authentication dependency. Mutation
routes enforce operator/admin authorization regardless of the caller's UI.
The dependency is also responsible for validating CSRF on cookie mutations.
"""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Callable, FrozenSet
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from app.domain.freshness import FreshnessPolicy, Materialization, identifier
from app.services.freshness_store import FreshnessStore, NotFoundError, ConflictError


@dataclass(frozen=True)
class Principal:
    subject: str
    roles: FrozenSet[str]

    def __post_init__(self):
        identifier(self.subject, "subject")
        object.__setattr__(self, "roles", frozenset(self.roles))


class PolicyInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    expected_partitions: list[str] = Field(min_length=1, max_length=1000)
    max_age_seconds: float = Field(gt=0)
    grace_seconds: float = Field(default=0, ge=0)
    expected_version: int = Field(default=0, ge=0, strict=True)


class MaterializationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    event_id: str = Field(min_length=1, max_length=200)
    partition: str = Field(min_length=1, max_length=200)
    source_watermark: datetime
    completed_at: datetime
    row_count: int = Field(ge=0, strict=True)


def current_time() -> datetime:
    return datetime.now(timezone.utc)


def invoke_domain(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except NotFoundError as error:
        raise HTTPException(404, str(error)) from error
    except ConflictError as error:
        raise HTTPException(409, str(error)) from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


def require_role(principal: Principal, roles: set[str]):
    if not isinstance(principal, Principal) or not principal.roles.intersection(roles):
        raise HTTPException(403, "This action requires an authorized role")


def evaluation_payload(evaluation):
    return {**asdict(evaluation), "healthy": evaluation.healthy, "coverage": evaluation.coverage,
            "stale_count": sum(p.status == "stale" for p in evaluation.partitions),
            "missing_count": sum(p.status == "missing" for p in evaluation.partitions)}


def create_router(store: FreshnessStore, authenticate: Callable, clock: Callable[[], datetime] = current_time) -> APIRouter:
    """Mount with a real session dependency; there is no anonymous fallback."""
    router = APIRouter(prefix="/api", tags=["feature-freshness"])

    @router.get("/features")
    def features(principal: Principal = Depends(authenticate)):
        require_role(principal, {"viewer", "operator", "admin"})
        return {"features": store.policies()}

    @router.put("/features/{feature_set}")
    def save_policy(feature_set: str, body: PolicyInput, principal: Principal = Depends(authenticate)):
        require_role(principal, {"operator", "admin"})
        policy = invoke_domain(FreshnessPolicy, feature_set, tuple(body.expected_partitions), body.max_age_seconds, body.grace_seconds)
        version = invoke_domain(store.save_policy, policy, principal.subject, clock(), body.expected_version)
        return {"feature_set": feature_set, "version": version}

    @router.post("/features/{feature_set}/materializations", status_code=201)
    def ingest(feature_set: str, body: MaterializationInput, principal: Principal = Depends(authenticate)):
        require_role(principal, {"operator", "admin"})
        event = invoke_domain(Materialization, body.event_id, feature_set, body.partition, body.source_watermark, body.completed_at, body.row_count)
        created = invoke_domain(store.record_materialization, event, principal.subject, clock())
        return {"event_id": body.event_id, "created": created}

    @router.post("/features/{feature_set}/materializations/batch", status_code=201)
    def ingest_batch(feature_set: str, items: list[MaterializationInput], principal: Principal = Depends(authenticate)):
        require_role(principal, {"operator", "admin"})
        if not items:
            raise HTTPException(422, "Batch must contain at least one materialization")
        if len(items) > 500:
            raise HTTPException(422, "Batch size exceeds limit of 500 items")
        events = [
            invoke_domain(Materialization, item.event_id, feature_set, item.partition, item.source_watermark, item.completed_at, item.row_count)
            for item in items
        ]
        created, replayed = invoke_domain(store.record_batch, events, principal.subject, clock())
        return {"total": len(items), "created": created, "replayed": replayed, "event_ids": [m.event_id for m in items]}

    @router.get("/features/{feature_set}/export")
    def export_snapshot(feature_set: str, principal: Principal = Depends(authenticate)):
        require_role(principal, {"viewer", "operator", "admin"})
        policy_info = invoke_domain(store.policy_by_name, feature_set)
        evaluation = invoke_domain(store.snapshot, feature_set, clock())
        incidents = invoke_domain(store.incidents, feature_set)
        return {
            "feature_set": feature_set,
            "exported_at": clock().isoformat(),
            "policy": policy_info,
            "health": evaluation_payload(evaluation),
            "incidents": [asdict(i) for i in incidents],
        }

    @router.get("/features/{feature_set}/health")
    def health(feature_set: str, principal: Principal = Depends(authenticate)):
        require_role(principal, {"viewer", "operator", "admin"})
        return evaluation_payload(invoke_domain(store.snapshot, feature_set, clock()))

    @router.post("/features/{feature_set}/evaluate")
    def run_evaluation(feature_set: str, principal: Principal = Depends(authenticate)):
        require_role(principal, {"operator", "admin"})
        evaluation, incidents, transitions = invoke_domain(store.evaluate, feature_set, principal.subject, clock())
        return {"evaluation": evaluation_payload(evaluation), "incidents": [asdict(i) for i in incidents], "transitions": [asdict(t) for t in transitions]}

    @router.get("/features/{feature_set}/incidents")
    def incidents(feature_set: str, active_only: bool = True, principal: Principal = Depends(authenticate)):
        require_role(principal, {"viewer", "operator", "admin"})
        records = invoke_domain(store.incidents, feature_set)
        return {"incidents": [asdict(i) for i in records if not active_only or i.resolved_at is None]}

    @router.get("/features/{feature_set}/timeline")
    def timeline(feature_set: str, after: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=1000), principal: Principal = Depends(authenticate)):
        require_role(principal, {"viewer", "operator", "admin"})
        rows = store.timeline(feature_set, after, limit)
        return {"events": rows, "next_cursor": rows[-1]["sequence"] if rows else after}

    @router.get("/features/{feature_set}/audit")
    def audit(feature_set: str, after: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=1000), principal: Principal = Depends(authenticate)):
        require_role(principal, {"admin"})
        rows = store.audit(feature_set, after, limit)
        return {"events": rows, "next_cursor": rows[-1]["sequence"] if rows else after}

    return router
