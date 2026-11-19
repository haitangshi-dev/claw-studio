# -*- coding: utf-8 -*-
"""插件系统：一切皆插件——插件可向框架注册任意新工具（热加载/热重载）。

结构：
  plugins/<name>/plugin.json   {name, version, description, readonly_safe?, enabled?}
  plugins/<name>/plugin.py     def register(api): api.register_tool(openai_spec, handler)

约定：
  - handler(args: dict, workdir: str, cfg: dict) -> str（与内置工具同签名，走同一执行/审计链路）
  - 坏插件只被隔离跳过，绝不影响核心启动（错误记录在 scan 结果里）
  - 热重载：reload(name) 先卸下该插件注册的工具再重新加载；plugin.json 里 enabled=false 可停用
"""
from __future__ import annotations

import importlib.util
import json
import re
import shutil
import sys
import time
from pathlib import Path

_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,39}$")

# 已加载插件：name -> {meta, tools: [tool_names], module_name, error}
_LOADED: dict[str, dict] = {}

PLUGIN_TEMPLATE = '''# -*- coding: utf-8 -*-
"""插件示例：register(api) 里向框架注册新工具。"""


def register(api):
    def my_tool(args, workdir, cfg):
        # args 是模型给的参数 dict；workdir 是会话工作目录；返回字符串结果
        return "hello from plugin: " + str(args)

    api.register_tool(
        {
            "type": "function",
            "function": {
                "name": "my_tool",
                "description": "示例工具：原样回显参数",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        my_tool,
    )
'''


def plugins_root(data_root: Path) -> Path:
    root = Path(data_root) / "plugins"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _plugin_dir(data_root: Path, name: str) -> Path:
    name = (name or "").strip()
    if not _NAME_RE.match(name):
        raise ValueError(f"插件名不合法：{name!r}（字母/数字/下划线/连字符，≤40）")
    d = plugins_root(data_root) / name
    if plugins_root(data_root).resolve() not in d.resolve().parents:
        raise ValueError("路径越权")
    return d


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


class PluginAPI:
    """交给插件 register() 的能力面。"""

    def __init__(self, name: str, data_root: Path, readonly_safe: bool = False):
        self.name = name
        self.data_root = data_root
        self.readonly_safe = readonly_safe
        self.registered: list[str] = []

    def register_tool(self, spec: dict, handler) -> None:
        from tools import register_plugin_tool

        tname = ((spec or {}).get("function") or {}).get("name") or ""
        if not tname:
            raise ValueError("工具 spec 缺少 function.name")
        register_plugin_tool(spec, handler, readonly_safe=self.readonly_safe)
        self.registered.append(tname)

    def log(self, *parts) -> None:
        print(f"[plugin:{self.name}]", *parts)


def _unload(name: str) -> None:
    info = _LOADED.pop(name, None)
    if not info:
        return
    try:
        from tools import unregister_plugin_tools

        unregister_plugin_tools(info.get("tools") or [])
    except Exception:
        pass


def load(data_root: Path, name: str) -> dict:
    """加载（或重载）单个插件；返回 {ok, tools, error}。"""
    d = _plugin_dir(data_root, name)
    if not d.is_dir():
        raise FileNotFoundError(f"插件不存在：{name}")
    meta = _read_json(d / "plugin.json")
    if meta.get("enabled") is False:
        _unload(name)
        _LOADED[name] = {"meta": meta, "tools": [], "module_name": "", "error": "已停用"}
        return {"ok": True, "tools": [], "disabled": True}
    entry = d / str(meta.get("entry") or "plugin.py")
    if not entry.is_file():
        raise FileNotFoundError(f"插件入口不存在：{entry.name}")

    _unload(name)  # 热重载：先卸旧
    module_name = f"claw_plugin_{name}_{int(time.time())}"
    api = PluginAPI(name, data_root, readonly_safe=bool(meta.get("readonly_safe")))
    try:
        spec = importlib.util.spec_from_file_location(module_name, str(entry))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = mod
        spec.loader.exec_module(mod)
        register = getattr(mod, "register", None)
        if not callable(register):
            raise ValueError("plugin.py 缺少 register(api) 函数")
        register(api)
    except Exception as e:
        # 隔离坏插件：卸下它已注册的部分，记录错误
        try:
            from tools import unregister_plugin_tools

            unregister_plugin_tools(api.registered)
        except Exception:
            pass
        err = f"{type(e).__name__}: {e}"
        _LOADED[name] = {"meta": meta, "tools": [], "module_name": module_name, "error": err}
        return {"ok": False, "tools": [], "error": err}
    _LOADED[name] = {"meta": meta, "tools": list(api.registered), "module_name": module_name, "error": ""}
    return {"ok": True, "tools": list(api.registered)}


