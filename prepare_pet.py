# -*- coding: utf-8 -*-
"""背景去除 v2：边缘洪水填充 + 保留最大连通域（角色本体）+ 腐蚀柔边"""
import sys
from collections import Counter
from PIL import Image, ImageFilter

SRC = "original.png"
OUT = "pet.png"
ICO = "pet.ico"

img = Image.open(SRC)
print("original:", img.mode, img.size)

def analyze(seen_removed, w, h):
    """对保留像素做连通域分析，返回 (最大块面积占比, 中心点是否在最大块)"""
    removed = seen_removed
    label = [0] * (w * h)
    comps = []
    for sy in range(0, h, 1):
        for sx in range(0, w, 1):
            i = sy * w + sx
            if removed[i] or label[i]:
                continue
            # BFS
            stack = [(sx, sy)]
            label[i] = len(comps) + 1
            count = 0
            while stack:
                x, y = stack.pop()
                count += 1
                for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                    if 0 <= nx < w and 0 <= ny < h:
                        j = ny * w + nx
                        if not removed[j] and not label[j]:
                            label[j] = len(comps) + 1
                            stack.append((nx, ny))
            comps.append(count)
    if not comps:
        return 0.0, False
    biggest = max(comps)
    center_i = (h // 2) * w + (w // 2)
    center_in = (label[center_i] == comps.index(biggest) + 1)
    return biggest / (w * h) * 100, center_in, len(comps)


img_rgba = img.convert("RGBA")
w, h = img_rgba.size
px = img_rgba.load()

# 边缘色直方图
ring = []
for x in range(w):
    for y in list(range(6)) + list(range(h - 6, h)):
        ring.append(px[x, y][:3])
for y in range(h):
    for x in list(range(6)) + list(range(w - 6, w)):
        ring.append(px[x, y][:3])

def quant(c, q=10):
    return (c[0] // q * q, c[1] // q * q, c[2] // q * q)

bg = Counter(quant(c) for c in ring).most_common(1)[0][0]
print("border dominant color(quantized):", bg)

def close(c, tol):
    return max(abs(c[0] - bg[0]), abs(c[1] - bg[1]), abs(c[2] - bg[2])) <= tol

def flood(tol):
    seen = bytearray(w * h)
    stack = []
    for x in range(w):
        for y in (0, h - 1):
            if close(px[x, y][:3], tol):
                seen[y * w + x] = 1
                stack.append((x, y))
    for y in range(h):
        for x in (0, w - 1):
            if close(px[x, y][:3], tol):
                seen[y * w + x] = 1
                stack.append((x, y))
    while stack:
        x, y = stack.pop()
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= nx < w and 0 <= ny < h:
                j = ny * w + nx
                if not seen[j] and close(px[nx, ny][:3], tol):
                    seen[j] = 1
                    stack.append((nx, ny))
    return seen

results = {}
for tol in (25, 30, 35, 40, 45, 50):
    seen = flood(tol)
    removed_pct = sum(seen) / (w * h) * 100
    biggest_pct, center_in, ncomps = analyze(seen, w, h)
    results[tol] = (seen, removed_pct, biggest_pct, center_in, ncomps)
    print(f"  tol={tol}: removed={removed_pct:.1f}%  biggest_kept={biggest_pct:.1f}%  "
          f"center_in={center_in}  comps={ncomps}")

# 选容差：从大到小，选第一个满足安全条件的（中心点在最大块、最大块>=20%、块数不爆炸）
chosen = None
for tol in sorted(results, reverse=True):
    _, removed_pct, biggest_pct, center_in, ncomps = results[tol]
    if center_in and biggest_pct >= 20 and ncomps <= 800:
        chosen = tol
        break
if chosen is None:
    print("ERROR: no safe tolerance")
    sys.exit(1)
print("chosen tolerance:", chosen)
seen, removed_pct, biggest_pct, center_in, ncomps = results[chosen]

# 保留最大连通域，清除孤立噪点
label = [0] * (w * h)
comp_id = 1
keep_id = 0
for sy in range(0, h):
    for sx in range(0, w):
        i = sy * w + sx
        if seen[i] or label[i]:
            continue
        stack = [(sx, sy)]
        label[i] = comp_id
        count = 0
        while stack:
            x, y = stack.pop()
            count += 1
            for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if 0 <= nx < w and 0 <= ny < h:
                    j = ny * w + nx
                    if not seen[j] and not label[j]:
                        label[j] = comp_id
                        stack.append((nx, ny))
        if count > biggest_pct / 100 * w * h * 0.5:
            keep_id = comp_id
        comp_id += 1

final_removed = bytearray(w * h)
for i in range(w * h):
    if seen[i]:
        final_removed[i] = 1
    elif label[i] and label[i] != keep_id:
        final_removed[i] = 1  # 孤立小块也去掉
alpha = bytes(0 if final_removed[i] else 255 for i in range(w * h))
mask = Image.frombytes("L", (w, h), alpha)
mask = mask.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MinFilter(3))
mask = mask.filter(ImageFilter.GaussianBlur(0.6))
out = img_rgba.copy()
out.putalpha(mask)

bbox = out.split()[3].point(lambda p: 255 if p > 10 else 0).getbbox()
print("content bbox:", bbox)
pad = 14
bbox = (max(0, bbox[0] - pad), max(0, bbox[1] - pad),
        min(w, bbox[2] + pad), min(h, bbox[3] + pad))
out = out.crop(bbox)
out.save(OUT)
print("saved", OUT, out.size)
out.save(ICO, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("saved", ICO)
print("DONE")
