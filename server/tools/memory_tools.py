# -*- coding: utf-8 -*-
"""记忆工具：memory_write / memory_search / memory_forget。

通过 cfg 携带的 _project_root / _memory 定位记忆库（由 main.py 组装传入）。
"""
from __future__ import annotations

from pathlib import Path

MEMORY_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "memory_write",
            "description": "记住一条信息，跨会话长期有效。四层："
                           "layer=1 常驻核心（特别重要、不含经验：身份/偏好/行为规则/安全/关系，会每次注入）；"
                           "layer=2 重要记忆（可复用经验/技术方案/技能入口，按需检索）；"
                           "layer=3 情景记忆（项目记录/具体事件/时效信息，按需检索）。"
                           "content 的第一句必须自成结论（注入时只读前 180 字）。"
                           "scope=auto（默认）：layer=1 写全局，layer=2/3 写当前工作目录，layer=4 写当前会话；"
                           "重要度≥4 的目录记忆会自动提取一份到全局记忆。",
            "parameters": {
                "type": "object",
                "properties": {
                    "layer": {"type": "integer", "description": "1=常驻核心；2=重要记忆(经验)；3=情景记忆(项目/时效)"},
                    "title": {"type": "string", "description": "简短标题"},
                    "content": {"type": "string", "description": "要记住的内容；第一句必须是结论"},
                    "summary": {"type": "string", "description": "结论，建议 ≤40 字（layer=1 必填，注入只读它）"},
                    "tags": {"type": "string", "description": "标签，逗号分隔（可选）"},
                    "importance": {"type": "integer", "description": "重要度 1-5（可选，默认 3；规则类经验可给 4，会自动提取到全局）"},
                    "scope": {"type": "string", "description": "auto（默认）/ workspace（仅当前工作目录）/ global（所有工作目录可见）"},
                },
                "required": ["layer", "title", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_search",
            "description": "回忆用户偏好、历史项目与过往保存的知识时使用；按关键词跨层搜索，或指定 layer 查看某一层。"
                           "也可以用 tag 按标签选一批记忆（只给 tag 不给 query 就是「列出这个标签下的全部记忆」，"
                           "标签数量不限、支持 # 前缀与子串匹配）——同一主题的旧记录没记在标题里时，用标签找比用词猜更准。"
                           "query 请使用中文关键词（记忆库以中文存储）。"
                           "默认检索「全局 + 当前工作目录」；scope=all 可看全部目录。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "中文关键词（与 tag 可同时给：先按标签收敛、再按词排序）"},
                    "tag": {"type": "string", "description": "按标签筛选（如 ClawAgent / 踩坑 / 用户偏好；可带 #）。只给 tag 即可列出该标签下的记忆"},
                    "layer": {"type": "integer", "description": "限定层级 1/2/3（可选，默认跨层）"},
                    "top_k": {"type": "integer", "description": "返回条数（可选，默认 5）"},
                    "scope": {"type": "string", "description": "auto（默认：全局+当前目录）/ workspace / global / all"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_forget",
            "description": "删除记忆（破坏性操作，仅在用户明确要求或信息确实错误时使用）。三种方式：按 id 删一条；"
                           "按 query 删除所有标题/标签/内容含该关键词的条目；"
                           "按 layer 清空某一整层（1=常驻核心 2=重要记忆 3=情景记忆 4=会话记忆）。"
                           "建议先用 memory_search 确认目标再删。",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "description": "要删除的记忆 id（单条）"},
                    "query": {"type": "string", "description": "删除所有包含该关键词的记忆（批量）"},
                    "layer": {"type": "integer", "description": "清空某一整层：1=常驻核心 2=重要记忆 3=情景记忆 4=会话记忆（批量清空）"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_tags",
            "description": "给一条记忆设置/追加/移除标签，或查看标签总览。标签是检索加分项（命中 +2 分），"
                           "数量不限（只在过多时给一句提醒，绝不截断）。"
                           "action=set 直接覆盖；add 追加（自动去重）；remove 移除指定标签；"
                           "action=overview 时返回「哪些标签被多少条记忆使用」，用于对齐用词、避免同义标签分裂。",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "set 覆盖 / add 追加 / remove 移除 / overview 总览（默认 add）"},
                    "id": {"type": "integer", "description": "记忆 id（overview 时可不填）"},
                    "tags": {"type": "string", "description": "标签，逗号分隔（数量不限；支持中英文逗号、顿号、空格分隔）"},
                    "top": {"type": "integer", "description": "overview 返回条数（默认 40）"},
                    "scope": {"type": "string", "description": "overview 的作用域：auto（默认）/ workspace / global / all"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_promote",
            "description": "把一条「工作目录记忆」提取到全局记忆（所有工作目录都可检索/注入）。"
                           "用于用户明确说「这个要记住，以后在哪都适用」的偏好、规则、通用经验。"
                           "只是复制并合并，不会删除原目录记忆。",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "description": "要提升的记忆 id（memory_search 结果里的 #id）"},
                },
                "required": ["id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_get",
            "description": "读取某条记忆的**完整正文**。对话里只注入 summary（结论），需要细节/原文时用这个按 id 取全文。",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "description": "记忆 id（memory_search 结果里的 #id）"},
                },
                "required": ["id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_stats",
            "description": "查看记忆库现状：四层各多少条/多少字、缺结论的条数、查重命中次数、归档情况。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_forget_candidates",
            "description": "【遗忘机制·只提议】算出该被降级的记忆候选（L2→L3、L3→归档），返回评分与理由，"
                           "但**不会真的改任何数据**。判据：层内得分垫底（相对）且长期未被真正使用（绝对，默认 90 天）。"
                           "「被注入」不算使用 —— 注入高而使用为 0 的会被重点标出。"
                           "请把候选念给用户听，得到明确同意后再调 memory_demote 执行。",
            "parameters": {
                "type": "object",
                "properties": {
                    "layer": {"type": "integer", "description": "只看某一层（2 或 3，可选；默认两层都看）"},
                    "min_age_days": {"type": "integer", "description": "绝对门槛天数（可选，默认 90）"},
                    "limit": {"type": "integer", "description": "最多返回几条（可选，默认 20）"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_demote",
            "description": "【破坏性】执行一次降级：L2 → L3（降为情景记忆，仍可检索）或 L3 → 归档（不再注入，仍可检索）。"
                           "只降一级；L1（只精不降）与 L4（会话暂存）不受理。"
                           "只在用户看了 memory_forget_candidates 的结果并明确同意后用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "description": "要降级的记忆 id"},
                    "ids": {"type": "string", "description": "批量：逗号分隔的多个 id（可选）"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_pin",
            "description": "锁定/解锁一条记忆。锁定（pin=1）后**永不被遗忘机制降级或归档** —— "
                           "用于「表达过强烈在意」或「绝不能失忆」的条目（身份、安全、关系、用户明确要求记住的）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "description": "记忆 id"},
                    "on": {"type": "boolean", "description": "true=锁定（默认），false=解锁"},
                },
                "required": ["id"],
            },
        },
    },
]


