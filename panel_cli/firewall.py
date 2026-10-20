"""防火墙管理:自动适配 1Panel v2.3.2/v2.4.0+ 统一规则接口、旧统一接口与 v2.2.x 旧接口。

接口演进:
- v2.2.x(legacy):`/firewall/search|port|ip|forward|batch` 旧门面接口;
- 中间版本(checked):`/firewall/rules*` + 创建前 `/rules/check` 规则检查(签名 checkFlag);
- v2.3.2(v231):`/firewall/rules*` 统一规则接口,写操作(规则创建/删除、转发、
  Docker 端口策略)全部改为异步任务,接口返回 taskID 后由 CLI 轮询
  `/logs/tasks/search` 并汇报结果;Docker 端口策略请求体改为 `policies` 结构。
- v2.4.0(v240):规则删除不再接受 `uuids`,改为提交完整的 `targets`
  (`scope` + `instanceKey` + `observed`);新增 IPv6 开关/分族操作与规则备份,
  移除 `docker/sync` 与规则同步接口。

`api_kind()` 在运行时探测当前面板属于哪一种,并据此选择调用路径。
"""

from __future__ import annotations

import builtins
import json
from dataclasses import dataclass

from .apps import wait_task
from .client import PanelClient
from .errors import CliError
from .session import load_session

BASE = "api/v2/hosts/firewall"

API_LEGACY = "legacy"
API_CHECKED = "checked"
API_V231 = "v231"
API_V240 = "v240"

# v2.3.2+ 防火墙写操作(创建/删除规则、转发、Docker 策略)为异步任务,轮询超时(秒)
TASK_TIMEOUT = 60


def check_code(resp: dict) -> None:
    if resp.get("code") != 200:
        raise CliError(
            f"面板返回错误 (code={resp.get('code', -1)}): {resp.get('message', '')}"
        )


def response_json(resp, context: str) -> dict:
    status = resp.status
    text = resp.text()
    try:
        value = json.loads(text)
    except ValueError as exc:
        raise CliError(f"{context}: HTTP {status}, 响应不是 JSON: {exc}; {text}") from exc
    if not resp.ok:
        message = value.get("message", "") if isinstance(value, dict) else ""
        raise CliError(f"{context}: HTTP {status}: {message or text}")
    check_code(value)
    return value


def post_json(client: PanelClient, path: str, body: dict, context: str) -> dict:
    return response_json(client.post_json(path, body), context)


def client_for(profile: str) -> PanelClient:
    session = load_session(profile)
    return PanelClient(session.panel_url, session.cookies, session.insecure)


# ---------------------------------------------------------------------------
# 接口类型探测
# ---------------------------------------------------------------------------


def supports_unified_api(client: PanelClient) -> bool:
    """是否提供新版统一接口(GET /firewall/settings)。"""
    try:
        resp = client.get(f"{BASE}/settings")
    except CliError:
        return False
    if not resp.ok:
        return False
    try:
        value = resp.json()
    except ValueError:
        return False
    return value.get("code") == 200


def supports_rule_check(client: PanelClient) -> bool:
    """是否提供规则检查接口 POST /firewall/rules/check(仅中间版本)。"""
    try:
        resp = client.post_json(f"{BASE}/rules/check", {"items": []})
    except CliError:
        return False
    return resp.status != 404


def supports_rule_backups(client: PanelClient) -> bool:
    """是否提供规则备份接口 GET /firewall/rules/backups(1Panel v2.4.0+)。"""
    try:
        resp = client.get(f"{BASE}/rules/backups")
    except CliError:
        return False
    return resp.status != 404


def api_kind(client: PanelClient) -> str:
    """返回当前面板的防火墙接口类型:legacy / checked / v231 / v240。"""
    if not supports_unified_api(client):
        return API_LEGACY
    if supports_rule_backups(client):
        return API_V240
    return API_CHECKED if supports_rule_check(client) else API_V231


def async_api(kind: str) -> bool:
    """v2.3.2+ 防火墙写操作均返回 taskID。"""
    return kind in (API_V231, API_V240)


def unified_api_required(client: PanelClient) -> None:
    if not supports_unified_api(client):
        raise CliError(
            "当前面板不支持统一防火墙接口(未检测到 /hosts/firewall/settings);"
            "Docker 端口守护需要 1Panel v2.3.x 及以上版本"
        )


def run_async_task(client: PanelClient, data: dict, label: str) -> bool:
    """v2.3.2 异步写操作:有 taskID 时轮询任务并汇报;返回是否等待过任务。"""
    task_id = (data or {}).get("taskID") or ""
    if not task_id:
        return False
    print(f"{label} 已提交,等待执行(taskID={task_id}) ...")
    wait_task(client, task_id, TASK_TIMEOUT, action=label)
    return True


# ---------------------------------------------------------------------------
# 状态 / 启停
# ---------------------------------------------------------------------------


def firewall_base(client: PanelClient) -> dict:
    return post_json(client, f"{BASE}/base", {"name": "base"}, "查询防火墙状态失败")


def firewall_provider(client: PanelClient) -> str:
    resp = firewall_base(client)
    data = resp.get("data") or {}
    provider = data.get("backend") or data.get("name") or ""
    provider = provider.lower()
    if provider in ("iptables", "nftables", "firewalld", "ufw"):
        return provider
    raise CliError(f"无法识别当前防火墙后端: {provider}")


