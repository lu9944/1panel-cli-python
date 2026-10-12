# 兼容性说明:Python 版 × 1Panel v2.3.2

本文件记录 `1panel-cli`(Python 版)与 **1Panel v2.3.2 stable** 的接口核对结果,以及与旧版接口(v2.2.x 旧防火墙、中间版本 `rules/check`)的差异处理。

## 验证环境

| 项 | 值 |
|---|---|
| 面板版本 | v2.3.2(fcvm VM 内 `1panel-cli status` 实测:`v2.3.2 (cn)`) |
| 面板类型 | fcvm microVM 内的本地安装(安全入口 `1panel`,地址 `127.0.0.3:22123`) |
| CLI 实现 | Python 3.12,仅标准库 |
| 内置 API 清单 | 由 1Panel `v2.3.2` 源码生成:`panel_cli/data/api-v2.3.2.json`(787 条路由,Swagger 收录 756 条) |
| 面板源码 | 随技能附带于 `source/`(1Panel v2.3.2 完整源码;下表路径均相对该目录) |

## 防火墙接口三形态

v2.3.2 起防火墙写操作全部改为**异步任务**,且路由集合与 v2.2.x 完全不同。CLI 通过 `api_kind()` 运行时探测:

| 形态 | 探测依据 | 创建路径 | 删除路径 |
|---|---|---|---|
| `v231`(v2.3.2+) | 有 `GET /hosts/firewall/settings`,无 `POST /rules/check` | `POST /hosts/firewall/rules`(`items[].rule` + `sourceKind=user`,返回 `taskID`) | `POST /rules/delete`(返回 `taskID`) |
| `checked`(中间版本) | 有 `settings` 且有 `rules/check` | `POST /rules/check` 取 `checkFlag` → `POST /rules` | `POST /rules/delete`(同步返回成功/失败计数) |
| `legacy`(v2.2.x) | 无 `settings` | `POST /firewall/port|ip|batch` | 同左 |

v2.3.2 的异步任务由 CLI 轮询 `POST /api/v2/logs/tasks/search`(taskID 过滤),任务 `Failed` 时以 `errorMsg` 报错;覆盖规则创建/删除、端口转发、Docker 端口策略、防火墙启停。

### Docker 端口守护(v2.3.2 变化)

| 项 | v2.2.x/中间版本 | v2.3.2 |
|---|---|---|
| 策略写入 | `{"endpoints":[...],"mode":...,"sources":...,"description":...}` | `{"policies":[{family,hostIP,hostPort,protocol,mode,sources,description}]}` |
| 结果 | 同步 | 异步任务(`taskID`) |
| 流量接管识别 | 无 | 每个端口带 `trafficPath` / `managementTarget` / `managementReason` |

`managementTarget=host_firewall` 的端口(宿主机防火墙接管,典型为启用了 ufw/firewalld 后的 Docker userland proxy 映射)不能用 Docker 守护策略;服务端会拒绝,CLI 在提交前识别并跳过、提示改用 `firewall port/ip`。`needs_diagnosis`(无法判定路径)直接报错。策略来源按端口地址族拆分:某个已发布地址族缺少对应来源时报错,不会静默跳过。

### 其他防火墙接口变化

- 旧门面接口(`/firewall/search|port|ip|forward|batch|update/*|filter/rule/*|filter/chain/status`)在 v2.3.2 已移除;`POST /hosts/firewall/port` 变为面板内部接口(`UpdatePanelFirewallPort`,从面板探针调用返回 403 `internal agent endpoint cannot be proxied`)。
- 新增 `/firewall/rules/{search,reset,native/detail,adopt,sync,update,reorder}`、`/firewall/forward/{base,search,operate,enable}`、`/firewall/settings*`、`/firewall/docker/*`、终端会话与诊断路由等。
- `rules/search` 必须携带 `scopes`(或面板默认 scope);CLI 按后端生成 `iptables/nftables`(3 链 × 2 族)、`firewalld`(public zone)、`ufw`(incoming × 2 族)的 scope 列表。

