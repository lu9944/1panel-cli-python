"""网站(OpenResty/nginx)管理:列表/创建/配置/HTTPS/SSL/上传/解压。

对应 Rust 版 web.rs。
"""

from __future__ import annotations

import json
import os
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

    if opts.enable is None and opts.ssl_id is None and opts.cert_file is None:
        resp = _parse(client.get(f"api/v2/websites/{website_id}/https"), "查询 https 配置失败")
        check_code(resp)
        st = resp.get("data") or {}
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
    http_config = opts.http_config or "HTTPToHTTPS"
    body: dict = {
        "websiteId": website_id,
        "enable": enable,
        "httpConfig": http_config,
        "type": "existed",
    }
    if enable:
        if opts.cert_file and opts.key_file:
            try:
                certificate = Path(opts.cert_file).read_text(encoding="utf-8")
            except OSError as exc:
                raise CliError(f"读取证书文件失败 {opts.cert_file}: {exc}") from exc
            try:
                private_key = Path(opts.key_file).read_text(encoding="utf-8")
            except OSError as exc:
                raise CliError(f"读取私钥文件失败 {opts.key_file}: {exc}") from exc
            body["type"] = "manual"
            body["importType"] = "paste"
            body["certificate"] = certificate
            body["privateKey"] = private_key
        else:
            if opts.ssl_id is None:
                raise CliError(
                    "启用 HTTPS 需要提供 --ssl-id(可先运行 web ssl 申请证书)或 --cert/--key 上传证书"
                )
            body["type"] = "existed"
            body["websiteSSLId"] = opts.ssl_id
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

    # v2.2.5 的 /websites/ssl 创建后会自动异步发起申请(website_ssl.go:263);
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


def new_uuid() -> str:
    return str(uuid.UUID(bytes=os.urandom(16), version=4))


def find_openresty_install(client: PanelClient) -> int:
    body = {"page": 1, "pageSize": 100, "name": "openresty", "type": "", "tags": []}
    resp = _parse(
        client.post_json("api/v2/apps/installed/search", body), "查询 openresty 安装信息失败"
    )
    check_code(resp)
    for it in (resp.get("data") or {}).get("items") or []:
        if it.get("appKey") == "openresty" and it.get("id"):
            return int(it["id"])
    raise CliError("未找到已安装的 openresty,请先运行 apps install openresty")


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


def create(profile: str, domain: str, opts: CreateOptions) -> None:
    client, _ = _client_for(profile)

    openresty_id = find_openresty_install(client)
    group_id = find_default_website_group(client)
    alias = opts.alias or (domain.split(".", 1)[0] or "site")

    body = {
        "primaryDomain": domain,
        "type": opts.type,
        "alias": alias,
        "remark": opts.remark or "",
        "appType": "installed",
        "appInstallID": openresty_id,
        "webSiteGroupID": group_id,
        "domains": [{"domain": domain, "port": 80, "ssl": False}],
        "taskID": new_uuid(),
        "IPV6": False,
    }
    resp = _parse(client.post_json("api/v2/websites", body), "解析创建网站响应失败")
    check_code(resp)
    print(f"网站已创建: {domain} (alias={alias}, 类型={opts.type})")
    print("可运行 `1panel-cli web list` 查看面板实际网站目录")
