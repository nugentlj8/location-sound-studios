"""Three-way encoder comparison at matched file size.

Renders once, then encodes the SAME layer PNGs and the SAME filter_complex
graph three ways: libx264 (the shipping settings), h264_nvenc and av1_nvenc.
Intercepting at build_video is what makes it apples-to-apples - the geometry,
the sky, the mask and the drawtext are one derivation, so any difference in
the output is the encoder and nothing else.

  py tools/encode_bench.py stage    # render + keep the layers
  py tools/encode_bench.py run      # interleaved timed encodes
  py tools/encode_bench.py stills   # extract matched-timestamp PNGs
"""
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "lss_studio"))

BENCH = os.path.join(REPO, "renders", "encode-bench")
LAYERS = os.path.join(BENCH, "layers")
CLIP = os.path.join(BENCH, "clip60.wav")


# ------------------------------------------------------------------ stage
def stage():
    """Run a real render, but keep the layer PNGs and the filter graph."""
    import lss_render as R

    orig = R.build_video
    captured = {}

    def keep(cfg, paths, audio, dur, W, H, out, fps=10, crf=None,
             on_progress=None):
        # the real encode still happens - that IS the libx264 reference, and
        # running it here means the filter script gets written for us
        t0 = time.time()
        res = orig(cfg, paths, audio, dur, W, H, out, fps=fps, crf=crf,
                   on_progress=on_progress)
        captured["inline_secs"] = time.time() - t0

        os.makedirs(LAYERS, exist_ok=True)
        for name, p in paths.items():
            shutil.copy2(p, os.path.join(LAYERS, os.path.basename(p)))
        fsrc = os.path.join(os.path.dirname(paths["bone"]), "_filters.txt")
        shutil.copy2(fsrc, os.path.join(LAYERS, "_filters.txt"))
        shutil.copy2(audio, os.path.join(LAYERS, "_audio.wav"))

        captured.update({
            "dur": dur, "W": W, "H": H, "fps": fps,
            "crf": crf if crf is not None else cfg.get("crf", 16),
            "x264_preset": cfg.get("x264_preset", "slow"),
            "pix_fmt": "yuv444p" if cfg.get("full_chroma") else "yuv420p",
            "layer_files": sorted(os.path.basename(p) for p in paths.values()),
            "reference_mp4": out,
        })
        return res

    R.build_video = keep
    sys.argv = [
        "lss_render.py", CLIP,
        "--series", "Sounds of the City",
        "--style", "city",
        "--colors", "Night",
        "--place", "Encoder Test",
        "--city", "Benchmark",
        "--conditions", "Clear 54F",
        "--date", "2026-08-11",
        "--start", "09:15 PM",
        "--outdir", BENCH,
    ]
    R.main()

    json.dump(captured, open(os.path.join(BENCH, "params.json"), "w"), indent=2)
    print("\nstaged:", LAYERS)
    print(json.dumps(captured, indent=2))


# -------------------------------------------------------------------- run
def cmd_for(codec, P, out, bitrate=None):
    """The shipping command with only -c:v and its rate control swapped."""
    fps, D = P["fps"], f"{P['dur']:.3f}"
    cmd = ["ffmpeg", "-y", "-v", "error",
           "-loop", "1", "-framerate", str(fps),
           "-i", os.path.join(LAYERS, "_bone.png"),
           "-loop", "1", "-framerate", str(fps),
           "-i", os.path.join(LAYERS, "_clay.png"),
           "-i", os.path.join(LAYERS, "_audio.wav"),
           "-loop", "1", "-framerate", str(fps),
           "-i", os.path.join(LAYERS, "_mhard.png"),
           "-filter_complex_script", os.path.join(LAYERS, "_filters.txt"),
           "-map", "[v]", "-map", "2:a"]

    if codec == "libx264":
        cmd += ["-c:v", "libx264", "-preset", P["x264_preset"],
                "-crf", str(P["crf"])]
    elif codec == "h264_nvenc":
        cmd += ["-c:v", "h264_nvenc", "-preset", "p7", "-tune", "hq",
                "-rc", "vbr", "-b:v", bitrate,
                "-maxrate", bitrate, "-bufsize", "16M"]
    elif codec == "av1_nvenc":
        cmd += ["-c:v", "av1_nvenc", "-preset", "p7", "-tune", "hq",
                "-rc", "vbr", "-b:v", bitrate,
                "-maxrate", bitrate, "-bufsize", "16M"]
    else:
        raise SystemExit("unknown codec " + codec)

    cmd += ["-pix_fmt", P["pix_fmt"], "-g", str(fps * 10),
            "-c:a", "aac", "-b:a", "320k", "-t", D, "-shortest", out]
    return cmd


def encode(codec, P, out, bitrate=None):
    cmd = cmd_for(codec, P, out, bitrate)
    t0 = time.time()
    p = subprocess.run(cmd, capture_output=True, text=True)
    el = time.time() - t0
    if p.returncode != 0:
        raise SystemExit(f"{codec} failed:\n{p.stderr[-2000:]}")
    return el, os.path.getsize(out)


