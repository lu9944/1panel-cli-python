"""crypto 模块测试:URL 解码、AES-256-CBC 已知向量、RSA PKCS1v15 往返。"""

import base64
import math
import os
import unittest

from panel_cli.crypto import (
    aes_256_cbc_encrypt,
    aes_cbc_decrypt,
    encrypt_password,
    rsa_pkcs1v15_encrypt,
    string_decrypt,
    url_decode,
)


class TestUrlDecode(unittest.TestCase):
    def test_keeps_plus(self):
        self.assertEqual(url_decode("a+b%20c%3D"), "a+b c=")

    def test_utf8(self):
        self.assertEqual(url_decode("%E4%B8%AD%E6%96%87"), "中文")

    def test_invalid_escape(self):
        from panel_cli.errors import CliError

        with self.assertRaises(CliError):
            url_decode("%zz")


class TestAesCbc(unittest.TestCase):
    def test_nist_sp800_38a_vector(self):
        # NIST SP 800-38A AES-256-CBC 第一组测试向量
        key = bytes.fromhex(
            "603deb1015ca71be2b73aef0857d7781"
            "1f352c073b6108d72d9810a30914dff4"
        )
        iv = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
        plaintext = bytes.fromhex("6bc1bee22e409f96e93d7e117393172a")
        first_block_expected = bytes.fromhex("f58c4c04d6e5f1ba779eabfb5f7bfbd6")
        ciphertext = aes_256_cbc_encrypt(key, iv, plaintext)
        # PKCS7 填充后为 2 个块,首块应与标准向量一致
        self.assertEqual(len(ciphertext), 32)
        self.assertEqual(ciphertext[:16], first_block_expected)

    def test_empty_input(self):
        key = os.urandom(32)
        iv = os.urandom(16)
        ciphertext = aes_256_cbc_encrypt(key, iv, b"")
        self.assertEqual(len(ciphertext), 16)  # 仅 PKCS7 填充块


# 由 openssl enc -aes-{128,192,256}-cbc 独立生成的向量(base64(iv || ciphertext))
_OPENSSL_VECTORS = [
    (b"0123456789abcdef", "YWJjZGVmMDEyMzQ1Njc4OWUZxtPxZLPOgF0LMB2wyaQ="),
    (b"0123456789abcdef01234567", "YWJjZGVmMDEyMzQ1Njc4OW+gHjXkWpAxM9ayVXcoGow="),
    (b"0123456789abcdef0123456789abcdef", "YWJjZGVmMDEyMzQ1Njc4OX7PgJ6gSNkUW16oM1ehlls="),
]
_OPENSSL_PLAINTEXT = b"1Panel@2026!"


class TestAesDecrypt(unittest.TestCase):
    def test_openssl_vectors(self):
        for key, encoded in _OPENSSL_VECTORS:
            data = base64.b64decode(encoded)
            iv, ciphertext = data[:16], data[16:]
            self.assertEqual(
                aes_cbc_decrypt(key, iv, ciphertext), _OPENSSL_PLAINTEXT, key
            )

    def test_roundtrip(self):
        for key_len in (16, 24, 32):
            key = os.urandom(key_len)
            iv = os.urandom(16)
            message = os.urandom(100)
            from panel_cli.crypto import aes_cbc_encrypt

            self.assertEqual(
                aes_cbc_decrypt(key, iv, aes_cbc_encrypt(key, iv, message)), message
            )

    def test_bad_padding_raises(self):
        from panel_cli.errors import CliError

        key = os.urandom(16)
        with self.assertRaises(CliError):
            aes_cbc_decrypt(key, os.urandom(16), os.urandom(16))


class TestStringDecrypt(unittest.TestCase):
    def test_openssl_vectors(self):
        for key, encoded in _OPENSSL_VECTORS:
            self.assertEqual(
                string_decrypt(encoded, key.decode("ascii")), _OPENSSL_PLAINTEXT.decode()
            )

    def test_empty(self):
        self.assertEqual(string_decrypt("", "key"), "")


