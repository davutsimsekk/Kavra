import unittest

from studio_web import access


class AllowedHostsTests(unittest.TestCase):
    def test_defaults_are_local_only(self):
        self.assertEqual(access.allowed_hosts({}), ["127.0.0.1", "localhost"])
        self.assertEqual(access.allowed_origins({}), set(access.LOCAL_ORIGINS))

    def test_extra_hosts_are_appended_and_normalized(self):
        env = {"KAVRA_ALLOWED_HOSTS": " Kavra.Tailnet.TS.net , https://sunucu.ts.net:443/ ,localhost"}
        self.assertEqual(
            access.allowed_hosts(env),
            ["127.0.0.1", "localhost", "kavra.tailnet.ts.net", "sunucu.ts.net"],
        )

    def test_origins_are_derived_from_extra_hosts_when_not_given(self):
        origins = access.allowed_origins({"KAVRA_ALLOWED_HOSTS": "kavra.ts.net"})
        self.assertIn("https://kavra.ts.net", origins)
        self.assertIn("http://kavra.ts.net", origins)
        self.assertTrue(set(access.LOCAL_ORIGINS) <= origins)

    def test_explicit_origins_replace_derivation(self):
        env = {
            "KAVRA_ALLOWED_HOSTS": "kavra.ts.net",
            "KAVRA_ALLOWED_ORIGINS": "https://kavra.ts.net/, http://100.64.0.7:8768",
        }
        origins = access.allowed_origins(env)
        self.assertIn("https://kavra.ts.net", origins)
        self.assertIn("http://100.64.0.7:8768", origins)
        self.assertNotIn("http://kavra.ts.net", origins)

    def test_published_docker_port_origin_is_accepted(self):
        origins = access.allowed_origins({"KAVRA_PUBLISHED_PORT": "8877"})
        self.assertIn("http://127.0.0.1:8877", origins)
        self.assertIn("http://localhost:8877", origins)
        for bad in ("abc", "0", "70000", ""):
            with self.subTest(bad=bad):
                self.assertEqual(access.allowed_origins({"KAVRA_PUBLISHED_PORT": bad}), set(access.LOCAL_ORIGINS))

    def test_wildcards_are_rejected(self):
        with self.assertRaises(access.AccessConfigError):
            access.allowed_hosts({"KAVRA_ALLOWED_HOSTS": "*"})
        with self.assertRaises(access.AccessConfigError):
            access.allowed_hosts({"KAVRA_ALLOWED_HOSTS": "*.ts.net"})
        with self.assertRaises(access.AccessConfigError):
            access.allowed_origins({"KAVRA_ALLOWED_ORIGINS": "https://*.ts.net"})

    def test_malformed_origins_are_rejected(self):
        for bad in ("kavra.ts.net", "ftp://kavra.ts.net", "https://kavra.ts.net/app", "https://"):
            with self.subTest(bad=bad), self.assertRaises(access.AccessConfigError):
                access.allowed_origins({"KAVRA_ALLOWED_ORIGINS": bad})


class AccessTokenTests(unittest.TestCase):
    TOKEN = "dogru-sifre-1234567890"

    def test_token_is_optional_but_must_be_long(self):
        self.assertIsNone(access.access_token({}))
        self.assertIsNone(access.access_token({"KAVRA_ACCESS_TOKEN": "  "}))
        self.assertEqual(access.access_token({"KAVRA_ACCESS_TOKEN": f" {self.TOKEN} "}), self.TOKEN)
        with self.assertRaises(access.AccessConfigError):
            access.access_token({"KAVRA_ACCESS_TOKEN": "kisa"})

    def _client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from studio_web.access_gate import install_access_gate

        app = FastAPI()

        @app.get("/api/ping")
        def ping():
            return {"ok": True}

        @app.get("/")
        def index():
            return {"page": "index"}

        install_access_gate(app, self.TOKEN)
        return TestClient(app)

    def test_gate_blocks_requests_without_token(self):
        client = self._client()
        self.assertEqual(client.get("/api/ping").status_code, 401)
        page = client.get("/")
        self.assertEqual(page.status_code, 401)
        self.assertIn('name="key"', page.text)
        self.assertEqual(client.get("/?key=yanlis-sifre-000000").status_code, 401)
        self.assertEqual(client.get("/api/ping", headers={"Authorization": "Bearer yanlis"}).status_code, 401)
        self.assertEqual(client.get("/api/ping", cookies={access.ACCESS_COOKIE: self.TOKEN}).status_code, 401)

    def test_key_link_sets_session_cookie_and_strips_token_from_url(self):
        client = self._client()
        response = client.get(f"/?key={self.TOKEN}&tab=video", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/?tab=video")
        self.assertNotIn(self.TOKEN, response.headers["set-cookie"])
        self.assertIn("httponly", response.headers["set-cookie"].lower())
        self.assertEqual(client.get("/api/ping").json(), {"ok": True})

    def test_bearer_header_is_accepted_for_scripts(self):
        client = self._client()
        response = client.get("/api/ping", headers={"Authorization": f"Bearer {self.TOKEN}"})
        self.assertEqual(response.json(), {"ok": True})


if __name__ == "__main__":
    unittest.main()
