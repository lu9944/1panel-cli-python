"""登录密码加密(与 1Panel 前端逻辑一致)+ 面板本地密钥解密 + URL 解码工具。

纯标准库实现:
- AES-128/192/256-CBC(PKCS7)加密与解密(FIPS-197);
- RSA PKCS#1 v1.5 公钥加密(解析 SPKI/PKCS#1 PEM,提取 n、e 后模幂);
- 面板 DB 密钥解密(init 命令读取/解密面板管理员密码,对应 Go `encrypt.StringDecrypt`);
- 保留字面量 `+` 的 URL 解码。

对应 Rust 版 crypto.rs(密码加密逻辑与输出格式一致)。
"""

from __future__ import annotations

import base64
import os

from .errors import CliError

# ---------------------------------------------------------------------------
# AES 加解密(FIPS-197 标准 S-box 实现,支持 128/192/256 位密钥)
# ---------------------------------------------------------------------------

_SBOX = bytes.fromhex(
    "637c777bf26b6fc53001672bfed7ab76"
    "ca82c97dfa5947f0add4a2af9ca472c0"
    "b7fd9326363ff7cc34a5e5f171d83115"
    "04c723c31896059a071280e2eb27b275"
    "09832c1a1b6e5aa0523bd6b329e32f84"
    "53d100ed20fcb15b6acbbe394a4c58cf"
    "d0efaafb434d338545f9027f503c9fa8"
    "51a3408f929d38f5bcb6da2110fff3d2"
    "cd0c13ec5f974417c4a77e3d645d1973"
    "60814fdc222a908846eeb814de5e0bdb"
    "e0323a0a4906245cc2d3ac629195e479"
    "e7c8376d8dd54ea96c56f4ea657aae08"
    "ba78252e1ca6b4c6e8dd741f4bbd8b8a"
    "703eb5664803f60e613557b986c11d9e"
    "e1f8981169d98e949b1e87e9ce5528df"
    "8ca1890dbfe6426841992d0fb054bb16"
)

_INV_SBOX = [0] * 256
for _i, _v in enumerate(_SBOX):
    _INV_SBOX[_v] = _i
_INV_SBOX = bytes(_INV_SBOX)

_RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36]


def _xtime(a: int) -> int:
    a <<= 1
    if a & 0x100:
        a = (a ^ 0x1B) & 0xFF
    return a


def _mul(a: int, b: int) -> int:
    result = 0
    while b:
        if b & 1:
            result ^= a
        a = _xtime(a)
        b >>= 1
    return result


