# Changelog

## 1.9.0 — work-in-progress checkpoint

Partial code published for continued development. Known issues: STATUS.md.

## 1.8.0 — work-in-progress checkpoint

Partial code published for continued development. Known issues: STATUS.md.

## 1.7.0 — work-in-progress checkpoint

- Added cursor-based pagination for webhook deliveries in the operator workspace (WebhookPanel)
- "Load more deliveries" button allows operators to page through webhook delivery history
- Webhook delivery cursor uses `delivery_id` for correct pagination consistency with backend API
- Started SLA compliance analytics feature (in_progress):
  - New domain types `SlaCompliance` and `SlaReport` for per-partition breach duration and compliance ratio computation
  - `compute_sla_compliance()` function pairing opened/resolved incident transitions to calculate breach durations
  - `timeline_all()` store method fetching all incident timeline events for analytics
  - New API endpoint `GET /api/features/{feature_set}/sla` with `window_start` and `window_end` parameters
  - Frontend `SlaPanel` component with datetime-local window picker and results table
  - API types `SlaCompliancePartition` and `SlaReport` in `feature-api.ts`
  - Domain and API tests for SLA compliance calculation and endpoint

Partial code published for continued development. Known issues: STATUS.md.

## 1.6.0 — work-in-progress checkpoint

Partial code published for continued development. Known issues: STATUS.md.

## 1.5.0 — work-in-progress checkpoint

- Added webhook delivery retry endpoint (`POST /api/features/{feature_set}/webhooks/deliveries/{delivery_id}/retry`) allowing operators to re-dispatch failed webhook notifications with original payload and HMAC signature.
- Retry button surfaced in operator workspace for failed deliveries; records new delivery attempt with audit trail.

Partial code published for continued development. Known issues: STATUS.md.
## 1.4.0 — work-in-progress checkpoint

- Added cursor-based pagination (`after` parameter) to webhook deliveries endpoint for consistency with timeline and audit endpoints.
- Backward compatible: default behavior returns most recent deliveries first.

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
