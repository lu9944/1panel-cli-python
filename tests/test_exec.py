"""exec 模块测试:命令组装、哨兵匹配、输出清洗、JSON 输出。"""

import base64
import json
import unittest

from panel_cli import exec as exec_module
from panel_cli.exec import (
    SENTINEL_PREFIX,
    ExecOptions,
    ExecResult,
    build_handshake,
    clean_output,
    compose_command,
    find_sentinel,
    local_conn_body,
    new_token,
    normalize_chunk,
    strip_ansi,
    tail_lines,
    to_json,
    ws_url,
)
from panel_cli.session import CookieData, Session


def test_session(url: str) -> Session:
    return Session(
        profile="default",
        panel_url=url,
        entrance="",
        username="admin",
        panel_name="",
        logged_in_at="",
        insecure=False,
        cookies=[],
    )


class TestWsUrl(unittest.TestCase):
    def test_schemes_and_ports(self):
        s = test_session("http://10.0.0.1:39870")
        url = ws_url(s, "echo hi", 120, 40)
        self.assertTrue(
            url.startswith("ws://10.0.0.1:39870/api/v2/hosts/terminal/local?")
        )
        self.assertIn("command=echo%20hi&cols=120&rows=40", url)

        s = test_session("https://panel.example.com")
        url = ws_url(s, "true", 80, 24)
        self.assertTrue(
            url.startswith("wss://panel.example.com/api/v2/hosts/terminal/local?")
        )
        self.assertIn("cols=80&rows=24", url)

        s = test_session("http://127.0.0.1/")
        url = ws_url(s, "ls -la /var/www", 120, 40)
        self.assertIn("command=ls%20-la%20%2Fvar%2Fwww", url)

        from panel_cli.errors import CliError

        with self.assertRaises(CliError):
            ws_url(s, "true", 0, 40)


class TestHandshake(unittest.TestCase):
    def test_headers(self):
        s = test_session("http://127.0.0.1:39870")
        s.cookies = [
            CookieData("psession", "MTczLi4u"),
            CookieData("SecurityEntrance", "abc"),
        ]
        import os

        saved = os.environ.pop("PANEL_NODE", None)
        try:
            hs = build_handshake(s)
            self.assertEqual(len(hs), 2)
            self.assertEqual(hs[0][0], "cookie")
            self.assertEqual(hs[0][1], "psession=MTczLi4u; SecurityEntrance=abc")
            self.assertEqual(hs[1][0], "currentnode")
            self.assertEqual(hs[1][1], "local")
        finally:
            if saved is not None:
                os.environ["PANEL_NODE"] = saved

    def test_node_is_query_unescape_roundtrip(self):
        # 服务端对 CurrentNode 做 url.QueryUnescape,编码后必须能还原
        from urllib.parse import unquote_plus

        from panel_cli.config import encode_current_node

        for node in ("local", "node-1", "节点 一号", "a+b&c=d/e:f", "a.b_c~d"):
            self.assertEqual(unquote_plus(encode_current_node(node)), node)


