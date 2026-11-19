# -*- coding: utf-8 -*-
"""内置工具族：规格（OpenAI tools 格式）+ 统一执行入口 + 审计日志。

工具列表（P2）：
  run_shell / file_read / file_create / file_edit / file_delete / file_search
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .shell import SHELL_SPEC, JOBS_SPEC, run_shell, shell_jobs
from .files import (
    FILE_SPECS,
    file_read,
    file_create,
    file_edit,
    file_delete,
    file_search,
    file_move,
    file_copy,
)
from .memory_tools import (
    MEMORY_SPECS,
    memory_write,
    memory_search,
    memory_forget,
    memory_get,
    memory_stats,
    memory_tags,
    memory_forget_candidates,
    memory_demote,
    memory_pin,
    memory_promote,
)
from .browser_tool import BROWSER_SPEC, browser_control
from .skill_tools import (
    SKILL_SPECS,
    skill_list,
    skill_read,
    skill_create,
    skill_run,
    skill_delete,
)
from .plugin_tools import (
    PLUGIN_SPECS,
    plugin_list,
    plugin_create,
    plugin_reload,
    plugin_toggle,
    plugin_delete,
)
from .update_tool import UPDATE_SPECS, framework_update
from plan import SPEC as PLAN_SPEC, handler as plan_handler

PROJECT_ROOT = Path(__file__).resolve().parents[2]

TOOL_SPECS: list[dict] = ([SHELL_SPEC, JOBS_SPEC] + FILE_SPECS + MEMORY_SPECS + [BROWSER_SPEC]
                          + SKILL_SPECS + PLUGIN_SPECS + UPDATE_SPECS + [PLAN_SPEC])

# 参数 schema 提示（供宽松类型强转用）：{tool: {param: {"type": ...}}}
SCHEMA_HINTS: dict[str, dict] = {
    t["function"]["name"]: t["function"]["parameters"].get("properties", {})
    for t in TOOL_SPECS
}

_HANDLERS = {
    "task_plan": plan_handler,
    "run_shell": run_shell,
    "shell_jobs": shell_jobs,
    "file_read": file_read,
    "file_create": file_create,
    "file_edit": file_edit,
    "file_delete": file_delete,
    "file_search": file_search,
    "file_move": file_move,
    "file_copy": file_copy,
    "memory_write": memory_write,
    "memory_search": memory_search,
    "memory_forget": memory_forget,
    "memory_get": memory_get,
    "memory_stats": memory_stats,
    "memory_tags": memory_tags,
    "memory_forget_candidates": memory_forget_candidates,
    "memory_demote": memory_demote,
    "memory_pin": memory_pin,
    "memory_promote": memory_promote,
    "browser_control": browser_control,
    "skill_list": skill_list,
    "skill_read": skill_read,
    "skill_create": skill_create,
    "skill_run": skill_run,
    "skill_delete": skill_delete,
    "plugin_list": plugin_list,
    "plugin_create": plugin_create,
    "plugin_reload": plugin_reload,
    "plugin_toggle": plugin_toggle,
    "plugin_delete": plugin_delete,
    "framework_update": framework_update,
}


def truncate_output(text: str, limit: int) -> str:
    """头 70% + 尾 30% 的截断策略（保留最有用信息）。"""
    if len(text) <= limit:
        return text
    head = text[: int(limit * 0.7)]
    tail = text[-int(limit * 0.3):]
    return f"{head}\n\n...[输出过长已截断，共 {len(text)} 字符]...\n\n{tail}"


def _rotate_log(path: Path, max_bytes: int, backups: int = 2) -> None:
    """超过阈值就滚动：audit.jsonl → .1 → .2，只保留最近两份，避免无限增长。"""
    try:
        if max_bytes <= 0 or not path.exists() or path.stat().st_size < max_bytes:
            return
        oldest = path.with_name(f"{path.name}.{backups}")
        if oldest.exists():
            oldest.unlink()
        for i in range(backups - 1, 0, -1):
            src = path.with_name(f"{path.name}.{i}")
            if src.exists():
                os.replace(src, path.with_name(f"{path.name}.{i + 1}"))
        os.replace(path, path.with_name(f"{path.name}.1"))
    except OSError:
        pass


def _audit(name: str, args: dict, ok: bool, out_len: int, workdir: str, cfg: dict,
           error: str = "", seconds: float | None = None) -> None:
    try:
        log_root = Path(cfg.get("_project_root") or PROJECT_ROOT)
        log_path = log_root / cfg.get("audit_log", "logs/tool-audit.jsonl")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            max_mb = int(cfg.get("audit_log_max_mb") or 8)
        except (TypeError, ValueError):
            max_mb = 8
        _rotate_log(log_path, max_mb * 1024 * 1024)
        entry = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "tool": name,
            "workdir": workdir,
            "workspace": cfg.get("_workspace") or "global",
            "ok": ok,
            "out_chars": out_len,
            "permission": cfg.get("_permission", "standard"),
            "args": {k: (str(v)[:300]) for k, v in args.items()},
        }
        if seconds is not None:
            entry["seconds"] = seconds
        # 失败时记录原因：没有它，事后只能靠猜（历史审计里 40 次失败零线索）
        if not ok:
            entry["error"] = str(error or "")[:500]
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass  # 审计失败不阻断主流程


# 权限档位下被禁止的写类工具（只读模式）
_WRITE_TOOLS = {"file_create", "file_edit", "file_delete", "file_move", "file_copy",
                "skill_create", "skill_delete", "skill_run",
                "plugin_create", "plugin_reload", "plugin_toggle", "plugin_delete",
                "framework_update",
                "memory_write", "memory_forget", "memory_promote",
                "memory_demote", "memory_pin"}
# 只读模式下浏览器允许的动作（纯浏览/取信息）
_BROWSER_READ_ACTIONS = {"open", "screenshot", "text", "html", "eval", "wait", "scroll",
                         "back", "forward", "status", "close"}

# 插件注册的工具（一切皆插件）：{tool_name}; 只读放行集合由插件声明 readonly_safe
_PLUGIN_TOOLS: set[str] = set()
_PLUGIN_RO_OK: set[str] = set()


def register_plugin_tool(spec: dict, handler, readonly_safe: bool = False) -> None:
    """插件注册工具（同名核心工具不可覆盖；插件热重载可替换自身）。"""
    tname = ((spec or {}).get("function") or {}).get("name") or ""
    if not tname:
        raise ValueError("工具 spec 缺少 function.name")
    existing = [t for t in TOOL_SPECS if t["function"]["name"] == tname]
    if existing:
        if tname not in _PLUGIN_TOOLS:
            raise ValueError(f"工具名与内置工具冲突：{tname}")
        TOOL_SPECS[:] = [t for t in TOOL_SPECS if t["function"]["name"] != tname]
    TOOL_SPECS.append(spec)
    SCHEMA_HINTS[tname] = (spec.get("function") or {}).get("parameters", {}).get("properties", {})
    _HANDLERS[tname] = handler
    _PLUGIN_TOOLS.add(tname)
    if readonly_safe:
        _PLUGIN_RO_OK.add(tname)
    else:
        _PLUGIN_RO_OK.discard(tname)


def unregister_plugin_tools(names) -> None:
    for n in list(names or []):
        TOOL_SPECS[:] = [t for t in TOOL_SPECS if t["function"]["name"] != n]
        SCHEMA_HINTS.pop(n, None)
        _HANDLERS.pop(n, None)
        _PLUGIN_TOOLS.discard(n)
        _PLUGIN_RO_OK.discard(n)


def execute_tool(name: str, args: dict, workdir: str, cfg: dict) -> tuple[str, bool]:
    """执行工具，返回 (结果文本, 是否成功)。异常统一转为给模型的错误文本。

    权限档位（cfg["_permission"]）：
      readonly  — 禁止写类工具；run_shell 仅允许只读命令白名单
      standard  — 默认：沙箱内可读写（写入限工作目录 / 临时目录），危险命令黑名单拦截
      full      — 无沙箱：全盘读写，放行黑名单级危险命令（仍写审计）
    """
    from sandbox import check_tool, normalize_permission

    perm = normalize_permission(cfg.get("_permission"))
    cfg = {**cfg, "_permission": perm}

    if perm == "readonly":
        if name in _WRITE_TOOLS:
            msg = f"[权限] 当前为只读模式，{name}（写操作）已被拦截。如需修改，请让用户切换权限档位。"
            _audit(name, args, False, len(msg), workdir, cfg)
            return msg, False
        if name in _PLUGIN_TOOLS and name not in _PLUGIN_RO_OK:
            msg = f"[权限] 只读模式下插件工具 {name} 未声明 readonly_safe，已被拦截。"
            _audit(name, args, False, len(msg), workdir, cfg)
            return msg, False
        if name == "browser_control":
            act = str(args.get("action") or "").lower()
            if act not in _BROWSER_READ_ACTIONS:
                msg = f"[权限] 只读模式下浏览器不允许 {act}（可能改动页面/提交数据）；浏览与取信息动作不受限。"
                _audit(name, args, False, len(msg), workdir, cfg)
                return msg, False
        if name == "memory_tags":
            act = str(args.get("action") or "add").lower()
            if act != "overview":
                msg = ("[权限] 只读模式下 memory_tags 只允许 overview（查看标签总览）；"
                       "set/add/remove 会改记忆库，已被拦截。")
                _audit(name, args, False, len(msg), workdir, cfg)
                return msg, False
        if name == "run_shell":
            from .shell import is_readonly_command

            cmd = str(args.get("command") or "")
            if not is_readonly_command(cmd):
                msg = ("[权限] 当前为只读模式，该命令可能修改系统状态，已被拦截。"
                       "查询信息请改用只读命令（ls、cat、find、systeminfo 等）。")
                _audit(name, args, False, len(msg), workdir, cfg)
                return msg, False
    elif perm == "full":
        cfg = {**cfg, "shell_guard": "off"}  # 完全模式：放行危险命令（审计照记）

    # 工作目录沙箱：文件写入 / shell 越界路径在标准档位下拦截
    allowed, sandbox_msg = check_tool(name, args, workdir, perm)
    if not allowed:
        _audit(name, args, False, len(sandbox_msg), workdir, cfg, error=sandbox_msg)
        return sandbox_msg, False

    # 审批闸门：有副作用的操作，在用户点头之前不落地（full 档直接放行）
    try:
        import approval as _approval

        _allowed, _why = _approval.check(name, args, workdir, cfg)
    except Exception as e:  # 闸门自身出问题不能把工具链整个卡死，但要留痕
        print(f"[approval] 检查失败，放行：{type(e).__name__}: {e}", flush=True)
        _allowed, _why = True, ""
    if not _allowed:
        msg = f"[审批] 未执行：{_why}。请换一种做法，或先向用户说明再重试。"
        _audit(name, args, False, len(msg), workdir, cfg, error=_why)
        return msg, False

    handler = _HANDLERS.get(name)
    if handler is None:
        return f"错误：未知工具 {name}（可用：{', '.join(_HANDLERS)}）", False
    err_text = ""
    t0 = time.time()
    try:
        result = handler(args, workdir, cfg)
        ok = True
    except Exception as e:
        result = f"错误：{type(e).__name__}: {e}"
        err_text = f"{type(e).__name__}: {e}"
        ok = False
    result = truncate_output(str(result), int(cfg.get("output_truncate", 8000)))
    _audit(name, args, ok, len(result), workdir, cfg,
           error=err_text, seconds=round(time.time() - t0, 2))
    return result, ok
