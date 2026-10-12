"""apps 模块测试:本地应用(local 前缀)key 兼容与已安装应用匹配。"""

import unittest

from panel_cli import apps
from panel_cli.errors import CliError


class _Resp:
    def __init__(self, payload):
        self._payload = payload
        self.status = 200

    def json(self):
        return self._payload


class FakeClient:
    """get 按顺序返回预置响应;post_json 返回固定响应。"""

    def __init__(self, get_payloads=None, post_payload=None):
        self.get_payloads = list(get_payloads or [])
        self.post_payload = post_payload
        self.gets = []

    def get(self, path):
        self.gets.append(path)
        if not self.get_payloads:
            raise AssertionError(f"unexpected GET: {path}")
        return _Resp(self.get_payloads.pop(0))

    def post_json(self, path, body):
        return _Resp(self.post_payload)


class TestNormalizeAppKey(unittest.TestCase):
    def test_strips_local_prefix(self):
        self.assertEqual(apps.normalize_app_key("localopenresty"), "openresty")
        self.assertEqual(apps.normalize_app_key("localmysql"), "mysql")

    def test_case_and_space(self):
        self.assertEqual(apps.normalize_app_key("  OpenResty "), "openresty")

    def test_empty(self):
        self.assertEqual(apps.normalize_app_key(""), "")
        self.assertEqual(apps.normalize_app_key(None), "")


class TestGetApp(unittest.TestCase):
    def test_direct_key(self):
        client = FakeClient([{"code": 200, "data": {"id": 2, "key": "openresty"}}])
        self.assertEqual(apps.get_app(client, "openresty")["id"], 2)

    def test_falls_back_to_local_key(self):
        client = FakeClient([
            {"code": 500, "message": "服务错误: record not found"},
            {"code": 200, "data": {"id": 2, "key": "localopenresty"}},
        ])
        app = apps.get_app(client, "openresty")
        self.assertEqual(app["key"], "localopenresty")
        self.assertEqual(client.gets, ["api/v2/apps/openresty", "api/v2/apps/localopenresty"])

    def test_local_key_falls_back_to_remote(self):
        client = FakeClient([
            {"code": 500, "message": "服务错误: record not found"},
            {"code": 200, "data": {"id": 2, "key": "openresty"}},
        ])
        app = apps.get_app(client, "localopenresty")
        self.assertEqual(app["key"], "openresty")

    def test_not_found_raises(self):
        client = FakeClient([
            {"code": 500, "message": "服务错误: record not found"},
            {"code": 500, "message": "服务错误: record not found"},
        ])
        with self.assertRaises(CliError):
            apps.get_app(client, "nope")


class TestFindInstalled(unittest.TestCase):
    def _client(self):
        return FakeClient(post_payload={"code": 200, "data": {"items": [
            {"id": 1, "name": "localopenresty", "appKey": "localopenresty", "appName": "OpenResty"},
        ]}})

    def test_match_by_local_key(self):
        self.assertEqual(apps.find_installed(self._client(), "openresty")["id"], 1)

    def test_match_by_display_name(self):
        self.assertEqual(apps.find_installed(self._client(), "OpenResty")["id"], 1)

    def test_match_by_installed_name(self):
        self.assertEqual(apps.find_installed(self._client(), "localopenresty")["id"], 1)

    def test_not_installed_raises(self):
        with self.assertRaises(CliError):
            apps.find_installed(self._client(), "mysql")


class TestResolveInstanceName(unittest.TestCase):
    """数据库/Redis 实例名 local 前缀兼容(db/redis 命令共用)。"""

    def _client(self, names):
        return FakeClient(post_payload={"code": 200, "data": {"items": [
            {"id": i, "name": n, "appKey": n} for i, n in enumerate(names, start=1)
        ]}})

    def test_direct_match(self):
        self.assertEqual(apps.resolve_instance_name(self._client(["mysql"]), "mysql"), "mysql")

    def test_local_name_resolves_to_canonical_install(self):
        self.assertEqual(
            apps.resolve_instance_name(self._client(["mysql"]), "localmysql"), "mysql"
        )

    def test_canonical_name_resolves_to_local_install(self):
        self.assertEqual(
            apps.resolve_instance_name(self._client(["localmysql"]), "mysql"), "localmysql"
        )

    def test_no_match_keeps_requested(self):
        self.assertEqual(
            apps.resolve_instance_name(self._client(["redis"]), "mysql"), "mysql"
        )

    def test_case_insensitive(self):
        self.assertEqual(
            apps.resolve_instance_name(self._client(["localRedis"]), "redis"), "localRedis"
        )

    def test_empty(self):
        self.assertEqual(apps.resolve_instance_name(self._client(["mysql"]), ""), "")


if __name__ == "__main__":
    unittest.main()
