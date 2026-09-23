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
try:
    import lss_photo as photo_mod
except ImportError:                  # see lss_update.missing(): an update that
    photo_mod = None                 # predates this module leaves it absent
                                     # for one launch, and the app must still
                                     # start so the updater can fetch it
import lss_scene as scene_mod
from PIL import Image, ImageDraw, ImageFont, ImageStat

INK, BONE = "#13232E", "#F0E7D6"     # the default sky, and what reads on it
FONT = None                      # resolved at runtime
# The frame layouts - slate positions and sizes, and the vertical frame. The
# shipped values until run() resolves them against lss_presets.json, once per
# render as FONT is, so every function below reads one settled record.
LAYOUTS = presets_mod.LAYOUTS

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
def compose(cfg, lv, W, H, line_col, out, time_text=None, played=False,
            lights_on=True, save=None):
    """The single composition used for both the thumbnail and the video frames.

    Laid out in 1280x720 design units and scaled by k, so the video is the
    thumbnail at a larger size. `time_text` is drawn only for the thumbnail;
    the video leaves that slot empty and ffmpeg draws a live clock there.

    `played` selects which side of the playhead this frame represents: the
    video builds one of each and lets the sliding mask cut between them.

    A frame taller than 16:9 - the square cover - keeps this same layout and
    moves the slate down with the frame's middle. `dy` is that move, and it is
    exactly 0.0 at 16:9, so every baseline below is the number it always was.

    The vertical Shorts frame is a different layout rather than a moved one:
    cfg carries its design width (`_dw`, 720 where everything else is 1280)
    and its already-stacked text column (`_column`), and the column is drawn
    where the slate would be. Everything under the text is this same function.
    """
    dw = cfg.get("_dw", 1280.0)
    k = W / dw
    dh = dw * H / W                  # 720.0 at 16:9, 1280.0 square and 9:16
    dy = dh / 2.0 - 360.0            # the "CITY . CONDITIONS" line rides the
                                     # frame's middle, as it has since 360/720
    acc = cfg["accent"]
    bg = cfg.get("background") or INK
    fg = cfg.get("foreground") or BONE
    slate = fg if cfg.get("slate_mono") else acc
    mode = cfg.get("geometry", "steps")
    nrows = int(cfg.get("rows", 1))
    filled = bool(cfg.get("filled", False))
    img, dr = D.new_canvas(W, H, bg)

    # the sky goes down before anything else, so every silhouette below
    # occludes it simply by being drawn - see lss_draw.draw_sky
    sky = cfg.get("_sky")
    if sky:
        D.draw_sky(dr, sky, W, H, cfg.get("star_color") or fg, bg)

    # ...then the clouds over them, and under everything else. A cloud is
    # opaque, so it occludes a star exactly as the silhouette occludes both -
    # and a sun or moon will land between these two calls, behind the cloud.
    wx = cfg.get("_weather")
    if wx:
        D.draw_clouds(dr, wx, W, H, bg, fg)

    bands = cfg.get("_bands") if line_col == "__cycle__" else None
    sc = cfg.get("_scene")
    if sc:
        D.draw_scene(dr, sc, W, H,
                     line_col if line_col != "__cycle__" else acc, bg,
                     played=played, face=cfg.get("mountain_face") or scene_mod.MOUNTAIN_FACE[0],
                     ahead=cfg.get("tree_ahead") or scene_mod.TREE_AHEAD[0],
                     filled=filled, bands=bands,
                     # a lit window is drawn in whichever of the pair this
                     # layer is not, so it still reads once the playhead has
                     # painted the body in the other one
                     lit=fg if played else acc, lights_on=lights_on)
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

    # Rain goes IN FRONT of the silhouette and BEHIND the slate: it is weather
    # between the viewer and the scene, and the text is the one thing standing
    # in front of the weather. Tinted rather than painted, so the played half
    # of the skyline stays the played half - see lss_draw.draw_rain.
    if wx:
        D.draw_rain(img, wx, W, H, bg, fg)

    if cfg.get("_column") is not None:
        # the number keeps its own override here too, and a badge is knocked
        # out of an accent pill in the sky colour
        D.draw_column(dr, cfg["_column"], W, k,
                      {"fg": fg, "slate": slate,
                       "number": cfg.get("number_color") or slate,
                       "badge": bg, "pill": acc}, FONT)
    else:
        D.draw_slate(dr, cfg, W, k, dy,
                     format_number(cfg.get("number", ""),
                                   cfg.get("number_style", "No.")),
                     time_text, fg, slate, FONT, LAYOUTS["landscape"])
    return D.finish(img, W, H, out, **(save or {}))


def _horizon(cfg, dh):
    """How far down the sky is clear, in design units - the cloud band's floor.

    A silhouette style is read off its own built geometry, so a style added
    later needs no entry anywhere. The row styles have no geometry to read, and
    the same number comes out of the layout they are actually drawn with -
    which is why this lives here rather than in lss_scene: row_layout is a
    drawing decision and compose() is where the two arguments to it are known.
    """
    sc = cfg.get("_scene")
    if sc:
        return scene_mod.horizon(sc)
    lay = D.row_layout(dh, int(cfg.get("rows", 1)), bool(cfg.get("filled")),
                       top=0.66, bot=0.95)
    return min(y0 - amp for y0, amp in lay)


def _weather(cfg, db, dh=720.0, dw=1280.0, lift=0.0):
    """This frame's weather, or None. Built per frame SHAPE, as the scene is:
    a square cover has a taller sky and gets more of it, not a stretched copy,
    and the vertical frame a narrower one with less of it across.
    """
    if (cfg.get("weather") or "off") == "off":
        return None
    return scene_mod.weather(db, state=cfg["weather"],
                             detail=cfg.get("detail", "Default"),
                             horizon_y=_horizon(cfg, dh), dh=dh, dw=dw,
                             lift=lift)


def _clouds_only(wx):
    """The same weather with the rain taken out.

    For the twinkle probe, and only for it. A cloud genuinely covers a star and
    has to be in that test; a streak does not - it is drawn in FRONT of the
    silhouette, one design unit wide, and under STAR_CLEAR = 0 a streak
    clipping the corner of a star rect would disqualify a twinkler sitting in
    open sky. The probe therefore never sees rain, whatever this render draws.
    """
    return dict(wx, rain=None) if wx else wx


def slate_boxes(cfg, dh=720.0, dy=None):
    """Every run of slate text as (x0, x1, ink_bottom), in design units.

    Measured with the real font at the real sizes rather than estimated from a
    character count, because that is the whole point: "PHOENIX" and "SOUTH
    MOUNTAIN PARK" leave wildly different amounts of the frame free, and so do
    a two-word conditions line and a six-word one.

    The slate is drawn in caps throughout, so a baseline IS the ink bottom -
    there is nothing below it to allow for. Positions mirror compose() exactly,
    at k=1; if the layout there moves, this has to move with it.

    `dy` is the slate's vertical offset and it is ACCEPTED rather than derived.
    It used to be computed here from `dh` alone, which was correct only while
    the slate had one possible position: once --slate-position could move the
    text, deriving it here would have left the ceiling and the legibility check
    measuring the top of the frame while the text sat somewhere else - and
    nothing in the rendered output would have shown it. Defaults to the
    top-position value, which is the expression this line always held.
    """
    S = LAYOUTS["landscape"]         # the same record draw_slate is handed
    M = float(S["margin"])
    if dy is None:
        dy = slate_dy(dh)            # the same slate move compose() makes, so
    out = []                         # the ceiling is measured off the real text
    n = format_number(cfg.get("number", ""), cfg.get("number_style", "No."))
    nw = (D.text_width(n, float(S["series_size"]), float(S["series_tracking"]),
                       FONT) if n else 0.0)
    # the same shrink-to-fit compose() applies, or a long series name would
    # reserve more width here than it actually occupies
    avail = 1280.0 - 2 * M - nw - (float(S["number_gap"]) if n else 0.0)
    ssize, strack = float(S["series_size"]), float(S["series_tracking"])
    for _ in range(24):
        if D.text_width(cfg["series"], ssize, strack, FONT) <= avail:
            break
        ssize *= 0.94
        strack *= 0.94
    sb = S["series_baseline"] + dy
    out.append((M, M + D.text_width(cfg["series"], ssize, strack, FONT), sb))
    if n:
        out.append((1280.0 - M - nw, 1280.0 - M, sb))
    out.append((M, M + D.text_width(cfg["place"], float(S["title_size"]),
                                    float(S["title_tracking"]), FONT),
                S["title_baseline"] + dy))
    cc = f'{cfg["city"]}  ·  {cfg["conditions"]}'
    tb = S["tagline_baseline"] + dy
    out.append((M, M + D.text_width(cc, float(S["tagline_size"]),
                                    float(S["tagline_tracking"]), FONT), tb))
    # The clock. Reserved whenever there is one, thumbnail or video: the
    # thumbnail draws it and the video has ffmpeg draw it in the same place, so
    # the column is spoken for either way. Measured off a full-width sample
    # rather than this render's start time, because the video's clock runs all
    # night. With no time given there is no clock anywhere, and the column is
    # handed back to the ceiling and the star field.
    if has_clock(cfg):
        tw = D.text_width("00:00 PM", float(S["clock_size"]), 0.0, FONT)
        out.append((1280.0 - M - tw, 1280.0 - M, tb))
    return out


def has_clock(cfg):
    """True if this render shows a time at all. A blank --start (or, for a clip,
    a blank --slate-time) leaves the clock off every output."""
    return bool((cfg.get("start") or cfg.get("slate_time") or "").strip())


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
    """True if the loaded font actually draws ch rather than a .notdef box.

    Asks the SLATE font only. The drawing borrows a missing glyph from a
    fallback font (lss_draw.FALLBACK_FONTS), but the numero-sign style keeps
    its 'NO.' swap: that is a choice about how the number reads, made before
    anything is drawn."""
    return D.has_glyph(FONT, ch)


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


def _thumbnail(cfg, lv, tw, th, out, work, frac=1.0):
    """One thumbnail. Between 0 and 1, `frac` cuts the played and unplayed
    layers at a fixed x instead of a moving one - a mid-playback frame without
    an encode. The ends need only one layer, so they skip the composite."""
    if frac >= 1.0:                      # fully played: the accent layer IS it
        return compose(cfg, lv, tw, th, cfg["accent"], out,
                       time_text=cfg.get("start"), played=True)
    if frac > 0:
        b = compose(cfg, lv, tw, th, cfg["foreground"],
                    os.path.join(work, "_t_bone.png"), time_text=cfg.get("start"))
        c = compose(cfg, lv, tw, th, cfg["accent"],
                    os.path.join(work, "_t_clay.png"), time_text=cfg.get("start"),
                    played=True)
        return D.progress_composite(b, c, frac, out)
    return compose(cfg, lv, tw, th, cfg["foreground"], out,
                   time_text=cfg.get("start"))


COVER_PX = 3000                  # Spotify's ceiling. Its floor is 1400, and
                                 # the file lands two orders of magnitude under
                                 # the 25 MB cap either way, so there is nothing
                                 # to trade and no size flag to offer
COVER_DH = 1280.0                # ...which makes the design frame 1280x1280 -
                                 # see lss_scene.vlayout for what moves


def _cover(cfg, db, lv, style, scale, dyn, out):
    """The square cover, built rather than cropped.

    The geometry is rebuilt at the square design height from the SAME envelope
    seed, so this is the recording's own skyline standing on a lower ground
    line with more sky over it - not the 16:9 frame with its sides cut off, and
    not the 16:9 frame stretched. Costs one extra build and one compose; the
    audio pass, the envelope and the levels are all already done.

    Always the finished, fully-played frame. A cover is a release's fixed
    identity, so it shows the state the video ends in, drawn in the flat accent
    - never a colour cycle's arbitrary last step.
    """
    boxes = slate_boxes(cfg, dh=COVER_DH)
    L = scene_mod.vlayout(COVER_DH)
    ccfg = dict(cfg)
    if style in scene_mod.SILHOUETTE:
        ceiling = (scene_mod.ceiling_profile(boxes, free_y=L.ceil_free)
                   if style == "city" and cfg.get("ceiling_profile", True)
                   else None)
        ccfg["_scene"] = scene_mod.build(style, db, lv,
                                         detail=cfg.get("detail", "Default"),
                                         scale=scale, dynamics=dyn,
                                         ceiling=ceiling, dh=COVER_DH)
    # ...and the weather over the square sky, built at the square design
    # height rather than reused: the cover has 1.8x the band and gets 1.8x the
    # rain, at the same streak size - see lss_scene.weather
    ccfg["_weather"] = _weather(ccfg, db, dh=COVER_DH)
    if cfg.get("stars"):
        ccfg["_sky"] = scene_mod.sky(db, detail=cfg.get("detail", "Default"),
                                     boxes=boxes,
                                     twinkle=not cfg.get("no_twinkle"),
                                     dh=COVER_DH)
    return compose(ccfg, lv, COVER_PX, COVER_PX, ccfg["accent"], out,
                   time_text=cfg.get("start"), played=True, save=D.png_meta())


# ----------------------------------------------------------------- vertical
# The 9:16 Shorts frame. A LAYOUT, not a crop: the geometry is rebuilt for a
# 720x1280 design frame off the same envelope seed, as the cover rebuilds it
# for 1280x1280, and the slate is restacked as a centred column rather than
# moved. Everything it is sized by lives in lss_presets.LAYOUTS["vertical"].

def _vnum(V, key):
    """One number from the vertical layout, or an error that names the key."""
    try:
        return float(V[key])
    except (KeyError, TypeError, ValueError):
        raise SystemExit(f"layouts.vertical.{key} in lss_presets.json must be "
                         f"a number, not {V.get(key)!r}.")


def vertical_frame():
    """(W, H, dw, dh, lift) of the vertical frame, from the layout record."""
    V = LAYOUTS["vertical"]
    W, H = int(_vnum(V, "width")), int(_vnum(V, "height"))
    dw = _vnum(V, "design_width")
    if W <= 0 or H <= 0 or dw <= 0:
        raise SystemExit("layouts.vertical: width, height and design_width "
                         "must all be above zero.")
    return W, H, dw, dw * H / W, _vnum(V, "ground_lift")


def _fit(text, size, track, width):
    """Shrink size and tracking together until `text` fits `width` - the move
    draw_slate makes for a long series name, and at the same 0.94 step."""
    for _ in range(40):
        if D.text_width(text, size, track, FONT) <= width:
            break
        size *= 0.94
        track *= 0.94
    return size, track


def _wrap(text, size, track, width, lines):
    """The title as one line if it fits, else split at the word break that
    leaves the LONGER half shortest - two balanced lines rather than a full
    first line and a stray last word."""
    words = text.split()
    if (lines < 2 or len(words) < 2
            or D.text_width(text, size, track, FONT) <= width):
        return [text]

    def worse(i):
        return max(D.text_width(" ".join(words[:i]), size, track, FONT),
                   D.text_width(" ".join(words[i:]), size, track, FONT))
    i = min(range(1, len(words)), key=worse)
    return [" ".join(words[:i]), " ".join(words[i:])]


