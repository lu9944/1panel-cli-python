"""面板 CLI 错误类型。

错误码统一为字符串消息;上层捕获 CliError 后打印到 stderr 并以退出码 1 退出。
"""

from __future__ import annotations


class CliError(Exception):
    """用户可见的错误(对应 Rust 版 anyhow::Error)。"""
