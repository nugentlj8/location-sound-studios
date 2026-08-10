"""Generative silhouette geometry - the loudness envelope becomes shapes.

Everything here is built ONCE per render, in 1280x720 design units, and scaled
by k = W/1280 when it is drawn. The thumbnail, the video frames and the two
playback layers are therefore one shape at four sizes rather than four separate
derivations of it - which is what makes a 1920 thumbnail and a 2560 frame read
identically instead of merely similarly.

Randomness is seeded from the envelope, so a recording always renders the same
way, and the seed is written to the render sidecar.
"""

import hashlib
import math
import numpy as np

DW, DH = 1280.0, 720.0           # the design basis, shared with compose()

LO_DB, HI_DB = -60.0, -6.0       # fixed-scale window
DYNAMICS = {"Natural": (5, 95), "More": (12, 88), "Most": (20, 80)}
SKYGAMMA = {"Natural": 1.2, "More": 1.6, "Most": 2.2}

# Which silhouettes each scene can wear. The first entry is that scene's
# default, i.e. the behaviour that predates styles existing at all.
SCENE_STYLES = {"nature": ["topo", "mountains", "forest", "mountains_forest"],
                "town": ["blocks", "houses"]}
DEFAULT_STYLE = {s: v[0] for s, v in SCENE_STYLES.items()}
# styles drawn by this module. The other two are the original envelope line.
SILHOUETTE = {"mountains", "forest", "mountains_forest", "houses"}

# Detail multiplies feature COUNT, never feature size, and is deliberately
# independent of output width - see the module docstring.
DETAIL = {"Coarse": 0.70, "Default": 1.00, "Fine": 1.45}
DETAIL_RANGE = (0.40, 2.50)

# First entry is the default in both cases.
TREE_AHEAD = ["faint", "outline"]       # how an unplayed tree is drawn
MOUNTAIN_FACE = ["twotone", "outline"]  # whether the faces carry a flat tone
# ...and the styles each one actually reaches, so a caller can say so rather
# than offer a control that would do nothing. A town's street trees are drawn
# with the houses, not on the forest's schedule, so TREE_AHEAD misses them.
TREE_AHEAD_STYLES = {"forest", "mountains_forest"}
MOUNTAIN_FACE_STYLES = {"mountains", "mountains_forest"}

# --- vertical layout, design units -----------------------------------------
# The slate's "CITY . CONDITIONS" baseline sits at 360, i.e. half the frame, so
# nothing here may rise above ~389 or the silhouette collides with the text.
GROUND = 690.0                   # where tree trunks stand
MTN_BASE = 726.0                 # just past the bottom edge, so the range runs
                                 # off the frame instead of floating on a strip
                                 # of sky the trees then stand in
MTN_TOP_MIN = 500.0              # the quietest recording's tallest summit
MTN_TOP_MAX = 392.0              # the loudest recording's tallest summit
# rise over run for a flank. A mountain's WIDTH follows from its height and one
# of these, rather than from the gap to its neighbour - deriving width from the
# gap is what turns a sparse range into shallow zigzag lines.
MTN_SLOPE = (0.85, 1.30)
MTN_FILL = 0.62                  # ...but never narrower than this much of the
                                 # way to the next summit, or sky opens up
                                 # underneath between two peaks
TOWN_BASE = 669.6                # 0.930 DH - the block baseline, unchanged

TREE_SPACING = 58.0              # design units between trunks at Default
TREE_H = 0.135 * DH              # tree height is pinned to the FRAME, so a
TREE_W = 0.62                    # quiet recording still gets a normal treeline
FOREST_H = 1.50                  # ...but with no mountains behind them the
                                 # trees have to hold the frame on their own

HOUSE_TOWER_PCT = 97.0           # only this loud a block earns a tall tower
HOUSE_TOWER_AMP = 0.70           # and it is a taller BLOCK, not a skyscraper -
                                 # a full-height tower over a village reads as
                                 # a different drawing, not a loud moment
HOUSE_N = 16                     # houses across the frame at Default detail.
                                 # A house has to be wider than it is tall or
                                 # the row reads as a picket fence, and at the
                                 # 40-odd blocks --towers gives a city there is
                                 # no width to spend - so houses get their own,
                                 # coarser grid and --towers keeps meaning only
                                 # what it always did, for blocks.
