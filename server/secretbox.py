# -*- coding: utf-8 -*-
"""本机密钥加密：Windows DPAPI（当前用户范围），非 Windows 退化为本地混淆。

为什么不用明文/自研加密：Config 里的 API Key 一旦被同步盘或误提交带走就是事故。
DPAPI 由系统做密钥托管，密文只能在同一 Windows 用户下解开，换机器/换用户即失效——
这正是我们想要的边界。密文形如 ``dpapi:<base64>``。
"""
from __future__ import annotations

import base64
import ctypes
import sys
from ctypes import wintypes as wt

IS_WIN = sys.platform == "win32"
_PREFIX_DPAPI = "dpapi:"
_PREFIX_PLAIN = "plain:"
_ENTROPY = b"claw-studio/settings/v1"


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes):
    """构造 DATA_BLOB，返回 (blob, buffer)——buffer 必须活到调用结束。"""
    buf = ctypes.create_string_buffer(data, len(data))
    return _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def _bytes_of(blob: _DATA_BLOB) -> bytes:
    return ctypes.string_at(blob.pbData, blob.cbData)


if IS_WIN:
    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DATA_BLOB), wt.LPCWSTR, ctypes.POINTER(_DATA_BLOB),
        ctypes.c_void_p, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(_DATA_BLOB),
    ]
    _crypt32.CryptProtectData.restype = wt.BOOL
    _crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DATA_BLOB), ctypes.POINTER(wt.LPWSTR), ctypes.POINTER(_DATA_BLOB),
        ctypes.c_void_p, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(_DATA_BLOB),
    ]
    _crypt32.CryptUnprotectData.restype = wt.BOOL
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _kernel32.LocalFree.restype = ctypes.c_void_p


def protect(text: str) -> str:
    """明文 → ``dpapi:...``。空串原样返回。"""
    text = str(text or "")
    if not text:
        return ""
    if not IS_WIN:
        return _PREFIX_PLAIN + base64.b64encode(text.encode("utf-8")).decode("ascii")
    data = text.encode("utf-8")
    blob_in, _keep = _blob(data)
    ent, _keep2 = _blob(_ENTROPY)
    blob_out = _DATA_BLOB()
    ok = _crypt32.CryptProtectData(
        ctypes.byref(blob_in), "claw-studio", ctypes.byref(ent), None, None, 0,
        ctypes.byref(blob_out),
    )
    if not ok:
        raise OSError(ctypes.get_last_error(), "CryptProtectData 失败")
    try:
        return _PREFIX_DPAPI + base64.b64encode(_bytes_of(blob_out)).decode("ascii")
    finally:
        _kernel32.LocalFree(blob_out.pbData)


def unprotect(token: str) -> str:
    """``dpapi:...`` → 明文；格式不认识则当作明文返回（兼容手填）。"""
    token = str(token or "")
    if not token:
        return ""
    try:
        if token.startswith(_PREFIX_DPAPI) and IS_WIN:
            raw = base64.b64decode(token[len(_PREFIX_DPAPI):])
            blob_in, _keep = _blob(raw)
            ent, _keep2 = _blob(_ENTROPY)
            blob_out = _DATA_BLOB()
            ok = _crypt32.CryptUnprotectData(
                ctypes.byref(blob_in), None, ctypes.byref(ent), None, None, 0,
                ctypes.byref(blob_out),
            )
            if not ok:
                return ""
            try:
                return _bytes_of(blob_out).decode("utf-8", "replace")
            finally:
                _kernel32.LocalFree(blob_out.pbData)
        if token.startswith(_PREFIX_PLAIN):
            return base64.b64decode(token[len(_PREFIX_PLAIN):]).decode("utf-8", "replace")
    except Exception:
        return ""
    return token


def mask(secret: str) -> str:
    """给界面看的掩码：只露尾部 4 位。"""
    s = str(secret or "")
    if not s:
        return ""
    if len(s) <= 8:
        return "•" * len(s)
    return "•" * 6 + s[-4:]