def _store(cfg: dict):
    from memory.store import get_store

    root = Path(cfg.get("_project_root", "."))
    mcfg = cfg.get("_memory", {})
    db = root / mcfg.get("db", "memory/memory.db")
    export = root / mcfg.get("export_dir", "memory/export")
    return get_store(db, export, mcfg)


def _workspace(cfg: dict) -> str:
    from memory.store import normalize_scope

    return normalize_scope(cfg.get("_workspace"))


def _scopes_for(cfg: dict, scope: str | None) -> list[str] | None:
    """把 scope 参数翻译成检索作用域列表。None = 不限（全部目录）。"""
    from memory.store import GLOBAL_SCOPE

    ws = _workspace(cfg)
    s = str(scope or "auto").strip().lower()
    if s in ("all", "*", "全部"):
        return None
    if s in ("global", "全局"):
        return [GLOBAL_SCOPE]
    if s in ("workspace", "本目录", "目录"):
        return [ws]
    return [GLOBAL_SCOPE] if ws == GLOBAL_SCOPE else [GLOBAL_SCOPE, ws]


def _ws_label(cfg: dict) -> str:
    p = str(cfg.get("_workspace_path") or "")
    if not p:
        return "当前工作目录"
    try:
        from pathlib import Path

        name = Path(p).name or p
        return f"工作目录「{name}」"
    except Exception:
        return "当前工作目录"