HOUSE_BODY = 0.62                # body height as a fraction of house width
TOWN_TREE_P = 0.28               # chance of a street tree between two houses
TOWN_TREE_H = 0.155 * DH         # taller than the rooflines. A street tree the
                                 # same height as a house disappears into it
                                 # once both are filled in the same colour, so
                                 # it has to clear the roof to read at all.
                                 # Like the houses, it does not answer to the
                                 # audio.


def seed_from(db):
    """A stable seed for one recording.

    Hashing the envelope rather than the file means a 2 GB flac is not read
    twice, and it guarantees the thumbnail and the video agree, since both are
    derived from this same array.
    """
    q = np.round(np.asarray(db, dtype=np.float64) * 10.0).astype(np.int32)
    return int.from_bytes(hashlib.blake2b(q.tobytes(), digest_size=8).digest(),
                          "big")


def resolve_detail(v):
    """A named preset or a bare number, as a multiplier."""
    if isinstance(v, (int, float)):
        f = float(v)
    else:
        s = str(v or "Default").strip()
        if s in DETAIL:
            return DETAIL[s]
        try:
            f = float(s)
        except ValueError:
            raise ValueError(
                f"--detail: '{s}' is neither a preset ({', '.join(DETAIL)}) "
                "nor a number like 1.2")
    lo, hi = DETAIL_RANGE
    if not lo <= f <= hi:
        raise ValueError(f"--detail: {f:g} is outside {lo:g}-{hi:g}")
    return f


# ----------------------------------------------------------------- envelope
def map_db(d, scale="Skyline (rank)", dynamics="More"):
    """dB values to 0..1. The single place loudness becomes height, shared by
    the towers, the summits and the treeline so every style answers to the same
    --scale and --dynamics."""
    d = np.asarray(d, dtype=np.float64)
    if scale == "Skyline (rank)":
        rank = d.argsort().argsort() / max(1, len(d) - 1)
        return rank ** SKYGAMMA.get(dynamics, 1.6)
    if scale == "Fixed loudness":
        return np.clip((d - LO_DB) / (HI_DB - LO_DB), 0, 1)
    p = DYNAMICS.get(dynamics, DYNAMICS["More"])
    lo, hi = float(np.percentile(d, p[0])), float(np.percentile(d, p[1]))
    if hi - lo < 3.0:
        mid = (hi + lo) / 2.0
        lo, hi = mid - 1.5, mid + 1.5
    return np.clip((d - lo) / (hi - lo), 0, 1)


def bin_peak(db, n, pct=95):
    """Reduce the envelope to n bins, each holding its own loud moment.

    The 95th percentile rather than the mean, for the same reason the towers
    use it: a siren inside a bin should raise the terrain, not be averaged into
    the bed around it.
    """
    db = np.asarray(db, dtype=np.float64)
    if len(db) <= n:
        return np.interp(np.linspace(0, len(db) - 1, n), np.arange(len(db)), db)
    e = np.linspace(0, len(db), n + 1).astype(int)
    return np.array([np.percentile(db[e[i]:max(e[i] + 1, e[i + 1])], pct)
                     for i in range(n)])


def smooth(y, sigma, passes=2):
    """Gaussian along a 1-D series, edge-padded so the ends do not sag.

    Two passes, not one: at the sigma a ridgeline wants, a single pass still
    leaves shoulder wobble, and that wobble shows up as visible kinks along a
    long straight flank - the exact artefact that makes a mountain read as a
    waveform instead.
    """
    y = np.asarray(y, dtype=np.float64)
    if sigma <= 0:
        return y.copy()
    r = max(1, int(round(sigma * 3)))
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    k /= k.sum()
    out = y
    for _ in range(passes):
        out = np.convolve(np.pad(out, r, mode="edge"), k, mode="valid")
    return out


