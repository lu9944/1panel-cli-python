"""commands 模块测试:api 透传的结果归类与错误提示。"""

import io
import json
import unittest
from unittest import mock

from panel_cli import commands
from panel_cli.errors import CliError


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

    def get(self, path):
        return self.response

    def post_json(self, path, body):
        return self.response


class TestApiCall(unittest.TestCase):
    def call(self, method, path, response):
        session = mock.Mock(panel_url="http://127.0.0.1:1", cookies=[], insecure=False)
        patches = (
            mock.patch.object(commands, "load_session", return_value=session),
            mock.patch.object(commands, "PanelClient", return_value=FakeClient(response)),
        )
        with patches[0], patches[1]:
            return commands.api_call("default", method, path, None)

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


if __name__ == "__main__":
    unittest.main()
