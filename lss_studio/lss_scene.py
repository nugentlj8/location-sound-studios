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
from typing import NamedTuple

import numpy as np

DW, DH = 1280.0, 720.0           # the design basis, shared with compose()
                                 # DH is the FEATURE basis and never changes:
                                 # a tree, a house and a window are the size
                                 # they always were. A frame taller than 16:9
                                 # moves the ground and the ceiling instead -
                                 # see vlayout() at the end of this section.

LO_DB, HI_DB = -60.0, -6.0       # fixed-scale window
DYNAMICS = {"Natural": (5, 95), "More": (12, 88), "Most": (20, 80)}
SKYGAMMA = {"Natural": 1.2, "More": 1.6, "Most": 2.2}

# Which silhouettes each scene can wear. The first entry is that scene's
# default, i.e. the behaviour that predates styles existing at all.
SCENE_STYLES = {"nature": ["topo", "mountains", "forest", "mountains_forest"],
                "town": ["blocks", "houses", "city"]}
DEFAULT_STYLE = {s: v[0] for s, v in SCENE_STYLES.items()}
# styles drawn by this module. The other two are the original envelope line.
#
# 'city' is a separate style rather than a richer 'blocks' on purpose: blocks
# is the plain envelope skyline that stacks under --rows, and two series still
# want exactly that. Giving the detail its own name leaves blocks alone and
# keeps --rows meaning what it always did.
SILHOUETTE = {"mountains", "forest", "mountains_forest", "houses", "city"}

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
BLINK_STYLES = {"city"}                 # the only style with beacons to blink
# ...and the styles --filled reaches. The three it misses define their own
# fill - trees fill as the playhead passes them, mountains stay outlined - so
# there is nothing for the switch to decide. check() and the GUI read this same
# set, so the window greys out exactly what the validator would reject.
FILLED_STYLES = {"topo", "blocks", "houses", "city"}

# --- vertical layout, design units -----------------------------------------
# The slate's "CITY . CONDITIONS" baseline sits at 360, i.e. half the frame, so
# nothing here may rise above ~389 or the silhouette collides with the text.
GROUND = 690.0                   # where tree trunks stand
MTN_BASE = GROUND                # the range stands on the same ground line the
                                 # trees do, rather than running off the bottom
                                 # edge. Every other style leaves a strip of
                                 # frame below its baseline - blocks and houses
                                 # on TOWN_BASE, trees on GROUND - and a range
                                 # that alone bled off the edge read as a
                                 # different drawing. Deriving it from GROUND
                                 # rather than repeating 690 is what keeps the
                                 # two lines together if either ever moves.
MTN_TOP_MIN = 500.0              # the quietest recording's tallest summit
MTN_TOP_MAX = 392.0              # the loudest recording's tallest summit, held
                                 # just clear of the slate baseline at 360. The
                                 # two are absolute y, so raising MTN_BASE
                                 # shortens the range rather than sliding it up
                                 # into the text - which is the intent: the
                                 # summits sit where they always did.
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
HOUSE_SIDE_F = 0.30              # how much of a house's width is its shaded
                                 # side. A flat division, like the mountain
                                 # crease - the point is the facet, not a
                                 # physically derived surface
TOWN_TREE_P = 0.28               # chance of a street tree between two houses
TOWN_TREE_H = 0.155 * DH         # taller than the rooflines. A street tree the
                                 # same height as a house disappears into it
                                 # once both are filled in the same colour, so
                                 # it has to clear the roof to read at all.
                                 # Like the houses, it does not answer to the
                                 # audio.
TOWN_DECID_P = 0.45              # chance a street tree is deciduous rather
                                 # than evergreen. A town has both; the nature
                                 # styles are deliberately left evergreen-only

# --- windows, doors -------------------------------------------------------
# The finest detail anything here draws, and the only detail small enough for
# the design-unit basis to matter. Two rules keep a thumbnail and a video the
# same image rather than two levels of detail:
#
#   1. Panes are gated at BUILD time, in design units, never at draw time - so
#      whichever panes survive exist at every output size identically.
#   2. A pane is subdivided by a GAP filled with sky, never by a drawn mullion.
#      There is no thin stroke anywhere in this detail, which is what lets it
#      survive the palettes with the least contrast to spend.
#
# Detail therefore reaches windows the same way it reaches everything else, by
# changing how many houses there are: more houses are narrower houses, and a
# bay that falls under PANE_MIN simply has no window.
PANE_MIN = 5.0                   # design units. Below this a pane is dropped
                                 # rather than drawn as mush - 7.5px on the
                                 # 1920 thumbnail, 10px on the 2560 video
PANE_GAP = 3.0                   # the sky gap between panes IS the mullion
BAY_W = 27.0                     # facade width per window bay
WIN_W_F, WIN_H_F = 0.52, 0.34    # window size, per bay and per facade height
WIN_SILL_F = 0.60                # window centre, up from the base
DOOR_W_F, DOOR_H_F = 0.40, 0.58  # door size, per bay and per facade height
TOWER_BAY_W, TOWER_ROW_H = 23.0, 27.0    # a tall block gets a grid instead
# Mostly unlit, a few lit - and clustered, because a house is occupied or it is
# not. Uniform speckle across every house reads as noise; a lit house beside a
# dark one reads as a street.
HOUSE_OCCUPIED_P = 0.45          # chance a house is occupied at all
HOUSE_LIT_P = 0.40               # chance one of ITS panes is lit
TOWER_OCCUPIED_P = 0.85          # a block of flats nearly always has someone in
TOWER_LIT_P = 0.30

# --- the city -------------------------------------------------------------
# Same parts as the town, at a different density. A city block is narrow and
# tall where a house is wide and low, so the window grid is finer and far more
# of it is lit - which is the whole difference between a street at night and a
# downtown at night.
CITY_MIN_H = 0.075 * DH          # even the quietest block is a BUILDING. The
CITY_MAX_H = 0.345 * DH          # envelope line is allowed to touch its
                                 # baseline; a building with windows in it is
                                 # not, or the street has a hole in it.
                                 #
                                 # The gap between them is the whole room the
                                 # recording has to move in, so the MIN stays
                                 # low: raising it to keep short blocks clear of
                                 # the near layer cost a tenth of that range,
                                 # and skyline height is what the render is FOR.
                                 # A near layer that hides the quiet blocks is a
                                 # near layer built wrong, not a floor set too
                                 # low - see FORE_H.
                                 #
                                 # The MAX is within a few units of its ceiling
                                 # and cannot usefully rise: the tallest roof
                                 # carries the tallest antenna, and that tip has
                                 # to stay clear of the slate baseline at 360,
                                 # which puts roof, setback and mast together
                                 # within about 3 units of the text already.
CITY_GAP_F = 0.10                # of the slot width, so the blocks read as
                                 # separate buildings without a gappy skyline
