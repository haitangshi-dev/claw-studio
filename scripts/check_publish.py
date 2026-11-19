# -*- coding: utf-8 -*-
"""发布前自检：仓库里不该出现个人数据与凭证。

用法：
    python scripts/check_publish.py          # 只检查
    python scripts/check_publish.py --fix-log  # 额外打印可执行建议

检查对象是 **git 已跟踪的文件**（未跟踪的运行期数据不该进库，也就不该被检查）。
退出码 0 = 干净，1 = 发现可疑内容。
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 不该被跟踪的路径（哪怕内容无害，也说明 .gitignore 漏了）
FORBIDDEN_PATHS = [
    r"^config/config\.json$",
    r"^data/",
    r"^sessions/",
    r"^memory/.*\.db$",
    r"^logs/",
    r"^workspace/",
    r"^bridge/(state|secure|inbox|outbox|logs|qr)/",
    r"\.exe$",
    r"\.db$",
]

# 内容模式：命中即视为泄漏风险
PATTERNS = [
    ("Windows 用户目录", re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+[^\\/\s\"']+", re.I)),
    ("macOS 用户目录", re.compile(r"/Users/[^/\s\"']+")),
    ("API Key 形态", re.compile(r"\b(sk|ghp|gho|xoxb|AKIA)[-_A-Za-z0-9]{16,}\b")),
    ("Bearer Token", re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]{20,}")),
    ("邮箱", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]{2,}\b")),
    ("手机号", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("微信 OpenID", re.compile(r"o[0-9A-Za-z]{24,}@im\.wechat")),
]

# 允许出现的邮箱（示例/协议地址）
EMAIL_ALLOW = re.compile(r"(@example\.(com|org)|@users\.noreply\.github\.com|dev@claw\.studio)$")

SKIP_FILES = {"scripts/check_publish.py"}  # 脚本自己的正则不该被自己举报

SKIP_SUFFIX = (".png", ".jpg", ".jpeg", ".ico", ".woff", ".woff2", ".ttf", ".lock")


def tracked_files() -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True)
    return [p for p in out.stdout.splitlines() if p.strip()]


def main() -> int:
    problems: list[tuple[str, str, str]] = []

    for rel in tracked_files():
        if rel in SKIP_FILES:
            continue
        for pat in FORBIDDEN_PATHS:
            if re.search(pat, rel, re.I):
                problems.append((rel, "不该入库的路径", pat))
                break
        if rel.endswith(SKIP_SUFFIX):
            continue
        path = ROOT / rel
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for name, rx in PATTERNS:
            for m in rx.finditer(text):
                hit = m.group(0)
                if "..." in hit or "…" in hit:   # 文档里的省略号示例，不是真路径
                    continue
                if name == "邮箱" and EMAIL_ALLOW.search(hit):
                    continue
                if name == "Windows 用户目录" and "claw-studio" in hit:
                    continue
                line = text[: m.start()].count("\n") + 1
                problems.append((rel, f"{name}：{hit[:40]}", f"第 {line} 行"))
                break

    if not problems:
        print("✔ 未发现个人数据或凭证，仓库可以发布。")
        return 0

    print(f"✘ 发现 {len(problems)} 处可疑内容：\n")
    for rel, why, where in problems:
        print(f"  {rel}\n    {why}  ({where})")
    print("\n处理建议：")
    print("  1) 凭证 → 立即在服务端作废并轮换，不要只删文件；")
    print("  2) 个人路径 → 改成配置项或相对路径；")
    print("  3) 运行期数据 → 加进 .gitignore 并 git rm --cached；")
    print("  4) 若已 push 过 → 需要重写历史（git filter-repo），否则历史里永远能翻到。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
