"""ws 模块测试:帧编解码与掩码(基于内存假 socket)。"""

import base64
import os
import struct
import unittest

from panel_cli import ws
from panel_cli.ws import WebSocket


class FakeSocket:
    """模拟对端:recv 读取预置字节流,sendall 收集输出。"""

    def __init__(self, incoming: bytes = b"") -> None:
        self.incoming = bytearray(incoming)
        self.sent = bytearray()
        self.closed = False

    def recv(self, count: int) -> bytes:
        if not self.incoming:
            return b""
        chunk = bytes(self.incoming[:count])
        del self.incoming[:count]
        return chunk

    def sendall(self, data: bytes) -> None:
        self.sent += data

    def close(self) -> None:
        self.closed = True

    def settimeout(self, seconds) -> None:
        pass


def unmask_client_frame(frame: bytes):
    """解析客户端发送的(掩码)帧,返回 (opcode, payload)。"""
    opcode = frame[0] & 0x0F
    masked = bool(frame[1] & 0x80)
    length = frame[1] & 0x7F
    offset = 2
    if length == 126:
        length = struct.unpack(">H", frame[2:4])[0]
        offset = 4
    elif length == 127:
        length = struct.unpack(">Q", frame[2:10])[0]
        offset = 10
    self_checked = mask = frame[offset : offset + 4]
    offset += 4
    payload = frame[offset : offset + length]
    if masked:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return opcode, payload, self_checked is not None


def server_frame(opcode: int, payload: bytes = b"", fin: bool = True) -> bytes:
    header = bytearray([(0x80 if fin else 0) | opcode])
    if len(payload) < 126:
        header.append(len(payload))
    elif len(payload) < 1 << 16:
        header.append(126)
        header += struct.pack(">H", len(payload))
    else:
        header.append(127)
        header += struct.pack(">Q", len(payload))
    return bytes(header) + payload


class TestSendFrame(unittest.TestCase):
    def test_text_frame_is_masked(self):
        sock = FakeSocket()
        client = WebSocket.__new__(WebSocket)
        client._sock = sock
        client._closed = False
        client.send_text("hello")
        opcode, payload, _ = unmask_client_frame(bytes(sock.sent))
        self.assertEqual(opcode, 0x1)
        self.assertEqual(payload, b"hello")
        # 客户端帧必须带掩码位
        self.assertTrue(sock.sent[1] & 0x80)

    def test_large_payload_extended_length(self):
        sock = FakeSocket()
        client = WebSocket.__new__(WebSocket)
        client._sock = sock
        client._closed = False
        payload = os.urandom(300)
        client._send_frame(0x2, payload)
        # 126 ≤ 300 < 65536 → 16 位扩展长度
        self.assertEqual(sock.sent[1] & 0x7F, 126)
        opcode, decoded, _ = unmask_client_frame(bytes(sock.sent))
        self.assertEqual(opcode, 0x2)
        self.assertEqual(decoded, payload)


class TestRecvFrame(unittest.TestCase):
    def _make(self, incoming: bytes) -> WebSocket:
        client = WebSocket.__new__(WebSocket)
        client._sock = FakeSocket(incoming)
        client._closed = False
        return client

    def test_text_frame(self):
        client = self._make(server_frame(0x1, "你好".encode("utf-8")))
        message = client.recv()
        self.assertTrue(message.is_text)
        self.assertEqual(message.data, "你好".encode("utf-8"))

    def test_binary_frame(self):
        client = self._make(server_frame(0x2, b"\x00\x01"))
        message = client.recv()
        self.assertTrue(message.is_binary)
        self.assertEqual(message.data, b"\x00\x01")

    def test_ping_auto_pong(self):
        client = self._make(server_frame(0x9, b"abc") + server_frame(0x1, b"ok"))
        message = client.recv()
        self.assertTrue(message.is_text)
        self.assertEqual(message.data, b"ok")
        # 自动应答了 Pong
        self.assertTrue(len(client._sock.sent) >= 2)
        self.assertEqual(client._sock.sent[0] & 0x0F, 0xA)

    def test_close_with_reason(self):
        payload = struct.pack(">H", 1000) + "bye".encode("utf-8")
        client = self._make(server_frame(0x8, payload))
        message = client.recv()
        self.assertTrue(message.is_close)
        self.assertEqual(message.close_code, 1000)
        self.assertEqual(message.close_reason, "bye")

    def test_close_empty_is_none(self):
        client = self._make(server_frame(0x8))
        message = client.recv()
        self.assertTrue(message.is_close)
        self.assertIsNone(message.close_code)
        self.assertEqual(message.close_reason, "")

    def test_close_single_byte_protocol_violation(self):
        client = self._make(server_frame(0x8, b"x"))
        message = client.recv()
        self.assertTrue(message.is_close)
        self.assertEqual(message.close_reason, "Protocol violation")

    def test_fragmented_text(self):
        incoming = server_frame(0x1, b"he", fin=False) + server_frame(0x0, b"llo")
        client = self._make(incoming)
        message = client.recv()
        self.assertTrue(message.is_text)
        self.assertEqual(message.data, b"hello")


class TestHandshakeParse(unittest.TestCase):
    def test_parse_status(self):
        response = b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n\r\n"
        self.assertEqual(WebSocket._parse_status(response), 101)
        response401 = b"HTTP/1.1 401 Unauthorized\r\n\r\n"
        self.assertEqual(WebSocket._parse_status(response401), 401)


if __name__ == "__main__":
    unittest.main()
