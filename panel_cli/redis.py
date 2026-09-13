"""Redis 实例管理:列表/状态/配置/密码。

对应 Rust 版 redis.rs。
"""

from __future__ import annotations

from .client import PanelClient
from .errors import CliError
from .session import load_session


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


def list(profile: str) -> None:
    client = client_for(profile)
    body = {"page": 1, "pageSize": 100, "name": "", "type": "", "tags": []}
    resp = _parse(
        client.post_json("api/v2/apps/installed/search", body), "查询 Redis 实例失败"
    )
    check_code(resp)
    items = (resp.get("data") or {}).get("items") or []
    print(f"{'ID':<8} {'名称':<16} {'状态':<12} {'版本':<10}")
    for it in items:
        if it.get("appKey") != "redis":
            continue
        print(
            f"{it.get('id', 0):<8} {it.get('name', ''):<16} "
            f"{it.get('status', ''):<12} {it.get('version', ''):<10}"
        )


def fmt_bytes(value: str) -> str:
    try:
        size = int(value)
    except (TypeError, ValueError):
        size = 0
    if size >= 1 << 30:
        return f"{size / (1 << 30):.2f} GB"
    if size >= 1 << 20:
        return f"{size / (1 << 20):.2f} MB"
    if size >= 1 << 10:
        return f"{size / (1 << 10):.2f} KB"
    return f"{size} B"


def info(profile: str, name: str) -> None:
    client = client_for(profile)
    body = {"type": "redis", "name": name}
    resp = _parse(client.post_json("api/v2/databases/redis/status", body), "查询 Redis 状态失败")
    check_code(resp)
    st = resp.get("data") or {}

    def field(key: str) -> str:
        value = st.get(key, "")
        return value if isinstance(value, str) else str(value)

    print(f"Redis 实例: {name}")
    print(f"端口: {field('tcp_port')}")
    print(f"运行天数: {field('uptime_in_days')}")
    print(f"当前连接数: {field('connected_clients')}")
    print(f"内存使用: {fmt_bytes(field('used_memory'))}")
    print(f"累计命令数: {field('total_commands_processed')}")

    resp = _parse(client.post_json("api/v2/databases/redis/conf", body), "查询 Redis 配置失败")
    check_code(resp)
    conf = resp.get("data") or {}
    print("\n配置:")

    def conf_str(key: str) -> str:
        value = conf.get(key, "")
        if isinstance(value, str):
            return value
        return "" if value is None else str(value)

    port = conf.get("port", 0)
    if not isinstance(port, int):
        try:
            port = int(port)
        except (TypeError, ValueError):
            port = 0
    print(f"  端口: {port}")
    print(f"  容器: {conf.get('containerName', '') or ''}")
    print(f"  timeout: {conf_str('timeout')}")
    print(f"  maxclients: {conf_str('maxclients')}")
    print(f"  maxmemory: {conf_str('maxmemory')}")
    print(f"  requirepass: {conf_str('requirepass')}")


def config(profile: str, name: str, set_values: list[tuple[str, str]]) -> None:
    client = client_for(profile)
    body = {"type": "redis", "name": name}
    resp = _parse(client.post_json("api/v2/databases/redis/conf", body), "查询 Redis 配置失败")
    check_code(resp)
    conf = resp.get("data") or {}

    def conf_str(key: str) -> str:
        value = conf.get(key, "")
        return value if isinstance(value, str) else ("" if value is None else str(value))

    if not set_values:
        print(f"Redis 配置({name}):")
        print(f"  timeout    = {conf_str('timeout')}")
        print(f"  maxclients = {conf_str('maxclients')}")
        print(f"  maxmemory  = {conf_str('maxmemory')}")
        print(f"  requirepass= {conf_str('requirepass')}")
        return

    update = {
        "database": name,
        "dbType": "redis",
        "timeout": conf_str("timeout"),
        "maxclients": conf_str("maxclients"),
        "maxmemory": conf_str("maxmemory"),
    }
    for key, value in set_values:
        if key in ("timeout", "maxclients", "maxmemory"):
            update[key] = value
        else:
            raise CliError(
                f"不支持的配置项 {key},仅支持 timeout / maxclients / maxmemory"
            )
    resp = _parse(
        client.post_json("api/v2/databases/redis/conf/update", update),
        "更新 Redis 配置失败",
    )
    check_code(resp)
    print(f"Redis 配置已更新({name}):")
    for key, value in set_values:
        print(f"  {key} = {value}")


def passwd(profile: str, name: str, password: str) -> None:
    from . import apps

    apps.password(profile, name, password)