def column_layout(cfg):
    """The vertical frame's text, stacked, as (lines, boxes).

    One function positions the column and both the drawing and the measuring
    read its result - the landscape slate needed slate_boxes() to mirror
    draw_slate() by hand, and a column that wraps and shrinks would make that
    mirror a second layout engine. `lines` goes to lss_draw.draw_column();
    `boxes` is (x0, x1, ink_bottom) per line, the shape ceiling_profile() and
    sky() already take.

    Stacked from `top` down: each line's baseline sits its own size below the
    gap under the line above, so a two-line title pushes everything under it
    down rather than overlapping it. Every line shrinks to fit the column's
    width; the title wraps first and shrinks only if a half still does not fit.

    Then the safe zones, and a line that enters one is an ERROR naming the
    preset key and the line - never a quiet nudge, since a nudged column is a
    layout nobody chose. A line's extent is nominal, in design units: its
    baseline less its size above, SLATE_DESCENT below, the pill's padding for
    the badge. The same rule slate_block() uses, so a machine on the Arial
    fallback checks the same numbers.
    """
    V = LAYOUTS["vertical"]
    W, H, dw, dh, lift = vertical_frame()
    colw = dw - 2 * _vnum(V, "margin")
    n = int(_vnum(V, "title_lines"))
    if n not in (1, 2):
        raise SystemExit("layouts.vertical.title_lines must be 1 or 2, not "
                         f"{V.get('title_lines')!r}.")
    lines = []

    def add(name, segs, size, track, base, top, bottom, pill=None):
        text = "".join(t for t, _ in segs)
        w = (sum(D.text_width(t, size, track, FONT) for t, _ in segs)
             + track * (len(segs) - 1))
        lines.append({"name": name, "text": text, "segments": segs,
                      "size": size, "tracking": track, "baseline": base,
                      "top": top, "bottom": bottom, "pill": pill,
                      "x0": (dw - w) / 2.0, "x1": (dw + w) / 2.0})

    y = _vnum(V, "top")
    badge = (cfg.get("badge") or "").strip().upper()
    if badge:
        px, py = _vnum(V, "badge_pad_x"), _vnum(V, "badge_pad_y")
        size, track = _fit(badge, _vnum(V, "badge_size"),
                           _vnum(V, "badge_tracking"), colw - 2 * px)
        base = y + py + size
        add("badge", [(badge, "badge")], size, track, base, y, base + py,
            pill=(px, py))
        y = base + py + _vnum(V, "badge_gap")

    size, track = _fit(cfg["series"], _vnum(V, "series_size"),
                       _vnum(V, "series_tracking"), colw)
    base = y + size
    add("series", [(cfg["series"], "fg")], size, track, base, base - size,
        base + SLATE_DESCENT)
    y = base + _vnum(V, "series_gap")

    size, track = _vnum(V, "title_size"), _vnum(V, "title_tracking")
    title = _wrap(cfg["place"], size, track, colw, n)
    size, track = _fit(max(title, key=lambda t: D.text_width(t, size, track,
                                                             FONT)),
                       size, track, colw)
    for i, t in enumerate(title):
        base = y + size if i == 0 else base + _vnum(V, "title_line_gap") + size
        add("title" if len(title) == 1 else f"title line {i + 1}",
            [(t, "fg")], size, track, base, base - size, base + SLATE_DESCENT)
    y = base + _vnum(V, "title_gap")

    tag = f'{cfg["city"]}  ·  {cfg["conditions"]}'
    size, track = _fit(tag, _vnum(V, "tagline_size"),
                       _vnum(V, "tagline_tracking"), colw)
    base = y + size
    add("tagline", [(tag, "slate")], size, track, base, base - size,
        base + SLATE_DESCENT)
    y = base + _vnum(V, "tagline_gap")

    # The footer: the number and the clock, the two runs the landscape slate
    # hangs off its right-hand edge. Either may be blank; both blank is no line
    num = format_number(cfg.get("number", ""), cfg.get("number_style", "No."))
    clock = (cfg.get("start") or "").strip()
    segs = ([(num, "number")] if num else []) + (
        [(("  ·  " if num else "") + clock, "slate")] if clock else [])
    if segs:
        size, track = _fit("".join(t for t, _ in segs), _vnum(V, "footer_size"),
                           _vnum(V, "footer_tracking"), colw)
        base = y + size
        add("footer", segs, size, track, base, base - size,
            base + SLATE_DESCENT)

    lo = dh * _vnum(V, "safe_top")
    hi = dh * (1.0 - _vnum(V, "safe_bottom"))
    for ln in lines:
        what = f"the {ln['name']} ('{ln['text']}')"
        if ln["top"] < lo:
            raise SystemExit(
                f"Vertical layout: {what} starts at y={ln['top']:.0f} of "
                f"{dh:.0f}, inside the top safe zone - "
                f"layouts.vertical.safe_top is {V['safe_top']:g}, which keeps "
                f"text below y={lo:.0f}. Move the column down with "
                "layouts.vertical.top.")
        if ln["bottom"] > hi:
            key = ln["name"].split()[0]
            raise SystemExit(
                f"Vertical layout: {what} ends at y={ln['bottom']:.0f} of "
                f"{dh:.0f}, inside the bottom safe zone - "
                f"layouts.vertical.safe_bottom is {V['safe_bottom']:g}, which "
                f"keeps text above y={hi:.0f}. Raise the column with "
                f"layouts.vertical.top, or shrink it with "
                f"layouts.vertical.{key}_size.")
    return lines, [(ln["x0"], ln["x1"], ln["bottom"]) for ln in lines]


def _vertical(cfg, db, n, style, scale, dyn, out, work, frac):
    """The vertical frame, built rather than cropped - _cover()'s bargain.

    Same envelope, same seed, rebuilt for a 720-unit-wide frame: the building
    count is scaled by the width so a block is the same width it is in
    landscape and there are fewer of them, and every feature is the size it
    is there because k is the same 1.5. Follows --progress like the thumbnail,
    since it is a thumbnail - a Short's, not a release's.

    Returns (path, levels) - the levels are the frame's own, and the sidecar
    records them.
    """
    W, H, dw, dh, lift = vertical_frame()
    lines, boxes = cfg["_vcolumn"]
    L = scene_mod.vlayout(dh, dw, lift)
    nv = max(2, int(round(n * dw / 1280.0)))
    lv = to_levels(db, nv, scale, dyn, align=cfg.get("align_loud", True),
                   stat=cfg.get("height_stat", "peak"))
    vcfg = dict(cfg, _dw=dw, _column=lines, _scene=None, _sky=None)
    if style in scene_mod.SILHOUETTE:
        ceiling = (scene_mod.ceiling_profile(boxes, free_y=L.ceil_free, dw=dw)
                   if style == "city" and cfg.get("ceiling_profile", True)
                   else None)
        vcfg["_scene"] = scene_mod.build(style, db, lv,
                                         detail=cfg.get("detail", "Default"),
                                         scale=scale, dynamics=dyn,
                                         ceiling=ceiling, dh=dh, dw=dw,
                                         lift=lift)
    vcfg["_weather"] = _weather(vcfg, db, dh=dh, dw=dw, lift=lift)
    if cfg.get("stars"):
        vcfg["_sky"] = scene_mod.sky(db, detail=cfg.get("detail", "Default"),
                                     boxes=boxes,
                                     twinkle=not cfg.get("no_twinkle"),
                                     dh=dh, dw=dw, lift=lift)
    return _thumbnail(vcfg, lv, W, H, out, work, frac), lv


PHOTO_INTERVAL = getattr(photo_mod, "DEFAULT_INTERVAL", 180.0)
SLATE_SCOPES = getattr(photo_mod, "SLATE_SCOPES", ["both", "thumbnail", "none"])
SCRIM_DEFAULT = 0.65             # how heavy the wash behind the slate is on a
                                 # photo. Set from the worst case rather than
                                 # by eye: a near-white hazy sky under the
                                 # Night palette measures 1.01:1 bare, 2.69 at
                                 # 0.45 and 3.52 at 0.55, and first clears the
                                 # 4.5:1 the small slate lines want at 0.65.
                                 # A dark photo is barely touched by it - the
                                 # wash is the palette's own sky - so the cost
                                 # of setting it for the hard case is small

# Where the slate block sits in the frame. The block itself never changes shape
# - these move it as a unit, through the design-unit `dy` draw_slate has taken
# since the cover - so every size, margin and tracking in it is untouched.
SLATE_POSITIONS = ["top", "middle", "bottom"]
SLATE_DESCENT = 8.0              # a descender allowance under the last
                                 # baseline. slate_boxes() says a baseline IS
                                 # the ink bottom because the slate is caps
                                 # throughout, which is very slightly
                                 # optimistic - the comma in "PHOENIX, AZ"
                                 # drops 3 units under it


def slate_block():
    """(top, bottom, margin) of the landscape slate block, in design units.

    The top is the series baseline less its own size - 150 less 27, 123.
    Stated in DESIGN units rather than measured off the font: the real ink top
    is 131 with Barlow Condensed Bold, but a machine on the Arial fallback must
    not get a different layout. The bottom is the tagline baseline plus
    SLATE_DESCENT - 368. The margin is the one draw_slate keeps left and right.
    Read off the layout record, so a tuned slate moves its block with it.
    """
    S = LAYOUTS["landscape"]
    return (float(S["series_baseline"] - S["series_size"]),
            S["tagline_baseline"] + SLATE_DESCENT, float(S["margin"]))


def slate_dy(dh=720.0, position="top"):
    """The slate block's vertical offset in design units, for a dh-tall frame.

    The ONE place the position mapping lives. Both the drawing (draw_slate) and
    the measuring (slate_boxes, and so the legibility check and the city
    ceiling) go through it, because the failure mode if they disagree is
    invisible: the text moves and the measurements quietly stay where they were.

    'top' is the layout every render has always had, and returns exactly the
    expression each caller used to compute inline - 0.0 at 16:9, and the same
    downward move at a square cover.
    """
    top, bot, margin = slate_block()
    if position == "bottom":
        return dh - margin - bot
    if position == "middle":
        return dh / 2.0 - (top + bot) / 2.0
    return dh / 2.0 - 360.0


def scrim_band(dh, dy, ink_bottom, position="top"):
    """The wash's band, (top, bottom) in design units, for a slate at `position`.

    The band butts against whichever frame edge the slate is nearest and fades
    on the other side; in the middle it fades on both. At 'top' that is
    0 -> the slate's lowest ink, which is the single downward ramp the wash has
    always been.
    """
    top = slate_block()[0]
    if position == "bottom":
        return top + dy, dh
    if position == "middle":
        return top + dy, ink_bottom
    return 0.0, ink_bottom


# ----------------------------------------------------------------- slate over video
VIDEO_MIN_W = 1280               # the slate's own design basis. Nothing is
                                 # upscaled below it - the slate is vector and
                                 # scales to any k - but every constant in
                                 # draw_slate is being scaled DOWN past this
                                 # point and the layout was never checked
                                 # there, so it is an error rather than a
                                 # quietly cramped frame
VIDEO_SAMPLES = 5                # frames the legibility check reads across the
                                 # clip. A still check reads one, and footage
                                 # changes tone: a slate that reads at second 1
                                 # can vanish at second 40
VIDEO_CRF = 20                   # measured on the real clip, not carried over
                                 # from photo_segment - see build_slate_video
VIDEO_PRESET = "medium"
VIDEO_SCRIM_DEFAULT = 0.0        # the wash is OFF for a clip, where photo mode
                                 # holds it at 0.65. A photograph is one fixed
                                 # frame and the wash is cheap insurance on it;
                                 # footage usually has a sky that already
                                 # carries the text, and the wash then reads as
                                 # a haze around it. Turned on per shot instead
VIDEO_SAMPLE_EVERY = 15.0        # seconds between legibility samples
VIDEO_SAMPLES_MIN = 5
VIDEO_SAMPLES_MAX = 24

# Looping one clip to fill an audio track. The slate shows for the first and
# last few minutes and nothing in between, which is what makes the body of the
# render one encode reused N times - see build_loop_video.
LOOP_FADE = 1.0                  # seconds the slate takes to arrive and leave
SLATE_INTRO_DEFAULT = 2.0        # minutes
SLATE_OUTRO_DEFAULT = 2.0


def slate_window(val, dur, flag):
    """--slate-intro/--slate-outro as SECONDS, from minutes or the word 'all'.

    'all' is how the always-on comparison is asked for on a looped render:
    there is no other way to say "the whole runtime" without knowing the
    audio's length at the command line. It is not the expensive case - the
    slate is static, so every full repeat is the same encode as every other.
    """
    s = str(val).strip().lower()
    if s in ("all", "always"):
        return float(dur)
    try:
        v = float(s)
    except ValueError:
        raise SystemExit(f"{flag}: '{val}' is not a number of minutes or 'all'.")
    if v < 0:
        raise SystemExit(f"{flag}: {v:g} is negative.")
    return v * 60.0


def loop_plan(dur, clip, intro=0.0, outro=0.0):
    """The looped timeline as pieces, and the distinct passes behind them.

    This is where the mode earns its keep. The body of the render is one clip
    repeated, so it is encoded ONCE and every bare repeat points at that same
    file; only the stretches that actually carry the slate need an encoder pass
    of their own, and a bare stretch that is part of a repeat is cut out of the
    body encode by STREAM COPY rather than re-encoded.

    Returns (pieces, encodes, cuts, keyframes):
      pieces    - the concat order, each naming the file it plays
      encodes   - {key: piece} that need their own encoder pass
      cuts      - {key: piece} that are stream copies out of the body
      keyframes - source offsets the body encode must put a keyframe on, which
                  is what lets those copies be copies

    The awkward case, and it is not hypothetical: the outro window can STRADDLE
    a loop seam, when the final truncated repeat is shorter than the outro. The
    slate then covers the tail of one repeat and the head of the next, which is
    two source ranges rather than one - a fourth encoder pass, and one repeat
    that is no longer interchangeable with its neighbours. Handled here rather
    than refused, because the alternative is telling somebody their audio is
    the wrong length.
    """
    if clip <= 0:
        raise SystemExit("--video: the clip has no duration to loop.")
    if dur <= 0:
        raise SystemExit("--video: the audio has no duration to cover.")
    if intro + outro > dur + 1e-6:
        raise SystemExit(
            f"--slate-intro and --slate-outro total {(intro + outro) / 60:.2f} "
            f"min, longer than the {dur / 60:.2f} min of audio. They would "
            "have to overlap, and silently merging them would hide that the "
            "schedule asked for something impossible.")

    wins = []
    if intro > 0:
        wins.append([0.0, min(intro, dur)])
    if outro > 0:
        wins.append([max(0.0, dur - outro), dur])
    wins.sort()
    merged = []
    for w in wins:                       # intro + outro == dur exactly is one
        if merged and w[0] <= merged[-1][1] + 1e-6:
            merged[-1][1] = max(merged[-1][1], w[1])
        else:
            merged.append(list(w))
    wins = merged

    pieces, encodes, cuts, keyframes = [], {}, {}, set()
    t = 0.0
    while t < dur - 1e-6:
        span = min(clip, dur - t)        # the last repeat is cut to the audio
        marks = {0.0, span}
        for a, b in wins:
            for x in (a, b):             # window edges falling inside this pass
                if t + 1e-6 < x < t + span - 1e-6:
                    marks.add(x - t)
        ms = sorted(marks)
        for i in range(len(ms) - 1):
            s0, s1 = ms[i], ms[i + 1]
            o0, o1 = t + s0, t + s1
            if any(a - 1e-6 <= o0 and o1 <= b + 1e-6 for a, b in wins):
                # fade only where the window really begins or ends. A slate
                # split across a loop seam must NOT fade at the seam - it is one
                # continuous appearance that happens to span two files
                fi = any(abs(o0 - a) < 1e-6 for a, _ in wins)
                fo = any(abs(o1 - b) < 1e-6 for _, b in wins)
                key = f"slate_{s0:08.3f}_{s1:08.3f}_{int(fi)}{int(fo)}"
                encodes.setdefault(key, {"src0": s0, "src1": s1,
                                         "fade_in": fi, "fade_out": fo})
            elif s0 <= 1e-6 and s1 >= clip - 1e-6:
                key = "body"             # a whole bare repeat: the body itself
            else:
                key = f"cut_{s0:08.3f}_{s1:08.3f}"
                cuts.setdefault(key, {"src0": s0, "src1": s1})
                if s0 > 1e-6:
                    keyframes.add(round(s0, 3))
                if s1 < clip - 1e-6:
                    keyframes.add(round(s1, 3))
            pieces.append({"key": key, "src0": s0, "src1": s1,
                           "slate": key.startswith("slate")})
        t += span
    return pieces, encodes, cuts, sorted(keyframes)


def scrim_default(cfg):
    """The wash's default strength, which differs by MODE rather than by flag.

    --scrim parses to None so that argparse carries no mode-conditional default
    of its own, and run() settles the number here, once, for every caller -
    GUI, CLI or a direct run(). Photo mode's 0.65 is shipped and must not move:
    changing it would alter every photo render ever made from the same inputs.
    """
    return VIDEO_SCRIM_DEFAULT if cfg.get("video") else SCRIM_DEFAULT


