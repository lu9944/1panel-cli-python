# 兼容性说明:Python 版 × 1Panel v2.5.1

本文件记录 `1panel-cli`(Python 版)与 **1Panel v2.5.1 stable** 的接口核对结果,以及相对 v2.4.2 的接口变化、实测结论与已知差异。CLI 仍保留 v2.3.2 / 中间版本 / v2.2.x 的自动探测,连接旧面板时会回退到对应接口。

## 验证环境

| 项 | 值 |
|---|---|
| 面板版本 | v2.5.1(cn;`1panel-cli status` 实测 `v2.5.1 (cn)`) |
| 面板类型 | 本机安装,地址 `127.0.0.1:22120`(含安全入口) |
| 面板状态 | 任务开始前已安装 v2.5.1;本次未重复执行面板升级,仅完成技能/CLI 适配与实机核验 |
| CLI 实现 | Python 3.14,仅标准库;CLI 版本 `2.5.1`(兼容 Python 3.9+) |
| 内置 API 清单 | 由 1Panel `v2.5.1` tag 源码生成:`panel_cli/data/api-v2.5.1.json`(866 条路由,Swagger 收录 794 条) |
| 面板源码 | 随技能附带于 `source/`(1Panel v2.5.1 tag 完整源码,2083 个文件;下表路径均相对该目录) |
| 单元测试 | 143 个用例全部通过(`python3 -m unittest discover -s tests`) |

## v2.5.1 路由变化(相对 v2.4.2 tag 源码)

源码路由清单由 821 条变为 **866 条**,新增 54 条、移除 9 条:

| 变化 | 方法与路径 | 说明 |
|---|---|---|
| 新增 ×41 | `/api/v2/vms/*` | **虚拟机模块升级为顶级菜单**:环境检测/启用、实例增删改查/启停、健康检查/统计、快照、ISO 导入、网络与网桥/网卡、存储池、模板、孤儿数据扫描/清理、VNC 控制台(含 WebSocket) |
| 新增 ×6 | `GET /ai/gpu/groups`、`POST /ai/gpu/groups/{save,del}`、`POST /ai/model/gpu/usage`、`POST /ai/accounts/chat`、`POST /ai/accounts/models/verify-model` | GPU 组/NUMA 绑定、模型账户流式对话与模型校验 |
| 新增 ×4 | `GET /containers/image-updates`、`POST /containers/image-updates/{check,schedule}`、`POST /containers/image/remote/search` | 容器镜像更新检查(手动/定时)与远程镜像搜索 |
| 新增 ×3 | `POST /runtimes/php/extensions/{detail,import,push}` | PHP 扩展模板详情、导入与推送(配合 xpack 同步) |
| 移除 ×5 | `POST /ai/tensorrt/{search,create,update,delete,operate}` | 移除 TensorRT-LLM 支持 |
| 移除 ×3 | `POST /ai/vllm/monitor/{search,current,clean}` | vLLM 监控迁移到 xpack 路由,社区版不再注册 |
| 移除 ×1 | `GET /core/auth/welcome` | 登录欢迎页接口移除,登录仍使用 `auth/setting` + `auth/login` |

> 统计口径:54 = 41(VM)+ 6(AI)+ 4(容器)+ 3(PHP 扩展);9 = 5(TensorRT)+ 3(vLLM 监控)+ 1(welcome)。除上述差异外,v2.4.2 的路由全部保留。

新增的 xpack/企业版路由(如 `/api/v2/xpack/model/downloader/*`、`/api/v2/xpack/vllm/*`)只在运行时 Swagger 中出现,不纳入内置开源清单;`api list` 会在当前面板 Swagger 可用时补充显示。

## ⚠️ 官方发行包与 tag 源码的 1 条路由差异

官方 v2.5.1 发行包与本机实机核验(2026-11-17)显示,**仅 1 条 tag 源码路由未注册**:

| 未注册路由 | 表现 |
|---|---|
| `POST /api/v2/ai/accounts/chat` | agent 侧 `404 page not found`。该路由由 v2.5.1 tag 最后一个提交「feat(ai): add streaming model chat drawer」新增,官方发行包构建早于该提交 |

说明:(1) 其余 865 条路由全部可用(845 条实调 + 20 条已知副作用路由跳过,跳过项均已在面板 Swagger 中确认注册)。(2) `ai/accounts/chat` 是流式对话接口,CLI 的 `api` 透传本身不解析 SSE,发行包注册后也建议仅用于简单探测;模型对话请优先使用面板页面或对应的流式客户端。(3) 除这 1 条外,内置清单与 v2.5.1 发行包完全一致。

