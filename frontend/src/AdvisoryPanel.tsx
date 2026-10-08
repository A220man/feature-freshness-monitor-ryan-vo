import {useState} from 'react'
import type {FeatureApi} from './feature-api'

export function AdvisoryPanel({api,feature}:{api:FeatureApi;feature:string}) {
  const [pending,setPending]=useState(false)
  const [result,setResult]=useState<Awaited<ReturnType<FeatureApi['advisory']>>|null>(null)
  const [error,setError]=useState('')
  async function request(){
    setPending(true);setError('');setResult(null)
    try{setResult(await api.advisory(feature))}
    catch(error){setError(error instanceof Error?error.message:'Unable to request advice')}
    finally{setPending(false)}
  }
  return <section><h2>Optional AI advice</h2><p>Request an explanation of the current server-computed snapshot. Advice never changes policies or incident state. The configured provider receives partition health summaries, not source rows.</p>
    <button disabled={pending} onClick={()=>void request()}>{pending?'Requesting advice…':'Explain freshness'}</button>
    {result&&<><p role="status">{result.text}</p><p>Snapshot: {result.evaluated_at}. Advisory only.</p></>}
    {error&&<p role="alert">{error}</p>}
  </section>
}
