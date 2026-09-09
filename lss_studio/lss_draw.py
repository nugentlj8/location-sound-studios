"""Drawing primitives on Pillow only - no Cairo, no native libraries.

Everything is drawn at SS times the final size and downsampled with LANCZOS,
which gives clean antialiasing on curves and text without a vector backend.
"""

from PIL import Image, ImageDraw, ImageFont, PngImagePlugin

SS = 2


def rgb(h):
    if not isinstance(h, str):
        return h
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def mix(a, b, t):
    """Blend colour a toward b by t, as an (r,g,b) tuple.

    Used instead of alpha because everything is drawn on an opaque RGB canvas;
    a flat blend against the known sky is indistinguishable from opacity here
    and costs no compositing pass.
    """
    ca, cb = rgb(a), rgb(b)
    t = max(0.0, min(1.0, t))
    return tuple(int(round(ca[i] + (cb[i] - ca[i]) * t)) for i in range(3))


def level_points(y0, amp, lv, W):
    w = (W + 60) / len(lv)
    return [(-30 + (i + 0.5) * w, y0 - amp * v) for i, v in enumerate(lv)], w


def catmull(P, per=16):
    """Flatten a Catmull-Rom spline through P into a dense polyline."""
    out = []
    for i in range(len(P) - 1):
        p0, p1 = P[max(i - 1, 0)], P[i]
        p2, p3 = P[i + 1], P[min(i + 2, len(P) - 1)]
        for s in range(per):
            t = s / per
            t2, t3 = t * t, t * t * t
            out.append((
                0.5 * (2 * p1[0] + (-p0[0] + p2[0]) * t
                       + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2
                       + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3),
                0.5 * (2 * p1[1] + (-p0[1] + p2[1]) * t
                       + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2
                       + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3)))
    out.append(P[-1])
    return out


def draw_line(dr, y0, amp, lv, W, col, sw, mode="steps"):
    """One envelope line. Steps use rectangles so the corners stay square."""
    c = rgb(col)
    half = sw / 2.0
    if mode == "curves":
        P, _ = level_points(y0, amp, lv, W)
        P = [(-90.0, P[0][1])] + P + [(W + 90.0, P[-1][1])]
        pts = [(x * SS, y * SS) for x, y in catmull(P)]
        dr.line(pts, fill=c, width=max(1, int(round(sw * SS))), joint="curve")
        return
    w = (W + 60) / len(lv)
    x, prev = -30.0, None
    for v in lv:
        y = y0 - amp * v
        dr.rectangle([x * SS, (y - half) * SS, (x + w) * SS, (y + half) * SS], fill=c)
        if prev is not None:
            lo, hi = sorted((prev, y))
            dr.rectangle([(x - half) * SS, (lo - half) * SS,
                          (x + half) * SS, (hi + half) * SS], fill=c)
        prev = y
        x += w


def text_run(dr, s, size, tracking, x, baseline, col, font_path):
    """Per-glyph placement so tracking is exact. Returns the ending x."""
    f = ImageFont.truetype(font_path, max(1, int(round(size * SS))))
    c = rgb(col)
    px = x * SS
    for ch in s:
        if ch != " ":
            dr.text((px, baseline * SS), ch, font=f, fill=c, anchor="ls")
        px += f.getlength(ch) + tracking * SS
    return px / SS


def text_width(s, size, tracking, font_path):
    f = ImageFont.truetype(font_path, max(1, int(round(size * SS))))
    return (sum(f.getlength(c) for c in s) + tracking * SS * (len(s) - 1)) / SS


def draw_slate(dr, cfg, W, k, dy, number, time_text, fg, slate, font_path):
    """Every run of slate text, in design units scaled by k.

    Lifted out of lss_render.compose() unchanged - same expressions, same
    order - so that it needs only a DRAW TARGET rather than a composed frame
    under it. That is the whole point of it living here: the photo mode draws
    the identical slate onto a transparent layer and composites it over a
    photograph, and there is no second implementation to drift from this one.

    `number` arrives already formatted, and `font_path` is passed rather than
    read from a global, because both of those belong to lss_render - the
    numero-sign fallback needs the loaded font and the number styles are its
    table. What is left here is drawing, which is what this module is.

    lss_render.slate_boxes() measures these same runs at k=1 to find where the
    ink ends. If a position below moves, that has to move with it.
    """
    M = 84 * k
    n = number
    nw = text_width(n, 27 * k, 11 * k, font_path) if n else 0.0
    avail = W - 2 * M - nw - (40 * k if n else 0)

    # a theme suffix can make the series long; shrink it to clear the number
    ssize, strack = 27 * k, 11 * k
    for _ in range(24):
        if text_width(cfg["series"], ssize, strack, font_path) <= avail:
            break
        ssize *= 0.94
        strack *= 0.94
    text_run(dr, cfg["series"], ssize, strack, M, (150 + dy) * k, fg, font_path)
    if n:
        text_run(dr, n, 27 * k, 11 * k, W - M - nw, (150 + dy) * k, slate, font_path)

    text_run(dr, cfg["place"], 104 * k, 7 * k, M, (296 + dy) * k, fg, font_path)
    text_run(dr, f'{cfg["city"]}  ·  {cfg["conditions"]}',
             31 * k, 8 * k, M, (360 + dy) * k, slate, font_path)
    if time_text:
        w = text_width(time_text, 31 * k, 0, font_path)
        text_run(dr, time_text, 31 * k, 0, W - M - w, (360 + dy) * k, slate,
                 font_path)


