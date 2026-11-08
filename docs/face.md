# Her face

[← Back to the README](../README.md)

The page uses the **mesh rig** in `web/assets/kurisu/rig/` when it exists (and WebGL works), and the **sprites** otherwise. Open `/?avatar=sprites` to compare, or `/?rigdebug` for sliders and expression buttons.

## Mesh rig

[See-through](https://github.com/shitagaki-lab/see-through) splits the portrait into parts (front hair, back hair, face, eye whites, irises, lashes, brows, nose, mouth, neck, clothes) and repaints what each part hides, plus a depth map per part. `tools/psd_to_rig.py` puts each part on a deformable grid, and `web/rig.js` drives it in the browser:

- **Head**: yaw, pitch and roll. Every vertex has a depth, so the nose and bangs move more than the cheeks, and back hair moves the other way. The face's outline sits further back than its middle, so a turned face keeps its shape instead of sliding sideways.
- **Eyes**: the eyelids close along the shape of each eye, the eye smile (^^) raises the lower lid, and the irises look around (clipped to the eye white). The converter paints in the top of each iris that the lid hides, so a wide-open, surprised eye shows a whole iris.
- **Brows, mouth, blush**: the mouth is drawn per frame at the portrait's own resolution, with soft lines that thin toward the corners like the art (closed line ↔ open with teeth and tongue, smile/frown, smirk). Lip-sync takes each mora's shape (a/i/u/e/o, ん, っ) from the line's kana reading, spread over the voiced parts of the audio; loudness only decides how far it opens.
- **Expressions**: 15, some in strengths (smile < happy < laugh, annoyed < angry < furious), plus sulking with puffed cheeks and mumbling, puzzled with one brow up and a squint, the half-lidded disgusted stare. Some move on their own: laughing shakes her, fury trembles.
- **Body**: the two side locks and the bangs swing on their own springs behind head turns and in a light breeze, breathing, idle sway.

Only building a rig needs See-through; the rig itself is a few images and a JSON file that run in any browser, phones included.

**Build it** (Windows, NVIDIA GPU with 12 GB+):

```bash
python tools/clean_scanlines.py web/assets/kurisu/neutral.webp art/kurisu/kurisu_clean.png
```

```bash
tools\seethrough\install.bat
```

```bash
tools\make_rig.bat art\kurisu\kurisu_clean.png
```

1. `clean_scanlines.py` removes the CRT scanlines of Amadeus screenshots, which would otherwise be baked into every part (the app draws its own). Skip it for clean images.
2. `install.bat` installs See-through into `\SeeThrough` on the same drive. That's a download of about 17.5 GB (PyTorch and the model weights).
3. `make_rig.bat` decomposes the image (a few minutes) and builds the rig. Check `web/assets/kurisu/rig/preview.png`, then reload the page.

**No GPU?** Upload the image to the See-through online demo ([ModelScope](https://modelscope.cn/studios/ljsabc/See-Through), [Hugging Face](https://huggingface.co/spaces/24yearsold/see-through-demo)), download the PSD, and run `tools\make_rig.bat IMAGE PSD`. The demos return only the PSD, so a head-shaped depth model stands in for the depth maps.

## Sprites (fallback)

`web/assets/kurisu/sprites.json` defines the expressions. Each one has a `base` image plus optional **full-canvas transparent overlays** that are stacked on top:

```json
"annoyed": {
  "base": "annoyed.webp",
  "eyes_closed": "annoyed_eyes_closed.png",
  "mouth_closed": "annoyed_mouth_closed.png",
  "mouth_open": "annoyed_mouth_open.png"
}
```

Overlay keys: `eyes_half`, `eyes_closed`, `mouth_closed`, `mouth_half`, `mouth_open`. A missing key means "the base already looks like that". Expressions you haven't drawn go through `aliases` to `neutral`, and CSS effects still react (blush, huff, hop).

The included closed-eyes and closed-mouth overlays were generated from the portrait by `tools/make_overlays.py`. It works by pulling skin texture in step with the 4-px scanlines, so you can adapt it for a new portrait by editing the box coordinates.