def status(profile: str) -> None:
    client = client_for(profile)
    resp = firewall_base(client)
    d = resp.get("data") or {}
    print(f"防火墙: {d.get('name', '-') or '-'}")
    if d.get("backend"):
        print(f"后端: {d.get('backend')}")
    print("状态: {}".format("运行中" if d.get("isActive") is True else "未运行"))
    print(f"版本: {d.get('version', '-') or '-'}")
    ping_status = d.get("pingStatus")
    if ping_status == "Enable":
        ping = "已禁止"
    elif ping_status == "Disable":
        ping = "允许"
    else:
        ping = "未知"
    print(f"Ping: {ping}")
    print("初始化: {}".format("是" if d.get("isInit") is True else "否"))
    if "ipv6Enabled" in d:
        print("IPv6: {}".format("已启用" if d.get("ipv6Enabled") is True else "未启用"))
    if d.get("message"):
        print(f"消息: {d.get('message')}")
    if supports_unified_api(client):
        resp = response_json(client.get(f"{BASE}/settings"), "查询防火墙后端设置失败")
        s = resp.get("data") or {}

        def selected(group: dict) -> str:
            sel = group.get("selected", "-") or "-"
            cur = group.get("current", "") or ""
            if cur and cur != sel:
                return f"{sel}(当前 {cur})"
            return str(sel)

        system = s.get("system") or {}
        print(f"后端选择: 系统 {selected(system)}")
        forwarding = s.get("forwarding")
        if isinstance(forwarding, dict) and forwarding:
            print(f"          转发 {selected(forwarding)}")
        docker = s.get("docker")
        if isinstance(docker, dict) and docker:
            print(f"          Docker {selected(docker)}")


def ipv6_status(profile: str) -> None:
    """查看 v2.4.0 的防火墙 IPv6 开关状态。"""
    client = client_for(profile)
    unified_api_required(client)
    if api_kind(client) != API_V240:
        raise CliError("防火墙 IPv6 开关需要 1Panel v2.4.0 及以上版本")
    resp = response_json(client.get(f"{BASE}/settings"), "查询防火墙设置失败")
    data = resp.get("data") or {}
    print("IPv6 防火墙: {}".format("已启用" if data.get("ipv6Enabled") is True else "未启用"))


def ipv6_operate(profile: str, enable: bool) -> None:
    """启用/禁用 v2.4.0 的防火墙 IPv6 支持(异步任务)。"""
    client = client_for(profile)
    unified_api_required(client)
    if api_kind(client) != API_V240:
        raise CliError("防火墙 IPv6 开关需要 1Panel v2.4.0 及以上版本")
    status = "Enable" if enable else "Disable"
    resp = post_json(
        client,
        f"{BASE}/settings/ipv6",
        {"status": status},
        "操作防火墙 IPv6 失败",
    )
    label = "防火墙 IPv6 启用" if enable else "防火墙 IPv6 禁用"
    if run_async_task(client, resp.get("data") or {}, label):
        return
    print(f"{label}已完成")


def operate(profile: str, operation: str) -> None:
    client = client_for(profile)
    kind = api_kind(client)
    op_map = {
        "start": "start",
        "stop": "stop",
        "restart": "restart",
        "allow-ping": "disableBanPing",
        "ban-ping": "enableBanPing",
    }
    if operation not in op_map:
        raise CliError(f"不支持的操作 {operation}")
    desc_map = {
        "start": "防火墙启动",
        "stop": "防火墙停止",
        "restart": "防火墙重启",
        "allow-ping": "允许 Ping",
        "ban-ping": "禁止 Ping",
    }
    resp = post_json(
        client,
        f"{BASE}/operate",
        {"operation": op_map[operation], "withDockerRestart": False},
        "操作防火墙失败",
    )
    data = resp.get("data") or {}
    if async_api(kind) and run_async_task(client, data, desc_map[operation]):
        return
    done_map = {
        "start": "防火墙已启动",
        "stop": "防火墙已停止",
        "restart": "防火墙已重启",
        "allow-ping": "已允许 Ping",
        "ban-ping": "已禁止 Ping",
    }
    print(done_map[operation])


# ---------------------------------------------------------------------------
# 规则列表
# ---------------------------------------------------------------------------


@dataclass
class ListOptions:
    type: str
    info: str
    strategy: str


def _items_of(resp: dict, context: str) -> list[dict]:
    data = resp.get("data") or {}
    items = data.get("items")
    if items is None:
        # 面板在无规则时可能返回 items: null,按空列表处理
        return []
    if not isinstance(items, builtins.list):
        raise CliError(f"{context}: 响应 items 类型异常")
    return items


def stable_list(client: PanelClient, opts: ListOptions) -> None:
    body = {
        "page": 1,
        "pageSize": 500,
        "strategy": opts.strategy,
        "info": opts.info,
        "type": opts.type,
    }
    resp = post_json(client, f"{BASE}/search", body, "查询防火墙规则失败")
    items = _items_of(resp, "解析防火墙规则失败")
    print_stable_items(opts.type, items)
    total = (resp.get("data") or {}).get("total")
    print(f"共 {total if total is not None else len(items)} 条规则")


def _f(rule: dict, name: str) -> str:
    value = rule.get(name, "")
    return value if isinstance(value, str) else ("" if value is None else str(value))


def item_state(item: dict) -> str:
    """规则状态:v2.3.2 用 state,v2.4.0 改为 isWhitelist/protected。"""
    state = item.get("state")
    if isinstance(state, str) and state:
        return state
    if item.get("isWhitelist") is True:
        return "whitelist"
    observed = item.get("observed") or {}
    if observed.get("protected") is True:
        return "protected"
    persistence = observed.get("persistence")
    if isinstance(persistence, str) and persistence:
        return persistence
    return "-"


