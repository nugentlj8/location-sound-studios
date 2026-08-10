# Location Sound Studios

Turns a field-recording audio file into a matching YouTube thumbnail and a
full-length video whose skyline is drawn from the recording's own loudness.

## Install (Windows, once)

1. Install **Python** from [python.org](https://www.python.org/downloads/).
   On the first installer screen, tick **"Add Python to PATH."**
2. Download this repository: green **Code** button → **Download ZIP**, then
   unzip it somewhere permanent like `Documents\LSS`.
3. Double-click **`Setup.bat`**. It installs ffmpeg and the two Python
   packages. If it installs ffmpeg, close the window and run it once more.

Then launch any time with **`Location Sound Studios.bat`**.

### Font (recommended)

Install **Barlow Condensed** (Bold) from Google Fonts, then in Command Prompt:

```
setx LSS_FONT "C:\Users\YOU\AppData\Local\Microsoft\Windows\Fonts\BarlowCondensed-Bold.ttf"
```

Open a fresh window afterward. Without it the app falls back to Arial.

## Colours

Pick a **Colours** preset to set the time of day. Each one sets the sky, the silhouette, and the
accent the playhead reveals, chosen together so the skyline still reads at thumbnail size:

| Preset | Sky | Skyline | Looks like |
|---|---|---|---|
| Morning | deep gold | white | low warm sun |
| Night | deep blue | bone | the original look |
| Evening | burnt orange | white | sunset |
| Canopy | sunlit green | amber | lit against foliage |

Presets are deliberately scene-agnostic — the same four work for city, town, nature and spaces,
because what makes a recording look like a city is the silhouette shape, not the colour. So
"Roosevelt Row at Morning" and "Roosevelt Row at Night" are clearly different images that both
still read as the same place.

Leave it on **None** to keep the series colour, exactly as before.

A seasonal **Occasion** keeps its own accent and colour cycling on top of whichever sky the
preset chose, so Night + 4th of July is still the red/white/blue cycle over a night sky.

**Custom sky** overrides the preset. Fill in the background and, if you want, the silhouette
colour; leave the second box blank and it picks whichever of bone or ink stays readable.

To add your own preset, edit `COLOR_PRESETS` at the top of `lss_studio/lss_presets.py` — nothing
in the render code needs touching. You can also add a `"colors"` block to `lss_presets.json`,
which overrides what ships in code and is never overwritten by an update.

From the command line:

```
py lss_studio\lss_render.py recording.flac --colors Morning ...
py lss_studio\lss_render.py recording.flac --colors Canopy --accent "#8A5C28" ...
py lss_studio\lss_render.py recording.flac --background "#2B1B3D" --foreground "#EDE4F2" ...
py lss_studio\lss_render.py --list-presets
```

`--background` and `--foreground` override whichever preset is chosen, and `--accent` overrides
its accent. Give a background without a foreground and it derives a readable one for you, so a
custom sky can never leave the skyline invisible.

## Where renders go

By default `Z:\Sounds of the City\LSS Renders`. To send one render somewhere else:

```
py lss_studio\lss_render.py recording.flac --outdir renders ...
```

To change the default permanently, either set `LSS_OUTDIR`:

```
setx LSS_OUTDIR "D:\LSS Renders"
```

or add an `"outdir"` key to `lss_presets.json`. `--outdir` wins over `LSS_OUTDIR`, which wins
over the JSON key, which wins over the built-in default — so the flag stays a per-run switch and
never goes sticky.

## Naming

Enter a **Number** and the render folder leads with it, zero-padded so the list still sorts
correctly past episode 9:

```
003 - Phoenix Monsoon Ambience/
  003 - Phoenix Monsoon Ambience_thumb.png
  003 - Phoenix Monsoon Ambience.mp4
  003 - Phoenix Monsoon Ambience_render.json
```

Leave the number blank and folders are named the way they always were. Either way an existing
folder is never overwritten — a repeat render becomes `..._2`.

## Updates

The app checks this repository for a newer version each time it opens. If one
exists it asks whether to update; if you say yes it downloads the new files and
uses them next launch. If GitHub is unreachable it just runs the copy you have.

Your `lss_presets.json` is **never** overwritten by an update, so any presets
you add stay yours. The previous version is kept in `lss_studio/previous/`; to
roll back, run `py lss_studio\lss_update.py --rollback` from the repo folder.

## Files

```
Location Sound Studios.bat   launch
Setup.bat                    one-time setup
lss_studio/
  lss_studio.py              the window
  lss_render.py              render engine
  lss_draw.py                drawing
  lss_presets.py             preset loading
  lss_presets.json           YOUR presets (edit freely)
  lss_update.py              self-update
  VERSION                    current version
```

See `RELEASING.md` for how to publish an update.
