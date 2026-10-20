# 兼容性说明:Python 版 × 1Panel v2.4.0

本文件记录 `1panel-cli`(Python 版)与 **1Panel v2.4.0 stable** 的接口核对结果,以及相对 v2.3.2 的接口变化与兼容处理。CLI 仍保留 v2.3.2 / 中间版本 / v2.2.x 的自动探测,连接旧面板时会回退到对应接口。

## 验证环境

| 项 | 值 |
|---|---|
| 面板版本 | v2.4.0(cn;`1panel-cli status` 实测 `v2.4.0 (cn)`) |
| 面板类型 | 本机安装,地址 `127.0.0.1:17778`(含安全入口) |
| CLI 实现 | Python 3.12,仅标准库;CLI 版本 `2.4.0` |
| 内置 API 清单 | 由 1Panel `v2.4.0` 源码生成:`panel_cli/data/api-v2.4.0.json`(788 条路由,Swagger 收录 759 条) |
| 面板源码 | 随技能附带于 `source/`(1Panel v2.4.0 完整源码;下表路径均相对该目录) |
| 单元测试 | 142 个用例全部通过(`python3 -m unittest discover -s tests`) |

## v2.4.0 路由变化(相对 v2.3.2)

源码路由清单由 787 条变为 **788 条**,新增 6 条、移除 5 条:

| 变化 | 方法与路径 |
|---|---|
| 新增 | `GET /api/v2/hosts/firewall/rules/backups` |
| 新增 | `POST /api/v2/hosts/firewall/family/operate` |
| 新增 | `POST /api/v2/hosts/firewall/settings/ipv6` |
| 新增 | `POST /api/v2/ai/vllm/monitor/search` |
| 新增 | `POST /api/v2/ai/vllm/monitor/current` |
| 新增 | `POST /api/v2/ai/vllm/monitor/clean` |
| 移除 | `GET /api/v2/hosts/firewall/rules/sync/task` |
| 移除 | `POST /api/v2/hosts/firewall/rules/sync` |
| 移除 | `POST /api/v2/hosts/firewall/rules/sync/preview` |
| 移除 | `POST /api/v2/hosts/firewall/rules/adopt` |
| 移除 | `POST /api/v2/hosts/firewall/docker/sync` |

CLI 使用的登录、应用商店、网站、数据库、Redis、文件上传等路由在 v2.4.0 仍然存在。

## 防火墙接口变化与适配

### 规则删除:v2.3.2 `uuids` → v2.4.0 `targets`

v2.3.2:

```json
{"uuids": ["<rule-uuid>"]}
```

v2.4.0(`dto.FirewallRuleDelete`):

```json
{
  "targets": [
    {
      "scope": {"provider": "iptables", "family": "ipv4", "table": "filter", "chain": "1PANEL_BASIC", "direction": "input"},
      "instanceKey": "sha256:...",
      "observed": {"rule": {}, "locator": {}, "instanceKey": "sha256:...", "parseStatus": "supported", "protected": false}
    }
  ]
}
```

`observed` 是规则清单项中的完整对象,服务端用它做删除前的精确校验(`deleteRules` 会核对 scope、instanceKey 与 observed,并拒绝受保护规则)。清单项在 v2.4.0 中不再包含 `desired` / `state`,改为:

```json
{
  "rule": {"scope": {}, "protocol": "tcp", "destinationPort": "8080", "action": "accept"},
  "observed": {"instanceKey": "sha256:...", "marker": "1panel-rule:...", "protected": false, "persistence": "converged"},
  "isWhitelist": false,
  "descriptionID": "firewall:sha256:..."
}
```

CLI 的适配:

1. `api_kind()` 通过 `GET /hosts/firewall/rules/backups` 是否可用识别 `v240`(该路由仅 v2.4.0+ 提供);
2. `delete_request()` 在 `v240` 下提交 `targets`,在 v2.3.2 下继续提交 `uuids`;
3. 删除前自动跳过 `isWhitelist=true` / `observed.protected=true` 的规则;若匹配项全部受保护,CLI 给出明确提示而不是提交必然失败的任务;
4. 删除仍为异步任务,继续轮询 `/logs/tasks/search` 并汇报结果。

### 规则清单字段变化

