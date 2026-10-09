import { useState, type FormEvent } from 'react'
import type { FeatureApi, Policy, MaterializationInput } from './feature-api'

export function BatchImportForm({ api, policy, onSaved }: { api: FeatureApi; policy: Policy; onSaved: () => Promise<void> }) {
  const [jsonText, setJsonText] = useState('')
  const [pending, setPending] = useState(false)
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')

  function populateSample() {
    const now = new Date().toISOString()
    const sample: MaterializationInput[] = policy.expected_partitions.slice(0, 3).map((p, i) => ({
      event_id: `batch-${p}-${Date.now()}-${i}`,
      partition: p,
      source_watermark: now,
      completed_at: now,
      row_count: 100 * (i + 1),
    }))
    setJsonText(JSON.stringify(sample, null, 2))
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    setPending(true)
    setError('')
    setStatus('')
    try {
      let parsed: unknown
      try {
        parsed = JSON.parse(jsonText)
      } catch {
        throw new Error('Invalid JSON format; enter a JSON array of materializations')
      }
      if (!Array.isArray(parsed) || parsed.length === 0) {
        throw new Error('Provide a non-empty array of materializations')
      }
      const items: MaterializationInput[] = parsed.map((item, idx) => {
        if (!item || typeof item !== 'object') throw new Error(`Item ${idx} is not an object`)
        const record = item as Record<string, unknown>
        const { event_id, partition, source_watermark, completed_at, row_count } = record
        if (typeof event_id !== 'string' || !event_id.trim()) throw new Error(`Item ${idx} missing valid event_id`)
        if (typeof partition !== 'string' || !partition.trim()) throw new Error(`Item ${idx} missing valid partition`)
        if (typeof source_watermark !== 'string') throw new Error(`Item ${idx} missing source_watermark`)
        if (typeof completed_at !== 'string') throw new Error(`Item ${idx} missing completed_at`)
        if (typeof row_count !== 'number' || row_count < 0) throw new Error(`Item ${idx} row_count must be a nonnegative integer`)
        return {
          event_id: event_id.trim(),
          partition: partition.trim(),
          source_watermark,
          completed_at,
          row_count,
        }
      })
      const result = await api.batchIngest(policy.feature_set, items)
      setStatus(`Batch processed: ${result.created} created, ${result.replayed} replayed (${result.total} total).`)
      setJsonText('')
      await onSaved()
    } catch (error) {
      setError(error instanceof Error ? error.message : 'Unable to import materializations')
    } finally {
      setPending(false)
    }
  }

  return (
    <section aria-labelledby="batch-import-title">
      <h2 id="batch-import-title">Batch import materializations</h2>
      <p>Import multiple partition materializations at once. Duplicate events are replayed idempotently.</p>
      <form onSubmit={submit}>
        <fieldset disabled={pending}>
          <label>
            Materializations JSON array
            <textarea
              rows={6}
              required
              value={jsonText}
              onChange={e => setJsonText(e.target.value)}
              placeholder='[{"event_id": "run-01", "partition": "us", "source_watermark": "...", "completed_at": "...", "row_count": 100}]'
            />
          </label>
          <div>
            <button type="submit">{pending ? 'Importing…' : 'Import materializations'}</button>
            <button type="button" onClick={populateSample}>Load sample batch</button>
          </div>
        </fieldset>
      </form>
      {status && <p role="status">{status}</p>}
      {error && <p role="alert">{error}</p>}
    </section>
  )
}
