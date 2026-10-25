"""Draws six candidate uWhisper logos (fire + sound waves) on one comparison sheet."""
import math
import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from uwhisper_logo import _flame_points, _paste_shape, _vgradient

HERE = os.path.dirname(os.path.abspath(__file__))
S = 1024

BEIGE_T, BEIGE_B = (255, 240, 219), (240, 211, 172)
ROAST_T, ROAST_B = (66, 42, 30), (31, 23, 19)
ORANGE_T, ORANGE_B = (255, 160, 60), (226, 72, 22)
OUT_T, OUT_B = (255, 150, 40), (226, 72, 22)
IN_T, IN_B = (255, 236, 140), (255, 176, 56)
BROWN = (74, 42, 24)
CREAM = (255, 246, 234)
EMBER = (255, 179, 71)


def tile(top, bottom):
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle((24, 24, S - 24, S - 24), radius=230, fill=255)
    img.paste(_vgradient((S, S), top, bottom), (0, 0), mask)
    return img


def glow(img, box, color, blur=60):
    g = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(g).ellipse(box, fill=color)
    img.alpha_composite(g.filter(ImageFilter.GaussianBlur(blur)))


def flame(img, cx, cy, w, h, lean=0.14, outer=(OUT_T, OUT_B), inner=(IN_T, IN_B)):
    _paste_shape(img, _flame_points(cx, cy, w, h, lean=lean), *outer)
    if inner:
        _paste_shape(img, _flame_points(cx + w * 0.02, cy + h * 0.18, w * 0.62, h * 0.62, lean=lean * 0.6), *inner)


def waves(d, cx, cy, radii, side=1, color=BROWN, width=46, span=42):
    for i, r in enumerate(radii):
        if side > 0:
            start, end = -span, span
        else:
            start, end = 180 - span, 180 + span
        d.arc((cx - r, cy - r, cx + r, cy + r), start=start, end=end, fill=color, width=max(10, width - i * 6))


# 1. Main pick: warm flame on beige, brown waves to the right ------------------
def opt1():
    img = tile(BEIGE_T, BEIGE_B)
    glow(img, (170, 230, 720, 960), (255, 150, 60, 110))
    flame(img, 430, 540, 560, 780)
    waves(ImageDraw.Draw(img), 560, 640, (230, 330), color=BROWN, width=50)
    return img


# 2. Night campfire: flame on roast brown, orange waves both sides -------------
def opt2():
    img = tile(ROAST_T, ROAST_B)
    glow(img, (230, 220, 790, 940), (255, 120, 30, 120), blur=80)
    flame(img, 512, 545, 470, 700)
    d = ImageDraw.Draw(img)
    waves(d, 512, 640, (270, 360), side=1, color=EMBER, width=44)
    waves(d, 512, 640, (270, 360), side=-1, color=EMBER, width=44)
    return img


# 3. Bold & simple: cream flame silhouette on an orange tile, cream waves ------
def opt3():
    img = tile(ORANGE_T, ORANGE_B)
    _paste_shape(img, _flame_points(430, 540, 540, 760, lean=0.14), CREAM, (255, 226, 196))
    _paste_shape(img, _flame_points(440, 680, 250, 330, lean=0.08), ORANGE_T, ORANGE_B)
    waves(ImageDraw.Draw(img), 560, 640, (230, 330), color=CREAM, width=52)
    return img