def new_canvas(W, H, bg="#13232E"):
    img = Image.new("RGB", (W * SS, H * SS), rgb(bg))
    return img, ImageDraw.Draw(img)


def finish(img, W, H, path, **save):
    """Downsample to the final size and write it.

    `save` goes straight to Pillow, which is how the square cover gets its
    300 dpi pHYs chunk and its sRGB marker without needing a second save path.
    """
    img.resize((W, H), Image.LANCZOS).save(path, **save)
    return path


def png_meta():
    """The PNG chunks a Spotify cover has to carry: 300 dpi and an sRGB marker.

    Pillow writes dpi as pHYs in pixels per METRE, and 300 dpi lands on exactly
    the 11811 the spec asks for rather than near it. There is no alpha to strip
    and no profile to convert - new_canvas() only ever makes RGB, and a PNG
    with no embedded profile is read as sRGB everywhere - so the marker is
    saying out loud what the file already was.
    """
    m = PngImagePlugin.PngInfo()
    m.add(b"sRGB", bytes([0]))                 # rendering intent 0, perceptual
    return {"dpi": (300, 300), "pnginfo": m}


def solid_mask(W, H, path):
    Image.new("RGB", (W, H), (255, 255, 255)).save(path)
    return path


def draw_filled(dr, y0, amp, lv, W, baseline, col, mode="steps"):
    """Skyline as a solid silhouette from the roofline down to a baseline."""
    c = rgb(col)
    if mode == "curves":
        P, _ = level_points(y0, amp, lv, W)
        P = [(-90.0, P[0][1])] + P + [(W + 90.0, P[-1][1])]
        top = catmull(P)
    else:
        top, x = [], -30.0
        w = (W + 60) / len(lv)
        for v in lv:
            y = y0 - amp * v
            top += [(x, y), (x + w, y)]
            x += w
    poly = [(p[0] * SS, p[1] * SS) for p in top]
    poly += [(top[-1][0] * SS, baseline * SS), (top[0][0] * SS, baseline * SS)]
    dr.polygon(poly, fill=c)


def row_layout(H, n, filled=False, top=0.392, bot=0.736):
    """(y, amplitude) for n stacked envelope rows inside a fixed vertical zone."""
    if n <= 1:
        # Bottom-anchored: the shortest building sits near the frame bottom and
        # towers rise a fixed height upward, so tall towers clear the slate while
        # the skyline hugs the lower edge. Independent of top/bot.
        base_frac = 0.930                       # shortest tower base, above bottom
        amp = 0.218 if not filled else 0.150    # tower height (level range = 1.4)
        y0 = base_frac - amp * 0.45             # so level -0.45 lands at base_frac
        return [(y0 * H, amp * H)]
    span = (bot - top) / (n - 1)
    mid = (top + bot) / 2.0
    half = (bot - top) / 2.0
    out = []
    for i in range(n):
        y = top + span * i
        k = 1.0 if n <= 2 else 1.0 - 0.6 * abs(y - mid) / half
        amp = min(span * 0.62, 0.16) * k
        out.append((y * H, amp * H))
    return out


def split_at_xs(pts, bounds):
    """Cut a left-to-right polyline at the given x positions."""
    if not bounds:
        return [pts]
    segs, cur, i = [], [pts[0]], 0
    for a, b in zip(pts, pts[1:]):
        while i < len(bounds) and a[0] < bounds[i] <= b[0]:
            x = bounds[i]
            t = 0.0 if b[0] == a[0] else (x - a[0]) / (b[0] - a[0])
            pt = (x, a[1] + (b[1] - a[1]) * t)
            cur.append(pt)
            segs.append(cur)
            cur = [pt]
            i += 1
        cur.append(b)
    segs.append(cur)
    return [s for s in segs if len(s) > 1]


def envelope_pts(y0, amp, lv, W, mode="steps"):
    """The polyline for one envelope row, in final-image coordinates."""
    if mode == "curves":
        P, _ = level_points(y0, amp, lv, W)
        P = [(-90.0, P[0][1])] + P + [(W + 90.0, P[-1][1])]
        return catmull(P)
    pts, x = [], -30.0
    w = (W + 60) / len(lv)
    for v in lv:
        y = y0 - amp * v
        pts += [(x, y), (x + w, y)]
        x += w
    return pts


