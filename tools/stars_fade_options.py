"""Three fade depths on the same 30-second clip, to be picked from the encode.

Only cfg["star_fade"] varies - the field, the seed, the periods and the phases
are identical in all three, so they are the same sky fading by different
amounts rather than three different skies. 1.00 is what ships; the other two
are here so the choice can be re-made against a real encode rather than a
number, which is how it was made in the first place.

    py tools/stars_fade_options.py       # three encodes into renders/stars-fade/
"""
import os, sys, wave

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "lss_studio"))

import numpy as np
import lss_render as R
import lss_presets as P

RENDERS = os.path.join(ROOT, "renders")
AUDIO = os.path.join(RENDERS, "_twinkle_audio.wav")
OUT = os.path.join(RENDERS, "stars-fade")

OPTIONS = [
    ("A_fade080", 0.80, "clearly faint, still visibly a star at the bottom"),
    ("B_fade092", 0.92, "barely there at the bottom"),
    ("C_fade100", 1.00, "gone entirely - disappears and returns. SHIPPED"),
]


def make_audio():
    """A 30-second clip with a little structure. Short on purpose: judging a
    fade wants several periods, not a long file."""
    os.makedirs(RENDERS, exist_ok=True)
    if os.path.exists(AUDIO):
        return
    sr, dur = 48000, 30.0
    t = np.arange(int(sr * dur)) / sr
    rng = np.random.default_rng(7)
    x = rng.normal(0, 0.06, len(t)) * (
        0.5 + 0.45 * np.sin(2 * np.pi * t / 17.0)
        + 0.25 * np.sin(2 * np.pi * t / 6.3))
    for c in (4.0, 11.5, 19.0, 25.2):
        i, n = int(c * sr), int(1.0 * sr)
        x[i:i + n] += rng.normal(0, 0.35, n) * np.hanning(n)
    x = np.clip(x, -0.99, 0.99)
    w = wave.open(AUDIO, "wb")
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
    w.writeframes((x * 32767).astype("<i2").tobytes()); w.close()


def main():
    make_audio()
    presets = P.load()
    name, base = P.resolve("Sounds of the City", "None (use series colour)",
                           presets, None)
    bg, fg, acc = P.resolve_colors("Night", "None (use series colour)",
                                   presets, base, None)
    for key, fade, note in OPTIONS:
        cfg = {
            "series": name, "place": "SOUTH MOUNTAIN PARK", "city": "PHOENIX",
            "conditions": "CLEAR 78F", "date": "2026-08-11",
            "start": "06:30 PM", "number": "7", "number_style": "No.",
            "background": bg, "foreground": fg, "accent": acc,
            "color_preset": "Night", "geometry": "steps",
            "scene": "town", "style": "city", "detail": "Default",
            "towers": "Default", "scale": "Skyline (rank)", "dynamics": "More",
            "height_stat": "peak", "align_loud": True, "rows": 1,
            "filled": True, "stars": True, "star_fade": fade,
            "width": 2560, "height": 1440, "fps": 10,
            "audio": AUDIO, "outdir": OUT, "outname": key,
        }
        r = R.run(cfg, progress=lambda s: None)
        print(f"  {key}  fade {fade:.2f}  {note}")
        print(f"    {r['video']}")


if __name__ == "__main__":
    main()