def print_stable_items(kind: str, items: list[dict]) -> None:
    if kind == "port":
        print(
            f"{'端口':<10} {'协议':<10} {'策略':<10} {'来源':<20} {'使用中':<10} 备注"
        )
        for it in items:
            used = "是" if it.get("usedStatus", "") in ("in_used", "used") else "否"
            print(
                f"{_f(it, 'port'):<10} {_f(it, 'protocol'):<10} {_f(it, 'strategy'):<10} "
                f"{_f(it, 'address'):<20} {used:<10} {_f(it, 'description')}"
            )
    elif kind == "forward":
        print(f"{'编号':<6} {'端口':<10} {'目标':<24} {'协议':<10} 网卡")
        for it in items:
            target = f"{_f(it, 'targetIP')}:{_f(it, 'targetPort')}"
            interface = _f(it, "interface") or "*"
            print(
                f"{_f(it, 'num'):<6} {_f(it, 'port'):<10} {target:<24} "
                f"{_f(it, 'protocol'):<10} {interface}"
            )
    elif kind == "address":
        print(f"{'地址':<24} {'策略':<10} 备注")
        for it in items:
            print(f"{_f(it, 'address'):<24} {_f(it, 'strategy'):<10} {_f(it, 'description')}")
    else:
        raise CliError(f"不支持的规则类型 {kind}(可选 port / address / forward)")


def scopes(provider: str) -> list[dict]:
    if provider in ("iptables", "nftables"):
        result = []
        for family in ("ipv4", "ipv6"):
            for chain in ("1PANEL_BASIC_BEFORE", "1PANEL_BASIC", "1PANEL_BASIC_AFTER"):
                result.append(
                    {
                        "provider": provider,
                        "family": family,
                        "table": "filter",
                        "chain": chain,
                        "direction": "input",
                    }
                )
        return result
    if provider == "firewalld":
        return [
            {
                "provider": provider,
                "family": "inet",
                "zone": "public",
                "direction": "input",
            }
        ]
    if provider == "ufw":
        return [
            {
                "provider": provider,
                "family": family,
                "chain": "incoming",
                "direction": "input",
            }
            for family in ("ipv4", "ipv6")
        ]
    return []


def unified_inventory(client: PanelClient, provider: str, info: str) -> list[dict]:
    body = {
        "page": 1,
        "pageSize": 500,
        "scopes": scopes(provider),
        "info": info,
        "all": True,
    }
    resp = post_json(client, f"{BASE}/rules/search", body, "查询统一防火墙规则失败")
    return (resp.get("data") or {}).get("items") or []


def matches_filter(rule: dict, item: dict, opts: ListOptions) -> bool:
    if opts.type == "port":
        kind_match = bool(_f(rule, "destinationPort"))
    elif opts.type == "address":
        kind_match = bool(_f(rule, "sourceAddress")) and not _f(rule, "destinationPort")
    else:
        kind_match = False
    strategy_match = not opts.strategy or _f(rule, "action").lower() == opts.strategy.lower()
    haystack = " ".join(
        [
            _f(rule, "sourceAddress"),
            _f(rule, "destinationAddress"),
            _f(rule, "sourcePort"),
            _f(rule, "destinationPort"),
            _f(rule, "description"),
            item_state(item),
        ]
    ).lower()
    return kind_match and strategy_match and (
        not opts.info or opts.info.lower() in haystack
    )


def unified_list(client: PanelClient, opts: ListOptions) -> None:
    if opts.type == "forward":
        resp = post_json(
            client,
            f"{BASE}/forward/search",
            {"page": 1, "pageSize": 500, "info": opts.info, "status": "", "strategy": opts.strategy},
            "查询端口转发失败",
        )
        items = _items_of(resp, "解析端口转发规则失败")
        print_stable_items("forward", items)
        total = (resp.get("data") or {}).get("total")
        print(f"共 {total if total is not None else len(items)} 条规则")
        return
    if opts.type not in ("port", "address"):
        raise CliError(f"不支持的规则类型 {opts.type}(可选 port / address / forward)")
    provider = firewall_provider(client)
    items = unified_inventory(client, provider, opts.info)
    filtered = [item for item in items if matches_filter(item.get("rule") or {}, item, opts)]
    if opts.type == "port":
        print(
            f"{'端口':<12} {'协议':<10} {'策略':<10} {'来源':<24} {'状态':<12} 备注"
        )
        for item in filtered:
            rule = item.get("rule") or {}
            source = _f(rule, "sourceAddress") or "Anywhere"
            print(
                f"{_f(rule, 'destinationPort'):<12} {_f(rule, 'protocol'):<10} "
                f"{_f(rule, 'action'):<10} {source:<24} "
                f"{item_state(item):<12} {_f(rule, 'description')}"
            )
    else:
        print(f"{'地址':<24} {'策略':<10} {'状态':<12} 备注")
        for item in filtered:
            rule = item.get("rule") or {}
            print(
                f"{_f(rule, 'sourceAddress'):<24} {_f(rule, 'action'):<10} "
                f"{item_state(item):<12} {_f(rule, 'description')}"
            )
    print(f"共 {len(filtered)} 条规则(统一规则 API,后端 {provider})")


def list(profile: str, opts: ListOptions) -> None:
    client = client_for(profile)
    normalized = ListOptions(
        type=(opts.type or "port").lower(),
        info=opts.info,
        strategy=opts.strategy,
    )
    if supports_unified_api(client):
        unified_list(client, normalized)
    else:
        stable_list(client, normalized)


# ---------------------------------------------------------------------------
# 规则构造
# ---------------------------------------------------------------------------


