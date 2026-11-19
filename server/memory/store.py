# -*- coding: utf-8 -*-
"""四层记忆存储（SQLite）+ 检索 + 归档维护 + 人类可读导出。

层级（2026-10-11 由三层重构为四层）：
  L1 常驻核心  特别重要、不含经验（身份/偏好/行为规则/安全/关系）—— 全量注入，读 summary
  L2 重要记忆  重要但可按需检索：可复用经验/技术方案/技能入口 —— 检索 top-k
  L3 情景记忆  历史项目记录/事件/时效信息 —— 检索 top-k
  L4 会话记忆  会话内任务状态 —— 取本会话最近 n 条，过期归档进 L3

summary 字段：每条记忆的「结论」（建议 ≤40 字）。注入只读它，content 保留全文细节。
存取分离（存冗余、读精简）：解决「存 800 字但注入只读 180 字、且读到的多是铺垫」的问题。

检索：jieba 分词 + LIKE 命中计分（title×3 / tags×2 / content×1）
      × 时间衰减 × 重要度加成；命中自动累计 hit 统计。
"""
from __future__ import annotations

import math
import os
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

_jieba = None
_jieba_checked = False


def _get_jieba():
    """懒加载：jieba 首次分词要建 1s 左右的词典，不该拖慢整个界面启动。"""
    global _jieba, _jieba_checked
    if not _jieba_checked:
        _jieba_checked = True
        try:
            import jieba
            _jieba = jieba
        except Exception:  # pragma: no cover
            _jieba = None
    return _jieba

_TS_FMT = "%Y-%m-%d %H:%M:%S"

# 记忆作用域：global = 全局（所有工作目录可见）；其余为工作目录规范路径
GLOBAL_SCOPE = "global"


def normalize_scope(scope: str | None) -> str:
    """作用域规范化：空值/global 归全局；路径统一大小写与分隔符。"""
    s = str(scope or "").strip()
    if not s or s.lower() == GLOBAL_SCOPE or s == "*":
        return GLOBAL_SCOPE
    return os.path.normcase(os.path.normpath(s))

