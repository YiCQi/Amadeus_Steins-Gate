"""Turn a See-through layered PSD into a mesh rig for the web avatar (web/rig.js).

    python tools/psd_to_rig.py PSD --source IMAGE [--out web/assets/kurisu/rig]

PSD     See-through's output: one layer per part ("front hair", "face", "irides", ...).
        If "<name>_depth.psd" sits next to it (local runs write one), its per-part depth
        drives the head-turn parallax; without it (the online demos return only the PSD)
        depth comes from a head-shaped model instead.
--source the image that was fed to See-through. See-through pads it to a square and
        resizes it; the rig is cropped back to the image's own frame so it lines up
        with the page.

Writes one lossless WebP per part, rig.json (parts, meshes, per-vertex depth, face
anchors), and preview.png (all parts composited) to check the decomposition.
Needs numpy, Pillow and psd-tools.
"""
import argparse
import json
import time
from pathlib import Path

import numpy as np
from PIL import Image
from psd_tools import PSDImage

ALIASES = {"hairf": "front hair", "hairb": "back hair", "eyebg": "eyewhite"}
SPLIT = ("eyewhite", "irides", "eyelash", "eyebrow", "ears")  # parts that come as an image-left/right pair
EYE_ORDER = {"eyewhite": 0, "irides": 1, "eyelash": 2}       # irides are clipped by the white under them
CELL = {"eyewhite": 5, "irides": 8, "eyelash": 5, "eyebrow": 7, "mouth": 5, "nose": 10, "ears": 12,
        "face": 16, "front hair": 20, "back hair": 26, "headwear": 20, "neck": 20}
CELL_DEFAULT = 32
EDGE_PAD = 48   # px of edge-repeat added where a part is cut by the frame, so swaying never shows the cut
MIN_ALPHA = 10


class Part:
    def __init__(self, tag, side, rgba, depth, x, y):
        self.tag, self.side = tag, side
        self.rgba = rgba            # HxWx4 uint8, cropped to its content
        self.depth = depth          # HxW float 0 (near) .. 1 (far), same crop; None = no depth map
        self.x, self.y = x, y       # top-left in rig px

    @property
    def name(self):
        return f"{self.tag}-{self.side}" if self.side else self.tag

    @property
    def alpha(self):
        return self.rgba[..., 3].astype(np.float32) / 255

    def box(self, thresh=0.5):
        """(x0, y0, x1, y1) of pixels above `thresh` alpha, in rig px."""
        ys, xs = np.nonzero(self.alpha > thresh)
        if not len(xs):
            ys, xs = np.nonzero(self.alpha > 0)
        return (self.x + xs.min(), self.y + ys.min(), self.x + xs.max() + 1, self.y + ys.max() + 1)


# ----------------------------------------------------------------- loading

def split_tag(name):
    name = ALIASES.get(name.strip(), name.strip())
    for suffix in ("-l", "-r"):
        if name.endswith(suffix) and name[:-2] in SPLIT:
            return name[:-2], suffix[1]
    return name, ""


def load_layers(path):
    psd = PSDImage.open(path)
    out = []
    for layer in psd:  # bottom to top
        if not layer.visible or layer.kind not in ("pixel", "smartobject", "shape"):
            continue
        img = layer.topil()
        if img is None:
            continue
        x0, y0, _, _ = layer.bbox
        out.append((layer.name, x0, y0, img))
    return psd.width, psd.height, out


def crop_rect(frame_w, frame_h, source):
    """The source image's rectangle inside See-through's square canvas (center_square_pad_resize)."""
    if source is None:
        return 0, 0, frame_w, frame_h
    w, h = Image.open(source).size
    side = max(w, h)
    px, py = (side - w) // 2, (side - h) // 2
    s = frame_w / side
    return px * s, py * s, w * s, h * s


def trim(rgba, depth, x, y):
    """Crop to alpha content; returns None for an empty layer."""
    ys, xs = np.nonzero(rgba[..., 3] >= MIN_ALPHA)
    if len(xs) < 30:
        return None
    a, b, c, d = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    return rgba[a:b, c:d], (None if depth is None else depth[a:b, c:d]), x + c, y + a


def split_lr(part, cx):
    """Split a two-eyed part (eyewhite, irides, ...) at the face's centre line."""
    cut = int(round(cx - part.x))
    out = []
    for side, sl in (("l", slice(0, max(cut, 0))), ("r", slice(max(cut, 0), None))):
        rgba = part.rgba[:, sl]
        if rgba.shape[1] == 0:
            continue
        dep = None if part.depth is None else part.depth[:, sl]
        t = trim(rgba, dep, part.x + (0 if side == "l" else max(cut, 0)), part.y)
        if t:
            out.append(Part(part.tag, side, *t))
    return out


