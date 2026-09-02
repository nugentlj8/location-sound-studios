# tools

Development scripts. **Not part of the app** — `lss_update.py` ships only what is
in `lss_studio/`, so nothing here reaches an installed copy.

All of them write into `renders/`, which is gitignored, and regenerate their own
test audio if it is not there.

| | |
|---|---|
| `identity_check.py` | renders the style/palette/state/stars matrix plus one video per style and SHAs them, to prove a change left every existing style byte-for-byte identical |
| `weather_samples.py` | the weather review set — every style off/clouds/rain, the three palettes that decide the contrast rule, and Morning at thumbnail size on four seeds plus one with the cloud band forced onto the slate |
| `stars_preview.py` | the 24-still star-field review set — both styles, both playback states, every palette that allows a field |
| `encode_bench.py` | libx264 vs h264_nvenc vs av1_nvenc on the SAME layer PNGs and filter graph, timed interleaved, so the difference measured is the encoder and nothing else |
| `stars_fade_options.py` | three star fade depths on one 30-second clip, so the depth can be chosen against a real encode |

## Proving a change renders identically

Run the baseline **from the commit you are changing away from**, then again with
your change applied:

```
git stash                              # or check out the old commit
py tools/identity_check.py baseline
git stash pop
py tools/identity_check.py after
py tools/identity_check.py compare
```

The SHA files land in `renders/` rather than being committed: they depend on
which fonts this machine has, so a checked-in baseline would be misleading
somewhere else. Regenerate it from the old commit instead.

The matrix covers both stars off and stars on — stars have been on by default
since 1.9.0, so a run without them tests half the shipped behaviour. The video
cases hash the **decoded frames** rather than the mp4: a container carries
metadata that moves between runs, so hashing the file would fail on every
rebuild and prove nothing.

Stash only `lss_studio/` when taking the baseline, or the harness reverts along
with the code it is measuring:

```
git stash push lss_studio/
py tools/identity_check.py baseline
git stash pop
```
