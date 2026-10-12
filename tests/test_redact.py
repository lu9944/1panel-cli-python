"""redact 模块测试:敏感字段按名脱敏、PEM 私钥按内容兜底、开关控制。"""

import unittest

from panel_cli import redact

PEM_KEY = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "MIIEowIBAAKCAQEApStCo2DeeeWg2E6zlVPpvqZLxqDFCZsL20iXmRAihQsK/aGL\n"
    "-----END RSA PRIVATE KEY-----"
)
PEM_CERT = (
    "-----BEGIN CERTIFICATE-----\n"
    "MIIGKzCCBROgAwIBAgIQCn8V4abEJ3T6SQbkxKHabDANBgkqhkiG9w0BAQsFADBg\n"
    "-----END CERTIFICATE-----"
)


class RedactTestCase(unittest.TestCase):
    def setUp(self):
        redact.configure(False)
        self.addCleanup(redact.configure, False)


class TestSensitiveKeys(RedactTestCase):
    def test_private_key_masked_in_nested_response(self):
        payload = {
            "code": 200,
            "data": {
                "items": [
                    {
                        "primaryDomain": "*.example.com",
                        "privateKey": PEM_KEY,
                        "expireDate": "2027-01-05",
                    }
                ]
            },
        }
        out = redact.redact(payload)
        item = out["data"]["items"][0]
        self.assertEqual(redact.REDACTED, item["privateKey"])
        self.assertEqual("*.example.com", item["primaryDomain"])
        self.assertEqual("2027-01-05", item["expireDate"])

    def test_common_secret_fields_masked(self):
        payload = {
            "password": "p1",
            "dbPassword": "p2",
            "PANEL_DB_ROOT_PASSWORD": "p3",
            "client_secret": "s1",
            "eabHmacKey": "s2",
            "botToken": "t1",
            "accessKey": "k1",
            "authorization": "Bearer x",
            "credential": {"user": "u", "pass": "p"},
            "passPhrase": "pp",
            "密码": "p4",
            "私钥": "p5",
        }
        out = redact.redact(payload)
        for key in payload:
            self.assertEqual(redact.REDACTED, out[key], key)

    def test_non_secret_lookalikes_kept(self):
        payload = {
            "completionTokens": 128,
            "promptTokens": 32,
            "totalTokens": 160,
            "privateKeyPath": "/opt/1panel/ssl/key.pem",
            "hasPassword": True,
            "passwordAuthentication": True,
            "rememberPassword": True,
            "rememberApiKey": True,
            "allowCredentials": True,
            "proxyPasswdKeep": True,
            "apiKeyValidityTime": 3600,
            "keyType": "RSA2048",
            "pem": PEM_CERT,
        }
        self.assertEqual(payload, redact.redact(payload))

    def test_empty_and_null_values_kept(self):
        payload = {"privateKey": "", "password": None}
        self.assertEqual(payload, redact.redact(payload))

    def test_non_string_key_kept(self):
        payload = {1: "one", "privateKey": PEM_KEY}
        out = redact.redact(payload)
        self.assertEqual("one", out[1])
        self.assertEqual(redact.REDACTED, out["privateKey"])


class TestPemContentFallback(RedactTestCase):
    def test_masks_key_under_unexpected_field_name(self):
        out = redact.redact({"data": "内容:\n" + PEM_KEY + "\n结束"})
        self.assertNotIn("BEGIN RSA PRIVATE KEY", out["data"])
        self.assertIn(redact.REDACTED_PEM, out["data"])

    def test_redact_text_masks_key_but_keeps_certificate(self):
        out = redact.redact_text(PEM_KEY + "\n" + PEM_CERT)
        self.assertNotIn("BEGIN RSA PRIVATE KEY", out)
        self.assertEqual(1, out.count(redact.REDACTED_PEM))
        self.assertIn("BEGIN CERTIFICATE", out)

    def test_openssh_and_pgp_blocks(self):
        text = (
            "-----BEGIN OPENSSH PRIVATE KEY-----\n"
            "b3BlbnNzaC1rZXk=\n"
            "-----END OPENSSH PRIVATE KEY-----\n"
            "-----BEGIN PGP PRIVATE KEY BLOCK-----\n"
            "xxx\n"
            "-----END PGP PRIVATE KEY BLOCK-----"
        )
        out = redact.redact_text(text)
        self.assertNotIn("OPENSSH PRIVATE KEY", out)
        self.assertNotIn("PGP PRIVATE KEY", out)
        self.assertEqual(2, out.count(redact.REDACTED_PEM))


class TestShowSecrets(RedactTestCase):
    def test_show_secrets_returns_raw(self):
        payload = {"privateKey": PEM_KEY, "password": "p"}
        redact.configure(True)
        self.assertFalse(redact.enabled())
        self.assertIs(payload, redact.redact(payload))
        self.assertEqual(PEM_KEY, redact.redact_text(PEM_KEY))
        self.assertEqual("secret", redact.redact({"password": "secret"})["password"])


if __name__ == "__main__":
    unittest.main()
