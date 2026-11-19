# -*- coding: utf-8 -*-
"""单实例锁：Windows 命名互斥体，进程退出时由系统自动释放。"""
import ctypes
import os

_handles = []


def acquire(name):
    """尝试占用 <name> 的锁：拿到返回 True，已有实例在跑返回 False。"""
    if os.name != "nt":
        return True
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = k32.CreateMutexW(None, False, "Local\\ClawWechatBridge-" + name)
        if not handle:
            return True
        if ctypes.get_last_error() == 183:      # ERROR_ALREADY_EXISTS
            k32.CloseHandle(handle)
            return False
        _handles.append(handle)                 # 保活，锁随进程退出释放
        return True
    except Exception:
        return True