def pick_peaks(y, min_sep, lo, hi):
    """Local maxima, taken tallest first, each one blocking a zone around it.

    One vertex per time bucket would make an N-gon whose apparent shape is set
    by N. Picking maxima with an exclusion zone instead lets the NUMBER of
    summits follow the recording's structure and their POSITIONS follow where
    the loud passages actually are, so a loud stretch 40% of the way in puts a
    summit 40% of the way across.
    """
    n = len(y)
    if n < 3:
        return [0] if n else []
    cand = [i for i in range(1, n - 1) if y[i] >= y[i - 1] and y[i] > y[i + 1]]
    if y[0] > y[1]:
        cand.append(0)
    if y[-1] > y[-2]:
        cand.append(n - 1)
    cand.sort(key=lambda i: -y[i])
    picked = []
    for i in cand:
        if len(picked) >= hi:
            break
        if all(abs(i - j) >= min_sep for j in picked):
            picked.append(i)
    # A flat recording yields almost no maxima. Split the widest empty stretch
    # at its own high point until a RANGE exists, rather than one lump.
    while len(picked) < lo:
        b = [-min_sep] + sorted(picked) + [n - 1 + min_sep]
        span, widest = 0, None
        for a, c in zip(b, b[1:]):
            if c - a > span:
                span, widest = c - a, (a, c)
        if widest is None or span < 2 * min_sep:
            break
        s, e = max(0, widest[0] + min_sep), min(n, widest[1] - min_sep + 1)
        if e <= s:
            break
        picked.append(int(s + np.argmax(y[s:e])))
    return sorted(picked)


# ----------------------------------------------------------------- helpers
def _monotone(pts, sx, ytop, ybot):
    """Force x to keep travelling in direction sx, and keep y inside the peak.

    Only x is constrained, so a flank may rise again into a sub-peak and still
    be a simple polygon - two flanks that each march monotonically away from
    the apex cannot cross each other whatever they do vertically.
    """
    out = []
    for x, y in pts:
        y = min(max(y, ytop), ybot)
        if out:
            x = max(x, out[-1][0]) if sx > 0 else min(x, out[-1][0])
        out.append((x, y))
    return out


def _flank(rng, apex, base, segs):
    """Apex to base as a few straight runs broken by shelves and shoulders.

    Without these a peak is a clean triangle, which reads as a pictogram. The
    reference's slopes step and jog on the way down, and it is that faceting -
    not the outline - that makes flat colour look like rock.
    """
    ax, ay = apex
    bx, by = base
    dx, dy = bx - ax, by - ay
    pts = [apex]
    for t in sorted(rng.uniform(0.12, 0.92, max(0, segs - 1))):
        # the sideways jitter stays well under the step between vertices: any
        # larger and the monotone clamp starts firing, which turns a kink into
        # a long horizontal run or a vertical drop - reads as a staircase, not
        # as rock, and it showed up badly on wide flanks
        x = ax + dx * t + rng.uniform(-0.045, 0.045) * abs(dx)
        y = ay + dy * (t + rng.uniform(-0.04, 0.04))
        pts.append((x, y))
        r = rng.random()
        if r < 0.32:                                 # a shelf cut in the slope
            pts.append((x + rng.uniform(0.05, 0.10) * dx,
                        y + rng.uniform(0.0, 0.02) * dy))
        elif r < 0.52:                               # a shoulder that rises
            pts.append((x + rng.uniform(0.05, 0.10) * dx,
                        y - rng.uniform(0.025, 0.055) * dy))
    pts.append(base)
    return _monotone(pts, 1.0 if dx >= 0 else -1.0, ay, by)


def _truncate(pts, frac):
    """The first `frac` of a polyline, by length."""
    seg = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:])]
    want = sum(seg) * frac
    out, run = [pts[0]], 0.0
    for (a, b), d in zip(zip(pts, pts[1:]), seg):
        if run + d >= want:
            t = (want - run) / d if d else 0.0
            out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
            break
        out.append(b)
        run += d
    return out


