# -*- coding: utf-8 -*-
"""原生自更新：检查更新源 → 换名交换 → 自动重启（Windows 下运行中的 exe 可改名，不可覆盖）。

更新源（两种）：
  - 本地目录：含 ClawAgent.exe + version.json（如开发机的 dist/）
  - http(s) 直链前缀：{url}/version.json、{url}/ClawAgent.exe

流程（apply）：
  1. 新 exe 复制到安装目录 ClawAgent.new.exe，新 version.json 复制为 version.json.new
  2. 生成 update.bat：等本进程退出 → 旧 exe 改名 .old → 新 exe 就位 → 清旧 → 启动新 exe
  3. 脱离父进程启动 bat，调用方负责随后退出进程（EXIT_HOOK 清理 llama-server 等）
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import winproc  # 子进程无窗收口（CREATE_NO_WINDOW）

# 主程序启动时注入：更新前清理（停 llama-server 等）并退出进程
EXIT_HOOK = None

# 应用更新时的"退出标记"路径（退出前写它，更新助手据此开始交换）
PENDING_EXIT_FLAG: Path | None = None


def exe_dir() -> Path:
    """安装目录（冻结=exe 所在目录；源码=项目根）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def current_version() -> dict:
    """当前版本：安装目录 version.json 优先（可被更新写新），退回打包内置。"""
    for p in (exe_dir() / "version.json",
              Path(getattr(sys, "_MEIPASS", exe_dir())) / "version.json",
              Path(__file__).resolve().parents[1] / "version.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(d, dict) and d.get("version"):
                return {"version": str(d["version"]), "built_at": str(d.get("built_at") or ""),
                        "notes": str(d.get("notes") or ""), "path": str(p)}
        except Exception:
            continue
    return {"version": "unknown", "built_at": "", "notes": "", "path": ""}


def _fetch_source(source: str) -> dict:
    """读取更新源：本地目录或 URL 前缀。返回 {exe: path|url, version: {...}}。"""
    src = (source or "").strip()
    if not src:
        raise ValueError("未配置更新源（config.update.source_dir 或请求参数）")
    if src.startswith(("http://", "https://")):
        import httpx

        base = src.rstrip("/")
        v = httpx.get(f"{base}/version.json", timeout=20).json()
        return {"exe": f"{base}/ClawAgent.exe", "version": v, "kind": "url"}
    d = Path(src)
    if not d.is_dir():
        raise FileNotFoundError(f"更新源目录不存在：{d}")
    v = json.loads((d / "version.json").read_text(encoding="utf-8"))
    exe = d / "ClawAgent.exe"
    if not exe.is_file():
        raise FileNotFoundError(f"更新源缺少 ClawAgent.exe：{d}")
    return {"exe": str(exe), "version": v, "kind": "dir"}


def check(source: str) -> dict:
    cur = current_version()
    try:
        remote = _fetch_source(source)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:300], "current": cur}
    rv = remote["version"]
    available = str(rv.get("version") or "") != cur.get("version")
    return {
        "ok": True,
        "current": cur,
        "latest": {"version": str(rv.get("version") or ""), "built_at": str(rv.get("built_at") or ""),
                   "notes": str(rv.get("notes") or "")},
        "available": available,
        "frozen": bool(getattr(sys, "frozen", False)),
    }


