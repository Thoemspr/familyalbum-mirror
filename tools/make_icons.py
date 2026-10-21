"""Draw the PWA icons of the gallery into site/: a tilted polaroid with a family (two adults,
a child, a heart) on the gallery's dark panel colour, gold like the gallery accent."""
from pathlib import Path
from PIL import Image, ImageDraw

SITE = Path(__file__).resolve().parent.parent / "site"
BG, PAPER, INK, ACCENT, TERRA = "#1b1d21", "#f4f1ea", "#2a2d33", "#d8a657", "#c8735a"
SKY_TOP, SKY_BOTTOM = (0xF6, 0xD4, 0x9A), (0xD8, 0xA6, 0x57)
K = 4  # supersampling


def person(d, at, head_r, body, fill):
    """Head circle plus a body with a rounded top; body = (left, top, right, bottom)."""
    (hx, hy), (l, t, r, b) = at, body
    arc = (r - l) * 0.47
    d.ellipse([l, t, r, t + 2 * arc], fill=fill)
    d.rectangle([l, t + arc, r, b], fill=fill)
    d.ellipse([hx - head_r, hy - head_r, hx + head_r, hy + head_r], fill=fill)


def photo(scale: float) -> Image.Image:
    """The picture inside the polaroid; local coordinates have their origin at the photo centre."""
    w, h = 232, 206
    u = K * scale
    img = Image.new("RGB", (round(w * u), round(h * u)))
    d = ImageDraw.Draw(img)
    for y in range(img.height):  # vertical sky gradient
        f = y / (img.height - 1)
        d.line([(0, y), (img.width, y)], fill=tuple(round(a + (b - a) * f) for a, b in zip(SKY_TOP, SKY_BOTTOM)))
    # Photo spans x -116..116, y -128..78 in the design grid.
    p = lambda x, y: ((x + 116) * u, (y + 128) * u)
    for hx, hy, r, l, t, rr in ((-56, -46, 22, -92, 8, -20), (56, -42, 21, 22, 12, 90)):
        person(d, p(hx, hy), r * u, (*p(l, t), *p(rr, 90)), INK)
    # The child in front, set off from the parents by a gold rim (drawn as a larger shape behind it).
    person(d, p(0, 4), 20.5 * u, (*p(-29.5, 34.5), *p(29.5, 90)), ACCENT)
    person(d, p(0, 4), 17 * u, (*p(-26, 38), *p(26, 90)), TERRA)
    for cx in (-13, 13):  # heart: two lobes and a point
        d.ellipse([*p(cx - 13, -97), *p(cx + 13, -71)], fill=TERRA)
    d.polygon([p(-25.5, -80), p(25.5, -80), p(0, -56)], fill=TERRA)
    return img


def draw(size: int, scale: float) -> Image.Image:
    s = 512 * K
    canvas = Image.new("RGB", (s, s), BG)
    layer = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    u = K * scale
    c = s / 2
    p = lambda x, y: (c + x * u, c + y * u)
    d.rounded_rectangle([*p(-136, -148), *p(136, 158)], radius=8 * u, fill=PAPER)
    layer.paste(photo(scale), tuple(round(v) for v in p(-116, -128)))
    layer = layer.rotate(5, resample=Image.BICUBIC, center=(c, c))
    canvas.paste(layer, (0, round(4 * u)), layer)
    return canvas.resize((size, size), Image.LANCZOS)


draw(192, 1.0).save(SITE / "icon-192.png")
draw(512, 1.0).save(SITE / "icon-512.png")
draw(512, 0.8).save(SITE / "icon-maskable-512.png")
draw(180, 1.0).save(SITE / "apple-touch-icon.png")