# ----------------------------------------------------------------- mountains
def _mountains(db, detail, scale, dynamics, rng):
    """Summits from the envelope, flanks and faces from the summits."""
    n = max(48, int(round(160 * detail)))
    prof = smooth(bin_peak(db, n), n / 22.0, passes=2)
    # Few and large. A steep flank needs roughly its own height in width, so
    # more than about five summits across 1280 units can only be short ones.
    lo = max(3, int(round(3 * detail)))
    hi = max(lo, int(round(5 * detail)))
    idx = pick_peaks(prof, max(2, int(round(n / 9.0))), lo, hi)
    if not idx:
        return []
    hgt = map_db(prof[idx], scale, dynamics)
    span = DW + 120.0

    def X(i):
        return -60.0 + (i + 0.5) * span / n

    def summit(h):
        return MTN_TOP_MIN - float(h) * (MTN_TOP_MIN - MTN_TOP_MAX)

    peaks = [{"x": X(i), "y": summit(h)} for i, h in zip(idx, hgt)]
    # A summit just past each edge, so the range continues off-frame at a real
    # slope. Stretching the outermost real peak to the border instead gives it
    # a long shallow tail, which is the one thing that still reads as waveform.
    # These are a framing device, not a data channel: only an inner flank shows.
    peaks.insert(0, {"x": -190.0,
                     "y": summit(float(hgt[0]) * rng.uniform(0.55, 0.80))})
    peaks.append({"x": DW + 190.0,
                  "y": summit(float(hgt[-1]) * rng.uniform(0.55, 0.80))})

    out = []
    for j, p in enumerate(peaks):
        h = MTN_BASE - p["y"]
        dl = (p["x"] - peaks[j - 1]["x"]) if j else 520.0
        dr = (peaks[j + 1]["x"] - p["x"]) if j < len(peaks) - 1 else 520.0
        # asymmetric on purpose - two identical flanks read as a pictogram
        lx = p["x"] - max(h / rng.uniform(*MTN_SLOPE), MTN_FILL * dl)
        rx = p["x"] + max(h / rng.uniform(*MTN_SLOPE), MTN_FILL * dr)
        apex = (p["x"], p["y"])
        left = _flank(rng, apex, (lx, MTN_BASE), int(rng.integers(3, 7)))
        right = _flank(rng, apex, (rx, MTN_BASE), int(rng.integers(3, 7)))
        # the lit/shadow boundary. Light comes from the right, so the crease
        # falls down the right of the summit and the big face is the shadow.
        cx = p["x"] + (rx - p["x"]) * rng.uniform(0.34, 0.50)
        crease = _flank(rng, apex, (cx, MTN_BASE), int(rng.integers(2, 5)))
        out.append({
            "poly": list(reversed(left)) + right[1:] + [(lx, MTN_BASE)],
            "crease": crease,
            # stroked instead of the full crease when there is no fill to
            # divide: a line running the whole height with nothing either side
            # of it reads as a stray diagonal, a spur off the summit does not
            "spur": _truncate(crease, rng.uniform(0.28, 0.48)),
            "shadow": list(reversed(left)) + crease[1:] + [(lx, MTN_BASE)],
            "lit": crease + [(rx, MTN_BASE)] + list(reversed(right))[1:],
            "y": p["y"],
            "cx": p["x"],
        })
    # tallest first, so the shorter peaks in front paint over it
    out.sort(key=lambda m: m["y"])
    return out


# ----------------------------------------------------------------- trees
def _tree_poly(rng, x, base, h, tiers):
    """One evergreen: stacked triangular tiers over a short trunk, built as a
    single closed path so it can be stroked or filled as one shape."""
    w = h * TREE_W * rng.uniform(0.90, 1.10) / 2.0
    trunk_h = h * 0.10
    body = h - trunk_h
    right = []
    for i in range(1, tiers + 1):
        y = base - trunk_h - body * (tiers - i) / tiers
        hw = w * (i / tiers) ** 0.82
        right.append((x + hw, y))                    # flare out
        if i < tiers:
            right.append((x + hw * 0.45, y))         # step back in
    tw = w * 0.12
    right.append((x + tw, base - trunk_h))
    right.append((x + tw, base))
    left = [(2 * x - px, py) for px, py in reversed(right)]
    return [(x, base - h)] + right + left


