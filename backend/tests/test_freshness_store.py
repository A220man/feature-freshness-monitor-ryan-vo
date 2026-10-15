from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from app.domain.freshness import FreshnessPolicy, Materialization
from app.services.freshness_store import FreshnessStore, ConflictError, NotFoundError

BASE = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)


class FreshnessStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = FreshnessStore(Path(self.temp.name)/"features.sqlite")
        self.store.initialize()
        self.policy = FreshnessPolicy("risk", ("us", "eu"), 60)
        self.store.save_policy(self.policy, "operator", BASE)

    def event(self, event_id="run-1", age=30):
        return Materialization(event_id, "risk", "us", BASE-timedelta(seconds=age), BASE, 25)

    def test_policy_optimistic_concurrency_prevents_lost_update(self):
        self.assertEqual(self.store.save_policy(self.policy, "operator", BASE, 1), 2)
        with self.assertRaises(ConflictError):
            self.store.save_policy(self.policy, "other", BASE, 1)
        self.assertEqual(self.store.policies()[0]["version"], 2)
        self.assertEqual(len(self.store.audit("risk")), 2)

    def test_invalid_actor_rolls_back_policy_change(self):
        with self.assertRaises(ValueError):
            self.store.save_policy(FreshnessPolicy("other", ("us",), 30), "", BASE)
        self.assertEqual(len(self.store.policies()), 1)

    def test_event_idempotency_does_not_duplicate_audit(self):
        self.assertTrue(self.store.record_materialization(self.event(), "ingestor", BASE))
        self.assertFalse(self.store.record_materialization(self.event(), "ingestor", BASE))
        self.assertEqual(len(self.store.audit("risk")), 2)

    def test_conflicting_event_identity_preserves_original(self):
        self.store.record_materialization(self.event(), "ingestor", BASE)
        with self.assertRaises(ConflictError):
            self.store.record_materialization(self.event(age=300), "ingestor", BASE)
        snapshot, _, _ = self.store.evaluate("risk", "operator", BASE)
        self.assertEqual(snapshot.partitions[0].source_age_seconds, 30)

    def test_unknown_feature_and_future_completion_rejected(self):
        with self.assertRaises(NotFoundError):
            self.store.evaluate("unknown", "operator", BASE)
        with self.assertRaises(ValueError):
            self.store.record_materialization(self.event(), "ingestor", BASE-timedelta(seconds=1))

    def test_two_concurrent_evaluations_open_each_incident_once(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.store.evaluate("risk", "operator", BASE), range(2)))
        self.assertEqual(sum(len(r[2]) for r in results), 2)
        self.assertEqual(len(self.store.timeline("risk")), 2)

    def test_missing_to_stale_then_recovered_timeline(self):
        self.store.evaluate("risk", "operator", BASE)
        self.store.record_materialization(self.event(age=120), "ingestor", BASE)
        self.store.evaluate("risk", "operator", BASE)
        self.store.record_materialization(Materialization("run-2", "risk", "us", BASE, BASE+timedelta(seconds=1), 40), "ingestor", BASE+timedelta(seconds=1))
        snapshot, records, transitions = self.store.evaluate("risk", "operator", BASE+timedelta(seconds=1))
        self.assertEqual(snapshot.partitions[0].status, "fresh")
        self.assertEqual(transitions[0].action, "resolved")
        self.assertEqual(len([r for r in records if r.partition == "us" and r.resolved_at is None]), 0)
        self.assertEqual([r["action"] for r in self.store.timeline("risk") if r["partition_name"] == "us"], ["opened", "resolved", "opened", "resolved"])

    def test_policy_partition_removal_resolves_orphan_incident(self):
        self.store.evaluate("risk", "operator", BASE)
        self.store.save_policy(FreshnessPolicy("risk", ("us",), 60), "operator", BASE+timedelta(seconds=1), 1)
        _, records, transitions = self.store.evaluate("risk", "operator", BASE+timedelta(seconds=1))
        self.assertEqual([(r.partition, r.action) for r in transitions], [("eu", "resolved")])
        self.assertTrue(all(r.resolved_at is not None for r in records if r.partition == "eu"))

    def test_evaluation_clock_cannot_roll_back_even_after_healthy_snapshot(self):
        self.store.save_policy(FreshnessPolicy("risk", ("us",), 60), "operator", BASE, 1)
        self.store.record_materialization(self.event(), "ingestor", BASE)
        snapshot, records, _ = self.store.evaluate("risk", "operator", BASE)
        self.assertTrue(snapshot.healthy)
        self.assertFalse(records)
        with self.assertRaises(ConflictError):
            self.store.evaluate("risk", "operator", BASE-timedelta(seconds=1))

    def test_data_and_incidents_survive_store_recreation(self):
        self.store.record_materialization(self.event(), "ingestor", BASE)
        self.store.evaluate("risk", "operator", BASE)
        reopened = FreshnessStore(self.store.path)
        reopened.initialize()
        self.assertEqual(reopened.policies(), self.store.policies())
        self.assertEqual(reopened.timeline("risk"), self.store.timeline("risk"))
        self.assertEqual(len(reopened.audit("risk")), 3)

    def test_cursor_pagination_has_no_duplicate_timeline_rows(self):
        self.store.evaluate("risk", "operator", BASE)
        first = self.store.timeline("risk", limit=1)
        rest = self.store.timeline("risk", after=first[0]["sequence"])
        self.assertEqual(len(first)+len(rest), 2)
        self.assertNotEqual(first[0]["sequence"], rest[0]["sequence"])

    def test_policy_update_cannot_rewrite_a_future_evaluation(self):
        self.store.evaluate("risk", "operator", BASE+timedelta(seconds=10))
        with self.assertRaises(ConflictError):
            self.store.save_policy(self.policy, "operator", BASE+timedelta(seconds=1), 1)
        self.assertEqual(self.store.policies()[0]["version"], 1)

    def test_record_batch_atomicity_and_deduplication(self):
        ev1 = Materialization("b1", "risk", "us", BASE-timedelta(seconds=20), BASE, 50)
        ev2 = Materialization("b2", "risk", "eu", BASE-timedelta(seconds=10), BASE, 30)
        created, replayed = self.store.record_batch([ev1, ev2], "ingestor", BASE)
        self.assertEqual((created, replayed), (2, 0))

        # Replaying exact batch
        created2, replayed2 = self.store.record_batch([ev1, ev2], "ingestor", BASE)
        self.assertEqual((created2, replayed2), (0, 2))

        # Conflicting event in batch fails
        conflicting = Materialization("b1", "risk", "us", BASE-timedelta(seconds=100), BASE, 999)
        with self.assertRaises(ConflictError):
            self.store.record_batch([conflicting], "ingestor", BASE)

    def test_policy_by_name_and_empty_batch(self):
        pol = self.store.policy_by_name("risk")
        self.assertEqual(pol["feature_set"], "risk")
        with self.assertRaises(NotFoundError):
            self.store.policy_by_name("nonexistent")
        self.assertEqual(self.store.record_batch([], "ingestor", BASE), (0, 0))

    def test_materializations_query_and_filtering(self):
        with self.assertRaises(NotFoundError):
            self.store.materializations("unknown_feature")
        with self.assertRaises(ValueError):
            self.store.materializations("risk", limit=0)
        with self.assertRaises(ValueError):
            self.store.materializations("risk", limit=501)

        # Initially empty
        self.assertEqual(self.store.materializations("risk"), [])

        # Ingest events across partitions
        ev1 = Materialization("m1", "risk", "us", BASE-timedelta(seconds=50), BASE-timedelta(seconds=10), 10)
        ev2 = Materialization("m2", "risk", "eu", BASE-timedelta(seconds=40), BASE-timedelta(seconds=5), 20)
        ev3 = Materialization("m3", "risk", "us", BASE-timedelta(seconds=20), BASE, 30)
        self.store.record_batch([ev1, ev2, ev3], "ingestor", BASE)

        # All events ordered by completed_at DESC
        all_events = self.store.materializations("risk")
        self.assertEqual(len(all_events), 3)
        self.assertEqual([e["event_id"] for e in all_events], ["m3", "m2", "m1"])

        # Filtered by partition
        us_events = self.store.materializations("risk", partition="us")
        self.assertEqual(len(us_events), 2)
        self.assertEqual([e["event_id"] for e in us_events], ["m3", "m1"])

        # With limit
        limited = self.store.materializations("risk", limit=1)
        self.assertEqual(len(limited), 1)
        self.assertEqual(limited[0]["event_id"], "m3")

    def test_webhook_registration_ssrf_and_signed_dispatch(self):
        from app.services.webhook_dispatcher import verify_webhook_signature

        # Reject non-HTTPS, localhost, private IPs, and short secrets
        for bad_url in [
            "http://alerts.example.com/hook",
            "https://localhost/hook",
            "https://127.0.0.1/hook",
            "https://10.0.0.5/hook",
            "https://169.254.169.254/latest/meta-data",
            "https:// internal.local/hook",
            "https://user:pass@alerts.example.com/hook",
        ]:
            with self.assertRaises(ValueError):
                self.store.register_webhook("risk", bad_url, ("opened",), "valid-signing-phrase", "operator", BASE)
        with self.assertRaises(ValueError):
            self.store.register_webhook("risk", "https://alerts.example.com/hook", ("opened",), "short", "operator", BASE)

        # Register valid webhook and verify secret is never returned or logged in audit
        hook = self.store.register_webhook(
            "risk",
            "https://alerts.example.com/freshness-hook",
            ("opened", "resolved"),
            "hmac-signing-phrase-one",
            "operator",
            BASE,
        )
        self.assertNotIn("signing_secret", hook)
        self.assertEqual(len(self.store.webhooks("risk")), 1)
        self.assertNotIn("hmac-signing-phrase-one", str(self.store.audit("risk")))

        # Duplicate endpoint for same feature set raises ConflictError
        with self.assertRaises(ConflictError):
            self.store.register_webhook(
                "risk",
                "https://alerts.example.com/freshness-hook",
                ("opened",),
                "hmac-signing-phrase-one",
                "operator",
                BASE,
            )

        # Evaluate opens 2 missing incidents and dispatches 2 signed webhooks
        dispatched = []

        def fake_sender(url: str, body: bytes, headers: dict[str, str]) -> tuple[int, str]:
            dispatched.append((url, body, headers))
            return 202, "HTTP 202"

        self.store.evaluate("risk", "operator", BASE, webhook_sender=fake_sender)
        self.assertEqual(len(dispatched), 2)
        for url, body, headers in dispatched:
            self.assertEqual(url, "https://alerts.example.com/freshness-hook")
            self.assertEqual(headers["X-Freshness-Event"], "incident.opened")
            self.assertTrue(
                verify_webhook_signature(body, "hmac-signing-phrase-one", headers["X-Freshness-Signature"])
            )

        deliveries = self.store.webhook_deliveries("risk")
        self.assertEqual(len(deliveries), 2)
        self.assertTrue(all(d["status"] == "delivered" and d["status_code"] == 202 for d in deliveries))

        # Failing webhook sender records failed delivery without rolling back incident recovery
        self.store.record_materialization(self.event(event_id="rec-1", age=10), "ingestor", BASE + timedelta(seconds=1))

        def failing_sender(url: str, body: bytes, headers: dict[str, str]) -> tuple[int, str]:
            return 503, "HTTP 503"

        _, incidents, transitions = self.store.evaluate(
            "risk", "operator", BASE + timedelta(seconds=1), webhook_sender=failing_sender
        )
        self.assertEqual(len(transitions), 1)
        self.assertEqual(transitions[0].action, "resolved")
        latest_deliveries = self.store.webhook_deliveries("risk", limit=1)
        self.assertEqual(latest_deliveries[0]["action"], "resolved")
        self.assertEqual(latest_deliveries[0]["status"], "failed")
        self.assertEqual(latest_deliveries[0]["status_code"], 503)

        # Delete webhook
        self.store.delete_webhook("risk", hook["webhook_id"], "operator", BASE + timedelta(seconds=2))
        self.assertEqual(self.store.webhooks("risk"), [])
        with self.assertRaises(NotFoundError):
            self.store.delete_webhook("risk", hook["webhook_id"], "operator", BASE + timedelta(seconds=2))


