#!/usr/bin/env python3
"""Weather stills: every style with weather off, clouds and rain.

Flat filenames rather than run()'s per-render folders, so the set is something
you can arrow through in a viewer:

    <style>_1off.png  <style>_2clouds.png  <style>_3rain.png

...and a second set on the palettes that decide the contrast rule - the
lightest sky, the darkest, and Morning, which has the least contrast to spend
and is therefore the one where a cloud behind the title has to be checked
rather than argued about.

    py tools/weather_samples.py
"""

import glob, json, os, shutil, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "lss_studio"))

import lss_presets as P                                          # noqa: E402
import lss_render as R                                           # noqa: E402

RENDERS = os.path.join(ROOT, "renders")
AUDIO = os.path.join(RENDERS, "_identity_audio.wav")
OUT = os.path.join(RENDERS, "weather")
WORK = os.path.join(OUT, "_work")

STYLES = [("nature", "topo"), ("town", "blocks"), ("nature", "mountains"),
          ("nature", "forest"), ("nature", "mountains_forest"),
          ("town", "houses"), ("town", "city")]
STATES = [("1off", "off"), ("2clouds", "clouds"), ("3rain", "rain")]
SERIES_FOR = {"nature": "Sounds of Nature", "town": "Sounds of the City"}

# The palettes the rule has to survive, not the comfortable middle: Morning has
# 2.40 of sky-to-skyline contrast and Aurora has 15.41, and Mist is the only
# one whose sky is lighter than its silhouette.
EDGE = ["Morning", "Mist", "Aurora"]
# Morning again, four ways. Its slate sits at the top of the frame, which is
# where the cloud band is, so a cloud behind the title is the ordinary case
# there and one seed proves nothing.
MORNING_SEEDS = ["SOUTH MOUNTAIN PARK", "PHOENIX", "PAPAGO", "ENCANTO"]


def cfg_for(scene, style, pal, state, key, place="SOUTH MOUNTAIN PARK",
            width=1920):
    presets = P.load()
    series = SERIES_FOR[scene]
    name, base = P.resolve(series, "None (use series colour)", presets, None)
    bg, fg, acc = P.resolve_colors(pal, "None (use series colour)",
                                   presets, base, None)
    return {
        "series": name, "place": place, "city": "ARIZONA",
        "conditions": "OVERCAST 71F", "date": "2026-09-02",
        "start": "06:30 PM", "number": "12", "number_style": "No.",
        "background": bg, "foreground": fg, "accent": acc,
        "geometry": P.series_geometry(series, presets),
        "scene": scene, "style": style, "detail": "Default",
        "towers": "Default", "scale": "Skyline (rank)", "dynamics": "More",
        "height_stat": "peak", "align_loud": True, "rows": 1,
        "filled": style in ("topo", "blocks", "houses", "city"),
        "progress": 1.0, "thumb_only": True, "thumb_width": width,
        "stars": bool(P.star_color(pal, presets, fg, acc, bg)),
        "color_preset": pal, "weather": state,
        "audio": AUDIO, "outdir": WORK, "outname": key,
    }


def shot(cfg, dest):
    # counts come off the sidecar rather than the cfg: run() copies what it is
    # handed, so the dict here never sees the geometry that was drawn
    r = R.run(cfg, progress=lambda s: None)
    shutil.copyfile(r["thumbnail"], os.path.join(OUT, dest))
    side = glob.glob(os.path.join(r["folder"], "*_render.json"))
    with open(side[0], "r", encoding="utf-8") as fh:
        return json.load(fh)["look"]


def main():
    if not os.path.exists(AUDIO):
        raise SystemExit("run tools/identity_check.py baseline first - it "
                         "makes " + AUDIO)
    shutil.rmtree(OUT, ignore_errors=True)
    os.makedirs(OUT, exist_ok=True)
    R.FONT = R.find_font()

    for scene, style in STYLES:
        for tag, state in STATES:
            wx = shot(cfg_for(scene, style, "Night", state, f"{style}_{tag}"),
                      f"{style}_{tag}.png")
            print(f"  {style:17} {state:6} {wx['cloud_count']:2} clouds "
                  f"{wx['rain_streaks']:5} streaks  seed {wx['weather_seed']}")

    os.makedirs(os.path.join(OUT, "palettes"), exist_ok=True)
    for pal in EDGE:
        for tag, state in STATES[1:]:
            c = cfg_for("town", "city", pal, state, f"{pal}_{tag}")
            # the hex in the filename, so a folder of palettes stays reviewable
            shot(c, os.path.join(
                "palettes", f"{pal}_{tag}_bg-{c['background'][1:].lower()}.png"))
        print(f"  palette {pal}")

    # Morning at the size it is actually judged at, four seeds, clouds and rain
    os.makedirs(os.path.join(OUT, "morning-title"), exist_ok=True)
    for i, place in enumerate(MORNING_SEEDS, 1):
        for tag, state in STATES[1:]:
            c = cfg_for("town", "city", "Morning", state,
                        f"morning_{i}_{tag}", place=place, width=1280)
            shot(c, os.path.join("morning-title",
                                 f"{i}_{place.split()[0].lower()}_{tag}.png"))
        print(f"  Morning seed {i}: {place}")

    # ...and one that does not wait for a seed to do it. The band is forced
    # onto the slate itself, so every cloud lands across the series line, the
    # title and the conditions line at once - the case four seeds might not
    # produce and the one the contrast rule has to survive.
    import lss_scene as SC
    top, horizon = SC.CLOUD_TOP, R._horizon
    SC.CLOUD_TOP, R._horizon = 118.0, lambda cfg, dh: 330.0
    try:
        for tag, state in STATES[1:]:
            shot(cfg_for("town", "city", "Morning", state, f"worst_{tag}",
                         width=1280), os.path.join("morning-title",
                                                   f"0_worst_{tag}.png"))
        print("  Morning worst case: band forced onto the slate")
    finally:
        SC.CLOUD_TOP, R._horizon = top, horizon

    shutil.rmtree(WORK, ignore_errors=True)
    print(f"\n-> {OUT}")


if __name__ == "__main__":
    main()
