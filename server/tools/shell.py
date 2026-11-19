# -*- coding: utf-8 -*-
"""run_shell 工具：执行 shell 命令。

- shell_mode: "bash"（默认，Git Bash：ls/mkdir -p/mv/cp/rm 等 Unix 命令）
             | "cmd"（cmd.exe）
- shell_guard: "block" 时命中危险命令黑名单直接拒绝（返回给模型，不执行）
- Windows 编码兼容：UTF-8 / GB18030 智能解码
- 超时保护：默认 60s，上限 600s
- 无窗口：宿主是 --windowed 的 GUI 进程（无控制台），子进程若不显式
  指定 CREATE_NO_WINDOW，Windows 会给每个 bash.exe/cmd.exe 新开一个
  控制台窗口 —— 用户每看一次工具调用就闪一个黑框。所以统一走 _run()。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

# Windows 上不弹控制台窗口（GUI 宿主必需）；非 Windows 退化为 0
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


def _run(args, **kw):
    """统一的子进程入口：确保不弹出控制台窗口。"""
    kw.setdefault("creationflags", _NO_WINDOW)
    return subprocess.run(args, **kw)

SHELL_SPEC = {
    "type": "function",
    "function": {
        "name": "run_shell",
        "description": "执行一条 shell 命令并返回输出（Git Bash 环境：支持 ls -la、mkdir -p、mv、cp、rm、find、grep 等；"
                       "如需 Windows 原生命令可用 cmd.exe /c \"...\"）。用于列目录、移动/复制/删除文件、运行程序等。",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的完整命令"},
                "cwd": {"type": "string", "description": "命令的工作目录（可选；默认会话工作目录）"},
                "timeout": {"type": "integer", "description": "超时秒数（可选，默认 60）"},
                "background": {
                    "type": "boolean",
                    "description": "true = 后台启动并立刻返回（不等待、不阻塞）。"
                                   "启动服务器/常驻监听/守护进程/长时间任务时必须用 true，"
                                   "否则会一直占用到超时。返回里给出 PID 与日志路径，"
                                   "之后用 shell_jobs 查看输出或结束进程。",
                },
                "log": {"type": "string", "description": "后台模式下日志文件名（可选）"},
            },
            "required": ["command"],
        },
    },
}

# 危险命令黑名单（保守，只拦真正灾难性的模式）
_DANGEROUS_PATTERNS = [
    r"rm\s+(-[a-z]*\s+)*-[a-z]*r[a-z]*f?[a-z]*\s+(/|~|\$HOME|[a-z]:\\)",   # rm -rf / 或 ~ 或盘根
    r"rm\s+-[a-z]*f[a-z]*r[a-z]*\s+(/|~|[a-z]:\\)",
    r"del\s+/[a-z]*q[a-z]*\s+[a-z]:\\?\s*$",                                 # del /q C:\
    r"del\s+/[a-z]*s[a-z]*\s+/[a-z]*q[a-z]*\s+[a-z]:\\",
    r"format\s+[a-z]:",                                                       # format C:
    r"diskpart", r"vssadmin", r"bcdedit",
    r"shutdown(\.exe)?\s", 
    r"reg\s+delete",
    r"remove-item\s+.*-recurse.*-force\s+[a-z]:\\?\s*$",                      # PS 全盘删
    r":\(\)\s*\{\s*:\|:&\s*\};:",                                             # fork bomb
]
_DANGEROUS_RE = [re.compile(p, re.IGNORECASE) for p in _DANGEROUS_PATTERNS]


def is_dangerous(command: str) -> str | None:
    """命中返回命中的模式描述，否则 None。"""
    for pat in _DANGEROUS_RE:
        if pat.search(command):
            return pat.pattern
    return None


# 只读模式允许的命令白名单（按 && ; | 分段后检查每段首词）
# 注意：awk/env/date 等可执行子命令或改系统时间的工具不在白名单内。
_READONLY_CMDS = {
    "ls", "dir", "cat", "type", "pwd", "find", "grep", "head", "tail", "wc", "stat", "file",
    "echo", "which", "where", "systeminfo", "ipconfig", "tasklist", "ver", "hostname", "whoami",
    "tree", "more", "less", "du", "df", "uname", "nproc", "printenv",
    "sort", "uniq", "cut", "comm", "diff", "md5sum", "sha256sum", "realpath",
}

# 只读模式额外拒绝的「看起来无害但能写/执行」的参数
_READONLY_DENY_RE = re.compile(
    r"(\bfind\b[^|;&]*(?:-exec|-execdir|-ok|-okdir|-delete|-fprint|-fls)\b"
    r"|\bsort\b[^|;&]*\s-o\b"
    r"|\$\(|`|<\(|>\s*\(|/dev/tcp|/dev/udp)",
    re.IGNORECASE,
)


def is_readonly_command(command: str) -> bool:
    """命令是否完全由只读片段组成（只读权限下使用）。"""
    import re as _re

    if ">" in command or "<" in command:
        return False  # 重定向可能写文件/读任意输入
    if _READONLY_DENY_RE.search(command):
        return False
    parts = _re.split(r"&&|\|\||;|\|", command)
    for part in parts:
        words = part.strip().split()
        if not words:
            continue
        head = words[0].lower()
        if head.endswith(".exe"):
            head = head[:-4]
        if head not in _READONLY_CMDS:
            return False
    return True


def _smart_decode(b: bytes) -> str:
    for enc in ("utf-8", "gb18030"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", "replace")


def _resolve_cwd(cwd: str | None, workdir: str) -> Path:
    if not cwd:
        p = Path(workdir)
    else:
        p = Path(cwd)
        if not p.is_absolute():
            p = Path(workdir) / cwd
    p = p.resolve()
    if not p.is_dir():
        raise FileNotFoundError(f"工作目录不存在：{p}")
    return p


def _find_bash() -> str | None:
    exe = shutil.which("bash")
    if exe:
        return exe
    for cand in (r"C:\Program Files\Git\bin\bash.exe",
                 r"C:\Program Files (x86)\Git\bin\bash.exe"):
        if Path(cand).exists():
            return cand
    return None


# ---------- 后台任务 ----------
_JOBS: dict[int, dict] = {}
_KILLABLE_IMAGES = {"python.exe", "pythonw.exe", "node.exe", "bash.exe", "sh.exe",
                    "ssh.exe", "cmd.exe", "java.exe"}


def _jobs_file(workdir: str) -> Path:
    return Path(workdir) / ".claw_jobs.json"


def _save_jobs(workdir: str) -> None:
    try:
        data = {str(pid): {k: v for k, v in j.items() if k != "proc"}
                for pid, j in _JOBS.items()}
        _jobs_file(workdir).write_text(json.dumps(data, ensure_ascii=False, indent=1),
                                      encoding="utf-8")
    except Exception:
        pass


def _load_jobs(workdir: str) -> dict:
    try:
        return json.loads(_jobs_file(workdir).read_text(encoding="utf-8"))
    except Exception:
        return {}


def _pid_alive(pid: int) -> bool:
    try:
        r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                           capture_output=True, text=True, timeout=15,
                           creationflags=_NO_WINDOW)
        return str(pid) in (r.stdout or "")
    except Exception:
        return False


def _pid_image(pid: int) -> str:
    try:
        r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                           capture_output=True, text=True, timeout=15,
                           creationflags=_NO_WINDOW)
        line = (r.stdout or "").strip().splitlines()
        if line and line[0].startswith('"'):
            return line[0].split('","')[0].strip('"')
    except Exception:
        pass
    return ""


def _kill_pid(pid: int) -> bool:
    try:
        return subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                              capture_output=True, text=True, timeout=20,
                              creationflags=_NO_WINDOW).returncode == 0
    except Exception:
        return False


def _extract_ports(command: str) -> set[int]:
    """从命令里猜出「本进程要监听」的端口（用于启动前清理自家残留）。

    注意：ssh 的 -R/-L 转发里也写端口，但那是转发目标不是本地监听，
    清理它会误杀被转发的服务，所以直接跳过隧道类命令。
    """
    if re.search(r"\bssh\b", command) and re.search(r"-[RL]\s", command):
        return set()
    ports: set[int] = set()
    for m in re.finditer(r"(?:--port[= ]|port[= ]|:|-p\s)(\d{2,5})\b", command):
        try:
            p = int(m.group(1))
        except ValueError:
            continue
        if 1024 <= p <= 65535:
            ports.add(p)
    return ports


def _port_pid(port: int) -> int | None:
    try:
        r = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                           timeout=25, creationflags=_NO_WINDOW)
        for line in (r.stdout or "").splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[3] == "LISTENING" and parts[1].endswith(f":{port}"):
                return int(parts[4])
    except Exception:
        pass
    return None


def _start_background(command: str, cwd: Path, mode: str, workdir: str,
                      log_name: str | None = None) -> str:
    """后台启动：立刻返回 PID 与日志路径，不等待命令结束。"""
    notes: list[str] = []
    # 启动前清理同端口的自家残留（否则新进程会因端口占用而悄悄退出）
    for port in sorted(_extract_ports(command)):
        pid = _port_pid(port)
        if not pid:
            continue
        img = _pid_image(pid)
        if img.lower() in _KILLABLE_IMAGES:
            _kill_pid(pid)
            time.sleep(0.8)
            if _port_pid(port):
                notes.append(f"端口 {port} 原由 {img}(PID {pid}) 占用，已尝试终止但仍被占用")
            else:
                notes.append(f"已清理端口 {port} 上的旧进程 {img}(PID {pid})")
        else:
            notes.append(f"警告：端口 {port} 被 {img or '未知进程'}(PID {pid}) 占用，"
                         f"新进程可能启动失败")

    logdir = Path(workdir) / ".claw_logs"
    logdir.mkdir(parents=True, exist_ok=True)
    logpath = logdir / (log_name or f"job_{time.strftime('%m%d_%H%M%S')}.log")

    env = dict(os.environ)
    env["MSYS_NO_PATHCONV"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    flags = 0
    if sys.platform == "win32":
        flags = 0x00000008 | 0x00000200   # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    fh = open(logpath, "wb")
    try:
        if mode == "bash":
            bash_exe = _find_bash()
            if not bash_exe:
                return "启动失败：未找到 Git Bash（bash.exe）"
            proc = subprocess.Popen([bash_exe, "-lc", command], cwd=str(cwd),
                                    stdout=fh, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, env=env, creationflags=flags)
        else:
            proc = subprocess.Popen(command, cwd=str(cwd), shell=True,
                                    stdout=fh, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, env=env, creationflags=flags)
    except Exception as e:
        return f"启动失败：{e}"

    time.sleep(1.2)
    alive = proc.poll() is None
    _JOBS[proc.pid] = {"pid": proc.pid, "command": command, "log": str(logpath),
                       "cwd": str(cwd), "started": time.strftime("%Y-%m-%d %H:%M:%S")}
    _save_jobs(workdir)

    try:
        head = logpath.read_bytes()[:700].decode("utf-8", "replace").strip()
    except Exception:
        head = ""

    lines = [f"已在后台启动  PID={proc.pid}  存活={'是' if alive else '否（已退出）'}",
             f"命令: {command[:200]}",
             f"日志: {logpath}"]
    if notes:
        lines.append("端口处理: " + "；".join(notes))
    lines.append("--- 日志开头 ---")
    lines.append(head if head else "(暂无输出)")
    lines.append(f"--- 用 shell_jobs 查看后续输出（pid={proc.pid}），"
                 f"或 background=true 之前先确认端口空闲 ---")
    return "\n".join(lines)


JOBS_SPEC = {
    "type": "function",
    "function": {
        "name": "shell_jobs",
        "description": "管理后台任务（配合 run_shell 的 background=true）。"
                       "action=list 列出；output 看日志（pid 或 log 指定）；"
                       "kill 结束进程；kill_port 按端口结束占用进程。",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "list | output | kill | kill_port"},
                "pid": {"type": "integer", "description": "进程号（output/kill 用）"},
                "port": {"type": "integer", "description": "端口（kill_port 用）"},
                "log": {"type": "string", "description": "日志文件路径（output 用，可选）"},
                "tail": {"type": "integer", "description": "output 返回末尾字符数（默认 2000）"},
            },
            "required": ["action"],
        },
    },
}


def shell_jobs(args: dict, workdir: str, cfg: dict) -> str:
    action = str(args.get("action") or "list").lower()
    saved = _load_jobs(workdir)

    if action == "list":
        rows = []
        for pid_s, j in saved.items():
            pid = int(pid_s)
            rows.append(f"  PID {pid:<7} 存活={'是' if _pid_alive(pid) else '否'}"
                        f"  {j.get('started','')}  {(j.get('command') or '')[:70]}")
        return "后台任务：\n" + ("\n".join(rows) if rows else "  (无)")

    if action == "output":
        pid = args.get("pid")
        log = args.get("log")
        if not log and pid is not None:
            log = (saved.get(str(int(pid))) or {}).get("log")
        if not log:
            return "需要 pid 或 log 参数（先 shell_jobs action=list 看有哪些）"
        p = Path(log)
        if not p.is_absolute():
            p = Path(workdir) / log
        if not p.exists():
            return f"日志不存在：{p}"
        n = int(args.get("tail") or 2000)
        text = p.read_text(encoding="utf-8", errors="replace")
        alive = _pid_alive(int(pid)) if pid is not None else None
        head = f"日志 {p}（{len(text)} 字节）"
        if alive is not None:
            head += f"  进程存活={'是' if alive else '否'}"
        return head + "\n--- 末尾 ---\n" + text[-n:]

    if action == "kill":
        pid = args.get("pid")
        if pid is None:
            return "需要 pid"
        pid = int(pid)
        ok = _kill_pid(pid)
        _JOBS.pop(pid, None)
        saved.pop(str(pid), None)
        try:
            _jobs_file(workdir).write_text(json.dumps(saved, ensure_ascii=False, indent=1),
                                          encoding="utf-8")
        except Exception:
            pass
        return f"PID {pid} {'已结束' if ok else '结束失败（可能已不存在）'}"

    if action == "kill_port":
        port = args.get("port")
        if port is None:
            return "需要 port"
        pid = _port_pid(int(port))
        if not pid:
            return f"端口 {port} 当前没有监听进程"
        img = _pid_image(pid)
        ok = _kill_pid(pid)
        return f"端口 {port} 由 {img or '未知'}(PID {pid}) 占用，{'已结束' if ok else '结束失败'}"

    return "未知 action，可用：list / output / kill / kill_port"


def run_shell(args: dict, workdir: str, cfg: dict) -> str:
    command = str(args.get("command") or "").strip()
    if not command:
        raise ValueError("缺少 command 参数")

    if cfg.get("shell_guard", "block") == "block":
        hit = is_dangerous(command)
        if hit:
            return (f"[安全策略] 该命令命中危险模式，已被拒绝执行：{hit}\n"
                    f"如果这是误判，请换一种更安全的等价写法。")

    cwd = _resolve_cwd(args.get("cwd"), workdir)
    try:
        timeout = int(args.get("timeout") or 60)
    except (TypeError, ValueError):
        timeout = 60
    timeout = max(1, min(timeout, 600))

    mode = cfg.get("shell_mode", "bash")

    # 后台模式：立刻返回，不阻塞。用于服务器 / 常驻监听 / 长任务。
    if args.get("background"):
        return _start_background(command, cwd, mode, workdir, args.get("log"))

    if mode == "bash":
        bash_exe = _find_bash()
        if not bash_exe:
            raise FileNotFoundError("未找到 Git Bash（bash.exe）；可在 config.json 把 shell_mode 改为 cmd")
        # MSYS_NO_PATHCONV：防止 Git Bash 把 cmd.exe /c 的 "/c" 转换成 "C:/"
        env = dict(os.environ)
        env["MSYS_NO_PATHCONV"] = "1"
        proc = _run([bash_exe, "-lc", command], cwd=str(cwd),
                    capture_output=True, timeout=timeout, env=env)
    else:
        proc = _run(command, shell=True, cwd=str(cwd),
                    capture_output=True, timeout=timeout)

    out = _smart_decode(proc.stdout).rstrip()
    err = _smart_decode(proc.stderr).rstrip()
    parts = [f"exit_code: {proc.returncode}"]
    parts.append(f"--- stdout ---\n{out}" if out else "--- stdout ---\n(空)")
    if err:
        parts.append(f"--- stderr ---\n{err}")
    # cd 常见坑（2B 模型高频）：未加引号的含空格路径、意图去不存在的老路径
    if proc.returncode != 0 and ("cd: too many arguments" in err or ("cd: " in err and "No such file" in err)):
        parts.append(
            "提示：命令已在会话工作目录中执行，可直接用相对路径，"
            "如 python -m py_compile utils.py；若需 cd 到含空格的路径，请用双引号包住"
        )
    # 路径引号坑（2B 模型高频）：Git Bash 中裸写的路径若含空格或单引号，会被 shell 拆词/吞掉
    if (proc.returncode != 0 and "No such file or directory" in err
            and re.search(r"(/c/|[A-Za-z]:[\\/])", command)):
        parts.append(
            "提示：命令行里的路径请用双引号包住（例如 \"/c/path/to/app\"），"
            "以免路径中的空格或引号被 shell 当作语法处理；"
            "当前就在会话工作目录，也可直接用相对路径（如 hello_msg.py）"
        )
    return "\n".join(parts)