# ------------------------------------------------------- generative scenes
# Depth is expressed as distance from the SKY, not as absolute darkness. The
# reference art is near-black trees on white; on the night sky near-black trees
# would simply vanish, so mountains are mixed toward the background to recede
# and trees are left at full strength to come forward. That reads correctly on
# a pale sky and inverts sensibly on a dark one.
MTN_RIDGE = 0.35                 # how far the ridgeline sits toward the sky
# The two flat faces, as a distance from the SKY - so the lit face is the one
# mixed LESS far toward it, whichever way round sky and silhouette happen to be.
# Separated enough to read as lit and shadow, but both kept well away from full
# strength: the trees fill solid as the progress indicator, so anything
# approaching their weight up here costs the read on mountains_forest. The gap
# between the two carries the facet; their distance from the sky carries depth.
MTN_LIT, MTN_SHADOW = 0.68, 0.87
MTN_RIDGE_FACE = 0.52            # a fill already defines the ridge - the
                                 # stroke only needs to keep the edge crisp
TREE_FAINT = 0.62                # an unplayed tree, when drawn faint. Further
                                 # toward the sky than this and it holds on a
                                 # dark sky but washes out on the paler ones,
                                 # where there is less contrast to spend
# The town's depth ladder, same measure as the mountains': distance from the
# sky. Street trees stay at 0, i.e. full strength, so they separate from the
# houses behind them once everything is filled in one colour - the same cue
# mountains_forest uses, at a fraction of the distance. It has to be a small
# step here: in a town the HOUSES are what the playhead recolours, so pushing
# them back the way the mountains are pushed back would cost the progress read.
HOUSE_FACE, HOUSE_SIDE = 0.22, 0.46
# The city's ladder, same measure again, but three rungs instead of two: the
# near layer at 0, the skyline's lit face pushed back to sit behind it, and its
# shadow face further still. The step up from the town's 0.22 is deliberately
# small - here too the SKYLINE is what the playhead recolours, so it has to stay
# dominant. The near layer separates by being a low band with sky cut around it,
# not by out-weighing the towers.
#
# 0.62 for the shadow face is measured rather than chosen by eye. A lit pane is
# drawn at full strength over whichever face it lands on, and on all eight
# presets the accent's luminance sits BETWEEN the foreground and the sky - so as
# a face is mixed toward the sky its luminance sweeps down and, somewhere in
# that sweep, crosses the lit colour and erases the pane. The town's 0.46 sat on
# exactly that crossing for Night, at 1.04. 0.62 clears it on the far side and
# lifts the worst case across every preset to about 1.6, for a little unlit-pane
# contrast on Evening (1.53 -> 1.45). There is no value that also rescues a lit
# pane on the LIT face: the crossing is at a different depth in every palette,
# so whatever is chosen, one of them is sitting on it.
#
# The FACETS run the other way from the ladder: the near layer carries the
# strong split and the skyline a soft one. Distance costs internal contrast
# before it costs anything else - a far building's own faces converge toward
# each other long before the building stops reading - so the layer that should
# look crisply divided is the near one. Built the opposite way round at first,
# with a 0.32 split on the towers against 0.26 up front, which is why the near
# layer kept reading flat no matter how hard its own shade was pushed: it was
# being asked to out-contrast a far layer that had no business being that
# defined. Softening the towers gets there without spending contrast the tight
# palettes do not have.
#
# Softened by moving the SHADOW face only. Moving the lit face as well took the
# whole layer a step further toward the sky, and since the mix runs toward the
# background that reads as the skyline going darker on a dark palette - a change
# to the picture's weight, when all that was wanted was a change to the split.
# The lit face is the layer's depth; the distance to the shadow face is its
# facet. They are separate decisions and only the second one was in question.
CITY_FACE, CITY_SHADOW = 0.30, 0.54          # a 0.24 split, down from 0.32
# The near layer is drawn in the SAME two tones as the skyline behind it - one
# city, one material, front to back. It was a rung nearer for a while (0.00 body
# against the skyline's 0.30, and 0.27 against 0.54), which is the ladder the
# comment above still describes for the towers, and the near band read as
# brighter and cleaner than the towers rather than as the same buildings closer
# up. Tonal depth was never what separated these two layers anyway: the sky gap
# cut around the band spends the whole silhouette-vs-sky contrast, where a rung
# of the ladder spends a fraction of one. Giving the rung back costs nothing
# that was doing any work and buys a single consistent material.
FORE_FACE, FORE_SIDE = CITY_FACE, CITY_SHADOW
LW_RIDGE, LW_CREASE = 4.5, 3.5   # design units
LW_TREE, LW_HOUSE = 3.5, 4.0
LW_CORNER = 3.0                  # the lit/shade division, when there is no
                                 # fill to divide
LW_TREE_GAP = 5.0                # a street tree's sky gap. Straddles the
                                 # outline, so half of it shows outside
