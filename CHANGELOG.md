# Changelog

## [1.1.0] - 2026-10-09

- Batch materialization ingestion endpoint and interactive UI form for bulk pipeline syncs.
- Partition search and status filtering (fresh, stale, missing) in the operator workspace.
- Complete feature snapshot export endpoint and client download workflow.
- Surfaced unexpected partition telemetry to alert operators to unmonitored materializations.

## [1.0.0] - 2026-10-08

- Source-watermark freshness checks across expected feature partitions.
- Replay-safe materialization ingestion and optimistic policy versioning.
- Transactional incident opening/recovery with timelines and actor audit records.
- OIDC PKCE sign-in and bearer authentication with viewer/operator/admin roles.
- Optional provider-neutral AI advice grounded in server-computed snapshots.
