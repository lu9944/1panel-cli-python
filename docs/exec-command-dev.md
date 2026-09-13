# 开发文档:`1panel-cli exec` 在面板本机执行命令(Python 版)

> 目标版本:1Panel v2.2.5(已验证;新版统一防火墙接口自动兼容)。实现语言:Python 3(纯标准库)。

## 0. 参考与本地环境

### 0.1 参考源码

1Panel 源码位于本机(检出到 v2.2.5):

```text
/home/ubuntu/code/1panel-code/1Panel      # 1Panel 源码(HEAD: v2.2.5)
```

- 核心服务(面板 HTTP/会话/代理):`core/...`
- Agent(本机终端、SSH、设置):`agent/...`

阅读顺序建议:
1. `agent/app/api/v2/terminal.go` — 终端 WS 入口(本地/主机/容器)
2. `agent/utils/terminal/ws_session.go` — WebSocket 帧协议与 SSH 会话
3. `agent/app/api/v2/setting.go` / `agent/app/service/setting.go` — 本地 SSH 连接(LocalSSHConn)的保存与读取
4. `core/middleware/session.go`、`core/init/router/proxy.go`、`agent/middleware/certificate.go` — 认证与本地代理链路

### 0.2 本地测试环境

本机已安装 1Panel v2.2.5 stable(`http://192.168.1.18:31206/<entrance>`),`1pctl version` 可核对。测试配置见项目根目录 `.env`(含真实凭据,勿提交到仓库)。

面板本地 SSH 连接(`hosts/terminal/local` 的前置条件)可通过面板 UI「设置 → 终端 → SSH 本地连接」配置,也可由 `exec` 命令在运行时用环境变量自动配置(见 4.4)。

### 0.3 `exec` 相关环境变量

```dotenv
LINUX_SSH_USER=ubuntu   # 面板所在 Linux 主机的 SSH 用户名
LINUX_SSH_PWD=ubuntu    # 对应用户的 SSH 密码
```

约定:
- 由 `panel_cli/config.py` 的 `linux_ssh_creds()` 读取;
- `login` 成功后即用这两个变量写入面板本地 SSH 连接(addr 固定 `127.0.0.1`,port 默认 `22`,authMode `password`),作为初始化的一部分;
- `exec` 执行时自动对齐:连接未配置或用户与 `LINUX_SSH_USER` 不一致时自动覆盖写入——保证 exec 默认以 .env 声明的(普通)用户执行,不残留 root 连接;root 操作用 `--sudo`;
- `--sync-ssh` 为显式强制覆盖;未设置这两个变量时不自动配置,直接透传服务端"未配置本地连接"的错误;
- `doctor` 第 7 步实测本地连接(`whoami` 验证连通性与身份),第 8 步验证 `sudo -n true`(免密 sudo,`--sudo` 前置)。

## 1. 背景与目标

1Panel 的"终端"页面基于 WebSocket 实现,面板未暴露"执行命令并返回结果"的 REST 接口。`exec` 子命令通过 1Panel 的 WebSocket 终端接口,把一条**单行命令**推送到面板本机执行,按**"等待后一次性取回输出"**的方式展示结果,无需交互。

范围:
- 目标:仅面板本机(`hosts/terminal/local`)
- 输出:等待后一次性打印,且**默认清洗**(剥离 ANSI/回显/提示符,`--raw` 看原始流)
- 命令:单行命令;**退出码透传**(哨兵携带 `$?`,见 2.5)
- 机器可读:`--json` 结构化结果、`--tail N` 输出截断、`--cwd` 切目录(服务脚本/AI 场景)

## 2. 可行性验证(基于 v2.2.5 源码)

### 2.1 终端接口本身支持"执行单条命令"

`agent/app/api/v2/terminal.go`:

```go
func (b *BaseApi) WsLocalTerminal(c *gin.Context) {
    b.runSSHSession(c, loadLocalConn, c.DefaultQuery("command", ""))
}
```

`runSSHSession` 连接本机 SSH 后调用终端会话封装(`agent/utils/terminal/ws_session.go`),把 ` clear &&<command>\n` 写入 SSH shell,命令输出通过 WebSocket 以 base64 JSON 帧推回。**不需要交互。**

### 2.2 WebSocket 帧格式

`agent/utils/terminal/ws_session.go`:

```go
const (
    WsMsgCmd       = "cmd"
    WsMsgResize    = "resize"
    WsMsgHeartbeat = "heartbeat"
)
type WsMsg struct {
    Type      string `json:"type"`
    Data      string `json:"data,omitempty"`      // cmd: base64 编码的终端输出/输入
    Cols      int    `json:"cols,omitempty"`      // resize
    Rows      int    `json:"rows,omitempty"`      // resize
    Timestamp int    `json:"timestamp,omitempty"` // heartbeat
}
```