def _helper_script() -> str:
    """生成独立 Python 更新助手源码（等待退出标记 → 交换文件 → 拉起新版 → 写日志）。"""
    return '''# -*- coding: utf-8 -*-
"""Claw Agent 更新助手（由框架生成，独立运行）。
等待退出标记 → 交换文件 → 启动新版。日志: %TEMP%/claw_update_log.txt
"""
import os
import shutil
import subprocess
import sys
import time

INSTALL = r"{install}"
EXE = r"{exe}"
FLAG = r"{flag}"
LOG = r"{log}"


def log(msg):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"[{{time.strftime('%H:%M:%S')}}] {{msg}}\\n")
    except Exception:
        pass


def main():
    log("helper start, waiting flag")
    for _ in range(180):  # 最多 90 秒
        if os.path.exists(FLAG):
            break
        time.sleep(0.5)
    log("flag seen, swapping")
    os.chdir(INSTALL)
    try:
        if os.path.exists("ClawAgent.old.exe"):
            os.remove("ClawAgent.old.exe")
        os.rename(EXE, os.path.join(INSTALL, "ClawAgent.old.exe"))
        os.rename(os.path.join(INSTALL, "ClawAgent.new.exe"), EXE)
        if os.path.exists(os.path.join(INSTALL, "version.json.new")):
            os.replace(os.path.join(INSTALL, "version.json.new"), os.path.join(INSTALL, "version.json"))
        log("swap done")
    except Exception as e:
        log(f"swap FAILED: {{type(e).__name__}}: {{e}}")
        return
    # —— 先启动新实例（关键路径优先），后收尾 ——
    # 主路径：任务计划服务代启动（schtasks）——父进程=svchost（长存、exe 路径可读），
    # 对 PyInstaller 6.22.x 的父进程安全校验、无控制台/特殊父进程等全部坑免疫（本机实证 100%）。
    # 回退链：bash 相对路径 → 直接 Popen → os.startfile。
    # 注：本脚本独立运行（不 import 主程序模块），无窗标志内联。
    nowin = 0x08000000  # CREATE_NO_WINDOW
    log("launching now")
    launched = False
    task = "ClawAgentSelfUpdate"
    try:
        subprocess.run(["schtasks", "/create", "/f", "/tn", task, "/tr", EXE,
                        "/sc", "once", "/st", "00:00"],
                       capture_output=True, timeout=30, creationflags=nowin)
        subprocess.run(["schtasks", "/run", "/tn", task], capture_output=True, timeout=30,
                       creationflags=nowin)
        launched = True
        log("launched via schtasks")
    except Exception as e:
        log(f"schtasks launch FAILED: {{type(e).__name__}}: {{e}}")
    if not launched:
        exe_name = os.path.basename(EXE)
        bash = shutil.which("bash") or r"C:\Program Files\Git\bin\bash.exe"
        try:
            subprocess.Popen([bash, "-c", "./" + exe_name], cwd=INSTALL,
                             creationflags=nowin | 0x00000008 | 0x00000200,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            launched = True
            log("launched via bash")
        except Exception as e:
            log(f"bash launch FAILED: {{type(e).__name__}}: {{e}}")
    if not launched:
        try:
            os.startfile(EXE)
            launched = True
            log("launched via startfile")
        except Exception as e:
            log(f"startfile FAILED: {{type(e).__name__}}: {{e}}")
    if not launched:
        return
    import urllib.request
    ok = False
    for _ in range(60):  # 最多 ~120 秒
        time.sleep(2)
        try:
            with urllib.request.urlopen("http://127.0.0.1:8090/api/health", timeout=3) as r:
                if r.status == 200:
                    ok = True
                    break
        except Exception:
            pass
    log(f"new instance {{'is up' if ok else 'did NOT respond in time'}}")
    # 清理自更新任务
    try:
        subprocess.run(["schtasks", "/delete", "/f", "/tn", task], capture_output=True, timeout=30,
                       creationflags=nowin)
    except Exception:
        pass
    # —— 收尾：删旧 exe 失败不阻塞（残留由下次更新开头清理） ——
    try:
        os.remove(os.path.join(INSTALL, "ClawAgent.old.exe"))
        log("old exe cleaned")
    except Exception as e:
        log(f"old exe cleanup skipped: {{type(e).__name__}}")
    try:
        os.remove(FLAG)
    except Exception:
        pass
    log("helper exit")


if __name__ == "__main__":
    main()
'''.format(
        install=str(exe_dir()),
        exe=str((exe_dir() / Path(sys.executable).name).resolve()) if getattr(sys, "frozen", False) else "",
        flag=str(Path(tempfile.gettempdir()) / "claw_update_ready.flag"),
        log=str(Path(tempfile.gettempdir()) / "claw_update_log.txt"),
    )


