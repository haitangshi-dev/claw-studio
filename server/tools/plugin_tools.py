# -*- coding: utf-8 -*-
"""插件管理工具：模型可以列出/创建/重载/启停/删除插件（一切皆插件）。"""
from __future__ import annotations

import json
from pathlib import Path

import plugins as plugins_mod

PLUGIN_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "plugin_list",
            "description": "列出已安装插件（名称/版本/注册的工具/状态）。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plugin_create",
            "description": (
                "创建（或覆盖）一个插件：给框架加一个全新的工具。plugin.py 里写 "
                "def register(api): api.register_tool(spec, handler)；handler(args, workdir, cfg) 返回字符串。"
                "适合把需要代码能力（网络、解析、算法等）的新功能接进框架；纯脚本流程用 skill_create 即可。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "插件名（字母/数字/下划线/连字符，≤40）"},
                    "description": {"type": "string", "description": "一句话说明插件做什么"},
                    "code": {"type": "string", "description": "plugin.py 完整源码（含 register(api)）"},
                    "readonly_safe": {"type": "boolean", "description": "工具是否在只读权限下也允许运行（默认否）"},
                    "overwrite": {"type": "boolean", "description": "同名已存在时是否覆盖热重载"},
                },
                "required": ["name", "description", "code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plugin_reload",
            "description": "热重载指定插件（改完 plugin 代码后无需重启框架即生效）。缺省重载全部。",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string", "description": "插件名（省略=重载全部）"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plugin_toggle",
            "description": "启用/停用插件（停用后其工具从工具表中移除）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "插件名"},
                    "enabled": {"type": "boolean", "description": "true=启用，false=停用"},
                },
                "required": ["name", "enabled"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plugin_delete",
            "description": "删除插件（目录整体删除，谨慎）。",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string", "description": "插件名"}},
                "required": ["name"],
            },
        },
    },
]


def _root(cfg: dict) -> Path:
    return Path(cfg.get("_project_root") or ".")


def plugin_list(args: dict, workdir: str, cfg: dict) -> str:
    items = plugins_mod.scan(_root(cfg))
    if not items:
        return "当前没有插件。可用 plugin_create 创建（给框架加新工具）。"
    lines = []
    for p in items:
        state = "停用" if not p["enabled"] else ("已加载" if p["loaded"] else "未加载")
        tools = ("、".join(p["tools"])) or "—"
        err = f" ⚠{p['error'][:60]}" if p["error"] and p["error"] != "已停用" else ""
        lines.append(f"- {p['name']} v{p['version'] or '?'} [{state}] 工具：{tools}｜{p['description'][:40]}{err}")
    return f"共 {len(items)} 个插件：\n" + "\n".join(lines)


def plugin_create(args: dict, workdir: str, cfg: dict) -> str:
    r = plugins_mod.create(
        _root(cfg),
        str(args.get("name") or "").strip(),
        str(args.get("description") or "").strip(),
        str(args.get("code") or ""),
        readonly_safe=bool(args.get("readonly_safe")),
        overwrite=bool(args.get("overwrite")),
    )
    if r.get("ok") and r.get("tools"):
        return f"插件已创建并热加载：{r['name']}，注册工具：{ '、'.join(r['tools']) }。现在可以直接调用这些工具。"
    if r.get("ok"):
        return f"插件已创建：{r['name']}（未注册工具）"
    return f"插件创建失败：{r.get('error')}（文件已写入，可修正后 plugin_reload）"


def plugin_reload(args: dict, workdir: str, cfg: dict) -> str:
    name = str(args.get("name") or "").strip()
    if name:
        r = plugins_mod.load(_root(cfg), name)
        return f"插件 {name} 重载{'成功，工具：' + '、'.join(r['tools']) if r.get('ok') else '失败：' + str(r.get('error'))}"
    results = plugins_mod.load_all(_root(cfg))
    okn = sum(1 for r in results if r.get("ok"))
    return f"已重载全部插件：{okn}/{len(results)} 成功"


def plugin_toggle(args: dict, workdir: str, cfg: dict) -> str:
    name = str(args.get("name") or "").strip()
    enabled = bool(args.get("enabled"))
    plugins_mod.set_enabled(_root(cfg), name, enabled)
    return f"插件 {name} 已{'启用' if enabled else '停用'}"


def plugin_delete(args: dict, workdir: str, cfg: dict) -> str:
    name = str(args.get("name") or "").strip()
    plugins_mod.delete(_root(cfg), name)
    return f"插件已删除：{name}"
