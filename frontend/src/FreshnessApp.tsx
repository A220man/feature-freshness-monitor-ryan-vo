import { useEffect, useState } from 'react'
import { AuthFlow, type AuthConfig } from './auth-flow'
import { FeatureApi, type Principal } from './feature-api'
import { FeatureWorkspace } from './FeatureWorkspace'

export interface AppConfig extends AuthConfig {apiBase: string}
export function FreshnessApp({config}: {config: AppConfig}) {
  const [services] = useState(()=> {
    try {const auth=new AuthFlow(config,sessionStorage);return {auth,api:new FeatureApi(config.apiBase,()=>auth.accessToken()),error:''}}
    catch(error){return {auth:null,api:null,error:error instanceof Error?error.message:'Invalid application configuration'}}
  })
  const [principal,setPrincipal]=useState<Principal|null>(null)
  const [pending,setPending]=useState(false)
  const [error,setError]=useState(services.error)
  useEffect(()=>{
    if(!services.auth||!services.api)return
    const callback=new URL(config.redirectUri)
    if(location.pathname!==callback.pathname)return
    // Consume the callback once and remove credentials from the address bar immediately.
    const response=location.href;history.replaceState(null,'','/');setPending(true)
    void services.auth.acceptCallback(response).then(()=>services.api!.me()).then(setPrincipal).catch(error=>{
      services.auth!.clear();setError(error instanceof Error?error.message:'Unable to sign in')
    }).finally(()=>setPending(false))
  },[services,config.redirectUri])
  useEffect(()=>{
    if(!principal||!services.auth)return
    const timer=setInterval(()=>{if(!services.auth!.accessToken()){setPrincipal(null);setError('Your session expired. Sign in again.')}},1000)
    return()=>clearInterval(timer)
  },[principal,services])
  async function login(){
    if(!services.auth)return
    setPending(true);setError('')
    try{location.assign(await services.auth.begin())}
    catch(error){setError(error instanceof Error?error.message:'Unable to start sign-in');setPending(false)}
  }
  function logout(){services.auth?.clear();setPrincipal(null);setError('');history.replaceState(null,'','/')}
  return <><header><strong>Feature Freshness Monitor</strong>{principal&&<><span>Signed in as {principal.subject}</span><button onClick={logout}>Sign out</button></>}</header>
    {error&&<p role="alert">{error}</p>}
    {principal&&services.api?<FeatureWorkspace api={services.api} principal={principal}/>:<main><h1>Monitor the freshness of your ML features</h1><p>Sign in through your organization’s identity provider to inspect policies, feature materializations, and incidents.</p><button disabled={pending||!services.auth} onClick={()=>void login()}>{pending?'Signing in…':'Sign in'}</button><p>Signing out clears this application’s token. Your identity provider’s SSO session may remain active.</p></main>}
  </>
}