def edge_extend(part, rect):
    """Repeat the edge pixels outward wherever the part is cut by the image frame."""
    rx0, ry0, rx1, ry1 = rect
    x0, y0 = part.x, part.y
    x1, y1 = x0 + part.rgba.shape[1], y0 + part.rgba.shape[0]
    pads = [(EDGE_PAD if y0 <= ry0 + 1 else 0, EDGE_PAD if y1 >= ry1 - 1 else 0),
            (EDGE_PAD if x0 <= rx0 + 1 else 0, EDGE_PAD if x1 >= rx1 - 1 else 0)]
    if not any(sum(p) for p in pads):
        return
    part.rgba = np.pad(part.rgba, pads + [(0, 0)], mode="edge")
    if part.depth is not None:
        part.depth = np.pad(part.depth, pads, mode="edge")
    part.x -= pads[1][0]
    part.y -= pads[0][0]


def coverage_windows(parts, size):
    """Yield (part, window into the part, window into the canvas, visibility) top-down.

    visibility: how much of each of the part's pixels shows through everything above it.
    The caller may change the part's pixels before its own alpha is added to the coverage."""
    W, H = size
    above = np.zeros((H, W), np.float32)
    for p in reversed(parts):
        h, w = p.rgba.shape[:2]
        cx0, cy0, cx1, cy1 = max(0, p.x), max(0, p.y), min(W, p.x + w), min(H, p.y + h)
        if cx1 <= cx0 or cy1 <= cy0:
            continue
        win = (slice(cy0 - p.y, cy1 - p.y), slice(cx0 - p.x, cx1 - p.x))
        cwin = (slice(cy0, cy1), slice(cx0, cx1))
        yield p, win, cwin, 1 - above[cwin]
        above[cwin] += p.alpha[win] * (1 - above[cwin])


def match_source(parts, size, source):
    """Where a part shows in the portrait, use the portrait's own colours.

    See-through redraws every part, and its colours drift a little from the source (light
    streaks in the back hair, for one). Going top-down, each opaque pixel gets the colour
    that, under what the parts above already contribute, adds up to the portrait; that also
    covers pixels half hidden under soft edges. The rig at rest is then the portrait."""
    src = np.asarray(Image.open(source).convert("RGB").resize(size, Image.LANCZOS)).astype(np.float32)
    comp = np.zeros_like(src)  # premultiplied colour of everything above, so far
    for p, win, cwin, vis in coverage_windows(parts, size):
        a = p.alpha[win]
        want = np.clip((src[cwin] - comp[cwin]) / np.maximum(vis, 1e-3)[..., None], 0, 255)
        k = np.clip((vis - 0.3) / 0.4, 0, 1) * np.clip((a - 0.85) / 0.1, 0, 1)
        rgb = p.rgba[win][..., :3].astype(np.float32)
        rgb = rgb * (1 - k[..., None]) + want * k[..., None]
        # soft edges can't be solved for (they mix with what lies behind): spread the
        # corrected colour into the visible ones instead
        rgb = bleed(rgb, k > 0.99, (a > 0.04) & (a <= 0.95) & (vis > 0.3))
        p.rgba[win + (slice(0, 3),)] = np.clip(rgb, 0, 255).astype(np.uint8)
        comp[cwin] += (vis * a)[..., None] * rgb


def bleed(rgb, solid, todo, steps=3):
    """Grow `solid` pixels' colour into `todo` pixels, one ring per step."""
    rgb, solid = rgb.copy(), solid.copy()
    for _ in range(steps):
        pr = np.pad(rgb * solid[..., None], ((1, 1), (1, 1), (0, 0)))
        ps = np.pad(solid.astype(np.float32), 1)
        acc = sum(pr[1 + dy: pr.shape[0] - 1 + dy, 1 + dx: pr.shape[1] - 1 + dx]
                  for dy in (-1, 0, 1) for dx in (-1, 0, 1) if dy or dx)
        cnt = sum(ps[1 + dy: ps.shape[0] - 1 + dy, 1 + dx: ps.shape[1] - 1 + dx]
                  for dy in (-1, 0, 1) for dx in (-1, 0, 1) if dy or dx)
        grow = todo & ~solid & (cnt > 0)
        if not grow.any():
            break
        rgb[grow] = acc[grow] / cnt[grow][:, None]
        solid |= grow
    return rgb


def repaint_under(parts, over=("eyewhite", "irides", "eyelash", "mouth")):
    """Repaint what See-through left under the parts it lifted off: the face's skin under
    eyes, lashes and mouth, and each eye white under its iris.

    The layers keep a faint ghost outline of what lay on them. It only shows once things
    move, as a pale line under a shut eye or an iris edge beside a glancing iris; a smooth
    (harmonic) fill from the surrounding skin or white removes it."""
    face = find(parts, "face")
    if face is not None:
        repaint(face, [p for p in parts if p.tag in over], grow=3)
    for side in ("l", "r"):
        white, iris = find(parts, "eyewhite", side), find(parts, "irides", side)
        if white is not None and iris is not None:
            repaint(white, [iris], grow=2)


