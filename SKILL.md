---
name: 1panel-cli
description: >-
  管理 1Panel v2 面板的命令行工具(1panel-cli,Python 版)。当用户提到 1Panel / 面板 / 宝塔
  类似的服务器管理面板,或需要登录面板、安装/管理应用(openresty、mysql、redis、
  phpmyadmin 等)、查看应用连接信息、修改数据库密码、创建/管理网站与域名(含反向代理、
  HTTPS、SPA 前端路由回退)、读写
  nginx 配置、配置 HTTPS/SSL、上传解压网站文件、管理 MySQL/MariaDB 数据库与用户、
  管理 Redis、防火墙端口/IP/转发规则,或发现和调用面板 API 时使用本 skill。执行前先
  运行 `1panel-cli doctor`;原始 API 调用前先用 `api list` / `api describe` 查询,不要猜测路径。
  请求失败或参数不确定时,阅读随技能附带的 1Panel v2.3.2 完整源码(技能目录内 `source/`)。
compatibility: opencode
metadata:
  tool: 1panel-cli
  panel: 1Panel
  language: python
  verified-panel-version: v2.3.2
---

# 1panel-cli — 1Panel v2 面板管理(Python)

通过 1Panel 官方 API 管理面板。当前验证基线是 **1Panel v2.3.2 stable**;内置 API 路由清单即由随技能附带的 v2.3.2 源码 `source/` 生成(787 条)。代码同时保留了旧版统一防火墙接口(带规则检查)与 v2.2.x 旧防火墙接口的自动探测:连接 v2.3.2 及更新版本时使用统一规则接口(写操作为异步任务,CLI 自动轮询并汇报结果),连接旧版面板时自动回退旧接口。

实现为**纯 Python 标准库**,无需安装任何第三方包。

## 环境

按顺序探测入口,使用第一个存在的路径:

1. `<本 skill 目录>/bin/1panel-cli`
2. `<仓库>/bin/1panel-cli`
3. `PATH` 中的 `1panel-cli`

要求系统自带 `python3`(3.9+,验证环境 3.12),入口脚本已带 shebang,可直接执行。若入口无执行权限,可用 `python3 <路径>/bin/1panel-cli` 运行,或 `chmod +x`。

**首次使用**:面板与本工具在同一台主机时,运行 `1panel-cli init` 自动生成 `.env`(执行 `sudo 1pctl user-info` 取地址/用户名,密码已掩码时读取面板数据库解密),无需手填。若无法本机执行,再把 `.env.example` 复制为 `.env` 并至少填写 `PANEL_URL`、`PANEL_USERNAME`、`PANEL_PASSWORD`;启用安全入口时填写 `PANEL_ENTRANCE`。多节点面板可设置 `PANEL_NODE` 或传全局参数 `--node`。会话保存在 `~/.config/1panel-cli/<profile>.json`。全局参数(`--env` / `--profile` / `--node`)放在子命令前后均可。

## 操作流程

任何面板操作前先执行:

```sh
1panel-cli doctor
```

仅在六项检查全部通过后继续。优先使用 `apps`、`db`、`redis`、`web`、`firewall` 等专用命令。专用命令无法覆盖时:

```sh
1panel-cli api list --filter template
1panel-cli api describe POST websites/templates/search
1panel-cli api post websites/templates/search --body '{"page":1,"pageSize":20}'
```

`api list` 基于 1Panel v2.3.2 源码(技能目录内 `source/`)生成的 787 条路由清单,并尝试读取当前面板 Swagger;它能显示 Swagger 未收录但源码已注册的路由。

## 源码参考 `source/`

技能目录内附带 **1Panel v2.3.2 完整源码** `source/`(与内置 787 条路由清单同源)。当请求失败(404、方法不符、参数校验错误、返回 HTML)或参数不确定时,**先查源码再重试,不要猜测**:

| 查阅内容 | 位置 |
|---|---|
| 路由注册与参数写法 | `source/agent/router/*.go`、`source/core/router/*.go` |
| 请求/响应 DTO 字段与校验 | `source/agent/app/dto/*.go`(含 `request/`、`response/`)、`source/core/app/dto/*.go` |
| 业务行为(默认值 / 副作用 / 异步任务) | `source/agent/app/service/*.go`、`source/core/app/service/*.go` |
| 鉴权 / CSRF / 请求头语义 | `source/core/middleware/*.go`、`source/agent/middleware/*.go` |
| Swagger 模型 | `source/core/cmd/server/docs/swagger.json` |
| 前端实际请求参数 | `source/frontend/src/api/` |

`api describe` 输出中的 `sourcePath`(如 `source/agent/router/ro_website.go`)就是可直接阅读的文件路径。

## 常用命令

