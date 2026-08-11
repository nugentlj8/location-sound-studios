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
**Preview at** sit at the bottom, always visible. Everything else is on five tabs, grouped by what
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

| Scene | Series | Styles | Default |
|---|---|---|---|
| town | Sounds of the City | `blocks`, `houses`, `city` | `city` |
| town | Sounds in Spaces | `blocks`, `houses`, `city` | `blocks` |
| town | Sounds in Towns | `blocks`, `houses`, `city` | `houses` |
| nature | Sounds of Nature | `topo`, `mountains`, `forest`, `mountains_forest` | `mountains_forest` |

The default is per **series**, not per scene — the three town series share a scene and want three
different silhouettes. To put your own series on a particular one, add `"style": "houses"` to it in
`lss_presets.json`.

- **`blocks`** — the skyline of rectangular towers, one per block of the recording. The plain
  envelope skyline, and the only town style that stacks under `--rows`.
- **`city`** — the same skyline built as actual buildings: window grids, antennas, a near layer of
  low buildings across the bottom, and depth shading. What `blocks` looks like up close.
- **`houses`** — a low residential row with varied rooflines, windows, doors and street trees. Only
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
renders the same shape — including every window, which house is occupied and which tree is which
shape. The seed is written into the render's `.json` sidecar.

### Inside a town

`houses` draws the street in some detail:

- **Windows and doors**, laid out per house from the recording's seed. Panes are separated by gaps
  of sky rather than by drawn glazing bars — a gap carries the same contrast the silhouette's own
  edge does, where a hairline stroke would disappear on the tighter palettes.
- **Lit windows.** Most are unlit and read as dark openings; a few are lit. Occupancy is decided per
  house first and only then per window, so lit windows cluster into a house instead of speckling
  evenly down the street. A lit window is drawn in whichever of the two colours the playhead is
  *not* showing there — accent ahead of it, silhouette colour behind it — so a house that is
  occupied stays occupied as the playhead crosses it instead of going dark.
- **Facade shading.** Light comes from the right, as it does on the mountains, and the left of each
  house carries a second, dimmer tone. It uses the same measure the mountains do — distance from the
  sky, scaled by how much contrast the palette actually has — at a much smaller step, because in a
  town the houses are what the playhead recolours and they have to stay dominant. With outlines
  instead of a fill there is nothing to divide, so only the division is drawn, and on a house that
  vertical run reads as a building corner.
- **Street trees in two shapes**, evergreen and deciduous, mixed. The nature silhouettes stay
  evergreen-only.

### Inside a city

`city` is the town's parts at a city's density — the same window builder, the same shade, the same
pane minimums, with the numbers a downtown wants rather than a street:

| | `houses` | `city` |
|---|---|---|
| window grid | 27u bays, one storey | 13×15u, full height |
| occupied | 45% of buildings | 95% |
| lit windows | 40% of an occupied one | 50% |

Every block is a building with a floor under its height, where the plain `blocks` line is allowed
to touch its own baseline. Roofs step in about two times in five, and the tallest buildings carry
**antennas** — a plain needle whose tip is the light, capped at 14 so a skyline doesn't turn into a
comb.

**`city` sets its own block width** — 27 blocks across the frame, about 45 design units each,
whatever `--towers` says. The far layer needs a grain of its own: at the near layer's 30–45u the two
read as one layer however they are toned, and width is what fixed that, not tone. So for this style
`--towers` no longer sets how many blocks there are; it changes only how finely the recording is
sampled before each block takes its loudest moment.

| `--towers` | envelope blocks | city blocks | block width |
|---|---|---|---|
| Thick | 28 | 27 | 44.7u |
| Default | 40 | 27 | 44.7u |
| Thin | 64 | 27 | 44.7u |
| Fine | 90 | 27 | 44.7u |
| Auto | 34–72 | 27 | 44.7u |

It is a fixed width rather than a floor on `--towers`, which was tried first and cannot work: `Thick`
is the widest preset there is and still only gives a 47.9u slot, so a 50u floor binds on every preset
including that one and they all collapse to the same count anyway. `blocks` and `houses` are
unaffected — `--towers` means exactly what it always did for them.

#### Depth

A city gets its depth from a near layer standing in front of the skyline, because a single row of
buildings on a single baseline has no way to say the towers are far away. Unlike `mountains_forest`,
which separates its two layers by tone, a city separates its two by **position and a cut of sky** —
both layers are drawn in the same two tones, because they are the same buildings at different
distances.

