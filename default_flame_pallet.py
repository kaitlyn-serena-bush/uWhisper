"""default_flame_pallet: every color uWhisper uses, in one place.

Warm roast browns for surfaces, beige for text, and fire oranges/yellows for
anything active. The window theme, the recording pill, the logo and the tray
badges all read their colors from here, so editing a value below changes it
everywhere. Run this file to regenerate the swatch image (default_flame_pallet.png).
"""

PALETTE_NAME = "default_flame_pallet"

# ---------------------------------------------------------------- surfaces (browns)
SURFACES = {
    "bg":          "#1f1713",  # window background, deep roast brown
    "card":        "#2b201a",  # raised boxes (shortcut card, value boxes)
    "hover":       "#37291f",  # row hover highlight
    "line":        "#3a2c23",  # dividers, empty meter track
    "raised":      "#45342a",  # keycaps, empty slider track
    "popup":       "#2e221b",  # dropdown list
    "popup_edge":  "#4d3a2d",  # dropdown list border
    "tip":         "#3b2c22",  # hover info bubble
    "tip_edge":    "#5a4433",  # hover info bubble border
    "scroll":      "#5a4433",  # mic list scrollbar
    "border":      "#4a382c",  # window edge
    "icon_back":   "#261a14",  # dark outline behind the logo bars
}

# ---------------------------------------------------------------- text (beiges)
TEXT = {
    "text":        "#f5e9d8",  # main text, beige
    "sub":         "#c2a68b",  # secondary text, muted beige-brown
    "dim":         "#8c725e",  # labels, hints
    "on_accent":   "#fff6ea",  # text and knobs drawn on orange
}

# ---------------------------------------------------------------- fire (accents)
FIRE = {
    "accent":      "#e8650f",  # buttons, sliders, switches, selections
    "switch_edge": "#6b3a1e",  # switch border when on, dark brown
    "ember":       "#ffb347",  # warm amber (level meter, "working")
    "bar_bottom":  "#e24816",  # logo/waveform bars: base, deep orange-red
    "bar_top":     "#ff9628",  # logo/waveform bars: tip of shorter bars
    "bar_hot":     "#ffec8c",  # logo/waveform bars: tip of tall/loud bars
}

# ---------------------------------------------------------------- status
STATUS = {
    "ready":       "#9fd46e",  # "Ready" dot
    "red":         "#ff5a3c",  # errors, "Recording" dot, loud meter
    "quit":        "#ff9070",  # Quit uWhisper text
    "rec_badge":   "#e83428",  # tray dot while recording
    "busy_badge":  "#ffaa1e",  # tray dot while transcribing
    "ok_text":     "#ffd29a",  # recording pill: "Pasted" message
    "warn_text":   "#ffc46b",  # recording pill: warnings
    "err_text":    "#ff8a70",  # recording pill: errors
}

GROUPS = {"Surfaces": SURFACES, "Text": TEXT, "Fire": FIRE, "Status": STATUS}
PALETTE = {k: v for group in GROUPS.values() for k, v in group.items()}


def rgb(name):
    """'bar_hot' -> (255, 236, 140)"""
    h = PALETTE[name].lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def dwm_color(name):
    """Color as Windows' 0x00BBGGRR (used for the window edge)."""
    r, g, b = rgb(name)
    return (b << 16) | (g << 8) | r


def save_swatches(path=None):
    """Render the palette as an image."""
    import os
    from PIL import Image, ImageDraw, ImageFont
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "default_flame_pallet.png")
    try:
        title_f = ImageFont.truetype("seguisb.ttf", 34)
        head_f = ImageFont.truetype("seguisb.ttf", 22)
        f = ImageFont.truetype("segoeui.ttf", 17)
        mono = ImageFont.truetype("consola.ttf", 16)
    except OSError:
        title_f = head_f = f = mono = ImageFont.load_default()
    cols, sw, sh_, pad = 4, 210, 150, 24
    rows = sum((len(g) + cols - 1) // cols for g in GROUPS.values())
    W = pad * 2 + cols * sw + (cols - 1) * 14
    H = 110 + rows * (sh_ + 14) + len(GROUPS) * 48 + pad
    img = Image.new("RGB", (W, H), PALETTE["bg"])
    d = ImageDraw.Draw(img)
    d.text((pad, 26), PALETTE_NAME, font=title_f, fill=PALETTE["text"])
    y = 92
    for gname, group in GROUPS.items():
        d.text((pad, y), gname.upper(), font=head_f, fill=PALETTE["dim"])
        y += 40
        for i, (name, hexv) in enumerate(group.items()):
            c, r = i % cols, i // cols
            x0, y0 = pad + c * (sw + 14), y + r * (sh_ + 14)
            d.rounded_rectangle((x0, y0, x0 + sw, y0 + sh_), radius=12, fill=PALETTE["card"],
                                outline=PALETTE["line"])
            d.rounded_rectangle((x0 + 8, y0 + 8, x0 + sw - 8, y0 + 88), radius=8, fill=hexv,
                                outline=PALETTE["raised"])
            d.text((x0 + 12, y0 + 96), name, font=f, fill=PALETTE["text"])
            d.text((x0 + 12, y0 + 120), hexv, font=mono, fill=PALETTE["sub"])
        y += ((len(group) + cols - 1) // cols) * (sh_ + 14) + 8
    img.save(path)
    return path


if __name__ == "__main__":
    print(save_swatches())