def video_samples(dur):
    """How many frames the legibility check reads, for a clip `dur` long.

    One every VIDEO_SAMPLE_EVERY seconds rather than a fixed count: five
    samples is one every 42s on a three-minute clip, and the tone under the
    slate moves far faster than that - headlights, a car crossing frame, a pan
    off a wall. Clamped at both ends so a ten-second clip is not sampled once
    and an hour-long one does not spend a minute seeking.

    It narrows the odds and does not close them, which is why the line this
    feeds says "worst of N samples" rather than "worst contrast".
    """
    n = int(round(max(1.0, dur) / VIDEO_SAMPLE_EVERY))
    return max(VIDEO_SAMPLES_MIN, min(VIDEO_SAMPLES_MAX, n))


def _slate_names(cfg):
    """The slate's lines, in the order slate_boxes() returns them."""
    n = format_number(cfg.get("number", ""), cfg.get("number_style", "No."))
    return (["series"] + (["number"] if n else [])
            + ["place", "city · conditions"]
            + (["time"] if has_clock(cfg) else []))


def _slate_legibility(img, cfg, W, H, dy=None):
    """The weakest contrast between slate ink and the photo under it.

    Reported rather than enforced. The colour presets were chosen against
    measured contrast on rendered frames and a photograph answers to nothing,
    so this is the only place the number can be known at all - and the fix is
    usually --scrim rather than a different palette, which is a judgement the
    render cannot make on its own.

    Measured on the FINISHED frame, so the scrim is included: what is wanted is
    the contrast the viewer gets, not the one the bare photo had.

    The sampled bands come from slate_boxes, so they FOLLOW the slate wherever
    --slate-position puts it rather than reading a fixed region of the frame.
    """
    k = W / 1280.0
    boxes = slate_boxes(cfg, dh=1280.0 * H / W, dy=dy)
    fg = cfg.get("foreground") or BONE
    worst, where = 99.0, ""
    for (x0, x1, b), name in zip(boxes, _slate_names(cfg)):
        # a band just above the baseline, which for caps IS where the ink is
        box = (max(0, int(x0 * k)), max(0, int((b - 34) * k)),
               min(W, int(x1 * k)), min(H, int(b * k)))
        if box[2] <= box[0] or box[3] <= box[1]:
            continue
        m = ImageStat.Stat(img.crop(box).convert("RGB")).mean
        hx = "#%02X%02X%02X" % tuple(max(0, min(255, int(round(v)))) for v in m)
        c = presets_mod.contrast(fg, hx)
        if c < worst:
            worst, where = c, name
    return worst, where


def _photo_frame(cfg, path, W, H, slate_on, time_text=None, out=None,
                 save=None, measure=False):
    """One photo as a finished frame, with the slate on it if this output wants one.

    The whole of photo mode's drawing is these few lines, because the slate is
    the same slate: lss_draw.draw_slate does not know or care that there is a
    photograph under it rather than a skyline.
    """
    img = photo_mod.frame(path, W, H)
    under = None
    if slate_on:
        k = W / 1280.0
        dh = 1280.0 * H / W
        fg = cfg.get("foreground") or BONE
        slate = fg if cfg.get("slate_mono") else cfg["accent"]
        boxes = slate_boxes(cfg, dh=dh)
        # the wash first, on its own, so `under` is the tone the ink will
        # actually sit on rather than the finished frame with the ink in it
        img = D.scrim_over(img, W, H, k, float(cfg.get("scrim", SCRIM_DEFAULT)),
                           cfg.get("background") or INK,
                           max(b[2] for b in boxes))
        under = img if measure else None
        img = D.slate_over(
            img, cfg, W, H, k, dh / 2.0 - 360.0,
            format_number(cfg.get("number", ""), cfg.get("number_style", "No.")),
            time_text, fg, slate, FONT, LAYOUTS["landscape"])
    if out:
        img.save(out, **(save or {}))
        img = out
    return (img, under) if measure else img


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
    S = LAYOUTS["landscape"]
    k = W / 1280.0
    size = int(round(S["clock_size"] * k))
    baseline = (S["tagline_baseline"] + (1280.0 * H / W) / 2.0 - 360.0) * k
    f = ImageFont.truetype(FONT, size)
    ink_top = baseline + f.getbbox(sample, anchor="ls")[1]
    return {"size": size,
            "y": int(round(ink_top - _drawtext_ink_top(size, sample))),
            "right_margin": int(round(S["margin"] * k))}


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
    """bone and clay full frames plus the sliding mask.

    Beacons are baked DARK when they are going to blink, and drawn back on by
    blink_layers() - so the lit state only ever gets added. Erasing a baked-in
    light would have to repaint it in the body colour, which differs either
    side of the playhead and under a colour cycle varies along the frame too.

    With blinking off they are baked LIT instead, and then the sliding mask
    flips them from accent to foreground for free, exactly as it does a window.
    """
    paths = {}
    bands = make_bands(cfg, W, dur)
    cfg = dict(cfg, _bands=bands)
    fg = cfg.get("foreground") or BONE
    steady = bool(cfg.get("no_blink"))
    # The stars take the OPPOSITE bargain to the beacons: both layers bake the
    # full field, exactly as the thumbnail draws it, and star_layers() fades
    # individual stars back toward the sky. A beacon cannot be erased - the
    # colour under it varies - where a star stands on bare sky and can.
    drift = bool(cfg.get("_sky")) and not cfg.get("no_twinkle")
    for name, col in (("bone", fg), ("clay", "__cycle__" if bands else cfg["accent"])):
        paths[name] = compose(cfg, lv, W, H, col, os.path.join(d, f"_{name}.png"),
                              played=(name == "clay"), lights_on=steady)
    paths["mhard"] = D.solid_mask(W, H, os.path.join(d, "_mhard.png"))
    if drift:
        # The occlusion reference: this same frame with no sky in it at all. A
        # twinkler only earns its filters where THIS is still bare background.
        # One extra compose against an encode measured in minutes, and it is
        # the only test that cannot be fooled - see _visible_stars.
        paths["probe"] = compose(dict(cfg, _sky=None,
                                      _weather=_clouds_only(cfg.get("_weather"))),
                                 lv, W, H, fg,
                                 os.path.join(d, "_probe.png"))
    return paths


def blink_layers(cfg, W, H, dur):
    """drawbox filters that light the antenna beacons, as a filter string.

    Two per light, not one. A beacon follows the same rule a lit window does -
    it is drawn in whichever colour the current state is NOT - so it has to be
    the accent ahead of the playhead and the foreground behind it, or it
    disappears into the body the moment the playhead arrives. The mask slides
    at x = W*t/dur, so the beacon at x crosses over at t = dur*x/W.

    This is the same ffmpeg timeline mechanism the clock already uses; the
    measured cost of a full skyline of them is inside the noise of the encode.
    """
    sc = cfg.get("_scene") or {}
    lights = sc.get("lights") or []
    if not lights or not dur or cfg.get("no_blink"):
        return ""
    k = W / 1280.0
    fg = cfg.get("foreground") or BONE
    acc = cfg["accent"]
    out = []
    for L in lights:
        x0, y0, x1, y1 = L["rect"]
        x, y = int(round(x0 * k)), int(round(y0 * k))
        w, h = max(1, int(round((x1 - x0) * k))), max(1, int(round((y1 - y0) * k)))
        cross = dur * (x + w / 2.0) / W
        on = (f"lt(mod(t+{L['phase']:.3f}\\,{L['period']:.3f})\\,"
              f"{scene_mod.BLINK_ON:.2f})")
        for col, side in ((acc, f"lt(t\\,{cross:.3f})"),
                          (fg, f"gte(t\\,{cross:.3f})")):
            out.append(f"drawbox=x={x}:y={y}:w={w}:h={h}:color=0x{col[1:]}"
                       f":t=fill:enable='{on}*{side}'")
    return "," + ",".join(out)


STAR_CLEAR = 0                   # how far, summed over three channels, a probe
                                 # pixel may sit from bare sky and still count
                                 # as clear - i.e. not at all. Flat sky
                                 # downsamples to exactly the sky colour, so an
                                 # open rect measures 0 and anything touching a
                                 # roofline or a glyph's antialiasing does not.
                                 # Slack was tried and is not worth having: it
                                 # buys back one twinkler in thirty and turns a
                                 # rule that can be stated exactly - a
                                 # twinkling star stands on bare sky - into one
                                 # with a number in it that has to be defended
STAR_PAD = 1                     # ...and this much margin around it, because a
                                 # star does not end at its own rectangle. The
                                 # supersampled block is downsampled with
                                 # LANCZOS, whose negative lobes leave a ring
                                 # about 5% of the star's amplitude just
                                 # outside it - measured at 8 levels a channel
                                 # against a 164-level star. Erasing only the
                                 # rectangle would leave that ring behind as a
                                 # faint ghost square at exactly the moment the
                                 # star is meant to be gone, so the erase
                                 # covers the margin too and the probe has to
                                 # clear it as well


def _visible_stars(stars, path, k, bg):
    """The twinklers the silhouette actually leaves showing.

    A drawbox does not know what is under it. An occluded twinkler would flash
    a bright square on top of a tower, a treeline or a letter of the slate -
    the one thing a BACKGROUND layer must never do - because the filter paints
    the frame long after the geometry that covered the star was drawn.

    `path` is this same frame composed with NO sky in it, so a rect is clear
    exactly when it is still bare background there. Two things make that the
    right reference. Reproducing the test in closed form would be a second
    implementation of draw_scene - a star can be behind a tower, inside a
    glyph, or cut out by the near layer's own sky gap - and it could only drift
    from the first. And probing the frame that HAS the stars in it cannot
    answer it at all: a tower's body tone can land within a few units of a
    faint star's, and then a covered star and a clear one look identical.

    Every pixel of the rect must be clear, not its average. A star a roofline
    clips would otherwise pass on the mean and flash half a square on the edge,
    which is the same fault as flashing a whole one.
    """
    try:
        img = np.array(Image.open(path).convert("RGB")).astype(np.int32)
    except Exception:
        # unreadable: draw no filters at all. A still sky is a small loss; a
        # square blinking on a building is the defect this exists to prevent
        return []
    want = np.asarray(D.rgb(bg), dtype=np.int32)
    h, w = img.shape[:2]
    out = []
    for s in stars:
        x, y, n = D.star_rect(s, k)
        if (x - STAR_PAD < 0 or y - STAR_PAD < 0
                or x + n + STAR_PAD > w or y + n + STAR_PAD > h):
            continue                     # a star off the edge of the frame
        p = STAR_PAD
        patch = np.abs(img[y - p:y + n + p, x - p:x + n + p] - want).sum(axis=2)
        if int(patch.max()) <= STAR_CLEAR:
            out.append(s)
    return out


def star_layers(cfg, W, H, dur, base=None):
    """drawbox filters that fade a twinkling star, as a filter string.

    ONE pair per star, where a beacon needs a pair per state: a star is the
    same colour either side of the playhead, so a single filter covers the
    whole frame instead of one for the played half and one for the unplayed.
    That halving is what pays for a star field costing what seven beacons do.

    The filters take a star DOWN from the tone baked into both layers, as far
    as the sky itself - which is only possible because a star stands on bare
    sky and the sky is one constant colour everywhere. See STAR_FADE.

    The two windows are NESTED - the floor sits inside the dip and comes second
    in the chain, so it wins where both are on. One period therefore reads
    tone -> part -> gone -> part -> tone off two filters, which is a fade
    rather than the on/off a single drawbox gives.

    Measured at 2560x1440: about 1us per filter per frame, linear to ~240. At
    the STAR_TWINKLE_MAX cap that is ~11s on the ~20min a three-hour render
    already takes.
    """
    sky = cfg.get("_sky") or {}
    stars = [s for s in sky.get("stars", []) if s.get("twinkle")]
    if not stars or not dur or cfg.get("no_twinkle"):
        return ""
    k = W / 1280.0
    col = cfg.get("star_color") or cfg.get("foreground") or BONE
    bg = cfg.get("background") or INK
    if base:
        stars = _visible_stars(stars, base, k, bg)
        sky["twinkling_drawn"] = len(stars)          # what the sidecar reports
    fade = float(cfg.get("star_fade", scene_mod.STAR_FADE))
    out = []
    for s in stars:
        x, y, n = D.star_rect(s, k)
        p, ph = s["period"], s["phase"]
        dip = p * scene_mod.STAR_DIP
        half = dip * scene_mod.STAR_DIP_FLOOR / 2.0
        m = f"mod(t+{ph:.3f}\\,{p:.3f})"
        for f, on in ((fade * scene_mod.STAR_FADE_MID, f"lt({m}\\,{dip:.3f})"),
                      (fade, f"between({m}\\,{dip / 2.0 - half:.3f}"
                              f"\\,{dip / 2.0 + half:.3f})")):
            c = D.star_fade(col, bg, s["tone"], f)
            # Only a paint that IS the sky may spread past the star's own
            # rectangle, and it has to: that is the one level where the LANCZOS
            # ring outside it would otherwise survive as a ghost. A partial
            # fade stays on the rectangle, or a star would appear to swell as
            # it dims - at these sizes a one-pixel margin is most of its width.
            p = STAR_PAD if tuple(c) == D.rgb(bg) else 0
            out.append(f"drawbox=x={x - p}:y={y - p}:w={n + 2 * p}:h={n + 2 * p}"
                       f":color=0x{c[0]:02X}{c[1]:02X}{c[2]:02X}"
                       f":t=fill:enable='{on}'")
    return "," + ",".join(out)


def epoch_for(datestr, timestr):
    dt = datetime.datetime.strptime(f"{datestr} {timestr}", "%Y-%m-%d %I:%M %p")
    return calendar.timegm(dt.timetuple())