- **A near layer** — *the same downtown, closer.* Not a second, smaller settlement in front of it:
  the near buildings use the city's own roofline and the city's own window grid, so what separates
  near from far is size, overlap and strength, never the vocabulary. Reach for the town's pitched
  roofs here and the band reads as a village that a city happens to stand behind.
- It stands about 20 units below the skyline's baseline, so the frame has two ground lines a short
  step apart. Its tallest buildings do rise a little past the *shortest* towers, so a block in the
  quietest 4% of the height range can be hidden behind it — which is the price of keeping the tower
  floor low, and the height range is what the render is for. Narrow buildings and deep notches keep
  the band's average top far lower than raising that floor ever did.
- It **carries no audio at all.** Exactly like the treeline in `mountains_forest`, its heights,
  widths and spacing come from the recording's seed and nothing else, so a quiet recording gets the
  same foreground as a loud one and only the skyline behind it breathes.
- **It is one continuous band** — neighbours touch or overlap by up to 20%, never leaving sky between
  them, which is what makes it read as a single nearer plane instead of a row of shapes each
  competing with the skyline behind it. Widths run 30–45u, about a tower's own width.
- **The top edge varies hard**, with deep notches cut right down to 14u about one block in five. Once
  the buildings are narrow and touching, an evenly varied top edge stops reading as buildings and
  becomes a bar across the bottom of the frame. Heights are a profile rather than a draw per
  building: a smoothed run gives the band districts, a per-building draw breaks those up, and the
  notches cut through both.
- Overlap is capped at 20% for the notches' sake. The silhouette at any x is the *max* over
  everything covering it, so heavy overlap is an upper envelope — a smoother — and it eats exactly
  the notches this layer needs. Tallest are built first so the shorter ones in front paint over them,
  the same order the mountains are built in.
- **Bigger windows, fewer of them** — a 1.6× coarser grid than the towers. A nearer building's
  windows *are* larger, and pitch turns out to do more work than tone: at the skyline's own spacing
  the near layer carried identical texture, and two layers with one texture are one layer however
  their tones are set.
- **More lit than the skyline, not less** — 92% of near buildings occupied and 60% of their panes
  lit, so about 55% burn against the towers' 48%. Held low at first on the theory that a quiet near
  layer would keep the skyline dominant, which was wrong twice over: one window in twelve read as an
  abandoned block rather than a restrained one, and the nearer thing is the one you can see into. The
  skyline stays dominant on size and position, which is where dominance actually comes from.
- The odd **deciduous street tree**, kept sparse — with the buildings overlapping there are no gaps
  to sit in, so trees simply stand in front, and enough of them at one height stops reading as
  street trees and starts reading as a hedge across the frame.
- Each near shape cuts a **gap of sky** around itself before it fills, the same trick a street tree
  uses in a town, because the skyline behind it is the same colour only weaker. The gap belongs to
  the *band*, not to each building in it: every stroke goes down first and the fills then paint out
  the shared edges, so only the outside keeps its cut. Where one near building meets another there is
  a **corner seam** instead — a thin line in a tone, never in sky, which would put the gap back.
- **A shaded side on the near buildings, and on the tree crowns too**, light from the right as
  everywhere else, and in the skyline's own shadow tone — the same facet the towers have.
- **The taller blocks carry a lit and a shadow face**, light from the right as everywhere else. The
  division is snapped to a window-column edge, so the facet falls *between* two grids rather than
  slicing one; at this block width it lands on the middle and the building reads corner-on. Short
  blocks are left flat — a roofline already reads them, and faceting the whole row turns the skyline
  back into texture.

So a city frame has **two tones, not four**, measured as distance from the sky: **a lit face at 0.30
and a shadow face at 0.54**, used by the near layer and the skyline alike. One city, one material,
front to back. The corner seam where two near buildings meet is a third value, 0.78, but it is a
3-unit hairline rather than a plane — it lands on the shaded side of the building it belongs to, so
it has to clear 0.54 to be seen at all.

The near layer spent a while a rung nearer — 0.00 body against the skyline's 0.30, and 0.27 against
0.54 — on the reasoning that the front of the frame should be the strongest thing in it. In practice
the band simply read *brighter and cleaner* than the towers: not as the same downtown closer up, but
as a different, better-lit one pasted over it. Tone was never what separated these two layers anyway.
The sky gap cut around every near shape spends the **whole** silhouette-vs-sky contrast, where a rung
of a ladder spends a fraction of one, so giving the rung back costs nothing that was doing any work.

