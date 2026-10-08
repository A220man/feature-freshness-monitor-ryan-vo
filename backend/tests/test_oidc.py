"""Real signature verification with ephemeral keys; no external IdP needed."""
import time
from types import SimpleNamespace
import unittest
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient
from app.security.oidc import OIDCAuthenticator, OIDCSettings


class OIDCTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def setUp(self):
        key = self.key.public_key()
        self.auth = OIDCAuthenticator(OIDCSettings("https://id.example/realm", "freshness-api", "https://id.example/keys"),
                                      SimpleNamespace(get_signing_key_from_jwt=lambda token: SimpleNamespace(key=key)))
        app = FastAPI()
        @app.get("/identity")
        def identity(principal=Depends(self.auth)):
            return {"subject": principal.subject, "roles": sorted(principal.roles)}
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def token(self, **updates):
        now = int(time.time())
        claims = dict(sub="alice", iss="https://id.example/realm", aud="freshness-api", iat=now, exp=now+300, roles=["viewer"])
        claims.update(updates)
        return jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": "ephemeral-test"})

    def request(self, token):
        return self.client.get("/identity", headers={"Authorization": "Bearer "+token})

    def test_valid_signature_and_roles(self):
        response = self.request(self.token(realm_access={"roles": ["operator", "unrelated-role"]}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"subject": "alice", "roles": ["operator", "viewer"]})

    def test_expired_wrong_issuer_and_wrong_audience_rejected(self):
        for update in ({"exp": int(time.time())-60}, {"iss": "https://attacker.example"}, {"aud": "other-api"}, {"iat": int(time.time())+120}, {"sub": ""}, {"roles": "admin"}):
            with self.subTest(update=update):
                self.assertEqual(self.request(self.token(**update)).status_code, 401)

    def test_wrong_signing_key_rejected(self):
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        payload = jwt.decode(self.token(), options={"verify_signature": False})
        self.assertEqual(self.request(jwt.encode(payload, other, algorithm="RS256")).status_code, 401)

    def test_missing_required_expiry_rejected(self):
        payload = jwt.decode(self.token(), options={"verify_signature": False});payload.pop("exp")
        self.assertEqual(self.request(jwt.encode(payload, self.key, algorithm="RS256")).status_code, 401)

    def test_unsigned_tokens_and_test_headers_rejected(self):
        self.assertEqual(self.request(jwt.encode({"sub": "admin"}, key="", algorithm="none")).status_code, 401)
        self.assertEqual(self.client.get("/identity", headers={"X-Test-Principal": "admin"}).status_code, 401)
        self.assertEqual(self.client.get("/identity", headers={"Cookie": "session=admin"}).status_code, 401)

    def test_identity_provider_outage_is_retryable(self):
        def unavailable(token):
            raise jwt.PyJWKClientConnectionError("network unreachable")
        self.auth.keys = SimpleNamespace(get_signing_key_from_jwt=unavailable)
        response = self.request(self.token())
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers["Retry-After"], "30")

    def test_http_only_allowed_for_explicit_loopback_development(self):
        with self.assertRaises(ValueError):
            OIDCSettings("http://id.example", "api", "http://id.example/keys", True)
        with self.assertRaises(ValueError):
            OIDCSettings("http://localhost", "api", "http://localhost/keys")
        OIDCSettings("http://localhost", "api", "http://localhost/keys", True)