def build_video(cfg, paths, audio, dur, W, H, out, fps=10, crf=None,
                on_progress=None):
    D = f"{dur:.3f}"
    if has_clock(cfg):
        ep = epoch_for(cfg["date"], cfg["start"])
        cb = clock_box(W, H)
        clock_size, cy, rm = cb["size"], cb["y"], cb["right_margin"]
        fp = FONT.replace("\\", "/").replace(":", "\\:")
        clock = (f"drawtext=fontfile='{fp}':fontsize={clock_size}"
                 f":fontcolor={cfg['accent'][1:]}:x=(w-tw-{rm}):y={cy}"
                 f":text='%{{pts\\:gmtime\\:{ep}\\:%I\\\\\\:%M %p}}'")
    else:
        clock = "null"               # the chain below still needs a head
    fc = (
        f"color=c=black:s={W}x{H}:r={fps}[b1];"
        f"[b1][3:v]overlay=x='{W}*t/{D}-{W}':y=0,format=gray[m1];"
        f"[1:v][m1]alphamerge[clayA];"
        f"[0:v][clayA]overlay=0:0[s1];"
        f"[s1]{clock}"
        + blink_layers(cfg, W, H, dur)
        + star_layers(cfg, W, H, dur, base=paths.get("probe")) + "[v]"
    )
    # The graph goes in a FILE, not on the command line. Windows caps a command
    # line at 32767 characters and ffmpeg fails outright past it - WinError 206,
    # at launch, with nothing rendered - and a drawbox costs about 91 of those
    # characters. Seven beacons never came close; a sky's worth of twinklers
    # runs to ten thousand and would leave a real ceiling a few hundred stars
    # away. Reading the graph from a file has no limit and is byte-identical in
    # what it encodes, verified by SHA against the inline form.
    fpath = os.path.join(os.path.dirname(paths["bone"]), "_filters.txt")
    with open(fpath, "w", encoding="utf-8") as fh:
        fh.write(fc)
    cmd = ["ffmpeg", "-y", "-v", "error",
           "-loop", "1", "-framerate", str(fps), "-i", paths["bone"],
           "-loop", "1", "-framerate", str(fps), "-i", paths["clay"],
           "-i", audio,
           "-loop", "1", "-framerate", str(fps), "-i", paths["mhard"],
           "-filter_complex_script", fpath, "-map", "[v]", "-map", "2:a",
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


def _ffmpeg_progress(cmd, dur, on_progress=None):
    """Run ffmpeg with -progress and report elapsed/total, as build_video does.

    Its own copy rather than a shared one: build_video's loop is load-bearing
    for every existing render and the identity harness hashes what it produces,
    so it is left exactly as it is.
    """
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
            if frac > 0.05:
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
    return noise


def photo_segment(png, out, dur, fps, cfg, crf=None):
    """Encode one held still for `dur` seconds.

    ONE keyframe, covering the whole segment. This is the single setting that
    matters here and it is not the one the shipping encode tunes: -g fps*10 was
    chosen against flat vector frames where an I-frame costs almost nothing, and
    on a photograph each one costs about 3 MB. Measured on a 180s segment at
    2560x1440, everything else held equal - -g 100: 27.3s and 57.6 MB; one
    keyframe: 14.5s and 3.1 MB. Eighteen times the bytes for a keyframe every
    ten seconds of an image that never changes.

    CRF stays at the shipping 16. With every P-frame a skip the segment's size
    is essentially its one I-frame, so the quality setting costs almost nothing
    here and there is no reason to spend it. -tune stillimage was measured too
    and earns nothing on a held frame - it tunes for still IMAGES, not for a
    repeated one - so it is deliberately not used.
    """
    n = max(1, int(round(dur * fps)))
    cmd = ["ffmpeg", "-y", "-v", "error",
           "-loop", "1", "-framerate", str(fps), "-i", png,
           "-t", f"{dur:.3f}", "-an",
           "-c:v", "libx264", "-preset", cfg.get("x264_preset", "slow"),
           "-crf", str(crf if crf is not None else cfg.get("crf", 16)),
           "-pix_fmt", "yuv444p" if cfg.get("full_chroma") else "yuv420p",
           "-g", str(n), "-video_track_timescale", "90000", out]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("ffmpeg failed encoding a photo segment:\n"
                         + r.stderr[-800:])
    return out


def build_photo_video(cfg, segs, unique, photos, audio, dur, W, H, out, work,
                      fps=10, crf=None, progress=lambda s: None,
                      on_progress=None):
    """The photo cycle as a video: encode each distinct segment once, then
    assemble the whole runtime by stream copy.

    This is the reason photo mode is cheap. A segment holds one still for the
    whole interval, so every full-length segment of a given photo is the same
    encode as every other one; only the final segment differs, because it is
    truncated to the audio's end. Four photos over three hours are therefore
    four encodes and, when the run time is not a whole number of intervals, one
    short fifth - not the sixty segments the timeline actually contains, and
    not the 108,000 frames a per-frame render would put through the encoder.

    The concat demuxer copies the video stream rather than re-encoding it, so
    the repeats cost nothing but the mux. What is left is the audio: a
    three-hour AAC pass is around five minutes and is the dominant cost of the
    whole render, which is the right thing for it to be.

    Every segment shares one resolution, pixel format and timebase and starts
    on a keyframe by construction, which is what lets the copy be a copy.
    """
    # straight into _work rather than a subdirectory of it, as the skyline
    # layers already go: nothing else is written here, and one less nested
    # directory is one less thing for the cleanup to have to remove
    seg_dir = work
    slate_on = cfg.get("slate_scope", "both") == "both"

    files = {}
    for i, (key, u) in enumerate(unique.items(), 1):
        png = os.path.join(seg_dir, f"_{key}.png")
        _photo_frame(cfg, photos[u["photo"]], W, H, slate_on, out=png)
        files[key] = photo_segment(png, os.path.join(seg_dir, f"{key}.mp4"),
                                   u["dur"], fps, cfg, crf)
        progress(f"  segment {i}/{len(unique)}: "
                 f"{os.path.basename(photos[u['photo']])}, {u['dur']:.0f}s, "
                 f"{os.path.getsize(files[key]) / 1e6:.2f} MB")
        if on_progress:
            on_progress(0.18 + 0.22 * i / len(unique), None)

    # basenames, and the list sits beside them: the concat demuxer resolves a
    # relative entry against the LIST's own directory, which sidesteps having
    # to escape a Windows path inside a demuxer argument
    lst = os.path.join(seg_dir, "_concat.txt")
    with open(lst, "w", encoding="utf-8") as fh:
        for sg in segs:
            fh.write(f"file '{os.path.basename(files[sg['key']])}'\n")

    progress(f"Assembling {len(segs)} segments and encoding audio…")
    _ffmpeg_progress(
        ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
         "-i", lst, "-i", audio, "-map", "0:v", "-map", "1:a",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "320k",
         "-t", f"{dur:.3f}", "-shortest", out],
        dur, on_progress=(lambda f, e=None: on_progress(0.40 + 0.60 * f, e))
        if on_progress else None)
    return out


# ------------------------------------------------- the slate over a source clip
def slate_clock(s):
    """--slate-time as the house 'hh:mm AM/PM'.

    Typed as 24-hour HH:MM, drawn the way every other LSS frame draws a time.
    A clip render is one still slate among a catalogue of them, and the one
    frame in 24-hour time would be the one that looks wrong. The 12-hour form
    is accepted too, so whichever way it is typed lands in the same place.
    Blank stays blank: the slate goes out with no clock on it.
    """
    s = " ".join((s or "").upper().split())
    if not s:
        return ""
    for fmt in ("%H:%M", "%I:%M %p", "%I:%M%p"):
        try:
            return datetime.datetime.strptime(s, fmt).strftime("%I:%M %p")
        except ValueError:
            pass
    raise ValueError(f"'{s}' is not a time like 18:30 or 06:30 PM")


def video_info(path):
    """What the source clip is, as ffmpeg will actually present it.

    The rotation matters and it is not cosmetic: a phone clip shot upright is
    stored landscape with a 90 degree display matrix, ffmpeg applies that
    matrix before any filter runs, and so the frame reaching overlay is the
    TRANSPOSED size while ffprobe's width and height are the stored one. An
    overlay built to the stored size would not line up with the frame at all.
    Every size below is therefore the displayed size.
    """
    if not shutil.which("ffprobe"):
        raise SystemExit("ffprobe not found on PATH. It ships with ffmpeg; "
                         "run Setup.bat to install both.")
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-of", "json",
         "-show_entries",
         "stream=width,height,r_frame_rate,pix_fmt,codec_name,"
         "color_primaries,color_transfer,color_space:"
         "stream_side_data=rotation:format=duration", path],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"--video: cannot read {path}\n  "
                         + (r.stderr or "").strip()[-400:])
    try:
        d = json.loads(r.stdout)
        st = (d.get("streams") or [None])[0]
        if st is None:
            raise ValueError("no video stream")
    except Exception as e:
        raise SystemExit(f"--video: {path} has no readable video stream ({e}).")

    rot = 0
    for sd in st.get("side_data_list") or []:
        if "rotation" in sd:
            rot = int(round(float(sd["rotation"])))
    w, h = int(st["width"]), int(st["height"])
    if abs(rot) % 180 == 90:
        w, h = h, w

    num, _, den = (st.get("r_frame_rate") or "0/1").partition("/")
    try:
        fps = float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        fps = 0.0
    try:
        dur = float((d.get("format") or {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        dur = 0.0
    return {"width": w, "height": h, "rotation": rot, "fps": fps,
            "duration": dur, "codec": st.get("codec_name") or "",
            "pix_fmt": st.get("pix_fmt") or "",
            # kept so the output is TAGGED as whatever the source was rather
            # than as whatever x264 assumes; an untagged bt709 clip plays back
            # as bt601 in some players and the whole frame shifts
            "primaries": st.get("color_primaries") or "bt709",
            "trc": st.get("color_transfer") or "bt709",
            "space": st.get("color_space") or "bt709"}


def _video_check(cfg):
    """Reject a clip render asking for something this mode cannot mean.

    An error rather than a quiet no-op, for the reason lss_scene.check() and
    lss_photo.check() both give: a flag that was typed and ignored is only ever
    discovered by noticing it did nothing.

    Almost the whole flag surface lands here, and that is the honest shape of
    the mode. Burning a static slate onto a supplied clip derives nothing from
    audio, builds no geometry, has no playhead and writes no still, so the only
    flags that survive are the ones that decide what the slate SAYS and what
    colour it is.
    """
    dead = [
        ("style", "--style", "", "a source clip is not a generated silhouette"),
        ("scale", "--scale", "Skyline (rank)",
         "nothing here is derived from loudness"),
        ("dynamics", "--dynamics", "More",
         "nothing here is derived from loudness"),
        ("towers", "--towers", "Default", "there are no towers to size"),
        ("detail", "--detail", "Default",
         "there is no generated detail to count"),
        ("weather", "--weather", "off", "the clip carries its own weather"),
        ("height_stat", "--height-stat", "peak",
         "nothing here is derived from loudness"),
        ("tree_ahead", "--tree-ahead", scene_mod.TREE_AHEAD[0],
         "there is no silhouette to draw a tree in"),
        ("mountain_face", "--mountain-face", scene_mod.MOUNTAIN_FACE[0],
         "there is no silhouette to draw a ridge in"),
        ("photo_interval", "--photo-interval", PHOTO_INTERVAL,
         "there is one clip and it is not a cycle"),
        ("slate_scope", "--slate-scope", "both",
         "the slate IS this mode; there is no still for it to skip"),
        ("cycle_minutes", "--cycle-minutes", 0.0,
         "a colour cycle steps along a playhead and there is none here"),
    ]
    for key, flag, dflt, why in dead:
        if cfg.get(key, dflt) != dflt:
            return f"{flag} has no meaning with --video: {why}."
    for key, flag, why in (
            ("rows", "--rows", "a clip is one frame, not stacked envelope rows"),
            ("filled", "--filled", "there is no silhouette to fill"),
            ("no_align", "--no-align", "nothing here is aligned to loudness"),
            ("no_blink", "--no-blink", "there are no beacons to blink"),
            ("no_twinkle", "--no-twinkle", "there is no star field to hold"),
            ("cover", "--cover", "this mode writes one video and no stills"),
            ("variants", "--variants",
             "the palette only reaches the slate, so every variant would "
             "differ in the text colour alone"),
            ("stars_explicit", "--stars",
             "the star field is drawn behind a silhouette, and the clip is "
             "the whole background"),
    ):
        v = cfg.get(key)
        if v and not (key == "rows" and v == 1):
            return f"{flag} has no meaning with --video: {why}."
    # --thumb-only writes a thumbnail and stops, which a LOOPED render can do
    # because it makes one. A bare clip render writes no still at all, so there
    # would be nothing left of it.
    if cfg.get("thumb_only") and not cfg.get("looping"):
        return ("--thumb-only has no meaning with --video on its own: this "
                "mode writes no thumbnail, so there would be nothing left. "
                "Pass an audio file to make it a looped render, which does.")
    p = cfg.get("progress", 1.0)
    if p is not None and float(p) != 1.0:
        return ("--progress has no meaning with --video: there is no playhead "
                "to draw part-way along.")
    return None


def _slate_overlay(cfg, W, H, time_text, out):
    """The scrim and the slate as ONE straight-alpha RGBA PNG at the clip's size.

    What _photo_frame() composites straight onto a still, handed back as a
    layer instead so ffmpeg can put it over every frame of a clip. Both halves
    are drawn by lss_draw exactly as the photo path draws them - draw_scrim and
    draw_slate, no second implementation - and only the ASSEMBLY lives here,
    which is the same split _photo_frame already sits on.

    'over' is associative, so slate-over-scrim-over-frame is the composite
    scrim_over() and slate_over() make in two passes onto a photo.

    The two halves combine PREMULTIPLIED, and the result is un-premultiplied
    once at the end because ffmpeg's overlay filter wants straight alpha. The
    slate's own half keeps slate_over's rule intact - drawn at SS, the coverage
    and the colour downsampled SEPARATELY - since that is what stops a LANCZOS
    negative lobe ringing into a bright fringe at a glyph edge.

    Measured against the Pillow path on three frames of the real 4K clip, in
    levels of mean absolute error: this layer through ffmpeg's overlay 0.005,
    peak 2, nothing above 2 anywhere. The ffmpeg blend pair (multiply then
    addition) that would avoid the un-premultiply measures 0.372 - worse,
    because blend truncates where ImageChops rounds - so the straight overlay
    is the one to use. Blending in yuv420 instead measures 1.43 with peaks of
    81 on the accent-coloured runs, which is why the filter graph forces rgb.
    """
    k = W / 1280.0
    dh = 1280.0 * H / W
    pos = cfg.get("slate_position", "top")
    dy = slate_dy(dh, pos)
    fg = cfg.get("foreground") or BONE
    slate = fg if cfg.get("slate_mono") else cfg["accent"]
    boxes = slate_boxes(cfg, dh=dh, dy=dy)
    band_top, band_bot = scrim_band(dh, dy, max(b[2] for b in boxes), pos)

    layer = Image.new("RGBA", (W * D.SS, H * D.SS), (0, 0, 0, 0))
    D.draw_slate(ImageDraw.Draw(layer), cfg, W, k, dy,
                 format_number(cfg.get("number", ""),
                               cfg.get("number_style", "No.")),
                 time_text, fg, slate, FONT, LAYOUTS["landscape"])
    pm_im, am_im = D.premultiplied(layer, W, H)

    # float32, not 64: every value here is a small integer and a 24-bit mantissa
    # carries them exactly, where the wider type doubles 200 MB of working set
    # per plane on a 4K frame for no precision that survives the rint below.
    #
    # And float rather than another ImageChops chain, which is what the scrim
    # combine below was first written as. Each 8-bit step in it rounds, and the
    # rounding is the whole error budget: measured against the Pillow path on
    # three frames of the real clip, the ImageChops combine gives 0.42 mean
    # absolute error and this gives 0.005, for four lines either way.
    pm = np.asarray(pm_im, dtype=np.float32)
    a = np.asarray(am_im, dtype=np.float32) / 255.0

    strength = float(cfg.get("scrim", scrim_default(cfg)))
    if strength > 0 and band_bot > 0:
        sc = np.asarray(
            D.draw_scrim(W, H, strength, cfg.get("background") or INK,
                         band_bot * k, D.SCRIM_FADE * k, band_top * k),
            dtype=np.float32)
        # the scrim goes UNDERNEATH the slate, both premultiplied. 'over' is
        # associative, so this is the composite _photo_frame() makes in two
        # passes onto a still
        ca = sc[..., 3] / 255.0
        inv = 1.0 - a
        pm += sc[..., :3] * (ca * inv)[..., None]
        a += ca * inv

    # where nothing covers, the colour is arbitrary and is multiplied by an
    # alpha of zero on the way out; guard the divide rather than special-case it
    col = pm / np.maximum(a, 1.0 / 255.0)[..., None]
    rgba = np.concatenate(
        [np.clip(np.rint(col), 0, 255),
         np.clip(np.rint(a * 255.0), 0, 255)[..., None]], axis=-1).astype(np.uint8)
    Image.fromarray(rgba, "RGBA").save(out)
    return out


def _video_legibility(cfg, src, W, H, dur, work, n=None, windows=None):
    """The weakest contrast the slate reaches anywhere in the clip, and when.

    _slate_legibility() reads one frame, which is all a still has. Footage does
    not hold still: a pan off a dark wall onto a bright sky, headlights
    crossing the lower third, a three-minute shot that starts at dusk and ends
    at night. A slate measured only at second 1 can be gone by second 40, and
    that is precisely the case --scrim exists to fix, so the number reported is
    the WORST across the clip rather than the first.

    Measured on the scrimmed frame before the ink goes down, for the reason
    scrim_over() gives: once the glyphs are there they are most of what a
    sample of their own band contains. With the scrim off - which is a clip's
    default - that is simply the bare footage, which is the point: the wash is
    no longer standing between the text and whatever the shot is doing.

    Returns (worst, line, when, n). The COUNT comes back with the number
    because it qualifies it: this is the worst of n samples, not the worst in
    the clip, and the two are only the same if the tone under the slate holds
    still between them. It does not, which is why the count is printed.

    `windows` restricts the sampling to (start, end) ranges of the SOURCE that
    actually show the slate. A looped render carries it for four minutes out of
    two and a half hours, and sampling the other two hours and twenty-six
    minutes would measure the contrast of text that is not on screen - slowly.
    The timestamps reported are source offsets, which is where the fix is
    applied anyway.
    """
    wins = windows or [(0.0, dur)]
    span = sum(b - a for a, b in wins) or dur
    n = n or video_samples(span)
    k = W / 1280.0
    dh = 1280.0 * H / W
    pos = cfg.get("slate_position", "top")
    dy = slate_dy(dh, pos)
    boxes = slate_boxes(cfg, dh=dh, dy=dy)
    band_top, band_bot = scrim_band(dh, dy, max(b[2] for b in boxes), pos)
    strength = float(cfg.get("scrim", scrim_default(cfg)))
    worst, where, when = 99.0, "", 0.0
    for i in range(n):
        # walk the sampling point through the windows in order, so the samples
        # are spread evenly over the seconds that SHOW the slate rather than
        # over the runtime
        off = span * (i + 0.5) / n
        t = wins[-1][1]
        for a, b in wins:
            if off <= b - a:
                t = a + off
                break
            off -= b - a
        png = os.path.join(work, f"_probe{i}.png")
        r = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", src,
             "-frames:v", "1", "-vf", "format=rgb24", png],
            capture_output=True, text=True)
        if r.returncode != 0 or not os.path.exists(png):
            continue
        img = D.scrim_over(Image.open(png).convert("RGB"), W, H, k, strength,
                           cfg.get("background") or INK, band_bot, band_top)
        c, w = _slate_legibility(img, cfg, W, H, dy=dy)
        if c < worst:
            worst, where, when = c, w, t
        os.remove(png)
    return worst, where, when, n


def build_slate_video(cfg, src, ovl, out, dur, info, on_progress=None):
    """One overlay pass: the static slate burned onto the clip, audio stripped.

    The encode settings are MEASURED on real footage rather than carried over
    from photo_segment(), whose one-keyframe-per-segment and "CRF costs nothing
    here" were both found on a HELD frame - where every P-frame is a skip and
    the segment IS its keyframe. Moving photographic content inverts that, so
    no -g is set at all and x264's own keyint with scene-cut detection does the
    job it is for.

    Measured on a 20s excerpt of a 3840x2160 30fps 22.5 Mb/s HEVC phone clip -
    56.3 MB of source - with PSNR and SSIM taken against the overlaid source
    regenerated live by this same filter chain, so what is measured is encode
    loss and not the overlay:

        CRF 14   89.72 MB   35.9 Mb/s   PSNR 48.64   SSIM 0.9944
        CRF 16   63.20 MB   25.3 Mb/s   PSNR 48.26   SSIM 0.9939
        CRF 18   43.73 MB   17.5 Mb/s   PSNR 47.80   SSIM 0.9933
        CRF 20   29.65 MB   11.9 Mb/s   PSNR 47.22   SSIM 0.9926
        CRF 22   19.71 MB    7.9 Mb/s   PSNR 46.55   SSIM 0.9917

    Eight CRF points buy 2.1 dB and 0.0027 SSIM, and cost 4.5x the bytes. That
    narrow spread is the finding: the source has ALREADY been through HEVC at
    22.5 Mb/s, so its high-frequency detail is gone before x264 ever sees it,
    and spending bitrate here buys precision about someone else's compression
    artefacts. CRF 16 - the shipping default for the generated renders - writes
    63.2 MB over a 56.3 MB source, more than the footage it is copying. CRF 20
    writes 29.7 MB, 53% of the source, still 47.2 dB and SSIM 0.993. That is
    the default. CRF 22 is a defensible 35% if size ever matters more.

    Preset at CRF 20, five interleaved rounds off a pre-extracted excerpt, BEST
    of each rather than the median: the wall clock on this machine swung 20s to
    144s on one config, noise far past the 5% a median absorbs, and with
    additive noise the minimum is the closest thing to the real cost. Sizes
    need none of that - they repeated to the byte every round.

        fast    19.5s   29.79 MB   +0.5%
        medium  21.3s   29.65 MB    ---
        slow    29.6s   28.75 MB   -3.0%

    Slow costs 39% more time to save 3.0% of the bytes; fast saves 8% of the
    time for half a percent more of them. Neither trade is worth taking on a
    mode whose whole point is a quick look at the slate over real footage.
    Medium.

    -fps_mode passthrough because the clip is very slightly variable (30000/1001
    nominal, 3733800/124561 average) and the requirement is to preserve what the
    source has rather than resample it onto a grid of our choosing.
    """
    fmt = "yuv444p" if cfg.get("full_chroma") else "yuv420p"
    # rgb for the blend itself, whatever the delivery format: compositing in
    # yuv420 measures peaks of 81 levels on the accent-coloured slate runs,
    # because the chroma plane is half resolution exactly where the glyph edges
    # are. Subsample ONCE, at the end, as the encoder was always going to.
    chain = f"[0:v]format=rgb24[b];[b][1:v]overlay=0:0:format=rgb,format={fmt}[v]"
    cmd = ["ffmpeg", "-y", "-v", "error",
           "-i", src, "-i", ovl,
           "-filter_complex", chain, "-map", "[v]",
           "-an",                     # camera audio; the real audio is muxed later
           "-c:v", "libx264",
           "-preset", cfg.get("x264_preset") or VIDEO_PRESET,
           "-crf", str(cfg.get("crf") if cfg.get("crf") is not None else VIDEO_CRF),
           "-pix_fmt", fmt, "-fps_mode", "passthrough",
           "-color_primaries", info["primaries"], "-color_trc", info["trc"],
           "-colorspace", info["space"],
           "-movflags", "+faststart", out]
    _ffmpeg_progress(cmd, dur, on_progress=on_progress)
    return out


def _video_sidecar(cfg, info, worst, where, when, samples, out):
    """The trimmed sidecar: no geometry, because there is none.

    _sidecar()'s look and shape blocks describe a silhouette derived from an
    envelope, and this mode has neither. The slate block keeps that one's shape
    exactly, so the half that IS the same reads the same.
    """
    return {
        "rendered_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "mode": "slate over video",
        "slate": {
            "series": cfg.get("series"),
            "place": cfg.get("place"),
            "city": cfg.get("city"),
            "conditions": cfg.get("conditions"),
            "time": cfg.get("slate_time"),
            "number": cfg.get("number"),
            "number_style": cfg.get("number_style"),
        },
        "look": {
            "color_preset": cfg.get("color_preset", ""),
            "background": cfg.get("background", INK),
            "foreground": cfg.get("foreground", BONE),
            "accent": cfg.get("accent"),
            "slate_mono": cfg.get("slate_mono", False),
            "number_color": cfg.get("number_color") or "",
            "slate_position": cfg.get("slate_position", "top"),
            "scrim": float(cfg.get("scrim", scrim_default(cfg))),
            # named "sampled_" on purpose: it is the worst of N frames, not the
            # worst in the clip, and the JSON should not read as a guarantee
            # either
            "sampled_min_contrast": round(worst, 2),
            "sampled_min_contrast_line": where,
            "sampled_min_contrast_at_s": round(when, 1),
            "contrast_samples": samples,
        },
        "video": {
            "source": os.path.basename(cfg.get("video", "")),
            "source_size": f"{info['width']}x{info['height']}",
            "source_codec": info["codec"],
            "source_pix_fmt": info["pix_fmt"],
            "source_rotation": info["rotation"],
            "fps": round(info["fps"], 6),
            "duration_s": round(info["duration"], 3),
        },
        "encode": {
            "crf": cfg.get("crf") if cfg.get("crf") is not None else VIDEO_CRF,
            "preset": cfg.get("x264_preset") or VIDEO_PRESET,
            "pix_fmt": "yuv444p" if cfg.get("full_chroma") else "yuv420p",
            "overlay_format": "rgb",
            "fps_mode": "passthrough",
            "colour_tags": [info["primaries"], info["trc"], info["space"]],
            "audio": "stripped",
            "file": os.path.basename(out),
            "size_mb": round(os.path.getsize(out) / 1e6, 2),
        },
    }


def audio_validate(path):
    """Refuse an audio file with no audio in it, before anything is built.

    probe_duration() answers with a number for anything that has a duration -
    a silent video among them - so every mode here would sail past the
    pre-flight and fail much later. A looped render fails at the FINAL MUX,
    where -map 1:a matches no stream, which is after every encode has run: on a
    two and a half hour render that is twenty-five minutes of work thrown away
    for a mistyped path. The generated and photo modes fare no better, decoding
    an empty PCM stream into an envelope with nothing in it.

    Checked here for the same reason lss_photo.validate() checks its stills
    here: the error is worth nothing once the folder exists and the encoder has
    started.
    """
    if not os.path.exists(path):
        raise SystemExit(f"audio: file not found: {path}")
    if not shutil.which("ffprobe"):
        raise SystemExit("ffprobe not found on PATH. It ships with ffmpeg; "
                         "run Setup.bat to install both.")
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
         "stream=codec_name,channels,sample_rate", "-of", "json", path],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"audio: cannot read {os.path.basename(path)}\n  "
                         + (r.stderr or "").strip()[-400:])
    try:
        streams = json.loads(r.stdout).get("streams") or []
    except Exception:
        streams = []
    if not streams:
        # name what IS in there, because the usual cause is the right path to
        # the wrong kind of file - a clip passed where the recording goes
        v = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
             "-of", "csv=p=0", path], capture_output=True, text=True)
        kinds = sorted({k.strip() for k in (v.stdout or "").split() if k.strip()})
        raise SystemExit(
            f"audio: {os.path.basename(path)} has no audio stream.\n"
            f"  It contains: {', '.join(kinds) if kinds else 'no streams at all'}.\n"
            "  This is the recording the render is built from - a FLAC or WAV. "
            "A video clip goes in --video.")
    s = streams[0]
    return {"codec": s.get("codec_name") or "",
            "channels": s.get("channels") or 0,
            "sample_rate": s.get("sample_rate") or ""}