def _trees(db, detail, scale, dynamics, rng, vary):
    """Even spacing, small deterministic variation, size pinned to the frame.

    Trees do not breathe with the recording - the mountains do - so in
    mountains/mountains_forest their height is independent of level. `forest`
    has nothing else carrying the signal, so there `vary` lets level move the
    height by a modest amount and add a tier.
    """
    spacing = TREE_SPACING / detail
    n = max(6, int(round((DW + 120.0) / spacing)))
    lvl = None
    if vary:
        lvl = map_db(smooth(bin_peak(db, n), max(1.0, n / 30.0), passes=1),
                     scale, dynamics)
    out = []
    for i in range(n):
        x = -60.0 + (i + 0.5) * spacing + rng.uniform(-0.30, 0.30) * spacing
        base = GROUND + rng.uniform(-0.015, 0.015) * DH
        h = TREE_H * rng.uniform(0.80, 1.22)
        tiers = int(rng.integers(3, 5))
        if lvl is not None:
            # forest carries the whole frame, so the treeline is grown to fill
            # it rather than sitting in a band along the bottom
            h *= FOREST_H
            # the loudness range is deliberately narrow: wider and a treeline
            # becomes a bar chart with tree-shaped bars
            h *= 1.0 + 0.35 * (2.0 * float(lvl[i]) - 1.0)
            tiers = 3 + int(round(2.0 * float(lvl[i])))
        out.append({"poly": _tree_poly(rng, x, base, h, tiers),
                    "y": base, "cx": x})
    out.sort(key=lambda t: t["y"])                   # far ones first
    return out


# ----------------------------------------------------------------- houses
def _roof(rng, x0, x1, top):
    """A roofline over a house body, as the points from left eave to right."""
    w = x1 - x0
    # weighted toward the pitched rooflines: a flat box carries no information
    # at thumbnail size, and a row of them is indistinguishable from blocks
    kind = rng.choice(["gable", "hip", "shed", "gambrel", "flat"],
                      p=[0.34, 0.24, 0.16, 0.14, 0.12])
    if kind == "gable":
        return [(x0, top), ((x0 + x1) / 2.0, top - w * 0.50), (x1, top)], kind
    if kind == "hip":
        return [(x0, top), (x0 + w * 0.26, top - w * 0.38),
                (x1 - w * 0.26, top - w * 0.38), (x1, top)], kind
    if kind == "shed":
        lo, hi = (top, top - w * 0.34) if rng.random() < 0.5 else \
                 (top - w * 0.34, top)
        return [(x0, lo), (x1, hi)], kind
    if kind == "gambrel":
        return [(x0, top), (x0 + w * 0.16, top - w * 0.26),
                ((x0 + x1) / 2.0, top - w * 0.46),
                (x1 - w * 0.16, top - w * 0.26), (x1, top)], kind
    lip = w * 0.10
    return [(x0, top), (x0, top - lip), (x1, top - lip), (x1, top)], kind


