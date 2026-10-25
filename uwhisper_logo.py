"""uWhisper's logo: chunky equalizer bars shaped like a flame with two spikes
(a short one on the left, a tall one on the right). No background.

Drawn in code (large, then shrunk) so it stays crisp at every size.
Run this file to rebuild uWhisper.ico and logo.png.
"""
import math
import os

from PIL import Image, ImageDraw

from default_flame_pallet import rgb

HERE = os.path.dirname(os.path.abspath(__file__))

BAR_HOT_TOP = rgb("bar_hot")     # tall bars: yellow tips...
BAR_TOP = rgb("bar_top")         # shorter bars: orange tips...
BAR_BOTTOM = rgb("bar_bottom")   # ...all fading to deep orange-red
# Bar heights (0..1): small spike on the left, dip, tall spike on the right.
HEIGHTS = [0.27, 0.54, 0.72, 0.52, 0.80, 0.96, 0.74, 0.38]
# Tiny sizes (taskbar at 16-24px) get fewer, fatter bars so the two spikes stay readable.
HEIGHTS_SMALL = [0.46, 0.78, 0.52, 0.98, 0.6]  # (used up to 72 px)

# kept for logo_options.py (the comparison sheet)
TILE_TOP, TILE_BOTTOM = (255, 240, 219), (240, 211, 172)


def _vgradient(size, top, bottom):
    w, h = size
    img = Image.new("RGBA", size)
    px = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(1, h - 1)
        px.line([(0, y), (w, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)) + (255,))
    return img


def _flame_points(cx, cy, w, h, lean=0.0, n=160):
    """Teardrop flame shape (used by the comparison sheet)."""
    pts = []
    for i in range(n):
        t = 2 * math.pi * i / n
        x = math.sin(t) * (math.sin(t / 2) ** 1.25) * w / 2
        y = -math.cos(t) * h / 2
        up = (h / 2 - y) / h
        x += lean * w * up ** 2.2
        pts.append((cx + x, cy + y))
    return pts


def _paste_shape(base, pts, top, bottom):
    """Fill a polygon with a vertical gradient (used by the comparison sheet)."""
    mask = Image.new("L", base.size, 0)
    ImageDraw.Draw(mask).polygon(pts, fill=255)
    ys = [p[1] for p in pts]
    grad = _vgradient(base.size, top, bottom)
    y0, y1 = int(min(ys)), int(max(ys))
    band = _vgradient((base.size[0], max(1, y1 - y0)), top, bottom)
    grad.paste(band, (0, y0))
    base.paste(grad, (0, 0), mask)


def _dark_fade(N):
    """Dark-brown rounded-square backdrop: solid in the middle, fading to clear at the edges."""
    import numpy as np
    yy, xx = np.mgrid[0:N, 0:N].astype(np.float32) + 0.5
    c = N / 2
    r = (np.abs(xx - c) ** 4 + np.abs(yy - c) ** 4) ** 0.25 / (N / 2)   # 0 center, 1 at edges
    t = np.clip((r - 0.62) / (1.0 - 0.62), 0, 1)                       # solid, then fade out
    alpha = (1 - t * t * (3 - 2 * t)) * 235
    rgba = np.zeros((N, N, 4), np.uint8)
    rgba[..., 0], rgba[..., 1], rgba[..., 2] = 38, 26, 20
    rgba[..., 3] = alpha.astype(np.uint8)
    return Image.fromarray(rgba, "RGBA")


def _layout(n, area, N, small):
    """Whole-pixel bar width and gap that fill `area` and leave equal space on
    both sides (so the bars are centered exactly and still crisp)."""
    ideal_gap = max(1, round(area * (0.07 if small else 0.045)))
    for bw in range(max(1, int((area - (n - 1) * ideal_gap) / n)) + 1, 0, -1):
        for gap in (ideal_gap, ideal_gap + 1, ideal_gap - 1):
            total = n * bw + (n - 1) * gap
            if gap >= 1 and total <= area + 2 and (N - total) % 2 == 0:
                return bw, gap, total
    bw = max(1, int((area - (n - 1) * ideal_gap) / n))
    return bw, ideal_gap, n * bw + (n - 1) * ideal_gap


def draw_logo(size=256, badge=None, backdrop=True, pad=None):
    """The logo at exactly `size` px, drawn for that size (not shrunk from a big
    picture) with bars on whole pixels, so it stays sharp.
    badge: None, 'rec' (red dot) or 'busy' (amber dot).
    backdrop=False: just the bars (used in the app window's header)."""
    N = size
    small = N <= 72  # tray, small taskbar sizes: fewer, chunkier bars
    heights = HEIGHTS_SMALL if small else HEIGHTS
    n = len(heights)

    # Bars sit inside the middle of the icon, leaving room for the backdrop's fade.
    if pad is None:
        pad = 0.10 if N <= 20 else 0.17 if small else 0.22
    pad *= N
    area = N - 2 * pad
    bw, gap, total = _layout(n, area, N, small)
    x0 = (N - total) // 2

    # Vertically center the group: tallest bar's top and the shared baseline are
    # the same distance from the edges.
    K = 8  # supersample the bars
    tallest = max(heights) * area
    base_k = (N + tallest) / 2 * K
    bars = Image.new("RGBA", (N * K, N * K), (0, 0, 0, 0))
    for i, h in enumerate(heights):
        bh_k = max(bw * K, round(area * h * K))
        x, y = (x0 + i * (bw + gap)) * K, round(base_k - bh_k)
        grad = _vgradient((bw * K, bh_k), BAR_HOT_TOP if h > 0.7 else BAR_TOP, BAR_BOTTOM)
        mask = Image.new("L", grad.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, bw * K - 1, bh_k - 1), radius=bw * K // 2, fill=255)
        bars.paste(grad, (x, y), mask)
    if badge:
        d = ImageDraw.Draw(bars)
        color = (232, 52, 40) if badge == "rec" else (255, 170, 30)
        sk = N * K
        d.ellipse((sk * 0.62, sk * 0.62, sk * 0.99, sk * 0.99), fill=(31, 23, 19, 255))
        d.ellipse((sk * 0.66, sk * 0.66, sk * 0.95, sk * 0.95), fill=color)

    img = _dark_fade(N) if backdrop else Image.new("RGBA", (N, N), (0, 0, 0, 0))
    img.alpha_composite(bars.resize((N, N), Image.BOX))
    return img


