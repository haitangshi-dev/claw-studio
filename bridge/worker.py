# -*- coding: utf-8 -*-
"""把微信消息接进 Claw Agent 本体。

流程：inbox/*.json（微信入站）-> POST /api/chat（Claw Agent 流式）-> outbox/*.reply.json -> 微信回信

- 常驻轮询，不设时长上限
- 一条一条串行处理，避免并发会话串话
- 会话连续：固定 session_id，上下文由 Claw Agent 侧维护
- 全程只读文件 + 出站 HTTPS，不开端口
"""
import json, os, re, sys, threading, time, uuid, urllib.request, urllib.error
from pathlib import Path

from single_instance import acquire as acquire_lock

ROOT = Path(__file__).resolve().parent
INBOX = ROOT / "inbox"; DONE = INBOX / "done"
OUTBOX = ROOT / "outbox"
LOGS = ROOT / "logs"
STATE = ROOT / "state"
ALIVE = STATE / "worker-alive.json"       # 心跳（设置面板据此判断存活）
for d in (INBOX, DONE, OUTBOX, LOGS, STATE): d.mkdir(parents=True, exist_ok=True)

API = os.environ.get("CLAW_API", "http://127.0.0.1:8090/api/chat")
SESSION = os.environ.get("CLAW_SESSION", "wechat")
PERMISSION = os.environ.get("CLAW_PERMISSION", "standard")
# 强制走云端模型：绝不使用本地模型（本地 2B 模型约 1.4 tokens/s，会卡死会话）
MODEL = os.environ.get("CLAW_MODEL") or "qoder:Auto"   # 别再用已下线的 wb: 通道
POLL = float(os.environ.get("WECHAT_WORKER_POLL", "3"))
TIMEOUT = float(os.environ.get("CLAW_TIMEOUT", "1800"))
MAXLEN = int(os.environ.get("WECHAT_MAXLEN", "3500"))

def log(msg):
    line = "%s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    try:
        with open(LOGS / "worker.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

def clean(text):
    """微信不渲染 Markdown，做轻量降噪。"""
    t = text or ""
    t = re.sub(r"```[a-zA-Z0-9_+-]*\n?", "", t)      # 去代码围栏
    t = t.replace("```", "")
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", t)            # 去加粗
    t = re.sub(r"^\s{0,3}#{1,6}\s*", "", t, flags=re.M)
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    if len(t) > MAXLEN:
        t = t[:MAXLEN] + "\n…（回复过长已截断）"
    return t

def ask_agent(text, workdir=None):
    body = {
        "messages": [{"role": "user", "content": text}],
        "session_id": SESSION,
        "permission": PERMISSION,
        "model": MODEL,          # 云端模型，本地引擎不会被拉起
    }
    if workdir: body["workdir"] = workdir
    req = urllib.request.Request(
        API, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    parts, err = [], None
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if not line.startswith("data: "):
                continue
            try:
                ev = json.loads(line[6:])
            except Exception:
                continue
            t = ev.get("type")
            if t == "content" and ev.get("text"):
                parts.append(ev["text"])
            elif t == "error":
                err = ev.get("message") or "未知错误"
    out = "".join(parts).strip()
    if not out and err:
        raise RuntimeError(err)
    return out, err

def reply(name, to_user, ctx_token, text):
    payload = {
        "to_user_id": to_user,
        "context_token": ctx_token or "",
        "text": text,
        "from_user_id": "",
    }
    tmp = OUTBOX / (name[:-5] + ".reply.json.tmp")
    fin = OUTBOX / (name[:-5] + ".reply.json")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, fin)

def handle(path: Path):
    try:
        msg = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        log("跳过坏文件 %s: %s" % (path.name, e))
        try: os.replace(path, DONE / path.name)
        except Exception: pass
        return
    text = (msg.get("text") or "").strip()
    to_user = msg.get("from_user_id") or msg.get("to_user_id")
    ctx = msg.get("context_token")
    if not text:
        log("空文本消息，忽略 %s" % path.name)
        os.replace(path, DONE / path.name)
        return
    log("收到微信消息: %r" % text[:120])
    try:
        answer, err = ask_agent(text)
    except Exception as e:
        log("调用本体失败: %s" % e)
        answer = "（本机 Claw Agent 暂时没接上：%s）" % e
    if not answer:
        answer = "（没有产出回复%s）" % ("：" + err if err else "")
    reply(path.name, to_user, ctx, clean(answer))
    log("已写回复 %s（%d 字）" % (path.name, len(answer)))
    os.replace(path, DONE / path.name)

def heartbeat_loop():
    """独立线程写心跳：ask_agent 卡住时也不会让看门狗误判 worker 已死。"""
    while True:
        try:
            tmp = ALIVE.with_name("%s.%d.tmp" % (ALIVE.name, os.getpid()))
            tmp.write_text(json.dumps({"ts": time.time(), "pid": os.getpid()}), encoding="utf-8")
            os.replace(tmp, ALIVE)
        except Exception:
            pass
        time.sleep(POLL)

def main():
    log("worker 启动：API=%s model=%s session=%s permission=%s 轮询=%ss（无时长上限，不用本地模型）"
        % (API, MODEL, SESSION, PERMISSION, POLL))
    threading.Thread(target=heartbeat_loop, daemon=True).start()
    while True:
        try:
            files = sorted(INBOX.glob("*.json"))
            for f in files:
                handle(f)
        except KeyboardInterrupt:
            log("worker 收到中断，退出"); return 0
        except Exception as e:
            log("循环异常（继续）: %s" % e)
        time.sleep(POLL)

if __name__ == "__main__":
    if not acquire_lock("worker"):
        log("已有 worker 在运行，本实例退出")
        sys.exit(0)
    sys.exit(main())
