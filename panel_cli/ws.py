"""极简 RFC6455 WebSocket 客户端(纯 socket/ssl 实现,面向面板终端)。

仅覆盖 1panel-cli 需要的子集:
- 客户端握手(自定义请求头:cookie / currentnode);
- 收发文本/二进制帧(客户端帧按规范掩码);
- 自动应答 Ping;解析 Close 帧并给出 code/reason;
- socket 超时用于实现 exec 的整体 deadline。

对应 Rust 版 exec.rs 中基于 tungstenite 的连接逻辑。
"""

from __future__ import annotations

import base64
import os
import socket
import ssl
import urllib.parse
from dataclasses import dataclass

from .errors import CliError

_OP_CONT = 0x0
_OP_TEXT = 0x1
_OP_BINARY = 0x2
_OP_CLOSE = 0x8
_OP_PING = 0x9
_OP_PONG = 0xA


class WsHandshakeError(CliError):
    """握手失败,携带 HTTP 状态码与响应体片段供上层归因。"""

    def __init__(self, status: int, body: bytes = b"") -> None:
        super().__init__(f"WS 握手失败: HTTP {status}")
        self.status = status
        self.body = body


class WsConnectionClosed(CliError):
    """连接已关闭(对端关闭或 EOF)。"""


@dataclass
class WSMessage:
    opcode: int  # _OP_TEXT / _OP_BINARY / _OP_CLOSE
    data: bytes
    close_code: int | None = None
    close_reason: str = ""

    @property
    def is_text(self) -> bool:
        return self.opcode == _OP_TEXT

    @property
    def is_binary(self) -> bool:
        return self.opcode == _OP_BINARY

    @property
    def is_close(self) -> bool:
        return self.opcode == _OP_CLOSE


