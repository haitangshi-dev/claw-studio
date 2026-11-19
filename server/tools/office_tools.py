# -*- coding: utf-8 -*-
"""办公室工具：把任务转交给当前会话的主管，由 Qoder 团队执行。

工具本身是同步的（agent_loop 在线程池里调 execute_tool），内部用 asyncio.run
跑办公室运行器；员工/主管消息通过本机 /api/team/message 推回主聊天。
"""
from __future__ import annotations

import asyncio

OFFICE_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "office_dispatch",
            "description": "【只派活，立即返回】把任务交给办公室：自动路由到主管部门，主管拆解并把子任务分给具体员工。"
                           "返回被派活的员工名单；之后用 office_talk 逐个跟员工对话拿结果。"
                           "单轮结束就停，后续要继续再调一次本工具。",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "交给主管的完整任务（目标、约束、验收标准）"},
                    "room": {"type": "string", "description": "部门：dev 项目开发部 / life 生活部 / memory 记忆管理部（可选，默认当前会话房间）"},
                    "focus": {"type": "string", "description": "本轮重点关注（可选）"},
                },
                "required": ["task"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "office_hire",
            "description": "给部门招一名新员工（需要新岗位/新技能时用）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "room": {"type": "string", "description": "dev / life / memory"},
                    "name": {"type": "string", "description": "员工名（岗位名，如 测试工程师、短视频剪辑）"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "office_talk",
            "description": "跟办公室里的一个成员对话（一次一问一答）：to=主管/员工名，text=要对他说的话。",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {"type": "string", "description": "对象：主管 或 具体员工名"},
                    "text": {"type": "string", "description": "要对他说的话/交给他的任务"},
                    "room": {"type": "string", "description": "部门 dev/life/memory（可省略，默认当前）"},
                },
                "required": ["to", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "office_roster",
            "description": "查看公司编制：各部门主管与员工。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


def office_hire(args: dict, workdir: str, cfg: dict) -> str:
    import json
    from office import hire
    return json.dumps(hire(str((args or {}).get("room") or "dev"), str((args or {}).get("name") or "")), ensure_ascii=False)


def office_roster(args: dict, workdir: str, cfg: dict) -> str:
    import json
    from office import ROOMS
    return json.dumps({rid: {"name": r["name"], "supervisor": r["supervisor"], "staff": r["staff"]} for rid, r in ROOMS.items()}, ensure_ascii=False)


def office_dispatch(args: dict, workdir: str, cfg: dict) -> str:
    from office import plan_dispatch

    task = str((args or {}).get("task") or "").strip()
    if not task:
        raise ValueError("task 不能为空")
    focus = str((args or {}).get("focus") or "").strip()
    if focus:
        task = f"{task}\n本轮重点：{focus}"
    qd = cfg.get("_qd")
    if qd is None:
        raise RuntimeError("Qoder 提供方未初始化，办公室无法派活")
    room_id = str((args or {}).get("room") or "auto")
    mode = "unattended" if cfg.get("_unattended") else "onduty"
    try:
        deadline = float(cfg.get("_unattended_deadline") or 0)
    except (TypeError, ValueError):
        deadline = 0.0
    import json
    result = asyncio.run(
        plan_dispatch(
            qd=qd,
            cfg=cfg,
            session_id=str(cfg.get("_session_id") or "default"),
            task=task,
            room_id=room_id,
            workdir=workdir,
            permission=str(cfg.get("_permission") or "standard"),
        )
    )
    return json.dumps(result, ensure_ascii=False)


def office_talk(args: dict, workdir: str, cfg: dict) -> str:
    from office import talk
    qd = cfg.get("_qd")
    if qd is None:
        raise RuntimeError("Qoder 提供方未初始化，办公室无法对话")
    to = str((args or {}).get("to") or "").strip()
    text = str((args or {}).get("text") or "").strip()
    if not to or not text:
        raise ValueError("to / text 不能为空")
    return asyncio.run(
        talk(
            qd=qd,
            cfg=cfg,
            session_id=str(cfg.get("_session_id") or "default"),
            room_id=str((args or {}).get("room") or cfg.get("_office_room") or "dev"),
            to=to,
            text=text,
            workdir=workdir,
            permission=str(cfg.get("_permission") or "standard"),
        )
    )
