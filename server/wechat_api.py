# -*- coding: utf-8 -*-
"""微信通道的界面侧接口：消息流、发消息、扫码登录、队列状态。

数据来源就是桥接自己的文件契约（inbox / outbox / state / secure），
这里只做**只读聚合 + 受控写入**，不改桥接的运行方式：
  - 收：inbox/done/*.json（worker 处理完归档的入站消息）
  - 发：outbox/*.json（待发）与 outbox/sent、outbox/expired
所有时间以文件名的毫秒前缀为准（桥接就是这么命名的）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse

import wechat as bridge

router = APIRouter()

DATA_ROOT: Path | None = None
CFG: dict | None = None
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


def configure(data_root: Path, cfg: dict) -> None:
    global DATA_ROOT, CFG
    DATA_ROOT = Path(data_root)
    CFG = cfg


def _bridge() -> Path | None:
    return bridge.resolve_bridge_dir((CFG or {}).get("wechat") or {}, DATA_ROOT)


def _ts_of(path: Path) -> int:
    try:
        return int(path.name.split("-")[0])
    except Exception:
        try:
            return int(path.stat().st_mtime * 1000)
        except OSError:
            return 0


def _read(path: Path) -> dict:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


@router.get("/api/wechat/messages")
def messages(limit: int = 60):
    root = _bridge()
    if root is None:
        return {"ok": False, "items": [], "error": "找不到微信桥接目录"}
    items: list[dict] = []

    # inbox 根目录 = 已收到但 worker 还没消费。原来只读 inbox/done，
    # worker 一卡住（没启动 / 端口冲突 / 崩了）你在微信发的消息在界面上就"消失"了。
    for sub, st in (("", "queued"), ("done", "received"), ("ignored", "ignored")):
        base = (root / "inbox" / sub) if sub else (root / "inbox")
        for p in base.glob("*.json"):
            d = _read(p)
            if not d.get("text"):
                continue
            items.append({
                "id": p.name,
                "dir": "in",
                "text": str(d.get("text") or "")[:4000],
                "at": int(d.get("received_at") or 0) * 1000 or _ts_of(p),
                "state": st,
            })

    for p in (root / "outbox").glob("*.json"):
        d = _read(p)
        items.append({
            "id": p.name, "dir": "out", "text": str(d.get("text") or "")[:4000],
            "at": _ts_of(p), "state": "pending",
            "attempts": int(d.get("_attempts") or 0),
            "error": str(d.get("_last_error") or "")[:160],
        })
    for sub, st in (("sent", "sent"), ("expired", "expired"), ("merged", "merged")):
        for p in (root / "outbox" / sub).glob("*.json"):
            d = _read(p)
            items.append({
                "id": p.name, "dir": "out", "text": str(d.get("text") or "")[:4000],
                "at": _ts_of(p), "state": st,
                "attempts": int(d.get("_attempts") or 0),
                "error": str(d.get("_last_error") or "")[:160],
            })

    items.sort(key=lambda x: x["at"])
    n = max(1, min(int(limit or 60), 300))
    return {"ok": True, "items": items[-n:], "total": len(items)}


@router.post("/api/wechat/send")
def send(payload: dict):
    """手动发一条。没有可用的 context_token 就明确拒绝——硬发只会被微信打回 -2。"""
    text = str((payload or {}).get("text") or "").strip()
    if not text:
        return {"ok": False, "error": "内容为空"}
    root = _bridge()
    if root is None:
        return {"ok": False, "error": "找不到微信桥接目录"}
    binding = _read(root / "secure" / "binding.json")
    to_user = str(binding.get("bound_user_id") or "")
    if not to_user:
        return {"ok": False, "error": "还没绑定微信，先扫码登录"}

    # 直接投递，不等会话窗口。
    # 旧逻辑强制要 context_token，没有就拒绝——用户得先去微信发一条"开窗口"，
    # 体验很差。现在有就用、没有就空着投给 relay：微信 -2 打回时 relay 会按
    # 次数/时长归档，不会无限重试（见 bridge/relay.py drain_outbox）。
    done = sorted((root / "inbox" / "done").glob("*.json"), key=_ts_of)
    ctx = str(_read(done[-1]).get("context_token") or "") if done else ""

    out = root / "outbox"
    out.mkdir(parents=True, exist_ok=True)
    name = f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}.reply.json"
    tmp = out / (name + ".tmp")
    tmp.write_text(json.dumps({"to_user_id": to_user, "context_token": ctx, "text": text,
                               "from_user_id": ""}, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(out / name))
    return {"ok": True, "id": name, "message": "已投递到发件队列"}


@router.post("/api/wechat/logout")
def logout():
    """退出微信账号：停掉桥接并清除本机登录凭证（token/binding/auth）。

    用于把项目交给别人之前清掉自己的账号，或换号重新扫码。
    清掉后状态变成未登录，需要重新扫码绑定。
    """
    import wechat as wechat_mod

    root = _bridge()
    if root is None:
        return {"ok": False, "error": "找不到微信桥接目录"}
    # 先停进程，避免看门狗把 relay/worker 拉回来、或进程内存里还握着旧 token
    try:
        wechat_mod.stop(CFG, DATA_ROOT)
    except Exception:
        pass
    removed = []
    for rel in ("secure/token.json", "secure/binding.json", "state/auth.json",
                "state/cursor.json", "qr/wechat-login.png", "qr/qr-content.txt"):
        p = root / rel
        try:
            if p.exists():
                p.unlink()
                removed.append(rel)
        except OSError:
            pass
    return {"ok": True, "removed": removed, "message": "已退出微信账号，凭证已清除"}


@router.post("/api/wechat/login")
def login():
    """拉起扫码：桥接自带的 login_qr.py 会把二维码渲染成 PNG。"""
    root = _bridge()
    if root is None:
        return {"ok": False, "error": "找不到微信桥接目录"}
    py = bridge.resolve_python((CFG or {}).get("agent") or {})
    if not py:
        return {"ok": False, "error": "找不到 Python 解释器"}
    env = dict(os.environ)
    env["WECHAT_BRIDGE_HOME"] = str(root)
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        kw = {"cwd": str(root), "env": env, "stdout": subprocess.DEVNULL,
              "stderr": subprocess.DEVNULL, "stdin": subprocess.DEVNULL}
        if _NO_WINDOW:
            kw["creationflags"] = _NO_WINDOW
        subprocess.Popen([py, str(root / "login_qr.py")], **kw)
        return {"ok": True, "message": "已拉起扫码窗口，请在微信里扫描"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:200]}


@router.get("/api/wechat/qr")
def qr():
    root = _bridge()
    if root is None:
        return JSONResponse({"ok": False, "error": "找不到桥接目录"}, status_code=404)
    p = root / "qr" / "wechat-login.png"
    if not p.is_file():
        return JSONResponse({"ok": False, "error": "还没有二维码，先点扫码登录"}, status_code=404)
    # 加时间戳防止浏览器缓存旧码
    return FileResponse(str(p), media_type="image/png",
                        headers={"Cache-Control": "no-store", "X-Qr-Mtime": str(int(p.stat().st_mtime))})
