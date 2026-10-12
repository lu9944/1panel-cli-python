"""登录流程:RSA+AES 密码加密、安全入口、验证码与 MFA。

对应 Rust 版 auth.rs。
"""

from __future__ import annotations

import base64
import os
import sys
import time
from dataclasses import dataclass

from .client import PanelClient
from .config import PanelConfig
from .crypto import debug_public_key, encrypt_password
from .errors import CliError
from .session import Session, save_session


@dataclass
class LoginData:
    mfa_status: str = ""
    mfa_session: str = ""


def check_code(code: int, message: str) -> None:
    if code != 200:
        raise CliError(f"面板返回错误 (code={code}): {message}")


def prompt(text: str) -> str:
    print(text, end="")
    sys.stdout.flush()
    line = sys.stdin.readline()
    if not line:
        raise CliError("读取输入失败(可能不是交互式终端)")
    return line.strip()


def now_string() -> str:
    secs = int(time.time())
    struct = time.gmtime(secs)
    return time.strftime("%Y-%m-%d %H:%M:%S (UTC)", struct)


def login(cfg: PanelConfig, profile: str) -> Session:
    client = PanelClient(cfg.url, None, cfg.insecure)

    resp = client.get("api/v2/core/auth/setting")
    try:
        setting = resp.json()
    except ValueError as exc:
        raise CliError(f"解析面板设置响应失败: {exc}") from exc
    check_code(setting.get("code", -1), setting.get("message", ""))
    setting_data = setting.get("data") or {}
    need_captcha = bool(setting_data.get("needCaptcha", False))
    panel_name = setting_data.get("panelName", "") or ""

    public_key = client.public_key_pem()
    debug_public_key(public_key)

    captcha, captcha_id = handle_captcha(client, cfg) if need_captcha else ("", "")

    encrypted_password = encrypt_password(cfg.password, public_key)
    if os.environ.get("PANEL_CLI_DEBUG"):
        print(f"[debug] encrypted password: {encrypted_password}", file=sys.stderr)

    body = {
        "name": cfg.username,
        "password": encrypted_password,
        "captcha": captcha,
        "captchaID": captcha_id,
        "language": cfg.language,
        "authSource": "local",
    }
    resp = login_request(client, "api/v2/core/auth/login", body, cfg.entrance)
    data = resp

    if data.mfa_status == "Enable":
        code = cfg.mfa_code
        if not code:
            if not sys.stdin.isatty():
                raise CliError(
                    "该面板已开启 MFA 动态验证码,请在 .env 中配置 PANEL_MFA_CODE 或通过 --mfa 参数提供"
                )
            code = prompt("该面板已开启 MFA,请输入动态验证码: ")
        mfa_body = {"sessionId": data.mfa_session, "code": code}
        login_request(client, "api/v2/core/auth/mfalogin", mfa_body, cfg.entrance)

    session = Session(
        profile=profile,
        panel_url=client.base_url,
        entrance=cfg.entrance,
        username=cfg.username,
        panel_name=panel_name,
        logged_in_at=now_string(),
        insecure=cfg.insecure,
        cookies=client.cookies(),
    )
    path = save_session(session)

    print(f"登录成功! 用户名: {session.username} 面板: {session.panel_name}")
    print(f"会话凭据已保存到: {path}")

    return session


def login_request(
    client: PanelClient, path: str, body: dict, entrance: str
) -> LoginData:
    extra: dict[str, str] = {}
    if entrance:
        extra["EntranceCode"] = base64.b64encode(entrance.encode("utf-8")).decode("ascii")
    resp = client.post_json_no_csrf(path, body, extra)
    try:
        payload = resp.json()
    except ValueError as exc:
        raise CliError(f"解析面板响应失败: {exc}") from exc
    code = payload.get("code", -1)
    if code != 200:
        raise CliError(f"登录失败 (code={code}): {payload.get('message', '')}")
    data = payload.get("data") or {}
    return LoginData(
        mfa_status=data.get("mfaStatus", "") or "",
        mfa_session=data.get("mfaSession", "") or "",
    )


def fetch_captcha(client: PanelClient) -> dict:
    resp = client.get("api/v2/core/auth/captcha")
    try:
        payload = resp.json()
    except ValueError as exc:
        raise CliError(f"解析验证码响应失败: {exc}") from exc
    if payload.get("code") != 200:
        raise CliError(
            f"获取验证码失败 (code={payload.get('code', -1)}): {payload.get('message', '')}"
        )
    if not payload.get("data"):
        raise CliError("验证码响应缺少数据")
    return payload["data"]


def captcha_id_of(cap: dict) -> str:
    """面板返回的字段是 `captchaID`(大写 ID);兼容 `captchaId` 拼写。"""
    value = cap.get("captchaID")
    if value is None:
        value = cap.get("captchaId")
    return value or ""


def handle_captcha(client: PanelClient, cfg: PanelConfig) -> tuple[str, str]:
    if cfg.captcha and cfg.captcha_id:
        return cfg.captcha, cfg.captcha_id
    cap = fetch_captcha(client)
    if cfg.captcha:
        return cfg.captcha, captcha_id_of(cap)
    if not sys.stdin.isatty():
        raise CliError(
            "面板要求输入验证码,但当前不是交互式终端。"
            "请通过 PANEL_CAPTCHA / PANEL_CAPTCHA_ID 环境变量或 --captcha / --captcha-id 参数提供"
        )
    image_path = cap.get("imagePath", "")
    if "," not in image_path:
        raise CliError("验证码图片格式异常")
    b64 = image_path.split(",", 2)[1]
    try:
        image = base64.b64decode(b64)
    except Exception as exc:  # noqa: BLE001
        raise CliError(f"解码验证码图片失败: {exc}") from exc
    import tempfile

    cap_id = captcha_id_of(cap)
    img_path = os.path.join(tempfile.gettempdir(), f"1panel-cli-captcha-{cap_id}.png")
    with open(img_path, "wb") as handle:
        handle.write(image)
    print(f"面板要求输入验证码。验证码图片已保存到: {img_path}")
    print("请打开图片,输入其中的算式结果(例如图片为 3+5=? 时输入 8)。")
    answer = prompt("验证码答案: ")
    if not answer:
        raise CliError("未输入验证码")
    return answer, cap_id