def repaint(base, over, grow):
    h, w = base.rgba.shape[:2]
    mask = np.zeros((h, w), bool)
    for p in over:
        a = p.alpha > 0.02
        y0, x0 = p.y - base.y, p.x - base.x
        ys, xs = np.nonzero(a)
        ys, xs = ys + y0, xs + x0
        ok = (ys >= 0) & (ys < h) & (xs >= 0) & (xs < w)
        mask[ys[ok], xs[ok]] = True
    mask = dilate(mask, grow) & (base.alpha > 0.5)
    if not mask.any():
        return
    ys, xs = np.nonzero(mask)
    y0, y1, x0, x1 = max(ys.min() - 4, 0), min(ys.max() + 5, h), max(xs.min() - 4, 0), min(xs.max() + 5, w)
    rgb = base.rgba[y0:y1, x0:x1, :3].astype(np.float32)
    a = base.alpha[y0:y1, x0:x1]
    m = mask[y0:y1, x0:x1]
    ring = dilate(m, 2) & ~m & (a > 0.9)
    # start from the colour right around the holes; keep transparent pixels out of the average
    rgb[m | (a < 0.5)] = np.median(rgb[ring], axis=0) if ring.any() else rgb[~m].mean(0)
    for _ in range(300):  # Jacobi iterations of Laplace's equation: blends into the edges
        p = np.pad(rgb, ((1, 1), (1, 1), (0, 0)), mode="edge")
        avg = (p[:-2, 1:-1] + p[2:, 1:-1] + p[1:-1, :-2] + p[1:-1, 2:]) / 4
        rgb[m] = avg[m]
    out = base.rgba[y0:y1, x0:x1, :3]
    out[m] = np.clip(rgb[m], 0, 255).astype(np.uint8)


