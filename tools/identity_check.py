"""Byte-identity harness: render a matrix of existing styles and SHA them.

Run once at HEAD to capture a baseline, once after a change to compare. Every
case here is a style/palette/state combination that predates the change under
test, so any difference at all is a regression.

    py tools/identity_check.py baseline      # at the commit you are changing FROM
    py tools/identity_check.py after         # with your change applied
    py tools/identity_check.py compare

Renders and the SHA files land in renders/, which is gitignored: they are
specific to this machine's fonts, so a committed baseline would be misleading
rather than useful. Regenerate the baseline from the old commit instead.
"""
import hashlib, json, os, shutil, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "lss_studio"))

import lss_render as R
import lss_presets as P

RENDERS = os.path.join(ROOT, "renders")
AUDIO = os.path.join(RENDERS, "_identity_audio.wav")
OUT = os.path.join(RENDERS, "stars-identity")

# style, scene, and the switches that style actually reaches
CASES = [
    ("nature", "topo", {}),
    ("nature", "topo", {"filled": True}),
    ("nature", "mountains", {"mountain_face": "outline"}),
    ("nature", "mountains", {"mountain_face": "twotone"}),
    ("nature", "forest", {"tree_ahead": "faint"}),
    ("nature", "forest", {"tree_ahead": "outline"}),
    ("nature", "mountains_forest", {"mountain_face": "twotone",
                                    "tree_ahead": "faint"}),
    ("town", "blocks", {}),
    ("town", "blocks", {"filled": True}),
    ("town", "blocks", {"rows": 3}),
    ("town", "houses", {"filled": True}),
    ("town", "houses", {}),
    ("town", "city", {"filled": True}),
    ("town", "city", {"filled": True, "detail": "Fine"}),
    ("town", "city", {"filled": True, "no_blink": True}),
]
PALETTES = ["Night", "Mist", "Morning"]
PROGRESS = [0.0, 1.0]
# Stars have been on by default since 1.9.0, so a matrix that only ever runs
# with them off tests half the shipped behaviour. Both, on every case: Morning
# carries no field and renders without one, which is itself worth pinning.
STARS = [False, True]

# The video half. One short case per style rather than the full matrix - what
# it is guarding is video_layers, the blink and twinkle filter graphs and
# build_video, and every one of those runs the same whatever the palette is.
VIDEO_CASES = [(scene, style, extra) for scene, style, extra in [
    ("nature", "topo", {}),
    ("nature", "mountains", {"mountain_face": "twotone"}),
    ("nature", "forest", {"tree_ahead": "faint"}),
    ("nature", "mountains_forest", {}),
    ("town", "blocks", {"filled": True}),
    ("town", "houses", {"filled": True}),
    ("town", "city", {"filled": True}),
]]
VIDEO_AUDIO = os.path.join(RENDERS, "_identity_audio_short.wav")

SERIES_FOR = {"nature": "Sounds of Nature", "town": "Sounds of the City"}


def make_audio():
    os.makedirs(RENDERS, exist_ok=True)
    # both clips, or a tree that already has the long one skips the short one
    # and the video half dies at the first encode
    if os.path.exists(AUDIO) and os.path.exists(VIDEO_AUDIO):
        return
    import numpy as np, wave
    sr, dur = 48000, 120.0
    t = np.arange(int(sr * dur)) / sr
    rng = np.random.default_rng(7)
    x = rng.normal(0, 0.06, len(t)) * (
        0.5 + 0.45 * np.sin(2 * np.pi * t / 37.0)
        + 0.25 * np.sin(2 * np.pi * t / 11.3))
    for c in (12.0, 41.5, 63.0, 88.2, 103.7):
        i, n = int(c * sr), int(1.2 * sr)
        x[i:i + n] += rng.normal(0, 0.35, n) * np.hanning(n)
    x = np.clip(x, -0.99, 0.99)
    w = wave.open(AUDIO, "wb")
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
    w.writeframes((x * 32767).astype("<i2").tobytes()); w.close()
    # ...and the first 20 seconds of it for the video cases. Long enough that
    # the mask slides, the beacons cross the playhead and the twinklers cycle;
    # short enough that seven encodes are not the reason nobody runs this.
    w = wave.open(VIDEO_AUDIO, "wb")
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
    w.writeframes((x[:sr * 20] * 32767).astype("<i2").tobytes()); w.close()


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def sha_frames(path):
    """SHA the DECODED frames, not the container.

    An mp4 carries metadata that moves between runs - creation time among it -
    so hashing the file would report a regression on every rebuild and prove
    nothing. Decoding first hashes what was actually rendered, which is the
    thing the guarantee is about, and it fails loudly if the pixels move by
    one level anywhere in the timeline.
    """
    p = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-i", path, "-f", "rawvideo",
         "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
    h = hashlib.sha256()
    for chunk in iter(lambda: p.stdout.read(1 << 20), b""):
        h.update(chunk)
    p.stdout.close()
    if p.wait() != 0:
        raise SystemExit(f"decode failed: {path}")
    return h.hexdigest()


