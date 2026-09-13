"""在面板本机执行单行命令(通过终端 WebSocket,非交互)。

对应 Rust 版 exec.rs:
- 组装命令(子 shell 包裹 + 退出码哨兵);
- WebSocket 会话收集输出,哨兵匹配后取回退出码;
- 输出清洗(剥离 ANSI / 回显 / 提示符),支持 --raw / --tail / --json;
- 退出码透传(124 表示超时)。
"""

from __future__ import annotations

import base64
import json
import os
import re
import sys
import time
import urllib.parse
from dataclasses import dataclass
from typing import Optional

from .client import PanelClient
from .config import ENV_NODE, encode_current_node, linux_ssh_creds
from .errors import CliError
from .session import Session, load_session
from .ws import WebSocket, WsConnectionClosed, WsHandshakeError

DEFAULT_TIMEOUT_SECS = 30
DEFAULT_COLS = 120
DEFAULT_ROWS = 40
DEFAULT_SSH_PORT = 22
# 收集输出的上限(超出截断并标记 truncated)
MAX_OUTPUT_BYTES = 10 * 1024 * 1024

WS_PATH = "/api/v2/hosts/terminal/local"
SENTINEL_PREFIX = "__1PCLI_"
MAX_COMMAND_LEN = 4000
# 哨兵匹配用的归一化流窗口大小(环形,防超大输出撑爆内存)
MATCH_WINDOW_BYTES = 1024 * 1024


@dataclass
class ExecOptions:
    command: str
    timeout: int = DEFAULT_TIMEOUT_SECS
    cols: int = DEFAULT_COLS
    rows: int = DEFAULT_ROWS
    json: bool = False
    tail: Optional[int] = None
    raw: bool = False
    cwd: Optional[str] = None
    sync_ssh: bool = False
    ssh_port: int = DEFAULT_SSH_PORT
    sudo: bool = False


@dataclass
class ExecResult:
    exit_code: int
    duration_ms: int
    truncated: bool
    error: Optional[str]
    output: str


# ---------------------------------------------------------------------------
# WS URL / 握手头 / 本地 SSH 连接
# ---------------------------------------------------------------------------


def ws_url(session: Session, command: str, cols: int, rows: int) -> str:
    """由面板 base URL 构建终端 WS 握手 URL(无需拼接安全入口路径)。"""
    if cols <= 0 or rows <= 0:
        raise CliError("--cols/--rows 必须为正整数(服务端要求纯数字)")
    base = session.panel_url.strip().rstrip("/")
    if base.startswith("https://"):
        ws_base = "wss://" + base[len("https://"):]
    elif base.startswith("http://"):
        ws_base = "ws://" + base[len("http://"):]
    elif base.startswith("ws://") or base.startswith("wss://"):
        ws_base = base
    else:
        ws_base = "ws://" + base
    if len(ws_base) <= 6:
        raise CliError("面板地址不能为空")
    cmd = urllib.parse.quote(command, safe="")
    return f"{ws_base}{WS_PATH}?command={cmd}&cols={cols}&rows={rows}"


def build_handshake(session: Session) -> list[tuple[str, str]]:
    """WS 握手附加请求头:全部会话 Cookie + CurrentNode(与 client 保持一致)。"""
    cookie = "; ".join(f"{c.name}={c.value}" for c in session.cookies)
    node = (os.environ.get(ENV_NODE) or "local").strip()
    node = encode_current_node(node)
    return [("cookie", cookie), ("currentnode", node)]


def local_conn_body(user: str, pwd: str, port: int) -> dict:
    """`POST api/v2/settings/ssh` (SaveLocalConn) 的请求体,password 需 base64 编码。"""
    return {
        "addr": "127.0.0.1",
        "port": port,
        "user": user,
        "authMode": "password",
        "password": base64.b64encode(pwd.encode("utf-8")).decode("ascii"),
    }


def ensure_local_conn(profile: str, user: str, pwd: str, port: int) -> None:
    """写入面板"本地 SSH 连接"(保存即由面板真实拨号校验)。

    供登录初始化与 doctor 复用:确保 exec 以 .env 声明的用户执行,而非残留的 root 连接。
    """
    session = load_session(profile)
    client = PanelClient(session.panel_url, session.cookies, session.insecure)
    resp = client.post_json("api/v2/settings/ssh", local_conn_body(user, pwd, port))
    if resp.status == 401:
        raise CliError("会话已失效,请重新运行 login")
    try:
        payload = resp.json()
    except ValueError:
        raise CliError(f"配置面板本地 SSH 连接失败: HTTP {resp.status}")
    code = payload.get("code", -1)
    if code == 401:
        raise CliError("会话已失效,请重新运行 login")
    if code != 200:
        raise CliError(
            f"配置面板本地 SSH 连接失败 (code={code}): {payload.get('message', '未知错误')}"
        )


