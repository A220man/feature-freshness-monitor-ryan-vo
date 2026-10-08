export interface Principal { subject: string; roles: string[] }
export interface PolicyInput { expected_partitions: string[]; max_age_seconds: number; grace_seconds?: number; expected_version?: number }
export interface Policy extends PolicyInput { feature_set: string; version: number; updated_at: string; last_evaluated_at: string | null }
export interface MaterializationInput { event_id: string; partition: string; source_watermark: string; completed_at: string; row_count: number }
export interface PartitionHealth { feature_set: string; partition: string; status: 'fresh' | 'stale' | 'missing'; evaluated_at: string; event_id: string | null; source_watermark: string | null; completed_at: string | null; source_age_seconds: number | null; materialization_delay_seconds: number | null; overdue_seconds: number | null; due_at: string | null; row_count: number | null }
export interface Evaluation { feature_set: string; evaluated_at: string; partitions: PartitionHealth[]; unexpected_partitions: string[]; healthy: boolean; coverage: number; stale_count: number; missing_count: number }
export interface Incident { incident_id: string; feature_set: string; partition: string; condition: 'missing' | 'stale'; opened_at: string; last_seen_at: string; resolved_at: string | null }
export interface Transition { incident_id: string; action: 'opened' | 'resolved'; occurred_at: string; feature_set: string; partition: string; condition: 'missing' | 'stale' }
export interface TimelineEvent { sequence: number; incident_id: string; action: 'opened' | 'resolved'; occurred_at: string; feature_set: string; partition_name: string; condition: 'missing' | 'stale' }
export interface AuditEvent { sequence: number; actor: string; action: string; feature_set: string; occurred_at: string; detail_json: string }
export interface Page<T> { events: T[]; next_cursor: number }

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) { super(message); this.name = 'ApiError'; this.status = status }
}

/** Every method maps to an actual FastAPI route and its response envelope. */
export class FeatureApi {
  private baseUrl: string
  private token: () => string | null
  constructor(baseUrl: string, token: () => string | null) { this.baseUrl = baseUrl.replace(/\/$/, ''); this.token = token }

  private async request<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
    const token = this.token()
    if (!token) throw new ApiError(401, 'Sign in to continue')
    const headers = new Headers({ Authorization: `Bearer ${token}` })
    const options: RequestInit = { method, headers, credentials: 'omit' }
    if (body !== undefined) { headers.set('Content-Type', 'application/json'); options.body = JSON.stringify(body) }
    const response = await fetch(this.baseUrl + path, options)
    if (!response.ok) {
      let message = `Request failed (${response.status})`
      try {
        const payload = await response.json() as { detail?: unknown }
        if (typeof payload.detail === 'string') message = payload.detail
        else if (Array.isArray(payload.detail)) message = 'Invalid input; check the field values'
      } catch { /* An HTML gateway error must still become a useful API error. */ }
      throw new ApiError(response.status, message)
    }
    return response.json() as Promise<T>
  }

  advisory(feature: string): Promise<{status: string; text: string; advisory_only: true; feature_set: string; evaluated_at: string}> {
    return this.request(`/features/${encodeURIComponent(feature)}/advisory`, 'POST')
  }
  me(): Promise<Principal> { return this.request('/me') }
  async policies(): Promise<Policy[]> { return (await this.request<{ features: Policy[] }>('/features')).features }
  savePolicy(feature: string, policy: PolicyInput): Promise<{ feature_set: string; version: number }> {
    return this.request(`/features/${encodeURIComponent(feature)}`, 'PUT', policy)
  }
  ingest(feature: string, event: MaterializationInput): Promise<{ event_id: string; created: boolean }> {
    return this.request(`/features/${encodeURIComponent(feature)}/materializations`, 'POST', event)
  }
  health(feature: string): Promise<Evaluation> { return this.request(`/features/${encodeURIComponent(feature)}/health`) }
  evaluate(feature: string): Promise<{ evaluation: Evaluation; incidents: Incident[]; transitions: Transition[] }> {
    return this.request(`/features/${encodeURIComponent(feature)}/evaluate`, 'POST')
  }
  async incidents(feature: string, activeOnly = true): Promise<Incident[]> {
    return (await this.request<{ incidents: Incident[] }>(`/features/${encodeURIComponent(feature)}/incidents?active_only=${activeOnly}`)).incidents
  }
  timeline(feature: string, after = 0, limit = 100): Promise<Page<TimelineEvent>> {
    return this.request(`/features/${encodeURIComponent(feature)}/timeline?after=${after}&limit=${limit}`)
  }
  audit(feature: string, after = 0, limit = 100): Promise<Page<AuditEvent>> {
    return this.request(`/features/${encodeURIComponent(feature)}/audit?after=${after}&limit=${limit}`)
  }
}