CITY_BLOCK_W = 50.0              # the slot width a city block gets, in design
                                 # units, independent of how many envelope
                                 # blocks --towers asked for. The far layer
                                 # needs its own grain: at the near layer's
                                 # 30-45u the two read as one layer however
                                 # they are toned, and it was WIDTH that fixed
                                 # that, not tone.
                                 #
                                 # It is a fixed width rather than a floor on
                                 # --towers, and that was tried the other way
                                 # first. The floor cannot work: --towers Thick,
                                 # the widest preset there is, gives a 47.9u
                                 # slot, so a 50u floor binds on every preset
                                 # including that one and they all collapse to
                                 # the same count. --towers therefore does not
                                 # set city block width - see the note on it in
                                 # main(). Set 0 to take one block per envelope
                                 # block again, which is what blocks and houses
                                 # still do.
CITY_SIDE_F = 0.28               # shaded side, as HOUSE_SIDE_F for a house
CITY_BAY_W, CITY_ROW_H = 13.0, 15.0      # the finer grid
CITY_OCCUPIED_P = 0.95           # a downtown block is essentially always in use
CITY_LIT_P = 0.50                # and half of it is lit - "busy" is this number
CITY_SETBACK_P = 0.42            # chance a roof steps in rather than being flat
CITY_SHADE_T = 0.40              # only a block this tall earns a shadow face. A
                                 # short one is already read by its roofline,
                                 # and faceting every block in the row turns the
                                 # skyline back into texture

# --- the city's near layer -------------------------------------------------
# The near end of the SAME downtown - not a second, smaller settlement in front
# of it. Every part is the city's own: _roofline for the roof, the city's window
# grid at a fraction of its occupancy. What separates near from far is size,
# overlap and strength, never the vocabulary. Reach for the town's pitched roofs
# here and the band reads as a village that a city happens to stand behind.
#
# It exists to say the towers are FAR, which is the one thing a single row of
# blocks on a single baseline cannot say - so it carries no audio whatever.
# Exactly like the treeline in mountains_forest: every number below comes from
# the seed alone, so a quiet recording gets the same foreground as a loud one
# and only the skyline behind it breathes.
FORE_BASE = GROUND               # 20u below TOWN_BASE, 30u above the frame
                                 # edge. Two ground lines a short step apart is
                                 # the whole depth cue; one baseline carrying
                                 # everything is what made the city read flat.
FORE_W = (30.0, 45.0)            # design units - about a tower's own 30u, not
                                 # the 34-104 tried first. Width is the whole
                                 # difference between a near CITY and a strip of
                                 # warehouses: a shape five times wider than the
                                 # towers behind it reads as squat whatever its
                                 # tone or its window scale, because nothing in a
                                 # downtown has that proportion. Narrow also
                                 # means MORE of them, which is what gives the
                                 # top edge somewhere to put a notch.
FORE_H = (38.0, 82.0)            # the ordinary run of them. The tallest reach
                                 # y=608, a little past the shortest tower top
                                 # at 615.6, so a block in the quietest ~4% of
                                 # the range can be hidden behind the band. That
                                 # is the price of keeping CITY_MIN_H low, and
                                 # it is the right way round: raising the floor
                                 # to clear the band cost a tenth of the height
                                 # range, where narrow buildings and deep
                                 # notches drop the band's average top further
                                 # for nothing...
FORE_NOTCH_P = 0.22              # ...and this often, a deep one instead. The
FORE_NOTCH_H = (14.0, 30.0)      # notches are the point: once the buildings are
                                 # narrow and touching, an evenly varied top edge
                                 # stops reading as buildings and becomes a bar
                                 # across the bottom of the frame. A hole cut
                                 # right down to 14u is what says these are
                                 # separate near things rather than one mass, and
                                 # it lets the skyline show through between them.
FORE_SLOW_F = 0.45               # how much of the height comes from a smoothed
                                 # run rather than a per-building draw. Pure
                                 # independent draws give a comb; a slow
                                 # component underneath gives the band districts
                                 # - a taller stretch, then a lower one - which
                                 # is what a real near skyline has
FORE_SIDE_F = 0.34               # how much of a near building's width is its
                                 # shaded side, as CITY_SIDE_F is for a tower.
                                 # Not snapped to a window column the way the
                                 # skyline's is: at this grid a near block holds
                                 # two or three bays, so snapping would put the
                                 # division on the halfway line every time
FORE_TREE_SIDE_F = 0.42          # ...and of a crown's width. Nearer the middle
                                 # than a building's, because a round shape
                                 # turns away from the light gradually and a
                                 # terminator far off centre reads as a bite
                                 # taken out of it rather than as shading
FORE_STEP = (0.80, 1.00)         # how far to advance, as a fraction of the
                                 # building's own width - so neighbours touch or
                                 # overlap by up to 20%, and NEVER leave sky
                                 # between them. A continuous band is what makes
                                 # the layer read as one nearer plane instead of
                                 # a row of shapes each competing with the
                                 # skyline behind it.
                                 #
                                 # The ceiling of 1.00 is what guarantees the
                                 # continuity; the floor of 0.80 is set by the
                                 # notches. The silhouette at any x is the MAX
                                 # over everything covering it, so heavy overlap
                                 # is an upper envelope - a smoother - and it
                                 # eats exactly the notches this layer needs. At
                                 # 20% a notched building still shows 60% of its
                                 # own width at its own height. The earlier 8-50%
                                 # is why the top edge came out flat.
                                 #
                                 # The skyline itself may not do any of this: x
                                 # is TIME up there, so sliding one tower into
                                 # its neighbour's slot would move a loud moment.
                                 # Nothing down here answers to the recording -
                                 # _fore() is not even handed the envelope -
                                 # which is what makes overlap free.
FORE_BAY_W = CITY_BAY_W * 1.6    # a nearer building has BIGGER windows, and
FORE_ROW_H = CITY_ROW_H * 1.6    # therefore fewer of them. Pitch is the third
                                 # depth cue after size and overlap, and the
                                 # one that does the most work: at the skyline's
                                 # own 13x15 the near layer carried the same
                                 # texture as the towers, and two layers with
                                 # one texture are one layer however their tones
                                 # are set
FORE_OCCUPIED_P = 0.92           # MORE lit than the downtown's 0.95 and 0.50,
FORE_LIT_P = 0.60                # not less: 55% of these panes are lit against
                                 # the towers' 48%. Held low at first on the
                                 # theory that a quiet near layer would keep the
                                 # skyline dominant, which was wrong twice over
                                 # - one window in twelve read as an abandoned
                                 # block rather than a restrained one, and the
                                 # nearer thing is the one you can see INTO. The
                                 # skyline stays dominant on size and position,
                                 # which is where dominance actually comes from.
FORE_TREE_P = 0.14               # chance of a street tree beside a building.
                                 # Low: the buildings overlap, so trees have no
                                 # gaps to sit in and simply stand in front -
                                 # and enough of them at one height stops
                                 # reading as street trees and starts reading as
                                 # a hedge drawn across the whole frame
