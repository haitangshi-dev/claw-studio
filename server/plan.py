# -*- coding: utf-8 -*-
"""任务规划：模型先把步骤写下来，界面只是同一份数据的视图。

为什么计划要落盘而不是只放上下文里：
  - 上下文会被压缩，压缩后模型就"忘了自己干到第几步"，容易重复劳动；
  - 面板要能显示，用户也可能手动勾掉/跳过某一步；
  - 定时任务跑完能回看当时是怎么拆的。

存储：sessions/<session_id>.plan.json
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from fastapi import APIRouter

router = APIRouter()

DATA_ROOT: Path | None = None
_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,80}$")

STATUSES = ("pending", "running", "done", "failed", "skipped")
_MARK = {"pending": " ", "running": ">", "done": "x", "failed": "!", "skipped": "-"}

SPEC = {
    "type": "function",
    "function": {
        "name": "task_plan",
        "description": (
            "维护当前任务的步骤计划。任务超过两步时先 set 一份计划，"
            "每完成一步立刻 update，不要攒到最后一次性汇报。"
            "只记步骤与状态，不要写预计耗时。简单问答不要建计划。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["set", "update", "get", "clear"]},
                "title": {"type": "string", "description": "计划标题（可选，一句话说明目标）"},
                "steps": {
                    "type": "array",
                    "description": "action=set 时必填。每条要有可验收的结果，不要写『优化一下』这种。",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "note": {"type": "string", "description": "补充说明，可留空"},
                        },
                        "required": ["title"],
                    },
                },
                "step_id": {"type": "string", "description": "action=update 时必填"},
                "status": {"type": "string", "enum": list(STATUSES)},
                "note": {"type": "string", "description": "这一步的产出摘要（可选）"},
            },
            "required": ["action"],
        },
    },
}


def configure(data_root: Path) -> None:
    global DATA_ROOT
    DATA_ROOT = Path(data_root)
    (DATA_ROOT / "sessions").mkdir(parents=True, exist_ok=True)


def _path(sid: str) -> Path:
    if not _ID_RE.match(str(sid or "")):
        raise ValueError(f"非法会话 id：{sid!r}")
    return DATA_ROOT / "sessions" / f"{sid}.plan.json"


def load(sid: str) -> dict:
    try:
        p = _path(sid)
    except ValueError:
        return {"session_id": sid, "title": "", "steps": []}
    if not p.exists():
        return {"session_id": sid, "title": "", "steps": []}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {"session_id": sid, "steps": []}
    except Exception:
        return {"session_id": sid, "steps": []}


def save(sid: str, plan: dict) -> dict:
    plan["session_id"] = sid
    plan["updated_at"] = int(time.time() * 1000)
    steps = []
    for i, s in enumerate(plan.get("steps") or []):
        steps.append({
            "id": str(s.get("id") or f"s{i + 1}"),
            "title": str(s.get("title") or "").strip()[:200],
            "note": str(s.get("note") or "")[:500],
            "status": s.get("status") if s.get("status") in STATUSES else "pending",
        })
    plan["steps"] = [s for s in steps if s["title"]]
    p = _path(sid)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(p))
    return plan


def clear(sid: str) -> None:
    try:
        p = _path(sid)
        if p.exists():
            p.unlink()
    except Exception:
        pass


def summary(plan: dict) -> dict:
    steps = plan.get("steps") or []
    done = sum(1 for s in steps if s["status"] in ("done", "skipped"))
    running = next((s for s in steps if s["status"] == "running"), None)
    return {"total": len(steps), "done": done, "running": running["title"] if running else ""}


def render(sid: str, max_steps: int = 15) -> str:
    """给模型看的紧凑文本。放在提示词尾部，位置稳定利于前缀缓存。"""
    plan = load(sid)
    steps = (plan.get("steps") or [])[:max_steps]
    if not steps:
        return ""
    done = sum(1 for s in steps if s["status"] in ("done", "skipped"))
    lines = [f"## 当前计划（{done}/{len(steps)}）" + (f"｜{plan.get('title')}" if plan.get("title") else "")]
    for i, s in enumerate(steps, 1):
        note = f"（{s['note']}）" if s.get("note") else ""
        lines.append(f"[{_MARK.get(s['status'], ' ')}] {i} {s['title']}{note}")
    lines.append("规则：完成一步立刻 task_plan(update)；不要重复已完成的步骤。")
    return "\n".join(lines)


# ---------------- 工具 ----------------
def handler(args: dict, workdir: str, cfg: dict) -> str:
    sid = str(cfg.get("_session_id") or cfg.get("session") or "default")
    action = str((args or {}).get("action") or "get").lower()
    plan = load(sid)

    if action == "clear":
        clear(sid)
        return "计划已清空。"

    if action == "get":
        txt = render(sid)
        return txt or "当前没有计划。"

    if action == "set":
        title = str((args or {}).get("title") or "").strip()
        incoming = (args or {}).get("steps") or []
        if not isinstance(incoming, list) or not incoming:
            return "错误：action=set 需要 steps 数组（至少一条）。"
        old = {s["title"]: s for s in (plan.get("steps") or [])}
        steps = []
        for i, s in enumerate(incoming[:12]):
            t = str((s or {}).get("title") or "").strip()
            if not t:
                continue
            prev = old.get(t)
            steps.append({
                "id": f"s{i + 1}",
                "title": t,
                "note": str((s or {}).get("note") or ""),
                # 重规划时同名步骤保留原状态，避免"已完成"被打回 pending
                "status": prev["status"] if prev else "pending",
            })
        plan = save(sid, {"session_id": sid, "title": title or plan.get("title") or "", "steps": steps})
        if len(steps) < 3:
            return f"已记录 {len(steps)} 步。提醒：少于 3 步的任务其实可以不建计划。"
        return f"已建计划《{plan.get('title') or '任务'}》，共 {len(steps)} 步。每完成一步请立刻 update。"

    if action == "update":
        step_id = str((args or {}).get("step_id") or "").strip()
        status = (args or {}).get("status")
        note = (args or {}).get("note")
        steps = plan.get("steps") or []
        target = next((s for s in steps if s["id"] == step_id), None)
        if target is None and step_id.isdigit():
            idx = int(step_id) - 1
            target = steps[idx] if 0 <= idx < len(steps) else None
        if target is None:
            return f"错误：找不到步骤 {step_id}。可用 get 查看当前计划与编号。"
        if status in STATUSES:
            # 同一时刻只允许一个 running，避免面板出现两条"进行中"
            if status == "running":
                for s in steps:
                    if s["status"] == "running" and s is not target:
                        s["status"] = "pending"
            target["status"] = status
        if note is not None:
            target["note"] = str(note)[:500]
        save(sid, plan)
        sm = summary(plan)
        return f"已更新「{target['title']}」为 {target['status']}（{sm['done']}/{sm['total']}）。"

    return f"错误：未知 action {action}"


# ---------------- HTTP ----------------
@router.get("/api/plan")
def get_plan(session: str = "default"):
    plan = load(session)
    return {"ok": True, "plan": plan, "summary": summary(plan)}


@router.post("/api/plan")
def post_plan(payload: dict):
    """界面端的手动干预：勾完成、跳过、改标题、加一步、清空。"""
    sid = str((payload or {}).get("session") or "default")
    op = str((payload or {}).get("op") or "update")
    plan = load(sid)

    if op == "clear":
        clear(sid)
        return {"ok": True, "plan": {"session_id": sid, "steps": []}, "summary": summary({})}

    if op == "set":
        steps = (payload or {}).get("steps") or []
        plan = save(sid, {"session_id": sid, "title": (payload or {}).get("title") or "", "steps": steps})
        return {"ok": True, "plan": plan, "summary": summary(plan)}

    step_id = str((payload or {}).get("step_id") or "")
    steps = plan.get("steps") or []
    target = next((s for s in steps if s["id"] == step_id), None)
    if target is None:
        return {"ok": False, "error": "步骤不存在"}

    if op == "update":
        if (payload or {}).get("status") in STATUSES:
            target["status"] = payload["status"]
        if (payload or {}).get("title"):
            target["title"] = str(payload["title"])[:200]
        if (payload or {}).get("note") is not None:
            target["note"] = str(payload["note"])[:500]
    elif op == "skip":
        target["status"] = "skipped"
    elif op == "remove":
        steps = [s for s in steps if s["id"] != step_id]
    elif op == "insert":
        idx = next((i for i, s in enumerate(steps) if s["id"] == step_id), len(steps))
        steps.insert(idx + 1, {
            "id": f"s{int(time.time() * 1000) % 100000}",
            "title": str((payload or {}).get("title") or "新步骤")[:200],
            "note": "",
            "status": "pending",
        })
    plan["steps"] = steps
    plan = save(sid, plan)
    return {"ok": True, "plan": plan, "summary": summary(plan)}