**CLI 的处理**:内置清单按 tag 源码收录(866 条,对齐全源),文档与 `api list` 说明标注该路由调用会 404。`catalog.py` 在调用未注册路由时会返回明确错误,不会把 HTML 回退页误当成业务响应。

## 非路由变更审计(v2.4.2 → v2.5.1)

对 `agent/app/dto`、`agent/app/service`、`core/app/dto`、`core/app/service`、`core/middleware` 全量 diff 后,与 CLI 现有命令相关的变更如下:

| 模块 | 变更 | CLI 影响 |
|---|---|---|
| 应用 `apps` | 应用安装/升级/重建/启停改为任务化(ollama/vllm 任务无超时),`AppInstallDTO` / `AppInstalledCheck` 新增 `taskID`;升级时保留 `.env` 原值,支持从已下架版本升级;新增镜像更新检查 | `apps list --installed`、`apps info/config` 实机通过;`taskID` 为新增字段,旧解析兼容;`apps install --wait` 仍按 `logs/tasks/search` 轮询。ollama/vllm 安装任务服务端无超时,CLI 默认等待 600 秒,大模型下载建议 `--timeout 3600` 或更长 |
| 网站 `web` | 创建网站、启用 HTTPS、添加域名时新增同域名同端口冲突检查(`ErrDomainIsUsed`);OpenResty 性能调优新增 `proxy_connect_timeout` / `proxy_send_timeout` / `proxy_read_timeout`;brotli 状态新增 `managedPending` | 请求字段未变;`web list/config` 实机通过;冲突时面板会返回明确业务错误。CLI 的 SPA/反代/HTTPS 流程不需要改参数 |
| Nginx | http 级配置读取改为只读直接指令,并补全 `conf.d` 与 default 中的 http 级值;Brotli 在 include 可自动插入时报告 `managedPending` | 仅影响面板「性能调优」页面;`web config` 仍读写站点原始配置,不受影响 |
| 数据库 `db` / Redis `redis` | 无接口/DTO 变化 | `db list/users`、`redis info/config` 实机通过 |
| 防火墙 `firewall` | 无接口/DTO 变化,v2.4.0 统一规则接口继续适用 | `firewall status/ipv6/list` 实机通过,探测走 `v240` 分支 |
| 登录/鉴权 | `auth/welcome` 移除;密码过期中间件改为统一恢复路由白名单;新增 trusted proxy / 审计 IP 处理 | 登录、`auth/setting`、`auth/current`、`settings/search/base` 实机通过;CLI 不调用 `welcome` |
| MCP / AI / 容器 / PHP | 新增 DTO/服务(模型对话、GPU 组、镜像更新、PHP 扩展模板、VM);MCP 网站绑定新增 `deleteWebsite` 可选字段 | CLI 暂无专用命令;可用 `api` 透传,源码已同步 |
| 初始化 `init` | 本机 v2.5.1 的 `/opt/1panel/db` 为 `0700 root`,普通用户无法直接读取/`exists()` | **已修复**:`init` 在直接读取失败时用 `sudo -n cat` 复制 core/agent DB 及 WAL/SHM 到私有临时目录,再解密;实测可生成 `.env` |

## 防火墙接口(与 v2.4.2 一致,相对 v2.3.2 的语义)

v2.5.1 防火墙接口与 v2.4.0/v2.4.1/v2.4.2 完全一致,CLI 的 `v240` 探测分支原样适用:

| 项 | v2.3.2 | v2.4.0+ / v2.5.1 |
|---|---|---|
| 探测依据 | 有 `GET /firewall/settings`,无规则备份接口 | `GET /hosts/firewall/rules/backups` 可用 |
| 规则删除 | `{"uuids": [...]}` | `{"targets": [{scope, instanceKey, observed}]}` |
| 清单字段 | `state` / `desired.uuid` | `isWhitelist` / `observed.protected` / `observed.instanceKey` |
| 分页 | `total/allTotal/managedTotal`,含 `refresh/states` | `total/allTotal`,移除 `refresh/states` |
| IPv6 开关 / 分族操作 | 无 | `POST /settings/ipv6`、`POST /family/operate` |
| Docker 端口守护 | 策略含 `description`;有 `docker/sync` | 策略移除 `description`,新增 `accept_sources/accept_all`;`docker/sync` 移除 |
| 端口转发 | `forceDelete` | 改为 `import`,规则可选 `family` |

