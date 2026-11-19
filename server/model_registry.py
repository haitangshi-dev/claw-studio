# -*- coding: utf-8 -*-
"""模型注册表：双目录扫描 + GGUF 头元数据解析。

设计原则：任何模型路径都不硬编码——扫描根目录全部来自 config.json 的 model_scan_dirs。
扫描规则：
  - 递归查找 *.gguf；跳过视觉投影器（mmproj*）；
  - 多分片模型只登记第一片（*-00001-of-*）；
  - 尽力读取 GGUF 头部元数据（general.name / architecture / 训练上下文长度），
    读不到不影响使用（回退文件名）。
"""
from __future__ import annotations

import re
import struct
from pathlib import Path

GGUF_MAGIC = b"GGUF"

# GGUF 值类型 → (struct 格式, 字节数)；字符串=8、数组=9 单独处理
_GGUF_TYPES = {
    0: ("B", 1),    # uint8
    1: ("b", 1),    # int8
    2: ("H", 2),    # uint16
    3: ("h", 2),    # int16
    4: ("I", 4),    # uint32
    5: ("i", 4),    # int32
    6: ("f", 4),    # float32
    7: ("?", 1),    # bool
    10: ("Q", 8),   # uint64
    11: ("q", 8),   # int64
    12: ("d", 8),   # float64
}

_MMPROJ_RE = re.compile(r"mmproj", re.IGNORECASE)
_SPLIT_RE = re.compile(r"-(\d{5})-of-(\d{5})", re.IGNORECASE)


def _read_string(f) -> str:
    (n,) = struct.unpack("<Q", f.read(8))
    if n > 1_000_000:
        raise ValueError("string too long")
    return f.read(n).decode("utf-8", "replace")


def read_gguf_meta(path: Path, max_kv: int = 200) -> dict:
    """尽力读取 GGUF 头部 KV 元数据。

    general.* / <arch>.* 一般位于文件前部；遇到超大数组（tokenizer.*）
    或解析异常即停止，返回已读到的部分。失败返回 {}。
    """
    meta: dict = {}
    try:
        with open(path, "rb") as f:
            if f.read(4) != GGUF_MAGIC:
                return meta
            (version,) = struct.unpack("<I", f.read(4))
            (tensor_count,) = struct.unpack("<Q", f.read(8))
            (kv_count,) = struct.unpack("<Q", f.read(8))
            meta["_version"] = version
            meta["_tensor_count"] = tensor_count

            for _ in range(min(kv_count, max_kv)):
                key = _read_string(f)
                (vtype,) = struct.unpack("<I", f.read(4))
                if vtype == 8:  # string
                    meta[key] = _read_string(f)
                elif vtype == 9:  # array
                    (etype,) = struct.unpack("<I", f.read(4))
                    (ecount,) = struct.unpack("<Q", f.read(8))
                    if etype == 8:  # 字符串数组（tokenizer.tokens 等）
                        if ecount > 50:
                            break
                        for _ in range(ecount):
                            _read_string(f)
                    elif etype in _GGUF_TYPES:
                        fmt, size = _GGUF_TYPES[etype]
                        total = ecount * size
                        if total > 2_000_000:
                            break
                        f.seek(total, 1)
                    else:
                        break
                elif vtype in _GGUF_TYPES:
                    fmt, size = _GGUF_TYPES[vtype]
                    (meta[key],) = struct.unpack("<" + fmt, f.read(size))
                else:
                    break  # 未知类型，停止
    except Exception:
        return meta
    return meta


def scan_models(scan_dirs) -> dict:
    """扫描模型目录，返回 {key: info}。

    scan_dirs: [{"path": ..., "label": ...}, ...]（来自 config.json）。
    key 取 GGUF 文件名词干；同名冲突时加 @来源 后缀。
    """
    found: dict = {}
    for entry in scan_dirs:
        root = Path(entry["path"]) if isinstance(entry, dict) else Path(entry)
        label = entry.get("label", root.name) if isinstance(entry, dict) else root.name
        if not root.exists():
            continue
        for gguf in root.rglob("*.gguf"):
            name = gguf.name
            if _MMPROJ_RE.search(name):
                continue  # 视觉投影器不是主模型
            m = _SPLIT_RE.search(name)
            if m and m.group(1) != "00001":
                continue  # 只登记多分片的第一片

            stem = name[:-5] if name.lower().endswith(".gguf") else name
            key = _SPLIT_RE.sub("", stem)
            if key in found:
                key = f"{key}@{label}"

            meta = read_gguf_meta(gguf)
            arch = meta.get("general.architecture", "")
            mmproj = None
            for mp in sorted(gguf.parent.glob("*.gguf")):
                if _MMPROJ_RE.search(mp.name):
                    mmproj = str(mp)  # 同目录的视觉投影器（VL 模型）
                    break
            info = {
                "key": key,
                "path": str(gguf),
                "source": label,
                "size_mb": round(gguf.stat().st_size / (1024 * 1024), 1),
                "mmproj": mmproj,
                "meta": {
                    "name": meta.get("general.name", ""),
                    "arch": arch,
                    "ctx_train": meta.get(f"{arch}.context_length") if arch else None,
                    "size_label": meta.get("general.size_label", ""),
                },
            }
            found[key] = info
    return found


if __name__ == "__main__":
    # 自测：直接运行本文件，扫描 config.json 里的目录
    import json
    import sys

    root = Path(__file__).resolve().parent.parent
    cfg = json.loads((root / "config" / "config.json").read_text(encoding="utf-8"))
    models = scan_models(cfg["model_scan_dirs"])
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    for k, v in sorted(models.items()):
        print(f"{k:45s} {v['size_mb']:>8.1f} MB  {v['source']:10s} "
              f"arch={v['meta']['arch'] or '?':12s} ctx_train={v['meta']['ctx_train'] or '?'}")
    print(f"共 {len(models)} 个模型")
