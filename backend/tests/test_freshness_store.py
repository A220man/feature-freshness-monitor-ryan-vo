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