@dataclass
class PortOptions:
    protocol: str
    strategy: str
    source: str
    desc: str


def validate_strategy(strategy: str) -> str:
    lowered = strategy.lower()
    if lowered in ("accept", "allow"):
        return "accept"
    if lowered in ("drop", "deny", "block"):
        return "drop"
    raise CliError(f"不支持的策略 {strategy}(可选 accept / drop)")


def protocols(protocol: str) -> list[str]:
    lowered = protocol.lower()
    if lowered == "tcp":
        return ["tcp"]
    if lowered == "udp":
        return ["udp"]
    if lowered in ("tcp/udp", "udp/tcp"):
        return ["tcp", "udp"]
    raise CliError(f"不支持的协议 {protocol}(可选 tcp / udp / tcp/udp)")


def address_family(address: str) -> str:
    return "ipv6" if ":" in address else "ipv4"


def rule_scope(provider: str, family: str) -> dict | None:
    if provider in ("iptables", "nftables"):
        return {
            "provider": provider,
            "family": family,
            "table": "filter",
            "chain": "1PANEL_BASIC",
            "direction": "input",
        }
    if provider == "firewalld":
        return {
            "provider": provider,
            "family": family,
            "zone": "public",
            "direction": "input",
        }
    if provider == "ufw":
        return {
            "provider": provider,
            "family": family,
            "chain": "incoming",
            "direction": "input",
        }
    return None


def port_rules(provider: str, port: str, opts: PortOptions) -> list[dict]:
    action = validate_strategy(opts.strategy)
    if not opts.source and provider == "firewalld":
        family = "inet"
    else:
        family = address_family(opts.source)
    scope = rule_scope(provider, family)
    return [
        {
            "scope": scope,
            "protocol": protocol,
            "sourceAddress": opts.source,
            "destinationPort": port,
            "action": action,
            "description": opts.desc,
        }
        for protocol in protocols(opts.protocol)
    ]


@dataclass
class IpOptions:
    strategy: str
    desc: str


def ip_rules(provider: str, address: str, opts: IpOptions) -> list[dict]:
    return [
        {
            "scope": rule_scope(provider, address_family(address)),
            "protocol": "all",
            "sourceAddress": address,
            "action": validate_strategy(opts.strategy),
            "description": opts.desc,
        }
    ]


# ---------------------------------------------------------------------------
# 规则创建 / 删除(按接口类型分派)
# ---------------------------------------------------------------------------


def checked_create(client: PanelClient, rules: list[dict], label: str) -> None:
    """中间版本:先 /rules/check 取得 checkFlag,再提交创建。"""
    check = post_json(
        client,
        f"{BASE}/rules/check",
        {"items": [{"rule": rule} for rule in rules]},
        "检查防火墙规则失败",
    )
    plans = (check.get("data") or {}).get("items") or []
    creates: list[dict] = []
    existing = 0
    for plan in plans:
        decision = plan.get("decision", "") or ""
        classification = plan.get("classification", "") or ""
        if decision == "blocked":
            raise CliError(
                f"规则检查被阻止: {plan.get('reason', '') or '未知原因'} ({classification})"
            )
        if decision == "no_change" or classification in ("exact_managed", "exact_external"):
            existing += 1
            continue
        allowed = plan.get("allowedActions") or []
        if "create" not in allowed:
            raise CliError(f"规则检查未允许 create 操作: {classification}")
        creates.append(
            {
                "rule": plan.get("requestedRule"),
                "checkFlag": plan.get("checkFlag"),
                "action": "create",
                "sourceKind": "user",
            }
        )
    if not creates:
        print(f"{label} 已存在,无需重复添加({existing} 条)")
        return
    resp = post_json(client, f"{BASE}/rules", {"items": creates}, "创建防火墙规则失败")
    data = resp.get("data") or {}
    failed = int(data.get("failed", 0) or 0)
    if failed > 0:
        raise CliError(f"有 {failed} 条规则创建失败: {data.get('errors')}")
    suffix = f", {existing} 条已存在" if existing else ""
    print(f"{label} 已添加: {int(data.get('succeeded', 0) or 0)} 条{suffix}")


def v231_create(client: PanelClient, rules: list[dict], label: str) -> None:
    """v2.3.2+:直接提交创建(服务端校验),等待异步任务结果。"""
    items = [{"rule": rule, "sourceKind": "user"} for rule in rules]
    resp = post_json(client, f"{BASE}/rules", {"items": items}, "创建防火墙规则失败")
    run_async_task(client, resp.get("data") or {}, label)


def unified_create(client: PanelClient, kind: str, rules: list[dict], label: str) -> None:
    if async_api(kind):
        v231_create(client, rules, label)
    else:
        checked_create(client, rules, label)


def stable_port(client: PanelClient, operation: str, port: str, opts: PortOptions) -> None:
    post_json(
        client,
        f"{BASE}/port",
        {
            "operation": operation,
            "address": opts.source,
            "port": port,
            "protocol": opts.protocol,
            "strategy": opts.strategy,
            "description": opts.desc,
        },
        "操作端口规则失败",
    )
    action = "添加" if operation == "add" else "删除"
    print(f"端口规则已{action}: {port}/{opts.protocol}(策略 {opts.strategy})")


def address_eq(actual: str, expected: str) -> bool:
    actual = actual.split("/", 1)[0]
    expected = expected.split("/", 1)[0]
    return actual.lower() == expected.lower()


def protocol_eq(actual: str, expected: str) -> bool:
    return any(part.lower() == actual.lower() for part in expected.split("/"))


