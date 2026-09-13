# 兼容性说明:Python 版 × 1Panel v2.2.5

本文件记录 `1panel-cli`(Python 版)与 **1Panel v2.2.5 stable** 的接口核对结果,以及它与 Rust 版(验证基线 dev-v2)的差异处理。

## 验证环境

| 项 | 值 |
|---|---|
| 面板版本 | v2.2.5(stable,`1pctl version` 实测) |
| 面板类型 | 本机安装,已启用安全入口 |
| CLI 实现 | Python 3.12,仅标准库 |
| 内置 API 清单 | 由 1Panel `v2.2.5` 源码生成:`panel_cli/data/api-v2.2.5.json`(754 条路由,Swagger 收录 731 条) |

## 关键接口核对(对照 v2.2.5 源码)

| 功能 | 接口 | 路由来源 | 结论 |
|---|---|---|---|
| 登录设置 | `GET /api/v2/core/auth/setting` | `core/router/ro_base.go` | ✅ |
| 验证码 | `GET /api/v2/core/auth/captcha` | `core/router/ro_base.go` | ✅ |
| 登录 | `POST /api/v2/core/auth/login` | `core/router/ro_base.go` | ✅(RSA+AES、`EntranceCode`) |
| MFA | `POST /api/v2/core/auth/mfalogin` | `core/router/ro_base.go` | ✅ |
| 当前用户 | `GET /api/v2/core/auth/current` | `core/router/ro_base.go` | ✅ |
| 登出 | `POST /api/v2/core/auth/logout` | `core/router/ro_base.go` | ✅ |
| 面板版本 | `POST /api/v2/core/settings/search/base` | `core/router/ro_setting.go` | ✅ |
| 防火墙状态 | `POST /api/v2/hosts/firewall/base` | `agent/router/ro_host.go:24` | ✅ 旧接口 |
| 防火墙列表 | `POST /api/v2/hosts/firewall/search` | `ro_host.go:25` | ✅ 旧接口 |
| 防火墙启停/Ping | `POST /api/v2/hosts/firewall/operate` | `ro_host.go:26` | ✅ |
| 端口规则 | `POST /api/v2/hosts/firewall/port` | `ro_host.go:27` | ✅ |
| 转发规则 | `POST /api/v2/hosts/firewall/forward` | `ro_host.go:28` | ✅ |
| IP 规则 | `POST /api/v2/hosts/firewall/ip` | `ro_host.go:29` | ✅ |
| 批量规则 | `POST /api/v2/hosts/firewall/batch` | `ro_host.go:30` | ✅ |
| 数据库列表/增删 | `/api/v2/databases/search`、`/databases`、`/databases/del[/check]` | `agent/router/ro_database.go` | ✅ |
| 数据库用户 | `/api/v2/databases/users[/search|/del|/password]` | `ro_database.go` | ✅ |
| 修改密码 | `/api/v2/databases/change/password`、`/databases/redis/password` | `ro_database.go` | ✅ |
| Redis 状态/配置 | `/api/v2/databases/redis/status|conf|conf/update` | `ro_database.go` | ✅ |
| 网站列表/创建 | `/api/v2/websites/search`、`/websites` | `agent/router/ro_website.go` | ✅ |
| nginx 配置 | `GET /websites/:id/config/openresty`、`POST /websites/nginx/update` | `ro_website.go` | ✅ |
| HTTPS | `GET|POST /websites/:id/https` | `ro_website.go` | ✅ 手动证书 `type=manual` |
| 证书申请 | `/websites/ssl`、`/websites/ssl/obtain`、`/websites/acme[/search]` | `ro_website_ssl.go` / `ro_website_acme_account.go` | ✅ |
| 上传/解压 | `POST /api/v2/files/upload`(multipart)、`/files/decompress` | `agent/router/ro_file.go` | ✅ |
| 应用商店 | `/api/v2/apps/search|tags|install`、`/apps/installed/*`、`/logs/tasks/search` | `agent/router/ro_app.go` / `ro_log.go` | ✅ |

## 与 Rust 版(dev-v2 基线)的差异处理

