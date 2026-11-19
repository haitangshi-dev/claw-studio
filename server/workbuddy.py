# -*- coding: utf-8 -*-
"""WorkBuddy 云端模型提供方：读取本机 WorkBuddy 桌面版登录态，调用其云端模型接口。

机制（参考社区 dsh-workbuddy-connect 与 Sliverkiss/workbuddy2api 的公开实现，2026-10-07 实测打通）：
  - 登录凭据：%LOCALAPPDATA%\CodeBuddyExtension\Data\Public\auth\workbuddy-desktop.info（明文 JSON）
      {auth: {accessToken, refreshToken, expiresAt, domain}, account: {uid, nickname, ...}}
  - 聊天：POST https://copilot.tencent.com/v2/chat/completions （Bearer + 一组身份头，SSE）
  - 续期：POST /v2/plugin/auth/token/refresh （X-Refresh-Token），只写本框架自有副本，不写桌面文件
  - 目录：GET /v3/config → data.models（id/name/credits/contextWindow/reasoning…）

只读用户的桌面登录文件；续期结果存 <DATA_ROOT>/data/workbuddy_auth.json（自有副本）。
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import httpx

# ---- 常量（与桌面客户端身份保持一致；2026-10-07 实测可用） ----
CN_CHAT_BASE = "https://copilot.tencent.com"
CN_ORIGIN = "https://www.codebuddy.cn"
GLOBAL_BASE = "https://www.workbuddy.ai"
CLIENT_VERSION = "5.5.6"  # 与 dsh 插件的 CN fallback 一致；如上游收紧可改为读本机 App 版本
UA = f"WorkBuddy/{CLIENT_VERSION} WorkBuddy/{CLIENT_VERSION} CLI/2.63.2"
DESKTOP_FILENAMES = ("workbuddy-desktop.info", "workbuddy-desktop-ai.info")
REFRESH_MARGIN_MS = 5 * 60 * 1000  # 距到期 5 分钟内触发续期
CATALOG_TTL_SEC = 3600  # 目录缓存 1 小时
CATALOG_SCHEMA = 2  # 目录条目结构版本：改了 _normalize_model 的字段就 +1（旧缓存自动失效重拉）
CONTEXT_FALLBACK = 192000  # 上游完全没声明窗口时的保守估值（同族最小档）
STREAM_ATTEMPTS = 3  # 流式请求最大尝试次数（仅"尚未下发任何数据"时自动重试）
RETRY_STATUSES = {500, 502, 503, 504}  # 上游瞬时故障码（429 限流不重试）


def _log(msg: str) -> None:
    try:
        print(f"[WorkBuddy] {time.strftime('%H:%M:%S')} {msg}", flush=True)
    except Exception:
        pass


def _expiry_to_ms(v) -> int:
    if not isinstance(v, (int, float)) or v <= 0:
        return 0
    return int(v if v > 1e12 else v * 1000)


class WorkBuddyProvider:
    def __init__(self, data_root: str | Path):
        self.dir = Path(data_root) / "data"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.own_auth_path = self.dir / "workbuddy_auth.json"
        self.catalog_path = self.dir / "workbuddy_catalog.json"
        self._catalog: list[dict] | None = None
        self._catalog_at = 0.0

    # ---------- 凭据 ----------
    def _desktop_candidates(self) -> list[Path]:
        env = os.environ.get("WORKBUDDY_AUTH_FILE", "").strip()
        if env:
            return [Path(env)]
        out: list[Path] = []
        for root in (os.environ.get("LOCALAPPDATA", ""), os.environ.get("APPDATA", "")):
            if not root:
                continue
            for name in DESKTOP_FILENAMES:
                out.append(Path(root) / "CodeBuddyExtension" / "Data" / "Public" / "auth" / name)
        return out

    @staticmethod
    def _parse_auth_doc(text: str) -> dict | None:
        try:
            doc = json.loads(text)
        except Exception:
            return None
        if not isinstance(doc, dict):
            return None
        auth = doc.get("auth") if isinstance(doc.get("auth"), dict) else doc
        account = doc.get("account") if isinstance(doc.get("account"), dict) else doc
        token = auth.get("accessToken")
        if not isinstance(token, str) or not token:
            return None
        return {
            "accessToken": token,
            "refreshToken": str(auth.get("refreshToken") or ""),
            "expiresAtMs": _expiry_to_ms(auth.get("expiresAt") or auth.get("expiresAtMs") or 0),
            "domain": str(auth.get("domain") or ""),
            "uid": str(account.get("uid") or ""),
            "enterpriseId": str(account.get("enterpriseId") or ""),
            "nickname": str(account.get("nickname") or ""),
        }

    def _read_own(self) -> dict | None:
        try:
            doc = json.loads(self.own_auth_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        cred = doc.get("credential") if isinstance(doc, dict) else None
        if isinstance(cred, dict) and cred.get("accessToken"):
            return cred
        return None

    def _read_desktop(self) -> dict | None:
        for p in self._desktop_candidates():
            try:
                text = p.read_text(encoding="utf-8")
            except OSError:
                continue
            if not text.strip():
                continue
            return self._parse_auth_doc(text)
        return None

    def credential(self) -> dict | None:
        """桌面文件为主、自有副本为辅：身份一致取更晚到期者；身份不同以桌面为准。"""
        desktop = self._read_desktop()
        own = self._read_own()
        # 桌面（GLOBAL）与 CN 文件名不同，读到什么用什么
        if desktop is None:
            return own
        if own is None:
            return desktop
        if own.get("uid") and own.get("uid") != desktop.get("uid"):
            return desktop
        return own if (own.get("expiresAtMs") or 0) > (desktop.get("expiresAtMs") or 0) else desktop

    def _base_for(self, domain: str) -> str:
        d = (domain or "").strip().lower()
        return GLOBAL_BASE if d.endswith("workbuddy.ai") else CN_CHAT_BASE

    def _origin_for(self, domain: str) -> str:
        d = (domain or "").strip().lower()
        return GLOBAL_BASE if d.endswith("workbuddy.ai") else CN_ORIGIN

    def _common_headers(self, cred: dict) -> dict:
        origin = self._origin_for(cred.get("domain", ""))
        return {
            "Accept": "application/json, text/plain, */*",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": origin,
            "Referer": f"{origin}/",
            "User-Agent": UA,
        }

    def chat_headers(self, cred: dict) -> dict:
        h = dict(self._common_headers(cred))
        h["Content-Type"] = "application/json"
        h["Authorization"] = f"Bearer {cred['accessToken']}"
        if cred.get("uid"):
            h["X-User-Id"] = cred["uid"]
        else:
            h["X-No-User-Id"] = "1"
        if cred.get("enterpriseId"):
            h["X-Enterprise-Id"] = cred["enterpriseId"]
        else:
            h["X-No-Enterprise-Id"] = "1"
        if cred.get("domain"):
            h["X-Domain"] = cred["domain"]
        else:
            h["X-No-Department-Info"] = "1"
        h["X-IDE-Type"] = "WorkBuddy"
        h["X-IDE-Name"] = "WorkBuddy"
        h["X-IDE-Version"] = CLIENT_VERSION
        h["X-Product"] = "SaaS"
        return h

    # ---------- 续期 ----------
    def _save_own(self, cred: dict) -> None:
        try:
            self.own_auth_path.write_text(
                json.dumps({"version": 1, "credential": cred}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass

    def _refresh(self, cred: dict) -> dict | None:
        base = self._base_for(cred.get("domain", ""))
        h = dict(self._common_headers(cred))
        h["X-Refresh-Token"] = cred.get("refreshToken", "")
        h["X-Auth-Refresh-Source"] = "workbuddy"
        if cred.get("enterpriseId"):
            h["X-Enterprise-Id"] = cred["enterpriseId"]
        try:
            r = httpx.post(f"{base}/v2/plugin/auth/token/refresh", headers=h, timeout=30)
            env = r.json()
        except Exception:
            return None
        data = env.get("data") if isinstance(env, dict) else None
        if not isinstance(data, dict):
            return None
        token = data.get("accessToken")
        if not isinstance(token, str) or not token:
            return None
        now_ms = int(time.time() * 1000)
        refreshed = dict(cred)
        refreshed["accessToken"] = token
        if isinstance(data.get("refreshToken"), str) and data["refreshToken"]:
            refreshed["refreshToken"] = data["refreshToken"]
        exp_in = data.get("expiresIn")
        if isinstance(exp_in, (int, float)) and exp_in > 0:
            refreshed["expiresAtMs"] = now_ms + int(exp_in) * 1000
        if isinstance(data.get("domain"), str) and data["domain"]:
            refreshed["domain"] = data["domain"]
        self._save_own(refreshed)
        return refreshed

    def effective_credential(self) -> dict:
        """可用凭据（必要时续期）；不可用抛 RuntimeError（消息给用户看）。"""
        cred = self.credential()
        if cred is None:
            raise RuntimeError("未找到 WorkBuddy 登录信息：请先打开 WorkBuddy 桌面版并登录一次")
        exp = cred.get("expiresAtMs") or 0
        if exp and exp > int(time.time() * 1000) + REFRESH_MARGIN_MS:
            return cred
        if cred.get("refreshToken"):
            renewed = self._refresh(cred)
            if renewed is not None:
                return renewed
            if exp > int(time.time() * 1000) + 30_000:
                return cred  # 续期失败但尚未过期，继续用
        if exp and exp > int(time.time() * 1000) + 30_000:
            return cred
        raise RuntimeError("WorkBuddy 登录已过期且自动续期失败：请打开 WorkBuddy 桌面版重新登录")

    def status(self) -> dict:
        cred = self.credential()
        if cred is None:
            return {"ok": False, "signed_in": False, "error": "未找到 WorkBuddy 登录文件"}
        now_ms = int(time.time() * 1000)
        return {
            "ok": True,
            "signed_in": True,
            "nickname": cred.get("nickname", ""),
            "uid": (cred.get("uid") or "")[:8],
            "domain": cred.get("domain", ""),
            "expires_at": cred.get("expiresAtMs") or 0,
            "expired": bool(cred.get("expiresAtMs") and cred["expiresAtMs"] < now_ms),
        }

    # ---------- 模型目录 ----------
    @staticmethod
    def _chat_capable(mid: str) -> bool:
        """过滤补全/代码内部/图像生成类模型（对话不可用），保守黑名单。"""
        low = (mid or "").lower()
        return not ("completion" in low or low.startswith("codewise") or "image" in low)

    @staticmethod
    def _int_or_none(v) -> int | None:
        """上游字段有的是数字、有的是字符串、有的是对象；统一成正整数或 None。"""
        if isinstance(v, bool):
            return None
        if isinstance(v, (int, float)):
            return int(v) if v > 0 else None
        if isinstance(v, str):
            s = v.strip()
            if s.isdigit():
                return int(s) or None
        return None

    @classmethod
    def _context_window(cls, m: dict) -> int:
        """该模型的真实上下文上限（输入侧）。

        优先级：maxInputTokens（上游真正校验的输入上限）
              → contextWindow.defaultLength（声明窗口）
              → maxAllowedSize（含输出的总包上限）
              → CONTEXT_FALLBACK（完全未声明时按同族保守值估）
        注意用 maxInputTokens 而不是 contextWindow：很多模型 contextWindow 为 null
        但 maxInputTokens 是明确值（如 kimi-k2.6 = 256K、deepseek-v3-1 = 96K），
        旧逻辑一律回落 192K，会把 31K 的小模型当 192K 用、把 1M 的大模型当 300K 用。
        """
        for v in (m.get("maxInputTokens"), cls._ctx_of_window(m.get("contextWindow")), m.get("maxAllowedSize")):
            n = cls._int_or_none(v)
            if n:
                return n
        return CONTEXT_FALLBACK

    @classmethod
    def _ctx_of_window(cls, cw) -> int | None:
        """contextWindow 可能是 {defaultLength, supportedLengths:[...]}。"""
        if isinstance(cw, dict):
            return cls._int_or_none(cw.get("defaultLength")) or cls._int_or_none(
                (cw.get("supportedLengths") or [None])[0]
            )
        return cls._int_or_none(cw)

    @classmethod
    def _normalize_model(cls, m: dict) -> dict | None:
        mid = m.get("id")
        if not isinstance(mid, str) or not mid:
            return None
        if not cls._chat_capable(mid):
            return None
        ctx = cls._context_window(m)
        max_out = cls._int_or_none(m.get("maxOutputTokens"))
        raw_reason = m.get("reasoning") if isinstance(m.get("reasoning"), dict) else {}
        efforts = [e for e in (raw_reason.get("supportedEfforts") or []) if isinstance(e, str)]
        return {
            "id": mid,
            "name": str(m.get("name") or mid),
            "credits": str(m.get("credits") or ""),
            "free": str(m.get("credits") or "").strip() in ("x0.00", "x0"),
            "tools": bool(m.get("supportsToolCall")),
            "images": bool(m.get("supportsImages")),
            "reasoning": bool(m.get("supportsReasoning")),
            "ctx": ctx,
            "max_out": max_out,
            # 思考档位：仅上游声明 supportedEfforts 的模型可调（与 dsh 插件同规则）
            "efforts": efforts,
            "can_off": raw_reason.get("canDisableThinking") is True and bool(efforts),
            "effort_default": str(raw_reason.get("defaultEffort") or raw_reason.get("effort") or ""),
        }

    def catalog(self, force: bool = False) -> list[dict]:
        """模型目录（内存 → 磁盘缓存 → 上游）。"""
        now = time.time()
        if not force and self._catalog is not None and now - self._catalog_at < CATALOG_TTL_SEC:
            return self._catalog
        if not force:
            try:
                doc = json.loads(self.catalog_path.read_text(encoding="utf-8"))
                if (
                    now - float(doc.get("fetched_at") or 0) < CATALOG_TTL_SEC
                    and int(doc.get("schema") or 0) == CATALOG_SCHEMA
                    and isinstance(doc.get("models"), list)
                ):
                    self._catalog = doc["models"]
                    self._catalog_at = float(doc["fetched_at"])
                    return self._catalog
            except Exception:
                pass
        cred = self.effective_credential()
        base = self._base_for(cred.get("domain", ""))
        h = dict(self._common_headers(cred))
        h["Authorization"] = f"Bearer {cred['accessToken']}"
        r = httpx.get(f"{base}/v3/config", headers=h, timeout=30)
        env = r.json()
        if not isinstance(env, dict) or env.get("code") != 0:
            raise RuntimeError(f"目录拉取失败: {str(env)[:200]}")
        raw = ((env.get("data") or {}).get("models")) or []
        models = [x for x in (self._normalize_model(m) for m in raw if isinstance(m, dict)) if x]
        if not models:
            raise RuntimeError("目录为空（上游未返回模型列表）")
        self._catalog = models
        self._catalog_at = now
        try:
            self.catalog_path.write_text(
                json.dumps({"schema": CATALOG_SCHEMA, "fetched_at": now, "models": models}, ensure_ascii=False, indent=1),
                encoding="utf-8",
            )
        except OSError:
            pass
        return models

    # ---------- 积分（billing） ----------
    def credits(self) -> dict:
        """查询账号积分/套餐：返回各额度包的余额与周期用量。"""
        cred = self.effective_credential()
        base = CN_ORIGIN if self._base_for(cred.get("domain", "")) == CN_CHAT_BASE else GLOBAL_BASE
        h = dict(self._common_headers(cred))  # Origin/Referer/UA 必带（否则 10085 请求不合法）
        h.update({
            "Authorization": f"Bearer {cred['accessToken']}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        })
        if cred.get("uid"):
            h["X-User-Id"] = cred["uid"]
        if cred.get("enterpriseId"):
            h["X-Enterprise-Id"] = cred["enterpriseId"]
            h["X-Tenant-Id"] = cred["enterpriseId"]
        if cred.get("domain"):
            h["X-Domain"] = cred["domain"]
        r = httpx.post(f"{base}/v2/billing/meter/get-user-resource", headers=h, json={}, timeout=30)
        env = r.json()
        inner = ((env.get("data") or {}).get("Response") or {}).get("Data") or {}
        accounts = []
        for a in inner.get("Accounts", []) or []:
            if not isinstance(a, dict):
                continue
            accounts.append({
                "package": str(a.get("PackageName") or a.get("SubProductName") or "额度包"),
                "cycle_remain": a.get("CycleCapacityRemainPrecise", a.get("CycleCapacityRemain")),
                "cycle_size": a.get("CycleCapacitySizePrecise", a.get("CycleCapacitySize")),
                "total_remain": a.get("CapacityRemainPrecise", a.get("CapacityRemain")),
                "total_size": a.get("CapacitySizePrecise", a.get("CapacitySize")),
                "cycle_start": str(a.get("CycleStartTime") or ""),
                "cycle_end": str(a.get("CycleEndTime") or ""),
                "unit": str(a.get("CapacityUnit") or "credits"),
            })
        return {"ok": True, "accounts": accounts, "total_dosage": inner.get("TotalDosage")}

    # ---------- 每日签到/领取积分 ----------
    def _billing_headers(self, cred: dict) -> dict:
        h = dict(self._common_headers(cred))
        h.update({
            "Authorization": f"Bearer {cred['accessToken']}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        })
        if cred.get("uid"):
            h["X-User-Id"] = cred["uid"]
        if cred.get("enterpriseId"):
            h["X-Enterprise-Id"] = cred["enterpriseId"]
            h["X-Tenant-Id"] = cred["enterpriseId"]
        if cred.get("domain"):
            h["X-Domain"] = cred["domain"]
        return h

    def checkin_status(self) -> dict:
        cred = self.effective_credential()
        base = CN_ORIGIN if self._base_for(cred.get("domain", "")) == CN_CHAT_BASE else GLOBAL_BASE
        r = httpx.post(f"{base}/v2/billing/meter/checkin-activity-status",
                       headers=self._billing_headers(cred), json={}, timeout=30)
        env = r.json()
        d = env.get("data") if isinstance(env, dict) else None
        if not isinstance(d, dict):
            return {"ok": False, "error": str(env)[:200]}
        return {
            "ok": True,
            "active": bool(d.get("active")),
            "today_checked_in": bool(d.get("today_checked_in")),
            "daily_credit": d.get("daily_credit"),
            "today_credit": d.get("today_credit"),
            "streak_days": d.get("streak_days"),
            "total_credits": d.get("total_credits"),
            "week_checkin_days": d.get("week_checkin_days"),
            "activity_name": str(d.get("activity_name") or ""),
            "theme_name": str(d.get("theme_name") or ""),
            "end_time": str(d.get("end_time") or ""),
            "claim_button_text": str(d.get("claim_button_text") or "立即领取"),
        }

    def daily_checkin(self) -> dict:
        cred = self.effective_credential()
        base = CN_ORIGIN if self._base_for(cred.get("domain", "")) == CN_CHAT_BASE else GLOBAL_BASE
        r = httpx.post(f"{base}/v2/billing/meter/daily-checkin",
                       headers=self._billing_headers(cred), json={}, timeout=30)
        env = r.json()
        if not isinstance(env, dict) or env.get("code") != 0:
            return {"ok": False, "error": str(env)[:200]}
        d = env.get("data") if isinstance(env.get("data"), dict) else {}
        return {"ok": True, "credit": d.get("credit"), "streak_days": d.get("streak_days"),
                "is_streak_day": bool(d.get("is_streak_day"))}

    # ---------- 自动领取（默认开；每自然日至多尝试一次） ----------
    def _checkin_state_path(self) -> Path:
        return self.dir / "wb_checkin_state.json"

    def checkin_state(self) -> dict:
        try:
            d = json.loads(self._checkin_state_path().read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    def set_checkin_auto(self, auto: bool) -> dict:
        st = self.checkin_state()
        st["auto"] = bool(auto)
        try:
            self._checkin_state_path().write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
        return {"ok": True, "auto": bool(auto)}

    def checkin_report(self) -> dict:
        """签到状态 + 自动领取（默认开）。前端获取状态即触发当日首次自动领取。"""
        state = self.checkin_state()
        auto = state.get("auto", True)
        st = self.checkin_status()
        result = {**st, "auto": auto}
        if not (auto and st.get("ok") and st.get("active")) or st.get("today_checked_in"):
            return result
        today = time.strftime("%Y-%m-%d")
        if state.get("last_attempt_date") == today:
            return result
        state["last_attempt_date"] = today
        try:
            self._checkin_state_path().write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
        r = self.daily_checkin()
        if r.get("ok"):
            result["auto_claimed"] = True
            result["claimed_credit"] = r.get("credit")
            result["streak_days"] = r.get("streak_days")
            result["today_checked_in"] = True
        else:
            err = str(r.get("error") or "")
            if "已签到" not in err:  # 上游"今天已签到"不算错误（可能已被其他实例领掉）
                result["auto_claim_error"] = err[:200]
        return result

    # ---------- 聊天（SSE 透传） ----------
    def prepare_body(self, body: dict) -> dict:
        """把 agent 循环发来的 OpenAI 体规范化为上游可接受的形态。"""
        allow = {"model", "messages", "stream", "tools", "tool_choice",
                 "temperature", "top_p", "max_tokens", "stop", "reasoning_effort"}
        out = {k: v for k, v in body.items() if k in allow}
        out["stream"] = True  # 上游拒绝非流式
        msgs = out.get("messages")
        if isinstance(msgs, list):
            for m in msgs:
                if isinstance(m, dict) and m.get("role") == "developer":
                    m["role"] = "system"
        # tool_choice：对象形态上游 400；只保留字符串 auto/none/required
        tc = out.get("tool_choice")
        if isinstance(tc, dict):
            t = str(tc.get("type") or "").lower()
            if t in ("auto", "required"):
                out["tool_choice"] = t
            else:
                out.pop("tool_choice", None)
        elif isinstance(tc, str) and tc.strip().lower() == "none":
            out.pop("tool_choice", None)
            out.pop("tools", None)
        return out

    async def stream_chat(self, body: dict):
        """异步生成上游 SSE 原始行（供垫片路由透传）。

        瞬时故障自动重试：仅当**尚未向调用方下发任何数据**时重试（连接失败/
        响应头阶段被断开/首个数据块前被切断/上游 5xx），最多 STREAM_ATTEMPTS 次。
        一旦已下发数据后中断，则以流内错误事件收尾（不抛异常），避免把垫片的
        ASGI 流直接打断——调用方（agent 循环）能拿到清晰错误而不是协议层乱码。
        """
        try:
            cred = self.effective_credential()
        except Exception as e:
            err = json.dumps({"error": {"message": f"WorkBuddy：{e}"}}, ensure_ascii=False)
            yield f"data: {err}\n\n"
            yield "data: [DONE]\n\n"
            return
        base = self._base_for(cred.get("domain", ""))
        prepared = self.prepare_body(body)
        headers = self.chat_headers(cred)
        timeout = httpx.Timeout(3600, connect=15)  # 长生成不因读超时被切断
        url = f"{base}/v2/chat/completions"

        def _err_line(msg: str) -> str:
            return "data: " + json.dumps({"error": {"message": msg}}, ensure_ascii=False) + "\n\n"

        for attempt in range(1, STREAM_ATTEMPTS + 1):
            emitted = False  # 本轮是否已向调用方下发过数据
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    async with client.stream("POST", url, headers=headers, json=prepared) as r:
                        if r.status_code != 200:
                            detail = (await r.aread()).decode("utf-8", "replace")
                            if r.status_code in RETRY_STATUSES and attempt < STREAM_ATTEMPTS:
                                _log(f"上游 {r.status_code}，{attempt * 0.8:.1f}s 后重试（{attempt + 1}/{STREAM_ATTEMPTS}）")
                                await asyncio.sleep(0.8 * attempt)
                                continue  # 下一轮尝试
                            yield _err_line(f"WorkBuddy 上游 {r.status_code}: {detail[:400]}")
                            yield "data: [DONE]\n\n"
                            return
                        async for line in r.aiter_lines():
                            if line == "":
                                continue
                            if not line.startswith("data:"):
                                continue  # 上游心跳等注释行不传给 agent
                            emitted = True
                            yield line + "\n\n"
                            if line.strip() == "data: [DONE]":
                                return
                        # 上游未发 [DONE]：补一个结束标记
                        yield "data: [DONE]\n\n"
                        return
            except httpx.TransportError as e:
                ename = type(e).__name__
                if not emitted and attempt < STREAM_ATTEMPTS:
                    _log(f"流式中断（{ename}: {e or '上游连接被断开'}），{attempt * 0.8:.1f}s 后重试（{attempt + 1}/{STREAM_ATTEMPTS}）")
                    await asyncio.sleep(0.8 * attempt)
                    continue  # 尚未下发任何数据，静默重试
                _log(f"流式中断且不再重试（emitted={emitted}, attempt={attempt}）：{ename}: {e or ''}")
                yield _err_line(f"WorkBuddy 连接中断（{ename}: {e or '上游提前断开连接'}）")
                yield "data: [DONE]\n\n"
                return

    async def complete(self, *, model: str, messages: list, temperature: float | None = None,
                       max_tokens: int | None = None, reasoning_effort: str | None = None) -> str:
        """非流式助手：聚合一次完整回复（压缩摘要、云端视觉识别用）。"""
        body: dict = {"model": model, "messages": messages, "stream": True}
        if temperature is not None:
            body["temperature"] = temperature
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if reasoning_effort:
            body["reasoning_effort"] = reasoning_effort
        parts: list[str] = []
        async for line in self.stream_chat(body):
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                ev = json.loads(payload)
            except Exception:
                continue
            if isinstance(ev.get("error"), dict):
                raise RuntimeError(str((ev.get("error") or {}).get("message") or "上游错误")[:300])
            delta = ((ev.get("choices") or [{}])[0]).get("delta") or {}
            if delta.get("content"):
                parts.append(delta["content"])
        return "".join(parts).strip()
