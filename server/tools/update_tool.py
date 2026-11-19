# -*- coding: utf-8 -*-
"""框架自更新工具：模型可以检查/应用 Claw Agent 框架更新（原生换血重启）。"""
from __future__ import annotations

import updater

UPDATE_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "framework_update",
            "description": (
                "检查或应用 Claw Agent 框架更新（从配置的更新源取新版本，就地替换并自动重启）。"
                "action=check 只看；action=apply 会立即更新并重启应用（先向用户确认再执行）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["check", "apply"], "description": "check 或 apply"},
                },
                "required": ["action"],
            },
        },
    },
]


def _source(cfg: dict) -> str:
    return str((cfg.get("_update") or {}).get("source_dir") or "")


def framework_update(args: dict, workdir: str, cfg: dict) -> str:
    action = str(args.get("action") or "check").lower()
    src = _source(cfg)
    if action == "check":
        r = updater.check(src)
        if not r.get("ok"):
            return f"检查更新失败：{r.get('error')}"
        cur = r["current"]; lat = r["latest"]
        if r.get("available"):
            return (f"发现新版本：{lat['version']}（当前 {cur['version']}，{lat['built_at']}）\n"
                    f"更新说明：{lat['notes'] or '（无）'}\n说\"更新\"即应用并重启。")
        return f"已是最新版本（{cur['version']}）"
    if action == "apply":
        r = updater.apply(src)
        if not r.get("ok"):
            return f"应用更新失败：{r.get('error')}"
        updater.schedule_exit(2.5)
        return f"更新已启动：{r['from']} → {r['to']}，应用将在数秒内自动重启完成切换。"
    return "错误：action 只支持 check / apply"
