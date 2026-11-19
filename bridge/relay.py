#!/usr/bin/env python3
"""Pure-standard-library WeChat iLink file bridge.

Modes:
  login   Request QR login and store credentials under secure/.
  daemon  Long-poll inbound messages and drain outbox replies.
  status  Print a secret-free local health summary.

This is a reference implementation. Tencent's upstream client/protocol may change;
see references/protocol.md before adapting it for production.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import random
import shutil
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from single_instance import acquire as acquire_lock

DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com"
CHANNEL_VERSION = os.environ.get("ILINK_CHANNEL_VERSION", "2.4.9")
BOT_AGENT = os.environ.get("ILINK_BOT_AGENT", "wechat-ilink-agent-bridge/0.1")
ROOT = Path(os.environ.get("WECHAT_BRIDGE_HOME", "~/workspace/wechat-bridge")).expanduser()
INBOX = ROOT / "inbox"
OUTBOX = ROOT / "outbox"
SECURE = ROOT / "secure"
STATE = ROOT / "state"
TOKEN_FILE = SECURE / "token.json"
BINDING_FILE = SECURE / "binding.json"
CURSOR_FILE = STATE / "cursor.json"
AUTH_FILE = STATE / "auth.json"
MAX_SEND_ATTEMPTS = 3            # 单条消息最多尝试几次
MAX_QUEUE_AGE_MS = 24 * 3600 * 1000  # 排队超过一天就判定发不出去
ALIVE_FILE = STATE / "relay-alive.json"   # 心跳（供设置面板做毫秒级状态探测）
_heartbeat_stop = threading.Event()


def heartbeat_loop() -> None:
    """独立线程写心跳：长轮询/发送卡住时也不让看门狗误判 relay 已死。"""
    while not _heartbeat_stop.wait(5):
        try:
            atomic_json(ALIVE_FILE, {"ts": time.time(), "pid": os.getpid()})
        except Exception:
            pass


def ensure_layout() -> None:
    for path in (INBOX, INBOX / "done", INBOX / "ignored", OUTBOX, OUTBOX / "sent",
                 OUTBOX / "bad", OUTBOX / "expired", OUTBOX / "merged", SECURE, STATE):
        path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(SECURE, 0o700)
    except OSError:
        pass


def atomic_json(path: Path, data: Any, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    if mode is not None:
        try:
            os.chmod(path, mode)
        except OSError:
            pass


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def random_uin() -> str:
    raw = str(random.SystemRandom().randrange(0, 2**32)).encode("ascii")
    return base64.b64encode(raw).decode("ascii")


def encode_client_version(version: str) -> str:
    try:
        major, minor, patch = (int(x) for x in version.split(".")[:3])
        value = ((major & 0xFF) << 16) | ((minor & 0xFF) << 8) | (patch & 0xFF)
        return str(value)
    except Exception:
        return "0"


def app_headers() -> dict[str, str]:
    return {
        "iLink-App-Id": "bot",
        "iLink-App-ClientVersion": encode_client_version(CHANNEL_VERSION),
    }


def post_headers(token: str | None = None) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "X-WECHAT-UIN": random_uin(),
        **app_headers(),
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def request_json(method: str, url: str, *, headers: dict[str, str] | None = None, body: Any = None, timeout: float = 45.0) -> dict[str, Any]:
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = resp.read()
    if not payload:
        return {}
    value = json.loads(payload.decode("utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("Expected JSON object response")
    return value


def base_info() -> dict[str, str]:
    return {"channel_version": CHANNEL_VERSION, "bot_agent": BOT_AGENT}


def explicit_error(resp: dict[str, Any]) -> int:
    for key in ("ret", "errcode"):
        value = resp.get(key)
        if isinstance(value, int) and value != 0:
            return value
    return 0


def login() -> int:
    ensure_layout()
    qr_url = f"{DEFAULT_BASE_URL}/ilink/bot/get_bot_qrcode?bot_type=3"
    resp = request_json("POST", qr_url, headers=post_headers(), body={"local_token_list": []}, timeout=20)
    qrcode = resp.get("qrcode")
    content = resp.get("qrcode_img_content")
    if not qrcode or not content:
        raise RuntimeError(f"QR response missing required fields; keys={sorted(resp.keys())}")

    print("QR login payload received.")
    print("Open/render the following qrcode_img_content in the trusted user interface:")
    print(content)
    print("The bot token will not be printed.")

    deadline = time.time() + 300
    poll_base = DEFAULT_BASE_URL
    verify_code: str | None = None
    while time.time() < deadline:
        params = {"qrcode": qrcode}
        if verify_code:
            params["verify_code"] = verify_code
        query = urllib.parse.urlencode(params)
        poll_url = f"{poll_base}/ilink/bot/get_qrcode_status?{query}"
        try:
            status_resp = request_json("GET", poll_url, headers=app_headers(), timeout=15)
        except Exception:
            time.sleep(2)
            continue

        status = status_resp.get("status")
        if status == "confirmed":
            token = status_resp.get("bot_token")
            if not token:
                raise RuntimeError("Login confirmed without bot_token")
            baseurl = status_resp.get("baseurl") or DEFAULT_BASE_URL
            token_data = {
                "bot_token": token,
                "baseurl": baseurl,
                "ilink_bot_id": status_resp.get("ilink_bot_id"),
                "ilink_user_id": status_resp.get("ilink_user_id"),
            }
            atomic_json(TOKEN_FILE, token_data, 0o600)
            if status_resp.get("ilink_user_id") and not BINDING_FILE.exists():
                atomic_json(
                    BINDING_FILE,
                    {
                        "bound_user_id": status_resp.get("ilink_user_id"),
                        "bot_user_id": status_resp.get("ilink_bot_id"),
                        "source": "qr_login",
                    },
                    0o600,
                )
            atomic_json(AUTH_FILE, {"status": "ok", "updated_at": int(time.time())}, 0o600)
            print("Login confirmed. Credentials saved under secure/ (token not displayed).")
            return 0
        if status == "expired":
            print("QR code expired. Run login again.", file=sys.stderr)
            return 2
        if status == "scaned_but_redirect" and status_resp.get("redirect_host"):
            host = str(status_resp["redirect_host"]).rstrip("/")
            if not host.startswith("http"):
                host = "https://" + host
            poll_base = host
        if status == "need_verifycode":
            if not sys.stdin.isatty():
                print("Verification code required. Re-run login interactively to enter it.", file=sys.stderr)
                return 3
            verify_code = input("WeChat verification code: ").strip() or None
        if status == "verify_code_blocked":
            print("Verification code attempts are blocked; request a fresh QR code.", file=sys.stderr)
            return 2
        time.sleep(2)

    print("QR login timed out after 5 minutes.", file=sys.stderr)
    return 2


def load_credentials() -> dict[str, Any]:
    data = read_json(TOKEN_FILE)
    if not isinstance(data, dict) or not data.get("bot_token"):
        raise FileNotFoundError("Not logged in. Run: relay.py login")
    return data


def bind_or_check(msg: dict[str, Any]) -> bool:
    from_user = msg.get("from_user_id")
    to_user = msg.get("to_user_id")
    if not from_user:
        return False
    binding = read_json(BINDING_FILE)
    if not isinstance(binding, dict):
        if os.environ.get("WECHAT_BRIDGE_AUTO_BIND_FIRST") != "1":
            return False
        # Compatibility mode for a private/single-user deployment only.
        binding = {"bound_user_id": from_user, "bot_user_id": to_user, "source": "first_message"}
        atomic_json(BINDING_FILE, binding, 0o600)
        return True
    return binding.get("bound_user_id") == from_user


def extract_text(msg: dict[str, Any]) -> str:
    parts: list[str] = []
    for item in msg.get("item_list") or []:
        if not isinstance(item, dict) or item.get("type") != 1:
            continue
        text_item = item.get("text_item") or {}
        text = text_item.get("text") if isinstance(text_item, dict) else None
        if isinstance(text, str) and text:
            parts.append(text)
    return "\n".join(parts)


def ingest_messages(msgs: list[Any]) -> int:
    accepted = 0
    for raw in msgs:
        if not isinstance(raw, dict):
            continue
        if not bind_or_check(raw):
            name = f"{int(time.time()*1000)}-{uuid.uuid4().hex[:8]}.json"
            atomic_json(INBOX / "ignored" / name, raw)
            continue
        name = f"{int(time.time()*1000)}-{uuid.uuid4().hex[:8]}.json"
        normalized = {
            "from_user_id": raw.get("from_user_id"),
            "to_user_id": raw.get("to_user_id"),
            "context_token": raw.get("context_token"),
            "text": extract_text(raw),
            "received_at": int(time.time()),
            "raw": raw,
        }
        atomic_json(INBOX / name, normalized)
        accepted += 1
    return accepted


def persist_client_id(path: Path, payload: dict[str, Any]) -> str:
    client_id = payload.get("client_id")
    if isinstance(client_id, str) and client_id:
        return client_id
    client_id = f"bridge-{uuid.uuid4().hex[:16]}"
    payload["client_id"] = client_id
    atomic_json(path, payload)
    return client_id


def merge_pending() -> int:
    """把同一收件人的多条待发合并成一条摘要。

    微信会话窗口有限：你不在的时候它把每件事都发一遍，等你回来可能十几条一起挤出去，
    既刷屏、又容易整批撞上过期。合并成一条「你不在时又完成了 N 件事」更符合直觉。

    合并保留最早那条（含它当时的 context_token），其余移到 outbox/merged/ 并写清单，
    出问题能查回每条原文。
    """
    groups: dict[str, list[Path]] = {}
    for path in sorted(OUTBOX.glob("*.json")):
        payload = read_json(path)
        if not isinstance(payload, dict):
            continue
        to_user = str(payload.get("to_user_id") or "")
        if not to_user:
            continue
        groups.setdefault(to_user, []).append(path)

    merged = 0
    for _to_user, paths in groups.items():
        if len(paths) < 2:
            continue
        payloads = []
        for p in paths:
            d = read_json(p)
            if isinstance(d, dict):
                payloads.append(d)
        if len(payloads) < 2:
            continue
        head = payloads[0]
        head_text = str(head.get("text") or "").strip()
        others = payloads[1:]
        lines = []
        for pl in others:
            t = str(pl.get("text") or "").strip().replace("\n", " ")
            lines.append("· " + (t[:60] + "…" if len(t) > 60 else t))
        head["text"] = (head_text + "\n\n——\n你不在时又完成了 %d 件事：\n" % len(others)
                        + "\n".join(lines))
        head["_merged_count"] = len(others)
        atomic_json(paths[0], head)
        try:
            with open(OUTBOX / "merged" / "manifest.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps({"head": paths[0].name, "merged": len(others),
                                    "at": int(time.time())}, ensure_ascii=False) + "\n")
        except OSError:
            pass
        for p in paths[1:]:
            try:
                shutil.move(str(p), str(OUTBOX / "merged" / p.name))
                merged += 1
            except OSError:
                pass
    if merged:
        print(f"Merged {merged} pending messages", flush=True)
    return merged


def drain_outbox(creds: dict[str, Any]) -> int:
    # 先把堆积的多条合并成一条，再发——窗口有限，一条条发容易整批过期
    try:
        merge_pending()
    except Exception as exc:
        print(f"merge failed: {type(exc).__name__}: {exc}", file=sys.stderr)
    sent = 0
    token = str(creds["bot_token"])
    baseurl = str(creds.get("baseurl") or DEFAULT_BASE_URL).rstrip("/")
    binding = read_json(BINDING_FILE, {}) or {}
    for path in sorted(OUTBOX.glob("*.json")):
        try:
            payload = read_json(path)
            if not isinstance(payload, dict):
                raise ValueError("outbox entry must be an object")
            to_user = payload.get("to_user_id")
            text = payload.get("text")
            if not isinstance(to_user, str) or not to_user or not isinstance(text, str):
                raise ValueError("outbox entry requires to_user_id and text")
            client_id = persist_client_id(path, payload)
            from_user = payload.get("from_user_id") or binding.get("bot_user_id") or ""
            msg = {
                "from_user_id": from_user,
                "to_user_id": to_user,
                "client_id": client_id,
                "message_type": 2,
                "message_state": 2,
                "context_token": payload.get("context_token") or "",
                "item_list": [{"type": 1, "text_item": {"text": text}}],
            }
            body = {"msg": msg, "base_info": base_info()}
            resp = request_json("POST", f"{baseurl}/ilink/bot/sendmessage", headers=post_headers(token), body=body, timeout=30)
            err = explicit_error(resp)
            if err:
                if err == -14:
                    mark_expired()
                    raise SystemExit(14)
                raise RuntimeError(f"sendmessage application error: {err}")
            shutil.move(str(path), str(OUTBOX / "sent" / path.name))
            sent += 1
        except SystemExit:
            raise
        except (json.JSONDecodeError, ValueError) as exc:
            print(f"Bad outbox file {path.name}: {exc}", file=sys.stderr)
            shutil.move(str(path), str(OUTBOX / "bad" / path.name))
        except Exception as exc:
            # 曾经这里是无条件保留重试 → 一个失效的 context_token 会堆出上千条永不成功的消息。
            # 现在给每条记尝试次数：-2（凭证失效）/ 超过 3 次 / 超过 24 小时 → 移入 expired 并留清单。
            msg = str(exc)
            attempts = int((payload or {}).get("_attempts") or 0) + 1 if isinstance(payload, dict) else 1
            try:
                age_ms = int(time.time() * 1000) - int(str(path.name).split("-")[0])
            except Exception:
                age_ms = 0
            hard_fail = ("application error: -2" in msg or attempts >= MAX_SEND_ATTEMPTS
                         or age_ms > MAX_QUEUE_AGE_MS)
            if isinstance(payload, dict):
                payload["_attempts"] = attempts
                payload["_last_error"] = msg[:200]
                try:
                    atomic_json(path, payload)
                except Exception:
                    pass
            if hard_fail:
                try:
                    target = OUTBOX / "expired" / path.name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(path), str(target))
                    with open(OUTBOX / "expired" / "manifest.jsonl", "a", encoding="utf-8") as f:
                        f.write(json.dumps({"file": path.name, "attempts": attempts,
                                            "error": msg[:200], "at": int(time.time())},
                                           ensure_ascii=False) + NL)
                    print(f"Expired {path.name} after {attempts} attempts: {msg[:120]}", file=sys.stderr)
                except Exception:
                    pass
            else:
                print(f"Send deferred for {path.name} ({attempts}/{MAX_SEND_ATTEMPTS}): {msg[:120]}",
                      file=sys.stderr)
    return sent


def mark_expired() -> None:
    _heartbeat_stop.set()
    atomic_json(AUTH_FILE, {"status": "expired", "updated_at": int(time.time())}, 0o600)
    try:
        if ALIVE_FILE.exists():
            ALIVE_FILE.unlink()
    except Exception:
        pass


def daemon() -> int:
    if not acquire_lock("relay"):
        print("已有 relay daemon 在运行，本实例退出", file=sys.stderr)
        return 0
    ensure_layout()
    try:
        creds = load_credentials()
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    token = str(creds["bot_token"])
    baseurl = str(creds.get("baseurl") or DEFAULT_BASE_URL).rstrip("/")
    cursor_state = read_json(CURSOR_FILE, {}) or {}
    cursor = cursor_state.get("get_updates_buf") or ""
    atomic_json(AUTH_FILE, {"status": "ok", "updated_at": int(time.time())}, 0o600)
    threading.Thread(target=heartbeat_loop, daemon=True).start()

    while True:
        drain_outbox(creds)
        body = {"get_updates_buf": cursor, "base_info": base_info()}
        try:
            resp = request_json("POST", f"{baseurl}/ilink/bot/getupdates", headers=post_headers(token), body=body, timeout=45)
            err = explicit_error(resp)
            if err == -14:
                mark_expired()
                return 14
            if err:
                raise RuntimeError(f"getupdates application error: {err}")
            new_cursor = resp.get("get_updates_buf")
            if isinstance(new_cursor, str) and new_cursor:
                cursor = new_cursor
                atomic_json(CURSOR_FILE, {"get_updates_buf": cursor, "updated_at": int(time.time())})
            msgs = resp.get("msgs") or []
            if isinstance(msgs, list):
                ingest_messages(msgs)
        except KeyboardInterrupt:
            return 0
        except urllib.error.HTTPError as exc:
            print(f"Long poll HTTP error: {exc.code}", file=sys.stderr)
            time.sleep(2)
        except Exception as exc:
            print(f"Long poll deferred: {exc}", file=sys.stderr)
            time.sleep(2)
        finally:
            drain_outbox(creds)


def status() -> int:
    ensure_layout()
    auth = read_json(AUTH_FILE, {}) or {}
    binding = read_json(BINDING_FILE, {}) or {}
    cursor = read_json(CURSOR_FILE, {}) or {}
    summary = {
        "root": str(ROOT),
        "logged_in": TOKEN_FILE.exists(),
        "auth_status": auth.get("status", "unknown"),
        "bound": bool(binding.get("bound_user_id")),
        "cursor_present": bool(cursor.get("get_updates_buf")),
        "inbox_pending": len(list(INBOX.glob("*.json"))),
        "outbox_pending": len(list(OUTBOX.glob("*.json"))),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="WeChat iLink file bridge")
    parser.add_argument("mode", choices=("login", "daemon", "status"))
    args = parser.parse_args()
    try:
        if args.mode == "login":
            return login()
        if args.mode == "daemon":
            return daemon()
        return status()
    except urllib.error.HTTPError as exc:
        print(f"HTTP error: {exc.code}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

