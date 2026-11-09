import { FormEvent, useState, useCallback } from 'react'
import type { FeatureApi, WebhookDelivery, WebhookSubscription } from './feature-api'

export function WebhookPanel({
  api,
  feature,
  operator,
  webhooks,
  deliveries: initialDeliveries,
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
  const [deliveries, setDeliveries] = useState<WebhookDelivery[]>(initialDeliveries)
  const [deliveryCursor, setDeliveryCursor] = useState<number | null>(initialDeliveries.length > 0 ? initialDeliveries[initialDeliveries.length - 1].delivery_id : null)
  const [hasMoreDeliveries, setHasMoreDeliveries] = useState(initialDeliveries.length >= 25)

  const loadMoreDeliveries = useCallback(async () => {
    if (deliveryCursor === null) return
    setPending(true); setError(''); setStatus('')
    try {
      const next = await api.webhookDeliveries(feature, 25, deliveryCursor)
      setDeliveries(prev => [...prev, ...next])
      if (next.length > 0) {
        setDeliveryCursor(next[next.length - 1].delivery_id)
      }
      setHasMoreDeliveries(next.length === 25)
      setStatus('Loaded more deliveries.')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to load more deliveries')
    } finally {
      setPending(false)
    }
  }, [api, feature, deliveryCursor])

  async function handleRegister(event: FormEvent) {
    event.preventDefault()
    setPending(true); setError(''); setStatus('')
    const events: ('opened' | 'resolved')[] =
      eventMode === 'both' ? ['opened', 'resolved'] : [eventMode]
    try {
      await api.registerWebhook(feature, { target_url: targetUrl, events, signing_secret: signingSecret })
      setStatus('Webhook endpoint registered.')
      setTargetUrl(''); setSigningSecret('')
      onSaved()
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
      onSaved()
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
        <>
          <table>
            <thead>
              <tr>
                <th>Action</th>
                <th>Partition</th>
                <th>Destination</th>
                <th>Delivery status</th>
                <th>Signature</th>
                {operator && <th>Actions</th>}
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
                  {operator && d.status === 'failed' && (
                    <td>
                      <button
                        type="button"
                        disabled={pending}
                        onClick={async () => {
                          setPending(true); setError(''); setStatus('')
                          try {
                            await api.retryWebhookDelivery(feature, d.delivery_id)
                            setStatus('Webhook delivery retried.')
                            onSaved()
                          } catch (err) {
                            setError(err instanceof Error ? err.message : 'Retry failed')
                          } finally {
                            setPending(false)
                          }
                        }}
                      >
                        {pending ? 'Retrying…' : 'Retry'}
                      </button>
                    </td>
                  )}
                  {operator && d.status !== 'failed' && <td/>}
                </tr>
              ))}
            </tbody>
          </table>
          {hasMoreDeliveries && deliveryCursor !== null && (
            <button type="button" disabled={pending} onClick={() => void loadMoreDeliveries()}>
              {pending ? 'Loading…' : 'Load more deliveries'}
            </button>
          )}
        </>
      ) : (
        <p>No webhook deliveries recorded.</p>
      )}
    </section>
  )
}
