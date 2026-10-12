"""响应脱敏:避免私钥、口令等敏感内容进入终端日志与智能体上下文。

默认对所有 API 响应开启双层脱敏:

- 按字段名屏蔽敏感值(privateKey / password / token / secret 等,递归嵌套结构);
- 按内容识别并屏蔽 PEM 私钥块(字段名未收录时也能兜底)。

确需查看原值时使用全局参数 ``--show-secrets`` 或环境变量
``PANEL_CLI_SHOW_SECRETS=1``(属显式知情操作,请谨慎)。
"""

from __future__ import annotations

import re

REDACTED = "***REDACTED***"
REDACTED_PEM = "***REDACTED PRIVATE KEY***"

_show_secrets = False

# 字段名归一化(小写、去掉空格与分隔符)后包含任一关键字,即视为敏感
_SENSITIVE_STEMS = (
    "password",
    "passwd",
    "passphrase",
    "privatekey",
    "secret",
    "token",
    "apikey",
    "accesskey",
    "hmackey",
    "credential",
    "authorization",
    "密码",
    "私钥",
    "密钥",
)

# 形似敏感字段但并非密钥内容(布尔开关、计数、路径),避免误伤
_NON_SECRET_KEYS = {
    "haspassword",
    "passwordauthentication",
    "rememberpassword",
    "rememberapikey",
    "allowcredentials",
    "proxypasswdkeep",
    "apikeyvaliditytime",
}

# PEM 私钥块(RSA / EC / DSA / OPENSSH / ENCRYPTED / PGP 等)
_PEM_PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----"
    r".*?"
    r"-----END [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----",
    re.DOTALL,
)


def configure(show_secrets: bool) -> None:
    """设置是否输出敏感内容(默认 False,即脱敏)。"""
    global _show_secrets
    _show_secrets = bool(show_secrets)


def enabled() -> bool:
    """当前是否处于脱敏状态。"""
    return not _show_secrets


def normalize_key(key: str) -> str:
    """归一化字段名:小写并去掉空格、下划线、连字符、点号。"""
    return re.sub(r"[\s_\-.]+", "", key.lower())


def is_sensitive_key(key: str) -> bool:
    """字段名是否敏感(需屏蔽其值)。"""
    if not isinstance(key, str):
        return False
    normalized = normalize_key(key)
    if not normalized:
        return False
    if normalized in _NON_SECRET_KEYS:
        return False
    if normalized.endswith("path") or normalized.endswith("tokens"):
        return False
    return any(stem in normalized for stem in _SENSITIVE_STEMS)


def redact_text(text: str) -> str:
    """屏蔽文本中的 PEM 私钥块(内容兜底,不依赖字段名)。"""
    if _show_secrets or "PRIVATE KEY" not in text:
        return text
    return _PEM_PRIVATE_KEY.sub(REDACTED_PEM, text)


def redact(value):
    """递归脱敏:命中敏感字段名的值整体替换,其余字符串做私钥内容兜底。"""
    if _show_secrets:
        return value
    return _walk(value)


def _walk(value):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if isinstance(key, str) and is_sensitive_key(key) and item not in (None, ""):
                result[key] = REDACTED
            else:
                result[key] = _walk(item)
        return result
    if isinstance(value, list):
        return [_walk(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value
