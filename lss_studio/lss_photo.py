#!/usr/bin/env python3
"""
Photo backgrounds - a supplied set of stills in place of the generated skyline.

Nothing in here reads the audio. A photo render has no envelope, no levels and
no geometry: the recording decides only how LONG the cycle runs, and the stills
decide what is on screen. That is the whole reason this is a mode rather than a
--style - every style in lss_scene is a shape derived from the loudness, and
there is no shape here to derive.

Photos are normalised to the output, never the output to the photos: EXIF
-rotated, converted to sRGB, centre-cropped to the frame's aspect and LANCZOS
-downscaled. An input too small for the frame is an ERROR rather than an
upscale, for the reason lss_scene.check() gives about impossible combinations -
the alternative is a soft render that nobody notices until it is published.

The cycle is planned as SEGMENTS, and the plan is what makes a photo video
cheap to encode: a segment holds one still for the whole interval, so N stills
on a fixed cycle have only N distinct segments however long the recording runs.
See lss_render.build_photo_video.
"""

import io
import os

from PIL import Image, ImageCms, ImageOps

DEFAULT_INTERVAL = 180.0         # seconds a photo holds before the next one
MIN_INTERVAL = 1.0

# What the slate is drawn onto, and where. 'both' is the slate on the thumbnail
# and on the video; 'thumbnail' leaves the video bare; 'none' leaves both bare.
SLATE_SCOPES = ["both", "thumbnail", "none"]

# Read by Pillow and worth offering. Deliberately not everything Pillow opens -
# a folder of stills should not sweep up a stray .gif or an .ico.
EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp"}


def collect(spec):
    """Ordered photo paths from a comma-separated list or a single directory.

    A directory sorts by name, which is what a camera's own filenames already
    encode; a list is used in the order it was typed. A folder is the likely
    real input - forty stills do not go on a Windows command line - so it is
    accepted wherever a list is.
    """
    spec = (spec or "").strip()
    if not spec:
        return []
    if os.path.isdir(spec):
        return [os.path.join(spec, f) for f in sorted(os.listdir(spec))
                if os.path.splitext(f)[1].lower() in EXTS]
    return [p.strip() for p in spec.split(",") if p.strip()]


def _to_srgb(im):
    """Convert to sRGB when the file says it is something else.

    Returns (image, note). A photo carrying Adobe RGB or Display P3 renders
    desaturated if its numbers are read as sRGB, and phone and camera exports
    carry one or the other constantly. An unreadable profile is not fatal -
    assuming sRGB is exactly what ignoring the profile would have done anyway -
    but it IS reported, because a wrong-looking render should say why.
    """
    icc = im.info.get("icc_profile")
    if not icc:
        return im.convert("RGB"), ""
    try:
        src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
        name = ImageCms.getProfileDescription(src).strip()
        if name.lower().startswith("srgb"):
            return im.convert("RGB"), ""
        out = ImageCms.profileToProfile(
            im, src, ImageCms.createProfile("sRGB"), outputMode="RGB")
        return out, f"{name} -> sRGB"
    except Exception as e:
        return im.convert("RGB"), f"unreadable colour profile ({e}), assumed sRGB"


def load(path):
    """One photo, upright and in sRGB. Returns (image, note).

    exif_transpose FIRST: Pillow does not apply the orientation tag on its own,
    so a phone photo arrives on its side and every measurement taken off it -
    the crop, the size check - would be measured on the wrong axis.
    """
    try:
        im = Image.open(path)
    except Exception as e:
        raise SystemExit(f"--photos: cannot read {path}\n  {e}")
    im = ImageOps.exif_transpose(im)
    return _to_srgb(im)


def crop_box(w, h, aspect):
    """The centred rectangle of (w, h) with the given width/height ratio."""
    if w / float(h) > aspect:                 # too wide: trim the sides
        cw, ch = h * aspect, float(h)
    else:                                     # too tall: trim top and bottom
        cw, ch = float(w), w / aspect
    x = (w - cw) / 2.0
    y = (h - ch) / 2.0
    return (int(round(x)), int(round(y)),
            int(round(x + cw)), int(round(y + ch)))


def _fits(w, h, W, H):
    """Does (w, h) survive a centre-crop to W:H without being upscaled?"""
    x0, y0, x1, y1 = crop_box(w, h, W / float(H))
    return (x1 - x0) >= W and (y1 - y0) >= H, (x1 - x0, y1 - y0)