FORE_TREE_H = 0.105 * DH         # about a short block's height, so it reads
                                 # against them the way TOWN_TREE_H does a house

# Antennas. Biased to the tall buildings, because that is where they are, and
# because a mast on a short block just looks like a mistake.
ANT_MIN_T = 0.45                 # normalised height below which none appear
ANT_P0, ANT_P1 = 0.22, 0.60      # chance = ANT_P0 + ANT_P1 * normalised height.
                                 # Raised from 0.15: at the old odds a wide
                                 # frame came out with five masts, which reads
                                 # as an accident rather than as a skyline. The
                                 # beacons are the one thing in the video that
                                 # moves on its own, so they have to be present
                                 # enough to notice - but only just. ANT_MAX
                                 # still stops a Fine skyline becoming a comb.
ANT_MAX = 14                     # ...but only this many, tallest first, or a
                                 # --towers Fine skyline turns into a comb
ANT_LEN_F = 0.16                 # A mast is a fraction of ITS OWN building's
ANT_LEN_JIT = (0.85, 1.15)       # height, give or take. Drawing an absolute
ANT_LEN = (14.0, 56.0)           # length from the seed and then trimming it to
                                 # whatever headroom was left over did the exact
                                 # opposite of what a skyline does: the tallest
                                 # tower sits closest to the ceiling, so it had
                                 # the least room and ended up wearing the
                                 # shortest stub. Scaling instead makes the
                                 # tallest tower carry the longest antenna,
                                 # which is the whole reason a mast reads as
                                 # height at all. The upper bound is fourteen
                                 # times MAST_W - past that it stops being a
                                 # mast and becomes a stray pin.
ANT_CEIL = 389.0                 # a mast is never allowed above this, and a
ANT_MIN_LEN = 12.0               # building with less than ANT_MIN_LEN of room
                                 # under it simply goes bare.
MAST_W = 4.0                     # a RECTANGLE, never a stroke. It stands
                                 # against open sky, so it carries the same
                                 # silhouette-vs-sky contrast every roofline
                                 # has; being axis-aligned it also downsamples
                                 # without the fringing a hairline would get
LIGHT_H = 6.0                    # the lit TIP of the needle, mast-width. No
                                 # housing: the needle is the whole shape and
                                 # its top segment is what lights up
# The tip is bounded by sky above and by the rest of the mast below, which is
# what keeps it readable everywhere. A lit colour against the SKY is only 1.90
# on Morning and 1.80 on Evening, and against the BODY only 2.02 on Canopy and
# 1.65 on Alpine - but the two are complementary, so whichever edge is weak in
# a palette, the other one carries the tip. Worst case is 4.56, on Morning.

# Blink, for the video only. Slow and out of step with each other; a whole
# skyline winking together reads as a fault, not as a city.
BLINK_PERIOD = (3.0, 4.6)        # seconds, per light
BLINK_ON = 2.0                   # seconds lit out of each period, i.e. lit
                                 # rather more than half the time. A beacon
                                 # that is mostly OFF makes the whole skyline
                                 # go dark whenever the phases happen to line
                                 # up, which reads as a fault; mostly ON reads
                                 # as a lit city with a slow wink in it, and
                                 # keeps the video close to its own thumbnail


# --- the sky ---------------------------------------------------------------
# A star field behind everything, drawn first so every silhouette occludes it
# for free. It is not silhouette geometry and carries no audio: only the seed
# reaches it, so a loud recording and a quiet one get the same sky and the
# skyline in front stays the only thing that answers to the sound.
#
# Built on its own RNG stream, salted off the same envelope seed, so turning
# stars on cannot move a single building. That is what makes the byte-identity
# guarantee hold in both directions rather than only with the feature off.
STAR_SALT = 0x57A125             # ...this, xored into the envelope seed
STAR_TOP = 8.0                   # stars run the WHOLE sky, text included -
STAR_BASE = GROUND               # ...down to the ground line the trees stand
                                 # on. Derived from GROUND rather than repeating
                                 # 690, the same way MTN_BASE is: below it every
                                 # style has ground, and a star under the
                                 # buildings' feet reads as a fault. The 30-unit
                                 # strip beneath is why this is not simply DH.
STAR_N = 170                     # stars at Default detail. Nearly free: they
                                 # are baked into the two layer PNGs and cost
                                 # the encode nothing at all - only the
                                 # TWINKLERS below cost anything, which is what
                                 # lets the field be large and the motion small.
STAR_SIZE = (1.10, 3.40)         # design units, so 2-6px at the sizes actually
                                 # rendered. Whole pixels at draw time - see
                                 # lss_draw.star_rect
STAR_GAMMA = 2.6                 # most stars small, a few large. A flat draw
                                 # gives a field of identical dots, which reads
                                 # as a texture rather than as a sky
STAR_DIM = (0.30, 0.72)          # distance toward the sky, brightest to
                                 # faintest, on the same measure every face in
                                 # lss_draw uses. The faint end is deliberately
                                 # near the sky: a star at the edge of visible
                                 # is what gives the field depth, and the ones
                                 # that read are the few at 0.30.

# Twinkle, for the video only - and the whole encode budget lives here.
# Measured at 2560x1440: a drawbox with an enable= expression costs about 1us
# per filter per frame, linear to ~240 filters. Two filters per twinkler at the
# cap below is 96, i.e. ~11s added to the ~18min a three-hour render already
# takes, about 1%. The cap is a hard bound rather than a target because
# --detail scales STAR_N: a Fine sky gets more stars, never more filters.
STAR_TWINKLE_F = 0.28            # chance a star twinkles at all. Most of the
                                 # sky is still; a field where every point moves
                                 # reads as noise, where a few do reads as air
STAR_TWINKLE_MAX = 48            # ...but never more than this many, brightest
                                 # first, exactly as ANT_MAX bounds the beacons
STAR_TWINKLE_MIN = 0.25          # and never one this far toward the sky - a
                                 # filter spent on an invisible dot is a filter
                                 # wasted out of a budget that is the whole
                                 # constraint on the feature
STAR_PERIOD = (9.0, 17.0)        # seconds, per star: 3.5-6.7 excursions a
                                 # minute against the beacons' 13-20. A beacon
                                 # is a light that BLINKS and wants to be
                                 # noticed; a star fades and must not be.
STAR_LEVELS = (0.62,)            # the star's drawn tone, as a fraction of its
                                 # own distance from the sky. Every star is
                                 # drawn here - in a still, and baked into both
                                 # video layers - so a twinkler and its
                                 # neighbours are the same brightness and the
                                 # field never looks thinner than the thumbnail.

