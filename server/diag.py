"""诊断日志：把「什么时候、哪个会话、哪条通道、做了什么、为什么失败」记成一条可查的记录。

和 tool-audit.jsonl 的分工：
- tool-audit：每一次工具调用的完整参数与结果（审计用，量大、只关心工具）。
- diag：请求级与异常级的事件（定位错误用，量小、带 traceback 和相关上下文）。

设计约束：
- 同步写（append 一行，微秒级），但在锁内做，避免多线程交错；
- 内存留最近 800 条供界面秒开，磁盘留全量、超 8MB 轮转；
- 任何写日志的失败都不能影响主流程（全部 try/except 吞掉）。
"""
from __future__ import annotations

import json
import threading
import time
import traceback
from collections import deque
from pathlib import Path

_LOCK = threading.Lock()
_BUF: deque[dict] = deque(maxlen=800)
_PATH: Path | None = None
_MAX_BYTES = 8 * 1024 * 1024
_LEVELS = {"debug": 10, "info": 20, "warn": 30, "error": 40}


def configure(root: Path) -> None:
    global _PATH
    try:
        d = Path(root) / "logs"
        d.mkdir(parents=True, exist_ok=True)
        _PATH = d / "diag.jsonl"
    except Exception:
        _PATH = None


def _rotate_if_needed() -> None:
    if _PATH is None:
        return
    try:
        if _PATH.exists() and _PATH.stat().st_size > _MAX_BYTES:
            bak = _PATH.with_suffix(".jsonl.1")
            if bak.exists():
                bak.unlink()
            _PATH.replace(bak)
    except Exception:
        pass


def record(level: str, event: str, **fields) -> None:
    """写一条。fields 里常见键：session / model / channel / elapsed_ms / detail / error / step。"""
    try:
        lv = level if level in _LEVELS else "info"
        item = {
            "ts": int(time.time() * 1000),
            "level": lv,
            "event": str(event)[:80],
        }
        for k, v in fields.items():
            if v is None or v == "":
                continue
            if isinstance(v, (int, float, bool)):
                item[k] = v
            else:
                item[k] = str(v)[:4000]
        with _LOCK:
            _BUF.append(item)
            if _PATH is not None:
                _rotate_if_needed()
                with open(_PATH, "a", encoding="utf-8") as f:
                    f.write(json.dumps(item, ensure_ascii=False) + "\n")
    except Exception:
        pass


def info(event: str, **fields) -> None:
    record("info", event, **fields)


def warn(event: str, **fields) -> None:
    record("warn", event, **fields)


def error(event: str, exc: BaseException | None = None, **fields) -> None:
    if exc is not None:
        fields.setdefault("error", f"{type(exc).__name__}: {exc}"[:1500])
        fields.setdefault("traceback", "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__))[-4000:])
    record("error", event, **fields)


def recent(limit: int = 200, level: str = "", q: str = "") -> list[dict]:
    """最近的日志（新→旧）。level 为「至少这个级别」，q 做全文包含匹配。"""
    with _LOCK:
        items = list(_BUF)[::-1]
    if level and level in _LEVELS:
        floor = _LEVELS[level]
        items = [x for x in items if _LEVELS.get(x.get("level", "info"), 20) >= floor]
    if q:
        needle = q.lower()
        items = [x for x in items
                 if needle in json.dumps(x, ensure_ascii=False).lower()]
    n = max(1, min(int(limit or 200), 800))
    return items[:n]


def stats() -> dict:
    with _LOCK:
        n = len(_BUF)
        errs = sum(1 for x in _BUF if x.get("level") == "error")
        warns = sum(1 for x in _BUF if x.get("level") == "warn")
    size = 0
    try:
        if _PATH is not None and _PATH.exists():
            size = _PATH.stat().st_size
    except Exception:
        pass
    return {"buffered": n, "errors": errs, "warns": warns,
            "file": str(_PATH) if _PATH else "", "fileBytes": size}


def clear() -> None:
    with _LOCK:
        _BUF.clear()
    try:
        if _PATH is not None and _PATH.exists():
            _PATH.unlink()
    except Exception:
        pass