class TestComposeCommand(unittest.TestCase):
    def test_basic_and_sentinel(self):
        token = "deadbeef"
        cmd = compose_command(None, "ls -la", token, False)
        self.assertEqual(cmd, "( ls -la ); echo __1PCLI_deadbeef_$?")
        cmd = compose_command("/opt/myapp", "bash deploy.sh", token, False)
        self.assertEqual(
            cmd,
            "cd -- '/opt/myapp' && ( bash deploy.sh ); echo __1PCLI_deadbeef_$?",
        )
        # exit/exec 类命令被子 shell 隔离,哨兵仍可执行并透传退出码
        cmd = compose_command(None, "exit 7", token, False)
        self.assertEqual(cmd, "( exit 7 ); echo __1PCLI_deadbeef_$?")

    def test_sudo(self):
        token = "deadbeef"
        cmd = compose_command(None, "systemctl restart nginx", token, True)
        self.assertEqual(
            cmd,
            "( sudo -n -H bash -c 'systemctl restart nginx' ); echo __1PCLI_deadbeef_$?",
        )
        # --sudo 时 cd 移入 sudo 环境内执行(目录可能仅 root 可读)
        cmd = compose_command("/opt/a b", "ls", token, True)
        self.assertEqual(
            cmd,
            r"( sudo -n -H bash -c 'cd -- '\''/opt/a b'\'' && ls' ); echo __1PCLI_deadbeef_$?",
        )

    def test_cwd_quoting_and_validation(self):
        token = "deadbeef"
        cmd = compose_command("/opt/a b/c", "true", token, False)
        self.assertTrue(cmd.startswith("cd -- '/opt/a b/c' && ( true ); "))
        cmd = compose_command("/opt/it's", "true", token, False)
        self.assertTrue(cmd.startswith(r"cd -- '/opt/it'\''s' && ( true ); "))
        # 预校验:空命令 / 换行
        from panel_cli.errors import CliError

        with self.assertRaises(CliError):
            compose_command(None, "  ", token, False)
        with self.assertRaises(CliError):
            compose_command(None, "echo a\necho b", token, False)
        with self.assertRaises(CliError):
            compose_command(None, "echo a\recho b", token, False)
        with self.assertRaises(CliError):
            compose_command("/opt/a\nb", "true", token, False)


class TestFindSentinel(unittest.TestCase):
    def test_single_hit_and_exit_code(self):
        token = new_token()
        self.assertEqual(len(token), 8)
        sentinel = f"{SENTINEL_PREFIX}{token}"
        # 回显行(字面量 $?)不参与匹配
        self.assertIsNone(
            find_sentinel(f" clear &&echo hi; echo {sentinel}_$?", token)
        )
        # 真实输出:单次命中即完成
        self.assertEqual(find_sentinel(f"hi\r\n{sentinel}_0\r\n$ ", token), 0)
        self.assertEqual(find_sentinel(f"out\r\n{sentinel}_7", token), 7)
        self.assertEqual(find_sentinel(f"{sentinel}_255", token), 255)
        # 0 次命中(超时路径)
        self.assertIsNone(find_sentinel("no sentinel here", token))
        self.assertIsNone(find_sentinel("", token))

    def test_wrapped_or_ansi_broken_stream(self):
        token = "ab12cd34"
        sentinel = f"{SENTINEL_PREFIX}{token}"
        # PTY 折行把哨兵拦腰截断:归一化(剥离 ANSI + 去掉 \r\n)后仍应命中
        raw = "hello\r\n\x1b[?2004h__1PCLI_ab12cd3\r\n4_42\r\n[ubuntu@host ~]$ "
        normalized = normalize_chunk(raw)
        self.assertEqual(find_sentinel(normalized, token), 42)
        # ANSI 序列夹在中间不影响
        raw2 = f"{sentinel}_\x1b[0K3"
        self.assertEqual(find_sentinel(normalize_chunk(raw2), token), 3)
        # 超过 3 位数字按贪婪 3 位解析(与文档正则 (\d{1,3}) 语义一致)
        self.assertEqual(find_sentinel(f"{sentinel}_1234", token), 123)


class TestStripAnsi(unittest.TestCase):
    def test_variants(self):
        self.assertEqual(strip_ansi("\x1b[2J\x1b[Hhello"), "hello")
        self.assertEqual(strip_ansi("a\x1b]0;title\x07b"), "ab")
        self.assertEqual(strip_ansi("a\x1b]0;title\x1b\\b"), "ab")
        self.assertEqual(strip_ansi("\x1b(Bx"), "x")
        self.assertEqual(strip_ansi("plain\r\ntext\x07"), "plain\r\ntext")

    def test_normalize_chunk_removes_line_breaks(self):
        self.assertEqual(normalize_chunk("a\r\nb\r\nc"), "abc")
        self.assertEqual(normalize_chunk("\x1b[31mx\x1b[0my"), "xy")