# A twinkle FADES a star toward the sky and brings it back; it does not
# brighten one. That is the way round it has to be, and the reason is what the
# filters can do rather than a preference.
#
# A drawbox paints any colour, including the sky's - the "only ever add" rule
# the beacons follow is not a property of drawbox, it is a property of a BEACON,
# which sits on a building whose colour differs either side of the playhead and
# varies along the frame under a cycle, so there is no one colour that erases
# it. A star has none of that: it stands on bare sky, guaranteed exactly by
# _visible_stars, and the sky is the same constant in both layers and every
# frame. So painting the sky over a star erases it perfectly.
#
# Which means the baked level is free to be the star's FULL brightness and the
# filters can carry the whole visible swing. Built the other way first - baked
# faint, filters brightening - and it was barely perceptible: the floor could
# not go below the baked level, so the swing was under 2x and read as a pulse
# rather than as a star appearing.
STAR_FADE = 1.00                 # the bottom of a fade, as a blend from the
                                 # star's own tone toward the sky. 1.0 is gone
                                 # entirely - a star that disappears and returns
                                 # is far more noticeable than one that dims
STAR_FADE_MID = 0.55             # the step on the way down, as a fraction of
                                 # that. Nested inside the window below and
                                 # drawn FIRST, so the deeper level wins where
                                 # both are on - which is how two filters make
                                 # five steps, tone -> part -> gone -> part ->
                                 # tone, rather than one on/off blink
STAR_DIP = 0.40                  # fraction of the period spent below the star's
                                 # own tone, so it is present for most of its
                                 # cycle and away for a stretch of it
STAR_DIP_FLOOR = 0.42            # ...and this fraction of the dip at the very
                                 # bottom, centred inside it. At the periods
                                 # above no step is shorter than about 1.5s,
                                 # which is what makes it read as a fade rather
                                 # than a flicker


# --- how high the silhouette may rise, per column --------------------------
# A single ceiling makes every column pay the WORST column's price, and the
# slate is not a solid bar: it is text down the left, a clock on the right and
# a wide hole in between. Measuring where the text actually ends hands that
# hole back to the skyline.
#
# This changes the height a loud passage is ALLOWED, never the loudness that
# earns it - a quiet block under an empty span is still a quiet block.
CEIL_SAMPLE = 2.0                # design units between profile samples
CEIL_CLEAR = 29.0                # air kept under a text baseline, and to its
                                 # left and right. The old flat 389 was exactly
                                 # this much under the lowest baseline at 360.
CEIL_FREE = 0.42 * DH            # ...and the ceiling where nothing is above at
                                 # all. Not zero: the top of the frame has to
                                 # stay sky or the slate stops reading as a
                                 # block sitting on a skyline.
CEIL_RAMP = 46.0                 # how far the ceiling takes to climb out of a
                                 # text column. A step would put a cliff in the
                                 # skyline that no loud moment put there.


# --- the vertical layout, as one record ------------------------------------
# Every constant above is a 16:9 value. A square cover is 1280x1280 design
# units rather than 1280x720, and the difference has to land SOMEWHERE. It
# lands here, split the only way that does not distort the drawing:
#
#   the ground and the ceiling MOVE          (this record)
#   a tree, a house, a window DO NOT         (DH, above, stays 720)
#
# so a taller frame gets a taller city with more storeys in it - _tower_windows
# derives its row count from the block height at a fixed pitch - rather than
# the same city stretched. The two styles that cannot grow that way, houses and
# forest, keep their proportions and gain sky instead.
#
# The slate rides the frame's middle, exactly as it always has: the "CITY .
# CONDITIONS" baseline is at half the design height, 360 of 720 and 640 of
# 1280. Pinning it there rather than deriving it per style is what keeps a
# shelf of covers reading as one series - the titles sit on one line whatever
# silhouette is under them.
#
# Every field is written so that at dh == DH it reduces to the constant above
# it EXACTLY: the scales are multiplications by 1.0 and the offsets are
# additions of 0.0, both of which are identities for any finite float. A 16:9
# render is therefore byte-identical by construction rather than by testing -
# and tools/identity_check.py tests it anyway.
class VLayout(NamedTuple):
    dh: float                    # design height for this aspect ratio
    dy: float                    # how far the slate rides down from 16:9
    ground: float
    town_base: float
    mtn_base: float
    fore_base: float
    star_base: float
    mtn_top_min: float
    mtn_top_max: float
    city_max_h: float
    ceil_free: float


def vlayout(dh=DH):
    """The vertical layout for a frame `dh` design units tall."""
    s = dh / DH                              # exactly 1.0 at 16:9
    grow = dh - DH                           # exactly 0.0 at 16:9
    ground = GROUND + grow                   # the ground rides the bottom edge
    return VLayout(
        dh=dh, dy=dh / 2.0 - 360.0,
        ground=ground, town_base=TOWN_BASE + grow,
        mtn_base=ground, fore_base=ground, star_base=ground,
        # a summit and a tower keep the FRACTION of the frame they always had,
        # which is what stops the skyline thinning to a strip on a tall frame
        mtn_top_min=ground - (GROUND - MTN_TOP_MIN) * s,
        mtn_top_max=ground - (GROUND - MTN_TOP_MAX) * s,
        city_max_h=CITY_MAX_H * s,
        # ...and the free ceiling travels with the slate, not with the frame:
        # it exists to keep air above the text, so it is measured from there
        ceil_free=CEIL_FREE + (dh / 2.0 - 360.0),
    )


DESIGN = vlayout()               # the 16:9 layout: every constant above, as-is


def ceiling_profile(boxes, free_y=CEIL_FREE, clear=CEIL_CLEAR, ramp=CEIL_RAMP):
    """The highest a silhouette may rise at each x, from measured text extents.

    `boxes` is (x0, x1, ink_bottom) per run of slate text, in design units.
    Smoothed so the ceiling ramps rather than steps, then floored by the hard
    profile again - the smoothing is only ever allowed to push the ceiling DOWN
    toward the text, never up into it.
    """
    xs = np.arange(-60.0, DW + 60.0 + CEIL_SAMPLE, CEIL_SAMPLE)
    hard = np.full(len(xs), float(free_y))
    for x0, x1, base in boxes:
        m = (xs >= x0 - clear) & (xs <= x1 + clear)
        hard[m] = np.maximum(hard[m], float(base) + clear)
    soft = smooth(hard, max(1.0, ramp / CEIL_SAMPLE), passes=1)
    return xs, np.maximum(soft, hard)


def ceil_at(prof, x, L=DESIGN):
    """The ceiling at one x, or the flat city_max_h line when there is none."""
    if prof is None:
        return L.town_base - L.city_max_h
    xs, p = prof
    return float(np.interp(x, xs, p))


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
def _mountains(db, detail, scale, dynamics, rng, L=DESIGN):
    """Summits from the envelope, flanks and faces from the summits."""
    # the frame this build is drawing into. At 16:9 every field IS the
    # module constant of the same name, so nothing below can tell.
    MTN_BASE, MTN_TOP_MIN, MTN_TOP_MAX = L.mtn_base, L.mtn_top_min, L.mtn_top_max
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


