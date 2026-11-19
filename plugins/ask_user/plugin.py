# -*- coding: utf-8 -*-
"""ask_user 插件：把提问「做进主界面」（不弹系统窗口、不在前端页面里另开弹层）。

通道（三段式，全程走主界面，不像系统弹窗那样另起窗口）：
  1) 本工具在 <DATA_ROOT>/.claw_ask/<id>.json 写一条 status=pending 的提问
     （<DATA_ROOT> = 安装目录，即 exe 所在目录）；
  2) 主界面每 1.2 秒轮询 GET /api/ask/pending 拿到它，在聊天区浮出一张卡片，用户点选或自拟；
  3) 用户提交后 POST /api/ask/answer 写回答案，本工具（在后台线程里等待）读到后返回给模型，
     同一轮对话无缝继续 —— 会话不会中断。

兜底：界面没开/用户不理/超时 → 自动退回「文字列选项」，把清单交给模型贴进回复，
      所以任何情况下都不会卡住会话。用户点「忽略」则同样退回文字版。

参数：
  question     要问的问题（必填）
  options      2-6 个候选，字符串或 {"label":"选项","desc":"简短说明"}
  allow_custom 是否允许自拟答案（默认 true）
  timeout      Interface 卡片等待秒数（默认 120，0=一直等，上限 900）
  title        预留字段（当前版本不使用）

返回：形如
  「用户已作答：重试 3 次再报错」               ← 界面点选/自拟
  「用户忽略/超时，未作答。请在回复里用文字列出下面的选项让用户选：…」 ← 退回文字版
"""
from __future__ import annotations

import json
import os
import random
import sys
import time
from pathlib import Path

ASK_DIRNAME = ".claw_ask"


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _data_root(cfg: dict) -> Path:
    """提问文件的落点：优先框架给的 _project_root（即安装目录 DATA_ROOT）。"""
    root = str(cfg.get("_project_root") or "").strip()
    if root and os.path.isdir(root):
        return Path(root)
    if getattr(sys, "frozen", False):          # 插件不在冻结进程里，通常走不到
        return Path(sys.executable).resolve().parent
    return Path(os.getcwd())


def _ask_dir(cfg: dict) -> Path:
    d = _data_root(cfg) / ASK_DIRNAME
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return d


def _question_file(cfg: dict, qid: str) -> Path:
    safe = "".join(ch for ch in str(qid) if ch.isalnum() or ch in "-_")[:64]
    return _ask_dir(cfg) / f"{safe}.json"


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write(path: Path, obj: dict) -> None:
    try:
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        pass


def _norm_options(raw) -> list:
    out: list = []
    if isinstance(raw, dict):
        raw = [raw]
    if isinstance(raw, str):
        raw = [s.strip() for s in raw.replace("；", ";").replace(";", "\n").splitlines()]
    if isinstance(raw, (list, tuple)):
        for it in raw:
            if isinstance(it, dict):
                label = str(it.get("label") or it.get("text") or it.get("value") or "").strip()
                desc = str(it.get("desc") or it.get("description") or it.get("detail") or "").strip()
            else:
                label, desc = str(it).strip(), ""
            if not label:
                continue
            if len(label) > 1 and label[0].isdigit() and label[1] in ".、)）":
                label = label[2:].strip()
            if label:
                out.append({"label": label, "desc": desc})
    return out[:6]


def _text_fallback(question: str, options: list, allow_custom: bool, why: str) -> str:
    lines = [f"{why}请在回复里用文字列出下面的选项让用户选（不要瞎猜）：", "",
             f"需要你确认一下：{question}", ""]
    for i, o in enumerate(options, 1):
        line = f"  {i}) {o['label']}"
        if o["desc"]:
            line += f" —— {o['desc']}"
        lines.append(line)
    if allow_custom:
        lines.append("")
        lines.append("直接回我一个序号（比如 2），或者自己写一句你的想法都行。" if options
                     else "直接说你的想法就行。")
    return "\n".join(lines)


