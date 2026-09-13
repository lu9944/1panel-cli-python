"""auth 模块测试:验证码 ID 解析与处理。"""

import unittest

from panel_cli import auth
from panel_cli.config import ConfigOverrides, PanelConfig
from panel_cli.crypto import encrypt_password  # noqa: F401  (确保模块可导入)


class FakeClient:
    def __init__(self, captcha_payload):
        self.captcha_payload = captcha_payload

    def get(self, path):
        return _Resp({"code": 200, "data": self.captcha_payload})


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _cfg(captcha=None, captcha_id=None):
    return PanelConfig(
        url="http://127.0.0.1:1",
        entrance="",
        username="admin",
        password="x",
        mfa_code=None,
        captcha=captcha,
        captcha_id=captcha_id,
        language="zh",
        insecure=False,
    )


class TestCaptchaId(unittest.TestCase):
    def test_reads_uppercase_id(self):
        # v2.2.5 的 CaptchaResponse JSON 字段是 captchaID
        self.assertEqual(auth.captcha_id_of({"captchaID": "abc"}), "abc")

    def test_fallback_lowercase_id(self):
        self.assertEqual(auth.captcha_id_of({"captchaId": "abc"}), "abc")

    def test_missing(self):
        self.assertEqual(auth.captcha_id_of({}), "")


class TestHandleCaptcha(unittest.TestCase):
    def test_explicit_answer_and_id(self):
        cfg = _cfg(captcha="8", captcha_id="pre-id")
        result = auth.handle_captcha(FakeClient({}), cfg)
        self.assertEqual(result, ("8", "pre-id"))

    def test_answer_only_uses_fetched_id(self):
        cfg = _cfg(captcha="8")
        client = FakeClient(
            {"captchaID": "fetched-id", "imagePath": "data:image/png;base64,AAAA"}
        )
        answer, cap_id = auth.handle_captcha(client, cfg)
        self.assertEqual(answer, "8")
        self.assertEqual(cap_id, "fetched-id")


if __name__ == "__main__":
    unittest.main()
