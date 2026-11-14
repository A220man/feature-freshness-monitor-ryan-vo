from datetime import datetime, timedelta, timezone
from dataclasses import replace
import unittest
from app.domain.freshness import FreshnessPolicy, Materialization, evaluate, reconcile_incidents

BASE = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)


def event(event_id="run-1", partition="us", age=30, completed=0, feature_set="risk"):
    return Materialization(event_id, feature_set, partition, BASE-timedelta(seconds=age), BASE+timedelta(seconds=completed), 10)


class FreshnessDomainTests(unittest.TestCase):
    def setUp(self):
        self.policy = FreshnessPolicy("risk", ("us", "eu"), 60, 10)

    def test_missing_partition_reduces_coverage(self):
        result = evaluate(self.policy, [event()], BASE)
        self.assertEqual([p.status for p in result.partitions], ["fresh", "missing"])
        self.assertEqual(result.coverage, 0.5)
        self.assertFalse(result.healthy)

    def test_fresh_job_does_not_hide_stale_source_data(self):
        result = evaluate(self.policy, [event(age=120)], BASE)
        self.assertEqual(result.partitions[0].status, "stale")
        self.assertEqual(result.partitions[0].materialization_delay_seconds, 120)
        self.assertEqual(result.partitions[0].overdue_seconds, 50)

    def test_grace_deadline_is_inclusive(self):
        source = event(age=70)
        self.assertEqual(evaluate(self.policy, [source], BASE).partitions[0].status, "fresh")
        self.assertEqual(evaluate(self.policy, [source], BASE+timedelta(microseconds=1)).partitions[0].status, "stale")

    def test_newer_job_watermark_regression_is_visible(self):
        old = event("old", age=30, completed=-10)
        new = event("new", age=120)
        for rows in ([old, new], [new, old]):
            result = evaluate(self.policy, rows, BASE).partitions[0]
            self.assertEqual((result.event_id, result.status), ("new", "stale"))

    def test_future_job_does_not_change_past_snapshot(self):
        result = evaluate(self.policy, [event(completed=30)], BASE)
        self.assertEqual(result.partitions[0].status, "missing")

    def test_duplicate_ingestion_is_idempotent(self):
        one = event()
        self.assertEqual(evaluate(self.policy, [one], BASE), evaluate(self.policy, [one, one], BASE))

    def test_conflicting_event_id_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "conflicting event ID"):
            evaluate(self.policy, [event(), event(age=60)], BASE)

    def test_unknown_partition_is_reported_without_hiding_expected_gaps(self):
        result = evaluate(self.policy, [event(partition="apac")], BASE)
        self.assertEqual(result.unexpected_partitions, ("apac",))
        self.assertEqual(result.coverage, 0)

    def test_other_feature_sets_are_isolated(self):
        self.assertEqual(evaluate(self.policy, [event(feature_set="fraud")], BASE).coverage, 0)

    def test_offset_timestamps_are_normalized(self):
        offset = timezone(timedelta(hours=2))
        self.assertEqual(evaluate(self.policy, [event()], BASE), evaluate(self.policy, [event()], BASE.astimezone(offset)))
        with self.assertRaisesRegex(ValueError, "UTC offset"):
            evaluate(self.policy, [], BASE.replace(tzinfo=None))

    def test_invalid_source_watermark_and_row_count(self):
        with self.assertRaisesRegex(ValueError, "watermark"):
            event(age=-1)
        with self.assertRaisesRegex(ValueError, "row count"):
            replace(event(), row_count=-1)

    def test_invalid_policy_cannot_create_misleading_health(self):
        for value in (float("nan"), float("inf"), 0, -1, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                FreshnessPolicy("risk", ("us",), value)
        with self.assertRaises(ValueError):
            FreshnessPolicy("risk", ("us", "us"), 60)
        with self.assertRaises(ValueError):
            FreshnessPolicy("risk", (), 60)

    def test_incident_open_replay_recovery_and_recurrence(self):
        policy = FreshnessPolicy("risk", ("us",), 60)
        stale = evaluate(policy, [event(age=120)], BASE)
        records, opened = reconcile_incidents(stale, [])
        same, replay = reconcile_incidents(stale, records)
        self.assertEqual(records, same)
        self.assertEqual(replay, ())
        self.assertEqual(opened[0].action, "opened")
        recovered = evaluate(policy, [event("fresh", age=10)], BASE+timedelta(seconds=1))
        resolved, transitions = reconcile_incidents(recovered, records)
        self.assertEqual(transitions[0].action, "resolved")
        self.assertIsNotNone(resolved[0].resolved_at)
        late = evaluate(policy, [event("fresh", age=10)], BASE+timedelta(seconds=120))
        reopened, transitions = reconcile_incidents(late, resolved)
        self.assertEqual(len(reopened), 2)
        self.assertNotEqual(reopened[0].incident_id, reopened[1].incident_id)
        self.assertEqual(transitions[0].action, "opened")

    def test_missing_to_stale_is_a_distinct_condition(self):
        policy = FreshnessPolicy("risk", ("us",), 60)
        missing = evaluate(policy, [], BASE)
        records, _ = reconcile_incidents(missing, [])
        stale = evaluate(policy, [event(age=120)], BASE+timedelta(seconds=1))
        records, transitions = reconcile_incidents(stale, records)
        self.assertEqual([x.action for x in transitions], ["resolved", "opened"])
        self.assertEqual([x.condition for x in records], ["missing", "stale"])

    def test_historical_evaluation_cannot_roll_back_incident_state(self):
        records, _ = reconcile_incidents(evaluate(self.policy, [], BASE), [])
        with self.assertRaisesRegex(ValueError, "older"):
            reconcile_incidents(evaluate(self.policy, [], BASE-timedelta(seconds=1)), records)

    def test_duplicate_active_incidents_are_rejected(self):
        records, _ = reconcile_incidents(evaluate(self.policy, [], BASE), [])
        with self.assertRaisesRegex(ValueError, "multiple active"):
            reconcile_incidents(evaluate(self.policy, [], BASE), records + records)

    def test_same_timestamp_recurrence_has_distinct_incident_ids(self):
        policy = FreshnessPolicy("risk", ("us",), 60)
        missing = evaluate(policy, [], BASE)
        records, _ = reconcile_incidents(missing, [])
        fresh = evaluate(policy, [event()], BASE)
        records, _ = reconcile_incidents(fresh, records)
        records, _ = reconcile_incidents(missing, records)
        self.assertEqual(len({x.incident_id for x in records}), 2)

    def test_unrepresentable_deadline_is_a_validation_error(self):
        policy = FreshnessPolicy("risk", ("us",), 1e300)
    def test_sla_compliance_computes_breach_durations_and_ratios(self):
        from app.domain.freshness import IncidentTransition, compute_sla_compliance
        policy = FreshnessPolicy("risk", ("us", "eu"), 60, 10)
        window_start = BASE
        window_end = BASE + timedelta(hours=1)
        # Create timeline events: us opened at BASE+10min, resolved at BASE+30min (20min breach)
        # eu opened at BASE+20min, still open at window_end (40min breach)
        transitions = [
            IncidentTransition("inc-1", "opened", BASE + timedelta(minutes=10), "risk", "us", "stale"),
            IncidentTransition("inc-1", "resolved", BASE + timedelta(minutes=30), "risk", "us", "stale"),
            IncidentTransition("inc-2", "opened", BASE + timedelta(minutes=20), "risk", "eu", "missing"),
            # eu incident still open at window_end
        ]
        report = compute_sla_compliance(transitions, window_start, window_end, ("us", "eu"))
        self.assertEqual(report.feature_set, "risk")
        self.assertEqual(report.window_start, window_start)
        self.assertEqual(report.window_end, window_end)
        # us: 20 min breach = 1200s out of 3600s = 66.67% compliance
        us = next(p for p in report.partitions if p.partition == "us")
        self.assertAlmostEqual(us.total_breach_seconds, 1200.0, places=1)
        self.assertAlmostEqual(us.compliance_ratio, 1.0 - 1200.0/3600.0, places=3)
        self.assertEqual(us.incident_count, 1)
        # eu: 40 min breach = 2400s out of 3600s = 33.33% compliance
        eu = next(p for p in report.partitions if p.partition == "eu")
        self.assertAlmostEqual(eu.total_breach_seconds, 2400.0, places=1)
        self.assertAlmostEqual(eu.compliance_ratio, 1.0 - 2400.0/3600.0, places=3)
        self.assertEqual(eu.incident_count, 1)
        # Overall compliance = average
        self.assertAlmostEqual(report.overall_compliance_ratio, (us.compliance_ratio + eu.compliance_ratio) / 2, places=3)

    def test_sla_compliance_no_incidents_is_100_percent(self):
        from app.domain.freshness import compute_sla_compliance
        window_start = BASE
        window_end = BASE + timedelta(hours=1)
        report = compute_sla_compliance([], window_start, window_end, ("us", "eu"))
        for p in report.partitions:
            self.assertEqual(p.total_breach_seconds, 0.0)
            self.assertEqual(p.compliance_ratio, 1.0)
            self.assertEqual(p.incident_count, 0)
        self.assertEqual(report.overall_compliance_ratio, 1.0)

    def test_sla_compliance_validation(self):
        from app.domain.freshness import compute_sla_compliance
        window_start = BASE
        window_end = BASE + timedelta(hours=1)
        with self.assertRaisesRegex(ValueError, "window_start must be before window_end"):
            compute_sla_compliance([], window_end, window_start, ("us",))
        with self.assertRaisesRegex(ValueError, "positive duration"):
            compute_sla_compliance([], BASE, BASE, ("us",))
