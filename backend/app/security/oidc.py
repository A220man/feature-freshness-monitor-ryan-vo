"""Bearer-only OIDC resource server; never authenticates cookies or test headers.

Pair with authorization-code + PKCE in the frontend. SAML organizations can
use an identity broker that issues OIDC access tokens for this API audience.
The JWKS URL is trusted configuration, never taken from token headers.
"""
from dataclasses import dataclass
from urllib.parse import urlsplit
import jwt
from fastapi import HTTPException, Request
from app.api.features import Principal


@dataclass(frozen=True)
class OIDCSettings:
    issuer: str
    audience: str
    jwks_url: str
    allow_local_http: bool = False

    def __post_init__(self):
        if not self.audience.strip():
            raise ValueError("OIDC audience is required")
        for url in (self.issuer, self.jwks_url):
            parsed = urlsplit(url)
            local_http = self.allow_local_http and parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            if not parsed.hostname or parsed.username or parsed.password or parsed.fragment or (parsed.scheme != "https" and not local_http):
                raise ValueError("OIDC URLs require HTTPS; explicit local HTTP is development-only")


class OIDCAuthenticator:
    def __init__(self, settings: OIDCSettings, key_client=None):
        self.settings = settings
        self.keys = key_client if key_client is not None else jwt.PyJWKClient(settings.jwks_url, cache_jwk_set=True, lifespan=300, timeout=5)

    def __call__(self, request: Request) -> Principal:
        header = request.headers.get("Authorization", "")
        scheme, separator, token = header.partition(" ")
        if scheme.lower() != "bearer" or not separator or not token or len(token) > 16384 or any(c.isspace() for c in token):
            raise HTTPException(401, "A bearer access token is required", headers={"WWW-Authenticate": "Bearer"})
        try:
            untrusted = jwt.get_unverified_header(token)
            if untrusted.get("alg") not in {"RS256", "ES256"}:
                raise jwt.InvalidAlgorithmError("Unsupported signing algorithm")
            key = self.keys.get_signing_key_from_jwt(token).key
            claims = jwt.decode(token, key, algorithms=["RS256", "ES256"], audience=self.settings.audience,
                                issuer=self.settings.issuer, leeway=10,
                                options={"require": ["exp", "iat", "iss", "aud", "sub"]})
            subject = claims["sub"]
            if not isinstance(subject, str) or not subject.strip():
                raise jwt.InvalidTokenError("Invalid subject")
            roles = claims.get("roles", [])
            realm = claims.get("realm_access", {})
            realm_roles = realm.get("roles", []) if isinstance(realm, dict) else []
            if not isinstance(roles, list) or not isinstance(realm_roles, list) or any(not isinstance(r, str) for r in roles + realm_roles):
                raise jwt.InvalidTokenError("Invalid role claims")
            return Principal(subject, frozenset(roles + realm_roles) & {"viewer", "operator", "admin"})
        except jwt.PyJWKClientConnectionError as error:
            raise HTTPException(503, "Identity provider temporarily unavailable", headers={"Retry-After": "30"}) from error
        except (jwt.PyJWTError, ValueError, TypeError) as error:
            raise HTTPException(401, "Access token is invalid or expired", headers={"WWW-Authenticate": "Bearer error=invalid_token"}) from error