def video_validate(src):
    """Probe the clip and refuse it now if it cannot carry the slate.

    Called before the render folder is created, for the reason photo mode
    checks its stills up front: every other early failure leaves an empty
    folder behind and that has never mattered, but a mistyped path or a clip
    off the wrong camera is the one somebody will actually hit.
    """
    if not os.path.exists(src):
        raise SystemExit(f"--video: file not found: {src}")
    info = video_info(src)
    W, H = info["width"], info["height"]
    if info["duration"] <= 0:
        raise SystemExit(f"--video: could not read a duration from {src}.")
    if H > W:
        raise SystemExit(
            f"--video: {os.path.basename(src)} is {W}x{H}, taller than it is "
            "wide. The slate is laid out across the top of a landscape frame, "
            "and on a vertical one it would centre in the middle of the shot - "
            "which looks deliberate and is not. Shoot or crop to landscape.")
    if W < VIDEO_MIN_W:
        raise SystemExit(
            f"--video: {os.path.basename(src)} is {W}x{H}, narrower than the "
            f"slate's {VIDEO_MIN_W}px design basis. Nothing would be upscaled - "
            "the slate is vector - but every size and margin in it would be "
            "scaled below what the layout was ever checked at. Use a larger "
            "source.")
    return info


def _loop_sidecar(cfg, info, plan, dur, worst, where, when, samples, thumb, out):
    """The looped render's sidecar - the clip-mode one plus the schedule.

    The plan is recorded in full because it is the claim this mode makes: N
    encodes and a pile of stream copies for a runtime many times longer. What
    actually went through the encoder is the thing worth being able to check
    afterwards, exactly as photo mode records its segments.
    """
    pieces, encodes, cuts, keyframes = plan
    d = _video_sidecar(cfg, info, worst, where, when, samples, out)
    d["mode"] = "slate over video, looped"
    d["video"]["audio"] = os.path.basename(cfg.get("audio", ""))
    d["video"]["audio_duration_s"] = round(dur, 3)
    d["video"]["clip_duration_s"] = round(info["duration"], 3)
    d["video"]["repeats"] = round(dur / info["duration"], 3)
    d["thumbnail"] = {"file": os.path.basename(thumb) if thumb else None,
                      "from_clip_s": float(cfg.get("thumb_at", 0.0))}
    enc = [{"key": "body", "src_s": [0.0, round(info["duration"], 3)],
            "dur_s": round(info["duration"], 3), "slate": False}]
    enc += [{"key": k, "src_s": [round(v["src0"], 3), round(v["src1"], 3)],
             "dur_s": round(v["src1"] - v["src0"], 3), "slate": True,
             "fade_in": v["fade_in"], "fade_out": v["fade_out"]}
            for k, v in encodes.items()]
    d["loop"] = {
        "slate_intro_s": round(float(cfg.get("slate_intro_s", 0.0)), 3),
        "slate_outro_s": round(float(cfg.get("slate_outro_s", 0.0)), 3),
        "fade_s": LOOP_FADE,
        "concat_entries": len(pieces),
        "encoded_segments": enc,
        "encoded_seconds": round(sum(e["dur_s"] for e in enc), 1),
        "stream_copied_segments": [
            {"key": k, "src_s": [round(v["src0"], 3), round(v["src1"], 3)],
             "dur_s": round(v["src1"] - v["src0"], 3)} for k, v in cuts.items()],
        "forced_keyframes_s": keyframes,
    }
    return d


def _run_loop(cfg, slug, outdir, work, progress, on_progress):
    """One clip looped to fill an audio track, slated at each end."""
    src = cfg["video"]
    info = cfg.get("_video_info") or video_validate(src)
    W, H = info["width"], info["height"]
    clip = info["duration"]

    dur = probe_duration(cfg["audio"])
    if dur <= 0:
        raise SystemExit(f"Could not read a duration from {cfg['audio']}.")
    intro = slate_window(cfg.get("slate_intro", SLATE_INTRO_DEFAULT), dur,
                         "--slate-intro")
    outro = slate_window(cfg.get("slate_outro", SLATE_OUTRO_DEFAULT), dur,
                         "--slate-outro")
    if intro >= dur:                     # 'all', or a window covering it all
        intro, outro = dur, 0.0
    cfg["slate_intro_s"], cfg["slate_outro_s"] = intro, outro

    plan = loop_plan(dur, clip, intro, outro)
    pieces, encodes, cuts, keyframes = plan
    enc_s = clip * (1 if (cuts or any(p["key"] == "body" for p in pieces))
                    else 0) + sum(v["src1"] - v["src0"] for v in encodes.values())

    progress(f"Source: {os.path.basename(src)}  {W}x{H} @ "
             f"{info['fps']:.3f} fps, {clip / 60:.2f} min, {info['codec']}")
    progress(f"Audio:  {os.path.basename(cfg['audio'])}  {dur / 60:.1f} min "
             f"= {dur / clip:.2f} repeats of the clip")
    progress(f"Slate:  {intro / 60:g} min in, {outro / 60:g} min out, "
             f"{LOOP_FADE:g}s fades, position {cfg.get('slate_position','top')}"
             f", scrim {float(cfg['scrim']):g}")
    progress(f"Plan:   {len(pieces)} concat entries from "
             f"{len(encodes) + 1} encodes and {len(cuts)} stream copies "
             f"- {enc_s / 60:.1f} min of footage encoded for a "
             f"{dur / 60:.1f} min output ({dur / enc_s:.1f}x less)")
    if on_progress:
        on_progress(0.03, None)

    progress("Building the slate overlay…")
    ovl = _slate_overlay(cfg, W, H, cfg["slate_time"],
                         os.path.join(work, "_slate.png"))

    # only the stretches that actually show the slate, in SOURCE time
    wins = sorted({(round(v["src0"], 3), round(v["src1"], 3))
                   for v in encodes.values()})
    if wins:
        span = sum(b - a for a, b in wins)
        ns = video_samples(span)
        progress(f"Checking slate contrast, {ns} samples across the "
                 f"{span / 60:.1f} min that show it…")
        worst, where, when, ns = _video_legibility(
            cfg, src, W, H, dur, work, ns, windows=wins)
        if not where:
            progress("  could not read frames to measure; skipping the check")
        else:
            progress(f"  slate contrast: worst of {ns} samples {worst:.1f}:1 "
                     f"({where}, at {when:.0f}s into the clip)")
            if worst < 4.5:
                progress(f"  WARNING: below 4.5:1. Raise --scrim (currently "
                         f"{float(cfg['scrim']):g}), or move "
                         "--slate-position off this part of the frame.")
    else:
        worst, where, when, ns = 99.0, "", 0.0, 0
        progress("No slate scheduled; skipping the contrast check.")
    if on_progress:
        on_progress(0.08, None)

    tw = int(cfg.get("thumb_width", 1920))
    th = round(tw * H / float(W))
    progress(f"Building thumbnail from {float(cfg.get('thumb_at', 0.0)):g}s "
             f"into the clip…")
    thumb, under = loop_thumbnail(cfg, src, float(cfg.get("thumb_at", 0.0)),
                                  tw, th, work,
                                  os.path.join(outdir, f"{slug}_thumb.png"))
    c, wname = _slate_legibility(under, cfg, tw, th,
                                 dy=slate_dy(1280.0 * th / tw,
                                             cfg.get("slate_position", "top")))
    progress(f"  thumbnail slate contrast {c:.1f}:1 ({wname})")
    if on_progress:
        on_progress(0.10, None)

    def done(vid):
        json.dump(_loop_sidecar(cfg, info, plan, dur, worst, where, when, ns,
                                thumb, vid or ""),
                  open(os.path.join(outdir, f"{slug}_render.json"), "w"),
                  indent=2)
        shutil.rmtree(work, ignore_errors=True)
        if on_progress:
            on_progress(1.0, 0)
        return {"thumbnail": thumb, "thumbnails": [thumb], "video": vid,
                "cover": None, "duration": dur, "folder": outdir}

    if cfg.get("thumb_only"):
        progress("Done (thumbnail only).")
        return done(None)

    progress(f"Encoding {enc_s / 60:.1f} min of footage…")
    t0 = time.time()
    vid = build_loop_video(cfg, src, ovl, cfg["audio"], dur,
                           os.path.join(outdir, f"{slug}.mp4"), work, info,
                           plan, progress=progress, on_progress=on_progress)
    el = time.time() - t0
    progress(f"  {os.path.basename(vid)}  "
             f"{os.path.getsize(vid) / 1e9:.2f} GB in {el / 60:.1f} min "
             f"({dur / el:.1f}x realtime for the finished runtime, "
             f"{enc_s / el:.2f}x for the footage actually encoded)")
    progress("Done.")
    return done(vid)


