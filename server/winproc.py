# -*- coding: utf-8 -*-
"""子进程启动的 Windows 细节收口。

背景：Claw Agent 打包成 --windowed 的 GUI 进程（没有自己的控制台）。
Windows 在这种情况下，子进程默认会**新开一个控制台窗口**——用户看到的就是
命令行窗口一闪一闪地冒出来。解决办法是给每个子进程带上 CREATE_NO_WINDOW。

这里集中封装，避免各模块各写一遍（也避免以后新加代码时漏掉）：
    from winproc import run, popen
    run(["bash", "-lc", cmd], capture_output=True)
"""

from __future__ import annotations

import subprocess
import sys

# Windows 专用标志；其它平台为 0（无害）
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


def run(args, **kw):
    """subprocess.run：默认不弹控制台窗口。"""
    if NO_WINDOW:
        kw.setdefault("creationflags", NO_WINDOW)
    return subprocess.run(args, **kw)


def popen(args, **kw):
    """subprocess.Popen：默认不弹控制台窗口。"""
    if NO_WINDOW:
        kw.setdefault("creationflags", NO_WINDOW)
    return subprocess.Popen(args, **kw)


def detached_flags():
    """完全脱离父进程（用 schtasks / 自更新辅助进程时用）。"""
    flags = 0
    flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
    flags |= getattr(subprocess, "DETACHED_PROCESS", 0)
    flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return flags
