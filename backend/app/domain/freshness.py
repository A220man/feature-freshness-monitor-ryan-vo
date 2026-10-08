"""Evaluate data freshness using source watermarks, not merely job completion.

No network, storage, clock reads, or model calls are needed. Callers supply an
explicit evaluation time and persist the returned immutable records.
"""
from __future__ import annotations
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from math import isfinite
from typing import Iterable, Literal

Status = Literal["fresh", "stale", "missing"]


def utc(value: datetime) -> datetime:
    """Reject ambiguous naive timestamps and normalize offsets to UTC."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    return value.astimezone(timezone.utc)


def identifier(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise ValueError(f"{field} must be a nonempty string of at most 200 characters")
    if value != value.strip():
        raise ValueError(f"{field} must not have surrounding whitespace")
    return value


@dataclass(frozen=True)
class FreshnessPolicy:
    feature_set: str
    expected_partitions: tuple[str, ...]
    max_age_seconds: float
    grace_seconds: float = 0

    def __post_init__(self) -> None:
        identifier(self.feature_set, "feature_set")
        if not isinstance(self.expected_partitions, (tuple, list)):
            raise ValueError("expected partitions must be a sequence of names")
        object.__setattr__(self, "expected_partitions", tuple(self.expected_partitions))
        if not self.expected_partitions or len(set(self.expected_partitions)) != len(self.expected_partitions):
            raise ValueError("expected partitions must be nonempty and unique")
        for partition in self.expected_partitions:
            identifier(partition, "partition")
        for field, value in (("max_age_seconds", self.max_age_seconds), ("grace_seconds", self.grace_seconds)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
                raise ValueError(f"{field} must be finite")
        if self.max_age_seconds <= 0 or self.grace_seconds < 0:
            raise ValueError("max age must be positive and grace must be nonnegative")


@dataclass(frozen=True)
class Materialization:
    event_id: str
    feature_set: str
    partition: str
    source_watermark: datetime
    completed_at: datetime
    row_count: int

    def __post_init__(self) -> None:
        for field in ("event_id", "feature_set", "partition"):
            identifier(getattr(self, field), field)
        watermark, completed = utc(self.source_watermark), utc(self.completed_at)
        if watermark > completed:
            raise ValueError("source watermark cannot be later than job completion")
        if isinstance(self.row_count, bool) or not isinstance(self.row_count, int) or self.row_count < 0:
            raise ValueError("row count must be a nonnegative integer")
        object.__setattr__(self, "source_watermark", watermark)
        object.__setattr__(self, "completed_at", completed)


@dataclass(frozen=True)
class PartitionHealth:
    feature_set: str
    partition: str
    status: Status
    evaluated_at: datetime
    event_id: str | None
    source_watermark: datetime | None
    completed_at: datetime | None
    source_age_seconds: float | None
    materialization_delay_seconds: float | None
    overdue_seconds: float | None
    due_at: datetime | None
    row_count: int | None


@dataclass(frozen=True)
class FreshnessEvaluation:
    feature_set: str
    evaluated_at: datetime
    partitions: tuple[PartitionHealth, ...]
    unexpected_partitions: tuple[str, ...]

    @property
    def healthy(self) -> bool:
        return all(partition.status == "fresh" for partition in self.partitions)

    @property
    def coverage(self) -> float:
        return sum(partition.event_id is not None for partition in self.partitions) / len(self.partitions)


def evaluate(policy: FreshnessPolicy, events: Iterable[Materialization], at: datetime) -> FreshnessEvaluation:
    """Return an as-of snapshot; future completions cannot affect past results.

    Duplicate identical event IDs are idempotent; conflicting reuse is rejected.
    The most recently completed job is authoritative, even if its source
    watermark regressed. A newer job must not hide stale source data.
    """
    at = utc(at)
    by_id: dict[str, Materialization] = {}
    latest: dict[str, Materialization] = {}
    unexpected: set[str] = set()
    for event in events:
        if event.feature_set != policy.feature_set:
            continue
        previous = by_id.get(event.event_id)
        if previous is not None and previous != event:
            raise ValueError(f"conflicting event ID: {event.event_id}")
        by_id[event.event_id] = event
        if event.completed_at > at:
            continue
        if event.partition not in policy.expected_partitions:
            unexpected.add(event.partition)
            continue
        previous = latest.get(event.partition)
        if previous is None or (event.completed_at, event.event_id) > (previous.completed_at, previous.event_id):
            latest[event.partition] = event
    health = []
    for partition in policy.expected_partitions:
        event = latest.get(partition)
        if event is None:
            health.append(PartitionHealth(policy.feature_set, partition, "missing", at, None, None, None, None, None, None, None, None))
            continue
        age = (at - event.source_watermark).total_seconds()
        delay = (event.completed_at - event.source_watermark).total_seconds()
        try:
            due = event.source_watermark + timedelta(seconds=policy.max_age_seconds + policy.grace_seconds)
        except OverflowError as error:
            raise ValueError("freshness deadline is outside the supported timestamp range") from error
        overdue = max(0.0, (at - due).total_seconds())
        health.append(PartitionHealth(policy.feature_set, partition, "stale" if at > due else "fresh", at, event.event_id, event.source_watermark, event.completed_at, age, delay, overdue, due, event.row_count))
    return FreshnessEvaluation(policy.feature_set, at, tuple(health), tuple(sorted(unexpected)))


@dataclass(frozen=True)
class Incident:
    incident_id: str
    feature_set: str
    partition: str
    condition: Literal["stale", "missing"]
    opened_at: datetime
    last_seen_at: datetime
    resolved_at: datetime | None = None


@dataclass(frozen=True)
class IncidentTransition:
    incident_id: str
    action: Literal["opened", "resolved"]
    occurred_at: datetime
    feature_set: str
    partition: str
    condition: Literal["stale", "missing"]


def reconcile_incidents(evaluation: FreshnessEvaluation, incidents: Iterable[Incident]) -> tuple[tuple[Incident, ...], tuple[IncidentTransition, ...]]:
    """Open once, update last seen, resolve recovery, and preserve past incidents.

    Changing a missing partition to a stale one resolves the missing incident
    and opens a distinct stale incident. Replaying the same snapshot is
    idempotent. Historical evaluations cannot mutate newer incident state.
    """
    at = utc(evaluation.evaluated_at)
    records = list(incidents)
    active: dict[str, int] = {}
    for index, incident in enumerate(records):
        if incident.feature_set != evaluation.feature_set:
            continue
        if utc(incident.last_seen_at) > at or (incident.resolved_at is not None and utc(incident.resolved_at) > at):
            raise ValueError("cannot reconcile an evaluation older than incident state")
        if incident.resolved_at is None:
            if incident.partition in active:
                raise ValueError("multiple active incidents for one partition")
            active[incident.partition] = index
    transitions = []
    for health in evaluation.partitions:
        index = active.get(health.partition)
        existing = records[index] if index is not None else None
        condition = health.status if health.status != "fresh" else None
        if existing is not None and existing.condition == condition:
            records[index] = replace(existing, last_seen_at=at)
            continue
        if existing is not None:
            records[index] = replace(existing, last_seen_at=at, resolved_at=at)
            transitions.append(IncidentTransition(existing.incident_id, "resolved", at, existing.feature_set, existing.partition, existing.condition))
        if condition is not None:
            generation = sum(i.feature_set == evaluation.feature_set and i.partition == health.partition and i.condition == condition for i in records)
            key = "\0".join((evaluation.feature_set, health.partition, condition, at.isoformat(), str(generation)))
            incident_id = sha256(key.encode()).hexdigest()[:24]
            records.append(Incident(incident_id, evaluation.feature_set, health.partition, condition, at, at))
            transitions.append(IncidentTransition(incident_id, "opened", at, evaluation.feature_set, health.partition, condition))
    return tuple(records), tuple(transitions)