- **服务端 → 客户端**:`{"type":"cmd","data":"<base64>"}` 携带终端输出(定时批量推送);`heartbeat` 原样回显。
- **客户端 → 服务端**:`{"type":"cmd","data":"<base64 输入>"}`;`{"type":"resize",...}`;`heartbeat`(可选)。
- 我们只读不写即可(命令通过 URL 参数注入)。
- `cols`/`rows` 查询参数服务端用 `strconv.Atoi` 解析且失败即报错,必须传纯数字;服务端默认 80x40。
- agent 侧 `upGrader.CheckOrigin` 恒为 true,握手无需伪造 `Origin` 头。

### 2.3 握手成功后的错误如何传递(以 Close 帧为主)

`wshandleError` 是 agent 侧统一的 WS 错误通道:**优先发送携带错误文本的 WebSocket Close 帧**(payload 即错误消息),仅当写 Close 帧失败时才退化为一条 `cmd` 消息。触发点包括:

- 本地 SSH 连接未配置/凭据错误:`loadLocalConn` 失败(`no such ssh conn info in db` 等);
- `cols`/`rows` 非数字、agent 建连/PTY 申请失败等。

**实现要点**:WS 升级(HTTP 101)成功后,错误大概率以 `Close` 帧形式到达。Python 端的 `ws.py` 解析 Close 帧 payload 为 `code + reason`,reason 即错误文本;`exec.py` 的读循环必须处理 Close 帧 reason 并将其作为服务端错误上报,不能只等 `cmd` 消息。注意面板 agent 发送的 Close 帧 payload 可能不含 RFC 状态码(长度 1),此时 reason 标记为 `Protocol violation`,CLI 结合 `settings/ssh/conn` 探测给出"本地连接未配置/凭据已变更"的准确归因。

### 2.4 认证链路(关键结论:复用会话 Cookie 即可)

WS 握手是 GET 请求,会穿过 core 的全局中间件链:

```text
WhiteAllow → BindDomain → FrontendFallback → OperationLog → GlobalLoading
→ CoreAPIAuth(API Key) → PasswordExpired → CSRFTokenGuard → RBAC → Proxy
```

对 `GET /api/v2/hosts/terminal/local` 逐项核对(v2.2.5 源码):

- **会话 Cookie**:v2 的会话 cookie 名是 **`psession`**;登录时面板还会下发 `SecurityEntrance`、`pcsrftoken`、`panel_public_key` cookie。`1panel-cli` 登录后保存**全部** cookie 并在后续请求重放,因此实现上无需关心具体名字。
- **实际拦 API 的是谁**:真正校验会话的是 `PasswordExpired()`(对 `/api/v2/*` 调 `global.SESSION.Get`,失效返回 `ErrNotLogin` JSON)与 `Proxy()` 内的 `checkSession`(失效返回 **401 HTML**)。错误处理需同时兼容 JSON 与 HTML 两种 401 响应——`ws.py` 对非 101 握手响应按状态码归因。
- **CSRF**:`CSRFTokenGuard` 只校验非 GET/HEAD/OPTIONS/TRACE,WS 握手是 GET,**无需 CSRF token**(`--sync-ssh` 的 POST 仍需,`client.py` 已带)。
- **节点选择**:`Proxy()` 优先读 `operateNode` 查询参数,其次 `CurrentNode` 请求头;`currentNode` 为 `local` 或空时,`/api/v2/hosts/terminal/local` 直接代理给本机 agent(Unix socket,`httputil.ReverseProxy` 原生支持 WS 升级),agent 侧在 `global.IsMaster` 时直接放行,**不需要额外的 agent API Key**。
- 因此 WS 握手只需携带全部会话 Cookie + `CurrentNode: local` 请求头;URL 无需拼接安全入口路径(API 路径不经过 entrance 校验)。

### 2.5 完成信号(关键难点)

- 命令执行完后 **shell 不退出**,WS 连接会一直保持,服务端不会自动关闭。
- 服务端关闭 WS 的时机与输出发送缓冲存在**竞态**,可能丢弃最后一批输出,因此**不能依赖 WS 关闭作为完成信号**。

**方案:随机哨兵携带 `$?`(单次命中即完成 + 退出码透传)**

- 实际发送(服务端会拼成 ` clear &&<命令行>\n`):`<user_cmd>; echo __1PCLI_<随机token>_$?`
- PTY 开启回显时,哨兵在输出中出现 2 处但**形态不同**:
  1. 回显的输入行里是**字面量** `__1PCLI_<token>_$?`(`$?` 未展开);
  2. `echo` 真正输出的是**展开后**的 `__1PCLI_<token>_<0-255>`。