LW_FORE_GAP = 5.0                # ...and the city's near layer, for the same
                                 # reason: a filled roofline crossing a filled
                                 # tower is otherwise the identical colour
FORE_SEAM = 0.78                 # the crease where two near buildings meet.
                                 # It lands on the shaded side of the building
                                 # it belongs to - that face covers the left
                                 # third and this is the left edge - so it has
                                 # to clear FORE_SIDE, not FORE_FACE. It sat at
                                 # 0.55 against a 0.27 face and moved with it
                                 # when the face went back to 0.54, keeping the
                                 # same 0.24 step.
                                 #
                                 # A plane is capped by the ladder; a 3-unit
                                 # line is not - nothing reads a hairline as a
                                 # layer, so it can go as far as it needs to.
                                 # It has to be a tone and not a sky gap: sky
                                 # here would put back exactly the separation
                                 # the band is built to remove.


def _lum(c):
    v = [x / 255.0 for x in rgb(c)]
    v = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in v]
    return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]


def _contrast(a, b):
    """WCAG contrast ratio between two colours, 1.0 to 21.0."""
    la, lb = _lum(a), _lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def _depth(t, col, bg):
    """Scale a mix-toward-the-sky by how much contrast there is to spend.

    A bright foreground on a dark sky can be pushed a long way back and still
    read. A mid-toned accent on that same sky goes muddy at the identical
    setting - which left the mountains behind the played half almost black
    while the unplayed half kept its two tones. Measuring the contrast instead
    of hard-coding the amount keeps both halves at a comparable weight, in any
    palette.
    """
    c = _contrast(col, bg)
    return t * max(0.60, min(1.0, (c / 8.0) ** 0.5))


def _pts(poly, k):
    """Design units to supersampled image coordinates."""
    return [(x * k * SS, y * k * SS) for x, y in poly]


def _stroke(dr, poly, k, col, lw, close=False):
    p = _pts(poly, k)
    if close:
        p = p + [p[0]]
    if len(p) < 2:
        return
    dr.line(p, fill=rgb(col), width=max(1, int(round(lw * k * SS))),
            joint="curve")


def _band_col(bands, x, k, fallback):
    """The colour the playback cycle is showing at this x."""
    if not bands:
        return fallback
    px = x * k
    for edge, col in bands:
        if px <= edge:
            return col
    return bands[-1][1]


def _pane(dr, p, k, c, bg, lit, filled):
    """One window pane or door.

    An UNLIT pane is whatever its wall is not - a sky hole punched in a solid
    house, a solid block inside an outlined one. Either way it carries the same
    contrast the silhouette's own edge already has, so it survives a palette
    with nothing to spare.

    A LIT pane is drawn in the colour the current state is NOT: accent ahead of
    the playhead, foreground behind it. Painting it accent unconditionally
    would make it vanish into the accent-filled body once the playhead passed,
    i.e. an occupied house would empty as it played. It flips colour instead.

    KNOWN DEFECT - a lit pane on a LIT face, ahead of the playhead
    ------------------------------------------------------------
    On four presets a lit window is very nearly invisible until the playhead
    reaches it: Alpine 1.05, Mist 1.11, Aurora 1.16, Canopy 1.17. Behind the
    playhead the pair inverts and the same presets measure 2.1 to 3.8, so a
    finished thumbnail - fully played by default - never shows it. It is the
    unplayed side of a video frame, and a --progress 0 still.

    The cause is not the depth ladder, and no choice of face depth fixes it. On
    every preset the accent's luminance sits BETWEEN the foreground and the sky,
    so any face swept from one toward the other crosses it somewhere; the
    crossing simply sits at a different depth in each palette, and whatever
    single depth is chosen, one of them is standing on it. Measured across
    0.22-0.42 the worst case never rises above 1.05.

    The fix belongs HERE, in the colour a lit pane is drawn in - it has to move
    when its own face crowds it, rather than always being the flat opposite
    state colour. Deliberately not done in the pass that found it: this function
    is shared with houses, so changing the rule moves that style too and needs
    its own byte-identity story. See the city depth notes in README.md.
    """
    x0, y0, x1, y1 = p["rect"]
    if p["lit"] and lit:
        f = rgb(lit)
    else:
        f = rgb(bg) if filled else rgb(c)
    dr.rectangle([x0 * k * SS, y0 * k * SS, x1 * k * SS, y1 * k * SS], fill=f)


def star_rect(s, k, ss=1):
    """One star as a whole-pixel square in final-image coordinates.

    Whole pixels for the same reason MAST_W is a rectangle rather than a
    stroke: in the video a twinkling star is faded by a drawbox, and a drawbox
    lands on integer pixels whatever the geometry underneath does. Rounding
    both to the same grid here is what makes the filter cover exactly the
    square that was baked, rather than a hard box inside a soft dot.
    """
    n = max(1, int(round(s["size"] * k)))
    x0 = int(round(s["cx"] * k - n / 2.0))
    y0 = int(round(s["cy"] * k - n / 2.0))
    return x0 * ss, y0 * ss, n * ss


