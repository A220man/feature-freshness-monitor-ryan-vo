import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import Settings, create_app
from app.security.oidc import OIDCSettings


class ApplicationTests(TestCase):
    def test_real_application_refuses_fake_identity_and_serves_health(self):
        with TemporaryDirectory() as directory:
            settings = Settings(OIDCSettings("https://id.example", "api", "https://id.example/keys"), Path(directory)/"state/db.sqlite")
            with TestClient(create_app(settings)) as client:
                self.assertEqual(client.get("/healthz").json(), {"status": "ok"})
                for path in ("/api/features", "/api/me"):
                    self.assertEqual(client.get(path, headers={"X-Test-Principal": "admin"}).status_code, 401)

    def test_missing_identity_configuration_fails_closed(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "Configure OIDC"):
                create_app()
