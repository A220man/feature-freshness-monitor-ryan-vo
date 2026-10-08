import { useState, type FormEvent } from 'react'
import type { FeatureApi, Policy } from './feature-api'

export function MaterializationForm({api, policy, onSaved}: {api: FeatureApi; policy: Policy; onSaved: () => Promise<void>}) {
  const [partition, setPartition] = useState(policy.expected_partitions[0] || '')
  const [eventId, setEventId] = useState<string>(() => crypto.randomUUID())
  const [watermark, setWatermark] = useState(() => new Date().toISOString())
  const [completed, setCompleted] = useState(() => new Date().toISOString())
  const [rows, setRows] = useState(0)
  const [pending, setPending] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  async function submit(event: FormEvent) {
    event.preventDefault(); setPending(true); setError(''); setMessage('')
    try {
      const result = await api.ingest(policy.feature_set, {event_id:eventId, partition, source_watermark:watermark, completed_at:completed, row_count:rows})
      await onSaved(); setMessage(result.created ? 'Materialization recorded. Evaluate incidents to reconcile alerts.' : 'This exact event was already recorded; no duplicate was created.')
    } catch (error) { setError(error instanceof Error ? error.message : 'Unable to record materialization') }
    finally { setPending(false) }
  }
  return <section><h2>Record materialization</h2><p>Use the source watermark from your pipeline, not the time the request reaches this service. Reusing an event ID with identical data is safe.</p>
    <form onSubmit={submit}><fieldset disabled={pending}>
      <label>Partition<select aria-label="Partition" value={partition} onChange={e => setPartition(e.target.value)}>{policy.expected_partitions.map(p => <option key={p}>{p}</option>)}</select></label>
      <label>Event ID<input required value={eventId} onChange={e => setEventId(e.target.value)} /></label>
      <label>Source watermark (ISO 8601)<input required value={watermark} onChange={e => setWatermark(e.target.value)} /></label>
      <label>Completed at (ISO 8601)<input required value={completed} onChange={e => setCompleted(e.target.value)} /></label>
      <label>Row count<input type="number" min="0" step="1" required value={rows} onChange={e => setRows(Number(e.target.value))} /></label>
      <button type="submit">{pending ? 'Recording…' : 'Record materialization'}</button>
    </fieldset></form>{message && <p role="status">{message}</p>}{error && <p role="alert">{error}</p>}
  </section>
}