def _fmt_entries(entries: list[dict], limit: int = 160) -> str:
    """列条目：正文优先显示 summary（结论），没有才回退 content 前 limit 字。"""
    lines = []
    for e in entries:
        tags = f"[{e['tags']}] " if e.get("tags") else ""
        body = (e.get("summary") or "").strip().replace("\n", " ")
        prefix = ""
        if body:
            prefix = ""
            if len(body) > limit:
                body = body[:limit] + "…"
            # 有结论时再附一小段正文，便于判断是不是要找的
            raw = e["content"].replace("\n", " ").strip()
            if raw and raw[:20] not in body:
                body += " ｜ " + (raw[:60] + "…" if len(raw) > 60 else raw)
        else:
            body = e["content"].replace("\n", " ").strip()
            if len(body) > limit:
                body = body[:limit] + "…"
        lines.append(f"#{e['id']} (L{e['layer']}·重要度{e['importance']}·命中{e['hit_count']}次"
                     f"{'·全局' if (e.get('workspace') or 'global') == 'global' else '·本目录'}) "
                     f"{tags}{e['title']}：{body}")
    return "\n".join(lines)


def memory_write(args: dict, workdir: str, cfg: dict) -> str:
    from memory.store import GLOBAL_SCOPE, LAYER_CONTENT_LIMIT, L1_SUMMARY_LIMIT

    layer = int(args.get("layer") or 2)
    if layer not in (1, 2, 3, 4):
        layer = 2
    title = str(args.get("title") or "").strip()
    content = str(args.get("content") or "").strip()
    if not title or not content:
        raise ValueError("title 和 content 都不能为空")
    summ = str(args.get("summary") or "").strip()
    store = _store(cfg)
    ws = _workspace(cfg)
    scope_arg = str(args.get("scope") or "auto").strip().lower()
    if layer == 4:
        target = ws
    elif scope_arg in ("global", "全局"):
        target = GLOBAL_SCOPE
    elif scope_arg in ("workspace", "本目录", "目录"):
        target = ws
    else:
        target = GLOBAL_SCOPE if layer == 1 else ws
    imp_arg = (int(args["importance"]) if str(args.get("importance") or "").strip() else None)
    before = store.find_duplicate(layer, title, content, workspace=target) if layer != 4 else None
    # 会话记忆不做查重（每次任务都该留痕）
    eid = store.add_entry(
        layer=layer, title=title, content=content, summary=summ,
        tags=str(args.get("tags") or ""),
        importance=imp_arg,
        dedup=(layer != 4),
        workspace=target,
        # 可追溯：这条记忆是哪次会话写进来的，出问题能查到出处
        kind=str(args.get("kind") or ("fact" if layer == 1 else "note"))[:24],
        source_session=str(cfg.get("_session_id") or cfg.get("session") or "")[:80],
        valid_until=str(args.get("valid_until") or "")[:19],
    )
    entry = store.get_entry(eid) or {}
    scope_txt = "全局" if target == GLOBAL_SCOPE else _ws_label(cfg)
    names = {1: "常驻核心(L1)", 2: "重要记忆(L2)", 3: "情景记忆(L3)", 4: "会话记忆(L4)"}
    verb = f"已更新 #{eid}（查重命中，同作用域已有同主题条目）" if before else f"已记住 #{eid}"
    warns = []
    promotes = []
    if target != GLOBAL_SCOPE and layer in (2, 3):
        threshold = int((cfg.get("_memory") or {}).get("auto_promote_importance", 4))
        imp_now = int(entry.get("importance") or 3)
        if imp_now >= threshold:
            pr = store.promote_entry(eid, min_importance=threshold)
            if pr.get("ok"):
                promotes.append(f"重要度 {imp_now}≥{threshold}，已自动提取到全局记忆 #{pr['id']}")
            elif pr.get("merged"):
                promotes.append(pr.get("msg") or "全局已有同主题记忆，已合并")
    if layer == 1 and not summ:
        warns.append("未给 summary，已自动取正文首句作结论；建议改成更精炼的 ≤60 字结论")
    if summ and layer == 1 and len(summ) > L1_SUMMARY_LIMIT:
        warns.append(f"summary {len(summ)} 字，超过 L1 显示上限 {L1_SUMMARY_LIMIT} 字会被截断，建议压缩")
    limit = LAYER_CONTENT_LIMIT.get(layer)
    if limit and len(content) > limit:
        warns.append(f"正文 {len(content)} 字，超过 L{layer} 建议上限 {limit} 字（不会丢，但检索/维护会变重）")
    if layer == 1:
        # 预算只提醒、不截断：写 L1 时告知当前额度，满了就该先精简旧条目
        try:
            mcfg = cfg.get("_memory") or {}
            _, meta = _store(cfg).get_l1_block(int(mcfg.get("l1_budget_chars", 1800)), with_meta=True)
            if meta["full"]:
                warns.append(f"L1 常驻预算已满（{meta['used']}/{meta['budget']} 字，超出 {meta['over_chars']} 字），"
                             "条目仍会全量注入，但建议先合并/压缩旧条目，或把可检索的经验类内容改放 L2")
        except Exception:
            pass
    try:
        from memory.store import tag_notice
        _tn = tag_notice(before["tags"] if before else str(args.get("tags") or ""))
        if _tn:
            warns.append(_tn)
    except Exception:
        pass
    hint = ("；注意：" + "；".join(warns)) if warns else ""
    prom = ("；" + "；".join(promotes)) if promotes else ""
    if before:
        return (f"{verb}（{names.get(layer,'?')}·{scope_txt}）：{title}"
                f"（要单独改标签可用 memory_tags）{prom}{hint}")
    return f"{verb}（{names.get(layer,'?')}·{scope_txt}）：{title}{prom}{hint}"


