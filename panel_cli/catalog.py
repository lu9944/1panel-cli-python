"""API 路由目录:内置 v2.2.5 源码清单 + 当前面板 Swagger 运行时合并。

对应 Rust 版 catalog.rs。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .client import PanelClient
from .errors import CliError
from .session import load_session

BUILTIN_CATALOG_PATH = Path(__file__).resolve().parent / "data" / "api-v2.2.5.json"
HTTP_METHODS = ("get", "post", "put", "delete", "patch")


@dataclass
class Route:
    method: str
    path: str
    handler: str
    source: str
    summary: str = ""
    tags: list[str] = field(default_factory=list)
    swagger_documented: bool = False


@dataclass
class Catalog:
    panel_version: str
    route_count: int
    swagger_documented_count: int
    routes: list[Route]


def builtin() -> Catalog:
    try:
        raw = json.loads(BUILTIN_CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CliError(f"内置 API 清单损坏: {exc}") from exc
    routes = [
        Route(
            method=r["method"],
            path=r["path"],
            handler=r.get("handler", ""),
            source=r.get("source", ""),
            summary=r.get("summary", ""),
            tags=list(r.get("tags", [])),
            swagger_documented=bool(r.get("swaggerDocumented", False)),
        )
        for r in raw.get("routes", [])
    ]
    return Catalog(
        panel_version=raw.get("panelVersion", ""),
        route_count=int(raw.get("routeCount", len(routes))),
        swagger_documented_count=int(raw.get("swaggerDocumentedCount", 0)),
        routes=routes,
    )


def client_for(profile: str) -> PanelClient:
    session = load_session(profile)
    return PanelClient(session.panel_url, session.cookies, session.insecure)


def runtime_swagger(profile: str) -> dict | None:
    try:
        client = client_for(profile)
        response = client.get("1panel/swagger/doc.json")
        if not response.ok:
            return None
        return response.json()
    except CliError:
        return None
    except ValueError:
        return None


def normalize_path(path: str) -> str:
    path = path.strip()
    for scheme in ("http://", "https://"):
        if path.startswith(scheme):
            rest = path[len(scheme):]
            path = rest.split("/", 1)[1] if "/" in rest else ""
            break
    path = path.lstrip("/")
    if path == "api/v2":
        return "/api/v2"
    if path.startswith("api/v2/"):
        return f"/{path}"
    return f"/api/v2/{path}"


def swagger_operation(swagger: dict, method: str, full_path: str) -> dict | None:
    swagger_path = full_path.removeprefix("/api/v2") or "/"
    return (swagger.get("paths", {}).get(swagger_path, {}) or {}).get(method.lower())


def path_segments(path: str) -> list[str]:
    return [segment for segment in path.strip("/").split("/") if segment]


def is_param_segment(segment: str) -> bool:
    """路由模板中的参数段,兼容 gin 的 `:id` 与 Swagger 的 `{id}` 写法。"""
    return (segment.startswith(":") and len(segment) > 1) or (
        segment.startswith("{") and segment.endswith("}") and len(segment) > 2
    )


def path_matches(template: str, path: str) -> bool:
    """模板路径(含 `:param` / `{param}`)是否匹配具体请求路径。"""
    template_segments = path_segments(template)
    path_parts = path_segments(path)
    if len(template_segments) != len(path_parts):
        return False
    return all(
        is_param_segment(pattern) or pattern == actual
        for pattern, actual in zip(template_segments, path_parts)
    )


def _static_score(template: str) -> int:
    return sum(not is_param_segment(segment) for segment in path_segments(template))


def resolve_route(routes: list[Route], method: str, path: str) -> tuple[Route | None, str | None]:
    """在路由清单中定位 `method + path`;path 可为真实取值或模板写法。

    先精确匹配,再按段通配匹配(如 `websites/ca/1` → `websites/ca/:id`),
    返回 (路由, 匹配到的模板路径);未匹配返回 (None, None)。
    """
    exact = next((r for r in routes if r.method == method and r.path == path), None)
    if exact is not None:
        return exact, exact.path
    matches = [
        r for r in routes
        if r.method == method and path_matches(r.path, path)
    ]
    if not matches:
        return None, None
    best = max(matches, key=lambda r: _static_score(r.path))
    return best, best.path


def resolve_swagger_operation(
    swagger: dict, method: str, path: str
) -> tuple[str | None, dict | None]:
    """在 Swagger 中定位操作;支持用真实取值匹配 `{id}` / `:id` 模板。"""
    operation = swagger_operation(swagger, method, path)
    if operation is not None:
        return path, operation
    best: tuple[int, str, dict] | None = None
    for swagger_path, path_item in (swagger.get("paths") or {}).items():
        if not isinstance(path_item, dict):
            continue
        candidate = path_item.get(method.lower())
        if not isinstance(candidate, dict):
            continue
        full_path = normalize_path(swagger_path)
        if not path_matches(full_path, path):
            continue
        score = _static_score(full_path)
        if best is None or score > best[0]:
            best = (score, full_path, candidate)
    if best is None:
        return None, None
    return best[1], best[2]


def route_text(route: Route) -> str:
    return " ".join(
        [route.method, route.path, route.handler, route.summary, " ".join(route.tags)]
    ).lower()


def operation_tags(operation: dict) -> list[str]:
    return [str(t) for t in operation.get("tags", [])]


def merged_routes(catalog: Catalog, swagger: dict | None) -> list[Route]:
    routes: dict[tuple[str, str], Route] = {
        (route.method, route.path): Route(**vars(route)) for route in catalog.routes
    }
    if not swagger:
        return list(routes.values())
    for swagger_path, path_item in (swagger.get("paths") or {}).items():
        if not isinstance(path_item, dict):
            continue
        for method in HTTP_METHODS:
            operation = path_item.get(method)
            if not isinstance(operation, dict):
                continue
            key = (method.upper(), normalize_path(swagger_path))
            if key in routes:
                route = routes[key]
                route.swagger_documented = True
                if not route.summary:
                    route.summary = operation.get("summary", "") or ""
                if not route.tags:
                    route.tags = operation_tags(operation)
            else:
                routes[key] = Route(
                    method=key[0],
                    path=key[1],
                    handler="",
                    source="runtime-swagger",
                    summary=operation.get("summary", "") or "",
                    tags=operation_tags(operation),
                    swagger_documented=True,
                )
    # 与 Rust 版 BTreeMap<method, path> 的输出顺序保持一致
    return sorted(routes.values(), key=lambda r: (r.method, r.path))


def list_routes(profile: str, filter_text: str) -> None:
    catalog = builtin()
    runtime = runtime_swagger(profile)
    routes = merged_routes(catalog, runtime)
    needle = filter_text.lower()
    shown = 0
    print(f"{'方法':<7} {'路径':<58} 处理器 / 摘要")
    for route in routes:
        if needle and needle not in route_text(route):
            continue
        summary = route.summary or route.handler
        print(f"{route.method:<7} {route.path:<58} {summary}")
        shown += 1
    print(
        f"显示 {shown}/{len(routes)} 条路由;内置基线 1Panel {catalog.panel_version},"
        f"Swagger 已记录 {catalog.swagger_documented_count}/{catalog.route_count}"
    )
    if runtime is None:
        print("提示:当前面板 Swagger 不可用,以上结果来自源码生成的内置完整清单。")
    elif len(routes) > catalog.route_count:
        print(f"当前面板 Swagger 另补充 {len(routes) - catalog.route_count} 条运行时路由。")


def collect_refs(value, root: dict, seen: set[str], schemas: dict) -> None:
    if isinstance(value, dict):
        reference = value.get("$ref")
        if isinstance(reference, str) and reference not in seen:
            seen.add(reference)
            if reference.startswith("#"):
                schema = _json_pointer(root, reference[1:])
                if schema is not None:
                    key = reference.rsplit("/", 1)[-1]
                    schemas[key] = schema
                    collect_refs(schema, root, seen, schemas)
        for child in value.values():
            collect_refs(child, root, seen, schemas)
    elif isinstance(value, list):
        for child in value:
            collect_refs(child, root, seen, schemas)


def _json_pointer(document: dict, pointer: str) -> object | None:
    if not pointer:
        return document
    current: object = document
    for token in pointer.split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and token in current:
            current = current[token]
        elif isinstance(current, list):
            try:
                current = current[int(token)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return current


def describe(profile: str, method: str, path: str) -> None:
    method = method.upper()
    requested = normalize_path(path)
    catalog = builtin()
    route, matched_path = resolve_route(catalog.routes, method, requested)
    runtime = runtime_swagger(profile)
    swagger_path, operation = (
        resolve_swagger_operation(runtime, method, requested) if runtime else (None, None)
    )
    if route is None and operation is None:
        raise CliError(
            f"内置 {catalog.panel_version} 清单和当前面板 Swagger 中均未找到 {method} {requested};"
            "可运行 `api list --filter <关键字>`"
        )

    result: dict = {"method": method, "path": matched_path or swagger_path or requested}
    if result["path"] != requested:
        result["requestedPath"] = requested
    if route is not None:
        result.update(
            {
                "handler": route.handler,
                "source": route.source,
                "summary": route.summary,
                "tags": route.tags,
                "swaggerDocumented": route.swagger_documented,
            }
        )
    else:
        result.update({"source": "runtime-swagger", "runtimeOnly": True})

    if runtime is not None and operation is not None:
        result["operation"] = operation
        seen: set[str] = set()
        schemas: dict = {}
        collect_refs(operation, runtime, seen, schemas)
        if schemas:
            result["schemas"] = schemas
    elif route is not None:
        result["note"] = "当前面板 Swagger 未收录或不可用;请求体请结合 handler/source 查看"
    print(json.dumps(result, ensure_ascii=False, indent=2))