- 因此匹配 `__1PCLI_<token>_(\d{1,3})`,**单次命中即判定命令完成**,捕获组即命令退出码;随后主动关闭 WS。
- 收益:**退出码可透传**;不依赖回显必然出现(`stty -echo` 场景仍可完成);折行拆断暴露面减半。
- 不依赖 WS 关闭时序 → 规避尾部输出丢失。

**容错:** 若哨兵未出现(0 次命中),以 `--timeout` 兜底(默认 30s),超时打印已收集内容并返回 124。

**注意(终端折行会拆断哨兵)**:PTY 列数有限(`--cols`,默认 120),回显输入行与输出行都会在列边界被插入 `\r\n`(软折行),哨兵串可能被拦腰截断,朴素子串匹配会漏判。对策:

- 在**归一化后的流**上匹配:把累计输出先剥离 ANSI 转义序列与 `\r\n` 再做匹配(展示时仍用原始流);
- 选用足够短的随机 token(8 个 hex 字符),降低跨折行边界的概率。

另外服务端的 logout 检测是**整批缓冲恰好等于** `\r\nlogout\r\n`,正常命令输出不会误触发。

## 3. 命令设计

```text
1panel-cli exec <COMMAND>
    [--timeout SECS]     超时(默认 30;超时返回 124,已收集内容仍会打印)
    [--cwd DIR]          执行前先切换到 DIR(拼为 cd -- 'DIR' && <COMMAND>)
    [--cols N]           PTY 列数(默认 120)
    [--rows N]           PTY 行数(默认 40)
    [--json]             输出单行结构化 JSON,适合脚本/AI 消费
    [--tail N]           仅显示清洗后输出的最后 N 行
    [--raw]              不做输出清洗(保留 ANSI/回显/提示符/哨兵;默认清洗)
    [--sync-ssh]         用 LINUX_SSH_USER / LINUX_SSH_PWD 自动配置面板本地 SSH 连接后执行
    [--ssh-port N]       --sync-ssh 时使用的 SSH 端口(默认 22)
    [--sudo]             以 root 身份执行:命令包进 `sudo -n -H bash -c '<cmd>'`
                         (--cwd 的 cd 一并移入 sudo 环境内);要求 SSH 用户已配置免密 sudo
```

示例:

```sh
1panel-cli exec 'ls -la /var/www'
1panel-cli exec 'df -h && free -m'
1panel-cli exec 'bash deploy.sh' --cwd /opt/myapp --timeout 300
1panel-cli exec 'systemctl status docker' --timeout 10; echo "exit=$?"
1panel-cli exec 'whoami' --json
1panel-cli exec 'bash build.sh' --cwd /opt/app --tail 50
1panel-cli exec 'whoami' --sync-ssh
```

返回码:
- `0`:命令完成且退出码为 0
- `1-255`:命令完成,**透传命令退出码**(命令本身退出 1 与 CLI 基础设施错误同为 1,需精确区分时用 `--json` 的 `error` 字段)
- `1`:CLI 侧错误(握手失败/服务端 Close 帧报错/`--sync-ssh` 失败/预校验不过)
- `124`:超时(对齐 GNU `timeout` 惯例;已收集内容仍会打印)

### 3.1 输出清洗与 AI 友好性

- **默认清洗**:以哨兵行为锚,剥离 ANSI 转义、PTY 回显输入行(` clear &&...`)、哨兵输出行及其后的提示符残余;`--raw` 保留原始流。
- **`--json`**:单行 `{"exit_code":N,"duration_ms":N,"truncated":false,"error":null,"output":"..."}`;`output` 默认为清洗后文本。CLI 自身出错时输出带 `error` 的 JSON 并退出 1。
- **上下文保护**:收集上限默认 10MB(超出截断并标记 `truncated`);`--tail N` 仅显示末 N 行。
- **长任务用"无状态轮询"**:
  ```sh
  1panel-cli exec 'nohup bash deploy.sh > /tmp/deploy.log 2>&1 &'
  1panel-cli exec 'tail -n 50 /tmp/deploy.log'
  1panel-cli exec 'pgrep -f deploy.sh'
  ```

## 4. 实现说明(Python)

### 4.1 模块划分(`panel_cli/`)

