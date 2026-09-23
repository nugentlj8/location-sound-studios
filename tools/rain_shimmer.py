#!/usr/bin/env python3
"""Shimmering rain: clips to watch, and what it costs to encode.

    py tools/rain_shimmer.py clips     # 30s clips, one per setting and scene
    py tools/rain_shimmer.py bench     # interleaved timed encodes, two lengths

The static rain reads as if it should be moving and nothing happens. Falling
rain fixed that and cost ~1.2x the runtime to render (see rain_motion.py), all
of it in three full-frame overlays. This tries the stars' bargain instead: the
rain stays baked into both layers exactly as it is today, and a sky-coloured
drawbox takes individual streaks away on a timer - a filter that touches only
its own rectangle, measured at ~1us a frame.

A drawbox paints a flat rectangle and cannot tell what is under it, so it can
only erase a streak whose WHOLE rectangle is bare sky - the rule
_visible_stars() applies to a star. A leaning streak's rectangle is 7-9x its
ink, so the test is strict, and a partial tone is impossible: it would paint a
visible grey box, not a fainter line. So a streak is on or off, never between.
Roughly half the rain qualifies; the rest - over the skyline, the clouds, the
slate - stays as still as it is now.

Nothing in lss_studio/ is changed. The experiment is patched in from here:
video_layers() gains a probe frame composed with everything BUT the rain, and
star_layers() gains the rain filters after the stars'. Both are the real
render path otherwise, so the timed command is the real encode command.
"""

import json
import math
import os
import shutil
import subprocess
import sys
import time

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "lss_studio"))

import lss_draw as D
import lss_presets as P
import lss_render as R

RENDERS = os.path.join(REPO, "renders")
OUT = os.path.join(RENDERS, "rain-shimmer")
STAGE = os.path.join(OUT, "_stage")
SOURCE = r"Z:\Sounds of the City\FLAC Export\7-13monsoon.flac"
SOURCE_AT = 600.0                 # seconds in: well into the storm

W, H, FPS = 2560, 1440, 10        # the shipped video defaults
REPS = 3
BUDGET = 140                      # rain filters. The stars take up to 96 and
                                  # the per-filter cost is linear to ~240,
                                  # then sharply worse - so this is the room
PAD = 1                           # px around a box, for the LANCZOS ring


def S(tag, mode, **kw):
    return dict({"tag": tag, "mode": mode}, **kw)


# period is seconds per cycle, drawn per streak from the range; `off` is the
# fraction of it a streak spends erased. glint turns that round - mostly
# erased, briefly shown. drain splits a streak into three boxes and runs a gap
# down it one frame per segment, so the streak appears to pulse DOWNWARD.
SETTINGS = [
    S("1_static", "static"),
    S("2_blink_slow", "blink", period=(2.5, 5.0), off=0.35),
    S("3_blink_fast", "blink", period=(0.8, 1.6), off=0.50),
    S("4_glint", "blink", period=(1.5, 3.5), off=0.70),
    S("5_drain", "drain", period=(1.0, 2.0), segs=3, step=0.1, hold=0.2),
]

SCENES = [
    # (label, series, style, palette, extra)
    ("city_night", "Sounds of the City", "city", "Night", {"filled": True}),
    ("nature_mist", "Sounds of Nature", "mountains_forest", "Mist", {}),
]

CURRENT = {"setting": SETTINGS[0], "probe": None, "report": {}}


# ------------------------------------------------------------- the patch
_orig_layers = R.video_layers
_orig_stars = R.star_layers


def video_layers(cfg, lv, W_, H_, d, dur=0):
    paths = _orig_layers(cfg, lv, W_, H_, d, dur)
    wx = cfg.get("_weather") or {}
    CURRENT["probe"] = None
    if CURRENT["setting"]["mode"] != "static" and wx.get("rain"):
        # everything but the rain: stars, clouds, the slate, the beacons LIT -
        # a box may only land where all of that leaves bare sky
        p = os.path.join(d, "_rainprobe.png")
        R.compose(dict(cfg, _bands=None, _weather=R._clouds_only(wx)), lv,
                  W_, H_, cfg.get("foreground") or R.BONE, p, lights_on=True)
        CURRENT["probe"] = p
    return paths


def star_layers(cfg, W_, H_, dur, base=None):
    return _orig_stars(cfg, W_, H_, dur, base=base) + rain_filters(cfg, W_, H_)


R.video_layers = video_layers
R.star_layers = star_layers


def _box(x0, y0, x1, y1, lw):
    return (int(math.floor(min(x0, x1) - lw)) - PAD,
            int(math.floor(min(y0, y1) - lw)) - PAD,
            int(math.ceil(max(x0, x1) + lw)) + PAD,
            int(math.ceil(max(y0, y1) + lw)) + PAD)