def _run_video(cfg, slug, outdir, work, progress, on_progress):
    """The slate burned onto one source clip, end to end.

    Neither audio pass exists here: there is no envelope to derive and no
    soundtrack to mux, so the clip is probed for its shape and otherwise only
    ever decoded once, by the encode itself.
    """
    src = cfg["video"]
    info = cfg.get("_video_info") or video_validate(src)
    W, H, dur = info["width"], info["height"], info["duration"]

    progress(f"Source: {os.path.basename(src)}  {W}x{H} @ "
             f"{info['fps']:.3f} fps, {dur / 60:.1f} min, {info['codec']}"
             + (f", rotated {info['rotation']}" if info["rotation"] else ""))
    if on_progress:
        on_progress(0.04, None)

    progress("Building the slate overlay…")
    ovl = _slate_overlay(cfg, W, H, cfg["slate_time"],
                         os.path.join(work, "_slate.png"))
    if on_progress:
        on_progress(0.08, None)

    ns = video_samples(dur)
    progress(f"Checking slate contrast, {ns} samples "
             f"(~{dur / ns:.0f}s apart)…")
    worst, where, when, ns = _video_legibility(cfg, src, W, H, dur, work, ns)
    if not where:
        # every sample failed to decode. Not fatal - the encode reads the clip
        # its own way and may well be fine - but the number must not be invented
        progress("  could not read frames to measure; skipping the check")
    else:
        # "worst of N samples", never "worst contrast": this is a sampled
        # minimum and reading it as a verified one is exactly how it misleads
        progress(f"  slate contrast: worst of {ns} samples {worst:.1f}:1 "
                 f"({where}, at {when:.0f}s)")
        if worst < 4.5:
            progress(f"  WARNING: below 4.5:1. Raise --scrim (currently "
                     f"{float(cfg['scrim']):g}), or move --slate-position off "
                     "this part of the frame.")
    if on_progress:
        on_progress(0.12, None)

    progress(f"Encoding {dur / 60:.1f} min at {W}x{H}…")
    out = build_slate_video(
        cfg, src, ovl, os.path.join(outdir, f"{slug}.mp4"), dur, info,
        on_progress=(lambda f, e=None: on_progress(0.12 + 0.88 * f, e))
        if on_progress else None)
    progress(f"  {os.path.basename(out)}  "
             f"{os.path.getsize(out) / 1e6:.2f} MB")

    json.dump(_video_sidecar(cfg, info, worst, where, when, ns, out),
              open(os.path.join(outdir, f"{slug}_render.json"), "w"), indent=2)
    shutil.rmtree(work, ignore_errors=True)
    if on_progress:
        on_progress(1.0, 0)
    progress("Done.")
    return {"thumbnail": None, "thumbnails": [], "video": out,
            "cover": None, "duration": dur, "folder": outdir}


def _x264(cfg, info):
    """The shared encoder tail, so every segment in a loop render matches.

    Concat-copy needs one resolution, one pixel format, one timebase and one
    set of encoder settings across every piece, or the copy cannot be a copy.
    One list, used by all of them.
    """
    fmt = "yuv444p" if cfg.get("full_chroma") else "yuv420p"
    return ["-c:v", "libx264",
            "-preset", cfg.get("x264_preset") or VIDEO_PRESET,
            "-crf", str(cfg.get("crf") if cfg.get("crf") is not None
                        else VIDEO_CRF),
            "-pix_fmt", fmt, "-fps_mode", "passthrough",
            "-color_primaries", info["primaries"], "-color_trc", info["trc"],
            "-colorspace", info["space"],
            # every segment on the same timebase, which is what lets the
            # concat demuxer copy rather than re-stamp
            "-video_track_timescale", "90000"]


def loop_body(cfg, src, out, keyframes, info, dur, on_progress=None):
    """The clip encoded once, bare, with keyframes forced at the cut points.

    This is the answer to how a stream copy can start mid-clip. The cut offsets
    are known BEFORE the body is encoded - loop_plan works them out from the
    schedule - so they are handed to x264 as -force_key_frames and an IDR lands
    exactly there. Cutting on a keyframe is then true by construction rather
    than by luck, and nothing around the slate boundaries has to be re-encoded.

    The alternative, cutting a clip somebody else encoded at offsets it has no
    keyframe near, is what would force re-encoding the GOP at each boundary.
    That case never arises here. The cost of this one is two extra I-frames in
    a seventeen-minute encode.
    """
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", src, "-an"]
    if keyframes:
        cmd += ["-force_key_frames", ",".join(f"{k:.3f}" for k in keyframes)]
    cmd += _x264(cfg, info) + [out]
    _ffmpeg_progress(cmd, dur, on_progress=on_progress)
    return out


def loop_slate_segment(cfg, src, ovl, out, seg, info, on_progress=None):
    """One stretch of the clip with the slate on it, faded in and/or out.

    The overlay is the same static PNG every other slated frame uses; what
    makes it arrive and leave is a looped image INPUT run through fade with
    alpha, which costs nothing structurally because these seconds were always
    going to be encoded on their own.

    A slate split across a loop seam fades only at its real edges - see
    loop_plan. Fading at the seam would read as the slate blinking in the
    middle of its own appearance.
    """
    length = seg["src1"] - seg["src0"]
    fmt = "yuv444p" if cfg.get("full_chroma") else "yuv420p"
    f = ["[1:v]format=rgba"]
    if seg["fade_in"]:
        f.append(f"fade=t=in:st=0:d={LOOP_FADE:g}:alpha=1")
    if seg["fade_out"]:
        f.append(f"fade=t=out:st={max(0.0, length - LOOP_FADE):.3f}"
                 f":d={LOOP_FADE:g}:alpha=1")
    chain = (",".join(f) + "[ovl];"
             "[0:v]format=rgb24[b];"
             f"[b][ovl]overlay=0:0:format=rgb,format={fmt}[v]")
    cmd = ["ffmpeg", "-y", "-v", "error",
           "-ss", f"{seg['src0']:.3f}", "-t", f"{length:.3f}", "-i", src,
           "-loop", "1", "-framerate", f"{info['fps']:.6f}", "-i", ovl,
           "-filter_complex", chain, "-map", "[v]", "-an",
           "-t", f"{length:.3f}"] + _x264(cfg, info) + [out]
    _ffmpeg_progress(cmd, length, on_progress=on_progress)
    return out


def loop_cut(body, out, seg):
    """A bare stretch taken out of the body encode by stream copy.

    -ss before -i so the seek is to a keyframe, which loop_body has guaranteed
    is exactly here. avoid_negative_ts make_zero restarts the segment's
    timestamps at 0, without which the concat demuxer inherits an offset and
    the assembled timeline drifts.
    """
    length = seg["src1"] - seg["src0"]
    cmd = ["ffmpeg", "-y", "-v", "error",
           "-ss", f"{seg['src0']:.3f}", "-i", body, "-t", f"{length:.3f}",
           "-c", "copy", "-avoid_negative_ts", "make_zero", out]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("ffmpeg failed cutting a loop segment:\n"
                         + r.stderr[-800:])
    return out


def build_loop_video(cfg, src, ovl, audio, dur, out, work, info, plan,
                     progress=lambda s: None, on_progress=None):
    """The looped render: a few encodes, some copies, one concat, one audio pass.

    What goes through the encoder is the clip once plus the minutes that carry
    the slate - for a seventeen-minute clip under a two-and-a-half hour
    recording that is about twenty minutes of footage for a two-and-a-half hour
    output. Everything else is a stream copy of a file already on disk, and the
    nine bare repeats are the SAME file named nine times, so they cost the mux
    and nothing else.

    No +faststart on the output. It rewrites the whole file to move the moov
    atom to the front, which on a twenty-gigabyte render means reading and
    writing twenty gigabytes a second time for a benefit - progressive HTTP
    streaming - that a file being uploaded to YouTube never collects.
    """
    pieces, encodes, cuts, keyframes = plan
    files = {}

    need_body = bool(cuts) or any(p["key"] == "body" for p in pieces)
    total = len(encodes) + (1 if need_body else 0)
    done = 0
    if need_body:
        progress(f"  encode 1/{total}: the clip, bare, "
                 f"{info['duration'] / 60:.1f} min"
                 + (f", keyframes forced at "
                    + ", ".join(f"{k:.2f}s" for k in keyframes)
                    if keyframes else ""))
        files["body"] = loop_body(
            cfg, src, os.path.join(work, "body.mp4"), keyframes, info,
            info["duration"],
            on_progress=(lambda f, e=None: on_progress(0.10 + 0.45 * f, e))
            if on_progress else None)
        done = 1
        progress(f"    {os.path.getsize(files['body']) / 1e9:.2f} GB")

    for key, seg in encodes.items():
        done += 1
        length = seg["src1"] - seg["src0"]
        progress(f"  encode {done}/{total}: slate over "
                 f"{seg['src0']:.1f}-{seg['src1']:.1f}s ({length:.0f}s"
                 + (", fade in" if seg["fade_in"] else "")
                 + (", fade out" if seg["fade_out"] else "") + ")")
        files[key] = loop_slate_segment(
            cfg, src, ovl, os.path.join(work, f"{key}.mp4"), seg, info)

    for key, seg in cuts.items():
        progress(f"  copy: {seg['src0']:.1f}-{seg['src1']:.1f}s out of the body")
        files[key] = loop_cut(files["body"], os.path.join(work, f"{key}.mp4"),
                              seg)

    # basenames, list beside them - the concat demuxer resolves a relative
    # entry against the LIST's directory, which sidesteps escaping a Windows
    # path inside a demuxer argument
    lst = os.path.join(work, "_concat.txt")
    with open(lst, "w", encoding="utf-8") as fh:
        for p in pieces:
            fh.write(f"file '{os.path.basename(files[p['key']])}'\n")

    progress(f"Assembling {len(pieces)} segments and encoding audio…")
    _ffmpeg_progress(
        ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
         "-i", lst, "-i", audio, "-map", "0:v", "-map", "1:a",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "320k",
         "-t", f"{dur:.3f}", "-shortest", out],
        dur, on_progress=(lambda f, e=None: on_progress(0.55 + 0.45 * f, e))
        if on_progress else None)
    return out


def loop_thumbnail(cfg, src, at, tw, th, work, out):
    """The thumbnail: one frame of the clip with the slate composited on it.

    Pulled through the Pillow path rather than the ffmpeg one, exactly as photo
    mode composes a still - this is a single frame, so there is no reason to
    pay for the overlay-and-encode route the video takes.
    """
    png = os.path.join(work, "_thumbframe.png")
    r = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-ss", f"{max(0.0, at):.3f}",
         "-i", src, "-frames:v", "1", "-vf", f"scale={tw}:{th}", png],
        capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(png):
        raise SystemExit(f"--thumb-at {at:g}: could not read a frame there.\n"
                         + (r.stderr or "")[-400:])
    k = tw / 1280.0
    dh = 1280.0 * th / tw
    pos = cfg.get("slate_position", "top")
    dy = slate_dy(dh, pos)
    fg = cfg.get("foreground") or BONE
    boxes = slate_boxes(cfg, dh=dh, dy=dy)
    band_top, band_bot = scrim_band(dh, dy, max(b[2] for b in boxes), pos)
    img = D.scrim_over(Image.open(png).convert("RGB"), tw, th, k,
                       float(cfg["scrim"]), cfg.get("background") or INK,
                       band_bot, band_top)
    under = img
    img = D.slate_over(img, cfg, tw, th, k, dy,
                       format_number(cfg.get("number", ""),
                                     cfg.get("number_style", "No.")),
                       cfg["slate_time"], fg,
                       fg if cfg.get("slate_mono") else cfg["accent"], FONT,
                       LAYOUTS["landscape"])
    img.save(out)
    os.remove(png)
    return out, under


# ----------------------------------------------------------------- driver
def _sidecar(cfg, lv, dur, scale, dyn, n, variants=None, cover=None,
             photos=None, vertical=None):
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
            "badge": cfg.get("badge") or "",
        },
        "look": {
            "scene": cfg.get("scene", ""),
            "style": cfg.get("style", ""),
            "detail": cfg.get("detail", "Default"),
            "seed": f"{cfg['_scene']['seed']:016x}" if cfg.get("_scene") else "",
            "tree_ahead": cfg.get("tree_ahead") or scene_mod.TREE_AHEAD[0],
            "mountain_face": cfg.get("mountain_face") or scene_mod.MOUNTAIN_FACE[0],
            "antennas": len((cfg.get("_scene") or {}).get("lights") or []),
            "foreground_shapes": len((cfg.get("_scene") or {}).get("fore") or []),
            "blink": not cfg.get("no_blink", False),
            "weather": cfg.get("weather") or "off",
            "cloud_count": len((cfg.get("_weather") or {}).get("clouds") or []),
            "rain_streaks": len(((cfg.get("_weather") or {}).get("rain")
                                 or {}).get("streaks") or []),
            "weather_seed": (f"{cfg['_weather']['seed']:016x}"
                             if cfg.get("_weather") else ""),
            "stars": bool(cfg.get("stars")),
            "star_count": len((cfg.get("_sky") or {}).get("stars") or []),
            "star_twinklers": (cfg.get("_sky") or {}).get("twinklers", 0),
            # ...and how many of those the silhouette left showing, so actually
            # got filters. The gap between the two is stars behind buildings
            "star_twinklers_drawn": (cfg.get("_sky") or {}).get(
                "twinkling_drawn", 0),
            "star_color": cfg.get("star_color", ""),
            "star_seed": (f"{cfg['_sky']['seed']:016x}"
                          if cfg.get("_sky") else ""),
            "twinkle": not cfg.get("no_twinkle", False),
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
            "number_color": cfg.get("number_color") or "",
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
        "cover": ({"file": os.path.basename(cover), "size": COVER_PX,
                   "design_height": COVER_DH, "dpi": 300}
                  if cover else None),
        # the vertical frame is its own build, so it carries its own levels
        # and the column as it was actually stacked - wrapped and shrunk
        "vertical": (_vertical_sidecar(cfg, vertical) if vertical else None),
        "photos": photos,
        "variants": variants or [],
        "source_audio": os.path.basename(cfg.get("audio", "")),
        "duration_s": dur,
        "levels": lv,
    }


