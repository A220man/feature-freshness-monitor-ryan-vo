import { OidcClient, WebStorageStateStore } from 'oidc-client-ts'
import { createRemoteJWKSet, jwtVerify } from 'jose'

export interface AuthConfig {
  issuer: string
  clientId: string
  redirectUri: string
}

/** Authorization-code + PKCE. Only temporary request state survives navigation. */
export class AuthFlow {
  private config: AuthConfig
  private client: OidcClient
  private token: string | null = null
  private expiresAt = 0
  private signingKeys?: ReturnType<typeof createRemoteJWKSet>

  constructor(config: AuthConfig, storage: Storage) {
    this.config = config
    for (const value of [config.issuer, config.redirectUri]) {
      const url = new URL(value)
      if (url.protocol !== 'https:' && !(url.protocol === 'http:' && ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname))) {
        throw new Error('SSO requires HTTPS except on loopback development hosts')
      }
    }
    this.client = new OidcClient({
      authority: config.issuer, client_id: config.clientId,
      redirect_uri: config.redirectUri, response_type: 'code', scope: 'openid profile email',
      loadUserInfo: false, disablePKCE: false, staleStateAgeInSeconds: 300,
      stateStore: new WebStorageStateStore({ store: storage, prefix: 'freshness.oidc.' }),
    })
  }

  async begin(): Promise<string> {
    await this.client.clearStaleState()
    const request = await this.client.createSigninRequest({ request_type: 'si:r', nonce: crypto.randomUUID() })
    return request.url
  }

  async acceptCallback(value: string): Promise<void> {
    this.clear()
    const actual = new URL(value)
    const expected = new URL(this.config.redirectUri)
    if (actual.origin !== expected.origin || actual.pathname !== expected.pathname || actual.hash) {
      throw new Error('Unexpected SSO callback URL')
    }
    const response = await this.client.processSigninResponse(value)
    if (!response.access_token || response.token_type.toLowerCase() !== 'bearer' || !response.expires_at || response.expires_at <= Date.now() / 1000) {
      throw new Error('Identity provider returned no usable access token')
    }
    if (!response.id_token || await this.client.metadataService.getIssuer() !== this.config.issuer) {
      throw new Error('Identity provider issuer or ID token is missing or inconsistent')
    }
    if (!this.signingKeys) {
      const endpoint = new URL(await this.client.metadataService.getKeysEndpoint(false))
      if (endpoint.protocol !== 'https:' && !(endpoint.protocol === 'http:' && ['localhost', '127.0.0.1', '[::1]'].includes(endpoint.hostname))) {
        throw new Error('Identity provider signing keys require HTTPS')
      }
      if (endpoint.username || endpoint.password) throw new Error('Unexpected credentials in signing key URL')
      this.signingKeys = createRemoteJWKSet(endpoint, { timeoutDuration: 5000 })
    }
    const { payload } = await jwtVerify(response.id_token, this.signingKeys, {
      issuer: this.config.issuer, audience: this.config.clientId,
      algorithms: ['RS256', 'ES256'], requiredClaims: ['exp', 'iat', 'sub', 'iss', 'aud'],
      clockTolerance: 10, maxTokenAge: 300,
    })
    if ((payload.azp !== undefined && payload.azp !== this.config.clientId) ||
        (Array.isArray(payload.aud) && payload.aud.length > 1 && payload.azp !== this.config.clientId)) {
      throw new Error('Identity token authorized party does not match this client')
    }
    this.token = response.access_token
    this.expiresAt = response.expires_at
  }

  accessToken(): string | null {
    if (this.expiresAt <= Date.now() / 1000) this.clear()
    return this.token
  }

  clear(): void {
    this.token = null
    this.expiresAt = 0
  }
}