1. **防火墙双模式**:v2.2.5 只有旧接口(`/firewall/base|search|operate|port|forward|ip|batch`),不存在 `/hosts/firewall/settings`、`/hosts/firewall/rules*`、`/hosts/firewall/docker/*`。Python 版保留与 Rust 版一致的自动探测(`supports_unified_api`),v2.2.5 自动回退旧接口;`firewall docker *` 在 v2.2.5 上给出明确提示。
2. **内置路由清单**:Rust 版内置 dev-v2 清单(766 条);Python 版内置 **v2.2.5 清单(754 条)**,更贴合目标版本的 `api list` / `api describe` 输出。`scripts/generate_api_catalog.py` 可用任意 1Panel 源码树重新生成。
3. **v2.2.5 无 DELETE/PUT 路由**:754 条路由全部为 POST/GET;`api put` / `api delete` 命令保留,供新版面板使用。
4. **空规则列表兼容**:v2.2.5 的 `/firewall/search` 在无规则时返回 `{"items": null, "total": 0}`。Rust 版会反序列化失败报错;Python 版将其视为空列表并正常打印表头与「共 0 条规则」。
5. **验证码字段名修复(功能缺陷)**:v2.2.5 的 `CaptchaResponse` JSON 字段是 **`captchaID`**(大写 ID,`core/app/dto/auth.go:3`),Rust 版按 `captchaId` 反序列化,导致面板要求验证码时无法取得 ID(表现为登录永远报验证码错误)。Python 版按 `captchaID` 读取(兼容 `captchaId` 回退),已实测通过验证码登录。
7. **CurrentNode 编码对齐前端**:前端用 `encodeURIComponent` 编码该头(`frontend/src/api/index.ts:42`),服务端 `url.QueryUnescape` 解码(`core/init/router/proxy.go:35`)。Python 版采用相同语义(quote safe=`!*'()`),并以「编码后经 QueryUnescape 可还原」作为单元测试不变式。
8. **安装进度提示修正**:Rust 版文案引导用户运行不存在的 `apps status`;Python 版改为引导面板「已安装应用」页面。
9. **已知小差异**:错误文本中的 OS 错误描述与 Rust 版略有差异,语义等价。
10. **`web ssl` 重复申请修复(功能缺陷)**:v2.2.5 的 `WebsiteSSLService.Create` 在创建记录后会**自动异步发起申请**(`agent/app/service/website_ssl.go:263-270`,`go w.ObtainSSL(...)`)。Rust 版创建后再次显式调用 `/websites/ssl/obtain`,在 v2.2.5 上必然返回 `任务执行中,请勿重复执行` 并以错误退出(尽管申请已经开始)。Python 版仍调用 obtain 以兼容不自动申请的版本,但将「任务执行中/请勿重复执行」视为申请已发起,命令正常成功退出。

## 源码审计:CLI 全部接口 × v2.2.5 路由清单

用脚本交叉比对 CLI 源码中的全部 API 调用(48 个去重端点)与 `panel_cli/data/api-v2.2.5.json`(754 条路由):

- 全部 48 个端点均存在于 v2.2.5 路由清单(仅 4 个脚本误报:`detail/.../app` 与 `config/openresty` 是 `:type` 字面量、`hosts/firewall` 是路径前缀常量、`apps_action` 是 argparse 名称)。
- 逐个核对的请求 DTO 字段(见下表)与 `agent`/`core` 源码一致,无字段名/类型偏差。

