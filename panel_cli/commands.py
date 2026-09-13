"""会话状态、API 透传调用(status / info / logout / api)。

对应 Rust 版 commands.rs。
"""

from __future__ import annotations

import json
import os
import sys

from .client import PanelClient
from .errors import CliError
from .session import delete_session, load_session, session_path


def status(profile: str) -> None:
    session = load_session(profile)
    client = PanelClient(session.panel_url, session.cookies, session.insecure)
    resp = client.get("api/v2/core/auth/current")
    try:
        payload = resp.json()
    except ValueError as exc:
        raise CliError(f"解析面板响应失败: {exc}") from exc
    code = payload.get("code", -1)
    if code == 200:
        data = payload.get("data") or {}
        name = data.get("name") or session.username
        role = data.get("role") or ""
        panel = (
            f"{session.panel_name} ({session.panel_url})"
            if session.panel_name
            else session.panel_url
        )
        print(f"已登录: {name} 角色: {role} 面板: {panel}")
        try:
            version, edition = panel_version(profile)
            print(f"面板版本: {version} ({edition})")
        except CliError:
            pass
    else:
        message = payload.get("message", "")
        print(f"会话已失效 (code={code}, message={message})")
        print("请重新运行 login 命令登录。")


def panel_version(profile: str) -> tuple[str, str]:
    session = load_session(profile)
    client = PanelClient(session.panel_url, session.cookies, session.insecure)
    resp = client.post("api/v2/core/settings/search/base")
    try:
        payload = resp.json()
    except ValueError as exc:
        raise CliError(f"解析面板版本响应失败: {exc}") from exc
    if payload.get("code") != 200:
        raise CliError(
            f"查询面板版本失败 (code={payload.get('code', -1)}): {payload.get('message', '')}"
        )
    data = payload.get("data") or {}
    return data.get("systemVersion") or "未知", data.get("edition") or "未知"


def info(profile: str) -> None:
    session = load_session(profile)
    print(f"profile     : {session.profile}")
    print(f"面板地址    : {session.panel_url}")
    entrance = session.entrance or "(未启用)"
    print(f"安全入口    : {entrance}")
    print(f"用户名      : {session.username}")
    print(f"面板名称    : {session.panel_name}")
    print(f"登录时间    : {session.logged_in_at}")
    names = ", ".join(c.name for c in session.cookies)
    print(f"凭据 cookies: {names} (值已隐藏)")
    print(f"凭据文件    : {session_path(profile)}")


def logout(profile: str) -> None:
    session = load_session(profile)
    client = PanelClient(session.panel_url, session.cookies, session.insecure)
    try:
        client.post("api/v2/core/auth/logout")
    except CliError:
        pass
    delete_session(profile)
    print("已退出登录,本地保存的凭据已清除。")


def normalize_api_path(path: str) -> str:
    p = path.lstrip("/")
    if p.startswith("api/v2/"):
        return p
    return f"api/v2/{p}"


def api_call(profile: str, method: str, path: str, body: str | None) -> None:
    session = load_session(profile)
    client = PanelClient(session.panel_url, session.cookies, session.insecure)
    full = normalize_api_path(path)

    json_body = None
    if body and body.strip():
        try:
            json_body = json.loads(body)
        except ValueError as exc:
            raise CliError(f"--body 不是合法的 JSON: {exc}") from exc

    if method == "GET":
        resp = client.get(full)
    elif method == "POST":
        resp = client.post_json(full, json_body)
    elif method == "PUT":
        resp = client.put_json(full, json_body)
    else:
        resp = client.delete(full)

    if os.environ.get("PANEL_CLI_DEBUG"):
        print(f"[debug] {method} {full} -> {resp.status}", file=sys.stderr)
        for cookie in client.cookies():
            print(f"[debug] cookie {cookie.name}={cookie.value[:12]}", file=sys.stderr)

    content_type = next(
        (value.lower() for name, value in resp.headers if name.lower() == "content-type"),
        "",
    )
    if resp.status == 404:
        raise CliError(
            f"面板未注册该路由: {method} /{full} (HTTP 404);"
            "可用 `api list --filter <关键字>` 或 `api describe <METHOD> <PATH>` 核对"
        )
    if content_type.startswith("text/html"):
        raise CliError(
            f"{method} /{full} 返回了 HTML 页面而不是 API 响应:"
            "通常是该路径不支持该方法(HTTP 405),或安全入口/登录态异常;"
            "可用 `api describe <METHOD> <PATH>` 核对方法与路径"
        )

    text = resp.text()
    try:
        print(json.dumps(json.loads(text), ensure_ascii=False, indent=2))
    except ValueError:
        print(text)
    if not resp.ok:
        raise CliError(f"{method} /{full} 返回 HTTP {resp.status}")
