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
import lss_photo as photo_mod
import lss_scene as scene_mod
from PIL import Image, ImageDraw, ImageFont, ImageStat

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
    """
    k = W / 1280.0
    dh = 1280.0 * H / W              # 720.0 at 16:9, 1280.0 square
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

    D.draw_slate(dr, cfg, W, k, dy,
                 format_number(cfg.get("number", ""),
                               cfg.get("number_style", "No.")),
                 time_text, fg, slate, FONT)
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


def _weather(cfg, db, dh=720.0):
    """This frame's weather, or None. Built per frame SHAPE, as the scene is:
    a square cover has a taller sky and gets more of it, not a stretched copy.
    """
    if (cfg.get("weather") or "off") == "off":
        return None
    return scene_mod.weather(db, state=cfg["weather"],
                             detail=cfg.get("detail", "Default"),
                             horizon_y=_horizon(cfg, dh), dh=dh)


def _clouds_only(wx):
    """The same weather with the rain taken out.

    For the twinkle probe, and only for it. A cloud genuinely covers a star and
    has to be in that test; a streak does not - it is drawn in FRONT of the
    silhouette, one design unit wide, and under STAR_CLEAR = 0 a streak
    clipping the corner of a star rect would disqualify a twinkler sitting in
    open sky. The probe therefore never sees rain, whatever this render draws.
    """
    return dict(wx, rain=None) if wx else wx


def slate_boxes(cfg, dh=720.0):
    """Every run of slate text as (x0, x1, ink_bottom), in design units.

    Measured with the real font at the real sizes rather than estimated from a
    character count, because that is the whole point: "PHOENIX" and "SOUTH
    MOUNTAIN PARK" leave wildly different amounts of the frame free, and so do
    a two-word conditions line and a six-word one.

    The slate is drawn in caps throughout, so a baseline IS the ink bottom -
    there is nothing below it to allow for. Positions mirror compose() exactly,
    at k=1; if the layout there moves, this has to move with it.
    """
    M = 84.0
    dy = dh / 2.0 - 360.0            # the same slate move compose() makes, so
    out = []                         # the ceiling is measured off the real text
    n = format_number(cfg.get("number", ""), cfg.get("number_style", "No."))
    nw = D.text_width(n, 27.0, 11.0, FONT) if n else 0.0
    # the same shrink-to-fit compose() applies, or a long series name would
    # reserve more width here than it actually occupies
    avail = 1280.0 - 2 * M - nw - (40.0 if n else 0.0)
    ssize, strack = 27.0, 11.0
    for _ in range(24):
        if D.text_width(cfg["series"], ssize, strack, FONT) <= avail:
            break
        ssize *= 0.94
        strack *= 0.94
    out.append((M, M + D.text_width(cfg["series"], ssize, strack, FONT), 150.0 + dy))
    if n:
        out.append((1280.0 - M - nw, 1280.0 - M, 150.0 + dy))
    out.append((M, M + D.text_width(cfg["place"], 104.0, 7.0, FONT), 296.0 + dy))
    cc = f'{cfg["city"]}  ·  {cfg["conditions"]}'
    out.append((M, M + D.text_width(cc, 31.0, 8.0, FONT), 360.0 + dy))
    # The clock. Reserved whatever this render is: the thumbnail draws it and
    # the video has ffmpeg draw it in the same place, so the column is spoken
    # for either way. Measured off a full-width sample rather than this
    # render's start time, because the video's clock runs all night.
    tw = D.text_width("00:00 PM", 31.0, 0.0, FONT)
    out.append((1280.0 - M - tw, 1280.0 - M, 360.0 + dy))
    return out


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


def _thumbnail(cfg, lv, tw, th, out, work, frac=1.0):
    """One thumbnail. Between 0 and 1, `frac` cuts the played and unplayed
    layers at a fixed x instead of a moving one - a mid-playback frame without
    an encode. The ends need only one layer, so they skip the composite."""
    if frac >= 1.0:                      # fully played: the accent layer IS it
        return compose(cfg, lv, tw, th, cfg["accent"], out,
                       time_text=cfg["start"], played=True)
    if frac > 0:
        b = compose(cfg, lv, tw, th, cfg["foreground"],
                    os.path.join(work, "_t_bone.png"), time_text=cfg["start"])
        c = compose(cfg, lv, tw, th, cfg["accent"],
                    os.path.join(work, "_t_clay.png"), time_text=cfg["start"],
                    played=True)
        return D.progress_composite(b, c, frac, out)
    return compose(cfg, lv, tw, th, cfg["foreground"], out,
                   time_text=cfg["start"])


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
                   time_text=cfg["start"], played=True, save=D.png_meta())


SCRIM_DEFAULT = 0.65             # how heavy the wash behind the slate is on a
                                 # photo. Set from the worst case rather than
                                 # by eye: a near-white hazy sky under the
                                 # Night palette measures 1.01:1 bare, 2.69 at
                                 # 0.45 and 3.52 at 0.55, and first clears the
                                 # 4.5:1 the small slate lines want at 0.65.
                                 # A dark photo is barely touched by it - the
                                 # wash is the palette's own sky - so the cost
                                 # of setting it for the hard case is small


def _slate_names(cfg):
    """The slate's lines, in the order slate_boxes() returns them."""
    n = format_number(cfg.get("number", ""), cfg.get("number_style", "No."))
    return (["series"] + (["number"] if n else [])
            + ["place", "city · conditions", "time"])


