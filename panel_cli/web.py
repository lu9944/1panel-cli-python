"""网站(OpenResty/nginx)管理:列表/创建/配置/HTTPS/SSL/上传/解压。

对应 Rust 版 web.rs。
"""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

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


def _client_for(profile: str) -> tuple[PanelClient, object]:
    session = load_session(profile)
    return PanelClient(session.panel_url, session.cookies, session.insecure), session


def find_website(client: PanelClient, domain: str) -> dict:
    for page in range(1, 21):
        body = {
            "page": page,
            "pageSize": 100,
            "name": "",
            "orderBy": "primary_domain",
            "order": "ascending",
            "type": "",
        }
        resp = _parse(client.post_json("api/v2/websites/search", body), "查询网站列表失败")
        check_code(resp)
        data = resp.get("data") or {}
        for it in data.get("items") or []:
            if it.get("primaryDomain", "") == domain:
                return it
        total = int(data.get("total", 0) or 0)
        if total <= page * 100:
            break
    raise CliError(f"未找到域名 {domain} 对应的网站")


def get_website(client: PanelClient, domain: str) -> dict:
    return find_website(client, domain)


def list(profile: str) -> None:
    client, _ = _client_for(profile)
    rows: list[list[str]] = []
    total = 0
    for page in range(1, 21):
        body = {
            "page": page,
            "pageSize": 100,
            "name": "",
            "orderBy": "primary_domain",
            "order": "ascending",
            "type": "",
        }
        resp = _parse(client.post_json("api/v2/websites/search", body), "查询网站列表失败")
        check_code(resp)
        data = resp.get("data") or {}
        total = int(data.get("total", 0) or 0)
        for it in data.get("items") or []:
            rows.append(
                [
                    it.get("primaryDomain", "") or "",
                    it.get("type", "") or "",
                    it.get("status", "") or "",
                    "是" if it.get("protocol", "") == "HTTPS" else "否",
                    it.get("sitePath", "") or "",
                ]
            )
        if total <= page * 100:
            break
    headers = ["域名", "类型", "状态", "HTTPS", "网站目录"]
    widths = [max([len(str(r[i])) for r in rows] + [0]) for i in range(5)]
    print("  ".join(f"{headers[i]:<{widths[i]}}" for i in range(5)))
    print("+".join("-" * (w + 2) for w in widths))
    for r in rows:
        print("  ".join(f"{str(r[i]):<{widths[i]}}" for i in range(5)))
    print(f"共 {total} 个网站")


def config(profile: str, domain: str, file: str | None) -> None:
    client, _ = _client_for(profile)
    website = get_website(client, domain)
    website_id = website.get("id", 0)
    ensure_openresty_compat(client)

    if file:
        try:
            content = Path(file).read_text(encoding="utf-8")
        except OSError as exc:
            raise CliError(f"读取文件失败 {file}: {exc}") from exc
        resp = _parse(
            client.post_json("api/v2/websites/nginx/update", {"id": website_id, "content": content}),
            "解析修改配置响应失败",
        )
        check_code(resp)
        print(f"{domain} 的 nginx 配置已更新并生效")
        return

    resp = _parse(
        client.get(f"api/v2/websites/{website_id}/config/openresty"), "读取 nginx 配置失败"
    )
    check_code(resp)
    content = (resp.get("data") or {}).get("content", "") or ""
    print(f"--- {domain} nginx 配置 ---")
    print(content)


@dataclass
class HttpsOptions:
    enable: bool | None
    ssl_id: int | None
    http_config: str | None
    cert_file: str | None
    key_file: str | None


