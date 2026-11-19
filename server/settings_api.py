# -*- coding: utf-8 -*-
"""统一设置（/api/settings）与推理供应商（/api/providers）。

设计取舍：
  - 设置只有**一个真源**：``data/settings.json``。前端不再往 localStorage 里塞半套配置。
  - 但后端还有一堆老代码直接读 ``config.json``（notify / wechat / agent.onduty_permission…），
    所以每次保存都做一次「向下投影」，把新结构里对应的值写回 config.json，
    老代码不用改、也不会出现两套配置打架。
  - API Key 用 DPAPI 加密后存在 settings.json 里，接口永远只返回 apiKeySet + 掩码。
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from secretbox import mask, protect, unprotect

router = APIRouter()

DATA_ROOT: Path | None = None
CONFIG_PATH: Path | None = None
CFG: dict | None = None
SETTINGS_PATH: Path | None = None

DEFAULTS: dict[str, Any] = {
    "appearance": {"theme": "warm", "followSystem": False, "radius": 12, "density": "comfortable"},
    "general": {
        "language": "zh",
        "splash": "orbit",
        "splashEnabled": True,
        "sendKey": "enter",
        "restoreLast": False,
        "keepDraft": True,
    },
    "providers": [],
    "roles": {"smart": "", "fast": "", "vision": "", "compact": "", "embedding": ""},
    "permission": {
        "onDuty": "ask",
        "unattendedPolicy": "safe",
        "confirmDestructive": True,
        "scope": "tool_dir",
        "timeout": 120,
        "showFullArgs": False,
    },
    "memory": {
        "autoExtract": True,
        "autoConsolidate": True,
        "budgetRatio": 6,
        "forgetConfirm": True,
        "halfLife": 30,
        "topK": 4,
    },
    "notify": {"system": True, "minSeconds": 60, "wechat": False},
    "wechat": {"enabled": False, "autoStart": False, "style": "plain", "replay": "digest", "media": False},
    "automation": {"missed": "once", "wakeModel": True, "keep": 50},
}

_PERM_TO_LEGACY = {"readonly": "readonly", "ask": "standard", "standard": "standard", "full": "full"}


# ---------------- 基础工具 ----------------
def configure(data_root: Path, config_path: Path, cfg: dict) -> None:
    """由 main.py 在启动时注入运行时路径与配置对象。"""
    global DATA_ROOT, CONFIG_PATH, CFG, SETTINGS_PATH
    DATA_ROOT = Path(data_root)
    CONFIG_PATH = Path(config_path)
    CFG = cfg
    SETTINGS_PATH = DATA_ROOT / "data" / "settings.json"
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)


def _deep_merge(base: dict, patch: dict) -> dict:
    out = dict(base or {})
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _atomic_write(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(path))


def _seed_from_legacy(cfg: dict) -> dict:
    """旧 config.json 里的值搬进新结构，避免用户升级后设置被重置。"""
    s = json.loads(json.dumps(DEFAULTS))
    n = cfg.get("notify") or {}
    if "enabled" in n:
        s["notify"]["system"] = bool(n.get("enabled"))
    if "on_done_min_seconds" in n:
        s["notify"]["minSeconds"] = int(n.get("on_done_min_seconds") or 60)
    w = n.get("wechat") or {}
    if "enabled" in w:
        s["notify"]["wechat"] = bool(w.get("enabled"))
    cw = cfg.get("wechat") or {}
    s["wechat"]["enabled"] = bool(cw.get("auto_start"))
    s["wechat"]["autoStart"] = bool(cw.get("auto_start"))
    perm = str((cfg.get("agent") or {}).get("onduty_permission") or "").strip()
    if perm in ("readonly", "full"):
        s["permission"]["onDuty"] = perm
    return s


def load_settings() -> dict:
    if SETTINGS_PATH is None:
        raise RuntimeError("settings_api 未初始化")
    raw = {}
    if SETTINGS_PATH.exists():
        try:
            raw = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except Exception:
            raw = {}
    base = _seed_from_legacy(CFG or {}) if not raw else json.loads(json.dumps(DEFAULTS))
    return _deep_merge(base, raw)


def save_settings(settings: dict) -> dict:
    merged = _deep_merge(json.loads(json.dumps(DEFAULTS)), settings or {})
    _atomic_write(SETTINGS_PATH, merged)
    _project_legacy(merged)
    return merged


def _project_legacy(s: dict) -> None:
    """把新结构投影回 config.json，让仍在读旧键的代码保持一致。"""
    if not (CFG is not None and CONFIG_PATH):
        return
    n = s.get("notify") or {}
    CFG["notify"] = {
        **(CFG.get("notify") or {}),
        "enabled": bool(n.get("system", True)),
        "on_done_min_seconds": int(n.get("minSeconds", 60) or 0),
        "wechat": {
            **(CFG.get("notify") or {}).get("wechat", {}),
            "enabled": bool(n.get("wechat", False)),
            "min_seconds": 0,
        },
    }
    w = s.get("wechat") or {}
    CFG["wechat"] = {**(CFG.get("wechat") or {}), "auto_start": bool(w.get("enabled") or w.get("autoStart"))}
    perm = (s.get("permission") or {}).get("onDuty", "ask")
    CFG.setdefault("agent", {})["onduty_permission"] = _PERM_TO_LEGACY.get(perm, "standard")

    # 角色绑定 → 旧键：以前这两者各存一份、互不相干，设置页绑了模型实际不生效。
    # smart 写进 default_model，其余写进 experts.{vision,compact,fast}，老代码直接读得到。
    roles = s.get("roles") or {}
    ex = dict(CFG.get("experts") or {})
    for _rk in ("vision", "compact", "fast"):
        if str(roles.get(_rk) or "").strip():
            ex[_rk] = str(roles[_rk]).strip()
    CFG["experts"] = ex
    smart = str(roles.get("smart") or "").strip()
    if smart:
        CFG["default_model"] = smart
    else:
        dm = str(CFG.get("default_model") or "").strip()
        ids = [str(p.get("id")) for p in (s.get("providers") or [])]
        if dm.startswith("api:") and not any(dm.startswith(f"api:{i}/") for i in ids):
            CFG["default_model"] = ""
    try:
        disk = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        disk = {}
    disk["notify"] = CFG["notify"]
    disk["wechat"] = CFG["wechat"]
    disk.setdefault("agent", {})["onduty_permission"] = CFG["agent"]["onduty_permission"]
    if CFG.get("experts"):
        disk["experts"] = CFG["experts"]
    disk["default_model"] = str(CFG.get("default_model") or "")
    try:
        _atomic_write(CONFIG_PATH, disk)
    except Exception:
        pass


# ---------------- 供应商视图 ----------------
def _public_provider(p: dict, with_key: bool = False) -> dict:
    key = unprotect(p.get("_apiKey") or "")
    out = {
        "id": p.get("id"),
        "name": p.get("name", ""),
        "baseUrl": p.get("baseUrl", ""),
        "model": p.get("model", ""),
        "models": p.get("models") or [],
        "timeout": int(p.get("timeout") or 120),
        "proxy": p.get("proxy", ""),
        "apiKeySet": bool(key),
        "apiKeyMask": mask(key),
        "createdAt": p.get("createdAt", 0),
    }
    if with_key:
        out["apiKey"] = key
    return out


def public_settings(s: dict) -> dict:
    out = json.loads(json.dumps({k: v for k, v in s.items() if k != "providers"}))
    out["providers"] = [_public_provider(p) for p in (s.get("providers") or [])]
    return out


def _merge_incoming_providers(current: list[dict], incoming: list[dict]) -> list[dict]:
    """按 id 合并：前端回传的 apiKey 为空表示「不改密钥」，保留已存密文。"""
    by_id = {p.get("id"): p for p in (current or [])}
    out: list[dict] = []
    for p in incoming or []:
        pid = str(p.get("id") or "").strip() or f"p_{uuid.uuid4().hex[:8]}"
        prev = by_id.get(pid, {})
        plain = str(p.get("apiKey") or "").strip()
        enc = protect(plain) if plain else (prev.get("_apiKey") or "")
        out.append({
            "id": pid,
            "name": str(p.get("name") or "").strip(),
            "baseUrl": str(p.get("baseUrl") or "").strip().rstrip("/"),
            "model": str(p.get("model") or "").strip(),
            "models": list(p.get("models") or []),
            "timeout": int(p.get("timeout") or 120),
            "proxy": str(p.get("proxy") or "").strip(),
            "_apiKey": enc,
            "createdAt": prev.get("createdAt") or int(time.time() * 1000),
        })
    # 保留前端没带上来的旧供应商（例如另一个窗口刚加的）
    incoming_ids = {p["id"] for p in out}
    for pid, p in by_id.items():
        if pid not in incoming_ids:
            out.append(p)
    return out


# ---------------- 路由 ----------------
@router.get("/api/settings")
def get_settings():
    s = load_settings()
    return {"ok": True, "settings": public_settings(s)}


@router.patch("/api/settings")
async def patch_settings(request: dict):
    cur = load_settings()
    incoming = dict(request or {})
    # 供应商单独合并，避免把密钥写进明文
    if "providers" in incoming:
        incoming["providers"] = _merge_incoming_providers(cur.get("providers") or [], incoming.get("providers") or [])
    merged = _deep_merge(cur, incoming)
    if not merged.get("providers"):
        merged["providers"] = cur.get("providers") or []
    saved = save_settings(merged)
    return {"ok": True, "settings": public_settings(saved)}


@router.get("/api/providers")
def list_providers():
    s = load_settings()
    return {"ok": True, "providers": [_public_provider(p) for p in (s.get("providers") or [])]}


@router.post("/api/providers")
async def upsert_provider(payload: dict):
    """新增或按 id 覆盖一个供应商；apiKey 留空表示沿用已存的密文。"""
    cur = load_settings()
    items = list(cur.get("providers") or [])
    cur["providers"] = _merge_incoming_providers(items, [payload or {}])
    saved = save_settings(cur)
    return {"ok": True, "providers": [_public_provider(p) for p in (saved.get("providers") or [])]}


@router.delete("/api/providers/{pid}")
def delete_provider(pid: str):
    cur = load_settings()
    before = len(cur.get("providers") or [])
    cur["providers"] = [p for p in (cur.get("providers") or []) if p.get("id") != pid]
    if len(cur["providers"]) == before:
        raise HTTPException(status_code=404, detail="供应商不存在")
    # 角色绑定指向它的一并清掉
    roles = dict(cur.get("roles") or {})
    for k, v in list(roles.items()):
        if str(v).startswith(f"{pid}:") or str(v).startswith(f"api:{pid}/"):
            roles[k] = ""
    cur["roles"] = roles
    # 默认模型如果指向这个供应商，一并清掉：否则下次对话直接报「未知模型」，
    # 而且用户找不到原因（设置页里供应商已经没了）。
    if CFG is not None and str(CFG.get("default_model") or "").startswith(f"api:{pid}/"):
        CFG["default_model"] = ""
    saved = save_settings(cur)
    return {"ok": True, "providers": [_public_provider(p) for p in (saved.get("providers") or [])]}


@router.post("/api/providers/test")
async def test_provider(payload: dict):
    """探活：拉一次 /models。只做连通性与鉴权判断，不落库。"""
    import httpx

    base = str((payload or {}).get("baseUrl") or "").strip().rstrip("/")
    if not base:
        return {"ok": False, "error": "缺少 Base URL"}
    key = str((payload or {}).get("apiKey") or "").strip()
    if not key:
        pid = str((payload or {}).get("id") or "")
        if pid:
            s = load_settings()
            found = next((p for p in (s.get("providers") or []) if p.get("id") == pid), {})
            key = unprotect(found.get("_apiKey") or "")
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    # base 可能填到根（https://api.xxx.com）也可能填到 /v1，统一交给 provider_proxy 归一化，
    # 否则「少写 /v1」会让探活 404、用户以为 key 填错了。
    import provider_proxy as _pp

    try:
        _chat, url = _pp.endpoints(base)
    except ValueError:
        url = f"{base}/models"
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as cli:
            r = await cli.get(url, headers=headers)
        if r.status_code == 401:
            return {"ok": False, "error": "鉴权失败（401）：API Key 不对或没有权限"}
        if r.status_code >= 400:
            return {"ok": False, "error": f"HTTP {r.status_code}：{r.text[:160]}"}
        data = r.json()
        items = data.get("data") if isinstance(data, dict) else None
        models = [str(m.get("id")) for m in (items or []) if isinstance(m, dict) and m.get("id")]
        return {"ok": True, "models": models[:200], "count": len(models)}
    except httpx.HTTPError as e:
        return {"ok": False, "error": f"连接失败：{type(e).__name__}: {e}"[:200]}