def _decid_poly(rng, x, base, h):
    """One deciduous tree: a lumpy crown over a bare trunk, as one closed path.

    Built to the same contract as the evergreen - a single simple polygon, so
    it strokes or fills as one shape and never needs a second pass.
    """
    tw = h * 0.050
    # a crown wider than about half the tree's height stops being a street tree
    # and starts being a blob laid over the houses behind it
    r = h * 0.29 * rng.uniform(0.90, 1.10)
    cy = base - h + r                                # crown centre
    # per-vertex jitter alone gives a starburst, since neighbouring vertices are
    # independent. Smoothing the radii first leaves a rounded crown carrying a
    # few lobes - which is the shape a shade tree actually reads as
    a0, a1, n = -0.25 * math.pi, 1.25 * math.pi, 21
    rad = smooth(r * (1.0 + rng.uniform(-0.16, 0.16, n + 1)), 1.1, passes=1)
    crown = []
    for i in range(n + 1):
        a = a0 + (a1 - a0) * i / n
        rr = float(rad[i])
        if rng.random() < 0.16:                      # an occasional notch out
            rr *= rng.uniform(0.84, 0.92)            # of the outline
        crown.append((x + rr * math.cos(a), cy - rr * math.sin(a)))
    yt = cy + r * 0.707                              # where the trunk meets it
    return ([(x + tw, base), (x + tw, yt)] + crown
            + [(x - tw, yt), (x - tw, base)])


def _trees(db, detail, scale, dynamics, rng, vary, L=DESIGN):
    """Even spacing, small deterministic variation, size pinned to the frame.

    Trees do not breathe with the recording - the mountains do - so in
    mountains/mountains_forest their height is independent of level. `forest`
    has nothing else carrying the signal, so there `vary` lets level move the
    height by a modest amount and add a tier.
    """
    # the frame this build is drawing into. At 16:9 every field IS the
    # module constant of the same name, so nothing below can tell.
    GROUND = L.ground
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


def _panes(rng, x0, y0, x1, y1, lit_p):
    """One opening, split into panes by sky gaps. [] if it is too small to draw.

    The split only happens when both halves AND the gap between them clear
    PANE_MIN - so a narrow house loses its glazing bars before it loses its
    windows, and loses its windows before it draws anything unreadable.
    """
    w, h = x1 - x0, y1 - y0
    if w < PANE_MIN or h < PANE_MIN:
        return []
    cols = 2 if (w - PANE_GAP) / 2.0 >= PANE_MIN else 1
    rows = 2 if (h - PANE_GAP) / 2.0 >= PANE_MIN else 1
    pw = (w - PANE_GAP * (cols - 1)) / cols
    ph = (h - PANE_GAP * (rows - 1)) / rows
    out = []
    for r in range(rows):
        for c in range(cols):
            px = x0 + c * (pw + PANE_GAP)
            py = y0 + r * (ph + PANE_GAP)
            out.append({"rect": (px, py, px + pw, py + ph),
                        "lit": rng.random() < lit_p})
    return out


def _windows(rng, a, b, base, top, occupied_p, lit_p, door=True):
    """A facade's openings: a row of windows and, usually, a door.

    Occupancy is decided per BUILDING and only then per pane, so lit windows
    cluster into a house instead of speckling evenly along the street.
    """
    hw, fh = b - a, base - top
    if hw <= 0 or fh <= 0:
        return []
    p = lit_p if rng.random() < occupied_p else 0.0
    bays = max(1, int(round(hw / BAY_W)))
    bw = hw / bays
    dbay = int(rng.integers(0, bays)) if door else -1
    out = []
    for i in range(bays):
        cx = a + (i + 0.5) * bw
        if i == dbay:                                # a door reaches the ground
            dw, dh = bw * DOOR_W_F, fh * DOOR_H_F
            if dw >= PANE_MIN and dh >= PANE_MIN:
                out.append({"rect": (cx - dw / 2.0, base - dh, cx + dw / 2.0,
                                     base), "lit": False})
            continue
        ww, wh = bw * WIN_W_F, fh * WIN_H_F
        wy = base - fh * WIN_SILL_F
        out += _panes(rng, cx - ww / 2.0, wy - wh / 2.0,
                      cx + ww / 2.0, wy + wh / 2.0, p)
    return out


def _tower_windows(rng, a, b, base, top, bay_w=TOWER_BAY_W, row_h=TOWER_ROW_H,
                   occupied_p=TOWER_OCCUPIED_P, lit_p=TOWER_LIT_P,
                   ww_f=0.50, wh_f=0.44):
    """A tall block gets a grid rather than a single storey of openings - which
    is most of what makes it read as flats instead of an enlarged house.

    The defaults are the town's. A city block is the same grid at a finer pitch
    and a much higher occupancy, so it passes its own numbers rather than
    getting a second copy of this.
    """
    hw, fh = b - a, base - top
    cols = max(1, int(round(hw / bay_w)))
    rows = max(1, int(round(fh / row_h)))
    bw, bh = hw / cols, fh / rows
    p = lit_p if rng.random() < occupied_p else 0.0
    ww, wh = bw * ww_f, bh * wh_f
    out = []
    for r in range(rows):
        for c in range(cols):
            cx = a + (c + 0.5) * bw
            cy = top + (r + 0.5) * bh
            out += _panes(rng, cx - ww / 2.0, cy - wh / 2.0,
                          cx + ww / 2.0, cy + wh / 2.0, p)
    return out


def _clip_left(poly, xc, base):
    """The part of a house left of xc, as its own polygon.

    Every roofline this module builds travels left to right without doubling
    back, so the cut is a single crossing and the result stays simple.
    """
    out = []
    for (x0, y0), (x1, y1) in zip(poly, poly[1:]):
        if x0 <= xc:
            out.append((x0, y0))
        if (x0 - xc) * (x1 - xc) < 0:
            t = (xc - x0) / (x1 - x0)
            out.append((xc, y0 + (y1 - y0) * t))
            break
    if len(out) < 2:
        return None
    out.append((xc, base))
    return out


def _clip_poly_left(poly, xc):
    """The part of a CLOSED polygon left of xc, as its own polygon.

    _clip_left is the cheap version, and it needs an outline whose x travels
    one way only - true of every roofline here and emphatically false of a tree
    crown, which goes out and comes back. This is the general half-plane cut,
    used for the shapes that double back.
    """
    out = []
    for a, b in zip(poly, list(poly[1:]) + [poly[0]]):
        if a[0] <= xc:
            out.append(a)
        if (a[0] - xc) * (b[0] - xc) < 0:
            t = (xc - a[0]) / (b[0] - a[0])
            out.append((xc, a[1] + (b[1] - a[1]) * t))
    return out if len(out) >= 3 else None


