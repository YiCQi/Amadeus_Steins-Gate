"""Generate blink / lip-sync overlays from a single Kurisu portrait.

The source image (web/assets/kurisu/neutral.webp) has the mouth slightly open
and the eyes open. This script paints:
  - eyes_closed.png : eyes replaced with skin + closed-lid curves
  - mouth_closed.png: mouth replaced with skin + a small closed-lip line
Each overlay is a full-canvas transparent PNG, so the front-end can simply
stack it on top of the base image.

Skin is "pulled" from rows below the patched area using the 4-pixel scanline
period, so the CRT texture of the original still lines up.

Usage:  python tools/make_overlays.py
"""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "web" / "assets" / "kurisu"
SCANLINE_PERIOD = 4
SS = 4  # supersampling factor for anti-aliased strokes

# Regions in source-image pixels (834x1260 portrait).
LEFT_EYE_BOX = (220, 414, 358, 494)
RIGHT_EYE_BOX = (484, 412, 630, 494)
MOUTH_BOX = (386, 636, 458, 664)

LID_COLOR = (58, 36, 40)
LIP_COLOR = (120, 92, 96)


def is_hair(p):
    r, g, b = p
    return r > 22 and r > g * 1.4 and r > b * 1.3 and r < 200


def is_skin(p):
    # warm (r clearly above b) separates skin from the grey/blue eye whites
    r, g, b = p
    return r > 150 and r - b >= 7 and r - g < 60


def pull_skin(src, x, y, below_y):
    """Find a skin pixel in the same scanline phase below `below_y`."""
    k = (below_y - y) // SCANLINE_PERIOD + 1
    for step in range(k, k + 20):
        yy = y + step * SCANLINE_PERIOD
        if yy >= src.height:
            break
        for dx in (0, -3, 3, -6, 6, -10, 10):
            p = src.getpixel((min(max(x + dx, 0), src.width - 1), yy))
            if is_skin(p):
                return p
    return None


def patch_region(src, out, box, keep_hair=True, dark=420, force=False):
    x0, y0, x1, y1 = box
    for y in range(y0, y1):
        for x in range(x0, x1):
            p = src.getpixel((x, y))
            if keep_hair and is_hair(p):
                continue
            if not force and is_skin(p) and not _near_feature(src, x, y, dark):
                continue
            q = pull_skin(src, x, y, y1)
            if q:
                out.putpixel((x, y), q + (255,))


def _near_feature(src, x, y, dark, r=2):
    """True if a dark non-hair (lash / lip) pixel is within r px — catches AA fringes."""
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            xx, yy = x + dx, y + dy
            if 0 <= xx < src.width and 0 <= yy < src.height:
                p = src.getpixel((xx, yy))
                if sum(p) < dark and not is_hair(p):
                    return True
                if p[2] > p[0] + 5:  # bluish: iris / eye white
                    return True
    return False


def quad(p0, c, p1, n=40):
    pts = []
    for i in range(n + 1):
        t = i / n
        x = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * c[0] + t * t * p1[0]
        y = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * c[1] + t * t * p1[1]
        pts.append((x, y))
    return pts


def stroke(layer, pts, w_mid, w_end, color):
    """Tapered stroke drawn on a supersampled layer."""
    d = ImageDraw.Draw(layer)
    n = len(pts) - 1
    for i in range(n):
        t = i / n
        w = w_end + (w_mid - w_end) * (1 - abs(2 * t - 1))
        (ax, ay), (bx, by) = pts[i], pts[i + 1]
        d.line([(ax * SS, ay * SS), (bx * SS, by * SS)], fill=color + (255,), width=max(1, int(w * SS)))
        rr = w * SS / 2
        d.ellipse([bx * SS - rr, by * SS - rr, bx * SS + rr, by * SS + rr], fill=color + (255,))


def composite_strokes(out, draw_fn):
    big = Image.new("RGBA", (out.width * SS, out.height * SS), (0, 0, 0, 0))
    draw_fn(big)
    small = big.resize(out.size, Image.LANCZOS)
    out.alpha_composite(small)


def main():
    src = Image.open(ASSETS / "neutral.webp").convert("RGB")

    eyes = Image.new("RGBA", src.size, (0, 0, 0, 0))
    patch_region(src, eyes, LEFT_EYE_BOX, dark=300, force=True)
    patch_region(src, eyes, RIGHT_EYE_BOX, dark=300, force=True)

    def lids(layer):
        # left eye (viewer's left): outer corner on the left
        stroke(layer, quad((229, 458), (290, 484), (348, 468)), 5.0, 1.6, LID_COLOR)
        stroke(layer, quad((232, 459), (224, 454), (219, 448)), 2.4, 0.9, LID_COLOR)
        # right eye: outer corner on the right
        stroke(layer, quad((494, 467), (556, 483), (621, 455)), 5.0, 1.6, LID_COLOR)
        stroke(layer, quad((618, 457), (626, 452), (631, 446)), 2.4, 0.9, LID_COLOR)

    composite_strokes(eyes, lids)
    # Re-draw hair that the lid strokes may have covered (hair is in front of eyes).
    for box in (LEFT_EYE_BOX, RIGHT_EYE_BOX):
        x0, y0, x1, y1 = box
        for y in range(y0, y1):
            for x in range(x0, x1):
                p = src.getpixel((x, y))
                if is_hair(p) and sum(p) < 260:
                    eyes.putpixel((x, y), (0, 0, 0, 0))
    eyes.save(ASSETS / "eyes_closed.png", optimize=True)

    mouth = Image.new("RGBA", src.size, (0, 0, 0, 0))
    patch_region(src, mouth, MOUTH_BOX, keep_hair=False)
    composite_strokes(mouth, lambda l: stroke(l, quad((403, 650), (421, 653), (440, 649)), 2.6, 1.0, LIP_COLOR))
    mouth.save(ASSETS / "mouth_closed.png", optimize=True)
    print("wrote", ASSETS / "eyes_closed.png", "and", ASSETS / "mouth_closed.png")


if __name__ == "__main__":
    main()
