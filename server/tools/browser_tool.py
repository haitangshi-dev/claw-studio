# -*- coding: utf-8 -*-
"""浏览器自动化工具（Playwright + 系统 Edge）。"""
from __future__ import annotations

from browser import run_browser

BROWSER_SPEC = {
    "type": "function",
    "function": {
        "name": "browser_control",
        "description": (
            "浏览器自动化（真实打开网页）：action=open 打开网址；screenshot 截屏存文件；"
            "text 取页面/元素文字；click 点击（selector 或 text 二选一）；fill 填输入框；"
            "press 按键；eval 执行 JS；wait 等待；scroll 滚动；back/forward；status；close。"
            "查资料、登录页操作、填表单、抓页面内容都走它。用完可 close 释放。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["open", "screenshot", "text", "click", "fill", "press", "eval",
                             "wait", "scroll", "back", "forward", "status", "close"],
                    "description": "要执行的动作",
                },
                "url": {"type": "string", "description": "open 的网址（可省 https://）"},
                "selector": {"type": "string", "description": "元素选择器（CSS），如 #id、.class、input[name=q]"},
                "text": {"type": "string", "description": "click 的可见文字 / fill 的填入内容"},
                "key": {"type": "string", "description": "press 的按键，如 Enter、Escape、Tab"},
                "js": {"type": "string", "description": "eval 的 JS 表达式，如 document.title"},
                "ms": {"type": "integer", "description": "wait 的毫秒数"},
                "full": {"type": "boolean", "description": "screenshot 是否整页截图"},
                "dy": {"type": "integer", "description": "scroll 的向下像素（负值向上）"},
            },
            "required": ["action"],
        },
    },
}


def browser_control(args: dict, workdir: str, cfg: dict) -> str:
    action = str(args.get("action") or "").strip()
    return run_browser(action, args, workdir)
