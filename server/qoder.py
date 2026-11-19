# -*- coding: utf-8 -*-
"""Qoder 云端模型提供方：复用本机 Qoder CLI 的登录态，把 Qoder agent 接入 Claw Agent。

机制（2026-10-18 实测，CLI 1.1.73，官方接口）：
  - 安装：npm install -g @qoder-ai/qodercli；登录：qodercli login（浏览器授权一次）
  - 无头：qodercli -p --output-format stream-json（prompt 走 stdin，避免命令行长度/引号问题）
  - 会话：首轮 --session-id <uuid>，后续 --resume <uuid>（实测可续聊）
  - 工具：Qoder 自己执行内置工具（Read/Write/Bash/WebFetch…）；工具与权限按 Claw 三档映射
  - 事件：system(init) / assistant(thinking|text|tool_use) / user(tool_result) / result

只读本机 Qoder 登录态（~/.qoder/.auth），不做任何凭据复制。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import quote

from quota import QuotaProvider

CREATE_NO_WINDOW = 0x08000000
MODEL_TTL = 3600.0       # 模型目录缓存 1 小时
MAX_RUNTIME = 0.0         # 单轮时长上限：0 = 不设上限（跑多久都行）
STALL_TIMEOUT = 1800.0    # 多久没有任何输出判定为「挂死」并中断（防卡死，不是时长上限）
# 这些环境变量会把 CLI 切到「SDK worker」模式并拒绝普通参数（实测踩坑）
_STRIP_ENV = (
    "QODER_AGENT_SDK_ENTRYPOINT", "QODER_CLI", "QODER_SESSION_TYPE",
    "QODERCLI_RUNTIME_PACKAGING", "QODER_WORKER_CWD",
)
# Claw 只读档下允许 Qoder 使用的工具
_READONLY_TOOLS = ["Read", "Glob", "Grep", "WebFetch", "WebSearch"]
# Claw 思考档位 → Qoder CLI --reasoning-effort（官方取值：auto/none/low/medium/high/xhigh/max/ultracode）
_EFFORT_MAP = {"off": "none", "low": "low", "medium": "medium", "high": "high", "xhigh": "xhigh", "max": "max"}
# 模型上下文窗口（Qoder 官方文档 docs.qoder.com/cli/model，2026-10）
_QODER_MODEL_CTX = {
    "qwen3.8-max": 1_000_000,
    "qwen3.8-flash": 1_000_000,
    "qwen3.7-max": 1_000_000,
    "qwen3.7-plus": 1_000_000,
    "kimi-k3": 1_000_000,
    "kimi-k2.8-preview": 256_000,
    "glm-5.3": 1_000_000,
    "glm-5.3-flash": 1_000_000,
    "deepseek-v4-pro": 1_000_000,
    "deepseek-v4-flash": 1_000_000,
    "deepseek-flash": 1_000_000,
    "minimax-m3": 1_000_000,
    "minimax-m3.1-flash": 1_000_000,
}
_DEFAULT_CTX = 200_000  # 档位模型（Auto/Ultimate/Fast/Efficient/Sonus/Cantus）默认 200K（可调 200K/400K/1M）
_STREAM_LINE_LIMIT = 16 * 1024 * 1024  # stream-json 单行上限（工具读大文件时单行可达数百 KB）
_MODEL_REQUEST_RETRIES = 5  # 交给 CLI 的 --max-model-request-retries（模型请求瞬时故障重试次数）
_TRANSIENT_RETRY_DELAYS = (2.0, 5.0, 10.0)  # CLI 整体失败（未产生任何事件）时的重尝试间隔
_TIMEOUT_RESUMES = 2       # 单轮超时后允许的自动续跑次数
_RESUME_AFTER_TIMEOUT = (
    "（上一轮因为执行时间过长被中断了）请从中断处继续：先确认哪些步骤已经完成，"
    "不要重复已经成功的操作，继续推进剩下的步骤并给出最终结论。"
)


def _looks_transient(msg: str) -> bool:
    low = (msg or "").lower()
    return any(k in low for k in (
        "timeout", "timed out", "connection", "connect", "reset", "aborted", "socket",
        "temporarily", "unavailable", "overloaded", "rate limit", "too many requests",
        "econnreset", "etimedout", "econnrefused", "broken pipe", "502", "503", "504",
        "network", "internet", "offline", "getaddrinfo", "dns",
    ))


def _append_system_prompt(unattended: bool, minutes: int = 0, deadline: float = 0.0,
                          budget: int = 40) -> str:
    """追加给 CLI 的系统提示。

    这里有个历史坑：CLI 只吃「最后一条用户消息」，主流程拼的完整 strategy 根本传不进来，
    所以模式规则必须在这里自带一份。以前这一份是手写的另一套，还写着
    「即使自认为任务完成也不要收尾，继续检查优化」——与主流程互相矛盾，
    实测把一个「一句话回答」跑成了 35 次工具调用。现在统一从 prompts.py 取，
    两处不会再各说各话。

    长度受 Windows 命令行上限限制（约 8K 字符），所以只放必要部分。
    """
    import prompts

    until = time.strftime("%H:%M", time.localtime(deadline)) if deadline else ""
    parts = [
        "【Claw 环境】Windows。需要用户拍板时调用 MCP 工具 ask_user（claw-agent 服务器），"
        "不要用内置提问工具；主界面没打开时会退回「问题+编号选项」文字清单。",
        "【记忆】只在任务涉及用户环境、项目、源码、路径、配置、历史决定时先 memory_search；"
        "闲聊与常识直接回答，不要为了「查证」反复检索。",
        "【单 agent】你自己用工具把任务做完，没有部门/办公室流程，先动手再汇报结果。",
        prompts.block("unattended" if unattended else "onduty", minutes, until, budget),
    ]
    return "\n".join(parts)


def _ctx_for(model_id: str) -> int:
    return _QODER_MODEL_CTX.get(str(model_id or "").strip().lower(), _DEFAULT_CTX)


def _log(msg: str) -> None:
    try:
        print(f"[Qoder] {time.strftime('%H:%M:%S')} {msg}", flush=True)
    except Exception:
        pass


class QoderProvider:
    def __init__(self, data_root: str | Path):
        self.data_root = Path(data_root)
        self.data_dir = self.data_root / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_path = self.data_dir / "qoder_sessions.json"
        self.usage_path = self.data_dir / "qoder_usage.json"
        self._models: list[dict] | None = None
        self._models_at = 0.0
        self._sessions: dict | None = None
        # MCP 桥接（由 main.py 启动时注入）：把 Claw 工具通过官方 MCP 通道交给 Qoder
        self.mcp_port: int | None = None
        self.mcp_token: str = ""
        # 模型请求瞬时故障重试次数（指挥 CLI 的 --max-model-request-retries）
        self.max_model_retries: int = _MODEL_REQUEST_RETRIES
        # 单轮执行上限（0 = 不设上限）与卡死判定（秒）：超时后自动续跑，不作废整轮
        self.max_runtime: float = MAX_RUNTIME
        self.stall_timeout: float = STALL_TIMEOUT

    # ---------- CLI 定位与环境 ----------
    @staticmethod
    def _cli_paths() -> dict:
        node = shutil.which("node")
        shim = shutil.which("qodercli") or shutil.which("qodercli.cmd")
        bundle = None
        cands = []
        if shim:
            cands.append(Path(shim).parent / "node_modules" / "@qoder-ai" / "qodercli" / "bundle" / "qodercli.js")
        cands.append(Path(os.environ.get("APPDATA", "")) / "npm" / "node_modules" / "@qoder-ai" / "qodercli" / "bundle" / "qodercli.js")
        for c in cands:
            if c.exists():
                bundle = c
                break
        return {"node": node, "bundle": bundle, "shim": shim}

    @staticmethod
    def _env() -> dict:
        env = dict(os.environ)
        for k in list(env):
            if k in _STRIP_ENV or k.startswith("QODER_AGENT_SDK"):
                env.pop(k, None)
        env["NO_COLOR"] = "1"
        # MCP 超时单位是毫秒：请求 1 小时、工具执行 15 分钟
        # （默认的 3.6 秒/短超时会把 ask_user 这类等待型工具直接掐断）
        env["MCP_TIMEOUT"] = "3600000"
        env["MCP_TOOL_TIMEOUT"] = "900000"
        return env

    def _argv_base(self) -> list[str]:
        p = self._cli_paths()
        if p["node"] and p["bundle"]:
            return [p["node"], str(p["bundle"])]
        shim = p["shim"]
        if shim:
            if shim.lower().endswith((".cmd", ".bat")):
                return ["cmd", "/c", shim]
            return [shim]
        raise RuntimeError("未找到 Qoder CLI：请安装 npm install -g @qoder-ai/qodercli 并运行 qodercli login")

    def available(self) -> bool:
        p = self._cli_paths()
        return bool((p["node"] and p["bundle"]) or p["shim"])

    def logged_in(self) -> bool:
        p = Path(os.environ.get("USERPROFILE", "")) / ".qoder" / ".auth" / "user"
        try:
            return p.exists() and p.stat().st_size > 0
        except OSError:
            return False

    def status(self) -> dict:
        p = self._cli_paths()
        return {
            "ok": True,
            "available": self.available(),
            "logged_in": self.logged_in(),
            "cli": str(p["bundle"] or p["shim"] or ""),
            "models": len(self.models()) if self.logged_in() else 0,
        }

    # ---------- 模型目录 ----------
    def models(self, force: bool = False) -> list[dict]:
        now = time.time()
        if not force and self._models is not None and now - self._models_at < MODEL_TTL:
            return self._models
        if not self.logged_in():
            return []
        argv = self._argv_base() + ["--list-models"]
        try:
            import subprocess
            r = subprocess.run(argv, capture_output=True, timeout=30, env=self._env(),
                               creationflags=CREATE_NO_WINDOW, encoding="utf-8", errors="replace")
            text = (r.stdout or "") + "\n" + (r.stderr or "")
        except Exception as e:
            _log(f"模型目录拉取失败: {type(e).__name__}: {e}")
            return self._models or []
        out: list[dict] = []
        for line in text.splitlines():
            s = line.strip()
            if not s or s.upper() == "MODEL":
                continue
            if any(ch in s for ch in "/\\:"):  # 跳过错误路径等噪声行
                continue
            if len(s) > 60:
                continue
            if s.upper() in ("MODEL", "MODELS"):
                continue
            out.append({"id": s, "name": s, "ctx": _ctx_for(s)})
        # 去重保序
        seen = set()
        out = [m for m in out if not (m["id"] in seen or seen.add(m["id"]))]
        if out:
            self._models = out
            self._models_at = now
        return out

    # ---------- 会话映射（Claw 会话 → Qoder 会话） ----------
    def _load_sessions(self) -> dict:
        if self._sessions is None:
            try:
                d = json.loads(self.sessions_path.read_text(encoding="utf-8"))
                self._sessions = d if isinstance(d, dict) else {}
            except Exception:
                self._sessions = {}
        return self._sessions

    def _save_sessions(self) -> None:
        try:
            self.sessions_path.write_text(
                json.dumps(self._sessions or {}, ensure_ascii=False, indent=1), encoding="utf-8"
            )
        except OSError:
            pass

    def _session_id(self, claw_session: str, fresh: bool = False) -> tuple[str, bool]:
        """返回 (qoder session id, 是否 resume)。"""
        s = self._load_sessions()
        ent = s.get(claw_session)
        if ent and not fresh and ent.get("qid"):
            return str(ent["qid"]), True
        qid = str(uuid.uuid4())
        s[claw_session] = {"qid": qid, "created": int(time.time())}
        self._save_sessions()
        return qid, False

    # ---------- 用量记账（仅统计，便于设置面板展示） ----------
    def usage(self) -> dict:
        try:
            d = json.loads(self.usage_path.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    def _usage_add(self, credits: float) -> None:
        if not credits:
            return
        today = time.strftime("%Y-%m-%d")
        d = self.usage()
        rec = d.get(today) if isinstance(d.get(today), dict) else {"calls": 0, "credits": 0.0}
        rec["calls"] = int(rec.get("calls") or 0) + 1
        rec["credits"] = round(float(rec.get("credits") or 0) + float(credits), 4)
        d[today] = rec
        if len(d) > 30:  # 只留最近 30 天
            for k in sorted(d)[:-30]:
                d.pop(k, None)
        try:
            self.usage_path.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass

    # ---------- 请求构造 ----------
    @staticmethod
    def _last_user_text(body: dict) -> str:
        for m in reversed(body.get("messages") or []):
            if m.get("role") != "user":
                continue
            c = m.get("content")
            if isinstance(c, str):
                return c
            if isinstance(c, list):
                parts = [str(b.get("text") or "") for b in c if isinstance(b, dict) and b.get("type") == "text"]
                return "\n".join(parts)
        return ""

    @staticmethod
    def _sse(obj: dict) -> str:
        return "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n"

    @staticmethod
    def _is_session_error(msg: str) -> bool:
        low = (msg or "").lower()
        return any(k in low for k in ("session", "会话", "resume", "not found", "in use"))

    # ---------- MCP 桥接配置（内联 JSON 传给 CLI 的 --mcp-config） ----------
    def _mcp_config(self, cwd: str, permission: str, unattended: bool = False,
                    claw_session: str = "default") -> str:
        url = (
            f"http://127.0.0.1:{self.mcp_port}/internal/mcp"
            f"?token={quote(self.mcp_token)}&cwd={quote(str(cwd))}&permission={quote(permission or 'standard')}"
            f"&unattended={1 if unattended else 0}"
            f"&session={quote(str(claw_session or 'default'))}"
        )
        return json.dumps({
            "mcpServers": {
                "claw-agent": {
                    "type": "http",
                    "url": url,
                    # timeout 单位是毫秒（写 3600 会被当成 3.6 秒，把等待型工具全部掐断）
                    "timeout": 3600000,
                    # trust=true：跳过 CLI 侧的逐次确认弹窗（无头模式无人应答）。
                    # 真正的权限门在 Claw 侧（execute_tool 按权限档位判定）。
                    "trust": True,
                    "description": "Claw Agent 框架工具（记忆/技能/插件/浏览器/提问/更新），自动注入",
                }
            }
        }, ensure_ascii=False)

    # ---------- 单轮执行（异步生成器：事件元组） ----------
    async def _run_once(self, prompt: str, qid: str, resume: bool, model: str,
                        permission: str, cwd: str | None, attachments: list[str] | None = None,
                        reasoning_effort: str = "", max_tokens=None, persist: bool = True,
                        unattended: bool = False, unattended_minutes: int = 0,
                        unattended_deadline: float = 0.0, claw_prompt: bool = True,
                        runtime_limit: float = 0.0, stall_limit: float = 0.0,
                        use_mcp: bool = True, claw_session: str = "default",
                        tool_budget: int = 40):
        argv = self._argv_base() + ["-p", "--output-format", "stream-json"]
        # 模型请求瞬时故障由 CLI 自己重试（网络抖动/5xx），失败再由上层重开 CLI
        argv += ["--max-model-request-retries", str(int(self.max_model_retries or _MODEL_REQUEST_RETRIES))]
        if claw_prompt:
            _ap = _append_system_prompt(unattended, unattended_minutes, unattended_deadline,
                                        tool_budget)
            try:
                import diag as _dg

                _dg.info("qoder.prompt", unattended=bool(unattended), budget=tool_budget,
                         chars=len(_ap), minutes=unattended_minutes,
                         has_boundary="【任务边界】" in _ap, has_rules="【无人监管·自主模式】" in _ap)
            except Exception:
                pass
            argv += ["--append-system-prompt", _ap]
        # MCP 桥接：内联配置注入（token/权限随会话变），并只放行本服务器
        if use_mcp and self.mcp_port and self.mcp_token and cwd:
            try:
                argv += ["--mcp-config", self._mcp_config(cwd, permission, unattended, claw_session),
                         "--allowed-mcp-server-names", "claw-agent",
                         # CLI 侧不认 MCP 工具默认要逐次确认（无头模式无人应答 → 直接失败），
                         # 这里放行整个服务器；真正的权限门在 Claw 侧按权限档位判定。
                         "--settings", json.dumps(
                             {"permissions": {"allow": ["mcp__claw-agent__*"]}},
                             ensure_ascii=False)]
            except Exception as e:
                _log(f"生成 MCP 配置失败: {type(e).__name__}: {e}")
        if persist:
            argv += ["--resume", qid] if resume else ["--session-id", qid]
        else:
            argv += ["--no-session-persistence"]
        if model:
            argv += ["-m", model]
        eff = _EFFORT_MAP.get(str(reasoning_effort or "").lower())
        if eff:
            argv += ["--reasoning-effort", eff]
        argv += ["--context-window", str(_ctx_for(model or "auto"))]
        try:
            if max_tokens:
                argv += ["--max-output-tokens", str(int(max_tokens))]
        except (TypeError, ValueError):
            pass
        for a in (attachments or []):
            argv += ["--attachment", str(a)]
        if permission == "readonly":
            argv += ["--tools"] + _READONLY_TOOLS + ["--permission-mode", "default"]
        elif permission == "full":
            argv += ["--permission-mode", "bypass_permissions"]
        else:
            # ask / standard：只放开只读内置工具，写操作必须走 Claw 的 MCP 工具，
            # 这样审批闸门才是唯一入口（CLI 自己的 Write/Bash 会绕过闸门）。
            argv += ["--tools"] + _READONLY_TOOLS + ["--permission-mode", "accept_edits"]
        if cwd:
            argv += ["-w", cwd]

        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd or None,
            env=self._env(),
            creationflags=CREATE_NO_WINDOW,
            limit=_STREAM_LINE_LIMIT,  # 工具结果可能是超大单行 JSON（默认 64KB 会 LimitOverrun）
        )
        stderr_tail: list[str] = []
        finish = {"saw_result": False, "credits": 0.0, "text": ""}

        async def pump_stderr():
            try:
                while True:
                    line = await proc.stderr.readline()
                    if not line:
                        break
                    s = line.decode("utf-8", "replace").rstrip()
                    if s:
                        stderr_tail.append(s)
                        del stderr_tail[:-10]
                        _log("stderr: " + s[:300])
            except Exception:
                pass

        err_task = asyncio.create_task(pump_stderr())
        t0 = time.time()
        try:
            try:
                proc.stdin.write(prompt.encode("utf-8"))
                await proc.stdin.drain()
                proc.stdin.close()
            except Exception as e:
                _log(f"stdin 写入失败: {type(e).__name__}: {e}")
            try:
                while True:
                    if runtime_limit and time.time() - t0 > runtime_limit:
                        try:
                            proc.kill()
                        except Exception:
                            pass
                        yield ("error", f"TIMEOUT: Qoder 单轮执行超过 {int(runtime_limit // 60)} 分钟，已中断本轮 CLI")
                        return
                    try:
                        line = await asyncio.wait_for(
                            proc.stdout.readline(), timeout=stall_limit) if stall_limit else await proc.stdout.readline()
                    except asyncio.TimeoutError:
                        # 长时间没有任何输出：判定卡死（工具挂住 / 网络断开），中断后交给上层续跑
                        try:
                            proc.kill()
                        except Exception:
                            pass
                        yield ("error", f"TIMEOUT: Qoder 已 {int(stall_limit // 60)} 分钟没有任何输出，判定为卡死并中断")
                        return
                    if not line:
                        break
                    s = line.decode("utf-8", "replace").strip()
                    if not s:
                        continue
                    try:
                        ev = json.loads(s)
                    except Exception:
                        continue
                    t = ev.get("type")
                    if t == "system" and ev.get("subtype") == "init":
                        servers = ev.get("mcp_servers") or []
                        if servers:
                            _log("MCP 服务器: " + json.dumps(servers, ensure_ascii=False)[:400])
                    elif t == "assistant":
                        for b in (ev.get("message") or {}).get("content") or []:
                            bt = b.get("type")
                            if bt == "thinking" and b.get("thinking"):
                                yield ("reasoning", str(b["thinking"]))
                            elif bt == "text" and b.get("text"):
                                yield ("content", str(b["text"]))
                            elif bt == "tool_use":
                                # MCP 元工具（mcp_call/mcp_get）带 toolName → 显示真实工具名与真实参数
                                tname = str(b.get("name") or "tool")
                                targs = b.get("input") if isinstance(b.get("input"), dict) else {}
                                real = str(targs.get("toolName") or "")
                                if real.startswith("mcp__"):
                                    tname = real
                                    inner = targs.get("arguments")
                                    if isinstance(inner, dict):
                                        targs = inner
                                yield ("tool_call", {
                                    "id": str(b.get("id") or ""),
                                    "name": tname,
                                    # 与本地工具路径一致：参数用 JSON 字符串
                                    "arguments": json.dumps(targs, ensure_ascii=False),
                                })
                    elif t == "user":
                        for b in (ev.get("message") or {}).get("content") or []:
                            if b.get("type") == "tool_result":
                                c = b.get("content")
                                if isinstance(c, list):
                                    snippet = " ".join(str(x.get("text") or "") for x in c if isinstance(x, dict))
                                else:
                                    snippet = str(c or "")
                                yield ("tool_result", {
                                    "id": str(b.get("tool_use_id") or ""),
                                    "ok": not bool(b.get("is_error")),
                                    "output": snippet[:4000],
                                })
                    elif t == "result":
                        finish["saw_result"] = True
                        finish["text"] = str(ev.get("result") or "")
                        try:
                            finish["credits"] = float(ev.get("total_credits") or 0)
                        except (TypeError, ValueError):
                            finish["credits"] = 0.0
                        if ev.get("is_error"):
                            yield ("error", finish["text"] or "Qoder 执行失败")
                        else:
                            yield ("done", finish)
                        return
            except Exception as e:
                # 任何读取/解析异常都不能打断垫片流：转成流内错误事件
                _log(f"读取 stream-json 失败: {type(e).__name__}: {e}")
                yield ("error", f"Qoder 输出流异常（{type(e).__name__}）：{str(e)[:160]}")
                return
        finally:
            try:
                if proc.returncode is None:
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=3)
                    except Exception:
                        try:
                            proc.kill()
                        except Exception:
                            pass
            finally:
                try:
                    await asyncio.wait_for(err_task, timeout=2)
                except Exception:
                    err_task.cancel()
        # 正常读到 EOF 但没拿到 result 事件
        if not finish["saw_result"]:
            code = proc.returncode
            tail = " | ".join(stderr_tail[-2:])[:300]
            yield ("error", f"Qoder CLI 提前退出（code={code}）{('：' + tail) if tail else ''}")

    # ---------- 对外：OpenAI SSE 流 ----------
    async def stream_chat(self, body: dict, *, session: str = "default",
                          permission: str = "standard", cwd: str | None = None,
                          attachments: list[str] | None = None,
                          unattended: bool = False):
        """把 Qoder CLI 的 stream-json 转成 agent 循环可消费的 OpenAI SSE 行。

        重试策略：
          - 会话失效（CLI 报 session 类错误，且尚未产生任何事件）→ 新建会话重跑一次；
          - 瞬时网络故障（timeout/连接重置/5xx…，且尚未产生任何事件）→ 按递增间隔重开 CLI；
          - 已产生事件（正文/思考/工具调用）后不再重试，避免重复输出或工具副作用翻倍。
        """
        prompt = self._last_user_text(body)
        try:
            _un_minutes = int(body.get("claw_unattended_minutes") or 0)
        except (TypeError, ValueError):
            _un_minutes = 0
        try:
            _un_deadline = float(body.get("claw_unattended_deadline") or 0)
        except (TypeError, ValueError):
            _un_deadline = 0.0
        try:
            _tool_budget = int(body.get("claw_tool_budget") or 40)
        except (TypeError, ValueError):
            _tool_budget = 40
        if not prompt.strip():
            yield self._sse({"error": {"message": "Qoder：没有可发送的用户消息"}})
            yield "data: [DONE]\n\n"
            return
        if not self.available():
            yield self._sse({"error": {"message": "Qoder 未接入：请先安装 npm install -g @qoder-ai/qodercli 并运行 qodercli login"}})
            yield "data: [DONE]\n\n"
            return
        if not self.logged_in():
            yield self._sse({"error": {"message": "Qoder 未登录：请在终端运行 qodercli login 完成授权"}})
            yield "data: [DONE]\n\n"
            return

        model = str(body.get("model") or "").strip()
        if model.startswith("qoder:"):
            model = model[6:]
        if model.lower() == "auto":
            model = ""  # 交给 CLI 默认
        effort = str(body.get("reasoning_effort") or "")
        max_tokens = body.get("max_tokens")

        session_fresh = False   # 会话失效后改用新会话
        transient_n = 0         # 瞬时网络故障已重试次数
        runtime_limit = float(self.max_runtime or 0)   # 0 = 不设上限
        stall_limit = float(self.stall_timeout or 0)
        if unattended:           # 无人监管：卡死判定更宽（时长上限不设）
            runtime_limit *= 3 if runtime_limit else 0
            stall_limit *= 2
        resume_prompt = prompt   # 超时续跑时改用「继续」提示
        timeout_resumes = 0
        while True:
            qid, resume = self._session_id(session or "default", fresh=session_fresh)
            produced = False    # 是否已向调用方下发正文/思考
            activity = False    # 是否已产生任何事件（含工具调用/结果，决定能否安全重试）
            retry_session = False
            retry_transient = False
            retry_resume = False
            err_msg = ""
            try:
                async for kind, payload in self._run_once(
                        resume_prompt, qid, resume, model, permission, cwd, attachments, effort,
                        max_tokens, unattended=unattended,
                        unattended_minutes=_un_minutes, unattended_deadline=_un_deadline,
                        runtime_limit=runtime_limit, stall_limit=stall_limit,
                        claw_session=session or "default", tool_budget=_tool_budget):
                    if kind == "reasoning":
                        produced = activity = True
                        yield self._sse({"choices": [{"delta": {"reasoning_content": payload}}]})
                    elif kind == "content":
                        produced = activity = True
                        yield self._sse({"choices": [{"delta": {"content": payload}}]})
                    elif kind == "tool_call":
                        activity = True
                        # 工具事件走 SSE 顶层自定义字段：让前端显示真实工具卡片
                        yield self._sse({"choices": [{"delta": {}}], "claw_tool_call": payload})
                    elif kind == "tool_result":
                        activity = True
                        yield self._sse({"choices": [{"delta": {}}], "claw_tool_result": payload})
                    elif kind == "error":
                        err_msg = str(payload)
                        if err_msg.startswith("TIMEOUT") and timeout_resumes < _TIMEOUT_RESUMES:
                            # 超时/卡死：中断本轮 CLI 后用同一会话续跑，而不是整轮作废
                            timeout_resumes += 1
                            resume_prompt = _RESUME_AFTER_TIMEOUT
                            retry_resume = True
                            _log(f"超时中断，自动续跑（{timeout_resumes}/{_TIMEOUT_RESUMES}）：{err_msg[:120]}")
                            break
                        if not activity and not session_fresh and self._is_session_error(err_msg):
                            retry_session = True
                            session_fresh = True
                            _log(f"会话 {qid[:8]} 不可用，创建新会话重试：{err_msg[:120]}")
                            break
                        if (not activity and transient_n < len(_TRANSIENT_RETRY_DELAYS)
                                and _looks_transient(err_msg)):
                            retry_transient = True
                            break
                        yield self._sse({"error": {"message": err_msg.replace("TIMEOUT: ", "", 1)}})
                        yield "data: [DONE]\n\n"
                        return
                    elif kind == "done":
                        try:
                            self._usage_add(float((payload or {}).get("credits") or 0))
                        except Exception:
                            pass
                        if not produced and (payload or {}).get("text"):
                            yield self._sse({"choices": [{"delta": {"content": str(payload["text"])}}]})
                        yield "data: [DONE]\n\n"
                        return
            except asyncio.CancelledError:
                raise
            except Exception as e:
                err_msg = f"{type(e).__name__}: {e}"
                if not activity and transient_n < len(_TRANSIENT_RETRY_DELAYS) and _looks_transient(err_msg):
                    retry_transient = True
                else:
                    # 兜底：任何异常都不能打断垫片流
                    _log(f"stream_chat 异常: {err_msg}")
                    yield self._sse({"error": {"message": f"Qoder 调用异常（{type(e).__name__}）：{str(e)[:160]}"}})
                    yield "data: [DONE]\n\n"
                    return
            if retry_resume:
                continue
            if retry_transient:
                delay = _TRANSIENT_RETRY_DELAYS[transient_n]
                transient_n += 1
                _log(f"瞬时故障（{err_msg[:120]}），{delay:.0f}s 后重开 CLI（{transient_n}/{len(_TRANSIENT_RETRY_DELAYS)}）")
                await asyncio.sleep(delay)
                continue
            if retry_session:
                continue
            if not activity:
                yield self._sse({"error": {"message": "Qoder CLI 未返回结果"}})
            yield "data: [DONE]\n\n"
            return

    # ---------- 单次聚合调用（压缩摘要 / 图片识别等专家任务） ----------
    async def complete(self, *, model: str, prompt: str,
                       attachments: list[str] | None = None,
                       permission: str = "readonly", max_tokens=None,
                       cwd: str | None = None, use_mcp: bool = True) -> str:
        """一次性执行并聚合文本结果；不保留会话（--no-session-persistence）。

        cwd：让 Qoder CLI 在该目录里工作（办公室员工用它落到会话工作目录）。
        use_mcp：是否注入 claw-agent MCP 桥；办公室员工关掉，避免递归调用 Claw 工具。
        """
        model = str(model or "").strip()
        if model.startswith("qoder:"):
            model = model[6:]
        if model.lower() == "auto":
            model = ""
        parts: list[str] = []
        err = ""
        async for kind, payload in self._run_once(
                prompt, "", False, model, permission, cwd, attachments, "", max_tokens,
                persist=False, claw_prompt=False, use_mcp=use_mcp):
            if kind == "content":
                parts.append(str(payload))
            elif kind == "error":
                err = str(payload)
            elif kind == "done":
                if not parts and (payload or {}).get("text"):
                    parts.append(str(payload["text"]))
        if err and not parts:
            raise RuntimeError(err)
        return "".join(parts).strip()

    # ---------- 额度查询（驱动交互式 TUI 的 /usage，10 分钟缓存） ----------
    @staticmethod
    def _clean_ansi(text: str) -> str:
        text = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", text)
        text = re.sub(r"\x1b\][^\x07]*\x07", "", text)
        return text.replace("\r", "")

    @staticmethod
    def _wait_for(pred, timeout: float) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if pred():
                return True
            time.sleep(0.2)
        return pred()

    def _quota_via_tui(self) -> dict:
        from winpty import PtyProcess  # 延迟导入（打包时需 collect-all winpty）

        argv = self._argv_base()
        env = self._env()
        env.setdefault("TERM", "xterm-256color")
        cwd = str(self.data_root)
        proc = PtyProcess.spawn(argv, dimensions=(40, 120), env=env, cwd=cwd)
        buf: list[str] = []
        stop = {"v": False}

        def reader():
            while not stop["v"]:
                try:
                    chunk = proc.read(4096)
                    if chunk:
                        buf.append(chunk)
                except EOFError:
                    break
                except Exception:
                    break

        threading.Thread(target=reader, daemon=True).start()

        def text() -> str:
            return self._clean_ansi("".join(buf))

        def compact() -> str:
            return re.sub(r"\s+", "", text())

        def send(s: str) -> None:
            try:
                proc.write(s)
            except Exception:
                pass

        try:
            ok = self._wait_for(
                lambda: ("trust" in text().lower()) or ("typeyourmessage" in compact().lower()),
                20,
            )
            if not ok:
                raise RuntimeError("Qoder TUI 未就绪")
            if "trust" in text().lower():
                send("\r")  # 选择「1. Trust folder」
                time.sleep(1.5)
            send("/usage\r")
            if not self._wait_for(
                lambda: ("plancreditsused" in compact().lower()) or ("addoncreditsused" in compact().lower()),
                25,
            ):
                raise RuntimeError("额度面板未出现（TUI 响应超时）")
            time.sleep(0.5)
            t = text()
            whole = re.sub(r"\s+", "", t)
            out: dict = {"ok": True}
            for line in t.splitlines():
                compact_line = line.replace(" ", "").strip()
                if compact_line.startswith("QoderPlan:"):
                    out["plan"] = compact_line.split(":", 1)[1]
                elif compact_line.startswith("PlanExpiresAt:"):
                    out["plan_expires"] = compact_line.split(":", 1)[1]
                elif compact_line.startswith("PlanCreditsUsed:"):
                    m = re.match(r"PlanCreditsUsed:(\d+)/(\d+)", compact_line)
                    if m:
                        out["plan_used"], out["plan_limit"] = int(m.group(1)), int(m.group(2))
                elif compact_line.startswith("Add-onCreditsUsed:"):
                    m = re.match(r"Add-onCreditsUsed:(\d+)/(\d+)", compact_line)
                    if m:
                        out["addon_used"], out["addon_limit"] = int(m.group(1)), int(m.group(2))
                elif compact_line.startswith("OrgResourcePackage:"):
                    out["org_package"] = compact_line.split(":", 1)[1]
            # 兜底：整段压缩文本再匹配一次（TUI 有时不换行）
            if "plan_used" not in out:
                m = re.search(r"PlanCreditsUsed:(\d+)/(\d+)", whole)
                if m:
                    out["plan_used"], out["plan_limit"] = int(m.group(1)), int(m.group(2))
            if "addon_used" not in out:
                m = re.search(r"Add-onCreditsUsed:(\d+)/(\d+)", whole)
                if m:
                    out["addon_used"], out["addon_limit"] = int(m.group(1)), int(m.group(2))
            if "plan" not in out:
                m = re.search(r"QoderPlan:([A-Za-z0-9._-]+?)(?=PlanExpiresAt|PlanCreditsUsed|Add-on|Org|Total|$)", whole)
                if m:
                    out["plan"] = m.group(1)
            if "plan_used" not in out and "addon_used" not in out:
                raise RuntimeError("额度解析失败")
            try:
                out["remaining"] = max(
                    0,
                    int(out.get("plan_limit", 0)) - int(out.get("plan_used", 0))
                    + int(out.get("addon_limit", 0)) - int(out.get("addon_used", 0)),
                )
            except (TypeError, ValueError):
                out["remaining"] = None
            return out
        finally:
            send("\x1b")
            time.sleep(0.3)
            send("/quit\r")
            time.sleep(0.5)
            try:
                proc.terminate(force=True)
            except Exception:
                pass
            stop["v"] = True

    def quota_cached(self) -> dict | None:
        try:
            d = json.loads((self.data_dir / "qoder_quota.json").read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else None
        except Exception:
            return None


class QoderQuotaProvider(QuotaProvider):
    """Qoder 额度来源：驱动交互式 TUI 的 /usage 面板抓真实额度（10 分钟缓存）。

    新接一家额度提供方时照抄这个类：写 fetch()（真实抓取）+ normalize()（映射到
    统一形状），然后在 main.py 里 QUOTA.register(...) 注册一行即可，前端自动出现
    一套额度卡片。
    """

    id = "qoder"
    name = "Qoder"
    unit = "credits"
    note = "额度来自 Qoder 官方 /usage 面板（10 分钟缓存）"
    cache_file = "qoder_quota.json"

    def __init__(self, qd: "QoderProvider", data_dir: Path):
        super().__init__(data_dir)
        self.qd = qd

    def available(self) -> bool:
        return bool(self.qd.available() and self.qd.logged_in())

    def status(self) -> dict:
        try:
            st = self.qd.status()
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"[:200]}
        if not st.get("available"):
            st["summary"] = "未安装 Qoder CLI"
        elif not st.get("logged_in"):
            st["summary"] = "CLI 未登录"
        else:
            st["summary"] = f"已登录 · {st.get('models', 0)} 个模型"
        return st

    def usage(self) -> dict:
        try:
            return self.qd.usage()
        except Exception:
            return {}

    def fetch(self) -> dict:
        return self.qd._quota_via_tui()

    def normalize(self, raw: dict) -> dict:
        raw = raw or {}
        packages = []
        plan_limit = raw.get("plan_limit")
        plan_used = raw.get("plan_used")
        if plan_limit:
            packages.append({
                "name": "计划额度",
                "used": plan_used or 0,
                "limit": plan_limit,
                "remaining": max(0, int(plan_limit) - int(plan_used or 0)),
            })
        addon_limit = raw.get("addon_limit")
        addon_used = raw.get("addon_used")
        if addon_limit:
            packages.append({
                "name": "加量包",
                "used": addon_used or 0,
                "limit": addon_limit,
                "remaining": max(0, int(addon_limit) - int(addon_used or 0)),
            })
        out = super().normalize(raw)
        out.update({
            "ok": bool(raw.get("ok", True)),
            "error": str(raw.get("error") or ""),
            "remaining": raw.get("remaining"),
            "plan": raw.get("plan"),
            "packages": packages,
            "plan_expires": raw.get("plan_expires"),
            "org_package": raw.get("org_package"),
        })
        return out