def v240_delete_target(item: dict) -> dict | None:
    """把 v2.4.0 规则清单项转换为 /rules/delete 所需的完整 target。"""
    observed = item.get("observed") or {}
    if not observed or not observed.get("instanceKey"):
        return None
    if item.get("isWhitelist") is True or observed.get("protected") is True:
        return None
    rule = item.get("rule") or observed.get("rule") or {}
    scope = rule.get("scope")
    if not scope:
        return None
    return {
        "scope": scope,
        "instanceKey": observed["instanceKey"],
        "observed": observed,
    }


def delete_request(kind: str, items: list[dict]) -> dict:
    """构造删除请求:v2.4.0 提交 targets,v2.3.2 提交 uuids。"""
    if kind == API_V240:
        targets = [target for target in (v240_delete_target(item) for item in items) if target]
        return {"targets": targets}
    uuids = [
        (item.get("desired") or {}).get("uuid")
        for item in items
        if (item.get("desired") or {}).get("uuid")
    ]
    return {"uuids": uuids}


def unified_delete(
    client: PanelClient, kind: str, items: list[dict], label: str
) -> None:
    body = delete_request(kind, items)
    if not (body.get("uuids") or body.get("targets")):
        raise CliError(
            f"未找到可删除的规则: {label};规则可能受保护、属于白名单,"
            "或由外部工具创建(当前接口不支持删除)"
        )
    resp = post_json(client, f"{BASE}/rules/delete", body, "删除防火墙规则失败")
    data = resp.get("data") or {}
    if async_api(kind) and run_async_task(client, data, label):
        return
    failed = int(data.get("failed", 0) or 0)
    if failed > 0:
        raise CliError(f"有 {failed} 条规则删除失败: {data.get('errors')}")
    print(f"{label} 已删除: {int(data.get('succeeded', 0) or 0)} 条")


def port_add(profile: str, port: str, opts: PortOptions) -> None:
    client = client_for(profile)
    kind = api_kind(client)
    if kind == API_LEGACY:
        stable_port(client, "add", port, opts)
        return
    provider = firewall_provider(client)
    unified_create(
        client,
        kind,
        port_rules(provider, port, opts),
        f"端口规则 {port}/{opts.protocol} 添加",
    )


def port_del(profile: str, port: str, opts: PortOptions) -> None:
    client = client_for(profile)
    kind = api_kind(client)
    if kind == API_LEGACY:
        stable_port(client, "remove", port, opts)
        return
    provider = firewall_provider(client)
    items = unified_inventory(client, provider, port)
    wanted_action = validate_strategy(opts.strategy)
    matched = [
        item
        for item in items
        if (item.get("rule") or {}) and _match_port(item, port, opts, wanted_action)
    ]
    unified_delete(
        client, kind, matched, f"端口规则 {port}/{opts.protocol} 删除"
    )


def _match_port(item: dict, port: str, opts: PortOptions, wanted_action: str) -> bool:
    rule = item.get("rule") or {}
    return (
        _f(rule, "destinationPort") == port
        and protocol_eq(_f(rule, "protocol"), opts.protocol)
        and address_eq(_f(rule, "sourceAddress"), opts.source)
        and _f(rule, "action") == wanted_action
    )


def stable_ip(client: PanelClient, operation: str, address: str, opts: IpOptions) -> None:
    post_json(
        client,
        f"{BASE}/ip",
        {
            "operation": operation,
            "address": address,
            "strategy": opts.strategy,
            "description": opts.desc,
        },
        "操作 IP 规则失败",
    )
    action = "添加" if operation == "add" else "删除"
    print(f"IP 规则已{action}: {address}(策略 {opts.strategy})")


def ip_add(profile: str, address: str, opts: IpOptions) -> None:
    client = client_for(profile)
    kind = api_kind(client)
    if kind == API_LEGACY:
        stable_ip(client, "add", address, opts)
        return
    provider = firewall_provider(client)
    unified_create(
        client, kind, ip_rules(provider, address, opts), f"IP 规则 {address} 添加"
    )


def ip_del(profile: str, address: str, opts: IpOptions) -> None:
    client = client_for(profile)
    kind = api_kind(client)
    if kind == API_LEGACY:
        stable_ip(client, "remove", address, opts)
        return
    provider = firewall_provider(client)
    items = unified_inventory(client, provider, address)
    wanted_action = validate_strategy(opts.strategy)
    matched = []
    for item in items:
        rule = item.get("rule") or {}
        if (
            not _f(rule, "destinationPort")
            and address_eq(_f(rule, "sourceAddress"), address)
            and _f(rule, "action") == wanted_action
        ):
            matched.append(item)
    unified_delete(client, kind, matched, f"IP 规则 {address} 删除")


# ---------------------------------------------------------------------------
# 端口转发
# ---------------------------------------------------------------------------


@dataclass
class ForwardOptions:
    protocol: str
    target: str
    interface: str
    num: str


def parse_target(target: str) -> tuple[str, str]:
    if ":" not in target:
        raise CliError("目标格式应为 IP:端口,如 10.0.0.2:8080")
    ip, _, port = target.rpartition(":")
    return ip, port