def https(profile: str, domain: str, opts: HttpsOptions) -> None:
    client, _ = _client_for(profile)
    website = get_website(client, domain)
    website_id = website.get("id", 0)
    ensure_openresty_compat(client)

    if opts.enable is None and opts.ssl_id is None and opts.cert_file is None:
        resp = client.get(f"api/v2/websites/{website_id}/https")
        if resp.status >= 500:
            raise CliError(
                "读取 HTTPS 配置失败(面板返回 HTTP 500)。已知原因:本地应用路径兼容问题;"
                "可先运行 `1panel-cli web repair` 修复后重试"
            )
        payload = _parse(resp, "查询 https 配置失败")
        check_code(payload)
        st = payload.get("data") or {}
        print(f"{domain} HTTPS: {'已开启' if st.get('enable') else '未开启'}")
        ssl = st.get("ssl")
        if ssl:
            print(
                f"证书 id: {ssl.get('id', 0)} ({ssl.get('primaryDomain', '')}，状态: {ssl.get('status', '')})"
            )
            print(f"到期: {ssl.get('expireDate', '')}")
        print(f"HTTP 跳转: {st.get('httpConfig', '')}")
        return

    enable = True if opts.enable is None else opts.enable
    if enable:
        apply_https(client, website_id, opts.ssl_id, opts.http_config, opts.cert_file, opts.key_file)
    else:
        body = {
            "websiteId": website_id,
            "enable": False,
            "httpConfig": opts.http_config or "HTTPToHTTPS",
            "type": "existed",
        }
        resp = _parse(
            client.post_json(f"api/v2/websites/{website_id}/https", body), "更新 https 配置失败"
        )
        check_code(resp)
    print(f"{domain} HTTPS 已{'开启' if enable else '关闭'}")


@dataclass
class SslOptions:
    email: str
    provider: str
    key_type: str
    auto_renew: bool


def ssl(profile: str, domain: str, opts: SslOptions) -> None:
    client, _ = _client_for(profile)

    acme_account_id = find_or_create_acme(client, opts.email)

    body = {
        "primaryDomain": domain,
        "provider": opts.provider,
        "acmeAccountId": acme_account_id,
        "keyType": opts.key_type,
        "autoRenew": opts.auto_renew,
    }
    resp = _parse(client.post_json("api/v2/websites/ssl", body), "创建证书请求失败")
    check_code(resp)
    ssl_id = int((resp.get("data") or {}).get("id", 0) or 0)
    if ssl_id == 0:
        raise CliError("创建证书失败: 未返回证书 ID")
    print(f"证书已创建: id={ssl_id} domain={domain}")

    # 1Panel 的 /websites/ssl 创建后会自动异步发起申请(website_ssl.go 中 `go ObtainSSL`);
    # 此时显式 obtain 会返回「任务执行中,请勿重复执行」——视为已发起,避免误报失败。
    resp = _parse(
        client.post_json("api/v2/websites/ssl/obtain", {"ID": ssl_id, "skipDNSCheck": False}),
        "申请证书失败",
    )
    if resp.get("code") != 200 and not _already_applying(resp.get("message", "")):
        check_code(resp)
    print(f"证书申请任务已提交(provider: {opts.provider}, 可能需要几分钟)")
    print("申请完成后运行以下命令启用 HTTPS:")
    print(f"  1panel-cli web https {domain} --enable --ssl-id {ssl_id}")


def _already_applying(message: str) -> bool:
    """判断 create 已自动发起申请导致的重复提交错误。"""
    markers = ("任务执行中", "请勿重复执行", "task is running", "already running", "in progress")
    lowered = (message or "").lower()
    return any(marker.lower() in lowered for marker in markers)


def find_or_create_acme(client: PanelClient, email: str) -> int:
    resp = _parse(
        client.post_json("api/v2/websites/acme/search", {"page": 1, "pageSize": 50}),
        "查询 ACME 账号失败",
    )
    check_code(resp)
    for it in (resp.get("data") or {}).get("items") or []:
        if it.get("email") == email and it.get("id"):
            acme_id = int(it["id"])
            print(f"使用已有 ACME 账号: {email} (id={acme_id})")
            return acme_id
    body = {"email": email, "type": "letsencrypt", "keyType": "RSA2048"}
    resp = _parse(client.post_json("api/v2/websites/acme", body), "创建 ACME 账号失败")
    check_code(resp)
    acme_id = (resp.get("data") or {}).get("id")
    if not acme_id:
        raise CliError("创建 ACME 账号失败: 未返回账号 ID")
    print(f"已创建 ACME 账号: {email} (id={acme_id})")
    return int(acme_id)


