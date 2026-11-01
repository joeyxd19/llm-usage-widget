# -*- coding: utf-8 -*-
"""密钥本机加密存取：Windows DPAPI（当前用户范围），零第三方依赖。

config.json 是明文 JSON，磁盘上的 API Key / SecretAccessKey 任何能读到
该文件的程序都能拿走。这里用 Windows 自带的 DPAPI（CryptProtectData）把
密钥字段加密后落盘：密文只有同一 Windows 账户能解开，复制到别的机器或
账户下解不开，不需要引入 cryptography 之类的第三方库。

- 存储格式："dpapi:" + base64(DPAPI blob)
- 非 Windows 平台：DPAPI 不可用，原样返回明文（行为与旧版一致）
- 解密失败（换机器 / 换 Windows 账户 / 文件损坏）：返回空字符串，
  提示用户在设置里重填，绝不让异常逃出配置加载链
"""

import base64
import os

PREFIX = "dpapi:"

# CRYPTPROTECT_UI_FORBIDDEN：加解密在无 UI 的后台线程/服务里也安全
_CRYPTPROTECT_UI_FORBIDDEN = 0x1


def is_encrypted(value):
    """值是否是本模块加密过的密文。"""
    return isinstance(value, str) and value.startswith(PREFIX)


def _dpapi(data, protect):
    """调 CryptProtectData / CryptUnprotectData，返回 bytes，失败返回 None。

    protect=True 加密，False 解密。DATA_BLOB 用 ctypes 手工组结构，
    输出缓冲由 DPAPI 分配，取完数据后 LocalFree 归还。"""
    import ctypes
    import ctypes.wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", ctypes.wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    try:
        if protect:
            ok = ctypes.windll.crypt32.CryptProtectData(
                ctypes.byref(blob_in), None, None, None, None,
                _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out))
        else:
            ok = ctypes.windll.crypt32.CryptUnprotectData(
                ctypes.byref(blob_in), None, None, None, None,
                _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out))
        if not ok or not blob_out.cbData or not blob_out.pbData:
            return None
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    except Exception:
        return None
    finally:
        if blob_out.pbData:
            try:
                ctypes.windll.kernel32.LocalFree(blob_out.pbData)
            except Exception:
                pass


def encrypt_secret(value):
    """明文 → 密文（"dpapi:" + base64）。

    非 Windows 平台 / 空值 / 加密失败时原样返回明文（宁可靠旧版的
    明文落盘，也不能因为加密故障把用户密钥弄丢）。"""
    if os.name != "nt" or not isinstance(value, str) or not value:
        return value
    blob = _dpapi(value.encode("utf-8"), protect=True)
    if not blob:
        return value
    return PREFIX + base64.b64encode(blob).decode("ascii")


def decrypt_secret(value):
    """密文 → 明文。失败（换机器 / 换账户 / 数据损坏 / 非 Windows）返回
    空字符串：密钥已经拿不回来，与其给一串垃圾字符去请求 API，不如让
    供应商标记为「未配置」，用户在设置里重填即可。"""
    if not is_encrypted(value):
        return value
    try:
        blob = base64.b64decode(value[len(PREFIX):].encode("ascii"), validate=True)
    except Exception:
        return ""
    if os.name != "nt":
        return ""
    plain = _dpapi(blob, protect=False)
    if plain is None:
        return ""
    try:
        return plain.decode("utf-8")
    except UnicodeDecodeError:
        return plain.decode("utf-8", "replace")
