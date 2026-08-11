"""Preset loading and colour resolution, shared by lss_render and lss_studio."""

import json, os, re

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "lss_presets.json")
INK = (0x13, 0x23, 0x2E)

DEFAULT_BG = "#13232E"           # the sky, when no preset says otherwise
DEFAULT_FG = "#F0E7D6"           # the silhouette and the large slate text

FALLBACK = {
    "series": {"Sounds of the City": {"name": "SOUNDS OF THE CITY", "accent": "#CF7A34"}},
    "themes": {"None (use series colour)": {}},
}

# ---------------------------------------------------------------------------
# Colour presets. THIS is the one structure to edit to add a look - nothing in
# the render code needs touching. Each entry sets the sky (background), the
# silhouette and slate text (foreground), and the accent the playhead reveals.
#
# They are deliberately scene-agnostic: identity comes from the silhouette
# SHAPE, so the same preset has to work for city, town, nature and spaces.
#
# Each was chosen by eye against rendered frames, with WCAG contrast measured
# alongside. Ratios below are fg/bg, accent/bg, accent/fg.
#
# Note the accent/bg figures are low by WCAG's yardstick. That metric is built
# for body text on a background; these accents are wide skyline strokes
# separated from the sky by HUE as much as luminance, and they read clearly at
# thumbnail size. The GUI's low-contrast warning is therefore skipped for these
# vetted presets and kept for hand-typed colours - see lss_studio.start_render.
# ---------------------------------------------------------------------------
COLOR_PRESETS = {
    "None (series colour)": {},
    "Morning": {                                     # 2.40, 1.90, 4.56
        "background": "#D69E10", "foreground": "#FFFFFF",
        "accent": "#BC5A10", "accent2": "#9C4712",
        "note": "low warm sun on deep gold, white skyline",
    },
    "Night": {                                       # 13.08, 4.96, 2.64
        "background": "#13232E", "foreground": "#F0E7D6",
        "accent": "#CF7A34", "accent2": "#F5C98A",
        "note": "the original look - blue hour, sodium-lamp accent",
    },
    "Evening": {                                     # 4.29, 1.80, 7.72
        "background": "#CC5522", "foreground": "#FFFFFF",
        "accent": "#8C3355", "accent2": "#73203F",
        "note": "burnt sunset orange, white skyline, plum playhead",
    },
    "Canopy": {                                      # 9.53, 4.73, 2.02
        "background": "#1D4029", "foreground": "#F1E9D2",
        "accent": "#E2953A", "accent2": "#A8C24A",
        "note": "deep forest green, sunlight coming through the leaves",
    },
    # Four more that read as outdoors without reaching for foliage green or
    # bark brown. Each still works for any scene, but they were chosen against
    # the mountains_forest silhouette, where a sky occupies most of the frame.
    "Alpine": {                                      # 6.98, 4.31, 1.62
        "background": "#3E5C78", "foreground": "#FFFFFF",
        "accent": "#9FD3E8", "accent2": "#6FB4D2",
        "note": "cold high air, snow silhouette, glacier-ice playhead",
    },
    "Alpenglow": {                                   # 11.97, 4.92, 2.43
        "background": "#2C2148", "foreground": "#F7E2E6",
        "accent": "#E8734A", "accent2": "#B8536E",
        "note": "violet dusk with the last sun still on the peaks",
    },
    "Mist": {                                        # 9.10, 4.76, 1.91
        "background": "#C6D2CE", "foreground": "#1F2E2C",
        "accent": "#2F5D5B", "accent2": "#4E7F73",
        "note": "fog off the water, dark pines standing in it",
    },
    "Aurora": {                                      # 15.41, 9.34, 1.65
        "background": "#0C1A2B", "foreground": "#E9F2F3",
        "accent": "#3DD68C", "accent2": "#5FA8E0",
        "note": "arctic night, northern lights running the timeline",
    },
}

HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")

# The colour slots a preset can fill, in the order they sit on screen - sky
# behind, silhouette in front, then the accent the playhead reveals. Used to
# name the colours in palette() below.
#
# "accent2" is not a render setting; nothing draws with it. It survives as a
# companion colour that was chosen alongside the other three, which makes it
# worth offering in the picker - hence "highlight" rather than "accent 2".
COLOR_ROLES = (("background", "sky"), ("foreground", "skyline"),
               ("accent", "accent"), ("accent2", "highlight"))


