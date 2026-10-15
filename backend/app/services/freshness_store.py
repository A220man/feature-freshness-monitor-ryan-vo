"""SQLite transactions for feature policies, immutable events, and incidents.

Each operation uses its own connection. BEGIN IMMEDIATE serializes mutation
and evaluation, avoiding two simultaneous incident openings for one partition.
The caller supplies an authenticated actor and evaluation/ingestion time.
"""
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Iterator
from app.domain.freshness import (FreshnessPolicy, Materialization, Incident,
    IncidentTransition, evaluate, reconcile_incidents, utc, identifier)
from app.services.webhook_dispatcher import (
    WebhookSender, build_transition_payload, default_http_sender,
    dispatch_webhook, validate_signing_secret, validate_webhook_url,
)


class NotFoundError(ValueError):
    """Requested feature policy does not exist."""


class ConflictError(ValueError):
    """An optimistic version, event identity, or clock invariant conflicted."""


def stamp(value: datetime) -> str:
    return utc(value).isoformat(timespec="microseconds")


def decode_time(value: str) -> datetime:
    return utc(datetime.fromisoformat(value))


class FreshnessStore:
    """A durable single-service store; pass a filesystem path, not :memory:."""
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path == ":memory:":
            raise ValueError("use a file database; operations use separate connections")

    @contextmanager
    def connection(self, write: bool = False) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            if write:
                conn.execute("BEGIN IMMEDIATE")
            yield conn
            if write:
                conn.commit()
        except BaseException:
            if write:
                conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS feature_policies (
                feature_set TEXT PRIMARY KEY,
                partitions_json TEXT NOT NULL,
                max_age_seconds REAL NOT NULL CHECK(max_age_seconds > 0),
                grace_seconds REAL NOT NULL CHECK(grace_seconds >= 0),
                version INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                last_evaluated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS materializations (
                event_id TEXT PRIMARY KEY,
                feature_set TEXT NOT NULL REFERENCES feature_policies(feature_set),
                partition_name TEXT NOT NULL,
                source_watermark TEXT NOT NULL,
                completed_at TEXT NOT NULL,
                row_count INTEGER NOT NULL CHECK(row_count >= 0),
                received_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS materialization_latest
              ON materializations(feature_set, partition_name, completed_at DESC, event_id DESC);
            CREATE INDEX IF NOT EXISTS materialization_recent
              ON materializations(feature_set, completed_at DESC, event_id DESC);
            CREATE TABLE IF NOT EXISTS incidents (
                incident_id TEXT PRIMARY KEY,
                feature_set TEXT NOT NULL REFERENCES feature_policies(feature_set),
                partition_name TEXT NOT NULL,
                condition TEXT NOT NULL CHECK(condition IN ('stale','missing')),
                opened_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                resolved_at TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS one_open_incident
              ON incidents(feature_set, partition_name) WHERE resolved_at IS NULL;
            CREATE TABLE IF NOT EXISTS incident_timeline (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                incident_id TEXT NOT NULL REFERENCES incidents(incident_id),
                action TEXT NOT NULL CHECK(action IN ('opened','resolved')),
                occurred_at TEXT NOT NULL,
                feature_set TEXT NOT NULL,
                partition_name TEXT NOT NULL,
                condition TEXT NOT NULL,
                UNIQUE(incident_id, action)
            );
            CREATE TABLE IF NOT EXISTS audit_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                actor TEXT NOT NULL,
                action TEXT NOT NULL,
                feature_set TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                detail_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS webhook_subscriptions (
                webhook_id TEXT PRIMARY KEY,
                feature_set TEXT NOT NULL REFERENCES feature_policies(feature_set),
                target_url TEXT NOT NULL,
                events_json TEXT NOT NULL,
                signing_secret TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(feature_set, target_url)
            );
            CREATE TABLE IF NOT EXISTS webhook_deliveries (
                delivery_id INTEGER PRIMARY KEY AUTOINCREMENT,
                webhook_id TEXT NOT NULL,
                feature_set TEXT NOT NULL REFERENCES feature_policies(feature_set),
                incident_id TEXT NOT NULL,
                partition_name TEXT NOT NULL,
                condition TEXT NOT NULL,
                action TEXT NOT NULL CHECK(action IN ('opened','resolved')),
                target_url TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                signature TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('delivered','failed')),
                status_code INTEGER,
                detail TEXT NOT NULL,
                occurred_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS webhook_delivery_recent
              ON webhook_deliveries(feature_set, delivery_id DESC);
            """)

    @staticmethod
    def _policy(row: sqlite3.Row) -> FreshnessPolicy:
        return FreshnessPolicy(row["feature_set"], tuple(json.loads(row["partitions_json"])), row["max_age_seconds"], row["grace_seconds"])

    @staticmethod
    def _audit(conn, actor: str, action: str, feature_set: str, at: datetime, detail: dict) -> None:
        identifier(actor, "actor")
        conn.execute("INSERT INTO audit_events(actor,action,feature_set,occurred_at,detail_json) VALUES(?,?,?,?,?)", (actor, action, feature_set, stamp(at), json.dumps(detail, sort_keys=True)))

    def save_policy(self, policy: FreshnessPolicy, actor: str, at: datetime, expected_version: int = 0) -> int:
        """Create with version 0 or update with the last observed version."""
        with self.connection(write=True) as conn:
            row = conn.execute("SELECT * FROM feature_policies WHERE feature_set=?", (policy.feature_set,)).fetchone()
            version = row["version"] if row else 0
            if expected_version != version:
                raise ConflictError("policy version changed; reload before editing")
            if row and stamp(at) < row["updated_at"]:
                raise ConflictError("policy update cannot precede the previous update")
            if row and row["last_evaluated_at"] and stamp(at) < row["last_evaluated_at"]:
                raise ConflictError("policy update cannot precede the last evaluation")
            version += 1
            if row:
                conn.execute("UPDATE feature_policies SET partitions_json=?,max_age_seconds=?,grace_seconds=?,version=?,updated_at=? WHERE feature_set=?", (json.dumps(policy.expected_partitions), policy.max_age_seconds, policy.grace_seconds, version, stamp(at), policy.feature_set))
            else:
                conn.execute("INSERT INTO feature_policies(feature_set,partitions_json,max_age_seconds,grace_seconds,version,updated_at) VALUES(?,?,?,?,?,?)", (policy.feature_set, json.dumps(policy.expected_partitions), policy.max_age_seconds, policy.grace_seconds, version, stamp(at)))
            self._audit(conn, actor, "policy.saved", policy.feature_set, at, {"version": version, "policy": asdict(policy)})
            return version

    def policies(self) -> list[dict]:
        with self.connection() as conn:
            return [{**asdict(self._policy(row)), "version": row["version"], "updated_at": row["updated_at"], "last_evaluated_at": row["last_evaluated_at"]} for row in conn.execute("SELECT * FROM feature_policies ORDER BY feature_set")]

    def policy_by_name(self, feature_set: str) -> dict:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM feature_policies WHERE feature_set=?", (feature_set,)).fetchone()
            if row is None:
                raise NotFoundError("feature policy not found")
            return {**asdict(self._policy(row)), "version": row["version"], "updated_at": row["updated_at"], "last_evaluated_at": row["last_evaluated_at"]}

    def record_materialization(self, event: Materialization, actor: str, received_at: datetime) -> bool:
        """Return False for exact replay; reject conflicting IDs atomically."""
        if utc(received_at) < event.completed_at:
            raise ValueError("job completion cannot be in the future at ingestion")
        values = (event.event_id, event.feature_set, event.partition, stamp(event.source_watermark), stamp(event.completed_at), event.row_count)
        with self.connection(write=True) as conn:
            if not conn.execute("SELECT 1 FROM feature_policies WHERE feature_set=?", (event.feature_set,)).fetchone():
                raise NotFoundError("feature policy not found")
            existing = conn.execute("SELECT event_id,feature_set,partition_name,source_watermark,completed_at,row_count FROM materializations WHERE event_id=?", (event.event_id,)).fetchone()
            if existing:
                if tuple(existing) != values:
                    raise ConflictError("event ID already identifies a different materialization")
                return False
            conn.execute("INSERT INTO materializations VALUES(?,?,?,?,?,?,?)", values + (stamp(received_at),))
            self._audit(conn, actor, "materialization.recorded", event.feature_set, received_at, {"event_id": event.event_id, "partition": event.partition})
            return True

    def record_batch(self, events: list[Materialization], actor: str, received_at: datetime) -> tuple[int, int]:
        """Record multiple materializations atomically; return (created, replayed)."""
        if not events:
            return 0, 0
        rec_time = utc(received_at)
        feature_sets = {e.feature_set for e in events}
        if len(feature_sets) > 1:
            raise ValueError("batch materializations must belong to a single feature set")
        feature_set = next(iter(feature_sets))
        for event in events:
            if rec_time < event.completed_at:
                raise ValueError("job completion cannot be in the future at ingestion")

        created = 0
        replayed = 0
        seen_in_batch: dict[str, tuple] = {}

        with self.connection(write=True) as conn:
            if not conn.execute("SELECT 1 FROM feature_policies WHERE feature_set=?", (feature_set,)).fetchone():
                raise NotFoundError("feature policy not found")

            for event in events:
                values = (event.event_id, event.feature_set, event.partition, stamp(event.source_watermark), stamp(event.completed_at), event.row_count)
                if event.event_id in seen_in_batch:
                    if seen_in_batch[event.event_id] != values:
                        raise ConflictError("event ID already identifies a different materialization in batch")
                    replayed += 1
                    continue

                existing = conn.execute("SELECT event_id,feature_set,partition_name,source_watermark,completed_at,row_count FROM materializations WHERE event_id=?", (event.event_id,)).fetchone()
                if existing:
                    if tuple(existing) != values:
                        raise ConflictError("event ID already identifies a different materialization")
                    replayed += 1
                    seen_in_batch[event.event_id] = values
                    continue

                conn.execute("INSERT INTO materializations VALUES(?,?,?,?,?,?,?)", values + (stamp(received_at),))
                seen_in_batch[event.event_id] = values
                created += 1

            self._audit(conn, actor, "materializations.batch_recorded", feature_set, received_at, {"count": len(events), "created": created, "replayed": replayed})
            return created, replayed

    @staticmethod
    def _incidents(conn, feature_set: str) -> tuple[Incident, ...]:
        rows = conn.execute("SELECT * FROM incidents WHERE feature_set=? ORDER BY opened_at,incident_id", (feature_set,))
        return tuple(Incident(r["incident_id"], r["feature_set"], r["partition_name"], r["condition"], decode_time(r["opened_at"]), decode_time(r["last_seen_at"]), decode_time(r["resolved_at"]) if r["resolved_at"] else None) for r in rows)

    def evaluate(self, feature_set: str, actor: str, at: datetime, webhook_sender: WebhookSender = default_http_sender):
        """Persist a monotonic snapshot and its incident transitions together."""
        with self.connection(write=True) as conn:
            row = conn.execute("SELECT * FROM feature_policies WHERE feature_set=?", (feature_set,)).fetchone()
            if row is None:
                raise NotFoundError("feature policy not found")
            if row["last_evaluated_at"] and stamp(at) < row["last_evaluated_at"]:
                raise ConflictError("evaluation is older than the last saved snapshot")
            events = conn.execute("""SELECT * FROM (
                SELECT *, ROW_NUMBER() OVER(PARTITION BY partition_name ORDER BY completed_at DESC,event_id DESC) AS rank
                FROM materializations WHERE feature_set=? AND completed_at<=? AND received_at<=?
                ) WHERE rank=1""", (feature_set, stamp(at), stamp(at)))
            materializations = [Materialization(r["event_id"], r["feature_set"], r["partition_name"], decode_time(r["source_watermark"]), decode_time(r["completed_at"]), r["row_count"]) for r in events]
            evaluation = evaluate(self._policy(row), materializations, at)
            records, transitions = reconcile_incidents(evaluation, self._incidents(conn, feature_set))
            # Resolve incidents for partitions removed from the current policy.
            from dataclasses import replace
            records = list(records)
            transitions = list(transitions)
            expected = {h.partition for h in evaluation.partitions}
            for index, incident in enumerate(records):
                if incident.partition not in expected and incident.resolved_at is None:
                    records[index] = replace(incident, last_seen_at=utc(at), resolved_at=utc(at))
                    transitions.append(IncidentTransition(incident.incident_id, "resolved", utc(at), feature_set, incident.partition, incident.condition))
            # Persist resolved records first so the partial unique index permits replacements.
            for incident in sorted(records, key=lambda i: i.resolved_at is None):
                conn.execute("""INSERT INTO incidents VALUES(?,?,?,?,?,?,?)
                    ON CONFLICT(incident_id) DO UPDATE SET last_seen_at=excluded.last_seen_at,resolved_at=excluded.resolved_at""", (incident.incident_id, incident.feature_set, incident.partition, incident.condition, stamp(incident.opened_at), stamp(incident.last_seen_at), stamp(incident.resolved_at) if incident.resolved_at else None))
            for transition in transitions:
                conn.execute("INSERT INTO incident_timeline(incident_id,action,occurred_at,feature_set,partition_name,condition) VALUES(?,?,?,?,?,?)", (transition.incident_id, transition.action, stamp(transition.occurred_at), feature_set, transition.partition, transition.condition))
            conn.execute("UPDATE feature_policies SET last_evaluated_at=? WHERE feature_set=?", (stamp(at), feature_set))
            self._audit(conn, actor, "freshness.evaluated", feature_set, at, {"healthy": evaluation.healthy, "coverage": evaluation.coverage, "transition_count": len(transitions)})
        if transitions:
            self.dispatch_transition_webhooks(feature_set, tuple(transitions), at, webhook_sender)
        return evaluation, tuple(records), tuple(transitions)

    def register_webhook(
        self,
        feature_set: str,
        target_url: str,
        events: tuple[str, ...] | list[str],
        signing_secret: str,
        actor: str,
        at: datetime,
    ) -> dict:
        identifier(feature_set, "feature_set")
        validated_url = validate_webhook_url(target_url)
        validated_secret = validate_signing_secret(signing_secret)
        if not isinstance(events, (tuple, list)) or not events:
            raise ValueError("webhook events must include at least one of opened or resolved")
        invalid = [e for e in events if e not in {"opened", "resolved"}]
        if invalid:
            raise ValueError("webhook events may only contain opened and resolved")
        normalized_events = [e for e in ("opened", "resolved") if e in events]
        webhook_id = "wh-" + hashlib.sha256(f"{feature_set}\0{validated_url}".encode("utf-8")).hexdigest()[:16]
        created_stamp = stamp(at)
        with self.connection(write=True) as conn:
            if not conn.execute("SELECT 1 FROM feature_policies WHERE feature_set=?", (feature_set,)).fetchone():
                raise NotFoundError("feature policy not found")
            existing = conn.execute(
                "SELECT 1 FROM webhook_subscriptions WHERE feature_set=? AND target_url=?",
                (feature_set, validated_url),
            ).fetchone()
            if existing:
                raise ConflictError("webhook endpoint already registered for this feature set")
            conn.execute(
                "INSERT INTO webhook_subscriptions(webhook_id,feature_set,target_url,events_json,signing_secret,created_at) VALUES(?,?,?,?,?,?)",
                (webhook_id, feature_set, validated_url, json.dumps(normalized_events), validated_secret, created_stamp),
            )
            self._audit(
                conn,
                actor,
                "webhook.registered",
                feature_set,
                at,
                {"webhook_id": webhook_id, "target_url": validated_url, "events": normalized_events},
            )
        return {
            "webhook_id": webhook_id,
            "feature_set": feature_set,
            "target_url": validated_url,
            "events": normalized_events,
            "created_at": created_stamp,
        }

    def webhooks(self, feature_set: str) -> list[dict]:
        with self.connection() as conn:
            if not conn.execute("SELECT 1 FROM feature_policies WHERE feature_set=?", (feature_set,)).fetchone():
                raise NotFoundError("feature policy not found")
            rows = conn.execute(
                "SELECT webhook_id,feature_set,target_url,events_json,created_at FROM webhook_subscriptions WHERE feature_set=? ORDER BY created_at,webhook_id",
                (feature_set,),
            )
            return [
                {
                    "webhook_id": r["webhook_id"],
                    "feature_set": r["feature_set"],
                    "target_url": r["target_url"],
                    "events": json.loads(r["events_json"]),
                    "created_at": r["created_at"],
                }
                for r in rows
            ]

    def delete_webhook(self, feature_set: str, webhook_id: str, actor: str, at: datetime) -> None:
        identifier(webhook_id, "webhook_id")
        with self.connection(write=True) as conn:
            if not conn.execute("SELECT 1 FROM feature_policies WHERE feature_set=?", (feature_set,)).fetchone():
                raise NotFoundError("feature policy not found")
            cursor = conn.execute(
                "DELETE FROM webhook_subscriptions WHERE feature_set=? AND webhook_id=?",
                (feature_set, webhook_id),
            )
            if cursor.rowcount == 0:
                raise NotFoundError("webhook subscription not found")
            self._audit(conn, actor, "webhook.deleted", feature_set, at, {"webhook_id": webhook_id})

    def dispatch_transition_webhooks(
        self,
        feature_set: str,
        transitions: tuple[IncidentTransition, ...],
        at: datetime,
        sender: WebhookSender = default_http_sender,
    ) -> list[dict]:
        if not transitions:
            return []
        with self.connection() as conn:
            subs = [
                dict(r)
                for r in conn.execute(
                    "SELECT webhook_id,feature_set,target_url,events_json,signing_secret FROM webhook_subscriptions WHERE feature_set=? ORDER BY created_at,webhook_id",
                    (feature_set,),
                )
            ]
        if not subs:
            return []
        occurred_stamp = stamp(at)
        pending_records = []
        for transition in transitions:
            for sub in subs:
                subscribed_events = json.loads(sub["events_json"])
                if transition.action not in subscribed_events:
                    continue
                payload = build_transition_payload(sub["webhook_id"], transition)
                payload_json, signature, status, status_code, detail = dispatch_webhook(
                    sub["target_url"], sub["signing_secret"], payload, sender
                )
                pending_records.append(
                    (
                        sub["webhook_id"],
                        feature_set,
                        transition.incident_id,
                        transition.partition,
                        transition.condition,
                        transition.action,
                        sub["target_url"],
                        payload_json,
                        signature,
                        status,
                        status_code,
                        detail,
                        occurred_stamp,
                    )
                )
        if not pending_records:
            return []
        results = []
        with self.connection(write=True) as conn:
            for rec in pending_records:
                cur = conn.execute(
                    """INSERT INTO webhook_deliveries(
                        webhook_id,feature_set,incident_id,partition_name,condition,action,
                        target_url,payload_json,signature,status,status_code,detail,occurred_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    rec,
                )
                results.append(
                    {
                        "delivery_id": cur.lastrowid,
                        "webhook_id": rec[0],
                        "feature_set": rec[1],
                        "incident_id": rec[2],
                        "partition": rec[3],
                        "condition": rec[4],
                        "action": rec[5],
                        "target_url": rec[6],
                        "signature": rec[8],
                        "status": rec[9],
                        "status_code": rec[10],
                        "detail": rec[11],
                        "occurred_at": rec[12],
                    }
                )
        return results

    def webhook_deliveries(self, feature_set: str, limit: int = 50) -> list[dict]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        with self.connection() as conn:
            if not conn.execute("SELECT 1 FROM feature_policies WHERE feature_set=?", (feature_set,)).fetchone():
                raise NotFoundError("feature policy not found")
            rows = conn.execute(
                """SELECT delivery_id,webhook_id,feature_set,incident_id,partition_name,
                          condition,action,target_url,signature,status,status_code,detail,occurred_at
                   FROM webhook_deliveries
                   WHERE feature_set=?
                   ORDER BY delivery_id DESC LIMIT ?""",
                (feature_set, limit),
            )
            return [
                {
                    "delivery_id": r["delivery_id"],
                    "webhook_id": r["webhook_id"],
                    "feature_set": r["feature_set"],
                    "incident_id": r["incident_id"],
                    "partition": r["partition_name"],
                    "condition": r["condition"],
                    "action": r["action"],
                    "target_url": r["target_url"],
                    "signature": r["signature"],
                    "status": r["status"],
                    "status_code": r["status_code"],
                    "detail": r["detail"],
                    "occurred_at": r["occurred_at"],
                }
                for r in rows
            ]

    def timeline(self, feature_set: str, after: int = 0, limit: int = 100) -> list[dict]:
        if after < 0 or not 1 <= limit <= 1000:
            raise ValueError("invalid timeline pagination")
        with self.connection() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM incident_timeline WHERE feature_set=? AND sequence>? ORDER BY sequence LIMIT ?", (feature_set, after, limit))]

    def audit(self, feature_set: str, after: int = 0, limit: int = 100) -> list[dict]:
        if after < 0 or not 1 <= limit <= 1000:
            raise ValueError("invalid audit pagination")
        with self.connection() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM audit_events WHERE feature_set=? AND sequence>? ORDER BY sequence LIMIT ?", (feature_set, after, limit))]

    def snapshot(self, feature_set: str, at: datetime):
        """Read current health without opening incidents or writing audit events."""
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM feature_policies WHERE feature_set=?", (feature_set,)).fetchone()
            if row is None:
                raise NotFoundError("feature policy not found")
            rows = conn.execute("""SELECT * FROM (
                SELECT *,ROW_NUMBER() OVER(PARTITION BY partition_name ORDER BY completed_at DESC,event_id DESC) AS rank
                FROM materializations WHERE feature_set=? AND completed_at<=? AND received_at<=?
                ) WHERE rank=1""", (feature_set, stamp(at), stamp(at)))
            events = [Materialization(r["event_id"],r["feature_set"],r["partition_name"],decode_time(r["source_watermark"]),decode_time(r["completed_at"]),r["row_count"]) for r in rows]
            return evaluate(self._policy(row), events, at)

    def incidents(self, feature_set: str):
        with self.connection() as conn:
            if not conn.execute("SELECT 1 FROM feature_policies WHERE feature_set=?", (feature_set,)).fetchone():
                raise NotFoundError("feature policy not found")
            return self._incidents(conn, feature_set)

    def materializations(self, feature_set: str, partition: str | None = None, limit: int = 50) -> list[dict]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        with self.connection() as conn:
            if not conn.execute("SELECT 1 FROM feature_policies WHERE feature_set=?", (feature_set,)).fetchone():
                raise NotFoundError("feature policy not found")
            if partition:
                query = """SELECT event_id, feature_set, partition_name, source_watermark, completed_at, row_count, received_at
                           FROM materializations
                           WHERE feature_set=? AND partition_name=?
                           ORDER BY completed_at DESC, event_id DESC LIMIT ?"""
                rows = conn.execute(query, (feature_set, partition, limit))
            else:
                query = """SELECT event_id, feature_set, partition_name, source_watermark, completed_at, row_count, received_at
                           FROM materializations
                           WHERE feature_set=?
                           ORDER BY completed_at DESC, event_id DESC LIMIT ?"""
                rows = conn.execute(query, (feature_set, limit))
            return [
                {
                    "event_id": r["event_id"],
                    "feature_set": r["feature_set"],
                    "partition": r["partition_name"],
                    "source_watermark": r["source_watermark"],
                    "completed_at": r["completed_at"],
                    "row_count": r["row_count"],
                    "received_at": r["received_at"],
                }
                for r in rows
            ]