def star_tone(col, bg, t):
    """A star's colour at one distance from the sky.

    Shared with the video's filter builder, so a baked star and the drawbox
    that fades it are mixed by the identical rule rather than two copies of it.
    """
    return mix(col, bg, _depth(t, col, bg))


def star_fade(col, bg, t, f):
    """A star's own tone taken back toward the sky by f, where 1.0 is gone.

    Blended from the drawn tone rather than by pushing t further, so f is the
    same fraction of the same visible distance in every palette. Going through
    t instead would run into _depth, which deliberately holds a low-contrast
    star off the sky - the exact thing a fade needs to be able to overrule.
    """
    return mix(star_tone(col, bg, t), bg, f)


# --- weather tones ---------------------------------------------------------
# Clouds and rain are never given a colour. They are derived from the two the
# palette already has, and the amount is a target CONTRAST RATIO rather than a
# mix fraction - for the reason _depth exists at all. The presets run from 2.40
# (Morning, white on gold) to 15.41 (Aurora, near-white on near-black), and one
# fixed mix would be a whisper on the first and a slab on the second.
#
# The pole is the palette's OWN foreground, which in every shipping preset is
# already on the correct side of its sky: lighter on the seven dark skies,
# DARKER on Mist's pale one, with no special case anywhere. A palette whose
# foreground is on the wrong side falls back to white or black - it has no
# other colour that could be a cloud.
CLOUD_F = 0.18                   # cloud contrast as a fraction of the sky-to-
                                 # skyline contrast the palette actually has.
                                 # This is what keeps Morning readable: a flat
                                 # target would put a cloud behind its white
                                 # slate at 1.78, the headroom term holds it
                                 # at 1.92
CLOUD_CR_MAX = 1.45              # ...and an absolute ceiling, so the roomy
                                 # palettes stay soft rather than spending
                                 # everything they have
CLOUD_UNDER_CR = 1.18            # the shaded underside, as a contrast step
                                 # from the BODY toward whichever of the sky
                                 # and the pole is DARKER. Not "further from the sky": on Mist
                                 # that is darker and correct, but on Night and
                                 # Morning it is lighter, and a cloud lit from
                                 # above with a bright rim along its bottom
                                 # edge reads as a mistake in any palette.
                                 # Both endpoints are the palette's own, so
                                 # this introduces no fourth colour
RAIN_F = 0.22                    # rain gets a little more than a cloud: a
                                 # streak is 1 design unit wide against a
                                 # cloud's 300, and a thin mark needs more
                                 # contrast to read at the same weight
RAIN_CR_MAX = 1.70               # ...before per-streak alpha takes it back
                                 # down to an effective 1.15-1.40
TONE_FLOOR, TONE_CEIL = 8, 247   # no channel may reach 0 or 255. Nothing in
                                 # the shipping presets comes close - the
                                 # extremes measure 37 and 238 - but a
                                 # pure-white streak on a dark sky is exactly
                                 # what chroma subsampling smears in the encode


def _pole(bg, fg):
    """Which way a cloud goes from this sky, and what it goes toward."""
    lb, lf = _lum(bg), _lum(fg)
    up = lb < 0.5                            # dark sky: lighter. Light: darker
    if (lf > lb) == up:
        return fg
    return "#FFFFFF" if up else "#000000"


def tone_at(bg, pole, cr):
    """The tone `cr` of contrast away from the sky, toward `pole`.

    Bisected rather than solved: mix() is integer-rounded per channel, so the
    closed form would not agree with the colour actually drawn. 24 halvings
    settle t to 1 part in 16 million, which is well past the point where the
    rounding decides the answer.
    """
    lo, hi = 0.0, 1.0
    for _ in range(24):
        m = (lo + hi) / 2.0
        if _contrast(mix(bg, pole, m), bg) < cr:
            lo = m
        else:
            hi = m
    return tuple(max(TONE_FLOOR, min(TONE_CEIL, v))
                 for v in mix(bg, pole, (lo + hi) / 2.0))


def weather_tones(bg, fg):
    """Every colour the weather layer draws in, for one palette."""
    pole = _pole(bg, fg)
    head = _contrast(fg, bg)
    ccr = min(CLOUD_CR_MAX, 1.0 + (head - 1.0) * CLOUD_F)
    rcr = min(RAIN_CR_MAX, 1.0 + (head - 1.0) * RAIN_F)
    body = tone_at(bg, pole, ccr)
    dark = bg if _lum(bg) < _lum(pole) else pole
    return {"cloud": body,
            # tone_at measures from whatever it is given, so the same
            # bisection that placed the body on the sky places the underside
            # on the body - one step, normalised, in every palette
            "under": tone_at(body, dark, CLOUD_UNDER_CR),
            "rain": tone_at(bg, pole, rcr),
            "cloud_cr": ccr, "rain_cr": rcr}


