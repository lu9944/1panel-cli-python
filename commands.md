# 1panel-cli v2.4.0 命令速查(Python / 1Panel v2.4.0)

首次使用先运行 `1panel-cli init` 自动生成 `.env`(无需手填账号);然后运行 `1panel-cli doctor`,确认六项检查通过。所有命令支持全局参数 `--env <PATH>`、`--profile <NAME>`、`--node <NODE>`;节点也可由 `PANEL_NODE` 设置。全局参数放在子命令前后均可。

> 本清单面向 1Panel v2.4.0 验证;旧版面板(v2.2.x)的旧防火墙接口与中间版本(带 `rules/check` 的统一接口)会自动探测并回退。
>
> 请求失败或参数不确定时,阅读随技能附带的 1Panel v2.4.0 完整源码 `source/`:`api describe` 输出的 `sourcePath` 可直接定位文件(路由 `source/*/router/`、DTO `source/*/app/dto/`、行为 `source/*/app/service/`)。内置路由清单 `panel_cli/data/api-v2.4.0.json` 即由该源码生成。

## 会话

| 命令 | 说明 |
|---|---|
| `init [-o PATH] [--force] [--host HOST] [--print]` | **在本机自动探测面板并生成 .env**:执行 `sudo 1pctl user-info` 获取地址/用户/初始密码;密码已掩码时读取面板数据库(`/opt/1panel/db/*.db`)并用 `EncryptKey` 解密,无需手动填写 |
| `login` | 使用本地账号源登录,保存会话 |
| `status` | 检查会话并显示面板版本/版本类型 |
| `info` | 查看脱敏后的会话信息 |
| `logout` | 登出并清除本地凭据 |
| `doctor [--force]` | 检查配置、连通性、会话、登录、面板版本兼容性(6 项);别名 `check` |

## apps — 应用商店

| 命令 | 说明 |
|---|---|
| `apps list [--name X] [--type X] [--tag X] [--installed] [--all]` | 列出应用 |
| `apps install <name> [--version V] [--param K=V] [--wait] [--timeout S] [--no-pull]` | 安装应用(`--no-pull` 离线场景直接用本地镜像) |
| `apps info <name>` | 应用详情及数据库/Redis 连接信息 |
| `apps config <name> [--set K=V] [--compose]` | 查看/修改配置 |
| `apps password <name> <new>` | 修改 mysql/mariadb/redis root 密码 |
| `apps tags` | 列出分类标签 |

## db — MySQL / MariaDB

| 命令 | 说明 |
|---|---|
| `db list [--type mysql\|mariadb] [--instance NAME]` | 列出指定类型和实例的数据库 |
| `db create <name> [--user U] [--password P] [--instance NAME]` | 创建数据库 |
| `db delete <name> [--type mysql\|mariadb] [--instance NAME] [--force]` | 按正确数据库类型删除 |
| `db users [--instance NAME]` | 列出用户 |
| `db user add <user> <pass> [--host %] [--dbs a,b] [--instance NAME]` | 创建用户并授权 |
| `db user del <user> [--host %] [--instance NAME]` | 删除用户 |
| `db user passwd <user> <new> [--host %] [--instance NAME]` | 修改用户密码 |
| `db passwd <new> [--instance NAME]` | 修改实例 root 密码 |

> 实例名即应用安装名。离线包中三应用以规范 key 注册(`mysql`/`redis`),若历史数据使用
> `local` 前缀名称(如 `localmysql`),CLI 会自动互相解析,`--instance` 传任一写法均可。

## redis

| 命令 | 说明 |
|---|---|
| `redis list` | 列出实例 |
| `redis info <name>` | 状态与配置 |
| `redis config <name> [--set K=V]` | 查看/修改配置 |
| `redis passwd <name> <new>` | 修改密码 |

## web — 网站 / OpenResty

| 命令 | 说明 |
|---|---|
| `web list` | 列出网站和实际网站目录 |
| `web create <domain> [--type static] [--alias X] [--remark X]` | 新建网站 |
| `web create <domain> --proxy http://127.0.0.1:3500 [--ssl-id N] [--spa]` | **一步完成反代 + HTTPS + SPA 回退**(指定 `--proxy` 时自动使用 proxy 类型) |
| `web config <domain> [--file x.conf]` | 读取/修改 nginx 配置 |
| `web https <domain> [--enable --ssl-id N \| --cert/--key \| --disable]` | 查看/设置 HTTPS |
| `web spa <domain> [--proxy URL] [--api-prefixes a,b] [--remove]` | 开启/关闭 SPA 前端路由回退(刷新深层路由不再 404) |
| `web delete <domain> [--force]` | 删除网站;报 `record not found` 时用 `--force` 走本地应用 key 兼容模式 |
| `web repair` | 修复本地应用路径兼容问题(软链) |
| `web ssl <domain> --email x [--provider http] [--key-type RSA2048] [--no-auto-renew]` | 申请证书 |
| `web upload <domain> <file> [--to DIR]` | 上传文件 |
| `web extract <domain> <archive> [--to DIR] [--type zip]` | 解压文件 |