def _recv_exact(sock: socket.socket, count: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < count:
        try:
            chunk = sock.recv(count - len(chunks))
        except (TimeoutError, socket.timeout):
            raise
        except ssl.SSLError as exc:
            if "timed out" in str(exc):
                raise TimeoutError(str(exc)) from exc
            raise
        except OSError as exc:
            raise WsConnectionClosed(f"连接被意外关闭: {exc}") from exc
        if not chunk:
            raise WsConnectionClosed("连接被意外关闭(对端关闭)")
        chunks += chunk
    return bytes(chunks)


class WebSocket:
    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock
        self._closed = False

    # ------------------------------------------------------------------

    @classmethod
    def connect(
        cls,
        url: str,
        headers: list[tuple[str, str]] | None = None,
        insecure: bool = False,
        connect_timeout: float = 15.0,
    ) -> "WebSocket":
        parsed = urllib.parse.urlsplit(url)
        scheme = parsed.scheme.lower()
        if scheme not in ("ws", "wss"):
            raise CliError(f"非法的 WS 地址: {url}")
        is_tls = scheme == "wss"
        host = parsed.hostname
        if not host:
            raise CliError(f"WS 地址缺少主机名: {url}")
        port = parsed.port or (443 if is_tls else 80)
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"

        try:
            raw = socket.create_connection((host, port), timeout=connect_timeout)
        except OSError as exc:
            raise CliError(f"连接面板失败: {host}:{port} ({exc})") from exc
        raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        if is_tls:
            if insecure:
                context = ssl.create_default_context()
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
            else:
                context = ssl.create_default_context()
            try:
                raw = context.wrap_socket(raw, server_hostname=host)
            except ssl.SSLError as exc:
                raw.close()
                raise CliError(
                    f"TLS 连接失败: {exc}(自签名证书可运行 login 时加 --insecure)"
                ) from exc

        ws_key = base64.b64encode(os.urandom(16)).decode("ascii")
        display_host = parsed.netloc
        lines = [
            f"GET {path} HTTP/1.1",
            f"Host: {display_host}",
            "Upgrade: websocket",
            "Connection: Upgrade",
            f"Sec-WebSocket-Key: {ws_key}",
            "Sec-WebSocket-Version: 13",
        ]
        for name, value in headers or []:
            lines.append(f"{name}: {value}")
        request = ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8")
        try:
            raw.sendall(request)
            response = cls._read_handshake_response(raw)
        except OSError as exc:
            raw.close()
            raise CliError(f"WS 握手失败: {exc}") from exc

        status = cls._parse_status(response)
        if status != 101:
            raw.close()
            _, _, body = response.partition(b"\r\n\r\n")
            raise WsHandshakeError(status, body[:512])
        return cls(raw)

    @staticmethod
    def _read_handshake_response(sock: socket.socket) -> bytes:
        buffer = bytearray()
        while b"\r\n\r\n" not in buffer:
            if len(buffer) > 64 * 1024:
                raise CliError("WS 握手响应异常")
            try:
                chunk = sock.recv(4096)
            except (TimeoutError, socket.timeout):
                raise
            except ssl.SSLError as exc:
                if "timed out" in str(exc):
                    raise TimeoutError(str(exc)) from exc
                raise
            if not chunk:
                break
            buffer += chunk
        return bytes(buffer)

    @staticmethod
    def _parse_status(response: bytes) -> int:
        try:
            head = response.split(b"\r\n", 1)[0].decode("latin-1")
            return int(head.split(" ", 2)[1])
        except (IndexError, ValueError):
            raise CliError("WS 握手响应格式异常")

    # ------------------------------------------------------------------

    def settimeout(self, seconds: float | None) -> None:
        self._sock.settimeout(seconds)

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        if self._closed:
            raise WsConnectionClosed("连接已关闭")
        mask = os.urandom(4)
        header = bytearray([0x80 | opcode])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < 1 << 16:
            header.append(0x80 | 126)
            header += length.to_bytes(2, "big")
        else:
            header.append(0x80 | 127)
            header += length.to_bytes(8, "big")
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        try:
            self._sock.sendall(bytes(header) + mask + masked)
        except OSError as exc:
            raise WsConnectionClosed(f"WebSocket 发送失败: {exc}") from exc

    def send_text(self, text: str) -> None:
        self._send_frame(_OP_TEXT, text.encode("utf-8"))

    def send_pong(self, payload: bytes) -> None:
        self._send_frame(_OP_PONG, payload)

    def _read_frame(self) -> WSMessage:
        head = _recv_exact(self._sock, 2)
        fin = bool(head[0] & 0x80)
        opcode = head[0] & 0x0F
        masked = bool(head[1] & 0x80)
        length = head[1] & 0x7F
        if length == 126:
            length = int.from_bytes(_recv_exact(self._sock, 2), "big")
        elif length == 127:
            length = int.from_bytes(_recv_exact(self._sock, 8), "big")
        if length > 64 * 1024 * 1024:
            raise CliError("WebSocket 帧过大")
        mask_key = _recv_exact(self._sock, 4) if masked else b""
        payload = _recv_exact(self._sock, length) if length else b""
        if masked and payload:
            payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))

        if opcode in (_OP_TEXT, _OP_BINARY):
            while not fin:
                # 处理分片:继续读取后续帧直到 FIN
                head2 = _recv_exact(self._sock, 2)
                fin = bool(head2[0] & 0x80)
                op2 = head2[0] & 0x0F
                if op2 != _OP_CONT:
                    raise CliError("WebSocket 分片格式异常")
                masked2 = bool(head2[1] & 0x80)
                length2 = head2[1] & 0x7F
                if length2 == 126:
                    length2 = int.from_bytes(_recv_exact(self._sock, 2), "big")
                elif length2 == 127:
                    length2 = int.from_bytes(_recv_exact(self._sock, 8), "big")
                mask2 = _recv_exact(self._sock, 4) if masked2 else b""
                part = _recv_exact(self._sock, length2) if length2 else b""
                if masked2 and part:
                    part = bytes(b ^ mask2[i % 4] for i, b in enumerate(part))
                payload += part
            return WSMessage(opcode, payload)

        if opcode == _OP_CLOSE:
            code: int | None = None
            reason = ""
            if len(payload) >= 2:
                code = int.from_bytes(payload[:2], "big")
                reason = payload[2:].decode("utf-8", errors="replace")
            elif len(payload) == 1:
                # 非规范帧(面板 agent 不带状态码),与 tungstenite 行为一致
                reason = "Protocol violation"
            return WSMessage(_OP_CLOSE, payload, code, reason)

        if opcode == _OP_PING:
            self.send_pong(payload)
            return self._read_frame()
        if opcode == _OP_PONG:
            return self._read_frame()
        # 未知控制帧/其他帧:跳过
        return self._read_frame()

    def recv(self) -> WSMessage:
        return self._read_frame()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._send_frame(_OP_CLOSE, (1000).to_bytes(2, "big"))
        except (CliError, OSError):
            pass
        try:
            self._sock.close()
        except OSError:
            pass