def draw_icon(size, badge=None):
    """Largest version of the icon that fits `size` px without touching the edges."""
    import numpy as np
    extra = 0
    while True:  # fit the bars + outline first (the badge dot may sit on the corner)
        img = _draw_icon(size, None, extra)
        a = np.asarray(img)[..., 3]
        edge = max(a[0].max(), a[-1].max(), a[:, 0].max(), a[:, -1].max())
        if edge <= 40 or extra >= size // 4:
            break
        extra += 1  # shrink by one pixel per side and try again
    return _draw_icon(size, badge, extra) if badge else img


def _draw_icon(size, badge=None, extra=0):
    """uWhisper's icon (taskbar, tray, window header, Start Menu): the bars with a
    dark outline that hugs them and a smooth straight bottom edge; transparent
    everywhere else. Drawn natively at `size` px (supersampled, on the pixel grid)
    so it's sharp. badge: None, 'rec' (red dot) or 'busy' (amber dot)."""
    from PIL import ImageFilter
    N = size
    K = max(2, min(8, 2048 // N))     # supersampling (less for huge sizes)
    heights = HEIGHTS_SMALL
    n = len(heights)
    # As big as possible: leave just enough room for the outline (~4.5% of the
    # icon) plus one pixel, so nothing gets clipped at the edges.
    pad = 0.05 * N + 1 + extra
    area = N - 2 * pad
    bw, gap, total = _layout(n, area, N, True)
    x0 = (N - total) // 2
    base_k = (N + max(heights) * area) / 2 * K

    bars = Image.new("RGBA", (N * K, N * K), (0, 0, 0, 0))
    left = right = bottom = None
    for i, h in enumerate(heights):
        bh = max(bw * K, round(area * h * K))
        x, y = (x0 + i * (bw + gap)) * K, round(base_k - bh)
        grad = _vgradient((bw * K, bh), BAR_HOT_TOP if h > 0.7 else BAR_TOP, BAR_BOTTOM)
        m = Image.new("L", grad.size, 0)
        ImageDraw.Draw(m).rounded_rectangle((0, 0, bw * K - 1, bh - 1), radius=bw * K // 2, fill=255)
        bars.paste(grad, (x, y), m)
        left = x if left is None else left
        right, bottom = x + bw * K, y + bh

    # Dark outline: the bars' shape, filled in along the bottom, then grown outward.
    shape = bars.getchannel("A").copy()
    ImageDraw.Draw(shape).rectangle((left, bottom - bw * K, right - 1, bottom - 1), fill=255)
    r = max(1.2 * K, N * K * 0.05)
    shape = shape.filter(ImageFilter.GaussianBlur(r * 0.6)).point(lambda v: 255 if v > 18 else 0)
    ImageDraw.Draw(shape).rectangle((0, bottom + r * 0.8, N * K, N * K), fill=0)  # straight bottom edge
    shape = shape.filter(ImageFilter.GaussianBlur(K * 0.4))
    backing = Image.new("RGBA", bars.size, rgb("icon_back") + (0,))
    backing.putalpha(shape.point(lambda v: v * 235 // 255))
    backing.alpha_composite(bars)
    if badge:
        d = ImageDraw.Draw(backing)
        color = rgb("rec_badge") if badge == "rec" else rgb("busy_badge")
        sk = N * K
        d.ellipse((sk * 0.60, sk * 0.60, sk * 1.0, sk * 1.0), fill=rgb("bg") + (255,))
        d.ellipse((sk * 0.65, sk * 0.65, sk * 0.95, sk * 0.95), fill=color)
    return backing.resize((N, N), Image.BOX)


# Every size Windows may ask for, including high-DPI ones (125%-300% scaling).
ICO_SIZES = [16, 20, 24, 28, 30, 32, 36, 40, 42, 48, 56, 60, 64, 72, 80, 96, 128, 256]


def build_files():
    draw_logo(256).save(os.path.join(HERE, "logo.png"))
    draw_icon(1024).save(os.path.join(HERE, "logo_1024.png"))   # high-res app icon
    imgs = {s: draw_icon(s) for s in ICO_SIZES}
    imgs[256].save(os.path.join(HERE, "uWhisper.ico"), sizes=[(s, s) for s in ICO_SIZES],
                   append_images=[imgs[s] for s in ICO_SIZES if s != 256])


if __name__ == "__main__":
    build_files()
    print("wrote logo.png, logo_1024.png and uWhisper.ico")
