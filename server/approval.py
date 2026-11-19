# -*- coding: utf-8 -*-
"""工具审批闸门：有副作用的操作，在你点头之前不落地。

四档（cfg["_permission"]）：
  readonly — 上游已直接拒绝写类工具，这里不重复拦
  ask      — 任何有副作用的操作都问（默认）
  standard — 工作目录内写放行；删除、外发、危险命令仍然问
  full     — 不问，直接执行

为什么放在 execute_tool 里而不是前端：
  前端只负责「显示卡片 + 回传决定」，真正的闸门必须在执行侧。否则换个客户端
  （微信、MCP、定时任务）就绕过去了。

无人值守 / 界面不在线时不弹卡：按设置里的策略自动裁决，并在结果里写明原因。
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from typing import Callable

from fastapi import APIRouter

router = APIRouter()

READ = "read"
WRITE = "write"
EXEC = "exec"
DESTRUCTIVE = "destructive"
EXTERNAL = "external"

_READ_TOOLS = {
    "file_read", "file_search", "memory_search", "memory_get", "memory_stats",
    "memory_forget_candidates", "skill_list", "skill_read", "plugin_list",
    "shell_jobs", "office_roster",
}
_DESTRUCTIVE_TOOLS = {
    "file_delete", "memory_forget", "memory_demote", "skill_delete", "plugin_delete",
    "framework_update",
}
_EXTERNAL_TOOLS = {"http_request", "office_dispatch", "office_hire", "office_talk", "memory_export"}
_EXEC_TOOLS = {"run_shell", "skill_run", "plugin_create", "plugin_reload", "plugin_toggle", "skill_create"}

_BROWSER_READ = {
    "open", "screenshot", "text", "html", "wait", "scroll", "back", "forward", "status", "close",
}

_settings_getter: Callable[[], dict] | None = None
_grants: set[tuple] = set()
_last_poll = 0.0   # 最近一次有人轮询待确认列表的时间（判断「界面在不在」）
_pending: dict[str, dict] = {}
_lock = threading.Lock()


def configure(settings_getter: Callable[[], dict]) -> None:
    global _settings_getter
    _settings_getter = settings_getter


def _ui_alive(window: float = 8.0) -> bool:
    """界面是否在线：前端每 0.9 秒轮询一次待确认列表，比进程探测可靠。"""
    return (time.time() - _last_poll) < window


def _policy() -> tuple[str, int]:
    """读设置：无人值守策略与确认超时。读不到就用保守默认。"""
    try:
        s = _settings_getter() if _settings_getter else {}
    except Exception:
        s = {}
    p = (s.get("permission") or {})
    return str(p.get("unattendedPolicy") or "safe"), int(p.get("timeout") or 120)


def classify(name: str, args: dict) -> str:
    if name == "browser_control":
        act = str((args or {}).get("action") or "").lower()
        return READ if act in _BROWSER_READ else EXTERNAL
    if name == "run_shell":
        try:
            from tools.shell import is_dangerous

            return DESTRUCTIVE if is_dangerous(str((args or {}).get("command") or "")) else EXEC
        except Exception:
            return EXEC
    if name in _DESTRUCTIVE_TOOLS:
        return DESTRUCTIVE
    if name in _EXTERNAL_TOOLS:
        return EXTERNAL
    if name in _EXEC_TOOLS:
        return EXEC
    if name in _READ_TOOLS:
        return READ
    return WRITE


def _scope(name: str, args: dict, workdir: str | None) -> str:
    """授权的记忆粒度：路径类按目录，其它按工具本身。"""
    for k in ("path", "target", "dst", "src", "file", "dir"):
        v = str((args or {}).get(k) or "").strip()
        if v:
            try:
                import os

                full = v if os.path.isabs(v) else os.path.join(workdir or ".", v)
                return os.path.dirname(os.path.abspath(full))
            except Exception:
                return "path"
    return "-"


def _preview(name: str, args: dict) -> tuple[str, str]:
    """给卡片用：一句话标题 + 细节（不回显密钥类参数）。"""
    a = args or {}
    if name == "run_shell":
        return "执行命令", str(a.get("command") or "")[:400]
    if name in ("file_create", "file_edit", "file_delete", "file_move", "file_copy", "file_read"):
        src = str(a.get("path") or a.get("src") or "")
        dst = str(a.get("dst") or a.get("target") or "")
        detail = src + (f" → {dst}" if dst else "")
        return {"file_delete": "删除文件"}.get(name, "写入文件" if name != "file_read" else "读取文件"), detail[:400]
    if name == "http_request":
        return "发起 HTTP 请求", f"{str(a.get('method') or 'GET').upper()} {a.get('url') or ''}"[:400]
    if name == "browser_control":
        return "操作浏览器", f"{a.get('action') or ''} {a.get('url') or a.get('selector') or ''}"[:400]
    if name.startswith("memory_"):
        return "修改记忆库", json.dumps({k: v for k, v in a.items() if k != "content"}, ensure_ascii=False)[:300]
    if name.startswith(("skill_", "plugin_")):
        return "改动扩展", json.dumps({k: v for k, v in a.items() if k != "code"}, ensure_ascii=False)[:300]
    return "执行工具", json.dumps(a, ensure_ascii=False)[:300]


def _auto_allow(risk: str, policy: str) -> bool:
    """无人值守时的自动裁决。"""
    if policy == "loose":
        return risk not in (DESTRUCTIVE,)
    if policy == "normal":
        return risk in (WRITE, READ)
    return risk in (WRITE, READ, EXEC)  # safe：只拒不可逆与外发


def _ask(name: str, args: dict, workdir: str, session: str, risk: str, timeout: int) -> tuple[bool, str]:
    aid = uuid.uuid4().hex[:12]
    title, detail = _preview(name, args)
    ev = threading.Event()
    rec = {
        "id": aid, "tool": name, "risk": risk, "title": title, "detail": detail,
        "workdir": workdir or "", "session": session,
        "created_at": int(time.time() * 1000), "deadline": timeout,
    }
    with _lock:
        _pending[aid] = {**rec, "_ev": ev, "_result": None}
    got = ev.wait(timeout)
    with _lock:
        item = _pending.pop(aid, None)
    if not got or item is None:
        return False, f"等待确认超时（{timeout} 秒），按拒绝处理"
    res = item.get("_result") or {}
    if res.get("action") == "allow":
        return True, ""
    if res.get("action") == "allow_session":
        with _lock:
            _grants.add((session, name, _scope(name, args, workdir)))
        return True, ""
    reason = str(res.get("reason") or "").strip()
    return False, reason or "用户拒绝了这个操作"


def check(name: str, args: dict, workdir: str | None, cfg: dict) -> tuple[bool, str]:
    """执行前的闸门。返回 (是否放行, 拒绝原因)。"""
    perm = str(cfg.get("_permission") or "standard")
    if perm == "full":
        return True, ""
    risk = classify(name, args or {})
    if risk == READ:
        return True, ""
    need = perm == "ask" or (perm == "standard" and risk in (DESTRUCTIVE, EXTERNAL))
    if not need:
        return True, ""
    session = str(cfg.get("_session_id") or cfg.get("session") or "-")
    scope = _scope(name, args or {}, workdir)
    with _lock:
        if (session, name, scope) in _grants:
            return True, ""

    policy, timeout = _policy()
    ui_alive = bool(cfg.get("_ask_ui_active")) or _ui_alive()
    if cfg.get("_unattended") or not ui_alive:
        ok = _auto_allow(risk, policy)
        if ok:
            return True, ""
        return False, f"无人值守策略（{policy}）拦下了这一步；如需执行请回到界面手动确认"

    return _ask(name, args or {}, workdir or "", session, risk, timeout)


# ---------------- HTTP：卡片数据与作答 ----------------
@router.get("/api/approval/pending")
def pending():
    global _last_poll
    _last_poll = time.time()
    now = int(time.time() * 1000)
    with _lock:
        items = [
            {k: v for k, v in rec.items() if not k.startswith("_")}
            | {"remaining": max(0, rec["deadline"] - (now - rec["created_at"]) // 1000)}
            for rec in _pending.values()
        ]
    return {"ok": True, "items": items}


@router.post("/api/approval/answer")
def answer(payload: dict):
    aid = str((payload or {}).get("id") or "")
    action = str((payload or {}).get("action") or "deny")
    if action not in ("allow", "allow_session", "deny"):
        return {"ok": False, "error": "action 只能是 allow / allow_session / deny"}
    with _lock:
        rec = _pending.get(aid)
        if rec is None:
            return {"ok": False, "error": "该确认已过期或不存在"}
        rec["_result"] = {"action": action, "reason": str((payload or {}).get("reason") or "")[:500]}
        rec["_ev"].set()
    return {"ok": True}


@router.get("/api/approval/grants")
def grants():
    with _lock:
        return {"ok": True, "grants": [{"session": a, "tool": b, "scope": c} for a, b, c in _grants]}


@router.delete("/api/approval/grants")
def clear_grants():
    with _lock:
        _grants.clear()
    return {"ok": True}