def render_all(tag):
    presets = P.load()
    shutil.rmtree(OUT, ignore_errors=True)
    os.makedirs(OUT, exist_ok=True)
    out = {}
    for scene, style, extra in CASES:
        for pal in PALETTES:
          for stars in STARS:
            for prog in PROGRESS:
                series = SERIES_FOR[scene]
                name, base = P.resolve(series, "None (use series colour)",
                                       presets, None)
                bg, fg, acc = P.resolve_colors(pal, "None (use series colour)",
                                               presets, base, None)
                key = (f"{scene}_{style}_{pal}_p{int(prog)}"
                       + ("_stars" if stars else "")
                       + ("_" + "_".join(f"{k}-{v}" for k, v in sorted(extra.items()))
                          if extra else ""))
                cfg = {
                    "series": name, "place": "SOUTH MOUNTAIN PARK",
                    "city": "PHOENIX", "conditions": "CLEAR 78F",
                    "date": "2026-08-11", "start": "06:30 PM",
                    "number": "7", "number_style": "No.",
                    "background": bg, "foreground": fg, "accent": acc,
                    "geometry": P.series_geometry(series, presets),
                    "scene": scene, "style": style,
                    "detail": "Default", "towers": "Default",
                    "scale": "Skyline (rank)", "dynamics": "More",
                    "height_stat": "peak", "align_loud": True,
                    "rows": 1, "filled": False, "progress": prog,
                    "thumb_only": True, "thumb_width": 1920,
                    "stars": stars, "color_preset": pal,
                    "audio": AUDIO, "outdir": OUT, "outname": key,
                    "number_style": "No.",
                }
                cfg.update(extra)
                r = R.run(cfg, progress=lambda s: None)
                out[key] = sha(r["thumbnail"])
                print(f"  {key}  {out[key][:16]}")
    # ---- the video half -----------------------------------------------
    # Everything the thumbnail matrix cannot reach: the sliding mask, the
    # beacon and twinkle filter graphs, and the encode itself.
    for scene, style, extra in VIDEO_CASES:
        series = SERIES_FOR[scene]
        name, base = P.resolve(series, "None (use series colour)",
                               presets, None)
        bg, fg, acc = P.resolve_colors("Night", "None (use series colour)",
                                       presets, base, None)
        key = f"VIDEO_{scene}_{style}"
        cfg = {
            "series": name, "place": "SOUTH MOUNTAIN PARK",
            "city": "PHOENIX", "conditions": "CLEAR 78F",
            "date": "2026-08-11", "start": "06:30 PM",
            "number": "7", "number_style": "No.",
            "background": bg, "foreground": fg, "accent": acc,
            "geometry": P.series_geometry(series, presets),
            "scene": scene, "style": style,
            "detail": "Default", "towers": "Default",
            "scale": "Skyline (rank)", "dynamics": "More",
            "height_stat": "peak", "align_loud": True,
            "rows": 1, "filled": False, "progress": 1.0,
            "thumb_only": False, "thumb_width": 1920,
            "stars": True, "color_preset": "Night",
            "width": 1280, "height": 720, "fps": 10,
            "audio": VIDEO_AUDIO, "outdir": OUT, "outname": key,
        }
        cfg.update(extra)
        r = R.run(cfg, progress=lambda s: None)
        out[key] = sha_frames(r["video"])
        print(f"  {key}  {out[key][:16]}")

    path = os.path.join(RENDERS, f"identity_{tag}.json")
    json.dump(out, open(path, "w"), indent=2)
    print(f"\n{len(out)} cases -> {path}")
    return out


def compare():
    a = json.load(open(os.path.join(RENDERS, "identity_baseline.json")))
    b = json.load(open(os.path.join(RENDERS, "identity_after.json")))
    miss = [k for k in a if k not in b] + [k for k in b if k not in a]
    diff = [k for k in a if k in b and a[k] != b[k]]
    print(f"cases: {len(a)} baseline, {len(b)} after")
    if miss:
        print("MISSING/EXTRA:", ", ".join(miss))
    if diff:
        print(f"DIFFERENT ({len(diff)}):")
        for k in diff:
            print(f"  {k}\n    baseline {a[k]}\n    after    {b[k]}")
    if not miss and not diff:
        print("IDENTICAL - every case matches byte for byte.")
    return not (miss or diff)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "baseline"
    if cmd == "compare":
        sys.exit(0 if compare() else 1)
    make_audio()
    render_all(cmd)
