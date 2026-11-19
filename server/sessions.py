# -*- coding: utf-8 -*-
"""会话持久化：一个会话一个 JSON 文件，存在数据目录 sessions/ 下。

为什么要搬到后端：
  旧版会话全放在浏览器 localStorage 里（上限约 5MB）。带工具调用的长会话
  （工具输出动辄几 MB）写满后，前端 persistSessions 的兜底逻辑会把列表砍掉一半，
  于是「之前的会话不见了」。改为文件存储后没有容量上限，也不再和浏览器缓存耦合。

设计要点：
  - 原子写（tmp → replace），进程被杀也不会留下半个文件；
  - 列表按 updatedAt 倒序返回；单条损坏的文件跳过而不是整体报错；
  - 兼容迁移：前端首次连上时会把自己 localStorage 里的旧会话整体推上来（replace）。
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,80}$")


class SessionStore:
    def __init__(self, root: Path):
        self.dir = Path(root) / "sessions"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # ---------- 内部 ----------
    def _path(self, sid: str) -> Path:
        if not _ID_RE.match(str(sid or "")):
            raise ValueError(f"非法会话 id：{sid!r}")
        return self.dir / f"{sid}.json"

    def list(self) -> list[dict]:
        out: list[dict] = []
        for p in self.dir.glob("*.json"):
            if p.name.startswith("."):
                continue
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(d, dict) and d.get("id"):
                    d.pop("_pending_team_ids", None)
                    out.append(d)
            except Exception:
                continue  # 单条损坏不影响整体
        out.sort(key=lambda s: s.get("updatedAt") or 0, reverse=True)
        return out

    def _write(self, path: Path, session: dict) -> None:
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(session, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)

    def save(self, s: dict) -> bool:
        if not isinstance(s, dict):
            raise ValueError("session 必须是对象")
        p = self._path(s.get("id"))
        s = {**s, "messages": list(s["messages"]) if isinstance(s.get("messages"), list) else []}
        s.pop("_pending_team_ids", None)
        now = int(time.time() * 1000)
        s.setdefault("createdAt", now)
        s.setdefault("updatedAt", now)
        with self._lock:
            previous = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
            incoming = {m.get("id") for m in s["messages"]}
            pending = set(previous.get("_pending_team_ids", [])) - incoming
            missing = [m for m in previous.get("messages", []) if m.get("id") in pending]
            # Only unacknowledged push messages survive a stale browser snapshot.
            s["messages"].extend(missing)
            s["_pending_team_ids"] = [m["id"] for m in missing]
            if missing:
                s["updatedAt"] = max(s["updatedAt"], previous.get("updatedAt", 0))
            self._write(p, s)
        return True

    def append_team_message(self, sid: str, message: dict) -> None:
        p = self._path(sid)
        now = int(time.time() * 1000)
        with self._lock:
            s = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {
                "id": sid, "title": "新会话", "messages": [], "createdAt": now,
            }
            if any(m.get("id") == message["id"] for m in s["messages"]):
                return
            s["messages"].append(message)
            s.setdefault("_pending_team_ids", []).append(message["id"])
            s["updatedAt"] = now
            self._write(p, s)

    def pending_team_events(self) -> list[dict]:
        events = []
        with self._lock:
            for p in self.dir.glob("*.json"):
                try:
                    s = json.loads(p.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                pending = set(s.get("_pending_team_ids", []))
                for m in s.get("messages", []):
                    if m.get("id") in pending:
                        events.append({
                            "type": "team_message", "id": m["id"], "session_id": s["id"],
                            "team_id": m.get("team_id"), "sender": m.get("sender", {}),
                            "text": m["content"], "at": m.get("at"),
                        })
        return events

    def save_many(self, sessions: list[dict]) -> int:
        n = 0
        for s in sessions or []:
            try:
                self.save(s)
                n += 1
            except Exception:
                continue
        return n

    def delete(self, sid: str) -> bool:
        p = self._path(sid)
        with self._lock:
            if p.exists():
                p.unlink()
                return True
        return False

    def replace(self, sessions: list[dict], drop_missing: bool = False) -> dict:
        """整体覆盖（迁移 / 恢复用）。drop_missing=True 时删掉不在列表里的旧文件。"""
        keep = set()
        n = self.save_many(sessions)
        for s in sessions or []:
            try:
                keep.add(self._path(s.get("id")).name)
            except Exception:
                continue
        removed = 0
        if drop_missing:
            for p in self.dir.glob("*.json"):
                if p.name not in keep:
                    try:
                        p.unlink()
                        removed += 1
                    except Exception:
                        pass
        return {"saved": n, "removed": removed, "dir": str(self.dir)}