### web 常见问题(本地应用商店)

- **反代目标**：`--proxy` 接受 `http://127.0.0.1:3500` 或 `127.0.0.1:3500`(自动补 `http://`);也可先建站再 `web spa` 自动读取现有反代目标。
- **HTTPS 报 500 / 开启后配置未生效**：本地应用路径问题,`web https/config/spa` 已内置自动软链修复;仍失败时运行 `web repair`(等价手动 `ln -s /opt/1panel/apps/local/openresty /opt/1panel/apps/openresty`)。
- **SPA 回退原理**：写入面板「自定义重写」(`/www/sites/<alias>/rewrite/<alias>.conf`),包含 `proxy_intercept_errors on`(必须,否则上游 404 不会触发 `error_page`)+ `error_page 404 = @spa_fallback`,回退到后端 `/index.html`;`/api` 等前缀、静态资源、隐藏路径保持原 404。
- **删除网站报 `record not found`**（历史数据）:旧版面板的删除流程固定按 `openresty` 查询应用记录(本地应用 key 为 `localopenresty`)。配套离线包已让本地应用以规范 key 注册,不再触发;历史数据可用 `web delete <domain> --force`(备份 agent.db、临时改名后再删除并自动还原,需与面板同机)。
- **应用 key / 实例名**:配套离线包注册规范 key(`openresty`/`mysql`/`redis`);历史数据可能是 `local*` 写法,CLI 的 `apps`/`db`/`redis` 命令会自动兼容两种写法。

## firewall

CLI 会自动探测四种接口形态:

| 形态 | 探测依据 | 说明 |
|---|---|---|
| `v240`(1Panel v2.4.0+) | `GET /hosts/firewall/rules/backups` 可用 | 删除规则提交 `targets`(`scope` + `instanceKey` + `observed`);新增 IPv6 开关/分族操作与规则备份 |
| `v231`(1Panel v2.3.2) | 有 `GET /hosts/firewall/settings`,无 `rules/check`、无 `rules/backups` | 统一规则接口;删除按 `uuids`;写操作返回 `taskID` |
| `checked`(中间版本) | 有 `GET /hosts/firewall/settings` 且有 `rules/check` | 创建前先规则检查(`checkFlag`) |
| `legacy`(v2.2.x) | 无 `/hosts/firewall/settings` | 旧门面接口(`/firewall/search|port|ip|forward|batch`) |

v2.3.2+ 面板的写操作均为异步任务(规则创建/删除、端口转发、Docker 端口策略、防火墙启停),CLI 默认等待任务完成(轮询超时 60 秒)并显示成功或失败原因。v2.4.0 的规则清单项不再返回 `desired/state`,CLI 从 `observed.instanceKey` 与完整 `observed` 构造删除请求,并自动跳过白名单/受保护规则。

| 命令 | 说明 |
|---|---|
| `firewall status` | 状态、后端与后端选择(系统/转发/Docker) |
| `firewall start\|stop\|restart` | 生命周期操作(等待异步任务) |
| `firewall allow-ping\|ban-ping` | Ping 策略 |
| `firewall list [--type port\|address\|forward] [--info X] [--strategy X]` | 规则列表(统一接口显示托管状态/来源) |
| `firewall port add <port> [--protocol tcp\|udp\|tcp/udp] [--strategy accept\|drop] [--source IP] [--desc X]` | 添加端口规则 |
| `firewall port del <port> [--protocol P] [--strategy S] [--source IP]` | 删除匹配的托管端口规则 |
| `firewall batch <p1,p2,...> [--protocol P]` | 批量添加端口规则 |
| `firewall ip add <address> [--strategy drop] [--desc X]` | 添加 IP 规则 |
| `firewall ip del <address> [--strategy drop]` | 删除匹配的托管 IP 规则 |
| `firewall forward add <port> --to IP:PORT [--protocol P] [--interface X]` | 添加端口转发 |
| `firewall forward del <port> --to IP:PORT [--num N]` | 删除端口转发 |
| `firewall ipv6 status` | 查看 IPv6 防火墙开关(需 v2.4.0+) |
| `firewall ipv6 enable\|disable` | 启用/禁用 IPv6 防火墙(异步任务,需 v2.4.0+) |

> v2.3.2+ 的 `POST /hosts/firewall/port` 仅供面板内部使用(从面板探针调用返回 403),统一接口下不要通过 `api post` 直接调用旧接口。v2.4.0 新增 `/hosts/firewall/rules/backups`、`/hosts/firewall/family/operate`、`/hosts/firewall/settings/ipv6`,并移除 `rules/adopt`、`rules/sync*`、`docker/sync`;这些新接口可用 `api` 透传调用。
>
> 若 `firewall forward add` 返回任务失败 `firewall provider is unavailable`,说明端口转发链尚未初始化:请先在面板「防火墙 → 端口转发」启用转发,或执行 `api post hosts/firewall/forward/enable`(v2.3.2+)。