## 关键接口核对(对照 `source/` 中的 v2.3.2 源码)

| 功能 | 接口 | 路由来源 | 结论 |
|---|---|---|---|
| 登录设置 | `GET /api/v2/core/auth/setting` | `core/router/ro_base.go` | ✅ |
| 验证码 | `GET /api/v2/core/auth/captcha`(`captchaID` 字段不变) | `core/router/ro_base.go` | ✅ |
| 登录 / MFA / 当前用户 / 登出 | `/core/auth/login|mfalogin|current|logout` | `core/router/ro_base.go` | ✅ |
| 面板版本 | `POST /api/v2/core/settings/search/base` | `core/router/ro_setting.go` | ✅ |
| 防火墙状态 | `POST /api/v2/hosts/firewall/base` | `agent/router/ro_host.go:25` | ✅(新增 `backend/ipv4/ipv6`) |
| 统一规则列表 | `POST /hosts/firewall/rules/search` | `agent/router/ro_host.go:37` | ✅(`InventoryItem{rule,desired,state,match}`) |
| 统一规则创建 | `POST /hosts/firewall/rules` | `agent/router/ro_host.go:41` | ✅ 异步任务 |
| 统一规则删除 | `POST /hosts/firewall/rules/delete` | `agent/router/ro_host.go:46` | ✅ 异步任务 |
| 端口转发 | `/hosts/firewall/forward/{search,operate,base}` | `agent/router/ro_host.go:33-36` | ✅ `operate` 异步任务 |
| 防火墙启停/Ping | `POST /hosts/firewall/operate` | `agent/router/ro_host.go:26` | ✅ 异步任务 |
| 后端设置 | `GET /hosts/firewall/settings` | `agent/router/ro_host.go:28` | ✅ |
| Docker 端口守护 | `/hosts/firewall/docker/{ports,endpoints,sync,operate,policies/batch,policies/delete/batch}` | `agent/router/ro_host.go:50-55` | ✅(写操作异步) |
| 数据库列表/增删/用户 | `/api/v2/databases*` | `agent/router/ro_database.go` | ✅(DTO 未变) |
| Redis 状态/配置/密码 | `/api/v2/databases/redis/*` | `agent/router/ro_database.go` | ✅(DTO 未变) |
| 网站列表/创建/配置/HTTPS/SSL | `/api/v2/websites*` | `agent/router/ro_website*.go` | ✅(请求/响应 DTO 未变) |
| 上传/解压 | `POST /api/v2/files/upload`、`/files/decompress` | `agent/router/ro_file.go` | ✅ |
| 应用商店/安装/任务查询 | `/api/v2/apps*`、`/logs/tasks/search` | `agent/router/ro_app.go` / `ro_log.go` | ✅(DTO 未变) |
| 终端会话/诊断(新增) | `/hosts/terminal/sessions/*`、`/core/hosts/diagnostics/*` | `agent/router/ro_host.go` / `core/router/ro_runtime_diagnostics.go` | ✅ |

DTO 差异审计(与 v2.2.5 源码 diff):`nginx.go` 仅新增 Brotli 相关键(`gzip_types/vary/proxied` 纳入 `http-per`);`request/file.go` 新增 `UseServerFilename`、`FileMove.TaskID`、`FileMoveStopReq`;`request/app.go` 新增字段均为 `json:"-"` 内部字段;`alert.go`/`terminal.go` 等为新增功能。CLI 使用的网站/应用/数据库/Redis 请求字段均未变化。

## 与 Rust 版(dev-v2 基线)的差异处理

