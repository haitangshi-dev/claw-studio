"""临时预览模式：数据不落盘、密钥只在内存。

两件事同时做到：
1. **不留数据** —— 数据根指向系统临时目录下的随机子目录，进程退出时整目录删除。
   记忆库、会话、诊断日志、工具产物、生成的技能，全都在这个临时目录里，结束即消失。
2. **不保存密钥** —— API Key 只存在本进程的内存字典里，不写 config.json、
   不写 data/settings.json、不进 DPAPI 加密文件。关掉页面或退出进程就没了。

密钥在 provider_proxy 里以 `mem:` 前缀传递（与落盘密文区分开），避免被误当成
加密串解密。
"""
from __future__ import annotations

import atexit
import shutil
import tempfile
import threading
from pathlib import Path

EPHEMERAL_ID = "__ephemeral__"

_lock = threading.Lock()
_creds: dict = {}
_temp_root: Path | None = None


def make_temp_root() -> Path:
    """建一个用完即删的数据根。"""
    global _temp_root
    d = Path(tempfile.mkdtemp(prefix="claw-preview-"))
    _temp_root = d

    def _cleanup():
        try:
            shutil.rmtree(str(d), ignore_errors=True)
        except Exception:
            pass

    atexit.register(_cleanup)
    return d


def set_creds(base_url: str, api_key: str, model: str = "") -> dict:
    with _lock:
        _creds.clear()
        _creds.update({
            "baseUrl": str(base_url or "").strip(),
            "apiKey": str(api_key or "").strip(),
            "model": str(model or "").strip(),
        })
    return status()


def get_creds() -> dict:
    with _lock:
        return dict(_creds)


def clear_creds() -> None:
    with _lock:
        _creds.clear()


def status() -> dict:
    c = get_creds()
    key = c.get("apiKey") or ""
    return {
        "active": bool(key),
        "baseUrl": c.get("baseUrl") or "",
        "model": c.get("model") or "",
        "apiKeySet": bool(key),
        "apiKeyMask": (key[:3] + "…" + key[-4:]) if len(key) > 8 else ("已设置" if key else ""),
        "tempRoot": str(_temp_root) if _temp_root else "",
    }