def operate_forward(
    client: PanelClient, kind: str, operation: str, port: str, opts: ForwardOptions
) -> None:
    target_ip, target_port = parse_target(opts.target)
    rule = {
        "operation": operation,
        "num": opts.num,
        "protocol": opts.protocol,
        "port": port,
        "targetIP": target_ip,
        "targetPort": target_port,
        "interface": opts.interface,
    }
    if kind == API_V240:
        rule["family"] = address_family(target_ip)
    body = {"rules": [rule]}
    # v2.3.2 及更早版本使用 forceDelete;v2.4.0 改为 import 标记
    body["import" if kind == API_V240 else "forceDelete"] = False
    unified = kind != API_LEGACY
    path = f"{BASE}/forward/operate" if unified else f"{BASE}/forward"
    resp = post_json(client, path, body, "操作端口转发失败")
    action = "添加" if operation == "add" else "删除"
    label = f"端口转发 {port}/{opts.protocol} {action}"
    if async_api(kind) and run_async_task(client, resp.get("data") or {}, label):
        return
    print(f"端口转发已{action}: {port}/{opts.protocol} -> {target_ip}:{target_port}")


def forward_add(profile: str, port: str, opts: ForwardOptions) -> None:
    client = client_for(profile)
    operate_forward(client, api_kind(client), "add", port, opts)


def forward_del(profile: str, port: str, opts: ForwardOptions) -> None:
    client = client_for(profile)
    operate_forward(client, api_kind(client), "remove", port, opts)


def batch_add(profile: str, ports: str, opts: PortOptions) -> None:
    client = client_for(profile)
    kind = api_kind(client)
    port_list = [p.strip() for p in ports.split(",") if p.strip()]
    if kind != API_LEGACY:
        provider = firewall_provider(client)
        rules: list[dict] = []
        for port in port_list:
            rules.extend(port_rules(provider, port, opts))
        if not rules:
            raise CliError("至少提供一个端口")
        unified_create(
            client, kind, rules, f"批量端口规则 {ports}/{opts.protocol} 添加"
        )
        return
    rules = [
        {
            "operation": "add",
            "address": opts.source,
            "port": port,
            "protocol": opts.protocol,
            "strategy": opts.strategy,
            "description": opts.desc,
        }
        for port in port_list
    ]
    post_json(
        client,
        f"{BASE}/batch",
        {"type": "port", "rules": rules},
        "批量添加端口规则失败",
    )
    print(f"已批量添加端口规则: {ports}/{opts.protocol}")


# ---------- Docker 端口守护(统一防火墙 API) ----------


def docker_base(client: PanelClient) -> dict:
    return response_json(client.get(f"{BASE}/docker/ports"), "查询 Docker 端口守护状态失败")


def bool_zh(value) -> str:
    return "是" if value is True else "否"


def docker_status(profile: str) -> None:
    client = client_for(profile)
    unified_api_required(client)
    resp = docker_base(client)
    data = resp.get("data") or {}
    base = data.get("base") or {}
    print(f"Docker 端口守护: {base.get('name', '-') or '-'}")
    print(f"后端: {base.get('backend', '-') or '-'}")
    print(f"版本: {base.get('version', '-') or '-'}")
    print(
        f"存在: {bool_zh(base.get('isExist'))}, "
        f"初始化: {bool_zh(base.get('initialized'))}, "
        f"绑定: {bool_zh(base.get('bound'))}"
    )
    if "ipv6Enabled" in base:
        print("IPv6 支持: {}".format("已启用" if base.get("ipv6Enabled") is True else "未启用"))
    for key, label in (("ipv4", "IPv4"), ("ipv6", "IPv6")):
        fam = base.get(key) or {}
        reason = fam.get("reason", "") or ""
        partial = " 部分" if fam.get("partial") is True else ""
        if reason:
            detail = f"({reason}){partial}"
        else:
            detail = (
                f"状态 {fam.get('state', '-') or '-'} "
                f"初始化 {bool_zh(fam.get('initialized'))} "
                f"绑定 {bool_zh(fam.get('bound'))} "
                f"生效 {bool_zh(fam.get('effective'))}{partial}"
            )
        print(f"{label}: {detail}")
    if base.get("message"):
        print(f"消息: {base.get('message')}")
    containers = data.get("containers") or []
    orphans = data.get("orphanPolicies") or []
    print(f"容器: {len(containers)} 个, 孤立策略: {len(orphans)} 条")


def endpoint_line(e: dict) -> str:
    family = e.get("family", "ipv4") or "ipv4"
    host_ip = e.get("hostIP", "") or ""
    host_port = e.get("hostPort", 0) or 0
    protocol = e.get("protocol", "") or ""
    if not host_ip:
        return f"{host_port}/{protocol}"
    if family == "ipv6":
        return f"[{host_ip}]:{host_port}/{protocol}"
    return f"{host_ip}:{host_port}/{protocol}"


def docker_mode_zh(mode: str) -> str:
    mapping = {
        "allow_sources": "仅允许来源",
        "deny_sources": "拒绝来源",
        "deny_all": "拒绝所有",
        "accept_sources": "接受来源",
        "accept_all": "接受所有",
    }
    return mapping.get(mode, mode)


def docker_management_zh(target: str) -> str:
    mapping = {
        "container_guard": "Docker守护",
        "host_firewall": "宿主机防火墙",
        "needs_diagnosis": "需诊断",
    }
    return mapping.get(target, target or "-")


def _display_width(text: str) -> int:
    import unicodedata

    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def pad_display(text: str, width: int) -> str:
    """按终端显示宽度对齐(中文按 2 列计)。"""
    return text + " " * max(0, width - _display_width(text))