The facets are equal for the same reason. The near split was deliberately the stronger one at 0.27
against the towers' 0.24 — distance costs internal contrast first, so the crisply divided layer
should be the near one — and it is a real effect, worth 1.05× to 1.22× in perceived lightness. But it
was being *added on top of* a body-tone difference, and the two together are what read as two
materials. With the bodies matched there is nothing left for a facet difference to do but reintroduce
the split.

The skyline was softened by moving its **shadow face only**, 0.62 → 0.54. Moving the lit face too
took the whole layer a step further toward the sky, and since the mix runs toward the background that
reads as the towers going darker on a dark palette — a change to the picture's weight, when all that
was wanted was a change to the split. The lit face is the layer's depth; the distance to the shadow
face is its facet. Separate decisions.

0.54 is also the floor on how soft either layer's shadow face can go — the near layer inherits the
constraint along with the tone — and that part is measured rather than judged.
A lit window is drawn at full strength over whichever face it lands on, and on all eight presets the
accent's luminance sits *between* the silhouette colour and the sky — so as a face is mixed toward
the sky its luminance sweeps down and, somewhere in that sweep, crosses the lit colour and erases the
window. That crossing sits around 0.46. Dropping the shadow face to 0.42 would land on it; 0.54 stays
clear on the far side.

> **Known defect — a lit window on a lit face, ahead of the playhead.** On four presets it is very
> nearly invisible until the playhead reaches it: Alpine 1.05, Mist 1.11, Aurora 1.16, Canopy 1.17.
> No choice of face depth rescues those, because the crossing above sits at a different depth in
> every palette and whatever single depth is chosen, one of them is standing on it — measured across
> 0.22–0.42 the worst case never rises above 1.05. The fix is in the **colour a lit pane is drawn
> in**, not the depth ladder: it has to move when its own face crowds it. Deliberately left for its
> own pass, because `_pane()` is shared with `houses` and changing the rule moves that style too.
> Behind the playhead the pair inverts and the same presets measure 2.1 to 3.8, so a finished
> thumbnail — fully played by default — never shows it. See the note on `_pane()` in `lss_draw.py`.

**Overlapping happens in the near layer only, never in the skyline.** Up there the x axis is time: a
building's centre picks its colour under a `cycle` and sets the second its beacon changes over, so
sliding one tower's mass into its neighbour's slot would move a loud moment and make the playhead
appear to re-cross a building it had already passed. Nothing in the near layer answers to the
recording, which is exactly what makes overlap free down there — and it is the cheapest depth in the
frame, since one building plainly in front of another says "near" with no tone at all. It is also
what lets the band stay low enough to keep the towers the subject.

#### How high the towers may go

The ceiling is **a profile across the frame, not a single line.** The slate is not a solid bar — it
is text down the left, a clock on the right and a wide hole in between — so a single ceiling made
every column pay the worst column's price. The free spans are measured from the **real text extents
at render time**, with the actual font at the actual sizes, because that is the whole point:
`PHOENIX` and `SOUTH MOUNTAIN PARK` leave very different amounts of the frame open, and so do a
two-word conditions line and a six-word one.

| slate | columns with room above | extra headroom | tallest tower |
|---|---|---|---|
| `PHOENIX` / `AZ · CLEAR` | 66% of frame | up to 87u | +31% |
| `SOUTH MOUNTAIN PARK` / `PHOENIX AZ · LIGHT WIND CICADAS` | 33% | up to 77u | +27% |

29 units of air are kept around every text box, and the profile is smoothed so the ceiling ramps
rather than steps — a cliff in the skyline that no loud moment put there would read as data. The
smoothing may only ever push the ceiling *down* toward text, never up into it.

**Height still comes entirely from the envelope.** A column's ceiling sets how much room a block
*has*; the recording decides how much of it the block uses, so a quiet block under an empty span
stays quiet. The honest cost is that the same loudness gives a different height depending on where it
lands — a tower under the place name can't be compared against one in the clear. That is a deliberate
trade: a tower that can break up and away from the rest is worth more than strict comparability.