def upload(profile: str, domain: str, file: str, to: str) -> None:
    client, _ = _client_for(profile)
    website = get_website(client, domain)

    local = Path(file)
    if not local.exists():
        raise CliError(f"本地文件不存在: {file}")
    dest = join_site_path(website.get("sitePath", ""), to)
    resp = client.upload_file(dest, local, True)
    text = resp.text()
    try:
        payload = json.loads(text)
    except ValueError:
        payload = None
    if not isinstance(payload, dict) or payload.get("code") != 200:
        code = payload.get("code", -1) if isinstance(payload, dict) else -1
        message = payload.get("message", text) if isinstance(payload, dict) else text
        raise CliError(f"上传失败 (code={code}): {message}")
    print(f"已上传 {local.name} 到 {dest}")


def extract(profile: str, domain: str, archive: str, to: str, archive_type: str) -> None:
    client, _ = _client_for(profile)
    website = get_website(client, domain)
    site_path = (website.get("sitePath") or "").rstrip("/")

    archive_path = archive if archive.startswith("/") else f"{site_path}/{archive}"
    dst = join_site_path(website.get("sitePath", ""), to)
    ctype = detect_archive_type(archive) if not archive_type else archive_type

    body = {
        "path": archive_path,
        "dst": dst,
        "type": ctype,
        "secret": "",
        "taskID": new_uuid(),
    }
    resp = _parse(client.post_json("api/v2/files/decompress", body), "解析解压响应失败")
    check_code(resp)
    print(f"已提交解压任务: {archive_path} -> {dst} (类型 {ctype})")


def join_site_path(site_path: str, sub: str) -> str:
    base = site_path.rstrip("/")
    sub = sub.strip("/")
    return base if not sub else f"{base}/{sub}"


ARCHIVE_SUFFIXES = [
    (".tar.gz", "tar.gz"),
    (".tgz", "tar.gz"),
    (".tar.bz2", "tar.bz2"),
    (".tar.xz", "tar.xz"),
    (".tar", "tar"),
    (".zip", "zip"),
    (".gz", "gz"),
    (".bz2", "bz2"),
    (".xz", "xz"),
    (".7z", "7z"),
    (".rar", "rar"),
]


def detect_archive_type(name: str) -> str:
    lower = name.lower()
    for suffix, ctype in ARCHIVE_SUFFIXES:
        if lower.endswith(suffix):
            return ctype
    raise CliError("无法从文件名识别压缩类型,请用 --type 指定(zip / tar.gz / tar / gz ...)")


@dataclass
class CreateOptions:
    type: str
    alias: str | None
    remark: str | None
    proxy: str | None = None
    ssl_id: int | None = None
    http_config: str | None = None
    spa: bool = False
    spa_api_prefixes: str | None = None


OPENRESTY_APP_KEYS = ("openresty", "localopenresty")
DEFAULT_SPA_API_PREFIXES = "api,auth,health,linear"
# 1Panel 本地应用(离线/内置商店)的安装目录为 <apps>/local/<key>/<name>,
# 但部分 nginx 相关接口固定读取 <apps>/<key>/<name>(详见 ensure_openresty_compat)
LOCAL_APP_MARKER = "/apps/local/"


def new_uuid() -> str:
    return str(uuid.UUID(bytes=os.urandom(16), version=4))


def list_installed_apps(client: PanelClient) -> list[dict]:
    body = {"page": 1, "pageSize": 200, "name": "", "type": "", "tags": []}
    resp = _parse(
        client.post_json("api/v2/apps/installed/search", body), "查询已安装应用失败"
    )
    check_code(resp)
    return (resp.get("data") or {}).get("items") or []


def find_openresty_install_item(client: PanelClient) -> dict:
    for it in list_installed_apps(client):
        if str(it.get("appKey") or "") in OPENRESTY_APP_KEYS:
            return it
    raise CliError("未找到已安装的 openresty,请先运行 apps install openresty")


def find_openresty_install(client: PanelClient) -> int:
    item = find_openresty_install_item(client)
    if not item.get("id"):
        raise CliError("解析 openresty 安装 ID 失败")
    return int(item["id"])


def openresty_path_compat(install: dict) -> tuple[str, str] | None:
    """推导本地应用的 (面板期望路径, 实际路径);不适用于远程应用时返回 None。"""
    app_key = str(install.get("appKey") or "")
    path = str(install.get("path") or "").rstrip("/")
    if not app_key.startswith("local") or LOCAL_APP_MARKER not in path:
        return None
    head, _, tail = path.partition(LOCAL_APP_MARKER)
    key_dir = tail.split("/", 1)[0]
    if not key_dir:
        return None
    return f"{head}/apps/{key_dir}", os.path.dirname(path)