1. **防火墙三形态探测**:Rust 版按 dev-v2 基线实现(带 `rules/check` 的检查流程)。Python 版在保留该流程的同时新增 v2.3.2 形态(无 check、异步任务)与 v2.2.x 旧接口,并对不同形态的输出与等待行为做了区分。
2. **内置路由清单**:Python 版内置 **v2.3.2 清单(787 条)**;`scripts/generate_api_catalog.py` 可用任意 1Panel 源码树重新生成。
3. **空规则列表兼容**:旧接口在无规则时可能返回 `{"items": null, "total": 0}`;Python 版将其视为空列表并正常打印表头与「共 0 条规则」。
4. **验证码字段名**:面板字段是 **`captchaID`**(大写 ID);Python 版按 `captchaID` 读取(兼容 `captchaId` 回退),已验证验证码登录。
5. **CurrentNode 编码对齐前端**:与前端一致采用 `quote(safe=!*'())`,并以「编码后经 QueryUnescape 可还原」作为单元测试不变式。
6. **`web ssl` 重复申请兼容**:`WebsiteSSLService.Create` 在创建记录后会**自动异步发起申请**(`source/agent/app/service/website_ssl.go`,`go w.ObtainSSL(...)`);CLI 仍调用 obtain 以兼容不自动申请的版本,但将「任务执行中/请勿重复执行」视为申请已发起,命令正常成功退出。

## 源码审计:CLI 全部接口 × v2.3.2 路由清单

CLI 源码中的全部 API 调用均存在于 `panel_cli/data/api-v2.3.2.json`(787 条路由);请求 DTO 字段在 v2.3.2 源码中逐一核对,未发现字段名/类型偏差。

| 接口 | DTO(源码) | CLI 字段 |
|---|---|---|
| `POST /apps/install` | `request.AppInstallCreate` + `AppContainerConfig` | appDetailId/params/name/taskID/advanced/allowPort/pullImage/cpuQuota/memoryLimit/memoryUnit/containerName/editCompose/dockerCompose/gpuConfig/specifyIP ✅ |
| `POST /apps/installed/params/update` | `request.AppInstalledUpdate` | installId/params/advanced ✅ |
| `POST /databases` / `del` / `users` / `change/password` / `redis/*` | `dto.MysqlDB*` / `ChangeDBInfo` / `ChangeRedisPass` / `RedisConfUpdate` | 与 v2.2.5 相同 ✅ |
| `POST /websites` | `request.WebsiteCreate` | type/alias/remark/webSiteGroupID/domains/appType/appInstallID/taskID/IPV6 ✅ |
| `POST /websites/nginx/update`、`/:id/https`、`/rewrite/update` | `request.WebsiteNginxUpdate` / `WebsiteHTTPSOp` | 与 v2.2.5 相同 ✅ |
| `POST /websites/ssl`、`/ssl/obtain`、`/acme` | `request.WebsiteSSLCreate` / `WebsiteSSLApply` / `WebsiteAcmeAccountCreate` | 与 v2.2.5 相同 ✅ |
| `POST /files/decompress`、multipart `/files/upload` | `request.FileDeCompress` | dst/type/path/secret/taskID;path/overwrite/file ✅ |
| `POST /hosts/firewall/rules` | `dto.FirewallRuleCreate` | `items[{rule,sourceKind,sourceID}]`;规则字段 scope/protocol/sourceAddress/sourcePort/destinationAddress/destinationPort/action/description ✅ |
| `POST /hosts/firewall/rules/delete` | `dto.FirewallRuleDelete` | `uuids` ✅ |
| `POST /hosts/firewall/forward/operate` | `dto.ForwardRuleOperate` | rules[operation/num/protocol/port/targetIP/targetPort/interface]/forceDelete ✅ |
| `POST /hosts/firewall/docker/policies/batch` | `dto.DockerPortGuardPolicyBatch` | policies[family/hostIP/hostPort/protocol/mode/sources/description] ✅ |
| `POST /logs/tasks/search` | `dto.SearchTaskLogReq`;`model.Task` | page/pageSize/type/status/taskID;status/currentStep/errorMsg ✅ |

