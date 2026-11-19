# -*- coding: utf-8 -*-
"""文件管理工具族：读取 / 创建 / 精确修改 / 删除 / 搜索。

路径规则（讨论稿 6.3②）：
  - 相对路径一律相对"会话工作目录"解析；绝对路径直接使用；
  - 所有结果文本里的路径以工作目录为基准展示（相对形式）。
"""
from __future__ import annotations

import shutil
from pathlib import Path

FILE_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "file_read",
            "description": "读取一个文本文件的内容（超过长度会截断）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径（相对工作目录或绝对路径）"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_create",
            "description": "创建新文件（自动创建父目录）。文件已存在时报错，改用 file_edit 修改。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"},
                    "content": {"type": "string", "description": "文件内容"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_edit",
            "description": "编辑文本文件。默认把 old_string 精确替换为 new_string（old_string 需在文件中唯一出现，否则补充上下文或设 replace_all=true）；append=true 时改为把 new_string 追加到文件末尾（大文件分块写入用）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"},
                    "old_string": {"type": "string", "description": "要被替换的原文（需唯一）；追加模式下可省略"},
                    "new_string": {"type": "string", "description": "替换后的新文本；追加模式下是要追加的内容"},
                    "replace_all": {"type": "boolean", "description": "是否替换所有出现（默认 false）"},
                    "append": {"type": "boolean", "description": "true = 把 new_string 追加到文件末尾（默认 false）"},
                },
                "required": ["path", "new_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_delete",
            "description": "删除一个文件或目录（目录会被递归删除）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "要删除的路径"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_search",
            "description": "搜索文件：mode=name 按文件名模糊匹配（如 \".txt\" 或 \"报告\"）；mode=content 在文本文件内容里搜关键词。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "文件名关键词/通配符，或内容关键词"},
                    "path": {"type": "string", "description": "搜索根目录（可选，默认工作目录）"},
                    "mode": {"type": "string", "description": "name 或 content（默认 name）"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_move",
            "description": "移动文件或目录（也可用于重命名）。目标为目录时移入其中；目标父目录自动创建。移动文件请优先用本工具，不要用 shell 拼 cd/mv。",
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "源路径"},
                    "target": {"type": "string", "description": "目标路径（目录或新路径）"},
                },
                "required": ["source", "target"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_copy",
            "description": "复制文件或目录。目标为目录时复制到其中；目标父目录自动创建。",
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "源路径"},
                    "target": {"type": "string", "description": "目标路径（目录或新路径）"},
                },
                "required": ["source", "target"],
            },
        },
    },
]

_TEXT_EXTS = {
    ".md", ".txt", ".py", ".json", ".log", ".csv", ".bat", ".cmd", ".js", ".ts",
    ".html", ".css", ".xml", ".yaml", ".yml", ".ini", ".toml", ".ps1", ".sh", ".c", ".cpp", ".java",
}
_MAX_FILE_READ = 8000
_MAX_CONTENT_HITS = 50
_MAX_SCAN_FILES = 3000
_MAX_SCAN_SIZE = 2_000_000


