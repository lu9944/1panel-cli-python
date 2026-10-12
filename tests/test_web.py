"""web 模块测试:SSL 申请重复提交容忍、SPA 回退、本地应用路径兼容与域名/路径工具。"""

import os
import tempfile
import unittest
from unittest import mock

from panel_cli import web
from panel_cli.errors import CliError
from panel_cli.web import (
    CreateOptions,
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


class TestNormalizeProxy(unittest.TestCase):
    def test_adds_scheme(self):
        self.assertEqual(web.normalize_proxy("127.0.0.1:3500"), "http://127.0.0.1:3500")
        self.assertEqual(web.normalize_proxy(" 127.0.0.1:3500 "), "http://127.0.0.1:3500")

    def test_keeps_existing_scheme(self):
        self.assertEqual(web.normalize_proxy("http://127.0.0.1:3500"), "http://127.0.0.1:3500")
        self.assertEqual(web.normalize_proxy("https://app.local"), "https://app.local")

    def test_empty_raises(self):
        with self.assertRaises(CliError):
            web.normalize_proxy("")


class TestSpaContent(unittest.TestCase):
    def test_contains_fallback_and_guards(self):
        content = web.build_spa_content("127.0.0.1:3500")
        self.assertIn("proxy_intercept_errors on;", content)
        self.assertIn("error_page 404 = @spa_fallback;", content)
        self.assertIn("proxy_pass http://127.0.0.1:3500;", content)
        self.assertIn("rewrite ^ /index.html break;", content)
        self.assertIn(r"^/(api|auth|health|linear)(/|$)", content)

    def test_custom_api_prefixes(self):
        content = web.build_spa_content("http://127.0.0.1:1", "api,static-api")
        self.assertIn(r"^/(api|static\-api)(/|$)", content)


class TestOpenrestyPathCompat(unittest.TestCase):
    def test_local_app(self):
        item = {
            "appKey": "localopenresty",
            "path": "/opt/1panel/apps/local/openresty/localopenresty",
        }
        self.assertEqual(
            web.openresty_path_compat(item),
            ("/opt/1panel/apps/openresty", "/opt/1panel/apps/local/openresty"),
        )

    def test_remote_app_ignored(self):
        item = {"appKey": "openresty", "path": "/opt/1panel/apps/openresty/openresty"}
        self.assertIsNone(web.openresty_path_compat(item))

    def test_other_base_dir_ignored(self):
        item = {"appKey": "localopenresty", "path": "/data/apps/openresty/custom"}
        self.assertIsNone(web.openresty_path_compat(item))

    def test_ensure_creates_symlink(self):
        item = {
            "appKey": "localopenresty",
            "path": "/opt/1panel/apps/local/openresty/localopenresty",
        }
        exists = {"/opt/1panel/apps/local/openresty": True, "/opt/1panel/apps/openresty": False}
        with mock.patch.object(web, "find_openresty_install_item", return_value=item), \
                mock.patch("os.path.exists", side_effect=lambda p: exists.get(p, False)), \
                mock.patch("os.symlink") as symlink:
            self.assertEqual(web.ensure_openresty_compat(mock.Mock()), "fixed")
            symlink.assert_called_once_with(
                "/opt/1panel/apps/local/openresty", "/opt/1panel/apps/openresty"
            )

    def test_ensure_already_ok(self):
        item = {
            "appKey": "localopenresty",
            "path": "/opt/1panel/apps/local/openresty/localopenresty",
        }
        with mock.patch.object(web, "find_openresty_install_item", return_value=item), \
                mock.patch("os.path.exists", return_value=True), \
                mock.patch("os.symlink") as symlink:
            self.assertEqual(web.ensure_openresty_compat(mock.Mock()), "ok")
            symlink.assert_not_called()

    def test_ensure_off_host(self):
        item = {
            "appKey": "localopenresty",
            "path": "/opt/1panel/apps/local/openresty/localopenresty",
        }
        with mock.patch.object(web, "find_openresty_install_item", return_value=item), \
                mock.patch("os.path.exists", return_value=False):
            self.assertEqual(web.ensure_openresty_compat(mock.Mock()), "unavailable")


class TestCreateOneShot(unittest.TestCase):
    def test_proxy_ssl_spa_in_one_request(self):
        responses = [
            {"code": 200, "data": {"items": [{
                "id": 1, "appKey": "localopenresty", "name": "localopenresty",
                "path": "/opt/1panel/apps/local/openresty/localopenresty",
            }]}},  # apps/installed/search
            {"code": 200, "data": [{"id": 4, "isDefault": True}]},  # groups/search
            {"code": 200, "data": None},  # POST websites
            {"code": 200, "data": {"items": [{"id": 9, "primaryDomain": "app.example.com"}]}},  # websites/search
            {"code": 200, "data": None},  # rewrite/update
        ]
        client = FakeClient(responses)
        with mock.patch.object(web, "_client_for", return_value=(client, None)), \
                mock.patch.object(web, "ensure_openresty_compat", return_value="skip"):
            web.create("default", "app.example.com", CreateOptions(
                type="static", alias=None, remark=None,
                proxy="127.0.0.1:3500", ssl_id=1, http_config=None, spa=True,
            ))
        create_body = next(b for p, b in client.posts if p == "api/v2/websites")
        self.assertEqual(create_body["type"], "proxy")
        self.assertEqual(create_body["proxy"], "http://127.0.0.1:3500")
        self.assertTrue(create_body["enableSSL"])
        self.assertEqual(create_body["websiteSSLID"], 1)
        rewrite_paths = [p for p, _ in client.posts if p == "api/v2/websites/rewrite/update"]
        self.assertEqual(len(rewrite_paths), 1)

    def test_spa_requires_proxy_type(self):
        client = FakeClient([])
        with mock.patch.object(web, "_client_for", return_value=(client, None)), \
                mock.patch.object(web, "ensure_openresty_compat", return_value="skip"):
            with self.assertRaises(CliError):
                web.create("default", "x.example.com", CreateOptions(
                    type="static", alias=None, remark=None, spa=True,
                ))


class TestDelete(unittest.TestCase):
    def _client(self, del_payload):
        return FakeClient([
            {"code": 200, "data": {"items": [{"id": 9, "primaryDomain": "x.example.com"}]}},
            del_payload,
        ])

    def test_delete_success(self):
        client = self._client({"code": 200, "message": "success"})
        with mock.patch.object(web, "_client_for", return_value=(client, None)):
            web.delete("default", "x.example.com", force=False)
        self.assertEqual(client.posts[-1][0], "api/v2/websites/del")

    def test_delete_record_not_found_requires_force(self):
        client = self._client({"code": 500, "message": "服务错误: record not found"})
        with mock.patch.object(web, "_client_for", return_value=(client, None)):
            with self.assertRaises(CliError) as ctx:
                web.delete("default", "x.example.com", force=False)
        self.assertIn("--force", str(ctx.exception))

    def test_delete_force_uses_workaround(self):
        client = self._client({"code": 500, "message": "服务错误: record not found"})
        with mock.patch.object(web, "_client_for", return_value=(client, None)), \
                mock.patch.object(web, "_delete_with_local_app_key_workaround") as workaround:
            web.delete("default", "x.example.com", force=True)
        workaround.assert_called_once()


class TestRepair(unittest.TestCase):
    def test_repair_without_apps(self):
        client = FakeClient([{"code": 200, "data": {"items": []}}])
        with mock.patch.object(web, "_client_for", return_value=(client, None)):
            web.repair("default")


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
