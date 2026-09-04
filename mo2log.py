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

from preprocess import prep, plain
from parse import parse_line, canonical_names, dedupe, is_weapon

# Every child process below opens a console window of its own unless told not
# to. Under the console build that went unnoticed -- children inherit the
# window already on screen -- but the app has no console to lend, so a single
# run flashed up ffmpeg's window and one per OCR worker: about ten black boxes
# for the length of the run.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


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
            capture_output=True, timeout=60, creationflags=NO_WINDOW,
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
            capture_output=True, timeout=3600, creationflags=NO_WINDOW,
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
        capture_output=True, text=True, creationflags=NO_WINDOW,
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
        vf = "crop=%d:%d:%d:%d" % (cw, ch, x, y)

        def grab(want, pre):
            """Seek to each wanted moment at once rather than one after another.

            These are independent single frames, and every run pays for them
            before it starts: four seeks into a 4K clip, in turn, was several
            seconds of the wait before anything appeared to be happening.
            """
            procs = [(i, subprocess.Popen(
                [_tool("ffmpeg"), "-v", "error"] + pre +
                ["-ss", str(round(step * (i + 1), 2)), "-i", video,
                 "-vf", vf, "-frames:v", "1",
                 os.path.join(tmp, "p%02d.png" % i)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=NO_WINDOW)) for i in want]
            for _, proc in procs:
                proc.wait()
            return [i for i, _ in procs
                    if not os.path.exists(os.path.join(tmp, "p%02d.png" % i))]

        # Hardware decode for all of them, then the software path for any it
        # could not manage -- the same fallback as before, one round instead
        # of one per frame.
        missing = grab(range(samples), ["-hwaccel", "d3d11va"])
        if missing:
            grab(missing, [])
        for f in os.listdir(tmp):
            if f.endswith(".png"):
                try:
                    p = os.path.join(tmp, f)
                    prep(Image.open(p)).save(p)
                except Exception:
                    pass
        pages = ocr_folder_parallel(tmp, samples)
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
                    capture_output=True, creationflags=NO_WINDOW)
                if os.path.exists(os.path.join(tmp, "s%02d.png" % i)):
                    break

        r = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", FIND_PS1, "-Dir", tmp],
            capture_output=True, creationflags=NO_WINDOW)
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


# Segments to split the decode across. Measured on a 72s 4K AV1 clip: one
# process 23.8s, two 13.6s, four 11.8s, eight 12.5s. Four is the knee -- past
# it the decoder itself is the limit, not the number of processes asking.
EXTRACT_SEGMENTS = 4


def _frame_time(name, fps):
    """When in the clip a frame was taken, from its number.

    Read from the name rather than the frame's place in the list, because the
    segments below each start counting from a number of their own. A segment
    that comes up a frame short then shifts nothing after it -- where counting
    positions would slide every later frame half a second early, and with it
    every mark on the report's timeline.
    """
    return (int(os.path.splitext(name)[0][1:]) - 1) / fps