def _vertical_sidecar(cfg, vertical):
    """The vertical frame's block of the sidecar."""
    W, H, dw, dh, lift = vertical_frame()
    return {"file": os.path.basename(vertical["path"]), "size": [W, H],
            "design": [dw, dh], "ground_lift": lift,
            "tower_count": len(vertical["levels"]),
            "column": [{"line": ln["name"], "text": ln["text"],
                        "size": round(ln["size"], 2),
                        "baseline": round(ln["baseline"], 2)}
                       for ln in cfg["_vcolumn"][0]],
            "levels": vertical["levels"]}


def _photo_targets(cfg):
    """Every output this render will actually write, as (label, W, H).

    They do not ask the same thing: a 3000x3000 cover needs 3000px on the SHORT
    edge and refuses photos that clear the video and the thumbnail comfortably.
    """
    tw = int(cfg.get("thumb_width", 1920))
    out = [("thumbnail", tw, round(tw * 9 / 16))]
    if not cfg.get("thumb_only"):
        out.append(("video", cfg.get("width", 2560), cfg.get("height", 1440)))
    if cfg.get("cover"):
        out.append(("cover", COVER_PX, COVER_PX))
    return out


def _run_photo(cfg, slug, outdir, work, progress, on_progress, stage):
    """A photo render, end to end.

    Deliberately never calls envelope(). There is no geometry to derive here,
    so the audio is probed for its LENGTH and otherwise not decoded at all -
    on a three-hour FLAC that is the single largest saving the mode makes,
    before any of the encode work below.
    """
    photos = list(cfg["photos"])
    dur = probe_duration(cfg["audio"])
    if dur <= 0:
        raise SystemExit(f"Could not read a duration from {cfg['audio']}.")

    tw = int(cfg.get("thumb_width", 1920))
    th = round(tw * 9 / 16)
    W, H = cfg.get("width", 2560), cfg.get("height", 1440)
    scope = cfg.get("slate_scope", "both")

    # Every output this render will actually write, checked in one pass before
    # anything is built. They do not ask the same thing - a 3000x3000 cover
    # needs 3000px on the SHORT edge and refuses photos that clear the other
    # two - and finding that out after the video has encoded is exactly the
    # waste the up-front rule exists to prevent.
    for p, note in cfg.get("_photos_ok") or photo_mod.validate(
            photos, _photo_targets(cfg)):
        if note:
            progress(f"  {os.path.basename(p)}: {note}")

    interval = float(cfg.get("photo_interval", PHOTO_INTERVAL))
    segs, unique = photo_mod.plan(dur, len(photos), interval)
    progress(f"{len(photos)} photos on a {interval:g}s cycle: {len(segs)} "
             f"segments over {dur / 60:.1f} min, "
             f"{len(unique)} distinct to encode")
    if on_progress:
        on_progress(0.08, None)

    # A still is a still: the cover follows the thumbnail rather than the
    # video, so 'thumbnail' means every IMAGE this render writes. Only the
    # encode is what 'thumbnail' takes the slate away from.
    still_slate = scope in ("both", "thumbnail")
    tt = cfg.get("start") if still_slate else None
    progress("Building thumbnail…")
    thumb, under = _photo_frame(cfg, photos[0], tw, th, still_slate,
                                time_text=tt, measure=True,
                                out=os.path.join(outdir, f"{slug}_thumb.png"))
    if still_slate:
        c, where = _slate_legibility(under, cfg, tw, th)
        progress(f"  slate contrast {c:.1f}:1 at its weakest ({where})"
                 + ("" if c >= 4.5 else " - raise --scrim if that reads thin"))
    if on_progress:
        on_progress(0.12, None)

    cover = None
    if cfg.get("cover"):
        progress("Building cover…")
        cover = _photo_frame(cfg, photos[0], COVER_PX, COVER_PX, still_slate,
                             time_text=tt, save=D.png_meta(),
                             out=os.path.join(outdir, f"{slug}_cover.png"))
        progress(f"  {os.path.basename(cover)}  {COVER_PX}x{COVER_PX}, "
                 f"{os.path.getsize(cover) / 1e6:.2f} MB")

    pinfo = {
        "interval_s": interval,
        "slate_scope": scope,
        "scrim": float(cfg.get("scrim", SCRIM_DEFAULT)),
        "sources": photos,
        "segments": len(segs),
        # what actually went through the encoder, which is the claim the mode
        # makes and so the thing worth being able to check afterwards
        "encoded_segments": [
            {"key": k, "photo": os.path.basename(photos[u["photo"]]),
             "dur_s": round(u["dur"], 3)} for k, u in unique.items()],
    }

    def done(vid):
        json.dump(_sidecar(cfg, [], dur, cfg.get("scale", ""),
                           cfg.get("dynamics", ""), 0, None, cover, pinfo),
                  open(os.path.join(outdir, f"{slug}_render.json"), "w"),
                  indent=2)
        shutil.rmtree(work, ignore_errors=True)
        if on_progress:
            on_progress(1.0, 0)
        return {"thumbnail": thumb, "thumbnails": [thumb], "video": vid,
                "cover": cover, "duration": dur, "folder": outdir}

    if cfg.get("thumb_only"):
        progress("Done (thumbnail only).")
        return done(None)

    progress(f"Encoding {dur / 60:.1f} min of video from "
             f"{len(unique)} segment encodes…")
    vid = build_photo_video(cfg, segs, unique, photos, cfg["audio"], dur, W, H,
                            os.path.join(outdir, f"{slug}.mp4"), work,
                            fps=cfg.get("fps", 10), progress=progress,
                            on_progress=on_progress)
    progress("Done.")
    return done(vid)