| 项 | v2.3.2 | v2.4.0 |
|---|---|---|
| 清单项状态 | `state`(managed/adopted/external/drifted/protected) | 无 `state`;用 `isWhitelist` / `observed.protected` / `observed.persistence` 表达 |
| 可删除标识 | `desired.uuid` | `observed.instanceKey` + 完整 `observed` |
| 分页响应总量 | `total` / `allTotal` / `managedTotal` | `total` / `allTotal`(移除 `managedTotal`) |
| 查询参数 | 含 `refresh` / `states` | 移除 `refresh` / `states` |
| 规则备份 | 无 | `GET /rules/backups?subsystem=system\|forwarding\|docker` |

CLI 的 `firewall list` 在 v2.4.0 下显示 `whitelist` / `protected` / `converged` 等状态,不再依赖已移除的 `state`。

### IPv6 与分族操作(新增)

v2.4.0 新增三个防火墙设置接口:

| 接口 | 用途 |
|---|---|
| `GET /api/v2/hosts/firewall/settings` | 响应新增 `ipv6Enabled`;分族状态新增 `partial` |
| `POST /api/v2/hosts/firewall/settings/ipv6` | `{"status":"Enable"\|"Disable"}`,异步任务 |
| `POST /api/v2/hosts/firewall/family/operate` | `subsystem/backend/family/operation`,用于 iptables/nftables 分族初始化、修复、绑定 |

CLI 新增:

```sh
1panel-cli firewall ipv6 status
1panel-cli firewall ipv6 enable
1panel-cli firewall ipv6 disable
```

`firewall status` 会显示 `IPv6: 已启用/未启用`;`firewall docker status` 会显示 Docker 守护的 IPv6 支持与分族 `partial` 标记。

### Docker 端口守护变化

| 项 | v2.3.2 | v2.4.0 |
|---|---|---|
| 策略备注 | `description` 字段 | **移除**;CLI 在 v2.4.0 不再提交该字段 |
| 策略模式 | `deny_sources` / `allow_sources` / `deny_all` | 新增 `accept_sources` / `accept_all`;旧模式仍兼容 |
| 手动同步 | `POST /docker/sync` | **移除**;CLI 的 `firewall docker sync` 在 v2.4.0 上提示无需同步 |
| 备份/恢复 | 无 | `backupFile` 字段 + 子系统备份接口 |

`firewall docker ports` 已支持显示 `accept_sources`(接受来源)与 `accept_all`(接受所有)。

### 端口转发请求体

v2.4.0 将 `ForwardRuleOperate.forceDelete` 改为 `import`,并为每条规则增加可选 `family`(ipv4/ipv6)。CLI 在 v2.4.0 下提交 `import=false`,并按 `--to` 目标地址自动推断 `family`;v2.3.2 及更早版本继续提交 `forceDelete=false`。

## 非防火墙 DTO 差异审计

对 `agent/app/dto` 与 `core/app/dto` 全量 diff,变化仅涉及:

- `alert.go`、`cronjob.go`、`monitor.go`、`ssh.go`:告警/计划任务/监控/SSH 新功能;
- `database.go`:新增 `RedisCliInstall` 请求(仅 `/databases/redis/install/cli`);
- `firewall.go`、`forwarding.go`:上述防火墙变化;
- `core/app/dto` 无变化。

CLI 使用的应用商店、网站、数据库、Redis、文件、任务日志请求/响应字段均未变化;1Panel v2.4.0 对这些接口的内部实现有调整(应用任务状态恢复、网站创建回滚、Redis CLI 安装任务化等),但不改变 CLI 的调用方式。

## 关键接口核对

| 功能 | 接口 | 结论 |
|---|---|---|
| 登录设置/验证码/登录/MFA/当前用户/登出 | `/api/v2/core/auth/*` | ✅ 路由与字段未变 |
| 面板版本 | `POST /api/v2/core/settings/search/base` | ✅ |
| 防火墙状态 | `POST /api/v2/hosts/firewall/base` | ✅ 新增 `ipv6Enabled` |
| 统一规则列表 | `POST /hosts/firewall/rules/search` | ✅ 响应改为 `{rule,observed,isWhitelist,descriptionID}` |
| 统一规则创建 | `POST /hosts/firewall/rules` | ✅ `items[].rule + sourceKind=user`,异步任务 |
| 统一规则删除 | `POST /hosts/firewall/rules/delete` | ✅ v2.4.0 改为 `targets` |
| 端口转发 | `/hosts/firewall/forward/{search,operate,base}` | ✅ `forceDelete` → `import` |
| 防火墙启停/Ping | `POST /hosts/firewall/operate` | ✅ 异步任务 |
| 防火墙 IPv6 开关 | `POST /hosts/firewall/settings/ipv6` | ✅ 新增,异步任务 |
| 防火墙分族操作 | `POST /hosts/firewall/family/operate` | ✅ 新增,可用 `api` 调用 |
| 规则备份列表 | `GET /hosts/firewall/rules/backups` | ✅ 新增,可用 `api get hosts/firewall/rules/backups` |
| Docker 端口守护 | `/hosts/firewall/docker/{ports,endpoints,operate,policies/*}` | ✅ 策略移除 `description`,新增 `accept_*` 模式 |
| 数据库列表/增删/用户 | `/api/v2/databases*` | ✅ DTO 未变 |
| Redis 状态/配置/密码 | `/api/v2/databases/redis/*` | ✅ 仅 CLI 安装接口新增任务参数,CLI 未使用 |
| 网站列表/创建/配置/HTTPS/SSL | `/api/v2/websites*` | ✅ DTO 未变 |
| 上传/解压 | `/api/v2/files/upload`、`/files/decompress` | ✅ |
| 应用商店/安装/任务查询 | `/api/v2/apps*`、`/logs/tasks/search` | ✅ DTO 未变 |

