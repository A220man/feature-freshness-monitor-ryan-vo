import { createServer } from 'node:http'
import { generateKeyPairSync, sign, createHash, randomUUID } from 'node:crypto'
import { spawn } from 'node:child_process'
import { mkdtemp, rm, readFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { once } from 'node:events'
import { test } from 'node:test'
import { chromium } from 'playwright'
import assert from 'node:assert/strict'
import { FeatureApi } from '../src/feature-api.ts'
import { AuthFlow } from '../src/auth-flow.ts'
import { InMemoryWebStorage } from 'oidc-client-ts'

// When published under frontend/tests, the default resolves to the repository root.
const repository = process.env.FRESHNESS_TEST_REPOSITORY
  ? resolve(process.env.FRESHNESS_TEST_REPOSITORY)
  : fileURLToPath(new URL('../../', import.meta.url))
const python = process.env.FRESHNESS_TEST_PYTHON || join(repository, '.venv/bin/python')

for (const browserRole of ['admin','operator','viewer']) {
test('React screens with '+browserRole+' role: real login, workflows and authorization controls', async () => {
  let loginRoles=['admin']
  const {privateKey,publicKey}=generateKeyPairSync('rsa',{modulusLength:2048})
  const jwk={...publicKey.export({format:'jwk'}),kid:'ephemeral-fixture',alg:'RS256',use:'sig'}
  let apiOrigin, browser
  const frontend=createServer(async(req,res)=>{
    if(req.url==='/react-app.js') {res.setHeader('Content-Type','text/javascript');return res.end(await readFile(new URL('./react-app.js',import.meta.url)))}
    res.setHeader('Content-Type','text/html')
    res.end('<!doctype html><html><body><div id="root"></div><script>window.fixtureConfig='+JSON.stringify({issuer,clientId:'freshness-ui',redirectUri:frontendOrigin+'/callback',apiBase:apiOrigin+'/api'})+'</script><script type="module" src="/react-app.js"></script></body></html>')
  })
  await new Promise(resolve=>frontend.listen(0,'127.0.0.1',resolve))
  const frontendOrigin='http://127.0.0.1:'+frontend.address().port
  const codes=new Map()
  function signed(claims) {
    const now=Math.floor(Date.now()/1000),encode=value=>Buffer.from(JSON.stringify(value)).toString('base64url')
    const data=encode({alg:'RS256',kid:jwk.kid})+'.'+encode({iss:issuer,sub:'fixture-user',iat:now,exp:now+300,...claims})
    return data+'.'+sign('RSA-SHA256',Buffer.from(data),privateKey).toString('base64url')
  }
  const identity=createServer(async (req,res)=>{
    res.setHeader('Content-Type','application/json');res.setHeader('Access-Control-Allow-Origin',frontendOrigin);const url=new URL(req.url,issuer)
    if(url.pathname==='/.well-known/openid-configuration') return res.end(JSON.stringify({issuer,authorization_endpoint:issuer+'/authorize',token_endpoint:issuer+'/token',jwks_uri:issuer+'/keys',response_types_supported:['code'],subject_types_supported:['public'],id_token_signing_alg_values_supported:['RS256']}))
    if(url.pathname==='/authorize') {
      const request=Object.fromEntries(url.searchParams);const code=randomUUID();codes.set(code,request)
      const callback=new URL(request.redirect_uri);callback.searchParams.set('code',code);callback.searchParams.set('state',request.state)
      res.statusCode=302;res.setHeader('Location',callback.href);return res.end()
    }
    if(url.pathname==='/token') {
      const chunks=[];for await(const chunk of req)chunks.push(chunk)
      const body=new URLSearchParams(Buffer.concat(chunks).toString());const request=codes.get(body.get('code'));codes.delete(body.get('code'))
      const challenge=createHash('sha256').update(body.get('code_verifier')||'').digest('base64url')
      if(!request||request.code_challenge!==challenge||request.redirect_uri!==body.get('redirect_uri')||request.client_id!==body.get('client_id')) {res.statusCode=400;return res.end(JSON.stringify({error:'invalid_grant'}))}
      return res.end(JSON.stringify({token_type:'Bearer',expires_in:300,scope:'openid profile email',access_token:signed({aud:'freshness-api',roles:loginRoles}),id_token:signed({aud:request.client_id,nonce:request.nonce})}))
    }
    if(url.pathname==='/keys')return res.end(JSON.stringify({keys:[jwk]}))
    res.statusCode=404;res.end('{}')
  })
  await new Promise(resolve=>identity.listen(0,'127.0.0.1',resolve))
  const issuer='http://127.0.0.1:'+identity.address().port
  const directory=await mkdtemp(join(tmpdir(),'freshness-http-'))
  let backend
  try {
    const env={PATH:'/usr/bin:/bin',PYTHONDONTWRITEBYTECODE:'1',OIDC_ISSUER:issuer,OIDC_AUDIENCE:'freshness-api',OIDC_JWKS_URL:issuer+'/keys',OIDC_LOCAL_HTTP:'true',DATABASE_PATH:join(directory,'state.sqlite'),FRONTEND_ORIGIN:frontendOrigin}
    backend=spawn(python,['-m','uvicorn','app.main:create_app','--factory','--host','127.0.0.1','--port','0'],{cwd:repository+'/backend',env,stdio:['ignore','ignore','pipe']})
    const origin=await new Promise((resolve,reject)=>{
      let output='';const timeout=setTimeout(()=>reject(new Error('Backend failed to become ready: '+output.slice(-1000))),10000)
      backend.stderr.on('data',chunk=>{output+=chunk;const match=output.match(/Uvicorn running on (http:\/\/127\.0\.0\.1:\d+)/);if(match){clearTimeout(timeout);resolve(match[1])}})
      backend.once('exit',code=>{clearTimeout(timeout);reject(new Error('Backend exited '+code+': '+output.slice(-1000)))})
    })
    apiOrigin=origin
    function token(roles,subject='fixture-user') {
      const now=Math.floor(Date.now()/1000),encode=value=>Buffer.from(JSON.stringify(value)).toString('base64url')
      const data=encode({alg:'RS256',kid:jwk.kid})+'.'+encode({iss:issuer,aud:'freshness-api',sub:subject,roles,iat:now,exp:now+300})
      return data+'.'+sign('RSA-SHA256',Buffer.from(data),privateKey).toString('base64url')
    }
    const storage=new InMemoryWebStorage()
    const config={issuer,clientId:'freshness-ui',redirectUri:issuer+'/callback'}
    const initialLogin=new AuthFlow(config,storage)
    const authorization=await initialLogin.begin()
    const callback=(await fetch(authorization,{redirect:'manual'})).headers.get('location')
    const afterRedirect=new AuthFlow(config,storage)
    await afterRedirect.acceptCallback(callback)
    const admin=new FeatureApi(origin+'/api',()=>afterRedirect.accessToken())
    const viewer=new FeatureApi(origin+'/api',()=>token(['viewer']))
    const operator=new FeatureApi(origin+'/api',()=>token(['operator']))
    assert.equal((await admin.me()).subject,'fixture-user')
    assert.deepEqual(await admin.policies(),[])
    const policy={expected_partitions:['us','eu'],max_age_seconds:300,expected_version:0}
    assert.equal((await admin.savePolicy('risk-features',policy)).version,1)
    assert.equal((await viewer.policies())[0].feature_set,'risk-features')
    await assert.rejects(viewer.savePolicy('forbidden',policy),e=>e.status===403)
    assert.equal((await viewer.health('risk-features')).missing_count,2)
    const evaluated=await operator.evaluate('risk-features')
    assert.equal(evaluated.incidents.length,2)
    assert.equal((await viewer.incidents('risk-features')).length,2)
    const now=new Date(Date.now()-1000).toISOString()
    const event={event_id:'job-1',partition:'us',source_watermark:now,completed_at:now,row_count:100}
    assert.equal((await operator.ingest('risk-features',event)).created,true)
    assert.equal((await operator.ingest('risk-features',event)).created,false)
    const recovery=await operator.evaluate('risk-features')
    assert.equal(recovery.evaluation.coverage,0.5)
    assert.ok(recovery.transitions.some(t=>t.action==='resolved'&&t.partition==='us'))
    const timelinePage=await viewer.timeline('risk-features',0,1)
    assert.equal(timelinePage.events.length,1);assert.ok((await viewer.timeline('risk-features',timelinePage.next_cursor)).events.length>0)
    assert.ok((await admin.audit('risk-features')).events.some(e=>e.actor==='fixture-user'))
    await assert.rejects(operator.audit('risk-features'),e=>e.status===403)
    await assert.rejects(admin.savePolicy('risk-features',policy),e=>e.status===409)
    assert.equal((await fetch(origin+'/api/features')).status,401)
    assert.equal((await fetch(origin+'/api/features',{headers:{Authorization:'Bearer invalid'}})).status,401)
    loginRoles=[browserRole]
    browser=await chromium.launch({executablePath:'/usr/bin/google-chrome',headless:true,args:['--no-sandbox','--disable-background-networking']})
    const page=await browser.newPage()
    await page.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort())
    await page.goto(frontendOrigin)
    await page.getByRole('button',{name:'Sign in',exact:true}).click()
    await page.getByText('Signed in as fixture-user',{exact:true}).waitFor()
    assert.equal(new URL(page.url()).search,'')
    assert.deepEqual(await page.evaluate(()=>({local:localStorage.length,session:sessionStorage.length})),{local:0,session:0})
    if(browserRole==='admin') {
    await page.getByRole('button',{name:'New feature set'}).click()
    const create=page.locator('section').filter({has:page.getByRole('heading',{name:'Create freshness policy'})})
    await create.getByLabel('Feature set',{exact:true}).fill('browser-feature')
    await create.getByLabel('Expected partitions').fill('us')
    await create.getByRole('button',{name:'Save policy'}).click()
    await page.getByRole('heading',{name:'Partition health: browser-feature'}).waitFor()
    assert.ok((await admin.policies()).some(p=>p.feature_set==='browser-feature'))
    await page.getByRole('button',{name:'Evaluate incidents',exact:true}).click()
    const incidents=page.locator('section').filter({has:page.getByRole('heading',{name:'Active incidents',exact:true})})
    await incidents.getByText(/us: missing/).waitFor()
    await page.getByLabel('Row count',{exact:true}).fill('42')
    await page.getByRole('button',{name:'Record materialization',exact:true}).click()
    await page.getByText('Materialization recorded. Evaluate incidents to reconcile alerts.',{exact:true}).waitFor()
    await page.getByRole('button',{name:'Evaluate incidents',exact:true}).click()
    await incidents.getByText('No active incidents.',{exact:true}).waitFor()
    assert.equal((await admin.health('browser-feature')).coverage,1)
    assert.equal((await admin.incidents('browser-feature')).length,0)
    const timeline=page.locator('section').filter({has:page.getByRole('heading',{name:'Incident timeline',exact:true})})
    await timeline.getByText(/incident resolved/).waitFor()
    const auditSection=page.locator('section').filter({has:page.getByRole('heading',{name:'Audit log',exact:true})})
    assert.ok((await auditSection.textContent()).includes('fixture-user'))
    } else {
      await page.getByRole('heading',{name:'Partition health: risk-features'}).waitFor()
      assert.equal(await page.getByRole('button',{name:'New feature set'}).count(),browserRole==='operator'?1:0)
      assert.equal(await page.getByRole('heading',{name:'Audit log',exact:true}).count(),0)
      assert.equal(await page.getByRole('button',{name:'Save policy'}).count(),browserRole==='operator'?1:0)
      if(browserRole==='viewer') {
        assert.equal(await page.getByRole('button',{name:'Evaluate incidents',exact:true}).count(),0)
        assert.equal(await page.getByRole('button',{name:'Record materialization',exact:true}).count(),0)
      } else {
        await page.getByLabel('Maximum source age (seconds)',{exact:true}).fill('600')
        await page.getByRole('button',{name:'Save policy',exact:true}).click()
        await page.getByRole('button',{name:'Save policy',exact:true}).waitFor()
        const updated=(await admin.policies()).find(p=>p.feature_set==='risk-features')
        assert.equal(updated.version,2);assert.equal(updated.max_age_seconds,600)
        await page.getByLabel('Partition',{exact:true}).selectOption('eu')
        await page.getByLabel('Row count',{exact:true}).fill('12')
        await page.getByRole('button',{name:'Record materialization',exact:true}).click()
        await page.getByText('Materialization recorded. Evaluate incidents to reconcile alerts.',{exact:true}).waitFor()
        await page.getByRole('button',{name:'Evaluate incidents',exact:true}).click()
        const active=page.locator('section').filter({has:page.getByRole('heading',{name:'Active incidents',exact:true})})
        await active.getByText('No active incidents.',{exact:true}).waitFor()
        assert.equal((await admin.health('risk-features')).coverage,1)
      }
    }
    if(browserRole!=='viewer') {
      await page.getByRole('button',{name:'Explain freshness',exact:true}).click()
      await page.getByText('AI advice is disabled. Source-watermark health and incident tracking remain available.',{exact:true}).waitFor()
    } else assert.equal(await page.getByRole('button',{name:'Explain freshness',exact:true}).count(),0)
    assert.equal(await page.getByRole('alert').count(),0)
    await page.getByRole('button',{name:'Sign out',exact:true}).click()
    await page.getByRole('button',{name:'Sign in',exact:true}).waitFor()
    assert.equal(await page.getByRole('heading',{name:'Partition health: browser-feature'}).count(),0)
    await page.reload();await page.getByRole('button',{name:'Sign in',exact:true}).waitFor()
    afterRedirect.clear();await assert.rejects(admin.me(),e=>e.status===401)
  } finally {
    if(browser)await browser.close()
    await new Promise(resolve=>frontend.close(resolve))
    if(backend && backend.exitCode===null){backend.kill('SIGTERM');await once(backend,'exit')}
    await new Promise(resolve=>identity.close(resolve));await rm(directory,{recursive:true,force:true})
  }
})

}
