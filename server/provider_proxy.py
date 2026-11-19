"""OpenAI 兼容供应商代理（把设置页填的 base_url + 密钥真正接进对话链路）。

设计要点：
- agent 循环内部只认一个 chat_url，这里做垫片，SSE 直通，状态码原样返回，
  错误由 agent_loop 统一翻译成可读提示（不在这里吞掉）。
- 必须在转发前**清洗 llama.cpp 专用字段**：chat_template_kwargs / reasoning_budget_*
  这些是本地推理用的，真 OpenAI 兼容端点遇到非标字段会直接 400。
- 模型 key 形如 `api:<providerId>/<modelName>`，pid 与模型名都不许含 `/`。
"""
from __future__ import annotations

import httpx
import settings_api
import ephemeral
from secretbox import unprotect

# 这些字段是本地 llama.cpp 的私有参数，转发给云端供应商会造成 400
_LOCAL_ONLY_FIELDS = (
    "chat_template_kwargs",
    "reasoning_budget_tokens",
    "reasoning_budget_message",
    "claw_unattended_minutes",
    "claw_unattended_deadline",
)


def parse_key(model_key: str) -> tuple[str, str] | None:
    """`api:p_ab12cd34/gpt-4o-mini` → (pid, model)；不是供应商 key 返回 None。"""
    s = str(model_key or "")
    if not s.startswith("api:"):
        return None
    rest = s[4:]
    if "/" not in rest:
        return None
    pid, model = rest.split("/", 1)
    pid, model = pid.strip(), model.strip()
    if not pid or not model:
        return None
    return pid, model


def key_for(pid: str, model: str) -> str:
    return f"api:{pid}/{model}"


def api_key_of(p: dict) -> str:
    """取密钥：mem: 前缀是内存态（临时预览用），其余是 DPAPI 密文。"""
    raw = str(p.get("_apiKey") or "")
    if raw.startswith("mem:"):
        return raw[4:]
    return unprotect(raw)


def find_provider(pid: str) -> dict | None:
    # 临时预览的虚拟供应商：凭据只在内存里，不落盘、不进设置
    if str(pid) == ephemeral.EPHEMERAL_ID:
        c = ephemeral.get_creds()
        if not c.get("apiKey"):
            return None
        return {
            "id": ephemeral.EPHEMERAL_ID,
            "name": "临时连接（不保存）",
            "baseUrl": c.get("baseUrl") or "",
            "model": c.get("model") or "",
            "_apiKey": "mem:" + c.get("apiKey", ""),
            "timeout": 120,
        }
    for p in (settings_api.load_settings().get("providers") or []):
        if str(p.get("id")) == str(pid):
            return p
    return None


def endpoints(base: str) -> tuple[str, str]:
    """返回 (chat_url, models_url)。base 允许填到根、/v1 或直接到 chat/completions。"""
    b = (base or "").strip().rstrip("/")
    if not b:
        raise ValueError("供应商缺少 Base URL")
    if b.endswith("/chat/completions"):
        return b, b[: -len("/chat/completions")] + "/models"
    if b.endswith("/v1"):
        return b + "/chat/completions", b + "/models"
    return b + "/v1/chat/completions", b + "/v1/models"


def _clean(payload: dict, model: str) -> dict:
    body = {k: v for k, v in (payload or {}).items() if k not in _LOCAL_ONLY_FIELDS}
    if model:
        body["model"] = model
    # 供应商不认空 tools，留着会让部分实现直接报错
    if not body.get("tools"):
        body.pop("tools", None)
        body.pop("tool_choice", None)
    return body


async def stream(pid: str, model: str, payload: dict):
    """透传上游 SSE。异常在调用方（main.py 的路由）转成 HTTP 错误。"""
    p = find_provider(pid)
    if not p:
        raise LookupError(f"供应商 {pid} 不存在，可能已被删除")
    chat_url, _ = endpoints(p.get("baseUrl") or "")
    key = api_key_of(p)
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    timeout = httpx.Timeout(None, connect=float(p.get("timeout") or 20))
    body = _clean(payload, model or str(p.get("model") or ""))

    client = httpx.AsyncClient(timeout=timeout)
    try:
        req = client.build_request("POST", chat_url, json=body, headers=headers)
        resp = await client.send(req, stream=True)
    except Exception:
        await client.aclose()
        raise

    async def _body():
        try:
            async for chunk in resp.aiter_bytes():
                yield chunk
        finally:
            await resp.aclose()
            await client.aclose()

    return resp, _body()


async def probe(pid: str) -> dict:
    """探活：拉一次 /models，用于设置页「测试连接」。"""
    p = find_provider(pid)
    if not p:
        return {"ok": False, "error": "供应商不存在"}
    _, models_url = endpoints(p.get("baseUrl") or "")
    key = api_key_of(p)
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    try:
        async with httpx.AsyncClient(timeout=float(p.get("timeout") or 15)) as c:
            r = await c.get(models_url, headers=headers)
            if r.status_code >= 400:
                return {"ok": False, "status": r.status_code,
                        "error": f"HTTP {r.status_code}: {r.text[:200]}"}
            data = r.json() if r.text else {}
            items = data.get("data") or data.get("models") or []
            names = [str(m.get("id") or m.get("name") or "") for m in items if isinstance(m, dict)]
            return {"ok": True, "models": [n for n in names if n][:80]}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:200]}


async def complete(pid: str, model: str, payload: dict, timeout: float = 180) -> str:
    """非流式一次性调用（压缩/摘要这类小任务用），返回正文。"""
    p = find_provider(pid)
    if not p:
        raise LookupError(f"供应商 {pid} 不存在")
    chat_url, _ = endpoints(p.get("baseUrl") or "")
    key = api_key_of(p)
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    body = _clean(payload, model or str(p.get("model") or ""))
    body["stream"] = False
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=20)) as c:
        r = await c.post(chat_url, json=body, headers=headers)
        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
        data = r.json()
    msg = ((data.get("choices") or [{}])[0] or {}).get("message") or {}
    return str(msg.get("content") or "").strip()
