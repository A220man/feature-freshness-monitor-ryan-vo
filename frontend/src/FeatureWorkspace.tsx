import { useCallback, useEffect, useRef, useState } from 'react'
import type { FeatureApi, Principal, Policy, Evaluation, Incident, TimelineEvent, AuditEvent, Page } from './feature-api'
import { PolicyEditor } from './PolicyEditor'
import { MaterializationForm } from './MaterializationForm'
import { BatchImportForm } from './BatchImportForm'
import { AdvisoryPanel } from './AdvisoryPanel'

export function FeatureWorkspace({api, principal}: {api: FeatureApi; principal: Principal}) {
  const [policies, setPolicies] = useState<Policy[]>([])
  const [selected, setSelected] = useState('')
  const [creating, setCreating] = useState(false)
  const [health, setHealth] = useState<Evaluation | null>(null)
  const [incidents, setIncidents] = useState<Incident[]>([])
  const [timeline, setTimeline] = useState<Page<TimelineEvent>>({events:[], next_cursor:0})
  const [audit, setAudit] = useState<Page<AuditEvent>>({events:[], next_cursor:0})
  const [error, setError] = useState('')
  const [pending, setPending] = useState(false)
  const [partitionSearch, setPartitionSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState<'all' | 'fresh' | 'stale' | 'missing'>('all')
  const [exportStatus, setExportStatus] = useState('')
  const requestId = useRef(0)
  const admin = principal.roles.includes('admin')
  const operator = admin || principal.roles.includes('operator')
  const policy = policies.find(p => p.feature_set === selected)

  const refresh = useCallback(async (name: string) => {
    const id = ++requestId.current; setError('')
    try {
      const list = await api.policies()
      const feature = name || list[0]?.feature_set || ''
      if (!feature) {if(id===requestId.current){setPolicies(list);setSelected('');setHealth(null)};return}
      const [evaluation, active, events, auditEvents] = await Promise.all([
        api.health(feature), api.incidents(feature), api.timeline(feature,0,25), admin ? api.audit(feature,0,25) : Promise.resolve({events:[],next_cursor:0}),
      ])
      if (id !== requestId.current) return
      setPolicies(list);setSelected(feature);setHealth(evaluation);setIncidents(active);setTimeline(events);setAudit(auditEvents)
    } catch (error) {if(id===requestId.current)setError(error instanceof Error ? error.message : 'Unable to load feature sets')}
  }, [api,admin])

  useEffect(() => {void refresh('');return () => {requestId.current++}}, [refresh])

  async function evaluate() {
    setPending(true);setError('')
    try {await api.evaluate(selected);await refresh(selected)}
    catch(error){setError(error instanceof Error ? error.message : 'Unable to evaluate incidents')}
    finally{setPending(false)}
  }

  async function loadMore(kind: 'timeline' | 'audit') {
    const feature = selected, id=requestId.current;setError('')
    try {
      if(kind==='timeline') {const next=await api.timeline(feature,timeline.next_cursor,25);if(id===requestId.current)setTimeline(old=>({events:[...old.events,...next.events.filter(e=>!old.events.some(o=>o.sequence===e.sequence))],next_cursor:next.next_cursor}))}
      else {const next=await api.audit(feature,audit.next_cursor,25);if(id===requestId.current)setAudit(old=>({events:[...old.events,...next.events.filter(e=>!old.events.some(o=>o.sequence===e.sequence))],next_cursor:next.next_cursor}))}
    } catch(error){setError(error instanceof Error ? error.message : 'Unable to load history')}
  }

  async function exportReport() {
    setError(''); setExportStatus('')
    try {
      const report = await api.export(selected)
      const blob = new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' })
      if (typeof URL.createObjectURL === 'function') {
        const url = URL.createObjectURL(blob)
        const a = document.createElement('a')
        a.href = url
        a.download = `${selected}-freshness-report.json`
        a.click()
        URL.revokeObjectURL(url)
      }
      setExportStatus('Freshness snapshot exported.')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to export report')
    }
  }

  const displayedPartitions = (health?.partitions || []).filter(p => {
    if (statusFilter !== 'all' && p.status !== statusFilter) return false
    if (partitionSearch.trim() && !p.partition.toLowerCase().includes(partitionSearch.trim().toLowerCase())) return false
    return true
  })

  return <main><h1>Feature Freshness Monitor</h1><p>Detect stale or missing model features, reconcile incidents, and audit pipeline recovery.</p>
    {error && <p role="alert">{error}</p>}
    <nav aria-label="Feature controls"><label>Feature set selection<select value={selected} onChange={e=>{setCreating(false);setPartitionSearch('');setStatusFilter('all');void refresh(e.target.value)}}><option value="" disabled>Select a feature set</option>{policies.map(p=><option key={p.feature_set}>{p.feature_set}</option>)}</select></label>
      <button onClick={()=>void refresh(selected)}>Refresh</button>{operator&&<button onClick={()=>setCreating(true)}>New feature set</button>}
    </nav>
    {operator && (creating || !policies.length) && <PolicyEditor api={api} onSaved={async name=>{await refresh(name);setCreating(false)}} />}
    {!policies.length && !operator && <p>No feature sets have been configured. Ask an administrator to create a freshness policy.</p>}
    {policy && health && <>
      <section><h2>Partition health: {selected}</h2><p>Evaluated at {health.evaluated_at}. Coverage: {Math.round(health.coverage*100)}%. Missing: {health.missing_count}. Stale: {health.stale_count}.</p>
        {health.unexpected_partitions && health.unexpected_partitions.length > 0 && (
          <aside aria-label="Unexpected partitions"><p><strong>Unexpected partitions:</strong> {health.unexpected_partitions.join(', ')}</p></aside>
        )}
        <div role="search" aria-label="Partition filters">
          <label>Search partitions<input value={partitionSearch} onChange={e=>setPartitionSearch(e.target.value)} placeholder="Filter partition names…" /></label>
          <label>Filter by status<select aria-label="Filter by status" value={statusFilter} onChange={e=>setStatusFilter(e.target.value as typeof statusFilter)}>
            <option value="all">All statuses</option>
            <option value="fresh">Fresh</option>
            <option value="stale">Stale</option>
            <option value="missing">Missing</option>
          </select></label>
          {(partitionSearch || statusFilter !== 'all') && <button type="button" onClick={()=>{setPartitionSearch('');setStatusFilter('all')}}>Reset filters</button>}
          <button type="button" onClick={()=>void exportReport()}>Export report</button>
        </div>
        {exportStatus && <p role="status">{exportStatus}</p>}
        {displayedPartitions.length ? (
          <table><thead><tr><th>Partition</th><th>Status</th><th>Source watermark</th><th>Source age (seconds)</th><th>Rows</th></tr></thead><tbody>{displayedPartitions.map(p=><tr key={p.partition}><td>{p.partition}</td><td>{p.status}</td><td>{p.source_watermark||'No materialization'}</td><td>{p.source_age_seconds ?? '—'}</td><td>{p.row_count ?? '—'}</td></tr>)}</tbody></table>
        ) : (
          <p role="status">No partitions match current filters.</p>
        )}
        {operator && <button disabled={pending} onClick={()=>void evaluate()}>{pending?'Evaluating…':'Evaluate incidents'}</button>}
      </section>
      {operator && <AdvisoryPanel key={selected+":advisory"} api={api} feature={selected}/>}
      {operator && <MaterializationForm key={selected} api={api} policy={policy} onSaved={()=>refresh(selected)} />}
      {operator && <BatchImportForm key={selected+":batch"} api={api} policy={policy} onSaved={()=>refresh(selected)} />}
      <section><h2>Active incidents</h2>{incidents.length?<ul>{incidents.map(i=><li key={i.incident_id}>{i.partition}: {i.condition}, opened {i.opened_at}</li>)}</ul>:<p>No active incidents.</p>}</section>
      <section><h2>Incident timeline</h2><ol>{timeline.events.map(e=><li key={e.sequence}>{e.occurred_at}: {e.partition_name} {e.condition} incident {e.action}</li>)}</ol><button disabled={!timeline.events.length} onClick={()=>void loadMore('timeline')}>Load more timeline entries</button></section>
      {operator && <PolicyEditor api={api} policy={policy} onSaved={refresh}/>}
      {admin && <><section><h2>Audit log</h2><ol>{audit.events.map(e=><li key={e.sequence}>{e.occurred_at}: {e.actor} — {e.action}<pre>{e.detail_json}</pre></li>)}</ol><button disabled={!audit.events.length} onClick={()=>void loadMore('audit')}>Load more audit entries</button></section></>}
    </>}
  </main>
}
