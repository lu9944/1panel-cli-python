"""命令行入口:argparse 解析 + 命令分发。

对应 Rust 版 main.rs(clap 命令树),支持同样的子命令与参数。
"""

from __future__ import annotations

import argparse
import os
import sys

from . import apps, catalog, commands, config, db, doctor
from . import firewall, initcmd, redis, web, auth, redact
from .errors import CliError

VERSION = "2.4.1"

EPILOG = (
    "示例:\n"
    "  login       读取 .env 中的账号信息登录面板\n"
    "  status      检查当前登录状态\n"
    "  api get core/auth/current  调用面板 API"
)


def parse_kv(raw: str) -> tuple[str, str]:
    key, sep, value = raw.partition("=")
    if not sep:
        raise argparse.ArgumentTypeError(f"参数格式应为 KEY=VALUE: {raw}")
    return key.strip(), value.strip()


def _common_args() -> argparse.ArgumentParser:
    """所有子命令共享的全局参数(对应 clap 的 global args)。

    default=SUPPRESS:子命令未提供时不覆盖根解析器已解析的值,
    使 `-e/--env` 等参数放在子命令前后均可。
    """
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-e", "--env", metavar="PATH", default=argparse.SUPPRESS,
                        help=".env 配置文件路径(默认查找当前目录与程序目录下的 .env)")
    common.add_argument("-p", "--profile", metavar="NAME", default=argparse.SUPPRESS,
                        help="会话 profile 名称,可同时保存多个面板的登录凭据(默认 default)")
    common.add_argument("--node", metavar="NODE", default=argparse.SUPPRESS,
                        help="目标节点标识(多节点面板使用;默认 local,也可设置 PANEL_NODE)")
    common.add_argument("--show-secrets", dest="show_secrets", action="store_true",
                        default=argparse.SUPPRESS,
                        help="不脱敏:按原样输出响应中的私钥/口令等敏感字段(默认脱敏为 ***REDACTED***)")
    return common


# ---------------------------------------------------------------------------
# parser 构造
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="1panel-cli",
        description="登录并调用 1Panel 面板 Web 服务的命令行工具",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        parents=[_common_args()],
    )
    parser.add_argument("--version", action="version", version=f"1panel-cli {VERSION}")
    subparsers = parser.add_subparsers(dest="command", metavar="<COMMAND>")
    subparsers.required = True

    common = _common_args()
    _add_init(subparsers, common)
    _add_login(subparsers, common)
    _add_status_info_logout(subparsers, common)
    _add_api(subparsers, common)
    _add_apps(subparsers, common)
    _add_web(subparsers, common)
    _add_db(subparsers, common)
    _add_redis(subparsers, common)
    _add_firewall(subparsers, common)
    _add_doctor(subparsers, common)
    return parser


def _add_init(subparsers, common) -> None:
    p = subparsers.add_parser(
        "init",
        parents=[common],
        help="从本机 1Panel 自动探测账号信息并生成 .env(1pctl user-info + 本地数据库解密)",
    )
    p.add_argument("-o", "--output", metavar="PATH", default=".env",
                   help="输出文件路径(默认当前目录 .env)")
    p.add_argument("--force", action="store_true", help="覆盖已存在的配置文件")
    p.add_argument("--host", metavar="HOST", default=None,
                   help="面板地址中的主机名/IP(默认自动探测 127.0.0.1 与主网卡 IP)")
    p.add_argument("--print", dest="print_only", action="store_true",
                   help="仅打印配置内容,不写入文件")
    p.set_defaults(func=_cmd_init)


def _add_login(subparsers, common) -> None:
    p = subparsers.add_parser("login", parents=[common], help="登录 1Panel 面板并保存会话凭据")
    p.add_argument("--url", default=None, help="面板地址,如 http://10.0.0.1:41394(可包含安全入口路径)")
    p.add_argument("--entrance", default=None, help="面板安全入口(可从地址路径中自动识别)")
    p.add_argument("--user", default=None, help="面板用户名")
    p.add_argument("--password", default=None, help="面板密码")
    p.add_argument("--mfa", default=None, help="MFA 动态验证码(开启 MFA 时需要)")
    p.add_argument("--captcha", default=None, help="验证码答案(面板要求验证码时需要)")
    p.add_argument("--captcha-id", dest="captcha_id", default=None,
                   help="验证码 ID(与 --captcha 配合使用,用于预先获取的验证码)")
    p.add_argument("--insecure", action="store_true", help="信任自签名证书")
    p.set_defaults(func=_cmd_login)