Masts are **proportional to their own building**, about 16% of its height, so the tallest tower in
frame carries the longest antenna. Drawing an absolute length and then trimming it to leftover
headroom did the exact opposite — the tallest tower sat closest to the ceiling, so it had the least
room and wore the shortest stub. The room is therefore reserved in proportion when the building is
sized, which comes out as a *scale* on the whole height range rather than a subtraction from it, and
that is what keeps it monotone: gate a reserve on the antenna threshold instead and a block just over
it comes out shorter than one just under.

The needle is a rectangle, never a stroke: it stands against open sky, so it already carries the
same contrast every roofline has, and being axis-aligned it downsamples without the fringing a
hairline would pick up. The lit tip is the top segment of that needle, at the same width — so it is
bounded by sky above and by the rest of the mast below. That matters because the two contrasts are
complementary: a lit colour against the **sky** is only 1.90 on Morning and 1.80 on Evening, and
against the **body** only 2.02 on Canopy and 1.65 on Alpine. Whichever edge a palette makes weak,
the other one carries the tip; the worst case across all eight presets is 4.56.

### Blinking beacons

In the video the beacons blink, each on its own slow cycle — about 2 seconds lit out of every 3 to
4.6, so 13–20 flashes a minute with the skyline mostly lit and winking rather than mostly dark and
flashing. Phases come from the recording's seed like everything else.

A typical frame carries 7 or 8 of them. Each gets its own period and phase from the seed, and the
periods are all distinct, so the skyline never goes dark all at once however long it runs.

This runs on the same ffmpeg timeline mechanism the live clock already uses, so it costs the encode
essentially nothing — measured at 2560×1440, a full skyline of beacons is inside the noise of the
encode time and adds 1–3% to the file size. A three-hour render still encodes in about the time it
always did.

A thumbnail always shows every beacon lit. Turn the blinking off with **Blink the antenna beacons**
in the window, or `--no-blink`, and they stay steady in the video too.

```
py lss_studio\lss_render.py recording.flac --style city --thumb-only ...
py lss_studio\lss_render.py recording.flac --style city --no-blink ...
```

Where a tree crosses a house, the tree is drawn at full strength with a thin gap of sky around it.
Without that gap a filled tree and a filled house are the same colour and the tree simply
disappears into the row — and on a palette like Morning, where accent and sky are only 1.90 apart,
a tone step alone is not enough to separate them.

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
shape those styles carry — how many summits, how many trees, how many houses, how many buildings in
a city's near layer. It counts features,
not pixels, so a thumbnail and the full video of the same recording read identically; only the
resolution differs. Tower width stays the control for `blocks`.

Windows are much finer than anything else drawn here, so they have a floor: a pane smaller than a
fixed minimum is not drawn. That test is applied **once, when the shape is built**, in the same
1280×720 design units everything else uses — never against the output size. A pane that survives it
therefore exists in the thumbnail and the video alike, which is what keeps the two the same image
rather than two levels of detail. Turning detail up gives more houses, so each is narrower and its
windows stop subdividing before they stop existing:

| `--detail` | houses | house width | panes drawn |
|---|---|---|---|
| Coarse | 11 | 122u | 177 |
| Default | 16 | 84u | 162 |
| Fine | 23 | 58u | 134 |
| 2.5 | 40 | 34u | 73 |

The smallest pane any setting produces is 5 design units — 7.7px on the 1920 thumbnail and 10.2px
on a 2560 video.

### Checking a look without an encode

A thumbnail is drawn as the **finished, fully-played frame** — the state the video ends on. Set
**Preview at** to a percentage, or `--progress` from the command line, to see any other point
instead, so you can check the fill behaviour in seconds rather than waiting for a full video:

```
py lss_studio\lss_render.py recording.flac --style mountains_forest --thumb-only --progress 0.5 ...
py lss_studio\lss_render.py recording.flac --thumb-only --progress 0 ...   # the unplayed frame
```

Both controls sit beside the Render button, outside the tabs, since they decide what actually gets
made. Leave Preview at blank and you get the finished frame; type `0` for the unplayed one.

Two further switches change the treatment. **Trees before the playhead** (`--tree-ahead
faint|outline`) sets how a tree looks before the playhead reaches it, and **Mountain faces**
(`--mountain-face twotone|outline`) whether the mountain faces carry flat lit and shadow tones or
only a ridgeline. The first of each is the default; `outline` on both gives a lighter, more linear
frame. In the window each greys out for a silhouette it does not reach — `blocks` has no trees to
draw faint, `forest` has no mountain faces.

