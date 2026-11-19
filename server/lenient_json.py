# -*- coding: utf-8 -*-
"""宽容 JSON 解析链（2026-10-03 用户要求："json解析松一点"）。

用于解析 2B 小模型生成的结构化输出（工具参数等）。层级：

  1) 严格   json.loads
  2) 围栏   剥离 ```json ... ``` 代码块
  3) 提取   从自由文本截取第一个"平衡括号"的 {...} / [...] 子串
  4) 修复   单引号→双引号、去尾逗号、补未闭合括号、Python 字面量→JSON
  5) 失败   返回 (None, None)，由上层触发结构化纠错重试

用法:
    obj, how = parse_lenient(text)   # how ∈ {'strict','extract','repair',None}
"""
from __future__ import annotations

import json
import re

_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*([\s\S]*?)\s*```")


def _extract_balanced(text: str, opener: str, closer: str) -> str | None:
    """截取从第一个 opener 起、括号平衡的子串（正确跳过字符串内的括号）。"""
    start = text.find(opener)
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    # 未闭合：返回从 start 到结尾（交给补括号修复）
    return text[start:]


def extract_json_substring(text: str) -> str | None:
    """优先找 {...}，其次 [...]。"""
    m = _extract_balanced(text, "{", "}")
    if m is not None:
        return m
    return _extract_balanced(text, "[", "]")


def _fix_literals(t: str) -> str:
    t = re.sub(r"\bTrue\b", "true", t)
    t = re.sub(r"\bFalse\b", "false", t)
    t = re.sub(r"\bNone\b", "null", t)
    return t


def _fix_single_quotes(t: str) -> str:
    """整体用单引号包裹的 JSON（常见于 Python 式字面量）→ 双引号。

    仅在文本不含双引号时整体替换，避免误伤字符串内部的撇号。
    """
    if '"' not in t and "'" in t:
        return t.replace("'", '"')
    return t


def _fix_control_chars(t: str) -> str:
    """字符串内的字面控制字符（换行/回车/制表）→ 转义写法。

    小模型写文件内容时常在 JSON 字符串里放真实换行（非法 JSON），修掉后即可严格解析。
    注意：只处理字符串内部；字符串之间的排版换行保持原样。
    """
    out: list[str] = []
    in_str = False
    esc = False
    for ch in t:
        if in_str:
            if esc:
                out.append(ch)
                esc = False
            elif ch == "\\":
                out.append(ch)
                esc = True
            elif ch == '"':
                in_str = False
                out.append(ch)
            elif ch == "\n":
                out.append("\\n")
            elif ch == "\r":
                out.append("\\r")
            elif ch == "\t":
                out.append("\\t")
            else:
                out.append(ch)
        else:
            if ch == '"':
                in_str = True
            out.append(ch)
    return "".join(out)


def _remove_trailing_commas(t: str) -> str:
    return re.sub(r",(\s*[}\]])", r"\1", t)


def _close_brackets(t: str) -> str:
    """补齐未闭合的括号/方括号（正确跳过字符串）。"""
    stack: list[str] = []
    in_str = False
    esc = False
    pairs = {"{": "}", "[": "]"}
    for ch in t:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in pairs:
            stack.append(pairs[ch])
        elif ch in "}]" and stack and stack[-1] == ch:
            stack.pop()
    if in_str:
        t += '"'
    return t + "".join(reversed(stack))


def _repair(t: str) -> str:
    t = _fix_literals(t)
    t = _fix_single_quotes(t)
    t = _fix_control_chars(t)
    t = _remove_trailing_commas(t)
    t = _close_brackets(t)
    return t


def parse_lenient(text) -> tuple[object | None, str | None]:
    """返回 (obj, how)；how 为命中的层级，全失败 (None, None)。

    how ∈ {'strict', 'extract', 'repair'}
    """
    if text is None:
        return None, None
    if isinstance(text, (dict, list)):
        return text, "strict"
    if not isinstance(text, str):
        return None, None

    s = text.strip()
    if not s:
        return None, None

    # 1) 严格
    try:
        return json.loads(s), "strict"
    except Exception:
        pass

    # 收集候选：围栏内容 + 平衡子串 + 原文
    candidates: list[str] = []
    m = _FENCE_RE.search(s)
    if m:
        candidates.append(m.group(1).strip())
    sub = extract_json_substring(s)
    if sub:
        candidates.append(sub)
    candidates.append(s)

    for cand in candidates:
        # 2) 直接试
        try:
            return json.loads(cand), "extract"
        except Exception:
            pass
        # 3) 修复后试
        try:
            return json.loads(_repair(cand)), "repair"
        except Exception:
            pass

    return None, None


def is_structurally_closed(text) -> bool:
    """判断 JSON 文本结构是否闭合（引号成对、括号平衡）。

    生成被截断时（如达到 max_tokens 上限）通常结构不闭合；
    用来区分"可宽容修复的小毛病"（字面换行、尾逗号等）与"被截断的残缺输出"。
    """
    if not isinstance(text, str):
        return True
    in_str = False
    esc = False
    stack: list[str] = []
    pairs = {"{": "}", "[": "]"}
    for ch in text:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in pairs:
            stack.append(pairs[ch])
        elif ch in "}]" and stack and stack[-1] == ch:
            stack.pop()
    return (not in_str) and (not stack)


def coerce_types(obj, schema_props: dict | None = None):
    """宽松校验第 3 步：按 schema 提示做类型强转（可选使用）。

    schema_props: {"参数名": {"type": "integer"|"number"|"boolean"|"array"|"object"|"string"}}
    返回 (obj, notes)；notes 记录被强转/忽略的项。
    """
    notes: list[str] = []
    if not isinstance(obj, dict) or not schema_props:
        return obj, notes
    for k, v in list(obj.items()):
        spec = schema_props.get(k)
        if not spec or "type" not in spec:
            continue
        want = spec["type"]
        try:
            if want == "integer" and isinstance(v, str):
                obj[k] = int(float(v.strip()))
                notes.append(f"{k}: str->int")
            elif want == "number" and isinstance(v, str):
                obj[k] = float(v.strip())
                notes.append(f"{k}: str->float")
            elif want == "boolean" and isinstance(v, str):
                lv = v.strip().lower()
                if lv in ("true", "1", "yes"):
                    obj[k] = True
                    notes.append(f"{k}: str->bool")
                elif lv in ("false", "0", "no"):
                    obj[k] = False
                    notes.append(f"{k}: str->bool")
            elif want in ("array", "object") and isinstance(v, str):
                sub, _ = parse_lenient(v)
                if isinstance(sub, (list, dict)):
                    obj[k] = sub
                    notes.append(f"{k}: str->{want}")
        except Exception:
            pass
    return obj, notes


if __name__ == "__main__":
    import sys

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    cases = [
        ('{"command": "ls", "timeout": 60}', "strict"),
        ('```json\n{"command": "ls"}\n```', "extract"),
        ('好的，这是参数：{"command": "ls -la", "cwd": "D:\\\\x"} 请执行', "extract"),
        ("{'command': 'ls', 'timeout': 30}", "repair"),
        ('{"command": "ls", "timeout": 60,}', "repair"),
        ('{"command": "ls", "timeout": True, "cwd": None}', "repair"),
        ('{"command": "ls"', "repair"),
        ('{"command": "echo hello', "repair"),
        ("这不是JSON", None),
    ]
    ok = 0
    for text, want in cases:
        obj, how = parse_lenient(text)
        flag = "OK " if how == want else "FAIL"
        ok += how == want
        print(f"[{flag}] how={str(how):8s} want={str(want):8s} -> {obj}")
    print(f"\n{ok}/{len(cases)} 通过")
    if ok != len(cases):
        raise SystemExit(1)
