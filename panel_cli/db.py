"""MySQL / MariaDB 数据库与用户管理。

对应 Rust 版 db.rs。
"""

from __future__ import annotations

import base64
import builtins

from .client import PanelClient
from .errors import CliError
from .session import load_session
from . import apps


def check_code(resp: dict) -> None:
    if resp.get("code") != 200:
        raise CliError(
            f"面板返回错误 (code={resp.get('code', -1)}): {resp.get('message', '')}"
        )


def _parse(resp, context: str) -> dict:
    try:
        return resp.json()
    except ValueError as exc:
        raise CliError(f"{context}: {exc}") from exc


def client_for(profile: str) -> PanelClient:
    session = load_session(profile)
    return PanelClient(session.panel_url, session.cookies, session.insecure)


def find_mysql_db(client: PanelClient, instance: str, name: str) -> dict:
    body = {
        "info": name,
        "database": instance,
        "page": 1,
        "pageSize": 100,
        "orderBy": "name",
        "order": "ascending",
    }
    resp = _parse(client.post_json("api/v2/databases/search", body), "查询数据库失败")
    check_code(resp)
    items = (resp.get("data") or {}).get("items") or []
    for d in items:
        if d.get("name", "") == name:
            return d
    raise CliError(f"在实例 {instance} 上未找到数据库 {name}")


def list(profile: str, db_type: str, instance: str | None) -> None:
    client = client_for(profile)
    db_type = db_type or "mysql"
    instance = apps.resolve_instance_name(client, instance or db_type)

    if db_type in ("mysql", "mariadb"):
        body = {
            "info": "",
            "database": instance,
            "page": 1,
            "pageSize": 100,
            "orderBy": "name",
            "order": "ascending",
        }
        resp = _parse(client.post_json("api/v2/databases/search", body), "查询数据库失败")
        check_code(resp)
        items = (resp.get("data") or {}).get("items") or []
        total = (resp.get("data") or {}).get("total", 0)
        print(f"{'ID':<8} {'名称':<24} {'用户':<16} {'权限':<12} 备注")
        for d in items:
            remark = d.get("description", "") or "-"
            print(
                f"{d.get('id', ''):<8} {d.get('name', ''):<24} "
                f"{d.get('username', ''):<16} {d.get('permission', ''):<12} {remark}"
            )
        print(f"共 {total} 个数据库(实例 {instance})")
    else:
        raise CliError(f"不支持的数据库类型: {db_type}(Redis 请使用 redis 命令)")


def create(
    profile: str,
    name: str,
    user: str | None,
    password: str | None,
    instance: str,
    fmt: str,
    permission: str,
    remark: str | None,
) -> None:
    client = client_for(profile)
    instance = apps.resolve_instance_name(client, instance)
    encoded = (
        base64.b64encode(password.encode("utf-8")).decode("ascii") if password else ""
    )
    body = {
        "name": name,
        "from": "local",
        "database": instance,
        "format": fmt,
        "username": user or "",
        "password": encoded,
        "permission": permission,
        "description": remark or "",
    }
    resp = _parse(client.post_json("api/v2/databases", body), "创建数据库失败")
    check_code(resp)
    print(f"数据库已创建: {name}(实例 {instance})")


def delete(profile: str, name: str, instance: str, db_type: str, force: bool) -> None:
    client = client_for(profile)
    instance = apps.resolve_instance_name(client, instance)
    db = find_mysql_db(client, instance, name)

    check_body = {"id": db.get("id", 0), "type": db_type, "database": instance}
    resp = _parse(
        client.post_json("api/v2/databases/del/check", check_body), "检查数据库占用失败"
    )
    check_code(resp)
    resources = resp.get("data") or []
    if resources and not force:
        names = ", ".join(
            f"{r.get('name', '')}[{r.get('type', '')}]" for r in resources
        )
        raise CliError(
            f"数据库 {name} 正被以下资源使用: {names};如确认删除请加 --force"
        )

    body = {
        "id": db.get("id", 0),
        "type": db_type,
        "database": instance,
        "forceDelete": force,
        "deleteBackup": False,
    }
    resp = _parse(client.post_json("api/v2/databases/del", body), "删除数据库失败")
    check_code(resp)
    print(f"数据库已删除: {name}")


def users(profile: str, instance: str) -> None:
    client = client_for(profile)
    instance = apps.resolve_instance_name(client, instance)
    resp = _parse(
        client.post_json("api/v2/databases/users/search", {"database": instance}),
        "查询用户失败",
    )
    check_code(resp)
    data = resp.get("data")
    if isinstance(data, dict) and "items" in data:
        items = data.get("items") or []
    elif isinstance(data, builtins.list):
        items = data
    else:
        items = []
    print(f"{'用户':<20} {'主机':<16} 备注")
    for u in items:
        remark = u.get("description", "") or "-"
        print(f"{u.get('username', ''):<20} {u.get('host', ''):<16} {remark}")
    print(f"共 {len(items)} 个用户")


def user_add(
    profile: str, username: str, password: str, instance: str, host: str,
    dbs: list[str], remark: str | None,
) -> None:
    client = client_for(profile)
    instance = apps.resolve_instance_name(client, instance)
    body = {
        "database": instance,
        "username": username,
        "password": base64.b64encode(password.encode("utf-8")).decode("ascii"),
        "host": host,
        "description": remark or "",
        "dbs": dbs,
    }
    resp = _parse(client.post_json("api/v2/databases/users", body), "创建用户失败")
    check_code(resp)
    print(f"用户已创建: {username}@{host}(实例 {instance})")


def user_del(profile: str, username: str, instance: str, host: str) -> None:
    client = client_for(profile)
    instance = apps.resolve_instance_name(client, instance)
    body = {"database": instance, "username": username, "host": host}
    resp = _parse(client.post_json("api/v2/databases/users/del", body), "删除用户失败")
    check_code(resp)
    print(f"用户已删除: {username}@{host}(实例 {instance})")


def user_passwd(profile: str, username: str, password: str, instance: str, host: str) -> None:
    client = client_for(profile)
    instance = apps.resolve_instance_name(client, instance)
    body = {
        "database": instance,
        "username": username,
        "host": host,
        "password": base64.b64encode(password.encode("utf-8")).decode("ascii"),
    }
    resp = _parse(
        client.post_json("api/v2/databases/users/password", body), "修改用户密码失败"
    )
    check_code(resp)
    print(f"用户密码已修改: {username}@{host}(实例 {instance})")


def root_passwd(profile: str, instance: str, password: str) -> None:
    from . import apps

    apps.password(profile, instance, password)