def _houses(lv, detail, rng, L=DESIGN):
    """Mostly low houses, with a tall block kept for the loudest few percent so
    the skyline stays residential."""
    # the frame this build is drawing into. At 16:9 every field IS the
    # module constant of the same name, so nothing below can tell.
    TOWN_BASE = L.town_base
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
            poly = [(a, TOWN_BASE), (a, top), (b, top), (b, TOWN_BASE)]
            out.append({"poly": poly, "tower": True, "cx": (a + b) / 2.0,
                        "panes": _tower_windows(rng, a, b, TOWN_BASE, top),
                        "shade": _clip_left(poly, a + hw * HOUSE_SIDE_F,
                                            TOWN_BASE)})
            continue
        # proportion the body to its own width, so a house stays house-shaped
        # however many of them there are, and let level move it only a little
        body = min(0.13 * DH, max(0.045 * DH,
                                  hw * HOUSE_BODY * (0.80 + 0.40 * float(t[i]))))
        top = TOWN_BASE - body
        roof, kind = _roof(rng, a, b, top)
        poly = [(a, TOWN_BASE)] + roof + [(b, TOWN_BASE)]
        # light comes from the right, as it does on the mountains, so the near
        # side is lit and the division falls to the left of centre
        h = {"poly": poly, "tower": False, "cx": (a + b) / 2.0,
             "panes": _windows(rng, a, b, TOWN_BASE, top,
                               HOUSE_OCCUPIED_P, HOUSE_LIT_P),
             "shade": _clip_left(poly, a + hw * HOUSE_SIDE_F, TOWN_BASE)}
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
        th = TOWN_TREE_H * rng.uniform(0.82, 1.18)
        if rng.random() < TOWN_DECID_P:
            # a shade tree carries its height as MASS, where an evergreen
            # carries it as a point, so the same number reads much heavier
            poly = _decid_poly(rng, x, TOWN_BASE, th * 0.80)
        else:
            poly = _tree_poly(rng, x, TOWN_BASE, th, int(rng.integers(3, 5)))
        trees.append({"poly": poly, "cx": x})
    return out, trees


# ----------------------------------------------------------------- city
def _roofline(rng, a, b, top):
    """A flat roof, or one that steps in once on its way up.

    Returns the points from the left parapet to the right one. x never travels
    backwards, which is what lets _clip_left cut the result in one crossing.
    """
    w = b - a
    if rng.random() >= CITY_SETBACK_P:
        return [(a, top), (b, top)]
    inset = w * rng.uniform(0.16, 0.30)
    rise = w * rng.uniform(0.18, 0.42)
    return [(a, top), (a + inset, top), (a + inset, top - rise),
            (b - inset, top - rise), (b - inset, top), (b, top)]


def _antenna(rng, cx, roof_y, ceil_y=ANT_CEIL, L=DESIGN):
    """A plain needle, as (polys, light_rect), or None if there is no room.

    Rectangles throughout - see MAST_W. The mast runs a little way below the
    roof so the building drawn over it hides the join rather than leaving the
    pole balanced on the parapet.

    Length comes from the building's own height, so the tallest tower in frame
    carries the longest mast. The headroom clamp is only a backstop now - the
    room was reserved in proportion when the building was sized.
    """
    h = ANT_LEN_F * (L.town_base - roof_y) * rng.uniform(*ANT_LEN_JIT)
    h = min(max(h, ANT_LEN[0]), ANT_LEN[1], roof_y - ceil_y)
    if h < ANT_MIN_LEN:
        return None, None
    tip = roof_y - h
    mast = [(cx - MAST_W / 2.0, roof_y + 6.0), (cx - MAST_W / 2.0, tip),
            (cx + MAST_W / 2.0, tip), (cx + MAST_W / 2.0, roof_y + 6.0)]
    light = (cx - MAST_W / 2.0, tip, cx + MAST_W / 2.0, tip + LIGHT_H)
    return [mast], light


def _city(lv, detail, rng, ceiling=None, L=DESIGN):
    """A downtown: one building per block, windows in a grid, a few antennas.

    Shares every part with the town - the same window builder at a finer pitch,
    the same left-hand shade, the same pane minimums - so the two styles differ
    in density and proportion rather than in kind.
    """
    # the frame this build is drawing into. At 16:9 every field IS the
    # module constant of the same name, so nothing below can tell.
    TOWN_BASE = L.town_base
    lv = np.asarray(lv, dtype=np.float64)
    if CITY_BLOCK_W > 0:
        # Regroup onto a fixed block width, keeping each group's LOUDEST block -
        # the same move _houses makes, and for the same reason: the onset
        # alignment already baked into lv survives the change of resolution, so
        # a siren still lands on the building that was playing when it happened.
        n = max(8, int(round((DW + 60.0) / CITY_BLOCK_W)))
        if len(lv) > n:
            e = np.linspace(0, len(lv), n + 1).astype(int)
            lv = np.array([lv[e[i]:max(e[i] + 1, e[i + 1])].max()
                           for i in range(n)])
        elif len(lv) < n:
            lv = np.interp(np.linspace(0, len(lv) - 1, n),
                           np.arange(len(lv)), lv)
    n = len(lv)
    w = (DW + 60.0) / n
    t = np.clip((lv + 0.45) / 1.40, 0.0, 1.0)        # level to 0..1
    out = []
    for i, v in enumerate(t):
        x0 = -30.0 + i * w
        gap = w * CITY_GAP_F
        a, b = x0 + gap / 2.0, x0 + w - gap / 2.0
        bw = b - a
        # The block's own column decides how much room it HAS; the recording
        # decides how much of that room it uses. A quiet block under an empty
        # span stays quiet - only the top of the range moves.
        cy = ceil_at(ceiling, (a + b) / 2.0, L)
        # ...and the ROOF stops a mast's length short of it. The ceiling bounds
        # the whole silhouette, antenna included, so a building that grows right
        # up to it leaves its own mast nowhere to stand - which put the beacons
        # on the short blocks and took them off the towers, exactly backwards.
        #
        # The mast is now a fraction of the building, so the reserve has to be
        # too, and roof and mast have to be solved together: a taller roof wants
        # a taller mast, which leaves less room for the roof. Setting the tip at
        # the ceiling and solving for the roof gives ry below. It comes out as a
        # SCALE on the whole height range rather than a subtraction from it,
        # which is what keeps it monotone - the inversion risk last time came
        # from GATING the reserve on the antenna threshold, not from letting it
        # vary with height, and a smooth scale reintroduces nothing.
        fj = ANT_LEN_F * ANT_LEN_JIT[1]
        ry_min = (cy + fj * TOWN_BASE) / (1.0 + fj)
        hmax = max(CITY_MIN_H + 1.0, TOWN_BASE - ry_min)
        top = TOWN_BASE - (CITY_MIN_H + (hmax - CITY_MIN_H) * float(v))
        roof = _roofline(rng, a, b, top)
        # a setback rises off the roof, so the ceiling has to be checked after
        # it is built, not before. Drop the whole thing rather than flatten it.
        over = ry_min - min(y for _, y in roof)
        if over > 0:
            roof = [(rx, ry + over) for rx, ry in roof]
            top += over
        poly = [(a, TOWN_BASE)] + roof + [(b, TOWN_BASE)]
        # The shadow face, on the tall blocks only, and snapped to a window
        # column edge. A plain fraction of the width put the division through
        # the middle of a column - and at the default tower count it landed
        # 8.4u into a 30u block, narrower than one 13u bay, so the grid drawn
        # over it hid the facet entirely and the row read as flat rectangles.
        # On the column edge it falls BETWEEN two grids instead, which on a
        # two-column block is the middle: a corner-on building, the same read
        # the mountains get from their crease.
        cols = max(1, int(round(bw / CITY_BAY_W)))
        nb = min(max(1, int(round(CITY_SIDE_F * cols))), max(1, cols - 1))
        out.append({
            "poly": poly, "tower": True, "cx": (a + b) / 2.0, "t": float(v),
            "panes": _tower_windows(rng, a, b, TOWN_BASE, top,
                                    bay_w=CITY_BAY_W, row_h=CITY_ROW_H,
                                    occupied_p=CITY_OCCUPIED_P,
                                    lit_p=CITY_LIT_P, ww_f=0.55, wh_f=0.50),
            "shade": (_clip_left(poly, a + nb * (bw / cols), TOWN_BASE)
                      if float(v) >= CITY_SHADE_T else None),
            "roof": roof,
        })

    # Antennas, decided for every building so the draw does not depend on the
    # order they are later capped in, then kept tallest-first.
    want = []
    for i, h in enumerate(out):
        # the row runs off both edges by design, so the outermost buildings are
        # only part on screen. A mast out there is invisible, and would still
        # cost the video a pair of blink filters aimed off the frame.
        on_frame = MAST_W <= h["cx"] <= DW - MAST_W
        if h["t"] < ANT_MIN_T or not on_frame:
            rng.random()                             # keep the stream aligned
            continue
        if rng.random() < ANT_P0 + ANT_P1 * h["t"]:
            want.append(i)
    want.sort(key=lambda i: -out[i]["t"])
    lights = []
    for i in want[:ANT_MAX]:
        h = out[i]
        # stand it on the highest part of the roof, so a setback carries it
        ry = min(y for _, y in h["roof"])
        span = [p for p in h["roof"] if p[1] == ry]
        cx = (span[0][0] + span[-1][0]) / 2.0 if len(span) > 1 else h["cx"]
        polys, light = _antenna(rng, cx, ry,
                                ceil_at(ceiling, cx, L) if ceiling is not None
                                else ANT_CEIL, L)
        if polys is None:                            # no headroom under the slate
            continue
        h["antenna"] = polys
        h["light"] = light
        lights.append({"rect": light, "cx": cx,
                       "period": float(rng.uniform(*BLINK_PERIOD)),
                       "phase": float(rng.uniform(0.0, 6.0))})
    lights.sort(key=lambda L: L["cx"])
    return out, lights