def _slate_legibility(img, cfg, W, H):
    """The weakest contrast between slate ink and the photo under it.

    Reported rather than enforced. The colour presets were chosen against
    measured contrast on rendered frames and a photograph answers to nothing,
    so this is the only place the number can be known at all - and the fix is
    usually --scrim rather than a different palette, which is a judgement the
    render cannot make on its own.

    Measured on the FINISHED frame, so the scrim is included: what is wanted is
    the contrast the viewer gets, not the one the bare photo had.
    """
    k = W / 1280.0
    boxes = slate_boxes(cfg, dh=1280.0 * H / W)
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
            time_text, fg, slate, FONT)
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
    k = W / 1280.0
    size = int(round(31 * k))
    baseline = (360 + (1280.0 * H / W) / 2.0 - 360.0) * k
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
        f":x=(w-tw-{rm}):y={cy}:text='%{{pts\\:gmtime\\:{ep}\\:%I\\\\\\:%M %p}}'"
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


# ----------------------------------------------------------------- driver
def _sidecar(cfg, lv, dur, scale, dyn, n, variants=None, cover=None,
             photos=None):
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
        "photos": photos,
        "variants": variants or [],
        "source_audio": os.path.basename(cfg.get("audio", "")),
        "duration_s": dur,
        "levels": lv,
    }


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

    interval = float(cfg.get("photo_interval", photo_mod.DEFAULT_INTERVAL))
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
    tt = cfg["start"] if still_slate else None
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
    # ...and the sky's own colour with them, from whichever of the three the
    # palette names. An error rather than a silent no-op, for the reason
    # scene_mod.check() gives: the alternative is finding out after the encode.
    if cfg.get("photos"):
        cfg["stars"] = False          # nothing to stand behind: the photo IS
                                      # the whole background
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
    if cfg.get("photos"):
        t = _photo_targets(cfg)
        progress("Checking photos against "
                 + ", ".join(f"{n} {w}x{h}" for n, w, h in t) + "…")
        cfg["_photos_ok"] = photo_mod.validate(cfg["photos"], t)

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

    if cfg.get("thumb_only"):
        json.dump(_sidecar(cfg, lv, dur, scale, dyn, n, variants, cover),
                  open(os.path.join(outdir, f"{slug}_render.json"), "w"), indent=2)
        shutil.rmtree(work, ignore_errors=True)
        if on_progress:
            on_progress(1.0, 0)
        progress("Done (thumbnail only).")
        return {"thumbnail": thumbs[0], "thumbnails": thumbs, "video": None,
                "cover": cover, "duration": dur, "folder": outdir}

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

    json.dump(_sidecar(cfg, lv, dur, scale, dyn, n, None, cover),
              open(os.path.join(outdir, f"{slug}_render.json"), "w"), indent=2)
    shutil.rmtree(work, ignore_errors=True)
    progress("Done.")
    return {"thumbnail": thumbs[0], "thumbnails": thumbs, "video": vid,
            "cover": cover, "duration": dur, "folder": outdir}


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
                   default=photo_mod.DEFAULT_INTERVAL, metavar="SECONDS",
                   help=f"how long each photo holds before the next "
                        f"(default {photo_mod.DEFAULT_INTERVAL:g}). The cycle "
                        "repeats until the audio is covered and the last "
                        "segment is cut to the audio's end")
    g.add_argument("--scrim", type=float, default=SCRIM_DEFAULT,
                   metavar="0-1",
                   help=f"how heavy the wash behind the slate is on a photo "
                        f"(default {SCRIM_DEFAULT}, 0 turns it off). A "
                        "photograph puts arbitrary luminance under the text "
                        "where a palette never would; this is what replaces "
                        "that guarantee. Painted in the background colour, "
                        "faded out below the slate's own lowest ink")

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
    g.add_argument("--slate-scope", default="both",
                   choices=photo_mod.SLATE_SCOPES,
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
    photos = photo_mod.collect(n.photos)
    if photos:
        # read off the RAW namespace, before any series default is written
        # back over a flag - otherwise every render would look as though it
        # had asked for a style by name
        err = photo_mod.check(dict(vars(n), photos=photos,
                                   stars_explicit=bool(n.stars)))
        if err:
            a.error(err)
    else:
        # ...and the same rule the other way round. A photo flag on a generated
        # render would do nothing at all, and a flag that was typed and ignored
        # is only ever discovered by noticing it had no effect.
        for flag, val, dflt in (("--photo-interval", n.photo_interval,
                                 photo_mod.DEFAULT_INTERVAL),
                                ("--scrim", n.scrim, SCRIM_DEFAULT),
                                ("--slate-scope", n.slate_scope, "both")):
            if val != dflt:
                a.error(f"{flag} needs --photos: it only means something for a "
                        "photo render. A generated render always carries its "
                        "slate, and has no photographs to wash behind it.")
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
    if not cfg["photos"]:
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
    if r["video"]:
        print(f"video     : {r['video']}")


if __name__ == "__main__":
    main()