def docker_ports(profile: str) -> None:
    client = client_for(profile)
    unified_api_required(client)
    resp = docker_base(client)
    data = resp.get("data") or {}
    containers = data.get("containers") or []
    total = 0
    host_firewall_ports: list[str] = []
    for c in containers:
        name = c.get("name", "-") or "-"
        app = c.get("application", "") or ""
        compose = c.get("compose", "") or ""
        if app:
            owner = f"应用 {app}"
        elif compose:
            owner = f"Compose {compose}"
        else:
            owner = "容器"
        print(f"\n{name} ({owner}):")
        endpoints = c.get("endpoints") or []
        total += len(endpoints)
        for e in endpoints:
            mode = e.get("mode", "-") or "-"
            sources = ",".join(s for s in (e.get("sources") or []) if isinstance(s, str))
            sources = sources or "-"
            uuid = e.get("policyUUID", "") or ""
            effective = "生效" if e.get("effective") is True else "未生效"
            target = e.get("managementTarget", "") or ""
            if target == "host_firewall":
                host_firewall_ports.append(endpoint_line(e))
            uuid_suffix = f"uuid={uuid}" if uuid else ""
            print(
                f"  {pad_display(endpoint_line(e), 28)} "
                f"{pad_display(docker_mode_zh(mode), 14)} "
                f"{pad_display(docker_management_zh(target), 14)} "
                f"{pad_display(effective, 8)} {uuid_suffix}"
            )
            description = e.get("description", "") or ""
            reason = e.get("managementReason", "") or ""
            if sources != "-" or description or reason:
                if sources != "-":
                    print(f"    来源: {sources}")
                if description:
                    print(f"    备注: {description}")
                if reason:
                    print(f"    诊断: {reason}")
    orphans = data.get("orphanPolicies") or []
    if orphans:
        print("\n孤立策略(对应的已发布端口已不存在):")
        for e in orphans:
            print(
                f"  {pad_display(endpoint_line(e), 28)} "
                f"{pad_display(docker_mode_zh(e.get('mode', '-') or '-'), 14)} "
                f"uuid={e.get('policyUUID', '') or ''}"
            )
    print(f"\n共 {total} 个已发布端口({len(containers)} 个容器), 孤立策略 {len(orphans)} 条")
    if host_firewall_ports:
        print(
            "提示:以下端口流量由宿主机防火墙接管,Docker 端口守护策略对其无效,"
            "请使用 firewall port/ip 规则管理:"
        )
        for line in host_firewall_ports:
            print(f"  {line}")


def docker_sync(profile: str) -> None:
    client = client_for(profile)
    unified_api_required(client)
    if api_kind(client) == API_V240:
        print(
            "1Panel v2.4.0 已移除 /hosts/firewall/docker/sync 接口,无需手动同步;"
            "请用 `firewall docker ports` 查看当前策略。"
        )
        return
    post_json(client, f"{BASE}/docker/sync", {}, "同步 Docker 端口防护规则失败")
    print("Docker 端口防护规则已同步")


def docker_operate(profile: str, operation: str) -> None:
    op_map = {"init": "initialize", "initialize": "initialize", "bind": "bind", "unbind": "unbind"}
    if operation not in op_map:
        raise CliError(f"不支持的操作 {operation}(可选 init / bind / unbind)")
    op = op_map[operation]
    client = client_for(profile)
    unified_api_required(client)
    resp = post_json(
        client,
        f"{BASE}/docker/operate",
        {"operation": op, "taskID": ""},
        "操作 Docker 端口守护失败",
    )
    done = {"initialize": "初始化", "bind": "绑定"}.get(op, "解绑")
    data = resp.get("data") or {}
    if run_async_task(client, data, f"Docker 端口守护{done}"):
        return
    print(f"Docker 端口守护已{done}")


def parse_docker_port(port: str) -> tuple[str, int]:
    port = port.strip()
    if port.isdigit():
        return "", int(port)
    if ":" in port:
        ip, _, num = port.rpartition(":")
        ip = ip.strip().strip("[]")
        if ip:
            if not num.isdigit():
                raise CliError(f"端口格式错误: {port}(应为数字或 IP:端口)")
            return ip, int(num)
    raise CliError(f"端口格式错误: {port}(应为 8080 或 0.0.0.0:8080)")


def docker_published_endpoints(
    client: PanelClient, port: str, protocol: str
) -> list[dict]:
    host_ip, host_port = parse_docker_port(port)
    protos = protocols(protocol)
    resp = response_json(
        client.get(f"{BASE}/docker/endpoints"), "查询 Docker 已发布端口失败"
    )
    containers = resp.get("data") or []
    endpoints: list[dict] = []
    for c in containers:
        for e in c.get("endpoints") or []:
            endpoint_ip = e.get("hostIP", "") or ""
            ip_ok = not host_ip or endpoint_ip.lower() == host_ip.lower()
            port_ok = e.get("hostPort") == host_port
            proto_ok = e.get("protocol") in protos
            if ip_ok and port_ok and proto_ok:
                endpoints.append(
                    {
                        "family": e.get("family", "ipv4") or "ipv4",
                        "hostIP": endpoint_ip,
                        "hostPort": e.get("hostPort", 0) or 0,
                        "protocol": e.get("protocol", "tcp") or "tcp",
                        "trafficPath": e.get("trafficPath", "") or "",
                        "managementTarget": e.get("managementTarget", "") or "",
                        "managementReason": e.get("managementReason", "") or "",
                    }
                )
    if not endpoints:
        raise CliError(
            f"未找到已发布的端口 {port}/{protocol};可先运行 `firewall docker ports` 查看实际端口"
        )
    return endpoints