def draw_clouds(dr, wx, W, H, bg, fg):
    """The cloud band, over the stars and under the silhouette.

    Opaque flat fills, which is what occludes a star for free - the same rule
    that lets an outlined mountain hide the range behind it. Lobes first, then
    the slab that closes them into a flat bottom, then the shaded strip along
    the underside of that.

    Nothing here is clipped to the sky band. A cloud hanging below the horizon
    is cut by the silhouette drawn over it, and a cloud behind the slate is cut
    by text that draws last and opaque - which is the read being aimed for.
    """
    k = W / 1280.0
    t = weather_tones(bg, fg)
    for c in wx.get("clouds") or []:
        # the shaded copy first, then the body riding above it - so what shows
        # underneath is the cloud's OWN outline offset, round ends and all,
        # rather than a bar with two corners in the middle of it
        for dy, col in ((0.0, t["under"]), (-c["under_dy"], t["cloud"])):
            for lx, ly, rx, ry in c["lobes"]:
                dr.ellipse([(lx - rx) * k * SS, (ly + dy - ry) * k * SS,
                            (lx + rx) * k * SS, (ly + dy + ry) * k * SS],
                           fill=col)
            x0, y0, x1, y1 = c["slab"]
            if x1 > x0:
                dr.rectangle([x0 * k * SS, (y0 + dy) * k * SS,
                              x1 * k * SS, (y1 + dy) * k * SS], fill=col)


def draw_rain(img, wx, W, H, bg, fg):
    """Static rain, over the silhouette and under the slate.

    The one thing in this module drawn with real per-pixel alpha rather than a
    mix(), and the reason is the layer underneath: a streak painted opaque in a
    sky tone would punch holes in the accent-filled skyline and fight the
    playhead, which is the one read this drawing is not allowed to weaken. An
    8-bit mask tints it instead, so a streak over the played half stays accent
    and a streak over the sky stays sky.

    The mask is L-mode - one byte a pixel, not four - so the 3000px cover costs
    36MB here rather than 144MB. Streaks land in the supersampled buffer and
    take the existing LANCZOS step down with everything else.
    """
    r = (wx or {}).get("rain")
    if not r:
        return
    k = W / 1280.0
    mask = Image.new("L", img.size, 0)
    md = ImageDraw.Draw(mask)
    # design units, so a streak is 1.7px on a 1920 thumbnail and 2.7px on the
    # square cover. A pixel width here is what would send it to a hairline
    lw = max(1, int(round(r["width"] * k * SS)))
    for x0, y0, x1, y1, a in r["streaks"]:
        md.line([(x0 * k * SS, y0 * k * SS), (x1 * k * SS, y1 * k * SS)],
                fill=int(round(255 * a)), width=lw)
    img.paste(weather_tones(bg, fg)["rain"], (0, 0, img.width, img.height), mask)


def draw_sky(dr, sky, W, H, col, bg):
    """The star field, behind everything.

    Drawn first, so the silhouette occludes it at no cost: every shape in
    draw_scene fills opaque before it strokes, which is the same trick that
    lets an outlined mountain hide the range behind it.

    Stars are the SAME colour in both playback layers - they never take the
    state colour. Two reasons, and the second one pays for the feature: the
    skyline is what reads as progress and a sky sweeping alongside it competes,
    and identical layers mean the sliding mask passes over a star invisibly, so
    ONE drawbox covers both sides of the playhead where a beacon needs two.

    Every star is drawn at its own tone, twinkling or not - a still and both
    video layers alike. The filters only ever take a star DOWN from here, so a
    frame of the video can never hold a thinner field than its own thumbnail.
    """
    k = W / 1280.0
    for s in sky.get("stars", []):
        x0, y0, n = star_rect(s, k, SS)
        dr.rectangle([x0, y0, x0 + n - 1, y0 + n - 1],
                     fill=star_tone(col, bg, s["tone"]))