# 4. Equalizer fire: sound bars rising into a flame shape ---------------------
def opt4():
    img = tile(BEIGE_T, BEIGE_B)
    glow(img, (200, 260, 820, 940), (255, 150, 60, 90))
    heights = [0.22, 0.38, 0.58, 0.82, 1.0, 0.86, 0.62, 0.42, 0.26]
    n, bw, gap = len(heights), 62, 22
    x0 = (S - (n * bw + (n - 1) * gap)) / 2
    base, top = 860, 170
    d = ImageDraw.Draw(img)
    for i, hgt in enumerate(heights):
        x = x0 + i * (bw + gap)
        y = base - (base - top) * hgt
        bar = _vgradient((bw, int(base - y)), IN_T if hgt > 0.7 else OUT_T, OUT_B)
        m = Image.new("L", bar.size, 0)
        ImageDraw.Draw(m).rounded_rectangle((0, 0, bw - 1, bar.size[1] - 1), radius=bw // 2, fill=255)
        img.paste(bar, (int(x), int(y)), m)
    return img


def eq_fire(heights, bw=62, gap=22, base=860, top=170):
    """Equalizer bars on a beige tile; bar heights trace the flame's outline."""
    img = tile(BEIGE_T, BEIGE_B)
    glow(img, (180, 240, 840, 950), (255, 150, 60, 90))
    n = len(heights)
    x0 = (S - (n * bw + (n - 1) * gap)) / 2
    for i, hgt in enumerate(heights):
        x = x0 + i * (bw + gap)
        y = base - (base - top) * hgt
        bar = _vgradient((bw, int(base - y)), IN_T if hgt > 0.7 else OUT_T, OUT_B)
        m = Image.new("L", bar.size, 0)
        ImageDraw.Draw(m).rounded_rectangle((0, 0, bw - 1, bar.size[1] - 1), radius=bw // 2, fill=255)
        img.paste(bar, (int(x), int(y)), m)
    return img


# Two-spike versions: small flame tongue on the left, tall one on the right.
TWO_SPIKE = [
    ("A  Two spikes", "Balanced: gentle dip between peaks",
     lambda: eq_fire([0.18, 0.34, 0.52, 0.64, 0.52, 0.46, 0.72, 1.0, 0.82, 0.52, 0.26], bw=56, gap=18)),
    ("B  Deep valley", "Clearer split between the two spikes",
     lambda: eq_fire([0.16, 0.32, 0.5, 0.62, 0.42, 0.34, 0.66, 1.0, 0.78, 0.46, 0.22], bw=56, gap=18)),
    ("C  Chunky", "Fewer, thicker bars (reads best tiny)",
     lambda: eq_fire([0.24, 0.48, 0.64, 0.46, 0.74, 1.0, 0.7, 0.34], bw=82, gap=24)),
]


# 5. Transparent, no tile: flame with three waves (cleanest on the taskbar) ---
def opt5():
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    flame(img, 380, 520, 560, 860, lean=0.16)
    waves(ImageDraw.Draw(img), 500, 640, (230, 320, 410), color=(255, 122, 26), width=50, span=38)
    return img


# 6. Flame-wave: the flame's tip curls into sound waves rising above it -------
def opt6():
    img = tile(ROAST_T, ROAST_B)
    glow(img, (220, 330, 800, 980), (255, 120, 30, 120), blur=80)
    flame(img, 512, 640, 500, 620, lean=0.0)
    d = ImageDraw.Draw(img)
    for i, r in enumerate((120, 200, 280)):
        d.arc((512 - r, 380 - r, 512 + r, 380 + r), start=235, end=305,
              fill=EMBER if i < 2 else (255, 122, 26), width=40 - i * 6)
    return img


OPTIONS = [
    ("1  Main pick", "Warm flame, beige tile, brown waves", opt1),
    ("2  Night campfire", "Dark tile, glowing waves both sides", opt2),
    ("3  Bold & simple", "Cream flame on orange, flat", opt3),
    ("4  Equalizer fire", "Sound bars shaped like a flame", opt4),
    ("5  No tile", "Flame + 3 waves, see-through", opt5),
    ("6  Rising waves", "Waves rise from the flame tip", opt6),
]


def sheet(options=None, name="logo_options.png"):
    options = options or OPTIONS
    rows = (len(options) + 2) // 3
    W, H = 1500, 30 + rows * 530
    sh = Image.new("RGBA", (W, H), (31, 23, 19, 255))
    d = ImageDraw.Draw(sh)
    try:
        big = ImageFont.truetype("seguisb.ttf", 34)
        small = ImageFont.truetype("segoeui.ttf", 22)
    except OSError:
        big = small = ImageFont.load_default()
    for k, (title, desc, fn) in enumerate(options):
        col, row = k % 3, k // 3
        x, y = 40 + col * 490, 30 + row * 530
        art = fn()
        sh.alpha_composite(art.resize((300, 300), Image.LANCZOS), (x, y + 10))
        # taskbar-size previews on dark and light strips
        for j, (sz, bg) in enumerate(((48, (32, 32, 32)), (32, (32, 32, 32)), (16, (32, 32, 32)),
                                      (48, (243, 243, 243)), (32, (243, 243, 243)), (16, (243, 243, 243)))):
            px, py = x + 320 + (j % 3) * 0, y + 20 + j * 48
            d.rectangle((x + 318, py - 4, x + 318 + 120, py + 44), fill=bg)
            sh.alpha_composite(art.resize((sz, sz), Image.LANCZOS), (x + 324 + (48 - sz) // 2 + (0 if sz == 48 else 0), py + (40 - sz) // 2))
            d.text((x + 380, py + 8), f"{sz}px", font=small, fill=(140, 140, 140) if bg[0] < 100 else (90, 90, 90))
        d.text((x, y + 330), title, font=big, fill=(245, 233, 216))
        d.text((x, y + 378), desc, font=small, fill=(194, 166, 139))
    out = os.path.join(HERE, name)
    sh.convert("RGB").save(out)
    return out


if __name__ == "__main__":
    import sys
    if "two" in sys.argv:
        print(sheet(TWO_SPIKE, "logo_two_spike.png"))
    else:
        print(sheet())
