"""Gulliver's Hoard icon: the family dragon (from Phileas's icon) recoloured to the app's green, the box glyph replaced by a gold relief tile.

Usage: python scripts/make_icon.py "..\\Icons\\Phileas hoard.png" app-icon-1254.png
"""
import colorsys, sys
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

SRC = sys.argv[1]; OUT = sys.argv[2]
TARGET_HUE = 117 / 360.0

im = Image.open(SRC).convert("RGBA")
a = np.asarray(im).astype(np.float32) / 255.0
rgb = a[..., :3]
mx, mn = rgb.max(2), rgb.min(2)
v = mx
s = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0)
# hue
r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
d = np.maximum(mx - mn, 1e-6)
h = np.where(mx == r, ((g - b) / d) % 6, np.where(mx == g, (b - r) / d + 2, (r - g) / d + 4)) / 6.0
H, W = h.shape
gold = (h > 25 / 360) & (h < 60 / 360) & (s > 0.3) & (v > 0.3)
# only the glyph area (centre), not stray pixels elsewhere
yy, xx = np.mgrid[0:H, 0:W]
gold &= (xx > 0.2 * W) & (xx < 0.8 * W) & (yy > 0.3 * H) & (yy < 0.85 * H)
mask = Image.fromarray((gold * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(15))
m = np.asarray(mask) > 0
print("gold px", int(gold.sum()), "mask", int(m.sum()))
# sample gold palette
gp = rgb[gold & (v > 0.6)]
light = np.percentile(gp, 85, axis=0); mid = np.median(gp, axis=0); dark = np.percentile(gp, 15, axis=0)
print("gold", light * 255, mid * 255, dark * 255)
# fill glyph area with the background around it (median of a ring outside the mask)
ring = (np.asarray(mask.filter(ImageFilter.MaxFilter(41))) > 0) & ~m & (v < 0.16)
bg = np.median(rgb[ring], axis=0)
print("bg", bg * 255)
out = rgb.copy()
out[m] = bg
# recolour the dragon: teal-ish saturated pixels -> target hue
teal = (h > 140 / 360) & (h < 200 / 360) & (s > 0.15) & ~m
hh = np.where(teal, TARGET_HUE, h)
ss = np.where(teal, np.clip(s * 0.82, 0, 1), s)
vv = np.where(teal, np.clip(v * 0.98, 0, 1), v)
# hsv -> rgb vectorised
i = np.floor(hh * 6).astype(int) % 6
f = hh * 6 - np.floor(hh * 6)
p = vv * (1 - ss); q = vv * (1 - f * ss); t = vv * (1 - (1 - f) * ss)
conv = np.stack([np.choose(i, [vv, q, p, p, t, vv]), np.choose(i, [t, vv, vv, q, p, p]), np.choose(i, [p, p, t, vv, vv, q])], -1)
out = np.where(teal[..., None], conv, out)
base = Image.fromarray((np.clip(out, 0, 1) * 255).astype(np.uint8)).convert("RGBA")

# the glyph: a gold relief tile split in two pieces, a mountain on it, drawn 4x and scaled down
S = 4
cx, cy = int(0.53 * W) * S, int(0.64 * H) * S
glyph = Image.new("RGBA", (W * S, H * S), (0, 0, 0, 0))
dr = ImageDraw.Draw(glyph)
L = tuple(int(c * 255) for c in light); M = tuple(int(c * 255) for c in mid); D = tuple(int(c * 255) for c in dark)
LL = tuple(min(255, int(c * 255 * 1.08)) for c in light)
SHADE = (196, 140, 62); DEEP = (160, 108, 44)
w = 0.17 * W * S
hgt = 0.095 * H * S
thick = 0.065 * H * S
def P(u, v, z=0.0):
    # u along the left edge (back-left -> front), v along the right edge; z up
    x = cx + (v - u) * w
    y = cy - hgt + (u + v) * hgt - z
    return (x, y)
gapc = (8, 12, 24, 255)
# side faces
dr.polygon([P(0, 1), P(1, 1), (P(1, 1)[0], P(1, 1)[1] + thick), (P(0, 1)[0], P(0, 1)[1] + thick)], fill=DEEP)
dr.polygon([P(1, 0), P(1, 1), (P(1, 1)[0], P(1, 1)[1] + thick), (P(1, 0)[0], P(1, 0)[1] + thick)], fill=SHADE)
# top face
dr.polygon([P(0, 0), P(1, 0), P(1, 1), P(0, 1)], fill=M)
# seam between the two tiles (u = 0.5 across the whole block, top and side)
lw = int(0.009 * W * S)
dr.line([P(0.5, 0), P(0.5, 1), (P(0.5, 1)[0], P(0.5, 1)[1] + thick)], fill=gapc, width=lw)
# terrain: a range along the horizontal diagonal of the top face (left corner -> right corner), towards the back
import math
def hfun(t):
    return (0.20 * H * S * math.exp(-((t - 0.40) / 0.16) ** 2) + 0.12 * H * S * math.exp(-((t - 0.70) / 0.12) ** 2)
            + 0.03 * H * S * math.exp(-((t - 0.15) / 0.08) ** 2))
ts = [i / 80 for i in range(81)]
off_back, off_front = -0.10, 0.18
ridge = [P(1 - t + off_back, t + off_back, hfun(t)) for t in ts]
base_front = [P(1 - t + off_front, t + off_front) for t in reversed(ts)]
dr.polygon(ridge + base_front, fill=L)
for peak, valley in ((0.40, 0.57), (0.70, 0.97)):
    seg = [t for t in ts if peak <= t <= valley]
    pts = [P(1 - t + off_back, t + off_back, hfun(t)) for t in seg]
    dr.polygon(pts + [P(1 - valley + off_front, valley + off_front), P(1 - peak + off_front * 0.4, peak + off_front * 0.4)], fill=SHADE)
# river
river = [P(0.05, 0.92), P(0.3, 0.86), P(0.55, 0.9), P(0.8, 0.84), P(0.97, 0.88)]
dr.line(river, fill=(70, 120, 170, 255), width=int(0.011 * W * S), joint="curve")
glyph = glyph.resize((W, H), Image.LANCZOS)
final = Image.alpha_composite(base, glyph)
final.save(OUT)
print("saved", OUT)
