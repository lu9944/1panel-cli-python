# 兼容性说明:Python 版 × 1Panel v2.4.2

本文件记录 `1panel-cli`(Python 版)与 **1Panel v2.4.2 stable** 的接口核对结果,以及相对 v2.4.1 的接口变化、目录抓取器修复与本次升级实测。CLI 仍保留 v2.3.2 / 中间版本 / v2.2.x 的自动探测,连接旧面板时会回退到对应接口。

## 验证环境

| 项 | 值 |
|---|---|
| 面板版本 | v2.4.2(cn;`1panel-cli status` 实测 `v2.4.2 (cn)`) |
| 面板类型 | 本机安装,地址 `127.0.0.1:22120`(含安全入口) |
| 升级方式 | 2026-11-08 经 CLI 调用面板官方升级接口,由 v2.4.1 原地升级(同 v2.4.1 流程) |
| CLI 实现 | Python 3.12,仅标准库;CLI 版本 `2.4.2` |
| 内置 API 清单 | 由 1Panel `v2.4.2` tag 源码生成:`panel_cli/data/api-v2.4.2.json`(821 条路由,Swagger 收录 759 条) |
| 面板源码 | 随技能附带于 `source/`(1Panel v2.4.2 tag 完整源码;下表路径均相对该目录) |
| 单元测试 | 142 个用例全部通过(`python3 -m unittest discover -s tests`) |

## v2.4.2 路由变化(相对 v2.4.1 tag 源码)

源码路由清单由 793 条变为 **821 条**,新增 29 条、移除 1 条、修改 0 条:

| 变化 | 方法与路径 | 说明 |
|---|---|---|
| 新增 ×22 | `/api/v2/core/mcp/*` | **MCP(模型上下文协议)管理接口全套**:status/settings(GET|POST)/catalog/nodes/service(operate\|install\|logs×2)/website(bind\|update\|unbind\|status×2)/credentials(search\|create\|update\|status\|revoke\|delete\|reveal\|rotate\|export)/invocations/search |
| 新增 | `GET /api/v2/ai/model/gpu/devices` | 模型 GPU 设备清单(`LoadModelGPUDevices`) |
| 新增 | `POST /api/v2/ai/agents/channel/feishu/switch` | 飞书插件开关(`SwitchAgentFeishuPlugin`) |
| 新增 | `POST /api/v2/ai/ollama/account/sync` | 取代原 `POST /ai/ollama/model/sync`(已移除) |
| 新增 | `POST /api/v2/core/auth/api/keys/delete`、`/reveal` | 面板 API Key 删除/查看 |
| 新增 | `POST /api/v2/websites/ca/import`、`/upload` | CA 证书导入/上传(`WebsiteCAImport`:`paste`/`local` 两种来源) |

### ⚠️ 重要:目录抓取器修复(GROUP 链式写法)

`core/router/ro_mcp.go`(v2.4.2 新增)首次出现两种此前未覆盖的注册写法,内置目录的抓取正则因此漏掉 22 条路由:

1. `group.Group("mcp").Use(middleware.SessionAuth())` 链式 `.Use(...)`;
2. `InitRouter(group *gin.RouterGroup)` 形参名不是惯用的 `Router`。

`scripts/generate_api_catalog.py` 已修复(允许 `.Use(...)` 链、探测 InitRouter 实参名作为根前缀),**v2.4.1 与 v2.4.2 tag 源码均无其他同类写法**;上表中的 `/core/mcp/*` 22 条即修复后成功收录的路由。

### ⚠️ 重要:官方发行包与 tag 源码的 34 条路由差异

官方 **v2.4.2 发行包实际只包含「默认禁用 MPTCP 缓解 CVE-2026-98432」的修复**(发布说明亦只列此项)。实机核验(2026-11-08)确认,相对 tag 源码,**官方发行包共 34 条路由未注册**:

| 未注册路由 | 表现 |
|---|---|
| `/api/v2/core/mcp/*` ×22 | core 侧未注册路由落到 SPA 首页回退:HTTP 200 + `text/html`(与自造路径响应完全一致,`strings` 检查发行包 core 二进制亦无该路由字符串) |
| `/api/v2/core/auth/api/keys/{delete,reveal}` ×2 | 同上(HTML 回退) |
| `GET /dashboard/quick/directory*` ×4、`GET /ai/benchmark/sources`、`GET /ai/model/gpu/devices`、`POST /ai/agents/channel/feishu/switch`、`POST /ai/ollama/account/sync` ×8 | agent 侧未注册:gin `404 page not found` |
| `POST /websites/ca/{import,upload}` ×2 | 同上(agent 侧 404) |

说明:(1) 发行包 core 二进制对「core 自身路由未注册」与「agent 代理路由未注册」的 404 形态不同——前者直接走前端回退页,后者为 gin 文本 404;全量核验时两类都视为"未注册"。(2) 未注册的 agent 侧路由中,原 `POST /ai/ollama/model/sync` 在发行包仍存在(该路由仅从 tag 源码移除,发行包二进制未更新)。

**CLI 的处理**:内置清单按 tag 源码收录(821 条,对齐全源),文档与 `api list` 说明标注"34 条为 v2.4.1/v2.4.2 tag 新增、官方发行包尚未注册,调用会 404/得到 HTML";除这 34 条外,清单与 v2.4.2 发行包完全一致。调用未注册路由时面板返回 404 文本或 HTML,这在 `catalog.py` 的调用校验下会给出明确报错,不会误配。

## 防火墙接口(与 v2.4.1 一致,相对 v2.3.2 的语义)

v2.4.2 防火墙接口与 v2.4.0/v2.4.1 完全一致,CLI 的 `v240` 探测分支原样适用:

| 项 | v2.3.2 | v2.4.0+(含 v2.4.1/v2.4.2) |
|---|---|---|
| 探测依据 | 有 `GET /firewall/settings`,无规则备份接口 | `GET /hosts/firewall/rules/backups` 可用 |
| 规则删除 | `{"uuids": [...]}` | `{"targets": [{scope, instanceKey, observed}]}` |
| 清单字段 | `state` / `desired.uuid` | `isWhitelist` / `observed.protected` / `observed.instanceKey` |
| 分页 | `total/allTotal/managedTotal`,含 `refresh/states` | `total/allTotal`,移除 `refresh/states` |
| IPv6 开关 / 分族操作 | 无 | `POST /settings/ipv6`、`POST /family/operate` |
| Docker 端口守护 | 策略含 `description`;有 `docker/sync` | 策略移除 `description`,新增 `accept_sources/accept_all`;`docker/sync` 移除 |
| 端口转发 | `forceDelete` | 改为 `import`,规则可选 `family` |

v2.4.0+ 写操作均为异步任务,CLI 自动轮询 `/logs/tasks/search` 并汇报;删除前自动跳过 `isWhitelist=true` / `observed.protected=true` 的规则。

## 非防火墙 DTO 差异审计(v2.4.1 → v2.4.2)

对 `agent/app/dto` 与 `core/app/dto` 全量 diff,变化均为**增量**(新可选字段/新 DTO),CLI 现有请求体不受影响:

- `agent/app/dto/app.go`、`request/app.go`、`response/app.go`:应用版本/安装/升级等新增 `feishuTransition`、`gpu`、模型账号(`syncModelAccount`/`modelAccountBaseURL*`)等可选字段;`AppItem` 新增可选 `icon`;不影响 `apps` 命令现有调用;
- `agent/app/dto/agents.go`:新增 `AgentFeishuTransition/SwitchReq` 等(飞书插件,配套新路由);
- `agent/app/dto/ai.go`:新增 `ModelGPUDevice(s)` DTO(配套 `GET /ai/model/gpu/devices`);
- `agent/app/dto/request/mcp_website.go`、`response/mcp_website.go`:MCP 网站绑定相关(新模块);
- `agent/app/dto/container.go`:新增可选 `containerName`;
- `agent/app/dto/dashboard.go`、`monitor.go`:监控项新增 `sharedMemory` 等字段(`tensorrt_llm` 同步小调整);
- `core/app/dto/api_key.go`:API Key 删除/查看 DTO(新路由);`core/app/dto/{mcp.go,mcp_enrollment.go,mcp_runtime.go,mcp_website.go}`:MCP 新模块全套 DTO;
- `core/app/dto/setting.go`:无结构变化。