def video_bytes(path):
    """Video stream size only - the AAC track is identical across all three
    and would otherwise flatter whichever codec we matched against."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "packet=size", "-of", "csv=p=0", path],
        capture_output=True, text=True).stdout
    return sum(int(x) for x in out.split() if x.strip().rstrip(",").isdigit()
               or x.strip().rstrip(",").lstrip("-").isdigit())


def run():
    P = json.load(open(os.path.join(BENCH, "params.json")))
    outdir = os.path.join(BENCH, "out")
    os.makedirs(outdir, exist_ok=True)

    # 1. the reference: shipping libx264 settings, CRF - size falls where it falls
    ref = os.path.join(outdir, "libx264.mp4")
    el, size = encode("libx264", P, ref)
    vbytes = video_bytes(ref)
    target_kbps = vbytes * 8 / P["dur"] / 1000.0
    print(f"reference libx264 CRF {P['crf']} preset {P['x264_preset']}: "
          f"{el:.1f}s, file {size/1e6:.2f} MB, video {vbytes/1e6:.2f} MB, "
          f"{target_kbps:.0f} kbps")

    # 2. calibrate each nvenc encoder onto that video-stream size. VBR lands
    #    close but not exact, so nudge the request by the ratio it missed by.
    rates = {}
    for codec in ("h264_nvenc", "av1_nvenc"):
        req = target_kbps
        for attempt in range(4):
            tmp = os.path.join(outdir, f"_cal_{codec}.mp4")
            encode(codec, P, tmp, bitrate=f"{req:.0f}k")
            got = video_bytes(tmp) * 8 / P["dur"] / 1000.0
            err = (got - target_kbps) / target_kbps
            print(f"  {codec} calibrate {attempt}: asked {req:.0f}k "
                  f"-> {got:.0f}k ({err*100:+.1f}%)")
            if abs(err) <= 0.02:
                break
            req = req * target_kbps / got
        rates[codec] = f"{req:.0f}k"
        os.remove(tmp)

    # 3. interleaved timed rounds. Blocked runs drift with clocks and thermals;
    #    round-robin puts all three under the same conditions.
    rounds = 3
    times = {"libx264": [], "h264_nvenc": [], "av1_nvenc": []}
    sizes = {}
    for r in range(rounds):
        for codec in ("libx264", "h264_nvenc", "av1_nvenc"):
            out = os.path.join(outdir, f"{codec}.mp4")
            el, size = encode(codec, P, out, bitrate=rates.get(codec))
            times[codec].append(el)
            sizes[codec] = (size, video_bytes(out))
            print(f"round {r+1} {codec:12s} {el:6.1f}s  {size/1e6:6.2f} MB")

    print("\n=== results ===")
    res = {}
    for codec in ("libx264", "h264_nvenc", "av1_nvenc"):
        t = sorted(times[codec])
        med, best = t[len(t)//2], t[0]
        size, vb = sizes[codec]
        rt = P["dur"] / med
        res[codec] = {"times": times[codec], "median": med, "best": best,
                      "file_bytes": size, "video_bytes": vb,
                      "kbps": vb*8/P["dur"]/1000.0, "realtime_x": rt,
                      "rate_control": rates.get(codec, f"CRF {P['crf']}")}
        print(f"{codec:12s} median {med:6.1f}s  best {best:6.1f}s  "
              f"{rt:5.2f}x realtime  file {size/1e6:5.2f} MB  "
              f"video {vb/1e6:5.2f} MB ({vb*8/P['dur']/1000:.0f} kbps)")
    json.dump(res, open(os.path.join(BENCH, "results.json"), "w"), indent=2)


# ----------------------------------------------------------------- stills
def stills(ts="00:00:41"):
    """Same timestamp from all three, plus 1:1 crops of sky and skyline."""
    P = json.load(open(os.path.join(BENCH, "params.json")))
    outdir = os.path.join(BENCH, "out")
    sd = os.path.join(BENCH, "stills")
    os.makedirs(sd, exist_ok=True)
    W, H = P["W"], P["H"]
    # sky crop: upper band, where the gradient and stars live.
    # edge crop: across the playhead, so both colour states of the skyline are
    # in one 1:1 view - that boundary is the hardest thing in the frame.
    crops = {
        "sky":  f"900:500:{int(W*0.30)}:{int(H*0.06)}",
        "edge": f"900:500:{int(W*0.34)}:{int(H*0.52)}",
    }
    for codec in ("libx264", "h264_nvenc", "av1_nvenc"):
        src = os.path.join(outdir, f"{codec}.mp4")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", ts, "-i", src,
                        "-frames:v", "1",
                        os.path.join(sd, f"full_{codec}.png")], check=True)
        for name, c in crops.items():
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", ts, "-i", src,
                            "-frames:v", "1", "-vf", f"crop={c}",
                            os.path.join(sd, f"{name}_{codec}.png")], check=True)
    print("stills in", sd)


if __name__ == "__main__":
    {"stage": stage, "run": run, "stills": stills}[
        sys.argv[1] if len(sys.argv) > 1 else "run"]()
