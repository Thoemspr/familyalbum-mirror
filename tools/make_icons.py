"""Draw the PWA icons of the gallery (camera on the gallery's dark panel colour) into site/."""
from pathlib import Path
from PIL import Image, ImageDraw

SITE = Path(__file__).resolve().parent.parent / "site"
BG, FG, ACCENT = "#1b1d21", "#e9eaec", "#d8a657"


def draw(size: int, safe: float) -> Image.Image:
    s = size * 4
    img = Image.new("RGB", (s, s), BG)
    d = ImageDraw.Draw(img)
    u = s * safe / 100
    o = (s - s * safe) / 2
    p = lambda x, y: (o + x * u, o + y * u)
    d.rounded_rectangle([p(30, 16), p(56, 30)], radius=3 * u, fill=FG)        # viewfinder hump
    d.rounded_rectangle([p(6, 24), p(94, 86)], radius=12 * u, fill=FG)        # body
    d.ellipse([p(28, 32), p(72, 76)], fill=BG)                                # lens ring
    d.ellipse([p(35, 39), p(65, 69)], fill=ACCENT)                            # lens
    d.ellipse([p(76, 32), p(86, 42)], fill=ACCENT)                            # flash
    return img.resize((size, size), Image.LANCZOS)


draw(192, 0.8).save(SITE / "icon-192.png")
draw(512, 0.8).save(SITE / "icon-512.png")
draw(512, 0.6).save(SITE / "icon-maskable-512.png")
draw(180, 0.8).save(SITE / "apple-touch-icon.png")
