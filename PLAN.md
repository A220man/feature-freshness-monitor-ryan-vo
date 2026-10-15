# Product development plan

Each round completes one feature or repair. Future items are planned, not implemented claims.

## Current round

partition-staleness-alert-webhooks

## Roadmap

### policy-management — Partition Freshness Policy Management

Status: done

Acceptance:
- Define partition freshness policies with max age and grace seconds
- Optimistic version concurrency checks prevent lost updates

Implementation: `backend/app/domain/freshness.py`, `backend/app/services/freshness_store.py`, `backend/app/api/features.py`, `frontend/src/PolicyEditor.tsx`

Tests: `backend/tests/test_freshness_domain.py`, `backend/tests/test_features_api.py`, `frontend/src/workflows.test.tsx`

### materialization-ingest — Replay-Safe Materialization Ingestion

Status: done

Acceptance:
- Ingest replay-safe materializations with idempotency on duplicate event IDs
- Reject conflicting materializations and future timestamps

Implementation: `backend/app/domain/freshness.py`, `backend/app/services/freshness_store.py`, `backend/app/api/features.py`, `frontend/src/MaterializationForm.tsx`

Tests: `backend/tests/test_freshness_store.py`, `backend/tests/test_features_api.py`, `frontend/src/workflows.test.tsx`

### freshness-evaluation — Source-Watermark Freshness Evaluation & Incident Recovery

Status: done

Acceptance:
- Evaluate source-watermark freshness against policy deadlines
- Open, track, and resolve stale and missing incidents transactionally

Implementation: `backend/app/domain/freshness.py`, `backend/app/services/freshness_store.py`, `backend/app/api/features.py`, `frontend/src/FeatureWorkspace.tsx`

Tests: `backend/tests/test_freshness_domain.py`, `backend/tests/test_freshness_store.py`, `backend/tests/test_features_api.py`

### audit-and-timeline — Administrator Audit Log & Incident Timeline

Status: done

Acceptance:
- Record admin audit logs for policy and ingestion events
- Track cursor-paginated timeline of incident transitions

Implementation: `backend/app/services/freshness_store.py`, `backend/app/api/features.py`, `frontend/src/FeatureWorkspace.tsx`

Tests: `backend/tests/test_freshness_store.py`, `backend/tests/test_features_api.py`

### oidc-pkce-auth — OIDC PKCE Authentication & Role-Based Access Control

Status: done

Acceptance:
- Authenticate users via Keycloak OIDC authorization code flow with PKCE and state/nonce validation
- Enforce role-based access control across viewer, operator, and admin roles

Implementation: `backend/app/security/oidc.py`, `backend/app/main.py`, `frontend/src/auth-flow.ts`, `frontend/src/FreshnessApp.tsx`

Tests: `backend/tests/test_oidc.py`, `frontend/tests/auth-flow.test.mjs`, `frontend/tests/react-integration.test.mjs`

### llm-advisory — Provider-Neutral AI Freshness Explanations

Status: done

Acceptance:
- Provide opt-in advisory explanations grounded in actual partition health
- Support multiple providers with explicit unavailable states without fabricating responses

Implementation: `backend/app/services/llm_advisory.py`, `backend/app/api/advisory.py`, `frontend/src/AdvisoryPanel.tsx`

Tests: `backend/tests/test_llm_advisory.py`, `backend/tests/test_advisory_api.py`, `frontend/src/workflows.test.tsx`

### batch-ingest-and-export — Batch Ingestion, Health Filtering & Snapshot Export

Status: done

Acceptance:
- Ingest atomic batches of materializations with replay reporting
- Export complete feature health, policy, and incident snapshots as JSON
- Filter and search partitions in operator workspace

Implementation: `backend/app/api/features.py`, `backend/app/services/freshness_store.py`, `frontend/src/BatchImportForm.tsx`, `frontend/src/FeatureWorkspace.tsx`

Tests: `backend/tests/test_features_api.py`, `backend/tests/test_freshness_store.py`, `frontend/src/workflows.test.tsx`

### materialization-history-and-telemetry — Materialization Event History and Ingestion Telemetry

Status: done

Acceptance:
- Query recent materialization events for a feature set with optional partition filter and limit pagination
- Return event ID, partition, source watermark, completed at, row count, and ingestion receipt timestamp
- Surface materialization event history table in the operator console with partition filtering and empty state
- Reject unauthorized unauthenticated or malformed requests

Implementation: `backend/app/services/freshness_store.py`, `backend/app/api/features.py`, `frontend/src/feature-api.ts`, `frontend/src/FeatureWorkspace.tsx`

Tests: `backend/tests/test_freshness_store.py`, `backend/tests/test_features_api.py`, `frontend/src/workflows.test.tsx`

### partition-staleness-alert-webhooks — Configurable Webhook Alert Dispatcher

Status: done

Acceptance:
- Register, list, and delete HTTPS webhook alert subscriptions with SSRF host validation
- Dispatch HMAC-SHA256 signed HTTP webhook notifications to registered endpoints when incidents open or resolve
- Record webhook delivery outcomes and surface webhook management and delivery history in the operator workspace

Implementation: `backend/app/services/webhook_dispatcher.py`, `backend/app/services/freshness_store.py`, `backend/app/api/features.py`, `frontend/src/feature-api.ts`, `frontend/src/WebhookPanel.tsx`, `frontend/src/FeatureWorkspace.tsx`

Tests: `backend/tests/test_freshness_store.py`, `backend/tests/test_features_api.py`, `frontend/src/workflows.test.tsx`

### sla-compliance-analytics — Partition SLA Compliance & Breach Duration Analytics

Status: planned

Acceptance:
- Compute per-partition incident breach durations and SLA compliance ratios across evaluation windows