def _add_status_info_logout(subparsers, common) -> None:
    p = subparsers.add_parser("status", parents=[common], help="检查当前登录状态")
    p.set_defaults(func=lambda a: commands.status(a.profile))

    p = subparsers.add_parser("info", parents=[common], help="查看已保存的会话凭据信息")
    p.set_defaults(func=lambda a: commands.info(a.profile))

    p = subparsers.add_parser("logout", parents=[common], help="退出登录并清除本地保存的凭据")
    p.set_defaults(func=lambda a: commands.logout(a.profile))


def _add_api(subparsers, common) -> None:
    p = subparsers.add_parser("api", parents=[common], help="使用已保存的凭据调用面板 API")
    sub = p.add_subparsers(dest="api_action", metavar="<ACTION>", required=True)

    q = sub.add_parser("list", help="列出 API 路由(内置 v2.4.1 清单 + 当前面板 Swagger)")
    q.add_argument("--filter", default="", help="按路径、处理器、摘要或标签过滤")
    q.set_defaults(func=lambda a: catalog.list_routes(a.profile, a.filter))

    q = sub.add_parser("describe", help="查看指定 API 的定义和请求模型")
    q.add_argument("method", help="HTTP 方法,如 GET / POST")
    q.add_argument("path", help="API 路径,可省略 /api/v2 前缀")
    q.set_defaults(func=lambda a: catalog.describe(a.profile, a.method, a.path))

    q = sub.add_parser("get", help="GET 请求,如: get core/auth/current")
    q.add_argument("path")
    q.set_defaults(func=lambda a: commands.api_call(a.profile, "GET", a.path, None))

    q = sub.add_parser("post", help="POST 请求")
    q.add_argument("path")
    q.add_argument("-b", "--body", default=None, help="JSON 请求体")
    q.add_argument("--body-file", dest="body_file", default=None, metavar="PATH",
                   help="从文件读取 JSON 请求体(- 表示标准输入),避免敏感内容出现在命令行")
    q.set_defaults(func=lambda a: commands.api_call(a.profile, "POST", a.path, a.body, a.body_file))

    q = sub.add_parser("put", help="PUT 请求")
    q.add_argument("path")
    q.add_argument("-b", "--body", default=None, help="JSON 请求体")
    q.add_argument("--body-file", dest="body_file", default=None, metavar="PATH",
                   help="从文件读取 JSON 请求体(- 表示标准输入),避免敏感内容出现在命令行")
    q.set_defaults(func=lambda a: commands.api_call(a.profile, "PUT", a.path, a.body, a.body_file))

    q = sub.add_parser("delete", help="DELETE 请求")
    q.add_argument("path")
    q.set_defaults(func=lambda a: commands.api_call(a.profile, "DELETE", a.path, None))