def _expand_key(key: bytes) -> tuple[list[list[int]], int]:
    """密钥扩展,支持 16/24/32 字节密钥,返回 (words, round 数)。"""
    if len(key) not in (16, 24, 32):
        raise CliError("AES 密钥长度必须为 16 / 24 / 32 字节")
    nk = len(key) // 4
    rounds = nk + 6
    total = 4 * (rounds + 1)
    words = [list(key[i * 4 : i * 4 + 4]) for i in range(nk)]
    for i in range(nk, total):
        temp = list(words[i - 1])
        if i % nk == 0:
            temp = temp[1:] + temp[:1]
            temp = [_SBOX[b] for b in temp]
            temp[0] ^= _RCON[i // nk - 1]
        elif nk > 6 and i % nk == 4:
            temp = [_SBOX[b] for b in temp]
        words.append([words[i - nk][j] ^ temp[j] for j in range(4)])
    return words, rounds


def _add_round_key(state: list[list[int]], round_keys: list[list[int]], rnd: int) -> None:
    for col in range(4):
        for row in range(4):
            state[row][col] ^= round_keys[rnd * 4 + col][row]


def _encrypt_block(block: bytes, round_keys: list[list[int]], rounds: int) -> bytes:
    state = [[block[row + 4 * col] for col in range(4)] for row in range(4)]

    _add_round_key(state, round_keys, 0)
    for rnd in range(1, rounds + 1):
        # SubBytes
        for col in range(4):
            for row in range(4):
                state[row][col] = _SBOX[state[row][col]]
        # ShiftRows
        for row in range(1, 4):
            state[row] = state[row][row:] + state[row][:row]
        # MixColumns(最后一轮除外)
        if rnd != rounds:
            for col in range(4):
                a0, a1, a2, a3 = state[0][col], state[1][col], state[2][col], state[3][col]
                state[0][col] = _mul(a0, 2) ^ _mul(a1, 3) ^ a2 ^ a3
                state[1][col] = a0 ^ _mul(a1, 2) ^ _mul(a2, 3) ^ a3
                state[2][col] = a0 ^ a1 ^ _mul(a2, 2) ^ _mul(a3, 3)
                state[3][col] = _mul(a0, 3) ^ a1 ^ a2 ^ _mul(a3, 2)
        _add_round_key(state, round_keys, rnd)

    return _state_to_bytes(state)


def _decrypt_block(block: bytes, round_keys: list[list[int]], rounds: int) -> bytes:
    state = [[block[row + 4 * col] for col in range(4)] for row in range(4)]

    _add_round_key(state, round_keys, rounds)
    for rnd in range(rounds - 1, 0, -1):
        # InvShiftRows
        for row in range(1, 4):
            state[row] = state[row][-row:] + state[row][:-row]
        # InvSubBytes
        for col in range(4):
            for row in range(4):
                state[row][col] = _INV_SBOX[state[row][col]]
        _add_round_key(state, round_keys, rnd)
        # InvMixColumns
        for col in range(4):
            a0, a1, a2, a3 = state[0][col], state[1][col], state[2][col], state[3][col]
            state[0][col] = _mul(a0, 14) ^ _mul(a1, 11) ^ _mul(a2, 13) ^ _mul(a3, 9)
            state[1][col] = _mul(a0, 9) ^ _mul(a1, 14) ^ _mul(a2, 11) ^ _mul(a3, 13)
            state[2][col] = _mul(a0, 13) ^ _mul(a1, 9) ^ _mul(a2, 14) ^ _mul(a3, 11)
            state[3][col] = _mul(a0, 11) ^ _mul(a1, 13) ^ _mul(a2, 9) ^ _mul(a3, 14)
    # 最后一轮
    for row in range(1, 4):
        state[row] = state[row][-row:] + state[row][:-row]
    for col in range(4):
        for row in range(4):
            state[row][col] = _INV_SBOX[state[row][col]]
    _add_round_key(state, round_keys, 0)

    return _state_to_bytes(state)


def _state_to_bytes(state: list[list[int]]) -> bytes:
    out = bytearray(16)
    for col in range(4):
        for row in range(4):
            out[col * 4 + row] = state[row][col]
    return bytes(out)


def aes_cbc_encrypt(key: bytes, iv: bytes, plaintext: bytes) -> bytes:
    """AES-CBC + PKCS7 加密(密钥 16/24/32 字节)。"""
    if len(iv) != 16:
        raise CliError("AES IV 必须为 16 字节")
    round_keys, rounds = _expand_key(key)
    pad_len = 16 - (len(plaintext) % 16)
    padded = plaintext + bytes([pad_len]) * pad_len
    previous = iv
    out = bytearray()
    for offset in range(0, len(padded), 16):
        block = bytes(a ^ b for a, b in zip(padded[offset : offset + 16], previous))
        previous = _encrypt_block(block, round_keys, rounds)
        out += previous
    return bytes(out)


def aes_cbc_decrypt(key: bytes, iv: bytes, ciphertext: bytes) -> bytes:
    """AES-CBC + PKCS7 解密(密钥 16/24/32 字节)。"""
    if len(iv) != 16:
        raise CliError("AES IV 必须为 16 字节")
    if not ciphertext or len(ciphertext) % 16:
        raise CliError("AES 密文长度非法")
    round_keys, rounds = _expand_key(key)
    previous = iv
    out = bytearray()
    for offset in range(0, len(ciphertext), 16):
        block = ciphertext[offset : offset + 16]
        decrypted = _decrypt_block(block, round_keys, rounds)
        out += bytes(a ^ b for a, b in zip(decrypted, previous))
        previous = block
    # PKCS7 去填充
    pad_len = out[-1]
    if pad_len < 1 or pad_len > 16 or out[-pad_len:] != bytes([pad_len]) * pad_len:
        raise CliError("AES 解密失败: 填充非法(密钥错误或数据损坏)")
    return bytes(out[:-pad_len])


def aes_256_cbc_encrypt(key: bytes, iv: bytes, plaintext: bytes) -> bytes:
    """AES-256-CBC + PKCS7 加密(登录密码加密用,保留原接口)。"""
    if len(key) != 32:
        raise CliError("AES-256 密钥必须为 32 字节")
    return aes_cbc_encrypt(key, iv, plaintext)


def string_decrypt(text: str, key: str) -> str:
    """解密 1Panel DB 中的加密字符串(对应 Go `encrypt.StringDecrypt`)。

    格式: base64(AES-CBC(key_bytes, iv=前 16 字节, PKCS7 密文)),
    密钥为面板设置 `EncryptKey`(通常 16 字符,即 AES-128)。
    """
    if not text:
        return ""
    if not key:
        raise CliError("解密失败: 面板 EncryptKey 为空")
    try:
        data = base64.b64decode(text)
    except Exception as exc:  # noqa: BLE001
        raise CliError(f"解密失败: 密文不是合法 base64 ({exc})") from exc
    if len(data) < 32 or len(data) % 16:
        raise CliError("解密失败: 密文长度非法")
    return aes_cbc_decrypt(key.encode("utf-8"), data[:16], data[16:]).decode("utf-8")


# ---------------------------------------------------------------------------
# RSA PKCS#1 v1.5 公钥加密(纯 Python 模幂,无需第三方库)
# ---------------------------------------------------------------------------


def _der_read_length(data: bytes, pos: int) -> tuple[int, int]:
    if pos >= len(data):
        raise CliError("解析面板 RSA 公钥失败: DER 数据不完整")
    first = data[pos]
    pos += 1
    if first < 0x80:
        return first, pos
    num = first & 0x7F
    if num == 0 or num > 4 or pos + num > len(data):
        raise CliError("解析面板 RSA 公钥失败: 非法的 DER 长度")
    length = int.from_bytes(data[pos : pos + num], "big")
    return length, pos + num


def _der_expect(data: bytes, pos: int, tag: int) -> tuple[bytes, int]:
    if pos >= len(data) or data[pos] != tag:
        raise CliError("解析面板 RSA 公钥失败: DER 结构异常")
    length, pos = _der_read_length(data, pos + 1)
    if pos + length > len(data):
        raise CliError("解析面板 RSA 公钥失败: DER 数据不完整")
    return data[pos : pos + length], pos + length


def _parse_rsa_public_key(der: bytes) -> tuple[int, int]:
    """从 DER 编码的 SubjectPublicKeyInfo 或 PKCS#1 RSAPublicKey 提取 (n, e)。"""
    seq, pos = _der_expect(der, 0, 0x30)
    # 先按 PKCS#1 RSAPublicKey 尝试: SEQUENCE { INTEGER n, INTEGER e }
    try:
        n, npos = _der_expect(seq, 0, 0x02)
        e, epos = _der_expect(seq, npos, 0x02)
        if epos == len(seq):
            return int.from_bytes(n, "big"), int.from_bytes(e, "big")
    except CliError:
        pass
    # SubjectPublicKeyInfo: SEQUENCE { AlgorithmIdentifier, BIT STRING }
    seq, _ = _der_expect(der, 0, 0x30)
    _alg, pos = _der_expect(seq, 0, 0x30)
    bits, _ = _der_expect(seq, pos, 0x03)
    if not bits or bits[0] != 0x00:
        raise CliError("解析面板 RSA 公钥失败: BIT STRING 格式异常")
    key_seq, _ = _der_expect(bits[1:], 0, 0x30)
    n, npos = _der_expect(key_seq, 0, 0x02)
    e, _ = _der_expect(key_seq, npos, 0x02)
    return int.from_bytes(n, "big"), int.from_bytes(e, "big")


def _int_to_bytes(value: int, length: int) -> bytes:
    return value.to_bytes(length, "big")


def rsa_pkcs1v15_encrypt(public_key_pem: str, message: bytes) -> bytes:
    try:
        body = "".join(
            line.strip()
            for line in public_key_pem.strip().splitlines()
            if "-----" not in line
        )
        der = base64.b64decode(body)
        n, e = _parse_rsa_public_key(der)
    except CliError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise CliError(f"解析面板 RSA 公钥失败: {exc}") from exc

    k = (n.bit_length() + 7) // 8
    if len(message) > k - 11:
        raise CliError("RSA 加密失败: 消息过长")
    ps_len = k - len(message) - 3
    while True:
        padding = os.urandom(ps_len)
        if all(b != 0 for b in padding):
            break
    em = b"\x00\x02" + padding + b"\x00" + message
    cipher = pow(int.from_bytes(em, "big"), e, n)
    return _int_to_bytes(cipher, k)


def encrypt_password(password: str, public_key_pem: str) -> str:
    """按 1Panel 前端方式加密登录密码,返回 `keyCipher:iv:cipher`。

    生成 16 字节随机 AES 密钥,RSA(PKCS1v15)加密该密钥的 hex 字符串,
    再用 AES-256-CBC(PKCS7)加密密码本体。
    """
    aes_raw = os.urandom(16)
    aes_key_hex = aes_raw.hex()
    aes_key_bytes = aes_key_hex.encode("ascii")

    try:
        key_cipher = rsa_pkcs1v15_encrypt(public_key_pem, aes_key_bytes)
    except CliError as exc:
        raise CliError(f"RSA 加密 AES 密钥失败: {exc}") from exc

    iv = os.urandom(16)
    ciphertext = aes_256_cbc_encrypt(aes_key_bytes, iv, password.encode("utf-8"))
    return "{}:{}:{}".format(
        base64.b64encode(key_cipher).decode("ascii"),
        base64.b64encode(iv).decode("ascii"),
        base64.b64encode(ciphertext).decode("ascii"),
    )


def debug_public_key(public_key_pem: str) -> None:
    import sys

    if os.environ.get("PANEL_CLI_DEBUG"):
        print(f"[debug] public key PEM:\n{public_key_pem}", file=sys.stderr)


def url_decode(s: str) -> str:
    """URL 解码百分号转义,保留字面量 `+`(与 Rust 版一致)。"""
    data = s.encode("utf-8")
    out = bytearray()
    i = 0
    while i < len(data):
        byte = data[i]
        if byte == 0x25 and i + 2 < len(data):
            hi = _hex_val(data[i + 1])
            lo = _hex_val(data[i + 2])
            out.append(hi * 16 + lo)
            i += 3
        else:
            out.append(byte)
            i += 1
    return out.decode("utf-8")


def _hex_val(b: int) -> int:
    if 0x30 <= b <= 0x39:
        return b - 0x30
    if 0x61 <= b <= 0x66:
        return b - 0x61 + 10
    if 0x41 <= b <= 0x46:
        return b - 0x41 + 10
    raise CliError(f"URL 解码失败: 非法的转义序列 %{chr(b)}")
