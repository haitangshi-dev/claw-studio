# -*- coding: utf-8 -*-
"""看门狗：常驻，保证 relay daemon 和 worker 一直活着（不设时长上限）。

【为什么不弹黑框（2026-10-11 修）】
watchdog 由 pythonw.exe 启动 = 自己没有控制台。此时只要调任何控制台程序
（wmic / tasklist），Windows 就会**新开一个控制台窗口**，表现为屏幕上一闪的
黑框。旧版每 20 秒用 wmic 查一次进程，所以每 20 秒弹一次。

现在改成：
  1) 存活判断 = 直接读心跳文件 state/*-alive.json（纯文件操作，零子进程）；
  2) 心跳里的 pid 是否真活着 = ctypes 调 OpenProcess（纯 API，零子进程）；
  3) 清理僵尸 = ctypes 调 TerminateProcess（纯 API，零子进程）。
整个循环不启动任何外部程序，因此不会再有黑框。
"""
import ctypes
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from single_instance import acquire as acquire_lock

ROOT = Path(__file__).resolve().parent
os.environ["WECHAT_BRIDGE_HOME"] = str(ROOT)
LOGS = ROOT / "logs"; LOGS.mkdir(exist_ok=True)
STATE = ROOT / "state"; STATE.mkdir(exist_ok=True)
ALIVE = STATE / "watchdog-alive.json"     # 心跳（设置面板据此判断存活）
PY = sys.executable


def _pick_pythonw():
    """优先用同目录的 pythonw.exe：它没有控制台，任何情况下都不会闪黑框。"""
    try:
        p = Path(PY)
        if IS_WIN:
            cand = p.with_name("pythonw.exe")
            if cand.exists():
                return str(cand)
        # 目录里没有就找系统 path 上的 pythonw
        import shutil as _sh
        w = _sh.which("pythonw")
        if w:
            return w
    except Exception:
        pass
    return PY

# 无窗口 / 脱离控制台（仅在极少数必须起子进程时使用）
NO_WINDOW = 0x08000000 if os.name == "nt" else 0
DETACHED = 0x00000008 if os.name == "nt" else 0

IS_WIN = (os.name == "nt")
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_TERMINATE = 0x0001
STILL_ACTIVE = 259
_k32 = ctypes.windll.kernel32 if IS_WIN else None

# (脚本, 额外参数, 心跳文件名, 心跳新鲜阈值秒)
TARGETS = [
    ("relay.py", ["daemon"], "relay-alive.json", 90.0),   # 长轮询一次最多 35s，留余量
    ("worker.py", [], "worker-alive.json", 60.0),
]


def log(m):
    try:
        with open(LOGS / "watchdog.log", "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), m))
    except Exception:
        pass


def beat():
    try:
        tmp = ALIVE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"ts": time.time(), "pid": os.getpid()}), encoding="utf-8")
        os.replace(tmp, ALIVE)
    except Exception:
        pass


def pid_alive(pid):
    """进程是否活着（ctypes，零子进程 → 不弹黑框）。"""
    if not pid or not IS_WIN:
        return False
    try:
        h = _k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not h:
            return False
        code = ctypes.c_ulong(0)
        ok = _k32.GetExitCodeProcess(h, ctypes.byref(code))
        _k32.CloseHandle(h)
        return bool(ok) and code.value == STILL_ACTIVE
    except Exception:
        return False


def kill_pid(pid):
    """强制结束进程（ctypes，零子进程 → 不弹黑框）。"""
    if not pid or not IS_WIN:
        return False
    try:
        h = _k32.OpenProcess(PROCESS_TERMINATE, False, int(pid))
        if not h:
            return False
        ok = _k32.TerminateProcess(h, 1)
        _k32.CloseHandle(h)
        return bool(ok)
    except Exception:
        return False


def spawn(script, extra):
    """拉起脚本（一律用 pythonw，无控制台 → 不会闪黑框）。"""
    py = _pick_pythonw()
    log("拉起 %s %s（%s）" % (script, " ".join(extra), Path(py).name))
    try:
        log_path = LOGS / (script.replace(".py", "") + ".log")
        # 日志轮转：relay 曾在这个项目里长到 205MB（长轮询每轮都写一行），不轮转会吃满磁盘
        try:
            if log_path.exists() and log_path.stat().st_size > 8 * 1024 * 1024:
                bak = log_path.with_suffix(".log.1")
                os.replace(str(log_path), str(bak))
        except OSError:
            pass
        fo = open(log_path, "a", encoding="utf-8")
    except Exception:
        fo = subprocess.DEVNULL
    try:
        subprocess.Popen(
            [py, str(ROOT / script)] + extra, cwd=str(ROOT),
            stdout=fo, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            creationflags=(DETACHED | NO_WINDOW) if IS_WIN else 0)
    except Exception as e:
        log("拉起 %s 失败: %s" % (script, e))


def ensure():
    for script, extra, hb, fresh in TARGETS:
        path = STATE / hb
        ok = False
        age = None
        pid = None
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            pid = int(d.get("pid") or 0) or None
            age = time.time() - float(d.get("ts") or 0)
            ok = (age <= fresh) and (pid is None or pid_alive(pid))
        except Exception:
            ok = False
        if ok:
            continue                       # 活着 → 不动它（不启任何进程）
        # 不健康：有残留进程先清掉（ctypes 结束，不弹框）
        if pid and pid_alive(pid):
            log("清理无心跳的 %s 进程 pid=%s（age=%s）" % (script, pid, None if age is None else round(age, 1)))
            kill_pid(pid)
            time.sleep(1.0)
        # 心跳文件是僵尸残留 → 删掉，避免下次误判
        try:
            if path.exists():
                path.unlink()
        except Exception:
            pass
        spawn(script, extra)


if __name__ == "__main__":
    if not acquire_lock("watchdog"):
        log("已有 watchdog 在运行，本实例退出")
        sys.exit(0)
    log("watchdog 启动（无时长上限 · 心跳+ctypes 模式，不起子进程不弹黑框）")
    while True:
        try:
            beat()
            ensure()
        except Exception as e:
            log("异常（继续）: %s" % e)
        time.sleep(20)
