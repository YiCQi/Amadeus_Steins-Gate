"""Remove the CRT scanlines from an Amadeus screenshot before layer decomposition.

The phone-screen frames of Amadeus have horizontal scanlines with a fixed
vertical period (4 px in the included portrait). Layer decomposition would bake
them into every part, and the parts it repaints (hair behind the face, skin under
the eyes) would come out without them, so the seams would show. The app draws
its own scanlines on top, so the rig wants a clean source.

The scanlines are a carrier at a fixed vertical frequency whose strength depends
on the brightness underneath (strong on skin, faint on dark hair). Each harmonic
is demodulated to baseband, low-passed into a local amplitude map, remodulated,
and subtracted, so dark and bright areas are each cleaned by their own amount.

Usage:  python tools/clean_scanlines.py SRC [OUT] [--period 4]
"""
import argparse
from pathlib import Path

import numpy as np
from PIL import Image

try:
    from scipy.ndimage import gaussian_filter
except ImportError:  # numpy-only fallback (the app's venv has no scipy)
    def _blur1d(a, sigma, axis):
        r = int(3 * sigma + 0.5)
        k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
        k /= k.sum()
        pad = [(0, 0)] * a.ndim
        pad[axis] = (r, r)
        a = np.pad(a, pad, mode="symmetric")  # same edge handling as scipy's default
        return np.apply_along_axis(lambda v: np.convolve(v, k, mode="valid"), axis, a)

    def gaussian_filter(a, sigma):
        for axis, s in enumerate(sigma):
            a = _blur1d(a, s, axis)
        return a


def phase_gain(lum: np.ndarray, period: int) -> np.ndarray:
    """Mean brightness of each scanline phase relative to its local average (1.0 = no scanlines)."""
    rows = lum.mean(axis=1)
    local = np.convolve(rows, np.ones(period) / period, mode="same")
    rel = rows / np.maximum(local, 1e-3)
    idx = np.arange(period, len(rows) - period)
    return np.array([rel[idx[idx % period == p]].mean() for p in range(period)])


def clean(img: np.ndarray, period: int, sigma_y: float = 3.0, sigma_x: float = 1.0) -> np.ndarray:
    """img: HxWxC float in 0..1. sigma_y must be >= ~3 so content near DC can't leak through the carrier."""
    h, w, _ = img.shape
    y = np.arange(h)[:, None] * np.ones((1, w))
    out = img.copy()
    for ch in range(img.shape[2]):
        band = img[..., ch]
        scan = np.zeros_like(band)
        for k in range(1, period // 2 + 1):
            if 2 * k == period:  # Nyquist harmonic: a real (-1)^y carrier
                car = np.cos(np.pi * y)
                scan += gaussian_filter(band * car, (sigma_y, sigma_x)) * car
            else:
                car = np.exp(-2j * np.pi * k * y / period)
                z = band * car
                amp = gaussian_filter(z.real, (sigma_y, sigma_x)) + 1j * gaussian_filter(z.imag, (sigma_y, sigma_x))
                scan += 2 * np.real(amp * np.conj(car))
        out[..., ch] = band - scan
    return np.clip(out, 0, 1)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src")
    ap.add_argument("out", nargs="?")
    ap.add_argument("--period", type=int, default=4)
    args = ap.parse_args()
    src = Path(args.src)
    out = Path(args.out) if args.out else src.with_name(src.stem + "_clean.png")

    arr = np.asarray(Image.open(src).convert("RGBA")).astype(np.float64) / 255
    rgb = arr[..., :3]
    mid = slice(rgb.shape[1] // 4, 3 * rgb.shape[1] // 4)
    before = phase_gain(rgb.mean(axis=2)[:, mid], args.period)
    cleaned = clean(rgb, args.period)
    after = phase_gain(cleaned.mean(axis=2)[:, mid], args.period)
    arr = np.concatenate([cleaned, arr[..., 3:]], axis=2)
    out.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.round(arr * 255).astype(np.uint8), "RGBA").save(out)
    fmt = lambda g: " ".join(f"{v:.3f}" for v in g)
    print(f"scanline gain by row phase  before: {fmt(before)}   after: {fmt(after)}")
    print(f"saved {out}")


if __name__ == "__main__":
    main()