def ensure_openresty_compat(client: PanelClient) -> str:
    """兼容 1Panel 本地应用路径问题(自动创建软链)。

    返回值: skip(无需处理) / ok(已兼容) / fixed(已修复) / unavailable(工具不在面板主机)。
    """
    try:
        install = find_openresty_install_item(client)
    except CliError:
        return "skip"
    compat = openresty_path_compat(install)
    if not compat:
        return "skip"
    expected_root, actual_root = compat
    if os.path.exists(expected_root):
        return "ok"
    if not os.path.exists(actual_root):
        return "unavailable"
    try:
        os.symlink(actual_root, expected_root)
    except OSError as exc:
        raise CliError(
            f"检测到 1Panel 本地应用路径兼容问题,但自动创建软链失败: {exc};"
            f"请在面板主机执行: ln -s {actual_root} {expected_root}"
        ) from exc
    print(f"已修复面板本地应用路径兼容: {expected_root} -> {actual_root}")
    return "fixed"


def repair(profile: str) -> None:
    """修复已知兼容问题(本地应用路径软链),供面板不匹配报错时使用。"""
    client, _ = _client_for(profile)
    installs = list_installed_apps(client)
    if not installs:
        print("当前没有已安装应用,无需修复。")
        return
    fixed = 0
    for install in installs:
        compat = openresty_path_compat(install)
        if not compat:
            continue
        expected_root, actual_root = compat
        name = install.get("name", "")
        if os.path.exists(expected_root):
            print(f"{name}: 路径兼容正常({expected_root})")
            continue
        if not os.path.exists(actual_root):
            print(f"{name}: 未找到实际目录 {actual_root}(工具可能不在面板主机,请手动检查)")
            continue
        try:
            os.symlink(actual_root, expected_root)
        except OSError as exc:
            raise CliError(f"创建软链失败: {exc};请手动执行: ln -s {actual_root} {expected_root}") from exc
        print(f"{name}: 已创建软链 {expected_root} -> {actual_root}")
        fixed += 1
    print(f"修复完成(新建软链 {fixed} 个)。")


def delete(profile: str, domain: str, force: bool) -> None:
    """删除网站;遇到面板本地应用 key 的已知问题时可用 --force 走兼容模式。"""
    client, _ = _client_for(profile)
    website = get_website(client, domain)
    website_id = int(website.get("id", 0) or 0)
    body = {
        "id": website_id,
        "deleteApp": False,
        "deleteBackup": True,
        "forceDelete": True,
        "deleteDB": False,
    }

    resp = _parse(client.post_json("api/v2/websites/del", body), "删除网站失败")
    if resp.get("code") == 200:
        print(f"网站已删除: {domain}")
        return
    message = resp.get("message", "") or ""
    if "record not found" not in message:
        raise CliError(f"面板返回错误 (code={resp.get('code', -1)}): {message}")
    if not force:
        raise CliError(
            "删除失败: 面板已知问题(本地应用 key 为 localopenresty,但删除流程固定按 openresty 查询,"
            "报 record not found);确认可用 --force 让本工具临时改名应用 key 完成删除"
            "(会先备份 agent.db,删除后自动还原)"
        )
    _delete_with_local_app_key_workaround(client, body, domain)