CLI 自身命令**(不调用)** agents/AI/MCP/API Key 管理/CA 导入等新增模块;新增路由均可通过 `api` 透传(发行包注册后)。

## 面板升级实测(v2.4.1 → v2.4.2,经 CLI 完成)

复用 v2.4.1 同款流程:`api get core/settings/upgrade` → `api post core/settings/upgrade {"version":"v2.4.2"}` → 服务自重启 → 重新 `login`。原文件备份于 `/opt/1panel/tmp/upgrade/v2.4.2`;`1pctl version` → `v2.4.2 stable`;本次发布仅含 MPTCP 内核修复,功能路由无变化。

## API 清单全量核验(2026-11-08,本机 v2.4.2)

对 `panel_cli/data/api-v2.4.2.json` 的 821 条路由逐条向 v2.4.2 面板发起请求,核验「路由是否注册、方法是否正确」:

- **787/821 注册**(v2.4.0 及更早路由 + 发行包既有能力全部可用):OK 767(实调,业务响应 200×117、400×615、403×3、500×32) + SKIPPED 20(已知副作用/特殊约束路由,均确认 Swagger 收录);
- **34 条未注册**:即上文标注的发行包差异(10 条 agent 侧 404 + 24 条 core 侧 SPA 回退);
- 实调方式:GET 直接调用;写操作发送畸形 JSON 请求体,在参数校验阶段失败,不产生业务副作用(新 MCP/API Key/CA/账户同步路由均绑定 DTO,畸形请求体即被拦截,无需豁免);
- 结论:除 34 条已知差异外,内置清单与 v2.4.2 面板完全一致;
- 可重复执行:`python3 scripts/verify_api_routes.py --profile <p>`;本次报告:`/tmp/api-verify-v2.4.2.json`。

## 实测记录

### 2026-11-08,1Panel v2.4.2(cn,本机,v2.4.1 原地升级)

- **升级流程**:CLI 官方升级接口原地升级,1 次重新 `login`,doctor 6/6(基线 v2.4.2)。
- `status`:显示 `v2.4.2 (cn)`。
- `firewall status / ipv6 status / list`:ufw(未初始化)后端正常读取,统一规则 API 探测 `v240` 分支。
- MCP 管理接口:已收录清单(22 条),发行包未注册(见上文差异)。
- 单元测试:142 个用例全部通过。

### 未在本机执行(说明)

| 项 | 原因 |
|---|---|
| 端口转发 / IPv6 / Docker 守护写流程 | 本机 ufw 未启动、端口转发链与 Docker 守护未初始化,不改动现有网络;相关接口已全量核验注册,写流程语义与 v2.4.0 基线实测一致 |
| 防火墙规则写流程 | 本机无规则可增删;接口与 v2.4.0/v2.4.1 零变化,历史实测结论依然成立 |

## 与旧版本的兼容性

- v2.4.1:tag 源码新增 MCP/CA/AI/API Key 等路由,其余路由集相同;CLI 探测(`v240` 分支)行为在两个版本上一致。
- v2.4.0:同 v2.4.1 说明。
- v2.3.2:仍走 `v231` 分支,删除提交 `uuids`,Docker 策略保留 `description`,规则清单显示 `state`。
- 中间版本:仍走 `checked` 分支(`rules/check` + `checkFlag`)。
- v2.2.x:仍自动回退旧门面接口。
- 版本探测只增加一次只读 `GET /rules/backups`;旧面板返回 404 时不会误判。