def _smart_decode(b: bytes) -> str:
    for enc in ("utf-8", "gb18030"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", "replace")


def _resolve(p: str, workdir: str) -> Path:
    path = Path(p) if p else Path(".")
    if not path.is_absolute():
        path = Path(workdir) / path
    return path.resolve()


def _display(path: Path, workdir: str) -> str:
    try:
        return str(path.relative_to(Path(workdir).resolve()))
    except ValueError:
        return str(path)


def _normalize_newlines(text: str) -> str:
    """统一换行为 LF——避免 Windows 文本模式把已有的 \\r\\n 反复加料。"""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _write_text(path: Path, text: str) -> None:
    """二进制规范写入（LF 换行，UTF-8），不做任何换行转换。"""
    path.write_bytes(_normalize_newlines(text).encode("utf-8"))


def _read_text(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"文件不存在：{path}")
    if path.is_dir():
        raise IsADirectoryError(f"这是一个目录，不是文件：{path}")
    raw = path.read_bytes()
    if b"\x00" in raw[:4096]:
        raise ValueError(f"看起来是二进制文件，无法按文本读取：{path}")
    return _normalize_newlines(_smart_decode(raw))


def file_read(args: dict, workdir: str, cfg: dict) -> str:
    path = _resolve(str(args.get("path") or ""), workdir)
    text = _read_text(path)
    if len(text) > _MAX_FILE_READ:
        text = text[:_MAX_FILE_READ] + f"\n...[已截断，全文共 {len(text)} 字符]"
    return f"文件：{_display(path, workdir)}\n---\n{text}"


def file_create(args: dict, workdir: str, cfg: dict) -> str:
    path = _resolve(str(args.get("path") or ""), workdir)
    if path.exists():
        raise FileExistsError(f"文件已存在：{_display(path, workdir)}（修改内容请用 file_edit）")
    content = str(args.get("content") or "")
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_text(path, content)
    return f"已创建：{_display(path, workdir)}（{len(content)} 字符）"


def file_edit(args: dict, workdir: str, cfg: dict) -> str:
    path = _resolve(str(args.get("path") or ""), workdir)
    new = str(args.get("new_string") or "")
    if args.get("append"):
        text = _read_text(path)
        _write_text(path, text + new)
        return f"已追加：{_display(path, workdir)}（+{len(new)} 字符，现共 {len(text) + len(new)} 字符）"
    old = str(args.get("old_string") or "")
    if not old:
        raise ValueError(
            'old_string 不能为空。追加内容请重新调用本工具并设 append=true，'
            '例如 {"path": "文件.py", "append": true, "new_string": "续写内容"}'
        )
    text = _read_text(path)
    cnt = text.count(old)
    if cnt == 0:
        raise ValueError("old_string 在文件中未找到（注意大小写与空格需完全一致）")
    if cnt > 1 and not args.get("replace_all"):
        raise ValueError(f"old_string 出现了 {cnt} 次，不唯一；请补充上下文使其唯一，或设 replace_all=true")
    text = text.replace(old, new) if args.get("replace_all") else text.replace(old, new, 1)
    _write_text(path, text)
    replaced = cnt if args.get("replace_all") else 1
    return f"已修改：{_display(path, workdir)}（替换 {replaced} 处）"


def file_delete(args: dict, workdir: str, cfg: dict) -> str:
    path = _resolve(str(args.get("path") or ""), workdir)
    if not path.exists():
        raise FileNotFoundError(f"路径不存在：{_display(path, workdir)}")
    if path.is_dir():
        shutil.rmtree(path)
        return f"已删除目录：{_display(path, workdir)}"
    path.unlink()
    return f"已删除文件：{_display(path, workdir)}"


def _resolve_move_target(src: Path, dst: Path, workdir: str) -> Path:
    """目标是已存在的目录 → 移入其中；否则按目标路径使用。"""
    if dst.is_dir():
        dst = dst / src.name
    if dst.exists():
        raise FileExistsError(
            f"目标已存在：{_display(dst, workdir)}（如需覆盖请先 file_delete，或换一个目标名）"
        )
    dst.parent.mkdir(parents=True, exist_ok=True)
    return dst


def file_move(args: dict, workdir: str, cfg: dict) -> str:
    src = _resolve(str(args.get("source") or ""), workdir)
    dst = _resolve(str(args.get("target") or ""), workdir)
    if not src.exists():
        raise FileNotFoundError(f"源路径不存在：{_display(src, workdir)}")
    dst = _resolve_move_target(src, dst, workdir)
    shutil.move(str(src), str(dst))
    return f"已移动：{_display(src, workdir)} -> {_display(dst, workdir)}"


def file_copy(args: dict, workdir: str, cfg: dict) -> str:
    src = _resolve(str(args.get("source") or ""), workdir)
    dst = _resolve(str(args.get("target") or ""), workdir)
    if not src.exists():
        raise FileNotFoundError(f"源路径不存在：{_display(src, workdir)}")
    dst = _resolve_move_target(src, dst, workdir)
    if src.is_dir():
        shutil.copytree(str(src), str(dst))
    else:
        shutil.copy2(str(src), str(dst))
    return f"已复制：{_display(src, workdir)} -> {_display(dst, workdir)}"


def file_search(args: dict, workdir: str, cfg: dict) -> str:
    query = str(args.get("query") or "").strip()
    if not query:
        raise ValueError("缺少 query 参数")
    root = _resolve(str(args.get("path") or "."), workdir)
    if not root.is_dir():
        raise FileNotFoundError(f"搜索根目录不存在：{root}")
    mode = str(args.get("mode") or "name").lower()

    if mode == "content":
        hits: list[str] = []
        scanned = 0
        q = query.lower()
        for p in root.rglob("*"):
            if len(hits) >= _MAX_CONTENT_HITS or scanned >= _MAX_SCAN_FILES:
                break
            if not p.is_file() or p.suffix.lower() not in _TEXT_EXTS:
                continue
            try:
                if p.stat().st_size > _MAX_SCAN_SIZE:
                    continue
                text = _read_text(p)
            except Exception:
                continue
            scanned += 1
            for i, line in enumerate(text.splitlines(), 1):
                if q in line.lower():
                    hits.append(f"{_display(p, workdir)}:{i}: {line.strip()[:200]}")
                    if len(hits) >= _MAX_CONTENT_HITS:
                        break
        if not hits:
            return f'内容搜索 "{query}" 无命中（搜索根：{_display(root, workdir)}）'
        return f'内容搜索 "{query}"（前 {len(hits)} 条）：\n' + "\n".join(hits)

    # name 模式
    pattern = query
    if not any(c in pattern for c in "*?[]"):
        pattern = f"*{pattern}*"
    matches = []
    try:
        for p in root.rglob(pattern):
            matches.append(p)
            if len(matches) >= 100:
                break
    except Exception as e:
        raise ValueError(f"通配符无效：{e}")
    if not matches:
        return (f'未找到匹配 "{query}" 的文件（搜索根：{_display(root, workdir)}）；'
                f'按文件名找不到时，可改用 mode=content 按内容检索')
    lines = []
    for p in sorted(matches, key=lambda x: str(x).lower()):
        tag = "/" if p.is_dir() else ""
        size = ""
        if p.is_file():
            try:
                size = f"  ({p.stat().st_size} B)"
            except OSError:
                pass
        lines.append(f"{_display(p, workdir)}{tag}{size}")
    return f'匹配 "{query}" 的 {len(matches)} 项：\n' + "\n".join(lines)