v2.4.0+ 写操作均为异步任务,CLI 自动轮询 `/logs/tasks/search` 并汇报;删除前自动跳过 `isWhitelist=true` / `observed.protected=true` 的规则。

## API 清单全量核验(2026-11-17,本机 v2.5.1)

对 `panel_cli/data/api-v2.5.1.json` 的 866 条路由逐条向 v2.5.1 面板发起请求,核验「路由是否注册、方法是否正确」:

- **845 条实调正常**:业务响应 200×132、400×672(写路由发送畸形 JSON,在参数校验阶段失败)、403×3、500×37、503×1;
- **20 条跳过实调**:已知副作用/特殊约束路由(清空日志、注销、重启、安装 redis-cli、同步状态等),均确认已在面板 Swagger 中注册;
- **1 条未注册**:`POST /api/v2/ai/accounts/chat`(发行包差异,见上文);
- 结论:865/866 路由可用,除 1 条已知差异外内置清单与 v2.5.1 面板完全一致;
- 可重复执行:`python3 scripts/verify_api_routes.py --profile <p>`;本次报告:`/tmp/api-verify-v2.5.1.json`。

## 实测记录

### 2026-11-17,1Panel v2.5.1(cn,本机)

- **环境检查**:`doctor --force` 6/6 通过,基线显示 `v2.5.1`;重新登录成功。
- **会话**:`status` → `v2.5.1 (cn)`。
- **应用**:`apps list --installed`(openresty/mysql/redis)、`apps info openresty`、`apps config mysql` 正常。
- **网站**:`web list`、`web config start-1.abcd.edu.cn`、`web https ...`(只读状态)正常。
- **数据库 / Redis**:`db list`、`db users --instance mysql`、`redis info redis`、`redis config redis` 正常。
- **防火墙**:`firewall status`、`firewall ipv6 status`、`firewall list` 正常,后端 ufw(未运行)读取无异常,探测为 `v240`。
- **新接口**:`api describe POST vms/search`、`api get vms/environment`、`api get ai/gpu/groups`、`api describe POST containers/image-updates/check`、`api describe POST runtimes/php/extensions/detail` 正常;`sourcePath` 指向 v2.5.1 `source/`。
- **数据库写流程冒烟**:创建临时 MySQL 数据库 → 创建带授权的临时用户 → 列表核对 → 删除用户/数据库;清理后数据库与用户均为 0。
- **网站写流程冒烟**:创建临时静态网站 → 列表/读取 nginx 配置/HTTPS 状态 → 删除;清理后仅剩原有 1 个网站。
- **初始化**:`init` 在非 root 用户下通过 `sudo -n cat` 读取 `0700 root` 的 DB 快照,成功生成 `.env`(密码未输出到终端)。
- **单元测试**:143 个用例全部通过。

### 未在本机执行(说明)

| 项 | 原因 |
|---|---|
| 虚拟机创建/启停/快照 | 本机无 `/dev/kvm` 与 libvirt(`vms/environment` 返回 `ready=false`);仅核验了接口注册与只读环境检测 |
| 容器镜像更新检查 | 本机无 Docker 容器/镜像更新任务;接口已核验注册,POST 走参数校验失败路径 |
| 防火墙规则/转发/IPv6/Docker 守护写流程 | 本机 ufw 未运行、转发链与 Docker 守护未初始化,不改动现有网络;接口与 v2.4.0 基线零变化 |
| 应用安装/升级、HTTPS 证书申请、SPA 回退写入 | 避免长时间下载或改动现有业务站点;数据库/网站的基础写流程已用临时资源完成冒烟并清理,这些接口已核验注册 |

## 与旧版本的兼容性

- v2.4.2 / v2.4.1 / v2.4.0:tag 路由集与 v2.5.1 有上述增删,CLI 的 `v240` 防火墙探测与登录流程继续适用;旧路由未使用已移除的 TensorRT/vLLM 监控接口。
- v2.3.2:仍走 `v231` 分支,删除提交 `uuids`,Docker 策略保留 `description`,规则清单显示 `state`。
- 中间版本:仍走 `checked` 分支(`rules/check` + `checkFlag`)。
- v2.2.x:仍自动回退旧门面接口。
- 版本探测只增加一次只读 `GET /rules/backups`;旧面板返回 404 时不会误判。