def extract(video, outdir, fps, crop, dur=None, segments=EXTRACT_SEGMENTS):
    """Sample frames and crop to the log panel.

    AV1 clips (what most modern capture tools produce) make ffmpeg's software
    decoder fall over with "no sequence header". Hardware decoding handles them,
    so it is tried first and the software path is kept as a fallback.

    One ffmpeg decoding the whole clip leaves most of the machine idle, so the
    clip is cut into stretches and one is given to each of several. Each is
    told the frame number to start counting from, so they write into a single
    sequence whatever order they finish in. The stretches are whole numbers of
    frames long, which keeps every frame at the time it would have had from a
    single pass.
    """
    x, y, cw, ch = crop
    vf = "fps=%s,crop=%d:%d:%d:%d" % (fps, cw, ch, x, y)
    out = os.path.join(outdir, "f%06d.png")

    # Splitting a short clip costs more in ffmpeg startups than it saves.
    n = 1
    if dur and segments > 1 and dur >= 20:
        n = max(1, min(segments, int(dur // 10)))
    per = int(-(-(dur * float(fps)) // n)) if n > 1 else 0

    for pre in (["-hwaccel", "d3d11va"], []):
        if n == 1:
            subprocess.run([_tool("ffmpeg"), "-v", "error"] + pre +
                           ["-i", video, "-vf", vf, out],
                           capture_output=True, text=True,
                           creationflags=NO_WINDOW)
        else:
            span = per / float(fps)
            procs = [subprocess.Popen(
                [_tool("ffmpeg"), "-v", "error"] + pre +
                ["-ss", "%.3f" % (i * span), "-t", "%.3f" % span,
                 "-i", video, "-vf", vf,
                 "-start_number", str(i * per + 1), out],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=NO_WINDOW) for i in range(n)]
            for p in procs:
                p.wait()

        frames = sorted(f for f in os.listdir(outdir) if f.endswith(".png"))
        if frames:
            return frames
        for f in os.listdir(outdir):
            if f.endswith(".png"):
                os.remove(os.path.join(outdir, f))
    raise RuntimeError("ffmpeg produced no frames -- check the file and codec")


# The treatments each frame is read through. Reading every frame twice, once
# treated and once merely enlarged, was worth 60% more runtime while the
# treatment was a contrast boost: the two failed on different lines and the
# consensus needed both. Lifting the mid greys instead of pushing contrast
# stopped the treated pass losing anything, so one is enough again -- it reads
# the same five of five hits and the same eleven of eleven lines, at the old
# speed. Adding `plain` back here is all it takes to read both ways again, and
# everything downstream still expects to be told which pass a reading came from.
TREATMENTS = (prep,)


def _prep_one(job):
    """Preprocess one frame. Top level so it can be sent to a worker process."""
    src, dst, which = job
    try:
        TREATMENTS[which](Image.open(src)).save(dst)
        return os.path.basename(src)
    except Exception:
        return None


def _workers():
    """Cores to put on the CPU-bound stage.

    Was capped at 8, which on a 16-core machine left half of it idle and the
    preprocess stage taking 13.5s where 12 workers took 10.5s.
    """
    return max(1, min(os.cpu_count() or 2, 16))


def _ocr_workers():
    """How many OCR processes to run at once.

    Measured over 145 frames on 16 cores, and the same in both directions so
    it is not a warm cache: 4 workers 32s, 8 workers 20s, 12 workers 10s,
    16 workers 5s. 24 was no better than 16.

    It keeps gaining well past the point a CPU-bound stage would stop because
    it is not CPU-bound -- each worker spends most of its life waiting on the
    system's OCR service. That is also why this is its own number rather than
    sharing one with the stage above: lowering that one for CPU reasons should
    not quietly cost four times here.
    """
    return max(2, min(os.cpu_count() or 2, 16))


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
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=NO_WINDOW))

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
        roster=None, min_seen=None, no_path=False, progress=None):
    """Read a clip and write its events to `out`.

    `progress`, if given, is called as progress(stage, done, total) as the run
    moves through it: stage is a short key, and done/total are None where a
    stage cannot count itself. It exists so a window can show what is
    happening; nothing about the reading depends on it.
    """
    def say(stage, done=None, total=None):
        if progress:
            progress(stage, done, total)

    say("probe")
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
    say("crop")
    if verbose:
        print("video   %dx%d  %.1fs" % (w, h, dur))
        print("crop    x=%d y=%d w=%d h=%d" % crop)
        print("sample  %s fps" % fps)

    tmp = tempfile.mkdtemp(prefix="mo2frames_")
    prepdirs = [os.path.join(tmp, "prep%d" % i) for i in range(len(TREATMENTS))]
    for d in prepdirs:
        os.makedirs(d, exist_ok=True)
    try:
        say("extract")
        frames = extract(video, tmp, fps, crop, dur=dur)
        if verbose:
            print("frames  %d extracted\n" % len(frames))

        if verbose:
            print("preprocessing...")
        jobs = [(os.path.join(tmp, fn), os.path.join(prepdirs[i], fn), i)
                for i in range(len(TREATMENTS)) for fn in frames]
        at = {fn: _frame_time(fn, fps_f) for fn in frames}
        nw = _workers()
        with ProcessPoolExecutor(max_workers=nw) as pool:
            # Iterated rather than collected in one go, so the count can be
            # reported as it climbs. Results still arrive in order.
            done = []
            for n, r in enumerate(pool.map(_prep_one, jobs, chunksize=4), 1):
                done.append(r)
                say("preprocess", n, len(jobs))
        # One entry per frame, whichever treatments managed it.
        ok = {fn for fn in done if fn}
        ready = [(fn, at[fn]) for fn in frames if fn in ok]

        if verbose:
            print("reading %d frames across %d workers..."
                  % (len(ready), _ocr_workers()))
        # Each worker hands back its whole slice when it finishes, so this
        # stage can say it is running but not how far along it is.
        say("ocr", None, len(ready) * len(TREATMENTS))
        reads = [ocr_folder_parallel(d, _ocr_workers()) for d in prepdirs]

        say("parse")
        raw_events, lines_seen = [], 0
        for fn, frame_t in ready:
            # A line both treatments read the same way is one sighting, not
            # two. One they read differently is two readings of one line, and
            # which treatment each came from is carried along -- otherwise two
            # readings of the same line look like two copies of it on screen,
            # which is how the dedupe tells overlapping lines apart.
            said = set()
            for which, pages in enumerate(reads):
                for raw in pages.get(fn, []):
                    key = " ".join(raw.split())
                    if key in said:
                        continue
                    said.add(key)
                    lines_seen += 1
                    ev = parse_line(raw, frame_t)
                    if ev:
                        ev["pass"] = which
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

        # Counting readings asks the wrong question of two kinds of real hit.
        #
        # A long line -- a name, a number, three flags -- has its number in the
        # middle, and OCR holds the ends of a line while dropping the middle.
        # One real hit here sat on screen for fifteen seconds across thirty
        # readings and gave up its number in six of them, which reads as noise
        # beside lines whose number came back every time. The in-game log had
        # it. How long it was there says what the count cannot.
        #
        # And a line still on screen when the recording stops was never going
        # to be read many times. Two heals in the last three seconds of a clip
        # were read seven times and twice, and both happened.
        #
        # Both need a timestamp before they are let back in. That is what
        # separates them from a stray misreading, which has no span, no
        # timestamp, and turns up in a single frame.
        solid = [e for e in events if e.get("seen", 1) >= floor]
        spans = sorted(e.get("span", 0) for e in solid)
        lasted = spans[len(spans) // 2] * 0.25 if spans else 0
        ends = max((t for _, t in ready), default=0)

        def real(e):
            if e.get("seen", 1) >= floor:
                return True
            if not e.get("exact"):
                return False
            if lasted > 0 and e.get("span", 0) >= lasted:
                return True
            return e.get("last", 0) >= ends and e.get("seen", 1) >= 2

        thin = [e for e in events if not real(e)]
        events = [e for e in events if real(e)]
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
        say("done", len(events), len(events))

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
