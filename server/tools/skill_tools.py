# -*- coding: utf-8 -*-
"""技能工具：模型可以列出/查看/创建/运行/删除技能（skill_list / skill_read / skill_create / skill_run / skill_delete）。"""
from __future__ import annotations

import json
from pathlib import Path

import skills as skills_mod

SKILL_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "skill_list",
            "description": "列出全部可用技能（名称+说明）。运行技能前先用它确认名字。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skill_read",
            "description": "查看某个技能的 SKILL.md 说明与 run.py 源码（学习/修改技能前先读）。",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string", "description": "技能名"}},
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skill_create",
            "description": (
                "创建（或覆盖）一个可复用技能：把一段流程固化成 run.py 脚本。"
                "脚本从 stdin 读 JSON 参数、结果 print 到 stdout（cwd=会话工作目录）。"
                "适合用户说'把这件事做成技能/以后都要这样'时。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "技能名（字母/数字/下划线/连字符，≤40）"},
                    "description": {"type": "string", "description": "一句话说明技能做什么"},
                    "skill_md": {"type": "string", "description": "SKILL.md 内容：使用说明与参数约定"},
                    "script": {"type": "string", "description": "run.py 的完整 Python 源码"},
                    "overwrite": {"type": "boolean", "description": "同名已存在时是否覆盖"},
                },
                "required": ["name", "description", "script"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skill_run",
            "description": "运行一个技能（stdin 传入 JSON 参数，返回脚本输出）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "技能名"},
                    "args": {"type": "object", "description": "传给技能的 JSON 参数对象（可省）"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skill_delete",
            "description": "删除一个技能（目录整体删除，谨慎）。",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string", "description": "技能名"}},
                "required": ["name"],
            },
        },
    },
]


def _root(cfg: dict) -> Path:
    return Path(cfg.get("_project_root") or ".")


def skill_list(args: dict, workdir: str, cfg: dict) -> str:
    items = skills_mod.scan(_root(cfg))
    if not items:
        return "当前没有技能。可以用 skill_create 创建一个（把常用流程固化成脚本）。"
    lines = [f"- {s['name']}：{s['description'] or '（无说明）'}" for s in items]
    return f"共 {len(items)} 个技能：\n" + "\n".join(lines)


def skill_read(args: dict, workdir: str, cfg: dict) -> str:
    data = skills_mod.load(_root(cfg), str(args.get("name") or ""))
    parts = [f"# 技能：{data['name']}"]
    if data["meta"].get("description"):
        parts.append(f"说明：{data['meta']['description']}")
    if data["skill_md"]:
        parts.append("--- SKILL.md ---\n" + data["skill_md"][:4000])
    parts.append("--- run.py ---\n" + (data["script"][:5000] or "（无脚本）"))
    return "\n".join(parts)


def skill_create(args: dict, workdir: str, cfg: dict) -> str:
    data = skills_mod.create(
        _root(cfg),
        str(args.get("name") or "").strip(),
        str(args.get("description") or "").strip(),
        str(args.get("skill_md") or ""),
        str(args.get("script") or ""),
        author="agent",
        overwrite=bool(args.get("overwrite")),
    )
    return f"技能已创建：{data['name']}\n目录：{data['path']}\n可以用 skill_run 试跑验证。"


def skill_run(args: dict, workdir: str, cfg: dict) -> str:
    a = args.get("args")
    if isinstance(a, str):
        try:
            a = json.loads(a) if a.strip() else {}
        except Exception:
            a = {"input": a}
    if not isinstance(a, dict):
        a = {}
    return skills_mod.run(_root(cfg), str(args.get("name") or "").strip(), a, workdir,
                          timeout=int(args.get("timeout") or cfg.get("skill_timeout", 120)),
                          python_exe=cfg.get("python_exe"))


def skill_delete(args: dict, workdir: str, cfg: dict) -> str:
    name = str(args.get("name") or "").strip()
    skills_mod.delete(_root(cfg), name)
    return f"技能已删除：{name}"
