"""额度（积分）管理的通用框架。

每接入一个账号制提供方（Qoder / OpenCode …）就注册一个
QuotaProvider：设置面板按 provider 逐个渲染「一套额度设置」，顶栏也按
provider 逐个显示额度 chip。新接一家 = 写一个子类 + 注册一行，前后端不用改。

统一额度结果（前端只认这一种形状）：
    {
        "ok": bool, "error": str,
        "at": int,                    # 抓取时间戳（秒）
        "remaining": float | None,    # 剩余额度
        "unit": str,                  # 单位显示名（credits / 元 / 次 …）
        "plan": str | None,           # 套餐名
        "packages": [                 # 额度包明细（可多个：套餐包 + 加量包 …）
            {"name": str, "used": float, "limit": float, "remaining": float}
        ],
        "usage": {                    # 近期用量 {"2026-10-24": {"calls": 3, "credits": 1.2}}
            "2026-10-24": {"calls": 3, "credits": 1.2}
        },
        "raw": {...}                  # 提供方原始字段（排查用）
    }
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

QUOTA_TTL = 600  # 额度缓存有效期（秒）：10 分钟


class QuotaProvider:
    """一个接入方的额度来源。子类至少实现 fetch()，可选实现 status()/available()/usage()。"""

    id = ""
    name = ""
    unit = "credits"
    note = ""  # 额度来源说明（面板里那行小字）
    cache_file = ""  # 缓存文件名（相对 data 目录）；留空则用 quota_<id>.json

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self._lock = threading.Lock()

    # ---------- 子类实现 ----------
    def fetch(self) -> dict:
        """真实抓取额度，返回提供方原始数据。异常向上抛，由 get() 统一兜底。"""
        raise NotImplementedError

    def normalize(self, raw: dict) -> dict:
        """把原始数据映射到统一形状。子类覆盖。"""
        raw = raw or {}
        return {
            "ok": bool(raw.get("ok", True)),
            "error": str(raw.get("error") or ""),
            "remaining": raw.get("remaining"),
            "unit": self.unit,
            "plan": raw.get("plan"),
            "packages": list(raw.get("packages") or []),
            "usage": dict(raw.get("usage") or {}),
            "raw": raw,
        }

    def available(self) -> bool:
        """该接入方是否已就绪（已安装/已登录等）。子类覆盖。"""
        return True

    def status(self) -> dict:
        """接入方自身状态（登录状态、模型数等），面板顶部那行说明。"""
        return {"ok": True}

    def usage(self) -> dict:
        """近期用量 {"日期": {"calls": n, "credits": x}}。子类覆盖。"""
        return {}

    # ---------- 通用逻辑 ----------
    @property
    def cache_path(self) -> Path:
        return self.data_dir / (self.cache_file or f"quota_{self.id}.json")

    def cached(self) -> dict | None:
        try:
            d = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(d, dict):
            return None
        if d.get("ok") and not d.get("raw"):
            # 旧格式缓存（provider 私有字段）：按当前映射重新归一化，保留原时间戳
            at = d.get("at")
            try:
                d = self.normalize(d)
                if at:
                    d["at"] = at
            except Exception:
                pass
        d.setdefault("unit", self.unit)
        d.setdefault("packages", [])
        d.setdefault("usage", {})
        return d

    def get(self, force: bool = False) -> dict:
        """取额度：命中缓存直接返回，否则真实抓取并落盘。抓取失败不覆盖旧缓存。"""
        now = time.time()
        cached = self.cached()
        if not force and cached and cached.get("ok") and now - float(cached.get("at") or 0) < QUOTA_TTL:
            return cached
        if not self.available():
            return {"ok": False, "error": f"{self.name} 未就绪", "at": int(now),
                    "remaining": None, "unit": self.unit, "plan": None,
                    "packages": [], "usage": {}, "raw": {}}
        with self._lock:
            cached = self.cached()
            if not force and cached and cached.get("ok") and now - float(cached.get("at") or 0) < QUOTA_TTL:
                return cached
            try:
                data = self.normalize(self.fetch())
            except Exception as e:
                data = {"ok": False, "error": f"{type(e).__name__}: {e}"[:200], "at": int(time.time()),
                        "remaining": None, "unit": self.unit, "plan": None,
                        "packages": [], "usage": {}, "raw": {}}
            data["at"] = int(time.time())
            if not data.get("usage"):
                data["usage"] = self.usage()
            if data.get("ok"):
                try:
                    self.cache_path.parent.mkdir(parents=True, exist_ok=True)
                    self.cache_path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
                except OSError:
                    pass
            return data


class QuotaRegistry:
    """额度提供方注册表（按接入方各一个）。"""

    def __init__(self):
        self._providers: dict[str, QuotaProvider] = {}

    def register(self, p: QuotaProvider) -> QuotaProvider:
        self._providers[p.id] = p
        return p

    def get(self, pid: str) -> QuotaProvider | None:
        return self._providers.get(str(pid or ""))

    def providers(self) -> list[QuotaProvider]:
        return list(self._providers.values())

    def describe(self) -> list[dict]:
        """给 /api/quota 用：每家的元信息 + 缓存快照 + 状态（不触网）。"""
        out = []
        for p in self.providers():
            try:
                st = p.status()
            except Exception as e:
                st = {"ok": False, "error": f"{type(e).__name__}: {e}"[:200]}
            try:
                usage = p.usage()
            except Exception:
                usage = {}
            try:
                avail = bool(p.available())
            except Exception:
                avail = False
            out.append({
                "id": p.id,
                "name": p.name,
                "unit": p.unit,
                "note": p.note,
                "available": avail,
                "status": st,
                "quota": p.cached(),
                "usage": usage,
            })
        return out
