# 兼容性说明:Python 版 × 1Panel v2.4.1

本文件记录 `1panel-cli`(Python 版)与 **1Panel v2.4.1 stable** 的接口核对结果,以及相对 v2.4.0 的接口变化与本次升级实测。CLI 仍保留 v2.3.2 / 中间版本 / v2.2.x 的自动探测,连接旧面板时会回退到对应接口。

## 验证环境

| 项 | 值 |
|---|---|
| 面板版本 | v2.4.1(cn;`1panel-cli status` 实测 `v2.4.1 (cn)`) |
| 面板类型 | 本机安装,地址 `127.0.0.1:22120`(含安全入口) |
| 升级方式 | 2026-10-29 经 CLI 调用面板官方升级接口,由 v2.3.2 原地升级(见下文) |
| CLI 实现 | Python 3.12,仅标准库;CLI 版本 `2.4.1` |
| 内置 API 清单 | 由 1Panel `v2.4.1` tag 源码生成:`panel_cli/data/api-v2.4.1.json`(793 条路由,Swagger 收录 759 条) |
| 面板源码 | 随技能附带于 `source/`(1Panel v2.4.1 tag 完整源码;下表路径均相对该目录) |
| 单元测试 | 142 个用例全部通过(`python3 -m unittest discover -s tests`) |

## v2.4.1 路由变化(相对 v2.4.0)

源码路由清单由 788 条变为 **793 条**,新增 5 条、移除 0 条、修改 0 条:

| 变化 | 方法与路径 | 处理器 |
|---|---|---|
| 新增 | `GET /api/v2/ai/benchmark/sources` | `AIBenchmarkSources` |
| 新增 | `GET /api/v2/dashboard/quick/directory` | `ListQuickDirectories` |
| 新增 | `POST /api/v2/dashboard/quick/directory` | `CreateQuickDirectory` |
| 新增 | `POST /api/v2/dashboard/quick/directory/update` | `UpdateQuickDirectory` |
| 新增 | `POST /api/v2/dashboard/quick/directory/del` | `DeleteQuickDirectory` |

### ⚠️ 重要:官方发行包与 tag 源码的 5 条路由差异

官方 **v2.4.1 发行包实际只包含 Go 升级到 1.27.3 的修复(公告:CVE-2026-111943)**。经实机核验,上述 5 条 v2.4.1 tag 新增路由在官方发行包的二进制中**并未注册**:

- 对 v2.4.1 实机面板调用这 5 条路由一律返回 gin 默认 `404 page not found`;
- 对发行包 agent 二进制做 `strings` 检查,均不包含 `/quick/directory`、`/benchmark/sources` 路由字符串(core 二进制中仅在前端资源里出现该路径);
- core 与 agent 均已替换为升级包内新二进制并重启(进程 inode 与 `/usr/local/bin` 返回值一致),可排除"未升级"因素。

**CLI 的处理**:内置清单按 tag 源码收录这 5 条(对齐全源),但已在清单引用文档与 `api list` 说明中标注"v2.4.1 发行包未注册,调用会 404";这 5 条属预期差异,待后续版本随 dashboard 快捷目录 / AI benchmark 功能正式发布。除这 5 条外,清单与 v2.4.1 发行包完全一致。

## 防火墙接口(与 v2.4.0 一致,相对 v2.3.2 的语义)

v2.4.1 防火墙接口与 v2.4.0 完全一致(788 条旧路由零变化),CLI 的 `v240` 探测分支原样适用:

| 项 | v2.3.2 | v2.4.0+(含 v2.4.1) |
|---|---|---|
| 探测依据 | 有 `GET /firewall/settings`,无规则备份接口 | `GET /hosts/firewall/rules/backups` 可用 |
| 规则删除 | `{"uuids": [...]}` | `{"targets": [{scope, instanceKey, observed}]}` |
| 清单字段 | `state` / `desired.uuid` | `isWhitelist` / `observed.protected` / `observed.instanceKey` |
| 分页 | `total/allTotal/managedTotal`,含 `refresh/states` | `total/allTotal`,移除 `refresh/states` |
| IPv6 开关 / 分族操作 | 无 | `POST /settings/ipv6`、`POST /family/operate` |
| Docker 端口守护 | 策略含 `description`;有 `docker/sync` | 策略移除 `description`,新增 `accept_sources/accept_all`;`docker/sync` 移除 |
| 端口转发 | `forceDelete` | 改为 `import`,规则可选 `family` |

v2.4.0+ 写操作均为异步任务,CLI 自动轮询 `/logs/tasks/search` 并汇报;删除前自动跳过 `isWhitelist=true` / `observed.protected=true` 的规则。

## 非防火墙 DTO 差异审计(v2.4.0 → v2.4.1)

对 `agent/app/dto` 与 `core/app/dto` 全量 diff,变化仅涉及:

- `agent/app/dto/agents.go`:AI Agent 类型枚举新增 `deepseek-harness`、`octop`;创建/列表项新增 `accessHost` 字段;
- `agent/app/dto/dashboard.go`:`ChangeQuicks.quicks` 由 `[]QuickJump` 改为 `[]QuickVisibility{id, isShow}`(快捷入口显隐语义变化),并新增快捷目录 CRUD 的 DTO(`QuickDirectoryCreate/Delete/Update`);
- `agent/app/dto/ai_benchmark.go`:新增(AI benchmark 来源),配套 `GET /ai/benchmark/sources`;
- `core/app/dto/setting.go`:设置项新增 `panelCache`(前端缓存偏好)。

CLI 自身命令**(不调用)** AI agents、dashboard 快捷入口/快捷目录、AI benchmark 相关接口,因此无需代码适配;上述新路由仅供 `api` 透传(发行包注册后可用)。

## 面板升级实测(v2.3.2 → v2.4.1,经 CLI 完成)

升级路径复用面板官方接口,即 Web 界面「检查更新 → 升级」同源实现(`source/core/app/service/upgrade.go`):

1. `GET /api/v2/core/settings/upgrade` → 返回 `latestVersion: v2.4.1` 与发布说明;
2. `POST /api/v2/core/settings/upgrade`,请求体 `{"version":"v2.4.1"}`;
3. 服务端自行下载(cn 镜像 `resource.fit2cloud.com/1panel/package/v2/stable/<版本>/release`)、解压、备份原文件(`/usr/local/bin` 二进制副本、`1pctl`、配套脚本等)至 `/opt/1panel/tmp/upgrade/v2.4.1`,写升级日志并支持回滚;
4. 替换 `/usr/local/bin/{1panel-core,1panel-agent,1pctl}` 后重启两个服务,配置与数据保留;
5. 表结构无破坏性变更:升级前 CLI 会话失效一次,重新 `login` 即恢复;磁盘要求 ≥ 500MB(源码常量 `minUpgradeFreeSpace`)。

实测:`1pctl version` → `v2.4.1 stable`;`bin/1panel-cli doctor` 6/6 通过(基线 v2.4.1)。

## API 清单全量核验(2026-10-29,本机 v2.4.1)

对 `panel_cli/data/api-v2.4.1.json` 的 793 条路由逐条向 v2.4.1 面板发起请求,核验「路由是否注册、方法是否正确」:

- **788/793 注册**(v2.4.0 及更早路由全部可用):OK 767(实调)+ SKIPPED 21(已知副作用/特殊约束路由,均确认 Swagger 收录);
- **5 条 MISSING**:即上文标注的 v2.4.1 tag 独有路由(官方发行包未注册);
- 实调方式:GET 直接调用;写操作发送畸形 JSON 请求体,在参数校验阶段失败,不产生业务副作用;
- 业务响应:200×117、400×615、403×3、500×32(均为预期的参数校验、面板内部代理限制或探针数据缺失);
- 结论:除 5 条已知差异外,内置清单与 v2.4.1 面板完全一致;
- 可重复执行:`python3 scripts/verify_api_routes.py --profile <p>`;本次报告:`/tmp/api-verify-v2.4.1.json`。

## 实测记录

### 2026-10-29,1Panel v2.4.1(cn,本机,v2.3.2 原地升级)

- **升级流程**:`api get core/settings/upgrade` → `api post core/settings/upgrade {"version":"v2.4.1"}` → 服务自重启 → 升级后 1 次重新 `login` → `doctor` 6/6。
- `status`:显示 `v2.4.1 (cn)`。
- `firewall status / ipv6 status / list --type port`:后端 ufw(未初始化)下正常读取与展示,统一规则 API 探测为 `v240` 分支。
- 单元测试:142 个用例全部通过。

### 未在本机执行(说明)

| 项 | 原因 |
|---|---|
| 端口转发 / IPv6 / Docker 守护写流程 | 本机 ufw 防火墙未启动、端口转发链与 Docker 守护未初始化,不应影响现有网络;相关接口已在路由全量核验中确认注册 |
| 防火墙规则写流程 | 同上,本机无规则可增删;v2.4.0 基线时的写流程实测结论对 v2.4.1 依然成立(接口零变化) |

## 与旧版本的兼容性

- v2.4.0:除 5 条 tag 新路由外路由集相同;CLI 探测(`v240` 分支)行为在两个版本上一致。
- v2.3.2:仍走 `v231` 分支,删除提交 `uuids`,Docker 策略保留 `description`,规则清单显示 `state`。
- 中间版本:仍走 `checked` 分支(`rules/check` + `checkFlag`)。
- v2.2.x:仍自动回退旧门面接口。
- 版本探测只增加一次只读 `GET /rules/backups`;旧面板返回 404 时不会误判。
