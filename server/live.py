# -*- coding: utf-8 -*-
"""实时活动广播（给桌面宠物看）。

背景：桌面宠物要显示「正在思考 / 正在调用 xxx 工具 / 正在给出答案」并且
把回答正文实时映射过去。前端 SSE 只到浏览器，宠物读不到；所以这里在
agent_loop 每次产生事件时，把当前状态写成一个极小的 JSON：

    logs/live.json = {
        "ts":     1770000000.12,   # 写入时刻（宠物据此判断「新鲜」）
        "phase":  "thinking" | "tooling" | "replying" | "idle" | "error",
        "text":   "正在调用 run_shell",   # 宠物头顶那行字
        "tool":   "run_shell",            # 正在调用的工具名（可空）
        "ok":     true,                   # 工具是否成功（phase=tooling 时有意义）
        "body":   "……回答正文（尾部 600 字）"   # phase=replying 时给宠物滚动显示
    }

设计要点
--------
* 节流写盘：模型流式输出每秒几十个 delta，不能每个都写文件。
  同一 phase 下最快 LIVE_MIN_INTERVAL 秒写一次；phase 变化立刻写。
* 原子替换：先写临时文件再 os.replace，宠物不会读到半个 JSON。
* 永不抛错：宠物是附加功能，绝不能因为它影响正经对话。
* 进程退出时清一次（写 idle），避免宠物一直停在「正在回答」。
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time

LIVE_MIN_INTERVAL = 0.25      # 同一 phase 最快写盘间隔（秒）
BODY_KEEP = 600               # 正文只留尾部这么多字（宠物窗口小，够用）

_lock = threading.Lock()
_last = {'ts': 0.0, 'phase': '', 'body_len': 0}

# 崩溃/强杀会残留 .live-*.tmp（原子替换只保证不读到半个 JSON，不负责收尸）
_STALE_TMP_SECONDS = 3600


def _cleanup_stale_tmp(log_dir: str) -> None:
    try:
        now = time.time()
        for name in os.listdir(log_dir):
            if not (name.startswith('.live-') and name.endswith('.tmp')):
                continue
            p = os.path.join(log_dir, name)
            try:
                if now - os.path.getmtime(p) > _STALE_TMP_SECONDS:
                    os.remove(p)
            except OSError:
                pass
    except OSError:
        pass


def _path(root: str | None = None) -> str:
    base = root or os.path.join(os.path.expanduser('~'), '.claw-live')
    return os.path.join(base, 'live.json')


class LiveActivity:
    """一次对话期间的实时活动；run_agent 每轮对话建一个即可。"""

    def __init__(self, log_dir: str, min_interval: float = LIVE_MIN_INTERVAL):
        self.path = os.path.join(log_dir, 'live.json')
        self.min_interval = min_interval
        try:
            os.makedirs(log_dir, exist_ok=True)
        except Exception:
            pass
        _cleanup_stale_tmp(log_dir)
        self._body = ''
        self._phase = 'idle'
        self._text = ''
        self._tool = ''
        self._ok = True
        self._last_write = 0.0

    # ---------------------------------------------------------------- 写盘
    def _write(self, force: bool = False):
        now = time.time()
        if not force and (now - self._last_write) < self.min_interval:
            return
        self._last_write = now
        rec = {
            'ts': now,
            'phase': self._phase,
            'text': self._text,
            'tool': self._tool,
            'ok': self._ok,
            'body': self._body[-BODY_KEEP:],
        }
        tmp = None
        try:
            d = os.path.dirname(self.path)
            fd, tmp = tempfile.mkstemp(dir=d, prefix='.live-', suffix='.tmp')
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(rec, f, ensure_ascii=False)
            # Windows 上宠物读着 live.json 时 os.replace 可能短暂 PermissionError：
            # 重试几次再放弃，避免 tmp 越积越多、live 状态也不再刷新（实测残留过 200+ 个）
            for attempt in range(3):
                try:
                    os.replace(tmp, self.path)
                    tmp = None
                    break
                except PermissionError:
                    if attempt < 2:
                        time.sleep(0.02)
        except Exception:
            pass
        finally:
            if tmp:
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    # ---------------------------------------------------------------- 状态
    def thinking(self, text: str = '正在思考'):
        self._phase, self._text, self._tool = 'thinking', text, ''
        self._write(force=True)

    def tool(self, name: str):
        """正在调用某个工具"""
        self._phase = 'tooling'
        self._tool = name
        self._text = '正在调用 %s' % (name or '工具')
        self._write(force=True)

    def tool_done(self, name: str, ok: bool = True, seconds: float | None = None):
        self._phase = 'tooling'
        self._tool = name
        self._ok = bool(ok)
        t = '已调用 %s' % (name or '工具')
        if seconds is not None:
            t += '（%.1fs）' % seconds
        self._text = t if ok else ('%s 出错' % (name or '工具'))
        self._write(force=True)

    def append_answer(self, piece: str = ''):
        """正在给出答案：把正文增量累积下来（节流写盘）"""
        if piece:
            self._body += piece
        # phase 变化（思考/工具 → 作答）必须立刻写，否则会被节流吞掉，
        # 宠物就看不到「正在给出答案」这一态（实测踩过）
        changed = (self._phase != 'replying')
        self._phase = 'replying'
        self._text = '正在给出答案'
        self._tool = ''
        self._write(force=changed)

    def notice(self, text: str):
        self._phase = 'thinking'
        self._text = str(text or '')[:120]
        self._tool = ''
        self._write(force=True)

    def error(self, text: str):
        self._phase = 'error'
        self._text = str(text or '')[:120]
        self._tool = ''
        self._write(force=True)

    def done(self):
        self._phase = 'idle'
        self._text = '待机中'
        self._tool = ''
        self._body = ''
        self._write(force=True)

    def flush(self):
        self._write(force=True)


def clear(log_dir: str):
    """把活动状态置为 idle（进程退出/对话结束时调）"""
    try:
        la = LiveActivity(log_dir)
        la.done()
    except Exception:
        pass