## API 清单全量核验(2026-10-20,本机 v2.4.0)

对 `panel_cli/data/api-v2.4.0.json` 的 788 条路由逐条向 v2.4.0 面板发起请求,核验「路由是否注册、方法是否正确」:

- **788/788 全部注册**:无 404、无 HTML、无方法不符。
- 实调 767 条(GET 直接调用;写操作使用畸形 JSON 请求体,在参数校验阶段失败,不产生业务副作用),21 条已知有副作用/特殊约束的路由跳过并在 Swagger 中确认注册。
- 业务响应:200×116、400×615、403×3、500×33(均为预期的参数校验、面板内部代理限制或探针数据缺失)。
- 结论:内置清单与 v2.4.0 面板完全一致;`api get/post` 可直接调用全部路由。
- 可重复执行:`python3 scripts/verify_api_routes.py --profile <p>`;本次报告:`/tmp/api-verify-v2.4.0.json`。

## 实测记录

### 2026-10-20,1Panel v2.4.0(cn,本机)

- `init`:自动生成本机 `.env`(0600,地址解析为 `127.0.0.1:17778/****`)。
- `doctor`:6/6 通过,面板版本显示 `v2.4.0 (cn)`。
- 会话:`login / status` 通过。
- `firewall status`:iptables 后端,`IPv6: 已启用`,后端选择(系统/转发/Docker)正常。
- `firewall ipv6 status`:显示 `IPv6 防火墙: 已启用`。
- `firewall list --type port`:正常读取 2.4.0 新清单结构,白名单规则显示 `whitelist`。
- `firewall docker status / ports`:正常显示守护状态、IPv6 支持、流量接管端口与孤立策略。
- `firewall docker sync`:正确提示 v2.4.0 已移除该接口。
- **规则写流程**:临时添加 `45999/tcp`(备注 `cli-v240-test`)→ 异步任务成功 → 清单可见 → 删除 → 异步任务成功 → 清单为 0 条。验证了 v2.4.0 `targets` 删除载荷与异步任务轮询。
- 单元测试:142 个用例全部通过(新增 v2.4.0 删除 target、受保护规则跳过、状态字段兼容、Docker 新模式显示等用例)。

### 未在本机执行(说明)

| 项 | 原因 |
|---|---|
| 端口转发写流程 | 本机端口转发链未初始化,实测添加任务返回 `firewall provider is unavailable`(面板要求先启用端口转发);`import/family` 载荷已做构造核验,未改动本机 NAT 规则。可在面板「防火墙 → 端口转发」启用后重试 |
| IPv6 启用/禁用写操作 | 会重建宿主机 IPv6 防火墙链,影响面较大;仅验证只读状态接口 |
| Docker 端口守护策略写入 | 本机守护未初始化(`guard_chain_missing`),未执行初始化以影响现有容器网络 |
| 规则备份/分族操作 | 新接口已通过路由全量核验,未执行实际恢复/重建 |

## 与 v2.3.2 的兼容性

- v2.3.2 面板:仍走 `v231` 分支,删除提交 `uuids`,Docker 策略保留 `description`,规则清单显示 `state`。
- 中间版本:仍走 `checked` 分支(`rules/check` + `checkFlag`)。
- v2.2.x:仍自动回退旧门面接口。
- 版本探测只增加一次只读 `GET /rules/backups`;旧面板返回 404 时不会误判。
