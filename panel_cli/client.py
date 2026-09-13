"""面板 HTTP 客户端(纯标准库 urllib + http.cookiejar)。

对应 Rust 版 client.rs:
- 自动维护 Cookie(登录会话 / panel_public_key / pcsrftoken);
- 默认携带 Accept-Language 与 CurrentNode 请求头;
- 写请求自动附加 X-CSRF-Token(来自 pcsrftoken cookie);
- multipart 文件上传(files/upload)。
"""

from __future__ import annotations

import base64
import http.cookiejar
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import ssl
from pathlib import Path

from .config import ENV_LANGUAGE, ENV_NODE, encode_current_node
from .crypto import url_decode
from .errors import CliError
from .session import CookieData

USER_AGENT = "1panel-cli"
DEFAULT_TIMEOUT = 30.0


class Response:
    """轻量响应封装:统一非 2xx(urllib 抛 HTTPError)与正常响应。"""

    def __init__(self, status: int, body: bytes, headers: list[tuple[str, str]]):
        self.status = status
        self._body = body
        self.headers = headers

    def text(self) -> str:
        return self._body.decode("utf-8", errors="replace")

    def json(self) -> dict:
        return json.loads(self._body.decode("utf-8", errors="replace"))

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


def _build_ssl_context(insecure: bool) -> ssl.SSLContext:
    if insecure:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context
    return ssl.create_default_context()


class PanelClient:
    def __init__(
        self,
        base_url: str,
        cookies: list[CookieData] | None = None,
        insecure: bool = False,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        parsed = urllib.parse.urlsplit(self.base_url)
        if not parsed.scheme or not parsed.netloc:
            raise CliError(f"非法的面板地址 {base_url}")
        self.host = parsed.hostname or ""
        self.timeout = timeout
        self.jar = http.cookiejar.CookieJar()
        for cookie in cookies or []:
            self._add_cookie(cookie.name, cookie.value)
        self.insecure = insecure
        context = _build_ssl_context(insecure)
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar),
            urllib.request.HTTPSHandler(context=context),
        )

    # ------------------------------------------------------------------
    # Cookie 管理
    # ------------------------------------------------------------------

    def _add_cookie(self, name: str, value: str) -> None:
        self.jar.set_cookie(
            http.cookiejar.Cookie(
                version=0,
                name=name,
                value=value,
                port=None,
                port_specified=False,
                domain=self.host,
                domain_specified=False,
                domain_initial_dot=False,
                path="/",
                path_specified=True,
                secure=False,
                expires=None,
                discard=True,
                comment=None,
                comment_url=None,
                rest={},
            )
        )

    def cookies(self) -> list[CookieData]:
        request = urllib.request.Request(self.url("/"))
        self.jar.add_cookie_header(request)
        header = request.get_header("Cookie") or ""
        result: list[CookieData] = []
        for pair in header.split(";"):
            pair = pair.strip()
            if not pair or "=" not in pair:
                continue
            name, _, value = pair.partition("=")
            result.append(CookieData(name.strip(), value.strip()))
        return result

    def cookie(self, name: str) -> str | None:
        for cookie in self.cookies():
            if cookie.name == name:
                return cookie.value
        return None

    def public_key_pem(self) -> str:
        """panel_public_key cookie 的值是 URL 编码的 base64 PEM 块。"""
        raw = self.cookie("panel_public_key")
        if not raw:
            raise CliError("未能从面板获取 RSA 公钥 (panel_public_key cookie)")
        decoded = url_decode(raw)
        try:
            return base64.b64decode(decoded).decode("utf-8")
        except Exception as exc:  # noqa: BLE001
            raise CliError(f"解码面板公钥失败: {exc}") from exc

    # ------------------------------------------------------------------
    # 请求发送
    # ------------------------------------------------------------------

    def url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def _default_headers(self) -> dict[str, str]:
        headers = {
            "User-Agent": USER_AGENT,
            "Accept-Language": os.environ.get(ENV_LANGUAGE) or "zh",
        }
        node = (os.environ.get(ENV_NODE) or "local").strip()
        headers["CurrentNode"] = encode_current_node(node)
        return headers

    def _send(self, method: str, path: str, *, body: bytes | None = None,
              content_type: str | None = None, extra: dict[str, str] | None = None,
              with_csrf: bool = False) -> Response:
        headers = self._default_headers()
        if content_type:
            headers["Content-Type"] = content_type
        if with_csrf:
            csrf = self.cookie("pcsrftoken")
            if csrf:
                headers["X-CSRF-Token"] = csrf
        if extra:
            headers.update(extra)
        request = urllib.request.Request(
            self.url(path), data=body, headers=headers, method=method
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as resp:
                return Response(resp.status, resp.read(), list(resp.getheaders()))
        except urllib.error.HTTPError as exc:
            try:
                payload = exc.read()
            except Exception:  # noqa: BLE001
                payload = b""
            return Response(exc.code, payload, list(exc.headers.items() if exc.headers else []))
        except urllib.error.URLError as exc:
            raise CliError(f"请求面板失败: {exc.reason}") from exc
        except OSError as exc:
            raise CliError(f"请求面板失败: {exc}") from exc

    def get(self, path: str) -> Response:
        return self._send("GET", path)

    def post_json(self, path: str, body) -> Response:
        return self._send(
            "POST", path, body=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            content_type="application/json", with_csrf=True,
        )

    def post_json_no_csrf(self, path: str, body, extra: dict[str, str] | None = None) -> Response:
        return self._send(
            "POST", path, body=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            content_type="application/json", extra=extra,
        )

    def post(self, path: str) -> Response:
        return self._send("POST", path, with_csrf=True)

    def put_json(self, path: str, body) -> Response:
        return self._send(
            "PUT", path, body=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            content_type="application/json", with_csrf=True,
        )

    def delete(self, path: str) -> Response:
        return self._send("DELETE", path, with_csrf=True)

    def upload_file(self, dest_dir: str, file_path: Path, overwrite: bool) -> Response:
        """multipart 上传文件到面板指定目录(files/upload)。"""
        try:
            content = file_path.read_bytes()
        except OSError as exc:
            raise CliError(f"读取本地文件失败: {exc}") from exc
        boundary = f"----1panel-cli{os.urandom(8).hex()}"
        parts: list[bytes] = []

        def field(name: str, value: str) -> bytes:
            return (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                f"{value}\r\n"
            ).encode("utf-8")

        parts.append(field("path", dest_dir))
        parts.append(field("overwrite", "true" if overwrite else "false"))
        filename = file_path.name
        parts.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                "Content-Type: application/octet-stream\r\n\r\n"
            ).encode("utf-8")
        )
        parts.append(content)
        parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
        payload = b"".join(parts)
        return self._send(
            "POST",
            "api/v2/files/upload",
            body=payload,
            content_type=f"multipart/form-data; boundary={boundary}",
            with_csrf=True,
        )
