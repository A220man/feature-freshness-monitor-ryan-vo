import { createServer } from 'node:http'
import { createHash, randomUUID, generateKeyPairSync, sign } from 'node:crypto'
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { InMemoryWebStorage } from 'oidc-client-ts'
import { AuthFlow } from '../src/auth-flow.ts'

async function fixture(run, claims = {}, corruptSignature = false) {
  const {privateKey,publicKey}=generateKeyPairSync('rsa',{modulusLength:2048})
  const jwk={...publicKey.export({format:'jwk'}),kid:'test-key',alg:'RS256',use:'sig'}
  const codes = new Map()
  let issuer, exchanges = 0
  const server = createServer(async (req, res) => {
    const url = new URL(req.url, issuer)
    res.setHeader('Content-Type', 'application/json')
    if (url.pathname === '/.well-known/openid-configuration') {
      return res.end(JSON.stringify({ issuer, authorization_endpoint: issuer+'/authorize', token_endpoint: issuer+'/token', jwks_uri: issuer+'/keys', response_types_supported: ['code'], subject_types_supported: ['public'], id_token_signing_alg_values_supported: ['RS256'] }))
    }
    if (url.pathname === '/keys') return res.end(JSON.stringify({keys:[jwk]}))
    if (url.pathname === '/authorize') {
      const code=randomUUID(); codes.set(code,Object.fromEntries(url.searchParams))
      const callback=new URL(url.searchParams.get('redirect_uri')); callback.searchParams.set('code',code); callback.searchParams.set('state',url.searchParams.get('state'))
      res.statusCode=302;res.setHeader('Location',callback.href);return res.end()
    }
    if (url.pathname === '/token') {
      exchanges++
      const chunks=[];for await (const chunk of req) chunks.push(chunk)
      const body=new URLSearchParams(Buffer.concat(chunks).toString())
      const request=codes.get(body.get('code'));codes.delete(body.get('code'))
      const challenge=createHash('sha256').update(body.get('code_verifier') || '').digest('base64url')
      if (!request || challenge!==request.code_challenge || body.get('redirect_uri')!==request.redirect_uri || body.get('client_id')!==request.client_id) {
        res.statusCode=400;return res.end(JSON.stringify({error:'invalid_grant'}))
      }
      const now=Math.floor(Date.now()/1000)
      const encode=v=>Buffer.from(JSON.stringify(v)).toString('base64url')
      const data=encode({alg:'RS256',kid:jwk.kid})+'.'+encode({iss:issuer,aud:request.client_id,sub:'fixture-user',exp:now+300,iat:now,nonce:request.nonce,...claims})
      const signature=sign('RSA-SHA256',Buffer.from(data),privateKey)
      if(corruptSignature) signature[0]^=1
      const idToken=data+'.'+signature.toString('base64url')
      return res.end(JSON.stringify({ access_token: 'ephemeral-'+randomUUID(), token_type:'Bearer', expires_in:300, id_token:idToken, scope:'openid profile email' }))
    }
    res.statusCode=404;res.end('{}')
  })
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));issuer='http://127.0.0.1:'+server.address().port
  const storage=new InMemoryWebStorage();const config={issuer,clientId:'freshness-ui',redirectUri:issuer+'/callback'}
  try {await run({storage,config,get exchanges(){return exchanges}})} finally {await new Promise(resolve=>server.close(resolve))}
}

async function callback(flow) {
  const url=await flow.begin(); const parsed=new URL(url)
  assert.equal(parsed.searchParams.get('code_challenge_method'),'S256')
  assert.ok(parsed.searchParams.get('nonce'))
  return (await fetch(url,{redirect:'manual'})).headers.get('location')
}

test('redirect reload preserves PKCE state and exchanges code, tokens remain memory-only',()=>fixture(async f=>{
  const first=new AuthFlow(f.config,f.storage);const url=await callback(first)
  const reloaded=new AuthFlow(f.config,f.storage);await reloaded.acceptCallback(url)
  assert.ok(reloaded.accessToken());assert.equal(f.exchanges,1)
  for(let i=0;i<f.storage.length;i++) assert.ok(!f.storage.getItem(f.storage.key(i)).includes(reloaded.accessToken()))
  assert.equal(new AuthFlow(f.config,f.storage).accessToken(),null)
  reloaded.clear();assert.equal(reloaded.accessToken(),null)
}))
test('unknown state rejected before token exchange',()=>fixture(async f=>{
  const flow=new AuthFlow(f.config,f.storage);const url=new URL(await callback(flow));url.searchParams.set('state','wrong')
  await assert.rejects(flow.acceptCallback(url.href));assert.equal(f.exchanges,0);assert.equal(flow.accessToken(),null)
}))
test('corrupted PKCE verifier rejected by token endpoint',()=>fixture(async f=>{
  const flow=new AuthFlow(f.config,f.storage);const url=await callback(flow)
  const key=f.storage.key(0);const state=JSON.parse(f.storage.getItem(key));state.code_verifier='incorrect-verifier';f.storage.setItem(key,JSON.stringify(state))
  await assert.rejects(flow.acceptCallback(url));assert.equal(flow.accessToken(),null)
}))
test('callback replay rejected and clears stale identity',()=>fixture(async f=>{
  const flow=new AuthFlow(f.config,f.storage);const url=await callback(flow);await flow.acceptCallback(url)
  await assert.rejects(flow.acceptCallback(url));assert.equal(flow.accessToken(),null);assert.equal(f.exchanges,1)
}))

for (const [name, claims] of [
  ['nonce mismatch', {nonce:'unrelated-request'}],
  ['issuer mismatch', {iss:'https://unrelated.example.test'}],
  ['audience mismatch', {aud:'another-client'}],
  ['expired token', {exp:1}],
  ['authorized party mismatch', {azp:'another-client'}],
  ['multiple audiences without authorized party', {aud:['freshness-ui','other']}],
]) {
  test(name+' rejects callback without retaining a token',()=>fixture(async f=>{
    const flow=new AuthFlow(f.config,f.storage);const url=await callback(flow)
    await assert.rejects(flow.acceptCallback(url));assert.equal(flow.accessToken(),null)
  },claims))
}

test('invalid ID token signature rejects callback without retaining a token',()=>fixture(async f=>{
  const flow=new AuthFlow(f.config,f.storage);const url=await callback(flow)
  await assert.rejects(flow.acceptCallback(url));assert.equal(flow.accessToken(),null)
},{},true))