def _delete_with_local_app_key_workaround(client: PanelClient, body: dict, domain: str) -> None:
    """临时把 localopenresty 改名为 openresty,删除网站后还原(仅同机可用)。"""
    import sqlite3

    from .initcmd import AGENT_DB

    db_path = Path(AGENT_DB)
    if not db_path.exists():
        raise CliError(
            f"未找到面板数据库 {db_path}(--force 需本工具与面板同机);"
            "可在面板主机手动执行: UPDATE apps SET key='openresty' WHERE key='localopenresty';"
            "删除后改回"
        )
    backup = db_path.with_name(db_path.name + ".1panel-cli.bak")
    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    dst = sqlite3.connect(str(backup))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()

    con = sqlite3.connect(str(db_path), timeout=10)
    renamed: list[tuple[int, str]] = []
    try:
        con.execute("PRAGMA busy_timeout=10000")
        rows = con.execute("SELECT id, key FROM apps WHERE key = 'localopenresty'").fetchall()
        if not rows:
            raise CliError("未发现 localopenresty 应用记录,请手动检查面板数据库")
        for app_id, key in rows:
            con.execute("UPDATE apps SET key = ? WHERE id = ?", ("openresty", app_id))
            renamed.append((app_id, key))
        con.commit()
        try:
            resp = _parse(client.post_json("api/v2/websites/del", body), "解析删除响应失败")
            check_code(resp)
        finally:
            for app_id, key in renamed:
                con.execute("UPDATE apps SET key = ? WHERE id = ?", (key, app_id))
            con.commit()
    finally:
        con.close()
    print(f"网站已删除: {domain}")
    print(f"已使用本地应用 key 兼容模式(数据库备份: {backup})")


def find_default_website_group(client: PanelClient) -> int:
    resp = _parse(
        client.post_json("api/v2/core/groups/search", {"type": "website"}),
        "查询网站分组失败",
    )
    check_code(resp)
    groups = resp.get("data") or []
    for g in groups:
        if g.get("isDefault") is True and g.get("id"):
            return int(g["id"])
    if groups:
        if groups[0].get("id"):
            return int(groups[0]["id"])
        raise CliError("解析分组 ID 失败")
    raise CliError("面板没有可用的网站分组")


def normalize_proxy(proxy_pass: str) -> str:
    """规范化反代地址:补全协议前缀,允许 127.0.0.1:3500 这种写法。"""
    upstream = (proxy_pass or "").strip()
    if not upstream:
        raise CliError("需要后端地址(如 http://127.0.0.1:3500)")
    if "://" not in upstream:
        upstream = "http://" + upstream
    return upstream


def build_spa_content(proxy_pass: str, api_prefixes: str = DEFAULT_SPA_API_PREFIXES) -> str:
    """生成 SPA 前端路由回退配置(error_page + proxy_intercept_errors)。

    后端返回 404 时改为返回 /index.html,使 React/Vue 等前端路由刷新不 404;
    API 前缀、静态资源与隐藏路径保持原 404。
    """
    upstream = normalize_proxy(proxy_pass)
    prefixes = [p.strip().strip("/") for p in (api_prefixes or "").split(",") if p.strip()]
    prefix_re = "|".join(re.escape(p) for p in prefixes) if prefixes else "api"
    return f"""# SPA 前端路由回退:后端返回 404 时改为返回 index.html(React/Vue 等)
proxy_intercept_errors on;
error_page 404 = @spa_fallback;

location @spa_fallback {{
    if ($uri ~ ^/({prefix_re})(/|$)) {{
        return 404;
    }}
    if ($uri ~* \\.[a-zA-Z0-9]+$) {{
        return 404;
    }}
    if ($uri ~ (^|/)\\.) {{
        return 404;
    }}
    rewrite ^ /index.html break;
    proxy_pass {upstream};
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Port $server_port;
}}
"""


def detect_proxy_pass(client: PanelClient, website_id: int) -> str:
    resp = _parse(
        client.post_json("api/v2/websites/proxies", {"id": website_id}), "读取反代配置失败"
    )
    check_code(resp)
    for item in resp.get("data") or []:
        if item.get("proxyPass") and item.get("enable") is not False:
            return str(item["proxyPass"])
    raise CliError("未找到该网站的反代目标,请用 --proxy 指定后端地址")


def write_spa_rewrite(
    client: PanelClient, website_id: int, proxy_pass: str, api_prefixes: str | None = None
) -> None:
    content = build_spa_content(proxy_pass, api_prefixes or DEFAULT_SPA_API_PREFIXES)
    resp = _parse(
        client.post_json(
            "api/v2/websites/rewrite/update",
            {"websiteId": website_id, "name": "SPA-Fallback", "content": content},
        ),
        "写入 SPA 回退配置失败",
    )
    check_code(resp)
    print("已启用 SPA 前端路由回退(刷新深层路由不再 404)")


