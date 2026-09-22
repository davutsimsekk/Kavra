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


if __name__ == "__main__":
    unittest.main()
