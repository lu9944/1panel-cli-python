"""防火墙管理:自动适配 v2.2.5 旧接口与新版统一规则接口,含 Docker 端口守护。

对应 Rust 版 firewall.rs。
"""

from __future__ import annotations

import builtins
import json
from dataclasses import dataclass

from .client import PanelClient
from .errors import CliError
from .session import load_session

BASE = "api/v2/hosts/firewall"


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


def supports_unified_api(client: PanelClient) -> bool:
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


def operate(profile: str, operation: str) -> None:
    client = client_for(profile)
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
        "start": "防火墙已启动",
        "stop": "防火墙已停止",
        "restart": "防火墙已重启",
        "allow-ping": "已允许 Ping",
        "ban-ping": "已禁止 Ping",
    }
    post_json(
        client,
        f"{BASE}/operate",
        {"operation": op_map[operation], "withDockerRestart": False},
        "操作防火墙失败",
    )
    print(desc_map.get(operation, "操作完成"))


@dataclass
class ListOptions:
    type: str
    info: str
    strategy: str


def _items_of(resp: dict, context: str) -> list[dict]:
    data = resp.get("data") or {}
    items = data.get("items")
    if items is None:
        # 面板在无规则时返回 items: null(Rust 基线此处会报错,Python 版按空列表处理)
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
    resp = post_json(client, f"{BASE}/rules/search", body, "查询新版防火墙规则失败")
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
            item.get("state", "") or "",
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
            "查询新版端口转发失败",
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
                f"{item.get('state', '-') or '-':<12} {_f(rule, 'description')}"
            )
    else:
        print(f"{'地址':<24} {'策略':<10} {'状态':<12} 备注")
        for item in filtered:
            rule = item.get("rule") or {}
            print(
                f"{_f(rule, 'sourceAddress'):<24} {_f(rule, 'action'):<10} "
                f"{item.get('state', '-') or '-':<12} {_f(rule, 'description')}"
            )
    print(f"共 {len(filtered)} 条规则(新版统一规则 API,后端 {provider})")


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


def unified_create(client: PanelClient, rules: list[dict], label: str) -> None:
    check = post_json(
        client,
        f"{BASE}/rules/check",
        {"items": [{"rule": rule} for rule in rules]},
        "检查新版防火墙规则失败",
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
    resp = post_json(client, f"{BASE}/rules", {"items": creates}, "创建新版防火墙规则失败")
    data = resp.get("data") or {}
    failed = int(data.get("failed", 0) or 0)
    if failed > 0:
        raise CliError(f"有 {failed} 条规则创建失败: {data.get('errors')}")
    suffix = f", {existing} 条已存在" if existing else ""
    print(f"{label} 已添加: {int(data.get('succeeded', 0) or 0)} 条{suffix}")


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


def unified_delete(client: PanelClient, uuids: list[str], label: str) -> None:
    if not uuids:
        raise CliError(
            f"未找到可删除的托管规则: {label};外部或受保护规则不能由 CLI 删除"
        )
    resp = post_json(
        client, f"{BASE}/rules/delete", {"uuids": uuids}, "删除新版防火墙规则失败"
    )
    data = resp.get("data") or {}
    failed = int(data.get("failed", 0) or 0)
    if failed > 0:
        raise CliError(f"有 {failed} 条规则删除失败: {data.get('errors')}")
    print(f"{label} 已删除: {int(data.get('succeeded', 0) or 0)} 条")


def port_add(profile: str, port: str, opts: PortOptions) -> None:
    client = client_for(profile)
    if supports_unified_api(client):
        provider = firewall_provider(client)
        unified_create(
            client,
            port_rules(provider, port, opts),
            f"端口规则 {port}/{opts.protocol}",
        )
    else:
        stable_port(client, "add", port, opts)


def port_del(profile: str, port: str, opts: PortOptions) -> None:
    client = client_for(profile)
    if not supports_unified_api(client):
        stable_port(client, "remove", port, opts)
        return
    provider = firewall_provider(client)
    items = unified_inventory(client, provider, port)
    wanted_action = validate_strategy(opts.strategy)
    uuids = [
        (item.get("desired") or {}).get("uuid")
        for item in items
        if (item.get("rule") or {}) and _match_port(item, port, opts, wanted_action)
    ]
    unified_delete(client, [u for u in uuids if u], f"端口规则 {port}/{opts.protocol}")


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
    if supports_unified_api(client):
        provider = firewall_provider(client)
        unified_create(client, ip_rules(provider, address, opts), f"IP 规则 {address}")
    else:
        stable_ip(client, "add", address, opts)


def ip_del(profile: str, address: str, opts: IpOptions) -> None:
    client = client_for(profile)
    if not supports_unified_api(client):
        stable_ip(client, "remove", address, opts)
        return
    provider = firewall_provider(client)
    items = unified_inventory(client, provider, address)
    wanted_action = validate_strategy(opts.strategy)
    uuids = []
    for item in items:
        rule = item.get("rule") or {}
        if (
            not _f(rule, "destinationPort")
            and address_eq(_f(rule, "sourceAddress"), address)
            and _f(rule, "action") == wanted_action
        ):
            uuid = (item.get("desired") or {}).get("uuid")
            if uuid:
                uuids.append(uuid)
    unified_delete(client, uuids, f"IP 规则 {address}")


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
    client: PanelClient, unified: bool, operation: str, port: str, opts: ForwardOptions
) -> None:
    target_ip, target_port = parse_target(opts.target)
    body = {
        "rules": [
            {
                "operation": operation,
                "num": opts.num,
                "protocol": opts.protocol,
                "port": port,
                "targetIP": target_ip,
                "targetPort": target_port,
                "interface": opts.interface,
            }
        ],
        "forceDelete": False,
    }
    path = f"{BASE}/forward/operate" if unified else f"{BASE}/forward"
    post_json(client, path, body, "操作端口转发失败")
    action = "添加" if operation == "add" else "删除"
    print(f"端口转发已{action}: {port}/{opts.protocol} -> {target_ip}:{target_port}")