def load():
    try:
        with open(PATH, "r", encoding="utf-8") as fh:
            d = json.load(fh)
        d.pop("_comment", None)
        if not d.get("series"):
            raise ValueError("no series defined")
        d.setdefault("themes", {"None (use series colour)": {}})
    except Exception:
        d = dict(FALLBACK)
    # Colour presets ship in code so the updater actually delivers new ones -
    # lss_presets.json is the user's own file and is never overwritten. A
    # "colors" key there still adds to, or overrides, what ships here.
    colors = dict(COLOR_PRESETS)
    colors.update(d.get("colors") or {})
    d["colors"] = colors
    return d


def series_names(p):
    return list(p["series"].keys())


def theme_names(p):
    return list(p["themes"].keys())


def color_preset_names(p):
    return list(p.get("colors", COLOR_PRESETS).keys())


def valid_hex(s):
    return bool(HEX.match(s.strip())) if s else False


def palette(presets):
    """Every colour the presets already use, as [(label, "#RRGGBB")].

    Read straight off the same structures the renderer resolves against, so a
    look added to COLOR_PRESETS or a series added to lss_presets.json turns up
    in the picker without the GUI being touched.

    De-duplicated by colour, first name wins: the colour presets come first
    because their names say what the colour IS ("Canopy sky"), where a series
    or occasion name says what it is FOR. Labels carry the hex so the list is
    still readable where a swatch is not available.
    """
    out, seen = [], set()

    def add(label, h):
        h = (h or "").strip().upper()
        if valid_hex(h) and h not in seen:
            seen.add(h)
            out.append((f"{label} {h}", h))

    for name, p in (presets.get("colors") or COLOR_PRESETS).items():
        for key, role in COLOR_ROLES:
            add(f"{name} {role}", p.get(key, ""))
    for name, s in (presets.get("series") or {}).items():
        add(name, s.get("accent", ""))
        add(f"{name} highlight", s.get("accent2", ""))
    for name, t in (presets.get("themes") or {}).items():
        add(name, t.get("accent", ""))
        add(f"{name} highlight", t.get("accent2", ""))
        for i, c in enumerate(t.get("cycle") or [], 1):
            add(f"{name} cycle {i}", c)
    return out


def _lum(h):
    c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    c = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def contrast(a, b):
    """WCAG contrast ratio between two colours."""
    x, y = _lum(a), _lum(b)
    hi, lo = max(x, y), min(x, y)
    return (hi + 0.05) / (lo + 0.05)


def contrast_on(h, bg=DEFAULT_BG):
    """WCAG contrast ratio of a colour against a given background."""
    return contrast(h, bg)


def auto_foreground(bg):
    """A silhouette/text colour that is actually legible on `bg`.

    Only used when nothing has specified one. Picking whichever of bone or ink
    reads better means a bare custom background can never render invisible
    text; on the default sky it returns bone, i.e. the original behaviour."""
    return DEFAULT_FG if contrast(bg, DEFAULT_FG) >= contrast(bg, DEFAULT_BG) else DEFAULT_BG


def series_geometry(series_key, presets):
    s = presets["series"].get(series_key) or list(presets["series"].values())[0]
    return s.get("geometry", "steps")


# Which scene a series is set in, and so which silhouette styles it can wear.
# Shipped in code because lss_presets.json is the user's own file and is never
# overwritten by an update - an existing install has no "scene" key to read.
SERIES_SCENE = {
    "Sounds of the City": "town",
    "Sounds in Spaces": "town",
    "Sounds in Towns": "town",
    "Sounds of Nature": "nature",
}


def series_scene(series_key, presets):
    """A "scene" key in the JSON wins, then the shipped map, then the geometry
    the series already uses - so a series someone added themselves still
    resolves to something rather than failing."""
    s = presets["series"].get(series_key) or {}
    if s.get("scene"):
        return s["scene"]
    if series_key in SERIES_SCENE:
        return SERIES_SCENE[series_key]
    return "nature" if s.get("geometry") == "curves" else "town"