def run(cfg, progress=lambda s: None, on_progress=None):
    global FONT, LAYOUTS
    FONT = find_font()
    LAYOUTS = presets_mod.layouts()
    if not shutil.which("ffmpeg"):
        raise SystemExit("ffmpeg not found on PATH.")
    # settle the sky before anything draws, so every caller - GUI, CLI, or a
    # direct run() - lands on the same colours. Copied, not mutated in place.
    cfg = dict(cfg)
    cfg["background"] = cfg.get("background") or INK
    cfg["foreground"] = (cfg.get("foreground")
                         or presets_mod.auto_foreground(cfg["background"]))
    # ...and the wash's strength with them, ONCE, because its default differs
    # by mode. None means "not typed" - see scrim_default().
    if cfg.get("scrim") is None:
        cfg["scrim"] = scrim_default(cfg)
    # ...and the sky's own colour with them, from whichever of the three the
    # palette names. An error rather than a silent no-op, for the reason
    # scene_mod.check() gives: the alternative is finding out after the encode.
    if cfg.get("photos") and cfg.get("video"):
        raise SystemExit("--photos and --video are different modes: one cycles "
                         "stills, the other burns the slate onto a clip. "
                         "Choose one.")
    if cfg.get("photos") or cfg.get("video"):
        cfg["stars"] = False          # nothing to stand behind: the photo, or
                                      # the clip, IS the whole background
    if cfg.get("stars"):
        P = presets_mod.load()
        key = cfg.get("color_preset") or "None (series colour)"
        cfg["star_color"] = presets_mod.star_color(
            key, P, cfg["foreground"], cfg["accent"], cfg["background"])
        if not cfg["star_color"]:
            # Stars are on by default, so a sky that cannot carry them must not
            # be an error - Morning would stop rendering at all. It stays an
            # error when they were asked for BY NAME, because then the useful
            # answer is why they did not appear rather than a quiet frame
            # without them. Which is the same split the GUI already draws: the
            # box greys out on these palettes instead of being refused later.
            if cfg.get("stars_explicit"):
                raise SystemExit(
                    f"--stars: the {key} palette has no star field. Its sky is "
                    "too bright to carry one. Choose from: "
                    + ", ".join(presets_mod.star_presets(P)))
            cfg["stars"] = False
    # Photos are checked before anything is created. Every other early failure
    # in here leaves an empty render folder behind and that has never mattered;
    # a mistyped photo path is the one somebody will actually hit.
    if cfg.get("photos") and photo_mod is None:
        raise SystemExit(
            "Photo mode needs lss_photo.py and this installation does not have "
            "it yet. Restart the app once to let the updater fetch it, or run: "
            "py lss_studio/lss_update.py --repair")
    if cfg.get("photos"):
        t = _photo_targets(cfg)
        progress("Checking photos against "
                 + ", ".join(f"{n} {w}x{h}" for n, w, h in t) + "…")
        cfg["_photos_ok"] = photo_mod.validate(cfg["photos"], t)
    if cfg.get("video"):
        # same rule, same place: refused before a folder exists to leave behind
        cfg["_video_info"] = video_validate(cfg["video"])
    # ...and the recording itself, wherever one is used. A bare --video render
    # is the only mode that takes none.
    if cfg.get("audio"):
        cfg["_audio_info"] = audio_validate(cfg["audio"])
        a = cfg["_audio_info"]
        progress(f"Audio: {os.path.basename(cfg['audio'])}  {a['codec']}, "
                 f"{a['channels']}ch, {a['sample_rate']} Hz")
    # The vertical frame's column is laid out and checked against its safe
    # zones HERE, before the audio pass: it needs only the slate text and the
    # font, and a collision found after reading a three-hour recording is the
    # failure this whole early block exists to avoid.
    if cfg.get("badge") and not cfg.get("vertical"):
        raise SystemExit("--badge needs --vertical: the badge is drawn on the "
                         "vertical frame only.")
    if cfg.get("vertical"):
        if cfg.get("photos") or cfg.get("video"):
            raise SystemExit("--vertical is for generated renders: a photo or "
                             "a clip is framed 16:9 and has no geometry to "
                             "rebuild for a tall frame.")
        cfg["_vcolumn"] = column_layout(cfg)

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
    # A looped render's scratch is the clip re-encoded plus the pieces cut out
    # of it - several gigabytes written, read and deleted - and outdir is
    # routinely a network drive or a synced folder. That work goes to LOCAL
    # temp; only the finished render lands in outdir.
    if cfg.get("video") and cfg.get("audio"):
        import tempfile
        work = tempfile.mkdtemp(prefix="lss_loop_")
        progress(f"Output folder: {outdir}")
        progress(f"Scratch (local): {work}")
    else:
        work = os.path.join(outdir, "_work")
        os.makedirs(work, exist_ok=True)
        progress(f"Output folder: {outdir}")

    def stage(lo, hi):
        return (lambda f, eta=None: on_progress(lo + (hi - lo) * f, eta)) \
            if on_progress else None

    if cfg.get("video"):
        # an audio file alongside --video is what asks for a looped render:
        # there is a runtime to fill, where --video alone is one pass over one
        # clip and has nothing to loop to
        if cfg.get("audio"):
            return _run_loop(cfg, slug, outdir, work, progress, on_progress)
        return _run_video(cfg, slug, outdir, work, progress, on_progress)

    if cfg.get("photos"):
        return _run_photo(cfg, slug, outdir, work, progress, on_progress, stage)

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
        # How high the silhouette may rise, column by column, from where this
        # render's own slate text actually ends. Only the city reads it - the
        # nature styles carry their own summit caps - so it is not measured for
        # anything else, which also keeps them clear of the font metrics.
        ceiling = (scene_mod.ceiling_profile(slate_boxes(cfg))
                   if style == "city" and cfg.get("ceiling_profile", True)
                   else None)
        cfg["_scene"] = scene_mod.build(style, db, lv,
                                        detail=cfg.get("detail", "Default"),
                                        scale=scale, dynamics=dyn,
                                        ceiling=ceiling)
        s = cfg["_scene"]
        fore = s.get("fore") or []
        progress(f"Style {style}: {len(s['mountains'])} summits, "
                 f"{len(s['trees']) + len(s['town_trees'])} trees, "
                 f"{len(s['houses'])} buildings, "
                 f"{sum(len(h.get('panes', [])) for h in s['houses'] + fore)} windows, "
                 f"{len(s['lights'])} antennas, "
                 f"{len(fore)} in front "
                 f"(seed {s['seed']:016x})")

    # The sky, if it was asked for. Built for EVERY style, not only the
    # silhouettes: it is a background layer rather than silhouette geometry, so
    # blocks and topo get one too. The slate boxes go in because that is where
    # a sun or moon will later stand - see lss_scene.sky().
    if cfg.get("stars"):
        cfg["_sky"] = scene_mod.sky(db, detail=cfg.get("detail", "Default"),
                                    boxes=slate_boxes(cfg),
                                    twinkle=not cfg.get("no_twinkle"))
        sk = cfg["_sky"]
        # the filter count is not known yet: a twinkler the silhouette covers
        # gets none, and which ones those are is read off the composed layer
        progress(f"Sky: {len(sk['stars'])} stars, {sk['twinklers']} twinkling, "
                 f"in {cfg['star_color']} (seed {sk['seed']:016x})")

    # The weather, on its own stream again. Built for every style: the band it
    # fills is read from whatever this style put in the sky, not from a table.
    cfg["_weather"] = _weather(cfg, db)
    if cfg.get("_weather"):
        wx = cfg["_weather"]
        rain = (wx.get("rain") or {}).get("streaks") or []
        progress(f"Weather {wx['state']}: {len(wx['clouds'])} clouds over "
                 f"{wx['band'][1] - wx['band'][0]:.0f} units of sky"
                 + (f", {len(rain)} streaks at "
                    f"{wx['rain']['lean']:+.0f}\u00b0" if rain else "")
                 + f" (seed {wx['seed']:016x})")

    tw = int(cfg.get("thumb_width", 1920))
    th = round(tw * 9 / 16)
    # a thumbnail shows the finished, fully-played frame unless asked otherwise
    frac = cfg.get("progress", 1.0)
    frac = 1.0 if frac is None else float(frac)
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
            if cfg.get("stars"):
                # each palette names its own star role, so a variant set
                # compares the sky along with everything else - and a palette
                # in the set that carries no field simply renders without one,
                # which is the comparison actually being asked for
                vcfg["star_color"] = presets_mod.star_color(
                    v["name"], presets_mod.load(), v["foreground"],
                    v["accent"], v["background"])
                if not vcfg["star_color"]:
                    vcfg["_sky"] = None
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

    cover = None
    if cfg.get("cover"):
        progress("Building cover…")
        cover = _cover(cfg, db, lv, style, scale, dyn,
                       os.path.join(outdir, f"{slug}_cover.png"))
        progress(f"  {os.path.basename(cover)}  "
                 f"{COVER_PX}×{COVER_PX}, "
                 f"{os.path.getsize(cover) / 1e6:.2f} MB")

    vert = None
    if cfg.get("vertical"):
        progress("Building vertical…")
        vpath, vlv = _vertical(cfg, db, n, style, scale, dyn,
                               os.path.join(outdir, f"{slug}_vertical.png"),
                               work, frac)
        vert = {"path": vpath, "levels": vlv}
        W_, H_ = vertical_frame()[:2]
        progress(f"  {os.path.basename(vpath)}  {W_}×{H_}, {len(vlv)} towers, "
                 f"title on {sum(1 for l in cfg['_vcolumn'][0] if l['name'].startswith('title'))} "
                 f"line(s)")

    if cfg.get("thumb_only"):
        json.dump(_sidecar(cfg, lv, dur, scale, dyn, n, variants, cover,
                           vertical=vert),
                  open(os.path.join(outdir, f"{slug}_render.json"), "w"), indent=2)
        shutil.rmtree(work, ignore_errors=True)
        if on_progress:
            on_progress(1.0, 0)
        progress("Done (thumbnail only).")
        return {"thumbnail": thumbs[0], "thumbnails": thumbs, "video": None,
                "cover": cover, "vertical": vert and vert["path"],
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

    json.dump(_sidecar(cfg, lv, dur, scale, dyn, n, None, cover,
                       vertical=vert),
              open(os.path.join(outdir, f"{slug}_render.json"), "w"), indent=2)
    shutil.rmtree(work, ignore_errors=True)
    progress("Done.")
    return {"thumbnail": thumbs[0], "thumbnails": thumbs, "video": vid,
            "cover": cover, "vertical": vert and vert["path"],
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
    g.add_argument("--start", default="",
                   help="e.g. 06:30 PM. Leave it out for no clock: nothing "
                        "is drawn in the time slot of the thumbnail or the "
                        "video, and --date is then not needed")
    g.add_argument("--number", default="")
    g.add_argument("--number-style", default="No.", choices=list(NUM_STYLES))
    g.add_argument("--badge", default="", metavar="TEXT",
                   help="a small label in an accent pill above the series on "
                        "the vertical frame, e.g. LIVE. Off unless given, and "
                        "--vertical only - the landscape slate has no slot "
                        "for one")

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
                        + ". Defaults to the series' own: city for Sounds of the "
                        "City, houses for Sounds in Towns, mountains_forest "
                        "for Sounds of Nature, blocks elsewhere")
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
    g.add_argument("--no-blink", action="store_true",
                   help="leave the antenna beacons steady instead of blinking "
                        "them (--style city only). Video only - a thumbnail "
                        "always shows them lit")
    g.add_argument("--stars", action="store_true",
                   help="a star field behind the silhouette, which occludes "
                        "it. ON by default, so this is only needed to ask for "
                        "one by name - which makes a palette that cannot carry "
                        "stars an error instead of a frame without them. Only "
                        "the night palettes have a field: each says which of "
                        "its own colours the stars take, so they are "
                        "near-white on the dark skies and the accent on Mist. "
                        "Works with every --style")
    g.add_argument("--no-stars", action="store_true",
                   help="no star field, whatever the palette")
    g.add_argument("--weather", default="off",
                   choices=scene_mod.WEATHER_STATES,
                   help="clouds in whatever sky the silhouette leaves free, "
                        "and rain in front of it. rain implies clouds. Both "
                        "are static - drawn once and held, so the video costs "
                        "nothing extra - and both take their colour from the "
                        "palette in use rather than being tuned per style, "
                        "landing softer than the skyline on every preset. "
                        "Works with every --style")
    g.add_argument("--no-twinkle", action="store_true",
                   help="hold the stars steady instead of letting them fade "
                        "out and return (stars only). Video only, and it "
                        "only removes motion - a thumbnail is always a still, "
                        "and the field is the same either way")
    g.add_argument("--filled", action="store_true",
                   help="solid silhouette instead of outlines")
    g.add_argument("--slate-mono", action="store_true",
                   help="draw the small slate text in the silhouette colour, "
                        "not the accent")
    g.add_argument("--number-color", default="", metavar="#RRGGBB",
                   help="colour for the number alone, e.g. #F2D289. Wins over "
                        "both the accent and --slate-mono; blank keeps it "
                        "with the rest of the small slate text")
    g = a.add_argument_group(
        "photo", "photographs instead of a generated silhouette")
    g.add_argument("--photos", default="", metavar="PATHS",
                   help="use photographs instead of a generated silhouette: a "
                        "comma-separated list of image files, or a folder of "
                        "them (sorted by name). Turns photo mode ON - there is "
                        "no audio-derived geometry in a photo render, so the "
                        "silhouette and loudness flags are refused rather than "
                        "ignored. Photos are EXIF-rotated, converted to sRGB, "
                        "centre-cropped to the frame and downscaled; one that "
                        "is too small is an error, never an upscale")
    g.add_argument("--photo-interval", type=float,
                   default=PHOTO_INTERVAL, metavar="SECONDS",
                   help=f"how long each photo holds before the next "
                        f"(default {PHOTO_INTERVAL:g}). The cycle "
                        "repeats until the audio is covered and the last "
                        "segment is cut to the audio's end")
    # default None, NOT a number: the default differs by mode and settling it
    # here would mean a conditional buried in the parser. run() settles it once,
    # through scrim_default(), for every caller - see that function
    g.add_argument("--scrim", type=float, default=None, metavar="0-1",
                   help=f"how heavy the wash behind the slate is, 0 turns it "
                        f"off. Defaults to {SCRIM_DEFAULT} on a photo, where "
                        "one fixed frame makes the wash cheap insurance, and "
                        f"to {VIDEO_SCRIM_DEFAULT:g} on a clip, where a sky "
                        "that already carries the text turns it into a haze "
                        "around the text. Painted in the background colour, "
                        "as a band behind the slate that fades out on "
                        "whichever side is not a frame edge")

    g = a.add_argument_group(
        "video", "the slate burned onto a supplied clip")
    g.add_argument("--video", default="", metavar="PATH",
                   help="burn the slate onto a source clip instead of "
                        "rendering one. Turns clip mode ON: one video in, one "
                        "video out, the slate composited on top, camera audio "
                        "stripped. The clip's resolution and frame rate are "
                        "preserved exactly - nothing is resized or resampled - "
                        "and a source narrower than "
                        f"{VIDEO_MIN_W}px, or taller than it is wide, is an "
                        "error rather than a cramped or centred slate. Takes "
                        "no audio file: the real audio is muxed later")
    g.add_argument("--slate-time", default="", metavar="HH:MM",
                   help="the time frozen on the slate, e.g. 18:30. Drawn in "
                        "the house 12-hour form whichever way it is typed. "
                        "Static, like photo mode's - a still slate is what "
                        "makes the overlay a single image and the encode one "
                        "pass. Leave it out for a slate with no clock")
    g.add_argument("--slate-intro", default=str(SLATE_INTRO_DEFAULT),
                   metavar="MINUTES",
                   help=f"how long the slate shows at the START of a looped "
                        f"render, in minutes (default {SLATE_INTRO_DEFAULT:g}, "
                        "0 turns it off, 'all' leaves it on for the whole "
                        "runtime). Looping needs an audio file alongside "
                        "--video; without one the slate is simply always on, "
                        "as it has been")
    g.add_argument("--slate-outro", default=str(SLATE_OUTRO_DEFAULT),
                   metavar="MINUTES",
                   help=f"the same at the END (default "
                        f"{SLATE_OUTRO_DEFAULT:g}, 0 turns it off). Together "
                        "these may not exceed the audio's length - that is an "
                        "error, not a quiet merge")
    g.add_argument("--thumb-at", type=float, default=0.0, metavar="SECONDS",
                   help="which frame of the clip the thumbnail comes from "
                        "(default 0, the first). Looped renders only")
    g.add_argument("--slate-position", default=SLATE_POSITIONS[0],
                   choices=SLATE_POSITIONS,
                   help="where the slate block sits in the frame (default "
                        "top, the layout every other render uses). Footage has "
                        "a subject and the slate can land on it; this moves "
                        "the whole block as a unit, in design units, so "
                        "nothing in it changes size or spacing. The wash and "
                        "the contrast check both follow it. --video only")

    g = a.add_argument_group("shape", "how loudness becomes height")
    g.add_argument("--scale", default="Skyline (rank)", choices=SCALES)
    g.add_argument("--dynamics", default="More", choices=list(DYNAMICS),
                   help="no effect on --scale 'Fixed loudness'")
    g.add_argument("--towers", default="Default",
                   choices=list(TOWERS) + ["Auto"],
                   help="tower width for --style blocks: Thick, Default, "
                        "Thin, Fine, or Auto. --style city sets its own block "
                        "width and always draws 27 of them, so there this only "
                        "changes how finely the recording is sampled before "
                        "each block takes its loudest moment")
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
    g.add_argument("--cover", action="store_true",
                   help=f"also write a {COVER_PX}x{COVER_PX} square cover for "
                        "Spotify, at 300 dpi. The same render laid out for 1:1 "
                        "- more sky, the slate on the frame's middle - always "
                        "as the finished fully-played frame. Adds about half a "
                        "second and composes with everything else")
    g.add_argument("--vertical", action="store_true",
                   help="also write a 1080x1920 frame for YouTube Shorts, "
                        "rebuilt rather than cropped: the same recording's "
                        "scene on a 9:16 frame - fewer buildings across at "
                        "the same size, more sky - with the slate restacked "
                        "as a centred column clear of the Shorts UI. A still, "
                        "following --progress like the thumbnail. Sizes, "
                        "positions and safe zones are the 'vertical' layout "
                        "in lss_presets.json")
    g.add_argument("--slate-scope", default="both",
                   choices=SLATE_SCOPES,
                   help="which outputs carry the slate in photo mode: both, "
                        "thumbnail (the images get it, the video stays bare), "
                        "or none. Photo mode only - a generated render always "
                        "carries its slate")
    g.add_argument("--progress", type=float, default=1.0,
                   help="how far through playback the thumbnail is drawn, 0-1. "
                        "Defaults to 1, the finished fully-played frame; use "
                        "--progress 0 for the unplayed state")
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
            d = presets_mod.series_style(s, P)
            print(f"series: {s}  [{sc}]  styles: "
                  + ", ".join(f"{v} (default)" if v == d else v
                              for v in scene_mod.SCENE_STYLES[sc]))
        print("themes:", ", ".join(presets_mod.theme_names(P)))
        print("colors:", ", ".join(presets_mod.color_preset_names(P)))
        print("stars:", ", ".join(presets_mod.star_presets(P)))
        print("detail:", ", ".join(scene_mod.DETAIL) + ", or a number")
        return
    # A clip render takes no audio file and has no live clock, so neither the
    # recording nor the date and start time it would be stamped from are
    # required: --slate-time carries the only time on the frame. Nothing needs
    # a time at all - a blank one is a slate with no clock - and the date is
    # only ever read to start the video's clock, so it goes with --start.
    need = (("video", "place", "city", "conditions")
            if n.video else
            ("audio", "place", "city", "conditions")
            + (("date",) if n.start else ()))
    missing = [k for k in need if not getattr(n, k)]
    if missing:
        a.error("missing required: "
                + ", ".join("audio" if m == "audio" else "--" + m.replace("_", "-")
                            for m in missing))
    for flag in ("accent", "background", "foreground", "number_color"):
        v = getattr(n, flag)
        if v and not presets_mod.valid_hex(v):
            a.error(f"--{flag.replace('_', '-')}: '{v}' is not a colour like #CF7A34")
    if n.color_preset not in presets_mod.color_preset_names(P):
        a.error(f"--colors: unknown preset '{n.color_preset}'. Choose from: "
                + ", ".join(presets_mod.color_preset_names(P)))
    photos = photo_mod.collect(n.photos) if photo_mod else (
        [n.photos] if n.photos.strip() else [])
    if photos and photo_mod is None:
        a.error("--photos needs lss_photo.py and this installation does not "
                "have it yet. Restart the app once to let the updater fetch "
                "it, or run: py lss_studio/lss_update.py --repair")
    if photos and n.video:
        a.error("--photos and --video are different modes: one cycles stills, "
                "the other burns the slate onto a clip. Choose one.")
    if photos:
        # read off the RAW namespace, before any series default is written
        # back over a flag - otherwise every render would look as though it
        # had asked for a style by name
        err = photo_mod.check(dict(vars(n), photos=photos,
                                   stars_explicit=bool(n.stars)))
        if err:
            a.error(err)
        # Not in lss_photo.check(): that module ships to installs that may be a
        # launch behind, and a flag it has never heard of must not become an
        # error there. The refusal is temporary anyway - photo mode is where
        # this is wanted next.
        if n.slate_position != SLATE_POSITIONS[0]:
            a.error("--slate-position is --video only for now: photo mode "
                    "always draws its slate at the top.")
        if n.vertical or n.badge:
            a.error(("--vertical" if n.vertical else "--badge")
                    + " has no meaning with --photos: a photograph is framed "
                    "16:9 and there is no geometry to rebuild for a tall "
                    "frame.")
    elif n.video:
        # the same RAW namespace rule, for the same reason
        try:
            slate_time = slate_clock(n.slate_time)
        except ValueError as e:
            a.error(f"--slate-time: {e}")
        err = _video_check(dict(vars(n), slate_time=slate_time,
                                stars_explicit=bool(n.stars),
                                looping=bool(n.audio)))
        if err:
            a.error(err)
        if n.vertical or n.badge:
            a.error(("--vertical" if n.vertical else "--badge")
                    + " has no meaning with --video: a clip is framed as it "
                    "was shot, and there is no geometry to rebuild for a tall "
                    "frame.")
        dead = [("--width", n.width, 2560), ("--height", n.height, 1440),
                ("--fps", n.fps, 10)]
        if not n.audio:
            # a bare clip render writes no still, so there is nothing for the
            # thumbnail flags to size or to pick a frame from
            dead.append(("--thumb-width", n.thumb_width, 1920))
        for flag, val, dflt in dead:
            if val != dflt:
                a.error(f"{flag} has no meaning with --video: the clip's own "
                        "resolution and frame rate are preserved exactly, and "
                        "nothing is resized or resampled.")
        if not n.audio:
            for flag, val, dflt in (("--slate-intro", n.slate_intro,
                                     str(SLATE_INTRO_DEFAULT)),
                                    ("--slate-outro", n.slate_outro,
                                     str(SLATE_OUTRO_DEFAULT)),
                                    ("--thumb-at", n.thumb_at, 0.0)):
                if val != dflt:
                    a.error(f"{flag} needs an audio file alongside --video: it "
                            "schedules the slate across a looped runtime, and "
                            "a bare clip render is one pass with the slate on "
                            "throughout.")
    else:
        # ...and the same rule the other way round. A photo flag on a generated
        # render would do nothing at all, and a flag that was typed and ignored
        # is only ever discovered by noticing it had no effect.
        for flag, val, dflt in (("--photo-interval", n.photo_interval,
                                 PHOTO_INTERVAL),
                                # None is "not typed" now that the default is
                                # settled per mode, so this reads the same test
                                # it always meant
                                ("--scrim", n.scrim, None),
                                ("--slate-scope", n.slate_scope, "both")):
            if val != dflt:
                a.error(f"{flag} needs --photos: it only means something for a "
                        "photo render. A generated render always carries its "
                        "slate, and has no photographs to wash behind it.")
        if n.badge and not n.vertical:
            a.error("--badge needs --vertical: the badge is drawn on the "
                    "vertical frame only.")
        if n.slate_position != SLATE_POSITIONS[0]:
            a.error("--slate-position needs --video: a generated render lays "
                    "its silhouette out around a slate at the top, and the "
                    "city style caps its towers under that same text.")
    cfg = vars(n)
    cfg["photos"] = photos
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
    cfg["style"] = n.style or presets_mod.series_style(n.series_key, P)
    if n.video:
        cfg["slate_time"] = slate_clock(n.slate_time)
    if not cfg["photos"] and not n.video:
        err = scene_mod.check(cfg["scene"], cfg["style"], n.rows, n.filled,
                              n.progress)
        if err:
            a.error(err)
    try:
        scene_mod.resolve_detail(n.detail)
    except ValueError as e:
        a.error(str(e))
    # On unless refused. --stars is kept as the way to ask by NAME: it is what
    # separates "no field here" from "no field, and here is why", which only
    # matters to someone who expected one - see run().
    # explicit FIRST: cfg is vars(n), so writing cfg["stars"] also writes
    # n.stars, and reading the flag afterwards would read what was just put
    # there rather than what was typed
    cfg["stars_explicit"] = bool(n.stars)
    cfg["stars"] = not n.no_stars
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
    if r.get("cover"):
        print(f"cover     : {r['cover']}")
    if r.get("vertical"):
        print(f"vertical  : {r['vertical']}")
    if r["video"]:
        print(f"video     : {r['video']}")


if __name__ == "__main__":
    main()
