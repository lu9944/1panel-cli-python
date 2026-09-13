"""init 模块测试:user-info 解析、DB 读取、地址解析与 .env 生成。"""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from panel_cli import initcmd
from panel_cli.errors import CliError

ZH_OUTPUT = """\x1b[0;34m============================================================\x1b[0m
\x1b[0;34m                       获取 1Panel 用户信息\x1b[0m
\x1b[0;34m============================================================\x1b[0m
面板地址: http://$LOCAL_IP:31206/70fb4ccdb4 
用户: deb45dc58a
用户密码: ********
提示: 修改密码可执行命令: 1pctl update password
\x1b[0;34m------------------------------------------------------------\x1b[0m
"""

ZH_INITIAL = """面板地址: https://panel.example.com:443/ent123
用户: admin
用户密码: Init@Pass123
提示: 修改密码可执行命令: 1pctl update password
"""

EN_OUTPUT = """Panel address: http://$LOCAL_IP:9999/abc
User: root
Password: ********
Tip: to change the password, run: 1pctl update password
"""


class TestParseUserInfo(unittest.TestCase):
    def test_zh_masked(self):
        info = initcmd.parse_user_info(ZH_OUTPUT)
        self.assertEqual(info["address"], "http://$LOCAL_IP:31206/70fb4ccdb4")
        self.assertEqual(info["username"], "deb45dc58a")
        self.assertEqual(info["password"], "********")

    def test_zh_initial_password(self):
        info = initcmd.parse_user_info(ZH_INITIAL)
        self.assertEqual(info["address"], "https://panel.example.com:443/ent123")
        self.assertEqual(info["username"], "admin")
        self.assertEqual(info["password"], "Init@Pass123")

    def test_en_output(self):
        info = initcmd.parse_user_info(EN_OUTPUT)
        self.assertEqual(info["address"], "http://$LOCAL_IP:9999/abc")
        self.assertEqual(info["username"], "root")
        self.assertEqual(info["password"], "********")

    def test_no_url(self):
        info = initcmd.parse_user_info("something else")
        self.assertEqual(info, {"address": "", "username": "", "password": ""})


class TestAddress(unittest.TestCase):
    def test_from_settings(self):
        settings = {
            "SSL": "Enable",
            "SystemIP": "10.0.0.2",
            "ServerPort": "12345",
            "SecurityEntrance": "ent",
        }
        self.assertEqual(
            initcmd.address_from_settings(settings), "https://10.0.0.2:12345/ent"
        )

    def test_from_settings_bind_domain(self):
        settings = {
            "BindDomain": "panel.example.com",
            "SystemIP": "10.0.0.2",
            "ServerPort": "9999",
        }
        self.assertEqual(
            initcmd.address_from_settings(settings), "http://panel.example.com:9999"
        )

    def test_from_settings_local_ip(self):
        settings = {"ServerPort": "9999"}
        self.assertEqual(
            initcmd.address_from_settings(settings), "http://$LOCAL_IP:9999"
        )

    def test_split(self):
        self.assertEqual(
            initcmd.split_address("http://1.2.3.4:8080/ent"),
            ("http", "1.2.3.4", ":8080", "/ent"),
        )
        with self.assertRaises(CliError):
            initcmd.split_address("not-a-url")


class TestResolveAddress(unittest.TestCase):
    def test_local_ip_picks_reachable(self):
        calls = []

        def probe(base):
            calls.append(base)
            return "ok" if "127.0.0.1" in base else None

        url, insecure, warning = initcmd.resolve_address(
            "http://$LOCAL_IP:9999/ent", probe=probe
        )
        self.assertEqual(url, "http://127.0.0.1:9999/ent")
        self.assertFalse(insecure)
        self.assertIsNone(warning)

    def test_local_ip_insecure(self):
        url, insecure, warning = initcmd.resolve_address(
            "https://$LOCAL_IP:9999/ent", probe=lambda base: "insecure"
        )
        self.assertEqual(url, "https://127.0.0.1:9999/ent")
        self.assertTrue(insecure)

    def test_explicit_host(self):
        url, insecure, warning = initcmd.resolve_address(
            "http://$LOCAL_IP:9999/ent", explicit_host="192.168.1.18", probe=lambda base: "ok"
        )
        self.assertEqual(url, "http://192.168.1.18:9999/ent")
        self.assertFalse(insecure)

    def test_concrete_host_unreachable_warns(self):
        url, insecure, warning = initcmd.resolve_address(
            "http://panel.example.com:9999/ent", probe=lambda base: None
        )
        self.assertEqual(url, "http://panel.example.com:9999/ent")
        self.assertIsNotNone(warning)


def _create_db(path: Path, rows: dict) -> None:
    connection = sqlite3.connect(str(path))
    connection.execute("create table settings (key text, value text)")
    connection.executemany(
        "insert into settings (key, value) values (?, ?)", list(rows.items())
    )
    connection.commit()
    connection.close()