def complete_irides(parts, reach=0.3):
    """Paint the top of each iris that the upper lid hides, so a wide-open eye shows a whole iris.

    In the portrait the lids cover the top of the irises, so See-through's iris ends in a
    flat cut there, and a surprised, widened eye shows that cut. The iris outline is fitted
    as an ellipse from its sides and bottom, and its hidden top (up to `reach` of the iris
    height) is filled with the colour of the iris's top rows, darkening a little upward
    like the lash shadow on a drawn iris."""
    for p in parts:
        if p.tag != "irides":
            continue
        a = p.alpha
        solid = a > 0.5
        rows = np.nonzero(solid.any(1))[0]
        if len(rows) < 8:
            continue
        top, bot = int(rows[0]), int(rows[-1])
        left = np.array([np.nonzero(r)[0][0] if r.any() else np.nan for r in solid])
        right = np.array([np.nonzero(r)[0][-1] if r.any() else np.nan for r in solid])
        half = (right - left) / 2 + 0.5
        wide = int(np.nanargmax(half))
        ax = float(np.nanmax(half))
        ex = float(np.nanmean(((left + right) / 2)[wide: wide + max(3, (bot - wide) // 3)])) + 0.5
        # the centre row whose ellipse best matches the half-widths below the widest row
        ys = np.arange(wide, bot - 1)
        if len(ys) < 4:
            continue
        best = None
        for yc in np.arange(top - (bot - top), wide + 0.5, 0.5):
            by = bot + 1 - yc
            err = np.nanmean((ax * np.sqrt(np.clip(1 - ((ys + 0.5 - yc) / by) ** 2, 0, 1)) - half[ys]) ** 2)
            if best is None or err < best[0]:
                best = (err, yc, by)
        _, yc, by = best
        grow = int(np.ceil(min(top - (yc - by), reach * 2 * by)))
        if grow < 2:
            continue
        h, w = a.shape
        rgb = p.rgba[..., :3].astype(np.float32)
        # colour of each column's top rows (just under the cut, where the lash's dark edge has
        # bled in), smoothed sideways so the pupil doesn't streak upward
        col = np.zeros((w, 3), np.float32)
        have = np.zeros(w, bool)
        cut = np.full(w, h, np.float32)
        for x in range(w):
            ys_ = np.nonzero(a[:, x] > 0.9)[0]
            if len(ys_):
                below = ys_[(ys_ >= ys_[0] + 4) & (ys_ < ys_[0] + 8)]
                cut[x] = ys_[0]
                col[x], have[x] = rgb[below if len(below) else ys_[:3], x].mean(0), True
        if not have.any():
            continue
        idx = np.arange(w)
        for c in range(3):
            col[:, c] = np.interp(idx, idx[have], col[have, c])
        k = np.ones(9) / 9
        col = np.stack([np.convolve(np.pad(col[:, c], 4, mode="edge"), k, "valid") for c in range(3)], 1)
        rim = np.array([rgb[y, int(x)] for y in range(wide, min(wide + 10, bot)) for x in (left[y], right[y])
                        if not np.isnan(x)]).mean(0)
        # the lid's line across the iris, from the middle columns (at the sides the iris's own
        # soft outline starts lower, and that must stay as it is)
        mid = have & (np.abs(idx + 0.5 - ex) < 0.6 * ax)
        lid = np.polyval(np.polyfit(idx[mid], cut[mid], 2), idx) if mid.sum() >= 5 else np.full(w, float(top))
        lid = np.minimum(lid, cut)
        # the extension, on a canvas `grow` rows taller
        H = h + grow
        Y, X = np.mgrid[0:H, 0:w].astype(np.float32) + 0.5
        byy = np.where(Y < yc + grow, min(by, 1.05 * ax), by)   # a drawn iris's top is round
        r = np.sqrt(((X - ex) / ax) ** 2 + ((Y - (yc + grow)) / byy) ** 2)
        d = (1 - r) * min(ax, by)                       # ~px inside the outline
        # down to a few rows past the old cut, where it fades into the original
        ea = np.clip(d + 0.5, 0, 1) * (Y < grow + lid[None, :] + 6)
        up = np.clip((top + grow + 2 - Y) / grow, 0, 1)  # 0 at the old cut .. 1 at the new top
        ergb = col[None, :, :] * (1 - 0.22 * up)[..., None]
        ergb = ergb + (rim - ergb) * np.clip(1 - d / 2.5, 0, 1)[..., None]
        oa = np.zeros((H, w), np.float32)
        oa[grow:] = a
        # fade the old cut's dark edge into the new top over a few rows
        oa *= 1 - ea * (1 - np.clip((Y - grow - lid[None, :]) / 6, 0, 1))
        orgb = np.zeros((H, w, 3), np.float32)
        orgb[grow:] = rgb
        out_a = oa + ea * (1 - oa)
        out = (orgb * oa[..., None] + ergb * (ea * (1 - oa))[..., None]) / np.maximum(out_a, 1e-6)[..., None]
        p.rgba = np.dstack([np.clip(out, 0, 255), np.clip(out_a * 255, 0, 255)]).astype(np.uint8)
        if p.depth is not None:
            p.depth = np.pad(p.depth, ((grow, 0), (0, 0)), mode="edge")
        p.y -= grow
        print(f"  {p.name}: iris top completed, +{grow} px")


def dilate(mask, steps):
    for _ in range(steps):
        m = np.pad(mask, 1)
        mask = m[1:-1, 1:-1] | m[:-2, 1:-1] | m[2:, 1:-1] | m[1:-1, :-2] | m[1:-1, 2:]
    return mask


def tint_hidden(parts, size, tags=("back hair", "front hair")):
    """Re-color the repainted, normally hidden parts of hair layers to match their visible parts.

    See-through repaints what a part hides (hair behind the head), but tends to paint it a
    flat light grey-pink. Turning the head uncovers some of it, so its colours are moved onto
    the visible part's (mean/spread per channel), keeping the strand texture.
    """
    for p, win, _, vis_win in coverage_windows(parts, size):
        if p.tag not in tags:
            continue
        vis = np.zeros(p.rgba.shape[:2], np.float32)  # off-frame pixels count as hidden
        vis[win] = vis_win
        a = p.alpha
        rgb = p.rgba[..., :3].astype(np.float32)
        seen, hidden = (a > 0.5) & (vis > 0.85), (a > 0.5) & (vis < 0.15)
        if seen.sum() < 500 or hidden.sum() < 500:
            continue
        mu_s, sd_s = rgb[seen].mean(0), rgb[seen].std(0) + 1
        mu_h, sd_h = rgb[hidden].mean(0), rgb[hidden].std(0) + 1
        if np.abs(mu_h - mu_s).max() > 20:
            mapped = (rgb - mu_h) * np.clip(sd_s / sd_h, 0.5, 2) + mu_s
            k = np.clip((0.7 - vis) / 0.5, 0, 1)[..., None]
            p.rgba[..., :3] = np.clip(rgb * (1 - k) + mapped * k, 0, 255).astype(np.uint8)
            print(f"  {p.name}: hidden colour {mu_h.round()} -> {mu_s.round()}")


# ----------------------------------------------------------------- geometry

def grid_size(part):
    h, w = part.rgba.shape[:2]
    cell = CELL.get(part.tag, CELL_DEFAULT)
    return max(2, int(np.ceil(w / cell)) + 1), max(2, int(np.ceil(h / cell)) + 1)


def sample_grid(values, valid, cols, rows):
    """Average `values` over each grid vertex's cell where `valid`; fill gaps from neighbours."""
    h, w = values.shape
    gx = np.linspace(0, w - 1, cols)
    gy = np.linspace(0, h - 1, rows)
    rx, ry = max(1.0, w / (cols - 1) * 0.6), max(1.0, h / (rows - 1) * 0.6)
    out = np.full((rows, cols), np.nan)
    for j, y in enumerate(gy):
        ya, yb = int(max(0, y - ry)), int(min(h, y + ry + 1))
        for i, x in enumerate(gx):
            xa, xb = int(max(0, x - rx)), int(min(w, x + rx + 1))
            m = valid[ya:yb, xa:xb]
            if m.any():
                out[j, i] = values[ya:yb, xa:xb][m].mean()
    return fill_nan(out)


def fill_nan(g):
    g = g.copy()
    if np.isnan(g).all():
        return np.zeros_like(g)
    for _ in range(g.shape[0] + g.shape[1]):
        holes = np.isnan(g)
        if not holes.any():
            break
        p = np.pad(g, 1, constant_values=np.nan)
        nb = np.stack([p[:-2, 1:-1], p[2:, 1:-1], p[1:-1, :-2], p[1:-1, 2:]])
        cnt = (~np.isnan(nb)).sum(0)
        avg = np.where(cnt > 0, np.nansum(nb, 0) / np.maximum(cnt, 1), np.nan)
        g[holes] = avg[holes]
    return g


def smooth(g, n=2):
    for _ in range(n):
        p = np.pad(g, 1, mode="edge")
        g = (p[1:-1, 1:-1] * 4 + p[:-2, 1:-1] + p[2:, 1:-1] + p[1:-1, :-2] + p[1:-1, 2:]) / 8
    return g


def vertex_xy(part, cols, rows):
    h, w = part.rgba.shape[:2]
    xs = part.x + np.linspace(0, w, cols)
    ys = part.y + np.linspace(0, h, rows)
    return np.meshgrid(xs, ys)


# ----------------------------------------------------------------- anchors

def eye_profile(white, n=14):
    """Top/bottom edge of the eye white along its width: the lids follow these when blinking."""
    a = white.alpha > 0.5
    cols = np.nonzero(a.any(0))[0]
    x0, x1 = cols.min(), cols.max()
    xs = np.linspace(x0, x1, n)
    top, bot = [], []
    for x in xs:
        c = a[:, int(round(x))]
        rows = np.nonzero(c)[0]
        top.append(rows.min() if len(rows) else np.nan)
        bot.append(rows.max() + 1 if len(rows) else np.nan)
    top, bot = fill_nan(np.array([top])).ravel(), fill_nan(np.array([bot])).ravel()
    return [round(float(v), 1) for v in xs + white.x], [round(float(v), 1) for v in top + white.y], \
           [round(float(v), 1) for v in bot + white.y]


def lash_profile(lash, xs, top, eye_h):
    """Top of the upper lash line sitting on the eye white, per column; None if there is none."""
    a = lash.alpha > 0.4
    out = []
    for x, t in zip(xs, top):
        c = int(round(x - lash.x))
        if not 0 <= c < a.shape[1]:
            out.append(np.nan)
            continue
        col = a[:, c]
        start = min(max(int(round(t - lash.y)) - 1, 0), len(col) - 1)
        found, gap, started = None, 0, False
        for yy in range(start, max(0, start - int(eye_h * 0.7)) - 1, -1):
            if col[yy]:
                started, gap, found = True, 0, yy
            elif started:
                gap += 1
                if gap > 1:
                    break
            elif start - yy > 3:   # no lash right above the white here
                break
        out.append(np.nan if found is None else found + lash.y)
    arr = np.array([out], dtype=float)
    if np.isnan(arr).all():
        return None
    return [round(float(v), 1) for v in fill_nan(arr).ravel()]


def mouth_colors(mouth):
    rgb = mouth.rgba[..., :3].reshape(-1, 3).astype(np.float32)
    a = mouth.alpha.ravel()
    rgb = rgb[a > 0.6]
    if len(rgb) < 10:
        return "#3a2228", "#5b2a35"
    lum = rgb @ [0.299, 0.587, 0.114]
    line = rgb[lum <= np.percentile(lum, 15)].mean(0)
    inner = rgb[(lum > np.percentile(lum, 40)) & (lum < np.percentile(lum, 85))]
    inner = inner.mean(0) if len(inner) else rgb.mean(0)
    hexc = lambda c: "#" + "".join(f"{int(np.clip(v, 0, 255)):02x}" for v in c)
    return hexc(line), hexc(inner)


def find(parts, tag, side=None):
    for p in parts:
        if p.tag == tag and (side is None or p.side == side):
            return p
    return None


def anchors(parts, size):
    face = find(parts, "face")
    if face is None:
        raise SystemExit("no 'face' layer: is this a See-through PSD?")
    fx0, fy0, fx1, fy1 = face.box()
    fw, fh = fx1 - fx0, fy1 - fy0
    a = face.alpha > 0.5

    eyes = []
    for side in ("l", "r"):
        white = find(parts, "eyewhite", side)
        if white is None:
            continue
        lash = find(parts, "eyelash", side)
        boxes = [p.box(0.3) for p in (white, find(parts, "irides", side), lash) if p is not None]
        x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
        x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
        xs, top, bot = eye_profile(white)
        eye = {"side": side, "box": [int(x0), int(y0), int(x1 - x0), int(y1 - y0)], "xs": xs, "top": top, "bot": bot}
        lash_top = lash_profile(lash, xs, top, y1 - y0) if lash is not None else None
        if lash_top:
            eye["lashTop"] = lash_top
        eyes.append(eye)
    eye_y = np.mean([e["box"][1] + e["box"][3] / 2 for e in eyes]) if eyes else fy0 + fh * 0.6
    # centre line between the eyes; See-through's face layer is the whole bald head, so the
    # face's own size is measured on it: chin, cheek width under the eyes, skull width
    cx = (float(np.mean([e["box"][0] + e["box"][2] / 2 for e in eyes])) if len(eyes) == 2
          else face.x + float(np.nonzero(a)[1].mean()))
    mid = int(round(cx - face.x))
    band = a[:, max(0, mid - 6): mid + 7]
    chin_y = face.y + float(np.nonzero(band.any(1))[0].max() + 1)

    def width_at(y):
        row = a[int(np.clip(y - face.y, 0, a.shape[0] - 1))]
        xs = np.nonzero(row)[0]
        return float(xs.max() - xs.min() + 1) if len(xs) else float(fw)

    eye_chin = chin_y - eye_y
    cheek_w = width_at(eye_y + 0.45 * eye_chin)
    head_w = max(width_at(y) for y in range(int(fy0), int(fy1), 4))

    brows = []
    for side in ("l", "r"):
        b = find(parts, "eyebrow", side)
        if b is not None:
            x0, y0, x1, y1 = b.box(0.3)
            brows.append({"side": side, "box": [int(x0), int(y0), int(x1 - x0), int(y1 - y0)]})

    out = {
        "head": {"cx": round(cx, 1), "eyeY": round(float(eye_y), 1), "chin": [round(cx, 1), round(chin_y, 1)],
                 "cheekW": round(cheek_w, 1), "headW": round(head_w, 1), "skullTop": int(fy0),
                 "faceBox": [int(fx0), int(fy0), int(fw), int(fh)]},
        "eyes": eyes, "brows": brows,
    }
    hair = [p for p in parts if p.tag in ("front hair", "back hair", "headwear")]
    out["head"]["top"] = int(min([p.box(0.5)[1] for p in hair] + [fy0]))
    mouth = find(parts, "mouth")
    if mouth is not None:
        x0, y0, x1, y1 = mouth.box(0.3)
        line, inner = mouth_colors(mouth)
        out["mouth"] = {"box": [int(x0), int(y0), int(x1 - x0), int(y1 - y0)], "line": line, "inner": inner}
    neck = find(parts, "neck")
    if neck is not None:
        x0, y0, x1, y1 = neck.box(0.5)
        out["neck"] = {"box": [int(x0), int(y0), int(x1 - x0), int(y1 - y0)]}
    body = [p for p in parts if p.tag in ("topwear", "neckwear", "handwear", "objects")]
    if body:
        out["body"] = {"top": int(min(p.box(0.5)[1] for p in body)), "bottom": int(size[1])}
    return out


# ----------------------------------------------------------------- depth

def depth_scale(parts, head):
    """Depth units -> px, from how much deeper the cheeks are than the middle of the face.

    None when the face's depth map is flat: See-through's maps mostly give each part one
    depth (good for stacking, useless for parallax), and then the head model is better."""
    face = find(parts, "face")
    if face is None or face.depth is None:
        return None
    a = face.alpha > 0.5
    eye_y, chin_y, cw = head["eyeY"], head["chin"][1], head["cheekW"]
    rows = slice(int(eye_y - face.y), int(chin_y - face.y))
    xs = np.arange(a.shape[1]) + face.x - head["cx"]
    centre = (np.abs(xs) < cw * 0.1)[None, :] & a
    edge = (np.abs(xs) > cw * 0.38)[None, :] & a
    c, e = face.depth[rows][centre[rows]], face.depth[rows][edge[rows]]
    if len(c) < 20 or len(e) < 20:
        return None
    dd = float(np.median(e) - np.median(c))
    if dd < 0.015:
        return None
    return 0.3 * cw / dd, float(np.median(c))


def smoothstep(a, b, x):
    t = np.clip((x - a) / (b - a), 0, 1)
    return t * t * (3 - 2 * t)


def synth_z(part, X, Y, head):
    """Head-shaped depth (px toward the viewer, 0 = the head's vertical axis).

    The face curves mostly sideways, like a cylinder, so the chin turns with the eyes; the
    features sit a little in front of it, bangs just in front of the forehead, hair that
    hangs beside or below the face near the head's sides, back hair behind."""
    cx, eye_y, chin_y, W = head["cx"], head["eyeY"], head["chin"][1], head["headW"]
    L = chin_y - eye_y
    rz = 0.36 * W
    cyl = lambda X, Y: rz * np.sqrt(np.clip(1 - ((X - cx) / (W / 2)) ** 2, 0, 1)) * \
        np.clip(1 - 0.3 * ((Y - (eye_y + 0.25 * L)) / (1.6 * L)) ** 2, 0.3, 1)
    u = (X - cx) / (W / 2)
    face = cyl(X, Y)
    tag = part.tag
    if tag == "face":
        # The face's outline is the side of the head, well behind the middle of the face.
        # The cylinder puts it nearly as far forward, so a turned head slid its whole outline
        # sideways and the far cheek bulged out; halve the depth toward the outline instead.
        # The middle (eyes, nose, mouth, which keep the cylinder's depth) is untouched.
        half, mid = face_rows(part, cx)
        Yc = np.minimum(Y, chin_y - 2)
        row = np.clip(np.round(Yc - part.y).astype(int), 0, len(half) - 1)
        hw, c = np.maximum(half[row], 1), mid[row]
        edge = cyl(c + np.where(X >= c, hw, -hw), Yc)
        drop = 0.5 * edge * smoothstep(0.3, 1, np.abs(X - c) / hw) * smoothstep(eye_y - 0.5 * L, eye_y - 0.1 * L, Y)
        return face - drop
    if tag == "ears":
        return face * 0.5
    if tag in ("eyewhite", "irides"):
        return face + 0.025 * W
    if tag in ("eyelash", "eyebrow"):
        return face + 0.035 * W
    if tag == "mouth":
        return face + 0.03 * W
    if tag == "nose":
        return face + 0.08 * W
    if tag in ("front hair", "headwear"):
        hang = np.maximum(np.clip((np.abs(u) - 0.75) / 0.35, 0, 1), np.clip((Y - chin_y) / L, 0, 1))
        return (face + 0.05 * W) * (1 - hang) + 0.06 * W * hang
    if tag == "back hair":
        return np.full_like(X, -0.45 * rz)
    if tag == "neck":
        # the throat is in front of the spine the head turns on: it follows the chin a little
        # instead of sliding the other way and opening a wedge of neck shadow under the jaw
        return np.full_like(X, 0.08 * W)
    return np.full_like(X, -0.1 * W)  # body: does not follow the head


def face_rows(face, cx):
    """Half-width and centre of the face layer's outline on each of its rows (rig px)."""
    a = face.alpha > 0.5
    half, mid = np.zeros(len(a)), np.full(len(a), float(cx))
    for i, r in enumerate(a):
        xs = np.nonzero(r)[0]
        if len(xs):
            half[i], mid[i] = (xs[-1] - xs[0]) / 2, face.x + (xs[0] + xs[-1]) / 2
    return half, mid


def part_z(part, cols, rows, head, scale):
    X, Y = vertex_xy(part, cols, rows)
    if scale is None or part.depth is None:
        return synth_z(part, X, Y, head)
    k, ref = scale
    z = sample_grid(part.depth, part.alpha > 0.4, cols, rows)
    z = smooth(z, 2 if part.tag in ("face", "front hair", "back hair") else 1)
    return -(z - ref) * k


# ----------------------------------------------------------------- main

def build(psd_path, source, out_dir, depth_path=None):
    fw, fh, layers = load_layers(psd_path)
    depth_path = depth_path or psd_path.with_name(psd_path.stem + "_depth.psd")
    depths = {}
    if depth_path.exists():
        _, _, dl = load_layers(depth_path)
        for name, x0, y0, img in dl:
            d = np.asarray(img.convert("L")).astype(np.float32) / 255
            depths[name] = (x0, y0, d)
        print(f"depth: {depth_path.name} ({len(depths)} layers)")
    else:
        print("depth: none, using a head-shaped model")

    rx, ry, rw, rh = crop_rect(fw, fh, source)
    size = (int(round(rw)), int(round(rh)))
    ox, oy = int(round(rx)), int(round(ry))

    parts = []
    for name, x0, y0, img in layers:
        tag, side = split_tag(name)
        rgba = np.asarray(img.convert("RGBA")).copy()
        dep = None
        if name in depths:
            dx, dy, d = depths[name]
            dep = np.zeros(rgba.shape[:2], np.float32)
            h = min(d.shape[0], rgba.shape[0] - (dy - y0))
            w = min(d.shape[1], rgba.shape[1] - (dx - x0))
            if h > 0 and w > 0 and dy >= y0 and dx >= x0:
                dep[dy - y0: dy - y0 + h, dx - x0: dx - x0 + w] = d[:h, :w]
        t = trim(rgba, dep, x0 - ox, y0 - oy)
        if t is None:
            continue
        parts.append(Part(tag, side, *t))

    face = find(parts, "face")
    if face is None:
        raise SystemExit(f"no 'face' layer in {psd_path.name}; layers: {[p.name for p in parts]}")
    a = face.alpha > 0.5
    cx = face.x + float(np.nonzero(a)[1].mean())
    split = []
    for p in parts:
        split.extend(split_lr(p, cx) if p.tag in SPLIT and not p.side else [p])
    parts = split
    # sides mean image left/right from here on (See-through names them from the character's view)
    for p in parts:
        if p.side:
            p.side = "l" if p.x + p.rgba.shape[1] / 2 < cx else "r"
    # eye white -> irides -> lashes, per eye, whatever order the PSD had them in
    def order_key(ip):
        i, p = ip
        if p.tag in EYE_ORDER:
            first = min(j for j, q in enumerate(parts) if q.tag in EYE_ORDER and q.side == p.side)
            return (first, EYE_ORDER[p.tag])
        return (i, 0)
    parts = [p for _, p in sorted(enumerate(parts), key=order_key)]

    head = anchors(parts, size)  # before edge padding, which would move the hair's top edge
    scale = depth_scale(parts, head["head"])
    if depths and scale is None:
        print("depth: maps are flat within each part, using the head-shaped model")
    # yaw/pitch pivot depth: the head model's axis, or behind the face for measured depth
    head["head"]["pivotZ"] = 0.0 if scale is None else round(-0.36 * head["head"]["headW"], 1)
    if source is not None:
        match_source(parts, size, source)
    tint_hidden(parts, size)
    repaint_under(parts)
    complete_irides(parts)
    for p in parts:
        edge_extend(p, (0, 0, size[0], size[1]))
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.webp"):
        old.unlink()

    rig_parts, preview = [], Image.new("RGBA", size, (0, 0, 0, 0))
    for p in parts:
        cols, rows = grid_size(p)
        z = part_z(p, cols, rows, head["head"], scale)
        fname = p.name.replace(" ", "_") + ".webp"
        img = Image.fromarray(p.rgba, "RGBA")
        img.save(out_dir / fname, "WEBP", lossless=True, quality=100, method=6)
        cx0, cy0 = max(0, -p.x), max(0, -p.y)   # parts may hang off the frame (edge padding)
        preview.alpha_composite(img.crop((cx0, cy0, img.width, img.height)), (p.x + cx0, p.y + cy0))
        rig_parts.append({
            "name": p.name, "tag": p.tag, "side": p.side, "file": fname,
            "rect": [int(p.x), int(p.y), int(p.rgba.shape[1]), int(p.rgba.shape[0])],
            "grid": [cols, rows], "z": [round(float(v), 1) for v in z.ravel()],
        })
        print(f"  {p.name:14s} {p.rgba.shape[1]:5d}x{p.rgba.shape[0]:<5d} at {int(p.x):5d},{int(p.y):<5d} grid {cols}x{rows}"
              f"  z {z.min():7.1f}..{z.max():7.1f}")

    rig = {"version": 1, "build": int(time.time()), "size": list(size), "depth": "psd" if scale else "model",
           "anchors": head, "parts": rig_parts}
    (out_dir / "rig.json").write_text(json.dumps(rig, separators=(",", ":")), encoding="utf-8")
    preview.save(out_dir / "preview.png")
    print(f"rig: {len(rig_parts)} parts, {size[0]}x{size[1]} -> {out_dir}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("psd", type=Path)
    ap.add_argument("--source", type=Path, help="the image fed to See-through (to undo its square padding)")
    ap.add_argument("--depth", type=Path, help="depth PSD (default: <psd>_depth.psd if present)")
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parent.parent / "web/assets/kurisu/rig")
    args = ap.parse_args()
    build(args.psd, args.source, args.out, args.depth)


if __name__ == "__main__":
    main()
