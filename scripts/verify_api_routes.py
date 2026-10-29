#!/usr/bin/env python3
"""核验内置 API 清单中的每条路由在真实面板上是否注册可用。

安全设计(不对面板产生业务副作用):
- GET 路由按只读约定直接请求;
- 写路由发送畸形 JSON 请求体,让参数绑定在执行业务逻辑之前失败;
- 已知「无请求体且带副作用」的路由(SKIP_ROUTES)不实调,只对照面板
  Swagger 核验注册情况。

用法:
  python3 scripts/verify_api_routes.py                 # 核验内置 v2.4.1 清单
  python3 scripts/verify_api_routes.py --profile prod  # 指定已登录的会话
  python3 scripts/verify_api_routes.py --filter websites
  python3 scripts/verify_api_routes.py --output /tmp/api-verify.json

退出码:0=未发现异常;1=存在 404/HTML/无法解析等异常;2=环境或配置错误。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from panel_cli import catalog  # noqa: E402
from panel_cli.client import PanelClient  # noqa: E402
from panel_cli.errors import CliError  # noqa: E402
from panel_cli.session import load_session  # noqa: E402

MALFORMED_JSON = b'{"__verify_api_routes__": '
PARAM_RE = re.compile(r":[A-Za-z][A-Za-z0-9]*|\{[A-Za-z][A-Za-z0-9]*\}")

SKIP_ROUTES: dict[str, str] = {
    "/api/v2/ai/ollama/model/sync": "同步 Ollama 模型状态",
    "/api/v2/alert/logs/clean": "清空告警日志",
    "/api/v2/apps/installed/sync": "同步已安装应用状态",
    "/api/v2/core/auth/api/generate": "生成新的 API Key",
    "/api/v2/core/auth/logout": "注销当前会话",
    "/api/v2/core/auth/mfa/close": "关闭 MFA",
    "/api/v2/core/auth/passkey/begin": "Passkey 登录流程",
    "/api/v2/core/auth/passkey/finish": "Passkey 登录流程",
    "/api/v2/core/auth/passkey/register/finish": "Passkey 注册流程",
    "/api/v2/core/settings/menu/default": "重置面板菜单",
    "/api/v2/core/settings/ssl/reload": "重载面板 SSL",
    "/api/v2/dashboard/system/restart/:operation": "重启系统/面板",
    "/api/v2/databases/pg/:database/load": "远程导入数据库",
    "/api/v2/databases/redis/install/cli": "安装 redis-cli",
    "/api/v2/files/recycle/clear": "清空回收站",
    "/api/v2/hosts/monitor/clean": "清空监控数据",
    "/api/v2/hosts/ssh/cert/sync": "同步 SSH 证书",
    "/api/v2/hosts/ssh/log/clean": "清空 SSH 日志",
    "/api/v2/runtimes/sync": "同步运行环境状态",
    "/api/v2/toolbox/ftp/sync": "同步 FTP 状态",
    "/api/v2/toolbox/scan": "启动系统扫描",
}


def fill_params(path: str) -> str:
    return PARAM_RE.sub("cli-probe", path)


def content_type_of(headers: list[tuple[str, str]]) -> str:
    return next(
        (value.lower() for name, value in headers if name.lower() == "content-type"),
        "",
    )


def classify(status: int, content_type: str, body: bytes) -> dict:
    text = body[:400].decode("utf-8", "replace")
    if status == 404 and "404 page not found" in text:
        return {"result": "MISSING", "snippet": text[:120]}
    if content_type.startswith("text/html"):
        return {"result": "HTML", "snippet": text[:120]}
    try:
        payload = json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        return {"result": "OTHER", "status": status, "snippet": text[:160]}
    return {
        "result": "OK",
        "code": payload.get("code"),
        "message": str(payload.get("message", ""))[:160],
    }


def swagger_registered(swagger: dict | None, method: str, path: str) -> bool | None:
    if not swagger:
        return None
    for swagger_path, item in (swagger.get("paths") or {}).items():
        if not isinstance(item, dict) or method.lower() not in item:
            continue
        if catalog.path_matches(catalog.normalize_path(swagger_path), path):
            return True
    return False


def probe(
    profile: str,
    filter_text: str,
    delay: float,
    timeout: float,
) -> tuple[dict, list[dict]]:
    catalog_data = catalog.builtin()
    session = load_session(profile)
    client = PanelClient(
        session.panel_url, session.cookies, session.insecure, timeout=timeout
    )
    try:
        current = client.get("api/v2/core/auth/current").json()
    except ValueError as exc:
        raise CliError(f"会话验证失败: {exc}") from exc
    if current.get("code") != 200:
        raise CliError("会话无效或已过期,请先运行 `1panel-cli login`(--profile 可指定会话)")
    swagger = catalog.runtime_swagger(profile)
    needle = filter_text.lower()
    results: list[dict] = []

    for route in catalog_data.routes:
        method, path = route.method, route.path
        if needle and needle not in f"{method} {path} {route.handler}".lower():
            continue
        entry: dict = {
            "method": method,
            "path": path,
            "handler": route.handler,
            "swaggerDocumented": route.swagger_documented,
        }
        if path in SKIP_ROUTES:
            registered = swagger_registered(swagger, method, path)
            entry.update(
                {
                    "result": "SKIPPED",
                    "reason": SKIP_ROUTES[path],
                    "registeredInSwagger": registered,
                }
            )
            results.append(entry)
            continue

        full = fill_params(path)
        try:
            if method == "GET":
                response = client.get(full)
            elif method == "DELETE":
                response = client._send("DELETE", full, with_csrf=True)
            else:
                response = client._send(
                    method,
                    full,
                    body=MALFORMED_JSON,
                    content_type="application/json",
                    with_csrf=True,
                )
            entry.update(
                classify(response.status, content_type_of(response.headers), response._body)
            )
        except CliError as exc:
            entry.update({"result": "ERROR", "snippet": str(exc)[:160]})
        results.append(entry)
        time.sleep(delay)

    meta = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "panelUrl": session.panel_url,
        "profile": profile,
        "catalogVersion": catalog_data.panel_version,
        "catalogRouteCount": catalog_data.route_count,
        "verifiedRouteCount": len(results),
        "swaggerAvailable": swagger is not None,
    }
    return meta, results


def summarize(results: list[dict]) -> int:
    counts: dict[str, int] = {}
    codes: dict[str, int] = {}
    anomalies: list[dict] = []
    for entry in results:
        result = entry["result"]
        counts[result] = counts.get(result, 0) + 1
        if result == "OK":
            code = str(entry.get("code"))
            codes[code] = codes.get(code, 0) + 1
        elif result != "SKIPPED":
            anomalies.append(entry)

    skipped_unregistered = [
        entry
        for entry in results
        if entry["result"] == "SKIPPED" and entry.get("registeredInSwagger") is False
    ]

    print(f"核验路由:{len(results)} 条")
    for name in ("OK", "SKIPPED", "MISSING", "HTML", "OTHER", "ERROR"):
        if counts.get(name):
            print(f"  {name:<8} {counts[name]}")
    if codes:
        detail = ", ".join(f"code={code} × {n}" for code, n in sorted(codes.items()))
        print(f"  业务响应:{detail}")
    if skipped_unregistered:
        print(
            f"  跳过实调且 Swagger 未收录 {len(skipped_unregistered)} 条"
            "(源码已注册,如需可人工确认):"
        )
        for entry in skipped_unregistered:
            print(f"    {entry['method']:<4} {entry['path']}  # {entry['reason']}")
    if not anomalies:
        print("结果:未发现注册/方法异常。")
        return 0

    print(f"结果:发现 {len(anomalies)} 条异常:")
    for entry in anomalies:
        detail = entry.get("snippet") or entry.get("message") or entry.get("status", "")
        print(f"  {entry['result']:<7} {entry['method']:<4} {entry['path']:<58} {detail}")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="核验内置 API 清单中的路由是否在真实面板上注册可用(写操作不会产生副作用)"
    )
    parser.add_argument("-p", "--profile", default="default", help="会话 profile(默认 default)")
    parser.add_argument("--filter", default="", help="仅核验路径/处理器包含该关键字的子集")
    parser.add_argument("--delay", type=float, default=0.01, help="请求间隔秒数(默认 0.01)")
    parser.add_argument("--timeout", type=float, default=30.0, help="单请求超时秒数(默认 30)")
    parser.add_argument("--output", metavar="PATH", help="将完整结果写入 JSON 报告")
    args = parser.parse_args()

    try:
        meta, results = probe(args.profile, args.filter, args.delay, args.timeout)
    except CliError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2

    exit_code = summarize(results)
    if args.output:
        report = dict(meta)
        counts: dict[str, int] = {}
        for entry in results:
            counts[entry["result"]] = counts.get(entry["result"], 0) + 1
        report["counts"] = counts
        report["results"] = results
        Path(args.output).write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"报告已写入 {args.output}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