def _fore(detail, rng, L=DESIGN):
    """The city's near layer: the same buildings, closer - shorter, wider,
    varied, and overlapping each other.

    Returned as one list in draw order. Nothing here consults the envelope; the
    whole point of the layer is a fixed near edge for the skyline to move
    behind.

    Everything here carries a shaded side, light from the right as everywhere
    else - but at a much shorter step than the skyline's, and the reason is the
    ladder rather than the drawing: the near layer's DARKEST tone still has to
    sit nearer than the skyline's lightest, or the two layers interleave and
    stop being two layers.
    """
    # the frame this build is drawing into. At 16:9 every field IS the
    # module constant of the same name, so nothing below can tell.
    FORE_BASE = L.fore_base
    # Widths first, because the heights are a PROFILE across the whole band
    # rather than a draw per building - and the profile needs to know how many
    # buildings there are. detail counts features, so it narrows them rather
    # than shrinking them, the same way more houses are narrower houses.
    span = []
    x = -60.0
    while x < DW + 40.0:
        w = rng.uniform(*FORE_W) / detail
        span.append((x, x + w))
        x += w * rng.uniform(*FORE_STEP)
    n = len(span)

    # Three components. The smoothed run gives the band districts - a taller
    # stretch, then a lower one - where independent draws alone give a comb;
    # the per-building draw breaks the districts up; and the notches cut holes
    # right through, which is what stops a continuous band reading as a bar.
    slow = smooth(rng.uniform(0.0, 1.0, n), max(1.0, n / 8.0), passes=1)
    lo, hi = float(slow.min()), float(slow.max())
    slow = (slow - lo) / (hi - lo) if hi - lo > 1e-9 else np.full(n, 0.5)
    fast = rng.uniform(0.0, 1.0, n)
    mix = FORE_SLOW_F * slow + (1.0 - FORE_SLOW_F) * fast
    hgt = FORE_H[0] + mix * (FORE_H[1] - FORE_H[0])
    notch = rng.random(n) < FORE_NOTCH_P
    hgt = np.where(notch, rng.uniform(*FORE_NOTCH_H, n), hgt)

    out = []
    for (a, b), h in zip(span, hgt):
        w, h = b - a, float(h)
        top = FORE_BASE - h
        roof = _roofline(rng, a, b, top)
        poly = [(a, FORE_BASE)] + roof + [(b, FORE_BASE)]
        out.append({"poly": poly, "cx": (a + b) / 2.0, "h": h,
                    "shade": _clip_left(poly, a + w * FORE_SIDE_F, FORE_BASE),
                    "panes": _tower_windows(rng, a, b, FORE_BASE, top,
                                            bay_w=FORE_BAY_W, row_h=FORE_ROW_H,
                                            occupied_p=FORE_OCCUPIED_P,
                                            lit_p=FORE_LIT_P,
                                            ww_f=0.55, wh_f=0.50)})
    # tallest first, so the shorter ones in front paint over them - the same
    # order the mountains are built in, and for the same reason: which shape
    # occludes which is the whole of what makes a flat row read as depth
    out.sort(key=lambda f: -f["h"])
    # street trees last of all, since they are the nearest thing in the frame.
    # Deciduous only - a town mixes evergreens in, a downtown street does not
    for f in list(out):
        if rng.random() < FORE_TREE_P:
            tx = f["cx"] + rng.uniform(-0.60, 0.60) * FORE_W[0]
            poly = _decid_poly(rng, tx, FORE_BASE,
                               FORE_TREE_H * rng.uniform(0.74, 1.26))
            xs = [p[0] for p in poly]
            out.append({"poly": poly, "cx": tx, "h": 0.0, "tree": True,
                        "shade": _clip_poly_left(
                            poly, min(xs) + (max(xs) - min(xs)) * FORE_TREE_SIDE_F)})
    return out


