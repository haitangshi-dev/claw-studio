# -*- coding: utf-8 -*-
"""ask_user 技能：把提问「做进主界面」——聊天区浮出选择卡片，用户点选，答案回到调用者。

与插件 ask_user 走**同一条通道**（提问文件 + /api/ask 端点），区别只是入口：
  * 插件  = 模型直接调用工具时用（首选）
  * 本技能 = 用户说「用技能问」「做成技能」或脚本流程里需要拍板时用

通道：
  1) 在 <DATA_ROOT>/.claw_ask/<id>.json 写一条 status=pending 的提问
  2) 主界面每 1.2 秒轮询 /api/ask/pending，浮出卡片（点选 / 自拟 / 忽略）
  3) 用户提交 → POST /api/ask/answer 写回；本脚本轮询读到后把答案 print 出来

兜底：后端不可用（旧版/没启动）、用户点忽略、等超时 → 退回「文字列选项」清单，
      调用者把清单贴进回复即可，不会卡住。

入参（stdin JSON）：
  question      要问的问题（必填）
  options       2-6 个候选：字符串，或 {"label":"选项","desc":"简短说明"}
  allow_custom  是否允许自拟（默认 true）
  timeout       等界面作答的秒数（默认 90，0=一直等，上限 600；注意 skill_run 本身约 120s 上限）
  port          后端端口（默认 8090）
  data_root     数据根目录（默认自动探测：CLAW_DATA_ROOT → CLAW_SKILL_DIR 上两级 → 安装目录）

输出（stdout，纯文本）：
  用户已作答：重试 3 次再报错
  或  用户点了忽略。请把下面的清单贴给用户选：…
"""
from __future__ import annotations

import json
import os
import random
import sys
import time
import urllib.request
from pathlib import Path

ASK_DIRNAME = ".claw_ask"
DEFAULT_PORT = 8090
DEFAULT_TIMEOUT = 90
MAX_TIMEOUT = 600


# ------------------------------------------------------------------ 基础工具
def _norm_options(raw) -> list:
    """把各种写法的候选项归一为 [{'label':..,'desc':..}]，最多 6 个。"""
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
            # 容忍「1) xxx」这种自带序号的写法，去掉前缀
            if len(label) > 1 and label[0].isdigit() and label[1] in ".、)）":
                label = label[2:].strip()
            if label:
                out.append({"label": label, "desc": desc})
    return out[:6]


def _text_fallback(question: str, options: list, allow_custom: bool, why: str) -> str:
    """退回文字列选项：调用者把这段贴进回复里让用户回序号。"""
    lines = [f"{why}", "", f"需要你确认一下：{question}", ""]
    for i, o in enumerate(options, 1):
        line = f"  {i}) {o['label']}"
        if o["desc"]:
            line += f" —— {o['desc']}"
        lines.append(line)
    if options:
        lines.append("")
    if allow_custom:
        lines.append("直接回我一个序号（比如 2），或者自己写一句你的想法都行。" if options
                     else "直接说你的想法就行。")
    else:
        lines.append("回我一个序号即可。")
    return "\n".join(lines).strip()


def _data_root(args: dict) -> Path:
    """探测数据根目录（即 .claw_ask 的落点；打包版 = exe 所在目录）。"""
    for k in ("data_root", "root", "project_root"):
        v = str(args.get(k) or "").strip()
        if v and Path(v).is_dir():
            return Path(v).resolve()
    env = str(os.environ.get("CLAW_DATA_ROOT") or "").strip()
    if env and Path(env).is_dir():
        return Path(env).resolve()
    sd = str(os.environ.get("CLAW_SKILL_DIR") or "").strip()
    if sd:
        try:
            cand = Path(sd).resolve().parents[1]        # <ROOT>/skills/<name> → <ROOT>
            if (cand / "skills").is_dir():
                return cand
        except Exception:
            pass
    la = os.environ.get("LOCALAPPDATA")
    if la:
        cand = Path(la) / "Programs" / "ClawAgent"
        if cand.is_dir():
            return cand
    return Path(os.getcwd())


def _http_json(url: str, payload: dict | None = None, timeout: float = 4.0):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data, headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write(path: Path, obj: dict) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
        return True
    except Exception:
        return False


# ------------------------------------------------------------------ 主流程
def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    raw = ""
    try:
        raw = sys.stdin.read()
    except Exception:
        raw = ""
    args: dict = {}
    if (raw or "").strip():
        try:
            a = json.loads(raw)
            args = a if isinstance(a, dict) else {}
        except Exception:
            args = {"question": raw.strip()}

    question = str(args.get("question") or "").strip()
    if not question:
        print("错误：缺少 question（要问用户的问题）。")
        return
    options = _norm_options(args.get("options"))
    ac = args.get("allow_custom", True)
    allow_custom = str(ac).strip().lower() not in ("false", "0", "no", "")

    try:
        timeout = int(float(str(args.get("timeout") if args.get("timeout") is not None
                                 else DEFAULT_TIMEOUT).strip()))
    except Exception:
        timeout = DEFAULT_TIMEOUT
    timeout = max(0, min(timeout, MAX_TIMEOUT))

    try:
        port = int(str(args.get("port") or DEFAULT_PORT).strip())
    except Exception:
        port = DEFAULT_PORT
    base = f"http://127.0.0.1:{port}"

    root = _data_root(args)

    # ① 主界面在不在？（后端有 /api/ask/pending 才说明支持卡片通道）
    try:
        _http_json(base + "/api/ask/pending", timeout=3)
    except Exception as e:
        print(_text_fallback(question, options, allow_custom,
                             f"主界面没连上（{base} 无响应：{type(e).__name__}）。"))
        return

    # ② 写提问文件（等界面来取）
    qid = str(args.get("id") or "").strip() or \
        ("s%s%03d" % (time.strftime("%y%m%d-%H%M%S"), random.randint(0, 999)))
    path = root / ASK_DIRNAME / f"{qid}.json"
    rec = {
        "id": qid,
        "question": question,
        "options": options,
        "allow_custom": allow_custom,
        "status": "pending",
        "created_ts": time.time(),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "ttl": timeout if timeout > 0 else MAX_TIMEOUT,
        "source": "skill:ask_user",
    }
    if not _write(path, rec) or _read(path).get("status") != "pending":
        print(_text_fallback(question, options, allow_custom,
                             f"提问文件写入失败（{path.parent} 不可写）。"))
        return

    # ③ 等界面回传
    t0 = time.time()
    deadline = t0 + timeout if timeout > 0 else None
    while True:
        d = _read(path)
        st = str(d.get("status") or "")
        if st == "answered":
            ans = str(d.get("answer") or "").strip() or "(空)"
            print(f"用户已作答：{ans}\n（提交时间 {d.get('answered_at') or time.strftime('%Y-%m-%d %H:%M:%S')}）")
            return
        if st in ("skipped", "expired"):
            why = "用户点了忽略。" if st == "skipped" else "用户没有在时限内作答。"
            print(_text_fallback(question, options, allow_custom, why))
            return
        if deadline is not None and time.time() > deadline:
            print(_text_fallback(question, options, allow_custom,
                                 f"用户没有在 {timeout} 秒内作答。"))
            return
        time.sleep(0.3)


if __name__ == "__main__":
    main()