def rain_filters(cfg, W_, H_):
    s = CURRENT["setting"]
    wx = cfg.get("_weather") or {}
    if s["mode"] == "static" or not wx.get("rain") or not CURRENT["probe"]:
        return ""
    k = W_ / wx.get("dw", 1280.0)
    bg = cfg.get("background") or R.INK
    img = np.array(Image.open(CURRENT["probe"]).convert("RGB")).astype(np.int32)
    want = np.asarray(D.rgb(bg), dtype=np.int32)
    lw = wx["rain"]["width"] * k
    # the live clock is drawn by ffmpeg into the frame, so the probe cannot
    # see it - keep every box off its whole row on the right
    cb = R.clock_box(W_, H_) if R.has_clock(cfg) else None
    clear = []
    for x0, y0, x1, y1, a in wx["rain"]["streaks"]:
        bx = _box(x0 * k, y0 * k, x1 * k, y1 * k, lw)
        if bx[0] < 0 or bx[1] < 0 or bx[2] > W_ or bx[3] > H_:
            continue
        if cb and bx[2] > W_ * 0.55 and bx[1] < cb["y"] + cb["size"] * 1.6 \
                and bx[3] > cb["y"] - cb["size"] * 0.4:
            continue
        if np.abs(img[bx[1]:bx[3], bx[0]:bx[2]] - want).sum(axis=2).max() == 0:
            clear.append((a, (x0 * k, y0 * k, x1 * k, y1 * k)))
    # the most opaque first - a faint streak blinking is a change nobody sees
    clear.sort(key=lambda c: -c[0])
    per = s.get("segs", 1)
    chosen = clear[:BUDGET // per]
    rng = np.random.default_rng(wx["seed"] ^ 0x5A1E)
    col = "0x%02X%02X%02X" % D.rgb(bg)
    out = []
    for _, (x0, y0, x1, y1) in chosen:
        p = float(rng.uniform(*s["period"]))
        ph = float(rng.uniform(0.0, p))
        m = f"mod(t+{ph:.3f}\\,{p:.3f})"
        if s["mode"] == "blink":
            parts = [((x0, y0, x1, y1), f"lt({m}\\,{p * s['off']:.3f})")]
        else:
            # top to bottom: the streak's upper end is whichever has smaller y
            if y1 < y0:
                x0, y0, x1, y1 = x1, y1, x0, y0
            n = s["segs"]
            parts = []
            for j in range(n):
                a0, a1 = j / n, (j + 1) / n
                seg = (x0 + (x1 - x0) * a0, y0 + (y1 - y0) * a0,
                       x0 + (x1 - x0) * a1, y0 + (y1 - y0) * a1)
                t0 = j * s["step"]
                parts.append((seg, f"between({m}\\,{t0:.3f}\\,"
                                   f"{t0 + s['hold'] - 0.001:.3f})"))
        for seg, on in parts:
            bx = _box(*seg, lw)
            out.append(f"drawbox=x={bx[0]}:y={bx[1]}:w={bx[2] - bx[0]}"
                       f":h={bx[3] - bx[1]}:color={col}:t=fill:enable='{on}'")
    CURRENT["report"] = {"streaks": len(wx["rain"]["streaks"]),
                         "clear": len(clear), "shimmering": len(chosen),
                         "filters": len(out)}
    return ("," + ",".join(out)) if out else ""


# ------------------------------------------------------------- rendering
def clip_audio(secs):
    p = os.path.join(OUT, f"_monsoon_{secs}s.wav")
    if not os.path.exists(p):
        os.makedirs(OUT, exist_ok=True)
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", str(SOURCE_AT),
                        "-t", str(secs), "-i", SOURCE, p], check=True)
    return p


def cfg_for(scene, secs, out, name):
    label, series, style, pal, extra = scene
    presets = P.load()
    nm, base = P.resolve(series, "None (use series colour)", presets, None)
    bg, fg, acc = P.resolve_colors(pal, "None (use series colour)", presets,
                                   base, None)
    cfg = {
        "series": nm, "place": "PINE HOLLOW" if "Nature" in series
        else "DOWNTOWN PHOENIX", "city": "PAYSON, AZ" if "Nature" in series
        else "PHOENIX, AZ", "conditions": "RAIN 64F", "date": "2026-07-13",
        "start": "06:30 PM", "number": "", "number_style": "No.",
        "background": bg, "foreground": fg, "accent": acc,
        "geometry": P.series_geometry(series, presets),
        "scene": P.series_scene(series, presets), "style": style,
        "detail": "Default", "towers": "Default", "scale": "Skyline (rank)",
        "dynamics": "More", "height_stat": "peak", "align_loud": True,
        "rows": 1, "filled": False, "stars": True, "weather": "rain",
        "color_preset": pal, "thumb_width": 1280, "width": W, "height": H,
        "fps": FPS, "audio": clip_audio(secs), "outdir": out, "outname": name,
        # the renderer shimmers sky rain itself since 1.15.0; this tool's
        # settings are the alternatives it was chosen from, so they draw alone
        "no_shimmer": True,
    }
    cfg.update(extra)
    return cfg