| 接口 | DTO(源码) | CLI 字段 |
|---|---|---|
| `POST /apps/install` | `request.AppInstallCreate` + `AppContainerConfig` | appDetailId/params/name/taskID/advanced/allowPort/pullImage/cpuQuota/memoryLimit/memoryUnit/containerName/editCompose/dockerCompose/gpuConfig/specifyIP ✅ |
| `POST /apps/installed/params/update` | `request.AppInstalledUpdate` | installId/params/advanced ✅ |
| `POST /apps/installed/conninfo` | `dto.OperationWithNameAndType` | type/name ✅ |
| `POST /databases` | `dto.MysqlDBCreate` | name/from/database/format/username/password/permission/description ✅ |
| `POST /databases/del[/check]` | `dto.MysqlDBDelete` | id/type/database/forceDelete/deleteBackup ✅ |
| `POST /databases/users[/search\|/del\|/password]` | `dto.MysqlUserCreate/Delete/Password` | database/username/password/host/description/dbs ✅ |
| `POST /databases/change/password` | `dto.ChangeDBInfo` | id/from/type/database/value(base64) ✅ |
| `POST /databases/redis/password` | `dto.ChangeRedisPass` | database/value(base64) ✅ |
| `POST /databases/redis/conf/update` | `dto.RedisConfUpdate` | database/dbType/timeout/maxclients/maxmemory ✅ |
| `GET /databases/redis/status\|conf` | `dto.LoadRedisStatus` / `dto.RedisConf` | type/name ✅ |
| `POST /websites` | `request.WebsiteCreate` | type/alias/remark/webSiteGroupID/domains/appType/appInstallID/taskID/IPV6 ✅ |
| `POST /websites/nginx/update` | `request.WebsiteNginxUpdate` | id/content ✅ |
| `POST /websites/:id/https` | `request.WebsiteHTTPSOp` | websiteId/enable/httpConfig/type(`existed`/`manual`)/websiteSSLId/certificate/privateKey/importType ✅ |
| `POST /websites/ssl` | `request.WebsiteSSLCreate` | primaryDomain/provider/acmeAccountId/keyType/autoRenew ✅ |
| `POST /websites/ssl/obtain` | `request.WebsiteSSLApply` | ID/skipDNSCheck ✅ |
| `POST /websites/acme` | `request.WebsiteAcmeAccountCreate` | email/type/keyType ✅ |
| `POST /files/decompress` | `request.FileDeCompress` | dst/type/path/secret/taskID ✅ |
| `POST /files/upload` | multipart | path/overwrite/file ✅ |
| `GET /core/auth/captcha` | `dto.CaptchaResponse` | captchaID(修复)/imagePath ✅ |
| `POST /core/auth/login\|mfalogin` | `dto.Login` / `dto.MFALogin` | name/password/captcha/captchaID/language/authSource;sessionId/code ✅ |
| `POST /hosts/firewall/*` | `dto.PortRuleOperate`/`AddrRuleOperate`/`ForwardRuleOperate`/`BatchRuleOperate`/`FirewallOperation` | 字段逐一对应 ✅ |
| `POST /logs/tasks/search` | `dto.SearchTaskLogReq`;`model.Task` 响应 | page/pageSize/type/status/taskID;status/currentStep/errorMsg ✅ |

## API 清单全量核验(2026-09-13,本机 v2.2.5)

对 `panel_cli/data/api-v2.2.5.json` 的 754 条路由逐条向面板发起请求,核验「路由是否注册、方法是否正确」:

- **754/754 全部注册**:无 404、无方法不符。其中 733 条直接请求(读操作 GET 直接调用;写操作使用畸形 JSON 请求体,触发参数校验失败而不产生副作用)。
- 21 条有副作用且不能靠请求体拦截的路由不实调,全部经当前面板 Swagger 确认注册(其中 `dashboard/system/restart/:operation` 与 `databases/pg/:database/load` 另用非法参数实调,均在执行动作前返回业务错误)。
- 13 条未被 Swagger 收录的路由(网站模板、编排置顶、分片上传停止等)均实调确认存在;运行时补充路由 `GET /api/v2/health/check` 可用。
- 结论:内置清单与 v2.2.5 面板完全一致;`api get/post` 可直接调用全部路由。
- 可重复执行:`python3 scripts/verify_api_routes.py`(写操作用畸形 JSON 拦截,不会产生副作用;有副作用路由自动跳过并标注)。

核验中发现并修复的「调用方法」问题:

1. `api describe` 之前只能按模板写法(`websites/ca/:id`)匹配,传入真实取值(如 `websites/ca/1`)会报「均未找到」;现在支持按路径段通配匹配,并兼容 `:id` / `{id}` 两种写法。
2. `api` 透传在「路径存在但方法不符」时,面板返回 HTTP 200 的 HTML 页面,CLI 之前会原样打印且 exit=0;现在会明确报错(方法不支持/入口异常),404 也会提示「面板未注册该路由」。

## 实测记录(2026-09-13,本机 v2.2.5)

