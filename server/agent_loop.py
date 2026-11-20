# -*- coding: utf-8 -*-
"""Agent 循环：模型 → 工具调用 → 执行 → 回注 → 续跑，直到最终答案。

设计（讨论稿第 6 章）：
  - 工具协议用 llama-server 原生 tool_calls（实测打通，无需自写 XML 解析）；
  - 工具参数用宽容 JSON 链解析（lenient_json）+ 按 schema 宽松强转；
  - 解析彻底失败 → 结构化纠错消息回注，让模型重发；
  - 思考预算逐步收紧（前期放开想，后期逼收束）；
  - 步数护栏 max_steps：0 = 不限（模型自主收尾或用户停止）；>0 时达到上限自动进入"收尾轮"（不带工具，要求总结）。

yield 的事件（dict，main.py 包成 SSE）：
  {"type": "step", "step": n, "final": bool}
  {"type": "reasoning", "text": ...} / {"type": "content", "text": ...}
  {"type": "tool_call", "id", "name", "arguments"}
  {"type": "tool_result", "id", "name", "ok", "seconds", "output"}
  {"type": "notice", "text", "retry"}  ← 自动恢复提示（如参数截断后转分块重试）
  {"type": "error", "message"} / {"type": "done", "steps"}
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import AsyncIterator

import httpx

from lenient_json import parse_lenient, coerce_types, is_structurally_closed
from tools import TOOL_SPECS, SCHEMA_HINTS, execute_tool

_BUDGET_MSG = "思考预算已用尽。请基于当前分析立即给出最终答案或下一步动作。"

# 工具调用参数被截断（多为单次写入内容超过 max_tokens）时 llama-server 的报错标记
_TOOL_PARSE_FAIL_MARK = "Failed to parse tool call arguments as JSON"
_MAX_PARSE_RETRIES = 3
_RETRY_MAX_TOKENS = 16384  # 截断重试时自动提升的输出上限（与参数面板上限一致；32K 上下文下单轮输出的实际拉满档）

_TRUNCATED_ARGS_MSG = (
    "上一次工具调用因参数内容过长在生成中途被截断，JSON 不完整未能送达。"
    "请改用分块写入的方式重新完成："
    "第一步用 file_create 只写入文件开头约 1000 字符；"
    "剩余内容用 file_edit（append=true）分多次追加，每次追加约 1000 字符；"
    "全部写完后用文件工具检查一遍。现在请从第一步开始。"
)
_TRUNCATED_ARGS_MSG_BOOSTED = (
    "上一次工具调用因内容过长在生成中途被截断，JSON 不完整未能送达。"
    "已为你增大输出上限，请把同一任务的内容完整输出一遍；"
    "如果仍被截断，再改用分块写入（file_create 写开头，file_edit 设 append=true 逐段续写，每段约 1000 字符）。"
)
_TRUNCATED_FAIL_MSG = (
    "工具调用参数多次解析失败（模型单次输出过长被截断）。"
    "请在参数面板把「最大输出」调大后重新发送，或把任务拆小一些再试。"
)

_EMPTY_NUDGE_MSG = (
    "你上一轮没有产生任何回复，也没有执行动作。请继续："
    "直接给出最终回答，或调用工具完成下一步，然后把结果告诉用户。"
)
_MAX_EMPTY_RETRIES = 2

# 网络层瞬时故障重试：只在本轮尚未向调用方下发任何内容时重试（重发安全）
_MAX_NET_RETRIES = 4
_UNATTENDED_MAX_NET_RETRIES = 6
_NET_RETRY_STATUSES = {408, 425, 429, 500, 502, 503, 504}

# 无人监管：不等待人工确认，自行决断继续
_MAX_AUTONOMOUS_NUDGES = 1
_ASK_WAIT_RE = re.compile(r"(需要你|请你|等你|等待你|告诉我|确认后|回复我|你选|要不要)")
_AUTONOMOUS_NUDGE_MSG = (
    "【无人监管模式】用户当前不在场，不要等待确认或回复。"
    "请自行选择最稳妥、可回退的方案继续执行，做完并验证后直接输出最终汇报"
    "（写清你的假设、实际完成项与未完成项）。"
)
_IMPROVE_NUDGE_MSG = (
    "【无人监管】无人值守窗口还没结束，任务不能就此收尾。"
    "请继续检查当前结果，找出可验证、可改进、可完善的点并实际推进"
    "（测试、边界情况、性能、稳定性、文档、清理等）；不要等待用户确认。"
    "每轮先自检，再继续改进，直到窗口结束。"
)
_DEADLINE_WRAP_MSG = (
    "【无人监管】已到约定的无人值守时间。请立即停止新工作，"
    "基于现有结果给出最终汇报：实际完成项、验证结果、你的假设与未完成项。不要再调用工具。"
)


def _retry_delay(attempt: int, retry_after: str | None = None) -> float:
    """指数退避 + 抖动；优先遵守上游 Retry-After（上限 30 秒）。"""
    if retry_after:
        try:
            return max(0.0, min(float(str(retry_after).strip()), 30.0))
        except (TypeError, ValueError):
            pass
    import random as _random
    return min(30.0, 1.5 * (2 ** (attempt - 1))) + _random.uniform(0, 0.4)


def _looks_transient(msg: str) -> bool:
    low = (msg or "").lower()
    return any(k in low for k in (
        "timeout", "timed out", "connection", "connect", "reset", "aborted",
        "temporarily", "unavailable", "overloaded", "rate limit", "too many requests",
        "econnreset", "etimedout", "econnrefused", "socket", "broken pipe", "502", "503", "504",
    ))

# "编造完成"防线：回复里出现成果类表述，但本轮还没有任何工具动作
_FABRICATION_NUDGE_MSG = (
    "刚才的回复里提到了「已完成 / 已创建 / 已写入 / 已运行」等成果，"
    "但本轮还没有实际执行过任何工具调用。若这是需要动手的任务，"
    "请现在实际调用工具完成它；若只是文字说明，请直接用一句话回答。"
)
_FABRICATION_RE = re.compile(r"已(经)?(创建|写入|运行|执行|完成|保存|修改|删除|更新|添加)")
_MAX_FABRICATE_RETRIES = 2


def _parse_fail_exhausted() -> dict:
    return {"type": "error", "message": _TRUNCATED_FAIL_MSG}


def _speed_stats(tokens: int, ms: float) -> dict | None:
    """本轮生成速度统计（来自 llama.cpp 流末 timings）。"""
    if tokens > 0 and ms > 0:
        return {"tokens": tokens, "tps": round(tokens / (ms / 1000.0), 1)}
    return None


def budget_for_step(base: int | None, step: int, max_steps: int) -> int | None:
    """预算递减：从 base 逐步收紧到 30%（下限 160；-1/0 原样透传）。

    max_steps <= 0（不限步数）时以 30 步为软收束窗口，避免预算在第 1 步就跳崖。
    """
    if base is None or base < 0 or base == 0:
        return base
    horizon = max_steps if max_steps and max_steps > 0 else 30
    frac = 1.0 - (min(step, horizon) / horizon) * 0.7
    return max(160, int(base * frac))


def _merge_tool_call_fragments(calls: dict, deltas: list) -> None:
    for tc in deltas:
        i = int(tc.get("index", 0))
        slot = calls.setdefault(i, {"id": None, "name": "", "arguments": ""})
        if tc.get("id"):
            slot["id"] = tc["id"]
        fn = tc.get("function") or {}
        if fn.get("name"):
            slot["name"] = fn["name"]
        if fn.get("arguments"):
            slot["arguments"] += fn["arguments"]


def _tool_brief(name: str, args: dict) -> str:
    """工具调用的一句话摘要（写 L3 记忆用：如 'file_create snake_game.py'）。"""
    for k in ("path", "command", "query", "title", "target", "source"):
        v = args.get(k)
        if v:
            s = str(v).replace("\n", " ").strip()
            return f"{name} {s[:60]}"
    return name


def _assemble_tool_calls(calls: dict, step: int) -> tuple[list[dict], dict, bool]:
    """组装工具调用：(OpenAI 格式列表, {id: (解析后参数|None, 原文)}, 是否疑似截断)。

    参数解析三档：
      - 严格合法（生成完整）→ 直接使用；
      - 严格失败但结构闭合（字面换行/尾逗号等小毛病）→ 宽容修复后使用；
      - 严格失败且结构不闭合（生成被截断）→ truncated=True，整步交调用方重发。
    回填进对话的参数一律经 json.dumps 净化：llama-server 会严格校验历史消息里
    每个 tool_call 的 arguments（func_args_not_string），原文若非法会让后续
    所有请求在模板阶段瞬间 500。
    """
    tc_list: list[dict] = []
    parsed: dict = {}
    truncated = False
    for i in sorted(calls):
        c = calls[i]
        name = c["name"]
        if not name:
            continue
        raw = c["arguments"] or "{}"
        args = None
        how = None
        try:
            args = json.loads(raw)
            how = "strict"
        except Exception:
            args, how = parse_lenient(raw)
        if isinstance(args, dict):
            args, _notes = coerce_types(args, SCHEMA_HINTS.get(name))
        balanced = is_structurally_closed(raw)
        if not isinstance(args, dict):
            args = None
            if not balanced:  # 结构不闭合且解析不出来：几乎必然是生成截断
                truncated = True
                break
        elif how != "strict" and not balanced:
            truncated = True
            break
        safe = "{}"
        if args is not None:
            try:
                safe = json.dumps(args, ensure_ascii=False, allow_nan=False)
            except (TypeError, ValueError):
                args = None
        cid = c.get("id") or f"call_{step}_{i}"
        tc_list.append(
            {
                "id": cid,
                "type": "function",
                "function": {"name": name, "arguments": safe},
            }
        )
        parsed[cid] = (args, raw)
    return tc_list, parsed, truncated


async def run_agent(
    *,
    client: httpx.AsyncClient,
    chat_url: str,
    messages: list[dict],
    sampling: dict,
    thinking: bool,
    budget: int | None,
    tools_enabled: bool,
    workdir: str,
    max_steps: int,
    agent_cfg: dict,
    model: str | None = None,
    reasoning_effort: str | None = None,
) -> AsyncIterator[dict]:
    convo = [dict(m) for m in messages]
    # 剩余的本地垫片只有 api 供应商代理，它是标准 OpenAI 流，不算旧式 local shim
    is_local_shim = False
    model_label = "模型"
    step = 0
    final_round = False
    parse_retries = 0  # 工具参数截断的自动重试次数（不消耗步数）
    mt_boost = 0       # 截断重试时自动提升的 max_tokens（0 = 未提升）
    empty_retries = 0  # 模型空转（无回复、无动作）的自动提醒次数（不消耗步数）
    budget_off = False # 空转后关闭思考预算限制，让模型把计划想完（防强制切断诱发漂移）
    executed_ok: set = set()  # 已成功执行过的 (工具名, 参数JSON)，用于拦截完全重复的调用
    tool_attempted = False    # 本轮是否发起过工具调用（编造完成防线的判据）
    fab_retries = 0    # "编造完成"提醒次数（不消耗步数）
    tot_pred = 0       # 本轮累计生成 token 数（所有步合计）
    tot_ms = 0.0       # 本轮累计生成耗时（ms，timings 口径）

    # 无人监管档位：提高各类自动恢复上限，不因小毛病停下等人
    unattended = bool(agent_cfg.get("_unattended"))
    try:
        unattended_minutes = int(agent_cfg.get("_unattended_minutes") or 0)
    except (TypeError, ValueError):
        unattended_minutes = 0
    try:
        unattended_deadline = float(agent_cfg.get("_unattended_deadline") or 0)
    except (TypeError, ValueError):
        unattended_deadline = 0.0
    deadline_wrapped = False   # 到点收尾轮是否已触发
    improve_nudges = 0         # 提前收尾后「继续优化改进」的催办次数

    def _deadline_reached() -> bool:
        return unattended and unattended_deadline > 0 and time.time() >= unattended_deadline

    max_net_retries = _UNATTENDED_MAX_NET_RETRIES if unattended else _MAX_NET_RETRIES
    max_parse_retries = _MAX_PARSE_RETRIES * 2 if unattended else _MAX_PARSE_RETRIES
    max_empty_retries = _MAX_EMPTY_RETRIES * 2 if unattended else _MAX_EMPTY_RETRIES
    max_fabricate_retries = _MAX_FABRICATE_RETRIES + 1 if unattended else _MAX_FABRICATE_RETRIES
    auto_nudges = 0    # 无人监管：结尾出现「等你确认」类表述时的自动续跑次数

    while True:
        # 无人监管到点：强制进入收尾轮（不再调工具），让模型先汇总再结束
        if _deadline_reached() and not deadline_wrapped:
            deadline_wrapped = True
            if not final_round:
                final_round = True
                convo.append({"role": "user", "content": _DEADLINE_WRAP_MSG})
                yield {"type": "notice", "text": "无人监管：已到约定时间，正在收尾汇报"}

        yield {"type": "step", "step": step, "final": final_round}

        body: dict = {
            "messages": convo,
            "stream": True,
            **sampling,
            "chat_template_kwargs": {"enable_thinking": bool(thinking)},
        }
        if model:
            body["model"] = model  # 云端模型（垫片需要 model 字段选择上游模型；llama-server 忽略）
        if reasoning_effort:
            body["reasoning_effort"] = reasoning_effort  # 云端思考档位（off/low/medium/high/xhigh/max）
        if mt_boost:
            try:
                cur_mt = int(body.get("max_tokens") or 0)
            except (TypeError, ValueError):
                cur_mt = 0
            if cur_mt < mt_boost:
                body["max_tokens"] = mt_boost
        if thinking and not budget_off:
            sb = budget_for_step(budget, step, max_steps)
            if sb is not None and sb != 0:
                body["reasoning_budget_tokens"] = sb
                body["reasoning_budget_message"] = _BUDGET_MSG
            elif sb == 0:
                body["reasoning_budget_tokens"] = 0
        if tools_enabled and not final_round:
            _specs = TOOL_SPECS
            # 无人监管：把 ask_user 摘掉。留着它，模型仍会在岔路口弹一张没人点的卡片，
            # 白等一轮超时；直接没有这个工具，它才会自己决策。
            if agent_cfg.get("_no_ask"):
                _specs = [t for t in TOOL_SPECS
                          if (t.get("function") or {}).get("name") != "ask_user"]
            body["tools"] = _specs
            body["tool_choice"] = "auto"
        reasoning_len = 0
        content = ""
        calls: dict = {}
        parse_fail = False  # 本轮因参数截断未送达

        # ---- 网络层：瞬时故障自动重连（仅在尚未下发任何内容时；重发安全） ----
        net_retries = 0
        while True:
            emitted = False   # 本轮已向调用方下发过内容（正文/思考/工具事件）
            retry_stream = False
            try:
                async with client.stream("POST", chat_url, json=body) as r:
                    if r.status_code != 200:
                        detail = (await r.aread()).decode("utf-8", "replace")
                        if _TOOL_PARSE_FAIL_MARK in detail:
                            parse_fail = True
                        elif r.status_code in _NET_RETRY_STATUSES and net_retries < max_net_retries:
                            net_retries += 1
                            delay = _retry_delay(net_retries, r.headers.get("retry-after"))
                            yield {"type": "notice", "retry": net_retries,
                                   "text": f"{model_label} 返回 {r.status_code}，{delay:.1f} 秒后自动重连"
                                           f"（{net_retries}/{max_net_retries}）"}
                            await asyncio.sleep(delay)
                            retry_stream = True
                        else:
                            yield {"type": "error", "message": f"{model_label} {r.status_code}: {detail[:300]}"}
                            return
                    else:
                        async for line in r.aiter_lines():
                            if not line.startswith("data: "):
                                continue
                            payload = line[6:].strip()
                            if payload == "[DONE]":
                                break
                            try:
                                obj = json.loads(payload)
                            except Exception:
                                continue
                            if obj.get("timings"):
                                try:
                                    tot_pred += int(obj["timings"].get("predicted_n") or 0)
                                    tot_ms += float(obj["timings"].get("predicted_ms") or 0)
                                except (TypeError, ValueError, AttributeError):
                                    pass
                            if obj.get("error"):
                                emsg = str((obj.get("error") or {}).get("message") or obj.get("error") or "")
                                if _TOOL_PARSE_FAIL_MARK in emsg:
                                    parse_fail = True
                                    break
                                # 垫片内部已做过上游重试，这里不再叠加以免重试次数失控
                                if (not emitted and not is_local_shim
                                        and _looks_transient(emsg) and net_retries < max_net_retries):
                                    net_retries += 1
                                    delay = _retry_delay(net_retries)
                                    yield {"type": "notice", "retry": net_retries,
                                           "text": f"{model_label} 网络异常，{delay:.1f} 秒后自动重连"
                                                   f"（{net_retries}/{max_net_retries}）"}
                                    await asyncio.sleep(delay)
                                    retry_stream = True
                                    break
                                yield {"type": "error", "message": f"{model_label}: {emsg[:300]}"}
                                return
                            delta = (obj.get("choices") or [{}])[0].get("delta") or {}
                            if delta.get("reasoning_content"):
                                emitted = True
                                reasoning_len += len(delta["reasoning_content"])
                                yield {"type": "reasoning", "text": delta["reasoning_content"]}
                            if delta.get("content"):
                                emitted = True
                                content += delta["content"]
                                yield {"type": "content", "text": delta["content"]}
                            if delta.get("tool_calls"):
                                emitted = True
                                _merge_tool_call_fragments(calls, delta["tool_calls"])
            except httpx.TransportError as e:
                # 连接失败 / 超时 / 上游提前断开（httpx 的 TimeoutException 也属此类）
                if not emitted and net_retries < max_net_retries:
                    net_retries += 1
                    delay = _retry_delay(net_retries)
                    yield {"type": "notice", "retry": net_retries,
                           "text": f"网络连接中断（{type(e).__name__}），{delay:.1f} 秒后自动重连"
                                   f"（{net_retries}/{max_net_retries}）"}
                    await asyncio.sleep(delay)
                    retry_stream = True
                else:
                    hint = "；本轮已产生内容，不能再重连以防重复输出" if emitted else ""
                    yield {"type": "error",
                           "message": f"网络中断（{type(e).__name__}）：{e}{hint}"}
                    return
            except Exception as e:
                yield {"type": "error", "message": f"{type(e).__name__}: {e}"}
                return

            if retry_stream:
                # 尚未下发任何内容，清空半成品后重发
                content, reasoning_len, calls = "", 0, {}
                parse_fail = False
                continue
            break

        # ---- 组装工具调用（含"截断嫌疑"检测：严格失败且结构不闭合 → 不执行，走重试） ----
        retry_needed = parse_fail
        tc_list: list[dict] = []
        parsed: dict = {}
        if not retry_needed:
            if not calls:
                # 编造完成防线：说了"已创建/已运行"但本轮从未发起过工具调用 → 提醒核实
                if ((content or "").strip() and not tool_attempted
                        and _FABRICATION_RE.search(content)
                        and fab_retries < max_fabricate_retries):
                    fab_retries += 1
                    convo.append({"role": "user", "content": _FABRICATION_NUDGE_MSG})
                    yield {"type": "notice", "retry": fab_retries,
                           "text": f"回复提到成果但未实际执行，已自动提醒核实（{fab_retries}/{max_fabricate_retries}）"}
                    continue
                # 空转防线：既无回复也无动作 → 解除思考预算限制 + 提醒继续，而不是静默收场
                if not (content or "").strip() and empty_retries < max_empty_retries:
                    empty_retries += 1
                    budget_off = True
                    convo.append({"role": "user", "content": _EMPTY_NUDGE_MSG})
                    yield {"type": "notice", "retry": empty_retries,
                           "text": f"模型未产生输出，已解除思考限制并提醒继续（{empty_retries}/{max_empty_retries}）"}
                    continue
                # 无人监管收尾自检：结尾还在等用户确认 → 自动续跑一次，逼模型自行决断
                if (unattended and tool_attempted and auto_nudges < _MAX_AUTONOMOUS_NUDGES
                        and (content or "").strip() and _ASK_WAIT_RE.search(content[-400:])):
                    auto_nudges += 1
                    convo.append({"role": "user", "content": _AUTONOMOUS_NUDGE_MSG})
                    yield {"type": "notice", "retry": auto_nudges,
                           "text": "无人监管：结尾在等待用户确认，已自动要求自行决断继续（1/1）"}
                    continue
                # 无人监管提前收尾：窗口没结束（最后一档=无限）就催它继续检查、优化、改进
                if unattended and not final_round and tool_attempted and not _deadline_reached():
                    improve_nudges += 1
                    left_txt = ""
                    if unattended_deadline > 0:
                        left = max(1, int((unattended_deadline - time.time() + 59) // 60))
                        left_txt = f"（剩约 {left} 分钟）"
                    convo.append({"role": "user", "content": _IMPROVE_NUDGE_MSG})
                    yield {"type": "notice", "retry": improve_nudges,
                           "text": f"无人监管：任务提前收尾，已要求继续检查并优化改进{left_txt}"}
                    continue
                yield {"type": "done", "steps": step, "reasoning_chars": reasoning_len,
                       "stats": _speed_stats(tot_pred, tot_ms)}
                return
            tc_list, parsed, truncated = _assemble_tool_calls(calls, step)
            if truncated:
                retry_needed = True
            elif not tc_list:
                # 流里出现过调用，但一个都没组装成功（如缺名称）→ 等同空步处理
                if empty_retries < max_empty_retries:
                    empty_retries += 1
                    budget_off = True
                    convo.append({"role": "user", "content": _EMPTY_NUDGE_MSG})
                    yield {"type": "notice", "retry": empty_retries,
                           "text": f"工具调用未能送达，已解除思考限制并提醒重发（{empty_retries}/{max_empty_retries}）"}
                    continue
                yield {"type": "done", "steps": step, "reasoning_chars": reasoning_len,
                       "stats": _speed_stats(tot_pred, tot_ms)}
                return

        # ---- 参数被截断/未送达：提升输出上限 + 回注纠错消息，原地重试（不消耗步数） ----
        if retry_needed:
            if parse_retries >= max_parse_retries:
                yield _parse_fail_exhausted()
                return
            parse_retries += 1
            notice = f"参数内容被截断，已自动转为分块写入重试（{parse_retries}/{max_parse_retries}）"
            fix_msg = _TRUNCATED_ARGS_MSG
            if not mt_boost:
                try:
                    cur_mt = int(sampling.get("max_tokens") or 0)
                except (TypeError, ValueError):
                    cur_mt = 0
                if cur_mt < _RETRY_MAX_TOKENS:
                    mt_boost = _RETRY_MAX_TOKENS
                    notice = f"参数内容被截断，已提升输出上限至 {mt_boost} 重试（{parse_retries}/{max_parse_retries}）"
                    fix_msg = _TRUNCATED_ARGS_MSG_BOOSTED
            convo.append({"role": "user", "content": fix_msg})
            yield {"type": "notice", "retry": parse_retries, "text": notice}
            continue

        convo.append({"role": "assistant", "content": content or "", "tool_calls": tc_list})

        tool_attempted = True
        for tc in tc_list:
            name = tc["function"]["name"]
            args, raw = parsed[tc["id"]]
            yield {"type": "tool_call", "id": tc["id"], "name": name, "arguments": raw}

            if args is None:
                msg = (f"错误：工具参数 JSON 无法解析。你给出的原文是：{raw[:300]}\n"
                       f"请重新输出该工具调用，参数必须是合法的 JSON 对象。")
                convo.append({"role": "tool", "tool_call_id": tc["id"], "content": msg})
                yield {"type": "tool_result", "id": tc["id"], "name": name, "ok": False,
                       "seconds": 0, "output": msg, "brief": f"{name}（参数解析失败）"}
                continue

            # 循环卫生：完全相同的调用若此前已成功执行过，本次跳过（防小模型重复螺旋；
            # 失败的调用不在拦截范围——模型修好前置条件后再试同一命令是合法路径）
            sig = (name, tc["function"]["arguments"])
            if sig in executed_ok:
                msg = ("注意：这个调用与之前完全相同的调用已经成功执行过，本次已自动跳过。"
                       "请查看上面的执行结果，直接推进下一步；如确需重跑，请调整参数或换一种做法。")
                convo.append({"role": "tool", "tool_call_id": tc["id"], "content": msg})
                yield {"type": "tool_result", "id": tc["id"], "name": name, "ok": False,
                       "seconds": 0, "output": msg, "brief": f"{name}（重复调用已跳过）"}
                yield {"type": "notice", "retry": 0, "text": "检测到与已成功执行过的完全重复调用，已跳过并提示模型继续"}
                continue

            t0 = time.time()
            output, ok = await asyncio.to_thread(execute_tool, name, args, workdir, agent_cfg)
            seconds = round(time.time() - t0, 2)
            if ok:
                executed_ok.add(sig)
            convo.append({"role": "tool", "tool_call_id": tc["id"], "content": output})
            yield {"type": "tool_result", "id": tc["id"], "name": name, "ok": ok,
                   "seconds": seconds, "output": output, "brief": _tool_brief(name, args)}

        empty_retries = 0  # 已执行过动作，空转计数归零
        step += 1
        if max_steps and max_steps > 0 and step >= max_steps:
            final_round = True
            convo.append(
                {
                    "role": "user",
                    "content": "已达到工具调用步数上限。请立即基于现有信息与已完成的操作，"
                               "用中文给出最终总结（不要再调用工具）。",
                }
            )
