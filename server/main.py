# -*- coding: utf-8 -*-
"""Claw Agent · P1 后端：模型管理 + SSE 流式对话。

运行:
    python server/main.py

接口:
    GET  /api/health          框架与推理服务状态
    GET  /api/models          模型列表（双目录扫描）+ 当前模型
    POST /api/models/refresh  重新扫描模型目录
    POST /api/models/switch   {"key": "<模型key>"} 切换模型（必要时重启 llama-server）
    GET  /api/status          llama-server 状态
    POST /api/chat            SSE 流式对话（事件: reasoning / content / tool_calls / done / error）

参数合并顺序：config.defaults → 模型 profile → 请求级覆盖。
思考预算走请求级 reasoning_budget_tokens（2026-10-03 实测确认的原生字段）。
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import secrets
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

# ---------- 路径布局（兼容源码运行与 PyInstaller 打包）----------
# 源码模式：BUNDLE = DATA_ROOT = 项目根
# 打包模式：BUNDLE = 解压的只读资源（web/dist、config 模板）；DATA_ROOT = exe 所在目录（数据写入这里）
FROZEN = bool(getattr(sys, "frozen", False))
if FROZEN:
    BUNDLE = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    DATA_ROOT = Path(sys.executable).resolve().parent
else:
    BUNDLE = Path(__file__).resolve().parent.parent
    DATA_ROOT = BUNDLE
ROOT = DATA_ROOT  # 兼容旧引用：数据根
sys.path.insert(0, str(BUNDLE / "server"))

# 临时预览模式（--ephemeral）：数据根改到系统临时目录，进程退出整目录删除——
# 记忆库、会话、诊断日志、工具产物、临时生成的技能全在里面，结束即不留痕。
EPHEMERAL = "--ephemeral" in sys.argv
if EPHEMERAL:
    import ephemeral as _eph_mod

    DATA_ROOT = _eph_mod.make_temp_root()
    ROOT = DATA_ROOT
    # 技能/插件在临时模式下复制一份进临时目录：
    # 这样扫描得到的是仓库里那批，同时「新建技能」写在临时目录里，退出即删，
    # 不会把你试用时生成的技能留在仓库里。
    for _sub in ("skills", "plugins"):
        _src = BUNDLE / _sub
        if _src.exists():
            try:
                shutil.copytree(str(_src), str(DATA_ROOT / _sub), dirs_exist_ok=True)
            except Exception:
                pass

# windowed（无控制台）打包：必须在任何三方库导入前把 stdout/stderr 指到日志文件——
# 否则 jieba 等在导入期创建 logging StreamHandler 时绑定到 None，之后每次日志都报
# "'NoneType' object has no attribute 'write'" 噪声。
if FROZEN and (sys.stdout is None or sys.stderr is None):
    try:
        _log_dir = DATA_ROOT / "logs"
        _log_dir.mkdir(parents=True, exist_ok=True)
        _log_file = _log_dir / "framework.log"
        try:
            # 长跑一直追加（uvicorn 每个请求一行），启动时轮转一次，只留一个备份
            if _log_file.exists() and _log_file.stat().st_size > 8 * 1024 * 1024:
                os.replace(str(_log_file), str(_log_dir / "framework.log.1"))
        except OSError:
            pass
        _logf = open(_log_file, "a", encoding="utf-8", buffering=1)
        if sys.stdout is None:
            sys.stdout = _logf
        if sys.stderr is None:
            sys.stderr = _logf
    except Exception:
        pass
logging.getLogger("jieba").setLevel(logging.WARNING)  # 静音分词库的启动日志

# config：优先数据目录（用户可改），缺失时从打包资源复制一份
CONFIG_DIR = DATA_ROOT / "config"
if not (CONFIG_DIR / "config.json").exists() and (BUNDLE / "config" / "config.json").exists():
    try:
        shutil.copytree(BUNDLE / "config", CONFIG_DIR, dirs_exist_ok=True)
    except Exception:
        pass

from model_registry import scan_models  # noqa: E402
from llm_server import LlamaServer  # noqa: E402
import winproc as _winproc  # noqa: E402
from agent_loop import run_agent  # noqa: E402
import live as live_mod  # noqa: E402
from lenient_json import parse_lenient  # noqa: E402
from sessions import SessionStore  # noqa: E402
from memory.store import get_store  # noqa: E402
from memory.inject import build_memory_block  # noqa: E402
from tools import TOOL_SPECS, execute_tool  # noqa: E402
from workdirs import (  # noqa: E402
    GLOBAL_SCOPE,
    WorkdirRegistry,
    normalize_workdir,
    workspace_key,
    workspace_name,
)
from native_folder import pick_folder as pick_native_folder  # noqa: E402
from sandbox import PERMISSION_LEVELS, normalize_permission  # noqa: E402
from qoder import QoderProvider, QoderQuotaProvider  # noqa: E402
from quota import QuotaRegistry  # noqa: E402
import skills as skills_mod  # noqa: E402
import plugins as plugins_mod  # noqa: E402
import updater  # noqa: E402
import pet as pet_mod  # noqa: E402  桌面宠物（启动/停止/自动启动）
import wechat as wechat_mod  # noqa: E402  微信桥接（启动/停止/自动启动）
import voice as voice_mod  # noqa: E402  本地语音识别 + Edge TTS 朗读

import httpx  # noqa: E402
from urllib.parse import quote  # noqa: E402
from fastapi import FastAPI, Request, Response  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel  # noqa: E402

CONFIG_PATH = CONFIG_DIR / "config.json"
PROFILES_PATH = CONFIG_DIR / "models.json"


def load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


CFG = load_json(CONFIG_PATH)
PROFILES: dict = load_json(PROFILES_PATH).get("profiles", {})
REGISTRY: dict = scan_models(CFG["model_scan_dirs"])
CLOUD_ONLY = bool(CFG.get("cloud_only"))
SERVER = LlamaServer(CFG, DATA_ROOT)
QD = QoderProvider(DATA_ROOT)       # 云端模型提供方（Qoder CLI 登录态，官方无头调用）
QD.max_model_retries = int((CFG.get("agent") or {}).get("max_model_request_retries", 5) or 5)
QD.max_runtime = float((CFG.get("agent") or {}).get("qoder_max_runtime_seconds", 0) or 0)  # 0 = 不设上限
QD.stall_timeout = float((CFG.get("agent") or {}).get("qoder_stall_seconds", 1800) or 1800)
# 额度（积分）提供方注册表：接一家就注册一个，设置面板自动多一套额度卡片
QUOTA = QuotaRegistry()
QUOTA.register(QoderQuotaProvider(QD, DATA_ROOT / "data"))
# 桌面宠物的实时活动句柄（每次对话在 /api/chat 里重新创建；见 server/live.py）
LIVE = None
SELF_PORT: int | None = None        # 主程序启动时写入（垫片自调用用）
try:
    _env_port = int(os.environ.get("CLAW_PORT") or 0)
    if _env_port:
        SELF_PORT = _env_port
except (TypeError, ValueError):
    pass


def _seed_skills() -> None:
    """首次运行播种一个示例技能（让模型学会技能约定；用户可删）。"""
    try:
        import skills as _sk
        if _sk.scan(DATA_ROOT):
            return
        _sk.create(
            DATA_ROOT, "system_report", "生成系统体检报告（磁盘/内存/关键目录占用）",
            skill_md=(
                "# system_report\n\n"
                "生成一份本机系统体检报告。\n\n"
                "## 运行\n- `skill_run(name=\"system_report\")` 无需参数\n\n"
                "## 输出\n- 磁盘占用、内存、工作目录大小的文本报告\n"
            ),
            script=(
                "# -*- coding: utf-8 -*-\n"
                "import json, os, shutil, sys, ctypes\n"
                "out = {}\n"
                "try:\n"
                "    total, used, free = shutil.disk_usage(os.path.abspath(os.sep))\n"
                "    out['disk'] = {'total_gb': round(total/2**30,1), 'used_pct': round(used/total*100,1), 'free_gb': round(free/2**30,1)}\n"
                "except Exception as e:\n"
                "    out['disk'] = str(e)\n"
                "try:\n"
                "    class MEMORYSTATUSEX(ctypes.Structure):\n"
                "        _fields_ = [('dwLength', ctypes.c_ulong), ('dwMemoryLoad', ctypes.c_ulong),\n"
                "                    ('ullTotalPhys', ctypes.c_ulonglong), ('ullAvailPhys', ctypes.c_ulonglong),\n"
                "                    ('ullTotalPageFile', ctypes.c_ulonglong), ('ullAvailPageFile', ctypes.c_ulonglong),\n"
                "                    ('ullTotalVirtual', ctypes.c_ulonglong), ('ullAvailVirtual', ctypes.c_ulonglong),\n"
                "                    ('ullAvailExtendedVirtual', ctypes.c_ulonglong)]\n"
                "    m = MEMORYSTATUSEX(); m.dwLength = ctypes.sizeof(MEMORYSTATUSEX)\n"
                "    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))\n"
                "    out['memory'] = {'load_pct': m.dwMemoryLoad, 'total_gb': round(m.ullTotalPhys/2**30,1), 'avail_gb': round(m.ullAvailPhys/2**30,1)}\n"
                "except Exception as e:\n"
                "    out['memory'] = str(e)\n"
                "wd = os.getcwd()\n"
                "size = 0; n = 0\n"
                "for root, dirs, files in os.walk(wd):\n"
                "    for f in files:\n"
                "        try:\n"
                "            size += os.path.getsize(os.path.join(root, f)); n += 1\n"
                "        except OSError:\n"
                "            pass\n"
                "out['workdir'] = {'path': wd, 'files': n, 'size_mb': round(size/2**20,1)}\n"
                "print(json.dumps(out, ensure_ascii=False, indent=2))\n"
            ),
            author="system",
        )
    except Exception:
        pass


_seed_skills()


def _seed_plugins() -> None:
    """首次运行播种示例插件（http_request：任意 HTTP 请求），并加载全部插件。"""
    try:
        import plugins as _pl
        if not _pl.scan(DATA_ROOT):
            _pl.create(
                DATA_ROOT, "http_request", "任意 HTTP 请求（GET/POST…），用来看接口/抓数据",
                code=(
                    "# -*- coding: utf-8 -*-\n"
                    "def register(api):\n"
                    "    import httpx\n"
                    "    def http_request(args, workdir, cfg):\n"
                    "        method = str(args.get('method') or 'GET').upper()\n"
                    "        url = str(args.get('url') or '').strip()\n"
                    "        if not url.startswith(('http://', 'https://')):\n"
                    "            url = 'https://' + url\n"
                    "        headers = args.get('headers') if isinstance(args.get('headers'), dict) else None\n"
                    "        body = args.get('body')\n"
                    "        kwargs = {'timeout': 30, 'follow_redirects': True}\n"
                    "        if headers:\n"
                    "            kwargs['headers'] = headers\n"
                    "        if body is not None and method in ('POST', 'PUT', 'PATCH'):\n"
                    "            kwargs['json'] = body\n"
                    "        r = httpx.request(method, url, **kwargs)\n"
                    "        return f'HTTP {r.status_code}\\n--- 响应前 4000 字 ---\\n' + r.text[:4000]\n"
                    "    api.register_tool(\n"
                    "        {'type': 'function', 'function': {\n"
                    "            'name': 'http_request',\n"
                    "            'description': '发起任意 HTTP 请求（GET/POST/PUT…），返回状态码与响应正文前 4000 字。',\n"
                    "            'parameters': {'type': 'object', 'properties': {\n"
                    "                'url': {'type': 'string', 'description': '完整 URL'},\n"
                    "                'method': {'type': 'string', 'description': 'GET/POST/PUT/PATCH，默认 GET'},\n"
                    "                'headers': {'type': 'object', 'description': '请求头（可选）'},\n"
                    "                'body': {'type': 'object', 'description': 'JSON 请求体（POST/PUT/PATCH 用，可选）'},\n"
                    "            }, 'required': ['url']}},\n"
                    "        },\n"
                    "        http_request,\n"
                    "    )\n"
                ),
                readonly_safe=True,
            )
    except Exception:
        pass
    try:
        plugins_mod.load_all(DATA_ROOT)
    except Exception:
        pass


_seed_plugins()


def _exit_hook() -> None:
    """自更新前的清理：停掉托管的 llama-server 再退出。"""
    try:
        SERVER.stop()
    except Exception:
        pass


updater.EXIT_HOOK = _exit_hook


import threading as _threading  # noqa: E402


def _warm_qoder_bg() -> None:
    """后台预热 Qoder 模型目录（首次调用 CLI 较慢，避免卡住 /api/models）。"""
    try:
        QD.models(force=True)
    except Exception:
        pass


_threading.Thread(target=_warm_qoder_bg, daemon=True).start()


def _auto_pet_bg() -> None:
    """启动后延迟数秒拉起桌面宠物（仅当 config.pet.auto_start 开启；失败静默）。"""
    import time as _t

    _t.sleep(6)
    try:
        pet_mod.auto_start(CFG.get("pet") or {})
    except Exception:
        pass


_threading.Thread(target=_auto_pet_bg, daemon=True).start()


def _auto_wechat_bg() -> None:
    """启动后延迟数秒拉起微信桥接（仅当 config.wechat.auto_start 开启；失败静默）。"""
    import time as _t

    _t.sleep(8)
    try:
        wechat_mod.auto_start(CFG, DATA_ROOT)
    except Exception:
        pass


_threading.Thread(target=_auto_wechat_bg, daemon=True).start()

MEM_CFG: dict = dict(CFG.get("memory", {}))
MEM_CFG.setdefault("auto_promote_importance", 4)  # 目录记忆重要度达到此值自动提取到全局
STORE = get_store(
    DATA_ROOT / MEM_CFG.get("db", "memory/memory.db"),
    DATA_ROOT / MEM_CFG.get("export_dir", "memory/export"),
    MEM_CFG,
)
STORE.archive_maintenance()
WORKDIRS = WorkdirRegistry(
    DATA_ROOT, str(CFG.get("agent", {}).get("default_workdir", "workspace"))
)


def _migrate_legacy_workspaces() -> None:
    """把旧版 MCP 通道写下的目录名作用域（如 workspace）归一到绝对路径键。"""
    try:
        scopes = STORE.workspace_scopes()
        by_name: dict[str, str] = {}
        dup: set[str] = set()

        def remember(name: str, key: str) -> None:
            n = str(name or "").strip().lower()
            if not n:
                return
            if n in by_name and by_name[n] != key:
                dup.add(n)
            by_name[n] = key

        remember(workspace_name(WORKDIRS.default), workspace_key(WORKDIRS.default))
        for it in WORKDIRS.list(include_default=False):
            remember(str(it.get("name") or workspace_name(it.get("path") or "")),
                     workspace_key(it.get("path") or ""))
        # 库里已有的规范路径键也参与名称匹配（目录可能已不在最近列表里）
        for s in scopes:
            if s != GLOBAL_SCOPE and Path(s).is_absolute():
                remember(Path(s).name, s)

        mapping: dict[str, str] = {}
        for s in scopes:
            if s == GLOBAL_SCOPE or Path(s).is_absolute():
                continue
            key = by_name.get(s.strip().lower())
            if key and s.strip().lower() not in dup:
                mapping[s] = key
        if mapping:
            n = STORE.remap_workspaces(mapping)
            print(f"[memory] 已归一历史目录作用域 {len(mapping)} 个，共 {n} 条")
    except Exception as e:
        print(f"[memory] 历史目录作用域归一失败：{type(e).__name__}: {e}")


_migrate_legacy_workspaces()

app = FastAPI(title="Claw Agent", version="0.1.0-P1")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------- 设置 / API 供应商（新设置页的唯一真源） ----------------
import settings_api as _settings_api  # noqa: E402

_settings_api.configure(DATA_ROOT, CONFIG_PATH, CFG)
app.include_router(_settings_api.router)

import approval as _approval  # noqa: E402

_approval.configure(_settings_api.load_settings)
app.include_router(_approval.router)

import plan as PLAN  # noqa: E402

PLAN.configure(DATA_ROOT)
app.include_router(PLAN.router)

import wechat_api as _wechat_api  # noqa: E402

_wechat_api.configure(DATA_ROOT, CFG)
app.include_router(_wechat_api.router)

import diag as DIAG  # noqa: E402

DIAG.configure(DATA_ROOT)
try:
    import tools as _t0

    DIAG.info("app.start", root=str(DATA_ROOT), port=CFG.get("server", {}).get("port"),
              models=len(_t0.TOOL_SPECS),
              providers=len((_settings_api.load_settings().get("providers") or [])),
              permission=(_settings_api.load_settings().get("permission") or {}).get("onDuty"))
except Exception:
    pass


# ---------------- 临时预览：只在内存里的凭据 ----------------
@app.get("/api/ephemeral")
def ephemeral_status():
    """临时连接状态（不回显 Key，只回掩码）。"""
    import ephemeral as _eph

    return {"ok": True, **_eph.status(), "enabled": EPHEMERAL}


@app.post("/api/ephemeral")
def ephemeral_set(payload: dict):
    """设置临时凭据。只存内存：不写 config.json、不写 settings.json、不做 DPAPI 加密。"""
    import ephemeral as _eph

    base = str((payload or {}).get("baseUrl") or "").strip()
    key = str((payload or {}).get("apiKey") or "").strip()
    model = str((payload or {}).get("model") or "").strip()
    if not base or not key:
        return {"ok": False, "error": "需要 Base URL 和 API Key"}
    if not EPHEMERAL:
        return {"ok": False, "error": "当前不是临时预览模式（启动时加 --ephemeral）"}
    st = _eph.set_creds(base, key, model)
    try:
        import diag as _dg

        _dg.info("ephemeral.set", baseUrl=base, model=model, ephemeral=True)
    except Exception:
        pass
    return {"ok": True, **st}


@app.delete("/api/ephemeral")
def ephemeral_clear():
    import ephemeral as _eph

    _eph.clear_creds()
    return {"ok": True}


@app.get("/api/logs")
def logs_list(limit: int = 200, level: str = "", q: str = ""):
    """诊断日志：新→旧。level 取 debug/info/warn/error（至少该级别），q 全文包含匹配。"""
    return {"ok": True, "items": DIAG.recent(limit, level, q), **DIAG.stats()}


@app.post("/api/logs/clear")
def logs_clear():
    DIAG.clear()
    return {"ok": True}


@app.get("/api/logs/file")
def logs_file():
    """下载完整日志（界面上排查不动时，让用户直接把文件发过来）。"""
    from fastapi.responses import FileResponse

    st = DIAG.stats()
    path = st.get("file") or ""
    if not path or not Path(path).exists():
        return JSONResponse({"ok": False, "error": "还没有日志文件"}, status_code=404)
    return FileResponse(path, filename="claw-diag.jsonl", media_type="application/x-ndjson")


def _memory_budget(model_key: str = "", is_qoder: bool = False) -> int:
    """L1 常驻预算 = 模型上下文 × 设置里的比例，夹在 1200–8000 字。

    固定 1800 字对不同模型的代价完全不同：32K 窗口的 1800 字是 5%，1M 窗口的 1800 字不到 0.2%。
    按比例算，才不会出现「小模型被记忆挤爆 / 大模型记忆饿死」两头不讨好的情况。
    """
    ctx = 32768
    try:
        if is_qoder:
            from qoder import _ctx_for

            ctx = int(_ctx_for(str(model_key).split(":", 1)[-1]))
        elif model_key:
            prof = resolve_profile(model_key) or {}
            ctx = int(prof.get("ctx") or (CFG.get("defaults") or {}).get("ctx") or 32768)
    except Exception:
        pass
    ratio = 6.0
    try:
        ratio = float((_settings_api.load_settings().get("memory") or {}).get("budgetRatio") or 6)
    except Exception:
        pass
    return max(1200, min(8000, int(ctx * ratio / 100.0)))



def sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


async def _guarded_sse(agen):
    """SSE 兜底：下游任何未预期异常都转成流内 error/done，
    避免 generator 抛错被 ASGI 当成未处理异常（连接直接断、前端收不到原因）。"""
    saw_done = False
    try:
        async for item in agen:
            if isinstance(item, str) and '"type": "done"' in item:
                saw_done = True
            yield item
    except asyncio.CancelledError:
        raise
    except Exception as e:
        try:
            DIAG.error("chat.stream", e)
        except Exception:
            pass
        try:
            print(f"[Claw Agent] SSE 流异常: {type(e).__name__}: {e}", file=sys.stderr)
        except Exception:
            pass
        if not saw_done:
            yield sse({"type": "error", "message": f"服务端流异常（{type(e).__name__}）：{str(e)[:200]}"})
            yield sse({"type": "done", "steps": 0})



def _inject_mmproj(key: str, params: dict) -> dict:
    """视觉模型自动带上同目录 mmproj（VL 投影器）。"""
    mm = (REGISTRY.get(key) or {}).get("mmproj")
    if mm and not params.get("mmproj"):
        params = {**params, "mmproj": mm}
    return params


def resolve_profile(key: str | None) -> tuple[str, str, dict]:
    """返回 (key, model_path, params)。未知 key 抛 KeyError。"""
    key = key or CFG["default_model"]
    if key in PROFILES:
        prof = PROFILES[key]
        return key, prof["path"], _inject_mmproj(key, SERVER.merged_params(prof))
    if key in REGISTRY:
        return key, REGISTRY[key]["path"], _inject_mmproj(key, SERVER.merged_params(None))
    raise KeyError(f"未知模型: {key}")


def _match_loaded(cur: str) -> tuple[str, str, dict] | None:
    """反查当前已加载的模型在配置/注册表中的档案。"""
    try:
        cur_res = Path(cur).resolve()
    except Exception:
        return None
    for key, prof in PROFILES.items():
        if Path(prof["path"]).resolve() == cur_res:
            return key, prof["path"], _inject_mmproj(key, SERVER.merged_params(prof))
    for key, info in REGISTRY.items():
        if Path(info["path"]).resolve() == cur_res:
            return key, info["path"], _inject_mmproj(key, SERVER.merged_params(None))
    return None


def resolve_for_chat(req_model: str | None) -> tuple[str, str, dict]:
    """聊天用：显式指定优先；未指定时沿用当前已加载的模型（粘性切换），
    服务未运行时才回退默认模型。"""
    if req_model:
        return resolve_profile(req_model)
    if SERVER.is_running():
        cur = SERVER.current_model_path()
        if cur:
            matched = _match_loaded(cur)
            if matched:
                return matched
    return resolve_profile(None)


def _sanitize_history(messages: list[dict]) -> list[dict]:
    """净化前端回放的历史：tool_calls 参数一律重写成严格合法 JSON。

    llama-server 会对请求里每个 tool_call 的 arguments 做严格校验
    （common/chat.cpp 的 func_args_not_string）；前端存的是流式原文，可能被
    宽容解析修过（含字面换行等），原样回传会让整个请求瞬间 500。
    """
    out: list[dict] = []
    for m in messages:
        tcs = m.get("tool_calls")
        if m.get("role") == "assistant" and isinstance(tcs, list):
            clean = []
            for tc in tcs:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") or {}
                name = fn.get("name")
                if not name:
                    continue
                raw = fn.get("arguments")
                if not isinstance(raw, str):
                    raw = json.dumps(raw, ensure_ascii=False) if isinstance(raw, (dict, list)) else "{}"
                obj = None
                try:
                    obj = json.loads(raw)
                except Exception:
                    obj = None
                if not isinstance(obj, dict):
                    obj, _ = parse_lenient(raw)
                safe = "{}"
                if isinstance(obj, dict):
                    try:
                        safe = json.dumps(obj, ensure_ascii=False, allow_nan=False)
                    except (TypeError, ValueError):
                        safe = "{}"
                clean.append({**tc, "function": {**fn, "arguments": safe}})
            if clean:
                m = {**m, "tool_calls": clean}
            else:
                m = {k: v for k, v in m.items() if k != "tool_calls"}
        out.append(m)
    return out


# ---------------- 模型管理 ----------------
@app.get("/api/health")
def health():
    return {"ok": True, "llama_running": SERVER.is_running(), "models": len(REGISTRY)}


@app.get("/api/models")
def models():
    cloud_only = bool(CFG.get("cloud_only"))
    cur = SERVER.current_model_path() if (SERVER.is_running() and not cloud_only) else None
    out = []
    if not cloud_only:
        for key, info in sorted(REGISTRY.items()):
            out.append(
                {
                    "key": key,
                    "name": info["meta"].get("name") or key,
                    "arch": info["meta"].get("arch", ""),
                    "ctx_train": info["meta"].get("ctx_train"),
                    "size_label": info["meta"].get("size_label", ""),
                    "source": info["source"],
                    "size_mb": info["size_mb"],
                    "has_profile": key in PROFILES,
                    "current": bool(cur and Path(cur).resolve() == Path(info["path"]).resolve()),
                }
            )
    default_key = str(CFG.get("default_model") or "")
    if cloud_only and not (default_key.startswith("qoder:") or default_key.startswith("api:")):
        default_key = "qoder:Auto"
    # Qoder 云端模型（官方 CLI 已安装并登录时并入；失败静默隐藏）
    try:
        if QD.available() and QD.logged_in():
            for qm in QD.models():
                key = f"qoder:{qm['id']}"
                out.append(
                    {
                        "key": key,
                        "name": qm["name"],
                        "arch": "",
                        "ctx_train": qm.get("ctx") or 200000,  # 官方模型上下文（Qoder 文档）
                        "ctx": qm.get("ctx") or 200000,
                        "size_label": "",
                        "source": "qoder",
                        "size_mb": None,
                        "has_profile": False,
                        "current": bool(cloud_only and key == default_key),
                        "cloud": True,
                        "provider": "qoder",
                        "credits": "",
                        "free": False,
                        "tools": True,   # Qoder 自己执行内置工具（不经 Claw 工具循环）
                        "images": True,  # 图片以附件直传（--attachment）
                        "efforts": ["auto", "low", "medium", "high", "xhigh", "max"],
                        "can_off": True,
                        "effort_default": "auto",
                    }
                )
    except Exception:
        pass
    # 临时预览：内存里的凭据也进清单（主界面模型菜单能直接选到）
    try:
        import ephemeral as _eph0

        _c = _eph0.get_creds()
        if _c.get("apiKey") and _c.get("model"):
            out.append({
                "key": f"api:{_eph0.EPHEMERAL_ID}/{_c['model']}",
                "name": f"临时连接（不保存） · {_c['model']}",
                "arch": "", "ctx_train": 128000, "ctx": 128000,
                "size_label": "", "source": "ephemeral", "size_mb": None,
                "has_profile": False, "current": bool(default_key == f"api:{_eph0.EPHEMERAL_ID}/{_c['model']}"),
                "cloud": True, "provider": "api", "tools": True, "images": False,
                "credits": "", "free": False,
            })
    except Exception:
        pass

    # 设置页里配好的 OpenAI 兼容供应商：每个供应商的模型并进清单，
    # key 形如 api:<pid>/<model>，选中它就直接走那家的接口（不再需要另配一套环境）。
    try:
        import provider_proxy as PP

        for pv in (_settings_api.load_settings().get("providers") or []):
            pid = str(pv.get("id") or "")
            if not pid or not pv.get("_apiKey"):
                continue
            names = [str(x) for x in (pv.get("models") or []) if str(x).strip()]
            if not names:
                names = [str(pv.get("model") or "").strip()]
            names = [n for n in names if n]
            if not names:
                continue
            for name in names:
                key = PP.key_for(pid, name)
                out.append(
                    {
                        "key": key,
                        "name": f"{pv.get('name') or pid} · {name}",
                        "arch": "",
                        "ctx_train": 128000,
                        "ctx": 128000,
                        "size_label": "",
                        "source": "provider",
                        "size_mb": None,
                        "has_profile": False,
                        "current": bool(default_key == key),
                        "cloud": True,
                        "provider": "api",
                        "tools": True,    # OpenAI 兼容端点支持 function calling，走本地工具循环
                        "images": False,
                        "credits": "",
                        "free": False,
                    }
                )
            if not default_key and out:
                default_key = PP.key_for(pid, names[0])
    except Exception:
        pass

    return {"models": out, "default": default_key, "current_path": cur, "cloud_only": cloud_only}


@app.get("/api/qoder/status")
def qoder_status():
    """Qoder CLI 接入状态（可用/已登录/模型数/最近用量）。"""
    try:
        st = QD.status()
        st["usage"] = QD.usage()
        return st
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}


# ---------------- 额度（积分）管理：按接入方各一套 ----------------
@app.get("/api/quota")
def quota_list():
    """所有额度提供方的元信息 + 缓存快照（不触网）。"""
    try:
        return {"ok": True, "providers": QUOTA.describe()}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}


@app.get("/api/quota/{pid}")
def quota_one(pid: str, refresh: int = 0):
    """单家额度：refresh=1 强制真实抓取（各家自己实现来源）。"""
    p = QUOTA.get(pid)
    if not p:
        return {"ok": False, "error": f"未知的额度提供方：{pid}"}
    try:
        return p.get(force=bool(refresh))
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}


# ---------------- 通知设置 / 打开目录 ----------------
@app.get("/api/notify")
def notify_get():
    n = dict(CFG.get("notify") or {})
    w = dict(n.get("wechat") or {})
    return {"ok": True, "enabled": n.get("enabled", True),
            "on_done_min_seconds": int(n.get("on_done_min_seconds", 60)),
            "wechat": {"enabled": bool(w.get("enabled", False)),
                       "min_seconds": int(w.get("min_seconds", 0)),
                       "available": _wechat_bridge_root() is not None}}


@app.post("/api/notify")
def notify_set(payload: dict):
    n = dict(CFG.get("notify") or {})
    if "enabled" in payload:
        n["enabled"] = bool(payload.get("enabled"))
    if "on_done_min_seconds" in payload:
        try:
            n["on_done_min_seconds"] = max(0, min(int(payload.get("on_done_min_seconds")), 3600))
        except (TypeError, ValueError):
            pass
    if isinstance(payload.get("wechat"), dict):
        w = dict(n.get("wechat") or {})
        pw = payload["wechat"]
        if "enabled" in pw:
            w["enabled"] = bool(pw.get("enabled"))
        if "min_seconds" in pw:
            try:
                w["min_seconds"] = max(0, min(int(pw.get("min_seconds")), 3600))
            except (TypeError, ValueError):
                pass
        n["wechat"] = w
    try:
        data = load_json(CONFIG_PATH)
    except Exception:
        data = {}
    data["notify"] = n
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        return {"ok": False, "error": f"配置写入失败：{e}"}
    CFG["notify"] = n
    return {"ok": True, **notify_get()}


# ---------------- 语音输出（Edge TTS 朗读） ----------------
def _voice_payload() -> dict:
    cfg = voice_mod.normalize_config(CFG.get("voice") or {})
    return {
        "ok": True,
        "config": cfg,
        "status": {"tts_engine": "edge"},
        "voices": voice_mod.voices_payload(),
    }


@app.get("/api/voice")
def voice_get():
    return _voice_payload()


@app.post("/api/voice/config")
def voice_set(payload: dict):
    merged = voice_mod.merge_patch(CFG.get("voice") or {}, payload or {})
    cfg = voice_mod.normalize_config(merged)
    try:
        data = load_json(CONFIG_PATH)
    except Exception:
        data = {}
    data["voice"] = cfg
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        return {"ok": False, "error": f"配置写入失败：{e}"}
    CFG["voice"] = cfg
    return _voice_payload()


@app.post("/api/tts")
async def tts_speak(payload: dict):
    text = str((payload or {}).get("text") or "")
    cfg = voice_mod.normalize_config(CFG.get("voice") or {})["tts"]
    voice = str((payload or {}).get("voice") or cfg.get("voice") or voice_mod.VOICES[0]["id"])
    if voice not in {v["id"] for v in voice_mod.VOICES}:
        voice = cfg.get("voice") or voice_mod.VOICES[0]["id"]
    rate = str((payload or {}).get("rate") or cfg.get("rate") or "+0%")
    volume = str((payload or {}).get("volume") or cfg.get("volume") or "+0%")
    try:
        audio, spoken = await voice_mod.synthesize(text, voice, rate, volume)
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"{type(e).__name__}: {e}"[:400]}, status_code=503)
    return Response(
        content=audio,
        media_type="audio/mpeg",
        headers={
            "Cache-Control": "no-store",
            "X-Claw-Voice": voice,
            "X-Claw-Spoken-Chars": str(len(spoken)),
        },
    )


# ---------------- 桌面宠物（启动 / 停止 / 随应用启动） ----------------
@app.get("/api/pet")
def pet_get():
    """宠物状态：在不在跑、能不能启动、是否随应用自动启动。"""
    p = dict(CFG.get("pet") or {})
    return {"auto_start": bool(p.get("auto_start")), **pet_mod.status(p)}


@app.post("/api/pet/start")
def pet_start():
    try:
        p = CFG.get("pet") or {}
        r = pet_mod.start(p)
        return {**pet_mod.status(p), **r}   # 带上最新状态（running/pid 以前端为准）
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}


@app.post("/api/pet/stop")
def pet_stop():
    try:
        p = CFG.get("pet") or {}
        return pet_mod.stop(p)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}


@app.post("/api/pet/auto")
def pet_set_auto(payload: dict):
    """开关「随 Claw Agent 启动自动拉起宠物」。"""
    p = dict(CFG.get("pet") or {})
    if "auto_start" in (payload or {}):
        p["auto_start"] = bool(payload.get("auto_start"))
    try:
        data = load_json(CONFIG_PATH)
    except Exception:
        data = {}
    data["pet"] = p
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        return {"ok": False, "error": f"配置写入失败：{e}"}
    CFG["pet"] = p
    # 打开开关时顺手把宠物拉起来（用户点「开」的意图就是要它跑）
    if p.get("auto_start"):
        try:
            pet_mod.start(p)
        except Exception:
            pass
    return {"ok": True, "auto_start": bool(p.get("auto_start")), **pet_mod.status(p)}


# ---------------- 微信桥接（微信直连 Claw Agent：启动 / 停止 / 随应用启动） ----------------
@app.get("/api/wechat")
def wechat_get():
    """微信桥接状态：在不在跑、登录没登录、绑定了谁、走哪个模型。"""
    w = dict(CFG.get("wechat") or {})
    return {**wechat_mod.status(CFG, DATA_ROOT), "auto_start": bool(w.get("auto_start"))}


@app.post("/api/wechat/start")
def wechat_start():
    try:
        r = wechat_mod.start(CFG, DATA_ROOT)
        return {**wechat_mod.status(CFG, DATA_ROOT), **r}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}


@app.post("/api/wechat/stop")
def wechat_stop():
    try:
        return wechat_mod.stop(CFG, DATA_ROOT)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}


@app.post("/api/wechat/auto")
def wechat_set_auto(payload: dict):
    """开关「随 Claw Agent 启动自动拉起微信桥接」。"""
    w = dict(CFG.get("wechat") or {})
    if "auto_start" in (payload or {}):
        w["auto_start"] = bool(payload.get("auto_start"))
    for k, cast in (("model", str), ("permission", str)):
        if k in (payload or {}) and (payload or {}).get(k):
            w[k] = cast((payload or {}).get(k))
    try:
        data = load_json(CONFIG_PATH)
    except Exception:
        data = {}
    data["wechat"] = w
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        return {"ok": False, "error": f"配置写入失败：{e}"}
    CFG["wechat"] = w
    # 打开开关时顺手把它拉起来（用户点「开」的意图就是要它跑）
    if w.get("auto_start"):
        try:
            wechat_mod.start(CFG, DATA_ROOT)
        except Exception:
            pass
    return {"ok": True, "auto_start": bool(w.get("auto_start")), **wechat_mod.status(CFG, DATA_ROOT)}


@app.post("/api/open_path")
def open_path(payload: dict):
    """在资源管理器中打开指定目录（workdir / logs / data / memory / 显式 path）。"""
    target = str((payload or {}).get("target") or "workdir")
    extra = str((payload or {}).get("path") or "").strip()
    paths = {
        "workdir": (DATA_ROOT / str(CFG.get("agent", {}).get("default_workdir", "workspace"))).resolve(),
        "logs": (DATA_ROOT / "logs").resolve(),
        "data": DATA_ROOT.resolve(),
        "memory": (DATA_ROOT / "memory").resolve(),
    }
    p = paths.get(target)
    if extra and target in ("workdir", "path"):
        try:
            p = normalize_workdir(extra, DATA_ROOT)
        except Exception:
            p = Path(extra)
    if p is None:
        return {"ok": False, "error": f"未知目标：{target}"}
    try:
        p.mkdir(parents=True, exist_ok=True)
        os.startfile(str(p))  # noqa: S606 - 本地 GUI 应用，打开资源管理器
        return {"ok": True, "path": str(p)}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:200]}


# ---------------- 界面内提问（ask_user：把提问做进主界面，不弹系统窗口） ----------------
# 通道：插件 ask_user 写 <DATA_ROOT>/.claw_ask/<id>.json（status=pending）→ 前端轮询
# /api/ask/pending 拿到提问并浮出卡片 → 用户点选 → POST /api/ask/answer 写回答案 →
# 工具线程读到答案返回给模型，会话继续。前端无需知道文件，全部走这两个端点。
ASK_DIR = DATA_ROOT / ".claw_ask"
ASK_TTL = 300          # 提问默认有效期（秒）
ASK_MAX_TTL = 900      # 上限
ASK_LAST_POLL = 0.0    # 主界面最后一次轮询 /api/ask/pending 的时间（判断卡片通道是否在线）


def _ask_ui_active(max_age: float = 15.0) -> bool:
    """主界面是否在轮询提问通道：不在线时工具应立刻退回文字版，而不是阻塞到超时。"""
    return bool(ASK_LAST_POLL) and (time.time() - ASK_LAST_POLL) <= max_age


def _ask_file(qid: str) -> Path:
    safe = "".join(ch for ch in str(qid) if ch.isalnum() or ch in "-_")[:64]
    return ASK_DIR / f"{safe}.json"


def _ask_read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _ask_write(path: Path, obj: dict) -> None:
    try:
        ASK_DIR.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        pass


@app.get("/api/ask/pending")
def ask_pending():
    """主界面轮询：是否有待用户点选的提问（返回最新的一个）。"""
    global ASK_LAST_POLL
    ASK_LAST_POLL = time.time()
    try:
        if not ASK_DIR.is_dir():
            return {"ok": True, "ask": None}
        now = time.time()
        best = None
        for p in ASK_DIR.glob("*.json"):
            d = _ask_read(p)
            if d.get("status") != "pending":
                continue
            created = float(d.get("created_ts") or 0)
            ttl = float(d.get("ttl") or ASK_TTL)
            if now - created > ttl:
                d["status"] = "expired"
                _ask_write(p, d)
                continue
            if best is None or created > float(best.get("created_ts") or 0):
                best = d
        if not best:
            return {"ok": True, "ask": None}
        created = float(best.get("created_ts") or 0)
        ttl = float(best.get("ttl") or ASK_TTL)
        return {"ok": True, "ask": {
            "id": str(best.get("id") or ""),
            "question": str(best.get("question") or ""),
            "options": best.get("options") or [],
            "allow_custom": best.get("allow_custom", True),
            "created_ts": created,
            "expires_in": max(0, int(ttl - (now - created))),
        }}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:200], "ask": None}


@app.post("/api/ask/answer")
def ask_answer(payload: dict):
    """用户在界面卡片上点选/自拟后的回传。"""
    qid = str((payload or {}).get("id") or "").strip()
    answer = str((payload or {}).get("answer") or "").strip()
    p = _ask_file(qid)
    if not qid or not p.is_file():
        return {"ok": False, "error": "提问不存在（可能已过期）"}
    d = _ask_read(p)
    if d.get("status") != "pending":
        return {"ok": True, "already": True, "status": str(d.get("status") or "")}
    d["status"] = "answered"
    d["answer"] = answer
    d["answered_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _ask_write(p, d)
    return {"ok": True, "status": "answered"}


@app.post("/api/ask/skip")
def ask_skip(payload: dict):
    """用户忽略这次提问（工具立刻返回，模型改用文字列选项）。"""
    qid = str((payload or {}).get("id") or "").strip()
    p = _ask_file(qid)
    if not qid or not p.is_file():
        return {"ok": False, "error": "提问不存在（可能已过期）"}
    d = _ask_read(p)
    if d.get("status") == "pending":
        d["status"] = "skipped"
        d["answered_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _ask_write(p, d)
    return {"ok": True, "status": "skipped"}


# ---------------- 技能 ----------------
@app.get("/api/skills")
def skills_list():
    try:
        return {"ok": True, "skills": skills_mod.scan(DATA_ROOT)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


@app.get("/api/skills/{name}")
def skills_detail(name: str):
    try:
        d = skills_mod.load(DATA_ROOT, name)
        return {"ok": True, "name": name, "meta": d["meta"], "skill_md": d["skill_md"], "script": d["script"]}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}


@app.delete("/api/skills/{name}")
def skills_delete_api(name: str):
    try:
        skills_mod.delete(DATA_ROOT, name)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}


# ---------------- 插件 ----------------
@app.get("/api/plugins")
def plugins_list():
    try:
        return {"ok": True, "plugins": plugins_mod.scan(DATA_ROOT)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


@app.post("/api/plugins/reload")
def plugins_reload(payload: dict | None = None):
    name = str((payload or {}).get("name") or "").strip()
    try:
        if name:
            r = plugins_mod.load(DATA_ROOT, name)
            return {"ok": r.get("ok", False), "tools": r.get("tools"), "error": r.get("error")}
        results = plugins_mod.load_all(DATA_ROOT)
        return {"ok": True, "results": results}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}


@app.post("/api/plugins/toggle")
def plugins_toggle(payload: dict):
    try:
        r = plugins_mod.set_enabled(DATA_ROOT, str(payload.get("name") or ""), bool(payload.get("enabled")))
        return {"ok": True, **r}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}


@app.delete("/api/plugins/{name}")
def plugins_delete_api(name: str):
    try:
        plugins_mod.delete(DATA_ROOT, name)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}


# ---------------- 框架自更新 ----------------
def _update_source(payload: dict | None = None) -> str:
    p = (payload or {}).get("source")
    return str(p or (CFG.get("update") or {}).get("source_dir") or "")


@app.get("/api/update/check")
def update_check(source: str | None = None):
    return updater.check(source or _update_source())


@app.post("/api/update/apply")
async def update_apply(payload: dict | None = None):
    r = updater.apply(_update_source(payload))
    if r.get("ok"):
        updater.schedule_exit(2.5)  # 响应送达后清理并退出，更新脚本拉起新进程
    return r


@app.get("/api/update/info")
def update_info():
    return {"ok": True, "current": updater.current_version(), "frozen": bool(getattr(sys, "frozen", False)),
            "source_dir": _update_source()}


@app.post("/internal/qoder/v1/chat/completions")
async def qoder_chat_shim(payload: dict, session: str = "default",
                          permission: str = "standard", cwd: str = "", attachments: str = "",
                          unattended: int = 0):
    """本地垫片：agent 循环的请求交给本机 Qoder CLI（官方无头模式，SSE 转换）。"""
    atts = [p for p in attachments.split("|") if p.strip()] if attachments else []
    return StreamingResponse(
        QD.stream_chat(payload, session=session, permission=permission,
                       cwd=cwd or None, attachments=atts, unattended=bool(unattended)),
        media_type="text/event-stream",
    )


@app.post("/internal/provider/v1/chat/completions")
async def provider_chat_shim(payload: dict, pid: str = "", model: str = ""):
    """本地垫片：把 agent 循环的请求转发到设置页里配好的 OpenAI 兼容供应商（SSE 直通）。"""
    import provider_proxy as PP

    try:
        resp, body = await PP.stream(pid, model, payload)
    except LookupError as e:
        return JSONResponse({"error": {"message": str(e), "type": "invalid_request_error"}}, status_code=404)
    except ValueError as e:
        return JSONResponse({"error": {"message": str(e), "type": "invalid_request_error"}}, status_code=400)
    except Exception as e:
        return JSONResponse(
            {"error": {"message": f"连接供应商失败：{type(e).__name__}: {e}"[:300],
                       "type": "upstream_error"}},
            status_code=502,
        )
    if resp.status_code >= 400:
        raw = await resp.aread()
        await resp.aclose()
        return Response(content=raw, status_code=resp.status_code,
                        media_type=resp.headers.get("content-type", "application/json"))
    return StreamingResponse(body, media_type="text/event-stream")


@app.get("/internal/qoder/models")
def qoder_models_api():
    try:
        return {"ok": True, "models": QD.models(force=True)}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}


# ---------------- MCP 桥接：把 Claw 工具暴露给 Qoder CLI（官方 MCP 通道） ----------------
# Qoder CLI 以 MCP 客户端身份连接本端点，工具调用回到主进程执行——
# 与本地模型共用同一套实现：记忆库 / 技能 / 插件 / 浏览器 / 提问卡片 / 权限档 / 审计。
MCP_TOKEN = secrets.token_urlsafe(18)


def _mcp_token_path() -> Path:
    return DATA_ROOT / "data" / "mcp_token.json"


def _save_mcp_token() -> None:
    """把 token+端口落盘（供本机调试/排查；同用户可读，风险等同应用自身数据）。"""
    try:
        p = _mcp_token_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps({"token": MCP_TOKEN, "port": SELF_PORT or CFG["server"]["port"]}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        pass


def _mcp_tools() -> list[dict]:
    out = []
    for t in TOOL_SPECS:
        fn = (t or {}).get("function") or {}
        name = fn.get("name")
        if not name:
            continue
        out.append({
            "name": name,
            "description": str(fn.get("description") or "")[:1500],
            "inputSchema": fn.get("parameters") or {"type": "object", "properties": {}},
        })
    return out


async def _mcp_handle(msg: dict, cwd: str, permission: str, unattended: bool = False,
                      session: str = "default") -> dict | None:
    mid = msg.get("id")
    method = str(msg.get("method") or "")
    params = msg.get("params") or {}

    def ok(result):
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def err(code, message):
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}

    if method == "initialize":
        try:
            ver = str((updater.current_version() or {}).get("version") or "1.0")
        except Exception:
            ver = "1.0"
        return ok({
            "protocolVersion": str(params.get("protocolVersion") or "2024-11-05"),
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "claw-agent", "version": ver},
        })
    if method in ("notifications/initialized", "initialized") or method.startswith("notifications/"):
        return None
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": _mcp_tools()})
    if method == "tools/call":
        name = str(params.get("name") or "")
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            args = {}
        root = DATA_ROOT.resolve()
        default_wd = root / CFG.get("agent", {}).get("default_workdir", "workspace")
        # 会话工作目录可以是安装目录之外的任意目录（用户在资源管理器里选的）；
        # cwd 来自带 token 的本机 MCP 配置，直接采用，失效时才回落默认目录。
        try:
            p = Path(cwd).expanduser() if str(cwd or "").strip() else default_wd
            wd = (p if p.is_absolute() else root / p).resolve()
        except Exception:
            wd = default_wd
        if wd.exists() and not wd.is_dir():
            wd = default_wd
        try:
            wd.mkdir(parents=True, exist_ok=True)
        except OSError:
            wd = default_wd
            try:
                wd.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
        cfg = {
            **CFG.get("agent", {}),
            "_memory": MEM_CFG,
            "_project_root": str(root),
            "_permission": permission or "standard",
            # MCP 是独立构造的 cfg，不带上预算的话 execute_tool 永远看不到它——
            # qoder 这类自带 agent 循环的通道就只能靠模型自觉收手（实测 84 次不停）
            "_tool_budget": int((CFG.get("agent") or {}).get("unattended_tool_budget") or 40)
                            if unattended else 0,
            # 记忆作用域必须是规范化绝对路径键（与前端目录键一致），不能是目录名
            "_workspace": workspace_key(wd),
            "_workspace_path": str(wd),
            "_update": CFG.get("update") or {},
            "_unattended": bool(unattended),
            "_ask_ui_active": _ask_ui_active(),
            "_session_id": str(session or "default"),
            "_self_port": SELF_PORT or int(CFG["server"]["port"]),
            "_qd": QD,
        }
        text, success = await asyncio.to_thread(execute_tool, name, args, str(wd), cfg)
        return ok({"content": [{"type": "text", "text": text}], "isError": not success})
    return err(-32601, f"method not found: {method}")


@app.on_event("startup")
def _mcp_token_on_startup():
    """绑定成功后再落 token：否则端口被占导致启动失败时，文件里留下的是死进程的 token。"""
    _save_mcp_token()
    if CFG.get("cloud_only"):
        try:
            SERVER.stop()
        except Exception:
            pass


@app.post("/internal/mcp")
async def mcp_endpoint(request: Request, token: str = "", cwd: str = "", permission: str = "standard",
                       unattended: int = 0, session: str = "default"):
    if token != MCP_TOKEN:
        return JSONResponse({"jsonrpc": "2.0", "error": {"code": -32001, "message": "unauthorized"}}, status_code=401)
    try:
        msg = await request.json()
    except Exception:
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
    if isinstance(msg, list):
        out = []
        for m in msg:
            r = await _mcp_handle(m, cwd, permission, bool(unattended), session)
            if r is not None:
                out.append(r)
        return JSONResponse(out)
    r = await _mcp_handle(msg, cwd, permission, bool(unattended), session)
    if r is None:
        return Response(status_code=202)
    return JSONResponse(r)


@app.post("/api/models/refresh")
def refresh_models():
    global REGISTRY
    REGISTRY = scan_models(CFG["model_scan_dirs"])
    # 云端目录一并强刷：上游窗口/档位会变，本地缓存 1 小时可能过期
    try:
        QD.models(force=True)
    except Exception:
        pass
    return {"ok": True, "models": len(REGISTRY)}


@app.post("/api/models/switch")
async def switch_model(payload: dict):
    req_key = str(payload.get("key") or "")
    if CFG.get("cloud_only") and not req_key.startswith("qoder:"):
        return {"ok": False, "error": "本地模型已禁用：请选择 Qoder 云端模型"}
    if req_key.startswith("qoder:"):
        # 云端模型：无需本地加载，前端记下即可
        return {"ok": True, "model": req_key, "cloud": True, "note": "云端模型，无需本地加载"}
    try:
        key, path, params = resolve_profile(payload.get("key"))
    except KeyError as e:
        return {"ok": False, "error": str(e)}
    result = await asyncio.to_thread(SERVER.ensure, path, params)
    return {"ok": True, "model": key, **result}


# 模型加载参数（改动需重启 llama-server 生效）：上下文长度 / KV 量化 / GPU 层数 / FlashAttention
_LOAD_KEYS = ("ctx", "kv_k", "kv_v", "ngl", "flash_attn")
_KV_CHOICES = ("f16", "q8_0", "q4_0")


class LoadParamsRequest(BaseModel):
    model: str
    params: dict


def _load_view(key: str, merged: dict) -> dict:
    defaults = SERVER.defaults
    prof = PROFILES.get(key, {})
    return {
        "model": key,
        "params": {k: merged.get(k, defaults.get(k)) for k in _LOAD_KEYS},
        "defaults": {k: defaults.get(k) for k in _LOAD_KEYS},
        "overridden": [k for k in _LOAD_KEYS if k in prof],
    }


@app.get("/api/models/load_params")
def get_load_params(model: str | None = None):
    """模型加载参数（defaults + 档案合并；model 省略时取当前粘性模型）。"""
    try:
        key, _path, merged = resolve_for_chat(model)
    except KeyError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, **_load_view(key, merged)}


@app.post("/api/models/load_params")
async def set_load_params(req: LoadParamsRequest):
    """保存模型加载参数（写入 models.json 档案）；若该模型正在加载 → 立即重启生效。"""
    key = req.model
    src = req.params or {}
    clean: dict = {}
    if "ctx" in src:
        try:
            v = int(src["ctx"])
        except (TypeError, ValueError):
            return {"ok": False, "error": "ctx 必须是整数"}
        if not (1024 <= v <= 131072):
            return {"ok": False, "error": "ctx 范围 1024 ~ 131072"}
        clean["ctx"] = v
    for kk in ("kv_k", "kv_v"):
        if kk in src:
            v = str(src[kk])
            if v not in _KV_CHOICES:
                return {"ok": False, "error": f"{kk} 仅支持 {'/'.join(_KV_CHOICES)}"}
            clean[kk] = v
    if "ngl" in src:
        try:
            v = int(src["ngl"])
        except (TypeError, ValueError):
            return {"ok": False, "error": "ngl 必须是整数"}
        if not (0 <= v <= 999):
            return {"ok": False, "error": "ngl 范围 0 ~ 999"}
        clean["ngl"] = v
    if "flash_attn" in src:
        clean["flash_attn"] = bool(src["flash_attn"])
    if not clean:
        return {"ok": False, "error": "没有要保存的参数"}

    try:
        data = load_json(PROFILES_PATH)
    except Exception:
        data = {"profiles": {}}
    profiles = data.setdefault("profiles", {})
    prev = json.loads(json.dumps(profiles.get(key) or {}, ensure_ascii=False))
    prof = profiles.setdefault(key, {})
    if "path" not in prof:
        ref = PROFILES.get(key) or REGISTRY.get(key) or {}
        if not ref.get("path"):
            return {"ok": False, "error": f"未知模型：{key}"}
        prof["path"] = ref["path"]
        if ref.get("note"):
            prof["note"] = ref["note"]
    for k, v in clean.items():
        if v == SERVER.defaults.get(k):
            prof.pop(k, None)  # 与默认一致 → 不落档案
        else:
            prof[k] = v
    try:
        with open(PROFILES_PATH, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        return {"ok": False, "error": f"档案写入失败：{e}"}
    PROFILES[key] = prof  # 内存同步

    # 正在加载该模型 → 用新参数重启；启动失败自动回滚档案
    restarted = False
    if SERVER.is_running():
        cur = SERVER.current_model_path()
        if cur and Path(cur).resolve() == Path(prof["path"]).resolve():
            try:
                await asyncio.to_thread(SERVER.start, prof["path"], SERVER.merged_params(prof))
                restarted = True
            except Exception as e:
                profiles[key] = prev
                PROFILES[key] = prev
                try:
                    with open(PROFILES_PATH, "w", encoding="utf-8", newline="\n") as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
                except Exception:
                    pass
                return {"ok": False, "error": f"模型以新参数启动失败，已回滚：{e}"}
    return {"ok": True, "restarted": restarted, **_load_view(key, SERVER.merged_params(prof))}


def _overhead_tokens() -> int:
    """估算固定注入开销（工具 schema + system 文案），供前端上下文占用显示。"""
    try:
        import json as _json

        chars = sum(len(_json.dumps(t, ensure_ascii=False)) for t in TOOL_SPECS)
        return int(chars * 0.8) + 800
    except Exception:
        return 6000


def _wechat_bridge_root() -> Path | None:
    """定位微信桥接目录（workspace/wechat-bridge）。

    找得到（存在 secure/binding.json = 已绑定用户）才认为「微信通道可用」。
    可用 config.wechat.bridge_dir 覆盖默认位置。
    """
    cands: list[Path] = []
    _w = str((CFG.get("wechat") or {}).get("bridge_dir") or "").strip()
    if _w:
        cands.append(Path(_w))
    # 仓库内的 bridge/ 是主位置；workspace/ 与安装目录是历史遗留，保留兜底
    cands.append(DATA_ROOT / "bridge")
    cands.append(DATA_ROOT / "workspace" / "wechat-bridge")
    _local = os.environ.get("LOCALAPPDATA")
    if _local:
        cands.append(Path(_local) / "Programs" / "ClawAgent" / "workspace" / "wechat-bridge")
    for c in cands:
        try:
            if (c / "secure" / "binding.json").is_file():
                return c
        except Exception:
            continue
    return None


def _notify_wechat(text: str) -> tuple[bool, str]:
    """把一条消息投进微信桥接的 outbox，由常驻 relay 进程发出（零依赖、不阻塞）。

    走的就是 iLink 桥接既有的文件契约：outbox/*.reply.json →
    relay.py daemon 每轮 drain_outbox() 扫描并发送（成功移入 outbox/sent/）。
    context_token 取最近一条入站消息的（回复窗口凭证）；取不到就留空（仍可发）。
    """
    root = _wechat_bridge_root()
    if root is None:
        return False, "未找到微信桥接（workspace/wechat-bridge/secure/binding.json 不存在）"
    try:
        binding = json.loads((root / "secure" / "binding.json").read_text(encoding="utf-8"))
        to_user = str((binding or {}).get("bound_user_id") or "").strip()
        if not to_user:
            return False, "微信桥接未绑定用户（需扫码登录）"
        ctx = ""
        done = sorted((root / "inbox" / "done").glob("*.json"))
        if done:
            try:
                ctx = json.loads(done[-1].read_text(encoding="utf-8")).get("context_token") or ""
            except Exception:
                ctx = ""
        out = root / "outbox"
        out.mkdir(parents=True, exist_ok=True)
        name = f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}.reply.json"
        payload = {"to_user_id": to_user, "context_token": ctx,
                   "text": text, "from_user_id": ""}
        tmp = out / (name + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(str(tmp), str(out / name))
        return True, "已投递微信 outbox（待 relay 发出）"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def _wechat_summary(user_query: str, content: str, steps: int, seconds: float) -> str:
    """把「任务完成」压成一条适合微信的短消息（微信不渲染 Markdown，去装饰）。"""
    ask = " ".join(str(user_query or "").split())[:60] or "(无)"
    body = " ".join(str(content or "").split())
    if len(body) > 300:
        body = "…" + body[-300:]
    lines = [f"【Claw Agent 任务完成】{ask}",
             f"耗时 {int(seconds)} 秒 / {steps} 步"]
    if body:
        lines += ["", body]
    return "\n".join(lines)


def _notify_done(text: str) -> None:
    """长任务完成时的 Windows 气泡通知（PowerShell 原生，零依赖；失败静默）。"""
    try:
        t = str(text or "任务完成").replace("'", " ").replace("\n", " ").strip()[:80] or "任务完成"
        ps = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$n=New-Object System.Windows.Forms.NotifyIcon;"
            "$n.Icon=[System.Drawing.SystemIcons]::Information;$n.Visible=$true;"
            f"$n.ShowBalloonTip(6000,'Claw Agent','{t}','Info');"
            "Start-Sleep -Seconds 7;$n.Dispose()"
        )
        subprocess.Popen(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        pass


# ---------------- 专家模型（压缩 / 图片识别，可自定义） ----------------
def _experts() -> dict:
    # 历史配置里可能还留着 wb: 专家（通道已删除），直接忽略而不是让下游报错
    exp = dict(CFG.get("experts") or {})
    return {k: v for k, v in exp.items() if not str(v or "").startswith("wb:")}


class ExpertsRequest(BaseModel):
    compact: str | None = None
    vision: str | None = None


@app.get("/api/experts")
def get_experts():
    return {
        "ok": True,
        "experts": _experts(),
        "defaults": {"compact": "Qwen3.5-2B-IQ4_XS", "vision": "Qwen3.5-0.8B-Q4_K_M"},
        "models": [{"key": k, "vision": bool(v.get("mmproj"))} for k, v in sorted(REGISTRY.items())],
    }


def _known_expert(key: str) -> bool:
    """专家模型合法性：本地注册表/档案，或 Qoder 云端模型（WorkBuddy 已移除）。"""
    if key in REGISTRY or key in PROFILES:
        return True
    if key.startswith("wb:"):
        return False
    if key.startswith("qoder:"):
        try:
            return any(f"qoder:{m['id']}" == key for m in QD.models())
        except Exception:
            return False
    return False


@app.post("/api/experts")
def set_experts(req: ExpertsRequest):
    cur = _experts()
    if req.compact is not None:
        if req.compact and not _known_expert(req.compact):
            return {"ok": False, "error": f"未知模型：{req.compact}"}
        cur["compact"] = req.compact
    if req.vision is not None:
        if req.vision and not _known_expert(req.vision):
            return {"ok": False, "error": f"未知模型：{req.vision}"}
        cur["vision"] = req.vision
    try:
        data = load_json(CONFIG_PATH)
    except Exception:
        data = {}
    data["experts"] = cur
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        return {"ok": False, "error": f"配置写入失败：{e}"}
    CFG["experts"] = cur
    return {"ok": True, "experts": cur}


@app.get("/api/status")
def status():
    st = SERVER.status()
    st["default_workdir"] = str(WORKDIRS.default)
    st["default_workdir_key"] = workspace_key(WORKDIRS.default)
    st["permission_levels"] = PERMISSION_LEVELS
    # 上下文长度跟随当前加载模型的档案（加载参数面板可改）
    ctx = int(CFG.get("defaults", {}).get("ctx", 32768))
    try:
        if SERVER.is_running():
            cur = SERVER.current_model_path()
            matched = _match_loaded(cur) if cur else None
            if matched:
                ctx = int(matched[2].get("ctx") or ctx)
    except Exception:
        pass
    st["ctx"] = ctx
    st["overhead_tokens"] = _overhead_tokens()
    # 工具数量由后端说了算：前端写死的 31 一旦加了工具就对不上，属于必然过期的硬编码
    try:
        import tools as _tools

        st["tool_count"] = len(_tools.TOOL_SPECS)
    except Exception:
        st["tool_count"] = 0
    return st


# ---------------- 记忆 ----------------
@app.get("/api/memory")
def memory_overview(layer: int | None = None, session_id: str | None = None, limit: int = 100,
                    workspace: str | None = None, include_global: bool = True):
    # 四层都要能看：L1 常驻核心、L2 重要记忆(经验)、L3 情景记忆、L4 会话记忆（L4 按 session 过滤）
    scopes = None
    if workspace and str(workspace).lower() not in ("all", "*"):
        key = workspace_key(workspace)
        scopes = [key] + ([GLOBAL_SCOPE] if include_global and key != GLOBAL_SCOPE else [])
    if layer in (1, 2, 3, 4):
        entries = STORE.get_layer(layer, session_id=(session_id if layer == 4 else None),
                                  limit=limit, scopes=scopes)
    else:
        entries = []
        for l in (1, 2, 3, 4):
            entries += STORE.get_layer(l, session_id=(session_id if l == 4 else None),
                                       limit=limit, scopes=scopes)
    return {"stats": STORE.stats(scopes=scopes), "entries": entries,
            "tags": STORE.tags_overview(60, scopes=scopes),
            "workspace_counts": STORE.workspace_counts(),
            "global_count": STORE.scope_entry_count(GLOBAL_SCOPE)}


class TagsRequest(BaseModel):
    tags: str = ""            # 覆盖式设置（数量不限）；空串=清空
    add: str = ""             # 追加（去重）
    remove: str = ""          # 移除指定标签


@app.post("/api/memory/{entry_id}/tags")
def memory_set_tags(entry_id: int, req: TagsRequest):
    """标签设置：覆盖 / 追加 / 移除（三者按 set→add→remove 顺序应用）。数量不限，不截断。"""
    from memory.store import normalize_tags, tag_notice

    e = STORE.get_entry(entry_id)
    if not e:
        return {"ok": False, "error": f"未找到记忆 #{entry_id}"}
    cur = (e.get("tags") or "").strip()
    if req.tags or not (req.add or req.remove):
        cur = normalize_tags(req.tags)
    if req.add:
        cur = normalize_tags(cur + "," + normalize_tags(req.add))
    if req.remove:
        drop = {t.lower() for t in normalize_tags(req.remove).split(",") if t}
        cur = normalize_tags(",".join(t for t in cur.split(",") if t.strip().lower() not in drop))
    new = STORE.set_tags(entry_id, cur)
    n = len([x for x in new.split(",") if x.strip()])
    return {"ok": True, "id": entry_id, "tags": new, "count": n,
            "notice": tag_notice(new), "tags_overview": STORE.tags_overview(60)}


class EntryPatch(BaseModel):
    title: str | None = None
    content: str | None = None
    summary: str | None = None
    tags: str | None = None
    importance: int | None = None


@app.patch("/api/memory/{entry_id}")
def memory_patch(entry_id: int, req: EntryPatch):
    """局部更新一条记忆（标题/正文/结论/标签/重要度）。"""
    from memory.store import tag_notice

    out = STORE.update_entry(
        entry_id, title=req.title, content=req.content, summary=req.summary,
        tags=(req.tags if req.tags is not None else None), importance=req.importance,
    )
    if not out:
        return {"ok": False, "error": f"未找到记忆 #{entry_id}"}
    return {"ok": True, "entry": out, "notice": tag_notice(out.get("tags") or "")}


@app.get("/api/memory/health")
def memory_health(workspace: str = ""):
    """记忆体检：重复 / 注入了从没用过 / 过期 / 被取代 / L1 占用。"""
    try:
        return {"ok": True, **STORE.health(int(MEM_CFG.get("l1_budget_chars", 1800)))}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}


@app.get("/api/memory/tags")
def memory_tags_overview(top: int = 60):
    return {"tags": STORE.tags_overview(top)}


# ---------------- 遗忘机制（只提议 + 人工确认后执行）----------------
class PinRequest(BaseModel):
    on: bool = True


class DemoteRequest(BaseModel):
    id: int | None = None
    ids: str = ""            # 逗号分隔，批量


@app.get("/api/memory/forget_candidates")
def memory_forget_candidates_api(layer: int | None = None, min_age_days: float | None = None,
                                 limit: int = 20):
    """算出遗忘候选（L2→L3 / L3→归档）。**只提议，不改任何数据。**"""
    cands = STORE.forget_candidates(
        layer=(layer if layer in (2, 3) else None),
        min_age_days=min_age_days, limit=limit,
    )
    return {"ok": True, "candidates": cands, "count": len(cands)}


@app.post("/api/memory/{entry_id}/pin")
def memory_pin_api(entry_id: int, req: PinRequest):
    """锁定/解锁一条记忆（pin=1 后永不被降级或归档）。"""
    r = STORE.set_pin(entry_id, bool(req.on))
    if not r:
        return {"ok": False, "error": f"未找到记忆 #{entry_id}"}
    return {"ok": True, **r}


@app.post("/api/memory/{entry_id}/promote")
def memory_promote_api(entry_id: int):
    """把一条目录记忆提取到全局记忆（复制并合并，不删除原条目）。"""
    r = STORE.promote_entry(entry_id)
    return {"ok": bool(r.get("ok")), **r}


@app.post("/api/memory/demote")
def memory_demote_api(req: DemoteRequest):
    """执行降级：L2→L3，L3→归档。破坏性操作，前端会先二次确认。"""
    ids: list[int] = []
    if (req.ids or "").strip():
        ids = [int(x) for x in req.ids.replace("，", ",").split(",") if x.strip()]
    elif req.id:
        ids = [int(req.id)]
    if not ids:
        return {"ok": False, "error": "请给 id 或 ids"}
    results = []
    for i in ids:
        r = STORE.demote(i)
        results.append(r if r else {"id": i, "ok": False, "msg": f"未找到记忆 #{i}"})
    ok = sum(1 for r in results if r.get("ok"))
    return {"ok": True, "done": ok, "total": len(ids), "results": results}


@app.post("/api/memory/export")
def memory_export():
    """导出全部记忆为 Markdown 备份，返回文件路径。"""
    STORE.export_markdown()
    try:
        out_dir = DATA_ROOT / "memory" / "export"
        out_dir.mkdir(parents=True, exist_ok=True)
        files = sorted(out_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
        return {"ok": True, "path": str(files[0]) if files else "",
                "dir": str(out_dir)}
    except Exception as e:
        return {"ok": True, "path": "", "error": f"{type(e).__name__}: {e}"[:200]}


@app.post("/api/memory/purge")
def memory_purge(payload: dict | None = None):
    """清空记忆库。

    这是不可逆操作，所以强制先导出一份备份再删；返回备份路径，出问题能手工还原。
    单独一个接口（而不是复用 DELETE /api/memory?layer=）是为了让界面上能明确提示这件事。
    """
    try:
        STORE.export_markdown()
        backup = ""
        out_dir = DATA_ROOT / "memory" / "export"
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        db = DATA_ROOT / "memory" / "memory.db"
        if db.exists():
            backup = str(out_dir / f"memory-purge-{stamp}.db")
            shutil.copy2(str(db), backup)
        before = STORE.stats().get("total", 0)
        n = STORE.purge_all()
        after = STORE.stats().get("total", 0)
        return {"ok": True, "deleted": n, "before": before, "after": after,
                "backup": backup}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}


@app.delete("/api/memory/{entry_id}")
def memory_delete(entry_id: int):
    return {"ok": STORE.forget(entry_id)}


@app.delete("/api/memory")
def memory_delete_batch(layer: int | None = None, query: str | None = None):
    """批量删除：?layer=N 清空某层；?query=xx 删除含关键词的条目。"""
    if layer is None and not (query or "").strip():
        return {"ok": False, "error": "需提供 layer 或 query 参数"}
    n = STORE.forget_many(layer=layer, query=(query or None))
    return {"ok": True, "deleted": n}


# ---------------- 对话 ----------------
class ChatRequest(BaseModel):
    messages: list
    model: str | None = None
    params: dict | None = None
    thinking: bool | None = None
    reasoning_budget_tokens: int | None = None
    max_tokens: int | None = None
    agent: bool = True               # 是否启用工具循环（False = 纯对话）
    workdir: str | None = None       # 会话工作目录（默认 config.agent.default_workdir）
    max_steps: int | None = None     # 工具步数上限覆盖（0 = 不限）
    session_id: str | None = None    # 会话标识（记忆 L3 用；默认 "default"）
    permission: str | None = None    # 工具权限档位：readonly / standard / full
    images: list | None = None       # 图片（data URI 列表）；云模型支持视觉时直传，否则先交视觉专家识别
    reasoning_effort: str | None = None  # 云端思考档位（off/low/medium/high/xhigh/max）
    mode: str | None = None          # 值守模式：onduty（值守中，专用值守权限）/ unattended（无人监管）
    unattended: bool | None = None   # 兼容旧客户端：无人监管模式：不等人工确认、加大自动恢复、结束时汇报
    unattended_minutes: int | None = None  # 无人值守时长（分钟；0=不限时，默认 30）


_VISION_PROMPT = (
    "你是图片信息提取器。请客观、详细地描述图片内容：文字、物体、人物、场景、颜色、数字。"
    "如有多张图请按顺序分别描述。描述将提供给另一个模型用于回答用户问题。"
)


def _cloud_image_parts(images: list, text: str) -> list[dict]:
    """把图片（data URI）组装成 OpenAI 内容块（云端视觉直传用）。"""
    content: list[dict] = [{"type": "text", "text": text}]
    for img in (images or [])[:4]:
        if isinstance(img, str) and img.startswith("data:image"):
            content.append({"type": "image_url", "image_url": {"url": img}})
    return content


def _save_qoder_images(images: list, workdir: Path) -> list[str]:
    """把图片（data URI）落盘到工作目录内，供 Qoder CLI 以 --attachment 直传。"""
    out: list[str] = []
    folder = workdir / "_uploads"
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        return out
    for i, img in enumerate((images or [])[:4]):
        try:
            s = str(img)
            if not s.startswith("data:image/"):
                continue
            head, b64 = s.split(",", 1)
            ext = "png"
            if "jpeg" in head or "jpg" in head:
                ext = "jpg"
            elif "webp" in head:
                ext = "webp"
            p = folder / f"qoder_{time.strftime('%Y%m%d_%H%M%S')}_{i}.{ext}"
            p.write_bytes(base64.b64decode(b64))
            out.append(str(p))
        except Exception:
            continue
    return out


def _pick_cloud_vision() -> str:
    """（已停用）WorkBuddy 通道移除后不再自动挑选云端视觉模型。"""
    return ""


async def _recognize_images(client: httpx.AsyncClient, images: list, user_text: str) -> str:
    """调用视觉专家模型识别图片，返回描述文本。"""
    content: list[dict] = [
        {
            "type": "text",
            "text": _VISION_PROMPT + (f"\n用户的问题是：{user_text[:200]}" if user_text else ""),
        }
    ]
    for img in images[:4]:
        if isinstance(img, str) and img.startswith("data:image"):
            content.append({"type": "image_url", "image_url": {"url": img}})
    if len(content) == 1:
        return ""
    body = {
        "messages": [{"role": "user", "content": content}],
        "temperature": 0.2,
        "max_tokens": 700,
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    r = await client.post(f"{SERVER.base}/v1/chat/completions", json=body)
    data = r.json()
    msg = (data.get("choices") or [{}])[0].get("message") or {}
    return str(msg.get("content") or "").strip()


@app.post("/api/chat")
async def chat(req: ChatRequest):
    # 全局只走云端：本地模型一律重定向到默认 Qoder 模型
    if CFG.get("cloud_only"):
        _m = str(req.model or CFG.get("default_model") or "qoder:Auto")
        # api: 前缀是设置页里配好的供应商，同样属于「云端」，不应被重定向掉
        if not (_m.startswith("qoder:") or _m.startswith("api:")):
            _m = "qoder:Auto"
        req.model = _m
    # WorkBuddy 通道已移除：旧会话/旧选择命中的模型给出明确提示（SSE 错误事件）
    if bool(req.model) and str(req.model).startswith("wb:"):
        async def _wb_gone():
            yield sse({"type": "error", "message": "WorkBuddy 通道已移除：请在模型菜单选择 Qoder 或本地模型"})
            yield sse({"type": "done", "steps": 0})
        return StreamingResponse(_wb_gone(), media_type="text/event-stream")
    # 没指定模型时用默认模型：api:/qoder: 是「通道型」key，必须原样透传，
    # 否则会走到本地模型解析里报「未知模型」，表现为默认模型永远回空。
    if not req.model:
        _d = str(CFG.get("default_model") or "")
        if _d.startswith("api:") or _d.startswith("qoder:"):
            req.model = _d
        elif not _d.strip():
            # 一个模型都没配时给明确指引，而不是让下游抛「未知模型: 」
            async def _no_model():
                yield sse({"type": "error",
                           "message": "还没有可用的模型：打开设置 → 模型与 API，"
                                      "填一个 OpenAI 兼容供应商（或确认 Qoder CLI 已登录）"})
                yield sse({"type": "done", "steps": 0})
            return StreamingResponse(_no_model(), media_type="text/event-stream")
    is_qoder = bool(req.model) and str(req.model).startswith("qoder:")
    import provider_proxy as PP

    _prov = PP.parse_key(str(req.model or "")) if not is_qoder else None
    is_provider = _prov is not None
    prov_pid, prov_model = (_prov or ("", ""))
    qoder_id = ""
    if is_qoder:
        qoder_id = str(req.model)[6:]
        key, path, params = str(req.model), None, {}
    elif is_provider:
        # 用户在设置页配好的 OpenAI 兼容供应商：不走本地模型，也不懒启动任何进程
        key, path, params = str(req.model), None, {}
        if PP.find_provider(prov_pid) is None:
            async def _no_provider():
                yield sse({"type": "error",
                           "message": f"供应商 {prov_pid} 不存在（可能已在设置页删除），请重新选择模型"})
                yield sse({"type": "done", "steps": 0})
            return StreamingResponse(_no_provider(), media_type="text/event-stream")
    else:
        try:
            key, path, params = resolve_for_chat(req.model)
        except KeyError as e:
            return {"ok": False, "error": str(e)}

        # 懒启动/换模型（阻塞操作放线程池，避免卡住事件循环）
        await asyncio.to_thread(SERVER.ensure, path, params)

    # 工作目录：请求级 > 配置默认（相对路径以项目根解析）
    permission = normalize_permission(req.permission)
    _mem_budget_key = key if not is_provider else f"api:{prov_model}"
    _mem_cfg = {**MEM_CFG, "l1_budget_chars": _memory_budget(_mem_budget_key, is_qoder)}
    wd_raw = req.workdir or CFG.get("agent", {}).get("default_workdir", "workspace")
    workdir = Path(wd_raw)
    if not workdir.is_absolute():
        workdir = DATA_ROOT / workdir
    workdir = workdir.resolve()
    if workdir.exists() and not workdir.is_dir():
        return {"ok": False, "error": f"工作目录不是文件夹：{workdir}"}
    workdir.mkdir(parents=True, exist_ok=True)
    ws_key = workspace_key(workdir)
    ws_label = workspace_name(workdir)
    try:
        WORKDIRS.touch(workdir)
    except Exception:
        pass

    req_params = dict(req.params or {})
    thinking = req.thinking if req.thinking is not None else params.get("thinking_default", True)
    budget = req.reasoning_budget_tokens
    if budget is None:
        budget = params.get("reasoning_budget_tokens", 1024)

    sampling = {
        "temperature": req_params.get("temperature", params.get("temperature")),
        "repeat_penalty": req_params.get("repeat_penalty", params.get("repeat_penalty")),
        "repeat_last_n": req_params.get("repeat_last_n", params.get("repeat_last_n")),
        "max_tokens": req.max_tokens or params.get("max_tokens", 16384),
    }
    sampling = {k: v for k, v in sampling.items() if v is not None}

    session_id = req.session_id or "default"

    mode = str(req.mode or "").strip().lower()
    if mode not in ("onduty", "unattended"):
        legacy_un = (req.unattended if req.unattended is not None
                     else bool(CFG.get("agent", {}).get("unattended", False)))
        mode = "unattended" if legacy_un else "onduty"
    unattended = mode == "unattended"
    # 值守中模式：请求没带权限时，走独立配置项（与无人监管的权限互不影响）
    if mode == "onduty" and not req.permission:
        _onduty_perm = str((CFG.get("agent") or {}).get("onduty_permission") or "").strip()
        if _onduty_perm:
            permission = normalize_permission(_onduty_perm)

    # 无人值守窗口：0 = 不限时（选择器最后一档），直到用户手动停止
    unattended_minutes = 0
    unattended_deadline = 0.0
    if unattended:
        try:
            unattended_minutes = int(req.unattended_minutes if req.unattended_minutes is not None else 30)
        except (TypeError, ValueError):
            unattended_minutes = 30
        unattended_minutes = max(0, min(unattended_minutes, 24 * 60))
        if unattended_minutes > 0:
            unattended_deadline = time.time() + unattended_minutes * 60
    # 两套模式的行为准则完全不同（见 server/prompts.py）：
    # 值守允许在岔路口提问；无人监管必须自主闭环、自己验证、按固定结构汇报。
    import prompts as _prompts

    if unattended:
        _until_lab = time.strftime("%H:%M", time.localtime(unattended_deadline)) if unattended_deadline else ""
        _un_budget = int((CFG.get("agent") or {}).get("unattended_tool_budget") or 40)
        _un_block = _prompts.block("unattended", unattended_minutes, _until_lab, _un_budget)
    else:
        _un_block = _prompts.block("onduty")

    # 记忆注入：身份 + L1(全量) + L2(按当前输入检索) + L3(本会话近期)
    user_query = ""
    for m in reversed(req.messages):
        if m.get("role") == "user":
            user_query = str(m.get("content") or "")
            break
    # 工作方式总则（块状结构：上下文 → 环境 → 取信息顺序 → 工具规则 → 写文件 → 记忆 → 回答）
    kb_ready = any(t.get("function", {}).get("name") == "knowledge_search" for t in TOOL_SPECS)
    strategy = (
        "【上下文】对话里的【历史摘要】是更早对话的压缩摘要，【图片识别】是视觉模型对用户图片的描述，都直接作为可用信息参考。"
        "【环境】Windows（Git Bash）；Python 命令为 python（本机 python3 是无效的商店桩，直接使用 python）。"
        "【取信息顺序】提示中已附带的记忆与资料，直接参考；"
        "在开始任何涉及用户环境、项目、源码、路径、配置、历史决定、之前做过的事的任务前，"
        "主动先用 memory_search 查相关记忆（不必等用户提醒；上下文里似乎知道也先查证）；"
        "用户偏好、历史、此前保存的内容，用 memory_search 查（中文关键词）；"
        "用户问「你记得/源码在哪/之前/上次/路径/配置」时，必须先 memory_search（必要时 memory_get）再回答，"
        "不能只凭注入记忆或上下文印象；"
        "找不到合适关键词时用 memory_search 的 tag 参数按标签选（只给 tag 即列出该标签下全部记忆，"
        "标签数量不限；先看有哪些标签用 memory_tags action=overview）；"
        + ("资料、文档、本地知识库内容，用 knowledge_search 检索；" if kb_ready else "")
        + "系统配置、文件内容、目录、程序状态这类真实环境数据，自己动手用工具获取，拿到结果再回答；"
        "常识、算术、推理、闲聊直接回答；"
        + _un_block
        + "【工具规则】文件操作优先专用文件工具（file_read / file_create / file_edit / file_delete / file_move / file_search 等）；"
        "运行程序、查询系统用 run_shell，它默认在会话工作目录执行，文件用相对路径；需要在命令里写绝对路径时用双引号包住；"
        "【前台/后台】要拿到结果才能继续的命令（编译、测试、查询、一次性脚本）用默认前台方式，等它跑完再往下；"
        "启动服务器、常驻监听、守护进程、长任务这类不会自己退出的命令，必须加 background=true 立即返回，"
        "之后用 shell_jobs 查看输出（action=output）或结束进程（action=kill / kill_port），绝不要在前台等待它们；"
        "同一件事不要重复执行两遍，先用 shell_jobs action=list 或 netstat 查是否已经在跑；"
        "禁止用 run_shell 直接写 config/config.json 或覆盖 ClawAgent.exe（会破坏运行中的框架，用 file_edit 或 framework_update）；"
        "任务可以验证结果时（运行脚本、读回文件等），先验证无误再汇报；"
        "文件类任务写完后对照要求核对（数量、篇幅、要点），不足的部分补写后再收尾；"
        "工具报错时先读报错信息，做最小修正再试，同一问题连续两次不通过就换思路（换命令写法或换工具）；"
        "被权限拦截时向用户说明，可提示切换权限档位。"
        "【汇报】只陈述实际发生过的事：说「已完成 / 已创建 / 已运行」之前，先确认对应的工具调用已经成功执行。"
        "【写文件】内容量按任务要求来：有篇幅要求时按目标字数组织内容（宁足勿缺；中文按汉字数计）；"
        "超过约 1500 字符的文件分多次写：file_create 写开头，再用 file_edit（append=true）逐段追加，每次约 1000~1500 字符。"
        "【技能】可复用流程固化用 skill_create（把步骤写成 run.py：stdin 读 JSON、stdout 输出）；"
        "已有技能直接 skill_run 运行，skill_list 查看；用户说“以后都这样/做成技能”时主动创建。"
        "【插件】需要给框架加全新工具能力（网络请求、解析、算法等代码类）时用 plugin_create 写 plugin.py（register(api) 注册工具，创建即热加载）；plugin_list 查看；改完可 plugin_reload 热重载。"
        "【更新】用户说“更新框架/升级”时用 framework_update（先 check 说明版本再 apply；apply 会重启应用）。"
        "【浏览器】需要打开网页、查在线资料、登录页操作、抓页面内容时用 browser_control（打开→取文字/点击/填表→截图）。"
        "【记忆】发现用户偏好、重要事实或长期信息，主动用 memory_write 记住。四层："
        "layer=1 常驻核心（特别重要、**不含经验**：身份/偏好/行为规则/安全/关系 → 每次都会注入你的上下文，"
        "**必须给 summary（≤60 字结论）**，content 第一句也要是结论）；"
        "layer=2 重要记忆（可复用的经验/技术方案/技能入口 → 按需检索）；"
        "layer=3 情景记忆（项目记录/事件/时效信息 → 按需检索）。"
        "【记忆作用域】默认写入当前工作目录记忆（同目录的所有会话共享，会话 A 能看到会话 B 在该目录积累的记忆）；"
        "layer=1 和 scope=global 写入全局记忆（所有目录可见）；重要度≥4 的目录记忆会自动提取一份到全局。"
        "跨目录通用的用户偏好/规则请用 scope=global；项目内经验留在当前目录，避免污染全局。"
        "凡是「经验」「怎么做」类内容一律不要放 layer=1；同主题写入会自动查重合并，不必担心重复。"
        "对话里注入的只是各条的结论（summary），要细节/原文用 memory_get(id=…)；"
        "查某层现状用 memory_stats；换关键词检索用 memory_search。"
        "找不到合适关键词时用 memory_search 的 tag 参数按标签选（只给 tag 即列出该标签下全部记忆，"
        "标签数量不限；先看有哪些标签用 memory_tags action=overview）；"
        "对话里注入的只是各条的结论（summary），要细节/原文用 memory_get(id=…)；"
        "查某层现状用 memory_stats。"
        "【遗忘机制】层是**用途标签**不是垃圾等级：L1 走「只精不降」（靠预算提醒合并，永不降级）、"
        "L2→L3→归档、L4 是会话暂存不参与。默认**只提议不动手**：用 memory_forget_candidates 算出候选（L2/L3 层内垫底"
        "且长期未被真正使用），念给用户听，得到明确同意后才用 memory_demote 执行。"
        "「被注入」不算使用（注入高、被用 0 的条目才是白占额度）；pin=1 或重要度 5 的一律豁免，"
        "用户表达过强烈在意/绝不能被忘的内容，主动用 memory_pin 锁上。"
        "会话状态（layer=4）由框架自动记，不用你写。"
        "【排版】回复支持完整 Markdown（标题、**粗体**、列表、`行内代码`、带语言标注的代码块、表格、引用、分割线），"
        "界面会渲染成富文本。分点、给步骤、对比方案时用列表或表格，代码一律用 ```语言 围栏包住，"
        "比大段纯文字更好读；但别为了排版而排版，短回答直接说即可。"
        "【回答】用中文、简洁直接、先给结论。"
        "【任务规划】需要三步以上的任务，先用 task_plan(action=set) 列出步骤（3–8 条，每条要可验收），"
        "每完成一步立刻 task_plan(action=update, step_id=..., status=done) 并写一句产出摘要；"
        "只记步骤与状态，不要写预计耗时。一步能做完的事和纯闲聊不要建计划。"
    )
    base_system = (
        f"你是 Claw Agent，在 Windows 上工作的 AI 助手。"
        f"会话工作目录：{workdir}（记忆作用域：{ws_label}）。相对路径都以此目录为基准。"
        f"{strategy}"
    )
    _mem_meta: dict = {}
    mem_block = build_memory_block(STORE, user_query=user_query, session_id=session_id,
                                   cfg=_mem_cfg, meta_out=_mem_meta,
                                   workspace=ws_key, workspace_label=ws_label)
    if mem_block:
        base_system = base_system + "\n\n" + mem_block
    _plan_block = PLAN.render(session_id)
    if _plan_block:
        base_system = base_system + "\n\n" + _plan_block
    # 预算只提醒、不截断：满了就在主界面弹一条提示，由模型自己决定精简哪几条
    # （注意：这里不能 yield，chat() 不是生成器；提示交给下面的 gen() 发）
    _mem_notice = ""
    if _mem_meta.get("full"):
        _mem_notice = (f"核心记忆预算已满：{_mem_meta['used']}/{_mem_meta['budget']} 字"
                       f"（超 {_mem_meta['over_chars']} 字，条目全部注入未截断）")

    # 记一条提示词摘要：排查「它为什么这样回答」时，第一件事就是确认注入了哪套规则
    try:
        DIAG.info("chat.prompt", session=session_id,
                  mode="unattended" if unattended else "onduty",
                  rules="自主模式" if "【无人监管·自主模式】" in base_system else "值守",
                  chars=len(base_system),
                  mem=len(mem_block or ""), plan=len(_plan_block or ""))
    except Exception:
        pass

    messages = _sanitize_history([dict(m) for m in req.messages])
    if messages and messages[0].get("role") == "system":
        messages[0]["content"] = base_system + "\n\n" + str(messages[0].get("content") or "")
    else:
        messages = [{"role": "system", "content": base_system}] + messages

    agent_cfg = {
        **CFG.get("agent", {}),
        "_memory": _mem_cfg,
        "_project_root": str(DATA_ROOT),
        "_permission": permission,
        "_workspace": ws_key,
        "_workspace_path": str(workdir),
        "_update": CFG.get("update") or {},
        "_unattended": unattended,
        "_no_ask": bool(unattended),   # 无人监管不给 ask_user，逼它自己判断
        # 自主模式必须有自动刹车：没人看着时，模型能连调上百次工具而不收尾
        "_tool_budget": int((CFG.get("agent") or {}).get("unattended_tool_budget") or 40)
                        if unattended else 0,
        "_unattended_minutes": unattended_minutes,
        "_unattended_deadline": unattended_deadline,
        "_ask_ui_active": _ask_ui_active(),
        "_session_id": session_id,
        "_self_port": SELF_PORT or int(CFG["server"]["port"]),
        "_qd": QD,
    }
    # Qoder：图片以附件形式直传给 CLI（文件落在工作目录内，Qoder 才能读）
    qoder_attachments: list[str] = []
    if is_qoder and req.images:
        qoder_attachments = _save_qoder_images(req.images, workdir)
    max_steps = req.max_steps if req.max_steps is not None else int(agent_cfg.get("max_steps", 0))
    if is_qoder:
        _port = SELF_PORT or CFG["server"]["port"]
        att_q = ("&attachments=" + quote("|".join(qoder_attachments))) if qoder_attachments else ""
        chat_url = (
            f"http://127.0.0.1:{_port}/internal/qoder/v1/chat/completions"
            f"?session={quote(session_id)}"
            f"&permission={quote(permission)}"
            f"&cwd={quote(str(workdir))}"
            f"&unattended={1 if unattended else 0}"
            f"{att_q}"
        )
    elif is_provider:
        _port = SELF_PORT or CFG["server"]["port"]
        chat_url = (
            f"http://127.0.0.1:{_port}/internal/provider/v1/chat/completions"
            f"?pid={quote(prov_pid)}&model={quote(prov_model)}"
        )
    else:
        chat_url = f"{SERVER.base}/v1/chat/completions"

    _channel = "qoder" if is_qoder else ("api" if is_provider else "local")
    _diag_model = str(req.model or key)

    async def gen():
        # 不设读取超时：模型/工具跑多久都不断流（连接超时仍保留 10 秒）
        timeout = httpx.Timeout(None, connect=10)
        try:
            import tools as _tj

            _tj.reset_tool_budget(session_id)
        except Exception:
            pass
        collected = {"content": "", "steps": 0, "tools": []}
        t_gen0 = time.time()
        try:
            DIAG.info("chat.start", session=session_id, model=_diag_model, channel=_channel,
                      mode="unattended" if unattended else "onduty", permission=permission,
                      agent=bool(req.agent), msgs=len(messages))
        except Exception:
            pass
        # 核心记忆预算只提醒不截断：满了先提示一句（由模型自己决定精简哪几条）
        if _mem_notice:
            yield sse({"type": "notice", "text": _mem_notice})
        # 桌面宠物的实时活动（写 logs/live.json；失败不影响对话）
        try:
            live_log_dir = DATA_ROOT / "logs"
            global LIVE
            LIVE = live_mod.LiveActivity(str(live_log_dir))
        except Exception:
            LIVE = None
        async with httpx.AsyncClient(timeout=timeout) as client:
            # 图片输入：云端模型支持视觉 → 直传；否则交视觉专家（本地或云端）识别
            if req.images:
                if is_qoder and qoder_attachments:
                    yield sse({"type": "notice", "text": f"图片直传 Qoder（{len(qoder_attachments)} 张，附件模式）"})
                else:
                    vision_key = str(_experts().get("vision") or "")
                    desc = ""
                    if not vision_key:
                        yield sse({"type": "notice", "text": "未配置图片识别专家（可在模型参数里选择）"})
                    else:
                        try:
                            if str(vision_key).startswith("qoder:"):
                                # Qoder 视觉专家：图片落盘后以附件直传（无需切换本地模型）
                                yield sse({"type": "notice", "text": f"正在用 Qoder 视觉模型 {vision_key} 识别图片（{len(req.images)} 张）…"})
                                vtext = _VISION_PROMPT + (f"\n用户的问题是：{user_query[:200]}" if user_query else "")
                                vfiles = _save_qoder_images(req.images, workdir)
                                desc = await QD.complete(
                                    model=str(vision_key)[6:], prompt=vtext, attachments=vfiles
                                ) if vfiles else ""
                            else:
                                vk, vpath, vparams = resolve_profile(vision_key)
                                need_switch = path is not None and Path(vpath).resolve() != Path(path).resolve()
                                if need_switch:
                                    yield sse({"type": "notice", "text": f"正在用 {vision_key} 识别图片（{len(req.images)} 张）…"})
                                    await asyncio.to_thread(SERVER.ensure, vpath, vparams)
                                desc = await _recognize_images(client, req.images, user_query)
                                if need_switch:
                                    yield sse({"type": "notice", "text": f"识别完成，切回主模型 {key}…"})
                                    await asyncio.to_thread(SERVER.ensure, path, params)
                        except Exception as e:
                            yield sse({"type": "notice", "text": f"图片识别失败（已尝试切回主模型）：{type(e).__name__}: {e}"})
                            if path is not None:
                                try:
                                    await asyncio.to_thread(SERVER.ensure, path, params)
                                except Exception:
                                    pass
                            desc = ""
                    if desc:
                        note = f"【图片识别（{vision_key}）】\n{desc}"
                        if messages and messages[-1].get("role") == "user":
                            messages[-1]["content"] = str(messages[-1].get("content") or "") + "\n\n" + note
                        yield sse({"type": "image_desc", "note": note})
            async for ev in run_agent(
                client=client,
                chat_url=chat_url,
                messages=messages,
                sampling=sampling,
                thinking=bool(thinking),
                budget=budget,
                tools_enabled=bool(req.agent) and not is_qoder,
                workdir=str(workdir),
                max_steps=max_steps,
                agent_cfg=agent_cfg,
                model=(qoder_id if is_qoder else None),
                reasoning_effort=(req.reasoning_effort if (is_qoder or is_provider) else None),
                own_tools=is_qoder,
            ):
                t = ev.get("type")
                # ---- 实时活动广播：桌面宠物据此显示「思考/调工具/作答 + 正文」 ----
                try:
                    if t == "step":
                        LIVE.thinking("正在思考（第 %d 步）" % (int(ev.get("step") or 0) + 1))
                    elif t == "reasoning":
                        LIVE.thinking("正在思考")
                    elif t == "tool_call":
                        LIVE.tool(str(ev.get("name") or ""))
                    elif t == "tool_result":
                        LIVE.tool_done(str(ev.get("name") or ""), bool(ev.get("ok", True)),
                                       ev.get("seconds"))
                    elif t == "notice":
                        LIVE.notice(str(ev.get("text") or ""))
                    elif t == "error":
                        LIVE.error(str(ev.get("message") or ""))
                except Exception:
                    pass
                if t == "content":
                    collected["content"] += ev.get("text", "")
                    try:
                        LIVE.append_answer(ev.get("text", ""))
                    except Exception:
                        pass
                elif t == "tool_result":
                    b = ev.get("brief")
                    if b:
                        collected["tools"].append(b if ev.get("ok", True) else f"{b} ✗")
                elif t == "done":
                    ev["model"] = key
                    collected["steps"] = int(ev.get("steps") or 0)
                    # 任务完成 → 写 L4 会话工作记忆（有工具调用或实质产出才记）
                    if collected["steps"] >= 1 or len(collected["content"]) > 150:
                        try:
                            # 结论取回复「结尾」：开头多是「收到，我先看看…」铺垫，结论一般在末段
                            _body = (collected["content"] or "").strip()
                            _tail = _body[-300:] if len(_body) > 300 else _body
                            # summary 取末段首句（去掉表格/代码行），供日后一眼看清这次干了什么
                            _concl = ""
                            for _ln in reversed(_tail.splitlines()):
                                _ln = _ln.strip().strip("*#-> `")
                                if len(_ln) >= 8 and not _ln.startswith(("|", "```", "!")):
                                    _concl = _ln
                                    break
                            if len(_concl) > 70:
                                _concl = _concl[:70] + "…"
                            # 重要度按「有无实质产出」动态给，不再写死 2：
                            # 写死 2 会让 L4 过期分流时把所有会话记录一律归档，
                            # 连带把「暂停→压缩→继续」这类真结论也一起埋掉。
                            #   用了工具（真的动手了）或正文够长 → 3（值得升进 L3 留档）
                            #   其余（纯闲聊/短问答） → 2（过期归档，不占 L3 检索配额）
                            _imp = 3 if (int(collected.get("steps") or 0) >= 1
                                         or len(collected["content"]) >= 400) else 2
                            STORE.add_entry(
                                layer=4,
                                title=f"任务：{user_query[:60] or '(无)'}",
                                content=f"结果：{_tail or '(无)'}",
                                summary=(f"结论：{_concl}" if _concl else f"任务「{user_query[:30]}」已完成"),
                                tags="会话", importance=_imp, session_id=session_id,
                                workspace=ws_key,
                                dedup=False,   # 会话记忆都留痕，不查重
                                kind="task", source_session=str(session_id or "")[:80],
                            )
                            ev["memory_saved"] = True
                        except Exception:
                            pass
                    # 长任务完成通知（设置面板可开关；≥ 阈值秒数才提醒，0=每次通知）
                    _ncfg = CFG.get("notify") or {}
                    try:
                        _nmin = 0 if unattended else int(_ncfg.get("on_done_min_seconds", 60))
                        if _ncfg.get("enabled", True) and time.time() - t_gen0 >= _nmin:
                            _notify_done(collected["content"])
                    except Exception:
                        pass
                    # 任务完成 → 微信推送（默认开：每次任务完成都发一条）
                    # 微信来的会话不发（worker 已在微信里回过了，避免重复刷屏）
                    try:
                        _wcfg = dict(_ncfg.get("wechat") or {})
                        _from_wechat = str(session_id or "").lower().startswith("wechat")
                        _wmin = 0 if unattended else int(_wcfg.get("min_seconds", 0))
                        if (_wcfg.get("enabled", False) and not _from_wechat
                                and time.time() - t_gen0 >= _wmin):
                            _wpushed = _notify_wechat(_wechat_summary(
                                user_query, collected["content"],
                                int(collected.get("steps") or 0), time.time() - t_gen0))
                            ev["wechat_notified"] = bool(_wpushed[0])
                    except Exception:
                        pass
                    try:
                        LIVE.done()
                    except Exception:
                        pass
                    # 一次请求的收尾记录：定位「刚才那次为什么慢/为什么失败」靠这条
                    try:
                        DIAG.info("chat.done", session=session_id, model=_diag_model,
                                  channel=_channel, elapsed_ms=int((time.time() - t_gen0) * 1000),
                                  steps=int(ev.get("steps") or collected.get("steps") or 0),
                                  chars=len(collected.get("content") or ""),
                                  tools=len(collected.get("tools") or []),
                                  tool_budget=int((agent_cfg or {}).get("_tool_budget") or 0),
                                  error=str(ev.get("error") or "")[:300])
                    except Exception:
                        pass
                elif t == "error":
                    try:
                        DIAG.warn("chat.error_event", session=session_id, model=_diag_model,
                                  channel=_channel, detail=str(ev.get("message") or "")[:600],
                                  elapsed_ms=int((time.time() - t_gen0) * 1000))
                    except Exception:
                        pass
                yield sse(ev)

    return StreamingResponse(_guarded_sse(gen()), media_type="text/event-stream")


# ---------------- 上下文压缩 ----------------
class CompressRequest(BaseModel):
    messages: list
    model: str | None = None            # 压缩专家模型（缺省跟随当前）
    restore_model: str | None = None    # 压缩完成后切回的会话模型（单实例模式下避免下次对话再等切换）


_COMPRESS_PROMPT = (
    "下面是较早的一段对话历史。请用要点式中文摘要它，保留：用户的偏好与要求、"
    "已确认的事实、完成的任务与结论、未完成的事项。去掉寒暄与冗余。直接输出摘要正文。"
)


@app.post("/api/compress")
async def compress(req: CompressRequest):
    """把一段对话历史交给模型压缩成摘要（供前端上下文压缩管理调用）。支持本地与云端专家。"""
    # 未指定压缩专家时：优先 roles.compact（设置页里绑的），退回默认模型。
    # 通道型 key（qoder:/api:/wb:）必须原样透传，否则会被当成本地模型解析报「未知模型」。
    if not req.model:
        _c = str((CFG.get("experts") or {}).get("compact") or CFG.get("default_model") or "")
        if _c.startswith(("qoder:", "api:", "wb:")):
            req.model = _c
    if CFG.get("cloud_only") and not str(req.model or "").startswith(("qoder:", "api:")):
        req.model = str(CFG.get("default_model") or "qoder:Auto")
        if not req.model.startswith(("qoder:", "api:")):
            req.model = "qoder:Auto"
    is_qoder_expert = bool(req.model) and str(req.model).startswith("qoder:")
    import provider_proxy as PP

    _cpp = PP.parse_key(str(req.model or "")) if not is_qoder_expert else None
    is_prov_expert = _cpp is not None
    if is_qoder_expert or is_prov_expert:
        key = str(req.model)
    else:
        try:
            key, path, params = resolve_for_chat(req.model)
        except KeyError as e:
            return {"ok": False, "error": str(e)}
        await asyncio.to_thread(SERVER.ensure, path, params)

    lines = []
    for m in req.messages:
        role = {"user": "用户", "tool": "工具", "assistant": "助手"}.get(str(m.get("role")), "助手")
        c = str(m.get("content") or "")[:2000]
        tcs = m.get("tool_calls") if isinstance(m.get("tool_calls"), list) else []
        if tcs:
            names = "、".join(
                ((tc.get("function") or {}).get("name") or "?") for tc in tcs if isinstance(tc, dict)
            )
            c = (c + f"（工具调用：{names}）").strip()
        if c:
            lines.append(f"[{role}] {c}")
    convo_text = "\n".join(lines)
    if not convo_text.strip():
        return {"ok": False, "error": "没有可压缩的内容"}

    body = {
        "messages": [
            {"role": "system", "content": "你是一个对话压缩器，输出精炼准确的摘要。"},
            {"role": "user", "content": _COMPRESS_PROMPT + "\n\n" + convo_text},
        ],
        "stream": False,
        "temperature": 0.3,
        "max_tokens": 800,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    try:
        if is_qoder_expert:
            # Qoder 专家：单次聚合（不保留会话）
            summary = await QD.complete(
                model=str(req.model)[6:],
                prompt=_COMPRESS_PROMPT + "\n\n" + convo_text,
            )
        elif is_prov_expert:
            summary = await PP.complete(
                _cpp[0], _cpp[1],
                {
                    "messages": [
                        {"role": "system", "content": "你是一个对话压缩器，输出精炼准确的摘要。"},
                        {"role": "user", "content": _COMPRESS_PROMPT + "\n\n" + convo_text},
                    ],
                    "temperature": 0.3,
                    "max_tokens": 900,
                },
            )
        else:
            timeout = httpx.Timeout(300, connect=10)
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(f"{SERVER.base}/v1/chat/completions", json=body)
                data = r.json()
                summary = (data["choices"][0]["message"].get("content") or "").strip()
        if not summary:
            return {"ok": False, "error": "模型返回了空摘要"}
        # 压缩完切回会话主模型（单实例：下次对话无缝衔接；云端模型无需切回）
        if (req.restore_model and req.restore_model != key
                and not str(req.restore_model).startswith("qoder:")
                and not str(req.restore_model).startswith("api:")
                and not str(req.restore_model).startswith("wb:")):
            try:
                rk, rpath, rparams = resolve_profile(req.restore_model)
                await asyncio.to_thread(SERVER.ensure, rpath, rparams)
            except Exception:
                pass
        return {"ok": True, "summary": summary, "model": key}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# ---------------- 会话持久化（后端文件，替代 localStorage） ----------------
SESSIONS = SessionStore(DATA_ROOT)


def _session_workdir(s: dict) -> str:
    """会话工作目录统一成绝对路径（旧会话可能存的是相对 workspace）。"""
    raw = str(s.get("workdir") or "").strip()
    if not raw:
        return str(WORKDIRS.default)
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = DATA_ROOT / p
    try:
        return str(p.resolve())
    except Exception:
        return raw


# ---------------- 工作目录（资源管理器原生选择 + 最近目录） ----------------
def _workdir_view(item: dict, mem_counts: dict | None = None,
                  session_counts: dict | None = None) -> dict:
    p = Path(str(item.get("path") or ""))
    key = workspace_key(p)
    return {
        "path": str(p),
        "key": key,
        "name": item.get("name") or workspace_name(p),
        "last_used": int(item.get("last_used") or 0),
        "uses": int(item.get("uses") or 0),
        "exists": p.is_dir(),
        "is_default": workspace_key(WORKDIRS.default) == key,
        "memory_count": int((mem_counts or {}).get(key, 0)),
        "session_count": int((session_counts or {}).get(key, 0)),
    }


def _session_workdir_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    for s in SESSIONS.list():
        key = workspace_key(_session_workdir(s))
        counts[key] = counts.get(key, 0) + 1
    return counts


@app.get("/api/workdirs")
def workdirs_list():
    """最近工作目录 + 每个目录的记忆/会话数量（供自绘目录选择面板）。"""
    mem_counts = STORE.workspace_counts()
    sess_counts = _session_workdir_counts()
    items = [_workdir_view(x, mem_counts, sess_counts) for x in WORKDIRS.list()]
    default_key = workspace_key(WORKDIRS.default)
    return {
        "ok": True,
        "default": str(WORKDIRS.default),
        "default_key": default_key,
        "items": items,
        "global_count": STORE.scope_entry_count(GLOBAL_SCOPE),
        "permission_levels": PERMISSION_LEVELS,
        "sandbox_note": "标准权限下文件写入限制在当前工作目录（沙箱）；重要记忆会自动提取到全局。",
    }


@app.post("/api/workdirs")
def workdirs_touch(payload: dict | None = None):
    """校验并登记一个工作目录（手输路径 / 前端选择器用）。"""
    raw = str((payload or {}).get("path") or "").strip()
    if not raw:
        return {"ok": False, "error": "缺少 path"}
    try:
        entry = WORKDIRS.touch(raw, base=DATA_ROOT)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}
    view = _workdir_view(entry, STORE.workspace_counts(), _session_workdir_counts())
    return {"ok": True, "workdir": view, **view}


@app.post("/api/fs/pick_folder")
def fs_pick_folder(payload: dict | None = None):
    """弹出 Windows 资源管理器式目录选择框（后端原生对话框）。"""
    initial = str((payload or {}).get("initial") or WORKDIRS.default)
    r = pick_native_folder(initial, title="选择会话工作目录")
    if not r.get("ok"):
        return r
    try:
        entry = WORKDIRS.touch(r["path"], base=DATA_ROOT)
    except Exception as e:
        return {"ok": False, "error": f"目录已选中但登记失败：{type(e).__name__}: {e}"[:300]}
    view = _workdir_view(entry, STORE.workspace_counts(), _session_workdir_counts())
    return {"ok": True, "path": view["path"], "workdir": view, **view}


class SessionSaveRequest(BaseModel):
    session: dict


class SessionsReplaceRequest(BaseModel):
    sessions: list[dict]
    drop_missing: bool = False


@app.get("/api/sessions")
def sessions_list():
    """全部会话（按 updatedAt 倒序）。工作目录统一返回绝对路径。"""
    sessions = []
    for s in SESSIONS.list():
        if s.get("workdir"):
            s = {**s, "workdir": _session_workdir(s)}
        sessions.append(s)
    return {"ok": True, "sessions": sessions}


@app.post("/api/sessions")
def sessions_save(req: SessionSaveRequest):
    """保存/更新单个会话（前端在会话变化时调用，可高频）。"""
    try:
        sess = req.session
        if sess.get("workdir"):
            sess = {**sess, "workdir": _session_workdir(sess)}
        SESSIONS.save(sess)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@app.delete("/api/sessions/{sid}")
def sessions_delete(sid: str):
    try:
        return {"ok": True, "deleted": SESSIONS.delete(sid)}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@app.post("/api/sessions/replace")
def sessions_replace(req: SessionsReplaceRequest):
    """整体覆盖：前端首次迁移（把 localStorage 里的旧会话推上来）或恢复备份时用。"""
    try:
        sessions = [
            {**s, "workdir": _session_workdir(s)}
            if isinstance(s, dict) and s.get("workdir") else s
            for s in req.sessions
        ]
        return {"ok": True, **SESSIONS.replace(sessions, drop_missing=req.drop_missing)}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# ---------------- 外部消息 / 实时推送（手机 Claw ↔ 当前聊天） ----------------
LIVE_CLIENTS: set[asyncio.Queue] = set()


class ExternalMessageRequest(BaseModel):
    text: str
    session_id: str | None = None
    source: str | None = "phone"


class TeamMessageRequest(BaseModel):
    text: str
    sender: dict | None = None
    session_id: str | None = None
    team_id: str | None = None


@app.get("/api/live")
async def live_events(request: Request):
    """前端 EventSource：接收外部（手机 Claw）消息，实时显示到当前聊天。"""
    q: asyncio.Queue = asyncio.Queue()
    LIVE_CLIENTS.add(q)

    async def gen():
        try:
            yield ": connected\n\n"
            for evt in await asyncio.to_thread(SESSIONS.pending_team_events):
                yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    evt = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            LIVE_CLIENTS.discard(q)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/external/message")
async def external_message(req: ExternalMessageRequest):
    """供灵动岛/手机收件箱调用：把手机消息推给当前打开的 Claw Agent 聊天。"""
    text = str(req.text or "").strip()
    if not text:
        return {"ok": False, "error": "empty text"}
    evt = {
        "type": "phone_message",
        "id": uuid.uuid4().hex,
        "session_id": req.session_id,
        "source": req.source or "phone",
        "text": text,
        "at": time.time(),
    }
    for q in list(LIVE_CLIENTS):
        try:
            q.put_nowait(evt)
        except Exception:
            pass
    return {"ok": True, "id": evt["id"], "clients": len(LIVE_CLIENTS)}


@app.post("/api/team/message")
async def team_message(req: TeamMessageRequest):
    """数字员工发言：把某个 claw 的消息实时插进当前聊天（群聊视图）。"""
    text = str(req.text or "").strip()
    if not text:
        return {"ok": False, "error": "empty text"}
    evt = {
        "type": "team_message",
        "id": uuid.uuid4().hex,
        "session_id": req.session_id,
        "team_id": req.team_id,
        "sender": req.sender or {},
        "text": text,
        "at": time.time(),
    }
    if req.session_id:
        try:
            await asyncio.to_thread(SESSIONS.append_team_message, req.session_id, {
                "id": evt["id"], "role": "assistant", "sender": evt["sender"],
                "content": text, "done": True, "team": True,
                "team_id": req.team_id, "at": evt["at"],
            })
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        except OSError:
            return JSONResponse({"ok": False, "error": "会话消息保存失败"}, status_code=500)
    for q in list(LIVE_CLIENTS):
        try:
            q.put_nowait(evt)
        except Exception:
            pass
    return {"ok": True, "id": evt["id"], "clients": len(LIVE_CLIENTS), "persisted": bool(req.session_id)}


# ---------------- 前端静态托管（web/dist，构建后生效） ----------------
WEB_DIST = BUNDLE / "web" / "dist"
if (WEB_DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")


@app.get("/")
def index():
    idx = WEB_DIST / "index.html"
    if idx.exists():
        return FileResponse(idx)
    return {"message": "前端尚未构建：cd web && npm run build"}


# 设置页 / 预览页 / 演示页是独立 HTML，必须各自有路由——
# 之前只 serve 了 "/" 和 "/assets"，主界面点设置（iframe 载 /admin.html）直接 404。
@app.get("/admin.html")
def admin_page():
    return _serve_page("admin.html", "设置页")


@app.get("/preview.html")
def preview_page():
    return _serve_page("preview.html", "临时预览页")


@app.get("/demo.html")
def demo_page():
    return _serve_page("demo.html", "演示页")


def _serve_page(name: str, label: str):
    f = WEB_DIST / name
    if f.exists():
        return FileResponse(f)
    # 没构建出来时给一句人话，而不是 FastAPI 的 {"detail":"Not Found"}
    return HTMLResponse(
        f"<h3>{label}还没构建</h3><p>请在 web 目录执行 <code>npm install && npm run build</code> 后重启。</p>",
        status_code=503,
    )


def _wait_ready(url: str, timeout_s: float = 25.0) -> bool:
    import time
    import urllib.request

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url + "/api/health", timeout=1)
            return True
        except Exception:
            time.sleep(0.25)
    return False


if __name__ == "__main__":
    import os
    import threading
    import time

    import uvicorn

    # 打包且无控制台（windowed）：日志重定向已在模块顶部完成（须早于三方库导入），
    # 这里只补 chcp 与编码设置
    if FROZEN:
        try:
            os.system("chcp 65001 >nul")
        except Exception:
            pass
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    host = CFG["server"]["host"]
    port = CFG["server"]["port"]
    if "--port" in sys.argv:  # 调试用：源码实例可与安装版并存（如 --port 8091）
        try:
            port = int(sys.argv[sys.argv.index("--port") + 1])
        except (IndexError, ValueError):
            pass
    SELF_PORT = port  # 垫片路由自调用用
    # MCP 桥接：把端口/token 交给 Qoder provider（每次调用前写 .mcp.json）
    QD.mcp_port = port
    QD.mcp_token = MCP_TOKEN
    _save_mcp_token()
    url = f"http://{host if host != '0.0.0.0' else '127.0.0.1'}:{port}"
    print(f"[Claw Agent] {time.strftime('%Y-%m-%d %H:%M:%S')} 启动  界面 {url}")

    # 窗口模式选择：默认原生窗口；传入 --browser 用系统浏览器（调试）
    use_browser = "--browser" in sys.argv
    if not use_browser:
        try:
            import webview  # noqa: F401
        except Exception:
            use_browser = True

    if not use_browser:
        # 原生窗口：后台线程跑服务，主线程持窗口
        threading.Thread(
            target=lambda: uvicorn.run(app, host=host, port=port, log_level="warning"),
            daemon=True,
        ).start()
        _wait_ready(url)
        try:
            import webview

            webview.create_window("Claw Agent", url, width=1280, height=860, min_size=(920, 600))
            # private_mode=False + 固定 storage_path：窗口重启后保留会话/设置（localStorage）
            webview.start(private_mode=False, storage_path=str(DATA_ROOT / "webview_data"))
        except Exception as e:
            print(f"[Claw Agent] 原生窗口不可用（{e}），改用浏览器")
            import webbrowser

            webbrowser.open(url)
            while True:
                time.sleep(3600)
        finally:
            # 窗口关闭 → 停掉托管的 llama-server，退出
            try:
                SERVER.stop()
            except Exception:
                pass
            print("[Claw Agent] 已退出")
    else:
        import webbrowser

        print("=" * 52)
        print(f"  模型: {len(REGISTRY)} 个（默认 {CFG['default_model']}）")
        print("  按 Ctrl+C 停止")
        print("=" * 52)
        if FROZEN:
            threading.Timer(1.5, lambda: webbrowser.open(url)).start()
        uvicorn.run(app, host=host, port=port)
