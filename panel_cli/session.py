"""登录会话的本地持久化(~/.config/1panel-cli/<profile>.json,权限 0600)。

对应 Rust 版 session.rs。
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .errors import CliError


@dataclass
class CookieData:
    name: str
    value: str


@dataclass
class Session:
    profile: str
    panel_url: str
    entrance: str
    username: str
    panel_name: str
    logged_in_at: str
    cookies: list[CookieData] = field(default_factory=list)
    insecure: bool = False


def config_dir() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    directory = base / "1panel-cli"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def session_path(profile: str) -> Path:
    if not profile or "/" in profile or "\\" in profile:
        raise CliError(f"非法的 profile 名称: {profile!r}")
    return config_dir() / f"{profile}.json"


def save_session(session: Session) -> Path:
    path = session_path(session.profile)
    payload = json.dumps(asdict(session), ensure_ascii=False, indent=2)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    except OSError as exc:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise CliError(f"保存会话失败: {exc}") from exc
    return path


def load_session(profile: str) -> Session:
    path = session_path(profile)
    try:
        data = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CliError(f"未找到已保存的会话: {path}") from exc
    try:
        raw = json.loads(data)
        return Session(
            profile=raw["profile"],
            panel_url=raw["panel_url"],
            entrance=raw.get("entrance", ""),
            username=raw["username"],
            panel_name=raw.get("panel_name", ""),
            logged_in_at=raw["logged_in_at"],
            insecure=bool(raw.get("insecure", False)),
            cookies=[CookieData(c["name"], c["value"]) for c in raw.get("cookies", [])],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise CliError(f"会话文件损坏: {path} ({exc})") from exc


def delete_session(profile: str) -> bool:
    path = session_path(profile)
    if path.exists():
        path.unlink()
        return True
    return False
