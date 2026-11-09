
import { FormEvent, useState, useCallback } from 'react'
import type { FeatureApi, SlaReport } from './feature-api'

export function SlaPanel({
  api,
  feature,
  operator,
}: {
  api: FeatureApi
  feature: string
  operator: boolean
}) {
  const [windowStart, setWindowStart] = useState('')
  const [windowEnd, setWindowEnd] = useState('')
  const [report, setReport] = useState<SlaReport | null>(null)
  const [error, setError] = useState('')
  const [status, setStatus] = useState('')
  const [pending, setPending] = useState(false)

  const now = new Date().toISOString().slice(0, 16)
  const weekAgo = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000).toISOString().slice(0, 16)

  if (!windowStart) setWindowStart(weekAgo)
  if (!windowEnd) setWindowEnd(now)

  const loadReport = useCallback(async () => {
    if (!windowStart || !windowEnd) return
    setPending(true)
    setError('')
    setStatus('')
    try {
      const data = await api.slaCompliance(feature, windowStart, windowEnd)
      setReport(data)
      setStatus('SLA report loaded.')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to load SLA report')
    } finally {
      setPending(false)
    }
  }, [api, feature, windowStart, windowEnd])

  return (
    <section aria-label="SLA compliance">
      <h2>SLA Compliance Analytics</h2>
      <p>Compute per-partition incident breach durations and SLA compliance ratios across evaluation windows.</p>
      {error && <p role="alert">{error}</p>}
      {status && <p role="status">{status}</p>}
      <form onSubmit={e => { e.preventDefault(); void loadReport() }}>
        <label>
          Window start (inclusive)
          <input type="datetime-local" required value={windowStart} onChange={e => setWindowStart(e.target.value)} />
        </label>
        <label>
          Window end (exclusive)
          <input type="datetime-local" required value={windowEnd} onChange={e => setWindowEnd(e.target.value)} />
        </label>
        <button type="submit" disabled={pending}>
          {pending ? 'Loading…' : 'Generate SLA Report'}
        </button>
      </form>
      {report && (
        <>
          <h3>Overall compliance: {(report.overall_compliance_ratio * 100).toFixed(2)}%</h3>
          <p>Window: {report.window_start} to {report.window_end}</p>
          <table>
            <thead>
              <tr>
                <th>Partition</th>
                <th>Breach duration</th>
                <th>Window duration</th>
                <th>Compliance</th>
                <th>Incident count</th>
              </tr>
            </thead>
            <tbody>
              {report.partitions.map(p => (
                <tr key={p.partition}>
                  <td>{p.partition}</td>
                  <td>{(p.total_breach_seconds / 3600).toFixed(2)} hours</td>
                  <td>{(p.window_seconds / 3600).toFixed(2)} hours</td>
                  <td>{(p.compliance_ratio * 100).toFixed(2)}%</td>
                  <td>{p.incident_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </section>
  )
}