def clips(secs=30):
    os.makedirs(OUT, exist_ok=True)
    only = sys.argv[2:]
    for scene in SCENES:
        for s in SETTINGS:
            tag = f"{scene[0]}_{s['tag']}"
            if only and s["tag"] not in only and tag not in only:
                continue
            CURRENT["setting"], CURRENT["report"] = s, {}
            t0 = time.time()
            res = R.run(cfg_for(scene, secs, STAGE, tag),
                        progress=lambda x: None)
            dt = time.time() - t0
            dest = os.path.join(OUT, tag + ".mp4")
            shutil.copyfile(res["video"], dest)
            r = CURRENT["report"]
            print(f"  {tag:32} {os.path.getsize(dest) / 1e6:6.2f} MB  "
                  f"{dt:5.1f}s  "
                  + (f"{r['shimmering']} of {r['clear']} clear / "
                     f"{r['streaks']} streaks, {r['filters']} filters"
                     if r else "static"), flush=True)
    shutil.rmtree(STAGE, ignore_errors=True)
    print(f"\n-> {OUT}")


# ------------------------------------------------------------- bench
def stage(scene, s, secs):
    """Render once, keeping the layers and the REAL encode command."""
    tag = f"{scene[0]}_{s['tag']}_{secs}s"
    d = os.path.join(STAGE, tag)
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d, exist_ok=True)
    grabbed = {}
    real = R.subprocess.Popen

    class Fake:
        stdout = iter(())
        returncode = 0

        def wait(self):
            return 0

    def spy(cmd, **kw):
        if cmd and cmd[0] == "ffmpeg" and "-filter_complex_script" in cmd:
            # copy NOW: run() deletes its work folder as soon as it returns
            cmd = list(cmd)
            for i, a in enumerate(cmd):
                if os.path.isfile(a) and a[-4:] in (".png", ".txt", ".wav"):
                    dst = os.path.join(d, os.path.basename(a))
                    shutil.copy2(a, dst)
                    cmd[i] = dst
            cmd[-1] = os.path.join(d, "out.mp4")
            grabbed["cmd"] = [c for c in cmd if c not in
                              ("-progress", "pipe:1", "-nostats")]
            return Fake()
        return real(cmd, **kw)

    CURRENT["setting"], CURRENT["report"] = s, {}
    R.subprocess.Popen = spy
    try:
        R.run(cfg_for(scene, secs, os.path.join(d, "r"), tag),
              progress=lambda x: None)
    finally:
        R.subprocess.Popen = real
    return tag, grabbed["cmd"], dict(CURRENT["report"])


def bench():
    """Encode time and size, interleaved, at two lengths.

    Two lengths separate the one-time graph setup from the per-frame cost -
    only the second one scales to a three-hour render. Interleaved because
    this machine drifts ~5% run to run, the same order as what is measured.
    """
    os.makedirs(OUT, exist_ok=True)
    staged = []
    for secs in (30, 120):
        for s in SETTINGS:
            tag, cmd, rep = stage(SCENES[0], s, secs)
            staged.append((tag, secs, s["tag"], cmd, rep))
            print("staged", tag, rep or "", flush=True)
    times = {t[0]: [] for t in staged}
    size = {}
    for r in range(REPS):
        for tag, secs, st, cmd, rep in staged:
            t0 = time.time()
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
            times[tag].append(time.time() - t0)
            size[tag] = os.path.getsize(cmd[-1]) / 1e6
        print(f"  pass {r + 1}/{REPS}", flush=True)
    rows = []
    for tag, secs, st, cmd, rep in staged:
        ts = sorted(times[tag])
        rows.append({"setting": st, "secs": secs, "median_s": ts[len(ts) // 2],
                     "all_s": [round(x, 2) for x in times[tag]],
                     "mb": size[tag], "filters": rep.get("filters", 0)})
    json.dump(rows, open(os.path.join(OUT, "bench.json"), "w"), indent=2)
    print(f"\n{W}x{H} {FPS}fps, city Night, median of {REPS} interleaved\n")
    print(f"{'setting':16}{'len':>5}{'filters':>9}{'encode s':>10}"
          f"{'xRT':>7}{'vs static':>11}{'MB/min':>9}{'vs static':>11}")
    base = {r["secs"]: r for r in rows if r["setting"] == "1_static"}
    for r in rows:
        b = base[r["secs"]]
        print(f"{r['setting']:16}{r['secs']:>5}{r['filters']:>9}"
              f"{r['median_s']:>10.2f}{r['secs'] / r['median_s']:>7.1f}"
              f"{r['median_s'] / b['median_s']:>10.2f}x"
              f"{r['mb'] * 60 / r['secs']:>9.2f}{r['mb'] / b['mb']:>10.2f}x")
    shutil.rmtree(STAGE, ignore_errors=True)


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "clips"
    {"clips": clips, "bench": bench}[what]()