- `ws.py` — 极简 RFC6455 客户端:握手(自定义 `cookie`/`currentnode` 头)、文本/二进制/分片帧、Ping 自动 Pong、Close 解析、socket 超时。纯 `socket` + `ssl` 实现,`--insecure` 时使用不校验证书的 SSLContext。
- `exec.py`:
  - `ws_url()`:`http:// → ws://`、`https:// → wss://`,拼 `/api/v2/hosts/terminal/local`,查询参数 `command`(percent-encoding)、`cols`、`rows`(纯数字校验)。
  - `build_handshake()`:拼接全部 `session.cookies`;`CurrentNode` 取 `--node` / `PANEL_NODE` / 默认 `local`(与 `client.py` 一致),非字母数字字符 percent-encode。
  - `ensure_local_conn()`:`POST api/v2/settings/ssh`(SaveLocalConn),password base64 编码;保存即由面板真实拨号校验。
  - `compose_command()`:预校验(非空、无换行、超长告警)、`--cwd` 单引号转义、`( ... )` 子 shell 包裹、追加 `; echo __1PCLI_<token>_$?`;`--sudo` 时包进 `sudo -n -H bash -c '<inner>'`,`cd` 移入 sudo 环境内。
  - `run_ws_session()`:读循环解码 `{"type":"cmd","data":base64}`,累积上限 10MB,归一化流上匹配哨兵(窗口上限 1MB+64KB),Close 帧归因,deadline 超时。
  - `clean_output()` / `tail_lines()` / `to_json()`:输出清洗、截断与结构化结果。
  - `run_capture()`:供 `doctor` 内部调用(执行 `whoami` / `true`)。
- `client.py` — `urllib` + `http.cookiejar`,登录后自动重放 Cookie,POST/PUT/DELETE 自动附加 `X-CSRF-Token`。

### 4.2 本地 SSH 连接自动配置(`--sync-ssh`)

面板"本地 SSH 连接"保存在 agent 设置 `LocalSSHConn`,`hosts/terminal/local` 依赖它。REST 接口(路由注册见 `agent/router/ro_setting.go:39-43`):

- 写入:`POST api/v2/settings/ssh`,请求体(`dto.SSHConnData`)。**`password` 必须先 base64 编码**:
  ```json
  {
    "addr": "127.0.0.1",
    "port": 22,
    "user": "<LINUX_SSH_USER>",
    "authMode": "password",
    "password": "<base64(LINUX_SSH_PWD)>"
  }
  ```
- **保存即校验**:`SaveConnInfo` 在写库前会真实 `gossh.Dial`(超时 5s)验证连通性,SSH 密码错误时该 POST 直接返回错误——凭据校验已内建。
- 探测是否已配置:`GET api/v2/settings/ssh/conn`(`LoadLocalConn`)返回 `addr` 为空即未配置;响应中的密码字段为 base64 回显,**勿打印**。
- 复用 `PanelClient.post_json`(会话 Cookie + CSRF)。

### 4.3 单元测试(已落地)

- `ws_url` 对 http/https/带端口/自签的 URL 构建正确
- 握手头拼装(`cookie` / `currentnode`)
- 哨兵判定:回显行(字面量 `$?`)不误判、真实输出单次命中、折行/ANSI 拆断归一化、0 次命中
- `compose_command`:cwd 单引号转义(空格/引号目录)、预校验拒绝换行与空命令、sudo 包裹
- `clean_output` / `tail_lines` / `to_json` 形态
- `ensure_local_conn` 请求体构建(不真正发请求)
- `ws.py` 帧编解码(掩码、扩展长度、Ping 自动 Pong、Close reason、分片)

## 5. 已知边界与风险

1. **前置条件**:`hosts/terminal/local` 依赖面板已保存"本地 SSH 连接";可用 `--sync-ssh` 自动写入,否则需先到面板 UI 配置。
2. **单行命令**:含换行会破坏 shell 解析;多行脚本列为后续增强。
3. **输出默认清洗**是启发式的,极端 PS1 场景可能残留杂项,可用 `--raw` 对照。
4. **哨兵启发式**:必须在归一化流上匹配;命中概率性冲突可忽略,异常由超时兜底。
5. **版本差异**:v2.2.5 与 dev-v2 的终端 WS 实现无差异;dev-v2 的差异主要在防火墙路由。`api list --filter terminal` 可复核路由存在。
6. **面板全局中间件的边缘拦截**:绑定域名、demo 模式、升级中(407)等,CLI 错误信息已区分这些状态码。
7. **凭据安全**:`LINUX_SSH_PWD` 只在 `--sync-ssh` 时经 `settings/ssh` 发给面板(body 中为 base64,非加密);`.env` 已被 `.gitignore` 忽略。
8. **交互式命令会挂到超时**:用非交互参数或免密 sudo;长任务用轮询模式。

## 6. 验证(已执行)

- 单元测试:`python3 -m unittest discover -s tests -v`(101 个用例)
- 手工冒烟(1Panel v2.2.5 stable):
  ```sh
  1panel-cli login && 1panel-cli status
  1panel-cli doctor                     # 8 项全通过(含 whoami 与 sudo 免密实测)
  1panel-cli exec 'echo hello && hostname'
  1panel-cli exec 'exit 7'; echo $?     # 7
  1panel-cli exec 'sleep 3; echo late' --timeout 2; echo $?   # 124
  1panel-cli exec 'whoami' --sudo       # root
  1panel-cli exec 'echo json' --json
  ```
  实测结果与 Rust 版逐字节一致(随机哨兵/时间除外)。