def memory_tags(args: dict, workdir: str, cfg: dict) -> str:
    """标签设置 / 追加 / 移除 / 总览。数量不限，只提醒不截断。"""
    from memory.store import normalize_tags, tag_notice

    store = _store(cfg)
    action = str(args.get("action") or "add").strip().lower()
    if action in ("overview", "list", "总览"):
        top = int(args.get("top") or 40)
        rows = store.tags_overview(top, scopes=_scopes_for(cfg, args.get("scope")))
        if not rows:
            return "标签总览：当前范围内没有任何标签。"
        lines = [f"标签总览（共 {len(rows)} 个，按使用条数排序）："]
        lines += [f"  {r['tag']}（{r['count']} 条）" for r in rows]
        lines.append("提示：同类内容用同一个标签，检索命中率更高；同义标签会分散权重。")
        return "\n".join(lines)

    eid = int(args.get("id") or 0)
    if eid <= 0:
        raise ValueError("请提供 id（可先用 memory_search 找到目标条目）")
    e = store.get_entry(eid)
    if not e:
        return f"未找到记忆 #{eid}"
    old = (e.get("tags") or "").strip()
    given = normalize_tags(args.get("tags"))

    if action == "set":
        new = given
    elif action == "remove":
        drop = {t.lower() for t in given.split(",") if t}
        new = normalize_tags(",".join(t for t in old.split(",") if t.strip().lower() not in drop))
    else:  # add / append
        if not given:
            n0 = len([x for x in old.split(",") if x.strip()])
            return f"#{eid}《{e['title']}》当前标签（{n0} 个）：{old or '（无）'}"
        new = store._merge_tags(old, given)

    store.set_tags(eid, new)
    n = len([x for x in new.split(",") if x.strip()])
    out = [f"已更新 #{eid}《{e['title']}》的标签：",
           f"  旧：{old or '（无）'}",
           f"  新：{new or '（无）'}（{n} 个）"]
    note = tag_notice(new)
    if note:
        out.append("提醒：" + note)
    return "\n".join(out)


def memory_get(args: dict, workdir: str, cfg: dict) -> str:
    eid = int(args.get("id") or 0)
    if eid <= 0:
        raise ValueError("请提供 id")
    store = _store(cfg)
    e = store.get_entry(eid)
    if not e:
        return f"未找到记忆 #{eid}"
    # 主动读全文 = 一次**真实使用**（这是最强信号：不是被撞词带出来的）
    try:
        store.record_hit(eid)
        e = store.get_entry(eid)
    except Exception:
        pass
    head = (f"#{e['id']} (L{e['layer']}·重要度{e['importance']}·被用{e['hit_count']}次·"
            f"注入{e.get('inject_count') or 0}次·{'已锁定 ' if e.get('pin') else ''}"
            f"创建{e['created_at']}·更新{e['updated_at']})")
    tags = f"标签：{e['tags']}\n" if e.get("tags") else ""
    summ = f"结论：{e['summary']}\n" if (e.get("summary") or "").strip() else "结论：（无）\n"
    return f"{head}\n标题：{e['title']}\n{tags}{summ}正文：\n{e['content']}"