def draw_scene(dr, sc, W, H, col, bg, played=False, face="outline",
               ahead="outline", filled=False, bands=None, lit=None,
               lights_on=True):
    """One generative silhouette, back to front.

    `col` is the state colour: the foreground ahead of the playhead, the accent
    behind it. `lit` is the other one of that pair, used only by lit windows.
    The two layers are otherwise identical, so the sliding mask in the video
    turns one into the other exactly where the playhead is.

    `lights_on` draws the antenna beacons. A still wants them lit; the video
    layers bake them dark and let ffmpeg blink them, which is the only part of
    this drawing that is not the same in every frame.

    Shapes are filled with the SKY before they are stroked. That is what gives
    a nearer shape occlusion over a further one without a vector clipper, and
    it is why an outline-only mountain still hides the range behind it.
    """
    k = W / 1280.0

    for m in sc.get("mountains", []):
        c = _band_col(bands, m["cx"], k, col)
        if face == "twotone":
            # both faces stay washed toward the sky: the trees fill solid as
            # the progress indicator, and if the mountains fill at anything
            # like full strength mountains_forest turns to mud
            dr.polygon(_pts(m["shadow"], k), fill=mix(c, bg, _depth(MTN_SHADOW, c, bg)))
            dr.polygon(_pts(m["lit"], k), fill=mix(c, bg, _depth(MTN_LIT, c, bg)))
        else:
            dr.polygon(_pts(m["poly"], k), fill=rgb(bg))
        ridge = mix(c, bg, _depth(MTN_RIDGE_FACE if face == "twotone"
                                  else MTN_RIDGE, c, bg))
        _stroke(dr, m["poly"][:-1], k, ridge, LW_RIDGE)   # flanks, not the base
        _stroke(dr, m["crease"] if face == "twotone" else m["spur"],
                k, ridge, LW_CREASE)
    if sc.get("mountains"):
        # A flank stroke ends ON the base, so it hangs half a line-width below
        # it, and a crease ends there too. That used to fall off the bottom of
        # the frame; now the range stands on a visible ground line, and a row of
        # drips along it reads as a fault. Cut them with the sky - the same
        # occlusion every shape here gets by being filled with the sky first.
        # The trees come after, so the ones in front still stand past the line.
        dr.rectangle([0, sc["mtn_base"] * k * SS, W * SS, H * SS], fill=rgb(bg))

    for t in sc.get("trees", []):
        c = _band_col(bands, t["cx"], k, col)
        if played:
            dr.polygon(_pts(t["poly"], k), fill=rgb(c))
        elif ahead == "faint":
            dr.polygon(_pts(t["poly"], k), fill=mix(c, bg, _depth(TREE_FAINT, c, bg)))
        else:
            dr.polygon(_pts(t["poly"], k), fill=rgb(bg))
            _stroke(dr, t["poly"], k, c, LW_TREE, close=True)

    # houses, then the street trees in front of them. Both fill on the same
    # schedule: in a town the whole row is what the playhead recolours, so a
    # tree filling on the forest's schedule would fight that read. Depth
    # between them comes from the ladder above instead.
    # a city stands behind its own near layer, so its skyline is pushed further
    # from the sky than a town's row is - see the ladder above
    face, side = ((CITY_FACE, CITY_SHADOW) if sc.get("style") == "city"
                  else (HOUSE_FACE, HOUSE_SIDE))
    for h in sc.get("houses", []):
        c = _band_col(bands, h["cx"], k, col)
        body = mix(c, bg, _depth(face, c, bg))
        # An antenna is too slender to outline - a 4-unit stroke around a
        # 3-unit mast is just a thicker mast - so it is always solid, in the
        # body tone when there is one and in the line colour when there is not.
        # It goes down first; the building drawn over it hides the join.
        for poly in h.get("antenna", []):
            dr.polygon(_pts(poly, k), fill=body if filled else rgb(c))
        # chimney next, house over it: the stack runs down to the ground so it
        # is never left hanging, and the body then hides everything below the
        # roof it pokes through
        for poly in ([h["chimney"]] if h.get("chimney") else []) + [h["poly"]]:
            if filled:
                dr.polygon(_pts(poly, k), fill=body)
            else:
                dr.polygon(_pts(poly, k), fill=rgb(bg))
                _stroke(dr, poly, k, c, LW_HOUSE, close=True)
        if h.get("shade"):
            if filled:
                dr.polygon(_pts(h["shade"], k),
                           fill=mix(c, bg, _depth(side, c, bg)))
            else:
                # no fill to divide, so only the division is drawn - on a house
                # that vertical run reads as a building corner, where the same
                # treatment on a mountain would be a stray diagonal
                _stroke(dr, h["shade"][-2:], k, c, LW_CORNER)
        for p in h.get("panes", []):
            _pane(dr, p, k, c, bg, lit, filled)
        # the beacon, on the same rule as a lit window. The video bakes it off
        # and blinks it back on with a pair of drawbox filters, so its position
        # has to be exactly this one - see lss_render.blink_layers()
        if lights_on and h.get("light"):
            _pane(dr, {"rect": h["light"], "lit": True}, k, c, bg, lit, filled)

    for t in sc.get("town_trees", []):
        c = _band_col(bands, t["cx"], k, col)
        if filled:
            # The tone step above carries the depth on most palettes. On the
            # tight ones it cannot: Morning has 1.90 between accent and sky, so
            # a fraction of that is nothing, and the tree goes back into the
            # houses. A sky gap around the near shape spends the WHOLE of that
            # contrast instead - it is the same occlusion the rest of this
            # function gets by filling a shape with the sky before stroking it,
            # and it reads exactly as well as the rooflines beside it do.
            _stroke(dr, t["poly"], k, bg, LW_TREE_GAP, close=True)
            dr.polygon(_pts(t["poly"], k), fill=rgb(c))   # full strength: this
        else:                                             # is the near layer
            dr.polygon(_pts(t["poly"], k), fill=rgb(bg))
            _stroke(dr, t["poly"], k, c, LW_HOUSE, close=True)

    # The city's near layer, last, because it stands in front of everything. It
    # comes off the same list in build order, so a tree in a gap lands over the
    # buildings either side of it, and each shape cuts a sky gap before it
    # fills, exactly as a street tree does - and here that gap is the ONLY
    # thing parting the two layers, since the band is now drawn in the same two
    # tones as the skyline behind it. Which is the right way round: a tone step
    # would have nothing to spend on the tight palettes anyway.
    fore = sc.get("fore", [])
    blocks = [f for f in fore if not f.get("tree")]
    # The sky gap belongs to the BAND, not to each building in it. Laying every
    # stroke down first and only then filling means a neighbour's fill paints
    # the shared edges out, while the gap along the outside - where nothing
    # fills - survives to hold the layer off the skyline. Cut them one at a
    # time and every touching pair gets a sky line between it, which is the
    # separate-shapes read this layer exists to avoid.
    if filled:
        for f in blocks:
            _stroke(dr, f["poly"], k, bg, LW_FORE_GAP, close=True)
    for f in blocks + [f for f in fore if f.get("tree")]:
        c = _band_col(bands, f["cx"], k, col)
        body = mix(c, bg, _depth(FORE_FACE, c, bg))
        if filled:
            # a tree stands in FRONT of the band, so it keeps its own gap - and
            # takes it here, after the band is down, or the band would fill it in
            if f.get("tree"):
                _stroke(dr, f["poly"], k, bg, LW_FORE_GAP, close=True)
            dr.polygon(_pts(f["poly"], k), fill=body)
        else:
            dr.polygon(_pts(f["poly"], k), fill=rgb(bg))
            _stroke(dr, f["poly"], k, c, LW_HOUSE, close=True)
        if f.get("shade"):
            if filled:
                dr.polygon(_pts(f["shade"], k),
                           fill=mix(c, bg, _depth(FORE_SIDE, c, bg)))
            elif not f.get("tree"):
                # as on a house: with no fill to divide, only the division is
                # drawn, and on a block that vertical run reads as a corner. A
                # crown gets nothing - the same line curving down a round shape
                # would read as a crack in it, not as its shaded side.
                _stroke(dr, f["shade"][-2:], k, c, LW_CORNER)
        if filled and not f.get("tree"):
            # The seam where this building meets the one beside it. Once the
            # band stopped cutting sky between neighbours it also stopped
            # showing where one ended, and a run of similar heights read as a
            # single wide building. Drawn in the shade tone rather than the sky:
            # a darker line is a CORNER and keeps the band continuous, where a
            # sky line would put the gap straight back. poly[:2] is the left
            # edge, ground to eave, which is exactly that corner.
            _stroke(dr, f["poly"][:2], k, mix(c, bg, _depth(FORE_SEAM, c, bg)),
                    LW_CORNER)
        for p in f.get("panes", []):
            _pane(dr, p, k, c, bg, lit, filled)


