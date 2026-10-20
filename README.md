# 1panel-cli (Python) v2.4.0 — 1Panel v2.4.0 面板管理

通过命令行工具 `1panel-cli` 管理 [1Panel](https://1panel.cn) 面板。所有操作均通过面板官方 API 完成,效果与面板管理页面一致。

本仓库是 [tech-1panel-cli](../tech-1panel-cli)(Rust 实现)的 **Python 移植版**,面向 **1Panel v2.4.0** 验证:
API 路由清单由 1Panel `v2.4.0` 源码生成(788 条),完整源码随仓库附带于 [`source/`](source/),请求失败或参数不确定时可直接对照 router / DTO / service 排障。登录 / 应用商店 / 数据库 / Redis / 网站 / 防火墙等命令与 Rust 版逐项对齐,并对 v2.4.0 统一防火墙接口(v2.3.2 删除语义变化、IPv6 开关、Docker 策略字段调整)与旧版(v2.2.x)接口做自动适配。

## 特点

- **零第三方依赖**:仅使用 Python 标准库(`urllib` / `http.cookiejar` / `socket` / `ssl`),无需 `pip install`;系统自带的 `python3`(3.9+)即可运行。
- **纯 Python 实现**:RSA PKCS#1 v1.5 公钥加密、AES-256-CBC、multipart 上传均为内置实现。
- **与 Rust 版输出对齐**:命令树、参数、表格排版与输出文本保持一致,可直接替换使用。
- **源码参考**:随仓库/技能附带 1Panel v2.4.0 完整源码 `source/`;请求失败或参数不确定时,可按 `api describe` 输出的 `sourcePath` 阅读 router / DTO / service 源码。
- **Agent Skill**:符合 [Agent Skills](https://opencode.ai/docs/skills/) 标准的 `SKILL.md`,可直接用于 opencode / Claude Code 等工具。

## 功能

- **初始化 `init`**:在本机 1Panel 上执行 `sudo 1pctl user-info` 并结合面板数据库解密,一条命令自动生成 `.env`(地址/用户名/密码),无需手动填写
- **会话**:`login / status / info / logout / doctor`,自动完成 RSA+AES 密码加密、安全入口、验证码与 MFA;登录一次后会话凭据保存在本机(`~/.config/1panel-cli/<profile>.json`,权限 0600)
- **应用商店 `apps`**:列出/安装应用、查看应用详情(含数据库与 Redis 连接信息)、修改配置参数与数据库 root 密码
- **数据库 `db`**:MySQL/MariaDB 数据库/用户增删改查、root 密码
- **Redis `redis`**:实例状态/配置读写/密码
- **网站 `web`**:网站列表、新建网站(支持一步完成反代 + HTTPS + SPA 回退,如 `web create app.example.com --proxy http://127.0.0.1:3500 --ssl-id 1 --spa`)、删除网站、SPA 前端路由回退配置、nginx 配置读写(保存后自动 reload)、HTTPS/SSL 证书、文件上传与解压、本地应用路径兼容修复(`web repair`)
- **防火墙 `firewall`**:自动探测四种接口形态——v2.4.0 统一规则接口(删除改为提交 `scope + instanceKey + observed`)、v2.3.2 统一规则接口、中间版本(带 `rules/check`)与 v2.2.x 旧接口;写操作均为异步任务,CLI 自动等待并汇报结果。支持状态/启停/Ping、端口/IP/转发规则增删查、批量放行、IPv6 开关(`firewall ipv6`,v2.4.0+),以及 v2.3.x+ 的 Docker 端口守护(`firewall docker`,含流量接管识别)
- **API `api`**:内置 v2.4.0 源码生成的 788 条路由目录,可 list/describe 并调用 GET/POST/PUT/DELETE,自动携带 Cookie、CSRF、语言和节点头;`describe` 会给出 `source/` 内对应的源码路径
- **敏感信息脱敏**:所有 `api` 响应默认脱敏——命中 `privateKey` / `password` / `token` / `secret` 等字段(及任意位置出现的 PEM 私钥块)输出为 `***REDACTED***`,避免私钥、口令进入终端日志或智能体上下文;确需原值时显式使用 `--show-secrets` 或 `PANEL_CLI_SHOW_SECRETS=1`,`api post/put --body-file` 可从文件/标准输入读取请求体,避免敏感内容出现在命令行

## 已知问题(1Panel 本地应用)

离线/内置应用商店的本地应用在旧版面板上注册为 `local<key>`(如 `localopenresty`),
而面板部分代码按规范 key(`openresty`)查询会失败。配套的离线安装包(patched agent,
本地应用保留规范 key)已不再触发以下问题;CLI 对两种 key 写法保持自动兼容,历史数据
遇到时可用 `web repair` 排查:

1. **nginx 相关接口路径错误**:固定读取 `<应用目录>/<key>/<name>`,实际在 `<应用目录>/local/<key>/<name>`。表现:HTTPS 操作报 500、`web https` 报 `open .../conf/nginx.conf: no such file or directory`、创建网站时 SSL 静默不生效(数据库显示已开启、nginx 仍是 HTTP)。CLI 在 `web create/config/https/spa` 前自动创建软链(`/opt/1panel/apps/<key>` → `/opt/1panel/apps/local/<key>`)。
2. **删除网站报 `record not found`**:删除流程固定按 `openresty` 查询应用记录。使用 `web delete <domain> --force`:自动备份 `agent.db`、临时改名应用 key、删除后还原(需本工具与面板同机)。
3. **应用 key 解析**:`apps install/info/config/password` 自动兼容 `openresty` 与 `localopenresty` 两种写法;
   `db`/`redis` 的实例名同样自动解析 `local` 前缀(`--instance mysql` ≡ `--instance localmysql`)。

另外:反代 React/Vue 等 SPA 且后端对深层路由返回 404 时,用 `web create --spa` 或 `web spa <domain>` 开启回退;配置会自动包含 `proxy_intercept_errors on`(缺少它时 `error_page` 对上游 404 不生效),并排除 API 前缀、静态资源与隐藏路径。

## 环境要求

- Python 3.9+(仅标准库,无第三方依赖;本机验证 Python 3.12)
- 可访问 1Panel 面板(HTTP/HTTPS,自签名证书可设置 `PANEL_INSECURE=true`)

## 快速开始

1. **生成配置**(面板与本工具在同一台主机时,一条命令自动完成,无需填写):

   ```sh
   ./bin/1panel-cli init
   ```

   也可手动配置:复制 `.env.example` 为 `.env` 并填写面板信息。

2. **环境检查**(建议每次操作前先执行):

   ```sh
   ./bin/1panel-cli doctor
   ```

3. **登录**:

   ```sh
   ./bin/1panel-cli login
   ```

4. **常用命令**:

   ```sh
   1panel-cli status                    # 检查登录状态
   1panel-cli apps list --installed     # 已安装应用
   1panel-cli apps install mysql --wait # 安装 MySQL(等待完成)
   1panel-cli apps info mysql           # 应用详情 + 数据库连接信息
   1panel-cli db list                   # 列出数据库
   1panel-cli redis info redis          # Redis 状态与配置
   1panel-cli web list                  # 列出网站/域名
   1panel-cli firewall status           # 防火墙状态(含 IPv6 开关)
   1panel-cli firewall ipv6 status      # IPv6 防火墙状态(v2.4.0+)
   1panel-cli api get core/auth/current # 调用面板任意 API
   1panel-cli api list --filter templates # 发现源码注册的 API
   ```

   也可以直接以模块方式运行:`python3 -m panel_cli status`(需保证 `panel_cli` 在 `PYTHONPATH`)。

   完整命令速查见同目录 `commands.md`。

## 安装为全局命令(可选)

```sh
# 方式一:软链接到 PATH
sudo ln -s "$(pwd)/bin/1panel-cli" /usr/local/bin/1panel-cli

# 方式二:有 pip 的环境下安装(生成 1panel-cli 入口)
pip install .
```

## 作为 Skill 安装(opencode / Claude Code)

一条命令安装/更新到 opencode 技能目录(默认 `~/.config/opencode/skills/1panel-skill`):

```sh
scripts/install_skill.sh                 # 更新到默认技能目录
scripts/install_skill.sh /path/to/skill  # 或指定目录
```

脚本会同步 `SKILL.md` / `commands.md` / `README.md` / `bin/1panel-cli` / `panel_cli/`、v2.4.0
参考资料以及完整源码 `source/`,并**保留目标目录已有的 `.env` 与 `bin/.env`**(真实凭据不被覆盖)。

安装后技能中的 CLI 入口是纯 Python 启动器,可直接执行:

```sh
~/.config/opencode/skills/1panel-skill/bin/1panel-cli --version
```

### 从 GitHub Actions 下载技能包

仓库自带 `.github/workflows/skill-package.yml`:

1. 在 GitHub 仓库的 **Actions** 页选择 **Build Skill Package** → **Run workflow**;
2. 运行完成后会自动发布 GitHub Release(手动运行使用 `v<pyproject 版本>` 作 tag,已存在则更新),也可在该次运行的 **Artifacts** 区域下载 `1panel-skill-<版本>`;
3. 解压得到 `1panel-skill/` 目录,执行 `bash install.sh` 自动安装(优先 opencode,其次 Claude Code,也可传入目标目录),或手动把该目录复制到对应 skills 目录。

推送 `v*` tag 时同样触发构建,并以该 tag 发布 Release。

## 配置说明(.env)

| 变量 | 必填 | 说明 |
|---|---|---|
| `PANEL_URL` | 是 | 面板地址,可包含安全入口路径,如 `http://10.0.0.1:41394/abcdef1234`(可由 `init` 自动生成) |
| `PANEL_USERNAME` | 是 | 面板登录用户名 |
| `PANEL_PASSWORD` | 是 | 面板登录密码 |
| `PANEL_ENTRANCE` | 否 | 安全入口(也可从 `PANEL_URL` 路径自动识别) |
| `PANEL_LANGUAGE` | 否 | 请求语言,默认 `zh` |
| `PANEL_NODE` | 否 | 多节点目标节点,默认 `local`;也可用 `--node` |
| `PANEL_MFA_CODE` | 否 | 开启 MFA 时的动态验证码(自动化场景) |
| `PANEL_CAPTCHA` / `PANEL_CAPTCHA_ID` | 否 | 登录验证码答案与 ID(配合外部 OCR 自动化) |
| `PANEL_INSECURE` | 否 | 面板为 HTTPS 且使用自签名证书时设为 `true` |

CLI 启动时依次从当前目录、入口脚本所在目录、仓库根目录查找 `.env`,也可用 `--env <path>` 指定,或通过 `--profile <name>` 保存多套配置;同时支持环境变量与命令行参数。配置优先级:**命令行参数 > 环境变量 > .env 文件**。

`.env`/`--profile`/`--node` 等全局参数放在子命令前后均可(如 `1panel-cli --env .env status` 与 `1panel-cli status --env .env` 等价)。

## 安全说明

- `.env` 包含面板登录密码,**请勿提交到版本库**(仓库已通过 `.gitignore` 忽略)。
- 登录会话凭据保存在 `~/.config/1panel-cli/<profile>.json`(权限 0600),请勿泄露给他人。
- 密码等敏感信息出现在命令行参数时,注意日志与 shell 历史脱敏。

## 仓库结构

```
1panel-cli-python/
├── SKILL.md               # 技能主体(触发条件、环境检查、命令速查、关键约定)
├── commands.md            # 完整命令速查表(会话 / apps / db / redis / web / firewall / api)
├── .env.example           # 面板账号配置模板
├── pyproject.toml         # 可选的 pip 安装配置(控制台入口 1panel-cli)
├── .github/workflows/skill-package.yml  # Actions:打包可下载的技能包(手动运行 / v* tag 发 Release)
├── references/
│   └── compatibility-v2.4.0.md  # v2.4.0 接口兼容性核对
├── source/                # 1Panel v2.4.0 完整源码(路由 / DTO / service,排障权威参考)
├── scripts/
│   ├── generate_api_catalog.py  # 从 1Panel 源码生成 API 清单
│   ├── verify_api_routes.py     # 逐条核验内置路由在真实面板上的注册情况
│   └── install_skill.sh         # 安装/更新到 opencode skill 目录
├── panel_cli/             # Python 包(零第三方依赖)
│   ├── main.py            #   argparse 命令树与分发
│   ├── initcmd.py         #   init:自动探测本机面板并生成 .env
│   ├── config.py          #   .env / 环境变量 / 命令行覆盖
│   ├── crypto.py          #   RSA PKCS1v15 + AES-256-CBC + URL 解码
│   ├── session.py         #   会话持久化(0600)
│   ├── client.py          #   urllib + CookieJar HTTP 客户端
│   ├── auth.py            #   登录(验证码 / MFA / 安全入口)
│   ├── commands.py        #   status / info / logout / api 透传
│   ├── catalog.py         #   API 清单 + Swagger 合并
│   ├── apps.py / db.py / redis.py / web.py / firewall.py
│   ├── doctor.py          #   6 项环境体检
│   └── data/api-v2.4.0.json   # v2.4.0 源码生成的 788 条路由
├── bin/
│   └── 1panel-cli         # 可执行入口(#!/usr/bin/env python3)
└── tests/                 # unittest 单元测试(142 个用例)
```

## 更新 API 清单(可选)

仓库附带的 `source/` 即 v2.4.0 源码,可直接重新生成内置路由清单:

```sh
python3 scripts/generate_api_catalog.py \
  --source source \
  --version v2.4.0 \
  --output panel_cli/data/api-v2.4.0.json
```

1Panel 源码升级后,把新版源码放到 `source/`(或指定其他路径)再执行同一命令即可。

## 测试

```sh
python3 -m unittest discover -s tests -v
```

覆盖:密码加密(RSA/AES 已知向量与往返)、验证码 ID 解析、URL 解码、命令组装与哨兵匹配、输出清洗、请求头/SSL 上下文、防火墙规则构造、v2.4.0 删除 target 载荷、路由清单与路径归一化、API 调用结果归类、.env 解析等。

### 核验 API 路由

对已登录的面板逐条核验内置 788 条路由是否注册可用。写操作发送畸形 JSON 请求体,在参数校验阶段即失败,不会产生业务副作用;有副作用且无法用请求体拦截的路由自动跳过并标注。v2.4.0 面板实测:788/788 全部注册,无 404/HTML/方法不符。

```sh
python3 scripts/verify_api_routes.py
python3 scripts/verify_api_routes.py --filter websites --output /tmp/api-verify.json
```

退出码:0=未发现异常,1=存在 404/HTML 等异常,2=环境或配置错误。

## License

MIT
