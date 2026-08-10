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

## The window

The audio file and the output folder sit at the top, and **Render**, **Thumbnail only** and
**Preview at** sit at the bottom, always visible. Everything else is on four tabs, grouped by what
it decides:

| Tab | What it decides |
|---|---|
| **Slate** | what the frame says — place, city, conditions, date, start time, number, file name |
| **Look** | series, silhouette, detail, and the two silhouette treatments |
| **Colour** | occasion, colours, custom colours, and the Compare list |
| **Shape** | how loudness becomes height — scaling, dynamics, tower width, stacked rows |
| **Video** | the encode only — resolution and chroma. Thumbnail only skips all of it |

`lss_render.py --help` is grouped the same way.

## Silhouettes

Each series is set in a **scene**, and the scene decides which silhouettes it can wear. Pick one
with the **Silhouette** dropdown, or `--style` from the command line:

| Scene | Series | Styles |
|---|---|---|
| town | Sounds of the City, in Towns, in Spaces | `blocks` (default), `houses` |
| nature | Sounds of Nature | `topo` (default), `mountains`, `forest`, `mountains_forest` |

The defaults are what these series have always drawn, so existing commands are unaffected.

- **`blocks`** — the skyline of rectangular towers, one per block of the recording.
- **`houses`** — a low residential row with varied rooflines and the occasional street tree. Only
  the loudest few percent of the recording earns a taller block, so the skyline stays a town rather
  than a city.
- **`topo`** — the smoothed contour line.
- **`mountains`** — a range whose summits sit where the loud passages are, taller for louder.
- **`forest`** — a treeline. With nothing else carrying the signal, tree height follows loudness.
- **`mountains_forest`** — trees in front, mountains behind.

In the nature silhouettes the **mountains carry the audio and the trees do not**: tree size is
pinned to the frame, so a quiet recording still gets a normal-looking treeline while the mountains
rise and fall with the recording. As the playhead crosses, trees fill solid; ahead of it they are
present but faint. The mountains only ever carry flat lit and shadow faces kept close to the sky
colour, so the trees stay the thing that reads as progress.

How far a face sits from the sky is measured against the sky, not fixed — a bright silhouette can
be pushed further back and still read, while a mid-toned accent goes muddy at the same setting.
That keeps the played and unplayed halves at a comparable weight in every palette.

Everything generated is seeded from the recording's own loudness envelope, so the same file always
renders the same shape. The seed is written into the render's `.json` sidecar.

```
py lss_studio\lss_render.py recording.flac --style mountains_forest ...
py lss_studio\lss_render.py recording.flac --style houses --colors Morning ...
```

Asking for a silhouette a series does not have is an error, not a silent fallback:

```
--style 'houses' is not available in the nature scene. Choose from: topo, mountains,
forest, mountains_forest. 'houses' belongs to the town scene.
```

To put your own series in a scene, add `"scene": "nature"` or `"scene": "town"` to it in
`lss_presets.json`. Leave it out and it is worked out from the series name, then from its geometry.

### Detail

**Silhouette detail** (`--detail Coarse | Default | Fine`, or a number like `1.2`) sets how much
shape those styles carry — how many summits, how many trees, how many houses. It counts features,
not pixels, so a thumbnail and the full video of the same recording read identically; only the
resolution differs. Tower width stays the control for `blocks`.

### Checking a look without an encode

Tick **Thumbnail only** and set **Preview at** to a percentage — or `--thumb-only --progress 0.5`
from the command line — and the thumbnail is drawn as a mid-playback frame instead of the unplayed
state, so you can see the fill behaviour in seconds rather than waiting for a full video:

```
py lss_studio\lss_render.py recording.flac --style mountains_forest --thumb-only --progress 0.5 ...
```

Both controls sit beside the Render button, outside the tabs, since they decide what actually gets
made. Leave Preview at blank for the unplayed frame.

Two further switches change the treatment. **Trees before the playhead** (`--tree-ahead
faint|outline`) sets how a tree looks before the playhead reaches it, and **Mountain faces**
(`--mountain-face twotone|outline`) whether the mountain faces carry flat lit and shadow tones or
only a ridgeline. The first of each is the default; `outline` on both gives a lighter, more linear
frame. In the window each greys out for a silhouette it does not reach — `blocks` has no trees to
draw faint, `forest` has no mountain faces.

## Colours

Pick a **Colours** preset to set the time of day. Each one sets the sky, the silhouette, and the
accent the playhead reveals, chosen together so the skyline still reads at thumbnail size:

| Preset | Sky | Skyline | Looks like |
|---|---|---|---|
| Morning | deep gold | white | low warm sun |
| Night | deep blue | bone | the original look |
| Evening | burnt orange | white | sunset |
| Canopy | sunlit green | amber | lit against foliage |
| Alpine | cold blue | snow white | high thin air, glacier-ice playhead |
| Alpenglow | violet dusk | pale pink | the last sun still on the peaks |
| Mist | fog grey-green | dark pine | fog off the water |
| Aurora | arctic night | starlight | northern lights running the timeline |

The last four were chosen against the nature silhouettes, where sky fills most of the frame, and
read as outdoors without reaching for foliage green or bark brown. They still work for any scene.

Presets are deliberately scene-agnostic — the same four work for city, town, nature and spaces,
because what makes a recording look like a city is the silhouette shape, not the colour. So
"Roosevelt Row at Morning" and "Roosevelt Row at Night" are clearly different images that both
still read as the same place.

Leave it on **None** to keep the series colour, exactly as before.

A seasonal **Occasion** keeps its own accent and colour cycling on top of whichever sky the
preset chose, so Night + 4th of July is still the red/white/blue cycle over a night sky.

### Custom colours

The three **Custom** rows — sky, silhouette, accent — override whichever preset is chosen. Leave
one blank and it follows the preset; leave the silhouette blank in particular and it picks
whichever of bone or ink stays readable on your sky, so a custom background can never render the
skyline invisible.

Each row has a dropdown of every colour the presets, series and occasions already use, named and
with its hex — "Canopy sky #357A2B", "Night sky #13232E" — so a look can be built out of colours
already known to work together. Picking one fills the hex box; typing a code in the box directly
still works and switches the dropdown to **Custom…**. Type a code that happens to be one of the
named colours and the dropdown says so. The swatch beside each box shows the colour you'll get.

The list is read from the presets themselves, so a colour preset you add to `COLOR_PRESETS` or a
series you add to `lss_presets.json` appears in all three dropdowns with no further work.

### Comparing several colours at once

Tick **Thumbnail only**, then tick as many colours as you like in the **Compare** list on the
Colour tab. You get **the Colours choice plus every ticked colour**, one thumbnail each, all in the
same folder and off a single pass over the audio — so three looks cost barely more than one. The
silhouette is identical in each, because the geometry is built once and only the colours change.

The extras are there to compare *against* whatever Colours is set to, so that one is always in the
set; ticking it in the list as well changes nothing. Leave the list empty and you get the single
Colours choice, named the way it always was.

```
py lss_studio\lss_render.py recording.flac --thumb-only --colors Night --variants "Aurora,Canopy" ...
```

Each file carries its palette in the name, since a folder of variants is otherwise unreviewable —
two nearby skies are genuinely hard to tell apart once they're separate files:

```
007 - Roosevelt Row/
  007 - Roosevelt Row_thumb_Night_bg-13232E_fg-F0E7D6_acc-CF7A34.png
  007 - Roosevelt Row_thumb_Aurora_bg-0C1A2B_fg-E9F2F3_acc-3DD68C.png
  007 - Roosevelt Row_thumb_Canopy_bg-357A2B_fg-F2D9A0_acc-72491E.png
  007 - Roosevelt Row_render.json
```

The sidecar lists which file got which palette. **Preview at** works alongside it, so you can
compare mid-playback frames rather than unplayed ones.

The list is greyed out unless Thumbnail only is ticked — comparing looks is the point, and six full
video encodes of one recording is not something to trigger by accident. A custom sky would override
every colour in the set and render them all identically, so that combination is refused rather than
silently wasted.

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

## Command line

`py lss_studio\lss_render.py --help` lists every flag, grouped as **slate**, **look**, **shape**
and **output** — the same four groups the window uses.

Two things changed in 1.3.0:

- **`--preset` is now `--series`.** It always chose a series, while `--colors` chooses a colour
  preset, so "preset" meant two different things. `--preset` still works and always will; nothing
  you have written needs updating.
- **`--accent2` is gone.** Nothing ever drew with it — the playhead's second colour comes from an
  occasion's `cycle`, not from `accent2`. A script passing `--accent2` will now error; drop the
  flag and the render is identical. An `"accent2"` in your `lss_presets.json` is still read
  without complaint, and those colours are still offered in the colour dropdowns as that preset's
  "highlight".

The render sidecar's `colors` field is likewise now `color_preset`. Older sidecars are unaffected.

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
  lss_scene.py               generative silhouette shapes
  lss_draw.py                drawing
  lss_presets.py             preset loading
  lss_presets.json           YOUR presets (edit freely)
  lss_update.py              self-update
  VERSION                    current version
```

See `RELEASING.md` for how to publish an update.
