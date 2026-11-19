# -*- coding: utf-8 -*-
"""Windows 原生文件夹选择框（资源管理器式）。

设计：
  - 用 Shell 的 SHBrowseForFolderW + BIF_NEWDIALOGSTYLE：底层就是资源管理器目录树，
    支持新建文件夹、右键、盘符浏览；
  - 在独立 STA 线程里弹框（FastAPI 的线程池线程通常已初始化 COM 为 MTA，
    直接调用 Shell 弹框容易失败）；
  - 以当前前台窗口为 owner，避免对话框弹到 WebView 后面。
"""
from __future__ import annotations

import ctypes
import queue
import sys
import threading
from pathlib import Path

_BIF_RETURNONLYFSDIRS = 0x00000001
_BIF_EDITBOX = 0x00000010
_BIF_NEWDIALOGSTYLE = 0x00000040
_BIF_USENEWUI = 0x00000050  # NEWDIALOGSTYLE | EDITBOX
_BIF_VALIDATE = 0x00000020
_BIF_NODELETE = 0x00080000


class _BROWSEINFOW(ctypes.Structure):
    _fields_ = [
        ("hwndOwner", ctypes.c_void_p),
        ("pidlRoot", ctypes.c_void_p),
        ("pszDisplayName", ctypes.c_wchar_p),
        ("lpszTitle", ctypes.c_wchar_p),
        ("ulFlags", ctypes.c_uint),
        ("lpfn", ctypes.c_void_p),
        ("lParam", ctypes.c_ssize_t),
        ("iImage", ctypes.c_int),
    ]


def _dialog_worker(initial_dir: str | None, title: str, q: "queue.Queue[dict]") -> None:
    if sys.platform != "win32":
        q.put({"ok": False, "error": "原生文件夹选择框仅支持 Windows"})
        return
    try:
        from ctypes import wintypes

        ole32 = ctypes.WinDLL("ole32", use_last_error=True)
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        # 64 位下 HWND 超过 32 位，必须显式声明返回类型，否则句柄被截断
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetForegroundWindow.argtypes = []

        shell32.SHBrowseForFolderW.argtypes = [ctypes.POINTER(_BROWSEINFOW)]
        shell32.SHBrowseForFolderW.restype = ctypes.c_void_p
        shell32.SHGetPathFromIDListW.argtypes = [ctypes.c_void_p, wintypes.LPWSTR]
        shell32.SHGetPathFromIDListW.restype = wintypes.BOOL
        shell32.SHParseDisplayName.argtypes = [
            wintypes.LPCWSTR, ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p), wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        shell32.SHParseDisplayName.restype = ctypes.c_long
        ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]

        hr = ole32.CoInitializeEx(None, 0x2)  # COINIT_APARTMENTTHREADED
        # hr=1 (S_FALSE) 也可以继续用；RPC_E_CHANGED_MODE 时 Shell 通常仍可弹框
        pidl_root = ctypes.c_void_p()
        attrs = wintypes.DWORD()
        if initial_dir and Path(initial_dir).is_dir():
            try:
                shell32.SHParseDisplayName(
                    str(initial_dir), None, ctypes.byref(pidl_root), 0, ctypes.byref(attrs)
                )
            except Exception:
                pidl_root = ctypes.c_void_p()

        display = ctypes.create_unicode_buffer(32768)
        info = _BROWSEINFOW()
        info.hwndOwner = user32.GetForegroundWindow()
        info.pidlRoot = pidl_root.value
        info.pszDisplayName = ctypes.cast(display, ctypes.c_wchar_p)
        info.lpszTitle = title
        info.ulFlags = (_BIF_RETURNONLYFSDIRS | _BIF_NEWDIALOGSTYLE | _BIF_EDITBOX
                        | _BIF_VALIDATE | _BIF_NODELETE)
        pidl = shell32.SHBrowseForFolderW(ctypes.byref(info))
        try:
            if not pidl:
                q.put({"ok": False, "canceled": True})
                return
            buf = ctypes.create_unicode_buffer(32768)
            if not shell32.SHGetPathFromIDListW(pidl, buf):
                q.put({"ok": False, "error": "无法读取所选目录路径"})
                return
            q.put({"ok": True, "path": buf.value})
        finally:
            if pidl:
                ole32.CoTaskMemFree(pidl)
            if pidl_root.value:
                ole32.CoTaskMemFree(pidl_root)
            try:
                ole32.CoUninitialize()
            except Exception:
                pass
    except Exception as e:
        q.put({"ok": False, "error": f"{type(e).__name__}: {e}"[:300]})


def pick_folder(initial_dir: str | None = None, title: str = "选择会话工作目录") -> dict:
    """弹资源管理器式目录选择框。返回 {ok,path} / {ok:False,canceled} / {ok:False,error}。"""
    q: queue.Queue[dict] = queue.Queue(maxsize=1)
    t = threading.Thread(target=_dialog_worker, args=(initial_dir, title, q), daemon=True)
    t.start()
    t.join()  # 用户点确定/取消后返回；HTTP 请求线程等待，不阻塞事件循环外的工作
    try:
        return q.get_nowait()
    except queue.Empty:
        return {"ok": False, "error": "目录选择框异常退出"}
