"""Route integration tests. Header identities exist ONLY in this test fixture.

Production mounts the router with a real OIDC/session dependency; these tests
exercise HTTP validation, domain behavior, RBAC, and the mandatory dependency.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from app.api.features import Principal, create_router
from app.services.freshness_store import FreshnessStore

BASE = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
POLICY = {"expected_partitions": ["us", "eu"], "max_age_seconds": 60}


async def fixture_identity(request: Request):
    role = request.headers.get("X-Test-Principal")
    if role not in {"admin", "operator", "viewer"}:
        raise HTTPException(401, "Login required")
    if request.method not in {"GET", "HEAD", "OPTIONS"} and request.headers.get("X-CSRF-Token") != "test-csrf":
        raise HTTPException(403, "CSRF validation failed")
    return Principal("test-"+role, frozenset({role}))


class FeatureApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = FreshnessStore(Path(self.temp.name)/"db.sqlite")
        self.store.initialize()
        self.app = FastAPI()
        self.app.include_router(create_router(self.store, fixture_identity, lambda: BASE))
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.admin = {"X-Test-Principal": "admin", "X-CSRF-Token": "test-csrf"}
        self.operator = {"X-Test-Principal": "operator", "X-CSRF-Token": "test-csrf"}
        self.viewer = {"X-Test-Principal": "viewer", "X-CSRF-Token": "test-csrf"}

    def policy(self):
        result = self.client.put("/api/features/risk", json=POLICY, headers=self.admin)
        self.assertEqual(result.status_code, 200)

    def materialization(self, event_id="r1", age=30, completed=None):
        return {"event_id": event_id, "partition": "us", "source_watermark": (BASE-timedelta(seconds=age)).isoformat(), "completed_at": (completed or BASE).isoformat(), "row_count": 12}

    def test_every_data_route_requires_authentication(self):
        for path in ["features", "features/risk/health", "features/risk/incidents", "features/risk/timeline", "features/risk/audit"]:
            with self.subTest(path=path):
                self.assertEqual(self.client.get("/api/"+path).status_code, 401)
        self.assertEqual(self.client.put("/api/features/risk", json=POLICY).status_code, 401)
        self.assertEqual(self.client.post("/api/features/risk/evaluate").status_code, 401)

    def test_viewer_cannot_create_or_evaluate(self):
        self.assertEqual(self.client.put("/api/features/risk", json=POLICY, headers=self.viewer).status_code, 403)
        self.assertEqual(self.client.post("/api/features/risk/evaluate", headers=self.viewer).status_code, 403)

    def test_operator_can_save_and_ingest(self):
        self.assertEqual(self.client.put("/api/features/risk", json=POLICY, headers=self.operator).status_code, 200)
        result = self.client.post("/api/features/risk/materializations", json=self.materialization(), headers=self.operator)
        self.assertEqual(result.status_code, 201)
        self.assertTrue(result.json()["created"])

    def test_mutations_cannot_bypass_authentication_dependency(self):
        result = self.client.put("/api/features/risk", json=POLICY, headers={"X-Test-Principal": "admin"})
        self.assertEqual(result.status_code, 403)
        self.assertEqual(self.store.policies(), [])

    def test_optimistic_policy_conflict_is_http_409(self):
        self.policy()
        result = self.client.put("/api/features/risk", json=POLICY, headers=self.admin)
        self.assertEqual(result.status_code, 409)
        result = self.client.put("/api/features/risk", json={**POLICY, "expected_version": 1}, headers=self.admin)
        self.assertEqual(result.json()["version"], 2)

    def test_event_replay_and_conflict_have_distinct_results(self):
        self.policy()
        body = self.materialization()
        first = self.client.post("/api/features/risk/materializations", json=body, headers=self.operator)
        replay = self.client.post("/api/features/risk/materializations", json=body, headers=self.operator)
        conflict = self.client.post("/api/features/risk/materializations", json={**body,"row_count":13}, headers=self.operator)
        self.assertTrue(first.json()["created"])
        self.assertFalse(replay.json()["created"])
        self.assertEqual(conflict.status_code, 409)

    def test_unknown_features_return_404(self):
        for path in ["health", "incidents"]:
            self.assertEqual(self.client.get("/api/features/unknown/"+path, headers=self.viewer).status_code, 404)
        self.assertEqual(self.client.post("/api/features/unknown/materializations", json=self.materialization(), headers=self.operator).status_code, 404)

    def test_naive_and_future_event_times_are_rejected(self):
        self.policy()
        naive = {**self.materialization(), "source_watermark": "2026-01-01T11:59:30"}
        future = self.materialization(completed=BASE+timedelta(seconds=1))
        for body in [naive, future]:
            self.assertEqual(self.client.post("/api/features/risk/materializations", json=body, headers=self.operator).status_code, 422)

    def test_health_get_is_read_only_and_reports_missing_partitions(self):
        self.policy()
        before = len(self.store.audit("risk"))
        result = self.client.get("/api/features/risk/health", headers=self.viewer)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["missing_count"], 2)
        self.assertEqual(len(self.store.audit("risk")), before)
        self.assertEqual(self.store.timeline("risk"), [])

    def test_evaluation_persists_incidents_once(self):
        self.policy()
        first = self.client.post("/api/features/risk/evaluate", headers=self.operator)
        second = self.client.post("/api/features/risk/evaluate", headers=self.operator)
        self.assertEqual(len(first.json()["transitions"]), 2)
        self.assertEqual(second.json()["transitions"], [])
        records = self.client.get("/api/features/risk/incidents", headers=self.viewer).json()["incidents"]
        self.assertEqual(len(records), 2)

    def test_audit_is_admin_only_and_uses_authenticated_actor(self):
        self.policy()
        self.assertEqual(self.client.get("/api/features/risk/audit", headers=self.operator).status_code, 403)
        self.assertEqual(self.client.get("/api/features/risk/audit", headers=self.viewer).status_code, 403)
        result = self.client.get("/api/features/risk/audit", headers=self.admin)
        self.assertEqual(result.json()["events"][0]["actor"], "test-admin")

    def test_client_cannot_supply_an_audit_actor_or_role(self):
        for extra in [{"actor":"administrator"}, {"roles":["admin"]}]:
            self.assertEqual(self.client.put("/api/features/risk", json={**POLICY,**extra}, headers=self.operator).status_code, 422)

    def test_pagination_validation_and_cursor(self):
        self.policy()
        self.client.post("/api/features/risk/evaluate", headers=self.operator)
        first = self.client.get("/api/features/risk/timeline?limit=1", headers=self.viewer).json()
        second = self.client.get("/api/features/risk/timeline?after="+str(first["next_cursor"]), headers=self.viewer).json()
        self.assertEqual(len(first["events"])+len(second["events"]), 2)
        for query in ["limit=0", "limit=1001", "after=-1"]:
            self.assertEqual(self.client.get("/api/features/risk/timeline?"+query, headers=self.viewer).status_code, 422)