def _add_apps(subparsers, common) -> None:
    p = subparsers.add_parser("apps", parents=[common], help="应用商店操作")
    sub = p.add_subparsers(dest="apps_action", metavar="<ACTION>", required=True)

    q = sub.add_parser("list", help="列出可安装应用")
    q.add_argument("--page", type=int, default=1, help="页码(默认 1)")
    q.add_argument("--page-size", dest="page_size", type=int, default=50, help="每页数量(默认 50)")
    q.add_argument("--name", default=None, help="按名称模糊搜索")
    q.add_argument("--type", default=None, help="按应用类型过滤(如 website / runtime / tool)")
    q.add_argument("--tag", default=None, help="按标签过滤(如 AI / Database / Website)")
    q.add_argument("--installed", action="store_true", help="只显示已安装的应用")
    q.add_argument("--all", action="store_true", help="拉取全部应用")
    q.set_defaults(
        func=lambda a: apps.list_apps(
            a.profile,
            apps.AppsQuery(
                page=a.page, page_size=a.page_size, name=a.name, type=a.type,
                tag=a.tag, installed=a.installed, all=a.all,
            ),
        )
    )

    q = sub.add_parser("install", help="安装指定应用")
    q.add_argument("name", help="应用名称(key),如 openresty / mysql / redis")
    q.add_argument("--version", default=None, help="指定安装版本(默认使用最新版本)")
    q.add_argument("--param", action="append", type=parse_kv, default=[], metavar="K=V",
                   help="覆盖安装参数,格式 KEY=VALUE,可多次指定")
    q.add_argument("--wait", action="store_true", help="等待安装完成并显示进度")
    q.add_argument("--timeout", type=int, default=600, help="等待超时时间(秒,默认 600)")
    q.add_argument("--no-pull", action="store_false", dest="pull_image", default=True,
                   help="不联网拉取镜像,直接使用本地已有镜像(离线安装包场景)")
    q.set_defaults(
        func=lambda a: apps.install(
            a.profile, a.name,
            apps.InstallOptions(version=a.version, params=a.param, wait=a.wait,
                                timeout=a.timeout, pull_image=a.pull_image),
        )
    )

    q = sub.add_parser("tags", help="列出所有应用分类标签")
    q.set_defaults(func=lambda a: apps.tags(a.profile))

    q = sub.add_parser("sync-local", help="同步/注册本地应用商店(离线安装包场景)")
    q.add_argument("--wait", action="store_true", help="等待同步任务完成")
    q.add_argument("--timeout", type=int, default=60, help="等待超时时间(秒,默认 60)")
    q.set_defaults(
        func=lambda a: apps.sync_local(
            a.profile, apps.SyncLocalOptions(wait=a.wait, timeout=a.timeout)
        )
    )

    q = sub.add_parser("info", help="查看已安装应用的详细信息(含数据库/Redis 连接信息)")
    q.add_argument("name", help="已安装应用的名称,如 mysql / redis")
    q.set_defaults(func=lambda a: apps.info(a.profile, a.name))

    q = sub.add_parser("config", help="查看或修改已安装应用的配置参数")
    q.add_argument("name", help="已安装应用的名称,如 mysql / redis / openresty")
    q.add_argument("--set", action="append", type=parse_kv, default=[], metavar="K=V",
                   help="修改配置项,格式 KEY=VALUE,可多次指定;不带此参数时仅查看")
    q.add_argument("--compose", action="store_true", help="同时显示 docker-compose 内容")
    q.set_defaults(
        func=lambda a: apps.config(
            a.profile, a.name, apps.ConfigOptions(set=a.set, show_compose=a.compose)
        )
    )

    q = sub.add_parser("password", help="修改数据库 root 密码(mysql / mariadb / redis)")
    q.add_argument("name", help="数据库应用名称,如 mysql / redis")
    q.add_argument("value", help="新密码")
    q.set_defaults(func=lambda a: apps.password(a.profile, a.name, a.value))


