# -*- coding: utf-8 -*-
"""Skill 系统：skills/<name>/ 目录式技能（可被模型创建/运行/管理）。

结构（对齐社区惯例与 P6 设计）：
  skills/<name>/skill.json   {name, description, created_at, author}
  skills/<name>/SKILL.md     使用说明（给人和模型看）
  skills/<name>/run.py       执行入口：stdin 读 JSON 参数，stdout 输出结果

约定：run.py 用 python 解释器执行，cwd = 会话工作目录，环境变量 CLAW_SKILL_DIR 指向技能目录。
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

import winproc as _winproc  # 子进程统一不弹控制台窗口（Windows GUI 宿主必需）

_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,39}$")

# ---- Python 解释器定位 ----
# 打包（PyInstaller）后 sys.executable 指向 ClawAgent.exe 本身，
# 直接拿它跑 .py 会变成"用应用打开脚本"→ 静默挂死（skill_run 超时的根因）。
# 因此这里显式、可验证地找一个真正的 python.exe。
_PY_CACHE: list[str] = []


def _probe_python(exe: str) -> bool:
    """验证这个可执行文件确实是能用的 Python 3（WindowsApps 商店桩会静默失败）。"""
    if not exe:
        return False
    try:
        p = _winproc.run([exe, "-c", "import sys;print(sys.version_info[0])"],
                         capture_output=True, timeout=15)
    except Exception:
        return False
    return p.returncode == 0 and p.stdout.decode("utf-8", "replace").strip().startswith("3")


def find_python(explicit: str | None = None) -> str:
    """定位可用的 Python 解释器（带验证与缓存）。

    顺序：显式参数 → 环境变量 CLAW_PYTHON → PATH 里的 python/python3 →
    常见安装目录 → 注册表 → 非打包模式下的 sys.executable。
    """
    if _PY_CACHE:
        return _PY_CACHE[0]

    cands: list[str] = []
    if explicit:
        cands.append(str(explicit))
    env_py = os.environ.get("CLAW_PYTHON")
    if env_py:
        cands.append(env_py)

    # PATH（跳过 WindowsApps 商店桩：探针会把它筛掉）
    for name in ("python", "python3"):
        w = shutil.which(name)
        if w and "WindowsApps" not in w:
            cands.append(w)

    # 常见安装目录
    roots = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Python",
        Path("C:/Python313"), Path("C:/Python312"), Path("C:/Python311"),
        Path("C:/Python310"), Path("C:/Python39"),
        Path("C:/Program Files/Python313"), Path("C:/Program Files/Python312"),
        Path("C:/Program Files/Python311"), Path("C:/Program Files/Python310"),
    ]
    for r in roots:
        if r.is_dir():
            for d in sorted(r.glob("Python3*"), reverse=True):
                cands.append(str(d / "python.exe"))
            cands.append(str(r / "python.exe"))

    # PATH 里剩下的（含商店桩，探针会筛掉）
    for name in ("python", "python3"):
        w = shutil.which(name)
        if w:
            cands.append(w)

    if not getattr(sys, "frozen", False) and sys.executable:
        cands.append(sys.executable)

    seen = set()
    for c in cands:
        key = str(c).lower()
        if not c or key in seen:
            continue
        seen.add(key)
        if _probe_python(c):
            _PY_CACHE.append(c)
            return c

    raise FileNotFoundError(
        "未找到可用的 Python 解释器（技能脚本需要 python.exe 运行）。"
        "请在 config.json 的 agent.python_exe 指定完整路径，或把 python 加入 PATH。"
    )

SKILL_TEMPLATE = '''# -*- coding: utf-8 -*-
"""技能入口脚本：从 stdin 读 JSON 参数，结果 print 到 stdout。"""
import json
import sys

args = {}
try:
    raw = sys.stdin.read().strip()
    if raw:
        args = json.loads(raw)
except Exception:
    pass

# TODO: 在这里实现技能逻辑（可 import 标准库/第三方库、读写文件、跑命令）
print(json.dumps({"ok": True, "echo": args}, ensure_ascii=False))
'''


def skills_root(data_root: Path) -> Path:
    root = Path(data_root) / "skills"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _skill_dir(data_root: Path, name: str) -> Path:
    name = (name or "").strip()
    if not _NAME_RE.match(name):
        raise ValueError(f"技能名不合法：{name!r}（只允许字母/数字/下划线/连字符，≤40 字符）")
    d = skills_root(data_root) / name
    if skills_root(data_root).resolve() not in d.resolve().parents:
        raise ValueError("路径越权")
    return d


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def scan(data_root: Path) -> list[dict]:
    """扫描全部技能（按名称排序）。"""
    root = skills_root(data_root)
    out = []
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        meta = _read_json(d / "skill.json")
        out.append({
            "name": d.name,
            "description": str(meta.get("description") or ""),
            "created_at": str(meta.get("created_at") or ""),
            "author": str(meta.get("author") or ""),
            "has_script": (d / "run.py").is_file(),
            "path": str(d),
        })
    return out


def load(data_root: Path, name: str) -> dict:
    d = _skill_dir(data_root, name)
    if not d.is_dir():
        raise FileNotFoundError(f"技能不存在：{name}")
    meta = _read_json(d / "skill.json")
    md = ""
    try:
        md = (d / "SKILL.md").read_text(encoding="utf-8")
    except OSError:
        pass
    script = ""
    try:
        script = (d / "run.py").read_text(encoding="utf-8")
    except OSError:
        pass
    return {"name": name, "meta": meta, "skill_md": md, "script": script, "path": str(d)}


def create(data_root: Path, name: str, description: str, skill_md: str = "", script: str = "",
           author: str = "agent", overwrite: bool = False) -> dict:
    d = _skill_dir(data_root, name)
    if d.exists() and not overwrite:
        raise FileExistsError(f"技能已存在：{name}（如需覆盖请传 overwrite=true）")
    d.mkdir(parents=True, exist_ok=True)
    (d / "skill.json").write_text(json.dumps({
        "name": name,
        "description": description,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "author": author,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    if skill_md:
        (d / "SKILL.md").write_text(skill_md, encoding="utf-8")
    (d / "run.py").write_text(script or SKILL_TEMPLATE, encoding="utf-8")
    return load(data_root, name)


def delete(data_root: Path, name: str) -> None:
    d = _skill_dir(data_root, name)
    if not d.is_dir():
        raise FileNotFoundError(f"技能不存在：{name}")
    shutil.rmtree(d)


def run(data_root: Path, name: str, args: dict | None, workdir: str, timeout: int = 120,
        python_exe: str | None = None) -> str:
    d = _skill_dir(data_root, name)
    script = d / "run.py"
    if not script.is_file():
        raise FileNotFoundError(f"技能 {name} 没有 run.py")
    try:
        py = find_python(python_exe)
        proc = _winproc.run(
            [py, str(script)],
            # 字节方式传参：UTF-8 编码写 stdin，避免 Windows 本地编码（GBK）歧义；
            # 子进程侧配合 PYTHONIOENCODING=utf-8，中文参数双向无损
            input=json.dumps(args or {}, ensure_ascii=False).encode("utf-8"),
            capture_output=True, timeout=max(5, min(int(timeout), 600)),
            cwd=str(workdir),
            env={**os.environ, "CLAW_SKILL_DIR": str(d), "PYTHONIOENCODING": "utf-8"},
        )
    except FileNotFoundError as e:
        return f"技能 {name} 无法启动：{e}"
    except subprocess.TimeoutExpired:
        return f"技能 {name} 执行超时（>{timeout}s）已终止；脚本卡住请检查是否有等待输入/无限循环"
    out = proc.stdout.decode("utf-8", "replace").strip()
    err = proc.stderr.decode("utf-8", "replace").strip()
    parts = [f"exit_code: {proc.returncode}"]
    parts.append(f"--- stdout ---\n{out}" if out else "--- stdout ---\n(空)")
    if err:
        parts.append(f"--- stderr ---\n{err[:1500]}")
    return "\n".join(parts)