def _houses(lv, detail, rng):
    """Mostly low houses, with a tall block kept for the loudest few percent so
    the skyline stays residential."""
    lv = np.asarray(lv, dtype=np.float64)
    n = max(8, int(round(HOUSE_N * detail)))
    # Regroup the block levels onto the coarser house grid, keeping each
    # group's LOUDEST block - so the onset alignment already baked into lv
    # survives the change of resolution and a siren still lands on the house
    # that was playing when it happened.
    if len(lv) <= n:
        hl = np.interp(np.linspace(0, len(lv) - 1, n), np.arange(len(lv)), lv)
    else:
        e = np.linspace(0, len(lv), n + 1).astype(int)
        hl = np.array([lv[e[i]:max(e[i] + 1, e[i + 1])].max() for i in range(n)])
    w = (DW + 60.0) / n
    cut = float(np.percentile(hl, HOUSE_TOWER_PCT))
    amp = 0.218 * DH * HOUSE_TOWER_AMP
    y0 = TOWN_BASE - amp * 0.45
    t = np.clip((hl + 0.45) / 1.40, 0.0, 1.0)        # level to 0..1
    out = []
    for i, v in enumerate(hl):
        x0 = -30.0 + i * w
        gap = w * 0.09
        a, b = x0 + gap / 2.0, x0 + w - gap / 2.0
        hw = b - a
        if v >= cut:                                 # the rare tall one
            top = y0 - amp * float(v)
            out.append({"poly": [(a, TOWN_BASE), (a, top), (b, top),
                                 (b, TOWN_BASE)],
                        "tower": True, "cx": (a + b) / 2.0})
            continue
        # proportion the body to its own width, so a house stays house-shaped
        # however many of them there are, and let level move it only a little
        body = min(0.13 * DH, max(0.045 * DH,
                                  hw * HOUSE_BODY * (0.80 + 0.40 * float(t[i]))))
        top = TOWN_BASE - body
        roof, kind = _roof(rng, a, b, top)
        poly = [(a, TOWN_BASE)] + roof + [(b, TOWN_BASE)]
        h = {"poly": poly, "tower": False, "cx": (a + b) / 2.0}
        if kind in ("gable", "hip") and rng.random() < 0.35:
            cxx = a + hw * rng.uniform(0.62, 0.78)
            cw = hw * 0.13
            # a separate shape, not a notch in the outline - a chimney has to
            # clear whatever the roof does at that x, so measure the roof
            ctop = min(y for _, y in roof) - hw * rng.uniform(0.08, 0.16)
            h["chimney"] = [(cxx, TOWN_BASE), (cxx, ctop),
                            (cxx + cw, ctop), (cxx + cw, TOWN_BASE)]
        out.append(h)

    # a street tree here and there, standing in the gaps between houses. They
    # follow the houses' fill rather than the forest's, because in a town the
    # houses are what the playhead recolours - trees filling on their own
    # schedule would compete with that read.
    trees = []
    for i in range(1, n):
        if rng.random() >= TOWN_TREE_P:
            continue
        x = -30.0 + i * w + rng.uniform(-0.14, 0.14) * w
        trees.append({"poly": _tree_poly(rng, x, TOWN_BASE,
                                         TOWN_TREE_H * rng.uniform(0.82, 1.18),
                                         int(rng.integers(3, 5))),
                      "cx": x})
    return out, trees


# ----------------------------------------------------------------- entry
def check(scene, style, rows=1, filled=False, progress=0.0):
    """Reject an impossible combination up front, saying what IS possible.

    Deliberately an error rather than a fallback: silently drawing blocks
    because 'houses' was asked for on a nature series would only be discovered
    after a full encode.
    """
    if scene not in SCENE_STYLES:
        return (f"unknown scene '{scene}'. Known scenes: "
                + ", ".join(SCENE_STYLES))
    ok = SCENE_STYLES[scene]
    if style not in ok:
        other = [s for sc, v in SCENE_STYLES.items() for s in v
                 if s == style and sc != scene]
        extra = (f" '{style}' belongs to the "
                 f"{[sc for sc, v in SCENE_STYLES.items() if style in v][0]} "
                 "scene.") if other else ""
        return (f"--style '{style}' is not available in the {scene} scene. "
                f"Choose from: {', '.join(ok)}.{extra}")
    if style in SILHOUETTE and rows > 1:
        return (f"--rows {rows} has no meaning for --style {style}: a "
                "silhouette is one scene, not stacked envelope rows. Use "
                f"--style {DEFAULT_STYLE[scene]} for multiple rows.")
    if filled and style in ("mountains", "forest", "mountains_forest"):
        return (f"--filled has no meaning for --style {style}: the style "
                "defines its own fill, with trees filling as the playhead "
                "passes and mountains staying outlined.")
    if not 0.0 <= progress <= 1.0:
        return f"--progress must be between 0 and 1, not {progress:g}"
    return None


def build(style, db, lv, detail="Default", scale="Skyline (rank)",
          dynamics="More"):
    """All the geometry one render needs, in design units."""
    f = resolve_detail(detail)
    seed = seed_from(db)
    rng = np.random.default_rng(seed)
    sc = {"style": style, "seed": seed, "detail": f,
          "mountains": [], "trees": [], "houses": [], "town_trees": []}
    if style in ("mountains", "mountains_forest"):
        sc["mountains"] = _mountains(db, f, scale, dynamics, rng)
    if style in ("forest", "mountains_forest"):
        sc["trees"] = _trees(db, f, scale, dynamics, rng,
                             vary=(style == "forest"))
    if style == "houses":
        sc["houses"], sc["town_trees"] = _houses(lv, f, rng)
    return sc
