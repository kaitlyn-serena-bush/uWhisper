"""Mock-ups: four taskbar icon styles where the dark backing hugs the bars
(rest transparent). Renders a comparison sheet; changes nothing in the app."""
import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from uwhisper_logo import (BAR_BOTTOM, BAR_HOT_TOP, BAR_TOP, HEIGHTS_SMALL, _layout, _vgradient)

HERE = os.path.dirname(os.path.abspath(__file__))
DARK = (38, 26, 20)
K = 8


def bars_layer(N, pad):
    """Bars at K x resolution plus their alpha mask (crisp, centered)."""
    heights = HEIGHTS_SMALL
    n = len(heights)
    p = pad * N
    area = N - 2 * p
    bw, gap, total = _layout(n, area, N, True)
    x0 = (N - total) // 2
    tallest = max(heights) * area
    base_k = (N + tallest) / 2 * K
    img = Image.new("RGBA", (N * K, N * K), (0, 0, 0, 0))
    boxes = []
    for i, h in enumerate(heights):
        bh = max(bw * K, round(area * h * K))
        x, y = (x0 + i * (bw + gap)) * K, round(base_k - bh)
        grad = _vgradient((bw * K, bh), BAR_HOT_TOP if h > 0.7 else BAR_TOP, BAR_BOTTOM)
        m = Image.new("L", grad.size, 0)
        ImageDraw.Draw(m).rounded_rectangle((0, 0, bw * K - 1, bh - 1), radius=bw * K // 2, fill=255)
        img.paste(grad, (x, y), m)
        boxes.append((x, y, x + bw * K, y + bh))
    return img, boxes, bw


def grow(mask, r):
    """Expand a mask outward by about r pixels with rounded corners."""
    return mask.filter(ImageFilter.GaussianBlur(r * 0.6)).point(lambda v: 255 if v > 18 else 0)


def compose(N, backing):
    out = Image.new("RGBA", (N * K, N * K), (0, 0, 0, 0))
    out.alpha_composite(backing)
    return out


def option_a(N):  # Hug: dark outline around each bar, merged into one connected shape
    bars, boxes, bw = bars_layer(N, 0.24)
    shape = grow(bars.getchannel("A"), N * K * 0.075)
    backing = Image.new("RGBA", bars.size, DARK + (0,))
    backing.putalpha(shape.point(lambda v: v * 235 // 255))
    img = compose(N, backing); img.alpha_composite(bars)
    return img.resize((N, N), Image.BOX)


def option_b(N):  # Plinth: hugging outline + a solid base joining the bottoms
    bars, boxes, bw = bars_layer(N, 0.24)
    m = bars.getchannel("A").copy()
    d = ImageDraw.Draw(m)
    x0 = min(b[0] for b in boxes); x1 = max(b[2] for b in boxes); yb = max(b[3] for b in boxes)
    d.rectangle((x0, yb - bw * K, x1, yb), fill=255)
    shape = grow(m, N * K * 0.08)
    backing = Image.new("RGBA", bars.size, DARK + (0,))
    backing.putalpha(shape.point(lambda v: v * 235 // 255))
    img = compose(N, backing); img.alpha_composite(bars)
    return img.resize((N, N), Image.BOX)


def option_c(N):  # Soft glow: dark haze hugging the bars, fading out
    bars, boxes, bw = bars_layer(N, 0.24)
    shape = grow(bars.getchannel("A"), N * K * 0.05).filter(ImageFilter.GaussianBlur(N * K * 0.06))
    backing = Image.new("RGBA", bars.size, DARK + (0,))
    backing.putalpha(shape.point(lambda v: min(235, v * 300 // 255)))
    img = compose(N, backing); img.alpha_composite(bars)
    return img.resize((N, N), Image.BOX)


def option_d(N):  # Tight tile: small rounded rectangle just around the bar group
    bars, boxes, bw = bars_layer(N, 0.26)
    x0 = min(b[0] for b in boxes); x1 = max(b[2] for b in boxes)
    y0 = min(b[1] for b in boxes); y1 = max(b[3] for b in boxes)
    p = N * K * 0.08
    backing = Image.new("RGBA", bars.size, (0, 0, 0, 0))
    ImageDraw.Draw(backing).rounded_rectangle((x0 - p, y0 - p, x1 + p, y1 + p), radius=p * 1.6,
                                              fill=DARK + (235,))
    img = compose(N, backing); img.alpha_composite(bars)
    return img.resize((N, N), Image.BOX)


OPTIONS = [
    ("A  Hug", "Dark outline follows each bar, all joined", option_a),
    ("B  Plinth", "Outline + solid base under the bars", option_b),
    ("C  Soft glow", "Dark haze around the bars, fades out", option_c),
    ("D  Tight tile", "Small rounded box just around the bars", option_d),
]


def hug(r):
    """Option A with a chosen outline thickness (fraction of the icon size)."""
    def fn(N):
        bars, boxes, bw = bars_layer(N, 0.24)
        shape = grow(bars.getchannel("A"), max(1.2 * K, N * K * r))
        backing = Image.new("RGBA", bars.size, DARK + (0,))
        backing.putalpha(shape.point(lambda v: v * 235 // 255))
        img = compose(N, backing); img.alpha_composite(bars)
        return img.resize((N, N), Image.BOX)
    return fn


def hug_flat(r, band, square_bottoms=False):
    """A1-style outline, but the bottom edge is one straight, smooth line:
    the gaps between bar bottoms are filled up to `band` x bar width."""
    def fn(N):
        bars, boxes, bw = bars_layer(N, 0.24)
        if square_bottoms:  # flat-bottomed bars for a cleaner base
            d = ImageDraw.Draw(bars)
            for (x0, y0, x1, y1), h in zip(boxes, HEIGHTS_SMALL):
                color = BAR_BOTTOM + (255,)
                d.rectangle((x0, y1 - (x1 - x0) // 2, x1 - 1, y1 - 1), fill=color)
        m = bars.getchannel("A").copy()
        x0 = min(b[0] for b in boxes); x1 = max(b[2] for b in boxes); yb = max(b[3] for b in boxes)
        ImageDraw.Draw(m).rectangle((x0, yb - band * bw * K, x1 - 1, yb - 1), fill=255)
        rr = max(1.2 * K, N * K * r)
        shape = grow(m, rr)
        # cut the grown shape flat just below the bars so the bottom is a straight line
        ImageDraw.Draw(shape).rectangle((0, yb + rr * 0.8, N * K, N * K), fill=0)
        shape = shape.filter(ImageFilter.GaussianBlur(K * 0.4))
        backing = Image.new("RGBA", bars.size, DARK + (0,))
        backing.putalpha(shape.point(lambda v: v * 235 // 255))
        img = compose(N, backing); img.alpha_composite(bars)
        return img.resize((N, N), Image.BOX)
    return fn


FLAT = [
    ("A1  (current pick)", "For comparison", hug(0.05)),
    ("F1  Smooth base", "Straight bottom edge, bars stay rounded", hug_flat(0.05, 1.0)),
    ("F2  Squared base", "Straight bottom, flat-bottomed bars", hug_flat(0.05, 1.2, square_bottoms=True)),
]


TIGHTER = [
    ("A1  Snug", "Outline about 2/3 of A", hug(0.05)),
    ("A2  Tight", "About half of A", hug(0.038)),
    ("A3  Tighter", "Thin outline, still joined", hug(0.028)),
    ("A4  Tightest", "Hairline edge around the bars", hug(0.02)),
]


def sheet(options=None, name="logo_taskbar_options.png"):
    global OPTIONS
    if options:
        OPTIONS = options
    W, H = 1640, 560
    sh = Image.new("RGBA", (W, H), (24, 18, 15, 255))
    d = ImageDraw.Draw(sh)
    big = ImageFont.truetype("seguisb.ttf", 30)
    small = ImageFont.truetype("segoeui.ttf", 20)
    for k, (title, desc, fn) in enumerate(OPTIONS):
        x = 30 + k * 400
        icon72 = fn(72)
        # zoomed 4x (nearest) on dark + light so pixel sharpness is visible
        for j, bg in enumerate(((32, 32, 32), (243, 243, 243))):
            yy = 20 + j * 150
            d.rectangle((x, yy, x + 144, yy + 144), fill=bg)
            sh.alpha_composite(icon72.resize((144, 144), Image.NEAREST), (x, yy))
        d.text((x, 322), "zoomed 2x", font=small, fill=(140, 120, 105))
        # true size on a dark and light "taskbar"
        for j, bg in enumerate(((32, 32, 32), (243, 243, 243))):
            yy = 20 + j * 150
            d.rectangle((x + 160, yy, x + 370, yy + 144), fill=bg)
            sh.alpha_composite(icon72, (x + 170, yy + 36))
            sh.alpha_composite(fn(48), (x + 255, yy + 48))
            sh.alpha_composite(fn(32), (x + 318, yy + 56))
        d.text((x + 160, 322), "real size: 72 / 48 / 32 px", font=small, fill=(140, 120, 105))
        d.text((x, 380), title, font=big, fill=(245, 233, 216))
        d.text((x, 424), desc, font=small, fill=(194, 166, 139))
    out = os.path.join(HERE, name)
    sh.convert("RGB").save(out)
    return out


if __name__ == "__main__":
    import sys
    if "flat" in sys.argv:
        print(sheet(FLAT, "logo_taskbar_flat.png"))
    else:
        print(sheet(TIGHTER, "logo_taskbar_tighter.png") if "tight" in sys.argv else sheet())