@dataclass
class LocalConnInfo:
    addr: str = ""
    user: str = ""


def local_conn_info(profile: str) -> LocalConnInfo:
    """读取面板本地 SSH 连接概要(GET api/v2/settings/ssh/conn,addr 为空即未配置)。"""
    session = load_session(profile)
    client = PanelClient(session.panel_url, session.cookies, session.insecure)
    resp = client.get("api/v2/settings/ssh/conn")
    try:
        payload = resp.json()
    except ValueError as exc:
        raise CliError(f"解析本地连接信息失败: {exc}") from exc
    data = payload.get("data") or {}
    return LocalConnInfo(addr=data.get("addr", "") or "", user=data.get("user", "") or "")


# ---------------------------------------------------------------------------
# 命令组装与哨兵
# ---------------------------------------------------------------------------


def quote_posix(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def compose_command(
    cwd: Optional[str], command: str, token: str, sudo: bool
) -> str:
    """组装发送给 shell 的完整命令:预校验 + cwd 拼接 + 子 shell 包裹 + 退出码哨兵。

    用户命令放进 `( )` 子 shell:`exit`/`exec` 类命令只结束子 shell,
    外层 shell 继续执行哨兵 echo,退出码经 `$?` 透传。
    sudo 为 true 时命令包进 `sudo -n -H bash -c`,cd 一并移入 root 环境内执行。
    """
    command = command.strip()
    if not command:
        raise CliError("命令不能为空")
    if "\n" in command or "\r" in command:
        raise CliError("命令不能包含换行符(exec 仅支持单行命令)")
    byte_len = len(command.encode("utf-8"))
    if byte_len > MAX_COMMAND_LEN:
        print(
            f"警告: 命令长度 {byte_len} 字节,超过 {MAX_COMMAND_LEN} 字节,"
            "可能超出 URL 长度限制导致失败",
            file=sys.stderr,
        )
    cd_prefix = None
    if cwd and cwd.strip():
        directory = cwd.strip()
        if "\n" in directory or "\r" in directory:
            raise CliError("--cwd 目录不能包含换行符")
        cd_prefix = f"cd -- {quote_posix(directory)} && "
    if sudo:
        inner = (cd_prefix or "") + command
        full = f"( sudo -n -H bash -c {quote_posix(inner)} )"
    else:
        if cd_prefix:
            full = f"{cd_prefix}( {command} )"
        else:
            full = f"( {command} )"
    full += f"; echo {SENTINEL_PREFIX}{token}_$?"
    return full


def new_token() -> str:
    return os.urandom(4).hex()


def find_sentinel(normalized: str, token: str) -> Optional[int]:
    """在归一化流(剥离 ANSI 转义与 \\r\\n)上匹配哨兵,单次命中即完成。

    捕获组即命令退出码;回显中的字面量 `$?` 不参与匹配。
    """
    needle = f"{SENTINEL_PREFIX}{token}_"
    search_from = 0
    while True:
        found = normalized.find(needle, search_from)
        if found < 0:
            return None
        start = found + len(needle)
        end = start
        while end < len(normalized) and end - start < 3 and "0" <= normalized[end] <= "9":
            end += 1
        if end > start:
            try:
                return int(normalized[start:end])
            except ValueError:
                pass
        search_from = start


# ---------------------------------------------------------------------------
# 输出清洗
# ---------------------------------------------------------------------------

ANSI_CSI = re.compile(r"\x1b\[[\x30-\x3f]*[\x20-\x2f]*[\x40-\x7e]")
ANSI_OSC_BEL = re.compile(r"\x1b\].*?(?:\x07|\x1b\\)", re.DOTALL)
ANSI_TWO = re.compile(r"\x1b[\(\)\*\+&#%][\x20-\x7e]")
ANSI_ESC = re.compile(r"\x1b[@-_]")


def strip_ansi(input: str) -> str:
    """剥离 ANSI/ECMA-48 转义序列(CSI/OSC/双字符序列)与 BEL。"""
    out = []
    i = 0
    n = len(input)
    while i < n:
        ch = input[i]
        if ch != "\x1b":
            if ch != "\x07":
                out.append(ch)
            i += 1
            continue
        nxt = input[i + 1] if i + 1 < n else None
        if nxt == "[":
            m = ANSI_CSI.match(input, i)
            i = m.end() if m else n
        elif nxt == "]":
            m = ANSI_OSC_BEL.match(input, i)
            i = m.end() if m else n
        elif nxt in "()*+&#%":
            m = ANSI_TWO.match(input, i)
            i = m.end() if m else i + 3
        elif nxt is not None:
            i += 2
        else:
            i += 1
    return "".join(out)


def normalize_chunk(chunk: str) -> str:
    """归一化用于哨兵匹配:剥离 ANSI 转义并移除所有 \\r 与 \\n(规避 PTY 折行拆断)。"""
    stripped = strip_ansi(chunk)
    return stripped.replace("\r", "").replace("\n", "")


def trim_blank_edges(lines: list[str]) -> list[str]:
    start, end = 0, len(lines)
    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1
    return lines[start:end]


def clean_output(raw: str, token: str) -> str:
    """默认输出清洗:以哨兵行为锚,剥离 ANSI、PTY 回显输入行、哨兵行及尾部提示符残余。"""
    text = strip_ansi(raw).replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    token_full = f"{SENTINEL_PREFIX}{token}"

    start = 0
    for i in range(len(lines) - 1, -1, -1):
        if f"{token_full}_$" in lines[i]:
            start = i + 1
            break
    else:
        for i in range(len(lines) - 1, -1, -1):
            if lines[i].startswith(" clear &&"):
                start = i + 1
                break

    end = len(lines)
    for i in range(len(lines) - 1, start - 1, -1):
        if token_full in lines[i]:
            end = i
            break

    kept = [line for line in lines[start:end] if token_full not in line]
    return "\n".join(trim_blank_edges(kept))


def tail_lines(text: str, n: int) -> tuple[str, bool]:
    """仅保留最后 n 行,返回 (文本, 是否发生截断)。"""
    lines = text.split("\n")
    if len(lines) <= n:
        return text, False
    if n == 0:
        return "", True
    return "\n".join(lines[len(lines) - n:]), True


def to_json(result: ExecResult) -> str:
    """序列化单行 JSON 结果(字段顺序固定,便于脚本/AI 消费)。"""
    payload = {
        "exit_code": result.exit_code,
        "duration_ms": result.duration_ms,
        "truncated": result.truncated,
        "error": result.error,
        "output": result.output,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


# ---------------------------------------------------------------------------
# WS 会话
# ---------------------------------------------------------------------------


def friendly_ws_error(exc: Exception) -> CliError:
    if isinstance(exc, WsHandshakeError):
        status = exc.status
        if status == 401:
            return CliError("会话已失效,请重新运行 login")
        if status == 403:
            return CliError("面板拒绝访问 (HTTP 403): 可能是绑定域名限制或演示模式")
        if status == 404:
            return CliError("终端接口不存在 (HTTP 404): 请确认面板版本 (需 v2.2.0+)")
        if status == 407:
            return CliError("面板正在升级或维护中 (HTTP 407),请稍后重试")
        # 会话失效可能被中间件包装为 HTTP 200 + {"code":401}
        if _body_code_401(exc.body):
            return CliError("会话已失效,请重新运行 login")
        return CliError(f"WS 握手失败: HTTP {status}")
    return CliError(f"WS 连接失败: {exc}")


def _body_code_401(body: bytes) -> bool:
    if not body:
        return False
    try:
        payload = json.loads(body.decode("utf-8", errors="replace"))
    except ValueError:
        return False
    return isinstance(payload, dict) and payload.get("code") == 401


def connect_ws(session: Session, url: str) -> WebSocket:
    try:
        return WebSocket.connect(
            url, headers=build_handshake(session), insecure=session.insecure
        )
    except WsHandshakeError as exc:
        raise friendly_ws_error(exc) from exc
    except CliError as exc:
        message = str(exc)
        if "TLS" in message:
            return CliError(
                f"{message}(自签名证书可运行 login 时加 --insecure)"
            )
        raise
    except OSError as exc:
        raise friendly_ws_error(exc) from exc


class _EndState:
    __slots__ = ("kind", "value")

    def __init__(self, kind: str, value=None) -> None:
        self.kind = kind  # completed / server_closed / disconnected / timed_out
        self.value = value


def run_ws_session(
    session: Session, composed: str, token: str, opts: ExecOptions
):
    """执行 WS 会话,返回 (结束状态, 输出缓冲, 是否截断, 耗时毫秒)。"""
    url = ws_url(session, composed, opts.cols, opts.rows)
    start = time.monotonic()
    deadline = start + max(1, opts.timeout)
    ws = connect_ws(session, url)

    buf = bytearray()
    truncated = False
    match_buf = ""
    end_state: _EndState | None = None

    try:
        while True:
            now = time.monotonic()
            if now >= deadline:
                end_state = _EndState("timed_out")
                break
            ws.settimeout(max(deadline - now, 0.001))
            try:
                message = ws.recv()
            except TimeoutError:
                end_state = _EndState("timed_out")
                break
            except WsConnectionClosed:
                end_state = _EndState("disconnected", "连接被意外关闭")
                break

            if message.is_close:
                if message.close_code is None and not message.close_reason:
                    end_state = _EndState("disconnected", "连接被服务端关闭(未携带原因)")
                else:
                    end_state = _EndState("server_closed", message.close_reason)
                break
            if message.is_text:
                text = message.data.decode("utf-8", errors="replace")
                chunk = _decode_cmd_message(text)
            elif message.is_binary:
                chunk = message.data.decode("utf-8", errors="replace")
            else:
                continue
            if not chunk:
                continue

            if len(buf) + len(chunk) > MAX_OUTPUT_BYTES:
                room = MAX_OUTPUT_BYTES - len(buf)
                buf.extend(chunk.encode("utf-8")[:room])
                truncated = True
            else:
                buf.extend(chunk.encode("utf-8"))
            match_buf += normalize_chunk(chunk)
            if len(match_buf) > MATCH_WINDOW_BYTES + 64 * 1024:
                match_buf = match_buf[len(match_buf) - MATCH_WINDOW_BYTES:]
            code = find_sentinel(match_buf, token)
            if code is not None:
                end_state = _EndState("completed", code)
                break
    finally:
        duration_ms = int((time.monotonic() - start) * 1000)
        ws.close()

    if end_state is None:
        end_state = _EndState("timed_out")
    return end_state, bytes(buf), truncated, duration_ms


def _decode_cmd_message(text: str) -> str:
    """解析面板终端消息:{"type":"cmd","data":"<base64>"} → 输出片段。"""
    try:
        msg = json.loads(text)
        if isinstance(msg, dict) and msg.get("type") == "cmd":
            data = msg.get("data", "")
            try:
                return base64.b64decode(data).decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                return ""
        return ""
    except ValueError:
        return text


# ---------------------------------------------------------------------------
# 结果输出
# ---------------------------------------------------------------------------


def build_result(
    opts: ExecOptions,
    raw: bytes,
    token: str,
    truncated: bool,
    error: Optional[str],
    exit_code: int,
    duration_ms: int,
) -> tuple[ExecResult, bool]:
    raw_text = raw.decode("utf-8", errors="replace")
    cleaned = raw_text if opts.raw else clean_output(raw_text, token)
    if opts.tail is not None:
        output, tail_truncated = tail_lines(cleaned, opts.tail)
    else:
        output, tail_truncated = cleaned, False
    result = ExecResult(
        exit_code=exit_code,
        duration_ms=duration_ms,
        truncated=truncated,
        error=error,
        output=output,
    )
    return result, tail_truncated


def finish(result: ExecResult, opts: ExecOptions, tail_truncated: bool) -> int:
    """输出最终结果:--json 打印单行 JSON,否则打印(清洗/tail 后的)文本。"""
    if opts.json:
        print(to_json(result))
        return result.exit_code
    if tail_truncated:
        print(
            "[提示] 输出超长,仅显示末尾部分(完整内容可在远端重定向到文件后用 tail 查看)",
            file=sys.stderr,
        )
    if result.truncated:
        print(
            f"[提示] 输出超过 {MAX_OUTPUT_BYTES // 1024 // 1024} MB 上限,已截断"
            "(建议远端重定向到文件后用 tail 查看)",
            file=sys.stderr,
        )
    if result.output:
        print(result.output)
    return result.exit_code


def fail_with_output(result: ExecResult, opts: ExecOptions, err: CliError) -> int:
    """出错时的兜底:--json 打印带 error 的 JSON 并返回 1,否则交给上层报错。"""
    if opts.json:
        print(to_json(result))
        return 1
    if result.output:
        print(result.output)
    raise err


def run(profile: str, opts: ExecOptions) -> int:
    """入口:返回进程退出码。"""
    try:
        return run_inner(profile, opts)
    except CliError as exc:
        if opts.json:
            payload = {
                "exit_code": 1,
                "duration_ms": 0,
                "truncated": False,
                "error": str(exc),
                "output": "",
            }
            print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
            return 1
        raise


def run_inner(profile: str, opts: ExecOptions) -> int:
    result, tail_truncated, err = execute(profile, opts)
    if err is None:
        return finish(result, opts, tail_truncated)
    return fail_with_output(result, opts, err)


def run_capture(profile: str, command: str, sudo: bool, timeout: int) -> tuple[int, str]:
    """供 doctor 等内部调用:执行单行命令,返回 (退出码, 清洗后输出),不打印。"""
    opts = ExecOptions(command=command, timeout=timeout, sudo=sudo)
    result, _, err = execute(profile, opts)
    if err is not None:
        raise err
    return result.exit_code, result.output


def execute(profile: str, opts: ExecOptions) -> tuple[ExecResult, bool, Optional[CliError]]:
    """执行主流程:预校验 + SSH 连接对齐 + WS 会话,构建结果(不打印)。"""
    if opts.timeout <= 0:
        raise CliError("--timeout 必须为正整数秒")
    if opts.cols <= 0 or opts.rows <= 0:
        raise CliError("--cols/--rows 必须为正整数")
    session = load_session(profile)
    token = new_token()
    composed = compose_command(opts.cwd, opts.command, token, opts.sudo)

    if opts.sync_ssh:
        user, pwd = linux_ssh_creds()
        if not user or not pwd:
            raise CliError(
                "--sync-ssh 需要在 .env(或环境变量)中配置 LINUX_SSH_USER / LINUX_SSH_PWD"
            )
        ensure_local_conn(profile, user, pwd, opts.ssh_port)
    else:
        # 自动对齐:.env 声明了 SSH 用户时,不允许残留 root 等其他连接,
        # 连接缺失或用户不一致时自动覆盖写入(exec 默认以普通用户执行,root 用 --sudo)
        user, pwd = linux_ssh_creds()
        if user and pwd:
            try:
                info = local_conn_info(profile)
                needs_sync = not info.addr or (info.user and info.user != user)
            except CliError:
                needs_sync = False
            if needs_sync:
                ensure_local_conn(profile, user, pwd, opts.ssh_port)

    state, buf, truncated, duration_ms = run_ws_session(session, composed, token, opts)
    if state.kind == "completed":
        code = max(0, min(255, int(state.value)))
        result, tail_truncated = build_result(
            opts, buf, token, truncated, None, code, duration_ms
        )
        return result, tail_truncated, None
    if state.kind == "timed_out":
        result, tail_truncated = build_result(
            opts,
            buf,
            token,
            truncated,
            f"超时({opts.timeout}s)未见完成哨兵,已返回收集到的输出",
            124,
            duration_ms,
        )
        return result, tail_truncated, None
    if state.kind == "server_closed":
        err = explain_server_close(profile, str(state.value))
        result, _ = build_result(
            opts, buf, token, truncated, str(err), 1, duration_ms
        )
        return result, False, err
    # disconnected
    what = str(state.value)
    err = CliError(f"{what}(shell 可能已退出或面板 agent 重启),命令可能未执行完成")
    result, _ = build_result(opts, buf, token, truncated, str(err), 1, duration_ms)
    return result, False, err


def explain_server_close(profile: str, reason: str) -> CliError:
    """服务端以 Close 帧终止终端会话后的错误归因。

    面板 agent 发送的 Close 帧 payload 不含 RFC 状态码,因此结合
    settings/ssh/conn 探测给出准确归因。
    """
    reason = reason.strip()
    readable = bool(reason) and reason != "Protocol violation"
    try:
        addr = local_conn_info(profile).addr
    except CliError:
        addr = None
    if addr is not None and not addr:
        suffix = f": {reason}" if readable else ""
        return CliError(
            f"面板本地 SSH 连接未配置{suffix}\n"
            "提示: 运行 exec 时加 --sync-ssh(需在 .env 中配置 LINUX_SSH_USER / LINUX_SSH_PWD),"
            "或到面板『设置 → 终端 → SSH 本地连接』手动配置"
        )
    if readable:
        if "demo server" in reason:
            return CliError(f"面板处于演示模式,终端功能被禁用: {reason}")
        return CliError(f"服务端终止了终端连接: {reason}")
    return CliError(
        "服务端终止了终端连接: 本地 SSH 连接验证可能失败(凭据已变更或连接不可达)\n"
        "提示: 可用 --sync-ssh 配合 LINUX_SSH_USER / LINUX_SSH_PWD 覆盖更新"
    )