- `doctor`:6/6 通过(不含已移除的 exec 相关检查)。
- 会话:login / status / info / logout / 多 profile / CLI 参数覆盖 / 错误密码 / 会话失效归因,与 Rust 版输出一致。
- **验证码流程**:错误密码触发 → 面板 `needCaptcha=true` → CLI 检测并拉取图片 → 穷举算式答案经 **`captchaID` 修复路径**登录成功(错验证码不计失败次数,成功自动清除状态)。
- `apps list --installed/--all`(253 应用分页)、`apps info`、`apps config`(**含 `--set` 触发容器重建**)、`apps tags`、`apps password redis`(同值)、重复安装报错、安装请求体过面板校验:全部通过。
- `db` 建库/删库/用户增删改密:通过;`redis info/config`:与 Rust 版一致。
- `web` 建站/读配置/写配置/**上传(含中文文件名)**/解压(自动与 `--type` 显式)/HTTPS 手动证书/`--ssl-id` 开启与关闭:通过;测试站点与证书记录已清理。
- `firewall` 状态/列表/端口(accept/drop+source)/转发(含 `--interface`)/IP/批量(旧接口):通过;`firewall docker status` 正确定位不支持。
- `api list/describe(含 Swagger 模型)/get/post/put/delete(方法分发)`、`api describe` 未收录路由:通过。
- `apps install --wait` **完整安装实测**(memcached 1.6.22):代理恢复后真实拉取镜像、参数覆盖(`MEM_CACHED=96` 生效)、任务轮询显示 Executing → 安装成功、容器端口映射正确、服务可连通(`stats` 正常)、经 `apps/installed/op` 卸载清理完毕。
- **防火墙全命令实测**(启用→停止,本机 `sudo ufw disable` 兜底):`start/stop/restart/allow-ping/ban-ping` 全部通过;ufw active 时 `firewall list` 正确显示 1Panel 托管规则(31206/80/443/22)与新增端口规则,`port add/del`、`ip add/del`、`forward` 均通过并清理;测试后 ufw 恢复 inactive。
- `db passwd mysql --instance mysql`(同值改密):面板与 MySQL 均正常。
- **`web ssl` 实测**:ACME 账号查重复用;`POST /websites/ssl` 创建成功;`obtain` 重复提交错误经修复后容忍,命令 exit=0;证书记录状态 `applying`(域名不可解析,最终 `applyError`,属预期);测试记录已删除。ACME 注册链路也实测过(LE 对 `example.com` 邮箱返回 forbidden domain,证明请求真实到达 LE)。
- 单元测试:101 个用例全部通过(新增 AES 解密交叉向量与 init 探测/生成用例)。

## init 命令:自动生成 .env(源码研究与实测)

`init` 通过两个来源获取面板账号,全程无需用户填写:

1. **`sudo 1pctl user-info`**(`core/cmd/server/cmd/user-info.go`):
   - 输出面板地址(协议/地址/端口/安全入口)、用户名、密码;
   - **密码仅在"从未成功登录过"时显示明文**(`shouldShowInitialPassword` 查登录日志,`logCount == 0`);
     已有成功登录记录后固定输出 `********`。
2. **面板本地 SQLite DB**(`/opt/1panel/db/core.db`、`agent.db`):
   - 密码以 `EncryptKey` 为密钥 AES-CBC 加密存储(`Password` 设置项);
   - 解密算法对应 `core/utils/encrypt/encrypt.go` 的 `StringDecrypt`:
     `base64(iv[16] + AES-CBC(PKCS7)(key=EncryptKey))`;
   - Python 版在 `crypto.py` 中以纯标准库实现了 AES-128/192/256 解密(`string_decrypt`),
     并以 openssl 独立生成的向量交叉验证;
   - DB 文件默认 0644,直接只读打开即可;必要时回退 `sudo -n` 复制副本(WAL 一并复制)。

**核心陷阱**:`agent.db` 的 `settings` 表中也存在同名 `EncryptKey`(值与 core.db 不同),
按库统一查询会互相覆盖导致解密失败;必须按库分别查询(core.db 取密码相关键,agent.db 取 `SystemIP`)。

实测(本机 v2.2.5,已有关联会话/登录记录、密码被掩码):
`init` → 生成 `.env`(0600) → `login` → `status`/`doctor` 全部通过;`--print`、`--force`、`--host` 均验证;
生成的地址默认把 `$LOCAL_IP` 解析为探测可达的 `127.0.0.1`(自签 HTTPS 会自动写入 `PANEL_INSECURE=true`)。

## 未在本机执行(说明)

| 项 | 原因 |
|---|---|
| Let's Encrypt 证书**签发完成** | 需要公网可解析并指向本机的域名与 80 端口验证;已实测到「创建 + 提交申请」全链路,签发由 LE 异步执行 |
| 数据库 root 密码**异值**变更 | 同值变更已实测;异值会同时改变面板自身连接凭据,风险高且无额外路径覆盖 |