class TestCleanOutput(unittest.TestCase):
    def test_strips_echo_banner_and_prompt(self):
        token = "ab12cd34"
        sentinel = f"{SENTINEL_PREFIX}{token}"
        raw = (
            "Last login: Mon Aug 31 00:00:00 2026 from 127.0.0.1\r\n"
            "[ubuntu@host ~]$  clear &&( echo hello && hostname ); echo __1PCLI_ab12cd34_$?\r\n"
            "\x1b[H\x1b[2J\x1b[3J"
            "hello\r\n"
            "myhost\r\n"
            "__1PCLI_ab12cd34_0\r\n"
            "[ubuntu@host ~]$ "
        )
        cleaned = clean_output(raw, token)
        self.assertEqual(cleaned, "hello\nmyhost")

    def test_without_echo_falls_back(self):
        token = "ab12cd34"
        sentinel = f"{SENTINEL_PREFIX}{token}"
        # stty -echo 场景:没有回显行,仅哨兵输出行
        raw = f"banner\r\nline1\r\n{sentinel}_2\r\n$ "
        cleaned = clean_output(raw, token)
        self.assertIn("line1", cleaned)
        self.assertNotIn(sentinel, cleaned)
        self.assertNotIn("$ ", cleaned)

    def test_keeps_output_containing_clear_and_word(self):
        token = "ab12cd34"
        sentinel = f"{SENTINEL_PREFIX}{token}"
        # 命令输出中出现 "clear &&" 字样但不在行首(带前导空格的行首判定不误伤)
        raw = f"run: clear &&ls\r\nout\r\n{sentinel}_0\r\n"
        cleaned = clean_output(raw, token)
        self.assertEqual(cleaned, "run: clear &&ls\nout")


class TestTailLines(unittest.TestCase):
    def test_tail(self):
        text = "a\nb\nc\nd\ne"
        self.assertEqual(tail_lines(text, 2), ("d\ne", True))
        self.assertEqual(tail_lines(text, 5), (text, False))
        self.assertEqual(tail_lines(text, 10), (text, False))
        self.assertEqual(tail_lines(text, 0), ("", True))
        self.assertEqual(tail_lines("", 3), ("", False))


class TestToJson(unittest.TestCase):
    def test_shape_and_order(self):
        r = ExecResult(
            exit_code=7, duration_ms=1234, truncated=False, error=None, output="hi"
        )
        self.assertEqual(
            to_json(r),
            '{"exit_code":7,"duration_ms":1234,"truncated":false,"error":null,"output":"hi"}',
        )
        r = ExecResult(
            exit_code=1, duration_ms=0, truncated=True, error="boom", output=""
        )
        self.assertEqual(
            to_json(r),
            '{"exit_code":1,"duration_ms":0,"truncated":true,"error":"boom","output":""}',
        )


class TestLocalConnBody(unittest.TestCase):
    def test_base64_password(self):
        body = local_conn_body("ubuntu", "s3cret", 22)
        self.assertEqual(body["addr"], "127.0.0.1")
        self.assertEqual(body["port"], 22)
        self.assertEqual(body["user"], "ubuntu")
        self.assertEqual(body["authMode"], "password")
        self.assertEqual(
            body["password"], base64.b64encode(b"s3cret").decode("ascii")
        )
        body = local_conn_body("u", "p", 2222)
        self.assertEqual(body["port"], 2222)


class TestWsMsgDecode(unittest.TestCase):
    def test_cmd_message(self):
        text = json.dumps({"type": "cmd", "data": base64.b64encode(b"hello").decode()})
        self.assertEqual(exec_module._decode_cmd_message(text), "hello")

    def test_heartbeat_message(self):
        text = json.dumps({"type": "heartbeat", "timestamp": 123})
        self.assertEqual(exec_module._decode_cmd_message(text), "")

    def test_non_json_falls_back(self):
        self.assertEqual(exec_module._decode_cmd_message("plain"), "plain")


class TestExecOptions(unittest.TestCase):
    def test_defaults(self):
        opts = ExecOptions(command="ls")
        self.assertEqual(opts.timeout, 30)
        self.assertEqual(opts.cols, 120)
        self.assertEqual(opts.rows, 40)
        self.assertEqual(opts.ssh_port, 22)
        self.assertFalse(opts.sudo)
        self.assertFalse(opts.json)


if __name__ == "__main__":
    unittest.main()
