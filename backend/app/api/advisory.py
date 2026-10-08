"""Opt-in AI explanation of a server-computed freshness snapshot."""
from dataclasses import asdict
from fastapi import APIRouter, Depends
from fastapi.encoders import jsonable_encoder
from starlette.concurrency import run_in_threadpool
from app.api.features import Principal, current_time, evaluation_payload, invoke_domain, require_role
from app.services.llm_advisory import LLMSettings, advise


def create_advisory_router(store, authenticate, settings: LLMSettings | None, *, clock=current_time, transport=None):
    router = APIRouter(prefix="/api", tags=["advisory"])

    @router.post("/features/{feature_set}/advisory")
    async def explanation(feature_set: str, principal: Principal = Depends(authenticate)):
        require_role(principal, {"operator", "admin"})
        snapshot = await run_in_threadpool(invoke_domain, store.snapshot, feature_set, clock())
        evidence = jsonable_encoder(evaluation_payload(snapshot))
        result = await advise(settings, evidence, transport=transport)
        return {**asdict(result), "feature_set": feature_set, "evaluated_at": evidence["evaluated_at"]}

    return router
