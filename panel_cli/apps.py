"""应用商店:列表/安装/详情/配置/密码。

对应 Rust 版 apps.rs。
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass, field

from .client import PanelClient
from .errors import CliError
from .session import load_session

DB_APP_KEYS = {"mysql", "mariadb", "redis", "mongodb", "postgresql", "sqlite", "clickhouse"}


@dataclass
class AppItem:
    name: str = ""
    key: str = ""
    installed: bool = False
    tags: list[str] = field(default_factory=list)
    type: str = ""


@dataclass
class AppsQuery:
    page: int = 1
    page_size: int = 50
    name: str | None = None
    type: str | None = None
    tag: str | None = None
    installed: bool = False
    all: bool = False


@dataclass
class InstallOptions:
    version: str | None = None
    params: list[tuple[str, str]] = field(default_factory=list)
    wait: bool = False
    timeout: int = 600
    pull_image: bool = True  # 离线包场景用 --no-pull 跳过联网拉取,直接用本地镜像


@dataclass
class SyncLocalOptions:
    wait: bool = False
    timeout: int = 60


@dataclass
class ConfigOptions:
    set: list[tuple[str, str]] = field(default_factory=list)
    show_compose: bool = False


def _client_for(profile: str) -> tuple:
    session = load_session(profile)
    return PanelClient(session.panel_url, session.cookies, session.insecure), session


def _parse_json(resp, context: str) -> dict:
    try:
        return resp.json()
    except ValueError as exc:
        raise CliError(f"{context}: {exc}") from exc


def list_apps(profile: str, q: AppsQuery) -> None:
    client, _ = _client_for(profile)

    tags = [q.tag] if q.tag else []
    page = max(1, q.page)
    page_size = max(1, min(1000, q.page_size))
    fetch_all = q.all or q.installed

    items: list[AppItem] = []
    total = 0
    while True:
        body = {
            "page": page,
            "pageSize": page_size,
            "name": q.name or "",
            "tags": tags,
            "type": q.type or "",
            "recommend": False,
            "resource": "",
            "showCurrentArch": True,
        }
        resp = _parse_json(client.post_json("api/v2/apps/search", body), "解析响应失败")
        if resp.get("code") != 200:
            raise CliError(
                f"面板返回错误 (code={resp.get('code', -1)}): {resp.get('message', '')}"
            )
        data = resp.get("data") or {}
        total = int(data.get("total", 0) or 0)
        batch = _deserialize_items(data.get("items"))
        fetched = len(items) + len(batch)
        items.extend(batch)
        if not fetch_all or fetched >= total or page * page_size >= total:
            break
        page += 1

    if q.installed:
        items = [it for it in items if it.installed]
        total = len(items)

    print_table(items, total)


def _deserialize_items(raw) -> list[AppItem]:
    items: list[AppItem] = []
    for entry in raw or []:
        items.append(
            AppItem(
                name=entry.get("name", "") or "",
                key=entry.get("key", "") or "",
                installed=bool(entry.get("installed", False)),
                tags=list(entry.get("tags", []) or []),
                type=entry.get("type", "") or "",
            )
        )
    return items


def tags(profile: str) -> None:
    client, _ = _client_for(profile)
    resp = _parse_json(client.get("api/v2/apps/tags"), "解析响应失败")
    if resp.get("code") != 200:
        raise CliError(
            f"面板返回错误 (code={resp.get('code', -1)}): {resp.get('message', '')}"
        )
    for t in resp.get("data") or []:
        print(f"{t.get('key', ''):<16} {t.get('name', '')}")


def print_table(items: list[AppItem], total: int) -> None:
    rows = [
        [
            it.type,
            it.name,
            it.key,
            "是" if it.installed else "否",
            ", ".join(it.tags),
        ]
        for it in items
    ]
    headers = ["类型", "名称", "key", "已安装", "标签"]
    # 与 Rust 版一致:列宽仅由数据行决定
    widths = [
        max([len(str(r[i])) for r in rows] + [0])
        for i in range(5)
    ]
    sep = "+".join("-" * (w + 2) for w in widths)
    header_line = "  ".join(
        f"{headers[i]:<{widths[i]}}" for i in range(5)
    )
    print(header_line)
    print(sep)
    for r in rows:
        print("  ".join(f"{str(r[i]):<{widths[i]}}" for i in range(5)))
    print(sep)
    print(f"共 {total} 个应用(展示 {len(rows)} 条)")


def check_code(resp: dict) -> None:
    if resp.get("code") != 200:
        raise CliError(
            f"面板返回错误 (code={resp.get('code', -1)}): {resp.get('message', '')}"
        )


def find_installed(client: PanelClient, name: str) -> dict:
    target = normalize_app_key(name)
    # 服务端按 name 模糊过滤:历史/规范两种写法(如 localmysql 与 mysql)只查一种可能查空,
    # 因此依次尝试原写法与规范化写法,再做客户端匹配。
    queries = [name]
    if target and target != name:
        queries.append(target)
    items: list[dict] = []
    for query in queries:
        body = {"page": 1, "pageSize": 100, "name": query, "type": "", "tags": []}
        resp = _parse_json(
            client.post_json("api/v2/apps/installed/search", body), "查询已安装应用失败"
        )
        check_code(resp)
        items = (resp.get("data") or {}).get("items") or []
        if items:
            break
    for it in items:
        candidates = (it.get("name"), it.get("appKey"), it.get("appName"))
        if any(normalize_app_key(str(c or "")) == target for c in candidates):
            return it
    raise CliError(f"未安装应用 {name},可先运行 apps install {name} 进行安装")


def resolve_instance_name(client: PanelClient, instance: str) -> str:
    """解析数据库/Redis 实例名:兼容本地应用的 `local` 前缀写法。

    实例名即应用安装名。离线/本地商店的应用名可能是 `localmysql`/`mysql` 两种形态,
    传入其一自动匹配实际安装名;未安装或查询失败时原样返回,不影响远程实例。
    """
    name = (instance or "").strip()
    if not name:
        return name
    try:
        body = {"page": 1, "pageSize": 200, "name": "", "type": "", "tags": []}
        resp = _parse_json(
            client.post_json("api/v2/apps/installed/search", body), "查询已安装应用失败"
        )
        check_code(resp)
        items = (resp.get("data") or {}).get("items") or []
    except CliError:
        return name
    names = {str(it.get("name") or "") for it in items if it.get("name")}
    if name in names:
        return name
    candidates = []
    if name.lower().startswith("local"):
        candidates.append(name[5:])
    else:
        candidates.append("local" + name)
    lower_map = {n.lower(): n for n in names}
    for cand in candidates:
        hit = lower_map.get(cand.lower())
        if hit:
            return hit
    hit = lower_map.get(name.lower())
    return hit or name


def get_install_info(client: PanelClient, install_id: int) -> dict:
    resp = _parse_json(
        client.get(f"api/v2/apps/installed/info/{install_id}"), "查询应用详情失败"
    )
    check_code(resp)
    return resp.get("data") or {}


def get_conn_info(client: PanelClient, app_key: str, name: str) -> dict:
    resp = _parse_json(
        client.post_json("api/v2/apps/installed/conninfo", {"type": app_key, "name": name}),
        "查询连接信息失败",
    )
    check_code(resp)
    return resp.get("data") or {}


def info(profile: str, name: str) -> None:
    client, session = _client_for(profile)
    installed = find_installed(client, name)
    detail = get_install_info(client, installed.get("id", 0))

    print(f"应用:   {detail.get('name', '')}")
    print(f"key:    {detail.get('appKey', '')}")
    print(f"版本:   {detail.get('version', '')}")
    print(f"状态:   {detail.get('status', '')}")
    if detail.get("container"):
        print(f"容器:   {detail.get('container')}")
    if detail.get("composePath"):
        print(f"compose: {detail.get('composePath')}")

    app_key = normalize_app_key(detail.get("appKey", ""))
    raw_key = detail.get("appKey", "") or ""
    if app_key in DB_APP_KEYS:
        # 本地应用(appKey 带 local 前缀)用原始 key 查 conninfo 才能命中安装记录;
        # 服务端对 local key 不填 root 密码,再从安装详情的 env 回退补齐
        conn = {}
        for candidate in (raw_key, app_key):
            if not candidate:
                continue
            info_conn = get_conn_info(client, candidate, detail.get("name") or name)
            if info_conn.get("containerName") or int(info_conn.get("port", 0) or 0) > 0:
                conn = info_conn
                break
        env_map = detail.get("env") or {}
        if not conn.get("password"):
            for env_field in ("PANEL_DB_ROOT_PASSWORD", "PANEL_REDIS_ROOT_PASSWORD"):
                if env_map.get(env_field):
                    conn["password"] = env_map[env_field]
                    break
        if conn.get("containerName") or int(conn.get("port", 0) or 0) > 0:
            host = session.panel_url.split("://", 1)[-1].split(":", 1)[0] or "127.0.0.1"
            username = conn.get("username", "") or ""
            if not username and app_key in ("mysql", "mariadb"):
                username = "root"
            print()
            print("连接信息:")
            print(f"  主机:  {host}")
            print(f"  端口:  {conn.get('port', '')}")
            print(f"  用户:  {username}")
            print(f"  密码:  {conn.get('password', '')}")
            print(f"  容器:  {conn.get('containerName', '')}")

    env = detail.get("env") or {}
    if env:
        print()
        print("环境变量:")
        for key in sorted(env):
            print(f"  {key} = {fmt_value(env[key])}")


def fmt_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(value, ensure_ascii=False)


def normalize_app_key(raw: str) -> str:
    """归一化应用 key/名称:去掉本地应用前缀(localmysql -> mysql),忽略大小写。"""
    value = (raw or "").strip().lower()
    if value.startswith("local"):
        value = value[5:]
    return value


def _get_app_once(client: PanelClient, key: str) -> dict | None:
    try:
        resp = _parse_json(client.get(f"api/v2/apps/{key}"), "解析应用信息失败")
    except CliError:
        return None
    if resp.get("code") != 200:
        return None
    app = resp.get("data") or {}
    return app if app.get("id") else None


def get_app(client: PanelClient, key: str) -> dict:
    """查询应用定义,自动兼容本地应用与远程应用两种 key 写法。

    1Panel 离线/内置应用商店中的应用 key 带 `local` 前缀(如 localopenresty),
    而官方商店为 openresty;这里两种写法都会尝试。
    """
    candidates = [key]
    if key.lower().startswith("local"):
        candidates.append(key[5:])
    else:
        candidates.append("local" + key)
    for candidate in candidates:
        app = _get_app_once(client, candidate)
        if app:
            return app
    raise CliError(f"未找到应用: {key}")


def get_app_detail(client: PanelClient, app_id: int, version: str) -> dict:
    resp = _parse_json(
        client.get(f"api/v2/apps/detail/{app_id}/{version}/app"), "解析应用版本信息失败"
    )
    check_code(resp)
    detail = resp.get("data") or {}
    if not detail.get("id"):
        raise CliError("未找到应用版本信息")
    return detail


def parse_value(raw: str):
    stripped = raw.strip()
    try:
        return int(stripped)
    except ValueError:
        pass
    try:
        return float(stripped)
    except ValueError:
        pass
    return raw


def new_uuid() -> str:
    return str(uuid.UUID(bytes=os.urandom(16), version=4))


def install(profile: str, name: str, opts: InstallOptions) -> None:
    client, _ = _client_for(profile)
    app = get_app(client, name)
    install_name = app.get("key") or name
    if install_name != name:
        print(f"应用 {name} 对应商店 key: {install_name}")
    if app.get("installed"):
        raise CliError(f"应用 {name} 已安装")
    versions = app.get("versions") or []
    if not versions:
        raise CliError(f"应用 {name} 没有可用版本")
    version = opts.version or versions[0]

    detail = get_app_detail(client, app["id"], version)

    params: dict = {}
    for form_field in (detail.get("params") or {}).get("formFields") or []:
        env_key = form_field.get("envKey", "")
        if env_key:
            params[env_key] = form_field.get("default")
    for key, value in opts.params:
        params[key] = parse_value(value)

    task_id = new_uuid()
    body = {
        "appDetailId": detail["id"],
        "params": params,
        "name": install_name,
        "taskID": task_id,
        "advanced": True,
        "allowPort": True,
        "pullImage": opts.pull_image,
        "cpuQuota": 0,
        "memoryLimit": 0,
        "memoryUnit": "M",
        "containerName": "",
        "editCompose": False,
        "dockerCompose": "",
        "gpuConfig": False,
        "specifyIP": "",
    }

    print(f"开始安装 {name} (版本 {version}) ...")
    resp = _parse_json(client.post_json("api/v2/apps/install", body), "解析安装响应失败")
    if resp.get("code") != 200 and "名称已存在" in str(resp.get("message", "")):
        raise CliError(f"应用 {name} 已安装(名称已存在)")
    check_code(resp)

    print(f"安装任务已提交, taskID={task_id}")
    if opts.wait:
        wait_task(client, task_id, opts.timeout)
    else:
        print('可通过面板"已安装应用"页面查看安装进度')


def sync_local(profile: str, opts: SyncLocalOptions) -> None:
    """触发面板同步本地应用商店(离线安装包场景)。

    对应 `POST /api/v2/apps/sync/local`:面板扫描 `resource/apps/local/` 注册本地
    应用(来源 resource=local;key 为应用自身规范 key,如 `mysql` / `redis` / `openresty`,
    历史版本可能带 `local` 前缀,CLI 两种写法均兼容)。
    """
    client, _ = _client_for(profile)
    task_id = new_uuid()
    resp = _parse_json(
        client.post_json("api/v2/apps/sync/local", {"taskID": task_id}),
        "同步本地应用失败",
    )
    check_code(resp)
    print("已触发本地应用同步")
    if opts.wait:
        wait_task(client, task_id, opts.timeout, action="同步")
    else:
        print("可通过 apps list 查看本地应用(如 mysql / redis / openresty)")


def wait_task(client: PanelClient, task_id: str, timeout: int, action: str = "安装") -> None:
    timeout = timeout if timeout > 0 else 600
    deadline = time.monotonic() + timeout
    while True:
        body = {
            "page": 1,
            "pageSize": 10,
            "type": "",
            "status": "",
            "taskID": task_id,
        }
        resp = _parse_json(client.post_json("api/v2/logs/tasks/search", body), "查询任务状态失败")
        items = (resp.get("data") or {}).get("items") or []
        if items:
            task = items[0]
            status = task.get("status", "")
            step = task.get("currentStep", "")
            if status == "Success":
                print(f"\n{action}成功!")
                return
            if status in ("Failed", "Error"):
                err = task.get("errorMsg") or "未知错误"
                print(f"\n{action}失败: {err}")
                raise CliError(f"{action}失败: {err}")
            print(f"\r{action}进行中... 状态: {status} {step:<40}", end="")
            sys.stdout.flush()
        if time.monotonic() >= deadline:
            print(f"\n等待{action}超时(超过 {timeout} 秒),请到面板查看进度")
            raise CliError(f"等待{action}超时")
        time.sleep(2)


def config(profile: str, name: str, opts: ConfigOptions) -> None:
    client, _ = _client_for(profile)
    installed = find_installed(client, name)
    install_id = installed.get("id", 0)

    if not opts.set:
        resp = _parse_json(
            client.get(f"api/v2/apps/installed/params/{install_id}"), "读取应用配置失败"
        )
        check_code(resp)
        cfg = resp.get("data") or {}
        params = cfg.get("params") or []
        print(f"{name} 配置(共 {len(params)} 项):")
        for p in params:
            edit = "是" if p.get("edit") else "否"
            label_zh = p.get("labelZh", "") or ""
            label = f" # {label_zh}" if label_zh else ""
            print(
                f"  {p.get('key', '')} = {fmt_value(p.get('value'))}{label}"
                f"  [类型:{p.get('type', '')} 可编辑:{edit}]"
            )
        if cfg.get("containerName"):
            print(f"容器: {cfg.get('containerName')}")
        print("端口自动分配: {}".format("开启" if cfg.get("allowPort") else "关闭"))
        if opts.show_compose and cfg.get("rawCompose"):
            print("\n--- docker-compose ---")
            print(cfg.get("rawCompose"))
        return

    params = {key: parse_value(value) for key, value in opts.set}
    body = {"installId": install_id, "params": params, "advanced": False}
    resp = _parse_json(
        client.post_json("api/v2/apps/installed/params/update", body),
        "解析修改配置响应失败",
    )
    check_code(resp)
    print(f"{name} 配置已提交,容器正在重建以应用新配置...")
    print(f"稍后可运行 apps info {name} 查看生效情况")


def password(profile: str, name: str, value: str) -> None:
    if not value:
        raise CliError("密码不能为空")
    client, _ = _client_for(profile)
    installed = find_installed(client, name)
    app_key = normalize_app_key(installed.get("appKey", ""))
    install_name = installed.get("name") or name

    encoded = base64.b64encode(value.encode("utf-8")).decode("ascii")
    if app_key in ("mysql", "mariadb"):
        body = {
            "id": 0,
            "from": "local",
            "type": app_key,
            "database": install_name,
            "value": encoded,
        }
        resp = _parse_json(
            client.post_json("api/v2/databases/change/password", body),
            "解析修改密码响应失败",
        )
        check_code(resp)
        print(f"{name} ({app_key}) root 密码已修改")
    elif app_key == "redis":
        resp = _parse_json(
            client.post_json(
                "api/v2/databases/redis/password", {"database": install_name, "value": encoded}
            ),
            "解析修改密码响应失败",
        )
        check_code(resp)
        print(f"{name} (redis) 密码已修改")
    else:
        raise CliError(f"暂不支持修改 {app_key} 的密码,仅支持 mysql / mariadb / redis")
    print(f"新密码: {value}")
    print(f"可通过 apps info {name} 查看最新的连接信息")
