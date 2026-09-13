"""web 模块测试:SSL 申请重复提交容忍与域名/路径工具。"""

import unittest
from unittest import mock

from panel_cli import web
from panel_cli.errors import CliError
from panel_cli.web import (
    SslOptions,
    _already_applying,
    detect_archive_type,
    join_site_path,
)


class _Resp:
    def __init__(self, payload):
        self._payload = payload
        self.status = 200

    def json(self):
        return self._payload


class FakeClient:
    """按调用顺序返回预置响应的假客户端。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.posts = []

    def post_json(self, path, body):
        self.posts.append((path, body))
        if not self.responses:
            raise AssertionError(f"unexpected request: {path}")
        payload = self.responses.pop(0)
        return _Resp(payload)

    def get(self, path):
        raise AssertionError(f"unexpected GET: {path}")


class TestAlreadyApplying(unittest.TestCase):
    def test_matches_panel_message(self):
        self.assertTrue(_already_applying("服务错误: 任务执行中，请勿重复执行"))
        self.assertTrue(_already_applying("Task is running, do not repeat"))
        self.assertFalse(_already_applying("数据库连接失败"))
        self.assertFalse(_already_applying(""))


class TestSslApply(unittest.TestCase):
    def _run(self, obtain_payload):
        responses = [
            # acme search: 已有账号,直接复用
            {"code": 200, "data": {"total": 1, "items": [{"id": 1, "email": "a@b.com"}]}},
            # websites/ssl 创建
            {"code": 200, "data": {"id": 7}},
            # websites/ssl/obtain
            obtain_payload,
        ]
        client = FakeClient(responses)
        with mock.patch.object(web, "_client_for", return_value=(client, None)):
            web.ssl("default", "demo.example.com", SslOptions(
                email="a@b.com", provider="http", key_type="RSA2048", auto_renew=True
            ))
        return client

    def test_tolerates_already_running(self):
        client = self._run({"code": 500, "message": "服务错误: 任务执行中，请勿重复执行"})
        paths = [p for p, _ in client.posts]
        self.assertIn("api/v2/websites/ssl/obtain", paths)

    def test_other_error_raises(self):
        with self.assertRaises(CliError):
            self._run({"code": 500, "message": "服务错误: 数据库连接失败"})


class TestPathTools(unittest.TestCase):
    def test_join_site_path(self):
        self.assertEqual(join_site_path("/www/site/", "index"), "/www/site/index")
        self.assertEqual(join_site_path("/www/site/", ""), "/www/site")
        self.assertEqual(join_site_path("/www/site/", "/a/b/"), "/www/site/a/b")

    def test_detect_archive_type(self):
        self.assertEqual(detect_archive_type("a.tar.gz"), "tar.gz")
        self.assertEqual(detect_archive_type("A.ZIP"), "zip")
        self.assertEqual(detect_archive_type("x.tar.xz"), "tar.xz")
        with self.assertRaises(CliError):
            detect_archive_type("plain.txt")


if __name__ == "__main__":
    unittest.main()
