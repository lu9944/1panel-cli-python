"""client 模块测试:默认请求头、SSL 上下文与 Cookie 处理。"""

import ssl
import unittest

from panel_cli.client import PanelClient, _build_ssl_context


class TestSslContext(unittest.TestCase):
    def test_insecure_context(self):
        context = _build_ssl_context(True)
        self.assertEqual(context.verify_mode, ssl.CERT_NONE)
        self.assertFalse(context.check_hostname)

    def test_secure_context(self):
        context = _build_ssl_context(False)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)


class TestDefaultHeaders(unittest.TestCase):
    def setUp(self):
        import os

        self._saved = {k: os.environ.pop(k, None) for k in ("PANEL_LANGUAGE", "PANEL_NODE")}

    def tearDown(self):
        import os

        for key, value in self._saved.items():
            if value is not None:
                os.environ[key] = value

    def test_headers_default(self):
        client = PanelClient("http://10.0.0.1:1000", None, False)
        headers = client._default_headers()
        self.assertEqual(headers["Accept-Language"], "zh")
        self.assertEqual(headers["CurrentNode"], "local")
        self.assertEqual(headers["User-Agent"], "1panel-cli")

    def test_headers_node_encoded(self):
        import os
        from urllib.parse import unquote_plus

        os.environ["PANEL_NODE"] = "node-1"
        os.environ["PANEL_LANGUAGE"] = "en"
        client = PanelClient("http://10.0.0.1:1000", None, False)
        headers = client._default_headers()
        self.assertEqual(headers["Accept-Language"], "en")
        self.assertEqual(unquote_plus(headers["CurrentNode"]), "node-1")


class TestCookies(unittest.TestCase):
    def test_cookie_roundtrip(self):
        from panel_cli.session import CookieData

        client = PanelClient(
            "http://10.0.0.1:1000",
            [CookieData("psession", "abc"), CookieData("SecurityEntrance", "def")],
            False,
        )
        pairs = {c.name: c.value for c in client.cookies()}
        self.assertEqual(pairs.get("psession"), "abc")
        self.assertEqual(pairs.get("SecurityEntrance"), "def")
        self.assertEqual(client.cookie("psession"), "abc")
        self.assertIsNone(client.cookie("missing"))


if __name__ == "__main__":
    unittest.main()