```sh
# 初始化(生成 .env,无需手填)
1panel-cli init

# 会话与版本
1panel-cli login
1panel-cli status
1panel-cli doctor --force
1panel-cli --profile prod --node local status

# 应用
1panel-cli apps list --installed
1panel-cli apps install mysql --wait
1panel-cli apps info mysql
1panel-cli apps config openresty
1panel-cli apps password mysql '新密码'

# MySQL / MariaDB
1panel-cli db list
1panel-cli db list --type mariadb --instance mariadb
1panel-cli db create appdb --user app --password 'App@123'
1panel-cli db delete appdb --type mariadb --instance mariadb
1panel-cli db users --instance mysql

# Redis
1panel-cli redis info redis
1panel-cli redis config redis --set maxmemory=268435456
1panel-cli redis passwd redis '新密码'

# 网站
1panel-cli web list
1panel-cli web create example.com --type static
1panel-cli web create app.example.com --type proxy --proxy http://127.0.0.1:3500
# 一步完成「反代 + HTTPS + SPA 前端路由回退」(React/Vue 刷新深层路由不 404)
1panel-cli web create app.example.com --proxy http://127.0.0.1:3500 --ssl-id <证书ID> --spa
1panel-cli web config example.com
1panel-cli web config example.com --file ./nginx.conf
1panel-cli web https example.com --enable --ssl-id <ID>
1panel-cli web spa example.com [--proxy http://127.0.0.1:3500] [--remove]
1panel-cli web delete example.com [--force]
1panel-cli web repair
1panel-cli web ssl example.com --email admin@example.com
1panel-cli web upload example.com ./site.zip --to index
1panel-cli web extract example.com index/site.zip --to index

# 防火墙(v2.3.2 统一规则接口:写操作为异步任务,CLI 自动等待并汇报结果)
1panel-cli firewall status
1panel-cli firewall port add 8080 --protocol tcp
1panel-cli firewall port del 8080
1panel-cli firewall ip add 1.2.3.4 --strategy drop
1panel-cli firewall forward add 18080 --to 10.0.0.2:8080
1panel-cli firewall list --type forward

# 防火墙 Docker 端口守护(v2.3.x+)
1panel-cli firewall docker status
1panel-cli firewall docker ports
1panel-cli firewall docker allow 8080 --sources 1.2.3.4 --desc web
1panel-cli firewall docker deny 0.0.0.0:8080 --desc '对外关闭'

# API 发现与调用
1panel-cli api list --filter openclaw
1panel-cli api describe POST ai/agents/plugins/search
1panel-cli api get core/auth/current
1panel-cli api post apps/search --body '{"page":1,"pageSize":10}'
```

完整参数见 `commands.md`。

## 关键约定

1. 专用命令优先于原始 `api`;原始调用必须先查询 `api list` / `api describe`。
2. `api` 路径相对 `/api/v2`;也接受完整的 `/api/v2/...` 路径。
3. 登录使用本地密码源 `authSource=local`;LDAP/OIDC/SAML 等企业认证登录不由此 CLI 代办。
4. 多节点面板务必选择正确的 `PANEL_NODE` / `--node`;未指定时发送 `CurrentNode: local`。
5. 防火墙接口有三种形态,由 CLI 自动探测:1Panel v2.3.2+ 走统一规则接口(`/hosts/firewall/rules*`),写操作为异步任务(返回 `taskID`,CLI 自动轮询 `logs/tasks/search` 并在任务失败时报错);中间版本创建前会先调用 `rules/check`(`checkFlag` 签名);v2.2.x 走旧门面接口。不要绕过探测手拼写请求。
6. nginx 配置保存后面板会 reload;写入前先读取原配置。生产密码与 Cookie 不应写入日志或提交仓库。
7. Swagger 不是完整路由清单;以 `source/` 的 1Panel v2.3.2 源码为准(内置清单 `panel_cli/data/api-v2.3.2.json` 即由其生成)。企业版闭源扩展接口不在该清单内。
8. `firewall docker` 等接口仅在 v2.3.x+ 面板存在;v2.2.x 会自动回退旧防火墙接口。v2.3.2 的 Docker 守护策略只对 `managementTarget=container_guard` 的端口生效,流量由宿主机防火墙接管的端口要用 `firewall port/ip` 规则(`firewall docker ports` 会标注)。
9. 请求失败或参数不确定时,按上表到 `source/` 阅读对应 router / DTO / service 源码确认路径、字段与行为,再修正调用。
10. 应用 key 与实例名自动兼容 `local` 前缀:配套离线包(patched agent)的本地应用以规范 key 注册
    (`openresty`/`mysql`/`redis`);历史面板数据可能是 `localopenresty`/`localmysql` 等写法,
    `apps` 命令与 `db`/`redis` 的实例名均会自动互相解析(两种写法可混用)。
11. 本地应用历史数据兼容(CLI 已自动处理,失败时用 `web repair` 排查):
    - nginx 相关接口固定读取 `<应用目录>/<key>/<name>`,而本地应用实际在 `<应用目录>/local/<key>/<name>` → web 命令会先自动建软链,否则 HTTPS / 伪静态等操作会报 500 或静默不生效;
    - 删除网站时旧版面板固定按 `openresty` 查询应用 key,会报 `record not found` → 用 `web delete <domain> --force`(需本工具与面板同机,自动备份 agent.db 并还原应用 key)。
12. 反代 React/Vue 等 SPA:后端对深层路由返回 404 时用 `--spa` / `web spa` 开启回退(自动写入 `proxy_intercept_errors on` + `error_page 404`,否则上游 404 不会触发回退);回退排除 API 前缀、静态资源与隐藏路径,默认 `api,auth,health,linear`,可用 `--spa-api-prefixes` 调整。
13. 本机用 curl 验证 127.0.0.1 上的网站时,注意 `http_proxy` / `https_proxy` 环境变量会拦截请求(表现为 503),加 `--noproxy '*'`。
14. `api` 响应默认脱敏:敏感字段(`privateKey` / `password` / `token` / `secret` 等)与任意位置出现的 PEM 私钥块输出为 `***REDACTED***` / `***REDACTED PRIVATE KEY***`,避免私钥、口令进入日志或智能体上下文;确需原值时显式加全局参数 `--show-secrets`(或 `PANEL_CLI_SHOW_SECRETS=1`)。含私钥的请求体用 `api post/put --body-file <文件>`(支持 `-` 读 stdin),不要把私钥写进命令行参数。
