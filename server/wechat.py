# -*- coding: utf-8 -*-
"""微信桥接的生命周期管理（把「微信直连 Claw Agent」接进设置面板）。

背景：微信通过腾讯官方 iLink Bot 协议直连本机 Claw Agent（不走 OpenClaw），
实现是 workspace/wechat-bridge/ 下的三个常驻脚本：
    relay.py    daemon  长轮询收微信消息 / 发送回复
    worker.py           微信消息 -> POST /api/chat -> 写回复文件
    watchdog.py         看门狗，每 20 秒保活上面两个（不设时长上限）

这个模块把它接进设置面板：
    - 状态探测（在不在跑 / 登录没登录 / 绑定的微信用户）
    - 启动 / 停止
    - 「随 Claw Agent 启动自动拉起」开关（config: wechat.auto_start）

状态探测为什么不用扫进程：
    Windows 上查进程命令行一次要好几秒（实测），放进设置面板的轮询里不能接受。
    所以让 relay / worker 各自轮询时写一个心跳文件（state/*-alive.json），
    这里只读那两个小文件 —— 亚毫秒级。看门狗本身用进程扫描兜底判断。
"""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
# 无控制台启动（Claw Agent 是 --windowed 打包，不留神就会弹出命令行黑框）
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0

# ---------- 进程存活 / 结束：用 ctypes 直接调 Win32 API ----------
# 为什么要这样：本框架是 --windowed 打包（无控制台）。此时调用 wmic/taskkill
# 这类控制台程序，即使带了 CREATE_NO_WINDOW，也有概率闪出黑框；而且 wmic
# 一次要好几秒，放进设置面板的 4 秒轮询里会拖慢。改用 OpenProcess/
# GetExitCodeProcess/TerminateProcess，纯 API 调用、亚毫秒、绝不弹框。
_IS_WIN = (sys.platform == "win32")
_k32 = ctypes.windll.kernel32 if _IS_WIN else None
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_PROCESS_TERMINATE = 0x0001
_STILL_ACTIVE = 259


def _pid_alive(pid: int | None) -> bool:
    """进程是否还活着（零子进程）。"""
    if not pid or not _IS_WIN:
        return False
    try:
        h = _k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not h:
            return False
        code = ctypes.c_ulong(0)
        ok = _k32.GetExitCodeProcess(h, ctypes.byref(code))
        _k32.CloseHandle(h)
        return bool(ok) and code.value == _STILL_ACTIVE
    except Exception:
        return False


def _kill_pid(pid: int | None) -> bool:
    """强制结束进程（零子进程、不弹框）。"""
    if not pid or not _IS_WIN:
        return False
    try:
        h = _k32.OpenProcess(_PROCESS_TERMINATE, False, int(pid))
        if not h:
            return False
        ok = _k32.TerminateProcess(h, 1)
        _k32.CloseHandle(h)
        return bool(ok)
    except Exception:
        return False


# 桥接目录候选（config.wechat.dir 优先）
BRIDGE_SUBDIRS = ["workspace/wechat-bridge", "wechat-bridge"]

# 心跳新鲜阈值（秒）。relay 的长轮询一次最多 35 秒，留足余量。
RELAY_FRESH = 90.0
WORKER_FRESH = 60.0

SCRIPTS = ("relay.py", "worker.py", "watchdog.py")


def resolve_bridge_dir(cfg_wechat: dict | None = None, data_root: Path | None = None) -> Path | None:
    """找到桥接目录（含 relay.py + worker.py 的那个）。"""
    cands: list[Path] = []
    d = str((cfg_wechat or {}).get("dir") or "").strip()
    if d:
        cands.append(Path(d))
    roots = [Path(p) for p in ((cfg_wechat or {}).get("roots") or [])]
    if data_root is not None:
        roots.insert(0, Path(data_root))
    # 兜底：始终把安装目录纳入候选（源码模式运行时 DATA_ROOT 是源码目录，
    # 而桥接实际部署在安装目录下）
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots.append(Path(local) / "Programs" / "ClawAgent")
    if not roots:
        roots.append(Path(__file__).resolve().parent.parent)
    for r in roots:
        for sub in BRIDGE_SUBDIRS:
            cands.append(Path(r) / sub)
    for p in cands:
        try:
            if (p / "relay.py").exists() and (p / "worker.py").exists():
                return p
        except OSError:
            continue
    return None


def resolve_python(cfg_agent: dict | None = None) -> str | None:
    """找 Python 解释器。打包后 sys.executable 是 ClawAgent.exe，不能用。"""
    p = str((cfg_agent or {}).get("python_exe") or "").strip()
    if p and Path(p).exists():
        # 优先无窗口版本（避免弹黑框）
        pw = Path(p).with_name("pythonw.exe")
        return str(pw) if pw.exists() else p
    for name in ("pythonw", "python"):
        w = shutil.which(name)
        if w:
            return w
    return None