# ----------------------------------------------------------------- sky
def _stars(rng, n, twinkle=True, L=DESIGN):
    """A field of stars on a jittered grid, most faint and small, a few not.

    A grid rather than n independent draws, for the reason the treeline is
    evenly spaced and then jittered: uniform random over an area clumps, and a
    clump in a star field reads as a mistake rather than as a cluster. One star
    per cell, a few cells skipped and a few doubled, gives even coverage that
    is nowhere regular.
    """
    # the frame this build is drawing into. At 16:9 every field IS the
    # module constant of the same name, so nothing below can tell.
    h = L.star_base - STAR_TOP
    cell = math.sqrt(DW * h / max(1, n))
    cols, rows_ = max(1, int(round(DW / cell))), max(1, int(round(h / cell)))
    cw, ch = DW / cols, h / rows_
    out = []
    for r in range(rows_):
        for c in range(cols):
            # a skipped cell and a doubled one are the same device: they break
            # the grid up. Without them the field reads as a lattice at exactly
            # the moment the eye stops looking for individual stars.
            if rng.random() < 0.10:
                continue
            for _ in range(2 if rng.random() < 0.08 else 1):
                # size and brightness come off ONE draw, so a big star is a
                # bright one - with a little slack, or the field looks graded
                u = rng.random()
                b = min(1.0, max(0.0, u + rng.uniform(-0.12, 0.12)))
                out.append({
                    "cx": (c + rng.uniform(0.08, 0.92)) * cw,
                    "cy": STAR_TOP + (r + rng.uniform(0.08, 0.92)) * ch,
                    "size": STAR_SIZE[0] + (STAR_SIZE[1] - STAR_SIZE[0])
                    * u ** STAR_GAMMA,
                    "dim": STAR_DIM[1] - (STAR_DIM[1] - STAR_DIM[0]) * b,
                    "b": b,
                })
    # Who twinkles. Decided for every star so the draw does not depend on the
    # order they are later capped in - the same move _city makes for antennas.
    want = []
    for s in out:
        if s["b"] < STAR_TWINKLE_MIN:
            rng.random()                             # keep the stream aligned
            continue
        if rng.random() < STAR_TWINKLE_F:
            want.append(s)
    want.sort(key=lambda s: -s["b"])                 # brightest first
    for s in want[:STAR_TWINKLE_MAX] if twinkle else []:
        s["twinkle"] = True
        s["period"] = float(rng.uniform(*STAR_PERIOD))
        s["phase"] = float(rng.uniform(0.0, STAR_PERIOD[1]))
    # One tone for every star, twinkling or not, drawn in the still and baked
    # into both video layers alike. A twinkler is only ever taken DOWN from
    # here by the filters, so nothing in the field is dimmer than it looks in
    # the thumbnail - see the note on STAR_FADE
    for s in out:
        s["tone"] = s["dim"] * STAR_LEVELS[0]
    out.sort(key=lambda s: s["b"])                   # faintest first
    return out


def _body_span(boxes):
    """The empty run between the series name and the episode number.

    Nothing draws with this yet. It is measured here because that span is where
    a sun or moon goes, and measuring it alongside the stars is what lets the
    celestial body join this same background layer without the call site or the
    structure moving. Both runs sit on the 150 baseline; if only one is there,
    the number is absent and the whole right of the frame is free.
    """
    if not boxes:
        return None
    # the topmost baseline, whatever it is - the slate rides down on a frame
    # taller than 16:9, so matching a literal 150 would find nothing there
    top_y = min(b[2] for b in boxes)
    top = sorted((b for b in boxes if abs(b[2] - top_y) < 1.0),
                 key=lambda b: b[0])
    if not top:
        return None
    left = top[0][1]
    return (left, top[1][0] if len(top) > 1 else DW)


def sky(db, detail="Default", boxes=None, twinkle=True, dh=DH):
    """The background layer: a star field now, a sun or moon later.

    Salted off the envelope seed onto its own RNG stream, so the silhouette is
    bit-identical whether or not there are stars in front of it.

    `dh` is the frame's design height: the field runs down to that frame's own
    ground line, so a square cover gets stars all the way to the rooftops
    rather than a 16:9 band of them with bare sky underneath.
    """
    f = resolve_detail(detail)
    seed = seed_from(db) ^ STAR_SALT
    rng = np.random.default_rng(seed)
    L = vlayout(dh)
    # STAR_N is a DENSITY, not a count. The square cover's sky is 1.8x the
    # 16:9 band, and holding the count fixed there would draw the same stars
    # thinner - a cover that reads as a clearer night than its own thumbnail.
    # Exactly 1.0 at 16:9: the same two numbers over each other.
    spread = (L.star_base - STAR_TOP) / (STAR_BASE - STAR_TOP)
    stars = _stars(rng, max(8, int(round(STAR_N * f * spread))), twinkle, L)
    return {"seed": seed, "stars": stars,
            "twinklers": sum(1 for s in stars if s.get("twinkle")),
            # the slot the next pass fills, and the room it has to fill it in
            "body": None, "body_span": _body_span(boxes)}


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
    if filled and style not in FILLED_STYLES:
        return (f"--filled has no meaning for --style {style}: the style "
                "defines its own fill, with trees filling as the playhead "
                "passes and mountains staying outlined.")
    if not 0.0 <= progress <= 1.0:
        return f"--progress must be between 0 and 1, not {progress:g}"
    return None


def build(style, db, lv, detail="Default", scale="Skyline (rank)",
          dynamics="More", ceiling=None, dh=DH):
    """All the geometry one render needs, in design units.

    `dh` is the frame's design height - 720 for the 16:9 thumbnail and video,
    1280 for a square cover. It is the ONLY thing that differs between the two:
    same seed, same envelope, same feature sizes, one layout apart. Which is
    also why a cover has to be built separately rather than cropped from the
    16:9 geometry - the shapes are the same shapes, standing on a lower ground
    line with more room over their heads.
    """
    L = vlayout(dh)
    f = resolve_detail(detail)
    seed = seed_from(db)
    rng = np.random.default_rng(seed)
    sc = {"style": style, "seed": seed, "detail": f, "dh": dh,
          # the ground line the range stands on, so the draw can trim the flank
          # strokes to it without importing this module's layout constants
          "mtn_base": L.mtn_base,
          "mountains": [], "trees": [], "houses": [], "town_trees": [],
          "fore": [], "lights": []}
    if style in ("mountains", "mountains_forest"):
        sc["mountains"] = _mountains(db, f, scale, dynamics, rng, L)
    if style in ("forest", "mountains_forest"):
        sc["trees"] = _trees(db, f, scale, dynamics, rng,
                             vary=(style == "forest"), L=L)
    if style == "houses":
        sc["houses"], sc["town_trees"] = _houses(lv, f, rng, L)
    if style == "city":
        # the same list the houses use: a city block and a house get the same
        # body, shade and pane treatment, so they are one thing to draw
        sc["houses"], sc["lights"] = _city(lv, f, rng, ceiling, L)
        # ...and the near layer after it, so the skyline's own draws land
        # exactly where they landed before there was a foreground at all
        sc["fore"] = _fore(f, rng, L)
    return sc
