#!/usr/bin/env python3
"""
Location Sound Studios - render engine.

Reads a rendered audio file, derives a level envelope from it, and produces
the thumbnail plus the finished video.

Requires: python 3.8+, numpy, pillow, ffmpeg on PATH.
"""

import argparse, calendar, datetime, json, math, os, shutil, subprocess, sys, time
import numpy as np
import lss_presets as presets_mod
import lss_draw as D
import lss_scene as scene_mod
from PIL import Image, ImageDraw, ImageFont

INK, BONE = "#13232E", "#F0E7D6"     # the default sky, and what reads on it
FONT = None                      # resolved at runtime

DEFAULT_OUT = r"Z:\Sounds of the City\LSS Renders"
DEFAULT_IN = r"Z:\Sounds of the City\FLAC Export"


def usable(path, fallback_name):
    """Use the configured folder if its drive is mounted, else fall back home."""
    home = os.path.join(os.path.expanduser("~"), fallback_name)
    looks_windows = len(path) > 1 and path[1] == ":"
    if os.name != "nt":
        return home if looks_windows else path
    if looks_windows and not os.path.isdir(path[:2] + os.sep):
        return home
    return path


def default_outdir(presets=None):
    """Where renders land when --outdir is not given.

    LSS_OUTDIR first, then an "outdir" key in lss_presets.json, then the
    network share. Both of those set a persistent default; --outdir stays the
    per-run switch so it never goes sticky.
    """
    env = os.environ.get("LSS_OUTDIR", "").strip()
    if env:
        return env
    try:
        p = presets if presets is not None else presets_mod.load()
        j = (p.get("outdir") or "").strip()
        if j:
            return j
    except Exception:
        pass
    return DEFAULT_OUT
LV_MIN, LV_MAX = -0.45, 0.95     # roofline level range
# The loudness-to-height mapping itself lives in lss_scene, so the towers, the
# mountain summits and the treeline all answer to the same --scale/--dynamics.
DYNAMICS = scene_mod.DYNAMICS
# Fixed tower counts, independent of file length, so tower WIDTH is consistent
# across every video. "Auto" grows slowly with duration but stays in a band
# that always reads as a skyline.
TOWERS = {"Thick": 28, "Default": 40, "Thin": 64, "Fine": 90}
TOWERS_AUTO = (34, 72)
SCALES = ["Skyline (rank)", "Auto (percentile)", "Fixed loudness"]
NUM_STYLES = {"None (number only)": "{n}", "No.": "NO. {n}", "\u2116": "\u2116 {n}",
              "Rec": "REC {n}", "Ep.": "EP. {n}", "Episode": "EPISODE {n}",
              "Take": "TAKE {n}"}
# fixed building counts (thumb, video). Chosen for readable tower WIDTH, not
# time resolution - a wider block just means each tower summarises more time,
# and peak-height + onset-align keep loud events visible regardless.


# ----------------------------------------------------------------- fonts
def find_font():
    cands = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf",
        "C:/Windows/Fonts/BarlowCondensed-Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ]
    env = os.environ.get("LSS_FONT")
    if env:
        cands.insert(0, env)
    for c in cands:
        if os.path.exists(c):
            return c
    raise SystemExit("No font found. Set LSS_FONT to a .ttf path.")


