"""环境体检:配置、连通性、会话、登录、版本兼容性、本地 SSH 连接与 sudo 免密。

对应 Rust 版 doctor.rs。
"""

from __future__ import annotations

from .client import PanelClient
from .config import ConfigOverrides, PanelConfig, env_search_paths, linux_ssh_creds
from .errors import CliError
from .session import Session, load_session

TOTAL = 8


def step(n: int, title: str, ok: bool, detail: str) -> None:
    mark = "通过" if ok else "失败"
    print(f"[{n}/{TOTAL}] {title} ... {mark} ({detail})")


def step_warn(n: int, title: str, detail: str) -> None:
    print(f"[{n}/{TOTAL}] {title} ... 警告 ({detail})")


def guidance(lines: list[str]) -> None:
    for line in lines:
        print(f"    - {line}")


def find_env_file():
    for candidate in env_search_paths():
        if candidate.exists():
            return candidate
    return None


def verify_session(session: Session) -> bool:
    try:
        client = PanelClient(session.panel_url, session.cookies, session.insecure)
        resp = client.get("api/v2/core/auth/current")
        body = resp.json()
        return body.get("code") == 200
    except (CliError, ValueError):
        return False


def doctor(profile: str, force: bool) -> None:
    from . import auth
    from . import exec as exec_module
    from .commands import panel_version

    all_ok = True

    print(f"1Panel CLI 环境检查 (profile: {profile})\n")

    # 1. .env 配置文件
    env_path = find_env_file()
    ok1 = env_path is not None
    step(1, ".env 配置文件", ok1, str(env_path) if env_path else "未找到")
    if not ok1:
        all_ok = False
        guidance(
            [
                "在以下位置创建 .env 文件:",
                "  1) 当前目录 ./.env",
                "  2) 可执行文件所在目录 ./.env",
                "  3) 或通过 --env <路径> 显式指定",
                "可参考项目中的 .env.example 模板。",
            ]
        )

    # 2. 配置项
    try:
        cfg = PanelConfig.from_env(ConfigOverrides())
    except CliError as exc:
        print(f"[2/{TOTAL}] 配置项 ... 失败 ({exc})")
        guidance(
            [
                "缺少环境变量: PANEL_URL / PANEL_USERNAME / PANEL_PASSWORD",
                "在 .env 中添加,例如:",
                "  PANEL_URL=http://10.0.0.1:41394",
                "  PANEL_USERNAME=admin",
                "  PANEL_PASSWORD=your_password",
            ]
        )
        return
    entrance = cfg.entrance if cfg.entrance else "(无)"
    step(
        2,
        "配置项",
        bool(cfg.url and cfg.username and cfg.password),
        f"URL={cfg.url}, USER={cfg.username}, ENTRANCE={entrance}",
    )

    # 3. 面板地址连通性
    try:
        client = PanelClient(cfg.url, None, cfg.insecure)
    except CliError as exc:
        print(f"[3/{TOTAL}] 面板地址 ... 失败 ({exc})")
        return
    try:
        resp = client.get("api/v2/core/auth/setting")
        status = resp.status
        body = resp.text()
        try:
            code = resp.json().get("code")
        except ValueError:
            code = None
        ok3 = status == 200 and code == 200
        step(3, "面板地址连通性", ok3, f"HTTP {status}")
        if not ok3:
            all_ok = False
            guidance(
                [
                    f"面板返回异常状态: HTTP {status}, body: {body[:120]}",
                    "请检查:",
                    "  1) PANEL_URL 是否正确(含端口与路径)并能在浏览器访问",
                    "  2) 服务器网络/防火墙是否放行了该端口",
                    "  3) 面板服务是否在运行(可执行 1pctl status 查看)",
                ]
            )
    except CliError as exc:
        print(f"[3/{TOTAL}] 面板地址 ... 失败 ({exc})")
        all_ok = False
        guidance(
            [
                "无法连接面板地址,请检查:",
                "  1) PANEL_URL 格式是否为 http://IP:端口 或 https://域名",
                "  2) 目标主机是否可达(ping / curl 验证)",
                "  3) 面板端口是否放行;HTTPS 自签名证书需设置 PANEL_INSECURE=true",
            ]
        )

    # 4. 已保存会话
    try:
        session = load_session(profile)
    except CliError:
        session = None
    if session is not None:
        if session.panel_url != cfg.url:
            print(
                f"[4/{TOTAL}] 已保存会话 ... 失败 (会话地址 {session.panel_url} "
                f"与配置地址 {cfg.url} 不一致)"
            )
            all_ok = False
            guidance(
                [
                    "会话与 .env 中的面板地址不匹配,将重新登录。",
                    "如多个面板,请用 --profile 区分。",
                ]
            )
        else:
            try:
                sclient = PanelClient(session.panel_url, session.cookies, session.insecure)
                resp = sclient.get("api/v2/core/auth/current")
                body = resp.json()
                ok4 = body.get("code") == 200
                if ok4:
                    detail4 = ((body.get("data") or {}).get("name")) or "有效"
                else:
                    detail4 = "会话已失效"
                step(4, "已保存会话", ok4, f"用户名 {detail4}")
                if not ok4:
                    all_ok = False
                    guidance(["会话已过期或被服务器注销,将尝试重新登录(见下一步)。"])
            except (CliError, ValueError) as exc:
                print(f"[4/{TOTAL}] 已保存会话 ... 失败 ({exc})")
                all_ok = False
    else:
        print(f"[4/{TOTAL}] 已保存会话 ... 跳过 (尚未登录,将直接登录)")

    # 5. 登录验证
    session_ok = session is not None and session.panel_url == cfg.url and verify_session(session)
    if session_ok and not force:
        print(
            f"[5/{TOTAL}] 登录验证 ... 通过 (会话有效,无需重新登录;可用 --force 强制验证凭据)"
        )
    else:
        if force:
            print(f"[5/{TOTAL}] 登录验证 ... 使用 --force 强制重新登录以验证凭据")
        try:
            auth.login(cfg, profile)
            step(5, "登录验证", True, "登录成功,凭据已保存")
        except CliError as exc:
            print(f"[5/{TOTAL}] 登录验证 ... 失败 ({exc})")
            all_ok = False
            guidance(
                [
                    "请检查:",
                    "  1) PANEL_USERNAME / PANEL_PASSWORD 是否正确",
                    "  2) 若提示验证码:连续登录失败会触发验证码,请稍后重试或在面板清除限制",
                    "  3) 若提示 ErrEntrance:PANEL_ENTRANCE 安全入口是否正确",
                    "  4) 若开启 MFA:通过 PANEL_MFA_CODE 环境变量或 --mfa 参数提供动态验证码",
                ]
            )

    # 6. 面板版本兼容性
    try:
        version, edition = panel_version(profile)
        compatible = version.startswith(("v2.", "2."))
        step(
            6,
            "面板版本兼容性",
            compatible,
            f"{version} ({edition});已验证基线 v2.2.5",
        )
        if not compatible:
            all_ok = False
            guidance(["当前技能包面向 1Panel v2;请升级面板后再使用写操作。"])
    except CliError as exc:
        step(6, "面板版本兼容性", False, str(exc))
        all_ok = False

    # 7. 本地 SSH 连接(exec 前置):配置状态 + 与 .env 声明用户对齐 + 实测连通性
    creds_user, creds_pwd = linux_ssh_creds()
    try:
        conn = exec_module.local_conn_info(profile)
        conn_user: str | None = conn.user if conn.addr else None
    except CliError:
        conn_user = None

    if creds_user and creds_pwd:
        mismatch = conn_user is None or (conn_user and conn_user != creds_user)
        if mismatch:
            try:
                exec_module.ensure_local_conn(
                    profile, creds_user, creds_pwd, exec_module.DEFAULT_SSH_PORT
                )
                print(
                    f"[7/{TOTAL}] 本地 SSH 连接 ... 自动改配为用户 {creds_user}"
                    f"(原为 {conn_user or '未配置'})"
                )
                conn_user = creds_user
            except CliError as exc:
                step(
                    7,
                    "本地 SSH 连接 (exec 前置)",
                    False,
                    f"自动改配失败: {exc}",
                )
                all_ok = False
                guidance(
                    [
                        "写入面板「设置 → 终端 → SSH 本地连接」失败:",
                        "  1) 检查 LINUX_SSH_USER / LINUX_SSH_PWD 是否正确",
                        "  2) 确认主机 SSH 服务已启用(端口默认 22,可用 exec --ssh-port 调整)",
                    ]
                )

    ssh_ok = False
    who = conn_user or ""
    if conn_user is None:
        step(
            7,
            "本地 SSH 连接 (exec 前置)",
            False,
            "面板未配置,且 .env 未声明 LINUX_SSH_USER / LINUX_SSH_PWD",
        )
        all_ok = False
        guidance(
            [
                "exec 需要面板先配置「设置 → 终端 → SSH 本地连接」:",
                "  1) 在 .env 中添加 LINUX_SSH_USER / LINUX_SSH_PWD,重跑 doctor 或 login 即自动写入",
                "  2) 或到面板 UI 手动配置",
            ]
        )
    else:
        try:
            code, out = exec_module.run_capture(profile, "whoami", False, 20)
            if code == 0:
                actual = out.strip()
                if not actual:
                    extra = ""
                elif actual == conn_user:
                    extra = f",whoami 实测 {actual}"
                else:
                    extra = f",whoami 实测为 {actual}(与连接声明用户不一致)"
                step(7, "本地 SSH 连接 (exec 前置)", True, f"用户 {conn_user}{extra}")
                ssh_ok = True
            else:
                step(
                    7,
                    "本地 SSH 连接 (exec 前置)",
                    False,
                    f"whoami 退出码 {code}: {out.strip()}",
                )
                all_ok = False
                guidance(
                    [
                        f"用户 {conn_user} 的本地 SSH 连接验证失败,exec 无法执行:",
                        "  1) 凭据可能已变更:重跑 1panel-cli login 自动改配",
                        f"  2) 或到面板「设置 → 终端 → SSH 本地连接」核对 {conn_user} 的密码与端口",
                    ]
                )
        except CliError as exc:
            step(7, "本地 SSH 连接 (exec 前置)", False, str(exc))
            all_ok = False
            guidance(
                [
                    "exec 连通性验证失败:",
                    "  1) 重跑 1panel-cli login(或 exec --sync-ssh)改配本地 SSH 连接",
                    "  2) 确认主机 SSH 服务可达",
                ]
            )

    # 8. sudo 免密(exec --sudo 前置;不阻塞整体通过,仅告警)
    if not ssh_ok:
        print(f"[8/{TOTAL}] sudo 免密 ... 跳过 (上一步 SSH 连接未验证通过)")
    else:
        try:
            code, out = exec_module.run_capture(profile, "true", True, 20)
            if code == 0:
                step(
                    8,
                    "sudo 免密 (exec --sudo 前置)",
                    True,
                    f"用户 {who} 可免密 sudo",
                )
            else:
                step_warn(
                    8,
                    "sudo 免密 (exec --sudo 前置)",
                    f"sudo -n 退出码 {code}: {out.strip()}",
                )
                guidance(
                    [
                        f"用户 {who} 未配置免密 sudo,exec --sudo 将失败;如需提权,在主机上配置:",
                        f"  echo '{who} ALL=(ALL) NOPASSWD:ALL' | sudo tee /etc/sudoers.d/1panel-cli",
                        "  或将本地 SSH 连接改配为 root(此时不需要 --sudo)",
                    ]
                )
        except CliError as exc:
            step_warn(8, "sudo 免密 (exec --sudo 前置)", str(exc))
            guidance(
                [
                    f"无法验证用户 {who} 的免密 sudo;如需 --sudo 提权,在主机上配置:",
                    f"  echo '{who} ALL=(ALL) NOPASSWD:ALL' | sudo tee /etc/sudoers.d/1panel-cli",
                ]
            )

    print()
    if all_ok:
        print("环境检查全部通过,可以开始使用:")
        print("  1panel-cli status      检查登录状态")
        print("  1panel-cli apps list   列出可安装应用")
        print("  更多命令见 1panel-cli --help")
    else:
        print("环境检查未全部通过,请按上面提示修复后重试。")