# 注入时单条正文的可见截断长度（与 inject._fmt_entry 的 content_limit 保持一致）。
# 预算必须按「可见长度」算，否则长条目会按全文吃光预算、实际却只显示 180 字。
INJECT_CONTENT_LIMIT = 180
# L1 单条结论的显示上限（与 inject.py 里 L1 的 _fmt_entry 上限一致）
L1_INJECT_LIMIT = 96
# 写入时若无 summary，自动用正文首句补一个（避免新条目又变成「注入读到铺垫」）
AUTO_SUMMARY_LEN = 70
_SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    layer       INTEGER NOT NULL,
    title       TEXT NOT NULL,
    content     TEXT NOT NULL,
    summary     TEXT DEFAULT '',
    tags        TEXT DEFAULT '',
    importance  INTEGER DEFAULT 3,
    session_id  TEXT,
    workspace   TEXT DEFAULT 'global',
    source_workspace TEXT DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    last_hit_at TEXT,
    hit_count   INTEGER DEFAULT 0,
    inject_count INTEGER DEFAULT 0,
    pin         INTEGER DEFAULT 0,
    status      TEXT DEFAULT 'active'
);
CREATE INDEX IF NOT EXISTS idx_entries_layer ON entries(layer, status);
CREATE INDEX IF NOT EXISTS idx_entries_session ON entries(session_id);
CREATE INDEX IF NOT EXISTS idx_entries_workspace ON entries(workspace, layer, status);
"""

# 轻量 schema 迁移：老库缺列时自动补，避免每次改结构都要人工 ALTER。
_MIGRATIONS = [
    ("entries", "summary", "ALTER TABLE entries ADD COLUMN summary TEXT DEFAULT ''"),
    ("entries", "inject_count", "ALTER TABLE entries ADD COLUMN inject_count INTEGER DEFAULT 0"),
    ("entries", "pin", "ALTER TABLE entries ADD COLUMN pin INTEGER DEFAULT 0"),
    ("entries", "workspace", "ALTER TABLE entries ADD COLUMN workspace TEXT DEFAULT 'global'"),
    ("entries", "source_workspace", "ALTER TABLE entries ADD COLUMN source_workspace TEXT DEFAULT ''"),
    ("entries", "kind", "ALTER TABLE entries ADD COLUMN kind TEXT DEFAULT ''"),
    ("entries", "source_session", "ALTER TABLE entries ADD COLUMN source_session TEXT DEFAULT ''"),
    ("entries", "source_msg", "ALTER TABLE entries ADD COLUMN source_msg TEXT DEFAULT ''"),
    ("entries", "strength", "ALTER TABLE entries ADD COLUMN strength REAL DEFAULT 1.0"),
    ("entries", "supersedes", "ALTER TABLE entries ADD COLUMN supersedes INTEGER DEFAULT 0"),
    ("entries", "superseded_by", "ALTER TABLE entries ADD COLUMN superseded_by INTEGER DEFAULT 0"),
    ("entries", "valid_until", "ALTER TABLE entries ADD COLUMN valid_until TEXT DEFAULT ''"),
    ("entries", "merged_into", "ALTER TABLE entries ADD COLUMN merged_into INTEGER DEFAULT 0"),
]

# 各层建议的单条正文上限（字）。超限只提醒不截断，避免丢数据。
LAYER_CONTENT_LIMIT = {1: 1200, 2: 2500, 3: 4000, 4: 800}
# 各层默认重要度：不传 importance 时按层给（原先 L3/L4 一律写死 2，导致排序退化成纯时间序）
LAYER_DEFAULT_IMPORTANCE = {1: 5, 2: 4, 3: 3, 4: 2}
# L1 是常驻注入，summary 建议不超过这个长度（inject.py 显示上限 96）
L1_SUMMARY_LIMIT = 96
# 归档豁免词：含这些词的情景记忆永不自动归档（情感/安全类不能因为「久未命中」而消失）
ARCHIVE_KEEP_KEYWORDS = ("乖乖", "自杀", "轻生", "自伤", "安全", "不爱", "骗", "说谎",
                         "欺骗", "背叛", "感情", "心痛", "委屈", "难过", "关系", "爱",
                         "画像", "性格", "手机号", "邮箱", "登录", "名字", "纠正")

# ---------- 遗忘机制（只提议、不自动执行）----------
# 作用域：L2 / L3。L1 只精不降（走预算提醒）；L4 是会话暂存，不参与评分。
# 「使用」的定义：hit_count（我主动检索/读全文）——被注入**不算使用**，记在 inject_count。
FORGET_MIN_AGE_DAYS = 90        # 绝对门槛：距上次「真正使用」超过这么多天才算候选
FORGET_MIN_LAYER_SIZE = 20      # 层内条数少于此值时不做相对比较，只看绝对天数
FORGET_EXEMPT_IMPORTANCE = 5    # 重要度达到此值 → 永久豁免
FORGET_CANDIDATE_LIMIT = 20
# L4 过期时：重要度 >= 此值才升进 L3，其余归档（避免会话流水淹没 L3）
L4_PROMOTE_IMPORTANCE = 3


def _auto_summary(content: str, limit: int = AUTO_SUMMARY_LEN) -> str:
    """从正文机械提取一句结论：取首个有意义行（去 markdown 装饰）。

    宁可粗糙也要有 —— 没有 summary 时注入只能读正文前 180 字，往往读到的全是铺垫。
    """
    for ln in (content or "").splitlines():
        ln = ln.strip()
        while ln[:1] in ("*", "#", "-", ">", "`", "|", " "):
            ln = ln[1:]
        ln = ln.replace("**", "").strip()
        if len(ln) >= 6 and not ln.startswith(("```", "!", "http")):
            return ln[:limit] + ("…" if len(ln) > limit else "")
    return (content or "").strip()[:limit]


# 标签数量「只提醒不截断」的软阈值（与 L1 预算同一套哲学）。
TAG_WARN_COUNT = 8
TAG_WARN_CHARS = 60


def normalize_tags(tags) -> str:
    """规范化标签：逗号/顿号/分号/竖线/空格统一切分，去重保序、去空白。

    数量**不做限制**（用户拍板：一条记忆的标签不限个数）——
    只在超出软阈值时由调用方给一句提醒，绝不截断、绝不丢弃。
    """
    if tags is None:
        return ""
    if isinstance(tags, (list, tuple, set)):
        raw = [str(t) for t in tags]
    else:
        s = str(tags)
        for ch in ("、", "；", ";", "|", "，", "\n", "\t", " "):
            s = s.replace(ch, ",")
        raw = s.split(",")
    parts, seen = [], set()
    for t in raw:
        t = t.strip().strip("#")
        if not t:
            continue
        k = t.lower()
        if k in seen:
            continue
        seen.add(k)
        parts.append(t)
    return ",".join(parts)


def tag_notice(tags: str) -> str:
    """标签过多/过长时给一句提醒（只提醒，不截断）。"""
    t = (tags or "").strip()
    if not t:
        return ""
    n = len([x for x in t.split(",") if x.strip()])
    if n > TAG_WARN_COUNT or len(t) > TAG_WARN_CHARS:
        return (f"标签 {n} 个 / {len(t)} 字，偏多。标签是检索加分项（命中 +2 分），"
                f"过多会让这条在各类查询下都被捞出来、挤掉真正相关的结果；"
                f"已按你的设定**全部保留、未截断**，建议只留真正能用来找它的几个。")
    return ""


def _now() -> str:
    return datetime.now().strftime(_TS_FMT)


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.strptime(ts, _TS_FMT)
    except ValueError:
        return None


# 检索用停用词：高频虚词，匹配它们基本全是噪声
_STOPWORDS = {
    "的", "了", "是", "在", "我", "你", "他", "她", "它", "们", "有", "和", "与", "或",
    "及", "之", "这", "那", "这个", "那个", "一个", "什么", "怎么", "如何", "为什么",
    "吗", "呢", "吧", "啊", "呀", "哦", "嗯", "就", "都", "也", "还", "又", "很", "太",
    "更", "最", "会", "能", "要", "可以", "请", "帮", "让", "把", "被", "给", "对",
    "从", "到", "向", "于", "以", "为", "着", "过", "并", "并且", "而且", "但是",
    "然后", "所以", "因为", "如果", "虽然", "已经", "一下", "现在",
}


def tokenize(query: str) -> list[str]:
    """jieba 分词（搜索模式）＋ 停用词过滤；过滤空白。"""
    q = (query or "").strip()
    if not q:
        return []
    toks: list[str] = []
    jb = _get_jieba()
    if jb is not None:
        toks = [t.strip() for t in jb.cut_for_search(q) if t.strip()]
    else:
        toks = q.split()
    # 去重保序，过滤停用词与纯符号
    seen, out = set(), []
    for t in toks:
        tl = t.lower()
        if tl in seen or tl in _STOPWORDS:
            continue
        if all(not ch.isalnum() for ch in t):  # 纯标点/空白
            continue
        if len(t) == 1 and t.isascii() and not t.isalnum():
            continue
        seen.add(tl)
        out.append(t)
    return out


class MemoryStore:
    def __init__(self, db_path: Path, export_dir: Path | None = None, cfg: dict | None = None):
        self.db_path = Path(db_path)
        self.export_dir = Path(export_dir) if export_dir else None
        self.cfg = cfg or {}
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._dedup_hits = 0
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            try:
                self._conn.executescript(_SCHEMA)
            except sqlite3.OperationalError:
                # 老库缺新列：_SCHEMA 里的新索引会先失败 → 先补列再重建
                self._migrate()
                self._conn.executescript(_SCHEMA)
            self._migrate()
            self._conn.commit()

    def _migrate(self) -> None:
        """轻量迁移：老库缺列自动补（幂等，失败忽略）。"""
        for _table, col, sql in _MIGRATIONS:
            try:
                cols = {r[1] for r in self._conn.execute(f"PRAGMA table_info({_table})").fetchall()}
                if col not in cols:
                    self._conn.execute(sql)
            except Exception:
                pass
        try:
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_entries_workspace"
                " ON entries(workspace, layer, status)"
            )
        except Exception:
            pass

    # ---------- 基础 ----------
    @staticmethod
    def _scope_sql(scopes: list[str] | tuple[str, ...] | None) -> tuple[str, tuple]:
        """把作用域列表编译成 SQL 片段。None = 不限制（UI 全量视图）。"""
        if scopes is None:
            return "", ()
        vals: list[str] = []
        for s in scopes:
            v = normalize_scope(s)
            if v not in vals:
                vals.append(v)
        if not vals:
            return "", ()
        return " AND workspace IN (" + ",".join("?" * len(vals)) + ")", tuple(vals)

    def _rows(self, sql: str, args: tuple = ()) -> list[dict]:
        with self._lock:
            cur = self._conn.execute(sql, args)
            return [dict(r) for r in cur.fetchall()]

    def _exec(self, sql: str, args: tuple = ()) -> int:
        with self._lock:
            cur = self._conn.execute(sql, args)
            self._conn.commit()
            return cur.lastrowid or cur.rowcount

    # ---------- 写入 ----------
    def add_entry(self, *, layer: int, title: str, content: str, tags: str = "",
                  importance: int | None = None, session_id: str | None = None,
                  summary: str = "", dedup: bool = True,
                  workspace: str | None = GLOBAL_SCOPE,
                  source_workspace: str = "",
                  kind: str = "", source_session: str = "", source_msg: str = "",
                  valid_until: str = "") -> int:
        """写入一条记忆。summary 为结论（建议 ≤40 字），注入时只读它。

        importance 省略时按层给默认值（LAYER_DEFAULT_IMPORTANCE）：
        原先会话记忆一律写死 2，导致 L1 之外的重要度形同虚设、排序退化成纯时间序。

        dedup=True 时先查重：同层同标题、或正文高度重合 → 不新增，改为更新原条目，
        避免同一件事反复入库（这是「写入不去重」缺陷的修复）。
        """
        title = title.strip()
        content = content.strip()
        summary = (summary or "").strip()
        if not summary:
            summary = _auto_summary(content)
        layer = int(layer)
        if importance is None:
            imp = int(LAYER_DEFAULT_IMPORTANCE.get(layer, 3))
        else:
            imp = max(1, min(5, int(importance)))
        ts = _now()
        ws = normalize_scope(workspace)

        superseded_id = 0
        if dedup:
            dup = self._find_duplicate(layer, title, content, workspace=ws)
            # 同标题但结论不同 → 不是「同一条记忆的新版本」，而是「旧结论被取代」。
            # 合并会把两种结论搅在一起（历史上出现过），所以改成保留旧条目 + 标记取代。
            if dup is not None and self._conflicts(dup, summary):
                self._exec("UPDATE entries SET status='superseded', updated_at=? WHERE id=?",
                           (ts, int(dup["id"])))
                superseded_id = int(dup["id"])
                dup = None
            if dup is not None:
                old_c = (dup.get("content") or "").strip()
                # 正文有实质新增才追加（避免覆盖丢信息，也避免无限膨胀）
                new_c = content
                if old_c and new_c and new_c not in old_c and len(new_c) > 40:
                    if old_c not in new_c:
                        merged = old_c + "\n---\n" + new_c
                        if len(merged) <= LAYER_CONTENT_LIMIT.get(layer, 2500):
                            new_c = merged
                        else:
                            new_c = new_c  # 超上限则用最新版本（结论更可能在后写的）
                self._exec(
                    "UPDATE entries SET content=?, summary=?, tags=?, importance=?,"
                    " status='active', updated_at=? WHERE id=?",
                    (new_c, summary or (dup.get("summary") or ""),
                     self._merge_tags(dup.get("tags", ""), tags),
                     max(int(dup.get("importance") or 3), imp), ts, dup["id"]),
                )
                self._dedup_hits += 1
                self.export_markdown()
                return int(dup["id"])

        eid = self._exec(
            "INSERT INTO entries (layer, title, content, summary, tags, importance,"
            " session_id, workspace, source_workspace, created_at, updated_at,"
            " kind, source_session, source_msg, valid_until)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (layer, title, content, summary, normalize_tags(tags), imp, session_id,
             ws, normalize_scope(source_workspace) if source_workspace else "", ts, ts,
             str(kind or "")[:24], str(source_session or "")[:80], str(source_msg or "")[:80],
             str(valid_until or "")[:19]),
        )
        if superseded_id:
            self._exec("UPDATE entries SET superseded_by=? WHERE id=?", (eid, superseded_id))
            self._exec("UPDATE entries SET supersedes=? WHERE id=?", (superseded_id, eid))
        self.archive_maintenance()
        self.export_markdown()
        return eid

    @staticmethod
    def _merge_tags(old: str, new: str) -> str:
        return normalize_tags((old or "") + "," + (new or ""))

    def set_tags(self, entry_id: int, tags) -> str:
        """改写某条记忆的标签（前端「标签设置」用）。返回规范化后的标签串。

        数量不限；tags 传空串即清空标签。
        """
        nt = normalize_tags(tags)
        self._exec("UPDATE entries SET tags=?, updated_at=? WHERE id=?",
                   (nt, _now(), int(entry_id)))
        self.export_markdown()
        return nt

    def update_entry(self, entry_id: int, *, title: str | None = None,
                     content: str | None = None, summary: str | None = None,
                     tags=None, importance: int | None = None) -> dict | None:
        """局部更新一条记忆（只更新传入的字段）。返回更新后的整条。"""
        e = self.get_entry(entry_id)
        if not e:
            return None
        sets, args = [], []
        if title is not None and str(title).strip():
            sets.append("title=?")
            args.append(str(title).strip())
        if content is not None and str(content).strip():
            sets.append("content=?")
            args.append(str(content).strip())
        if summary is not None:
            sets.append("summary=?")
            args.append(str(summary).strip() or _auto_summary(content or e["content"]))
        if tags is not None:
            sets.append("tags=?")
            args.append(normalize_tags(tags))
        if importance is not None:
            sets.append("importance=?")
            args.append(max(1, min(5, int(importance))))
        if not sets:
            return e
        sets.append("updated_at=?")
        args.append(_now())
        args.append(int(entry_id))
        self._exec(f"UPDATE entries SET {', '.join(sets)} WHERE id=?", tuple(args))
        self.export_markdown()
        return self.get_entry(entry_id)

    def find_duplicate(self, layer: int, title: str, content: str,
                       workspace: str | None = GLOBAL_SCOPE) -> dict | None:
        """查重（公开接口）：默认只在本作用域内查，避免把目录记忆并入全局条目。"""
        return self._find_duplicate(layer, title, content, workspace=workspace)

    def _find_duplicate(self, layer: int, title: str, content: str,
                        workspace: str | None = GLOBAL_SCOPE) -> dict | None:
        """查重：① 同层同标题 ② 同层正文前 60 字相同 ③ 字符集合重合率 ≥0.8。"""
        scope_sql, scope_args = self._scope_sql([workspace] if workspace else None)
        rows = self._rows(
            "SELECT * FROM entries WHERE layer=? AND status='active'" + scope_sql,
            (int(layer),) + scope_args)
        ct = (content or "")[:60]
        for e in rows:
            if e["title"] == title:
                return e
            ec = e["content"] or ""
            if ct and ec[:60] == ct:
                return e
        if len(content) >= 80:
            cs = set(content)
            for e in rows:
                ec = e["content"] or ""
                if len(ec) < 80:
                    continue
                inter = len(cs & set(ec))
                union = len(cs | set(ec))
                if union and inter / union >= 0.8:
                    return e
        return None

    def update_summary(self, entry_id: int, summary: str) -> None:
        """回填/修改结论（不触碰 content）。"""
        self._exec("UPDATE entries SET summary=?, updated_at=? WHERE id=?",
                   ((summary or "").strip(), _now(), int(entry_id)))

    def forget(self, entry_id: int) -> bool:
        n = self._exec("DELETE FROM entries WHERE id=?", (int(entry_id),))
        self.export_markdown()
        return n > 0

    def forget_many(self, *, ids: list[int] | None = None, layer: int | None = None,
                    query: str | None = None) -> int:
        """批量删除：按 ids / 整层 / 关键词（标题+标签+内容 LIKE）。返回删除条数。"""
        targets: list[int] = []
        if ids:
            targets = [int(i) for i in ids]
        elif query:
            like = f"%{query}%"
            rows = self._rows(
                "SELECT id FROM entries WHERE status='active'"
                " AND (title LIKE ? OR tags LIKE ? OR content LIKE ?)",
                (like, like, like),
            )
            targets = [r["id"] for r in rows]
        elif layer in (1, 2, 3, 4):
            rows = self._rows(
                "SELECT id FROM entries WHERE layer=? AND status='active'", (int(layer),)
            )
            targets = [r["id"] for r in rows]
        if not targets:
            return 0
        with self._lock:
            qmarks = ",".join("?" * len(targets))
            cur = self._conn.execute(f"DELETE FROM entries WHERE id IN ({qmarks})", targets)
            self._conn.commit()
            n = cur.rowcount
        self.export_markdown()
        return n

    # ---------- 使用计数（「被推送」与「被用过」分开记）----------
    def record_hit(self, entry_id: int, n: int = 1) -> None:
        """记一次**真实使用**（我主动检索命中 / 主动读全文）。

        ⚠️ 只有调用方能判断自己是不是「使用者」：注入（inject.build_memory_block）
        走的是 search(touch=False)，不写这里 —— 被推送给模型 ≠ 被用过。
        """
        self._exec("UPDATE entries SET last_hit_at=?, hit_count=hit_count+? WHERE id=?",
                   (_now(), int(n), int(entry_id)))

    def record_inject(self, entries: list[dict]) -> None:
        """记一次**注入**（这批条目被塞进了上下文）。只加 inject_count，不动 hit_count。

        「被注入很多次、却从没被用过」（inject 高 / hit 0）正是遗忘机制最有价值的信号。
        """
        ids = [int(e["id"]) for e in (entries or []) if e.get("id")]
        if not ids:
            return
        qmarks = ",".join("?" * len(ids))
        self._exec(f"UPDATE entries SET inject_count=inject_count+1 WHERE id IN ({qmarks})",
                   tuple(ids))

    # ---------- 检索 ----------
    def _touch(self, entries: list[dict]) -> None:
        """累计命中统计（内存检索 hot path，只对最终返回的条目写）。"""
        if not entries:
            return
        ts = _now()
        for e in entries:
            self._exec("UPDATE entries SET last_hit_at=?, hit_count=hit_count+1 WHERE id=?", (ts, e["id"]))

    def search(self, query: str, *, layer: int | None = None, top_k: int = 4,
               touch: bool = True, include_archived: bool = False,
               tag: str | None = None,
               scopes: list[str] | tuple[str, ...] | None = None) -> list[dict]:
        """检索。tag 给定时**先按标签精确筛选**，再按关键词计分。

        - 只给 tag 不给 query：等价于「列出这个标签下的记忆」（按重要度/时间排序）
          —— 这就是「用标签选中一批记忆」的入口。
        - tag 支持带不带 # 前缀；精确匹配（不区分大小写）无果时自动退回子串匹配。
        - query 与 tag 可叠加：先标签收敛范围，再关键词排序。
        """
        tag_want = (tag or "").strip().lower().lstrip("#")
        toks = tokenize(query) if (query or "").strip() else []
        if not toks and not tag_want:
            return []
        where = ["layer = ?"] if layer else []
        where.append("status = 'active'" if not include_archived else "1=1")
        scope_sql, scope_args = self._scope_sql(scopes)
        sql = "SELECT * FROM entries WHERE " + " AND ".join(where)
        args: tuple = (layer,) if layer else ()
        if scope_sql:
            sql += scope_sql.replace(" AND ", " AND ", 1)
            args = args + scope_args
        rows = self._rows(sql, args)

        tag_fallback = False
        if tag_want:
            exact = [e for e in rows
                     if any(t.strip().lower() == tag_want for t in (e["tags"] or "").split(","))]
            if not exact:
                exact = [e for e in rows
                         if any(tag_want in t.strip().lower() for t in (e["tags"] or "").split(","))]
                tag_fallback = bool(exact)
            rows = exact
            if not rows:
                return []

        # 只按标签选：不打分，按重要度 + 新近度直接给（标签命中本身已是最强信号）
        if not toks:
            rows.sort(key=lambda e: (int(e["importance"] or 3), e["updated_at"] or ""), reverse=True)
            top = rows[: max(1, int(top_k))]
            for e in top:
                if tag_fallback:
                    e["_tag_fallback"] = True
            if touch:
                self._touch(top)
            return top

        now = datetime.now()
        # ---- BM25：词频做长度归一，避免「条目越长越容易命中」的霸榜问题 ----
        k1, b = 1.4, 0.72
        n_docs = max(1, len(rows))
        lens = {
            e["id"]: (len(e["title"]) * 3 + len(e["tags"] or "") * 2
                      + len(e.get("summary") or "") * 2 + len(e["content"]))
            for e in rows
        }
        avgdl = sum(lens.values()) / n_docs
        df = {}
        for t in toks:
            q = t.lower()
            df[t] = sum(
                1 for e in rows
                if q in (e["title"] + " " + (e["tags"] or "") + " "
                         + (e.get("summary") or "") + " " + e["content"]).lower()
            )
        scored = []
        for e in rows:
            tl, gl = e["title"].lower(), (e["tags"] or "").lower()
            sl, cl = (e.get("summary") or "").lower(), e["content"].lower()
            tf = 0.0
            idf_sum = 0.0
            for t in toks:
                q = t.lower()
                local = 3 * tl.count(q) + 2 * gl.count(q) + 2 * sl.count(q) + cl.count(q)
                if local <= 0:
                    continue
                tf += local
                d = df.get(t, 0)
                idf_sum += math.log(1.0 + (n_docs - d + 0.5) / (d + 0.5))
            if tf <= 0 or idf_sum <= 0:
                continue
            denom = tf + k1 * (1 - b + b * lens[e["id"]] / max(1.0, avgdl))
            bm25 = idf_sum * (tf * (k1 + 1)) / max(1e-6, denom)
            ref = _parse_ts(e["last_hit_at"]) or _parse_ts(e["updated_at"]) or now
            days = max(0.0, (now - ref).total_seconds() / 86400)
            recency = 1.0 / (1.0 + days / 30.0)
            strength = float(e["strength"] if "strength" in e.keys() and e["strength"] else 1.0)
            final = (bm25 + 0.5 * e["importance"]) * recency * min(1.6, strength)
            scored.append((final, e))
        scored.sort(key=lambda x: x[0], reverse=True)
        top = [e for _, e in scored[: max(1, int(top_k))]]

        if touch:
            self._touch(top)
        return top

    def by_tag(self, tag: str, *, layer: int | None = None, top_k: int = 20,
               touch: bool = False) -> list[dict]:
        """按标签列出记忆（精确匹配，无果自动退回子串；默认不动命中等统计）。"""
        return self.search("", layer=layer, top_k=top_k, tag=tag, touch=touch)

    # ---------- 分层读取 ----------
    def get_layer(self, layer: int, *, session_id: str | None = None, limit: int | None = None,
                  order: str = "importance DESC, updated_at DESC",
                  scopes: list[str] | tuple[str, ...] | None = None) -> list[dict]:
        scope_sql, scope_args = self._scope_sql(scopes)
        if layer == 4 and session_id is not None:
            sql = (f"SELECT * FROM entries WHERE layer=4 AND session_id=? AND status='active'"
                   f"{scope_sql} ORDER BY {order}")
            rows = self._rows(sql, (session_id,) + scope_args)
        else:
            sql = (f"SELECT * FROM entries WHERE layer=? AND status='active'"
                   f"{scope_sql} ORDER BY {order}")
            rows = self._rows(sql, (layer,) + scope_args)
        return rows[:limit] if limit else rows

    def get_l1_block(self, budget_chars: int,
                     content_limit: int = INJECT_CONTENT_LIMIT,
                     entry_limit: int = L1_INJECT_LIMIT,
                     with_meta: bool = False,
                     scopes: list[str] | tuple[str, ...] | None = None):
        """L1 全量注入 + 预算只做「额度提醒」（不再一刀切丢弃）。

        预算语义（2026-10-11 用户拍板）：
          - 预算**不是**一刀切：超预算的条目不再被静默丢弃，照样全部注入
            （宁可多送，也绝不能让安全/身份这类关键条目在某一轮悄悄消失）；
          - 预算只做**提醒**：返回 meta（used / budget / over_chars / heavy…），
            由 inject.py 写一句「核心记忆预算已满」提示模型，
            该精简哪几条由模型自己判断，而不是由代码按长度一刀切。

        可见长度计费：summary 截断到 entry_limit（与 inject._fmt_entry 一致），
        没有 summary 才回退正文前 content_limit 字（避免「按全文付费、只拿到 180 字」）。
        """
        rows = self.get_layer(1, scopes=scopes)
        budget = int(budget_chars)
        # 超预算先把「最久未用 + 非锁定 + 重要度<5」的条目降级到 L2，再重新取一次。
        # 只提醒不处理的结果是：模型从不主动精简，常驻块只会越来越肥。
        if budget > 0 and rows:
            used_now = sum(self._l1_cost(e, entry_limit, content_limit) for e in rows)
            if used_now > budget:
                if self.trim_l1(budget, scopes=scopes, entry_limit=entry_limit,
                                content_limit=content_limit):
                    rows = self.get_layer(1, scopes=scopes)
        out, used = [], 0
        charged: list[tuple[dict, int]] = []
        for e in rows:
            s = (e.get("summary") or "").strip()
            vis = min(len(s), entry_limit) if s else min(len(e["content"]), content_limit)
            cost = len(e["title"]) + vis + 8
            out.append(e)
            used += cost
            charged.append((e, cost))
        if not with_meta:
            return out
        heavy = sorted(charged, key=lambda t: t[1], reverse=True)[:3]
        meta = {
            "count": len(out),
            "used": used,
            "budget": budget,
            "over_chars": max(0, used - budget),
            "full": used > budget,
            "near": used <= budget and budget > 0 and used >= budget * 0.9,
            "heavy": [(e["id"], e["title"], c) for e, c in heavy],
        }
        return out, meta

        # ---------- 预算 / 冲突 / 体检 ----------
    def _l1_cost(self, e: dict, entry_limit: int = L1_INJECT_LIMIT,
                 content_limit: int = INJECT_CONTENT_LIMIT) -> int:
        s = (e.get("summary") or "").strip()
        vis = min(len(s), entry_limit) if s else min(len(e["content"]), content_limit)
        return len(e["title"]) + vis + 8

    def trim_l1(self, budget: int, scopes=None, entry_limit: int = L1_INJECT_LIMIT,
                content_limit: int = INJECT_CONTENT_LIMIT) -> list[dict]:
        """L1 超预算时自动降级（不是删除）：按「最久未用」优先降到 L2，可检索、可回溯。"""
        rows = self.get_layer(1, scopes=scopes)
        total = sum(self._l1_cost(e, entry_limit, content_limit) for e in rows)
        if budget <= 0 or total <= budget:
            return []
        cand = [e for e in rows
                if not int(e.get("pin") or 0) and int(e.get("importance") or 3) < 5]
        cand.sort(key=lambda e: (e.get("last_hit_at") or "", e.get("updated_at") or ""))
        demoted = []
        for e in cand:
            if total <= budget:
                break
            self._exec(
                "UPDATE entries SET layer=2, updated_at=?, "
                "tags=CASE WHEN instr(tags,'由L1降级')>0 THEN tags "
                "WHEN tags IS NULL OR tags='' THEN '由L1降级' ELSE tags || ',由L1降级' END "
                "WHERE id=?",
                (_now(), int(e["id"])),
            )
            total -= self._l1_cost(e, entry_limit, content_limit)
            demoted.append({"id": e["id"], "title": e["title"]})
        if demoted:
            self.export_markdown()
        return demoted

    @staticmethod
    def _conflicts(dup: dict, summary: str) -> bool:
        """同标题、但两条结论几乎不重合 → 视为「旧结论被取代」。"""
        old_s = (dup.get("summary") or "").strip()
        new_s = (summary or "").strip()
        if len(old_s) < 4 or len(new_s) < 4:
            return False
        a, b = set(old_s), set(new_s)
        if not a or not b:
            return False
        return (len(a & b) / max(1, len(a | b))) < 0.45

    def health(self, budget_chars: int = 1800) -> dict:
        """记忆体检：重复、注入了却从没用过、过期、被取代、L1 占用。"""
        rows = self._rows("SELECT * FROM entries", ())
        active = [e for e in rows if e.get("status") == "active"]
        by_title: dict[str, list] = {}
        for e in active:
            by_title.setdefault(e["title"], []).append(e)
        dups = [{"title": t, "ids": [x["id"] for x in v], "layers": sorted({x["layer"] for x in v})}
                for t, v in by_title.items() if len(v) > 1]
        never = [
            {"id": e["id"], "title": e["title"], "layer": e["layer"],
             "inject": int(e.get("inject_count") or 0)}
            for e in active
            if int(e.get("hit_count") or 0) == 0 and int(e.get("inject_count") or 0) >= 3
        ]
        l1 = [e for e in active if int(e["layer"]) == 1]
        used = sum(self._l1_cost(e) for e in l1)
        now = _now()
        expired = [
            {"id": e["id"], "title": e["title"], "valid_until": e.get("valid_until")}
            for e in active
            if (e.get("valid_until") or "") and str(e["valid_until"]) < now
        ]
        layers = {}
        for e in active:
            layers[int(e["layer"])] = layers.get(int(e["layer"]), 0) + 1
        return {
            "total": len(active),
            "layers": layers,
            "duplicates": dups[:20],
            "never_hit": never[:20],
            "expired": expired[:20],
            "l1": {"count": len(l1), "used": used, "budget": int(budget_chars),
                   "over": max(0, used - int(budget_chars))},
            "superseded": len([e for e in rows if e.get("status") == "superseded"]),
        }

# ---------- 归档维护 ----------
    def _keep_words(self) -> list[str]:
        keep = self.cfg.get("archive_keep_keywords") or ARCHIVE_KEEP_KEYWORDS
        if isinstance(keep, (list, tuple)):
            return [str(k).strip() for k in keep if str(k).strip()]
        return [k.strip() for k in str(keep).split(",") if k.strip()]

    def _is_exempt(self, e: dict) -> bool:
        """永不降级/归档的条目：显式锁定 pin、重要度满级、或命中安全/情感豁免词。"""
        if int(e.get("pin") or 0) == 1:
            return True
        if int(e.get("importance") or 3) >= FORGET_EXEMPT_IMPORTANCE:
            return True
        blob = f"{e.get('title','')}{e.get('tags','')}{e.get('summary') or ''}"
        return any(k in blob for k in self._keep_words())

    def archive_maintenance(self) -> dict:
        """归档维护（启动时跑一次）。

        - L4 过期 → 重要度够的升 L3（会话记录），其余**归档**（不再淹没 L3）
        - L3 长期未用 → archived
        - 只统计，不改 L2（L2 的降级由 forget_candidates 提议 + 人工确认）

        注意：这里**不做**「L1→L2」「L3→L4」这类跨用途搬迁 ——
        层是用途标签、不是垃圾等级；L4 只注入本会话，把 L3 搬进去等于藏起来。
        """
        now = datetime.now()
        l4_days = float(self.cfg.get("l4_archive_days", self.cfg.get("l3_archive_days", 3)))
        l3_days = float(self.cfg.get("l3_archive_days", 90))
        promote_imp = int(self.cfg.get("l4_promote_importance", L4_PROMOTE_IMPORTANCE))
        actions = {"l4_to_l3": 0, "l4_archived": 0, "l3_archived": 0, "l3_protected": 0}

        cutoff4 = (now - timedelta(days=l4_days)).strftime(_TS_FMT)
        for e in self._rows(
                "SELECT id, title, tags, summary, importance, pin FROM entries"
                " WHERE layer=4 AND status='active' AND updated_at <= ?", (cutoff4,)):
            if self._is_exempt(e):
                actions["l3_protected"] += 1
                continue
            if int(e.get("importance") or 2) >= promote_imp:
                self._exec(
                    "UPDATE entries SET layer=3,"
                    " tags=CASE WHEN tags='' THEN '会话记录' ELSE tags||',会话记录' END"
                    " WHERE id=?", (e["id"],))
                actions["l4_to_l3"] += 1
            else:
                # 低价值会话流水：留档但不进活跃池（archived 仍可用 include_archived 检索）
                self._exec("UPDATE entries SET status='archived' WHERE id=?", (e["id"],))
                actions["l4_archived"] += 1

        cutoff3 = (now - timedelta(days=l3_days)).strftime(_TS_FMT)
        rows = self._rows(
            "SELECT id, title, tags, summary, importance, pin FROM entries WHERE layer=3 AND status='active'"
            " AND COALESCE(last_hit_at, updated_at) <= ?", (cutoff3,))
        for e in rows:
            if self._is_exempt(e):
                actions["l3_protected"] += 1
                continue
            self._exec("UPDATE entries SET status='archived' WHERE id=?", (e["id"],))
            actions["l3_archived"] += 1
        if actions["l4_to_l3"] or actions["l4_archived"] or actions["l3_archived"]:
            self.export_markdown()
        return actions

    # ---------- 遗忘候选（只提议，不执行）----------
    def forget_candidates(self, *, layer: int | None = None, limit: int | None = None,
                          min_age_days: float | None = None) -> list[dict]:
        """算出「遗忘候选」——**只提议，不动数据**。

        作用域 L2/L3（L1 只精不降、L4 是会话暂存）。
        「使用」= hit_count（主动检索/读全文）；`inject_count` 高而 hit 为 0 的会被额外标注。

        双门槛，同时满足才入选：
          ① 相对：层内得分垫底（低于该层中位数；层内条数 < FORGET_MIN_LAYER_SIZE 时跳过）
          ② 绝对：距上次真正使用 >= min_age_days（默认 90 天）
        豁免：pin=1 / importance>=5 / 命中安全情感豁免词。

        建议动作：L2 → L3（降为情景记忆）；L3 → 归档（不再注入，仍可检索）。
        """
        now = datetime.now()
        age_floor = float(min_age_days if min_age_days is not None
                          else self.cfg.get("forget_min_age_days", FORGET_MIN_AGE_DAYS))
        big_enough = int(self.cfg.get("forget_min_layer_size", FORGET_MIN_LAYER_SIZE))
        layers = [int(layer)] if layer in (2, 3) else [2, 3]
        out: list[dict] = []

        for L in layers:
            rows = self._rows(
                "SELECT * FROM entries WHERE layer=? AND status='active'", (L,))
            if not rows:
                continue
            ages, hits = {}, {}
            for e in rows:
                ref = _parse_ts(e.get("last_hit_at")) or _parse_ts(e.get("updated_at")) or now
                ages[e["id"]] = max(0.0, (now - ref).total_seconds() / 86400.0)
                hits[e["id"]] = int(e.get("hit_count") or 0)
            p75 = sorted(hits.values())[int(len(hits) * 0.75)] if hits else 0
            scored = []
            for e in rows:
                use = min(1.0, hits[e["id"]] / max(1, p75))       # 使用度（层内相对）
                val = int(e.get("importance") or 3) / 5.0          # 价值
                fresh = 1.0 / (1.0 + ages[e["id"]] / 90.0)         # 新近度
                scored.append((0.5 * use + 0.3 * val + 0.2 * fresh, e))
            med = sorted(s for s, _ in scored)[len(scored) // 2] if scored else 0.0

            for s, e in scored:
                if self._is_exempt(e):
                    continue
                d = ages[e["id"]]
                if d < age_floor:
                    continue
                if len(rows) >= big_enough and s > med:           # 相对门槛：层内垫底
                    continue
                kind = "ed" if (int(e.get("inject_count") or 0) >= 5 and hits[e["id"]] == 0) else "old"
                reason = (f"注入 {int(e.get('inject_count') or 0)} 次却从未被用过"
                          if kind == "ed" else f"上次使用在 {int(d)} 天前")
                out.append({
                    "id": e["id"], "layer": L, "title": e["title"],
                    "importance": int(e.get("importance") or 3),
                    "hit_count": hits[e["id"]],
                    "inject_count": int(e.get("inject_count") or 0),
                    "age_days": int(d),
                    "score": round(s, 3), "layer_median": round(med, 3),
                    "suggest": "归档" if L == 3 else "L3",
                    "reason": reason, "kind": kind,
                })

        out.sort(key=lambda c: (c["score"], -c["age_days"]))
        return out[:int(limit or self.cfg.get("forget_candidate_limit", FORGET_CANDIDATE_LIMIT))]

    def demote(self, entry_id: int) -> dict | None:
        """执行一次降级：L2 → L3（加「降级」标签），L3 → 归档。返回动作说明。

        只降一级、不跳级；L1/L4 不受理（L1 只精不降、L4 是会话暂存）。
        豁免条目（pin / importance>=5 / 豁免词）拒绝执行。
        """
        e = self.get_entry(int(entry_id))
        if not e:
            return None
        if e["status"] != "active":
            return {"id": e["id"], "ok": False, "msg": f"#{e['id']} 不是活跃条目（{e['status']}）"}
        if self._is_exempt(e):
            return {"id": e["id"], "ok": False, "msg": f"#{e['id']} 命中豁免（pin / 重要度满级 / 安全情感词），拒绝降级"}
        L = int(e["layer"])
        if L == 2:
            self._exec(
                "UPDATE entries SET layer=3, updated_at=?,"
                " tags=CASE WHEN tags='' THEN '由L2降级' ELSE tags||',由L2降级' END WHERE id=?",
                (_now(), e["id"]))
            self.export_markdown()
            return {"id": e["id"], "ok": True, "msg": f"#{e['id']}《{e['title']}》L2 → L3（可检索，不再占重要记忆配额）"}
        if L == 3:
            self._exec("UPDATE entries SET status='archived', updated_at=? WHERE id=?", (_now(), e["id"]))
            self.export_markdown()
            return {"id": e["id"], "ok": True, "msg": f"#{e['id']}《{e['title']}》L3 → 归档（不再注入，仍可检索）"}
        return {"id": e["id"], "ok": False,
                "msg": f"#{e['id']} 在 L{L}：L1 只精不降、L4 是会话暂存，不参与降级链"}

    def set_pin(self, entry_id: int, on: bool = True) -> dict | None:
        """锁定/解锁一条记忆（pin=1 后永不被降级或归档）。"""
        e = self.get_entry(int(entry_id))
        if not e:
            return None
        self._exec("UPDATE entries SET pin=?, updated_at=? WHERE id=?",
                   (1 if on else 0, _now(), int(entry_id)))
        self.export_markdown()
        return {"id": int(entry_id), "pin": bool(on),
                "title": e["title"], "msg": f"#{entry_id}《{e['title']}》已{'锁定（永不降级）' if on else '解锁'}"}

    # ---------- 元信息 ----------
    def stats(self, scopes: list[str] | tuple[str, ...] | None = None) -> dict:
        scope_sql, scope_args = self._scope_sql(scopes)
        rows = self._rows("SELECT layer, status, COUNT(*) n, SUM(LENGTH(content)) chars"
                          " FROM entries WHERE 1=1" + scope_sql +
                          " GROUP BY layer, status", scope_args)
        out = {"L1": 0, "L2": 0, "L3": 0, "L4": 0, "archived": 0, "deleted": 0,
               "total": 0, "chars": {"L1": 0, "L2": 0, "L3": 0, "L4": 0},
               "summary_missing": {"L1": 0, "L2": 0, "L3": 0, "L4": 0}, "dedup_hits": self._dedup_hits}
        for r in rows:
            key = f"L{r['layer']}"
            out["total"] += r["n"]
            if r["status"] == "archived":
                out["archived"] += r["n"]
            elif r["status"] == "deleted":
                out["deleted"] += r["n"]
            elif key in out:
                out[key] += r["n"]
                out["chars"][key] += int(r["chars"] or 0)
        for lv in (1, 2, 3, 4):
            n = self._rows(
                "SELECT COUNT(*) n FROM entries WHERE layer=? AND status='active'"
                " AND COALESCE(summary,'')=''" + scope_sql,
                (lv,) + scope_args)[0]["n"]
            out["summary_missing"][f"L{lv}"] = n
        return out

    def workspace_counts(self) -> dict[str, int]:
        """各工作目录（含 global）的活跃记忆条数，供目录选择面板显示。"""
        rows = self._rows(
            "SELECT COALESCE(NULLIF(workspace,''),'global') ws, COUNT(*) n"
            " FROM entries WHERE status='active' GROUP BY ws"
        )
        return {str(r["ws"]): int(r["n"]) for r in rows}

    def workspace_scopes(self) -> list[str]:
        """库里出现过的全部作用域值（用于迁移历史脏数据）。"""
        rows = self._rows("SELECT DISTINCT workspace FROM entries WHERE workspace IS NOT NULL")
        return [str(r["workspace"]) for r in rows if str(r["workspace"] or "").strip()]

    def remap_workspaces(self, mapping: dict[str, str]) -> int:
        """把历史遗留的非规范作用域（如目录名）改写为规范绝对路径键。"""
        if not mapping:
            return 0
        n = 0
        with self._lock:
            for old, new in mapping.items():
                if not old or not new or old == new:
                    continue
                n += self._conn.execute(
                    "UPDATE entries SET workspace=? WHERE workspace=?", (new, old)
                ).rowcount
                n += self._conn.execute(
                    "UPDATE entries SET source_workspace=? WHERE source_workspace=?",
                    (new, old),
                ).rowcount
            self._conn.commit()
        if n:
            self.export_markdown()
        return n

    def scope_entry_count(self, scope: str) -> int:
        scope_sql, scope_args = self._scope_sql([scope])
        rows = self._rows("SELECT COUNT(*) n FROM entries WHERE status='active'" + scope_sql,
                          scope_args)
        return int(rows[0]["n"]) if rows else 0

    def recent_workspace_tasks(self, workspace: str, exclude_session: str | None = None,
                               limit: int = 2) -> list[dict]:
        """同目录其他会话的近期任务（L4），用于「会话 A 能看到会话 B 的记忆」。"""
        ws = normalize_scope(workspace)
        if ws == GLOBAL_SCOPE:
            return []
        rows = self._rows(
            "SELECT * FROM entries WHERE layer=4 AND status='active' AND workspace=?"
            " AND COALESCE(session_id,'')<>? ORDER BY updated_at DESC LIMIT ?",
            (ws, str(exclude_session or ""), max(1, int(limit))),
        )
        return rows

    def promote_entry(self, entry_id: int, target: str = GLOBAL_SCOPE,
                      min_importance: int = 4) -> dict:
        """把一条目录记忆提取到全局记忆（复制，不改原条目）。

        已在全局 / 目标作用域已有同主题条目时，只合并标签与重要度，不重复新增。
        """
        target = normalize_scope(target)
        e = self.get_entry(int(entry_id))
        if not e:
            return {"ok": False, "msg": f"未找到记忆 #{entry_id}"}
        src = normalize_scope(e.get("workspace"))
        if src == target:
            return {"ok": False, "id": int(entry_id), "msg": f"#{entry_id} 已在全局记忆中"}
        dup = self._find_duplicate(int(e["layer"]), e["title"], e["content"], workspace=target)
        if dup:
            imp = max(int(dup.get("importance") or 3), int(e.get("importance") or 3),
                      int(min_importance))
            self._exec(
                "UPDATE entries SET tags=?, importance=?, updated_at=? WHERE id=?",
                (self._merge_tags(dup.get("tags", ""), (e.get("tags") or "") + ",全局,来自工作目录"),
                 min(5, imp), _now(), dup["id"]),
            )
            self.export_markdown()
            return {"ok": True, "id": int(dup["id"]), "merged": True,
                    "msg": f"全局已有同主题记忆 #{dup['id']}，已合并标签与重要度"}
        imp = max(int(min_importance), int(e.get("importance") or 3), 4)
        nid = self.add_entry(
            layer=int(e["layer"]),
            title=e["title"],
            content=e["content"],
            summary=e.get("summary") or "",
            tags=(e.get("tags") or "") + ",全局,来自工作目录",
            importance=min(5, imp),
            session_id=e.get("session_id"),
            workspace=target,
            source_workspace=src,
            dedup=False,
        )
        return {"ok": True, "id": int(nid), "source_id": int(entry_id),
                "msg": f"已把 #{entry_id}《{e['title']}》提取到全局记忆 #{nid}"}

    def get_entry(self, entry_id: int) -> dict | None:
        """取单条全文（注入只给 summary，深入查看/改写时用这个）。"""
        rows = self._rows("SELECT * FROM entries WHERE id=?", (int(entry_id),))
        return rows[0] if rows else None

    def tags_overview(self, top: int = 40,
                      scopes: list[str] | tuple[str, ...] | None = None) -> list[dict]:
        """标签总览：各标签被多少条活跃记忆使用（可按作用域过滤）。"""
        from collections import Counter

        scope_sql, scope_args = self._scope_sql(scopes)
        cnt: Counter = Counter()
        for e in self._rows("SELECT tags FROM entries WHERE status='active'" + scope_sql,
                            scope_args):
            for t in (e["tags"] or "").split(","):
                t = t.strip()
                if t:
                    cnt[t] += 1
        return [{"tag": t, "count": n} for t, n in cnt.most_common(int(top))]

    # ---------- 导出 ----------
    def export_markdown(self) -> None:
        if not self.export_dir:
            return
        try:
            self.export_dir.mkdir(parents=True, exist_ok=True)
            titles = {1: "L1-常驻核心", 2: "L2-重要记忆", 3: "L3-情景记忆", 4: "L4-会话记忆"}
            for layer, fname in titles.items():
                rows = self.get_layer(layer)
                lines = [f"# {fname}", f"> 导出时间：{_now()}；共 {len(rows)} 条（活跃）", ""]
                if layer == 4:
                    rows_sorted = sorted(rows, key=lambda r: r["updated_at"], reverse=True)
                else:
                    rows_sorted = rows
                for e in rows_sorted:
                    tags = f"  [{e['tags']}]" if e["tags"] else ""
                    lock = "  🔒锁定" if int(e.get("pin") or 0) == 1 else ""
                    scope = e.get("workspace") or "global"
                    scope_txt = "全局" if scope == "global" else f"目录 {scope}"
                    src = e.get("source_workspace") or ""
                    src_txt = f" ｜ 来源 {src}" if src and src != "global" else ""
                    lines.append(f"## #{e['id']} {e['title']}{tags}{lock}")
                    lines.append(f"> 重要度 {e['importance']} ｜ {scope_txt}{src_txt}"
                                 f" ｜ 创建 {e['created_at']}"
                                 f" ｜ 被用 {e['hit_count']} 次 ｜ 被注入 {e.get('inject_count') or 0} 次")
                    lines.append("")
                    if (e.get("summary") or "").strip():
                        lines.append(f"**结论**：{e['summary']}")
                        lines.append("")
                        lines.append("<details><summary>正文</summary>")
                        lines.append("")
                        lines.append(e["content"])
                        lines.append("")
                        lines.append("</details>")
                    else:
                        lines.append(e["content"])
                    lines.append("")
                (self.export_dir / f"{fname}.md").write_bytes("\n".join(lines).encode("utf-8"))
        except Exception:
            pass  # 导出失败不影响主流程


# ---------- 单体缓存 ----------
_STORES: dict[str, MemoryStore] = {}
_STORES_LOCK = threading.Lock()


def get_store(db_path: Path, export_dir: Path | None = None, cfg: dict | None = None) -> MemoryStore:
    key = str(Path(db_path).resolve())
    with _STORES_LOCK:
        if key not in _STORES:
            _STORES[key] = MemoryStore(Path(db_path), export_dir, cfg)
        return _STORES[key]