def apply(source: str) -> dict:
    """下载/复制新版本并就位，生成重启脚本并脱离启动；调用方随后退出进程。"""
    if not getattr(sys, "frozen", False):
        return {"ok": False, "error": "源码模式不支持应用更新（请重启源码进程）"}
    cur = current_version()
    try:
        remote = _fetch_source(source)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}
    rv = remote["version"]
    if str(rv.get("version") or "") == cur.get("version"):
        return {"ok": False, "error": f"已是最新版本（{cur.get('version')}）"}

    d = exe_dir()
    exe = Path(sys.executable).resolve()
    new_exe = d / "ClawAgent.new.exe"
    new_ver = d / "version.json.new"
    try:
        if remote["kind"] == "dir":
            shutil.copy2(remote["exe"], new_exe)
        else:
            import httpx

            r = httpx.get(remote["exe"], timeout=600, follow_redirects=True)
            r.raise_for_status()
            new_exe.write_bytes(r.content)
        new_ver.write_text(json.dumps(rv, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        return {"ok": False, "error": f"下载/复制失败：{type(e).__name__}: {e}"[:300]}

    # 清理旧标记；准备退出标记路径（EXIT_HOOK 会在退出前写它）
    flag = Path(tempfile.gettempdir()) / "claw_update_ready.flag"
    log = Path(tempfile.gettempdir()) / "claw_update_log.txt"
    try:
        flag.unlink(missing_ok=True)
    except Exception:
        pass
    global PENDING_EXIT_FLAG
    PENDING_EXIT_FLAG = flag

    # 首选：独立 Python 助手（逻辑 100% 可控）；无 Python 时回退 bat 朴素版
    helper = Path(tempfile.gettempdir()) / "claw_update_helper.py"
    helper.write_text(_helper_script(), encoding="utf-8")
    py = shutil.which("python") or shutil.which("python3")
    method = ""
    if py:
        subprocess.Popen([py, str(helper)], cwd=str(d),
                         creationflags=winproc.detached_flags(), close_fds=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        method = "python"
    else:
        bat = Path(tempfile.gettempdir()) / "claw_self_update.bat"
        pid = os.getpid()
        bat.write_text(
            "@echo off\r\n"
            f'cd /d "{d}"\r\n'
            f'echo start pid={pid} >> "{log}"\r\n'
            f'timeout /t 8 /nobreak >nul 2>nul\r\n'
            f'move /y "{exe.name}" "ClawAgent.old.exe" >> "{log}" 2>&1\r\n'
            f'move /y "ClawAgent.new.exe" "{exe.name}" >> "{log}" 2>&1\r\n'
            f'move /y "version.json.new" "version.json" >> "{log}" 2>&1\r\n'
            f'del "ClawAgent.old.exe" >nul 2>nul\r\n'
            f'start "" "{exe.name}"\r\n',
            encoding="utf-8",
        )
        subprocess.Popen(["cmd", "/c", str(bat)], cwd=str(d),
                         creationflags=winproc.detached_flags(), close_fds=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        method = "bat"
    return {"ok": True, "from": cur.get("version"), "to": str(rv.get("version") or ""),
            "restarting": True, "helper": str(helper), "method": method, "log": str(log)}


def schedule_exit(delay: float = 2.0) -> None:
    """响应送达后清理并退出进程（由更新脚本拉起新进程）。"""
    def _do():
        time.sleep(delay)
        try:
            if callable(EXIT_HOOK):
                EXIT_HOOK()
        except Exception:
            pass
        # 写退出标记：更新助手据此确认本进程已结束、开始交换
        try:
            if PENDING_EXIT_FLAG is not None:
                Path(PENDING_EXIT_FLAG).write_text(str(os.getpid()), encoding="utf-8")
        except Exception:
            pass
        os._exit(0)

    threading.Thread(target=_do, daemon=True).start()