# ---------------------------------------------------------------------------
# RSA 测试辅助:小素数生成 + SPKI PEM 构造
# ---------------------------------------------------------------------------


def _is_probable_prime(n: int, rounds: int = 16) -> bool:
    if n < 2:
        return False
    for p in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if n % p == 0:
            return n == p
        d = n - 1
        r = 0
        while d % 2 == 0:
            d //= 2
            r += 1
        x = pow(p, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def _gen_prime(bits: int) -> int:
    while True:
        candidate = int.from_bytes(os.urandom(bits // 8), "big") | (1 << (bits - 1)) | 1
        if _is_probable_prime(candidate):
            return candidate


def _der_len(length: int) -> bytes:
    if length < 0x80:
        return bytes([length])
    body = length.to_bytes((length.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(body)]) + body


def _der_int(value: int) -> bytes:
    body = value.to_bytes(max(1, (value.bit_length() + 7) // 8), "big")
    if body[0] & 0x80:
        body = b"\x00" + body
    return b"\x02" + _der_len(len(body)) + body


def _der(tag: int, content: bytes) -> bytes:
    return bytes([tag]) + _der_len(len(content)) + content


def _make_spki_pem(n: int, e: int) -> str:
    key_der = _der_int(n) + _der_int(e)
    key_seq = _der(0x30, key_der)
    alg = bytes.fromhex("300d06092a864886f70d0101010500")  # rsaEncryption + NULL
    bits = b"\x00" + key_seq
    spki = _der(0x30, alg + _der(0x03, bits))
    b64 = base64.encodebytes(spki).decode("ascii").replace("\n", "")
    lines = [b64[i : i + 64] for i in range(0, len(b64), 64)]
    return "-----BEGIN PUBLIC KEY-----\n" + "\n".join(lines) + "\n-----END PUBLIC KEY-----\n"


class TestRsa(unittest.TestCase):
    def test_pkcs1v15_roundtrip(self):
        # 生成 128 位素数对,n 约 256 位(k=32 字节)
        e = 65537
        while True:
            p, q = _gen_prime(128), _gen_prime(128)
            if p == q:
                continue
            n = p * q
            phi = (p - 1) * (q - 1)
            if math.gcd(e, phi) == 1:
                break
        d = pow(e, -1, phi)
        k = (n.bit_length() + 7) // 8
        pem = _make_spki_pem(n, e)

        message = b"hello world"
        cipher = rsa_pkcs1v15_encrypt(pem, message)
        self.assertEqual(len(cipher), k)
        em = pow(int.from_bytes(cipher, "big"), d, n).to_bytes(k, "big")
        self.assertEqual(em[0], 0)
        self.assertEqual(em[1], 2)
        self.assertIn(b"\x00", em[2:])
        sep = em.index(b"\x00", 2)
        self.assertGreaterEqual(sep, 10)  # PS 至少 8 字节
        self.assertEqual(em[sep + 1 :], message)

    def test_encrypt_password_shape(self):
        # 512 位素数对 → k=128 字节,可容纳 32 字节 AES key hex
        e = 65537
        while True:
            p, q = _gen_prime(512), _gen_prime(512)
            n = p * q
            phi = (p - 1) * (q - 1)
            if math.gcd(e, phi) == 1:
                break
        pem = _make_spki_pem(n, e)
        result = encrypt_password("s3cret", pem)
        parts = result.split(":")
        self.assertEqual(len(parts), 3)
        key_cipher = base64.b64decode(parts[0])
        iv = base64.b64decode(parts[1])
        cipher = base64.b64decode(parts[2])
        self.assertEqual(len(key_cipher), (n.bit_length() + 7) // 8)
        self.assertEqual(len(iv), 16)
        # 密码 "s3cret"(6 字节)+ PKCS7 → 一个 AES 块
        self.assertEqual(len(cipher), 16)


if __name__ == "__main__":
    unittest.main()
