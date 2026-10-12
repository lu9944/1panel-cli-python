"""init 命令:从本机 1Panel 自动探测并生成 .env 配置。

信息来源:
1. `sudo 1pctl user-info` — 面板地址(含协议/端口/安全入口)、用户名、初始密码;
2. 面板本地 SQLite DB(默认 /opt/1panel/db/{core,agent}.db)——
   当 user-info 因已登录过而掩码密码时,读取设置表中的 Password/EncryptKey
   并用 AES 解密,无需用户手填。DB 文件通常全局可读(0644),无需 sudo。

对应 1Panel v2.3.2 源码(技能目录内 `source/`):
- source/core/cmd/server/cmd/user-info.go — user-info 输出与掩码逻辑(有成功登录记录即掩码)
- source/core/utils/encrypt/encrypt.go    — StringDecrypt: base64(iv + AES-CBC(PKCS7))
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .client import PanelClient
from .crypto import string_decrypt
from .errors import CliError

CORE_DB = Path(os.environ.get("PANEL_CLI_CORE_DB") or "/opt/1panel/db/core.db")
AGENT_DB = Path(os.environ.get("PANEL_CLI_AGENT_DB") or "/opt/1panel/db/agent.db")
MASKED_PASSWORDS = {"", "********", "**********"}
PROBE_TIMEOUT = 3.0


# ---------------------------------------------------------------------------
# 1pctl user-info
# ---------------------------------------------------------------------------


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", text)


def _value_after_label(line: str) -> str:
    for sep in (": ", "："):
        if sep in line:
            return line.split(sep, 1)[1].strip()
    return line.strip()


def parse_user_info(text: str) -> dict:
    """解析 1panel user-info 输出。

    输出固定为四行(语言不同仅标签不同): 地址 / 用户 / 密码 / 修改密码提示。
    以含 `://` 的行为锚点,取其后前两个非空非边框行。
    """
    info = {"address": "", "username": "", "password": ""}
    lines = [_strip_ansi(line).rstrip() for line in text.splitlines()]
    anchor = None
    for index, line in enumerate(lines):
        if "://" in line:
            match = re.search(r"https?://\S+", line)
            if match:
                info["address"] = match.group(0).strip()
                anchor = index
                break
    if anchor is None:
        return info
    values: list[str] = []
    for line in lines[anchor + 1 :]:
        stripped = line.strip()
        if not stripped or set(stripped) <= set("=-"):
            continue
        values.append(stripped)
        if len(values) == 2:
            break
    if values:
        info["username"] = _value_after_label(values[0])
    if len(values) >= 2:
        info["password"] = _value_after_label(values[1])
    return info


def run_user_info() -> tuple[dict | None, str | None]:
    """执行 `sudo 1pctl user-info`,返回 (info, 错误描述)。"""
    pctl = shutil.which("1pctl")
    if not pctl:
        return None, "未找到 1pctl 命令(init 需在 1Panel 所在主机执行)"
    attempts: list[tuple[list[str], bool]] = [(["sudo", "-n", pctl, "user-info"], True)]
    if sys.stdin.isatty():
        # sudo 需要密码时,交互式重试(提示会输出到终端)
        attempts.append((["sudo", pctl, "user-info"], False))
    last_error = "执行 sudo 1pctl user-info 失败"
    for cmd, capture_all in attempts:
        try:
            if capture_all:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
                output = (proc.stdout or "") + (proc.stderr or "")
            else:
                proc = subprocess.run(cmd, stdout=subprocess.PIPE, text=True, timeout=180)
                output = proc.stdout or ""
        except FileNotFoundError:
            return None, "未找到 sudo 命令"
        except subprocess.TimeoutExpired:
            last_error = "执行 sudo 1pctl user-info 超时"
            continue
        if proc.returncode != 0:
            tail = [line for line in _strip_ansi(output).splitlines() if line.strip()]
            last_error = tail[-1].strip() if tail else f"sudo 1pctl 返回 {proc.returncode}"
            continue
        parsed = parse_user_info(output)
        if parsed.get("address"):
            return parsed, None
        last_error = "无法解析 user-info 输出"
    return None, last_error


# ---------------------------------------------------------------------------
# 面板本地数据库
# ---------------------------------------------------------------------------


_CORE_KEYS = (
    "UserName",
    "Password",
    "EncryptKey",
    "ServerPort",
    "SSL",
    "SecurityEntrance",
    "BindDomain",
)
_AGENT_KEYS = ("SystemIP",)


def _read_settings(core_db: Path, agent_db: Path, read_only: bool = True) -> dict:
    mode = "?mode=ro" if read_only else ""
    settings: dict = {}
    # 注意:同类 key 在不同库中可能同名(如 agent.db 也有 EncryptKey),
    # 必须按库分别查询,不能混在一起后互相覆盖。
    for path, keys in ((core_db, _CORE_KEYS), (agent_db, _AGENT_KEYS)):
        uri = f"file:{path}{mode}"
        connection = sqlite3.connect(uri, uri=True, timeout=5)
        try:
            placeholders = ",".join("?" for _ in keys)
            rows = connection.execute(
                f"select key, value from settings where key in ({placeholders})",
                keys,
            ).fetchall()
            for key, value in rows:
                settings[key] = value
        finally:
            connection.close()
    return settings


def _sudo_snapshot(core_db: Path, agent_db: Path) -> tuple[Path, Path] | None:
    """DB 不可读时用 sudo -n 复制一份到私有临时目录(副本含 WAL 可正常打开)。"""
    tmp_dir = Path(tempfile.mkdtemp(prefix="1panel-cli-db-"))
    os.chmod(tmp_dir, 0o700)
    try:
        for src in (core_db, agent_db):
            for suffix in ("", "-wal", "-shm"):
                source = Path(str(src) + suffix)
                if not source.exists():
                    continue
                target = tmp_dir / (src.name + suffix)
                proc = subprocess.run(
                    ["sudo", "-n", "cp", "-f", str(source), str(target)],
                    capture_output=True,
                    timeout=30,
                )
                if proc.returncode != 0 and suffix == "":
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                    return None
                if suffix != "" and proc.returncode != 0:
                    pass  # WAL/SHM 可能不存在或复制失败,尝试直接用 db 副本
        return tmp_dir / core_db.name, tmp_dir / agent_db.name
    except (OSError, subprocess.TimeoutExpired):
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return None


def read_panel_settings(core_db: Path | None = None, agent_db: Path | None = None) -> dict:
    core_db = Path(core_db or CORE_DB)
    agent_db = Path(agent_db or AGENT_DB)
    if not core_db.exists():
        raise CliError(f"未找到面板数据库: {core_db}(init 需在 1Panel 所在主机执行)")
    try:
        return _read_settings(core_db, agent_db, read_only=True)
    except (sqlite3.Error, OSError) as exc:
        snapshot = _sudo_snapshot(core_db, agent_db)
        if snapshot is None:
            raise CliError(f"读取面板数据库失败: {exc}") from exc
        try:
            return _read_settings(snapshot[0], snapshot[1], read_only=False)
        except (sqlite3.Error, OSError) as inner:
            raise CliError(f"读取面板数据库副本失败: {inner}") from inner
        finally:
            shutil.rmtree(snapshot[0].parent, ignore_errors=True)


def address_from_settings(settings: dict) -> str:
    protocol = "https" if settings.get("SSL") == "Enable" else "http"
    host = settings.get("BindDomain") or settings.get("SystemIP") or "$LOCAL_IP"
    port = settings.get("ServerPort") or "9999"
    entrance = settings.get("SecurityEntrance") or ""
    suffix = f"/{entrance}" if entrance else ""
    return f"{protocol}://{host}:{port}{suffix}"


# ---------------------------------------------------------------------------
# 地址探测
# ---------------------------------------------------------------------------


def primary_ip() -> str | None:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))  # 不实际发包,仅取路由源地址
            return sock.getsockname()[0]
        finally:
            sock.close()
    except OSError:
        return None


def split_address(address: str) -> tuple[str, str, str, str]:
    """拆出 (scheme, host, port_suffix, path);非法地址抛 CliError。"""
    match = re.match(r"^(https?)://([^/:]+)(:\d+)?(/.*)?$", address.strip())
    if not match:
        raise CliError(f"无法解析面板地址: {address}")
    scheme, host = match.group(1), match.group(2)
    port = match.group(3) or ""
    path = (match.group(4) or "").rstrip("/")
    return scheme, host, port, path


def probe_panel(base_url: str) -> str | None:
    """探测面板可用性,返回 'ok' / 'insecure'(仅自签跳过校验可达) / None。"""
    for insecure, result in ((False, "ok"), (True, "insecure")):
        try:
            client = PanelClient(base_url, None, insecure, timeout=PROBE_TIMEOUT)
            response = client.get("api/v2/core/auth/setting")
            if response.status == 200:
                try:
                    code = response.json().get("code")
                except ValueError:
                    code = None
                if code == 200:
                    return result
        except CliError:
            continue
    return None


def resolve_address(
    address: str,
    explicit_host: str | None = None,
    probe=None,
) -> tuple[str, bool, str | None]:
    """把 `$LOCAL_IP` 解析为可达主机。

    返回 (最终地址, 是否需要 PANEL_INSECURE, 警告)。
    """
    probe = probe or probe_panel
    scheme, host, port, path = split_address(address)
    if explicit_host:
        host = explicit_host
    elif host == "$LOCAL_IP":
        candidates = ["127.0.0.1"]
        detected = primary_ip()
        if detected and detected not in candidates:
            candidates.append(detected)
        last_error = None
        for candidate in candidates:
            base = f"{scheme}://{candidate}{port}"
            result = probe(base)
            if result:
                path_suffix = path
                final = f"{scheme}://{candidate}{port}{path_suffix}"
                return final, result == "insecure", None
            last_error = candidate
        final = f"{scheme}://{candidates[0]}{port}{path}"
        return final, False, f"面板地址不可达(已尝试 {', '.join(candidates)}),已写入 {candidates[0]}"
    # 已有具体主机:探测连通性与自签情况(探测失败仅警告,不阻断)
    base = f"{scheme}://{host}{port}"
    result = probe(base)
    if result == "insecure":
        return f"{scheme}://{host}{port}{path}", True, None
    if result is None:
        return (
            f"{scheme}://{host}{port}{path}",
            False,
            f"面板地址 {base} 暂不可达,已按原样写入配置",
        )
    return f"{scheme}://{host}{port}{path}", False, None


# ---------------------------------------------------------------------------
# .env 生成
# ---------------------------------------------------------------------------


def render_env(url: str, username: str, password: str, insecure: bool) -> str:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    lines = [
        f"# 由 1panel-cli init 生成 ({stamp})",
        "# 面板账号信息(含密码,请勿提交到版本库)",
        "",
        f"PANEL_URL={url}",
        f"PANEL_USERNAME={username}",
        f"PANEL_PASSWORD={password}",
    ]
    if insecure:
        lines.append("PANEL_INSECURE=true")
    lines += [""]
    return "\n".join(lines)


def write_env(path: Path, content: str, force: bool = False) -> None:
    if path.exists() and not force:
        raise CliError(f"配置文件已存在: {path}(如需覆盖请加 --force)")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def run_init(
    output: str = ".env",
    force: bool = False,
    host: str | None = None,
    print_only: bool = False,
    core_db: Path | None = None,
    agent_db: Path | None = None,
) -> None:
    # 1) user-info(需要 sudo;失败时回退 DB)
    info, info_error = run_user_info()
    if info_error:
        print(f"[提示] {info_error};改用本机面板数据库读取", file=sys.stderr)

    settings: dict = {}
    need_db = not info or not info.get("username") or info.get("password", "") in MASKED_PASSWORDS
    if need_db:
        settings = read_panel_settings(core_db, agent_db)

    address = (info or {}).get("address") or address_from_settings(settings)
    username = (info or {}).get("username") or settings.get("UserName") or ""
    password = (info or {}).get("password") or ""
    if password in MASKED_PASSWORDS:
        encrypted = settings.get("Password") or ""
        encrypt_key = settings.get("EncryptKey") or ""
        if not encrypted or not encrypt_key:
            raise CliError(
                "无法获取面板密码:user-info 已掩码且数据库缺少 Password/EncryptKey;"
                "可用 `sudo 1pctl update password` 重置后重试,或直接编辑 .env"
            )
        password = string_decrypt(encrypted, encrypt_key)

    if not address or not username or not password:
        raise CliError("探测失败:面板地址/用户名/密码不完整,请检查 1Panel 是否安装在本机")
    if password in MASKED_PASSWORDS:
        raise CliError("探测失败:未能获得面板密码")

    # 2) 地址解析($LOCAL_IP → 可达主机;自签 HTTPS → PANEL_INSECURE)
    final_url, insecure, warning = resolve_address(address, explicit_host=host)
    if warning:
        print(f"[警告] {warning}", file=sys.stderr)

    content = render_env(final_url, username, password, insecure)

    # 3) 输出
    if print_only:
        print(content, end="")
    else:
        path = Path(output)
        write_env(path, content, force=force)
        print(f"已生成配置: {path} (权限 600)")
        print(f"面板地址: {final_url}")
        print(f"用户名:   {username}")
        print(f"密码:     {'*' * len(password)}(已写入,共 {len(password)} 位)")
        if insecure:
            print("已检测到自签名 HTTPS 证书,PANEL_INSECURE=true 已写入")
        print()
        print("下一步:")
        print(f"  {Path(sys.argv[0] or '1panel-cli').name} --env {path} doctor")
