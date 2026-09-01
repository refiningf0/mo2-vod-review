"""Turn a Mortal Online 2 gameplay recording into structured combat data.

MO2 writes no combat log to disk -- the log exists only as text drawn in the
bottom-left of the screen. So the only way to get the data out of a recording
is to read it off the frames.

    video -> sample frames -> crop the log panel -> adaptive threshold -> OCR
          -> tolerant parse -> canonicalise names -> dedupe -> events.json

Frames are sampled faster than the log scrolls, so every line is seen several
times. That redundancy is what makes it work: OCR mangles a name differently
on each frame, and taking the most common reading across all of them recovers
the right spelling.

Usage:
    python mo2log.py <video> [--fps 2] [--crop x,y,w,h] [--out events.json]
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor

from PIL import Image

from preprocess import prep
from parse import parse_line, canonical_names, dedupe, is_weapon

def _here():
    """Where our own data files live.

    Running from source that is this folder. Inside a PyInstaller build the
    scripts, the PowerShell OCR helper and the bundled ffmpeg are unpacked to
    a temporary directory that only sys._MEIPASS knows about.
    """
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


def _tool(name):
    """Prefer the ffmpeg we ship, fall back to whatever is on PATH."""
    local = os.path.join(_here(), name + ".exe")
    return local if os.path.exists(local) else name


HERE = _here()
OCR_PS1 = os.path.join(HERE, "ocr_win.ps1")
OCR_BATCH = os.path.join(HERE, "ocr_batch.ps1")


def ocr(path):
    """Read text from an image with the OCR engine built into Windows."""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", OCR_PS1, "-Path", path],
            capture_output=True, timeout=60,
        )
    except Exception:
        return []
    # A frame occasionally comes back empty when the engine is under load.
    # One frame contributes little on its own -- every line is read from many
    # of them -- so a failure is skipped rather than aborting the run.
    if r.returncode != 0 or not r.stdout:
        return []
    # Decode explicitly rather than letting Python guess. OCR of noisy frames
    # regularly returns bytes that the console's default cp1252 codec cannot
    # map, which raised inside the pipe reader and silently killed whole
    # frames -- losing whichever part of the fight they covered.
    txt = r.stdout.decode("utf-8", errors="replace")
    return [ln.strip() for ln in txt.splitlines() if ln.strip()]



def ocr_folder(folder):
    """OCR every PNG in a folder in one PowerShell process.

    Spawning a process per frame costs about a second each, which dominates
    the runtime -- far more than the recognition itself. Doing the whole
    folder in one process turns minutes into well under one.

    Returns {filename: [lines]}.
    """
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", OCR_BATCH, "-Dir", folder],
            capture_output=True, timeout=3600,
        )
    except Exception:
        return {}
    if not r.stdout:
        return {}

    # Decode explicitly: OCR of noisy frames returns bytes the console's
    # default codec cannot map, and letting Python guess killed whole frames.
    txt = r.stdout.decode("utf-8", errors="replace")

    out, cur = {}, None
    for line in txt.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("##FILE "):
            cur = line[7:].strip()
            out[cur] = []
        elif cur:
            out[cur].append(line)
    return out

def probe(video):
    r = subprocess.run(
        [_tool("ffprobe"), "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height",
         "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", video],
        capture_output=True, text=True,
    )
    vals = [v for v in r.stdout.split() if v.strip()]
    if len(vals) < 2:
        # An IndexError here reads as a bug in the tool when it is almost
        # always a path that does not exist, or a file ffprobe cannot open.
        if not os.path.exists(video):
            raise SystemExit("no such file:\n  %s" % video)
        raise SystemExit(
            "ffprobe could not read this file:\n  %s\n\n%s"
            % (video, (r.stderr or "").strip()[:400] or "(no error output)"))
    return int(vals[0]), int(vals[1]), (float(vals[2]) if len(vals) > 2 else 0.0)


FIND_PS1 = os.path.join(_here(), "find_log.ps1")

# A line worth locating by: someone hitting someone for an amount.
LOGLINE = re.compile(r"\b(?:hit|hits|lit|nit)\b.{0,40}?\bfor\b", re.I)


def log_visible(video, crop, dur, samples=4, need=3):
    """Is there combat text where we expect it? Cheap check, a few frames."""
    x, y, cw, ch = crop
    tmp = tempfile.mkdtemp(prefix="mo2peek_")
    try:
        step = max(dur / (samples + 1), 1.0)
        for i in range(samples):
            for pre in (["-hwaccel", "d3d11va"], []):
                subprocess.run(
                    [_tool("ffmpeg"), "-v", "error"] + pre +
                    ["-ss", str(round(step * (i + 1), 2)), "-i", video,
                     "-vf", "crop=%d:%d:%d:%d" % (cw, ch, x, y),
                     "-frames:v", "1", os.path.join(tmp, "p%02d.png" % i)],
                    capture_output=True)
                if os.path.exists(os.path.join(tmp, "p%02d.png" % i)):
                    break
        for f in os.listdir(tmp):
            if f.endswith(".png"):
                try:
                    p = os.path.join(tmp, f)
                    prep(Image.open(p)).save(p)
                except Exception:
                    pass
        pages = ocr_folder_parallel(tmp, 2)
        hits = sum(1 for lines in pages.values()
                   for ln in lines if LOGLINE.search(ln))
        return hits >= need
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def detect_crop(video, w, h, dur, samples=6, verbose=True):
    """Locate the combat log by OCRing a few whole frames.

    The default crop assumes the log sits bottom-left at the size it does on
    one particular setup. A different UI scale, an ultrawide monitor or a moved
    chat panel all break that, and nobody sharing this is going to work out
    --crop by hand.

    MO2 also paints damage numbers over the world, which read as combat lines
    too. Those are scattered; the log is a left-aligned column, so lines whose
    left edge sits near the common one are the log and the strays are not.

    Returns a crop, or None to fall back to the default.
    """
    tmp = tempfile.mkdtemp(prefix="mo2find_")
    try:
        step = max(dur / (samples + 1), 1.0)
        for i in range(samples):
            for pre in (["-hwaccel", "d3d11va"], []):
                r = subprocess.run(
                    [_tool("ffmpeg"), "-v", "error"] + pre +
                    ["-ss", str(round(step * (i + 1), 2)), "-i", video,
                     "-frames:v", "1", os.path.join(tmp, "s%02d.png" % i)],
                    capture_output=True)
                if os.path.exists(os.path.join(tmp, "s%02d.png" % i)):
                    break

        r = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", FIND_PS1, "-Dir", tmp],
            capture_output=True)
        boxes = []
        for line in r.stdout.decode("utf-8", errors="replace").splitlines():
            if line.startswith("##FILE") or "\t" not in line:
                continue
            coords, text = line.split("\t", 1)
            if not LOGLINE.search(text):
                continue
            try:
                x, y, bw, bh = (int(v) for v in coords.split())
            except ValueError:
                continue
            boxes.append((x, y, bw, bh))

        if len(boxes) < 4:
            if verbose:
                print("crop    could not find the log; using the default")
            return None

        lefts = sorted(b[0] for b in boxes)
        common = lefts[len(lefts) // 2]
        near = [b for b in boxes if abs(b[0] - common) <= max(240, w * 0.12)]
        if len(near) < 4:
            return None

        tops = sorted(b[1] for b in near)
        bots = sorted(b[1] + b[3] for b in near)

        # Consecutive log lines sit a fixed distance apart. Measuring that
        # gives the crop a unit to reason in, instead of guessing at fractions
        # of the screen.
        gaps = [b - a for a, b in zip(tops, tops[1:]) if 6 < b - a < 120]
        pitch = sorted(gaps)[len(gaps) // 2] if gaps else max(14, int(h * 0.017))

        # A few sampled frames never show the log at its fullest, so the
        # highest line seen is not the highest it reaches -- leave slots above
        # it. Land on a line boundary while doing so: a half-included line
        # reads as a fragment, which is worse than not seeing it, and that is
        # what a crop sized to the frame rather than to the text produced.
        y0 = int(max(0, tops[0] - 3 * pitch))
        y1 = int(min(h, bots[-1] + pitch))

        x0 = int(max(0, min(b[0] for b in near) - 16))
        wide = max(b[0] + b[2] for b in near) - x0
        cw = int(min(w - x0, max(wide + 220, w * 0.52)))
        crop = (x0, y0, cw, y1 - y0)
        if verbose:
            print("crop    found the log in %d frames, lines %dpx apart"
                  % (samples, pitch))
        return crop
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def default_crop(w, h):
    """The combat log sits bottom-left; generous box as a fraction of frame."""
    return (0, int(h * 0.70), int(w * 0.52), int(h * 0.26))


def extract(video, outdir, fps, crop):
    """Sample frames and crop to the log panel.

    AV1 clips (what most modern capture tools produce) make ffmpeg's software
    decoder fall over with "no sequence header". Hardware decoding handles them,
    so it is tried first and the software path is kept as a fallback.
    """
    x, y, cw, ch = crop
    vf = "fps=%s,crop=%d:%d:%d:%d" % (fps, cw, ch, x, y)
    out = os.path.join(outdir, "f%06d.png")

    for pre in (["-hwaccel", "d3d11va"], []):
        r = subprocess.run([_tool("ffmpeg"), "-v", "error"] + pre +
                           ["-i", video, "-vf", vf, out],
                           capture_output=True, text=True)
        frames = sorted(f for f in os.listdir(outdir) if f.endswith(".png"))
        if frames:
            return frames
        for f in os.listdir(outdir):
            if f.endswith(".png"):
                os.remove(os.path.join(outdir, f))
    raise RuntimeError("ffmpeg produced no frames -- check the file and codec")


def _prep_one(job):
    """Preprocess one frame. Top level so it can be sent to a worker process."""
    src, dst = job
    try:
        prep(Image.open(src)).save(dst)
        return os.path.basename(src)
    except Exception:
        return None


def _workers():
    return max(1, min(os.cpu_count() or 2, 8))


def ocr_folder_parallel(folder, n):
    """OCR a folder using n PowerShell processes over disjoint slices.

    Each output line is tagged with its filename, so the slices can be merged
    without caring what order they finish in.
    """
    total = len([f for f in os.listdir(folder) if f.endswith(".png")])
    if total == 0:
        return {}
    n = max(1, min(n, total))
    size = (total + n - 1) // n

    procs = []
    for i in range(n):
        procs.append(subprocess.Popen(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", OCR_BATCH, "-Dir", folder,
             "-Skip", str(i * size), "-Take", str(size)],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL))

    pages, cur = {}, None
    for p in procs:
        out, _ = p.communicate()
        for line in out.decode("utf-8", errors="replace").splitlines():
            if line.startswith("##FILE "):
                cur = line[7:].strip()
                pages.setdefault(cur, [])
            elif cur and line.strip():
                pages[cur].append(line.strip())
    return pages


def run(video, fps=2.0, crop=None, out="events.json", keep=False, verbose=True,
        roster=None, min_seen=None, no_path=False):
    w, h, dur = probe(video)
    if crop is None:
        # The default crop is tuned, and tuned beats derived here: MO2 also
        # paints damage numbers over the world, so a crop reaching wider or
        # higher than the log panel starts reading those as log lines. A crop
        # 100px taller than the default invented hits on a hand-checked clip.
        # So only go hunting when the usual place turns up nothing -- a moved
        # chat panel, an unusual UI scale, an ultrawide screen.
        crop = default_crop(w, h)
        if not log_visible(video, crop, dur):
            found = detect_crop(video, w, h, dur, verbose=verbose)
            if found:
                crop = found
            elif verbose:
                print("crop    no log found anywhere; trying the default anyway")
    fps_f = float(fps)
    if verbose:
        print("video   %dx%d  %.1fs" % (w, h, dur))
        print("crop    x=%d y=%d w=%d h=%d" % crop)
        print("sample  %s fps" % fps)

    tmp = tempfile.mkdtemp(prefix="mo2frames_")
    prepdir = os.path.join(tmp, "prep")
    os.makedirs(prepdir, exist_ok=True)
    try:
        frames = extract(video, tmp, fps, crop)
        if verbose:
            print("frames  %d extracted\n" % len(frames))

        if verbose:
            print("preprocessing...")
        jobs = [(os.path.join(tmp, fn), os.path.join(prepdir, fn)) for fn in frames]
        at = {fn: i / fps_f for i, fn in enumerate(frames)}
        nw = _workers()
        with ProcessPoolExecutor(max_workers=nw) as pool:
            done = list(pool.map(_prep_one, jobs, chunksize=4))
        ready = [(fn, at[fn]) for fn in done if fn]

        if verbose:
            print("reading %d frames across %d workers..." % (len(ready), nw))
        pages = ocr_folder_parallel(prepdir, nw)

        raw_events, lines_seen = [], 0
        for fn, frame_t in ready:
            for raw in pages.get(fn, []):
                lines_seen += 1
                ev = parse_line(raw, frame_t)
                if ev:
                    raw_events.append(ev)

        anchors = canonical_names(raw_events, roster=roster)

        # Put both kinds of event on one clock before deduping. Lines whose
        # timestamp OCR recovered are anchored to the wall clock; the rest
        # already carry their frame time. Shifting the wall clock so its first
        # event lines up with that same event's frame time reconciles them.
        # Wall clock and frame clock differ by a constant -- when the recording
        # started -- and each timestamped line gives one estimate of it. But a
        # line stays on screen for seconds and is read from every frame in that
        # span, all carrying the same [hh:mm:ss]. Only its FIRST sighting marks
        # when it actually appeared; the later ones say the clip started
        # progressively earlier than it did. Averaging over all readings pulls
        # the estimate about half a window late and floats every timestamped
        # hit past the untimed ones around it.
        #
        # So collapse each line to its earliest sighting first, then take the
        # median across lines so one misread timestamp cannot skew the result.
        first = {}
        for e in raw_events:
            if not e["exact"] or e.get("ft") is None:
                continue
            key = (e["t"], e["dir"], e["amount"],
                   e["who"] if e["dir"] == "in" else e["target"])
            if key not in first or e["ft"] < first[key]:
                first[key] = e["ft"]
        offsets = sorted(t - ft for (t, _, _, _), ft in first.items())
        if offsets:
            shift = offsets[len(offsets) // 2]
            for e in raw_events:
                if e["exact"]:
                    e["t"] = round(e["t"] - shift, 1)

        events = dedupe(raw_events)
        events = [e for e in events if 0 <= e["t"] <= max(dur, 1) + 5]

        # A fabricated hit shows up in one frame while a real one is read from
        # every frame the line was on screen. How many that is varies hugely
        # between clips, though -- a slow fight gives 20 to 40 readings a line,
        # a fast-scrolling one gives two -- so a fixed cutoff either lets
        # phantoms through or deletes most of a busy fight. Judge each event
        # against what is normal for its own clip instead.
        seen_all = sorted(e.get("seen", 1) for e in events)
        typical = seen_all[len(seen_all) // 2] if seen_all else 1
        floor = min_seen if min_seen else max(1, int(typical * 0.25))

        thin = [e for e in events if e.get("seen", 1) < floor]
        events = [e for e in events if e.get("seen", 1) >= floor]
        events.sort(key=lambda e: e["t"])

        # "Unknown" stands in for an attacker OCR could not recover. It is a
        # placeholder, not a combatant, so it stays out of the roster.
        names = sorted(({e["who"] for e in events if e["who"] != "You"} |
                        {e["target"] for e in events if e["target"] != "You"})
                       - {"Unknown"})
        data = dict(
            source=os.path.basename(video),
            # Full path so the report can open the clip it was read from. It
            # travels with the file, so a shared report carries the folder it
            # came out of -- see --no-path.
            source_path=(None if no_path else os.path.abspath(video)),
            duration=round(dur, 1),
            fps_sampled=fps_f,
            frames=len(frames),
            ocr_lines=lines_seen,
            players=names,
            events=events,
        )
        with open(out, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)

        if verbose:
            hit = [e for e in events if e.get("kind", "hit") == "hit"]
            heal = [e for e in events if e.get("kind") == "heal"]
            o = sum(e["amount"] for e in hit if e["dir"] == "out")
            n = sum(e["amount"] for e in hit if e["dir"] == "in")
            print("\nOCR lines read     : %d" % lines_seen)
            print("raw events         : %d" % len(raw_events))
            print("after dedupe       : %d" % len(events))
            print("read %d times/hit  : typical, so anything under %d is noise"
                  % (typical, floor))
            if thin:
                print("dropped, seen < %d  : %d  (%s)" % (
                    floor, len(thin),
                    ", ".join("%s on %s" % (e["amount"],
                                            e["target"] if e["dir"] == "out" else e["who"])
                              for e in thin[:6])))
            print("players seen       : %s" % ", ".join(names) if names else "-")
            print("damage out / in    : %d / %d" % (o, n))
            if heal:
                hi = sum(e["amount"] for e in heal if e["dir"] == "in")
                ho = sum(e["amount"] for e in heal if e["dir"] == "out")
                who = sorted({e["who"] for e in heal if e["dir"] == "in"} - {"Unknown"})
                print("healing in / out   : %d / %d%s" % (
                    hi, ho, ("  (from %s)" % ", ".join(who)) if who else ""))
            magic = [e for e in hit if e.get("ability") and not is_weapon(e["ability"])]
            if magic:
                mo = sum(e["amount"] for e in magic if e["dir"] == "out")
                mi = sum(e["amount"] for e in magic if e["dir"] == "in")
                spells = sorted({e["ability"] for e in magic})
                print("magic out / in     : %d / %d  (%s)" % (mo, mi, ", ".join(spells)))
            print("wrote %s" % out)
        return data
    finally:
        if not keep:
            shutil.rmtree(tmp, ignore_errors=True)
        elif verbose:
            print("frames kept in %s" % tmp)


def main():
    ap = argparse.ArgumentParser(description="Extract MO2 combat data from a recording.")
    ap.add_argument("video")
    ap.add_argument("--fps", default="2", help="frames sampled per second (default 2)")
    ap.add_argument("--crop", help="x,y,w,h of the log panel; default is bottom-left")
    ap.add_argument("--out", default="events.json")
    ap.add_argument("--keep-frames", action="store_true")
    ap.add_argument("--min-seen", type=int, default=None,
                    help="frames an event must be read from to count "
                         "(default: a quarter of what is typical for the clip)")
    ap.add_argument("--players", help="comma-separated real player names; improves accuracy a lot")
    ap.add_argument("--no-path", action="store_true",
                    help="leave the video's location out of the report")
    a = ap.parse_args()
    crop = tuple(int(v) for v in a.crop.split(",")) if a.crop else None
    roster = [p.strip() for p in a.players.split(',')] if a.players else None
    run(a.video, fps=a.fps, crop=crop, out=a.out, keep=a.keep_frames,
        roster=roster, min_seen=a.min_seen, no_path=a.no_path)


if __name__ == "__main__":
    main()
