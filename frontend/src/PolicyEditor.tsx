import { useEffect, useState, type FormEvent } from 'react'
import type { FeatureApi, Policy } from './feature-api'

export function PolicyEditor({ api, policy, onSaved }: {api: FeatureApi; policy?: Policy; onSaved: (feature: string) => Promise<void>}) {
  const [name, setName] = useState('')
  const [partitions, setPartitions] = useState('')
  const [maxAge, setMaxAge] = useState(300)
  const [grace, setGrace] = useState(0)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => {
    setName(policy?.feature_set || ''); setPartitions(policy?.expected_partitions.join(', ') || '')
    setMaxAge(policy?.max_age_seconds || 300); setGrace(policy?.grace_seconds || 0); setError('')
  }, [policy])
  async function submit(event: FormEvent) {
    event.preventDefault(); setPending(true); setError('')
    try {
      const expected = partitions.split(',').map(p => p.trim()).filter(Boolean)
      if (!expected.length || new Set(expected).size !== expected.length) throw new Error('Enter unique partition names separated by commas')
      await api.savePolicy(name.trim(), {expected_partitions: expected, max_age_seconds: maxAge, grace_seconds: grace, expected_version: policy?.version || 0})
      await onSaved(name.trim())
    } catch (error) { setError(error instanceof Error ? error.message : 'Unable to save policy') }
    finally { setPending(false) }
  }
  return <section aria-labelledby="policy-title"><h2 id="policy-title">{policy ? 'Edit freshness policy' : 'Create freshness policy'}</h2>
    <p>Policies track source watermarks for every expected partition. Version checks prevent overwriting another operator’s changes.</p>
    <form onSubmit={submit}><fieldset disabled={pending}>
      <label>Feature set<input required value={name} readOnly={!!policy} onChange={e => setName(e.target.value)} /></label>
      <label>Expected partitions<input required value={partitions} onChange={e => setPartitions(e.target.value)} placeholder="us, eu" /></label>
      <label>Maximum source age (seconds)<input type="number" min="1" required value={maxAge} onChange={e => setMaxAge(Number(e.target.value))} /></label>
      <label>Grace period (seconds)<input type="number" min="0" required value={grace} onChange={e => setGrace(Number(e.target.value))} /></label>
      <button type="submit">{pending ? 'Saving…' : 'Save policy'}</button>
    </fieldset></form>{error && <p role="alert">{error}</p>}
  </section>
}