### firewall docker(Docker 端口守护,需 v2.3.x+)

| 命令 | 说明 |
|---|---|
| `firewall docker status` | 守护状态(后端/初始化/绑定/生效) |
| `firewall docker ports` | 列出容器已发布端口、防护策略、管控目标与策略 UUID |
| `firewall docker sync` | 同步 Docker 端口防护规则(v2.4.0 已移除该接口,命令会提示无需同步) |
| `firewall docker operate [--operation init\|bind\|unbind]` | 初始化/绑定/解绑守护(初始化等待异步任务) |
| `firewall docker allow <PORT> --sources a,b [--protocol P] [--desc X]` | 仅允许指定来源访问已发布端口 |
| `firewall docker deny <PORT> [--sources a,b] [--protocol P] [--desc X]` | 拒绝指定来源;省略 --sources 时拒绝所有来源 |
| `firewall docker policy-del --uuid <UUID>` | 删除一条防护策略(等待异步任务) |

v2.4.0 的 Docker 端口守护注意事项:

- 写操作请求体为 `policies` 列表(按 `family/hostIP/hostPort/protocol/mode/sources`),CLI 已按端口地址族拆分 `--sources`;某个已发布地址族没有对应来源时会报错而不是静默跳过。v2.4.0 移除了策略 `description` 字段,CLI 不再提交该字段,`--desc` 在 v2.4.0 上不生效(旧版仍保留备注)。
- v2.4.0 新增 `accept_sources` / `accept_all` 两种导入策略模式,`firewall docker ports` 会显示对应中文说明。
- 若端口流量由宿主机防火墙接管(`managementTarget=host_firewall`,例如宿主防火墙启用后 Docker userland proxy 处理的 IPv6 映射),Docker 守护策略对其无效;CLI 会跳过该端口并提示改用 `firewall port/ip` 规则。
- v2.2.x 面板上执行 `firewall docker *` 会明确提示不支持。规则备份(`rules/backups`)、重置(`rules/reset`)、排序(`rules/reorder`)、分族操作(`family/operate`)与规则检查可通过 `api` 命令调用;v2.4.0 已移除规则同步(`rules/sync*`)。

## api — 发现和调用 API

| 命令 | 说明 |
|---|---|
| `api list [--filter TEXT]` | 列出官方源码路由;按路径、处理器、摘要、标签过滤 |
| `api describe <METHOD> <PATH>` | 显示路由元数据,并合并当前面板 Swagger 请求模型 |
| `api get <PATH>` | GET |
| `api post <PATH> [--body JSON] [--body-file PATH]` | POST,`--body-file -` 从标准输入读取 |
| `api put <PATH> [--body JSON] [--body-file PATH]` | PUT,`--body-file -` 从标准输入读取 |
| `api delete <PATH>` | DELETE |

路径相对 `/api/v2`,请求自动带 Cookie、CSRF、`Accept-Language` 和 `CurrentNode`。内置清单由 1Panel `v2.4.0` 源码生成(788 条路由,Swagger 收录 759 条)。已在 v2.4.0 面板上全量核验:788 条路由全部注册可调用。`api describe` 支持用真实取值匹配参数路由(如 `websites/ca/1` 可匹配 `websites/ca/:id`);调用不存在的路由或方法不符时会给出明确报错,而不是打印 HTML。

响应默认脱敏:字段名命中 `privateKey` / `password` / `passwd` / `token` / `secret` / `accessKey` / `hmacKey` / `authorization` / `credential` 等,或值中出现 PEM 私钥块(`RSA/EC/OPENSSH/PGP ... PRIVATE KEY`),输出统一替换为 `***REDACTED***` / `***REDACTED PRIVATE KEY***`。确需原值(如读取证书私钥)时显式加全局参数 `--show-secrets`(或环境变量 `PANEL_CLI_SHOW_SECRETS=1`),并自行承担泄露风险;请求体含敏感内容时优先用 `--body-file`,不要让私钥出现在命令行参数里。

示例:

```sh
1panel-cli api list --filter firewall
1panel-cli api describe POST hosts/firewall/rules
1panel-cli api post hosts/firewall/rules/search --body '{"page":1,"pageSize":20,"info":"","all":true,"scopes":[{"provider":"ufw","family":"ipv4","chain":"incoming","direction":"input"}]}'
1panel-cli api post apps/search --body '{"page":1,"pageSize":10}'

# 导入本地 HTTPS 证书(nginx 格式 pem + key):JSON 经 stdin 提交,私钥不进命令行
python3 - <<'PY' | 1panel-cli api post websites/ssl/upload --body-file -
import json
print(json.dumps({
    "type": "paste",
    "privateKey": open("example.com.key").read(),
    "certificate": open("example.com.pem").read(),
    "description": "manual import",
}))
PY
```
