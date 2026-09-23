#!/usr/bin/env python3
"""Shimmering rain over the SCENERY: clips to watch, and what it costs.

    py tools/rain_scenery.py clips     # 30s clips: sky only, +60, +120
    py tools/rain_scenery.py bench     # interleaved timed encodes, two lengths

The shipped shimmer (1.15.0) erases a streak with a sky-coloured drawbox, so
only the streaks on bare sky can take part - about half. Over a building,
a mountain, a tree or a cloud there is no one colour to paint: the rectangle
holds walls and windows, and they are a different colour either side of the
playhead.

This tries the beacons' answer to that. Two rain-free copies of the frame -
unplayed and played - are composed alongside the real layers, and for each
scenery streak a PATCH of the matching copy is laid over it during its off
window: the unplayed patch until the playhead reaches the streak, the played
one after it. While the playhead is actually crossing a streak (the patch
would be half right) that streak just holds still.

Every patch is packed into ONE small atlas image, so the encode reads one
small extra input rather than two more full frames every frame.

Nothing in lss_studio/ is changed; the shipped sky shimmer runs as released
and this adds the scenery streaks on top, patched in from here.

RESULT (2026-09-23, 2560x1440 10fps, city Night, median of 3 interleaved):
a dead end. Against sky-only shimmer, 60 scenery streaks (120 overlays) cost
5.0x the encode time over 120s and 120 streaks 10.4x - worse than the falling
rain that was rejected. About 0.4ms per overlay per frame, ~400x a drawbox,
and it is the overlay itself: converting the atlas to yuv420p once instead of
per patch bought 5%. The cost scales with the streak count, so no count is
both worth seeing and affordable.
"""

import json
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
import lss_scene as SC

RENDERS = os.path.join(REPO, "renders")
OUT = os.path.join(RENDERS, "rain-scenery")
STAGE = os.path.join(OUT, "_stage")
SOURCE = r"Z:\Sounds of the City\FLAC Export\7-13monsoon.flac"
SOURCE_AT = 600.0
W, H, FPS = 2560, 1440, 10
REPS = 3

SETTINGS = [("1_sky_only", 0), ("2_scene_60", 60), ("3_scene_120", 120)]
SCENES = [
    ("city_night", "Sounds of the City", "city", "Night", {"filled": True}),
    ("nature_mist", "Sounds of Nature", "mountains_forest", "Mist", {}),
]
CURRENT = {"n": 0, "graph": "", "atlas": None, "report": {}}


def _even_box(b, W_, H_):
    """Snap a patch to even pixels. The chain is yuv420 by the time these
    land, and a patch on an odd edge would carry half a chroma sample of its
    neighbour - a faint box outline around every patch."""
    x0, y0, x1, y1 = b
    x0, y0 = x0 - x0 % 2, y0 - y0 % 2
    x1, y1 = x1 + x1 % 2, y1 + y1 % 2
    return max(0, x0), max(0, y0), min(W_, x1), min(H_, y1)


def _hits(b, rects):
    return any(b[0] < r[2] and r[0] < b[2] and b[1] < r[3] and r[1] < b[3]
               for r in rects)


# ------------------------------------------------------------- the patch
_orig_layers = R.video_layers
_orig_stars = R.star_layers


