# -*- coding: utf-8 -*-
"""工作目录注册表：最近目录持久化 + 记忆作用域键。

记忆作用域键（workspace）使用规范化后的绝对路径：
  - Windows 下统一大小写与分隔符，保证 C:\\Foo 与 c:/foo 指向同一份目录记忆；
  - 特殊值 "global" 表示全局记忆，所有工作目录可见。
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

GLOBAL_SCOPE = "global"


def normalize_workdir(path: str | Path, base: Path | None = None) -> Path:
    """解析工作目录：相对路径以 base（默认 DATA_ROOT）为基准，并校验为目录。"""
    p = Path(str(path or "")).expanduser()
    if not p.is_absolute():
        p = Path(base or Path.cwd()) / p
    p = p.resolve()
    if not p.is_dir():
        raise NotADirectoryError(f"工作目录不存在或不是目录：{p}")
    return p


def workspace_key(path: str | Path | None) -> str:
    """目录 -> 记忆作用域键。空路径/global 都归入全局作用域。"""
    if path is None:
        return GLOBAL_SCOPE
    s = str(path).strip()
    if not s or s.lower() == GLOBAL_SCOPE or s == "*":
        return GLOBAL_SCOPE
    return os.path.normcase(os.path.normpath(s))


def workspace_name(path: str | Path | None) -> str:
    """给界面用的短名：目录名，盘根用盘符。"""
    try:
        p = Path(str(path or ""))
        return p.name or p.drive or str(p)
    except Exception:
        return str(path or "")


class WorkdirRegistry:
    def __init__(self, data_root: Path, default_workdir: str = "workspace"):
        self.data_root = Path(data_root)
        self.file = self.data_root / "data" / "workdirs.json"
        self.default = self._resolve_default(default_workdir)
        self._lock = threading.Lock()

    def _resolve_default(self, raw: str) -> Path:
        p = Path(str(raw or "workspace")).expanduser()
        if not p.is_absolute():
            p = self.data_root / p
        return p.resolve()

    def _load(self) -> list[dict]:
        try:
            data = json.loads(self.file.read_text(encoding="utf-8"))
            items = data.get("items") if isinstance(data, dict) else data
            return [x for x in (items or []) if isinstance(x, dict) and x.get("path")]
        except Exception:
            return []

    def _save(self, items: list[dict]) -> None:
        self.file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"items": items}, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, self.file)

    def touch(self, path: str | Path, base: Path | None = None) -> dict:
        """登记一次使用。返回 {path,key,name,last_used,uses}。"""
        p = normalize_workdir(path, base)
        key = workspace_key(p)
        now = int(time.time() * 1000)
        with self._lock:
            items = self._load()
            hit = next((x for x in items if workspace_key(x.get("path")) == key), None)
            if hit is None:
                hit = {"path": str(p), "key": key, "name": workspace_name(p),
                       "last_used": now, "uses": 1, "created": now}
                items.insert(0, hit)
            else:
                hit.update({"path": str(p), "key": key, "name": workspace_name(p),
                            "last_used": now, "uses": int(hit.get("uses") or 0) + 1})
                items = [hit] + [x for x in items if x is not hit]
            items = sorted(items, key=lambda x: int(x.get("last_used") or 0), reverse=True)[:60]
            # 同一目录被高频选中时避免每轮对话都写盘
            if hit.get("uses") == 1 or now - int(hit.get("last_used_prev") or 0) > 60_000:
                hit["last_used_prev"] = now
                self._save(items)
        return {k: hit.get(k) for k in ("path", "key", "name", "last_used", "uses")}

    def list(self, include_default: bool = True) -> list[dict]:
        items = self._load()
        if include_default:
            dkey = workspace_key(self.default)
            if not any(workspace_key(x.get("path")) == dkey for x in items):
                items.append({"path": str(self.default), "key": dkey,
                              "name": workspace_name(self.default),
                              "last_used": 0, "uses": 0, "is_default": True})
        out = []
        for x in items:
            p = Path(str(x.get("path") or ""))
            item = dict(x)
            item["key"] = workspace_key(p)
            item["name"] = x.get("name") or workspace_name(p)
            item["exists"] = p.is_dir()
            out.append(item)
        out.sort(key=lambda x: (bool(x.get("is_default")), -int(x.get("last_used") or 0)))
        return out