def _add_web(subparsers, common) -> None:
    p = subparsers.add_parser("web", parents=[common], help="网站(OpenResty/nginx)操作")
    sub = p.add_subparsers(dest="web_action", metavar="<ACTION>", required=True)

    q = sub.add_parser("list", help="列出所有网站/域名")
    q.set_defaults(func=lambda a: web.list(a.profile))

    q = sub.add_parser("create", help="新建网站(可一步完成反代 + HTTPS + SPA 回退)")
    q.add_argument("domain", help="域名,如 test3.example.com")
    q.add_argument("--type", default="static",
                   help="网站类型: static / deployment / proxy / runtime / stream(默认 static)")
    q.add_argument("--alias", default=None, help="网站别名(默认取域名首段)")
    q.add_argument("--remark", default=None, help="备注")
    q.add_argument("--proxy", default=None,
                   help="反代后端地址(提供时自动使用 proxy 类型),如 http://127.0.0.1:3500")
    q.add_argument("--ssl-id", dest="ssl_id", type=int, default=None,
                   help="创建时直接启用 HTTPS,值为证书 ID(web ssl / 面板证书列表)")
    q.add_argument("--http-config", dest="http_config", default=None,
                   help="HTTP 处理方式: HTTPSOnly / HTTPToHTTPS / HTTPAlso(默认 HTTPToHTTPS)")
    q.add_argument("--spa", action="store_true",
                   help="启用 SPA 前端路由回退(React/Vue 刷新深层路由不再 404,仅反代网站)")
    q.add_argument("--spa-api-prefixes", dest="spa_api_prefixes", default=None,
                   help="SPA 回退排除的 API 路径前缀,逗号分隔(默认 api,auth,health,linear)")
    q.set_defaults(
        func=lambda a: web.create(
            a.profile, a.domain,
            web.CreateOptions(
                type=a.type, alias=a.alias, remark=a.remark, proxy=a.proxy,
                ssl_id=a.ssl_id, http_config=a.http_config,
                spa=a.spa, spa_api_prefixes=a.spa_api_prefixes,
            ),
        )
    )

    q = sub.add_parser("config", help="读取或修改指定域名的 nginx 配置")
    q.add_argument("domain", help="域名,如 test.local")
    q.add_argument("--file", default=None, help="用本地文件内容更新 nginx 配置(不带此参数时仅读取)")
    q.set_defaults(func=lambda a: web.config(a.profile, a.domain, a.file))

    q = sub.add_parser("https", help="查看或设置网站 HTTPS")
    q.add_argument("domain", help="域名,如 test.local")
    q.add_argument("--enable", action="store_true",
                   help="开启 HTTPS;搭配 --ssl-id 或 --cert/--key 使用")
    q.add_argument("--disable", action="store_true", help="关闭 HTTPS")
    q.add_argument("--ssl-id", dest="ssl_id", type=int, default=None,
                   help="证书 ID(web ssl 申请成功后返回)")
    q.add_argument("--cert", default=None, help="证书 PEM 文件(手动导入证书时使用)")
    q.add_argument("--key", default=None, help="私钥 PEM 文件(手动导入证书时使用)")
    q.add_argument("--http-config", dest="http_config", default="HTTPToHTTPS",
                   help="HTTP 处理方式: HTTPSOnly / HTTPToHTTPS / HTTPAlso(默认 HTTPToHTTPS)")
    q.set_defaults(
        func=lambda a: web.https(
            a.profile, a.domain,
            web.HttpsOptions(
                enable=(False if a.disable else (True if (a.enable or a.ssl_id or a.cert) else None)),
                ssl_id=a.ssl_id,
                http_config=a.http_config,
                cert_file=a.cert,
                key_file=a.key,
            ),
        )
    )

    q = sub.add_parser("spa", help="为网站启用/关闭 SPA 前端路由回退(刷新深层路由不再 404)")
    q.add_argument("domain", help="域名,如 test.local")
    q.add_argument("--proxy", default=None,
                   help="后端地址(默认自动读取网站的反代目标),如 http://127.0.0.1:3500")
    q.add_argument("--api-prefixes", dest="api_prefixes", default=None,
                   help="回退排除的 API 路径前缀,逗号分隔(默认 api,auth,health,linear)")
    q.add_argument("--remove", action="store_true", help="关闭 SPA 回退")
    q.set_defaults(
        func=lambda a: web.spa(a.profile, a.domain, a.proxy, a.api_prefixes, a.remove)
    )

    q = sub.add_parser("delete", help="删除网站")
    q.add_argument("domain", help="域名,如 test.local")
    q.add_argument("--force", action="store_true",
                   help="遇到面板 record not found 已知问题时,临时改名应用 key 完成删除(需与面板同机)")
    q.set_defaults(func=lambda a: web.delete(a.profile, a.domain, a.force))

    q = sub.add_parser("repair", help="修复已知兼容问题(本地应用路径软链,面板报 500 时使用)")
    q.set_defaults(func=lambda a: web.repair(a.profile))

    q = sub.add_parser("ssl", help="申请 SSL 证书(Let's Encrypt)")
    q.add_argument("domain", help="域名,如 test.local")
    q.add_argument("--email", required=True, help="ACME 账号邮箱")
    q.add_argument("--provider", default="http", help="验证方式: http / dnsAccount(默认 http)")
    q.add_argument("--key-type", dest="key_type", default="RSA2048", help="密钥类型(默认 RSA2048)")
    q.add_argument("--no-auto-renew", dest="no_auto_renew", action="store_true",
                   help="禁用自动续期(默认开启)")
    q.set_defaults(
        func=lambda a: web.ssl(
            a.profile, a.domain,
            web.SslOptions(
                email=a.email, provider=a.provider, key_type=a.key_type,
                auto_renew=not a.no_auto_renew,
            ),
        )
    )

    q = sub.add_parser("upload", help="上传本地文件到网站目录")
    q.add_argument("domain", help="域名,如 test.local")
    q.add_argument("file", help="本地文件路径")
    q.add_argument("--to", default="", help="网站目录下的子目录(默认网站根目录)")
    q.set_defaults(func=lambda a: web.upload(a.profile, a.domain, a.file, a.to))

    q = sub.add_parser("extract", help="解压网站目录中的压缩包")
    q.add_argument("domain", help="域名,如 test.local")
    q.add_argument("archive", help="压缩包路径(相对于网站目录,或以 / 开头的绝对路径)")
    q.add_argument("--to", default="", help="解压目标子目录(默认网站根目录)")
    q.add_argument("--type", default="", help="压缩类型(默认按扩展名识别): zip / tar.gz / tar / gz ...")
    q.set_defaults(func=lambda a: web.extract(a.profile, a.domain, a.archive, a.to, a.type))


