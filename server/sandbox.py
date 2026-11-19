# -*- coding: utf-8 -*-
"""工具层三档权限 + 工作目录沙箱。

档位（与前端 PermMenu 对应）：
  readonly  只读：不写文件；shell 仅允许只读白名单命令。
  standard  标准：文件写入限制在当前会话工作目录内（沙箱）；危险命令仍拦截。
  full      完全：不设路径沙箱、不拦危险命令（仍写审计）。

说明：这是工具层沙箱（文件工具精确校验路径；shell 拦截越界写路径与越界 cwd），
不是操作系统级容器。脚本解释器内部的任意写操作无法在没有受限令牌/容器的前提下
完全拦住——需要绝对隔离时请把工作目录本身放到虚拟机/容器里。
"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

PERMISSIONS = ("readonly", "ask", "standard", "full")

PERMISSION_LEVELS = [
    {
        "key": "readonly",
        "label": "只读",
        "desc": "不写文件；命令仅允许只读白名单",
        "sandbox": "readonly",
    },
    {
        "key": "ask",
        "label": "询问",
        "desc": "任何有副作用的操作都先问你（默认档）",
        "sandbox": "ask",
    },
    {
        "key": "standard",
        "label": "标准",
        "desc": "沙箱：写入限工作目录，危险命令拦截",
        "sandbox": "workspace",
    },
    {
        "key": "full",
        "label": "完全",
        "desc": "无沙箱：全盘读写，危险命令放行",
        "sandbox": "off",
    },
]

# 写类文件工具及其需要校验的路径参数
_WRITE_PATH_ARGS = {
    "file_create": ("path",),
    "file_edit": ("path",),
    "file_delete": ("path",),
    "file_move": ("source", "target"),
    "file_copy": ("target",),  # 允许从目录外读入，不允许写出目录外
}

# 明显的写命令：出现绝对路径时检查是否越界
_WRITE_CMDS = {
    "rm", "rmdir", "mv", "cp", "mkdir", "touch", "chmod", "chown", "chgrp",
    "ln", "tee", "dd", "truncate", "install", "patch", "unzip", "tar",
    "del", "erase", "copy", "move", "ren", "rename", "md", "rd", "attrib",
    "icacls", "takeown", "mklink", "xcopy", "robocopy",
}
_WRITE_SEG_RE = re.compile(
    r"(?:^|[;&|]\s*|\b(?:cmd|powershell|pwsh)(?:\.exe)?\s+(?:/[a-z]\s+)*)"
    r"(?:" + "|".join(sorted(_WRITE_CMDS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)
_ABS_PATH_RE = re.compile(
    r"""(?:
        [A-Za-z]:[\\/][^"'|;&\s]*          # C:\dir  C:/dir
      | \\\\[^"'|;&\s]+                    # \\server\share
      | /cygdrive/[A-Za-z]/[^"'|;&\s]*      # MSYS
      | /[A-Za-z]/[^"'|;&\s]*              # /c/dir
      | (?<![\w/])/(?!/)[^"'|;&\s]+        # 其它 POSIX 绝对路径
    )""",
    re.VERBOSE,
)
_REDIRECT_RE = re.compile(r"(?:^|[^>])>{1,2}\s*(\"[^\"]+\"|'[^']+'|[^\s&|;]+)")
_CD_RE = re.compile(r"(?:^|[;&|]\s*)cd\s+(\"[^\"]+\"|'[^']+'|[^\s;&|]+)", re.IGNORECASE)
_SAFE_SINKS = {"/dev/null", "/dev/stdout", "/dev/stderr", "nul", "nul:", "con"}


def normalize_permission(value: str | None) -> str:
    v = str(value or "standard").strip().lower()
    if v in ("readonly", "read-only", "read", "只读"):
        return "readonly"
    if v in ("ask", "confirm", "询问", "逐次确认"):
        return "ask"
    if v in ("full", "off", "none", "完全", "全权"):
        return "full"
    return "standard"


def _norm(path: str | Path) -> str:
    try:
        return os.path.normcase(os.path.normpath(str(path)))
    except Exception:
        return str(path)


def is_within(child: str | Path, root: str | Path) -> bool:
    """child 是否位于 root 内（Windows 大小写不敏感）。"""
    try:
        c = _norm(Path(child).resolve())
        r = _norm(Path(root).resolve())
        return c == r or c.startswith(r.rstrip("\\/") + os.sep)
    except Exception:
        return False


def _resolve(path: str, workdir: str) -> Path:
    p = Path(str(path or "")).expanduser()
    if not p.is_absolute():
        p = Path(workdir) / p
    return p.resolve()


def _bash_to_local(raw: str) -> Path | None:
    """把 Git Bash 风格路径转成本地 Path；无法识别返回 None。"""
    s = raw.strip().strip('"').strip("'")
    if not s:
        return None
    low = s.lower()
    if low in _SAFE_SINKS:
        return None
    if low.startswith("/cygdrive/") and len(s) > 11:
        drive = s[10]
        rest = s[11:].lstrip("/")
        return Path(f"{drive.upper()}:/{rest}")
    if re.match(r"^/[A-Za-z]/", s) and len(s) > 3:
        return Path(f"{s[1].upper()}:/{s[3:]}")
    if re.match(r"^[A-Za-z]:[\\/]", s) or s.startswith("\\\\"):
        return Path(s)
    if s.startswith("/"):
        return Path(s)
    return None


def _allowed_write(path: Path, workdir: str) -> bool:
    if is_within(path, workdir):
        return True
    # 临时目录属于常见安全落点，避免工具链因 TMP 写入被误伤
    try:
        return is_within(path, tempfile.gettempdir())
    except Exception:
        return False


def _outside(path: str | Path, workdir: str, action: str = "写入") -> tuple[bool, str]:
    msg = (f"[沙箱] 标准权限下只允许{action}当前工作目录（或系统临时目录）内的文件，"
           f"已拦截越界路径：{path}\n工作目录：{workdir}\n"
           f"如确需访问该位置，请让用户把权限切到「完全」。")
    return False, msg


def check_tool(name: str, args: dict, workdir: str, permission: str) -> tuple[bool, str]:
    """执行前的沙箱检查。返回 (allowed, message)。"""
    perm = normalize_permission(permission)
    if perm == "full":
        return True, ""
    if perm == "readonly":
        # 写类工具由 tools/__init__ 的只读白名单统一拦截；这里只兜底文件路径
        return True, ""

    path_args = _WRITE_PATH_ARGS.get(name)
    if path_args:
        for key in path_args:
            raw = str((args or {}).get(key) or "").strip()
            if not raw:
                continue
            p = _resolve(raw, workdir)
            if key == "source" and name == "file_copy":
                continue  # 复制来源允许在目录外（只读）
            if not _allowed_write(p, workdir):
                return _outside(p, workdir)

    if name == "run_shell":
        cmd = str((args or {}).get("command") or "")
        cwd = str((args or {}).get("cwd") or "").strip()
        if cwd:
            p = _resolve(cwd, workdir)
            if not is_within(p, workdir):
                return _outside(p, workdir, "在")
        if cmd:
            return check_shell_command(cmd, workdir)
    return True, ""


def check_shell_command(command: str, workdir: str) -> tuple[bool, str]:
    """标准权限下的 shell 路径沙箱：拦截越界 cwd / 重定向 / 写命令绝对路径。"""
    wd = Path(workdir).resolve()

    for m in _CD_RE.finditer(command or ""):
        raw = m.group(1).strip().strip('"').strip("'")
        p = _bash_to_local(raw)
        if p is None:
            continue
        if not p.is_absolute():
            p = wd / p
        p = p.resolve()
        if not is_within(p, wd):
            return _outside(p, str(wd), "切换到")

    for m in _REDIRECT_RE.finditer(command or ""):
        raw = m.group(1).strip().strip('"').strip("'")
        if raw.lower() in _SAFE_SINKS or raw.startswith("/dev/"):
            continue
        p = _bash_to_local(raw)
        if p is None:
            p = wd / raw
        p = p.resolve()
        if not _allowed_write(p, str(wd)):
            return _outside(p, str(wd))

    # 写命令出现绝对路径：例如 rm -rf C:\Windows\... / cp a.txt D:\backup\
    if _WRITE_SEG_RE.search(command or ""):
        for m in _ABS_PATH_RE.finditer(command or ""):
            p = _bash_to_local(m.group(0))
            if p is None:
                continue
            if not p.is_absolute():
                continue
            p = p.resolve()
            if not _allowed_write(p, str(wd)):
                return _outside(p, str(wd))
    return True, ""
