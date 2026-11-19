# -*- coding: utf-8 -*-
"""浏览器自动化：Playwright（系统 Edge 通道，零内核下载）+ 专用工作线程。

Playwright 的 sync API 必须在固定线程内使用，而工具执行走线程池（线程不固定），
因此用「单工作线程 + 命令队列」封装：任何线程发命令 → 工作线程执行 → 事件回传。
"""
from __future__ import annotations

import json
import queue
import threading
import time
from pathlib import Path

_CMD_Q: "queue.Queue" = queue.Queue()
_THREAD: threading.Thread | None = None
_LOCK = threading.Lock()

_TEXT_CAP = 6000
_HTML_CAP = 6000
_EVAL_CAP = 2500


def _ensure_thread() -> None:
    global _THREAD
    with _LOCK:
        if _THREAD is None or not _THREAD.is_alive():
            _THREAD = threading.Thread(target=_worker, name="browser-worker", daemon=True)
            _THREAD.start()


def _preview(text: str, cap: int = 400) -> str:
    text = (text or "").strip()
    return text[:cap] + ("…" if len(text) > cap else "")


def _do(action: str, args: dict, page, workdir: str) -> str:
    action = (action or "").strip().lower()
    if action in ("open", "goto", "navigate"):
        url = str(args.get("url") or "").strip()
        if not url:
            return "错误：需要 url 参数"
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        try:
            page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass
        body = ""
        try:
            body = page.inner_text("body")
        except Exception:
            pass
        return f"已打开：{page.title()}\nURL: {page.url}\n--- 正文预览 ---\n{_preview(body, 500)}"

    if action == "screenshot":
        out_dir = Path(workdir) / "browser_shots"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"shot_{time.strftime('%m%d_%H%M%S')}.png"
        page.screenshot(path=str(path), full_page=bool(args.get("full")))
        size_kb = round(path.stat().st_size / 1024, 1)
        return f"截图已保存：{path}（{size_kb} KB，1440x900{' 全页' if args.get('full') else ''}）"

    if action == "click":
        sel = str(args.get("selector") or "").strip()
        txt = str(args.get("text") or "").strip()
        if sel:
            page.click(sel, timeout=15000)
        elif txt:
            page.get_by_text(txt, exact=False).first.click(timeout=15000)
        else:
            return "错误：click 需要 selector 或 text 参数"
        try:
            page.wait_for_load_state("domcontentloaded", timeout=8000)
        except Exception:
            pass
        return f"已点击。当前页：{page.title()}\nURL: {page.url}"

    if action == "fill":
        sel = str(args.get("selector") or "").strip()
        val = str(args.get("text") or "")
        if not sel:
            return "错误：fill 需要 selector 参数"
        page.fill(sel, val, timeout=15000)
        return f"已填入 {sel} ← 「{_preview(val, 80)}」"

    if action == "press":
        key = str(args.get("key") or "Enter")
        target = str(args.get("selector") or "").strip()
        if target:
            page.press(target, key, timeout=15000)
        else:
            page.keyboard.press(key)
        try:
            page.wait_for_load_state("domcontentloaded", timeout=8000)
        except Exception:
            pass
        return f"已按键 {key}。当前页：{page.title()}\nURL: {page.url}"

    if action == "text":
        sel = str(args.get("selector") or "body").strip() or "body"
        text = page.inner_text(sel, timeout=15000)
        return f"【{sel} 文本】\n{text[:_TEXT_CAP]}"

    if action == "html":
        sel = str(args.get("selector") or "body").strip() or "body"
        html = page.inner_html(sel, timeout=15000)
        return f"【{sel} HTML】\n{html[:_HTML_CAP]}"

    if action == "eval":
        js = str(args.get("js") or "").strip()
        if not js:
            return "错误：eval 需要 js 参数"
        res = page.evaluate(js)
        try:
            out = json.dumps(res, ensure_ascii=False, default=str)
        except Exception:
            out = str(res)
        return f"【eval 结果】\n{out[:_EVAL_CAP]}"

    if action == "wait":
        ms = int(args.get("ms") or 0)
        sel = str(args.get("selector") or "").strip()
        if sel:
            page.wait_for_selector(sel, timeout=30000)
            return f"元素已出现：{sel}"
        if ms > 0:
            page.wait_for_timeout(min(ms, 60000))
            return f"已等待 {ms} ms"
        return "错误：wait 需要 ms 或 selector"

    if action == "scroll":
        dy = int(args.get("dy") or 600)
        page.mouse.wheel(0, dy)
        page.wait_for_timeout(300)
        return f"已滚动 {dy}px。URL: {page.url}"

    if action == "back":
        page.go_back(timeout=20000)
        return f"已后退。当前页：{page.title()}\nURL: {page.url}"
    if action == "forward":
        page.go_forward(timeout=20000)
        return f"已前进。当前页：{page.title()}\nURL: {page.url}"

    if action == "status":
        return f"当前页：{page.title()}\nURL: {page.url}"

    if action == "close":
        return "__CLOSE__"

    return ("错误：未知 action。可用：open / screenshot / click / fill / press / text / html / "
            "eval / wait / scroll / back / forward / status / close")


def _worker() -> None:
    pw = None
    browser = None
    ctx = None
    page = None
    while True:
        cmd = _CMD_Q.get()
        if cmd is None:
            break
        action = cmd["action"]
        ev: threading.Event = cmd["ev"]
        box: dict = cmd["box"]
        try:
            if pw is None:
                from playwright.sync_api import sync_playwright

                pw = sync_playwright().start()
            if browser is None:
                # 系统 Edge 通道：不下载 Playwright 内核（磁盘友好）
                browser = pw.chromium.launch(channel="msedge", headless=True)
                ctx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="zh-CN")
                page = ctx.new_page()
                page.set_default_timeout(20000)
            result = _do(action, cmd["args"], page, cmd["workdir"])
            if result == "__CLOSE__":
                try:
                    browser.close()
                except Exception:
                    pass
                try:
                    pw.stop()
                except Exception:
                    pass
                pw = browser = ctx = page = None
                result = "浏览器已关闭"
            box["result"] = result
        except Exception as e:
            box["result"] = f"浏览器错误：{type(e).__name__}: {e}"
            # 严重错误（浏览器崩了）→ 重置，下次调用重新拉起
            try:
                if browser is None and pw is not None:
                    pw.stop()
                    pw = None
            except Exception:
                pass
        finally:
            ev.set()


def run_browser(action: str, args: dict, workdir: str, timeout: float = 180.0) -> str:
    """线程安全入口：派发浏览器命令并等待结果。"""
    _ensure_thread()
    ev = threading.Event()
    box: dict = {}
    _CMD_Q.put({"action": action, "args": args or {}, "ev": ev, "box": box, "workdir": workdir})
    if not ev.wait(timeout):
        return f"浏览器操作超时（>{int(timeout)}s）：{action}"
    return str(box.get("result") or "（无结果）")