def _add_db(subparsers, common) -> None:
    p = subparsers.add_parser("db", parents=[common], help="数据库操作(MySQL 数据库/用户管理)")
    sub = p.add_subparsers(dest="db_action", metavar="<ACTION>", required=True)

    q = sub.add_parser("list", help="列出数据库/实例")
    q.add_argument("--type", default="mysql", help="类型: mysql / redis(默认 mysql)")
    q.add_argument("--instance", default=None, help="数据库实例名(默认与 --type 相同)")
    q.set_defaults(func=lambda a: db.list(a.profile, a.type, a.instance))

    q = sub.add_parser("create", help="创建数据库(MySQL)")
    q.add_argument("name", help="数据库名")
    q.add_argument("--user", default=None, help="同时创建的用户名(可选)")
    q.add_argument("--password", default=None, help="用户密码(可选)")
    q.add_argument("--instance", default="mysql", help="MySQL 实例名(默认 mysql)")
    q.add_argument("--format", default="utf8mb4", help="字符集(默认 utf8mb4)")
    q.add_argument("--permission", default="%", help="用户访问权限(默认 %%)")
    q.add_argument("--remark", default=None, help="备注")
    q.set_defaults(
        func=lambda a: db.create(
            a.profile, a.name, a.user, a.password, a.instance, a.format, a.permission, a.remark
        )
    )

    q = sub.add_parser("delete", help="删除数据库(MySQL)")
    q.add_argument("name", help="数据库名")
    q.add_argument("--force", action="store_true", help="强制删除(忽略被引用资源)")
    q.add_argument("--type", default="mysql", help="数据库类型: mysql / mariadb")
    q.add_argument("--instance", default=None, help="数据库实例名(默认与 --type 相同)")
    q.set_defaults(
        func=lambda a: db.delete(
            a.profile, a.name, a.instance or a.type, a.type, a.force
        )
    )

    q = sub.add_parser("users", help="列出数据库用户")
    q.add_argument("--instance", default="mysql", help="MySQL 实例名(默认 mysql)")
    q.set_defaults(func=lambda a: db.users(a.profile, a.instance))

    q = sub.add_parser("user", help="用户管理")
    user_sub = q.add_subparsers(dest="user_action", metavar="<ACTION>", required=True)

    r = user_sub.add_parser("add", help="创建用户")
    r.add_argument("username", help="用户名")
    r.add_argument("password", help="密码")
    r.add_argument("--host", default="%", help="允许的主机(默认 %%)")
    r.add_argument("--dbs", default="", help="授权的数据库(逗号分隔,可选)")
    r.add_argument("--remark", default=None, help="备注")
    r.add_argument("--instance", default="mysql", help="MySQL 实例名(默认 mysql)")
    r.set_defaults(
        func=lambda a: db.user_add(
            a.profile, a.username, a.password, a.instance, a.host,
            [s.strip() for s in a.dbs.split(",") if s.strip()], a.remark,
        )
    )

    r = user_sub.add_parser("del", help="删除用户")
    r.add_argument("username", help="用户名")
    r.add_argument("--host", default="%", help="主机(默认 %%)")
    r.add_argument("--instance", default="mysql", help="MySQL 实例名(默认 mysql)")
    r.set_defaults(func=lambda a: db.user_del(a.profile, a.username, a.instance, a.host))

    r = user_sub.add_parser("passwd", help="修改用户密码")
    r.add_argument("username", help="用户名")
    r.add_argument("password", help="新密码")
    r.add_argument("--host", default="%", help="主机(默认 %%)")
    r.add_argument("--instance", default="mysql", help="MySQL 实例名(默认 mysql)")
    r.set_defaults(
        func=lambda a: db.user_passwd(a.profile, a.username, a.password, a.instance, a.host)
    )

    q = sub.add_parser("passwd", help="修改数据库 root 密码(MySQL)")
    q.add_argument("password", help="新密码")
    q.add_argument("--instance", default="mysql", help="MySQL 实例名(默认 mysql)")
    q.set_defaults(func=lambda a: db.root_passwd(a.profile, a.instance, a.password))