def spa(
    profile: str,
    domain: str,
    proxy: str | None,
    api_prefixes: str | None,
    remove: bool,
) -> None:
    client, _ = _client_for(profile)
    website = get_website(client, domain)
    website_id = website.get("id", 0)
    ensure_openresty_compat(client)

    if remove:
        content = "# SPA 回退已关闭\n"
        resp = _parse(
            client.post_json(
                "api/v2/websites/rewrite/update",
                {"websiteId": website_id, "name": "SPA-Fallback", "content": content},
            ),
            "关闭 SPA 回退失败",
        )
        check_code(resp)
        print(f"{domain} 的 SPA 回退已关闭")
        return

    upstream = proxy or detect_proxy_pass(client, website_id)
    write_spa_rewrite(client, website_id, upstream, api_prefixes)


def apply_https(
    client: PanelClient,
    website_id: int,
    ssl_id: int | None,
    http_config: str | None,
    cert_file: str | None = None,
    key_file: str | None = None,
) -> None:
    body: dict = {
        "websiteId": website_id,
        "enable": True,
        "httpConfig": http_config or "HTTPToHTTPS",
        "type": "existed",
    }
    if cert_file and key_file:
        try:
            certificate = Path(cert_file).read_text(encoding="utf-8")
        except OSError as exc:
            raise CliError(f"读取证书文件失败 {cert_file}: {exc}") from exc
        try:
            private_key = Path(key_file).read_text(encoding="utf-8")
        except OSError as exc:
            raise CliError(f"读取私钥文件失败 {key_file}: {exc}") from exc
        body["type"] = "manual"
        body["importType"] = "paste"
        body["certificate"] = certificate
        body["privateKey"] = private_key
    else:
        if ssl_id is None:
            raise CliError(
                "启用 HTTPS 需要提供 --ssl-id(可先运行 web ssl 申请证书)或 --cert/--key 上传证书"
            )
        body["websiteSSLId"] = ssl_id
    resp = _parse(
        client.post_json(f"api/v2/websites/{website_id}/https", body), "更新 https 配置失败"
    )
    check_code(resp)


def create(profile: str, domain: str, opts: CreateOptions) -> None:
    client, _ = _client_for(profile)
    ensure_openresty_compat(client)

    web_type = "proxy" if opts.proxy else opts.type
    if opts.proxy and opts.type != "proxy":
        print(f"检测到 --proxy,网站类型使用 proxy(忽略 --type {opts.type})")
    if opts.spa and web_type != "proxy":
        raise CliError("--spa 仅适用于反代网站(请配合 --proxy 使用)")
    if opts.ssl_id and opts.type not in ("", "static", "proxy"):
        raise CliError("--ssl-id 仅支持创建 static / proxy 网站时使用")

    openresty_id = find_openresty_install(client)
    group_id = find_default_website_group(client)
    alias = opts.alias or (domain.split(".", 1)[0] or "site")

    body = {
        "primaryDomain": domain,
        "type": web_type,
        "alias": alias,
        "remark": opts.remark or "",
        "appType": "installed",
        "appInstallID": openresty_id,
        "webSiteGroupID": group_id,
        "domains": [{"domain": domain, "port": 80, "ssl": False}],
        "taskID": new_uuid(),
        "IPV6": False,
    }
    if opts.proxy:
        body["proxy"] = normalize_proxy(opts.proxy)
    if opts.ssl_id:
        body["enableSSL"] = True
        body["websiteSSLID"] = opts.ssl_id
    resp = _parse(client.post_json("api/v2/websites", body), "解析创建网站响应失败")
    check_code(resp)
    print(f"网站已创建: {domain} (alias={alias}, 类型={web_type})")
    if opts.proxy:
        print(f"反代目标: {normalize_proxy(opts.proxy)}")

    website = find_website(client, domain)
    website_id = website.get("id", 0)

    if opts.ssl_id:
        if opts.http_config and opts.http_config != "HTTPToHTTPS":
            apply_https(client, website_id, opts.ssl_id, opts.http_config)
        print(f"HTTPS 已启用(证书 ID {opts.ssl_id},HTTP 跳转 {opts.http_config or 'HTTPToHTTPS'})")
    if opts.spa:
        upstream = opts.proxy or detect_proxy_pass(client, website_id)
        write_spa_rewrite(client, website_id, upstream, opts.spa_api_prefixes)
    print("可运行 `1panel-cli web list` 查看面板实际网站目录")
