"""Render the star-field review set: two styles, both playback states, every
palette that allows a field. Palette and state go in the filename, because a
folder of these is otherwise unreviewable.

    py tools/stars_preview.py        # 24 stills into renders/stars/
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "lss_studio"))

import lss_render as R
import lss_presets as P
from identity_check import make_audio, AUDIO      # the same clip, regenerated
                                                  # if renders/ has been cleared
OUT = os.path.join(ROOT, "renders", "stars")

STYLES = [
    ("city", "Sounds of the City", "town", True,
     {"place": "SOUTH MOUNTAIN PARK", "city": "PHOENIX",
      "conditions": "CLEAR 78F"}),
    ("mountains_forest", "Sounds of Nature", "nature", False,
     {"place": "MOUNT LEMMON", "city": "TUCSON", "conditions": "STILL 41F"}),
]


def main():
    make_audio()
    presets = P.load()
    pals = [k for k in P.star_presets(presets) if k != "None (series colour)"]
    print("palettes:", ", ".join(pals))
    for style, series, scene, filled, slate in STYLES:
        for pal in pals:
            for prog in (0.0, 1.0):
                name, base = P.resolve(series, "None (use series colour)",
                                       presets, None)
                bg, fg, acc = P.resolve_colors(pal, "None (use series colour)",
                                               presets, base, None)
                key = f"{style}_{pal.lower()}_p{int(prog)}"
                cfg = {
                    "series": name, "date": "2026-08-11", "start": "06:30 PM",
                    "number": "7", "number_style": "No.",
                    "background": bg, "foreground": fg, "accent": acc,
                    "color_preset": pal,
                    "geometry": P.series_geometry(series, presets),
                    "scene": scene, "style": style,
                    "detail": "Default", "towers": "Default",
                    "scale": "Skyline (rank)", "dynamics": "More",
                    "height_stat": "peak", "align_loud": True,
                    "rows": 1, "filled": filled, "progress": prog,
                    "stars": True, "thumb_only": True, "thumb_width": 1920,
                    "audio": AUDIO, "outdir": OUT, "outname": key,
                }
                cfg.update(slate)
                r = R.run(cfg, progress=lambda s: None)
                print(f"  {key}  ->  {os.path.basename(r['thumbnail'])}")


if __name__ == "__main__":
    main()
