"""catalog / config 模块测试。"""

import unittest

from panel_cli import catalog
from panel_cli.catalog import Catalog, Route, builtin, merged_routes, normalize_path
from panel_cli.commands import normalize_api_path
from panel_cli.config import PanelConfig, parse_env_file, split_url


class TestBuiltinCatalog(unittest.TestCase):
    def test_valid_and_nonempty(self):
        catalog_data = builtin()
        self.assertGreater(catalog_data.route_count, 300)
        self.assertEqual(catalog_data.route_count, len(catalog_data.routes))


class TestNormalizePath(unittest.TestCase):
    def test_normalizes_api_paths(self):
        self.assertEqual(normalize_path("core/auth/current"), "/api/v2/core/auth/current")
        self.assertEqual(normalize_path("/api/v2/apps/search"), "/api/v2/apps/search")
        self.assertEqual(normalize_path("api/v2"), "/api/v2")

    def test_normalize_api_path_commands(self):
        self.assertEqual(normalize_api_path("core/auth/current"), "api/v2/core/auth/current")
        self.assertEqual(normalize_api_path("/api/v2/apps/search"), "api/v2/apps/search")

    def test_normalize_full_urls(self):
        self.assertEqual(
            normalize_path("http://10.0.0.1:8080/api/v2/apps/search"),
            "/api/v2/apps/search",
        )
        self.assertEqual(
            normalize_path("https://panel.example.com/core/auth/current"),
            "/api/v2/core/auth/current",
        )
        self.assertEqual(normalize_path("http://panel.example.com/api/v2"), "/api/v2")


class TestMergeRoutes(unittest.TestCase):
    def test_merges_runtime_only_swagger_routes(self):
        catalog_data = builtin()
        swagger = {
            "paths": {
                "/future/feature": {
                    "post": {"summary": "Future feature", "tags": ["Future"]}
                }
            }
        }
        routes = merged_routes(catalog_data, swagger)
        self.assertEqual(len(routes), catalog_data.route_count + 1)
        self.assertTrue(any(r.path == "/api/v2/future/feature" for r in routes))

    def test_no_swagger(self):
        catalog_data = builtin()
        routes = merged_routes(catalog_data, None)
        self.assertEqual(len(routes), catalog_data.route_count)

    def test_enriches_existing_route(self):
        catalog_data = builtin()
        route = catalog_data.routes[0]
        swagger = {
            "paths": {
                route.path.removeprefix("/api/v2"): {
                    route.method.lower(): {"summary": "运行时摘要", "tags": ["X"]}
                }
            }
        }
        routes = merged_routes(catalog_data, swagger)
        matched = next(
            r for r in routes if r.path == route.path and r.method == route.method
        )
        if not route.summary:
            self.assertEqual(matched.summary, "运行时摘要")
        self.assertTrue(matched.swagger_documented)


class TestRoute(unittest.TestCase):
    def test_route_text_lowercase(self):
        route = Route("POST", "/api/v2/apps", "CreateApp", "src", summary="Hi", tags=["App"])
        self.assertEqual(route_text_value(route), "post /api/v2/apps createapp hi app")


def route_text_value(route: Route) -> str:
    return catalog.route_text(route)


class TestSplitUrl(unittest.TestCase):
    def test_with_path(self):
        base, entrance = split_url("http://10.0.0.1:41394/abcdef1234")
        self.assertEqual(base, "http://10.0.0.1:41394")
        self.assertEqual(entrance, "abcdef1234")

    def test_without_path(self):
        base, entrance = split_url("https://panel.example.com/")
        self.assertEqual(base, "https://panel.example.com")
        self.assertEqual(entrance, "")

    def test_adds_scheme(self):
        base, entrance = split_url("10.0.0.1:41394")
        self.assertEqual(base, "http://10.0.0.1:41394")
        self.assertEqual(entrance, "")

    def test_empty_raises(self):
        from panel_cli.errors import CliError

        with self.assertRaises(CliError):
            split_url("  ")


class TestParseEnvFile(unittest.TestCase):
    def test_parse(self):
        import tempfile
        import os

        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as handle:
            handle.write(
                "# comment\n"
                "PANEL_URL=http://1.2.3.4:1000\n"
                "PANEL_USERNAME = admin\n"
                "PANEL_PASSWORD='my pass'\n"
                'PANEL_ENTRANCE="ent"\n'
                "export PANEL_LANGUAGE=zh\n"
                "EMPTY=\n"
            )
            path = handle.name
        try:
            values = parse_env_file(__import__("pathlib").Path(path))
            self.assertEqual(values["PANEL_URL"], "http://1.2.3.4:1000")
            self.assertEqual(values["PANEL_USERNAME"], "admin")
            self.assertEqual(values["PANEL_PASSWORD"], "my pass")
            self.assertEqual(values["PANEL_ENTRANCE"], "ent")
            self.assertEqual(values["PANEL_LANGUAGE"], "zh")
        finally:
            os.unlink(path)


class TestPanelConfig(unittest.TestCase):
    def setUp(self):
        import os

        self._saved = {}
        for key in (
            "PANEL_URL", "PANEL_ENTRANCE", "PANEL_USERNAME", "PANEL_PASSWORD",
            "PANEL_LANGUAGE", "PANEL_INSECURE", "PANEL_MFA_CODE",
            "PANEL_CAPTCHA", "PANEL_CAPTCHA_ID",
        ):
            self._saved[key] = os.environ.pop(key, None)

    def tearDown(self):
        import os

        for key, value in self._saved.items():
            if value is not None:
                os.environ[key] = value

    def test_from_env(self):
        import os

        os.environ["PANEL_URL"] = "http://1.2.3.4:1000/ent123"
        os.environ["PANEL_USERNAME"] = "admin"
        os.environ["PANEL_PASSWORD"] = "secret"
        cfg = PanelConfig.from_env()
        self.assertEqual(cfg.url, "http://1.2.3.4:1000")
        self.assertEqual(cfg.entrance, "ent123")
        self.assertEqual(cfg.username, "admin")
        self.assertEqual(cfg.language, "zh")
        self.assertFalse(cfg.insecure)

    def test_missing_url_raises(self):
        from panel_cli.errors import CliError

        with self.assertRaises(CliError):
            PanelConfig.from_env()


if __name__ == "__main__":
    unittest.main()