def validate(paths, targets):
    """Reject undersized photos up front, listing every one that fails.

    `targets` is [(label, W, H), ...] - every output this render will actually
    write, because they do not ask the same thing. The video is 2560x1440, the
    thumbnail 1920x1080 and a Spotify cover is a 3000x3000 SQUARE, which needs
    3000px on the short edge and so refuses most photos that clear the other
    two. Checked together and reported together: finding out about the cover
    after the video has already encoded would be the same fault the
    no-silent-upscale rule exists to prevent.

    Returns [(path, note)] for the photos that pass, so the caller does not
    open every file a second time.
    """
    if not paths:
        raise SystemExit("--photos: no photos given.")
    ok, bad = [], []
    for p in paths:
        if not os.path.exists(p):
            bad.append(f"  {p}\n      file not found")
            continue
        im, note = load(p)
        w, h = im.size
        im.close()
        fails = []
        for label, W, H in targets:
            good, (cw, ch) = _fits(w, h, W, H)
            if not good:
                fails.append(f"{label} needs {W}x{H}, crop gives {cw}x{ch}")
        if fails:
            bad.append(f"  {os.path.basename(p)}  ({w}x{h})\n      "
                       + "\n      ".join(fails))
        else:
            ok.append((p, note))
    if bad:
        raise SystemExit(
            "--photos: these are too small for this render, and photos are "
            "never upscaled.\n" + "\n".join(bad)
            + "\n\n  Use larger originals, or drop --cover / lower --width "
              "so the frame asks for less.")
    return ok


def frame(path, W, H):
    """One photo as a W x H frame: upright, sRGB, centre-cropped, downscaled.

    Returned at the OUTPUT size, not at the supersampled size everything else
    in lss_draw is drawn at. That is deliberate and it is the no-upscale rule
    again: drawing this at SS would mean resampling the photo to twice the
    frame, which is precisely the interpolation the rule refuses. The slate is
    supersampled on its own layer instead and composited down onto this - see
    lss_draw.slate_layer.
    """
    im, _ = load(path)
    im = im.crop(crop_box(im.width, im.height, W / float(H)))
    if im.size != (W, H):
        im = im.resize((W, H), Image.LANCZOS)
    return im


def plan(dur, n, interval=DEFAULT_INTERVAL):
    """The cycle as segments, and the distinct ones among them.

    Returns (segments, unique). A segment is
        {"slot": i, "photo": p, "start": t, "dur": d, "key": k}
    and `unique` maps each key to the (photo, dur) that has to be encoded once.

    The key is what the whole encode strategy rests on: a full-length segment
    of photo p is identical to every other full-length segment of photo p, so
    they share a key and are encoded once between them. Only the final segment
    differs - it is truncated to the audio's end - and it gets a key of its own.
    Four photos over three hours are therefore four encodes and, when the run
    time is not an exact multiple of the interval, one short fifth.
    """
    if dur <= 0:
        raise SystemExit("--photos: the audio has no duration to cover.")
    if interval < MIN_INTERVAL:
        raise SystemExit(f"--photo-interval: {interval}s is too short; "
                         f"the minimum is {MIN_INTERVAL:g}s.")
    segs, unique = [], {}
    t, i = 0.0, 0
    while t < dur - 1e-6:
        d = min(interval, dur - t)
        p = i % n
        # a truncated tail is its own segment; a full one is shared by name
        key = f"p{p}" if abs(d - interval) < 1e-6 else f"p{p}_t{d:.3f}"
        segs.append({"slot": i, "photo": p, "start": t, "dur": d, "key": key})
        unique.setdefault(key, {"photo": p, "dur": d})
        t += d
        i += 1
    return segs, unique


def check(cfg):
    """Reject a photo render asking for something photo mode cannot mean.

    An error rather than a quiet no-op, for the reason lss_scene.check() gives:
    a flag that was typed and ignored is only discovered by noticing it did
    nothing, which on a three-hour render is after the encode.

    Everything listed here is audio-derived geometry or the playhead that
    reveals it. A photo render has neither: there is no silhouette to shape and
    no accent sweeping a timeline, so there is nothing for these to decide.
    """
    dead = [
        ("style", "--style", "a photo is not a generated silhouette"),
        ("scale", "--scale", "nothing here is derived from loudness"),
        ("dynamics", "--dynamics", "nothing here is derived from loudness"),
        ("towers", "--towers", "there are no towers to size"),
        ("detail", "--detail", "there is no generated detail to count"),
        ("weather", "--weather", "the photo carries its own weather"),
    ]
    for key, flag, why in dead:
        v = cfg.get(key)
        if v and v not in ("off", "Default", "Skyline (rank)", "More"):
            return f"{flag} has no meaning with --photos: {why}."
    if cfg.get("rows", 1) > 1:
        return ("--rows has no meaning with --photos: a photo is one frame, "
                "not stacked envelope rows.")
    if cfg.get("filled"):
        return "--filled has no meaning with --photos: there is no silhouette to fill."
    if cfg.get("stars_explicit"):
        return ("--stars has no meaning with --photos: the star field is drawn "
                "behind a silhouette, and the photo is the whole background.")
    if cfg.get("variants"):
        return ("--variants has no meaning with --photos: the palette only "
                "reaches the slate text, so every variant would differ in the "
                "text colour alone.")
    p = cfg.get("progress", 1.0)
    if p is not None and float(p) not in (0.0, 1.0):
        return ("--progress has no meaning with --photos: there is no playhead "
                "to draw part-way along.")
    scope = cfg.get("slate_scope", "both")
    if scope not in SLATE_SCOPES:
        return (f"--slate-scope '{scope}' is not one of: "
                + ", ".join(SLATE_SCOPES) + ".")
    return None