class TestReadSettings(unittest.TestCase):
    def test_core_and_agent_are_separate(self):
        # agent.db 也有同名 EncryptKey,不能覆盖 core.db 的值
        with tempfile.TemporaryDirectory() as tmp:
            core = Path(tmp) / "core.db"
            agent = Path(tmp) / "agent.db"
            _create_db(core, {"EncryptKey": "core-key", "Password": "p"})
            _create_db(agent, {"EncryptKey": "agent-key", "SystemIP": "1.2.3.4"})
            settings = initcmd._read_settings(core, agent)
            self.assertEqual(settings["EncryptKey"], "core-key")
            self.assertEqual(settings["SystemIP"], "1.2.3.4")

    def test_missing_db_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(CliError):
                initcmd.read_panel_settings(
                    Path(tmp) / "nope.db", Path(tmp) / "nope2.db"
                )


class TestWriteEnv(unittest.TestCase):
    def test_write_and_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            initcmd.write_env(path, "A=1\n")
            self.assertEqual(path.read_text(), "A=1\n")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(CliError):
                initcmd.write_env(path, "B=2\n")
            initcmd.write_env(path, "B=2\n", force=True)
            self.assertEqual(path.read_text(), "B=2\n")


class TestRenderEnv(unittest.TestCase):
    def test_fields(self):
        content = initcmd.render_env(
            "https://panel:9999/ent", "admin", "secret", insecure=True
        )
        self.assertIn("PANEL_URL=https://panel:9999/ent", content)
        self.assertIn("PANEL_USERNAME=admin", content)
        self.assertIn("PANEL_PASSWORD=secret", content)
        self.assertIn("PANEL_INSECURE=true", content)


class TestRunInit(unittest.TestCase):
    def _fake_db(self, tmp: str) -> tuple[Path, Path]:
        core = Path(tmp) / "core.db"
        agent = Path(tmp) / "agent.db"
        _create_db(
            core,
            {
                "UserName": "dbuser",
                "Password": "unused-encrypted",
                "EncryptKey": "abcdefghijklmnop",
                "ServerPort": "9999",
                "SSL": "Disable",
                "SecurityEntrance": "ent",
            },
        )
        _create_db(agent, {"SystemIP": ""})
        return core, agent

    def test_uses_db_password_when_masked(self):
        with tempfile.TemporaryDirectory() as tmp:
            core, agent = self._fake_db(tmp)
            info = {
                "address": "http://$LOCAL_IP:9999/ent",
                "username": "cli-user",
                "password": "********",
            }
            output = Path(tmp) / ".env"
            with mock.patch.object(initcmd, "run_user_info", return_value=(info, None)), \
                 mock.patch.object(initcmd, "string_decrypt", return_value="real-password") as decrypt, \
                 mock.patch.object(initcmd, "probe_panel", return_value="ok"):
                initcmd.run_init(
                    output=str(output), core_db=core, agent_db=agent
                )
            decrypt.assert_called_once_with("unused-encrypted", "abcdefghijklmnop")
            content = output.read_text()
            self.assertIn("PANEL_USERNAME=cli-user", content)
            self.assertIn("PANEL_PASSWORD=real-password", content)
            self.assertIn("PANEL_URL=http://127.0.0.1:9999/ent", content)

    def test_uses_user_info_password_when_shown(self):
        with tempfile.TemporaryDirectory() as tmp:
            core, agent = self._fake_db(tmp)
            info = {
                "address": "http://panel.local:9999/ent",
                "username": "admin",
                "password": "Shown@Pass",
            }
            output = Path(tmp) / ".env"
            with mock.patch.object(initcmd, "run_user_info", return_value=(info, None)), \
                 mock.patch.object(initcmd, "string_decrypt") as decrypt, \
                 mock.patch.object(initcmd, "probe_panel", return_value="ok"):
                initcmd.run_init(output=str(output), core_db=core, agent_db=agent)
            decrypt.assert_not_called()
            self.assertIn("PANEL_PASSWORD=Shown@Pass", output.read_text())

    def test_falls_back_to_db_when_user_info_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            core, agent = self._fake_db(tmp)
            output = Path(tmp) / ".env"
            with mock.patch.object(initcmd, "run_user_info", return_value=(None, "无 sudo")), \
                 mock.patch.object(initcmd, "string_decrypt", return_value="db-pass"), \
                 mock.patch.object(initcmd, "probe_panel", return_value="ok"):
                initcmd.run_init(output=str(output), core_db=core, agent_db=agent)
            content = output.read_text()
            self.assertIn("PANEL_USERNAME=dbuser", content)
            self.assertIn("PANEL_PASSWORD=db-pass", content)


if __name__ == "__main__":
    unittest.main()