def container_guard_endpoints(endpoints: list[dict]) -> list[dict]:
    """v2.3.2:流量由宿主机防火墙接管的端口不能用 Docker 端口守护策略。

    返回可交由 Docker 守护管理的端口;被宿主机防火墙接管的端口跳过并给出提示
    (可用 `firewall port/ip` 规则管理),无法判定流量路径的端口直接报错。
    """
    needs_diagnosis = [
        f"{endpoint_line(e)}({e.get('managementReason') or '原因未知'})"
        for e in endpoints
        if (e.get("managementTarget") or "") == "needs_diagnosis"
    ]
    if needs_diagnosis:
        raise CliError(
            "以下端口无法判定流量路径,Docker 端口守护策略不可用: "
            + ", ".join(needs_diagnosis)
        )
    guarded = [
        e for e in endpoints if (e.get("managementTarget") or "") != "host_firewall"
    ]
    skipped = [
        endpoint_line(e)
        for e in endpoints
        if (e.get("managementTarget") or "") == "host_firewall"
    ]
    if not guarded:
        raise CliError(
            "以下端口流量由宿主机防火墙接管,Docker 端口守护策略对其无效: "
            + ", ".join(skipped)
            + ";请改用 `firewall port add/del`、`firewall ip add/del` 管理"
        )
    if skipped:
        print(
            "提示:以下端口流量由宿主机防火墙接管,本次不写入 Docker 守护策略,"
            "如需限制请使用 `firewall port add/del`、`firewall ip add/del`:"
        )
        for line in skipped:
            print(f"  {line}")
    return guarded


def normalize_sources(sources: list[str]) -> list[str]:
    result = []
    for source in sources:
        source = source.strip().strip("[]")
        if source:
            result.append(source)
    return result


def sources_for_family(sources: list[str], family: str) -> list[str]:
    if family == "ipv6":
        return [s for s in sources if ":" in s]
    return [s for s in sources if ":" not in s]


def v231_docker_policies(
    client: PanelClient,
    kind: str,
    endpoints: list[dict],
    mode: str,
    source_list: list[str],
    desc: str,
) -> None:
    policies = []
    for endpoint in endpoints:
        family = endpoint.get("family", "ipv4") or "ipv4"
        if mode in ("deny_all", "accept_all"):
            sources: list[str] = []
        else:
            sources = sources_for_family(source_list, family)
            if not sources:
                raise CliError(
                    f"端口在 {family} 上有映射({endpoint_line(endpoint)}),但 --sources 中没有"
                    f"{'IPv6' if family == 'ipv6' else 'IPv4'} 来源地址;"
                    "请为每个已发布地址族都提供来源(或使用不带 --sources 的 deny 拒绝所有)"
                )
        policy = {
            "family": family,
            "hostIP": endpoint.get("hostIP", "") or "",
            "hostPort": endpoint.get("hostPort", 0) or 0,
            "protocol": endpoint.get("protocol", "tcp") or "tcp",
            "mode": mode,
            "sources": sources,
        }
        # v2.4.0 移除了策略 description 字段;旧版仍可写入备注
        if kind != API_V240:
            policy["description"] = desc
        policies.append(policy)
    resp = post_json(
        client,
        f"{BASE}/docker/policies/batch",
        {"policies": policies},
        "更新 Docker 端口防护策略失败",
    )
    label = f"Docker 端口防护策略更新({docker_mode_zh(mode)})"
    if run_async_task(client, resp.get("data") or {}, label):
        if source_list:
            print(f"来源: {', '.join(source_list)}")
        return
    print(f"已更新 {len(policies)} 条 Docker 端口防护策略: {docker_mode_zh(mode)}")
    if source_list:
        print(f"来源: {', '.join(source_list)}")


def legacy_docker_apply(
    client: PanelClient,
    endpoints: list[dict],
    mode: str,
    source_list: list[str],
    desc: str,
) -> None:
    body = {
        "endpoints": endpoints,
        "mode": mode,
        "sources": source_list,
        "description": desc,
    }
    post_json(
        client,
        f"{BASE}/docker/policies/batch",
        body,
        "更新 Docker 端口防护策略失败",
    )
    print(f"已更新 {len(endpoints)} 条 Docker 端口防护策略: {docker_mode_zh(mode)}")
    if source_list:
        print(f"来源: {', '.join(source_list)}")


def docker_apply(
    profile: str, port: str, protocol: str, sources: str, desc: str, mode: str
) -> None:
    client = client_for(profile)
    unified_api_required(client)
    endpoints = container_guard_endpoints(
        docker_published_endpoints(client, port, protocol)
    )
    source_list = normalize_sources([s for s in sources.split(",") if s.strip()])
    if mode == "allow_sources":
        if not source_list:
            raise CliError("放行操作需要 --sources 指定允许的来源 IP")
        real_mode = "allow_sources"
    elif mode == "deny_sources":
        if not source_list:
            raise CliError("拒绝指定来源需要 --sources;拒绝所有来源请省略 --sources")
        real_mode = "deny_sources"
    else:
        real_mode = "deny_all"
    kind = api_kind(client)
    if async_api(kind):
        v231_docker_policies(client, kind, endpoints, real_mode, source_list, desc)
    else:
        legacy_docker_apply(client, endpoints, real_mode, source_list, desc)


def docker_policy_del(profile: str, uuid: str) -> None:
    if not uuid.strip():
        raise CliError("请通过 --uuid 指定策略 UUID(firewall docker ports 中查看)")
    client = client_for(profile)
    unified_api_required(client)
    resp = post_json(
        client,
        f"{BASE}/docker/policies/delete/batch",
        {"uuids": [uuid]},
        "删除 Docker 端口防护策略失败",
    )
    if run_async_task(client, resp.get("data") or {}, "Docker 端口防护策略删除"):
        return
    print(f"已删除 Docker 端口防护策略: {uuid}")