def load_all(data_root: Path) -> list[dict]:
    out = []
    for d in sorted(plugins_root(data_root).iterdir()):
        if not d.is_dir() or not (d / "plugin.json").exists():
            continue
        try:
            r = load(data_root, d.name)
            out.append({"name": d.name, **r})
        except Exception as e:
            _LOADED[d.name] = {"meta": {}, "tools": [], "module_name": "", "error": f"{type(e).__name__}: {e}"}
            out.append({"name": d.name, "ok": False, "tools": [], "error": str(e)[:200]})
    return out


def scan(data_root: Path) -> list[dict]:
    """插件清单（含加载状态与错误）。"""
    out = []
    for d in sorted(plugins_root(data_root).iterdir()):
        if not d.is_dir():
            continue
        meta = _read_json(d / "plugin.json")
        if not meta and not (d / "plugin.py").exists():
            continue
        info = _LOADED.get(d.name) or {}
        out.append({
            "name": d.name,
            "version": str(meta.get("version") or ""),
            "description": str(meta.get("description") or ""),
            "enabled": meta.get("enabled") is not False,
            "loaded": d.name in _LOADED,
            "tools": list(info.get("tools") or []),
            "error": str(info.get("error") or ""),
            "path": str(d),
        })
    return out


def create(data_root: Path, name: str, description: str, code: str, version: str = "1.0",
           readonly_safe: bool = False, overwrite: bool = False) -> dict:
    d = _plugin_dir(data_root, name)
    if d.exists() and not overwrite:
        raise FileExistsError(f"插件已存在：{name}（覆盖请传 overwrite=true）")
    d.mkdir(parents=True, exist_ok=True)
    (d / "plugin.json").write_text(json.dumps({
        "name": name, "version": version, "description": description,
        "readonly_safe": bool(readonly_safe), "enabled": True,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    (d / "plugin.py").write_text(code or PLUGIN_TEMPLATE, encoding="utf-8")
    result = load(data_root, name)  # 创建即热加载
    return {"name": name, "path": str(d), **result}


def delete(data_root: Path, name: str) -> None:
    d = _plugin_dir(data_root, name)
    if not d.is_dir():
        raise FileNotFoundError(f"插件不存在：{name}")
    _unload(name)
    shutil.rmtree(d)


def set_enabled(data_root: Path, name: str, enabled: bool) -> dict:
    d = _plugin_dir(data_root, name)
    meta = _read_json(d / "plugin.json")
    if not meta:
        raise FileNotFoundError(f"插件不存在或缺少 plugin.json：{name}")
    meta["enabled"] = bool(enabled)
    (d / "plugin.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    if enabled:
        return load(data_root, name)
    _unload(name)
    _LOADED[name] = {"meta": meta, "tools": [], "module_name": "", "error": "已停用"}
    return {"ok": True, "tools": []}


def readonly_safe_tools(data_root: Path) -> set[str]:
    """plugin.json 里标了 readonly_safe 的插件所注册的工具名（只读档放行用）。"""
    out: set[str] = set()
    for name, info in _LOADED.items():
        if (info.get("meta") or {}).get("readonly_safe") is True:
            out.update(info.get("tools") or [])
    return out
