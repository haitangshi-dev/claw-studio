"""打一个「干净分发包」：只含源码与模板，不含任何运行期数据。

为什么需要它：这个项目把记忆、会话、密钥、日志都放在项目目录下（这样迁移方便）。
好处是自己用很省事，坏处是**直接打包发出去就会把使用者的记忆和聊天记录一起带走**。
.gitignore 只挡住了 git，挡不住 zip。

用法：
    python scripts/pack_release.py              # 输出 dist-release/claw-studio-YYYYMMDD.zip
    python scripts/pack_release.py --out D:\\tmp  # 指定输出目录

包里保证没有：
    memory/           记忆库（memory.db、导出的 markdown）
    sessions/         会话文件与任务计划
    data/             设置与加密后的 API Key
    logs/             诊断日志、工具审计
    workspace/        工作目录内容（工具产物）
    bridge/{state,secure,inbox,outbox,qr,logs}   微信登录凭证与消息
    .claw_ask/        未回答的提问
    node_modules/ web/dist/ _preview/ __pycache__/ .git/
"""
from __future__ import annotations

import argparse
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 目录级排除：任何人的运行期数据都在这些目录里
EXCLUDE_DIRS = {
    ".git", ".github", "node_modules", "dist", "_preview",
    "__pycache__", ".pytest_cache", ".venv", "venv",
    "memory", "sessions", "logs", "data", "workspace", ".claw_ask",
    "dist-release",
}

# 文件名级排除
EXCLUDE_SUFFIX = (".pyc", ".pyo", ".db", ".db-wal", ".db-shm", ".log", ".zip")

# bridge 只保留源码：登录态在 state/ 与 secure/ 里，绝不能进包
BRIDGE_KEEP = {
    "relay.py", "worker.py", "watchdog.py", "login_qr.py",
    "single_instance.py", "说明.md",
}

# 明确要带的顶层条目（白名单，避免以后新增的目录被漏掉或误带）
INCLUDE_TOP = {
    "server", "web", "bridge", "config", "scripts", "plugins", "skills", "assets",
    "README.md", "LICENSE", "requirements.txt", ".gitignore",
}


def should_skip(rel: Path) -> bool:
    parts = rel.parts
    if not parts:
        return True
    if parts[0] in EXCLUDE_DIRS:
        return True
    if any(p in EXCLUDE_DIRS for p in parts[:-1]):
        return True
    # bridge 的运行期目录
    if parts[0] == "bridge" and len(parts) > 1 and parts[1] not in ("",):
        if parts[1] not in BRIDGE_KEEP and parts[1] not in ("state", "secure", "inbox", "outbox", "qr", "logs", "__pycache__"):
            return True
        if parts[1] in ("state", "secure", "inbox", "outbox", "qr", "logs", "__pycache__"):
            return True
    if rel.name in ("config.json",) and parts[0] == "config":
        return True      # 本地配置（含个人路径/模型选择），只发 config.example.json
    if rel.suffix.lower() in EXCLUDE_SUFFIX:
        return True
    return False


def collect() -> list[Path]:
    files: list[Path] = []
    for top in sorted(INCLUDE_TOP):
        p = ROOT / top
        if not p.exists():
            continue
        if p.is_file():
            rel = p.relative_to(ROOT)
            if not should_skip(rel):
                files.append(rel)
            continue
        for f in p.rglob("*"):
            if not f.is_file():
                continue
            rel = f.relative_to(ROOT)
            if not should_skip(rel):
                files.append(rel)
    return sorted(files)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "dist-release"))
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d")
    zp = out_dir / f"claw-studio-{stamp}.zip"

    files = collect()
    total = 0
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in files:
            z.write(ROOT / rel, rel.as_posix())
            total += (ROOT / rel).stat().st_size

    # 自检：包里不能出现任何运行期数据
    bad = []
    with zipfile.ZipFile(zp) as z:
        for name in z.namelist():
            parts = name.split("/")
            if parts[0] in ("memory", "sessions", "data", "logs", "workspace", ".claw_ask"):
                bad.append(name)
            elif parts[0] == "bridge" and len(parts) > 1 and parts[1] in ("state", "secure", "inbox", "outbox", "qr", "logs"):
                bad.append(name)
            elif name.endswith((".db", ".log", ".pyc")) or name == "config/config.json":
                bad.append(name)

    print(f"打包完成：{zp}")
    print(f"文件 {len(files)} 个，压缩前 {total / 1024 / 1024:.1f} MB，"
          f"压缩后 {zp.stat().st_size / 1024 / 1024:.1f} MB")
    if bad:
        print(f"\n[!] 仍有 {len(bad)} 个不该进包的文件：")
        for b in bad[:10]:
            print("   ", b)
        return 1
    print("自带检查：包内不含记忆库 / 会话 / 密钥 / 日志 / 工作区内容")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