def forward_add(profile: str, port: str, opts: ForwardOptions) -> None:
    client = client_for(profile)
    operate_forward(client, supports_unified_api(client), "add", port, opts)


def forward_del(profile: str, port: str, opts: ForwardOptions) -> None:
    client = client_for(profile)
    operate_forward(client, supports_unified_api(client), "remove", port, opts)


def batch_add(profile: str, ports: str, opts: PortOptions) -> None:
    client = client_for(profile)
    port_list = [p.strip() for p in ports.split(",") if p.strip()]
    if supports_unified_api(client):
        provider = firewall_provider(client)
        rules: list[dict] = []
        for port in port_list:
            rules.extend(port_rules(provider, port, opts))
        if not rules:
            raise CliError("至少提供一个端口")
        unified_create(
            client, rules, f"批量端口规则 {ports}/{opts.protocol}"
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


# ---------- Docker 端口守护(新版统一防火墙 API) ----------


def unified_api_required(client: PanelClient) -> None:
    if not supports_unified_api(client):
        raise CliError(
            "当前面板不支持新版统一防火墙 API(未检测到 /hosts/firewall/settings);"
            "Docker 端口守护需要新版 1Panel"
        )


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
    for key, label in (("ipv4", "IPv4"), ("ipv6", "IPv6")):
        fam = base.get(key) or {}
        reason = fam.get("reason", "") or ""
        if reason:
            detail = f"({reason})"
        else:
            detail = (
                f"状态 {fam.get('state', '-') or '-'} "
                f"初始化 {bool_zh(fam.get('initialized'))} "
                f"绑定 {bool_zh(fam.get('bound'))} "
                f"生效 {bool_zh(fam.get('effective'))}"
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
    }
    return mapping.get(mode, mode)


def docker_ports(profile: str) -> None:
    client = client_for(profile)
    unified_api_required(client)
    resp = response_json(
        client.get(f"{BASE}/docker/endpoints"), "查询 Docker 已发布端口失败"
    )
    containers = resp.get("data") or []
    total = 0
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
            uuid_suffix = f"uuid={uuid}" if uuid else ""
            print(
                f"  {endpoint_line(e):<28} {docker_mode_zh(mode):<14} {effective:<8} {uuid_suffix}"
            )
            description = e.get("description", "") or ""
            if sources != "-" or description:
                if sources != "-":
                    print(f"    来源: {sources}")
                if description:
                    print(f"    备注: {description}")
    print(f"\n共 {total} 个已发布端口({len(containers)} 个容器)")


def docker_sync(profile: str) -> None:
    client = client_for(profile)
    unified_api_required(client)
    post_json(client, f"{BASE}/docker/sync", {}, "同步 Docker 端口防护规则失败")
    print("Docker 端口防护规则已同步")


def docker_operate(profile: str, operation: str) -> None:
    op_map = {"init": "initialize", "initialize": "initialize", "bind": "bind", "unbind": "unbind"}
    if operation not in op_map:
        raise CliError(f"不支持的操作 {operation}(可选 init / bind / unbind)")
    op = op_map[operation]
    client = client_for(profile)
    unified_api_required(client)
    post_json(
        client,
        f"{BASE}/docker/operate",
        {"operation": op, "taskID": ""},
        "操作 Docker 端口守护失败",
    )
    done = {"initialize": "初始化", "bind": "绑定"}.get(op, "解绑")
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
                    }
                )
    if not endpoints:
        raise CliError(
            f"未找到已发布的端口 {port}/{protocol};可先运行 `firewall docker ports` 查看实际端口"
        )
    return endpoints


def docker_apply(
    profile: str, port: str, protocol: str, sources: str, desc: str, mode: str
) -> None:
    client = client_for(profile)
    unified_api_required(client)
    endpoints = docker_published_endpoints(client, port, protocol)
    source_list = [s.strip() for s in sources.split(",") if s.strip()]
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
    body = {
        "endpoints": endpoints,
        "mode": real_mode,
        "sources": source_list,
        "description": desc,
    }
    post_json(
        client,
        f"{BASE}/docker/policies/batch",
        body,
        "更新 Docker 端口防护策略失败",
    )
    print(f"已更新 {len(endpoints)} 条 Docker 端口防护策略: {docker_mode_zh(real_mode)}")
    if source_list:
        print(f"来源: {', '.join(source_list)}")


def docker_policy_del(profile: str, uuid: str) -> None:
    if not uuid.strip():
        raise CliError("请通过 --uuid 指定策略 UUID(firewall docker ports 中查看)")
    client = client_for(profile)
    unified_api_required(client)
    post_json(
        client,
        f"{BASE}/docker/policies/delete/batch",
        {"uuids": [uuid]},
        "删除 Docker 端口防护策略失败",
    )
    print(f"已删除 Docker 端口防护策略: {uuid}")