def progress_composite(bone, clay, frac, out):
    """Bone left of the playhead replaced by clay, exactly as ffmpeg's sliding
    mask does it. Lets a still show a mid-playback frame without an encode."""
    b = Image.open(bone).convert("RGB")
    c = Image.open(clay).convert("RGB")
    x = max(0, min(b.width, int(round(b.width * frac))))
    if x:
        b.paste(c.crop((0, 0, x, b.height)), (0, 0))
    b.save(out)
    return out


def draw_banded(dr, y0, amp, lv, W, sw, mode, bands, filled=False, baseline=None):
    """Draw one row where the colour changes at fixed x positions.
    `bands` is a list of (x_end, colour); the last x_end should exceed W."""
    pts = envelope_pts(y0, amp, lv, W, mode)
    segs = split_at_xs(pts, [b[0] for b in bands[:-1]])
    for seg, (_, col) in zip(segs, bands):
        c = rgb(col)
        if filled:
            poly = [(p[0] * SS, p[1] * SS) for p in seg]
            poly += [(seg[-1][0] * SS, baseline * SS), (seg[0][0] * SS, baseline * SS)]
            dr.polygon(poly, fill=c)
        else:
            p = [(x * SS, y * SS) for x, y in seg]
            w = max(1, int(round(sw * SS)))
            dr.line(p, fill=c, width=w, joint="curve" if mode == "curves" else None)
            r = w / 2.0
            for x, y in p[1:-1]:
                if mode == "curves":
                    dr.ellipse([x - r, y - r, x + r, y + r], fill=c)
                else:
                    dr.rectangle([x - r, y - r, x + r, y + r], fill=c)