def _add_redis(subparsers, common) -> None:
    p = subparsers.add_parser("redis", parents=[common], help="Redis 实例操作")
    sub = p.add_subparsers(dest="redis_action", metavar="<ACTION>", required=True)

    q = sub.add_parser("list", help="列出 Redis 实例")
    q.set_defaults(func=lambda a: redis.list(a.profile))

    q = sub.add_parser("info", help="查看 Redis 实例状态与配置")
    q.add_argument("name", help="Redis 实例名(默认 redis)")
    q.set_defaults(func=lambda a: redis.info(a.profile, a.name))

    q = sub.add_parser("config", help="读取或修改 Redis 配置")
    q.add_argument("name", help="Redis 实例名(默认 redis)")
    q.add_argument("--set", action="append", type=parse_kv, default=[], metavar="K=V",
                   help="修改配置项,格式 KEY=VALUE(timeout/maxclients/maxmemory),可多次指定")
    q.set_defaults(func=lambda a: redis.config(a.profile, a.name, a.set))

    q = sub.add_parser("passwd", help="修改 Redis 密码")
    q.add_argument("name", help="Redis 实例名(默认 redis)")
    q.add_argument("password", help="新密码")
    q.set_defaults(func=lambda a: redis.passwd(a.profile, a.name, a.password))


