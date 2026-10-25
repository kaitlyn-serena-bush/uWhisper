"""Draw the uWhisper logo images used by the installer (installer.iss).

Inno Setup picks the best-fitting file for the screen's DPI, so each image is
made at several scales (100% .. 300%). Run by build_installer.ps1.
"""
import os

from PIL import Image

from uwhisper_logo import draw_icon

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "installer_art")
SCALES = (100, 125, 150, 175, 200, 250, 300)


def small(scale):
    """Top-right header image: 55x55 at 100%."""
    px = round(55 * scale / 100)
    return draw_icon(px)


def large(scale):
    """Left-side image on the first/last pages: 164x314 at 100%, logo centered."""
    w, h = round(164 * scale / 100), round(314 * scale / 100)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    logo = draw_icon(round(w * 0.62))
    img.paste(logo, ((w - logo.width) // 2, (h - logo.height) // 2), logo)
    return img


def main():
    os.makedirs(OUT, exist_ok=True)
    for s in SCALES:
        small(s).save(os.path.join(OUT, f"small_{s}.png"))
        large(s).save(os.path.join(OUT, f"large_{s}.png"))
    print("installer images ->", OUT)


if __name__ == "__main__":
    main()