# Which silhouette a series draws when --style says nothing. A SERIES rather
# than a scene default, because the three town series do not want the same
# one: Towns is houses while City stays on blocks. Shipped in code for the same
# reason SERIES_SCENE is - an existing lss_presets.json has no key to read.
SERIES_STYLE = {
    "Sounds in Towns": "houses",
    "Sounds of Nature": "mountains_forest",
}


def series_style(series_key, presets):
    """The silhouette a series defaults to.

    A "style" key in the JSON wins, then the shipped map, then the scene's own
    first style. Anything the scene cannot actually wear is ignored rather than
    passed on to fail check() later.
    """
    import lss_scene
    scene = series_scene(series_key, presets)
    ok = lss_scene.SCENE_STYLES[scene]
    s = presets["series"].get(series_key) or {}
    want = s.get("style") or SERIES_STYLE.get(series_key)
    return want if want in ok else lss_scene.DEFAULT_STYLE[scene]


def theme_extras(theme_key, presets):
    """(suffix, cycle_colours, cycle_minutes) for a theme."""
    t = presets["themes"].get(theme_key) or {}
    return (t.get("suffix", ""), list(t.get("cycle", [])),
            float(t.get("cycle_minutes", 3)))


def resolve(series_key, theme_key, presets, custom=None):
    """Return (series_name, accent). custom overrides everything."""
    s = presets["series"].get(series_key) or list(presets["series"].values())[0]
    name, accent = s["name"], s["accent"]
    t = presets["themes"].get(theme_key) or {}
    if t.get("accent"):
        accent = t["accent"]
    suffix = t.get("suffix", "")
    if suffix:
        name = f"{name} {suffix}"
    if custom and valid_hex(custom.get("accent", "")):
        accent = custom["accent"].strip()
    return name, accent


def color_preset(preset_key, presets):
    return (presets.get("colors") or COLOR_PRESETS).get(preset_key) or {}


def variants(preset_keys, theme_key, presets, accent, custom=None):
    """Several colour presets resolved against the same series, occasion and
    custom colours - one entry per preset, in the order given.

    Each entry is the three colours a frame actually has, so the caller renders
    one thumbnail per entry off a single pass over the audio. Resolution goes
    through resolve_colors() like everything else, which is what keeps a
    variant identical to rendering that preset on its own.
    """
    out = []
    for k in preset_keys:
        bg, fg, acc = resolve_colors(k, theme_key, presets, accent, custom)
        out.append({"name": k, "background": bg, "foreground": fg,
                    "accent": acc})
    return out


def variant_set(primary_key, extra_keys, theme_key, presets, accent, custom=None):
    """The colours one comparison should render, the chosen one first.

    The Colours choice is always in the set: the extras are picked to compare
    AGAINST it, so dropping it would leave out the one look you started from.
    Order-preserving and de-duplicated, so also ticking the primary in the list
    costs nothing.
    """
    keys = [primary_key] + [k for k in extra_keys if k != primary_key]
    return variants(keys, theme_key, presets, accent, custom)


def resolve_colors(preset_key, theme_key, presets, accent, custom=None):
    """Return (background, foreground, accent).

    A colour preset owns the sky: it always supplies background and foreground.
    It only supplies the accent when no seasonal theme is doing so, since a
    theme's accent and colour cycle are an explicit choice worth keeping - so
    'Night + 4th of July' is the holiday cycle over the night sky. Explicit
    custom colours beat everything.

    `accent` comes in already resolved by resolve(), so a theme has had its say
    by the time we get here; theme_sets_accent says whether it did.
    """
    p = color_preset(preset_key, presets)
    theme_sets_accent = bool((presets["themes"].get(theme_key) or {}).get("accent"))

    background = p.get("background") or DEFAULT_BG
    foreground = p.get("foreground") or ""
    if p.get("accent") and not theme_sets_accent:
        accent = p["accent"]

    custom = custom or {}
    if valid_hex(custom.get("background", "")):
        background = custom["background"].strip()
    if valid_hex(custom.get("foreground", "")):
        foreground = custom["foreground"].strip()
    if valid_hex(custom.get("accent", "")):
        accent = custom["accent"].strip()

    return background, foreground or auto_foreground(background), accent
