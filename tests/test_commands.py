"""commands 模块测试:api 透传的结果归类、脱敏与错误提示。"""

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from panel_cli import commands, redact
from panel_cli.errors import CliError

PEM_KEY = "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----"


class FakeResponse:
    def __init__(self, status=200, body=b"", content_type="application/json"):
        self.status = status
        self._body = body
        self.headers = [("Content-Type", content_type)]

    def text(self):
        return self._body.decode("utf-8", "replace")

    @property
    def ok(self):
        return 200 <= self.status < 300


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, path):
        self.calls.append(("GET", path, None))
        return self.response

    def post_json(self, path, body):
        self.calls.append(("POST", path, body))
        return self.response


class TestApiCall(unittest.TestCase):
    def setUp(self):
        redact.configure(False)
        self.addCleanup(redact.configure, False)

    def call(self, method, path, response, body=None, body_file=None):
        session = mock.Mock(panel_url="http://127.0.0.1:1", cookies=[], insecure=False)
        client = FakeClient(response)
        patches = (
            mock.patch.object(commands, "load_session", return_value=session),
            mock.patch.object(commands, "PanelClient", return_value=client),
        )
        with patches[0], patches[1]:
            commands.api_call("default", method, path, body, body_file)
        return client

    def test_404_raises_with_route_hint(self):
        response = FakeResponse(404, b"404 page not found", "text/plain")
        with self.assertRaises(CliError) as ctx:
            self.call("GET", "does/not/exist", response)
        self.assertIn("未注册", str(ctx.exception))

    def test_html_response_raises(self):
        response = FakeResponse(200, b"<html></html>", "text/html; charset=utf-8")
        with self.assertRaises(CliError) as ctx:
            self.call("POST", "core/auth/current", response)
        self.assertIn("HTML", str(ctx.exception))

    def test_json_response_is_printed(self):
        body = json.dumps({"code": 200, "data": None}).encode()
        with mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            self.call("GET", "core/auth/current", FakeResponse(200, body))
        self.assertIn('"code": 200', stdout.getvalue())

    def test_http_error_prints_body_then_raises(self):
        response = FakeResponse(500, b"boom", "text/plain")
        with mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            with self.assertRaises(CliError):
                self.call("GET", "core/auth/current", response)
        self.assertIn("boom", stdout.getvalue())

    def test_private_key_is_redacted_in_json_output(self):
        payload = {
            "code": 200,
            "data": {"items": [{"primaryDomain": "a.com", "privateKey": PEM_KEY}]},
        }
        response = FakeResponse(200, json.dumps(payload).encode())
        with mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            self.call("GET", "websites/ssl/search", response)
        printed = stdout.getvalue()
        self.assertIn(redact.REDACTED, printed)
        self.assertNotIn("BEGIN RSA PRIVATE KEY", printed)
        self.assertIn("a.com", printed)

    def test_private_key_is_redacted_in_plain_text_output(self):
        response = FakeResponse(200, PEM_KEY.encode(), "text/plain")
        with mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            self.call("GET", "files/content", response)
        printed = stdout.getvalue()
        self.assertNotIn("BEGIN RSA PRIVATE KEY", printed)
        self.assertEqual(redact.REDACTED_PEM, printed.strip())

    def test_show_secrets_prints_raw(self):
        redact.configure(True)
        payload = {"code": 200, "data": {"privateKey": "raw-secret"}}
        response = FakeResponse(200, json.dumps(payload).encode())
        with mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            self.call("GET", "websites/ssl/search", response)
        self.assertIn("raw-secret", stdout.getvalue())

    def test_body_file_is_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "body.json"
            path.write_text('{"page": 1}', encoding="utf-8")
            client = self.call(
                "POST", "apps/search", FakeResponse(200, b'{"code":200}'),
                body_file=str(path),
            )
        self.assertEqual(("POST", "api/v2/apps/search", {"page": 1}), client.calls[0])

    def test_body_file_dash_reads_stdin(self):
        with mock.patch("sys.stdin", io.StringIO('{"page": 2}')):
            client = self.call(
                "POST", "apps/search", FakeResponse(200, b'{"code":200}'),
                body_file="-",
            )
        self.assertEqual(("POST", "api/v2/apps/search", {"page": 2}), client.calls[0])

    def test_body_and_body_file_conflict(self):
        with self.assertRaises(CliError) as ctx:
            self.call(
                "POST", "apps/search", FakeResponse(200, b'{"code":200}'),
                body="{}", body_file="/tmp/whatever.json",
            )
        self.assertIn("二选一", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