def _read_json(path: Path) -> dict | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def _alive(path: Path, fresh: float) -> tuple[bool, int | None, float | None]:
    """读心跳：返回 (是否新鲜, pid, 距今秒数)。"""
    d = _read_json(path)
    if not d:
        return False, None, None
    try:
        age = time.time() - float(d.get("ts") or 0)
    except Exception:
        return False, None, None
    pid = d.get("pid")
    return (age <= fresh), (int(pid) if pid else None), round(age, 1)


def _script_pids(bridge: Path, script: str, alive_name: str | None = None) -> list[int]:
    """定位某脚本的进程：只信心跳里的 pid，并确认它真活着。

    不做命令行扫描（那要起 wmic 子进程，既慢又可能弹黑框）；心跳由各脚本
    自己每轮刷新，启动时也会写，足够可靠。
    """
    if not alive_name:
        return []
    d = _read_json(bridge / "state" / alive_name)
    try:
        pid = int((d or {}).get("pid") or 0)
    except Exception:
        return []
    if pid and _pid_alive(pid):
        return [pid]
    return []


def status(cfg: dict | None = None, data_root: Path | None = None) -> dict:
    """桥接当前状态：能不能启动、在不在跑、登录没登录、绑定谁。

    全程零子进程：心跳读文件 + ctypes 判 pid 存活（不启 wmic，不弹黑框）。
    """
    cfg = cfg or {}
    cfg_w = dict(cfg.get("wechat") or {})
    bridge = resolve_bridge_dir(cfg_w, data_root)
    python = resolve_python(cfg.get("agent") or {})
    info: dict = {
        "ok": True,
        "dir": str(bridge) if bridge else "",
        "can_start": bool(bridge and python),
        "running": False,
        "relay_running": False,
        "worker_running": False,
        "watchdog_running": False,
        "pid": None,
        "relay_age": None,
        "worker_age": None,
        "logged_in": False,
        "auth_status": "unknown",
        "bound_user": "",
        "bot_id": "",
        "inbox_pending": 0,
        "outbox_pending": 0,
        "model": str(cfg_w.get("model") or "wb:auto"),
        "auto_start": bool(cfg_w.get("auto_start")),
    }
    if bridge is None:
        info["error"] = "找不到桥接目录（workspace/wechat-bridge 下的 relay.py/worker.py）"
        return info
    if python is None:
        info["error"] = "找不到 Python 解释器（config.agent.python_exe）"

    state = bridge / "state"
    secure = bridge / "secure"

    relay_ok, relay_pid, relay_age = _alive(state / "relay-alive.json", RELAY_FRESH)
    worker_ok, worker_pid, worker_age = _alive(state / "worker-alive.json", WORKER_FRESH)
    dog_ok, dog_pid, dog_age = _alive(state / "watchdog-alive.json", WORKER_FRESH)
    info["relay_age"] = relay_age
    info["worker_age"] = worker_age
    info["watchdog_age"] = dog_age
    # 心跳 + pid 双重校验：心跳新鲜且进程真活着才算运行中（ctypes 判活，零子进程）
    info["relay_running"] = bool(relay_ok and (relay_pid is None or _pid_alive(relay_pid)))
    info["worker_running"] = bool(worker_ok and (worker_pid is None or _pid_alive(worker_pid)))
    info["watchdog_running"] = bool(dog_ok and (dog_pid is None or _pid_alive(dog_pid)))
    # 看门狗在 = 桥接在跑（它会补齐 relay/worker）
    info["running"] = bool(info["relay_running"] or info["worker_running"] or info["watchdog_running"])
    info["pid"] = relay_pid or worker_pid

    tok = _read_json(secure / "token.json")
    info["logged_in"] = bool(tok and tok.get("bot_token"))
    info["bot_id"] = str((tok or {}).get("ilink_bot_id") or "")
    auth = _read_json(state / "auth.json")
    info["auth_status"] = str((auth or {}).get("status") or ("ok" if info["logged_in"] else "unknown"))
    bind = _read_json(secure / "binding.json")
    info["bound_user"] = str((bind or {}).get("bound_user_id") or "")

    try:
        info["inbox_pending"] = len(list((bridge / "inbox").glob("*.json")))
        info["outbox_pending"] = len(list((bridge / "outbox").glob("*.json")))
    except Exception:
        pass
    if not info["logged_in"]:
        info["hint"] = "还没扫码登录：在桥接目录运行 python login_qr.py"
    return info


