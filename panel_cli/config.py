"""面板配置加载:.env 文件 + 环境变量 + 命令行覆盖。

对应 Rust 版 config.rs。
"""

from __future__ import annotations

import os
import sys
import urllib.parse
from pathlib import Path

from .errors import CliError

ENV_URL = "PANEL_URL"
ENV_ENTRANCE = "PANEL_ENTRANCE"
ENV_USERNAME = "PANEL_USERNAME"
ENV_PASSWORD = "PANEL_PASSWORD"
ENV_MFA = "PANEL_MFA_CODE"
ENV_CAPTCHA = "PANEL_CAPTCHA"
ENV_CAPTCHA_ID = "PANEL_CAPTCHA_ID"
ENV_LANGUAGE = "PANEL_LANGUAGE"
ENV_INSECURE = "PANEL_INSECURE"
ENV_NODE = "PANEL_NODE"
ENV_LINUX_SSH_USER = "LINUX_SSH_USER"
ENV_LINUX_SSH_PWD = "LINUX_SSH_PWD"


def set_node_override(node: str | None) -> None:
    if node and node.strip():
        os.environ[ENV_NODE] = node


def encode_current_node(value: str) -> str:
    """CurrentNode 请求头的编码(与前端 encodeURIComponent 语义一致)。

    服务端 core/init/router/proxy.go 会对该头做 url.QueryUnescape,
    因此编码后必须能解码还原为原始节点名。
    """
    return urllib.parse.quote(value, safe="!*'()")


def linux_ssh_creds() -> tuple[str | None, str | None]:
    """读取面板所在主机的 SSH 凭据(exec --sync-ssh 自动配置本地连接时使用)。"""
    return env_get(ENV_LINUX_SSH_USER), env_get(ENV_LINUX_SSH_PWD)


def env_get(key: str) -> str | None:
    value = os.environ.get(key)
    if value is not None and value.strip():
        return value
    return None


def env_flag(key: str) -> bool | None:
    value = env_get(key)
    if value is None:
        return None
    return value.strip().lower() in ("1", "true", "yes", "on")


def parse_env_file(path: Path) -> dict[str, str]:
    """解析 dotenv 格式文件(支持注释、export 前缀与单双引号)。"""
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CliError(f"解析 .env 文件失败: {path}: {exc}") from exc
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        else:
            inline = value.find(" #")
            if inline >= 0:
                value = value[:inline].rstrip()
        values[key] = value
    return values


def env_search_paths() -> list[Path]:
    """依次查找:当前目录、入口脚本所在目录(如 bin/)、包所在项目根目录。"""
    candidates: list[Path] = []
    seen: set[Path] = set()
    for raw in (
        Path(".env"),
        Path(sys.argv[0] or "").resolve().parent / ".env",
        Path(__file__).resolve().parent.parent / ".env",
    ):
        if str(raw) not in seen:
            seen.add(str(raw))
            candidates.append(raw)
    return candidates


def load_env_file(env_path: str | None) -> Path | None:
    """加载 .env 到进程环境(不覆盖已存在的变量),返回实际加载的路径。"""
    if env_path:
        path = Path(env_path).expanduser()
        if not path.exists():
            raise CliError(f"指定的 .env 文件不存在: {path}")
        for key, value in parse_env_file(path).items():
            os.environ.setdefault(key, value)
        return path
    for candidate in env_search_paths():
        if candidate.exists():
            for key, value in parse_env_file(candidate).items():
                os.environ.setdefault(key, value)
            return candidate
    return None


class ConfigOverrides:
    def __init__(
        self,
        url: str | None = None,
        entrance: str | None = None,
        username: str | None = None,
        password: str | None = None,
        mfa_code: str | None = None,
        captcha: str | None = None,
        captcha_id: str | None = None,
        language: str | None = None,
        insecure: bool | None = None,
        linux_ssh_user: str | None = None,
        linux_ssh_pwd: str | None = None,
    ) -> None:
        self.url = url
        self.entrance = entrance
        self.username = username
        self.password = password
        self.mfa_code = mfa_code
        self.captcha = captcha
        self.captcha_id = captcha_id
        self.language = language
        self.insecure = insecure
        self.linux_ssh_user = linux_ssh_user
        self.linux_ssh_pwd = linux_ssh_pwd


def split_url(raw: str) -> tuple[str, str]:
    """把 `http://host:port/entrance` 拆成 (base_url, entrance)。"""
    raw = raw.strip().rstrip("/")
    if not raw:
        raise CliError("面板地址不能为空")
    url = raw
    entrance = ""
    scheme_end = raw.find("://")
    after_scheme = raw[scheme_end + 3:] if scheme_end >= 0 else raw
    slash = after_scheme.find("/")
    if slash >= 0:
        path = after_scheme[slash:].strip("/")
        if path:
            entrance = path
        if scheme_end >= 0:
            url = raw[: scheme_end + 3] + after_scheme[:slash]
        else:
            url = after_scheme[:slash]
    if url.startswith("http://") or url.startswith("https://"):
        return url, entrance
    return f"http://{url}", entrance


class PanelConfig:
    def __init__(
        self,
        url: str,
        entrance: str,
        username: str,
        password: str,
        mfa_code: str | None,
        captcha: str | None,
        captcha_id: str | None,
        language: str,
        insecure: bool,
        linux_ssh_user: str | None,
        linux_ssh_pwd: str | None,
    ) -> None:
        self.url = url
        self.entrance = entrance
        self.username = username
        self.password = password
        self.mfa_code = mfa_code
        self.captcha = captcha
        self.captcha_id = captcha_id
        self.language = language
        self.insecure = insecure
        self.linux_ssh_user = linux_ssh_user
        self.linux_ssh_pwd = linux_ssh_pwd

    @classmethod
    def from_env(cls, overrides: ConfigOverrides | None = None) -> "PanelConfig":
        overrides = overrides or ConfigOverrides()
        url = overrides.url or env_get(ENV_URL)
        if not url:
            raise CliError(
                "未配置面板地址: 请通过 --url 参数、环境变量 PANEL_URL 或 .env 文件提供"
            )
        base_url, url_entrance = split_url(url)

        entrance = overrides.entrance or env_get(ENV_ENTRANCE) or url_entrance or ""
        username = overrides.username or env_get(ENV_USERNAME)
        if not username:
            raise CliError(
                "未配置面板用户名: 请通过 --user 参数、环境变量 PANEL_USERNAME 或 .env 文件提供"
            )
        password = overrides.password or env_get(ENV_PASSWORD)
        if not password:
            raise CliError(
                "未配置面板密码: 请通过 --password 参数、环境变量 PANEL_PASSWORD 或 .env 文件提供"
            )
        insecure = overrides.insecure if overrides.insecure is not None else (env_flag(ENV_INSECURE) or False)

        return cls(
            url=base_url,
            entrance=entrance,
            username=username,
            password=password,
            mfa_code=overrides.mfa_code or env_get(ENV_MFA),
            captcha=overrides.captcha or env_get(ENV_CAPTCHA),
            captcha_id=overrides.captcha_id or env_get(ENV_CAPTCHA_ID),
            language=overrides.language or env_get(ENV_LANGUAGE) or "zh",
            insecure=insecure,
            linux_ssh_user=overrides.linux_ssh_user or env_get(ENV_LINUX_SSH_USER),
            linux_ssh_pwd=overrides.linux_ssh_pwd or env_get(ENV_LINUX_SSH_PWD),
        )