# ----------------------------------------------------------------- audio
def probe_duration(path):
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                            "format=duration", "-of", "default=nk=1:nw=1", path],
                           capture_output=True, text=True, check=True)
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def envelope(path, on_progress=None, win=0.10):
    """Per-window RMS in dBFS, streamed so a two-hour file never lands in RAM."""
    total = probe_duration(path)
    cmd = ["ffmpeg", "-v", "error", "-i", path, "-ac", "1",
           "-f", "s16le", "-ar", "48000", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    wn = max(1, int(48000 * win))
    parts, leftover, done = [], b"", 0
    while True:
        chunk = p.stdout.read(1 << 20)
        if not chunk:
            break
        buf = leftover + chunk
        n = (len(buf) // 2 // wn) * wn * 2
        if n:
            a = (np.frombuffer(buf[:n], dtype="<i2")
                 .astype(np.float32) / 32768.0).reshape(-1, wn)
            parts.append(np.sqrt((a ** 2).mean(axis=1)))
            done += a.shape[0] * wn
            if on_progress and total > 0:
                on_progress(min(1.0, done / 48000.0 / total))
        leftover = buf[n:]
    p.stdout.close()
    err = p.stderr.read().decode("utf-8", "replace")
    p.wait()
    if p.returncode != 0 or not parts:
        raise SystemExit(f"ffmpeg could not read audio from {path}\n{err[:400]}")
    rms = np.concatenate(parts)
    dur = total if total > 0 else len(rms) * win
    return 20 * np.log10(rms + 1e-12), dur


def find_standouts(db, dur, n, thresh=4.0):
    """Frame indices where the recording is clearly louder than its own bed.
    Robust (median/MAD) so the threshold adapts per recording. Returns at most
    one onset per building so alignment never fights itself."""
    x = 10 ** (db / 20.0)
    med = np.median(x)
    mad = np.median(np.abs(x - med)) + 1e-9
    z = (x - med) / (1.4826 * mad)
    loud = z > thresh
    onsets = np.where(loud & ~np.concatenate([[False], loud[:-1]]))[0]
    bw = len(db) / n
    merged = []
    for o in onsets:
        if not merged or (o - merged[-1]) > bw:   # one marker per building
            merged.append(int(o))
    return merged


def block_edges(db, n, align=True, snap_frac=0.45):
    """n+1 boundaries. Uniform, except edges near a loud onset snap onto it so
    the loud building's LEFT edge coincides with the sound. Widths stay close
    to uniform, so on-screen x still tracks time to within a fraction of a
    building; only the quiet gaps absorb the small shifts."""
    L = len(db)
    edges = np.linspace(0, L, n + 1)
    if align:
        bw = L / n
        for o in find_standouts(db, L, n):        # dur arg unused inside
            k = int(round(o / bw))
            if 0 < k < n and abs(edges[k] - o) <= bw * snap_frac:
                edges[k] = o
        edges = np.maximum.accumulate(edges)
        edges[0], edges[-1] = 0, L
    return edges.astype(int)


def tower_count(width, dur):
    """How many towers to draw. Fixed presets keep tower WIDTH constant across
    videos; Auto grows slowly with length but stays skyline-readable."""
    if width in TOWERS:
        return TOWERS[width]
    lo, hi = TOWERS_AUTO
    return int(max(lo, min(hi, round(dur / 12.0))))   # ~12s/tower, clamped


def to_levels(db, n, scale="Skyline (rank)", dynamics="More", align=True,
              stat="peak"):
    """Reduce the envelope to n buildings and map into the level range.
    stat='peak' makes each building as tall as its LOUDEST moment, so a brief
    siren isn't averaged away; 'rms' uses the block average (old behaviour)."""
    if len(db) < n:                      # very short file: interpolate up
        d = np.interp(np.linspace(0, len(db) - 1, n), np.arange(len(db)), db)
    else:
        edges = block_edges(db, n, align)
        d = np.empty(n)
        for i in range(n):
            seg = db[edges[i]:max(edges[i] + 1, edges[i + 1])]
            if stat == "peak":
                # 95th percentile: the block's loud moment, ignoring one-frame spikes
                d[i] = np.percentile(seg, 95)
            else:
                lin = 10 ** (seg / 20.0)
                d[i] = 20 * np.log10(np.sqrt((lin ** 2).mean()) + 1e-12)
    # 'Skyline (rank)' keeps every block's true loudness ORDER and gives a
    # skyline profile - many low buildings, a few towers - whatever the source
    # dynamics were. Ratios are not preserved; ranking is.
    x = scene_mod.map_db(d, scale, dynamics)
    return (LV_MIN + x * (LV_MAX - LV_MIN)).round(3).tolist()


# ----------------------------------------------------------------- drawing
# ----------------------------------------------------------------- outputs
def compose(cfg, lv, W, H, line_col, out, time_text=None, played=False):
    """The single composition used for both the thumbnail and the video frames.

    Laid out in 1280x720 design units and scaled by k, so the video is the
    thumbnail at a larger size. `time_text` is drawn only for the thumbnail;
    the video leaves that slot empty and ffmpeg draws a live clock there.

    `played` selects which side of the playhead this frame represents: the
    video builds one of each and lets the sliding mask cut between them.
    """
    k = W / 1280.0
    acc = cfg["accent"]
    bg = cfg.get("background") or INK
    fg = cfg.get("foreground") or BONE
    slate = fg if cfg.get("slate_mono") else acc
    mode = cfg.get("geometry", "steps")
    nrows = int(cfg.get("rows", 1))
    filled = bool(cfg.get("filled", False))
    img, dr = D.new_canvas(W, H, bg)

    bands = cfg.get("_bands") if line_col == "__cycle__" else None
    sc = cfg.get("_scene")
    if sc:
        D.draw_scene(dr, sc, W, H,
                     line_col if line_col != "__cycle__" else acc, bg,
                     played=played, face=cfg.get("mountain_face") or scene_mod.MOUNTAIN_FACE[0],
                     ahead=cfg.get("tree_ahead") or scene_mod.TREE_AHEAD[0],
                     filled=filled, bands=bands)
    else:
        lay = D.row_layout(H, nrows, filled, top=0.66, bot=0.95)
        cols = [line_col if line_col != "__cycle__" else cfg["accent"]] * nrows
        step = (W + 60) / len(lv)
        lw = max(5.0 * k, min(14.0 * k, step * 0.30))
        for (y0, amp), c in zip(lay, cols):
            if bands:
                D.draw_banded(dr, y0, amp, lv, W, lw, mode, bands,
                              filled=filled, baseline=H + 10)
            elif filled:
                D.draw_filled(dr, y0, amp, lv, W, H + 10, c, mode)
            else:
                D.draw_line(dr, y0, amp, lv, W, c, lw, mode)

    M = 84 * k
    n = format_number(cfg.get("number", ""), cfg.get("number_style", "No."))
    nw = D.text_width(n, 27 * k, 11 * k, FONT) if n else 0.0
    avail = W - 2 * M - nw - (40 * k if n else 0)

    # a theme suffix can make the series long; shrink it to clear the number
    ssize, strack = 27 * k, 11 * k
    for _ in range(24):
        if D.text_width(cfg["series"], ssize, strack, FONT) <= avail:
            break
        ssize *= 0.94
        strack *= 0.94
    D.text_run(dr, cfg["series"], ssize, strack, M, 150 * k, fg, FONT)
    if n:
        D.text_run(dr, n, 27 * k, 11 * k, W - M - nw, 150 * k, slate, FONT)

    D.text_run(dr, cfg["place"], 104 * k, 7 * k, M, 296 * k, fg, FONT)
    D.text_run(dr, f'{cfg["city"]}  \u00b7  {cfg["conditions"]}',
               31 * k, 8 * k, M, 360 * k, slate, FONT)
    if time_text:
        w = D.text_width(time_text, 31 * k, 0, FONT)
        D.text_run(dr, time_text, 31 * k, 0, W - M - w, 360 * k, slate, FONT)
    return D.finish(img, W, H, out)


def ff_escape(t):
    """Escape text for use inside an ffmpeg filter argument."""
    return (t.replace("\\", "\\\\").replace(":", "\\:")
             .replace("'", "\\'").replace("%", "\\%")
             .replace(",", "\\,").replace("[", "\\[").replace("]", "\\]"))


def _drawtext_ink_top(size, sample):
    """ffmpeg's drawtext y is its own box top, which is not the ink top.
    Render once at y=0 and measure, so the offset is right for any font.
    Falls back to the font's own ascent if ffmpeg can't be probed."""
    import tempfile
    from PIL import Image as _I, ImageFont
    try:
        fp = FONT.replace("\\", "/").replace(":", "\\:")
        tmp = os.path.join(tempfile.gettempdir(), "_lss_cal.png")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                        "-i", f"color=c=black:s={size*10}x{size*4}:d=1",
                        "-vf", (f"drawtext=fontfile='{fp}':fontsize={size}"
                                f":fontcolor=white:x=5:y=0"
                                f":text='{ff_escape(sample)}'"),
                        "-frames:v", "1", tmp], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        a = np.array(_I.open(tmp).convert("L"))
        ys = np.where(a.max(axis=1) > 60)[0]
        if len(ys):
            return int(ys.min())
    except Exception:
        pass
    f = ImageFont.truetype(FONT, size)
    asc = f.getmetrics()[0]
    return max(0, asc + f.getbbox(sample, anchor="ls")[1])


def has_glyph(ch):
    """True if the loaded font actually draws ch rather than a .notdef box."""
    from PIL import ImageFont
    try:
        f = ImageFont.truetype(FONT, 48)
        a = f.getmask(ch, mode="L")
        b = f.getmask("\uf8ff\u0000"[0], mode="L")   # a codepoint no font defines
        return bytes(a) != bytes(b)
    except Exception:
        return False


def format_number(raw, style="No."):
    """Turn '7' into 'NO. 007'. Non-numeric input is used verbatim."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    tpl = NUM_STYLES.get(style, "{n}")
    if "\u2116" in tpl and not has_glyph("\u2116"):
        tpl = "NO. {n}"                                  # font lacks the numero sign
    pad = style not in ("Ep.", "Episode")      # "EPISODE 007" reads wrong
    if raw.isdigit():
        n = f"{int(raw):03d}" if pad else str(int(raw))
    else:
        n = raw.upper()
    return tpl.format(n=n)


def _safe_name(s):
    """Drop anything that has no business in a folder name."""
    return "".join(ch if (ch.isalnum() or ch in " -_") else "" for ch in s).strip()


def episode_prefix(raw):
    """'7' -> '007', so folders still sort correctly past episode 9.

    Padded to three digits to match how format_number() already prints them.
    A non-numeric label ('bonus') is used verbatim - padding it would be
    meaningless."""
    raw = _safe_name((raw or "").strip())
    if not raw:
        return ""
    return f"{int(raw):03d}" if raw.isdigit() else raw


def output_base(cfg):
    """Folder name for a render, also used for the files inside it.

    With an episode number:  '003 - Phoenix Monsoon Ambience'
    Without one:             the original lowercase slug, unchanged.
    """
    name = (cfg.get("outname") or cfg.get("place") or "").strip()
    ep = episode_prefix(cfg.get("number", ""))
    if not ep:
        slug = _safe_name(name).replace(" ", "_").lower()
        return slug or "render"
    # strip punctuation before case-folding, or "ST. MARY'S" title-cases into
    # "St MaryS" off the apostrophe
    name = " ".join(_safe_name(name).split())
    # place arrives upper-cased for the slate; title-case it so the folder is
    # readable. An outname typed with deliberate casing is left alone.
    if name.isupper():
        name = name.title()
    return f"{ep} - {name}".strip(" -") if name else ep


def _thumbnail(cfg, lv, tw, th, out, work, frac=0.0):
    """One thumbnail. Above 0, `frac` cuts the played and unplayed layers at a
    fixed x instead of a moving one - a mid-playback frame without an encode."""
    if frac > 0:
        b = compose(cfg, lv, tw, th, cfg["foreground"],
                    os.path.join(work, "_t_bone.png"), time_text=cfg["start"])
        c = compose(cfg, lv, tw, th, cfg["accent"],
                    os.path.join(work, "_t_clay.png"), time_text=cfg["start"],
                    played=True)
        return D.progress_composite(b, c, frac, out)
    return compose(cfg, lv, tw, th, cfg["foreground"], out,
                   time_text=cfg["start"])


def variant_filename(slug, v):
    """Palette baked into the name, because a folder of variants is otherwise
    unreviewable: they differ only in colour, and two nearby skies are hard to
    tell apart by eye once they are separate files."""
    return (f"{slug}_thumb_{_safe_name(v['name']).replace(' ', '_')}"
            f"_bg-{v['background'][1:]}_fg-{v['foreground'][1:]}"
            f"_acc-{v['accent'][1:]}.png")


def clock_box(W, H, sample="06:30 PM"):
    """Position and size for the live clock so it lands exactly where the
    thumbnail draws its static one."""
    from PIL import ImageFont
    k = W / 1280.0
    size = int(round(31 * k))
    baseline = 360 * k
    f = ImageFont.truetype(FONT, size)
    ink_top = baseline + f.getbbox(sample, anchor="ls")[1]
    return {"size": size,
            "y": int(round(ink_top - _drawtext_ink_top(size, sample))),
            "right_margin": int(round(84 * k))}


def make_bands(cfg, W, dur):
    """(x_end, colour) pairs so the played line cycles along the timeline."""
    cyc = cfg.get("cycle") or []
    mins = float(cfg.get("cycle_minutes", 3) or 3)
    if len(cyc) < 2 or dur <= 0 or mins <= 0:
        return None
    seg = mins * 60.0
    n = max(1, int(math.ceil(dur / seg)))
    out = []
    for i in range(n):
        x = min(W + 60, (i + 1) * seg / dur * W)
        out.append((x, cyc[i % len(cyc)]))
    out[-1] = (W + 120, out[-1][1])
    return out


def video_layers(cfg, lv, W, H, d, dur=0):
    """bone and clay full frames plus the sliding mask."""
    paths = {}
    bands = make_bands(cfg, W, dur)
    cfg = dict(cfg, _bands=bands)
    fg = cfg.get("foreground") or BONE
    for name, col in (("bone", fg), ("clay", "__cycle__" if bands else cfg["accent"])):
        paths[name] = compose(cfg, lv, W, H, col, os.path.join(d, f"_{name}.png"),
                              played=(name == "clay"))
    paths["mhard"] = D.solid_mask(W, H, os.path.join(d, "_mhard.png"))
    return paths


def epoch_for(datestr, timestr):
    dt = datetime.datetime.strptime(f"{datestr} {timestr}", "%Y-%m-%d %I:%M %p")
    return calendar.timegm(dt.timetuple())


def build_video(cfg, paths, audio, dur, W, H, out, fps=10, crf=None,
                on_progress=None):
    ep = epoch_for(cfg["date"], cfg["start"])
    cb = clock_box(W, H)
    clock_size, cy, rm = cb["size"], cb["y"], cb["right_margin"]
    fp = FONT.replace("\\", "/").replace(":", "\\:")
    D = f"{dur:.3f}"
    fc = (
        f"color=c=black:s={W}x{H}:r={fps}[b1];"
        f"[b1][3:v]overlay=x='{W}*t/{D}-{W}':y=0,format=gray[m1];"
        f"[1:v][m1]alphamerge[clayA];"
        f"[0:v][clayA]overlay=0:0[s1];"
        f"[s1]drawtext=fontfile='{fp}':fontsize={clock_size}:fontcolor={cfg['accent'][1:]}"
        f":x=(w-tw-{rm}):y={cy}:text='%{{pts\\:gmtime\\:{ep}\\:%I\\\\\\:%M %p}}'[v]"
    )
    cmd = ["ffmpeg", "-y", "-v", "error",
           "-loop", "1", "-framerate", str(fps), "-i", paths["bone"],
           "-loop", "1", "-framerate", str(fps), "-i", paths["clay"],
           "-i", audio,
           "-loop", "1", "-framerate", str(fps), "-i", paths["mhard"],
           "-filter_complex", fc, "-map", "[v]", "-map", "2:a",
           "-c:v", "libx264", "-preset", cfg.get("x264_preset", "slow"),
           "-crf", str(crf if crf is not None else cfg.get("crf", 16)),
           "-pix_fmt", "yuv444p" if cfg.get("full_chroma") else "yuv420p",
           "-g", str(fps * 10),
           "-c:a", "aac", "-b:a", "320k", "-t", D, "-shortest", out]
    cmd = cmd[:1] + ["-progress", "pipe:1", "-nostats"] + cmd[1:]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, bufsize=1)
    t0, noise, eta_s = time.time(), [], None
    for line in p.stdout:
        line = line.strip()
        if line.startswith("out_time="):
            try:
                hh, mm, ss = line.split("=", 1)[1].split(":")
                secs = int(hh) * 3600 + int(mm) * 60 + float(ss)
            except ValueError:
                continue
            frac = min(1.0, max(0.0, secs / dur)) if dur else 0.0
            el = time.time() - t0
            eta = None
            if frac > 0.05:                       # early estimates are noise
                raw = el / frac - el
                eta_s = raw if eta_s is None else eta_s * 0.7 + raw * 0.3
                eta = eta_s
            if on_progress:
                on_progress(frac, eta)
        elif line and not any(line.startswith(k) for k in (
                "frame=", "fps=", "bitrate=", "total_size=", "out_time_",
                "dup_frames=", "drop_frames=", "speed=", "progress=", "stream_")):
            noise.append(line)
    p.wait()
    if p.returncode != 0:
        raise SystemExit("ffmpeg failed:\n" + "\n".join(noise[-12:]))
    if on_progress:
        on_progress(1.0, 0)
    return out


# ----------------------------------------------------------------- driver
def _sidecar(cfg, lv, dur, scale, dyn, n, variants=None):
    """Everything needed to understand or reproduce a render, saved beside it."""
    import datetime
    if variants:
        # the "look" block below describes the primary colours; this says which
        # file got which palette, so a folder of variants stays self-describing
        variants = [{"name": v["name"], "background": v["background"],
                     "foreground": v["foreground"], "accent": v["accent"],
                     "file": os.path.basename(v.get("file", ""))}
                    for v in variants]
    return {
        "rendered_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "slate": {
            "series": cfg.get("series"),
            "place": cfg.get("place"),
            "city": cfg.get("city"),
            "conditions": cfg.get("conditions"),
            "date": cfg.get("date"),
            "start_time": cfg.get("start"),
            "number": cfg.get("number"),
            "number_style": cfg.get("number_style"),
        },
        "look": {
            "scene": cfg.get("scene", ""),
            "style": cfg.get("style", ""),
            "detail": cfg.get("detail", "Default"),
            "seed": f"{cfg['_scene']['seed']:016x}" if cfg.get("_scene") else "",
            "tree_ahead": cfg.get("tree_ahead") or scene_mod.TREE_AHEAD[0],
            "mountain_face": cfg.get("mountain_face") or scene_mod.MOUNTAIN_FACE[0],
            "towers": cfg.get("towers", "Default"),
            "tower_count": n,
            "geometry": cfg.get("geometry", "steps"),
            "rows": cfg.get("rows", 1),
            "filled": cfg.get("filled", False),
            # "colors" is the pre-1.3 name for the same value
            "color_preset": cfg.get("color_preset") or cfg.get("colors", ""),
            "background": cfg.get("background", INK),
            "foreground": cfg.get("foreground", BONE),
            "accent": cfg.get("accent"),
            "cycle": cfg.get("cycle", []),
            "cycle_minutes": cfg.get("cycle_minutes"),
            "slate_mono": cfg.get("slate_mono", False),
        },
        "shape": {
            "scale": scale,
            "dynamics": dyn,
            "height_stat": cfg.get("height_stat", "peak"),
            "align_loud": cfg.get("align_loud", True),
        },
        "encode": {
            "width": cfg.get("width"),
            "height": cfg.get("height"),
            "fps": cfg.get("fps", 10),
            "full_chroma": cfg.get("full_chroma", False),
        },
        "variants": variants or [],
        "source_audio": os.path.basename(cfg.get("audio", "")),
        "duration_s": dur,
        "levels": lv,
    }


def run(cfg, progress=lambda s: None, on_progress=None):
    global FONT
    FONT = find_font()
    if not shutil.which("ffmpeg"):
        raise SystemExit("ffmpeg not found on PATH.")
    # settle the sky before anything draws, so every caller - GUI, CLI, or a
    # direct run() - lands on the same colours. Copied, not mutated in place.
    cfg = dict(cfg)
    cfg["background"] = cfg.get("background") or INK
    cfg["foreground"] = (cfg.get("foreground")
                         or presets_mod.auto_foreground(cfg["background"]))
    # degrade an unmounted network path here rather than in the GUI, so the CLI
    # stays usable off the studio's network too instead of dying in makedirs
    base = usable(cfg["outdir"], "LSS Renders")
    os.makedirs(base, exist_ok=True)

    slug = output_base(cfg)

    # each render gets its own folder; never overwrite an earlier one
    outdir, n = os.path.join(base, slug), 2
    while os.path.exists(outdir):
        outdir = os.path.join(base, f"{slug}_{n}")
        n += 1
    os.makedirs(outdir)
    work = os.path.join(outdir, "_work")
    os.makedirs(work, exist_ok=True)
    progress(f"Output folder: {outdir}")

    def stage(lo, hi):
        return (lambda f, eta=None: on_progress(lo + (hi - lo) * f, eta)) \
            if on_progress else None

    progress("Reading audio…")
    db, dur = envelope(cfg["audio"],
                       on_progress=(lambda f: stage(0.0, 0.10)(f)) if on_progress else None)
    scale = cfg.get("scale", "Skyline (rank)")
    dyn = cfg.get("dynamics", "More")
    stat = cfg.get("height_stat", "peak")
    n = tower_count(cfg.get("towers", "Default"), dur)
    lv = to_levels(db, n, scale, dyn, align=cfg.get("align_loud", True), stat=stat)

    style = cfg.get("style") or scene_mod.DEFAULT_STYLE.get(
        cfg.get("scene", "town"), "blocks")
    cfg["style"] = style
    if style in scene_mod.SILHOUETTE:
        # built once, in design units, and reused by the thumbnail and both
        # video layers - so all three are one shape at three sizes
        cfg["_scene"] = scene_mod.build(style, db, lv,
                                        detail=cfg.get("detail", "Default"),
                                        scale=scale, dynamics=dyn)
        progress(f"Style {style}: {len(cfg['_scene']['mountains'])} summits, "
                 f"{len(cfg['_scene']['trees'])} trees, "
                 f"{len(cfg['_scene']['houses'])} houses "
                 f"(seed {cfg['_scene']['seed']:016x})")

    tw = int(cfg.get("thumb_width", 1920))
    th = round(tw * 9 / 16)
    frac = float(cfg.get("progress", 0.0) or 0.0)
    # A variant set is for comparing looks, so it only runs without an encode -
    # the callers reject the combination up front, this is the belt and braces.
    variants = list(cfg.get("variants") or []) if cfg.get("thumb_only") else []

    if variants:
        # everything expensive - the audio pass, the levels, the geometry - is
        # already done and shared, so each extra look costs one compose()
        progress(f"Building {len(variants)} thumbnails…")
        for i, v in enumerate(variants, 1):
            vcfg = dict(cfg, background=v["background"],
                        foreground=v["foreground"], accent=v["accent"])
            v["file"] = _thumbnail(vcfg, lv, tw, th,
                                   os.path.join(outdir, variant_filename(slug, v)),
                                   work, frac)
            progress(f"  {v['name']}: {os.path.basename(v['file'])}")
            if on_progress:
                on_progress(0.10 + 0.90 * i / len(variants), None)
        thumbs = [v["file"] for v in variants]
    else:
        progress("Building thumbnail…")
        thumbs = [_thumbnail(cfg, lv, tw, th,
                             os.path.join(outdir, f"{slug}_thumb.png"), work, frac)]

    if cfg.get("thumb_only"):
        json.dump(_sidecar(cfg, lv, dur, scale, dyn, n, variants),
                  open(os.path.join(outdir, f"{slug}_render.json"), "w"), indent=2)
        shutil.rmtree(work, ignore_errors=True)
        if on_progress:
            on_progress(1.0, 0)
        progress("Done (thumbnail only).")
        return {"thumbnail": thumbs[0], "thumbnails": thumbs, "video": None,
                "duration": dur, "folder": outdir}

    W, H = cfg.get("width", 2560), cfg.get("height", 1440)
    progress("Building frame layers…")
    if on_progress:
        on_progress(0.12, None)
    paths = video_layers(cfg, lv, W, H, work, dur)
    if on_progress:
        on_progress(0.18, None)

    progress(f"Encoding {dur/60:.1f} min of video…")
    vid = build_video(cfg, paths, cfg["audio"], dur, W, H,
                      os.path.join(outdir, f"{slug}.mp4"),
                      fps=cfg.get("fps", 10), on_progress=stage(0.18, 1.0))

    json.dump(_sidecar(cfg, lv, dur, scale, dyn, n),
              open(os.path.join(outdir, f"{slug}_render.json"), "w"), indent=2)
    shutil.rmtree(work, ignore_errors=True)
    progress("Done.")
    return {"thumbnail": thumbs[0], "thumbnails": thumbs, "video": vid,
            "duration": dur, "folder": outdir}


def main():
    # NUM_STYLES carries the numero sign, and argparse prints it in --number-style's
    # choices. A cp1252 console cannot encode it, which killed --help outright.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, OSError):
            pass
    a = argparse.ArgumentParser(description="Render a Location Sound Studios video.")
    a.add_argument("audio", nargs="?")

    g = a.add_argument_group("slate", "what the frame says")
    g.add_argument("--place")
    g.add_argument("--city")
    g.add_argument("--conditions")
    g.add_argument("--date", help="YYYY-MM-DD")
    g.add_argument("--start", help="e.g. 06:30 PM")
    g.add_argument("--number", default="")
    g.add_argument("--number-style", default="No.", choices=list(NUM_STYLES))

    g = a.add_argument_group("look", "colour and silhouette")
    g.add_argument("--series", "--preset", dest="series_key",
                   metavar="NAME", default="Sounds of the City",
                   help="which series to render. --preset is the old name")
    g.add_argument("--theme", default="None (use series colour)",
                   help="seasonal occasion: its accent and colour cycle sit on "
                        "top of whichever sky --colors chose")
    g.add_argument("--colors", dest="color_preset", metavar="PRESET",
                   default="None (series colour)",
                   help="colour preset: Morning, Night, Evening, Canopy. Sets "
                        "background and foreground; supplies the accent unless "
                        "a --theme already does")
    g.add_argument("--variants", metavar="A,B,C", default="",
                   help="extra colour presets to compare against --colors, "
                        "e.g. 'Aurora,Canopy,Evening'. One thumbnail each plus "
                        "one for --colors itself, all in one folder with the "
                        "palette in each filename. Needs --thumb-only")
    g.add_argument("--background", default="",
                   help="sky colour, e.g. #F2D289 - overrides --colors")
    g.add_argument("--foreground", default="",
                   help="silhouette and slate text colour, e.g. #2A2018. "
                        "Defaults to whichever of bone or ink reads on the "
                        "background")
    g.add_argument("--accent", default="",
                   help="the colour the playhead reveals")
    g.add_argument("--cycle-minutes", type=float, default=0.0,
                   help="override how often a theme's colour cycle steps")
    g.add_argument("--style", default="",
                   help="silhouette shape. nature: "
                        + ", ".join(scene_mod.SCENE_STYLES["nature"])
                        + "; town: " + ", ".join(scene_mod.SCENE_STYLES["town"])
                        + ". Defaults to the series' own (topo or blocks)")
    g.add_argument("--detail", default="Default",
                   help="how much shape the silhouette styles carry: "
                        + ", ".join(scene_mod.DETAIL) + ", or a number like "
                        "1.2. Counts features, not pixels, so a thumbnail and "
                        "the video read the same")
    g.add_argument("--tree-ahead", default=scene_mod.TREE_AHEAD[0],
                   choices=scene_mod.TREE_AHEAD,
                   help="how a tree looks before the playhead reaches it "
                        "(--style " + ", ".join(sorted(scene_mod.TREE_AHEAD_STYLES))
                        + " only)")
    g.add_argument("--mountain-face", default=scene_mod.MOUNTAIN_FACE[0],
                   choices=scene_mod.MOUNTAIN_FACE,
                   help="outline: ridgeline only; twotone: flat lit and shadow "
                        "faces, kept washed toward the sky so the trees stay "
                        "the thing that reads as progress (--style "
                        + ", ".join(sorted(scene_mod.MOUNTAIN_FACE_STYLES)) + " only)")
    g.add_argument("--filled", action="store_true",
                   help="solid silhouette instead of outlines")
    g.add_argument("--slate-mono", action="store_true",
                   help="draw the small slate text in the silhouette colour, "
                        "not the accent")

    g = a.add_argument_group("shape", "how loudness becomes height")
    g.add_argument("--scale", default="Skyline (rank)", choices=SCALES)
    g.add_argument("--dynamics", default="More", choices=list(DYNAMICS),
                   help="no effect on --scale 'Fixed loudness'")
    g.add_argument("--towers", default="Default",
                   choices=list(TOWERS) + ["Auto"],
                   help="tower width, for --style blocks: Thick, Default, "
                        "Thin, Fine, or Auto")
    g.add_argument("--rows", type=int, default=1, choices=[1, 2, 3, 4, 5],
                   help="stacked envelope rows. Not available for the "
                        "generative silhouette styles")
    g.add_argument("--height-stat", default="peak", choices=["peak", "rms"],
                   help="peak: height = loudest moment in the block (shows "
                        "brief events); rms: block average (old)")
    g.add_argument("--no-align", action="store_true",
                   help="don't snap tall features onto loud moments")

    g = a.add_argument_group("output", "where it lands and how it encodes")
    g.add_argument("--outdir", default=None,
                   help="where renders land. Falls back to LSS_OUTDIR, then an "
                        '"outdir" key in lss_presets.json, then ' + DEFAULT_OUT)
    g.add_argument("--outname", default="",
                   help="name the render folder and files. Defaults to --place")
    g.add_argument("--thumb-only", action="store_true",
                   help="render just the thumbnail - no video encode")
    g.add_argument("--progress", type=float, default=0.0,
                   help="render the thumbnail as a mid-playback frame, 0-1, "
                        "instead of the unplayed state")
    g.add_argument("--thumb-width", type=int, default=1920,
                   help="thumbnail width in pixels; height follows at 16:9")
    g.add_argument("--width", type=int, default=2560, help="video width")
    g.add_argument("--height", type=int, default=1440, help="video height")
    g.add_argument("--fps", type=int, default=10)
    g.add_argument("--full-chroma", action="store_true",
                   help="encode 4:4:4 instead of 4:2:0 - much kinder to coloured "
                        "text, but an unusual profile; check the upload processes")

    a.add_argument("--list-presets", action="store_true",
                   help="print the available series, occasions and colours")
    n = a.parse_args()
    P = presets_mod.load()
    if n.list_presets:
        for s in presets_mod.series_names(P):
            sc = presets_mod.series_scene(s, P)
            print(f"series: {s}  [{sc}]  styles: "
                  + ", ".join(scene_mod.SCENE_STYLES[sc]))
        print("themes:", ", ".join(presets_mod.theme_names(P)))
        print("colors:", ", ".join(presets_mod.color_preset_names(P)))
        print("detail:", ", ".join(scene_mod.DETAIL) + ", or a number")
        return
    missing = [k for k in ("audio","place","city","conditions","date","start")
               if not getattr(n, k)]
    if missing:
        a.error("missing required: " + ", ".join("--"+m if m!="audio" else "audio"
                                                 for m in missing))
    for flag in ("accent", "background", "foreground"):
        v = getattr(n, flag)
        if v and not presets_mod.valid_hex(v):
            a.error(f"--{flag}: '{v}' is not a colour like #CF7A34")
    if n.color_preset not in presets_mod.color_preset_names(P):
        a.error(f"--colors: unknown preset '{n.color_preset}'. Choose from: "
                + ", ".join(presets_mod.color_preset_names(P)))
    cfg = vars(n)
    custom = {"accent": n.accent,
              "background": n.background, "foreground": n.foreground}
    # base_acc is the series/occasion/custom accent before any colour preset
    # has had a say, so each variant below resolves from the same starting
    # point the single-preset path does
    name, base_acc = presets_mod.resolve(n.series_key, n.theme, P, custom)
    cfg["series"] = name
    bg, fg, acc = presets_mod.resolve_colors(
        n.color_preset, n.theme, P, base_acc, custom)
    cfg["background"], cfg["foreground"] = bg, fg
    cfg["accent"] = acc
    picks = [s.strip() for s in n.variants.split(",") if s.strip()]
    if picks:
        unknown = [s for s in picks if s not in presets_mod.color_preset_names(P)]
        if unknown:
            a.error("--variants: unknown preset(s) " + ", ".join(unknown)
                    + ". Choose from: "
                    + ", ".join(presets_mod.color_preset_names(P)))
        if not n.thumb_only:
            a.error("--variants needs --thumb-only: it is for comparing looks "
                    "in one pass, not for encoding several videos")
        # custom[], not n.background: cfg is vars(n), so the resolved sky has
        # already been written back over the flag by this point
        if custom["background"]:
            a.error("--variants and --background conflict: the custom sky "
                    "would override every preset and they would all render "
                    "the same")
        cfg["variants"] = presets_mod.variant_set(n.color_preset, picks,
                                                  n.theme, P, base_acc, custom)
    cfg["outdir"] = n.outdir or default_outdir(P)
    cfg["geometry"] = presets_mod.series_geometry(n.series_key, P)
    cfg["scene"] = presets_mod.series_scene(n.series_key, P)
    cfg["style"] = n.style or scene_mod.DEFAULT_STYLE.get(cfg["scene"], "blocks")
    err = scene_mod.check(cfg["scene"], cfg["style"], n.rows, n.filled,
                          n.progress)
    if err:
        a.error(err)
    try:
        scene_mod.resolve_detail(n.detail)
    except ValueError as e:
        a.error(str(e))
    cfg["align_loud"] = not n.no_align
    cfg["height_stat"] = n.height_stat
    cfg["towers"] = n.towers
    suf, cyc, cmin = presets_mod.theme_extras(n.theme, P)
    cfg["cycle"] = cyc
    cfg["cycle_minutes"] = n.cycle_minutes or cmin
    for k in ("place", "city", "conditions", "series"):
        cfg[k] = cfg[k].upper()
    r = run(cfg, progress=lambda s: print(s, flush=True))
    print(f"\nfolder    : {r['folder']}")
    for p in r["thumbnails"]:
        print(f"thumbnail : {p}")
    if r["video"]:
        print(f"video     : {r['video']}")


if __name__ == "__main__":
    main()