**Solid silhouette instead of outlines** is ticked by default in the window, since it is what the
city, town and skyline styles are usually wanted as. It greys out and clears itself for
`mountains`, `forest` and `mountains_forest`, which define their own fill — trees fill as the
playhead passes them and mountains stay outlined, so there is nothing left for the switch to
decide. Pick one of those and the box simply steps aside; pick a style that does take a fill and
your choice comes back. On the command line nothing changed: `--filled` is still off unless you
pass it, and passing it with one of those three is still an error rather than a silent no-op.

## Colours

Pick a **Colours** preset to set the time of day. Each one sets the sky, the silhouette, and the
accent the playhead reveals, chosen together so the skyline still reads at thumbnail size:

| Preset | Sky | Skyline | Looks like |
|---|---|---|---|
| Morning | deep gold | white | low warm sun |
| Night | deep blue | bone | the original look |
| Evening | burnt orange | white | sunset |
| Canopy | deep forest green | cream | sunlight through the leaves |
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
with its hex — "Canopy sky #1D4029", "Night sky #13232E" — so a look can be built out of colours
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
  007 - Roosevelt Row_thumb_Canopy_bg-1D4029_fg-F1E9D2_acc-E2953A.png
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
py lss_studio\lss_render.py recording.flac --colors Canopy --accent "#A8C24A" ...
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

### What changed in 1.7.1

`--style city` only, and only its tones. No flags, no defaults, no geometry.

The near layer is now drawn in **the same two tones as the skyline behind it** — lit face 0.30,
shadow face 0.54 — where 1.7.0 kept it a rung nearer at 0.00 and 0.27. Shipped that way, the band
read as brighter and cleaner than the towers rather than as the same buildings closer up. The layers
were never parted by tone in the first place: the cut of sky around every near shape does that, and
it spends far more contrast than a rung of a ladder can. The corner seam between near buildings moved
with the face it sits on, 0.55 → 0.78, or it would have vanished into it.

Every other silhouette renders byte-for-byte what it rendered in 1.7.0, verified by SHA.

### What changed in 1.7.0

`--style city` is substantially redrawn. No defaults moved for any other style and no flag changed
its name, but one flag changed what it reaches:

| | Was | Now |
|---|---|---|
| `--towers` for `--style city` | set the block count | **no longer sets it** — city is always 27 blocks at ~45u; the flag now only changes how finely the recording is sampled before each block takes its loudest moment |

`--towers` is unchanged for `blocks` and `houses`.

What the style gained: a near layer of the same buildings seen closer — narrower, touching, deeply
notched, more brightly lit than the towers; a per-column ceiling measured from the slate's own text
so the skyline rises higher where nothing is above it; and masts proportional to their own building.
See [Depth](#depth) and
[How high the towers may go](#how-high-the-towers-may-go).

Every other silhouette is untouched — `blocks`, `houses`, `topo`, `mountains`, `forest` and
`mountains_forest` all render byte-for-byte what they rendered in 1.6.2, verified by SHA against a
worktree of the shipped version across both playback states and both fill modes.

### What changed in 1.6.0

One default moved:

| | Was | Now | Old behaviour |
|---|---|---|---|
| Sounds of the City silhouette | `blocks` | `city` | `--style blocks` |

`city` is a **new style** rather than a richer `blocks`, so `blocks` itself is untouched — Sounds in
Spaces still draws exactly what it drew, `--rows` still stacks, and `--style blocks` renders
byte-for-byte what it rendered in 1.5.0. Nature and Towns are likewise byte-identical.

### What changed in 1.5.0

Three defaults moved. Each one changes what an existing command renders, and each has an explicit
flag that brings the old behaviour back unchanged:

| | Was | Now | Old behaviour |
|---|---|---|---|
| Sounds in Towns silhouette | `blocks` | `houses` | `--style blocks` |
| Sounds of Nature silhouette | `topo` | `mountains_forest` | `--style topo` |
| Thumbnail playback point | unplayed | fully played | `--progress 0` |

In the window, the Silhouette dropdown now follows the series you pick until you choose a
silhouette yourself, and **Preview at** starts at 100.

Nothing about the nature silhouettes themselves changed — `--style mountains_forest --progress 1`
renders byte-for-byte what it rendered in 1.4.2. Only which style Sounds of Nature reaches for by
default is different.

### What changed in 1.3.0

Two things:

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