ALIVE_NAMES = {
    "watchdog.py": "watchdog-alive.json",
    "relay.py": "relay-alive.json",
    "worker.py": "worker-alive.json",
}


def _kill_scripts(bridge: Path, scripts: tuple[str, ...]) -> list[int]:
    """结束指定脚本进程（ctypes，零子进程、不弹框）。"""
    killed: list[int] = []
    for s in scripts:
        for pid in _script_pids(bridge, s, ALIVE_NAMES.get(s)):
            if _kill_pid(pid):
                killed.append(pid)
    return killed


def start(cfg: dict | None = None, data_root: Path | None = None) -> dict:
    """拉起桥接（已跑着就返回现状，不重复起）。"""
    cfg = cfg or {}
    st = status(cfg, data_root)
    # 三个都活着才算真的在跑；只看 watchdog 心跳会被残留文件骗到而拒绝启动
    if st.get("relay_running") and st.get("worker_running") and st.get("watchdog_running"):
        return {**st, "started": False, "message": "微信桥接已经在跑了"}
    bridge = resolve_bridge_dir(dict(cfg.get("wechat") or {}), data_root)
    python = resolve_python(cfg.get("agent") or {})
    if bridge is None:
        return {"ok": False, "error": "找不到桥接目录（workspace/wechat-bridge）"}
    if python is None:
        return {"ok": False, "error": "找不到 Python 解释器（config.agent.python_exe）"}
    if not st.get("logged_in"):
        return {"ok": False, "error": "还没扫码登录微信（先跑 python login_qr.py 扫码）"}

    env = dict(os.environ)
    env["WECHAT_BRIDGE_HOME"] = str(bridge)
    env["PYTHONIOENCODING"] = "utf-8"
    env["CLAW_MODEL"] = str(dict(cfg.get("wechat") or {}).get("model") or "wb:auto")
    env["CLAW_PERMISSION"] = str(dict(cfg.get("wechat") or {}).get("permission") or "standard")
    try:
        kw = {"cwd": str(bridge), "env": env,
              "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
              "stdin": subprocess.DEVNULL}
        if _NO_WINDOW:
            kw["creationflags"] = _NO_WINDOW
        proc = subprocess.Popen([python, str(bridge / "watchdog.py")], **kw)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}

    # 等心跳出现（最多 8 秒），让设置面板立刻看到 running=true
    deadline = time.time() + 8
    while time.time() < deadline:
        time.sleep(0.4)
        s = status(cfg, data_root)
        if s.get("relay_running") or s.get("worker_running"):
            return {**s, "ok": True, "started": True, "pid": proc.pid, "message": "微信桥接已启动"}
    s = status(cfg, data_root)
    return {**s, "ok": True, "started": True, "pid": proc.pid, "message": "微信桥接已启动（等心跳）"}


def stop(cfg: dict | None = None, data_root: Path | None = None) -> dict:
    """停掉桥接：先杀看门狗（否则它会把 relay/worker 拉回来），再杀 relay/worker。"""
    cfg = cfg or {}
    bridge = resolve_bridge_dir(dict(cfg.get("wechat") or {}), data_root)
    if bridge is None:
        return {"ok": False, "error": "找不到桥接目录"}
    killed = _kill_scripts(bridge, ("watchdog.py", "worker.py", "relay.py"))
    # 清掉全部心跳，避免残留文件让面板继续显示「运行中」
    for f in ("watchdog-alive.json", "relay-alive.json", "worker-alive.json"):
        try:
            p = bridge / "state" / f
            if p.exists():
                p.unlink()
        except Exception:
            pass
    time.sleep(1.5)
    st = status(cfg, data_root)
    return {**st, "ok": True, "stopped": True, "killed": killed,
            "message": "微信桥接已停止" if not st.get("running") else "部分进程仍在运行"}


def auto_start(cfg: dict | None = None, data_root: Path | None = None) -> dict:
    """应用启动时调用：配置开了才拉起，失败静默（不能影响框架启动）。"""
    if not ((cfg or {}).get("wechat") or {}).get("auto_start"):
        return {"ok": True, "started": False, "reason": "auto_start 未开启"}
    try:
        return start(cfg, data_root)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:200]}


if __name__ == "__main__":
    # 调试：python server/wechat.py [status|start|stop]
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("mode", nargs="?", default="status", choices=("status", "start", "stop"))
    ap.add_argument("--root", default="")
    a = ap.parse_args()
    root = Path(a.root) if a.root else None
    fn = {"status": status, "start": start, "stop": stop}[a.mode]
    print(json.dumps(fn({}, root), ensure_ascii=False, indent=2))