def memory_stats(args: dict, workdir: str, cfg: dict) -> str:
    store = _store(cfg)
    s = store.stats()
    names = {"L1": "常驻核心", "L2": "重要记忆", "L3": "情景记忆", "L4": "会话记忆"}
    lines = ["记忆库现状（全库）："]
    for k in ("L1", "L2", "L3", "L4"):
        miss = s["summary_missing"].get(k)
        extra = f"，缺结论 {miss} 条" if miss else ""
        lines.append(f"  {k} {names[k]}：{s[k]} 条 / {s['chars'][k]} 字{extra}")
    lines.append(f"  已归档 {s['archived']} 条；去重命中累计 {s['dedup_hits']} 次")
    # 工作目录作用域：同目录所有会话共享，重要条目会自动提取到全局
    try:
        ws = _workspace(cfg)
        scopes = [ws] if ws == "global" else ["global", ws]
        cur = store.stats(scopes=scopes)
        own = store.scope_entry_count(ws)
        glob = store.scope_entry_count("global")
        lines.append(f"  当前检索范围：{_scope_desc(cfg, 'auto')} → 全局 {glob} 条 / 本目录 {own} 条")
        lines.append(f"  本范围活跃合计：L1 {cur['L1']} · L2 {cur['L2']} · L3 {cur['L3']} · L4 {cur['L4']}")
    except Exception:
        pass
    # 「被用」vs「被注入」：分开看才知道哪些条目是白占额度的
    try:
        rows = store._rows(
            "SELECT COUNT(*) n, SUM(CASE WHEN hit_count=0 AND inject_count>0 THEN 1 ELSE 0 END) churn"
            " FROM entries WHERE status='active'")
        r0 = rows[0] if rows else {}
        lines.append(f"  使用计数：活跃 {r0.get('n') or 0} 条中，"
                     f"「被注入过但从未被用」{r0.get('churn') or 0} 条")
    except Exception:
        pass
    # L1 预算用量（预算只提醒、不截断：满了照样全量注入）
    try:
        mcfg = cfg.get("_memory") or {}
        budget = int(mcfg.get("l1_budget_chars", 1800))
        ws = _workspace(cfg)
        scopes = [ws] if ws == "global" else ["global", ws]
        _, meta = store.get_l1_block(budget, with_meta=True, scopes=scopes)
        state = "已满（仍全量注入，未截断）" if meta["full"] else ("将满" if meta["near"] else "充裕")
        lines.append(f"  L1 常驻预算：{meta['used']} / {meta['budget']} 字（{state}）")
    except Exception:
        pass
    return "\n".join(lines)


def memory_forget_candidates(args: dict, workdir: str, cfg: dict) -> str:
    """列出遗忘候选（只提议、不改数据）。"""
    store = _store(cfg)
    layer = args.get("layer")
    cands = store.forget_candidates(
        layer=(int(layer) if str(layer or "").strip() not in ("", "None") else None),
        min_age_days=(float(args["min_age_days"]) if str(args.get("min_age_days") or "").strip() else None),
        limit=int(args.get("limit") or 20),
    )
    if not cands:
        return ("遗忘候选：暂无。\n"
                "（判据：L2/L3 中层内得分垫底 + 距上次真正使用超过门槛天数；"
                "pin / 重要度 5 / 命中安全情感词的一律豁免。库龄不足时通常为空 —— 这是正常的。）")
    lines = [f"遗忘候选 {len(cands)} 条（**只提议，未改动任何数据**）："]
    for c in cands:
        act = "归档" if c["suggest"] == "归档" else "降为 L3"
        flag = " ⚠白占额度" if c["kind"] == "ed" else ""
        lines.append(
            f"  #{c['id']} L{c['layer']}《{c['title'][:34]}》"
            f"重要度{c['importance']} 被用{c['hit_count']}次 注入{c['inject_count']}次"
            f"（{c['reason']}）→ 建议{act}{flag}")
    lines.append("确认后我再调 memory_demote 执行（可指定 id，或说「都执行」）。")
    return "\n".join(lines)


def memory_demote(args: dict, workdir: str, cfg: dict) -> str:
    """执行降级（破坏性，需用户已明确同意）。"""
    store = _store(cfg)
    ids: list[int] = []
    if str(args.get("ids") or "").strip():
        ids = [int(x) for x in str(args["ids"]).replace("，", ",").split(",") if x.strip()]
    elif int(args.get("id") or 0) > 0:
        ids = [int(args["id"])]
    if not ids:
        raise ValueError("请给 id（单个）或 ids（逗号分隔）")
    out = []
    for i in ids:
        r = store.demote(i)
        out.append(r["msg"] if r else f"未找到记忆 #{i}")
    return "\n".join(out)


def memory_pin(args: dict, workdir: str, cfg: dict) -> str:
    """锁定/解锁一条记忆（锁定后永不被降级或归档）。"""
    eid = int(args.get("id") or 0)
    if eid <= 0:
        raise ValueError("请提供 id")
    on = True
    raw = args.get("on")
    if isinstance(raw, bool):
        on = raw
    elif str(raw or "").strip().lower() in ("false", "0", "no", "否", "解锁"):
        on = False
    r = _store(cfg).set_pin(eid, on)
    if not r:
        return f"未找到记忆 #{eid}"
    return r["msg"]


