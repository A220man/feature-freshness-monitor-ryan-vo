import { FormEvent, useState } from 'react'
import type { FeatureApi, WebhookDelivery, WebhookSubscription } from './feature-api'

export function WebhookPanel({
  api,
  feature,
  operator,
  webhooks,
  deliveries,
  onSaved,
}: {
  api: FeatureApi
  feature: string
  operator: boolean
  webhooks: WebhookSubscription[]
  deliveries: WebhookDelivery[]
  onSaved: () => Promise<void>
}) {
  const [targetUrl, setTargetUrl] = useState('')
  const [signingSecret, setSigningSecret] = useState('')
  const [eventMode, setEventMode] = useState<'both' | 'opened' | 'resolved'>('both')
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')
  const [pending, setPending] = useState(false)

  async function handleRegister(event: FormEvent) {
    event.preventDefault()
    setPending(true); setError(''); setStatus('')
    const events: ('opened' | 'resolved')[] =
      eventMode === 'both' ? ['opened', 'resolved'] : [eventMode]
    try {
      await api.registerWebhook(feature, {
        target_url: targetUrl.trim(),
        events,
        signing_secret: signingSecret,
      })
      setTargetUrl('')
      setSigningSecret('')
      setStatus('Webhook endpoint registered.')
      await onSaved()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to register webhook')
    } finally {
      setPending(false)
    }
  }

  async function handleDelete(webhookId: string) {
    setPending(true); setError(''); setStatus('')
    try {
      await api.deleteWebhook(feature, webhookId)
      setStatus('Webhook endpoint removed.')
      await onSaved()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to remove webhook')
    } finally {
      setPending(false)
    }
  }

  return (
    <section aria-label="Alert webhooks">
      <h2>Alert webhooks</h2>
      <p>Dispatch HMAC-SHA256 signed HTTPS notifications when partition incidents open or resolve.</p>
      {error && <p role="alert">{error}</p>}
      {status && <p role="status">{status}</p>}
      {webhooks.length ? (
        <ul>
          {webhooks.map(w => (
            <li key={w.webhook_id}>
              <code>{w.target_url}</code> ({w.events.join(', ')})
              {operator && (
                <button type="button" disabled={pending} onClick={() => void handleDelete(w.webhook_id)}>
                  Remove webhook
                </button>
              )}
            </li>
          ))}
        </ul>
      ) : (
        <p>No webhook endpoints registered.</p>
      )}
      {operator && (
        <form onSubmit={e => void handleRegister(e)}>
          <label>
            Webhook URL
            <input
              type="url"
              required
              placeholder="https://alerts.example.com/freshness"
              value={targetUrl}
              onChange={e => setTargetUrl(e.target.value)}
            />
          </label>
          <label>
            Webhook events
            <select value={eventMode} onChange={e => setEventMode(e.target.value as typeof eventMode)}>
              <option value="both">Opened and resolved</option>
              <option value="opened">Opened only</option>
              <option value="resolved">Resolved only</option>
            </select>
          </label>
          <label>
            Signing secret
            <input
              type="password"
              required
              minLength={8}
              value={signingSecret}
              onChange={e => setSigningSecret(e.target.value)}
            />
          </label>
          <button type="submit" disabled={pending}>
            {pending ? 'Saving webhook…' : 'Register webhook'}
          </button>
        </form>
      )}
      <h3>Recent webhook deliveries</h3>
      {deliveries.length ? (
        <table>
          <thead>
            <tr>
              <th>Action</th>
              <th>Partition</th>
              <th>Destination</th>
              <th>Delivery status</th>
              <th>Signature</th>
            </tr>
          </thead>
          <tbody>
            {deliveries.map(d => (
              <tr key={d.delivery_id}>
                <td>{d.action}</td>
                <td>{d.partition}</td>
                <td>{d.target_url}</td>
                <td>{d.status}{d.status_code ? ` (${d.status_code})` : ''}</td>
                <td><code>{d.signature.slice(0, 20)}…</code></td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p>No webhook deliveries recorded.</p>
      )}
    </section>
  )
}