def video_layers(cfg, lv, W_, H_, d, dur=0):
    paths = _orig_layers(cfg, lv, W_, H_, d, dur)
    CURRENT["graph"], CURRENT["atlas"], CURRENT["report"] = "", None, {}
    wx = cfg.get("_weather") or {}
    rain = wx.get("rain")
    if not CURRENT["n"] or not rain or not dur:
        return paths
    # the two rain-free copies, composed EXACTLY as video_layers composes the
    # real ones - same bands, same beacon state - bar the rain
    bands = R.make_bands(cfg, W_, dur)
    c2 = dict(cfg, _bands=bands, _weather=R._clouds_only(wx))
    steady = bool(cfg.get("no_blink"))
    fg = cfg.get("foreground") or R.BONE
    bone = R.compose(c2, lv, W_, H_, fg, os.path.join(d, "_nr_bone.png"),
                     lights_on=steady)
    clay = R.compose(c2, lv, W_, H_, "__cycle__" if bands else cfg["accent"],
                     os.path.join(d, "_nr_clay.png"), played=True,
                     lights_on=steady)
    k = W_ / wx.get("dw", 1280.0)
    lw = rain["width"] * k
    streaks = rain["streaks"]
    sky = set(R._visible_rain(streaks, paths["rainprobe"], k, lw,
                              cfg.get("background") or R.INK)
              if paths.get("rainprobe") else [])
    # what a patch must not land on, because it is drawn AFTER them in this
    # prototype: the live clock's row, a beacon, a twinkling star. The shipped
    # version would go ahead of all three in the chain instead
    avoid = []
    if R.has_clock(cfg):
        cb = R.clock_box(W_, H_)
        avoid.append((int(W_ * 0.55), cb["y"] - cb["size"], W_,
                      cb["y"] + 2 * cb["size"]))
    for L in (cfg.get("_scene") or {}).get("lights", []):
        x0, y0, x1, y1 = (v * k for v in L["rect"])
        avoid.append((int(x0) - 4, int(y0) - 4, int(x1) + 4, int(y1) + 4))
    for s in (cfg.get("_sky") or {}).get("stars", []):
        x, y, n = D.star_rect(s, k)
        avoid.append((x - 3, y - 3, x + n + 3, y + n + 3))
    cand = []
    for i, st in enumerate(streaks):
        if i in sky:
            continue
        b = _even_box(R._rain_box(st, k, lw), W_, H_)
        if b[2] - b[0] < 2 or b[3] - b[1] < 2 or _hits(b, avoid):
            continue
        cand.append((st[4], i, b))
    cand.sort(key=lambda c: -c[0])
    chosen = cand[:CURRENT["n"]]

    # the atlas: every patch, bone then clay, shelf-packed
    bi, ci = Image.open(bone).convert("RGB"), Image.open(clay).convert("RGB")
    AW, x, y, row, place = 1024, 0, 0, 0, []
    for _, i, b in chosen:
        w, h = b[2] - b[0], b[3] - b[1]
        spots = []
        for _src in (0, 1):
            if x + w > AW:
                x, y, row = 0, y + row, 0
            spots.append((x, y))
            x += w + 2
            row = max(row, h + 2)
        place.append((i, b, spots))
    AH = y + row + (y + row) % 2
    atlas = Image.new("RGB", (AW, max(2, AH)))
    for i, b, spots in place:
        for src, (ax, ay) in zip((bi, ci), spots):
            atlas.paste(src.crop(b), (ax, ay))
    apath = os.path.join(d, "_nr_atlas.png")
    atlas.save(apath)
    CURRENT["atlas"] = apath

    # the graph, continuing the main chain after the stars' filters
    off = SC.RAIN_SHIMMER_OFF
    n2 = 2 * len(place)
    # the atlas goes to the chain's yuv420p ONCE, before the split - left as
    # RGB, ffmpeg would convert every patch separately, every frame
    g = [",null[q0]", "[4:v]format=yuv420p,split=%d%s" %(n2, "".join(f"[a{j}]" for j in range(n2)))]
    q = 0
    for j, (i, b, spots) in enumerate(place):
        p, ph = rain["timing"][i]
        m = f"lt(mod(t+{ph:.3f}\\,{p:.3f})\\,{p * off:.3f})"
        t0, t1 = b[0] * dur / W_, b[2] * dur / W_
        w, h = b[2] - b[0], b[3] - b[1]
        for side, (ax, ay) in enumerate(spots):
            a = 2 * j + side
            g.append(f"[a{a}]crop={w}:{h}:{ax}:{ay}[p{a}]")
            when = f"lt(t\\,{t0:.3f})" if side == 0 else f"gt(t\\,{t1:.3f})"
            g.append(f"[q{q}][p{a}]overlay=x={b[0]}:y={b[1]}"
                     f":enable='{when}*{m}'[q{q + 1}]")
            q += 1
    g.append(f"[q{q}]null")
    CURRENT["graph"] = g[0] + ";" + ";".join(g[1:])
    CURRENT["report"] = {"streaks": len(streaks), "sky": len(sky),
                         "scenery_candidates": len(cand),
                         "scenery": len(place), "overlays": q,
                         "atlas": f"{AW}x{AH}"}
    return paths


def star_layers(cfg, W_, H_, dur, base=None):
    return _orig_stars(cfg, W_, H_, dur, base=base) + CURRENT["graph"]


R.video_layers = video_layers
R.star_layers = star_layers
_real_popen = R.subprocess.Popen


def _inject(cmd):
    """The atlas as input 4, ahead of the filter graph that names it."""
    if CURRENT["atlas"] and "-filter_complex_script" in cmd:
        i = cmd.index("-filter_complex_script")
        cmd = cmd[:i] + ["-loop", "1", "-framerate", str(FPS),
                         "-i", CURRENT["atlas"]] + cmd[i:]
    return cmd


def popen(cmd, **kw):
    return _real_popen(_inject(list(cmd)), **kw)


R.subprocess.Popen = popen


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
    nature = "Nature" in series
    cfg = {
        "series": nm, "place": "PINE HOLLOW" if nature else "DOWNTOWN PHOENIX",
        "city": "PAYSON, AZ" if nature else "PHOENIX, AZ",
        "conditions": "RAIN 64F", "date": "2026-07-13", "start": "06:30 PM",
        "number": "", "number_style": "No.",
        "background": bg, "foreground": fg, "accent": acc,
        "geometry": P.series_geometry(series, presets),
        "scene": P.series_scene(series, presets), "style": style,
        "detail": "Default", "towers": "Default", "scale": "Skyline (rank)",
        "dynamics": "More", "height_stat": "peak", "align_loud": True,
        "rows": 1, "filled": False, "stars": True, "weather": "rain",
        "color_preset": pal, "thumb_width": 1280, "width": W, "height": H,
        "fps": FPS, "audio": clip_audio(secs), "outdir": out, "outname": name,
    }
    cfg.update(extra)
    return cfg