def _add_firewall(subparsers, common) -> None:
    p = subparsers.add_parser("firewall", parents=[common], help="防火墙操作")
    sub = p.add_subparsers(dest="firewall_action", metavar="<ACTION>", required=True)

    q = sub.add_parser("status", help="查看防火墙状态")
    q.set_defaults(func=lambda a: firewall.status(a.profile))

    for name, op, help_text in (
        ("start", "start", "启动防火墙"),
        ("stop", "stop", "停止防火墙"),
        ("restart", "restart", "重启防火墙"),
        ("allow-ping", "allow-ping", "允许 Ping"),
        ("ban-ping", "ban-ping", "禁止 Ping"),
    ):
        q = sub.add_parser(name, help=help_text)
        q.set_defaults(func=lambda a, op=op: firewall.operate(a.profile, op))

    q = sub.add_parser("list", help="列出防火墙规则")
    q.add_argument("--type", default="port", help="规则类型: port / address(默认 port)")
    q.add_argument("--info", default="", help="按端口/地址关键字过滤")
    q.add_argument("--strategy", default="", help="按策略过滤: accept / drop")
    q.set_defaults(
        func=lambda a: firewall.list(
            a.profile, firewall.ListOptions(type=a.type, info=a.info, strategy=a.strategy)
        )
    )

    q = sub.add_parser("port", help="端口规则")
    port_sub = q.add_subparsers(dest="port_action", metavar="<ACTION>", required=True)

    r = port_sub.add_parser("add", help="添加端口规则")
    r.add_argument("port", help="端口,如 8080 或 8080-8090")
    r.add_argument("--protocol", default="tcp", help="协议: tcp / udp / tcp/udp(默认 tcp)")
    r.add_argument("--strategy", default="accept", help="策略: accept / drop(默认 accept)")
    r.add_argument("--source", default="", help="来源地址(默认 Anywhere)")
    r.add_argument("--desc", default="", help="备注")
    r.set_defaults(
        func=lambda a: firewall.port_add(
            a.profile, a.port,
            firewall.PortOptions(
                protocol=a.protocol, strategy=a.strategy, source=a.source, desc=a.desc
            ),
        )
    )

    r = port_sub.add_parser("del", help="删除端口规则")
    r.add_argument("port", help="端口")
    r.add_argument("--protocol", default="tcp", help="协议(需与添加时一致,默认 tcp)")
    r.add_argument("--strategy", default="accept", help="策略(需与添加时一致,默认 accept)")
    r.add_argument("--source", default="", help="来源地址(默认 Anywhere)")
    r.set_defaults(
        func=lambda a: firewall.port_del(
            a.profile, a.port,
            firewall.PortOptions(
                protocol=a.protocol, strategy=a.strategy, source=a.source, desc=""
            ),
        )
    )

    q = sub.add_parser("ip", help="IP 规则")
    ip_sub = q.add_subparsers(dest="ip_action", metavar="<ACTION>", required=True)

    r = ip_sub.add_parser("add", help="添加 IP 规则")
    r.add_argument("address", help="IP 或网段,如 1.2.3.4 或 1.2.3.0/24")
    r.add_argument("--strategy", default="drop", help="策略: accept / drop(默认 drop)")
    r.add_argument("--desc", default="", help="备注")
    r.set_defaults(
        func=lambda a: firewall.ip_add(
            a.profile, a.address, firewall.IpOptions(strategy=a.strategy, desc=a.desc)
        )
    )

    r = ip_sub.add_parser("del", help="删除 IP 规则")
    r.add_argument("address", help="IP 或网段")
    r.add_argument("--strategy", default="drop", help="策略(默认 drop)")
    r.set_defaults(
        func=lambda a: firewall.ip_del(
            a.profile, a.address, firewall.IpOptions(strategy=a.strategy, desc="")
        )
    )

    q = sub.add_parser("forward", help="端口转发")
    fwd_sub = q.add_subparsers(dest="forward_action", metavar="<ACTION>", required=True)

    r = fwd_sub.add_parser("add", help="添加端口转发")
    r.add_argument("port", help="监听端口")
    r.add_argument("--to", required=True, help="转发目标,格式 IP:端口,如 10.0.0.2:8080")
    r.add_argument("--protocol", default="tcp", help="协议: tcp / udp / tcp/udp(默认 tcp)")
    r.add_argument("--interface", default="", help="网卡(可选)")
    r.set_defaults(
        func=lambda a: firewall.forward_add(
            a.profile, a.port,
            firewall.ForwardOptions(
                protocol=a.protocol, target=a.to, interface=a.interface, num=""
            ),
        )
    )

    r = fwd_sub.add_parser("del", help="删除端口转发")
    r.add_argument("port", help="监听端口")
    r.add_argument("--to", required=True, help="转发目标,格式 IP:端口")
    r.add_argument("--protocol", default="tcp", help="协议(默认 tcp)")
    r.add_argument("--interface", default="", help="网卡(可选)")
    r.add_argument("--num", default="", help="规则编号(firewall list --type forward 中的编号)")
    r.set_defaults(
        func=lambda a: firewall.forward_del(
            a.profile, a.port,
            firewall.ForwardOptions(
                protocol=a.protocol, target=a.to, interface=a.interface, num=a.num
            ),
        )
    )

    q = sub.add_parser("docker", help="Docker 端口守护(新版统一防火墙 API)")
    docker_sub = q.add_subparsers(dest="docker_action", metavar="<ACTION>", required=True)

    r = docker_sub.add_parser("status", help="查看 Docker 端口守护状态(后端/绑定/初始化)")
    r.set_defaults(func=lambda a: firewall.docker_status(a.profile))

    r = docker_sub.add_parser("ports", help="列出 Docker 已发布端口与防护策略")
    r.set_defaults(func=lambda a: firewall.docker_ports(a.profile))

    r = docker_sub.add_parser("sync", help="同步 Docker 端口防护规则")
    r.set_defaults(func=lambda a: firewall.docker_sync(a.profile))

    r = docker_sub.add_parser("operate", help="初始化/绑定/解绑 Docker 端口守护")
    r.add_argument("--operation", default="init", help="操作: init / bind / unbind(默认 init)")
    r.set_defaults(func=lambda a: firewall.docker_operate(a.profile, a.operation))

    r = docker_sub.add_parser("allow", help="放行已发布端口(仅允许指定来源访问)")
    r.add_argument("port", help="宿主端口,如 8080 或 0.0.0.0:8080")
    r.add_argument("--protocol", default="tcp", help="协议: tcp / udp / tcp/udp(默认 tcp)")
    r.add_argument("--sources", default="", help="允许访问的来源 IP(逗号分隔,必填)")
    r.add_argument("--desc", default="", help="备注")
    r.set_defaults(
        func=lambda a: firewall.docker_apply(
            a.profile, a.port, a.protocol, a.sources, a.desc, "allow_sources"
        )
    )

    r = docker_sub.add_parser("deny", help="拒绝指定来源访问已发布端口(不带 --sources 时拒绝所有来源)")
    r.add_argument("port", help="宿主端口,如 8080 或 0.0.0.0:8080")
    r.add_argument("--protocol", default="tcp", help="协议: tcp / udp / tcp/udp(默认 tcp)")
    r.add_argument("--sources", default="", help="拒绝访问的来源 IP(逗号分隔;为空则拒绝所有来源)")
    r.add_argument("--desc", default="", help="备注")
    r.set_defaults(
        func=lambda a: firewall.docker_apply(
            a.profile, a.port, a.protocol, a.sources, a.desc,
            "deny_all" if not a.sources.strip() else "deny_sources",
        )
    )

    r = docker_sub.add_parser("policy-del", help="删除 Docker 端口守护策略")
    r.add_argument("--uuid", required=True, help="策略 UUID(firewall docker ports 中查看)")
    r.set_defaults(func=lambda a: firewall.docker_policy_del(a.profile, a.uuid))

    q = sub.add_parser("ipv6", help="IPv6 防火墙开关(1Panel v2.4.0+)")
    ipv6_sub = q.add_subparsers(dest="ipv6_action", metavar="<ACTION>", required=True)
    r = ipv6_sub.add_parser("status", help="查看 IPv6 防火墙是否启用")
    r.set_defaults(func=lambda a: firewall.ipv6_status(a.profile))
    r = ipv6_sub.add_parser("enable", help="启用 IPv6 防火墙(异步任务)")
    r.set_defaults(func=lambda a: firewall.ipv6_operate(a.profile, True))
    r = ipv6_sub.add_parser("disable", help="禁用 IPv6 防火墙(异步任务)")
    r.set_defaults(func=lambda a: firewall.ipv6_operate(a.profile, False))

    q = sub.add_parser("batch", help="批量添加端口放行")
    q.add_argument("ports", help="添加多个端口,逗号分隔,如 8080,9090")
    q.add_argument("--protocol", default="tcp", help="协议: tcp / udp / tcp/udp(默认 tcp)")
    q.add_argument("--strategy", default="accept", help="策略: accept / drop(默认 accept)")
    q.add_argument("--source", default="", help="来源地址(默认 Anywhere)")
    q.add_argument("--desc", default="", help="备注")
    q.set_defaults(
        func=lambda a: firewall.batch_add(
            a.profile, a.ports,
            firewall.PortOptions(
                protocol=a.protocol, strategy=a.strategy, source=a.source, desc=a.desc
            ),
        )
    )


