from datetime import datetime, timezone
import json
import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from app.api.advisory import create_advisory_router
from app.api.features import Principal
from app.domain.freshness import FreshnessPolicy
from app.services.freshness_store import FreshnessStore
from app.services.llm_advisory import LLMSettings


@pytest.fixture
def harness(tmp_path):
    store = FreshnessStore(tmp_path / "state.sqlite")
    store.initialize()
    now = datetime.now(timezone.utc)
    store.save_policy(FreshnessPolicy("risk", ("us",), 300, 0), "operator", now, 0)
    identity = {"principal": Principal("operator", frozenset({"operator"}))}
    calls = []
    def authenticate():
        if identity["principal"] is None: raise HTTPException(401, "Sign in")
        return identity["principal"]
    def respond(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "The us partition has no recorded source watermark."}}]})
    def client(enabled=True):
        app = FastAPI()
        settings = LLMSettings("compatible", "fixture-model", "https://fixture.example.test/v1") if enabled else None
        app.include_router(create_advisory_router(store, authenticate, settings, clock=lambda: now, transport=httpx.MockTransport(respond)))
        return TestClient(app)
    return store, identity, calls, client


def test_advice_uses_server_snapshot_without_mutation(harness):
    store, identity, calls, client = harness
    before = store.policies()
    with client() as api:
        response = api.post("/api/features/risk/advisory")
    assert response.status_code == 200
    assert response.json()["advisory_only"] is True
    assert response.json()["status"] == "available"
    evidence = json.loads(calls[0]["messages"][-1]["content"])
    assert evidence["partitions"][0]["status"] == "missing"
    assert isinstance(evidence["evaluated_at"], str)
    assert store.policies() == before
    assert store.incidents("risk") == ()


@pytest.mark.parametrize("roles,status", [(None, 401), ({"viewer"}, 403), ({"unknown"}, 403)])
def test_advice_authorization_precedes_provider_call(harness, roles, status):
    _, identity, calls, client = harness
    identity["principal"] = None if roles is None else Principal("viewer", frozenset(roles))
    with client() as api: assert api.post("/api/features/risk/advisory").status_code == status
    assert calls == []


def test_missing_feature_and_disabled_provider_make_no_external_call(harness):
    _, _, calls, client = harness
    with client() as api: assert api.post("/api/features/absent/advisory").status_code == 404
    with client(False) as api:
        response = api.post("/api/features/risk/advisory")
        assert response.status_code == 200
        assert response.json()["status"] == "disabled"
    assert calls == []
