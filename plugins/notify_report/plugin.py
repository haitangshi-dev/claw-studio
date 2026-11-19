# -*- coding: utf-8 -*-
"""notify_report —— 把进度汇报发出去（邮箱 / 微信 / QQ / 桌面通知）。

为什么需要这个插件：打包版里 sys.executable 指向 ClawAgent.exe 本身，
直接用 skill_run 跑技能脚本会静默卡死。本插件自己定位真正的 python.exe，
再调用 skills/notify/run.py，因此在打包环境下也能稳定工作。
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

_PY = []


def _probe(exe):
    if not exe:
        return False
    try:
        p = subprocess.run([exe, "-c", "import sys;print(sys.version_info[0])"],
                           capture_output=True, timeout=15)
    except Exception:
        return False
    return p.returncode == 0 and p.stdout.decode("utf-8", "replace").strip().startswith("3")


def _find_python(explicit=None):
    """定位可用的 Python 解释器（带缓存）。"""
    if _PY:
        return _PY[0]
    cands = []
    if explicit:
        cands.append(str(explicit))
    if os.environ.get("CLAW_PYTHON"):
        cands.append(os.environ["CLAW_PYTHON"])
    for name in ("python", "python3"):
        w = shutil.which(name)
        if w and "WindowsApps" not in w:
            cands.append(w)
    local = os.environ.get("LOCALAPPDATA") or ""
    for base in (Path(local) / "Programs" / "Python", Path("C:/"), Path("C:/Program Files")):
        if base.is_dir():
            try:
                for d in sorted(base.glob("Python3*"), reverse=True):
                    cands.append(str(d / "python.exe"))
            except Exception:
                pass
            cands.append(str(base / "python.exe"))
    for name in ("python", "python3"):
        w = shutil.which(name)
        if w:
            cands.append(w)
    seen = set()
    for c in cands:
        k = str(c).lower()
        if k in seen:
            continue
        seen.add(k)
        if _probe(c):
            _PY.append(c)
            return c
    return None


def _script_path(cfg, workdir):
    roots = []
    if cfg.get("_project_root"):
        roots.append(Path(str(cfg["_project_root"])))
    if os.environ.get("CLAW_SKILL_DIR"):
        roots.append(Path(os.environ["CLAW_SKILL_DIR"]).parent)
    if workdir:
        roots.append(Path(str(workdir)))
    roots.append(Path(__file__).resolve().parent.parent)
    for r in roots:
        p = r / "skills" / "notify" / "run.py"
        if p.is_file():
            return p
    return None


def notify_report(args, workdir, cfg):
    msg = str(args.get("message") or "").strip()
    if not msg:
        return "错误：缺少 message（要汇报的正文）"

    script = _script_path(cfg, workdir)
    if not script:
        return ("错误：找不到 skills/notify/run.py。"
                "请确认 notify 技能已创建（skills/notify/run.py）。")

    payload = {"message": msg}
    for k in ("subject", "channel"):
        if args.get(k):
            payload[k] = args[k]

    py = _find_python(cfg.get("python_exe"))
    if not py:
        return ("错误：找不到可用的 Python 解释器（技能脚本需要 python.exe）。"
                "请在 config.json 的 agent.python_exe 里指定完整路径。")

    try:
        p = subprocess.run(
            [py, str(script)],
            input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            capture_output=True, timeout=180, cwd=str(workdir or script.parent),
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
    except subprocess.TimeoutExpired:
        return "错误：notify 技能执行超时（>180s）"
    except Exception as e:
        return "错误：%s: %s" % (type(e).__name__, e)

    out = p.stdout.decode("utf-8", "replace").strip()
    err = p.stderr.decode("utf-8", "replace").strip()
    if out:
        return out
    return "错误：技能无输出（rc=%s）%s" % (p.returncode, ("\n" + err[:300]) if err else "")


def register(api):
    api.register_tool(
        {
            "type": "function",
            "function": {
                "name": "notify_report",
                "description": (
                    "把进度/结果汇报发给用户（默认发邮箱，也可发微信/QQ，并同时写本地留档）。"
                    "任务做完、告一段落、或需要用户知情时调用。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "message": {"type": "string", "description": "汇报正文，支持多行"},
                        "subject": {"type": "string", "description": "标题（可选）"},
                        "channel": {
                            "type": "string",
                            "description": "auto（默认，发所有已启用的通道）| agentmail（邮箱，推荐）| mail | pushplus | serverchan | qqbot | local",
                        },
                    },
                    "required": ["message"],
                },
            },
        },
        notify_report,
    )
