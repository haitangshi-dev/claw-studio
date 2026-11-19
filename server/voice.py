# -*- coding: utf-8 -*-
"""语音输出：Edge TTS 在线神经语音（免费、无需 Azure Key）。

默认女声晓晓；支持晓伊 / 晓北 / 晓妮，语速与自动朗读可调。
"""
from __future__ import annotations

import asyncio
import json
import re

VOICES = [
    {"id": "zh-CN-XiaoxiaoNeural", "name": "晓晓", "desc": "温暖自然 · 默认"},
    {"id": "zh-CN-XiaoyiNeural", "name": "晓伊", "desc": "年轻活泼"},
    {"id": "zh-CN-liaoning-XiaobeiNeural", "name": "晓北", "desc": "东北口音"},
    {"id": "zh-CN-shaanxi-XiaoniNeural", "name": "晓妮", "desc": "陕西口音"},
]

DEFAULTS = {
    "tts": {
        "voice": "zh-CN-XiaoxiaoNeural",
        "rate": "+0%",
        "volume": "+0%",
        "auto_read": True,
    },
}


def _merge(dst: dict, src: dict) -> dict:
    for k, v in (src or {}).items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _merge(dst[k], v)
        else:
            dst[k] = v
    return dst


def merge_patch(base: dict, patch: dict) -> dict:
    out = json.loads(json.dumps(base or {}))
    return _merge(out, patch or {})


def normalize_config(cfg: dict | None) -> dict:
    out = json.loads(json.dumps(DEFAULTS))
    _merge(out, cfg or {})
    tts = out["tts"]
    ids = {v["id"] for v in VOICES}
    if tts.get("voice") not in ids:
        tts["voice"] = VOICES[0]["id"]
    for key in ("rate", "volume"):
        s = str(tts.get(key) or "+0%").strip()
        if re.fullmatch(r"\d{1,3}%", s):
            s = "+" + s
        if not re.fullmatch(r"[+-]\d{1,3}%", s):
            s = "+0%"
        tts[key] = s
    tts["auto_read"] = bool(tts.get("auto_read", True))
    return out


def voices_payload() -> list[dict]:
    return list(VOICES)


def clean_for_speech(text: str, limit: int = 4000) -> str:
    s = str(text or "")
    s = re.sub(r"```.*?```", " ", s, flags=re.S)
    s = re.sub(r"`([^`]*)`", r"\1", s)
    s = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", s)
    s = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"https?://\S+", " ", s)
    s = re.sub(r"^\s{0,3}#{1,6}\s*", "", s, flags=re.M)
    s = re.sub(r"^\s*[-+*]\s+", "", s, flags=re.M)
    s = re.sub(r"[*_>|~#]", " ", s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()[:limit]


async def synthesize(text: str, voice: str, rate: str, volume: str) -> tuple[bytes, str]:
    cleaned = clean_for_speech(text)
    if not cleaned:
        raise ValueError("没有可朗读的文字")
    import edge_tts

    last_err = None
    for attempt in range(2):
        try:
            comm = edge_tts.Communicate(cleaned, voice, rate=rate, volume=volume)
            buf = bytearray()
            async for chunk in comm.stream():
                if chunk.get("type") == "audio" and chunk.get("data"):
                    buf.extend(chunk["data"])
            if not buf:
                raise RuntimeError("Edge TTS 没有返回音频")
            return bytes(buf), cleaned
        except Exception as e:
            last_err = e
            if attempt == 0:
                await asyncio.sleep(0.5)
    raise RuntimeError(f"Edge TTS 合成失败：{last_err}")