def _add_doctor(subparsers, common) -> None:
    p = subparsers.add_parser(
        "doctor", parents=[common], aliases=["check"],
        help="检查环境配置是否合格(会尝试登录并给出修复指导)",
    )
    p.add_argument("--force", action="store_true", help="强制重新登录以验证 .env 中的账号密码")
    p.set_defaults(
        func=lambda a: doctor.doctor(a.profile, a.force, getattr(a, "env", None))
    )


# ---------------------------------------------------------------------------
# 命令实现
# ---------------------------------------------------------------------------


def _cmd_init(args: argparse.Namespace) -> None:
    initcmd.run_init(
        output=args.output,
        force=args.force,
        host=args.host,
        print_only=args.print_only,
    )


def _cmd_login(args: argparse.Namespace) -> None:
    overrides = config.ConfigOverrides(
        url=args.url,
        entrance=args.entrance,
        username=args.user,
        password=args.password,
        mfa_code=args.mfa,
        captcha=args.captcha,
        captcha_id=args.captcha_id,
        insecure=args.insecure,
    )
    cfg = config.PanelConfig.from_env(overrides)
    auth.login(cfg, args.profile)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    # 避免管道输出被提前关闭(如 | head)时出现 Broken pipe 报错
    try:
        signal_ = None
        import signal

        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
        del signal_
    except (ImportError, AttributeError, ValueError, OSError):
        pass

    # 与 Rust 版一致:stdout 按行缓冲,保证与 stderr 的输出顺序稳定
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, ValueError, OSError):
        pass

    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        # SUPPRESS 默认值:未提供时属性不存在,回退到默认 profile
        env_path = config.load_env_file(getattr(args, "env", None))
        del env_path
        config.set_node_override(getattr(args, "node", None))
        redact.configure(
            show_secrets=bool(getattr(args, "show_secrets", False))
            or bool(config.env_flag(config.ENV_SHOW_SECRETS))
        )
        if not hasattr(args, "profile"):
            args.profile = "default"
        func = getattr(args, "func", None)
        if func is None:
            parser.print_help()
            return 1
        func(args)
        return 0
    except CliError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    except BrokenPipeError:
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
        except OSError:
            pass
        return 141


if __name__ == "__main__":
    sys.exit(main())