## API 清单全量核验(2026-10-03,fcvm v2.3.2 面板)

对 `panel_cli/data/api-v2.3.2.json` 的 787 条路由逐条向 v2.3.2 面板发起请求,核验「路由是否注册、方法是否正确」:

- **787/787 全部注册**:无 404、无方法不符;766 条实调(读操作 GET 直接调用;写操作使用畸形 JSON 请求体,触发参数校验失败而不产生副作用),21 条有副作用或有特殊约束的路由跳过。
- 实调业务码:200×111、400×613(参数校验/探针数据不合法)、403×3(面板内部代理接口,如 `/hosts/firewall/port`、`/internal/terminal/sessions/revoke`)、500×39(探针 ID `record not found`、缺失系统文件等预期错误)。
- 跳过样例:`core/auth/logout`、`dashboard/system/restart/:operation`、`hosts/firewall/docker/sync`、`hosts/terminal/sessions/closeAll` 等,经面板 Swagger 或源码确认注册。
- 结论:内置清单与 v2.3.2 面板完全一致;`api get/post` 可直接调用全部路由。
- 可重复执行:`python3 scripts/verify_api_routes.py --profile <p>`(写操作用畸形 JSON 拦截,不会产生副作用;有副作用路由自动跳过并标注);本次报告:`/tmp/api-verify-v2.3.2.json`。

## 实测记录

### 2026-10-03,v2.3.2(fcvm VM)

- 会话:`login / status` 通过,面板版本 `v2.3.2 (cn)`。
- 路由核验:787 条全部注册(见上节);`apps sync-local` 子命令随本版新增,应用注册/安装流程在离线包 WS4 验收中实测(见 `dev-1panel/1panel-offline-installer.md`)。

### 2026-09-23,v2.3.1(历史记录,本机)

- `init`:自动生成 `.env`(0600,地址解析为 `127.0.0.1`),`doctor` 6/6 通过(基线显示 v2.3.2)。
- 会话:`login / status / info / doctor`,面板版本 `v2.3.2 (cn)`。
- `api list/describe`:`api list --filter firewall` 显示统一规则接口;`api describe POST hosts/firewall/rules` 输出 Swagger 模型与 `sourcePath=source/agent/router/ro_host.go`。
- **防火墙全命令实测**(临时启用 ufw 并预先放行 22/22120/2053,测试后已恢复 inactive 且删除测试规则):
  - `status`(运行中/后端 ufw/后端选择)、`start`/`stop`(异步任务等待)通过;
  - `port add/del`(含备注、托管规则识别)、`ip add/del`、`batch`、`list --info` 通过;`list` 正确显示 `managed/protected/external/drifted` 状态;
  - `forward add/del`(异步任务)通过,`list --type forward` 编号正确;
  - 统一接口创建/删除规则通过 `logs/tasks/search` 汇报成功;任务失败(ufw 未启用时)`errorMsg` 原样展示且退出码为 1。
- **Docker 端口守护实测**:`firewall docker status/ports` 通过;策略写入(新 `policies` 结构)与删除均为异步任务并成功;`docker ports` 正确显示策略 UUID、来源、生效状态与管控目标;宿主机防火墙启用后,IPv6 映射被标记为 `host_firewall`,CLI 提交前跳过并提示,不再提交会被服务端拒绝的请求。
- `apps list/--all/tags/--installed`、`db list/users`、`redis list`、`web list` 通过;无 OpenResty/MySQL 时相关写命令给出明确的前置错误。
- 单元测试:130 个用例全部通过(新增接口形态探测、v2.3.2 创建载荷、Docker 策略地址族拆分、任务等待失败路径、流量接管跳过等用例)。

## init 命令:自动生成 .env(源码研究与实测)

`init` 通过两个来源获取面板账号,全程无需用户填写:

1. **`sudo 1pctl user-info`**(`source/core/cmd/server/cmd/user-info.go`):
   - 输出面板地址(协议/地址/端口/安全入口)、用户名、密码;
   - **密码仅在"从未成功登录过"时显示明文**;已有成功登录记录后固定输出 `********`。
2. **面板本地 SQLite DB**(`/opt/1panel/db/core.db`、`agent.db`):
   - 密码以 `EncryptKey` 为密钥 AES-CBC 加密存储(`Password` 设置项);
   - 解密算法对应 `source/core/utils/encrypt/encrypt.go` 的 `StringDecrypt`:
     `base64(iv[16] + AES-CBC(PKCS7)(key=EncryptKey))`;
   - Python 版在 `crypto.py` 中以纯标准库实现了 AES-128/192/256 解密,并以 openssl 独立生成的向量交叉验证。

**核心陷阱**:`agent.db` 的 `settings` 表中也存在同名 `EncryptKey`(值与 core.db 不同),必须按库分别查询(core.db 取密码相关键,agent.db 取 `SystemIP`)。

## 本地应用兼容(离线/内置应用商店)

离线或内置应用商店中,应用 key 带 `local` 前缀(如 `localopenresty` / `localmysql`,`app_utils.go` 中 `app.Key = "local" + appDefine.Key`),安装目录为 `<应用目录>/local/<key>/<name>`;而面板部分代码仍按在线商店的 `openresty` / `<应用目录>/<key>/<name>` 硬编码处理。已确认的问题与 CLI 处理:

| 面板行为(`source/` 位置) | 表现 | CLI 处理 |
|---|---|---|
| `getNginxFull`(`agent/app/service/nginx_utils.go`)按 `<apps>/openresty/<name>` 读 nginx 配置,`getAppInstallByKey("openresty")` 找不到 `localopenresty` | HTTPS 操作报 500 或静默不生效 | `web create/config/https/spa` 前自动创建软链 `<apps>/openresty` → `<apps>/local/openresty`;提供 `web repair` 手动修复/排查 |
| `delNginxConfig`(`agent/app/service/website_utils.go`)固定 `appRepo.WithKey(constant.AppOpenresty)` | 删除网站报 `服务错误: record not found` | `web delete <domain> --force` 兼容模式:备份 `agent.db` → 临时改名应用 key → 删除 → 自动还原(需与面板同机) |
| `GET /apps/<key>`、`find_installed` 等按 key/name 精确匹配 | `apps install openresty`、`apps info openresty` 查不到 `localopenresty` | `apps.py` 统一 `normalize_app_key` 归一化 `local` 前缀,读写两侧都做兼容 |

注意:不要把 `agent.db` 中的应用 key 永久改名——本地应用同步会按 `local<key>` 重新 upsert,改名会造成应用记录重复/失联;兼容模式只在删除瞬间临时生效并立即还原。

## SPA 反代要点(与面板版本无关)

- 仅配 `error_page 404` 对上游 404 不生效,必须同时 `proxy_intercept_errors on;`;
- 回退位置改写 `/index.html` 后 `proxy_pass` 后端(命名 location + `rewrite ... break`);
- CLI 的 `--spa` / `web spa` 自动写入面板「自定义重写」(`/websites/rewrite/update`),默认排除 API 前缀 `api,auth,health,linear`、静态资源与隐藏路径。

## 未在本机执行(说明)

| 项 | 原因 |
|---|---|
| Let's Encrypt 证书**签发完成** | 需要公网可解析并指向本机的域名与 80 端口验证;测试面板无可用域名 |
| 网站/数据库/Redis 的完整写流程 | 本机面板未安装 OpenResty/MySQL/Redis;相关路由已全量核验,请求 DTO 与 v2.2.5 一致(当时已实测) |
| 数据库 root 密码**异值**变更 | 同值变更已在 v2.2.5 实测;异值会同时改变面板自身连接凭据,风险高且无额外路径覆盖 |
