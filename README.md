# 1panel-cli (Python) v0.4.1 — 1Panel v2.2.5 面板管理

通过命令行工具 `1panel-cli` 管理 [1Panel](https://1panel.cn) 面板。所有操作均通过面板官方 API 完成,效果与面板管理页面一致。

本仓库是 [tech-1panel-cli](../tech-1panel-cli)(Rust 实现)的 **Python 移植版**,面向 **1Panel v2.2.5** 验证:
API 路由清单由 1Panel `v2.2.5` 源码生成(754 条),登录 / 应用商店 / 数据库 / Redis / 网站 / 防火墙 / 终端执行等命令与 Rust 版逐项对齐,并对 v2.2.5 的旧版防火墙接口做自动适配。

## 特点

- **零第三方依赖**:仅使用 Python 标准库(`urllib` / `http.cookiejar` / `socket` / `ssl`),无需 `pip install`;系统自带的 `python3`(3.9+)即可运行。
- **纯 Python 实现**:RSA PKCS#1 v1.5 公钥加密、AES-256-CBC、自定义 RFC6455 WebSocket 客户端、multipart 上传均为内置实现。
- **与 Rust 版输出对齐**:命令树、参数、表格排版、`exec` 的 JSON 结构与退出码语义保持一致,可直接替换使用。
- **Agent Skill**:符合 [Agent Skills](https://opencode.ai/docs/skills/) 标准的 `SKILL.md`,可直接用于 opencode / Claude Code 等工具。

## 功能

- **初始化 `init`**:在本机 1Panel 上执行 `sudo 1pctl user-info` 并结合面板数据库解密,一条命令自动生成 `.env`(地址/用户名/密码),无需手动填写
- **会话**:`login / status / info / logout / doctor`,自动完成 RSA+AES 密码加密、安全入口、验证码与 MFA;登录一次后会话凭据保存在本机(`~/.config/1panel-cli/<profile>.json`,权限 0600)
- **应用商店 `apps`**:列出/安装应用、查看应用详情(含数据库与 Redis 连接信息)、修改配置参数与数据库 root 密码
- **数据库 `db`**:MySQL/MariaDB 数据库/用户增删改查、root 密码
- **Redis `redis`**:实例状态/配置读写/密码
- **网站 `web`**:网站列表、nginx 配置读写(保存后自动 reload)、HTTPS/SSL 证书、文件上传与解压
- **防火墙 `firewall`**:自动探测并适配新版统一规则接口与 v2.2.5 旧接口;v2.2.5 走旧接口(状态/启停/Ping、端口/IP/转发规则增删查、批量放行);新版面板额外支持 Docker 端口守护(`firewall docker`)
- **远程执行 `exec`**:通过终端 WebSocket 在面板本机执行单行命令,退出码透传,支持 `--cwd`、`--timeout`、`--json`、`--tail` 与输出清洗;配置 `LINUX_SSH_USER`/`LINUX_SSH_PWD` 后 `login` 自动配置、`exec` 自动对齐面板本地 SSH 连接(默认以该用户执行,不接触 root);`--sudo` 提权执行 root 命令(需免密 sudo);`doctor` 实测 SSH 连通性与免密 sudo
- **API `api`**:内置 v2.2.5 源码生成的 754 条路由目录,可 list/describe 并调用 GET/POST/PUT/DELETE,自动携带 Cookie、CSRF、语言和节点头

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
   1panel-cli firewall status           # 防火墙状态
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

脚本会同步 `SKILL.md` / `commands.md` / `README.md` / `bin/1panel-cli` / `panel_cli/` 与 v2.2.5
参考资料,并**保留目标目录已有的 `.env` 与 `bin/.env`**(真实凭据不被覆盖)。

安装后技能中的 CLI 入口是纯 Python 启动器,可直接执行:

```sh
~/.config/opencode/skills/1panel-skill/bin/1panel-cli --version
```

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
| `LINUX_SSH_USER` / `LINUX_SSH_PWD` | 否 | 面板所在主机的 SSH 凭据:`login` 自动写入面板本地 SSH 连接,`exec` 自动对齐(exec 默认以该用户执行,root 命令用 `--sudo`) |

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
├── commands.md            # 完整命令速查表(会话 / apps / db / redis / web / firewall / exec / api)
├── .env.example           # 面板账号配置模板
├── pyproject.toml         # 可选的 pip 安装配置(控制台入口 1panel-cli)
├── docs/
│   └── exec-command-dev.md    # exec 子命令实现说明(终端 WS / 哨兵 / 本地 SSH 连接)
├── references/
│   └── compatibility-v2.2.5.md  # v2.2.5 接口兼容性核对
├── scripts/
│   ├── generate_api_catalog.py  # 从 1Panel 源码生成 API 清单
│   └── install_skill.sh         # 安装/更新到 opencode skill 目录
├── panel_cli/             # Python 包(零第三方依赖)
│   ├── main.py            #   argparse 命令树与分发
│   ├── initcmd.py         #   init:自动探测本机面板并生成 .env
│   ├── config.py          #   .env / 环境变量 / 命令行覆盖
│   ├── crypto.py          #   RSA PKCS1v15 + AES-256-CBC + URL 解码
│   ├── session.py         #   会话持久化(0600)
│   ├── client.py          #   urllib + CookieJar HTTP 客户端
│   ├── ws.py              #   极简 RFC6455 WebSocket 客户端
│   ├── auth.py            #   登录(验证码 / MFA / 安全入口)
│   ├── commands.py        #   status / info / logout / api 透传
│   ├── catalog.py         #   API 清单 + Swagger 合并
│   ├── apps.py / db.py / redis.py / web.py / firewall.py
│   ├── exec.py            #   终端执行(哨兵 / 清洗 / 退出码)
│   ├── doctor.py          #   8 项环境体检
│   └── data/api-v2.2.5.json   # v2.2.5 源码生成的 754 条路由
├── bin/
│   └── 1panel-cli         # 可执行入口(#!/usr/bin/env python3)
└── tests/                 # unittest 单元测试(101 个用例)
```

## 更新 API 清单(可选)

1Panel 源码升级后,可重新生成内置路由清单:

```sh
python3 scripts/generate_api_catalog.py \
  --source /path/to/1Panel \
  --version v2.2.5 \
  --output panel_cli/data/api-v2.2.5.json
```

## 测试

```sh
python3 -m unittest discover -s tests -v
```

覆盖:密码加密(RSA/AES 已知向量与往返)、验证码 ID 解析、URL 解码、命令组装与哨兵匹配、输出清洗、WebSocket 帧编解码、请求头/SSL 上下文、防火墙规则构造、路由清单与路径归一化、.env 解析等。

## License

MIT