def clips(secs=30):
    os.makedirs(OUT, exist_ok=True)
    for scene in SCENES:
        for tag, n in SETTINGS:
            name = f"{scene[0]}_{tag}"
            CURRENT["n"] = n
            t0 = time.time()
            res = R.run(cfg_for(scene, secs, STAGE, name),
                        progress=lambda x: None)
            dest = os.path.join(OUT, name + ".mp4")
            shutil.copyfile(res["video"], dest)
            print(f"  {name:28} {os.path.getsize(dest) / 1e6:6.2f} MB  "
                  f"{time.time() - t0:5.1f}s  {CURRENT['report'] or 'sky only'}",
                  flush=True)
    shutil.rmtree(STAGE, ignore_errors=True)
    print(f"\n-> {OUT}")


# ------------------------------------------------------------- bench
def stage(scene, tag, n, secs):
    name = f"{scene[0]}_{tag}_{secs}s"
    d = os.path.join(STAGE, name)
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d, exist_ok=True)
    grabbed = {}

    class Fake:
        stdout = iter(())
        returncode = 0

        def wait(self):
            return 0

    def spy(cmd, **kw):
        cmd = _inject(list(cmd))
        if cmd and cmd[0] == "ffmpeg" and "-filter_complex_script" in cmd:
            for i, a in enumerate(cmd):     # copy NOW: run() deletes its work
                if os.path.isfile(a) and a[-4:] in (".png", ".txt", ".wav"):
                    dst = os.path.join(d, os.path.basename(a))
                    shutil.copy2(a, dst)
                    cmd[i] = dst
            cmd[-1] = os.path.join(d, "out.mp4")
            grabbed["cmd"] = [c for c in cmd if c not in
                              ("-progress", "pipe:1", "-nostats")]
            return Fake()
        return _real_popen(cmd, **kw)

    CURRENT["n"] = n
    R.subprocess.Popen = spy
    try:
        R.run(cfg_for(scene, secs, os.path.join(d, "r"), name),
              progress=lambda x: None)
    finally:
        R.subprocess.Popen = popen
    return name, grabbed["cmd"], dict(CURRENT["report"])


def bench():
    os.makedirs(OUT, exist_ok=True)
    staged = []
    for secs in (30, 120):
        for tag, n in SETTINGS:
            name, cmd, rep = stage(SCENES[0], tag, n, secs)
            staged.append((name, secs, tag, cmd, rep))
            print("staged", name, rep or "", flush=True)
    # every staged command already carries its own atlas; the timed runs below
    # go through the same patched Popen, and must not have one added again
    CURRENT["atlas"] = None
    times = {s[0]: [] for s in staged}
    size = {}
    for r in range(REPS):
        for name, secs, tag, cmd, rep in staged:
            t0 = time.time()
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
            times[name].append(time.time() - t0)
            size[name] = os.path.getsize(cmd[-1]) / 1e6
        print(f"  pass {r + 1}/{REPS}", flush=True)
    rows = []
    for name, secs, tag, cmd, rep in staged:
        ts = sorted(times[name])
        rows.append({"setting": tag, "secs": secs, "median_s": ts[len(ts) // 2],
                     "all_s": [round(x, 2) for x in times[name]],
                     "mb": size[name], "overlays": rep.get("overlays", 0)})
    json.dump(rows, open(os.path.join(OUT, "bench.json"), "w"), indent=2)
    print(f"\n{W}x{H} {FPS}fps, city Night, sky shimmer ON in every row, "
          f"median of {REPS} interleaved\n")
    print(f"{'setting':14}{'len':>5}{'overlays':>10}{'encode s':>10}"
          f"{'xRT':>7}{'vs sky':>9}{'MB/min':>9}{'vs sky':>9}")
    base = {r["secs"]: r for r in rows if r["setting"] == "1_sky_only"}
    for r in rows:
        b = base[r["secs"]]
        print(f"{r['setting']:14}{r['secs']:>5}{r['overlays']:>10}"
              f"{r['median_s']:>10.2f}{r['secs'] / r['median_s']:>7.1f}"
              f"{r['median_s'] / b['median_s']:>8.2f}x"
              f"{r['mb'] * 60 / r['secs']:>9.2f}{r['mb'] / b['mb']:>8.2f}x")
    shutil.rmtree(STAGE, ignore_errors=True)


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "clips"
    {"clips": clips, "bench": bench}[what]()
