# -*- coding: utf-8 -*-
"""桌面宠物（Claw 宠物）的生命周期管理。

背景：宠物是 `C:\\workspace\\dynamic-island\\pet` 下的一个 Electron 透明浮窗，
之前只能靠 `host/start-pet.bat` 手动拉起。这个模块把它接进设置面板：
    - 状态探测（在不在跑）
    - 启动 / 停止
    - 「随 Claw Agent 启动自动拉起」开关（config: pet.auto_start）

状态探测为什么不用扫进程：
    Windows 上 `Get-CimInstance Win32_Process` 一次要 3 秒上下（实测），
    放进设置面板的轮询里完全不能接受。所以让宠物自己每 2 秒写一个心跳
    （dynamic-island/logs/pet-alive.json，见 pet/host/main.js），
    这里只读那个小文件 —— 亚毫秒级，且退出时会删掉，误判率低。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

# 无控制台启动（Claw Agent 是 --windowed 打包，不留神就会弹出命令行黑框）
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0

# 宠物宿主目录候选（第一个存在的即用；config.pet.dir 优先）
PET_DIR_CANDIDATES = [
    Path(r"C:\workspace\dynamic-island\pet\host"),
    Path(r"C:\workspace\dynamic-island\pet"),
]

# 心跳新鲜阈值（秒）。宠物每 2 秒写一次，留 3 个周期的余量。
ALIVE_FRESH = 7.0


def resolve_pet_dir(cfg_pet: dict | None = None) -> Path | None:
    """找到宠物宿主目录（含 main.js 的那个）。"""
    cands: list[Path] = []
    d = str((cfg_pet or {}).get("dir") or "").strip()
    if d:
        cands.append(Path(d))
    cands.extend(PET_DIR_CANDIDATES)
    for p in cands:
        try:
            if (p / "main.js").exists():
                return p
        except OSError:
            continue
    return None


def resolve_electron(pet_dir: Path | None) -> Path | None:
    """找到 electron.exe。宠物复用 dynamic-island 下 node_modules 里的 Electron。"""
    cands: list[Path] = []
    if pet_dir is not None:
        # pet/host → pet → dynamic-island
        island = pet_dir.parent.parent
        cands.append(island / "node_modules" / "electron" / "dist" / "electron.exe")
        cands.append(pet_dir / "node_modules" / "electron" / "dist" / "electron.exe")
    cands.append(Path(r"C:\workspace\dynamic-island\node_modules\electron\dist\electron.exe"))
    for p in cands:
        try:
            if p.exists():
                return p
        except OSError:
            continue
    return None


def alive_file(pet_dir: Path | None) -> Path | None:
    """宠物的存活心跳文件（由 pet/host/main.js 写）。"""
    if pet_dir is None:
        return None
    return pet_dir.parent.parent / "logs" / "pet-alive.json"


def read_alive(pet_dir: Path | None) -> dict | None:
    p = alive_file(pet_dir)
    if p is None:
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def status(cfg_pet: dict | None = None) -> dict:
    """宠物当前状态：能不能启动、在不在跑、跑着的 pid。"""
    pet_dir = resolve_pet_dir(cfg_pet)
    electron = resolve_electron(pet_dir)
    info: dict = {
        "ok": True,
        "dir": str(pet_dir) if pet_dir else "",
        "electron": str(electron) if electron else "",
        "can_start": bool(pet_dir and electron),
        "running": False,
        "pid": None,
        "age": None,
    }
    if pet_dir is None:
        info["error"] = "找不到宠物目录（pet/host/main.js）"
        return info
    if electron is None:
        info["error"] = "找不到 electron.exe（dynamic-island/node_modules）"
    a = read_alive(pet_dir)
    if a:
        try:
            age = time.time() - float(a.get("ts") or 0)
        except Exception:
            age = 1e9
        info["age"] = round(age, 1)
        if age <= ALIVE_FRESH:
            info["running"] = True
            info["pid"] = a.get("pid")
    return info


def start(cfg_pet: dict | None = None) -> dict:
    """拉起宠物（已跑着就直接返回，不重复起）。"""
    st = status(cfg_pet)
    if st.get("running"):
        return {**st, "started": False, "message": "宠物已经在跑了"}
    pet_dir = resolve_pet_dir(cfg_pet)
    electron = resolve_electron(pet_dir)
    if pet_dir is None:
        return {"ok": False, "error": "找不到宠物目录（pet/host/main.js）"}
    if electron is None:
        return {"ok": False, "error": "找不到 electron.exe（dynamic-island/node_modules/electron）"}
    try:
        kw = {"cwd": str(pet_dir), "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if _NO_WINDOW:
            kw["creationflags"] = _NO_WINDOW
        proc = subprocess.Popen([str(electron), str(pet_dir)], **kw)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}
    # 等心跳出现（最多 2.5 秒）——这样调用方（设置面板）拿到的就是 running=true，
    # 不会先闪一下「未运行」再靠轮询纠正。
    deadline = time.time() + 2.5
    while time.time() < deadline:
        time.sleep(0.2)
        a = read_alive(pet_dir)
        try:
            if a and time.time() - float(a.get("ts") or 0) <= ALIVE_FRESH:
                break
        except Exception:
            continue
    st2 = status(cfg_pet)
    return {**st2, "ok": True, "started": True, "pid": proc.pid, "message": "宠物已启动"}


def stop(cfg_pet: dict | None = None) -> dict:
    """停掉宠物（按心跳里的 pid 杀进程树；心跳里的 pid 就是 Electron 主进程）。"""
    st = status(cfg_pet)
    pid = st.get("pid")
    if not st.get("running"):
        _clean_alive(cfg_pet)
        return {**st, "stopped": False, "message": "宠物本来就没在跑"}
    if pid:
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                creationflags=_NO_WINDOW,
                timeout=15,
            )
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}
    # 等它把心跳删掉（main.js 退出时会 unlink）；最多等 3 秒，再不行就手动清
    for _ in range(15):
        time.sleep(0.2)
        if not read_alive(resolve_pet_dir(cfg_pet)):
            break
    _clean_alive(cfg_pet)
    st2 = status(cfg_pet)
    return {**st2, "ok": True, "stopped": True, "message": "宠物已停止"}


def _clean_alive(cfg_pet: dict | None = None) -> None:
    """清掉残留心跳文件（进程被强杀时 main.js 没来得及删）。"""
    p = alive_file(resolve_pet_dir(cfg_pet))
    if p is None:
        return
    try:
        if p.exists():
            p.unlink()
    except Exception:
        pass


def auto_start(cfg_pet: dict | None = None) -> dict:
    """应用启动时调用：配置开了才拉起，失败静默（不能影响框架启动）。"""
    if not (cfg_pet or {}).get("auto_start"):
        return {"ok": True, "started": False, "reason": "auto_start 未开启"}
    try:
        return start(cfg_pet)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:200]}


if __name__ == "__main__":
    # 调试：python server/pet.py [status|start|stop]
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    fn = {"status": status, "start": start, "stop": stop}.get(cmd, status)
    print(json.dumps(fn({}), ensure_ascii=False, indent=2))
