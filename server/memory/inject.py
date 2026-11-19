# -*- coding: utf-8 -*-
"""记忆注入管线：把四层记忆组装成注入 system prompt 的文本块。

四层设计（2026-10-11 重构）：
  L1 常驻核心   特别重要、**不含经验**（身份/偏好/行为规则/安全/关系）≈ 系统提示
                全量注入（预算**只做额度提醒、不截断不丢弃**），只读 summary 字段（存冗余、读精简）
  L2 重要记忆   重要但**可按需检索**的可复用经验/技术方案
                按当前输入检索 top-k
  L3 情景记忆   历史项目记录/事件/时效信息（原 L2）→ 检索 top-k
  L4 会话记忆   本会话任务状态（原 L3）→ 取本会话最近 n 条

结构（稳定信息在前、动态信息在后，利于 llama-server 前缀缓存命中）：
  ## 核心记忆 → ## 重要记忆 → ## 情景记忆 → ## 当前会话状态
"""
from __future__ import annotations

from .store import GLOBAL_SCOPE, MemoryStore, normalize_scope


def _scope_badge(e: dict) -> str:
    ws = e.get("workspace") or GLOBAL_SCOPE
    return "" if ws == GLOBAL_SCOPE else "本目录｜"


def _fmt_entry(e: dict, content_limit: int = 180, prefer_summary: bool = True,
               with_detail: bool = False) -> str:
    """格式化一条记忆。

    prefer_summary=True 时优先用 summary（结论），没有才回退正文前 N 字。
    summary 是「结论前置」的载体：注入只读它，全文留在 content 里备查。

    with_detail=True（用于 L2/L3 检索结果）：结论 + 正文片段，兼顾「一眼看懂」和细节。
    """
    tags = f"[{e['tags']}] " if e["tags"] else ""
    summ = (e.get("summary") or "").strip().replace("\n", " ") if prefer_summary else ""
    raw = (e["content"] or "").replace("\n", " ").strip()

    if summ and with_detail and raw and raw[:24] not in summ:
        text = f"{summ} ｜ {raw}"
    elif summ:
        text = summ
    else:
        text = raw

    if len(text) > content_limit:
        text = text[:content_limit] + "…"
    return f"- {_scope_badge(e)}{tags}{e['title']}：{text}"


def _l1_budget_note(meta: dict) -> str:
    """预算提醒：只提醒、不截断。

    预算的用途是「提示模型该精简哪几条」，而不是由代码一刀切丢弃条目 ——
    安全/身份这类关键记忆绝不能因为排在后面对不上账就消失。
    """
    used, budget = int(meta.get("used", 0)), int(meta.get("budget", 0))
    if budget <= 0:
        return ""
    heavy = meta.get("heavy") or []
    heavy_txt = "、".join(f"#{i} {t[:14]}（{c} 字）" for i, t, c in heavy) if heavy else ""
    if meta.get("full"):
        head = (f"> ⚠ 核心记忆预算**已满**：当前 {used} 字 / 预算 {budget} 字，"
                f"超出 {meta.get('over_chars', 0)} 字。条目已**全部注入、未截断**。")
        tail = "建议按上面的用量从大到小精简（合并同类项、或把可检索的经验类内容改放 L2），保持常驻体积可控。"
    elif meta.get("near"):
        head = (f"> 核心记忆预算将满：当前 {used} 字 / 预算 {budget} 字"
                f"（剩 {budget - used} 字）。")
        tail = "再新增常驻条目时请先合并/压缩旧条目，或把经验类内容放 L2 按需检索。"
    else:
        return ""
    if heavy_txt:
        tail = f"当前占用最大的三条：{heavy_txt}。" + tail
    return head + tail


def build_memory_block(store: MemoryStore, *, user_query: str, session_id: str,
                       cfg: dict, meta_out: dict | None = None,
                       workspace: str | None = None,
                       workspace_label: str = "") -> str:
    """返回注入文本；四层都为空时返回空串。

    workspace：当前会话工作目录的记忆作用域键。L1/L2/L3 注入「全局 + 本目录」，
    L4 只注入本会话；另附同目录其他会话的近期任务（跨会话共享目录记忆的直达入口）。

    meta_out（可选）：把 L1 预算用量写回给调用方（{used,budget,full,near,heavy}），
    用于在主界面弹一条「核心记忆预算已满」的提示（不截断，只提醒）。
    """
    parts: list[str] = []
    ws = normalize_scope(workspace)
    scopes = None if ws == GLOBAL_SCOPE else [GLOBAL_SCOPE, ws]
    where = f"（工作目录：{workspace_label}｜含全局）" if ws != GLOBAL_SCOPE and workspace_label else ""

    # L1 常驻核心（全量注入；预算只做额度提醒，不截断、不丢弃）
    l1, meta = store.get_l1_block(int(cfg.get("l1_budget_chars", 1800)), with_meta=True,
                                  scopes=scopes)
    if meta_out is not None:
        meta_out.update(meta)
    if l1:
        parts.append(f"## 核心记忆（长期重要·常驻｜{meta['count']} 条，"
                     f"约 {meta['used']}/{meta['budget']} 字）\n"
                     + "\n".join(_fmt_entry(e, 96) for e in l1))
        note = _l1_budget_note(meta)
        if note:
            parts.append(note)

    q = (user_query or "").strip()
    if q:
        # L2 重要记忆（可复用经验，按需检索）
        # touch=False：**被注入 ≠ 被使用**。注入只累加 inject_count，
        # hit_count 只记录「主动检索/读全文」，否则命中次数会被注入刷成噪声。
        l2 = store.search(q, layer=2, top_k=int(cfg.get("l2_top_k", 4)), touch=False,
                          scopes=scopes)
        if l2:
            parts.append(f"## 重要记忆（按需检索{where}）\n"
                         + "\n".join(_fmt_entry(e, 200, with_detail=True) for e in l2))
            store.record_inject(l2)

        # L3 情景记忆（原 L2，项目/事件）
        l3 = store.search(q, layer=3, top_k=int(cfg.get("l3_top_k", 3)), touch=False,
                          scopes=scopes)
        if l3:
            parts.append(f"## 情景记忆（项目/事件{where}）\n"
                         + "\n".join(_fmt_entry(e, 200, with_detail=True) for e in l3))
            store.record_inject(l3)

    # L4 当前会话状态（原 L3，只取本会话最近 n 条 —— 按时间倒序才叫「最近」）
    l4 = store.get_layer(4, session_id=session_id,
                         limit=int(cfg.get("l4_recent", 3)),
                         order="updated_at DESC",
                         scopes=None if ws == GLOBAL_SCOPE else [ws])
    if l4:
        parts.append("## 当前会话状态\n" + "\n".join(_fmt_entry(e, 120) for e in l4))

    # 同目录其他会话的近期任务：目录记忆跨会话直达（不必等到 L4 归档进 L3）
    if ws != GLOBAL_SCOPE:
        recent = store.recent_workspace_tasks(ws, exclude_session=session_id, limit=2)
        if recent:
            parts.append("## 同目录其他会话的近期任务\n"
                         + "\n".join(_fmt_entry(e, 140) for e in recent))

    return "\n\n".join(parts)