def memory_search(args: dict, workdir: str, cfg: dict) -> str:
    query = str(args.get("query") or "").strip()
    tag = str(args.get("tag") or "").strip()
    if not query and not tag:
        raise ValueError("query 和 tag 至少给一个")
    store = _store(cfg)
    scopes = _scopes_for(cfg, args.get("scope"))
    layer = args.get("layer")
    top_k = int(args.get("top_k") or (20 if (tag and not query) else 5))
    lyr = int(layer) if layer in (1, 2, 3, 4) else None
    if tag:
        entries = store.search(query, layer=lyr, top_k=top_k, tag=tag, scopes=scopes)
    elif lyr:
        entries = store.search(query, layer=lyr, top_k=top_k, scopes=scopes)
        if not entries:
            entries = store.get_layer(lyr, limit=top_k, scopes=scopes)
    else:
        entries = store.search(query, top_k=top_k, scopes=scopes)

    if entries and tag:
        # 标签检索结果：明确标出是「按标签选中的」，并提示标签匹配方式
        fb = any(e.get("_tag_fallback") for e in entries)
        head = (f"按标签「{tag}」找到 {len(entries)} 条"
                + ("（精确匹配无果，已按子串匹配）" if fb else "")
                + (f"，关键词「{query}」再排序" if query else "")
                + "：")
        return head + "\n" + _fmt_entries(entries)

    if not entries:
        # 兜底：提醒模型核心记忆已直接注入，并可换中文关键词/标签重试
        l1 = store.get_layer(1, limit=5, scopes=scopes)
        what = " 与 ".join(x for x in (f'「{query}」' if query else "", f'标签「{tag}」' if tag else "") if x)
        hint = f"记忆库中没有匹配{what}的词条（检索范围：{_scope_desc(cfg, args.get('scope'))}）。"
        hint += "注意：核心记忆（用户资料、偏好等）已直接包含在你的系统提示中，请先参考那里；"
        hint += "如需再查请换用中文关键词，或用 tag 按标签找（action=overview 可先看有哪些标签）。"
        if tag:
            try:
                ov = store.tags_overview(20, scopes=scopes)
                if ov:
                    hint += "\n现有常用标签：" + "、".join(f"{t['tag']}({t['count']})" for t in ov)
            except Exception:
                pass
        if l1:
            hint += "\n当前核心记忆概览：\n" + _fmt_entries(l1)
        return hint
    return f'检索 "{query}" 命中 {len(entries)} 条（范围：{_scope_desc(cfg, args.get("scope"))}）：\n' + _fmt_entries(entries)


def _scope_desc(cfg: dict, scope: str | None) -> str:
    s = str(scope or "auto").strip().lower()
    if s in ("all", "*", "全部"):
        return "全部目录 + 全局"
    if s in ("workspace", "本目录", "目录"):
        return _ws_label(cfg)
    if s in ("global", "全局"):
        return "全局"
    ws = _workspace(cfg)
    return "全局" if ws == "global" else f"全局 + {_ws_label(cfg)}"


def memory_promote(args: dict, workdir: str, cfg: dict) -> str:
    eid = int(args.get("id") or 0)
    if eid <= 0:
        raise ValueError("请提供 id（可先用 memory_search 找到目标条目）")
    r = _store(cfg).promote_entry(eid)
    return r.get("msg") or str(r)


def memory_forget(args: dict, workdir: str, cfg: dict) -> str:
    store = _store(cfg)
    eid = int(args.get("id") or 0)
    query = str(args.get("query") or "").strip()
    layer = args.get("layer")
    if eid > 0:
        ok = store.forget(eid)
        return f"已删除记忆 #{eid}" if ok else f"未找到记忆 #{eid}"
    if query:
        n = store.forget_many(query=query)
        return f"已删除 {n} 条包含「{query}」的记忆" if n else f"没有找到包含「{query}」的记忆"
    if layer in (1, 2, 3, 4):
        n = store.forget_many(layer=int(layer))
        names = {1: "常驻核心", 2: "重要", 3: "情景", 4: "会话"}
        return f"已清空 {names[int(layer)]}记忆（L{int(layer)}，共 {n} 条）"
    raise ValueError("请提供 id、query 或 layer 之一")
