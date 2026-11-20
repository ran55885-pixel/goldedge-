"""Render the neon-gold Aurum Nexus logo to assets/icon.png (512px) and assets/icon.ico (multi-size)."""
import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent


def hexagon(cx, cy, r):
    return [(cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a))) for a in range(-90, 270, 60)]


def render(size=512) -> Image.Image:
    S = size
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    g = ImageDraw.Draw(glow)
    c = S / 2
    g.polygon(hexagon(c, c, S * 0.46), outline=(245, 197, 66, 255), width=int(S * 0.05))
    g.line([(S * 0.31, S * 0.72), (c, S * 0.27), (S * 0.69, S * 0.72)], fill=(245, 197, 66, 255), width=int(S * 0.08),
           joint="curve")
    glow = glow.filter(ImageFilter.GaussianBlur(S * 0.03))
    img.alpha_composite(glow)
    d = ImageDraw.Draw(img)
    d.polygon(hexagon(c, c, S * 0.44), fill=(4, 14, 34, 255))
    d.polygon(hexagon(c, c, S * 0.46), outline=(250, 210, 90, 255), width=int(S * 0.04))
    d.polygon(hexagon(c, c, S * 0.36), outline=(0, 229, 255, 150), width=max(1, int(S * 0.01)))
    gold = (247, 205, 80, 255)
    d.line([(S * 0.31, S * 0.72), (c, S * 0.27), (S * 0.69, S * 0.72)], fill=gold, width=int(S * 0.07), joint="curve")
    d.line([(S * 0.39, S * 0.58), (S * 0.61, S * 0.58)], fill=gold, width=int(S * 0.055))
    r = S * 0.04
    d.ellipse([c - r, S * 0.27 - r, c + r, S * 0.27 + r], fill=(0, 229, 255, 255))
    d.ellipse([S * 0.31 - r * .7, S * 0.72 - r * .7, S * 0.31 + r * .7, S * 0.72 + r * .7], fill=(57, 255, 20, 255))
    d.ellipse([S * 0.69 - r * .7, S * 0.72 - r * .7, S * 0.69 + r * .7, S * 0.72 + r * .7], fill=(255, 61, 242, 255))
    return img


def main():
    out = ROOT / "assets"
    out.mkdir(exist_ok=True)
    img = render(512)
    img.save(out / "icon.png")
    img.save(out / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("written", out / "icon.png", out / "icon.ico")


if __name__ == "__main__":
    sys.exit(main())
