# Her voice

[← Back to the README](../README.md)

She always speaks Japanese; only the subtitles follow your language. There are three voices, picked by `engine` under `[tts]` in `config.toml` (`auto` takes the best one installed):

| Voice | Setup | Speed per line | Notes |
|---|---|---|---|
| **Amadeus voice** (default) | `tools\voice\install_amadeus_vits.bat`, once | 0.3–0.8 s on the CPU, well under 0.1 s on an NVIDIA GPU | [mio/amadeus](https://huggingface.co/mio/amadeus), a ready-made Kurisu voice (ESPnet VITS) |
| **GPT-SoVITS** | your own clone, below | 0.3–0.5 s | the most faithful, if you train it well |
| **edge-tts** | none | 1–1.4 s | a Microsoft Japanese voice, needs internet; not hers |

## The Amadeus voice

Run `tools\voice\install_amadeus_vits.bat` once (about 0.8 GB: CPU PyTorch, ESPnet and the [mio/amadeus](https://huggingface.co/mio/amadeus) model). After that she speaks in her Amadeus voice, synthesized on your CPU in about 0.3–0.8 s a line, with no GPU or internet needed. The app starts the voice with itself and stops it on exit. Without it, she uses a free Microsoft Japanese voice (edge-tts, needs internet).

**On an NVIDIA GPU** her voice can run there instead: a line then takes well under 0.1 s, and a game or anything else busy on the CPU no longer slows her down. Install the CUDA build of PyTorch into the voice's own venv (about 2 GB; `cu130` needs a recent driver, `cu128` for older ones), with the app closed:

```
%LOCALAPPDATA%\Amadeus\vits-venv\Scripts\python.exe -m pip install --no-deps torch==2.11.0+cu130 torchaudio==2.11.0+cu130 --index-url https://download.pytorch.org/whl/cu130
```

The server window then says `[voice] Amadeus voice ready (…, cuda)`. A CPU copy of the voice stands by: when the GPU runs out of memory, is short of free memory, or gets far slower than its usual pace, lines go to the CPU for a minute. If a line's voice still fails, the page asks for it once more before letting it go unvoiced, and the server window says why (`[voice] line … failed: …`).

## GPT-SoVITS: your own clone

### Install GPT-SoVITS

1. Download the **Windows integrated package** from the [GPT-SoVITS releases](https://github.com/RVC-Boss/GPT-SoVITS/releases). For an **RTX 50-series** GPU, pick the build marked for 50-series / CUDA 12.8; older builds don't support Blackwell GPUs.
2. Extract it to a simple path such as `D:\GPT-SoVITS`. **Not inside this folder**: the `;` in the folder name breaks it.

### Collect Kurisu's voice clips

Take them from **your own copy** of the anime or the games. Clean clips matter much more than the amount of audio:

- Speech only: no background music, sound effects, or other characters talking over her. The GPT-SoVITS WebUI includes **UVR5** (vocal separation) to strip music.
- Calm, clear lines make the best neutral reference.

### Option A: zero-shot (5 minutes, decent)

1. Pick one **3–10 second** clip, save it as `voice/refs/neutral.wav`, and write down **exactly** what she says (Japanese).
2. In `config.toml`:
   ```toml
   [tts.refs.neutral]
   audio = "voice/refs/neutral.wav"
   text  = "まったく、何を言ってるのよ。"   # exact transcript of the clip
   ```

### Option B: fine-tune (about 1 hour, sounds like Kurisu)

With **1–10+ minutes** of clean clips (more is better), in the GPT-SoVITS WebUI (`go-webui.bat`):

1. **0-Fetch dataset**: put your clips in one folder → *Audio slicer* → *ASR* (choose **Faster Whisper**, language **ja**) → *Proofreading* (fix the transcripts; this matters).
2. **1-GPT-SoVITS-TTS → 1A**: experiment name `kurisu`, point it at the sliced audio + `.list` file, and run *one-click formatting*.
3. **1B fine-tuning**: train **SoVITS** (≈8–10 epochs), then **GPT** (≈10–15 epochs). The defaults are fine on a 16 GB GPU.
4. The weights appear in `SoVITS_weights_v2Pro\` and `GPT_weights_v2Pro\` (the folder names depend on the version you train). Point `config.toml` at them:
   ```toml
   gpt_weights    = "D:/GPT-SoVITS/GPT_weights_v2Pro/kurisu-e15.ckpt"
   sovits_weights = "D:/GPT-SoVITS/SoVITS_weights_v2Pro/kurisu_e8_s400.pth"
   ```
5. You still need a reference clip (Option A). The fine-tuned model adds her timbre on top of it.

### Run the voice server

Each time, before (or after) `start.bat`, open a terminal in the GPT-SoVITS folder:

```bash
runtime\python.exe api_v2.py -a 127.0.0.1 -p 9880 -c GPT_SoVITS/configs/tts_infer.yaml
```

Then restart Amadeus and press **Settings → Test voice**.

### Emotional voices (optional)

The model tags every sentence with an emotion. Add a clip per emotion and GPT-SoVITS copies its tone:

```toml
[tts.refs.annoyed]
audio = "voice/refs/annoyed.wav"
text  = "クリスティーナって呼ぶな！"

[tts.refs.embarrassed]
audio = "voice/refs/embarrassed.wav"
text  = "べ、別にそういうわけじゃ…"
```

Emotions: `neutral smile happy laugh smug annoyed angry furious pout disgusted embarrassed sad surprised puzzled thinking`. Any emotion without a clip falls back to `neutral`.

> **Please keep it private.** Kurisu's voice belongs to her voice actress, Asami Imai, and the rights holders. Use it for personal use only, and don't share the trained model, the clips, or recordings of the clone.