def handler(args: dict, workdir: str, cfg: dict) -> str:
    question = str(args.get("question") or "").strip()
    if not question:
        return "错误：缺少 question（要问用户的问题）。"
    options = _norm_options(args.get("options"))
    ac = args.get("allow_custom", True)
    allow_custom = str(ac).strip().lower() not in ("false", "0", "no", "")

    try:
        timeout = int(float(str(args.get("timeout") if args.get("timeout") is not None else 120).strip()))
    except Exception:
        timeout = 120
    timeout = max(0, min(timeout, 900))

    # 无人监管模式：用户不在场，不等待作答，直接让模型自行决断（不落盘、不阻塞）
    if bool(cfg.get("_unattended")):
        return (
            "无人监管模式：用户当前不在场，本工具不等待作答。请自行选择最稳妥、可回退的方案继续执行，"
            "并把你的假设写进最终汇报。\n"
            "（原问题：%s）" % question
        )

    # 主界面没在轮询（窗口没开/别的客户端在用）：立刻退回文字版，避免白等超时
    if cfg.get("_ask_ui_active") is False:
        return _text_fallback(question, options, allow_custom,
                              "主界面当前没有在接收提问（可能未打开），已直接改为文字版。")

    qid = str(args.get("id") or "").strip() or \
        ("q%s%03d" % (time.strftime("%y%m%d-%H%M%S"), random.randint(0, 999)))
    path = _question_file(cfg, qid)
    if not str(path.parent).strip():
        return _text_fallback(question, options, allow_custom, "无法写入提问文件。")

    rec = {
        "id": qid,
        "question": question,
        "options": options,
        "allow_custom": allow_custom,
        "status": "pending",
        "created_ts": time.time(),
        "created_at": _now(),
        "ttl": timeout if timeout > 0 else 900,
        "source": "ask_user",
    }
    _write(path, rec)
    if _read(path).get("status") != "pending":
        return _text_fallback(question, options, allow_custom,
                              "没能把提问送到主界面（文件写入失败）。")

    # 等待主界面回传：0 = 一直等
    t0 = time.time()
    deadline = t0 + timeout if timeout > 0 else None
    while True:
        d = _read(path)
        st = str(d.get("status") or "")
        if st == "answered":
            ans = str(d.get("answer") or "").strip()
            if not ans:
                ans = "(空)"
            return "用户已作答：%s\n（提交时间 %s）" % (ans, d.get("answered_at") or _now())
        if st in ("skipped", "expired"):
            why = "用户点了忽略。" if st == "skipped" else "用户没有在时限内作答。"
            return _text_fallback(question, options, allow_custom, why)
        if deadline is not None and time.time() > deadline:
            return _text_fallback(question, options, allow_custom,
                                  "用户没有在 %d 秒内作答。" % timeout)
        time.sleep(0.4)


def register(api):
    api.register_tool(
        {
            "type": "function",
            "function": {
                "name": "ask_user",
                "description": (
                    "需求模糊、缺关键信息、或有多个可行方案难以猜测时用它："
                    "在主界面（聊天区）浮出一张选择卡片，用户点选项或自拟一句话作答，"
                    "答案直接回到你这里继续同一轮对话（会话不中断）。"
                    "question 是要问的问题，options 是 2-6 个候选"
                    "（字符串，或 {\"label\":\"选项\",\"desc\":\"简短说明\"}），allow_custom=true 允许自拟。"
                    "用户忽略或超时则退回文字版：返回一段可直接贴进回复的「问题+编号选项」清单，"
                    "照它用文字再问一次即可。只用于真正需要用户拍板的岔路口，能自己判断就不要问。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string", "description": "要问用户的问题（一句话，越具体越好）"},
                        "options": {
                            "type": "array",
                            "description": "候选项（2-6 个）。元素可为字符串，或 {\"label\":\"选项\",\"desc\":\"说明\"}",
                            "items": {"type": "string"},
                        },
                        "allow_custom": {"type": "boolean", "description": "是否允许用户自拟答案（默认 true）"},
                        "timeout": {"type": "integer", "description": "界面卡片等待秒数（默认 120，0=一直等，上限 900）"},
                        "id": {"type": "string", "description": "本次提问 id（一般不用填）"},
                    },
                    "required": ["question"],
                },
            },
        },
        handler,
    )
