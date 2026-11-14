"""Generate PWA / apple-touch icons with Pillow: python scripts/make_icons.py"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent.parent / "docs" / "icons"


def font(size):
    for p in ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "/System/Library/Fonts/Supplemental/Arial Bold.ttf", "DejaVuSans-Bold.ttf"]:
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


def icon(size, maskable=False, rounded=False):
    s = size * 4  # supersample
    img = Image.new("RGB", (s, s), (11, 15, 23))
    d = ImageDraw.Draw(img)
    # diagonal gradient background tile
    inset = int(s * (0.10 if maskable else 0.0))
    grad = Image.new("RGB", (s, s))
    gp = grad.load()
    for y in range(s):
        for x in range(0, s, 4):
            t = (x + y) / (2 * s)
            c = (int(56 + (34 - 56) * t), int(189 + (197 - 189) * t), int(248 + (94 - 248) * t))
            for k in range(4):
                if x + k < s:
                    gp[x + k, y] = c
    mask = Image.new("L", (s, s), 0)
    md = ImageDraw.Draw(mask)
    r = int(s * 0.22) if rounded else 0
    md.rounded_rectangle([inset, inset, s - inset, s - inset], radius=r, fill=255)
    img.paste(grad, (0, 0), mask)
    # rising chart line
    pts = [(0.18, 0.72), (0.36, 0.58), (0.50, 0.64), (0.66, 0.42), (0.82, 0.30)]
    pts = [(inset + (s - 2 * inset) * x, inset + (s - 2 * inset) * y) for x, y in pts]
    d.line(pts, fill=(4, 18, 28), width=int(s * 0.045), joint="curve")
    for p in pts[-1:]:
        rr = s * 0.04
        d.ellipse([p[0] - rr, p[1] - rr, p[0] + rr, p[1] + rr], fill=(4, 18, 28))
    f = font(int((s - 2 * inset) * 0.42))
    d.text((inset + (s - 2 * inset) * 0.17, inset + (s - 2 * inset) * 0.06), "K", font=f, fill=(4, 18, 28))
    return img.resize((size, size), Image.LANCZOS)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    icon(180).save(OUT / "apple-touch-icon.png")      # iOS applies its own rounding
    icon(192, rounded=True).save(OUT / "icon-192.png")
    icon(512, rounded=True).save(OUT / "icon-512.png")
    icon(512, maskable=True).save(OUT / "icon-512-maskable.png")
    icon(32, rounded=True).save(OUT / "favicon-32.png")
    print("icons written to", OUT)
