# Changelog

## 1.4.0 — work-in-progress checkpoint

Partial code published for continued development. Known issues: STATUS.md.

## [1.3.0] - 2026-10-22

- Configurable HTTPS webhook alert dispatcher with HMAC-SHA256 payload signatures and SSRF host validation.
- Webhook registration, removal, and delivery telemetry routes and operator workspace panel.
- Queryable materialization event history endpoint with partition filtering and limit pagination.
- Operator workspace table surfacing recent materializations, row counts, and ingestion telemetry.
- SQLite index on feature set completion time for optimized event log queries.

## [1.2.0] - 2026-10-15

- Batch materialization ingestion endpoint and interactive UI form for bulk pipeline syncs.
- Partition search and status filtering (fresh, stale, missing) in the operator workspace.
- Complete feature snapshot export endpoint and client download workflow.
- Surfaced unexpected partition telemetry to alert operators to unmonitored materializations.

## [1.1.0] - 2026-10-09

- Source-watermark freshness checks across expected feature partitions.
- Replay-safe materialization ingestion and optimistic policy versioning.
- Transactional incident opening/recovery with timelines and actor audit records.
- OIDC PKCE sign-in and bearer authentication with viewer/operator/admin roles.
- Optional provider-neutral AI advice grounded in server-computed snapshots.