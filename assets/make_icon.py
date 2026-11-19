# -*- coding: utf-8 -*-
"""生成 ClawAgent.exe 的程序图标（紫→青渐变爪痕，透明底）。

用法: python assets/make_icon.py  →  assets/claw.ico
"""
from pathlib import Path

from PIL import Image, ImageDraw

S = 1024  # 超采样，缩小时自动平滑
VIOLET = (155, 140, 255)
CYAN = (43, 200, 224)


def lerp(c1, c2, t):
    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))


def stroke_points(p0, c, p1, seg=64):
    """二次贝塞尔曲线（SVG 坐标 32 系 → 像素点列）。"""
    k = S / 32
    pts = []
    for i in range(seg + 1):
        t = i / seg
        x = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * c[0] + t ** 2 * p1[0]
        y = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * c[1] + t ** 2 * p1[1]
        pts.append((x * k, y * k))
    return pts


def stamp_mask(mask_d, pts, width):
    """沿路径重叠盖圆点（无接缝，抗锯齿平滑）。"""
    import math

    r = width / 2
    step = max(1.0, r * 0.35)
    for i in range(len(pts) - 1):
        (x1, y1), (x2, y2) = pts[i], pts[i + 1]
        dist = math.hypot(x2 - x1, y2 - y1)
        n = max(1, int(dist / step))
        for j in range(n + 1):
            t = j / n
            x = x1 + (x2 - x1) * t
            y = y1 + (y2 - y1) * t
            mask_d.ellipse([x - r, y - r, x + r, y + r], fill=255)


# 单色遮罩（平滑线条）
W = int(3.4 * S / 32)
mask = Image.new("L", (S, S), 0)
md = ImageDraw.Draw(mask)
stamp_mask(md, stroke_points((9, 21.5), (6.0, 12.0), (7.8, 8)), W)       # 左爪痕
stamp_mask(md, stroke_points((16, 23), (13.0, 13.0), (14.9, 8)), W)     # 中爪痕
stamp_mask(md, stroke_points((23, 21.5), (26.0, 12.0), (21.8, 8)), W)   # 右爪痕
stamp_mask(md, stroke_points((7.5, 26.5), (16, 30.0), (24.5, 26.5)), W)  # 底弧

# 紫→青纵向渐变，经遮罩合成为最终图形
grad = Image.new("RGBA", (S, S))
gd = ImageDraw.Draw(grad)
for y in range(S):
    gd.line([(0, y), (S, y)], fill=lerp(VIOLET, CYAN, y / S))

img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
img.paste(grad, (0, 0), mask)

out = Path(__file__).resolve().parent / "claw.ico"
img.save(out, sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
print("written:", out)
